"""Kinjy · ledger-service

The single source of financial truth: immutable double-entry journals, member
wallets, direct affiliate commissions, custodial escrow, the Kinjy Leaders pool
and payout batches.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from common import economy, migrations, settings
from common.auth import AdminUser, CurrentUser
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import models
import posting

log = logging.getLogger("ledger-service")
AUTH_URL = "http://auth-service:8000"

# Columns that changed with the move from the 10-level programme to the direct
# one. create_all cannot alter an existing table, so a live database needs
# these; they are written to be safe to run on every boot, including a fresh one.
MIGRATIONS = [
    f"ALTER TABLE {models.SCHEMA}.leader_snapshots "
    "ADD COLUMN IF NOT EXISTS direct_commissions NUMERIC(18,2) DEFAULT 0",
    # verified_l1_referrals left the model when ranking moved from head count to
    # commission. The column stays so closed months keep their history, but it
    # can no longer be NOT NULL or every new snapshot row would be rejected.
    migrations.column_nullable(models.SCHEMA, "leader_snapshots", "verified_l1_referrals"),
    # Same for the per-entry affiliate level: there are no levels any more.
    migrations.column_nullable(models.SCHEMA, "entries", "level"),
    migrations.column_nullable(models.SCHEMA, "commissions", "level"),
]

app = create_app(
    name="ledger-service",
    schema=models.SCHEMA,
    description="Immutable ledger, wallets, direct commissions, escrow, Kinjy Leaders, payouts.",
    migrations=MIGRATIONS,
)


def _sponsor(user_id: str) -> str | None:
    """Who is paid on this member's activity, per auth-service.

    A failure here must not silently produce a split with no sponsor — that
    would quietly hand the commission to the platform, and afterwards there is
    no way to tell it apart from a genuinely unsponsored member. We fail the
    posting instead and let the caller retry.
    """
    try:
        response = httpx.get(f"{AUTH_URL}/internal/sponsor/{user_id}", timeout=5)
        response.raise_for_status()
        return response.json().get("sponsor_id")
    except Exception as exc:
        log.error("sponsor lookup failed for %s: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Could not resolve the sponsor; posting aborted")


# --- schemas ---------------------------------------------------------------

class AdPurchaseIn(BaseModel):
    advertiser_id: str
    amount: Decimal = Field(gt=0)
    campaign_id: str | None = None
    idempotency_key: str | None = None


class CreatorRevenueIn(BaseModel):
    creator_id: str
    amount: Decimal = Field(gt=0)
    period: str | None = None
    idempotency_key: str | None = None


class MarketplaceOrderIn(BaseModel):
    buyer_id: str
    vendor_id: str
    vendor_price: Decimal = Field(gt=0)
    order_id: str
    idempotency_key: str | None = None


class ServiceFeeIn(BaseModel):
    buyer_id: str
    fee: Decimal = Field(gt=0)
    reference: str | None = None
    idempotency_key: str | None = None


class PoolEntryIn(BaseModel):
    """A member buying a seat in the referral pool."""

    member_id: str
    amount: Decimal = Field(gt=0)
    reference: str | None = None
    idempotency_key: str | None = None


class EscrowIn(BaseModel):
    """Money moving into or out of the custodian's hands."""

    order_id: str
    amount: Decimal = Field(gt=0)
    action: str = Field(pattern="^(hold|release|refund)$")
    description: str = ""
    idempotency_key: str | None = None


class SimulateIn(BaseModel):
    kind: str = Field(pattern="^(ad_purchase|creator_revenue|marketplace_order|service)$")
    amount: Decimal = Field(gt=0)
    sponsor_id: str | None = None
    creator_id: str = "creator"


def _split_out(split: economy.Split) -> dict:
    return {
        "source": str(split.source),
        "basis": str(split.basis),
        "distributed": str(split.distributed),
        "shares": [
            {
                "beneficiary": s.beneficiary,
                "role": s.role,
                "amount": str(s.amount),
                "reason": s.reason,
            }
            for s in split.shares
        ],
    }


def _journal_out(journal: models.Journal, db: OrmSession) -> dict:
    entries = db.scalars(select(models.Entry).where(models.Entry.journal_id == journal.id)).all()
    return {
        "id": journal.id,
        "kind": journal.kind,
        "reference": journal.reference,
        "amount": str(journal.amount),
        "currency": journal.currency,
        "created_at": journal.created_at,
        "entries": [
            {
                "account": e.account,
                "beneficiary_id": e.beneficiary_id,
                "role": e.role,
                "debit": str(e.debit),
                "credit": str(e.credit),
                "reason": e.reason,
            }
            for e in entries
        ],
    }


# ---------------------------------------------------------------------------
# Simulation — no writes. Powers the public "how the economy works" pages.
# ---------------------------------------------------------------------------

@app.post("/ledger/simulate", tags=["simulation"])
def simulate(payload: SimulateIn):
    sponsor = payload.sponsor_id or "sponsor"
    if payload.kind == "ad_purchase":
        split = economy.ad_purchase_split(payload.amount, sponsor)
    elif payload.kind == "creator_revenue":
        split = economy.creator_revenue_split(payload.amount, payload.creator_id, sponsor)
    elif payload.kind == "marketplace_order":
        result = economy.marketplace_order_split(payload.amount, sponsor)
        return {
            "kind": payload.kind,
            "pricing": {k: str(v) for k, v in result["pricing"].items()},
            "margin_split": _split_out(result["margin_split"]),
        }
    else:
        split = economy.service_affiliate_split(payload.amount, sponsor)
    return {"kind": payload.kind, **_split_out(split)}


@app.get("/ledger/rules", tags=["simulation"])
def rules():
    """The economy as configured — so the frontend never hard-codes a percentage."""
    commission = settings.SPONSOR_COMMISSION_PCT
    leaders = settings.LEADERS_POOL_PCT
    example = economy.marketplace_pricing(Decimal("100"))
    return {
        "programme": "direct",
        "levels": 1,
        "sponsor_commission_pct": str(commission),
        "leaders_pool_pct": str(leaders),
        "platform_pct": str(Decimal("100") - commission - leaders),
        "basis": "Kinjy revenue on the transaction, never the seller's price.",
        "marketplace": {
            "markup_pct": str(settings.MARKETPLACE_MARKUP_PCT),
            "note": (
                "Where Kinjy acts as an agent between a buyer and a seller, the customer "
                "price is the seller's price plus the markup. The markup is Kinjy's revenue, "
                "and the sponsor's commission is paid out of it."
            ),
            "worked_example": {
                "seller_price": str(example["vendor_price"]),
                "customer_price": str(example["customer_price"]),
                "kinjy_revenue": str(example["margin"]),
                "sponsor_commission": str(economy.pct_of(example["margin"], commission)),
                "leaders_pool": str(economy.pct_of(example["margin"], leaders)),
            },
        },
        "creator_revenue": {
            "creator_pct": str(settings.CREATOR_SHARE_PCT),
            "note": "The sponsor commission is computed on what Kinjy retains after the creator share.",
        },
        "leaders": {
            "pool_pct": str(leaders),
            "pool_size": settings.LEADERS_POOL_SIZE,
            "ranked_by": "direct commission earned during the calendar month",
            "allocation": "member commission / total commission of the qualifying members",
        },
        "referral_pool": {
            "entry_price": str(settings.REFERRAL_POOL_ENTRY_USD),
            "cap": settings.REFERRAL_POOL_CAP,
        },
        "escrow": {
            "custodian": settings.CUSTODIAN_NAME,
            "licence": settings.CUSTODIAN_LICENCE,
            "auto_release_days": settings.ESCROW_AUTO_RELEASE_DAYS,
            "note": "Buyer funds are released to the seller only once the buyer confirms receipt.",
        },
        "payout": {
            "threshold_usd": str(settings.PAYOUT_THRESHOLD_USD),
            "requires": ["kyc_verified", "wallet_on_file"],
        },
    }


# ---------------------------------------------------------------------------
# Postings — internal, called by commerce/creator/payment services
# ---------------------------------------------------------------------------

@app.post("/internal/post/ad-purchase", tags=["postings"])
def post_ad_purchase(payload: AdPurchaseIn, db: OrmSession = Depends(get_db)):
    split = economy.ad_purchase_split(payload.amount, _sponsor(payload.advertiser_id))
    journal = posting.post_split(
        db,
        kind="ad_purchase",
        split=split,
        revenue_account=models.ACC_REVENUE_ADS,
        reference=payload.campaign_id,
        description=f"Ad purchase by {payload.advertiser_id}",
        idempotency_key=payload.idempotency_key,
        created_by=payload.advertiser_id,
    )
    db.commit()
    return _journal_out(journal, db)


@app.post("/internal/post/creator-revenue", tags=["postings"])
def post_creator_revenue(payload: CreatorRevenueIn, db: OrmSession = Depends(get_db)):
    split = economy.creator_revenue_split(payload.amount, payload.creator_id, _sponsor(payload.creator_id))
    journal = posting.post_split(
        db,
        kind="creator_revenue",
        split=split,
        revenue_account=models.ACC_REVENUE_ADS,
        reference=payload.period,
        description=f"Creator revenue share for {payload.creator_id}",
        idempotency_key=payload.idempotency_key,
        created_by=payload.creator_id,
    )
    db.commit()
    return _journal_out(journal, db)


@app.post("/internal/post/marketplace-order", tags=["postings"])
def post_marketplace_order(payload: MarketplaceOrderIn, db: OrmSession = Depends(get_db)):
    """The seller is paid their asking price; the commission comes out of the markup."""
    pricing = economy.marketplace_pricing(payload.vendor_price)
    split = economy.service_affiliate_split(pricing["margin"], _sponsor(payload.buyer_id))

    journal = posting.post_split(
        db,
        kind="marketplace_order",
        split=split,
        revenue_account=models.ACC_REVENUE_MARGIN,
        reference=payload.order_id,
        description=f"Order {payload.order_id}",
        idempotency_key=payload.idempotency_key,
        created_by=payload.buyer_id,
    )

    # The seller's own money is a separate pair of lines: it was never Kinjy's
    # revenue, so it must not touch the markup split — and cannot fund a commission.
    db.add(
        models.Entry(
            id=new_id("led"),
            journal_id=journal.id,
            account=models.ACC_CASH,
            debit=pricing["vendor_price"],
            reason="vendor_proceeds_received",
        )
    )
    db.add(
        models.Entry(
            id=new_id("led"),
            journal_id=journal.id,
            account=models.ACC_VENDOR_PAYABLE,
            credit=pricing["vendor_price"],
            beneficiary_id=payload.vendor_id,
            role="vendor",
            reason="vendor_proceeds_payable",
        )
    )
    wallet = posting.get_wallet(db, payload.vendor_id)
    wallet.available += pricing["vendor_price"]
    wallet.lifetime_earned += pricing["vendor_price"]

    journal.amount = pricing["customer_price"]
    posting.assert_balanced(db, journal.id)
    db.commit()
    return {"pricing": {k: str(v) for k, v in pricing.items()}, "journal": _journal_out(journal, db)}


@app.post("/internal/post/service-fee", tags=["postings"])
def post_service_fee(payload: ServiceFeeIn, db: OrmSession = Depends(get_db)):
    split = economy.service_affiliate_split(payload.fee, _sponsor(payload.buyer_id))
    journal = posting.post_split(
        db,
        kind="service",
        split=split,
        revenue_account=models.ACC_REVENUE_MARGIN,
        reference=payload.reference,
        idempotency_key=payload.idempotency_key,
        created_by=payload.buyer_id,
    )
    db.commit()
    return _journal_out(journal, db)


@app.post("/internal/post/referral-pool-entry", tags=["postings"])
def post_referral_pool_entry(payload: PoolEntryIn, db: OrmSession = Depends(get_db)):
    """A referral-pool seat is bought.

    The entry fee is Kinjy revenue like any other, so the buyer's own sponsor is
    paid their 20% on it and the Leaders pool takes its 5%. Nothing about the
    pool exempts it from the programme it feeds.
    """
    split = economy.revenue_split(payload.amount, _sponsor(payload.member_id), kind="referral_pool")
    journal = posting.post_split(
        db,
        kind="referral_pool_entry",
        split=split,
        revenue_account=models.ACC_REVENUE_REFERRAL_POOL,
        reference=payload.reference,
        description=f"Referral pool seat for {payload.member_id}",
        idempotency_key=payload.idempotency_key,
        created_by=payload.member_id,
    )
    db.commit()
    return _journal_out(journal, db)


@app.post("/internal/post/escrow", tags=["postings"])
def post_escrow(payload: EscrowIn, db: OrmSession = Depends(get_db)):
    """Record custody of buyer money, or its release/refund.

    Deliberately separate from settlement: holding money is not earning it. The
    settlement journal — the one that recognises revenue and accrues the
    commission — is posted only when the buyer confirms receipt.
    """
    kind = {"hold": "escrow_hold", "release": "escrow_release", "refund": "escrow_refund"}[payload.action]
    try:
        journal = posting.post_escrow(
            db,
            kind=kind,
            amount=payload.amount,
            reference=payload.order_id,
            description=payload.description or f"Escrow {payload.action} for {payload.order_id}",
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return _journal_out(journal, db)


@app.post("/admin/journals/{journal_id}/reverse", tags=["admin"])
def reverse_journal(journal_id: str, reason: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    try:
        reversal = posting.reverse(db, journal_id, reason=reason, created_by=admin.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return _journal_out(reversal, db)


# ---------------------------------------------------------------------------
# Member-facing
# ---------------------------------------------------------------------------

@app.get("/wallet", tags=["wallet"])
def my_wallet(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    wallet = posting.get_wallet(db, principal.user_id)
    db.commit()
    eligible, blocker = economy.is_payout_eligible(
        accrued_usd=wallet.available,
        kyc_verified=principal.kyc_verified,
        wallet_on_file=True,  # payment-service owns the real check; surfaced there
    )
    # Always two decimals. A fresh wallet holds Decimal("0") and a used one
    # Decimal("100.00"), so the raw string flipped between "0" and "0.00"
    # depending on whether the member had ever earned anything.
    return {
        "owner_id": wallet.owner_id,
        "currency": wallet.currency,
        "available": str(economy.money(wallet.available)),
        "pending": str(economy.money(wallet.pending)),
        "lifetime_earned": str(economy.money(wallet.lifetime_earned)),
        "lifetime_paid": str(economy.money(wallet.lifetime_paid)),
        "payout_threshold": str(settings.PAYOUT_THRESHOLD_USD),
        "payout_eligible": eligible,
        "blocked_by": blocker,
    }


@app.get("/commissions", tags=["wallet"])
def my_commissions(
    principal: CurrentUser,
    source_kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    stmt = select(models.Commission).where(models.Commission.member_id == principal.user_id)
    if source_kind:
        stmt = stmt.where(models.Commission.source_kind == source_kind)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Commission.created_at.desc()).limit(min(limit, 200)).offset(offset)
    ).all()

    by_source = db.execute(
        select(models.Commission.source_kind, func.sum(models.Commission.amount))
        .where(
            models.Commission.member_id == principal.user_id,
            models.Commission.status != "reversed",
        )
        .group_by(models.Commission.source_kind)
    ).all()

    return {
        "total": total,
        "by_source": {kind: str(amount) for kind, amount in by_source},
        "items": [
            {
                "id": r.id,
                "source_kind": r.source_kind,
                "source_ref": r.source_ref,
                "role": r.role,
                "amount": str(r.amount),
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Kinjy Leaders
# ---------------------------------------------------------------------------
# 5% of monthly company revenue is set aside as it is earned (every revenue
# split contributes its LEADERS_POOL_PCT to liabilities:leaders_pool) and shared
# at month end between the members with the highest direct commissions.


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    """UTC half-open bounds [start, next month) for a YYYY-MM period."""
    try:
        year, month = (int(part) for part in period.split("-"))
        start = datetime(year, month, 1, tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    following = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=timezone.utc)
    return start, following


def _monthly_commissions(db: OrmSession, period: str) -> dict[str, Decimal]:
    """Direct commission earned per member during the month.

    Reversed accruals are excluded: a commission that was clawed back was never
    earned, and letting it count would let a cancelled order buy a leaderboard
    place.
    """
    start, following = _period_bounds(period)
    rows = db.execute(
        select(models.Commission.member_id, func.sum(models.Commission.amount))
        .where(
            models.Commission.role == "sponsor",
            models.Commission.status != "reversed",
            models.Commission.created_at >= start,
            models.Commission.created_at < following,
        )
        .group_by(models.Commission.member_id)
    ).all()
    return {member_id: Decimal(amount) for member_id, amount in rows}


def _pool_balance(db: OrmSession, period: str | None = None) -> Decimal:
    stmt = select(
        func.coalesce(func.sum(models.Entry.credit), 0) - func.coalesce(func.sum(models.Entry.debit), 0)
    ).where(models.Entry.account == models.ACC_LEADERS_POOL)
    if period:
        start, following = _period_bounds(period)
        stmt = stmt.where(models.Entry.created_at >= start, models.Entry.created_at < following)
    return Decimal(db.scalar(stmt) or 0)


@app.get("/leaders/standings", tags=["kinjy-leaders"])
def leaders_standings(
    period: str | None = None, limit: int = 100, db: OrmSession = Depends(get_db)
):
    """The live board for a month, recomputed on read.

    Deliberately not the snapshot table: members want to see where they stand
    *now*, and a board that only updates once a month is a board nobody opens.
    """
    period = period or datetime.now(timezone.utc).strftime("%Y-%m")
    commissions = _monthly_commissions(db, period)
    pool = _pool_balance(db, period)
    ratios = economy.leader_share_ratios(commissions)
    ranked = sorted(ratios.items(), key=lambda item: -item[1])
    return {
        "period": period,
        "pool_amount": str(pool),
        "pool_pct": str(settings.LEADERS_POOL_PCT),
        "pool_size": settings.LEADERS_POOL_SIZE,
        "qualifying": len(ratios),
        "items": [
            {
                "rank": index + 1,
                "member_id": member_id,
                "direct_commissions": str(commissions[member_id]),
                "share_ratio": str(ratio),
                "projected_payout": str(economy.money(pool * ratio)),
            }
            for index, (member_id, ratio) in enumerate(ranked[: min(limit, 500)])
        ],
    }


@app.get("/leaders/me", tags=["kinjy-leaders"])
def my_leaders_standing(
    principal: CurrentUser, period: str | None = None, db: OrmSession = Depends(get_db)
):
    period = period or datetime.now(timezone.utc).strftime("%Y-%m")
    commissions = _monthly_commissions(db, period)
    ratios = economy.leader_share_ratios(commissions)
    pool = _pool_balance(db, period)
    mine = ratios.get(principal.user_id)
    rank = None
    if mine is not None:
        ordered = sorted(ratios.items(), key=lambda item: -item[1])
        rank = next(i + 1 for i, (uid, _) in enumerate(ordered) if uid == principal.user_id)
    return {
        "period": period,
        "direct_commissions": str(commissions.get(principal.user_id, Decimal("0"))),
        "qualifying": mine is not None,
        "rank": rank,
        "share_ratio": str(mine) if mine is not None else None,
        "projected_payout": str(economy.money(pool * mine)) if mine is not None else "0.00",
        "pool_amount": str(pool),
        "pool_size": settings.LEADERS_POOL_SIZE,
    }


@app.post("/admin/leaders-pool/snapshot", tags=["kinjy-leaders"])
def leaders_snapshot(period: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Freeze the month's ranking. Does not pay — a fraud review runs first."""
    commissions = _monthly_commissions(db, period)
    pool_balance = _pool_balance(db, period)
    split = economy.distribute_leaders_pool(pool_balance, commissions)
    ratios = economy.leader_share_ratios(commissions)

    db.query(models.LeaderSnapshot).filter(models.LeaderSnapshot.period == period).delete()
    rank = 0
    for share in split.shares:
        if share.role != "leaders_payout":
            continue
        rank += 1
        db.add(
            models.LeaderSnapshot(
                period=period,
                leader_id=share.beneficiary,
                rank=rank,
                direct_commissions=commissions.get(share.beneficiary, Decimal("0")),
                lsr=ratios.get(share.beneficiary, Decimal("0")),
                pool_amount=pool_balance,
                payout_amount=share.amount,
            )
        )
    db.commit()
    return {
        "period": period,
        "pool_amount": str(pool_balance),
        "leaders": rank,
        "pool_size_cap": settings.LEADERS_POOL_SIZE,
        "note": "Snapshot only — run the fraud review then /admin/leaders-pool/pay to disburse.",
    }


@app.post("/admin/leaders-pool/pay", tags=["kinjy-leaders"])
def leaders_pay(period: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Credit the snapshot to member wallets and clear the pool liability.

    Refuses a snapshot that has not been through fraud review, and refuses to
    pay the same period twice — the snapshot rows carry both flags, so a second
    call is a no-op rather than a double payout.
    """
    rows = db.scalars(
        select(models.LeaderSnapshot).where(models.LeaderSnapshot.period == period)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No snapshot for {period}; run the snapshot first")
    unreviewed = [r for r in rows if not r.fraud_reviewed]
    if unreviewed:
        raise HTTPException(
            status_code=409,
            detail=f"{len(unreviewed)} of {len(rows)} rows have not been fraud-reviewed",
        )
    if all(r.paid for r in rows):
        return {"period": period, "paid": 0, "note": "already paid"}

    journal = models.Journal(
        id=new_id("jrn"),
        kind="leaders_payout",
        reference=period,
        description=f"Kinjy Leaders payout for {period}",
        amount=sum((r.payout_amount for r in rows), Decimal("0")),
        idempotency_key=f"leaders:{period}",
        created_by=admin.user_id,
    )
    if posting.find_existing(db, journal.idempotency_key) is not None:
        return {"period": period, "paid": 0, "note": "already paid"}
    db.add(journal)
    db.flush()

    paid = 0
    for row in rows:
        if row.paid or row.payout_amount <= 0:
            continue
        db.add(
            models.Entry(
                id=new_id("led"),
                journal_id=journal.id,
                account=models.ACC_LEADERS_POOL,
                debit=row.payout_amount,
                reason="leaders_pool_released",
            )
        )
        db.add(
            models.Entry(
                id=new_id("led"),
                journal_id=journal.id,
                account=models.ACC_MEMBER_PAYABLE,
                credit=row.payout_amount,
                beneficiary_id=row.leader_id,
                role="leaders_payout",
                reason="kinjy_leaders_monthly",
            )
        )
        wallet = posting.get_wallet(db, row.leader_id)
        wallet.available += row.payout_amount
        wallet.lifetime_earned += row.payout_amount
        row.paid = True
        paid += 1

    posting.assert_balanced(db, journal.id)
    db.commit()
    return {"period": period, "paid": paid, "journal_id": journal.id}


@app.post("/admin/leaders-pool/{period}/reviewed", tags=["kinjy-leaders"])
def leaders_mark_reviewed(period: str, _: AdminUser, db: OrmSession = Depends(get_db)):
    """Mark the month's snapshot as fraud-reviewed, unlocking payment."""
    rows = db.scalars(
        select(models.LeaderSnapshot).where(models.LeaderSnapshot.period == period)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No snapshot for {period}")
    for row in rows:
        row.fraud_reviewed = True
    db.commit()
    return {"period": period, "reviewed": len(rows)}


@app.get("/leaders-pool/{period}", tags=["kinjy-leaders"])
def leaders_board(period: str, limit: int = 100, db: OrmSession = Depends(get_db)):
    """The frozen snapshot for a closed month."""
    rows = db.scalars(
        select(models.LeaderSnapshot)
        .where(models.LeaderSnapshot.period == period)
        .order_by(models.LeaderSnapshot.rank)
        .limit(min(limit, 500))
    ).all()
    return {
        "period": period,
        "items": [
            {
                "rank": r.rank,
                "leader_id": r.leader_id,
                "direct_commissions": str(r.direct_commissions),
                "share_ratio": str(r.lsr),
                "payout_amount": str(r.payout_amount),
                "fraud_reviewed": r.fraud_reviewed,
                "paid": r.paid,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Reporting / reconciliation
# ---------------------------------------------------------------------------

@app.get("/admin/trial-balance", tags=["admin"])
def trial_balance(_: AdminUser, db: OrmSession = Depends(get_db)):
    """Every account with its balance. Total debit must equal total credit —
    if it doesn't, something bypassed :func:`posting.post_split`."""
    rows = db.execute(
        select(
            models.Entry.account,
            func.coalesce(func.sum(models.Entry.debit), 0),
            func.coalesce(func.sum(models.Entry.credit), 0),
        ).group_by(models.Entry.account)
    ).all()
    accounts = [
        {"account": acc, "debit": str(debit), "credit": str(credit), "balance": str(debit - credit)}
        for acc, debit, credit in rows
    ]
    total_debit = sum((Decimal(a["debit"]) for a in accounts), Decimal("0"))
    total_credit = sum((Decimal(a["credit"]) for a in accounts), Decimal("0"))
    return {
        "accounts": accounts,
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        "balanced": total_debit == total_credit,
    }


@app.get("/admin/reconcile", tags=["admin"])
def reconcile(_: AdminUser, db: OrmSession = Depends(get_db)):
    """Wallets vs ledger. Any drift here is a bug, not a rounding artefact."""
    ledger_rows = db.execute(
        select(
            models.Entry.beneficiary_id,
            func.coalesce(func.sum(models.Entry.credit), 0) - func.coalesce(func.sum(models.Entry.debit), 0),
        )
        .where(
            models.Entry.beneficiary_id.is_not(None),
            models.Entry.account.in_([models.ACC_MEMBER_PAYABLE, models.ACC_VENDOR_PAYABLE]),
        )
        .group_by(models.Entry.beneficiary_id)
    ).all()
    ledger_map = {uid: Decimal(balance) for uid, balance in ledger_rows}

    drift = []
    for wallet in db.scalars(select(models.Wallet)).all():
        expected = ledger_map.get(wallet.owner_id, Decimal("0")) - wallet.lifetime_paid
        if expected != wallet.available:
            drift.append(
                {
                    "owner_id": wallet.owner_id,
                    "wallet_available": str(wallet.available),
                    "ledger_expected": str(expected),
                    "delta": str(wallet.available - expected),
                }
            )
    return {"checked": len(ledger_map), "drift_count": len(drift), "drift": drift}


@app.get("/admin/journals", tags=["admin"])
def list_journals(
    _: AdminUser,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    stmt = select(models.Journal).order_by(models.Journal.created_at.desc())
    if kind:
        stmt = stmt.where(models.Journal.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.limit(min(limit, 200)).offset(offset)).all()
    return {"total": total, "items": [_journal_out(r, db) for r in rows]}


@app.get("/internal/wallets/{member_id}", tags=["internal"])
def wallet_of(member_id: str, db: OrmSession = Depends(get_db)):
    """One member's balance, for payment-service to quote a withdrawal on."""
    wallet = posting.get_wallet(db, member_id)
    db.commit()
    return {
        "member_id": member_id,
        "available": str(economy.money(wallet.available)),
        "pending": str(economy.money(wallet.pending)),
        "lifetime_paid": str(economy.money(wallet.lifetime_paid)),
    }


class HoldIn(BaseModel):
    member_id: str
    amount: Decimal = Field(gt=0)
    key: str = Field(min_length=4, max_length=80)


def _locked_wallet(db: OrmSession, member_id: str) -> models.Wallet:
    """The member's wallet, locked until the end of this transaction.

    The lock is what makes the balance check mean something: without it, two
    withdrawals arriving together both read the same balance, both pass, and
    both are paid out of money that was only there once.
    """
    wallet = db.scalar(
        select(models.Wallet)
        .where(models.Wallet.owner_id == member_id, models.Wallet.currency == "USD")
        .with_for_update()
    )
    if wallet is None:
        raise HTTPException(status_code=409, detail="Nothing to withdraw")
    return wallet


@app.post("/internal/payouts/reserve", tags=["internal"])
def reserve_payout(payload: HoldIn, db: OrmSession = Depends(get_db)):
    """Take a withdrawal's amount out of the member's balance, once.

    Called by payment-service before it asks Sonco Pay to send anything, so the
    money has already left the balance by the time it can leave the platform.
    The same key again returns the hold that already exists and moves nothing.
    """
    amount = economy.money(payload.amount)
    existing = db.get(models.PayoutHold, payload.key)
    if existing is not None:
        if existing.member_id != payload.member_id or economy.money(existing.amount) != amount:
            # The same key for a different member or amount is a bug in the
            # caller, and paying it would be a guess.
            raise HTTPException(status_code=409, detail="That withdrawal key is already used for something else")
        return {"key": existing.key, "status": existing.status, "amount": str(existing.amount), "created": False}

    wallet = _locked_wallet(db, payload.member_id)
    # Two requests with the same key both got here; the second waited on the
    # lock while the first took the money. Look again now that it is ours.
    if db.scalar(select(models.PayoutHold.key).where(models.PayoutHold.key == payload.key)):
        db.rollback()
        return reserve_payout(payload, db)
    if economy.money(wallet.available) < amount:
        raise HTTPException(status_code=409, detail="The balance is lower than the amount to withdraw")
    wallet.available = economy.money(wallet.available) - amount
    wallet.lifetime_paid = economy.money(wallet.lifetime_paid) + amount
    db.add(models.PayoutHold(key=payload.key, member_id=payload.member_id, amount=amount))
    db.commit()
    return {
        "key": payload.key,
        "status": "reserved",
        "amount": str(amount),
        "available": str(wallet.available),
        "created": True,
    }


@app.post("/internal/payouts/release", tags=["internal"])
def release_payout(payload: HoldIn, db: OrmSession = Depends(get_db)):
    """Give a withdrawal's amount back, once. For a payout that did not happen.

    Only ever after Sonco Pay has said no, or that it failed - never on a
    timeout, when the money may well be on its way. Releasing a hold that was
    already released, or that never existed, moves nothing.
    """
    hold = db.scalar(
        select(models.PayoutHold).where(models.PayoutHold.key == payload.key).with_for_update()
    )
    if hold is None:
        raise HTTPException(status_code=404, detail="No such withdrawal hold")
    if hold.member_id != payload.member_id or economy.money(hold.amount) != economy.money(payload.amount):
        raise HTTPException(status_code=409, detail="That withdrawal key belongs to something else")
    if hold.status == "released":
        return {"key": hold.key, "status": "released", "released": False}

    wallet = _locked_wallet(db, hold.member_id)
    wallet.available = economy.money(wallet.available) + economy.money(hold.amount)
    wallet.lifetime_paid = economy.money(wallet.lifetime_paid) - economy.money(hold.amount)
    hold.status = "released"
    hold.released_at = datetime.now(timezone.utc)
    db.commit()
    return {"key": hold.key, "status": "released", "released": True, "available": str(wallet.available)}


@app.get("/internal/payout-candidates", tags=["internal"])
def payout_candidates(db: OrmSession = Depends(get_db)):
    """Members at or above the $1 threshold. payment-service applies the KYC and
    wallet-on-file gates before building the batch."""
    rows = db.scalars(
        select(models.Wallet).where(models.Wallet.available >= settings.PAYOUT_THRESHOLD_USD)
    ).all()
    return {
        "threshold": str(settings.PAYOUT_THRESHOLD_USD),
        "count": len(rows),
        "items": [{"member_id": r.owner_id, "amount": str(r.available), "currency": r.currency} for r in rows],
    }
