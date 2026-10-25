from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "commerce"
AMOUNT = Numeric(18, 2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    """A marketplace listing. ``vendor_price`` is what the seller wants; the
    customer price is derived from it and never stored twice."""

    __tablename__ = "products"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("prd"))
    vendor_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="product")  # product|service|digital
    vendor_price: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    stock: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(120))
    images: Mapped[str | None] = mapped_column(Text)  # csv of urls
    status: Mapped[str] = mapped_column(String(20), default="active")
    # active|paused|draft|suspended|removed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # --- an ebook (a digital listing delivered as a file) -------------------
    # Set on digital listings made with an ebook file; older digital listings
    # leave them empty and keep the seller-delivers flow they always had.
    ebook: Mapped[bool] = mapped_column(Boolean, default=False)
    subtitle: Mapped[str | None] = mapped_column(String(200))
    author: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(40), index=True)
    language: Mapped[str | None] = mapped_column(String(10), index=True)
    published_on: Mapped[date | None] = mapped_column(Date)
    edition: Mapped[str | None] = mapped_column(String(60))
    page_count: Mapped[int | None] = mapped_column(Integer)
    isbn: Mapped[str | None] = mapped_column(String(17))
    license: Mapped[str | None] = mapped_column(String(20))  # personal|commercial
    # The current file (see EbookFile for every version) and the free sample.
    ebook_file_id: Mapped[int | None] = mapped_column(Integer)
    sample_asset_id: Mapped[str | None] = mapped_column(String(40))
    sample_url: Mapped[str | None] = mapped_column(String(500))
    sample_format: Mapped[str | None] = mapped_column(String(10))
    # Null means no limit: nothing restrictive is assumed for the seller.
    download_limit: Mapped[int | None] = mapped_column(Integer)
    access_days: Mapped[int | None] = mapped_column(Integer)
    suspended_reason: Mapped[str | None] = mapped_column(String(300))


class EbookFile(Base):
    """One version of an ebook's complete file. Never deleted or overwritten.

    Replacing the file adds a version and moves the listing to it. Policy:
    every buyer of the ebook downloads the current version - a corrected
    edition reaches the people who already paid for the book - and the order
    history keeps which version existed when.
    """

    __tablename__ = "ebook_files"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[str] = mapped_column(String(40))
    # The private media address; signed per download, never published.
    url: Mapped[str] = mapped_column(String(500))
    file_format: Mapped[str] = mapped_column(String(10))  # pdf|epub
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EbookDownload(Base):
    """One download link handed to a buyer. Counted, not the bytes."""

    __tablename__ = "ebook_downloads"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(40), index=True)
    buyer_id: Mapped[str] = mapped_column(String(40), index=True)
    file_id: Mapped[int] = mapped_column(Integer)
    # False for a retry within the grace window: an interrupted download
    # asked for again does not use up an allowance.
    counted: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class CommerceAudit(Base):
    """Sensitive administrative actions on listings and ebook access."""

    __tablename__ = "commerce_audit"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(20))  # product|order
    target_id: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ord"))
    buyer_id: Mapped[str] = mapped_column(String(40), index=True)
    vendor_id: Mapped[str] = mapped_column(String(40), index=True)
    product_id: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    vendor_price: Mapped[Decimal] = mapped_column(AMOUNT)
    margin: Mapped[Decimal] = mapped_column(AMOUNT)
    customer_price: Mapped[Decimal] = mapped_column(AMOUNT)
    currency: Mapped[str] = mapped_column(String(10), default="USD")

    # Escrow: the buyer's money sits with an authorised financial institution,
    # not with Kinjy, and reaches the seller only once the buyer confirms
    # receipt — or once the confirmation window closes with no dispute.
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending|in_escrow|delivered|settled|disputed|refunded|part_refunded|cancelled
    custodian: Mapped[str | None] = mapped_column(String(40))
    custody_ref: Mapped[str | None] = mapped_column(String(120))
    escrow_funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escrow_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispute_window_ends: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # Set by the seller when they hand the goods over / deliver the file. It does
    # not release the money: only the buyer, or the window expiring, does that.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_note: Mapped[str | None] = mapped_column(Text)
    carrier: Mapped[str | None] = mapped_column(String(80))
    tracking_number: Mapped[str | None] = mapped_column(String(120))
    tracking_url: Mapped[str | None] = mapped_column(String(500))
    # JSON blob; only the buyer, the vendor and admins ever see it.
    shipping_address: Mapped[str | None] = mapped_column(Text)
    refunded_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    ledger_journal_id: Mapped[str | None] = mapped_column(String(40))
    # An ebook order: downloads before this time are not counted against the
    # limit (an admin resetting a buyer's allowance), and access revoked by
    # an admin for a justified reason.
    downloads_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Campaign(Base):
    """An advertising campaign."""

    __tablename__ = "campaigns"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cmp"))
    advertiser_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(String(40))  # awareness|traffic|leads|sales|local
    pricing_model: Mapped[str] = mapped_column(String(20))  # cpm|cpm_video|cpc|cpv|engagement|lead|daily_local|daily_city
    bid: Mapped[Decimal] = mapped_column(AMOUNT)
    budget: Mapped[Decimal] = mapped_column(AMOUNT)
    spent: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))

    target_country: Mapped[str | None] = mapped_column(String(2))
    target_city: Mapped[str | None] = mapped_column(String(120))
    target_radius_km: Mapped[int | None] = mapped_column(Integer)
    target_age_min: Mapped[int | None] = mapped_column(Integer)
    target_age_max: Mapped[int | None] = mapped_column(Integer)
    target_langs: Mapped[str | None] = mapped_column(String(60))

    creative_headline: Mapped[str | None] = mapped_column(String(200))
    creative_body: Mapped[str | None] = mapped_column(Text)
    creative_media_url: Mapped[str | None] = mapped_column(String(500))
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)

    # An AI-built campaign never goes live on its own (blueprint §13).
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft|pending_approval|active|paused|completed
    approved_by: Mapped[str | None] = mapped_column(String(40))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Blueprint §13 — auction floor prices, AI-adjusted upward but never below these.
AD_FLOORS = {
    "cpm": "0.50",
    "cpm_video": "1.00",
    "cpc": "0.05",
    "cpv": "0.005",
    "engagement": "0.02",
    "lead": "0.25",
    "daily_local": "1.00",
    "daily_city": "5.00",
}


class Dispute(Base):
    """A contested order, and the record of how it was settled.

    Opening a dispute freezes the escrow: the auto-release stops, so a seller
    cannot simply wait out a buyer who is raising a problem, and a buyer cannot
    keep the goods and the money by opening a case and going quiet — the seller
    has a deadline to answer and arbitration decides if they do not.

    Every exchange lives in :class:`DisputeMessage` rather than in a single
    free-text field, so the case can be read in order by whoever arbitrates it.
    """

    __tablename__ = "disputes"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dsp"))
    order_id: Mapped[str] = mapped_column(String(40), index=True)
    opened_by: Mapped[str] = mapped_column(String(40), index=True)
    against: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(10))  # buyer|seller
    category: Mapped[str] = mapped_column(String(40))
    # not_received|not_as_described|damaged|unauthorised|other
    reason: Mapped[str] = mapped_column(Text)
    amount_claimed: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))

    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    # open|answered|arbitration|resolved|withdrawn
    outcome: Mapped[str | None] = mapped_column(String(24))
    # release_to_seller|refund_buyer|split|withdrawn
    refund_amount: Mapped[Decimal] = mapped_column(AMOUNT, default=Decimal("0"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(40))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The other side has until here to answer before the case is escalated.
    respond_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    arbitrate_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DisputeMessage(Base):
    """One statement in a dispute, from either party or from Kinjy."""

    __tablename__ = "dispute_messages"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dispute_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(String(40))
    author_role: Mapped[str] = mapped_column(String(12))  # buyer|seller|kinjy
    body: Mapped[str] = mapped_column(Text)
    # Evidence is a list of media URLs, stored as a newline-separated blob: the
    # files themselves live in media-service like any other upload.
    evidence: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


DISPUTE_CATEGORIES = (
    "not_received",
    "not_as_described",
    "damaged",
    "unauthorised",
    "other",
)


class ProductReview(Base):
    """A review tied to one settled order, so only real buyers can rate."""

    __tablename__ = "product_reviews"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rev"))
    order_id: Mapped[str] = mapped_column(String(40), unique=True)
    product_id: Mapped[str] = mapped_column(String(40), index=True)
    vendor_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(String(40), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
