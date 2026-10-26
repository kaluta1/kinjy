"""Double-entry posting helpers.

Every function here takes a :class:`common.economy.Split` (which already sums
exactly to its source) and turns it into balanced journal lines, credits the
member wallets, and records the reporting rows — in one transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from common.economy import Split
from common.ids import new_id
from common.notify import notify

import models


def get_wallet(db: OrmSession, owner_id: str, currency: str = "USD") -> models.Wallet:
    wallet = db.scalar(
        select(models.Wallet).where(
            models.Wallet.owner_id == owner_id, models.Wallet.currency == currency
        )
    )
    if wallet is None:
        wallet = models.Wallet(id=new_id("wlt"), owner_id=owner_id, currency=currency)
        db.add(wallet)
        db.flush()
    return wallet


def find_existing(db: OrmSession, idempotency_key: str | None) -> models.Journal | None:
    if not idempotency_key:
        return None
    return db.scalar(
        select(models.Journal).where(models.Journal.idempotency_key == idempotency_key)
    )


def post_split(
    db: OrmSession,
    *,
    kind: str,
    split: Split,
    revenue_account: str,
    reference: str | None = None,
    description: str = "",
    currency: str = "USD",
    idempotency_key: str | None = None,
    created_by: str | None = None,
    credit_wallets: bool = True,
    pending: bool = False,
) -> models.Journal:
    """Post a distribution.

    With ``pending``, members are credited to their wallet's *pending* balance:
    owed, but not withdrawable until the money behind it is confirmed with the
    payment provider (``release_pending``).

    Shape of the journal, for a $100 ad purchase:

        debit  assets:cash                 100.00     (we received the money)
        credit revenue:ads                 100.00

        debit  expense:affiliate            10.00     (the affiliate pool)
        credit liabilities:member_payable   10.00     (one line per beneficiary)

    Members are credited to a *liability*, not to cash: an accrued commission is
    money we owe, and it only leaves the balance sheet when a payout settles.
    """
    existing = find_existing(db, idempotency_key)
    if existing is not None:
        return existing

    journal = models.Journal(
        id=new_id("jrn"),
        kind=kind,
        reference=reference,
        description=description,
        currency=currency,
        amount=Decimal(split.source),
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    db.add(journal)
    db.flush()

    def line(**kwargs) -> None:
        db.add(models.Entry(id=new_id("led"), journal_id=journal.id, currency=currency, **kwargs))

    # 1. The money coming in.
    line(account=models.ACC_CASH, debit=Decimal(split.source), reason=f"{kind}_received")
    line(account=revenue_account, credit=Decimal(split.source), reason=f"{kind}_revenue")

    # 2. What we owe out of it.
    for share in split.shares:
        if share.amount == 0:
            continue

        if share.role == "platform":
            # Platform keeps it: no liability, but unclaimed affiliate money is
            # booked to its own revenue account so it stays visible in reports.
            if share.reason.startswith("unclaimed_"):
                line(
                    account=revenue_account,
                    debit=share.amount,
                    reason=share.reason,
                )
                line(
                    account=models.ACC_UNCLAIMED,
                    credit=share.amount,
                    reason=share.reason,
                )
            continue

        expense = (
            models.ACC_EXPENSE_CREATOR if share.role == "creator" else models.ACC_EXPENSE_AFFILIATE
        )
        liability = (
            models.ACC_LEADERS_POOL if share.role == "leaders_pool" else models.ACC_MEMBER_PAYABLE
        )

        line(account=expense, debit=share.amount, reason=share.reason)
        line(
            account=liability,
            credit=share.amount,
            beneficiary_id=share.beneficiary,
            role=share.role,
            reason=share.reason,
        )

        if share.role == "leaders_pool":
            continue

        db.add(
            models.Commission(
                id=new_id("com"),
                journal_id=journal.id,
                member_id=share.beneficiary,
                source_kind=kind,
                source_ref=reference,
                role=share.role,
                amount=share.amount,
                currency=currency,
            )
        )
        if share.role == "sponsor" and share.amount > 0:
            _tell_sponsor(share.beneficiary, share.amount, kind, created_by)

        if credit_wallets:
            wallet = get_wallet(db, share.beneficiary, currency)
            if pending:
                wallet.pending += share.amount
            else:
                wallet.available += share.amount
            wallet.lifetime_earned += share.amount

    assert_balanced(db, journal.id)
    return journal


# Accounts whose credits are what members are owed, and so what a wallet holds.
PAYABLE_ACCOUNTS = ("liabilities:member_payable", "liabilities:vendor_payable")


def release_pending(db: OrmSession, journal: models.Journal) -> bool:
    """Make a journal's member credits withdrawable, once.

    Called when the money behind them is confirmed with the payment provider.
    Returns False when it was already done (a retry changes nothing).
    """
    if journal.funds_released_at is not None:
        return False
    rows = db.scalars(select(models.Entry).where(
        models.Entry.journal_id == journal.id,
        models.Entry.account.in_(PAYABLE_ACCOUNTS),
        models.Entry.beneficiary_id.is_not(None),
        models.Entry.credit > 0,
    )).all()
    for row in rows:
        wallet = get_wallet(db, row.beneficiary_id, row.currency)
        wallet.pending -= row.credit
        wallet.available += row.credit
    journal.funds_released_at = datetime.now(timezone.utc)
    return True


def assert_balanced(db: OrmSession, journal_id: str) -> None:
    """Refuse to leave an unbalanced journal in the database."""
    rows = db.scalars(select(models.Entry).where(models.Entry.journal_id == journal_id)).all()
    debit = sum((row.debit for row in rows), Decimal("0"))
    credit = sum((row.credit for row in rows), Decimal("0"))
    if debit != credit:
        raise ValueError(f"journal {journal_id} unbalanced: debit {debit} != credit {credit}")


def reverse(db: OrmSession, journal_id: str, *, reason: str, created_by: str | None = None) -> models.Journal:
    """Post the mirror image of a journal. Never mutate the original."""
    original = db.get(models.Journal, journal_id)
    if original is None:
        raise ValueError(f"journal {journal_id} not found")

    reversal = models.Journal(
        id=new_id("jrn"),
        kind="reversal",
        reference=original.reference,
        description=f"Reversal of {journal_id}: {reason}",
        currency=original.currency,
        amount=original.amount,
        reverses_journal_id=journal_id,
        created_by=created_by,
    )
    db.add(reversal)
    db.flush()

    for row in db.scalars(select(models.Entry).where(models.Entry.journal_id == journal_id)).all():
        db.add(
            models.Entry(
                id=new_id("led"),
                journal_id=reversal.id,
                account=row.account,
                beneficiary_id=row.beneficiary_id,
                role=row.role,
                debit=row.credit,
                credit=row.debit,
                currency=row.currency,
                reason=f"reversal:{row.reason}",
            )
        )
        # Claw the accrual back out of the member's wallet.
        if row.credit and row.beneficiary_id and row.account == models.ACC_MEMBER_PAYABLE:
            wallet = get_wallet(db, row.beneficiary_id, row.currency)
            wallet.available -= row.credit
            wallet.lifetime_earned -= row.credit

    for commission in db.scalars(
        select(models.Commission).where(models.Commission.journal_id == journal_id)
    ).all():
        commission.status = "reversed"

    assert_balanced(db, reversal.id)
    return reversal


def refund_to_balance(
    db: OrmSession,
    journal: models.Journal,
    *,
    amount: Decimal,
    member_id: str,
    reason: str,
    idempotency_key: str,
    created_by: str | None = None,
) -> models.Journal:
    """Refund part (or all) of a payment onto the payer's Kinjy balance.

    Every line of the original journal is mirrored in proportion
    (``amount / journal.amount``): the revenue, the sponsor's commission and
    the Leaders share come back by the same fraction, and each clawed-back
    commission leaves the beneficiary's wallet. The cash line is not mirrored
    as cash - nothing has left yet - but as a liability to the payer, which
    they withdraw like any balance. Rounding is absorbed by the revenue line,
    so the journal always balances to the cent.
    """
    existing = find_existing(db, idempotency_key)
    if existing is not None:
        return existing
    original_total = Decimal(journal.amount)
    amount = Decimal(amount).quantize(Decimal("0.01"))
    if amount <= 0 or amount > original_total:
        raise ValueError(f"refund {amount} outside 0..{original_total}")
    ratio = amount / original_total

    refund = models.Journal(
        id=new_id("jrn"),
        kind="refund",
        reference=journal.reference,
        description=f"Refund of {amount} of {journal.id}: {reason}"[:500],
        currency=journal.currency,
        amount=amount,
        reverses_journal_id=journal.id,
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    db.add(refund)
    db.flush()

    lines: list[models.Entry] = []
    cash_seen = False
    for row in db.scalars(select(models.Entry).where(models.Entry.journal_id == journal.id)).all():
        debit = (Decimal(row.credit or 0) * ratio).quantize(Decimal("0.01"))
        credit = (Decimal(row.debit or 0) * ratio).quantize(Decimal("0.01"))
        if row.account == models.ACC_CASH:
            # A sale has a cash line per share (the seller's, Kinjy's): the
            # payer is owed the refunded amount once, whatever their number.
            cash_seen = True
            continue
        if not debit and not credit:
            continue
        lines.append(models.Entry(
            id=new_id("led"), journal_id=refund.id, account=row.account, beneficiary_id=row.beneficiary_id,
            role=row.role, debit=debit, credit=credit, currency=row.currency, reason=f"refund:{row.reason}",
        ))
        if debit and row.beneficiary_id and row.account == models.ACC_MEMBER_PAYABLE:
            wallet = get_wallet(db, row.beneficiary_id, row.currency)
            wallet.available -= debit
            wallet.lifetime_earned -= debit
        elif debit and row.beneficiary_id and row.account == models.ACC_VENDOR_PAYABLE:
            # A seller's (or community owner's) proceeds come back in proportion.
            get_wallet(db, row.beneficiary_id, row.currency).available -= debit
    if cash_seen:
        # Owed back to the payer, not paid out yet.
        lines.append(models.Entry(
            id=new_id("led"), journal_id=refund.id, account=models.ACC_MEMBER_PAYABLE,
            beneficiary_id=member_id, role="refund", debit=Decimal("0"), credit=amount,
            currency=journal.currency, reason="refund_to_balance",
        ))

    gap = sum((e.credit for e in lines), Decimal("0")) - sum((e.debit for e in lines), Decimal("0"))
    if gap:
        revenue = next((e for e in lines if e.account.startswith("revenue:") and e.debit), None)
        if revenue is None:
            raise ValueError("no revenue line to absorb rounding")
        revenue.debit += gap
    for e in lines:
        db.add(e)
    payer = get_wallet(db, member_id, journal.currency)
    payer.available += amount
    db.flush()
    assert_balanced(db, refund.id)
    return refund


# ---------------------------------------------------------------------------
# Custodial escrow
# ---------------------------------------------------------------------------
# Buyer money is held by an authorised financial institution until the buyer
# confirms receipt. On our books that is an asset (cash we can point at, in a
# custodial account) against a matching liability (it is not ours). Recognising
# it as revenue on receipt would overstate every month that has unsettled
# orders in it, and would let a commission be paid on a deal that later refunds.

def post_escrow(
    db: OrmSession,
    *,
    kind: str,
    amount: Decimal,
    reference: str,
    description: str = "",
    currency: str = "USD",
    idempotency_key: str | None = None,
    created_by: str | None = None,
    beneficiary_id: str | None = None,
) -> models.Journal:
    """Move money into or out of custody.

    ``kind`` is ``escrow_hold`` (money arrives with the custodian),
    ``escrow_release`` (the buyer confirmed; custody clears so the settlement
    journal can recognise the revenue) or ``escrow_refund`` (the money goes
    back to the buyer and no revenue is ever recognised).
    """
    if kind not in ("escrow_hold", "escrow_release", "escrow_refund", "escrow_refund_balance"):
        raise ValueError(f"unknown escrow kind {kind}")
    if kind == "escrow_refund_balance" and not beneficiary_id:
        raise ValueError("a refund to a member's balance needs the member")

    existing = find_existing(db, idempotency_key)
    if existing is not None:
        return existing

    value = Decimal(amount)
    journal = models.Journal(
        id=new_id("jrn"),
        kind=kind,
        reference=reference,
        description=description,
        currency=currency,
        amount=value,
        idempotency_key=idempotency_key,
        created_by=created_by,
    )
    db.add(journal)
    db.flush()

    def line(**kwargs) -> None:
        db.add(models.Entry(id=new_id("led"), journal_id=journal.id, currency=currency, **kwargs))

    if kind == "escrow_hold":
        line(account=models.ACC_CUSTODY, debit=value, reason="escrow_funded")
        line(account=models.ACC_ESCROW_HELD, credit=value, reason="escrow_owed_to_parties")
    elif kind == "escrow_refund_balance":
        # The held money now belongs to the buyer, on their Kinjy balance. It is
        # posted only once the payment provider has confirmed the allocation.
        line(account=models.ACC_ESCROW_HELD, debit=value, reason="escrow_refund_to_balance")
        line(account=models.ACC_MEMBER_PAYABLE, credit=value, beneficiary_id=beneficiary_id,
             role="refund", reason="order_refund_payable")
        wallet = get_wallet(db, beneficiary_id, currency)
        wallet.available += value
    else:
        line(account=models.ACC_ESCROW_HELD, debit=value, reason=f"{kind}_cleared")
        line(account=models.ACC_CUSTODY, credit=value, reason=f"{kind}_paid_out")

    assert_balanced(db, journal.id)
    return journal


# What the member the sponsor invited did, in the words of the notification.
_WHAT = {
    "kyc_fee": "paid their verification fee",
    "referral_pool_entry": "took a seat in the referral pool",
    "ad_purchase": "bought advertising",
    "marketplace_order": "completed an order",
    "creator_revenue": "earned creator revenue",
    "service_fee": "paid for a service",
    "community_membership": "joined a paid community",
    "community_ticket": "bought an event ticket",
}


def _tell_sponsor(sponsor_id: str, amount: Decimal, kind: str, member_id: str | None) -> None:
    """A sponsor hears about each commission as it is earned - the member it
    came from and the amount - instead of finding it in a balance later.
    Best effort: a notification must never cost the posting."""
    name = "A member you invited"
    if member_id:
        try:
            import httpx

            response = httpx.get(f"http://auth-service:8000/internal/users/{member_id}", timeout=3)
            if response.status_code == 200:
                name = response.json().get("display_name") or name
        except Exception:
            pass
    notify(
        sponsor_id,
        kind="commission_earned",
        title=f"You earned ${Decimal(amount):.2f}",
        body=f"{name} {_WHAT.get(kind, 'paid on Kinjy')}. Your 20% is in your balance.",
        link="/dashboard?tab=earnings",
    )
