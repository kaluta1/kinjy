from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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

SCHEMA = "community"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Community(Base):
    """Blueprint module E — public / private / secret / paid."""

    __tablename__ = "communities"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cmy"))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="public")  # public|private|secret|paid
    # An open community's content is readable by anybody, including people who
    # have not joined and visitors who are not signed in: its page opens, a
    # link to one of its posts opens, and a preview is drawn when that link is
    # shared off Kinjy. Reading only - joining is still what lets somebody
    # like, comment or post.
    #
    # Defaults to True because that is already what an open community does: its
    # page has always been readable by anyone, and a default of False would
    # quietly close a door every existing public community already has open.
    # Only meaningful for kind == "public"; a private, secret or paid community
    # ignores it, since its whole promise is that content is for members.
    public_content: Mapped[bool] = mapped_column(Boolean, default=True)
    price_usd: Mapped[float | None] = mapped_column(Numeric(18, 2))
    lang: Mapped[str] = mapped_column(String(5), default="en")
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(120))
    members_count: Mapped[int] = mapped_column(Integer, default=0)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    # The photo and the banner, uploaded by the owner through media-service and
    # applied by id; the urls are what the pages draw.
    avatar_asset_id: Mapped[str | None] = mapped_column(String(40))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    cover_asset_id: Mapped[str | None] = mapped_column(String(40))
    # What lets a link to a *secret* community open for somebody outside it.
    # Minted the first time a member asks for a share link; public and private
    # communities never need one, their plain address already opens.
    invite_code: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # The template it started from (spaces.py). Defaults only: changing it
    # later changes no one's access.
    template: Mapped[str] = mapped_column(String(20), default="interest", server_default="interest")
    # Whether posts wait for a steward before they appear: none, new_members
    # (joined less than COMMUNITY_NEW_MEMBER_DAYS ago) or all.
    post_approval: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    # Sent once to each new member when they are let in.
    welcome_message: Mapped[str | None] = mapped_column(Text)
    rules: Mapped[str] = mapped_column(Text, default="", server_default="")
    # free | paid (joining needs an entitlement) | mixed (free to join; some
    # spaces and events need one).
    access_model: Mapped[str] = mapped_column(String(10), default="free", server_default="free")


class CommunitySpace(Base):
    """One space of one community, switched on or off by its owner (spaces.py)."""

    __tablename__ = "community_spaces"
    __table_args__ = {"schema": SCHEMA}

    community_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    space: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    # In a mixed community: only members with a paid entitlement open it.
    paid_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class CommunityAudit(Base):
    """What the people who run a community did in it, and why. Never edited.

    Kept per community so an owner can answer "who removed this, and why"
    without asking Kinjy, and so a moderator's actions are visible to the
    owner who gave them the role.
    """

    __tablename__ = "community_audit"
    __table_args__ = (
        Index("ix_community_audit_time", "community_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    community_id: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(40))
    # approve|reject|ban|unban|promote|demote|remove_member|settings|spaces|
    # privacy|post_approve|post_reject|post_remove|post_pin|post_unpin|invite
    action: Mapped[str] = mapped_column(String(30))
    target_kind: Mapped[str | None] = mapped_column(String(20))  # member|post|community
    target_id: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("community_id", "user_id", name="uq_membership"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    community_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # member|moderator|owner
    status: Mapped[str] = mapped_column(String(20), default="active")  # pending|active|banned|lapsed (paid access ended)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Pinned to the member's sidebar, and when. On the membership because a pin
    # belongs to somebody *in* the group: leaving deletes the row and the pin
    # with it, and a ban keeps the row but the pinned list reads active only.
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether this member is told about each new post in the group. On by
    # default - joining a group is asking to hear from it - and theirs to turn
    # off from the group's page.
    notify_posts: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Who brought them into *this community* - an invitation link of a member.
    # Separate from the account's sponsor (auth users.invited_by), which a
    # community invitation never touches.
    invited_by: Mapped[str | None] = mapped_column(String(40), index=True)
    invite_token: Mapped[str | None] = mapped_column(String(16))
    # When the welcome message went out: once per membership, retries included.
    welcomed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Forum(Base):
    """Blueprint module C — forums nest by geography *and* by topic."""

    __tablename__ = "forums"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("frm"))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(String(40), index=True)
    hierarchy: Mapped[str] = mapped_column(String(20), default="topic")  # topic|geographic
    scope: Mapped[str | None] = mapped_column(String(40))  # global|continent|country|state|city|neighborhood
    scope_value: Mapped[str | None] = mapped_column(String(120))
    community_id: Mapped[str | None] = mapped_column(String(40), index=True)
    threads_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("thr"))
    forum_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    replies_count: Mapped[int] = mapped_column(Integer, default=0)
    views_count: Mapped[int] = mapped_column(Integer, default=0)
    # Filled by ai-service; kept nullable so a thread is never blocked on the AI.
    ai_summary: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rpl"))
    thread_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    accepted_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    upvotes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReplyVote(Base):
    """One member's upvote on one reply.

    `Reply.upvotes` is only a cached count. The votes live here so a member can
    vote once, take the vote back, and so the count can always be rebuilt from
    what actually happened instead of trusting an increment.
    """

    __tablename__ = "reply_votes"
    __table_args__ = (
        UniqueConstraint("reply_id", "user_id", name="uq_reply_vote"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rvt"))
    reply_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KnowledgeEntry(Base):
    """Forum-to-Knowledge transformation (blueprint §18).

    Every entry keeps its source thread and reply ids: the knowledge base cites
    the discussion it came from instead of asserting facts anonymously.
    """

    __tablename__ = "knowledge_entries"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("kbe"))
    forum_id: Mapped[str] = mapped_column(String(40), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    source_thread_ids: Mapped[str] = mapped_column(Text)  # csv
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    reviewed_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContentSafetyClassification(Base):
    """Age suitability for a thread, a reply or a community.

    Same shape as the social schema's table on purpose: one vocabulary across
    the platform means one engine can read both, and a rating means the same
    thing wherever it was written.
    """

    __tablename__ = "content_safety"
    __table_args__ = (
        Index("ix_community_safety_rating", "age_rating"),
        Index("ix_community_safety_review", "human_review_status"),
        UniqueConstraint("content_id", name="uq_community_safety_content"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("csc"))
    content_id: Mapped[str] = mapped_column(String(40), index=True)
    content_kind: Mapped[str] = mapped_column(String(20), default="thread")  # thread|reply|community

    age_rating: Mapped[str] = mapped_column(String(20), default="UNCLASSIFIED")

    sexual_content_level: Mapped[int] = mapped_column(Integer, default=0)
    nudity_level: Mapped[int] = mapped_column(Integer, default=0)
    violence_level: Mapped[int] = mapped_column(Integer, default=0)
    graphic_content_level: Mapped[int] = mapped_column(Integer, default=0)
    drugs_level: Mapped[int] = mapped_column(Integer, default=0)
    alcohol_level: Mapped[int] = mapped_column(Integer, default=0)
    gambling_level: Mapped[int] = mapped_column(Integer, default=0)
    dangerous_activity_level: Mapped[int] = mapped_column(Integer, default=0)
    self_harm_risk: Mapped[int] = mapped_column(Integer, default=0)
    hate_or_abuse_risk: Mapped[int] = mapped_column(Integer, default=0)
    exploitation_risk: Mapped[int] = mapped_column(Integer, default=0)

    classifier_source: Mapped[str] = mapped_column(String(60), default="")
    classifier_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    human_review_status: Mapped[str] = mapped_column(String(20), default="none")
    jurisdiction_overrides: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


# --- Phase 2: paid access, events, listings (25/10) -------------------------
#
# Payment state and access are kept apart on purpose: a CommunityPayment says
# what money moved, a CommunityEntitlement says what the member may open, and
# only the second is read by access checks.


class PlatformSetting(Base):
    """A platform rule an administrator adjusts (community markup, grace days...)."""

    __tablename__ = "platform_settings"
    __table_args__ = {"schema": SCHEMA}

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))
    updated_by: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityTier(Base):
    """A way in to a paid community: its owner's price, per period or once."""

    __tablename__ = "community_tiers"
    __table_args__ = (Index("ix_community_tiers_community", "community_id", "position"), {"schema": SCHEMA})

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("tier"))
    community_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    benefits: Mapped[str] = mapped_column(Text, default="")  # one per line
    # The owner's price. What the member pays adds Kinjy's markup on top.
    price: Mapped[float] = mapped_column(Numeric(18, 2))
    period: Mapped[str] = mapped_column(String(10))  # monthly|annual|one_time
    trial_days: Mapped[int] = mapped_column(Integer, default=0)
    # A lower owner price for the first period, if any.
    intro_price: Mapped[float | None] = mapped_column(Numeric(18, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityCoupon(Base):
    __tablename__ = "community_coupons"
    __table_args__ = (UniqueConstraint("community_id", "code", name="uq_community_coupon"), {"schema": SCHEMA})

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cpn"))
    community_id: Mapped[str] = mapped_column(String(40))
    code: Mapped[str] = mapped_column(String(32))
    # 1..100, off the owner's price of the first payment only.
    percent_off: Mapped[int] = mapped_column(Integer)
    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    redemptions: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityQuote(Base):
    """A price computed on the server for one member and one tier or ticket.

    The payment is created for exactly this total and credited against it; a
    quote is used once (``payment_ref`` unique) and expires.
    """

    __tablename__ = "community_quotes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cq"))
    community_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # membership|ticket
    tier_id: Mapped[str | None] = mapped_column(String(40))
    event_id: Mapped[str | None] = mapped_column(String(40))
    coupon_id: Mapped[str | None] = mapped_column(String(40))
    owner_price: Mapped[float] = mapped_column(Numeric(18, 2))
    markup_pct: Mapped[float] = mapped_column(Numeric(6, 2))
    markup: Mapped[float] = mapped_column(Numeric(18, 2))
    total: Mapped[float] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payment_ref: Mapped[str | None] = mapped_column(String(80), unique=True)


class CommunityEntitlement(Base):
    """What a member may open in a community, whatever the payments did.

    status: trial | active | grace | expired | cancelled | refunded. Access is
    granted for trial, active and grace. One per member and community.
    """

    __tablename__ = "community_entitlements"
    __table_args__ = (
        UniqueConstraint("community_id", "user_id", name="uq_community_entitlement"),
        Index("ix_entitlements_due", "status", "period_end"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ent"))
    community_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    tier_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Null for one-time access, which does not end.
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    # The period end a renewal reminder was sent for: one reminder per period.
    reminded_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityPayment(Base):
    """Money that moved for a community: once per payment (``payment_ref`` unique)."""

    __tablename__ = "community_payments"
    __table_args__ = (Index("ix_community_payments_community", "community_id", "created_at"), {"schema": SCHEMA})

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cpay"))
    community_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    quote_id: Mapped[str] = mapped_column(String(40))
    payment_ref: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(10))  # membership|ticket
    tier_id: Mapped[str | None] = mapped_column(String(40))
    event_id: Mapped[str | None] = mapped_column(String(40))
    owner_price: Mapped[float] = mapped_column(Numeric(18, 2))
    markup: Mapped[float] = mapped_column(Numeric(18, 2))
    total: Mapped[float] = mapped_column(Numeric(18, 2))
    coupon_code: Mapped[str | None] = mapped_column(String(32))
    period_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="paid")  # paid|partially_refunded|refunded
    refunded: Mapped[float] = mapped_column(Numeric(18, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityRefund(Base):
    """A refund of (part of) a community payment, once per request id."""

    __tablename__ = "community_refunds"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    payment_id: Mapped[str] = mapped_column(String(40), index=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 2))
    reason: Mapped[str] = mapped_column(String(300), default="")
    actor_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|done|failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityEvent(Base):
    __tablename__ = "community_events"
    __table_args__ = (Index("ix_community_events_when", "community_id", "starts_at"), {"schema": SCHEMA})

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("evt"))
    community_id: Mapped[str] = mapped_column(String(40))
    host_id: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Stored in UTC; ``timezone`` is where it happens (IANA name), for display.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(60), default="UTC")
    mode: Mapped[str] = mapped_column(String(12), default="online")  # online|in_person|hybrid
    location: Mapped[str | None] = mapped_column(String(300))
    # Shown to registered attendees only.
    online_url: Mapped[str | None] = mapped_column(String(500))
    capacity: Mapped[int | None] = mapped_column(Integer)
    waitlist: Mapped[bool] = mapped_column(Boolean, default=True)
    # The owner's ticket price; null or 0 = free.
    ticket_price: Mapped[float | None] = mapped_column(Numeric(18, 2))
    speakers: Mapped[str] = mapped_column(Text, default="")
    recording_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(12), default="scheduled")  # scheduled|cancelled
    registered_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EventRegistration(Base):
    __tablename__ = "event_registrations"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_registration"),
        Index("ix_event_registrations_queue", "event_id", "status", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(12))  # registered|waitlisted|cancelled
    payment_id: Mapped[str | None] = mapped_column(String(40))
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CommunityListing(Base):
    """A member's marketplace product shown in a community's marketplace space."""

    __tablename__ = "community_listings"
    __table_args__ = (UniqueConstraint("community_id", "product_id", name="uq_community_listing"), {"schema": SCHEMA})

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    community_id: Mapped[str] = mapped_column(String(40), index=True)
    product_id: Mapped[str] = mapped_column(String(40))
    added_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
