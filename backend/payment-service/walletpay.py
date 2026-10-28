"""Paying a marketplace order from the member's Kinjy balance.

A member with money on Kinjy (commissions, a sale, a refund) used to have to
withdraw it and pay it back in to buy something. This pays the order straight
from the balance instead. There is no second balance: what the member holds is
what the ledger says Kinjy owes them, and paying with it settles part of that
debt (ledger ``/internal/wallet/pay``). From there the order goes exactly the
way a card payment goes - escrow opened by commerce-service, the seller paid
when the purchase is final, a cancelled order refunded to the balance - with
``wallet`` as its rail.

Order of the steps, each safe to run again:

1. a payment intent, one per order (a unique index), so a double click or two
   tabs are the same payment;
2. the balance debited in the ledger under the order's key - once, under the
   wallet lock withdrawals also take, so the same money cannot be spent and
   withdrawn, or spent twice;
3. commerce-service told the order is funded, with this payment as its
   custody reference.

If step 3 does not answer, the payment stays ``processing`` and the
reconciler finishes it. If the order turns out to be paid another way, or no
longer payable, the debit is given back by a compensating journal and the
payment is ``returned``. The client never sends an amount, a balance or a
member: all three are the server's.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from anyio import from_thread
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from common import agefeatures, economy, events, settings
from common.auth import AdminUser, CurrentUser
from common.database import SessionLocal, get_db
from common.ids import new_id

import models

log = logging.getLogger("payment-service.walletpay")
router = APIRouter()

LEDGER_URL = "http://ledger-service:8000"
COMMERCE_URL = "http://commerce-service:8000"
RAIL = "wallet"


class Pending(Exception):
    """A service did not answer; the payment stays processing and is retried."""


def _key(order_id: str) -> str:
    return f"order:{order_id}"


def _quote(order_id: str) -> dict | None:
    try:
        response = httpx.get(f"{COMMERCE_URL}/internal/orders/{order_id}/quote", timeout=6)
    except httpx.HTTPError as exc:
        raise Pending(f"commerce did not answer: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise Pending(f"commerce answered {response.status_code}")
    return response.json()


def _available(user_id: str) -> str:
    try:
        response = httpx.get(f"{LEDGER_URL}/internal/wallets/{user_id}", timeout=6)
        response.raise_for_status()
        return response.json()["available"]
    except Exception as exc:
        log.error("balance of %s unavailable: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Your balance is unavailable right now. Try again.")


def _payable_order(order_id: str, user_id: str) -> dict:
    """The order, if this member may pay it now; the price is the server's."""
    try:
        quote = _quote(order_id)
    except Pending:
        raise HTTPException(status_code=503, detail="The marketplace did not answer. Try again.")
    if quote is None or quote["buyer_id"] != user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    if quote["status"] != "pending":
        raise HTTPException(status_code=409, detail="This order is already paid or no longer payable")
    return quote


def _ledger_body(intent: models.PaymentIntent) -> dict:
    return {"member_id": intent.user_id, "amount": str(intent.amount), "key": intent.external_id,
            "reference": intent.reference, "payment_id": intent.id}


def _give_back(intent: models.PaymentIntent, why: str) -> None:
    """Return the debit by a compensating journal; the intent is ``returned``."""
    try:
        response = httpx.post(f"{LEDGER_URL}/internal/wallet/pay/reverse", json=_ledger_body(intent), timeout=10)
    except httpx.HTTPError as exc:
        raise Pending(f"ledger did not answer the reversal: {exc}") from exc
    if response.status_code >= 300:
        raise Pending(f"ledger refused the reversal: {response.text[:200]}")
    intent.status = "returned"
    intent.raw_payload = json.dumps({"returned": why, "ledger": response.json()})
    log.warning("wallet payment %s returned: %s", intent.id, why)


def _advance(intent: models.PaymentIntent, db: OrmSession) -> str:
    """Take one processing payment as far as it can go. Returns its status.

    The intent row is locked by the caller, so two requests (or a request and
    the reconciler) advance it one at a time.
    """
    if intent.status != "processing":
        return intent.status
    try:
        paid = httpx.post(f"{LEDGER_URL}/internal/wallet/pay", json=_ledger_body(intent), timeout=10)
    except httpx.HTTPError:
        return "processing"
    if paid.status_code == 409:
        intent.status = "failed"
        insufficient = "insufficient_funds" in paid.text
        intent.raw_payload = json.dumps({"failed": "insufficient_funds" if insufficient else paid.text[:200]})
        db.commit()
        return "insufficient_funds" if insufficient else "failed"
    if paid.status_code >= 300:
        return "processing"
    debit = paid.json()

    try:
        funded = httpx.post(
            f"{COMMERCE_URL}/internal/orders/{intent.reference}/funded",
            params={"custody_ref": intent.id, "amount": str(intent.amount), "rail": RAIL},
            timeout=10,
        )
        if funded.status_code >= 500:
            return "processing"
        quote = _quote(intent.reference)
        if funded.status_code < 300 and quote is not None and quote.get("custody_ref") == intent.id:
            intent.status = "finished"
            intent.settled_at = datetime.now(timezone.utc)
            intent.fulfilled_at = intent.settled_at
            intent.raw_payload = json.dumps({"ledger_journal_id": debit.get("journal_id")})
            db.commit()
            return "finished"
        # Paid another way meanwhile, or not payable any more: the money goes back.
        _give_back(intent, "order paid another way or no longer payable"
                   if funded.status_code < 300 else funded.text[:200])
        db.commit()
        return "returned"
    except (httpx.HTTPError, Pending) as exc:
        log.warning("wallet payment %s waits: %s", intent.id, exc)
        db.rollback()
        return "processing"


def _out(intent: models.PaymentIntent, status: str | None = None) -> dict:
    status = status or intent.status
    return {
        "payment_id": intent.id,
        "rail": RAIL,
        "order_id": intent.reference,
        "amount": str(intent.amount),
        "currency": intent.currency,
        "status": "paid" if status == "finished" else status,
        "paid_at": intent.settled_at,
    }


@router.get("/payments/wallet/quote", tags=["wallet-pay"])
def wallet_quote(order_id: str, principal: CurrentUser):
    """What paying this order from the balance would take, for the confirmation step."""
    if not settings.WALLET_PAY_ENABLED:
        return {"enabled": False}
    quote = _payable_order(order_id, principal.user_id)
    available = _available(principal.user_id)
    price = economy.money(quote["customer_price"])
    return {
        "enabled": True,
        "order_id": order_id,
        "amount": str(price),
        "currency": quote.get("currency") or "USD",
        "available": available,
        "enough": economy.money(available) >= price,
    }


class WalletPayIn(BaseModel):
    order_id: str = Field(min_length=4, max_length=80)


@router.post("/payments/wallet/pay", tags=["wallet-pay"])
def wallet_pay(payload: WalletPayIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Pay one marketplace order, in full, from the member's available balance.

    A plain (threaded) handler, not ``async``: it waits on a row lock and on
    other services, and doing that on the event loop froze the whole service
    the moment two requests for one order arrived together.
    """
    agefeatures.require(principal.user_id, "payments")
    if not settings.WALLET_PAY_ENABLED:
        raise HTTPException(status_code=404, detail="Paying from your balance is not available")
    key = _key(payload.order_id)

    lock = select(models.PaymentIntent).where(
        models.PaymentIntent.rail == RAIL, models.PaymentIntent.external_id == key).with_for_update()
    intent = db.scalar(lock)
    if intent is not None and intent.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    if intent is not None and intent.status == "finished":
        return _out(intent)  # paid already: the same answer, nothing taken twice
    if intent is None or intent.status in ("failed", "returned"):
        quote = _payable_order(payload.order_id, principal.user_id)
        price = economy.money(quote["customer_price"])
        if intent is None:
            intent = models.PaymentIntent(
                id=new_id("pmt"), user_id=principal.user_id, rail=RAIL, purpose="order",
                reference=payload.order_id, amount=price, currency=quote.get("currency") or "USD",
                external_id=key, status="processing",
            )
            db.add(intent)
            try:
                db.commit()
            except IntegrityError:
                # Another request for the same order created it first.
                db.rollback()
            intent = db.scalar(lock)
            if intent.status == "finished":
                return _out(intent)
        elif intent.status == "returned":
            # Its debit was given back; the ledger key is spent. Not payable this way again.
            raise HTTPException(status_code=409, detail="This order cannot be paid from your balance")
        else:
            # A try that found too little; the balance may have grown since.
            intent.amount = price
            intent.status = "processing"
            db.commit()
            intent = db.scalar(lock)

    status = _advance(intent, db)
    if status == "insufficient_funds":
        raise HTTPException(status_code=409, detail="insufficient_funds")
    if status in ("failed", "returned"):
        raise HTTPException(status_code=409, detail="This order could not be paid from your balance. Nothing was taken.")
    if status == "finished":
        try:
            from_thread.run(events.publish, "payment.settled", {
                "payment_id": intent.id, "user_id": intent.user_id, "purpose": "order", "amount": str(intent.amount)})
        except Exception as exc:  # the payment is made; a lost event must not undo the answer
            log.warning("payment.settled for %s not published: %s", intent.id, exc)
    return _out(intent, status)


@router.get("/admin/payments/wallet", tags=["admin"])
def wallet_payments(_: AdminUser, status: str | None = None, limit: int = 100, db: OrmSession = Depends(get_db)):
    """Balance payments, newest first: which went through, which wait on a
    service (processing), which found too little, and which were given back."""
    stmt = select(models.PaymentIntent).where(models.PaymentIntent.rail == RAIL)
    if status:
        stmt = stmt.where(models.PaymentIntent.status == status)
    rows = db.scalars(stmt.order_by(models.PaymentIntent.created_at.desc()).limit(min(limit, 500))).all()
    return {"items": [
        {**_out(r), "user_id": r.user_id, "created_at": r.created_at, "detail": r.raw_payload} for r in rows
    ]}


def reconcile_once() -> int:
    """Finish balance payments a service did not answer for (processing for over a minute)."""
    with SessionLocal() as db:
        ids = db.scalars(select(models.PaymentIntent.id).where(
            models.PaymentIntent.rail == RAIL, models.PaymentIntent.status == "processing",
            models.PaymentIntent.created_at < datetime.now(timezone.utc) - timedelta(minutes=1),
        ).limit(100)).all()
        for payment_id in ids:
            intent = db.scalar(select(models.PaymentIntent).where(models.PaymentIntent.id == payment_id)
                               .with_for_update(skip_locked=True))
            if intent is None:
                continue
            try:
                _advance(intent, db)
            except Exception as exc:
                db.rollback()
                log.error("wallet payment %s retry failed: %s", payment_id, exc)
            db.commit()
        return len(ids)


async def reconcile_loop() -> None:
    while True:
        await asyncio.sleep(90)
        try:
            await asyncio.to_thread(reconcile_once)
        except Exception as exc:
            log.error("wallet payment reconciler: %s", exc)
