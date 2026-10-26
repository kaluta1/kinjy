from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "ledger"
AMOUNT = Numeric(18, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Journal(Base):
    """A business event that moved money. Groups the debit/credit lines.

    Journals are append-only: a mistake is corrected by posting a reversing
    journal, never by editing or deleting rows. That is what makes the ledger
    auditable (Singapore accounting requirement in the blueprint).
    """

    __tablename__ = "journals"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_journal_idempotency"),
        Index("ix_journal_kind_created", "kind", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("jrn"))
    kind: Mapped[str] = mapped_column(String(50))  # ad_purchase|creator_revenue|marketplace_order|payout|kyc_fee|reversal
    reference: Mapped[str | None] = mapped_column(String(80), index=True)  # order id, campaign id...
    description: Mapped[str] = mapped_column(Text, default="")
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))

    # Guards against double-posting when a caller retries. Two identical requests
    # with the same key produce one journal, not two.
    idempotency_key: Mapped[str | None] = mapped_column(String(120))

    reverses_journal_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    created_by: Mapped[str | None] = mapped_column(String(40))
    # A journal posted with its member credits held as *pending* (a marketplace
    # sale): when the money behind them was confirmed and they became withdrawable.
    funds_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Entry(Base):
    """One immutable double-entry line. Per journal, sum(debit) == sum(credit)."""

    __tablename__ = "entries"
    __table_args__ = (
        Index("ix_entry_account", "account", "created_at"),
        Index("ix_entry_beneficiary", "beneficiary_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("led"))
    journal_id: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.journals.id"), index=True)
    account: Mapped[str] = mapped_column(String(60))  # see common chart of accounts below
    beneficiary_id: Mapped[str | None] = mapped_column(String(40))
    role: Mapped[str | None] = mapped_column(String(40))   # sponsor, creator, leaders_pool, vendor...
    debit: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    reason: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Wallet(Base):
    """A member's balance. Always reconcilable to the sum of their entries.

    ``available`` is what can enter a payout batch; ``pending`` holds accruals
    still inside a dispute/refund window.
    """

    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("owner_id", "currency", name="uq_wallet_owner_currency"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("wlt"))
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    available: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    pending: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    lifetime_earned: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    lifetime_paid: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Commission(Base):
    """A single commission accrual, kept alongside the ledger for reporting.

    The ledger is the truth; this table exists so "what did I earn from the
    marketplace last month" is one indexed query instead of a ledger scan — and
    so Kinjy Leaders can rank a month by commission without scanning journals.
    """

    __tablename__ = "commissions"
    __table_args__ = (
        Index("ix_commission_member_created", "member_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("com"))
    journal_id: Mapped[str] = mapped_column(String(40), index=True)
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    source_kind: Mapped[str] = mapped_column(String(40))   # ad_purchase|creator_revenue|marketplace_order|service
    source_ref: Mapped[str | None] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(40))   # sponsor | creator
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="accrued")  # accrued|available|paid|reversed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LeaderSnapshot(Base):
    """Monthly Kinjy Leaders snapshot.

    Ranking is on **commission earned during the calendar month**, not on head
    count: a member who introduced one serious buyer outranks one who
    introduced fifty who never transacted. ``share_ratio`` is that member's
    commission over the total commission of the qualifying members only.
    """

    __tablename__ = "leader_snapshots"
    __table_args__ = (
        UniqueConstraint("period", "leader_id", name="uq_leader_period"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    leader_id: Mapped[str] = mapped_column(String(40), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    direct_commissions: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    lsr: Mapped[Decimal] = mapped_column(Numeric(12, 10))   # share of the pool
    pool_amount: Mapped[Decimal] = mapped_column(AMOUNT)
    payout_amount: Mapped[Decimal] = mapped_column(AMOUNT)
    fraud_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PayoutBatch(Base):
    """A batch handed to a payment rail (NowPayments mass payout / Mangopay)."""

    __tablename__ = "payout_batches"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pay"))
    rail: Mapped[str] = mapped_column(String(30), default="nowpayments")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|submitted|verifying|paid|failed
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    total_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    external_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PayoutItem(Base):
    __tablename__ = "payout_items"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.payout_batches.id"), index=True)
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    destination: Mapped[str | None] = mapped_column(String(255))  # wallet address / iban ref
    status: Mapped[str] = mapped_column(String(20), default="pending")
    failure_reason: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PayoutHold(Base):
    """Money taken out of a member's balance for one withdrawal.

    Keyed by the withdrawal it belongs to, so reserving the same withdrawal
    twice - a double click, a retry after a timeout, two tabs - takes the money
    once, and releasing it twice puts it back once. That key is the whole
    point: a withdrawal pays real money, and "the request was sent again" must
    never mean "the member was paid again".

    Reserving moves the amount from ``available`` to ``lifetime_paid`` at once,
    which is what the reconciliation expects of money that has left. If Sonco
    Pay then refuses or fails the payout, releasing moves it back.
    """

    __tablename__ = "payout_holds"
    __table_args__ = {"schema": SCHEMA}

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    status: Mapped[str] = mapped_column(String(20), default="reserved")  # reserved|released
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Chart of accounts ------------------------------------------------------
# Kept as constants so a typo becomes an import error rather than a silently
# orphaned ledger line.
ACC_CASH = "assets:cash"                       # money actually received
# Buyer money sitting with the custodian. It is on our balance sheet as an asset
# with a matching liability, because it is not ours until the buyer confirms —
# booking it as revenue on receipt would overstate every unsettled month.
ACC_CUSTODY = "assets:custodial_cash"
ACC_ESCROW_HELD = "liabilities:escrow_held"
ACC_MEMBER_PAYABLE = "liabilities:member_payable"   # what we owe members
ACC_LEADERS_POOL = "liabilities:leaders_pool"
ACC_VENDOR_PAYABLE = "liabilities:vendor_payable"
ACC_REVENUE_ADS = "revenue:ads"
ACC_REVENUE_MARGIN = "revenue:marketplace_margin"
ACC_REVENUE_SUBSCRIPTION = "revenue:subscription"
ACC_REVENUE_KYC = "revenue:kyc_fee"
ACC_REVENUE_REFERRAL_POOL = "revenue:referral_pool"
ACC_REVENUE_COMMUNITY = "revenue:community_markup"
ACC_EXPENSE_AFFILIATE = "expense:affiliate"
ACC_EXPENSE_CREATOR = "expense:creator_share"
ACC_UNCLAIMED = "revenue:unclaimed_commission"
