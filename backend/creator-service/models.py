from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "creator"
AMOUNT = Numeric(18, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CreatorProfile(Base):
    __tablename__ = "creator_profiles"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tagline: Mapped[str | None] = mapped_column(String(200))
    categories: Mapped[str | None] = mapped_column(String(255))
    brand_kit: Mapped[str | None] = mapped_column(Text)  # JSON: colors, fonts, logo
    monetization_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    subscription_price_usd: Mapped[Decimal | None] = mapped_column(AMOUNT)
    total_views: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Series(Base):
    """Serialized content — episodes with a "next episode" rail."""

    __tablename__ = "series"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("srs"))
    creator_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    cover_url: Mapped[str | None] = mapped_column(String(500))
    cadence: Mapped[str | None] = mapped_column(String(40))  # weekly, daily...
    episodes_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PublishingJob(Base):
    """The One-to-Many Publishing Engine (blueprint module G).

    One source piece fans out into article / short video / long video / audio /
    carousel / newsletter / translations. Each output tracks its own state so a
    failed dubbing job never blocks the article.
    """

    __tablename__ = "publishing_jobs"
    __table_args__ = {"schema": SCHEMA}

    TARGETS = ("article", "short_video", "long_video", "audio", "carousel", "newsletter", "translation")

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pub"))
    creator_id: Mapped[str] = mapped_column(String(40), index=True)
    source_kind: Mapped[str] = mapped_column(String(20))  # text|video|audio
    source_ref: Mapped[str | None] = mapped_column(String(120))
    source_text: Mapped[str | None] = mapped_column(Text)
    source_lang: Mapped[str] = mapped_column(String(5), default="en")
    targets: Mapped[str] = mapped_column(String(255))     # csv of TARGETS
    target_langs: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # One job per (creator, post): asking again returns it instead of a second
    # set of outputs. NULL on jobs made before the durable pipeline.
    idem_key: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishingOutput(Base):
    """One thing a job produces: a short video, a newsletter draft, one translation.

    Rows with ``pipeline = 'v2'`` are the durable queue the worker in
    onetomany.py drains: claimed with a lease, retried a bounded number of
    times, and failed for good when they run out. Older rows (``pipeline`` NULL)
    are history and are only touched by the backfill an admin runs.
    """

    __tablename__ = "publishing_outputs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(30))
    lang: Mapped[str] = mapped_column(String(5), default="en")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    content: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(String(500))
    # Anything the engine produced is labelled — the provenance rule applies to
    # the platform's own AI output too, not only to users'.
    provenance: Mapped[str] = mapped_column(String(20), default="ai_generated")
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    STATUSES = ("queued", "processing", "completed", "failed", "cancelled")

    pipeline: Mapped[str | None] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The post as it stood when this was produced; a newer one makes it stale.
    source_revision: Mapped[str | None] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(40))
    # Newsletter draft: subject, preview line, HTML (sanitized) - `content` is its plain text.
    title: Mapped[str | None] = mapped_column(String(300))
    preview_text: Mapped[str | None] = mapped_column(String(300))
    content_html: Mapped[str | None] = mapped_column(Text)
    # The author changed the draft; regenerating must not silently replace it.
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    # Short video: the restricted asset in media-service.
    media_id: Mapped[str | None] = mapped_column(String(40))
    duration_s: Mapped[float | None] = mapped_column(Float)


class Subscription(Base):
    """Platform tiers (Free/Basic/Premium) and creator subscriptions."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("subscriber_id", "target_id", name="uq_subscription"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("sub"))
    subscriber_id: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(40), index=True)  # "platform" or a creator id
    tier: Mapped[str] = mapped_column(String(20), default="free")   # free|basic|premium|creator
    period: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly|yearly
    price_usd: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_on: Mapped[date] = mapped_column(Date, default=lambda: datetime.now(timezone.utc).date())
    renews_on: Mapped[date | None] = mapped_column(Date)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Badge(Base):
    """Gamification (blueprint §19) — rewards quality, not volume."""

    __tablename__ = "badges"
    __table_args__ = (
        UniqueConstraint("user_id", "badge", name="uq_badge"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    badge: Mapped[str] = mapped_column(String(40))
    awarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    reason: Mapped[str | None] = mapped_column(String(255))


BADGES = [
    "helpful_contributor", "expert", "community_builder",
    "verified_creator", "family_historian", "local_expert",
]

# Blueprint §12 — one-off purchases are priced 2–4x the implied subscription
# unit cost, so a subscription always remains the better deal.
PLANS = {
    "free": {"monthly": "0.00", "yearly": "0.00"},
    "basic": {"monthly": "4.99", "yearly": "49.00"},
    "premium": {"monthly": "9.99", "yearly": "99.00"},
}
