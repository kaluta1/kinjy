from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "payment"
AMOUNT = Numeric(18, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PaymentIntent(Base):
    """A payment being collected on one of the rails."""

    __tablename__ = "payment_intents"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pmt"))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    rail: Mapped[str] = mapped_column(String(20), default="soncopay")  # kalutapay|soncopay|mock (test); nowpayments|mangopay on old rows only
    purpose: Mapped[str] = mapped_column(String(40))  # order|subscription|ad_credit|kyc_fee|tribute
    reference: Mapped[str | None] = mapped_column(String(80), index=True)

    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    pay_currency: Mapped[str | None] = mapped_column(String(20))
    pay_address: Mapped[str | None] = mapped_column(String(255))

    external_id: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting")
    # waiting|confirming|confirmed|sending|partially_paid|finished|failed|refunded|expired
    raw_payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Referral pool: when the units (and the revenue) of this settled payment
    # were recorded. Null on a settled one: the reconciler still owes it.
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Kaluta Pay: whether the split of this payment (what stays in escrow for
    # members) was set. "pending" until it is; the reconciler retries.
    split_status: Mapped[str | None] = mapped_column(String(20))


class PayoutDestination(Base):
    """Where a member wants to be paid. One of the two eligibility gates."""

    __tablename__ = "payout_destinations"
    __table_args__ = (
        UniqueConstraint("user_id", "rail", name="uq_destination_rail"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    rail: Mapped[str] = mapped_column(String(20))  # soncopay; nowpayments|mangopay on old rows only
    # For crypto: a BSC address. Whitelisted on the provider side before use.
    address: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(20), default="usdtbsc")
    label: Mapped[str | None] = mapped_column(String(80))
    whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # The Sonco Pay wallet (rail "soncopay") goes through two checks before a
    # withdrawal may use it: the member confirms it from their e-mail - so a
    # stolen session cannot quietly redirect their money - and then an admin
    # approves it, and which admin is kept. Changing the address starts both
    # checks again.
    status: Mapped[str] = mapped_column(String(20), default="pending_email")
    # pending_email|pending_admin|confirmed|rejected
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    confirm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(40))
    reviewed_by_name: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(300))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KycRecord(Base):
    """KinjyKYC (blueprint §20).

    Payment and verification are deliberately decoupled: paying the $10 fee does
    not make someone verified, and a failed check does not silently consume the
    year they paid for.
    """

    __tablename__ = "kyc_records"
    __table_args__ = {"schema": SCHEMA}

    MAX_ATTEMPTS_PER_YEAR = 3

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    fee_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    fee_payment_id: Mapped[str | None] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Only the *result* is stored on-platform, never the identity documents.
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    # unverified|pending|verified|rejected
    provider_reference: Mapped[str | None] = mapped_column(String(120))
    # The open Kaluta session, so coming back to the page reopens it instead
    # of spending another of the year's attempts.
    verification_url: Mapped[str | None] = mapped_column(String(500))
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FundAllocation(Base):
    """One allocation of a marketplace order's held money (orderfunds.py):
    settlement to the seller and sponsor, or a refund to the buyer.

    ``status``: requested | retrying | confirmed | action_required. Confirmed
    only on the provider's answer (or, for Sonco Pay direct, against Kinjy's own
    merchant balance - ``backing`` says which).
    """

    __tablename__ = "fund_allocations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reference: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    order_id: Mapped[str] = mapped_column(String(40), index=True)
    intent_id: Mapped[str | None] = mapped_column(String(40), index=True)
    rail: Mapped[str | None] = mapped_column(String(20))
    purpose: Mapped[str] = mapped_column(String(20))  # settlement | refund
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(20), default="requested", index=True)
    backing: Mapped[str | None] = mapped_column(String(40))
    provider_refs: Mapped[str | None] = mapped_column(String(500))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Refund(Base):
    """Money instructed back to a buyer out of custody.

    A row exists even when the rail refuses, with the reason on it. A refund
    that failed silently looks exactly like a refund that was never asked for,
    and the buyer is the one who finds out.
    """

    __tablename__ = "refunds"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rfd"))
    order_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    rail: Mapped[str] = mapped_column(String(30), default="mock")
    custody_ref: Mapped[str | None] = mapped_column(String(120))
    external_id: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending|submitted|finished|failed
    failure_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Withdrawal(Base):
    """A member taking their commissions out, paid by Sonco Pay in USDT on BSC.

    The id is the idempotency key end to end: the ledger reserves the amount
    under it, and Sonco Pay receives it as the payout reference. A retry of the
    same withdrawal is the same payout on both sides, never a second one.
    """

    __tablename__ = "withdrawals"
    __table_args__ = (
        # One withdrawal in flight per member. Two tabs, or a double click,
        # meet here before they can meet the money.
        Index(
            "uq_withdrawal_in_flight",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('reserved', 'submitted')"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("wdr"))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    destination_id: Mapped[int | None] = mapped_column(Integer)
    # What leaves the member's balance, the network fee, and what reaches the
    # wallet. With the fee on the member, net = amount - fee.
    amount: Mapped[Decimal] = mapped_column(AMOUNT)
    fee: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    net: Mapped[Decimal] = mapped_column(AMOUNT)
    asset: Mapped[str] = mapped_column(String(10), default="USDT")
    network: Mapped[str] = mapped_column(String(10), default="BSC")
    address: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="reserved", index=True)
    # reserved|submitted|completed|failed
    soncopay_payout_id: Mapped[str | None] = mapped_column(String(80), index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(120))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
