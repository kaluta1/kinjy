"""Final acceptance, settlement and refunds of marketplace orders (27/10).

The purchase becomes final in one of two ways, and only these:

* **physical products and services** - the buyer confirms receipt, in their own
  authenticated Kinjy account, after inspecting what is in front of them;
* **ebooks** - the buyer's first *successful* download (media-service saw the
  last byte go out), not the upload, the library entry or a link being made.

A seller marking an order shipped or done, a courier, a deadline: none of them
releases anything. There is no automatic release.

Acceptance and settlement are different facts and are kept apart:

* acceptance is recorded at once, with its own reference, and stays recorded
  whatever happens to the payment afterwards;
* settlement starts at once and is *pending* until every step is confirmed:
  the ledger posting (seller and sponsor credited as pending), the allocation
  of the held money at the payment provider (payment-service), then the
  credits made withdrawable. Each step is idempotent, so a retry after a
  timeout repeats nothing; a step that cannot be done automatically leaves the
  order in ``action_required`` for a person, never in a made-up "paid".

Before acceptance an order can be cancelled (the seller cannot supply it, the
buyer refused it at handover, delivery failed, an operational correction by
Kinjy). Its money then goes back to the buyer's Kinjy balance through the same
kind of confirmed allocation.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as OrmSession

from common.database import SessionLocal
from common.notify import notify

import models

log = logging.getLogger("commerce-service.acceptance")

LEDGER_URL = "http://ledger-service:8000"
PAYMENT_URL = "http://payment-service:8000"

# The marketplace policy the buyer acknowledges. A new wording is a new version.
POLICY_VERSION = "2026-10-27"
POLICY = {
    "version": POLICY_VERSION,
    "checkout": [
        "Your payment is held until the purchase becomes final. The seller is not paid before that.",
        "For a product or a service, the purchase becomes final when you confirm receipt in your own Kinjy account. "
        "For an ebook, it becomes final at your first successful download.",
        "Confirm only when the right product is in front of you and you have inspected it (or, for a service, when it "
        "is completed as described). Your confirmation immediately starts the payment to the seller.",
        "Kinjy does not arbitrate ordinary marketplace disputes. After the purchase is final, questions about quality, "
        "repair, replacement or warranty go to the seller - or to the insurer, if the listing includes insurance.",
        "This policy does not remove rights the law gives you, chargebacks through your payment provider, or the "
        "possibility of reporting fraud. Kinjy support still helps with payment problems, duplicate charges, an item "
        "not handed over after you confirmed, fraud and legally required action.",
    ],
    "acceptance": [
        "Confirm only if the correct product is physically in front of you, you have inspected it, and it is ready to "
        "be handed over now (for a service: it was performed as described).",
        "Your confirmation makes the purchase final under Kinjy's marketplace policy and immediately authorises "
        "payment to the seller.",
        "The seller or courier must check your confirmation in their own Kinjy account and then hand the product over "
        "at once.",
        "Later problems with the product are handled with the seller, under their guarantee or warranty or the "
        "included insurance if any, subject to the rights the law gives you.",
    ],
    "download": [
        "Your first successful download makes this purchase final under Kinjy's ebook policy and authorises payment "
        "to the seller.",
        "After that, ordinary marketplace refunds and disputes are not available, subject to the rights the law gives "
        "you. Kinjy support still helps if the file is missing, damaged or not the book described.",
    ],
    "handover_awaiting": "Awaiting buyer confirmation. Do not release the product or final service deliverables.",
    "handover_verified": "Buyer confirmation verified. Release the product or final service deliverables.",
}

CANCEL_REASONS = {
    # who may give it -> label
    "cannot_supply": ("seller", "The seller cannot supply it"),
    "refused_at_handover": ("seller", "The buyer refused it at handover"),
    "delivery_failed": ("seller", "Delivery failed"),
    "unpaid": ("buyer", "Cancelled before payment"),
    "operational": ("admin", "Cancelled by Kinjy (operational correction)"),
}

MAX_ATTEMPTS = int(os.getenv("SETTLEMENT_MAX_ATTEMPTS", "12"))
POLL_S = float(os.getenv("SETTLEMENT_POLL_S", "15"))
RETRY_BASE_S = int(os.getenv("SETTLEMENT_RETRY_BASE_S", "30"))
WORKER_ENABLED = os.getenv("SETTLEMENT_WORKER", "1") not in ("0", "false", "off")


class Retry(Exception):
    pass


class NeedsPerson(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_acceptance_ref() -> str:
    return f"ACC-{secrets.token_hex(5).upper()}"


# --- accepting ---------------------------------------------------------------

def accept(db: OrmSession, order: models.Order, *, by: str, kind: str) -> bool:
    """Record that the purchase is final and queue its settlement. The order row
    must be locked by the caller. Returns False if it was already accepted."""
    if order.accepted_at is not None:
        return False
    order.accepted_at = now()
    order.accepted_by = by
    order.acceptance_ref = new_acceptance_ref()
    order.acceptance_kind = kind
    order.acceptance_policy = POLICY_VERSION
    order.status = "accepted"
    order.settlement_status = "pending"
    order.settlement_attempts = 0
    order.settlement_next_at = order.accepted_at
    order.settlement_error = None
    return True


def record_ack(db: OrmSession, order: models.Order, stage: str, buyer_id: str) -> None:
    exists = db.scalar(select(models.PolicyAck).where(
        models.PolicyAck.order_id == order.id, models.PolicyAck.stage == stage))
    if exists is None:
        db.add(models.PolicyAck(order_id=order.id, buyer_id=buyer_id, stage=stage, policy_version=POLICY_VERSION))


def has_ack(db: OrmSession, order_id: str, stage: str) -> bool:
    return db.scalar(select(models.PolicyAck.id).where(
        models.PolicyAck.order_id == order_id, models.PolicyAck.stage == stage)) is not None


# --- the steps, each idempotent -----------------------------------------------------

def _call(method: str, url: str, **kwargs) -> dict:
    """A call to another service: 2xx -> its body; 409/4xx -> NeedsPerson; else Retry."""
    try:
        response = httpx.request(method, url, timeout=20, **kwargs)
    except httpx.HTTPError as exc:
        raise Retry(f"{url.split('/')[2]} did not answer") from exc
    if response.status_code < 300:
        return response.json() if response.content else {}
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text[:200]
    if response.status_code in (408, 429) or response.status_code >= 500:
        raise Retry(str(detail or f"HTTP {response.status_code}"))
    raise NeedsPerson(str(detail or f"HTTP {response.status_code}"))


def _settle_steps(order: models.Order) -> str:
    """Do (or finish) the settlement of one accepted order. Returns the backing."""
    held = order.customer_price - Decimal(order.refunded_amount or 0)
    _call("POST", f"{LEDGER_URL}/internal/post/escrow", json={
        "order_id": order.id, "amount": str(held), "action": "release",
        "description": f"Purchase final ({order.acceptance_kind}, {order.acceptance_ref})",
        "idempotency_key": f"escrow:release:{order.id}",
    })
    posted = _call("POST", f"{LEDGER_URL}/internal/post/marketplace-order", json={
        "buyer_id": order.buyer_id, "vendor_id": order.vendor_id, "vendor_price": str(order.vendor_price),
        "order_id": order.id, "idempotency_key": f"order:{order.id}", "hold": True,
    })
    journal = posted["journal"]
    order.ledger_journal_id = journal["id"]
    allocations = []
    for entry in journal["entries"]:
        amount = Decimal(entry.get("credit") or "0")
        if amount <= 0 or not entry.get("beneficiary_id"):
            continue
        if entry["account"] == "liabilities:vendor_payable":
            allocations.append({"member_id": entry["beneficiary_id"], "amount": str(amount), "kind": "seller"})
        elif entry["account"] == "liabilities:member_payable":
            allocations.append({"member_id": entry["beneficiary_id"], "amount": str(amount), "kind": "commission"})
    if not allocations:
        raise NeedsPerson("The settlement journal names nobody to pay")
    funds = _call("POST", f"{PAYMENT_URL}/internal/order-funds/allocate", json={
        "order_id": order.id, "custody_ref": order.custody_ref, "purpose": "settlement",
        "reference": f"settle-{order.id}", "allocations": allocations,
    })
    _call("POST", f"{LEDGER_URL}/internal/journals/release-pending", json={"key": f"order:{order.id}"})
    return funds.get("backing") or ""


def _refund_steps(order: models.Order) -> str:
    """Return a cancelled order's held money to the buyer's Kinjy balance."""
    amount = order.customer_price - Decimal(order.refunded_amount or 0)
    if amount <= 0:
        return "nothing_held"
    funds = _call("POST", f"{PAYMENT_URL}/internal/order-funds/allocate", json={
        "order_id": order.id, "custody_ref": order.custody_ref, "purpose": "refund",
        "reference": f"refund-{order.id}",
        "allocations": [{"member_id": order.buyer_id, "amount": str(amount), "kind": "refund"}],
    })
    _call("POST", f"{LEDGER_URL}/internal/post/escrow", json={
        "order_id": order.id, "amount": str(amount), "action": "refund_to_balance",
        "beneficiary_id": order.buyer_id, "description": f"Order cancelled before it was final: {order.cancel_reason}",
        "idempotency_key": f"escrow:refund-balance:{order.id}",
    })
    return funds.get("backing") or ""


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(RETRY_BASE_S * 2 ** max(attempts - 1, 0), 3600))


# --- running them -------------------------------------------------------------------

def run_settlement(order_id: str) -> str:
    """One attempt at settling an accepted order. Returns its settlement status."""
    with SessionLocal() as db:
        order = db.scalar(select(models.Order).where(models.Order.id == order_id)
                          .with_for_update(skip_locked=True))
        if order is None or order.settlement_status != "pending":
            db.rollback()
            return order.settlement_status if order is not None else "unknown"
        order.settlement_attempts = (order.settlement_attempts or 0) + 1
        try:
            backing = _settle_steps(order)
        except Retry as exc:
            order.settlement_error = str(exc)[:300]
            if order.settlement_attempts >= MAX_ATTEMPTS:
                order.settlement_status = "action_required"
            else:
                order.settlement_next_at = now() + _backoff(order.settlement_attempts)
            db.commit()
            log.warning("settlement of %s: %s (attempt %s)", order_id, exc, order.settlement_attempts)
            return order.settlement_status
        except NeedsPerson as exc:
            order.settlement_error = str(exc)[:300]
            order.settlement_status = "action_required"
            db.commit()
            log.error("settlement of %s needs a person: %s", order_id, exc)
            return order.settlement_status
        order.settlement_status = "confirmed"
        order.settlement_error = None
        order.settlement_backing = backing[:40] or None
        order.status = "settled"
        order.escrow_released_at = now()
        vendor, price = order.vendor_id, order.vendor_price
        db.commit()
    notify(vendor, "order_settled", "Settlement confirmed",
           body=f"{price} for order {order_id} is now available in your wallet.",
           link=f"/market/orders/{order_id}")
    return "confirmed"


def run_refund(order_id: str) -> str:
    """One attempt at refunding a cancelled order to the buyer's balance."""
    with SessionLocal() as db:
        order = db.scalar(select(models.Order).where(models.Order.id == order_id)
                          .with_for_update(skip_locked=True))
        if order is None or order.refund_status != "pending":
            db.rollback()
            return order.refund_status if order is not None else "unknown"
        order.refund_attempts = (order.refund_attempts or 0) + 1
        try:
            _refund_steps(order)
        except Retry as exc:
            order.refund_error = str(exc)[:300]
            if order.refund_attempts >= MAX_ATTEMPTS:
                order.refund_status = "action_required"
            else:
                order.refund_next_at = now() + _backoff(order.refund_attempts)
            db.commit()
            return order.refund_status
        except NeedsPerson as exc:
            order.refund_error = str(exc)[:300]
            order.refund_status = "action_required"
            db.commit()
            log.error("refund of %s needs a person: %s", order_id, exc)
            return order.refund_status
        amount = order.customer_price - Decimal(order.refunded_amount or 0)
        order.refunded_amount = Decimal(order.refunded_amount or 0) + amount
        order.refund_status = "confirmed"
        order.refund_error = None
        order.status = "refunded"
        buyer, vendor = order.buyer_id, order.vendor_id
        db.commit()
    notify(buyer, "order_refunded", "Your payment was returned to your Kinjy balance",
           body=f"{amount} for order {order_id} is on your balance.", link=f"/market/orders/{order_id}")
    notify(vendor, "order_refunded", "A cancelled order was refunded to the buyer",
           link=f"/market/orders/{order_id}")
    return "confirmed"


def _due(db: OrmSession) -> tuple[list[str], list[str]]:
    moment = now()
    settle = db.scalars(select(models.Order.id).where(
        models.Order.settlement_status == "pending",
        or_(models.Order.settlement_next_at.is_(None), models.Order.settlement_next_at <= moment),
    ).limit(50)).all()
    refund = db.scalars(select(models.Order.id).where(
        models.Order.refund_status == "pending",
        or_(models.Order.refund_next_at.is_(None), models.Order.refund_next_at <= moment),
    ).limit(50)).all()
    return list(settle), list(refund)


def drain_once() -> int:
    with SessionLocal() as db:
        settle, refund = _due(db)
    for order_id in settle:
        run_settlement(order_id)
    for order_id in refund:
        run_refund(order_id)
    return len(settle) + len(refund)


async def worker_loop() -> None:
    from starlette.concurrency import run_in_threadpool

    log.info("settlement worker started (every %ss, %s attempts before a person)", POLL_S, MAX_ATTEMPTS)
    while True:
        try:
            await run_in_threadpool(drain_once)
        except Exception as exc:  # the loop must outlive one bad pass
            log.error("settlement worker: %s", exc)
        await asyncio.sleep(POLL_S)


def start_worker() -> None:
    if WORKER_ENABLED:
        asyncio.get_event_loop().create_task(worker_loop())
