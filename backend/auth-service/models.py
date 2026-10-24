from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from common.database import Base
from common.ids import new_id

SCHEMA = "auth"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """The account. Profile data lives in user-service; this table is the
    credential + lifecycle record only."""

    __tablename__ = "users"
    __table_args__ = (
        Index("ix_auth_users_email_lower", "email"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    handle: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Null for an account whose password has never been changed, including every
    # member imported from DSM - absent is not the same as "changed at import
    # time", and a member asking when they last changed it deserves the
    # difference.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)

    role: Mapped[str] = mapped_column(String(20), default="member")  # member|creator|admin|superadmin
    lang: Mapped[str] = mapped_column(String(5), default="en")
    country: Mapped[str | None] = mapped_column(String(2))

    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    kyc_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    kyc_verified_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Affiliate graph: who sponsored this member. One level, and one level only
    # - the direct affiliate programme pays this member and nobody above them.
    # Set either from a personal invitation link or by a referral-pool draw.
    invited_by: Mapped[str | None] = mapped_column(String(40), index=True)
    referral_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)

    # Lifecycle — self-service deletion (blueprint §4). No justification stored.
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|deactivated|pending_deletion|deleted
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    passkeys: Mapped[list["Passkey"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def has_password(self) -> bool:
        """Whether there is a password to change at all.

        Read by the security screen so it can say so, rather than offering a
        form that can only ever be refused.
        """
        return bool(self.password_hash)


class Session(Base):
    """One logged-in device. Refresh tokens are bound to a session so a single
    device can be revoked from the device manager without touching the others."""

    __tablename__ = "sessions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ses"))
    user_id: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), index=True)
    device_label: Mapped[str | None] = mapped_column(String(120))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")

    @property
    def active(self) -> bool:
        return self.revoked_at is None and self.expires_at > _now()


class Passkey(Base):
    """A WebAuthn credential.

    Blueprint §5: device-level passkeys, *no central biometric database*. What is
    stored here is a public key and a counter — never a fingerprint or a face
    template. The biometric never leaves the user's device.
    """

    __tablename__ = "passkeys"
    __table_args__ = (
        UniqueConstraint("credential_id", name="uq_passkey_credential"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pky"))
    user_id: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[str | None] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(80), default="Passkey")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="passkeys")


class OtpCode(Base):
    """Email one-time codes (verification, passwordless login, deletion confirm)."""

    __tablename__ = "otp_codes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    purpose: Mapped[str] = mapped_column(String(40))  # verify_email|login|delete_account
    code_hash: Mapped[str] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LoginEvent(Base):
    """Login alerts + device management surface (blueprint §5)."""

    __tablename__ = "login_events"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    # password|passkey|otp|refresh|logout|failed|password_change|password_reset
    kind: Mapped[str] = mapped_column(String(30))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ReferralPoolSeat(Base):
    """A paid seat in the referral pool.

    Members buy a seat; sign-ups that arrive with no personal invitation link
    are assigned to a seat at random and the seat holder becomes their sponsor,
    with exactly the same 20% direct commission as a personal invitation.

    The pool closes to new entrants once REFERRAL_POOL_CAP seats are sold. Seats
    already held keep receiving assignments: a seat that stopped working the
    moment the last one sold would make the final purchases worthless.
    """

    __tablename__ = "referral_pool_seats"
    __table_args__ = (
        UniqueConstraint("member_id", name="uq_pool_seat_member"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("seat"))
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    seat_number: Mapped[int] = mapped_column(Integer, index=True)
    price_paid: Mapped[str] = mapped_column(String(20), default="0")
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    payment_ref: Mapped[str | None] = mapped_column(String(80))
    # active seats receive assignments; a refunded or suspended seat does not.
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    assigned_count: Mapped[int] = mapped_column(Integer, default=0)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PoolAssignment(Base):
    """Audit trail: which sign-up went to which seat, and when.

    Without this row the assignment is unreviewable — a member disputing their
    sponsor, or a fraud review looking for a seat that took an implausible share
    of sign-ups, has nothing to read.
    """

    __tablename__ = "pool_assignments"
    __table_args__ = (
        UniqueConstraint("new_member_id", name="uq_pool_assignment_member"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seat_id: Mapped[str] = mapped_column(String(40), index=True)
    sponsor_id: Mapped[str] = mapped_column(String(40), index=True)
    new_member_id: Mapped[str] = mapped_column(String(40), index=True)
    # Before rounds: the seats in the draw. Since: the round's whole remaining
    # quota at the moment of the draw (the denominator of the odds).
    seats_in_draw: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Rounds. Null on assignments made before them, which are kept as they were.
    pool_id: Mapped[str | None] = mapped_column(String(20))
    round_id: Mapped[str | None] = mapped_column(String(40), index=True)
    round_number: Mapped[int | None] = mapped_column(Integer)
    # The receiving member's quota in that round, and what was left of it
    # just before this assignment.
    quota_total: Mapped[int | None] = mapped_column(Integer)
    quota_remaining_before: Mapped[int | None] = mapped_column(Integer)


POOL_ID = "main"


class ReferralPoolPurchase(Base):
    """One confirmed payment for pool units: what a member's weight is made of.

    ``payment_ref`` is unique, so a payment notified twice credits once. Units
    join the allocation from round ``active_from_round`` - the round after the
    one open when they were credited - and stop counting the moment they are
    reversed. ``units_reversed`` never exceeds ``units``.
    """

    __tablename__ = "referral_pool_purchases"
    __table_args__ = (
        UniqueConstraint("payment_ref", name="uq_pool_purchase_payment"),
        CheckConstraint("units >= 0", name="ck_pool_purchase_units"),
        CheckConstraint("units_reversed >= 0 AND units_reversed <= units", name="ck_pool_purchase_reversed"),
        Index("ix_pool_purchase_member", "pool_id", "member_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rpp"))
    pool_id: Mapped[str] = mapped_column(String(20), default=POOL_ID)
    member_id: Mapped[str] = mapped_column(String(40))
    seat_id: Mapped[str | None] = mapped_column(String(40))
    payment_ref: Mapped[str] = mapped_column(String(80))
    units: Mapped[int] = mapped_column(Integer)
    units_reversed: Mapped[int] = mapped_column(Integer, default=0)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    # credited | partially_reversed | reversed | review (a migrated record that
    # could not be matched to one confirmed $100 entry: it counts for nothing
    # until an administrator settles it).
    status: Mapped[str] = mapped_column(String(20), default="credited", index=True)
    source: Mapped[str] = mapped_column(String(20), default="payment")  # payment|migration
    active_from_round: Mapped[int] = mapped_column(Integer, default=1)
    review_note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    @property
    def active_units(self) -> int:
        if self.status not in ("credited", "partially_reversed"):
            return 0
        return max(self.units - self.units_reversed, 0)


class ReferralPoolUnitEntry(Base):
    """The unit ledger: every change to a member's units, never edited."""

    __tablename__ = "referral_pool_unit_ledger"
    __table_args__ = (
        Index("ix_pool_unit_ledger_member", "pool_id", "member_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_id: Mapped[str] = mapped_column(String(20), default=POOL_ID)
    member_id: Mapped[str] = mapped_column(String(40))
    purchase_id: Mapped[str] = mapped_column(String(40), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))  # purchase|migration|refund|reversal|review
    reason: Mapped[str | None] = mapped_column(String(300))
    actor: Mapped[str | None] = mapped_column(String(40))  # payment-service, or the admin's id
    # Idempotency for reversals: the same refund notified twice moves units once.
    reference: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReferralPoolRound(Base):
    """One complete rotation: every eligible unit, snapshot at the start, used once.

    At most one round per pool is open - the partial unique index refuses a
    second, so two registrations racing to open the next round cannot both.
    """

    __tablename__ = "referral_pool_rounds"
    __table_args__ = (
        UniqueConstraint("pool_id", "number", name="uq_pool_round_number"),
        Index("uq_pool_round_open", "pool_id", unique=True, postgresql_where=text("status = 'open'")),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rnd"))
    pool_id: Mapped[str] = mapped_column(String(20), default=POOL_ID)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open|closed
    members: Mapped[int] = mapped_column(Integer, default=0)
    units_snapshot: Mapped[int] = mapped_column(Integer, default=0)
    assigned: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(40))  # complete|no_eligible_quota


class ReferralPoolQuota(Base):
    """A member's share of one round: their units at the snapshot, and how
    many of them are used. ``assigned`` can never pass ``quota_total``.

    ``quota_total`` only ever shrinks during a round - a refund or a loss of
    eligibility takes the unused part away; nothing already assigned moves.
    """

    __tablename__ = "referral_pool_quotas"
    __table_args__ = (
        CheckConstraint("assigned >= 0 AND assigned <= quota_total", name="ck_pool_quota_bounds"),
        {"schema": SCHEMA},
    )

    round_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    seat_id: Mapped[str] = mapped_column(String(40))
    units_snapshot: Mapped[int] = mapped_column(Integer)
    quota_total: Mapped[int] = mapped_column(Integer)
    assigned: Mapped[int] = mapped_column(Integer, default=0)
    excluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excluded_reason: Mapped[str | None] = mapped_column(String(80))


class ReferralPoolUnassigned(Base):
    """A sign-up the pool could not place, and why. Kept for review and retry."""

    __tablename__ = "referral_pool_unassigned"
    __table_args__ = {"schema": SCHEMA}

    new_member_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    pool_id: Mapped[str] = mapped_column(String(20), default=POOL_ID)
    reason: Mapped[str] = mapped_column(String(120))  # pool_empty|error:<detail>
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAgeProfile(Base):
    """The authoritative age record. One row per account.

    Deliberately not columns on :class:`User`. The user row is serialised into
    a dozen responses across the platform, and a date of birth that lives there
    leaks the first time somebody adds a field to a profile payload. Here it
    has to be fetched on purpose.

    ``tier`` is derived from ``date_of_birth`` and the jurisdiction policy, and
    it is the value every other service reads. Nothing a client sends can
    change it, and the member never chooses it.
    """

    __tablename__ = "user_age_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_age_profile_user"),
        Index("ix_age_profile_tier", "tier"),
        # The transition job scans this: whose tier is due to change today?
        Index("ix_age_profile_next_transition", "next_transition_on"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("agp"))
    user_id: Mapped[str] = mapped_column(String(40), index=True)

    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    # Never rendered publicly, at any granularity, for a minor.
    tier: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    jurisdiction: Mapped[str] = mapped_column(String(2), default="XX")
    policy_version: Mapped[str] = mapped_column(String(40), default="")

    # self_declared | risk_based | verified - how much we actually know.
    assurance_level: Mapped[str] = mapped_column(String(20), default="self_declared")
    assurance_method: Mapped[str | None] = mapped_column(String(40))
    assured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Set when credible signals contradict the stated age. While this is true
    # the tier resolves to AGE_REVIEW_REQUIRED and the account is treated as a
    # minor until a human resolves it.
    under_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The next date the tier changes by itself, so the transition job is an
    # indexed lookup rather than a scan of every account.
    next_transition_on: Mapped[date | None] = mapped_column(Date)

    dob_change_count: Mapped[int] = mapped_column(Integer, default=0)
    last_dob_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RegistrationAttempt(Base):
    """Rejected sign-up attempts, kept briefly to blunt date-of-birth probing.

    The attack this exists for: enter 12, get refused, enter 19, get in. Keyed
    on a hash of the identifier rather than the identifier itself, so the table
    cannot be mined for who tried to join and failed.
    """

    __tablename__ = "registration_attempts"
    __table_args__ = (
        Index("ix_reg_attempt_key_time", "subject_hash", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_hash: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(30))   # under_minimum|allowed|blocked
    declared_age: Mapped[int | None] = mapped_column(Integer)
    jurisdiction: Mapped[str | None] = mapped_column(String(2))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AgeVerification(Base):
    """One age-assurance attempt and its outcome.

    Stores the *conclusion*, not the evidence: that this account is over a
    threshold, a provider reference, and when it was established. Identity
    documents are never stored here, which is the whole point of preferring an
    over-threshold assertion to a copy of a passport.
    """

    __tablename__ = "age_verifications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("agv"))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    method: Mapped[str] = mapped_column(String(40))   # provider_token|id_check|vouching|inference
    provider: Mapped[str | None] = mapped_column(String(60))
    provider_reference: Mapped[str | None] = mapped_column(String(120))
    threshold_age: Mapped[int] = mapped_column(Integer, default=18)
    result: Mapped[str] = mapped_column(String(20), default="pending")  # pending|over|under|failed
    confidence: Mapped[str] = mapped_column(String(20), default="")
    requested_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgeReviewCase(Base):
    """An open question about how old somebody actually is."""

    __tablename__ = "age_review_cases"
    __table_args__ = (
        Index("ix_age_review_status", "status", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("arc"))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    # reported_underage | dob_change | signal_mismatch | appeal
    source: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="")
    reported_by: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="open")  # open|verifying|closed
    outcome: Mapped[str | None] = mapped_column(String(40))
    resolved_by: Mapped[str | None] = mapped_column(String(40))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class JurisdictionPolicy(Base):
    """A jurisdiction's age rules, versioned, editable without a deploy.

    Superseded rows are kept rather than updated in place, so a decision taken
    under last quarter's policy can still be explained by it.
    """

    __tablename__ = "jurisdiction_policies"
    __table_args__ = (
        UniqueConstraint("jurisdiction", "policy_version", name="uq_jurisdiction_version"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jurisdiction: Mapped[str] = mapped_column(String(2), index=True)
    policy_version: Mapped[str] = mapped_column(String(40))
    minimum_registration_age: Mapped[int] = mapped_column(Integer, default=13)
    teen_high_protection_until: Mapped[int] = mapped_column(Integer, default=16)
    adult_age: Mapped[int] = mapped_column(Integer, default=18)
    parental_consent_required: Mapped[bool] = mapped_column(Boolean, default=False)
    age_assurance_level: Mapped[str] = mapped_column(String(20), default="self_declared")
    restricted_features: Mapped[str] = mapped_column(Text, default="")            # csv
    prohibited_content_categories: Mapped[str] = mapped_column(Text, default="")  # csv
    feature_minimum_ages: Mapped[str] = mapped_column(Text, default="{}")         # json
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|draft|superseded
    updated_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PasswordReset(Base):
    """One "I forgot my password" link.

    What is stored is a SHA-256 of the token, never the token. The token is a
    credential for the hour it lives: anybody holding it can take the account,
    so a copy of this table must not be enough to do that. The member's own
    copy, in their mail, is the only one that exists.

    `used_at` makes it single-use, and `invalidated_at` lets a newer request -
    or the member changing their password by other means - retire the older
    links without deleting the row, so the history of what was issued survives.
    """

    __tablename__ = "password_resets"
    __table_args__ = (
        Index("ix_auth_reset_user_time", "user_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pwr"))
    user_id: Mapped[str] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who asked. Kept so a member who reports "I never asked for this" can be
    # told where it came from.
    requested_ip: Mapped[str | None] = mapped_column(String(45))
    requested_user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    @property
    def usable(self) -> bool:
        return self.used_at is None and self.invalidated_at is None and self.expires_at > _now()


class ShareLink(Base):
    """A short link (kinjy.com/s/<token>): where it goes, who made it, and
    whether it carries their invitation.

    Destination and invitation never change after creation - a different
    either is a new link. A member asking again for the same page with the
    same choice gets the same active link back: the partial unique index makes
    concurrent requests meet on one row. Disabling frees the pair, so a fresh
    link can be made after one was switched off. The token is the primary key,
    so it is unique by construction and every lookup is an index hit.
    """

    __tablename__ = "share_links"
    __table_args__ = (
        Index(
            "uq_share_link_active",
            "member_id",
            "destination",
            "with_invitation",
            unique=True,
            postgresql_where=text("disabled_at IS NULL"),
        ),
        Index("ix_share_links_member_created", "member_id", "created_at"),
        {"schema": SCHEMA},
    )

    token: Mapped[str] = mapped_column(String(16), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    destination: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(20))  # invite|post|profile|community|chat|page
    with_invitation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
