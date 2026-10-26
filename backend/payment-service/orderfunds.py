"""Marketplace order funds: what happens to the money held for an order.

A marketplace payment is held until the purchase is final (the buyer's
confirmation, or an ebook's first successful download). Then commerce-service
asks this module to *allocate* it:

* **settlement** - the seller's price (and the sponsor's commission) become
  theirs;
* **refund** - before the purchase is final, an order that cannot go ahead
  (the seller cannot supply it, the buyer refused it at handover, delivery
  failed, an operational correction) goes back to the buyer's Kinjy balance.

What "allocate" means depends on the rail the buyer paid through:

* **Kaluta Pay** holds the order whole in Kinjy's escrow there (a line of the
  ``marketplace_orders`` pool, set by ``main._split``). Allocating is a Kaluta
  Pay *distribution* from that pool to the members, each with its own
  idempotent reference. The money stays with Kaluta Pay, now owed to them.
* **Sonco Pay** (direct) settles into Kinjy's own Sonco Pay merchant balance,
  the balance withdrawals are paid from. There is no hold at the provider to
  release, so allocation is confirmed against that balance and said so.
* The **test rail** (development machines only) confirms with backing
  ``test_rail``. In production it is refused.
* Anything else - including payments taken through the removed Mangopay and
  NowPayments rails - is refused and needs a person.

Every attempt writes a :class:`models.FundAllocation` row, so what was asked,
what the provider answered and when is on record. Nothing here ever reports a
success the provider did not give.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from common import economy

import kalutapay
import models
import rails

log = logging.getLogger("payment-service.orderfunds")

POOL = "marketplace_orders"
CATEGORIES = {
    # code: (label, holding, description)
    POOL: ("Marketplace orders held", "pool", "Buyer payments held until the purchase is final."),
    "marketplace_sales": ("Marketplace sales", "member", "A seller's price on a final marketplace purchase."),
    "marketplace_refunds": ("Marketplace refunds", "member", "A buyer's money returned before the purchase was final."),
}
# Which Kaluta Pay member category each kind of allocation becomes.
CATEGORY_FOR = {"seller": "marketplace_sales", "commission": "commission", "refund": "marketplace_refunds"}

_categories_ready = False


class Refused(Exception):
    """The allocation cannot be made as things stand; a person has to look."""


class Retry(Exception):
    """Not now (a provider did not answer, held funds not confirmed yet)."""


def ensure_categories() -> None:
    """The Kaluta Pay escrow categories this module uses, created once if missing."""
    global _categories_ready
    if _categories_ready:
        return
    try:
        have = {c["code"] for c in kalutapay.list_categories()}
        for code, (label, holding, description) in CATEGORIES.items():
            if code not in have:
                kalutapay.create_category(code, label, holding, description)
    except kalutapay.KalutaPayError as exc:
        if exc.status == 409:  # created meanwhile by another worker
            pass
        elif exc.definite:
            raise Refused(f"Kaluta Pay refused the escrow categories: {exc}") from exc
        else:
            raise Retry(f"Kaluta Pay did not answer: {exc}") from exc
    _categories_ready = True


def _record(db: OrmSession, *, reference: str, order_id: str, intent: models.PaymentIntent | None,
            purpose: str, total: Decimal) -> models.FundAllocation:
    row = db.scalar(select(models.FundAllocation).where(models.FundAllocation.reference == reference))
    if row is None:
        row = models.FundAllocation(
            reference=reference, order_id=order_id, intent_id=intent.id if intent else None,
            rail=intent.rail if intent else None, purpose=purpose, amount=total, status="requested",
        )
        db.add(row)
        db.flush()
    return row


def allocate(
    db: OrmSession,
    *,
    order_id: str,
    custody_ref: str | None,
    purpose: str,
    reference: str,
    allocations: list[dict],
) -> models.FundAllocation:
    """Allocate an order's held money. Returns the confirmed record.

    Raises :class:`Retry` (try again later) or :class:`Refused` (needs a
    person). Idempotent on ``reference``: a confirmed allocation is returned as
    it is, and Kaluta Pay distributions are idempotent on their own references.
    """
    intent = db.get(models.PaymentIntent, custody_ref) if custody_ref else None
    total = sum((economy.money(Decimal(str(a["amount"]))) for a in allocations), Decimal("0"))
    row = _record(db, reference=reference, order_id=order_id, intent=intent, purpose=purpose, total=total)
    if row.status == "confirmed":
        return row
    row.attempts = (row.attempts or 0) + 1

    def fail(message: str, *, retry: bool):
        row.status = "retrying" if retry else "action_required"
        row.error = message[:300]
        db.commit()
        raise (Retry if retry else Refused)(message)

    if intent is None or intent.settled_at is None:
        fail("The payment for this order is not confirmed.", retry=False)
    if intent.purpose != "order" or intent.reference != order_id:
        fail("This payment is not this order's.", retry=False)
    if any(Decimal(str(a["amount"])) <= 0 for a in allocations) or total > economy.money(intent.amount):
        fail("The allocation does not fit the payment.", retry=False)

    if intent.rail == "kalutapay":
        if intent.split_status != "done":
            fail("Kaluta Pay has not confirmed the held funds for this payment yet.", retry=True)
        try:
            ensure_categories()
            refs = []
            grouped: dict[str, list[dict]] = defaultdict(list)
            for a in allocations:
                grouped[CATEGORY_FOR[a["kind"]]].append(
                    {"member": {"external_id": a["member_id"]}, "amount": str(economy.money(Decimal(str(a["amount"]))))}
                )
            for category, members in grouped.items():
                done = kalutapay.create_distribution(
                    pool=POOL, category=category, reference=f"{reference}:{category}"[:120],
                    allocations=members, label=f"Kinjy order {order_id}",
                )
                refs.append(str(done.get("id") or done.get("reference") or f"{reference}:{category}"))
        except (Retry, Refused) as exc:
            fail(str(exc), retry=isinstance(exc, Retry))
        except kalutapay.KalutaPayError as exc:
            fail(f"Kaluta Pay: {exc}", retry=not exc.definite)
        row.backing = "kalutapay_escrow"
        row.provider_refs = ",".join(refs)[:500]
    elif intent.rail == "soncopay":
        # Settled into Kinjy's own Sonco Pay merchant balance: nothing is held at
        # the provider, and that balance is what withdrawals are paid from.
        row.backing = "soncopay_merchant_balance"
    elif intent.rail == "mock" and rails.test_rail_allowed():
        row.backing = "test_rail"
    else:
        fail(f"Payments through {intent.rail} cannot be allocated automatically.", retry=False)

    row.status = "confirmed"
    row.error = None
    row.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    log.info("order %s: %s allocation %s confirmed (%s)", order_id, purpose, reference, row.backing)
    return row
