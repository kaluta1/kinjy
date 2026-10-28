"""Kinjy · commerce-service — the agency marketplace and the ad platform.

A buyer's payment is held until the purchase is final: the buyer confirms
receipt in their own Kinjy account (a product or a service), or downloads an
ebook for the first time. That, and only that, starts settlement to the seller
(acceptance.py). Kinjy does not arbitrate ordinary marketplace disputes; it
keeps operational support (payment problems, duplicate charges, an item not
handed over after confirmation, fraud, legal requests).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession
from starlette.concurrency import run_in_threadpool

from common import crypto, economy, events, mediasign, settings
from common.article_html import sanitize_article
from common import agefeatures
from common.auth import AdminUser, CurrentUser, MaybeUser
from common.notify import notify, notify_many
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import acceptance
import models

log = logging.getLogger("commerce-service")
LEDGER_URL = "http://ledger-service:8000"
PAYMENT_URL = "http://payment-service:8000"

# Escrow and dispute columns added to an existing orders table. create_all
# cannot alter a live table, and an order row missing custody_ref would make
# every settlement unattributable.
MIGRATIONS = [
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS custodian VARCHAR(40)",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS custody_ref VARCHAR(120)",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS escrow_funded_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS delivery_note TEXT",
    f"ALTER TABLE {models.SCHEMA}.orders "
    "ADD COLUMN IF NOT EXISTS refunded_amount NUMERIC(18,2) DEFAULT 0",
]

MIGRATIONS.extend([
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS shipping_address TEXT",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS carrier VARCHAR(80)",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS tracking_number VARCHAR(120)",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS tracking_url VARCHAR(500)",
])

# Ebooks (25/10): additive columns on listings and orders. The new tables
# (ebook_files, ebook_downloads, commerce_audit) are created by create_all.
MIGRATIONS.extend([
    f"ALTER TABLE {models.SCHEMA}.products ADD COLUMN IF NOT EXISTS ebook BOOLEAN NOT NULL DEFAULT FALSE",
    *[
        f"ALTER TABLE {models.SCHEMA}.products ADD COLUMN IF NOT EXISTS {column}"
        for column in (
            "subtitle VARCHAR(200)", "author VARCHAR(200)", "summary VARCHAR(500)", "category VARCHAR(40)",
            "language VARCHAR(10)", "published_on DATE", "edition VARCHAR(60)", "page_count INTEGER",
            "isbn VARCHAR(17)", "license VARCHAR(20)", "ebook_file_id INTEGER", "sample_asset_id VARCHAR(40)",
            "sample_url VARCHAR(500)", "sample_format VARCHAR(10)", "download_limit INTEGER",
            "access_days INTEGER", "suspended_reason VARCHAR(300)",
        )
    ],
    f"CREATE INDEX IF NOT EXISTS ix_products_category ON {models.SCHEMA}.products (category)",
    f"CREATE INDEX IF NOT EXISTS ix_products_language ON {models.SCHEMA}.products (language)",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS downloads_reset_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS access_revoked_at TIMESTAMPTZ",
])

# Final acceptance and settlement (27/10): additive columns; the new tables
# (policy_acks, support_cases) are created by create_all.
MIGRATIONS.extend([
    *[
        f"ALTER TABLE {models.SCHEMA}.orders ADD COLUMN IF NOT EXISTS {column}"
        for column in (
            "payment_rail VARCHAR(20)", "accepted_at TIMESTAMPTZ", "accepted_by VARCHAR(40)",
            "acceptance_ref VARCHAR(40)", "acceptance_kind VARCHAR(20)", "acceptance_policy VARCHAR(20)",
            "settlement_status VARCHAR(20) NOT NULL DEFAULT 'none'", "settlement_attempts INTEGER NOT NULL DEFAULT 0",
            "settlement_next_at TIMESTAMPTZ", "settlement_error VARCHAR(300)", "settlement_backing VARCHAR(40)",
            "cancelled_at TIMESTAMPTZ", "cancelled_by VARCHAR(40)", "cancel_reason VARCHAR(40)",
            "cancel_note VARCHAR(500)", "refund_status VARCHAR(20) NOT NULL DEFAULT 'none'",
            "refund_attempts INTEGER NOT NULL DEFAULT 0", "refund_next_at TIMESTAMPTZ", "refund_error VARCHAR(300)",
        )
    ],
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_acceptance_ref ON {models.SCHEMA}.orders (acceptance_ref) "
    "WHERE acceptance_ref IS NOT NULL",
    f"CREATE INDEX IF NOT EXISTS ix_orders_settlement_due ON {models.SCHEMA}.orders (settlement_status, settlement_next_at)",
    f"CREATE INDEX IF NOT EXISTS ix_orders_refund_due ON {models.SCHEMA}.orders (refund_status, refund_next_at)",
    *[
        f"ALTER TABLE {models.SCHEMA}.products ADD COLUMN IF NOT EXISTS {column}"
        for column in ("guarantee TEXT", "insurance TEXT", "service_terms TEXT")
    ],
    f"ALTER TABLE {models.SCHEMA}.ebook_downloads ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
])

app = create_app(
    name="commerce-service",
    schema=models.SCHEMA,
    description="Agency marketplace (held payments, final acceptance, settlement), advertising.",
    migrations=MIGRATIONS,
    on_startup=[acceptance.start_worker],
)


EBOOK_CATEGORIES = (
    "business", "education", "fiction", "health", "history", "religion", "self_help", "technology",
    "children", "cooking", "arts", "finance", "languages", "science", "travel", "other",
)
EBOOK_LICENSES = ("personal", "commercial")


def _isbn_ok(value: str) -> bool:
    """An ISBN-10 or ISBN-13 whose check digit is right."""
    digits = value.replace("-", "").replace(" ", "").upper()
    if len(digits) == 10 and digits[:9].isdigit() and (digits[9].isdigit() or digits[9] == "X"):
        total = sum((10 - i) * int(d) for i, d in enumerate(digits[:9]))
        total += 10 if digits[9] == "X" else int(digits[9])
        return total % 11 == 0
    if len(digits) == 13 and digits.isdigit():
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
        return (10 - total % 10) % 10 == int(digits[12])
    return False


class EbookIn(BaseModel):
    """What makes a digital listing an ebook: its file, and its book details."""

    file_asset_id: str | None = Field(default=None, max_length=40)
    sample_asset_id: str | None = Field(default=None, max_length=40)
    subtitle: str | None = Field(default=None, max_length=200)
    author: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=500)
    category: str | None = None
    language: str | None = Field(default=None, pattern="^[a-z]{2,3}$")
    published_on: date | None = None
    edition: str | None = Field(default=None, max_length=60)
    page_count: int | None = Field(default=None, ge=1, le=100000)
    isbn: str | None = Field(default=None, max_length=17)
    license: str = Field(default="personal", pattern="^(personal|commercial)$")
    download_limit: int | None = Field(default=None, ge=1, le=1000)
    access_days: int | None = Field(default=None, ge=1, le=3650)

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str | None) -> str | None:
        if value and value not in EBOOK_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(EBOOK_CATEGORIES)}")
        return value or None

    @field_validator("isbn")
    @classmethod
    def _check_isbn(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        if not _isbn_ok(value):
            raise ValueError("That ISBN is not valid (check the digits, or leave it empty)")
        return value.replace(" ", "").upper()


class GuaranteeIn(BaseModel):
    """A seller's guarantee or warranty, as they state it."""

    scope: str = Field(min_length=3, max_length=1000)
    duration: str = Field(min_length=1, max_length=120)
    exclusions: str = Field(default="", max_length=1000)
    claim_contact: str = Field(min_length=3, max_length=300)


class InsuranceIn(BaseModel):
    """Insurance included with the purchase, as the seller states it."""

    insurer: str = Field(min_length=2, max_length=200)
    coverage: str = Field(min_length=3, max_length=1000)
    exclusions: str = Field(default="", max_length=1000)
    claim_process: str = Field(min_length=3, max_length=1000)
    premium_included: bool = True


class ProductIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = ""
    kind: str = Field(default="product", pattern="^(product|service|digital)$")
    # "draft" keeps a listing to the seller until it is ready.
    status: str = Field(default="active", pattern="^(active|draft)$")
    # A digital listing with an ebook file: delivered automatically, as a
    # download, once the payment is confirmed.
    ebook: EbookIn | None = None
    vendor_price: Decimal = Field(gt=0)
    # Services and digital goods have no inventory, so null means unlimited.
    stock: int | None = Field(default=None, ge=0)
    country: str | None = None
    city: str | None = Field(default=None, max_length=120)
    images: list[str] = Field(default_factory=list, max_length=5)
    # Disclosed to the buyer before they pay. None means none, and is said so.
    guarantee: GuaranteeIn | None = None
    insurance: InsuranceIn | None = None
    # A service: what "completed" means and how the buyer can assess it.
    service_terms: str | None = Field(default=None, max_length=3000)

    @field_validator("images")
    @classmethod
    def _check_images(cls, value: list[str]) -> list[str]:
        return _clean_images(value)

    @field_validator("country")
    @classmethod
    def _check_country(cls, value: str | None) -> str | None:
        return _clean_country(value)


_MEDIA_PATH = re.compile(r"/media/(mda_[0-9A-Za-z]{6,60})$")


def _unsigned(url: str) -> str:
    """A media address without the viewing ticket a page was shown it with.
    Tickets expire, so only the bare address is ever stored."""
    base = url.split("?", 1)[0] if isinstance(url, str) else url
    return base if _MEDIA_PATH.search(base or "") else url


def _clean_images(value: list[str]) -> list[str]:
    # Images are stored as a csv, so a comma inside a url would split it in two.
    value = [_unsigned(url) for url in value]
    for url in value:
        if (
            not isinstance(url, str)
            or not 1 <= len(url) <= 500
            or "," in url
            or not (url.startswith(("http://", "https://")) or (url.startswith("/") and not url.startswith("//")))
        ):
            raise ValueError("images must be http(s) or root-relative urls of at most 500 characters")
    return value


def _clean_country(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if len(value) != 2 or not value.isalpha() or not value.isascii():
        raise ValueError("country must be a 2-letter code")
    return value.upper()


class ShippingIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str = Field(pattern="^[A-Za-z]{2}$")
    phone: str | None = Field(default=None, max_length=40)


class OrderIn(BaseModel):
    product_id: str
    quantity: int = Field(default=1, ge=1)
    shipping: ShippingIn | None = None
    # The buyer read the marketplace policy before paying (acceptance.POLICY).
    acknowledge_policy: bool = False
    policy_version: str | None = None


class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    objective: str = Field(pattern="^(awareness|traffic|leads|sales|local)$")
    pricing_model: str
    bid: Decimal = Field(gt=0)
    budget: Decimal = Field(gt=0)
    target_country: str | None = None
    target_city: str | None = None
    target_radius_km: int | None = None
    target_age_min: int | None = None
    target_age_max: int | None = None
    creative_headline: str | None = None
    creative_body: str | None = None


# --- marketplace -----------------------------------------------------------

@app.get("/commerce/pricing", tags=["marketplace"])
def pricing_preview(vendor_price: Decimal):
    """Shows exactly how a vendor price becomes a customer price."""
    pricing = economy.marketplace_pricing(vendor_price)
    return {
        **{k: str(v) for k, v in pricing.items()},
        "explanation": (
            f"The vendor receives {pricing['vendor_price']}. Kinjy adds a "
            f"{pricing['markup_pct']}% markup ({pricing['margin']}), so the customer pays "
            f"{pricing['customer_price']}. Affiliate commissions are paid out of the "
            f"{pricing['margin']} margin only."
        ),
    }


@app.post("/commerce/products", status_code=201, tags=["marketplace"])
def create_product(payload: ProductIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    # Selling to strangers is a contract, not a post. The refusal is 403 and
    # says nothing about which age would qualify.
    agefeatures.require(principal.user_id, "marketplace_sell")
    if payload.ebook is not None and payload.kind != "digital":
        raise HTTPException(status_code=422, detail="Only a digital listing can be an ebook")

    if payload.kind == "service" and not (payload.service_terms or "").strip():
        raise HTTPException(
            status_code=422,
            detail="Describe what completing this service means and how the buyer can check it before confirming",
        )
    data = payload.model_dump(exclude={"images", "ebook", "guarantee", "insurance", "service_terms"})
    product = models.Product(
        id=new_id("prd"),
        vendor_id=principal.user_id,
        images=",".join(payload.images) or None,
        guarantee=payload.guarantee.model_dump_json() if payload.guarantee else None,
        insurance=payload.insurance.model_dump_json() if payload.insurance else None,
        service_terms=(payload.service_terms or "").strip() or None,
        **data,
    )
    if payload.ebook is not None:
        # Nothing physical about it: no stock, no pickup city.
        product.stock = None
        product.city = None
        product.ebook = True
        product.description = sanitize_article(payload.description or "")
        _apply_ebook(db, product, payload.ebook, principal.user_id)
        if product.status == "active":
            _require_publishable(product)
    db.add(product)
    db.commit()
    pricing = economy.marketplace_pricing(product.vendor_price)
    return {"id": product.id, "status": product.status, "pricing": {k: str(v) for k, v in pricing.items()}}


# --- ebooks: files, details, publication ------------------------------------------

MEDIA_URL = "http://media-service:8000"


def _media_asset(asset_id: str) -> dict | None:
    try:
        response = httpx.get(f"{MEDIA_URL}/internal/media/{asset_id}", timeout=6)
    except httpx.HTTPError as exc:
        log.error("media-service unreachable for %s: %s", asset_id, exc)
        raise HTTPException(status_code=503, detail="The file service did not answer; try again")
    return response.json() if response.status_code == 200 else None


def _ebook_asset(asset_id: str, owner_id: str, purpose: str) -> dict:
    """An uploaded file the seller may put on their ebook - their own, stored,
    and uploaded as exactly this (the full book, or its sample)."""
    info = _media_asset(asset_id)
    what = "ebook file" if purpose == "ebook" else "sample"
    if info is None or info.get("owner_id") != owner_id or info.get("purpose") != purpose:
        raise HTTPException(status_code=422, detail=f"That {what} was not found among your uploads")
    if info.get("status") != "ready":
        raise HTTPException(status_code=422, detail=f"The {what} has not finished uploading")
    if info.get("content_type") not in ("application/pdf", "application/epub+zip"):
        raise HTTPException(status_code=422, detail=f"The {what} must be a PDF or an EPUB")
    if purpose == "ebook" and not info.get("private"):
        # Defence in depth: a paid file that is not private is never attached.
        raise HTTPException(status_code=422, detail="The ebook file is not stored privately")
    return info


def _format_of(content_type: str) -> str:
    return "pdf" if content_type == "application/pdf" else "epub"


def _add_ebook_version(db: OrmSession, product: models.Product, info: dict, note: str | None = None) -> models.EbookFile:
    """A new version of the book's file. The previous ones stay on record."""
    if product.id is None:
        product.id = new_id("prd")
    latest = db.scalar(
        select(func.max(models.EbookFile.version)).where(models.EbookFile.product_id == product.id)
    ) or 0
    version = models.EbookFile(
        product_id=product.id,
        version=latest + 1,
        asset_id=info["id"],
        url=info["url"],
        file_format=_format_of(info["content_type"]),
        filename=(info.get("filename") or f"ebook.{_format_of(info['content_type'])}")[:255],
        size_bytes=int(info.get("size_bytes") or 0),
        note=note,
    )
    db.add(version)
    db.flush()
    product.ebook_file_id = version.id
    return version


def _apply_ebook(db: OrmSession, product: models.Product, ebook: EbookIn, owner_id: str, *, fields: set | None = None) -> None:
    """Set an ebook's details (all of them, or only ``fields`` on an edit)."""
    data = ebook.model_dump()
    wanted = fields if fields is not None else set(data)
    for key in ("subtitle", "author", "summary", "category", "language", "published_on", "edition",
                "page_count", "isbn", "license", "download_limit", "access_days"):
        if key in wanted:
            value = data[key]
            setattr(product, key, value.strip() or None if isinstance(value, str) else value)
    if "sample_asset_id" in wanted:
        if data["sample_asset_id"]:
            info = _ebook_asset(data["sample_asset_id"], owner_id, "ebook_sample")
            product.sample_asset_id = info["id"]
            product.sample_url = info["url"]
            product.sample_format = _format_of(info["content_type"])
        else:
            product.sample_asset_id = product.sample_url = product.sample_format = None
    if "file_asset_id" in wanted and data["file_asset_id"]:
        current = db.get(models.EbookFile, product.ebook_file_id) if product.ebook_file_id else None
        if current is None or current.asset_id != data["file_asset_id"]:
            _add_ebook_version(db, product, _ebook_asset(data["file_asset_id"], owner_id, "ebook"))


def _require_publishable(product: models.Product) -> None:
    """What a paid ebook needs before buyers can see it. Said, field by field."""
    missing = []
    if not product.ebook_file_id:
        missing.append("the ebook file (PDF or EPUB)")
    if not (product.author or "").strip():
        missing.append("the author or publisher")
    if missing:
        raise HTTPException(status_code=422, detail="To publish this ebook, add " + " and ".join(missing))


@app.get("/commerce/products", tags=["marketplace"])
def list_products(
    principal: MaybeUser,
    q: str | None = None,
    country: str | None = None,
    kind: str | None = None,
    limit: int = 30,
    offset: int = 0,
    city: str | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    sort: str = "recent",
    mine: bool = False,
    category: str | None = None,
    language: str | None = None,
    ebook: bool | None = None,
    db: OrmSession = Depends(get_db),
):
    if kind and kind not in PRODUCT_KINDS:
        raise HTTPException(status_code=422, detail="kind must be product, service or digital")
    if sort not in PRODUCT_SORTS:
        raise HTTPException(status_code=422, detail="sort must be recent, price_asc or price_desc")
    if mine and principal is None:
        # "My listings" of nobody is empty, not an error: the page is public.
        return {"total": 0, "items": []}

    stmt = select(models.Product).where(models.Product.status == "active")
    if mine and principal is not None:
        stmt = stmt.where(models.Product.vendor_id == principal.user_id)
    if q:
        # Title, and for an ebook its author, description and category too.
        needle = f"%{q.lower().strip()}%"
        stmt = stmt.where(
            func.lower(models.Product.title).like(needle)
            | func.lower(func.coalesce(models.Product.author, "")).like(needle)
            | func.lower(func.coalesce(models.Product.description, "")).like(needle)
            | func.lower(func.coalesce(models.Product.category, "")).like(needle)
        )
    if category:
        stmt = stmt.where(models.Product.category == category)
    if language:
        stmt = stmt.where(models.Product.language == language.lower())
    if ebook is not None:
        stmt = stmt.where(models.Product.ebook.is_(ebook))
    if country:
        stmt = stmt.where(models.Product.country == country.upper())
    if city:
        stmt = stmt.where(func.lower(models.Product.city) == city.strip().lower())
    if kind:
        stmt = stmt.where(models.Product.kind == kind)
    stmt = _price_bounds(stmt, min_price, max_price)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        _sorted(stmt, sort).limit(max(1, min(limit, 100))).offset(max(0, offset))
    ).all()
    vendors = _profiles({r.vendor_id for r in rows})
    ratings = _ratings_for(db, [r.id for r in rows])
    items = []
    for r in rows:
        average, count = ratings.get(r.id, (0.0, 0))
        items.append(
            {
                **_product_item(r, vendors),
                "rating_average": average if count else None,
                "rating_count": count,
            }
        )
    return {"total": total, "items": items}


# --- catalog: detail, seller workspace, storefront ---------------------------
# Order amounts: ``Order.vendor_price`` / ``margin`` / ``customer_price`` are
# totals for the whole order (create_order prices ``unit * quantity``), so the
# seller sums below add them as they are and never multiply by quantity again.

from decimal import ROUND_FLOOR  # noqa: E402

from pydantic import ConfigDict  # noqa: E402

from common.auth import MaybeUser  # noqa: E402

USER_URL = "http://user-service:8000"
PRODUCT_KINDS = ("product", "service", "digital")
PRODUCT_SORTS = ("recent", "price_asc", "price_desc")
ORDER_STATUSES = (
    "pending", "in_escrow", "delivered", "accepted", "settled", "refund_pending", "refunded", "cancelled",
    # Orders from before 27/10 only:
    "disputed", "part_refunded",
)
# While an order is in one of these the money or the goods are still in motion.
OPEN_ORDER_STATUSES = ("pending", "in_escrow", "delivered", "accepted", "refund_pending", "disputed")
SETTLED_STATUSES = ("settled", "part_refunded")
CENT = Decimal("0.01")


def _profiles(user_ids: set[str]) -> dict[str, dict]:
    """Resolve every vendor on the page in one call rather than one per row."""
    if not user_ids:
        return {}
    try:
        response = httpx.post(
            f"{USER_URL}/internal/profiles", json={"ids": sorted(user_ids)}, timeout=5
        )
        response.raise_for_status()
        return response.json()["profiles"]
    except Exception as exc:
        log.warning("could not resolve marketplace members: %s", exc)
        return {}


def _brief(user_id: str, profiles: dict[str, dict]) -> dict:
    p = profiles.get(user_id) or {}
    return {
        "user_id": user_id,
        "handle": p.get("handle"),
        "display_name": p.get("display_name"),
        "avatar_url": p.get("avatar_url"),
    }


def _customer_price(vendor_price: Decimal) -> Decimal:
    return economy.marketplace_pricing(vendor_price)["customer_price"]


def _vendor_bound(bound: Decimal, *, upper: bool) -> Decimal:
    """The vendor price whose customer price sits on ``bound``, to the cent.

    The stored column is the vendor price, so a customer-price bound is turned
    into one by asking the pricing function itself rather than dividing and
    hoping the rounding agrees. ``upper``: the largest vendor price whose
    customer price is <= bound; otherwise the smallest with customer price >= bound.
    """
    bound = min(bound, Decimal("1000000000000"))
    markup = Decimal("1") + Decimal(settings.MARKETPLACE_MARKUP_PCT) / Decimal("100")
    v = (bound / markup).quantize(CENT, rounding=ROUND_FLOOR)
    if upper:
        while _customer_price(v + CENT) <= bound:
            v += CENT
        while v >= 0 and _customer_price(v) > bound:
            v -= CENT
    else:
        while _customer_price(v) < bound:
            v += CENT
        while v > 0 and _customer_price(v - CENT) >= bound:
            v -= CENT
    return v


def _price_bounds(stmt, min_price: Decimal | None, max_price: Decimal | None):
    if min_price is not None and min_price > 0:
        stmt = stmt.where(models.Product.vendor_price >= _vendor_bound(min_price, upper=False))
    if max_price is not None:
        if max_price < 0:
            return stmt.where(models.Product.vendor_price < 0)
        stmt = stmt.where(models.Product.vendor_price <= _vendor_bound(max_price, upper=True))
    return stmt


def _sorted(stmt, sort: str):
    if sort == "price_asc":
        return stmt.order_by(models.Product.vendor_price.asc(), models.Product.created_at.desc())
    if sort == "price_desc":
        return stmt.order_by(models.Product.vendor_price.desc(), models.Product.created_at.desc())
    return stmt.order_by(models.Product.created_at.desc())


def _images(product: models.Product) -> list[str]:
    """The listing's pictures, each with a viewing ticket.

    Uploaded pictures are stored restricted (with UploadCenter, always): the
    media origin serves them only against a ticket. A listing is public, so
    the ticket is an anonymous one - without it, no buyer saw any photo or
    ebook cover. Addresses that are not Kinjy media pass through unchanged.
    """
    out = []
    for url in (product.images or "").split(","):
        if not url:
            continue
        found = _MEDIA_PATH.search(url)
        out.append(mediasign.sign_url(url, found.group(1), None) if found else url)
    return out


def _pricing_out(product: models.Product) -> dict:
    p = economy.marketplace_pricing(product.vendor_price)
    return {
        "vendor_price": str(p["vendor_price"]),
        "margin": str(p["margin"]),
        "customer_price": str(p["customer_price"]),
        "markup_pct": str(p["markup_pct"]),
    }


def _product_item(product: models.Product, profiles: dict[str, dict]) -> dict:
    """The public shape: the buyer sees the customer price and nothing else."""
    return {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "kind": product.kind,
        "vendor_id": product.vendor_id,
        "vendor": _brief(product.vendor_id, profiles),
        "customer_price": str(_customer_price(product.vendor_price)),
        "currency": product.currency,
        "country": product.country,
        "city": product.city,
        "images": _images(product),
        **(
            {
                "ebook": True,
                "subtitle": product.subtitle,
                "author": product.author,
                "category": product.category,
                "language": product.language,
            }
            if product.ebook
            else {}
        ),
    }


def _json_or_none(value: str | None) -> dict | None:
    try:
        return json.loads(value) if value else None
    except ValueError:
        return None


def _protection_out(product: models.Product) -> dict:
    """What the buyer is told before paying: the seller's guarantee, any
    insurance, a service's completion terms, and that Kinjy does not arbitrate.
    ``None`` is stated as none - nothing is implied."""
    return {
        "guarantee": _json_or_none(product.guarantee),
        "insurance": _json_or_none(product.insurance),
        "service_terms": product.service_terms,
        "final_when": "first_download" if product.ebook else "buyer_confirmation",
        "kinjy_arbitrates": False,
        "policy_version": acceptance.POLICY_VERSION,
    }


def _vendor_stats(db: OrmSession, vendor_id: str) -> dict:
    active = db.scalar(
        select(func.count()).select_from(models.Product).where(
            models.Product.vendor_id == vendor_id, models.Product.status == "active"
        )
    )
    sold = db.scalar(
        select(func.count()).select_from(models.Order).where(
            models.Order.vendor_id == vendor_id, models.Order.status.in_(SETTLED_STATUSES)
        )
    )
    return {"listings_active": active or 0, "sales_settled": sold or 0}


def _own_product(db: OrmSession, product_id: str, user_id: str) -> models.Product:
    product = db.get(models.Product, product_id)
    if product is None or product.status == "removed":
        raise HTTPException(status_code=404, detail="Product not found")
    if product.vendor_id != user_id:
        raise HTTPException(status_code=403, detail="This is not your listing")
    return product


@app.get("/commerce/products/mine", tags=["marketplace"])
def my_products(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The seller's own listings, paused ones included, with the full price split."""
    rows = db.scalars(
        select(models.Product)
        .where(models.Product.vendor_id == principal.user_id, models.Product.status != "removed")
        .order_by(models.Product.created_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "kind": r.kind,
                "stock": r.stock,
                "status": r.status,
                "currency": r.currency,
                "country": r.country,
                "city": r.city,
                "images": _images(r),
                "created_at": r.created_at,
                **_pricing_out(r),
                **({"ebook": True, "author": r.author, "has_file": bool(r.ebook_file_id),
                    "suspended_reason": r.suspended_reason} if r.ebook else {}),
            }
            for r in rows
        ]
    }


class ProductBatchIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=100)


@app.post("/internal/products/batch", tags=["internal"])
def products_batch(payload: ProductBatchIn, db: OrmSession = Depends(get_db)):
    """Active listings by id, in their public shape (communities show a
    member's listings this way). Paused or removed ones are left out."""
    if not payload.ids:
        return {"items": []}
    rows = db.scalars(select(models.Product).where(
        models.Product.id.in_(payload.ids), models.Product.status == "active")).all()
    profiles = _profiles({r.vendor_id for r in rows})
    return {"items": [_product_item(r, profiles) for r in rows]}


@app.get("/commerce/products/{product_id}", tags=["marketplace"])
def get_product(product_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    product = db.get(models.Product, product_id)
    owner = principal is not None and principal.user_id == product.vendor_id if product else False
    # A paused listing is the owner's business; to everyone else it is gone.
    if product is None or product.status == "removed" or (product.status != "active" and not owner):
        raise HTTPException(status_code=404, detail="Product not found")

    profiles = _profiles({product.vendor_id})
    out = {
        "id": product.id,
        "title": product.title,
        "description": product.description,
        "kind": product.kind,
        "stock": product.stock,
        "country": product.country,
        "city": product.city,
        "images": _images(product),
        "currency": product.currency,
        "status": product.status,
        "created_at": product.created_at,
        "vendor_id": product.vendor_id,
        "vendor": _brief(product.vendor_id, profiles),
        "vendor_since": (profiles.get(product.vendor_id) or {}).get("created_at"),
        "vendor_stats": _vendor_stats(db, product.vendor_id),
        "is_owner": owner,
        "protection": _protection_out(product),
    }
    if owner:
        out.update(_pricing_out(product))
    else:
        out["customer_price"] = str(_customer_price(product.vendor_price))
    if product.ebook:
        out["ebook"] = _ebook_out(db, product, owner=owner)
        if principal is not None and not owner:
            purchase = _owned_order(db, principal.user_id, product.id)
            out["ebook"]["purchased_order_id"] = purchase.id if purchase else None
    return out


def _ebook_out(db: OrmSession, product: models.Product, *, owner: bool) -> dict:
    """An ebook's public details. The file is described, never addressed."""
    current = db.get(models.EbookFile, product.ebook_file_id) if product.ebook_file_id else None
    out = {
        "subtitle": product.subtitle,
        "author": product.author,
        "summary": product.summary,
        "category": product.category,
        "language": product.language,
        "published_on": product.published_on,
        "edition": product.edition,
        "page_count": product.page_count,
        "isbn": product.isbn,
        "license": product.license or "personal",
        "format": current.file_format if current else None,
        "size_bytes": current.size_bytes if current else None,
        "version": current.version if current else None,
        "updated_at": current.created_at if current else None,
        # The sample is free by design: a public address is fine for it.
        "sample": {"url": product.sample_url, "format": product.sample_format} if product.sample_url else None,
        "download_limit": product.download_limit,
        "access_days": product.access_days,
        "delivery": "Download from your library as soon as the payment is confirmed",
        "description_format": "html",
    }
    if owner:
        out["file"] = (
            {"filename": current.filename, "version": current.version, "size_bytes": current.size_bytes}
            if current else None
        )
        out["suspended_reason"] = product.suspended_reason
        out["sample_asset_id"] = product.sample_asset_id
    return out


class ProductPatch(BaseModel):
    # ``kind`` is not a field: it is immutable, and a body that tries to change
    # it is refused rather than silently ignored.
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = None
    vendor_price: Decimal | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)
    country: str | None = None
    city: str | None = Field(default=None, max_length=120)
    images: list[str] | None = Field(default=None, max_length=5)
    status: str | None = Field(default=None, pattern="^(active|paused|draft)$")
    ebook: EbookIn | None = None
    # Sent as null to state there is none.
    guarantee: GuaranteeIn | None = None
    insurance: InsuranceIn | None = None
    service_terms: str | None = Field(default=None, max_length=3000)

    @field_validator("images")
    @classmethod
    def _check_images(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_images(value)

    @field_validator("country")
    @classmethod
    def _check_country(cls, value: str | None) -> str | None:
        return _clean_country(value)


@app.patch("/commerce/products/{product_id}", tags=["marketplace"])
def update_product(
    product_id: str, payload: ProductPatch, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    product = _own_product(db, product_id, principal.user_id)
    changes = payload.model_dump(exclude_unset=True)
    for required in ("title", "description", "vendor_price", "status", "images"):
        if required in changes and changes[required] is None:
            raise HTTPException(status_code=422, detail=f"{required} cannot be cleared")
    if product.status == "suspended" and "status" in changes:
        raise HTTPException(status_code=409, detail="This listing was suspended by Kinjy; contact support")
    for disclosed in ("guarantee", "insurance"):
        if disclosed in changes:
            changes.pop(disclosed)
            value = getattr(payload, disclosed)
            setattr(product, disclosed, value.model_dump_json() if value else None)
    if "service_terms" in changes:
        terms = (changes.pop("service_terms") or "").strip()
        if product.kind == "service" and not terms:
            raise HTTPException(status_code=422, detail="A service needs its completion terms")
        product.service_terms = terms or None
    ebook_changes = changes.pop("ebook", None)
    if ebook_changes is not None:
        if not product.ebook:
            raise HTTPException(status_code=422, detail="Only an ebook listing has ebook details")
        _apply_ebook(db, product, payload.ebook, principal.user_id, fields=set(payload.ebook.model_fields_set))
    if product.ebook and "description" in changes:
        changes["description"] = sanitize_article(changes["description"] or "")
    if product.ebook:
        changes.pop("stock", None)
        changes.pop("city", None)

    # A price edit only reaches new orders: every order stores the prices it was
    # placed at, so nothing in escrow moves when the listing changes.
    if "images" in changes:
        product.images = ",".join(changes.pop("images")) or None
    for field, value in changes.items():
        setattr(product, field, value)
    if product.ebook and product.status == "active":
        _require_publishable(product)
    db.commit()
    return {"id": product.id, "status": product.status, "pricing": _pricing_out(product)}


@app.delete("/commerce/products/{product_id}", tags=["marketplace"])
def remove_product(product_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    product = _own_product(db, product_id, principal.user_id)
    open_orders = db.scalar(
        select(func.count()).select_from(models.Order).where(
            models.Order.product_id == product.id, models.Order.status.in_(OPEN_ORDER_STATUSES)
        )
    )
    if open_orders:
        raise HTTPException(
            status_code=409, detail="This listing has orders in progress; finish or settle them first"
        )
    # Soft delete: settled orders and disputes keep pointing at a real row.
    product.status = "removed"
    db.commit()
    return {"id": product.id, "status": product.status}


@app.get("/commerce/sales", tags=["marketplace"])
def my_sales(
    principal: CurrentUser,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    if status and status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown order status")
    stmt = select(models.Order).where(models.Order.vendor_id == principal.user_id)
    if status:
        stmt = stmt.where(models.Order.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Order.created_at.desc()).limit(max(1, min(limit, 100))).offset(max(0, offset))
    ).all()

    titles = {
        p.id: p.title
        for p in db.scalars(select(models.Product).where(models.Product.id.in_({r.product_id for r in rows})))
    } if rows else {}
    buyers = _profiles({r.buyer_id for r in rows})
    return {
        "total": total,
        "items": [
            {
                **_order_out(r, principal.user_id),
                "product_title": titles.get(r.product_id),
                "buyer": _brief(r.buyer_id, buyers),
                "to_ship": r.status == "in_escrow" and r.delivered_at is None,
            }
            for r in rows
        ],
    }


@app.get("/commerce/seller/finances", tags=["marketplace"])
def seller_finances(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What the seller is owed, has been paid and lost, in vendor-price terms.

    ``refunded`` is the seller's side of a refund: the whole vendor price of a
    refunded order, plus the part of a part-refunded order that went back to
    the buyer. ``refunded_amount`` on an order is a customer-price figure, so a
    partial settlement is converted back with the same markup _resolve uses.
    """
    orders = db.scalars(select(models.Order).where(models.Order.vendor_id == principal.user_id)).all()
    markup = Decimal("1") + settings.MARKETPLACE_MARKUP_PCT / Decimal("100")
    zero = Decimal("0.00")
    pending = released = refunded = zero
    counts = {"escrow_pending": 0, "to_ship": 0, "settled": 0, "refunded": 0, "disputed": 0}

    for o in orders:
        if o.status in ("in_escrow", "delivered", "disputed"):
            pending += o.vendor_price
            counts["escrow_pending"] += 1
            if o.status == "disputed":
                counts["disputed"] += 1
            if o.status == "in_escrow" and o.delivered_at is None:
                counts["to_ship"] += 1
        elif o.status == "settled":
            released += o.vendor_price
            counts["settled"] += 1
        elif o.status == "part_refunded":
            kept = economy.money((o.customer_price - (o.refunded_amount or 0)) / markup)
            released += kept
            refunded += o.vendor_price - kept
            counts["settled"] += 1
        elif o.status == "refunded":
            refunded += o.vendor_price
            counts["refunded"] += 1

    return {
        "currency": "USD",
        "escrow_pending": str(pending),
        "released": str(released),
        "refunded": str(refunded),
        "counts": counts,
    }


@app.get("/commerce/vendors/{vendor_id}", tags=["marketplace"])
def vendor_storefront(
    vendor_id: str, limit: int = 30, offset: int = 0, db: OrmSession = Depends(get_db)
):
    stats = _vendor_stats(db, vendor_id)
    if not stats["listings_active"] and not stats["sales_settled"]:
        raise HTTPException(status_code=404, detail="Vendor not found")

    stmt = select(models.Product).where(
        models.Product.vendor_id == vendor_id, models.Product.status == "active"
    )
    rows = db.scalars(
        stmt.order_by(models.Product.created_at.desc()).limit(max(1, min(limit, 100))).offset(max(0, offset))
    ).all()
    profiles = _profiles({vendor_id})
    first_listing = db.scalar(
        select(func.min(models.Product.created_at)).where(models.Product.vendor_id == vendor_id)
    )
    return {
        "vendor": _brief(vendor_id, profiles),
        "since": (profiles.get(vendor_id) or {}).get("created_at") or first_listing,
        "stats": stats,
        "total": stats["listings_active"],
        "items": [_product_item(r, profiles) for r in rows],
    }


class DeliveryIn(BaseModel):
    note: str = Field(default="", max_length=2000)
    carrier: str | None = Field(default=None, max_length=80)
    tracking_number: str | None = Field(default=None, max_length=120)
    tracking_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://\S+$")


def _check_evidence(urls: list[str]) -> list[str]:
    """Evidence is rendered as links and images, so it must be a plain http(s)
    or root-relative URL — never a javascript: or data: one."""
    if len(urls) > 5:
        raise ValueError("At most 5 evidence files per message")
    for url in urls:
        if len(url) > 500:
            raise ValueError("An evidence URL is too long")
        if not (url.startswith("/") and not url.startswith("//")) and not re.match(r"^https?://\S+$", url):
            raise ValueError("Evidence must be an http(s) or root-relative URL")
    return urls


class _RetiredDisputeIn(BaseModel):
    category: str = Field(pattern="^(not_received|not_as_described|damaged|unauthorised|other)$")
    reason: str = Field(min_length=10, max_length=4000)
    amount_claimed: Decimal | None = None
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def _evidence_ok(cls, value: list[str]) -> list[str]:
        return _check_evidence(value)


class DisputeMessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    evidence: list[str] = Field(default_factory=list)

    @field_validator("evidence")
    @classmethod
    def _evidence_ok(cls, value: list[str]) -> list[str]:
        return _check_evidence(value)




def _ledger_escrow(
    order: models.Order, action: str, amount: Decimal, description: str = "", key: str = ""
) -> None:
    """Record the movement of custodial money. Raises on failure.

    Deliberately not best-effort: custody that the ledger does not know about is
    money nobody can reconcile.
    """
    response = httpx.post(
        f"{LEDGER_URL}/internal/post/escrow",
        json={
            "order_id": order.id,
            "amount": str(amount),
            "action": action,
            "description": description,
            "idempotency_key": key or f"escrow:{action}:{order.id}",
        },
        timeout=10,
    )
    response.raise_for_status()


def _shipping_of(order: models.Order) -> dict | None:
    try:
        return json.loads(order.shipping_address) if order.shipping_address else None
    except ValueError:
        return None


def _order_out(order: models.Order, viewer_id: str | None = None, *, admin: bool = False) -> dict:
    # The address is a stranger's home: only the two parties of the order (and
    # an admin arbitrating it) get it, everyone else gets the key as null.
    party = admin or (viewer_id is not None and viewer_id in (order.buyer_id, order.vendor_id))
    return {
        "shipping": _shipping_of(order) if party else None,
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "id": order.id,
        "role": None
        if viewer_id is None
        else ("buyer" if order.buyer_id == viewer_id else "seller"),
        "product_id": order.product_id,
        "quantity": order.quantity,
        "customer_price": str(order.customer_price),
        "vendor_price": str(order.vendor_price),
        "margin": str(order.margin),
        "refunded_amount": str(order.refunded_amount or 0),
        "status": order.status,
        "payment_rail": order.payment_rail,
        "escrow_funded_at": order.escrow_funded_at,
        "delivered_at": order.delivered_at,
        "delivery_note": order.delivery_note,
        # The purchase made final: by whom, when (the server's clock), how,
        # and under which policy. The one record both back offices show.
        "acceptance": {
            "accepted": order.accepted_at is not None,
            "status": "buyer_confirmed" if order.acceptance_kind == "buyer_confirmation"
            else "first_download" if order.acceptance_kind == "first_download"
            else "awaiting",
            "accepted_at": order.accepted_at,
            "reference": order.acceptance_ref,
            "kind": order.acceptance_kind,
            "policy_version": order.acceptance_policy,
        },
        # For the seller (and the courier they authorise): what to do with the goods.
        "handover": None if viewer_id != order.vendor_id and not admin else (
            acceptance.POLICY["handover_verified"] if order.accepted_at is not None
            else acceptance.POLICY["handover_awaiting"]
        ),
        # Separate from acceptance: "seller paid" only when this is confirmed.
        "settlement": {
            "status": order.settlement_status,
            "error": order.settlement_error if admin else None,
            "confirmed_at": order.escrow_released_at if order.settlement_status == "confirmed" else None,
        },
        "cancellation": None if order.cancelled_at is None else {
            "cancelled_at": order.cancelled_at,
            "reason": order.cancel_reason,
            "note": order.cancel_note,
            "refund_status": order.refund_status,
            "refund_error": order.refund_error if admin else None,
        },
        "escrow_released_at": order.escrow_released_at,
        "created_at": order.created_at,
    }


@app.get("/commerce/marketplace/policy", tags=["marketplace"])
def marketplace_policy():
    """The marketplace policy a buyer acknowledges, in the words they are shown.

    Public: somebody deciding whether to pay a stranger should be able to read
    it without an account.
    """
    return {
        **acceptance.POLICY,
        "payments": "Payments are processed by Sonco Pay through Kaluta Pay. The buyer's payment is held "
                    "until the purchase is final; the seller is paid only after that.",
        "automatic_release": False,
        "kinjy_arbitrates": False,
    }


# Kept for clients written against the old address; it now says what is true.
@app.get("/commerce/escrow/terms", tags=["marketplace"])
def escrow_terms():
    return marketplace_policy()


@app.post("/commerce/orders", status_code=201, tags=["marketplace"])
async def create_order(payload: OrderIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Places an order. Nothing is posted to the ledger and nothing reaches the
    seller yet — the next step is payment into the custodian's hands."""
    agefeatures.require(principal.user_id, "marketplace_buy")
    if not payload.acknowledge_policy or payload.policy_version != acceptance.POLICY_VERSION:
        raise HTTPException(
            status_code=422,
            detail="Read and accept the marketplace policy before paying: your confirmation (or an ebook's first "
                   "download) makes the purchase final, and Kinjy does not arbitrate ordinary disputes.",
        )

    product = db.get(models.Product, payload.product_id)
    if product is None or product.status != "active":
        raise HTTPException(status_code=404, detail="Product not available")
    if product.vendor_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot buy your own listing")
    if product.ebook:
        # One copy per buyer, and a second checkout is the first one again.
        if payload.quantity != 1:
            raise HTTPException(status_code=422, detail="An ebook is bought one copy at a time")
        if not product.ebook_file_id:
            raise HTTPException(status_code=409, detail="This ebook is not available yet")
        owned = _owned_order(db, principal.user_id, product.id)
        if owned is not None:
            raise HTTPException(status_code=409, detail="You already own this ebook. It is in your library.")
        waiting = db.scalar(
            select(models.Order).where(
                models.Order.buyer_id == principal.user_id,
                models.Order.product_id == product.id,
                models.Order.status == "pending",
            ).order_by(models.Order.created_at.desc())
        )
        if waiting is not None:
            acceptance.record_ack(db, waiting, "checkout", principal.user_id)
            db.commit()
            return {
                "id": waiting.id,
                "status": waiting.status,
                "customer_price": str(waiting.customer_price),
                "policy_version": acceptance.POLICY_VERSION,
                "pay_at": "/api/payments/checkout",
            }
    if product.stock is not None and product.stock < payload.quantity:
        raise HTTPException(status_code=409, detail="Not enough stock")
    # A parcel needs somewhere to go; a service or a file does not.
    if product.kind == "product" and payload.shipping is None:
        raise HTTPException(status_code=422, detail="A shipping address is required for a physical product")
    shipping = None
    if product.kind == "product":
        shipping = payload.shipping.model_dump()
        shipping["country"] = shipping["country"].upper()

    pricing = economy.marketplace_pricing(product.vendor_price * payload.quantity)
    order = models.Order(
        id=new_id("ord"),
        buyer_id=principal.user_id,
        vendor_id=product.vendor_id,
        product_id=product.id,
        quantity=payload.quantity,
        vendor_price=pricing["vendor_price"],
        margin=pricing["margin"],
        customer_price=pricing["customer_price"],
        status="pending",
        shipping_address=json.dumps(shipping) if shipping else None,
    )
    db.add(order)
    db.flush()
    acceptance.record_ack(db, order, "checkout", principal.user_id)
    if product.stock is not None:
        product.stock -= payload.quantity
    db.commit()

    await events.publish("order.created", {"order_id": order.id, "amount": str(order.customer_price)})
    return {
        "id": order.id,
        "status": order.status,
        "customer_price": str(order.customer_price),
        "policy_version": acceptance.POLICY_VERSION,
        "pay_at": "/api/payments/checkout",
    }


@app.get("/internal/orders/{order_id}/quote", tags=["internal"])
def order_quote(order_id: str, db: OrmSession = Depends(get_db)):
    """What payment-service needs before taking money for an order: whose it
    is, what it costs, and whether it is still waiting to be paid."""
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"id": order.id, "buyer_id": order.buyer_id, "customer_price": str(order.customer_price),
            "currency": order.currency, "status": order.status,
            # Which payment opened its escrow: a balance payment that lost the
            # race to another payment gives its money back.
            "custody_ref": order.custody_ref}


@app.post("/internal/orders/{order_id}/funded", tags=["internal"])
def mark_funded(
    order_id: str,
    custody_ref: str | None = None,
    amount: Decimal | None = None,
    rail: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """payment-service calls this once the buyer's payment is confirmed.

    Idempotent: a second confirmation of the same payment changes nothing, so
    a retried or duplicated payment event cannot fulfil, deliver or post twice.
    """
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending":
        return _order_out(order)
    if amount is not None and economy.money(amount) != economy.money(order.customer_price):
        # The payment does not pay this order. Nothing is opened or delivered.
        log.error("order %s funded with %s, expected %s: refused", order_id, amount, order.customer_price)
        raise HTTPException(status_code=409, detail="The payment does not match the order total")

    try:
        _ledger_escrow(order, "hold", order.customer_price, "buyer funds received by custodian")
    except httpx.HTTPError as exc:
        log.error("escrow hold posting failed for %s: %s", order_id, exc)
        raise HTTPException(status_code=503, detail="Could not record the escrow hold; retry")

    order.status = "in_escrow"
    order.custody_ref = custody_ref
    order.payment_rail = (rail or "")[:20] or None
    order.escrow_funded_at = datetime.now(timezone.utc)
    product = db.get(models.Product, order.product_id)
    ebook = product is not None and product.ebook
    if ebook:
        # Available in the buyer's library now. Not final: that is the first
        # successful download, and nothing before it pays the seller.
        order.delivery_note = "Available to download in the buyer's library."
    db.commit()

    if ebook:
        notify(
            order.buyer_id,
            "ebook_ready",
            "Your ebook is ready to download",
            body=f"{product.title} is in your library.",
            link="/market/library",
        )
        notify(
            order.vendor_id,
            "order_funded",
            "An ebook was sold",
            body="It is in the buyer's library. The sale becomes final at their first successful download.",
            link=f"/market/orders/{order.id}",
        )
        return _order_out(order)

    notify(
        order.vendor_id,
        "order_funded",
        "An order is paid and its payment held",
        body="Present the product to the buyer. Hand it over only after their confirmation shows on this order.",
        link=f"/market/orders/{order.id}",
    )
    notify(
        order.buyer_id,
        "order_funded",
        "Your payment is held",
        body="The seller is paid only when you confirm receipt in your Kinjy account, after inspecting the product.",
        link=f"/market/orders/{order.id}",
    )
    return _order_out(order)


# Kept for callers written against the old name - internal only. It was at
# /commerce/orders/{id}/paid, a path the gateway serves to anybody, with no
# authentication: any order could be put in escrow (and an ebook delivered)
# without paying.
@app.post("/internal/orders/{order_id}/paid", tags=["internal"])
def mark_paid(order_id: str, db: OrmSession = Depends(get_db)):
    return mark_funded(order_id, None, None, None, db)


@app.post("/commerce/orders/{order_id}/delivered", tags=["marketplace"])
def mark_delivered(
    order_id: str, payload: DeliveryIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """The seller says the product is on its way or ready for handover, or the
    service is performed and ready for the buyer to assess. Information only:
    it never releases the money - only the buyer's own confirmation does.
    """
    order = db.get(models.Order, order_id)
    if order is None or order.vendor_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ("in_escrow", "delivered"):
        raise HTTPException(status_code=409, detail=f"Order is {order.status}")
    carrier = (payload.carrier or "").strip() or None
    tracking_number = (payload.tracking_number or "").strip() or None
    note = payload.note.strip()
    product = db.get(models.Product, order.product_id)
    if product is not None and product.kind == "product" and not (carrier or tracking_number or note):
        raise HTTPException(
            status_code=422,
            detail="Add a carrier, a tracking number or a note so the buyer can follow the parcel",
        )

    order.status = "delivered"
    order.delivered_at = datetime.now(timezone.utc)
    order.delivery_note = payload.note or None
    order.carrier = carrier
    order.tracking_number = tracking_number
    order.tracking_url = payload.tracking_url
    db.commit()

    notify(
        order.buyer_id,
        "order_delivered",
        "The seller says your order is ready",
        body="Inspect it when it is in front of you. Confirm receipt in your Kinjy account only then.",
        link=f"/market/orders/{order.id}",
    )
    return _order_out(order, principal.user_id)


class ConfirmIn(BaseModel):
    """The buyer's final confirmation, with the policy they were shown again."""

    acknowledge_final: bool = False
    policy_version: str | None = None


@app.post("/commerce/orders/{order_id}/confirm-delivery", tags=["marketplace"])
async def confirm_delivery(
    order_id: str, payload: ConfirmIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """The buyer confirms receipt: the purchase is final and settlement starts.

    Only the buyer, through their own account - never the seller, a courier or
    an admin on their behalf. The order is locked, so a double tap or two tabs
    confirm once; a repeat returns the same acceptance. An ebook is not
    confirmed here: its first successful download makes it final. A service is
    confirmed only after the seller marked it performed.
    """
    order = db.scalar(select(models.Order).where(models.Order.id == order_id).with_for_update())
    if order is None or order.buyer_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.accepted_at is not None:
        db.rollback()
        return _order_out(order, principal.user_id)
    if not payload.acknowledge_final or payload.policy_version != acceptance.POLICY_VERSION:
        raise HTTPException(status_code=422, detail="Confirm that you have read what confirming means")
    product = db.get(models.Product, order.product_id)
    if product is not None and product.ebook:
        raise HTTPException(status_code=409, detail="An ebook becomes final at its first successful download")
    if order.status not in ("in_escrow", "delivered"):
        raise HTTPException(status_code=409, detail=f"This order cannot be confirmed (it is {order.status})")
    if product is not None and product.kind == "service" and order.status != "delivered":
        raise HTTPException(status_code=409,
                            detail="The seller has not marked the service as performed yet: there is nothing to confirm")

    acceptance.record_ack(db, order, "acceptance", principal.user_id)
    acceptance.accept(db, order, by=principal.user_id, kind="buyer_confirmation")
    db.commit()

    notify(
        order.vendor_id,
        "order_accepted",
        "Buyer confirmed receipt",
        body=f"Order {order.id}, ref {order.acceptance_ref}. Verify it on the order, then hand over.",
        link=f"/market/orders/{order.id}",
    )
    await events.publish("order.accepted", {"order_id": order.id, "acceptance_ref": order.acceptance_ref})
    # Settlement starts now. One attempt inline; the worker carries on if a
    # service is slow, and the acceptance stands either way.
    await run_in_threadpool(acceptance.run_settlement, order.id)
    db.refresh(order)
    return _order_out(order, principal.user_id)


# --- cancellation before the purchase is final ------------------------------------------

class CancelIn(BaseModel):
    reason: str = Field(pattern="^(cannot_supply|refused_at_handover|delivery_failed|unpaid)$")
    note: str = Field(default="", max_length=500)


def _cancel(db: OrmSession, order: models.Order, *, by: str, reason: str, note: str) -> None:
    """Cancel a locked order that is not final. Paid: its money goes back to the
    buyer's balance (refund queued). Unpaid: nothing to return; stock comes back."""
    if order.accepted_at is not None or order.status not in ("pending", "in_escrow", "delivered"):
        raise HTTPException(status_code=409, detail=f"This order can no longer be cancelled (it is {order.status})")
    order.cancelled_at = datetime.now(timezone.utc)
    order.cancelled_by = by
    order.cancel_reason = reason
    order.cancel_note = note.strip() or None
    product = db.get(models.Product, order.product_id)
    if product is not None and product.stock is not None:
        product.stock += order.quantity
    if order.status == "pending":
        order.status = "cancelled"
        return
    order.status = "refund_pending"
    order.refund_status = "pending"
    order.refund_attempts = 0
    order.refund_next_at = order.cancelled_at


@app.post("/commerce/orders/{order_id}/cancel", tags=["marketplace"])
async def cancel_order(order_id: str, payload: CancelIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Cancel an order before it is final.

    The seller: they cannot supply it, the buyer refused it at handover, or
    delivery failed - the buyer's payment goes back to their Kinjy balance.
    The buyer: only an order not paid yet. Nothing here charges a fee.
    """
    order = db.scalar(select(models.Order).where(models.Order.id == order_id).with_for_update())
    if order is None or principal.user_id not in (order.buyer_id, order.vendor_id):
        raise HTTPException(status_code=404, detail="Order not found")
    role = "buyer" if principal.user_id == order.buyer_id else "seller"
    allowed_role = acceptance.CANCEL_REASONS[payload.reason][0]
    if role != allowed_role:
        raise HTTPException(status_code=403, detail="That reason is not yours to give on this order")
    if role == "buyer" and order.status != "pending":
        raise HTTPException(status_code=409, detail="A paid order is cancelled by the seller or by Kinjy support")
    _cancel(db, order, by=principal.user_id, reason=payload.reason, note=payload.note)
    db.commit()
    other = order.buyer_id if role == "seller" else order.vendor_id
    notify(other, "order_cancelled", "An order was cancelled",
           body=acceptance.CANCEL_REASONS[payload.reason][1], link=f"/market/orders/{order.id}")
    if order.refund_status == "pending":
        await run_in_threadpool(acceptance.run_refund, order.id)
        db.refresh(order)
    return _order_out(order, principal.user_id)


class AdminCancelIn(BaseModel):
    note: str = Field(min_length=3, max_length=500)


@app.post("/admin/commerce/orders/{order_id}/cancel", tags=["admin"])
async def admin_cancel_order(order_id: str, payload: AdminCancelIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """An operational correction by Kinjy - a payment problem, a duplicate
    charge, fraud, a legal requirement - on an order that is not final yet."""
    order = db.scalar(select(models.Order).where(models.Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    _cancel(db, order, by=admin.user_id, reason="operational", note=payload.note)
    _audit(db, admin.user_id, "order_cancel", "order", order.id, payload.note)
    db.commit()
    notify_many([order.buyer_id, order.vendor_id], "order_cancelled", "An order was cancelled by Kinjy",
                body=payload.note, link=f"/market/orders/{order.id}")
    if order.refund_status == "pending":
        await run_in_threadpool(acceptance.run_refund, order.id)
        db.refresh(order)
    return _order_out(order, admin=True)


@app.post("/admin/commerce/orders/{order_id}/retry", tags=["admin"])
async def admin_retry_order(order_id: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Try a settlement or refund that needed a person again, once the cause
    (a provider setting, a missing account) is fixed. Every step is idempotent."""
    order = db.scalar(select(models.Order).where(models.Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    retried = None
    if order.settlement_status == "action_required":
        order.settlement_status, order.settlement_attempts, order.settlement_next_at = "pending", 0, None
        retried = "settlement"
    elif order.refund_status == "action_required":
        order.refund_status, order.refund_attempts, order.refund_next_at = "pending", 0, None
        retried = "refund"
    else:
        raise HTTPException(status_code=409, detail="Nothing on this order is waiting for a person")
    _audit(db, admin.user_id, f"order_retry_{retried}", "order", order.id, None)
    db.commit()
    runner = acceptance.run_settlement if retried == "settlement" else acceptance.run_refund
    await run_in_threadpool(runner, order.id)
    db.refresh(order)
    return _order_out(order, admin=True)


@app.get("/admin/commerce/orders", tags=["admin"])
def admin_orders(_: AdminUser, attention: bool = False, limit: int = 100, db: OrmSession = Depends(get_db)):
    """Orders, newest first. ``attention``: a settlement or refund waiting for a
    person, or still pending."""
    stmt = select(models.Order).order_by(models.Order.created_at.desc())
    if attention:
        stmt = stmt.where(
            models.Order.settlement_status.in_(("pending", "action_required"))
            | models.Order.refund_status.in_(("pending", "action_required"))
        )
    rows = db.scalars(stmt.limit(min(limit, 300))).all()
    return {"items": [_order_out(o, admin=True) for o in rows]}


# --- operational support (not arbitration) --------------------------------------------

class SupportIn(BaseModel):
    kind: str = Field(pattern="^(payment_problem|duplicate_charge|not_handed_over|file_problem|fraud|legal)$")
    message: str = Field(min_length=10, max_length=4000)


def _support_out(case: models.SupportCase) -> dict:
    return {
        "id": case.id, "order_id": case.order_id, "role": case.role, "kind": case.kind,
        "message": case.message, "status": case.status, "resolution_note": case.resolution_note,
        "created_at": case.created_at, "closed_at": case.closed_at,
    }


@app.post("/commerce/orders/{order_id}/support", status_code=201, tags=["marketplace"])
def open_support_case(order_id: str, payload: SupportIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Ask Kinjy support for help with an order: a payment problem, a duplicate
    charge, an item not handed over after confirmation, a file problem, fraud,
    a legal request. Not a dispute: no money moves because a case is opened."""
    order = db.get(models.Order, order_id)
    if order is None or principal.user_id not in (order.buyer_id, order.vendor_id):
        raise HTTPException(status_code=404, detail="Order not found")
    case = models.SupportCase(
        order_id=order.id, opened_by=principal.user_id,
        role="buyer" if principal.user_id == order.buyer_id else "seller",
        kind=payload.kind, message=payload.message.strip(),
    )
    db.add(case)
    db.commit()
    return _support_out(case)


@app.get("/commerce/support/me", tags=["marketplace"])
def my_support_cases(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(select(models.SupportCase).where(models.SupportCase.opened_by == principal.user_id)
                      .order_by(models.SupportCase.created_at.desc()).limit(100)).all()
    return {"items": [_support_out(c) for c in rows]}


@app.get("/admin/commerce/support", tags=["admin"])
def admin_support_cases(_: AdminUser, status: str | None = "open", db: OrmSession = Depends(get_db)):
    stmt = select(models.SupportCase).order_by(models.SupportCase.created_at)
    if status:
        stmt = stmt.where(models.SupportCase.status == status)
    return {"items": [_support_out(c) for c in db.scalars(stmt.limit(300)).all()]}


class CloseCaseIn(BaseModel):
    note: str = Field(min_length=3, max_length=4000)


@app.post("/admin/commerce/support/{case_id}/close", tags=["admin"])
def close_support_case(case_id: str, payload: CloseCaseIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    case = db.get(models.SupportCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.status == "closed":
        raise HTTPException(status_code=409, detail="This case is already closed")
    case.status, case.resolution_note = "closed", payload.note
    case.closed_by, case.closed_at = admin.user_id, datetime.now(timezone.utc)
    _audit(db, admin.user_id, "support_close", "order", case.order_id, payload.note[:500])
    db.commit()
    notify(case.opened_by, "support_closed", "Kinjy support answered your request", body=payload.note[:300],
           link=f"/market/orders/{case.order_id}")
    return _support_out(case)


def _order_extras(db: OrmSession, orders: list[models.Order], viewer_id: str) -> dict[str, dict]:
    """What a screen needs to show an order as more than a number: the product
    bought, the other party, and whether the viewer already reviewed it.

    One query per kind of thing for the whole page, never one per order. A product
    the seller later removed is still returned: the buyer bought it.
    """
    if not orders:
        return {}
    products = {
        p.id: p
        for p in db.scalars(
            select(models.Product).where(models.Product.id.in_({o.product_id for o in orders}))
        ).all()
    }
    other_party = {o.id: (o.vendor_id if o.buyer_id == viewer_id else o.buyer_id) for o in orders}
    profiles = _profiles(set(other_party.values()))
    reviewed = set(
        db.scalars(
            select(models.ProductReview.order_id).where(
                models.ProductReview.order_id.in_([o.id for o in orders]),
                models.ProductReview.author_id == viewer_id,
            )
        ).all()
    )
    extras: dict[str, dict] = {}
    for o in orders:
        p = products.get(o.product_id)
        brief = profiles.get(other_party[o.id])
        extras[o.id] = {
            "product": None
            if p is None
            else {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "kind": p.kind,
                "images": _images(p),
                "country": p.country,
                "city": p.city,
                "available": p.status == "active",
                "ebook": bool(p.ebook),
                "author": p.author,
            },
            "counterpart": None
            if brief is None
            else {
                "user_id": other_party[o.id],
                "handle": brief.get("handle"),
                "display_name": brief.get("display_name"),
                "avatar_url": brief.get("avatar_url"),
            },
            "reviewed": o.id in reviewed,
        }
    return extras




@app.get("/commerce/orders/me", tags=["marketplace"])
def my_orders(
    principal: CurrentUser,
    status: str | None = None,
    role: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """My orders, newest first. `role` = buyer or seller narrows to what I bought
    or what I sold; `status` narrows to one stage of the order."""
    if status and status not in ORDER_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of: {', '.join(ORDER_STATUSES)}")
    if role and role not in ("buyer", "seller"):
        raise HTTPException(status_code=422, detail="role must be buyer or seller")
    stmt = select(models.Order)
    if role == "buyer":
        stmt = stmt.where(models.Order.buyer_id == principal.user_id)
    elif role == "seller":
        stmt = stmt.where(models.Order.vendor_id == principal.user_id)
    else:
        stmt = stmt.where(
            (models.Order.buyer_id == principal.user_id) | (models.Order.vendor_id == principal.user_id)
        )
    if status:
        stmt = stmt.where(models.Order.status == status)
    rows = db.scalars(stmt.order_by(models.Order.created_at.desc())).all()
    extras = _order_extras(db, rows, principal.user_id)
    return {"items": [{**_order_out(r, principal.user_id), **extras.get(r.id, {})} for r in rows]}


@app.get("/commerce/orders/{order_id}", tags=["marketplace"])
def get_order(order_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None or principal.user_id not in (order.buyer_id, order.vendor_id):
        raise HTTPException(status_code=404, detail="Order not found")
    product = db.get(models.Product, order.product_id)
    return {
        **_order_out(order, principal.user_id),
        "protection": _protection_out(product) if product else None,
        "final_when": "first_download" if product is not None and product.ebook else "buyer_confirmation",
        "can_confirm": (
            principal.user_id == order.buyer_id and order.accepted_at is None
            and product is not None and not product.ebook
            and (order.status == "delivered" if product.kind == "service" else order.status in ("in_escrow", "delivered"))
        ),
        **_order_extras(db, [order], principal.user_id).get(order.id, {}),
    }


# ---------------------------------------------------------------------------
# Disputes
# ---------------------------------------------------------------------------

def _dispute_out(dispute: models.Dispute, messages: list[models.DisputeMessage] | None = None) -> dict:
    out = {
        "id": dispute.id,
        "order_id": dispute.order_id,
        "opened_by": dispute.opened_by,
        "against": dispute.against,
        "role": dispute.role,
        "category": dispute.category,
        "reason": dispute.reason,
        "amount_claimed": str(dispute.amount_claimed),
        "status": dispute.status,
        "outcome": dispute.outcome,
        "refund_amount": str(dispute.refund_amount),
        "resolution_note": dispute.resolution_note,
        "respond_by": dispute.respond_by,
        "arbitrate_by": dispute.arbitrate_by,
        "resolved_at": dispute.resolved_at,
        "created_at": dispute.created_at,
    }
    if messages is not None:
        out["messages"] = [
            {
                "author_id": m.author_id,
                "author_role": m.author_role,
                "body": m.body,
                "evidence": [e for e in (m.evidence or "").split("\n") if e],
                "created_at": m.created_at,
            }
            for m in messages
        ]
    return out


def _load_dispute(db: OrmSession, dispute_id: str, user_id: str) -> tuple[models.Dispute, models.Order, str]:
    dispute = db.get(models.Dispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="Dispute not found")
    order = db.get(models.Order, dispute.order_id)
    if order is None or user_id not in (order.buyer_id, order.vendor_id):
        raise HTTPException(status_code=404, detail="Dispute not found")
    return dispute, order, "buyer" if order.buyer_id == user_id else "seller"


# Retired 27/10: Kinjy does not arbitrate ordinary marketplace disputes. The
# history (and the GET routes below) stays; nothing new can be opened or moved.
@app.post("/commerce/orders/{order_id}/dispute", tags=["disputes"])
def open_dispute(order_id: str):
    raise HTTPException(
        status_code=410,
        detail="Kinjy no longer arbitrates marketplace disputes. Before the purchase is final, talk to the "
               "seller; after it, use the seller's guarantee or insurance. For payment problems, an item not "
               "handed over after confirmation or fraud, contact Kinjy support from the order.",
    )


@app.get("/commerce/disputes/me", tags=["disputes"])
def my_disputes(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.Dispute)
        .where(
            (models.Dispute.opened_by == principal.user_id)
            | (models.Dispute.against == principal.user_id)
        )
        .order_by(models.Dispute.created_at.desc())
    ).all()
    return {"items": [_dispute_out(d) for d in rows]}


@app.get("/commerce/disputes/{dispute_id}", tags=["disputes"])
def get_dispute(dispute_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if principal.is_admin:
        dispute = db.get(models.Dispute, dispute_id)
        order = db.get(models.Order, dispute.order_id) if dispute else None
        if dispute is None or order is None:
            raise HTTPException(status_code=404, detail="Dispute not found")
        viewer_role = "admin"
    else:
        dispute, order, viewer_role = _load_dispute(db, dispute_id, principal.user_id)
    messages = db.scalars(
        select(models.DisputeMessage)
        .where(models.DisputeMessage.dispute_id == dispute_id)
        .order_by(models.DisputeMessage.created_at, models.DisputeMessage.id)
    ).all()
    return _dispute_detail(db, dispute, order, messages, viewer_role, principal.user_id)


@app.post("/commerce/disputes/{dispute_id}/messages", tags=["disputes"])
@app.post("/commerce/disputes/{dispute_id}/withdraw", tags=["disputes"])
@app.post("/commerce/disputes/{dispute_id}/concede", tags=["disputes"])
def retired_dispute_action(dispute_id: str):
    raise HTTPException(
        status_code=410,
        detail="Kinjy no longer arbitrates marketplace disputes. Before the purchase is final, talk to the "
               "seller; after it, use the seller's guarantee or insurance. For payment problems, an item not "
               "handed over after confirmation or fraud, contact Kinjy support from the order.",
    )


@app.get("/admin/disputes", tags=["disputes"])
def admin_disputes(
    _: AdminUser, status: str | None = None, limit: int = 50, db: OrmSession = Depends(get_db)
):
    stmt = select(models.Dispute).order_by(models.Dispute.created_at)
    if status:
        stmt = stmt.where(models.Dispute.status == status)
    rows = db.scalars(stmt.limit(min(limit, 200))).all()
    return {"items": [_dispute_out(d) for d in rows]}


@app.get("/admin/disputes/{dispute_id}", tags=["disputes"])
def admin_dispute(dispute_id: str, _: AdminUser, db: OrmSession = Depends(get_db)):
    dispute = db.get(models.Dispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="Dispute not found")
    messages = db.scalars(
        select(models.DisputeMessage)
        .where(models.DisputeMessage.dispute_id == dispute_id)
        .order_by(models.DisputeMessage.created_at)
    ).all()
    order = db.get(models.Order, dispute.order_id)
    return {
        **_dispute_out(dispute, messages),
        "order": _order_out(order, admin=True) if order else None,
    }


@app.post("/admin/disputes/{dispute_id}/resolve", tags=["disputes"])
def retired_resolution(dispute_id: str, _: AdminUser):
    raise HTTPException(
        status_code=410,
        detail="Kinjy no longer arbitrates marketplace disputes. Before the purchase is final, talk to the "
               "seller; after it, use the seller's guarantee or insurance. For payment problems, an item not "
               "handed over after confirmation or fraud, contact Kinjy support from the order.",
    )


# Retired 27/10 with the automatic release: nothing pays a seller because time
# passed. Settlement and refunds run in acceptance.worker_loop.
@app.post("/internal/escrow/sweep", tags=["internal"])
def escrow_sweep():
    raise HTTPException(status_code=410, detail="There is no automatic release any more")


# --- advertising -----------------------------------------------------------

@app.get("/ads/rate-card", tags=["ads"])
def rate_card():
    return {
        "floors": models.AD_FLOORS,
        "currency": "USD",
        "note": "Floors are minimums for the auction. The AI adjusts bids upward by demand, never below the floor.",
        "sponsorships": {
            "country": "by auction",
            "global": "by auction",
        },
    }


@app.post("/ads/campaigns", status_code=201, tags=["ads"])
def create_campaign(payload: CampaignIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    floor = models.AD_FLOORS.get(payload.pricing_model)
    if floor is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown pricing model. Available: {', '.join(models.AD_FLOORS)}",
        )
    if payload.bid < Decimal(floor):
        raise HTTPException(status_code=400, detail=f"Bid is below the {payload.pricing_model} floor of ${floor}")

    campaign = models.Campaign(
        id=new_id("cmp"), advertiser_id=principal.user_id, status="draft", **payload.model_dump()
    )
    db.add(campaign)
    db.commit()
    return {"id": campaign.id, "status": campaign.status, "floor": floor}


@app.post("/ads/campaigns/{campaign_id}/submit", tags=["ads"])
def submit_campaign(campaign_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    campaign = db.get(models.Campaign, campaign_id)
    if campaign is None or campaign.advertiser_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not campaign.creative_headline:
        raise HTTPException(status_code=400, detail="A campaign needs a creative before review")
    campaign.status = "pending_approval"
    db.commit()
    return {"id": campaign_id, "status": campaign.status}


@app.post("/admin/ads/campaigns/{campaign_id}/approve", tags=["ads"])
async def approve_campaign(campaign_id: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Human approval before launch — required even when the AI wrote everything."""
    campaign = db.get(models.Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = "active"
    campaign.approved_by = admin.user_id
    db.commit()

    # The advertiser's spend is Kinjy revenue: their own sponsor is paid the
    # direct commission on it, and the Leaders pool takes its share.
    try:
        httpx.post(
            f"{LEDGER_URL}/internal/post/ad-purchase",
            json={
                "advertiser_id": campaign.advertiser_id,
                "amount": str(campaign.budget),
                "campaign_id": campaign.id,
                "idempotency_key": f"campaign:{campaign.id}",
            },
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        log.error("ad purchase posting failed for %s: %s", campaign_id, exc)
        raise HTTPException(status_code=503, detail="Campaign approved but the ledger posting failed; retry")

    await events.publish("ad.purchased", {"campaign_id": campaign.id, "amount": str(campaign.budget)})
    return {"id": campaign_id, "status": campaign.status}


@app.get("/ads/campaigns/me", tags=["ads"])
def my_campaigns(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.Campaign).where(models.Campaign.advertiser_id == principal.user_id)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "objective": r.objective,
                "pricing_model": r.pricing_model,
                "bid": str(r.bid),
                "budget": str(r.budget),
                "spent": str(r.spent),
                "status": r.status,
                "ai_generated": r.ai_generated,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# Verified reviews and the dispute detail
# ---------------------------------------------------------------------------

USER_URL = "http://user-service:8000"


def _profiles(user_ids: set[str]) -> dict[str, dict]:
    """Resolve everyone on the page in one call; a user-service outage costs
    the names, never the page."""
    user_ids = {i for i in user_ids if i}
    if not user_ids:
        return {}
    try:
        response = httpx.post(
            f"{USER_URL}/internal/profiles", json={"ids": sorted(user_ids)}, timeout=5
        )
        response.raise_for_status()
        return response.json()["profiles"]
    except Exception as exc:
        log.warning("could not resolve commerce members: %s", exc)
        return {}


def _seconds_left(deadline: datetime | None, now: datetime) -> int | None:
    if deadline is None:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return max(0, int((deadline - now).total_seconds()))


def _dispute_detail(
    db: OrmSession,
    dispute: models.Dispute,
    order: models.Order,
    messages: list[models.DisputeMessage],
    viewer_role: str,
    viewer_id: str,
) -> dict:
    """Everything a dispute screen needs in one payload. The ``can_*`` flags
    mirror the checks in the concede / withdraw / message routes."""
    now = datetime.now(timezone.utc)
    open_case = dispute.status not in ("resolved", "withdrawn")
    profiles = _profiles({order.buyer_id, order.vendor_id, *(m.author_id for m in messages)})
    product = db.get(models.Product, order.product_id)

    out = _dispute_out(dispute, messages)
    out["messages"] = [
        {**shown, "author": profiles.get(m.author_id)} for shown, m in zip(out["messages"], messages)
    ]
    out.update(
        viewer_role=viewer_role,
        can_reply=open_case and viewer_role != "admin",
        can_withdraw=open_case and viewer_id == dispute.opened_by,
        can_concede=open_case and viewer_id == dispute.against,
        # Only an unanswered case is waiting on the respondent; once anyone has
        # answered, the arbitration deadline is the one that still runs.
        seconds_left_to_respond=_seconds_left(dispute.respond_by, now) if dispute.status == "open" else None,
        seconds_left_to_arbitrate=_seconds_left(dispute.arbitrate_by, now) if open_case else None,
        buyer={"id": order.buyer_id, "profile": profiles.get(order.buyer_id)},
        seller={"id": order.vendor_id, "profile": profiles.get(order.vendor_id)},
        order={
            "id": order.id,
            "product_id": order.product_id,
            "title": product.title if product else None,
            "quantity": order.quantity,
            "customer_price": str(order.customer_price),
            "currency": order.currency,
            "status": order.status,
        },
    )
    return out


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


def _ratings_for(db: OrmSession, product_ids: list[str]) -> dict[str, tuple[float, int]]:
    """{product_id: (average, count)} for a page of products in one query.
    Products with no review are absent: callers default to (0.0, 0)."""
    if not product_ids:
        return {}
    rows = db.execute(
        select(
            models.ProductReview.product_id,
            func.avg(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .where(models.ProductReview.product_id.in_(product_ids))
        .group_by(models.ProductReview.product_id)
    ).all()
    return {pid: (round(float(avg), 2), int(n)) for pid, avg, n in rows}


@app.post("/commerce/orders/{order_id}/review", status_code=201, tags=["reviews"])
def review_order(
    order_id: str, payload: ReviewIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """The buyer rates what they bought. Only a ``settled`` order qualifies: the
    money was released with nothing refunded, so the buyer actually received the
    goods. A part-refunded or refunded order is a dispute outcome, not a sale
    worth rating."""
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.buyer_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the buyer can review an order")
    if order.status != "settled":
        raise HTTPException(status_code=409, detail="You can review an order once it is settled")
    if db.scalar(select(models.ProductReview.id).where(models.ProductReview.order_id == order_id)):
        raise HTTPException(status_code=409, detail="You already reviewed this order")

    review = models.ProductReview(
        id=new_id("rev"),
        order_id=order.id,
        product_id=order.product_id,
        vendor_id=order.vendor_id,
        author_id=principal.user_id,
        rating=payload.rating,
        comment=(payload.comment or "").strip() or None,
    )
    db.add(review)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="You already reviewed this order")

    notify(
        order.vendor_id,
        "order_reviewed",
        f"You received a {review.rating}-star review",
        link="/market/seller",
    )
    return {
        "id": review.id,
        "order_id": review.order_id,
        "product_id": review.product_id,
        "rating": review.rating,
        "comment": review.comment,
        "created_at": review.created_at,
    }


@app.get("/commerce/products/{product_id}/reviews", tags=["reviews"])
def product_reviews(
    product_id: str, limit: int = 20, offset: int = 0, db: OrmSession = Depends(get_db)
):
    if db.get(models.Product, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    where = models.ProductReview.product_id == product_id
    counts = dict(
        db.execute(
            select(models.ProductReview.rating, func.count(models.ProductReview.id))
            .where(where)
            .group_by(models.ProductReview.rating)
        ).all()
    )
    count = sum(counts.values())
    average = round(sum(r * n for r, n in counts.items()) / count, 2) if count else 0.0

    rows = db.scalars(
        select(models.ProductReview)
        .where(where)
        .order_by(models.ProductReview.created_at.desc(), models.ProductReview.id)
        .limit(max(1, min(limit, 50)))
        .offset(max(0, offset))
    ).all()
    profiles = _profiles({r.author_id for r in rows})
    return {
        "average": average,
        "count": count,
        "distribution": {str(star): int(counts.get(star, 0)) for star in range(1, 6)},
        "items": [
            {
                "id": r.id,
                "rating": r.rating,
                "comment": r.comment,
                "created_at": r.created_at,
                "author_id": r.author_id,
                "reviewer": profiles.get(r.author_id),
            }
            for r in rows
        ],
    }


@app.get("/commerce/vendors/{vendor_id}/rating", tags=["reviews"])
def vendor_rating(vendor_id: str, db: OrmSession = Depends(get_db)):
    avg, n = db.execute(
        select(func.avg(models.ProductReview.rating), func.count(models.ProductReview.id)).where(
            models.ProductReview.vendor_id == vendor_id
        )
    ).one()
    return {"vendor_id": vendor_id, "average": round(float(avg), 2) if n else 0.0, "count": int(n)}



# --- ebook library and downloads --------------------------------------------------
#
# Policy, in one place:
# - access starts when the server confirms the payment (the order is funded),
#   never on a redirect or anything the browser says;
# - it lasts while the order is held, delivered, settled, partly refunded or
#   in dispute; a full refund ends it, and so does an admin revocation;
# - the listing being paused, unpublished or removed does not end it;
# - every buyer downloads the current version of the file;
# - limits (a number of downloads, a number of days) are the seller's choice
#   per ebook, and there are none unless they set them.

ENTITLED_STATUSES = ("in_escrow", "delivered", "accepted", "settled", "part_refunded", "disputed")
_download_requests: dict[str, list[float]] = {}
_download_lock = threading.Lock()


def _owned_order(db: OrmSession, buyer_id: str, product_id: str) -> models.Order | None:
    """The buyer's paid order for this ebook, if they own it."""
    return db.scalar(
        select(models.Order).where(
            models.Order.buyer_id == buyer_id,
            models.Order.product_id == product_id,
            models.Order.status.in_(ENTITLED_STATUSES),
            models.Order.access_revoked_at.is_(None),
        ).order_by(models.Order.created_at.desc())
    )


def _access(order: models.Order, product: models.Product | None, used: int) -> dict:
    """Whether this order may download now, and why not if it may not."""
    until = None
    if product is not None and product.access_days and order.escrow_funded_at:
        until = order.escrow_funded_at + timedelta(days=product.access_days)
    if order.status in ("refunded", "cancelled") or order.status == "pending":
        state = "refunded" if order.status == "refunded" else "unpaid"
    elif order.access_revoked_at is not None:
        state = "revoked"
    elif until is not None and until < datetime.now(timezone.utc):
        state = "expired"
    elif product is not None and product.download_limit and used >= product.download_limit:
        state = "limit_reached"
    elif product is None or not product.ebook_file_id:
        state = "unavailable"
    else:
        state = "active"
    return {"state": state, "access_until": until, "downloads_used": used,
            "download_limit": product.download_limit if product else None}


def _downloads_used(db: OrmSession, order: models.Order) -> int:
    stmt = select(func.count()).select_from(models.EbookDownload).where(
        models.EbookDownload.order_id == order.id, models.EbookDownload.counted.is_(True)
    )
    if order.downloads_reset_at is not None:
        stmt = stmt.where(models.EbookDownload.created_at > order.downloads_reset_at)
    return db.scalar(stmt) or 0


@app.get("/commerce/library", tags=["ebooks"])
def my_library(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The buyer's ebooks: every paid ebook order, with what they can do now."""
    orders = db.scalars(
        select(models.Order).where(
            models.Order.buyer_id == principal.user_id,
            models.Order.status.notin_(("pending", "cancelled")),
        ).order_by(models.Order.created_at.desc())
    ).all()
    products = {
        p.id: p for p in db.scalars(
            select(models.Product).where(models.Product.id.in_({o.product_id for o in orders} or {""}),
                                         models.Product.ebook.is_(True))
        ).all()
    }
    orders = [o for o in orders if o.product_id in products]
    files = {
        f.id: f for f in db.scalars(
            select(models.EbookFile).where(models.EbookFile.id.in_(
                {p.ebook_file_id for p in products.values() if p.ebook_file_id} or {0}))
        ).all()
    }
    last_file = dict(db.execute(
        select(models.EbookDownload.order_id, func.max(models.EbookDownload.file_id))
        .where(models.EbookDownload.order_id.in_([o.id for o in orders] or [""]))
        .group_by(models.EbookDownload.order_id)
    ).all())
    profiles = _profiles({p.vendor_id for p in products.values()})
    items = []
    for o in orders:
        p = products[o.product_id]
        current = files.get(p.ebook_file_id)
        access = _access(o, p, _downloads_used(db, o))
        items.append({
            "order_id": o.id,
            "status": o.status,
            "purchased_at": o.escrow_funded_at or o.created_at,
            "product": {
                "id": p.id, "title": p.title, "subtitle": p.subtitle, "author": p.author,
                "images": _images(p), "available": p.status == "active",
            },
            "seller": _brief(p.vendor_id, profiles),
            "file": {"format": current.file_format, "size_bytes": current.size_bytes,
                     "version": current.version, "updated_at": current.created_at} if current else None,
            # A newer file than the one this buyer last downloaded.
            "update_available": bool(current and last_file.get(o.id) and last_file[o.id] != current.id),
            **access,
        })
    return {"items": items}


def _download_allowed(buyer_id: str) -> bool:
    now = time.time()
    with _download_lock:
        recent = [t for t in _download_requests.get(buyer_id, []) if now - t < 60]
        if len(_download_requests) > 5000:
            _download_requests.clear()
        if len(recent) >= settings.EBOOK_LINKS_PER_MINUTE:
            _download_requests[buyer_id] = recent
            return False
        recent.append(now)
        _download_requests[buyer_id] = recent
        return True


class DownloadIn(BaseModel):
    # Required before the first successful download: it makes the purchase final.
    acknowledge_final: bool = False
    policy_version: str | None = None


@app.post("/commerce/library/{order_id}/download", tags=["ebooks"])
def download_ebook(
    order_id: str, principal: CurrentUser, payload: DownloadIn | None = None, db: OrmSession = Depends(get_db)
):
    """A short-lived link to the ebook, for its buyer, checked every time.

    The order must be the caller's, paid as confirmed by the server, not
    refunded or revoked, within any access period and download limit. The
    link is signed for this file only and stops working after
    EBOOK_LINK_TTL_S; asking again gives a fresh one. Asking again within
    EBOOK_RETRY_GRACE_S of the last link is a retry and is not counted.
    """
    order = db.get(models.Order, order_id)
    # Somebody else's order and no order look the same.
    if order is None or order.buyer_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    product = db.get(models.Product, order.product_id)
    if product is None or not product.ebook:
        raise HTTPException(status_code=404, detail="Order not found")
    if not _download_allowed(principal.user_id):
        raise HTTPException(status_code=429, detail="Too many download requests. Wait a minute and try again.")

    file = db.get(models.EbookFile, product.ebook_file_id) if product.ebook_file_id else None
    last = db.scalar(
        select(models.EbookDownload).where(models.EbookDownload.order_id == order.id)
        .order_by(models.EbookDownload.created_at.desc()).limit(1)
    )
    retry = (
        last is not None and file is not None and last.file_id == file.id
        and (datetime.now(timezone.utc) - last.created_at).total_seconds() < settings.EBOOK_RETRY_GRACE_S
        and (order.downloads_reset_at is None or last.created_at > order.downloads_reset_at)
    )
    used = _downloads_used(db, order)
    access = _access(order, product, used if not retry else 0)
    messages = {
        "unpaid": "This order has not been paid yet. The download opens once the payment is confirmed.",
        "refunded": "This order was refunded, so the ebook is no longer available to download.",
        "revoked": "Access to this ebook was withdrawn. Contact support if you think this is a mistake.",
        "expired": "The download period for this ebook has ended.",
        "limit_reached": "You have used all the downloads this ebook allows. Contact the seller or support.",
        "unavailable": "The file is not available right now. Try again later or contact the seller.",
    }
    if access["state"] != "active":
        raise HTTPException(status_code=403, detail=messages[access["state"]])
    if order.accepted_at is None:
        if payload is None or not payload.acknowledge_final or payload.policy_version != acceptance.POLICY_VERSION:
            raise HTTPException(
                status_code=428,
                detail="Your first successful download makes this purchase final. Confirm that you have read this.",
            )
        acceptance.record_ack(db, order, "download", principal.user_id)

    download = models.EbookDownload(order_id=order.id, buyer_id=principal.user_id, file_id=file.id, counted=not retry)
    db.add(download)
    db.flush()
    signed = crypto.sign_media_url(file.url, file.asset_id, ttl_s=settings.EBOOK_LINK_TTL_S)
    expires = int(signed.split("exp=", 1)[1].split("&", 1)[0])
    receipt = crypto.download_receipt(file.asset_id, download.id, expires)
    db.commit()
    return {
        # dlr/dls: media-service reports the last byte sent on this link, and only then is it a download.
        "url": f"{signed}&dl=1&dlr={download.id}&dls={receipt}",
        "expires_in": settings.EBOOK_LINK_TTL_S,
        "filename": file.filename,
        "format": file.file_format,
        "size_bytes": file.size_bytes,
        "version": file.version,
        "counted": not retry,
        "downloads_used": used + (0 if retry else 1),
        "download_limit": product.download_limit,
    }


@app.post("/internal/ebook-downloads/{download_id}/completed", tags=["internal"])
async def ebook_download_completed(download_id: int, asset_id: str, db: OrmSession = Depends(get_db)):
    """media-service sent the last byte of the file on this download's link.

    The first time it happens on an order that is not final, the purchase
    becomes final and settlement starts. Later completions, or a second report
    of the same one, change nothing.
    """
    download = db.get(models.EbookDownload, download_id)
    file = db.get(models.EbookFile, download.file_id) if download else None
    if download is None or file is None or file.asset_id != asset_id:
        raise HTTPException(status_code=404, detail="Download not found")
    order = db.scalar(select(models.Order).where(models.Order.id == download.order_id).with_for_update())
    if download.completed_at is None:
        download.completed_at = datetime.now(timezone.utc)
    finalised = False
    if (order is not None and order.accepted_at is None and order.status in ("in_escrow", "delivered")
            and acceptance.has_ack(db, order.id, "download")):
        finalised = acceptance.accept(db, order, by=order.buyer_id, kind="first_download")
    db.commit()
    if finalised:
        notify(order.vendor_id, "order_accepted", "Ebook downloaded: sale final",
               body=f"Order {order.id}, ref {order.acceptance_ref}. Settlement has started.",
               link=f"/market/orders/{order.id}")
        await run_in_threadpool(acceptance.run_settlement, order.id)
    return {"completed": True, "finalised": finalised}


# --- seller: file versions and sales ------------------------------------------------

class EbookFileIn(BaseModel):
    asset_id: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=300)


@app.post("/commerce/products/{product_id}/ebook/file", tags=["ebooks"])
def replace_ebook_file(
    product_id: str, payload: EbookFileIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """A new version of the book's file. Earlier versions stay on record, and
    every buyer downloads the new one from their library."""
    product = _own_product(db, product_id, principal.user_id)
    if not product.ebook:
        raise HTTPException(status_code=422, detail="Only an ebook listing has a file")
    info = _ebook_asset(payload.asset_id, principal.user_id, "ebook")
    current = db.get(models.EbookFile, product.ebook_file_id) if product.ebook_file_id else None
    if current is not None and current.asset_id == info["id"]:
        return {"version": current.version, "unchanged": True}
    version = _add_ebook_version(db, product, info, (payload.note or "").strip() or None)
    db.commit()
    return {"version": version.version, "format": version.file_format, "size_bytes": version.size_bytes}


@app.get("/commerce/products/{product_id}/ebook/files", tags=["ebooks"])
def ebook_versions(product_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    product = _own_product(db, product_id, principal.user_id)
    rows = db.scalars(
        select(models.EbookFile).where(models.EbookFile.product_id == product.id)
        .order_by(models.EbookFile.version.desc())
    ).all()
    return {"items": [
        {"version": f.version, "filename": f.filename, "format": f.file_format, "size_bytes": f.size_bytes,
         "note": f.note, "created_at": f.created_at, "current": f.id == product.ebook_file_id}
        for f in rows
    ]}


@app.get("/commerce/products/{product_id}/sales", tags=["ebooks"])
def product_sales(product_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """One listing's orders, in the seller's own numbers (what they asked,
    not what the buyer paid): paid orders, settled, refunded, net proceeds."""
    product = _own_product(db, product_id, principal.user_id)
    rows = db.scalars(select(models.Order).where(models.Order.product_id == product.id)).all()
    paid = [o for o in rows if o.escrow_funded_at is not None]
    settled = [o for o in paid if o.status in SETTLED_STATUSES]
    refunded = [o for o in paid if o.status in ("refunded", "part_refunded")]
    held = [o for o in paid if o.status in OPEN_ORDER_STATUSES]
    # Refunds come out of what the buyer paid; the seller's share of it is in
    # the same proportion as their price to the buyer's total.
    def seller_share(o: models.Order, amount: Decimal) -> Decimal:
        return (amount * o.vendor_price / o.customer_price).quantize(CENT) if o.customer_price else Decimal("0")
    gross = sum((o.vendor_price for o in paid), Decimal("0"))
    refunds = sum((seller_share(o, Decimal(o.refunded_amount or 0)) for o in refunded), Decimal("0"))
    net_released = sum((o.vendor_price - seller_share(o, Decimal(o.refunded_amount or 0)) for o in settled), Decimal("0"))
    return {
        "orders_paid": len(paid),
        "units_sold": sum(o.quantity for o in paid),
        "awaiting_release": len(held),
        "gross": str(gross),
        "refunds": str(refunds),
        "net_released": str(net_released),
        "currency": product.currency,
    }


# --- administration -------------------------------------------------------------------

class AdminReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _audit(db: OrmSession, actor_id: str, action: str, target_type: str, target_id: str, reason: str | None) -> None:
    db.add(models.CommerceAudit(actor_id=actor_id, action=action, target_type=target_type,
                                target_id=target_id, reason=reason))


@app.post("/admin/commerce/products/{product_id}/suspend", tags=["admin"])
def admin_suspend_product(product_id: str, payload: AdminReasonIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Take a listing down (a copyright complaint, an unsafe file). New
    purchases stop; buyers keep what they bought unless their access is
    revoked separately."""
    product = db.get(models.Product, product_id)
    if product is None or product.status == "removed":
        raise HTTPException(status_code=404, detail="Product not found")
    product.status = "suspended"
    product.suspended_reason = payload.reason
    _audit(db, admin.user_id, "suspend_listing", "product", product.id, payload.reason)
    db.commit()
    notify(product.vendor_id, "listing_suspended", "One of your listings was suspended",
           body=payload.reason, link="/market/seller")
    return {"id": product.id, "status": product.status}


@app.post("/admin/commerce/products/{product_id}/reinstate", tags=["admin"])
def admin_reinstate_product(product_id: str, payload: AdminReasonIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    product = db.get(models.Product, product_id)
    if product is None or product.status != "suspended":
        raise HTTPException(status_code=404, detail="No suspended listing with this id")
    product.status = "paused"
    product.suspended_reason = None
    _audit(db, admin.user_id, "reinstate_listing", "product", product.id, payload.reason)
    db.commit()
    return {"id": product.id, "status": product.status}


@app.get("/admin/commerce/orders/{order_id}/access", tags=["admin"])
def admin_order_access(order_id: str, _: AdminUser, db: OrmSession = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    product = db.get(models.Product, order.product_id)
    downloads = db.scalars(
        select(models.EbookDownload).where(models.EbookDownload.order_id == order.id)
        .order_by(models.EbookDownload.created_at.desc()).limit(100)
    ).all()
    audit = db.scalars(
        select(models.CommerceAudit).where(models.CommerceAudit.target_id == order.id)
        .order_by(models.CommerceAudit.created_at.desc())
    ).all()
    return {
        "order": _order_out(order, admin=True),
        **_access(order, product, _downloads_used(db, order)),
        "downloads": [{"at": d.created_at, "file_id": d.file_id, "counted": d.counted} for d in downloads],
        "audit": [{"at": a.created_at, "action": a.action, "actor_id": a.actor_id, "reason": a.reason} for a in audit],
    }


@app.post("/admin/commerce/orders/{order_id}/reset-downloads", tags=["admin"])
def admin_reset_downloads(order_id: str, payload: AdminReasonIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.downloads_reset_at = datetime.now(timezone.utc)
    _audit(db, admin.user_id, "reset_downloads", "order", order.id, payload.reason)
    db.commit()
    return {"id": order.id, "downloads_reset_at": order.downloads_reset_at}


@app.post("/admin/commerce/orders/{order_id}/revoke-access", tags=["admin"])
def admin_revoke_access(order_id: str, payload: AdminReasonIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.access_revoked_at = datetime.now(timezone.utc)
    _audit(db, admin.user_id, "revoke_access", "order", order.id, payload.reason)
    db.commit()
    return {"id": order.id, "access_revoked_at": order.access_revoked_at}


@app.post("/admin/commerce/orders/{order_id}/restore-access", tags=["admin"])
def admin_restore_access(order_id: str, payload: AdminReasonIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    order.access_revoked_at = None
    _audit(db, admin.user_id, "restore_access", "order", order.id, payload.reason)
    db.commit()
    return {"id": order.id, "access_revoked_at": None}


@app.get("/commerce/ebooks/terms", tags=["ebooks"])
def ebook_terms():
    """What a seller needs to know before uploading, and a buyer before paying."""
    return {
        "formats": ["pdf", "epub"],
        "max_file_mb": settings.EBOOK_MAX_MB,
        "max_sample_mb": settings.EBOOK_SAMPLE_MAX_MB,
        "categories": list(EBOOK_CATEGORIES),
        "licenses": list(EBOOK_LICENSES),
        "link_minutes": settings.EBOOK_LINK_TTL_S // 60,
        "link_days": round(settings.EBOOK_LINK_TTL_S / 86400, 1),
        "scanned_for_malware": False,
        "access": (
            "Your ebook is in your library as soon as the payment is confirmed. It stays available "
            "if the listing is later unpublished. A full refund ends access."
        ),
    }
