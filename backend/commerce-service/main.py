"""Kinjy · commerce-service — the agency marketplace, custodial escrow, disputes
and the ad platform.

Kinjy does not hold buyer money. Payment goes to an authorised financial
institution acting as custodian; it reaches the seller only once the buyer
confirms receipt, or once the confirmation window closes with nothing contested.
A dispute freezes that clock at both ends: the seller cannot wait out a buyer
raising a problem, and a buyer cannot keep the goods and the money by opening a
case and going quiet.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from common import economy, events, settings
from common import agefeatures
from common.auth import AdminUser, CurrentUser, MaybeUser
from common.notify import notify, notify_many
from common.database import get_db
from common.ids import new_id
from common.service import create_app

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

app = create_app(
    name="commerce-service",
    schema=models.SCHEMA,
    description="Agency marketplace, custodial escrow, dispute resolution, advertising.",
    migrations=MIGRATIONS,
)


class ProductIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = ""
    kind: str = Field(default="product", pattern="^(product|service|digital)$")
    vendor_price: Decimal = Field(gt=0)
    # Services and digital goods have no inventory, so null means unlimited.
    stock: int | None = Field(default=None, ge=0)
    country: str | None = None
    city: str | None = Field(default=None, max_length=120)
    images: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("images")
    @classmethod
    def _check_images(cls, value: list[str]) -> list[str]:
        return _clean_images(value)

    @field_validator("country")
    @classmethod
    def _check_country(cls, value: str | None) -> str | None:
        return _clean_country(value)


def _clean_images(value: list[str]) -> list[str]:
    # Images are stored as a csv, so a comma inside a url would split it in two.
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

    data = payload.model_dump(exclude={"images"})
    product = models.Product(
        id=new_id("prd"),
        vendor_id=principal.user_id,
        images=",".join(payload.images) or None,
        **data,
    )
    db.add(product)
    db.commit()
    pricing = economy.marketplace_pricing(product.vendor_price)
    return {"id": product.id, "pricing": {k: str(v) for k, v in pricing.items()}}


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
        stmt = stmt.where(func.lower(models.Product.title).like(f"%{q.lower()}%"))
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
    "pending", "in_escrow", "delivered", "settled", "disputed", "refunded", "part_refunded", "cancelled",
)
# While an order is in one of these the money or the goods are still in motion.
OPEN_ORDER_STATUSES = ("pending", "in_escrow", "delivered", "disputed")
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
    return [i for i in (product.images or "").split(",") if i]


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
    }
    if owner:
        out.update(_pricing_out(product))
    else:
        out["customer_price"] = str(_customer_price(product.vendor_price))
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
    status: str | None = Field(default=None, pattern="^(active|paused)$")

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

    # A price edit only reaches new orders: every order stores the prices it was
    # placed at, so nothing in escrow moves when the listing changes.
    if "images" in changes:
        product.images = ",".join(changes.pop("images")) or None
    for field, value in changes.items():
        setattr(product, field, value)
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


class DisputeIn(BaseModel):
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


class ResolveIn(BaseModel):
    outcome: str = Field(pattern="^(release_to_seller|refund_buyer|split)$")
    refund_amount: Decimal | None = None
    note: str = Field(min_length=3, max_length=4000)


def _custody_window() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.ESCROW_AUTO_RELEASE_DAYS)


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


def _settle(db: OrmSession, order: models.Order, *, vendor_price: Decimal) -> dict:
    """Release custody and post the settlement that recognises the revenue.

    ``vendor_price`` may be below the order's own price when a dispute ended in
    a partial refund: the seller is paid for what the buyer kept, and Kinjy's
    markup — and therefore the sponsor's commission — shrinks with it.
    """
    _ledger_escrow(order, "release", order.customer_price - order.refunded_amount, "buyer confirmed receipt")
    response = httpx.post(
        f"{LEDGER_URL}/internal/post/marketplace-order",
        json={
            "buyer_id": order.buyer_id,
            "vendor_id": order.vendor_id,
            "vendor_price": str(vendor_price),
            "order_id": order.id,
            "idempotency_key": f"order:{order.id}",
        },
        timeout=10,
    )
    response.raise_for_status()
    result = response.json()

    order.status = "part_refunded" if order.refunded_amount > 0 else "settled"
    order.escrow_released_at = datetime.now(timezone.utc)
    order.ledger_journal_id = result["journal"]["id"]
    return result


def _refund(db: OrmSession, order: models.Order, amount: Decimal, reason: str, key: str) -> None:
    """Send money back to the buyer out of custody. Raises on ledger failure.

    ``key`` makes the posting idempotent *per refund*, not per order: keying it
    on the order alone would make a second partial refund a silent no-op that
    still told the buyer their money was on its way.
    """
    _ledger_escrow(order, "refund", amount, reason, key=f"escrow:refund:{key}")
    order.refunded_amount = Decimal(order.refunded_amount or 0) + amount
    try:
        httpx.post(
            f"{PAYMENT_URL}/internal/escrow/refund",
            json={
                "order_id": order.id,
                "buyer_id": order.buyer_id,
                "amount": str(amount),
                "custody_ref": order.custody_ref,
                "reason": reason,
            },
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        # The ledger already records the refund as owed to the buyer, so the
        # money is not lost — but the rail did not move it, and somebody has to
        # know. Loud, and the order stays disputed until it is chased.
        log.error("refund rail failed for order %s: %s", order.id, exc)
        raise HTTPException(
            status_code=503,
            detail="The refund is recorded but the payment rail did not confirm it. Support has been alerted.",
        )


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
        "custodian": order.custodian,
        "escrow_funded_at": order.escrow_funded_at,
        "delivered_at": order.delivered_at,
        "delivery_note": order.delivery_note,
        "dispute_window_ends": order.dispute_window_ends,
        "escrow_released_at": order.escrow_released_at,
        "created_at": order.created_at,
    }


@app.get("/commerce/escrow/terms", tags=["escrow"])
def escrow_terms():
    """Who holds the money, under what licence, and for how long.

    Public on purpose: a buyer deciding whether to pay a stranger should be able
    to read the custody arrangement without an account.
    """
    return {
        "custodian": settings.CUSTODIAN_NAME,
        "provider": settings.CUSTODIAN_PROVIDER,
        "licence": settings.CUSTODIAN_LICENCE,
        "holds_funds": "the custodian, in a segregated client-money account — not Kinjy",
        "released_when": "the buyer confirms receipt",
        "auto_release_days": settings.ESCROW_AUTO_RELEASE_DAYS,
        "dispute_response_days": settings.DISPUTE_RESPONSE_DAYS,
        "arbitration_days": settings.DISPUTE_ARBITRATION_DAYS,
        "outcomes": ["release_to_seller", "refund_buyer", "split"],
    }


@app.post("/commerce/orders", status_code=201, tags=["marketplace"])
async def create_order(payload: OrderIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Places an order. Nothing is posted to the ledger and nothing reaches the
    seller yet — the next step is payment into the custodian's hands."""
    agefeatures.require(principal.user_id, "marketplace_buy")

    product = db.get(models.Product, payload.product_id)
    if product is None or product.status != "active":
        raise HTTPException(status_code=404, detail="Product not available")
    if product.vendor_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot buy your own listing")
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
        custodian=settings.CUSTODIAN_PROVIDER,
        status="pending",
        shipping_address=json.dumps(shipping) if shipping else None,
    )
    db.add(order)
    if product.stock is not None:
        product.stock -= payload.quantity
    db.commit()

    await events.publish("order.created", {"order_id": order.id, "amount": str(order.customer_price)})
    return {
        "id": order.id,
        "status": order.status,
        "customer_price": str(order.customer_price),
        "custodian": settings.CUSTODIAN_NAME,
        "pay_at": "/api/payments/checkout",
    }


@app.post("/internal/orders/{order_id}/funded", tags=["internal"])
def mark_funded(order_id: str, custody_ref: str | None = None, db: OrmSession = Depends(get_db)):
    """payment-service calls this when the buyer's money reaches the custodian."""
    order = db.get(models.Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending":
        return _order_out(order)

    try:
        _ledger_escrow(order, "hold", order.customer_price, "buyer funds received by custodian")
    except httpx.HTTPError as exc:
        log.error("escrow hold posting failed for %s: %s", order_id, exc)
        raise HTTPException(status_code=503, detail="Could not record the escrow hold; retry")

    order.status = "in_escrow"
    order.custody_ref = custody_ref
    order.escrow_funded_at = datetime.now(timezone.utc)
    order.dispute_window_ends = _custody_window()
    db.commit()

    notify(
        order.vendor_id,
        "order_funded",
        "An order is paid and in escrow",
        body="Deliver it, then the buyer confirms and the money is released to you.",
        link="/market/seller",
    )
    notify(
        order.buyer_id,
        "order_funded",
        "Your payment is held in escrow",
        body=f"{settings.CUSTODIAN_NAME} is holding it. Confirm receipt to release it to the seller.",
        link="/market#orders",
    )
    return _order_out(order)


# Kept for callers written against the old name.
@app.post("/commerce/orders/{order_id}/paid", tags=["internal"])
def mark_paid(order_id: str, db: OrmSession = Depends(get_db)):
    return mark_funded(order_id, None, db)


@app.post("/commerce/orders/{order_id}/delivered", tags=["marketplace"])
def mark_delivered(
    order_id: str, payload: DeliveryIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """The seller says they have delivered. This does **not** release the money.

    Only the buyer's confirmation, or the window expiring, does — otherwise a
    seller could release their own escrow by ticking a box.
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
        "Your order has been marked delivered",
        body="Confirm receipt to release the payment, or open a dispute if something is wrong.",
        link="/market#orders",
    )
    return _order_out(order, principal.user_id)


@app.post("/commerce/orders/{order_id}/confirm-delivery", tags=["marketplace"])
async def confirm_delivery(order_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Buyer confirms receipt -> custody clears -> the ledger settles the split."""
    order = db.get(models.Order, order_id)
    if order is None or order.buyer_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "disputed":
        raise HTTPException(status_code=409, detail="This order is in dispute; resolve the dispute first")
    if order.status not in ("in_escrow", "delivered"):
        raise HTTPException(status_code=409, detail=f"Order is {order.status}, not in escrow")

    try:
        result = _settle(db, order, vendor_price=order.vendor_price)
    except httpx.HTTPError as exc:
        # Leave the order in escrow rather than marking it settled without the
        # ledger entry — a settled order with no journal is unrecoverable.
        log.error("ledger posting failed for order %s: %s", order_id, exc)
        raise HTTPException(status_code=503, detail="Settlement failed; the order stays in escrow. Retry shortly.")
    db.commit()

    notify(
        order.vendor_id,
        "order_settled",
        "Payment released to you",
        body=f"{order.vendor_price} for order {order.id} is now in your wallet.",
        link="/market/seller",
    )
    await events.publish("order.settled", {"order_id": order.id, "journal_id": order.ledger_journal_id})
    return {"id": order.id, "status": order.status, "settlement": result}


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
                "images": [i for i in (p.images or "").split(",") if i],
                "country": p.country,
                "city": p.city,
                "available": p.status == "active",
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


ORDER_STATUSES = ("pending", "in_escrow", "delivered", "settled", "part_refunded", "refunded", "disputed", "cancelled")


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
    open_disputes = {
        d.order_id: d.id
        for d in db.scalars(
            select(models.Dispute).where(
                models.Dispute.order_id.in_([r.id for r in rows] or [""]),
                models.Dispute.status != "withdrawn",
            )
        ).all()
    }
    return {
        "items": [
            {**_order_out(r, principal.user_id), "dispute_id": open_disputes.get(r.id), **extras.get(r.id, {})}
            for r in rows
        ]
    }


@app.get("/commerce/orders/{order_id}", tags=["marketplace"])
def get_order(order_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    order = db.get(models.Order, order_id)
    if order is None or principal.user_id not in (order.buyer_id, order.vendor_id):
        raise HTTPException(status_code=404, detail="Order not found")
    dispute = db.scalar(select(models.Dispute).where(models.Dispute.order_id == order_id))
    return {
        **_order_out(order, principal.user_id),
        "custodian_name": settings.CUSTODIAN_NAME,
        "dispute_id": dispute.id if dispute else None,
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


@app.post("/commerce/orders/{order_id}/dispute", status_code=201, tags=["disputes"])
def open_dispute(
    order_id: str, payload: DisputeIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Open a case. Either side may; the escrow freezes either way.

    Refused once the money has left custody: after settlement there is nothing
    to hold back, and pretending otherwise would promise a protection that does
    not exist.
    """
    order = db.get(models.Order, order_id)
    if order is None or principal.user_id not in (order.buyer_id, order.vendor_id):
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in ("settled", "part_refunded", "refunded", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail="This order has already been settled — the funds are no longer in escrow.",
        )
    existing = db.scalar(
        select(models.Dispute).where(
            models.Dispute.order_id == order_id,
            models.Dispute.status.in_(("open", "answered", "arbitration")),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="A dispute is already open on this order")

    role = "buyer" if order.buyer_id == principal.user_id else "seller"
    against = order.vendor_id if role == "buyer" else order.buyer_id
    claimed = payload.amount_claimed
    if claimed is None:
        claimed = order.customer_price if role == "buyer" else Decimal("0")
    if claimed > order.customer_price:
        raise HTTPException(status_code=400, detail="You cannot claim more than the order is worth")

    now = datetime.now(timezone.utc)
    dispute = models.Dispute(
        id=new_id("dsp"),
        order_id=order.id,
        opened_by=principal.user_id,
        against=against,
        role=role,
        category=payload.category,
        reason=payload.reason,
        amount_claimed=claimed,
        status="open",
        respond_by=now + timedelta(days=settings.DISPUTE_RESPONSE_DAYS),
        arbitrate_by=now + timedelta(days=settings.DISPUTE_ARBITRATION_DAYS),
    )
    db.add(dispute)
    db.add(
        models.DisputeMessage(
            dispute_id=dispute.id,
            author_id=principal.user_id,
            author_role=role,
            body=payload.reason,
            evidence="\n".join(payload.evidence) or None,
        )
    )
    # Freezing the window is the whole point: the auto-release must not fire
    # while a case is open, or the dispute would resolve itself in the seller's
    # favour by running out the clock.
    order.status = "disputed"
    order.dispute_window_ends = None
    db.commit()

    notify(
        against,
        "dispute_opened",
        "A dispute was opened on your order",
        body=f"You have {settings.DISPUTE_RESPONSE_DAYS} days to respond before it goes to arbitration.",
        link=f"/app/disputes/{dispute.id}",
    )
    return _dispute_out(dispute)


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


@app.post("/commerce/disputes/{dispute_id}/messages", status_code=201, tags=["disputes"])
def add_dispute_message(
    dispute_id: str,
    payload: DisputeMessageIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    dispute, _order, role = _load_dispute(db, dispute_id, principal.user_id)
    if dispute.status in ("resolved", "withdrawn"):
        raise HTTPException(status_code=409, detail="This dispute is closed")

    db.add(
        models.DisputeMessage(
            dispute_id=dispute.id,
            author_id=principal.user_id,
            author_role=role,
            body=payload.body,
            evidence="\n".join(payload.evidence) or None,
        )
    )
    # The first answer from the other side moves the case out of "waiting".
    if dispute.status == "open" and principal.user_id == dispute.against:
        dispute.status = "answered"
    db.commit()

    other = dispute.against if principal.user_id == dispute.opened_by else dispute.opened_by
    notify(
        other,
        "dispute_message",
        "New message on your dispute",
        link=f"/app/disputes/{dispute.id}",
    )
    return {"ok": True, "status": dispute.status}


@app.post("/commerce/disputes/{dispute_id}/withdraw", tags=["disputes"])
def withdraw_dispute(dispute_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The opener drops the case; the escrow clock restarts."""
    dispute, order, _role = _load_dispute(db, dispute_id, principal.user_id)
    if dispute.opened_by != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the member who opened it can withdraw it")
    if dispute.status in ("resolved", "withdrawn"):
        raise HTTPException(status_code=409, detail="This dispute is closed")

    dispute.status = "withdrawn"
    dispute.outcome = "withdrawn"
    dispute.resolved_at = datetime.now(timezone.utc)
    order.status = "delivered" if order.delivered_at else "in_escrow"
    order.dispute_window_ends = _custody_window()
    db.commit()

    notify(dispute.against, "dispute_withdrawn", "A dispute against you was withdrawn",
           link=f"/market/orders/{order.id}/dispute")
    return _dispute_out(dispute)


@app.post("/commerce/disputes/{dispute_id}/concede", tags=["disputes"])
def concede_dispute(dispute_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The other side accepts the claim, and it is settled without arbitration.

    A seller conceding refunds the buyer what was claimed; a buyer conceding
    releases the money to the seller. Either way it resolves in minutes rather
    than days, which is what most cases actually need.
    """
    dispute, order, _role = _load_dispute(db, dispute_id, principal.user_id)
    if principal.user_id != dispute.against:
        raise HTTPException(status_code=403, detail="Only the member the dispute is against can concede")
    if dispute.status in ("resolved", "withdrawn"):
        raise HTTPException(status_code=409, detail="This dispute is closed")

    if dispute.role == "buyer":
        _resolve(db, dispute, order, outcome="refund_buyer" if dispute.amount_claimed >= order.customer_price
                 else "split", refund_amount=dispute.amount_claimed,
                 note="Conceded by the seller.", resolved_by=principal.user_id)
    else:
        _resolve(db, dispute, order, outcome="release_to_seller", refund_amount=Decimal("0"),
                 note="Conceded by the buyer.", resolved_by=principal.user_id)
    db.commit()
    return _dispute_out(dispute)


def _resolve(
    db: OrmSession,
    dispute: models.Dispute,
    order: models.Order,
    *,
    outcome: str,
    refund_amount: Decimal,
    note: str,
    resolved_by: str,
) -> None:
    """Apply an outcome to the money, then close the case.

    The money moves first. Marking a dispute resolved and *then* failing to move
    the funds would leave a closed case over an unsettled escrow, which is the
    one state nobody can recover from the outside.
    """
    refund_amount = Decimal(refund_amount or 0)
    if outcome == "release_to_seller":
        refund_amount = Decimal("0")
    elif outcome == "refund_buyer":
        refund_amount = order.customer_price - Decimal(order.refunded_amount or 0)
    if refund_amount < 0 or refund_amount > order.customer_price - Decimal(order.refunded_amount or 0):
        raise HTTPException(status_code=400, detail="The refund cannot exceed what is still held in escrow")

    if refund_amount > 0:
        _refund(db, order, refund_amount, f"dispute {dispute.id}: {outcome}", key=dispute.id)

    if outcome == "refund_buyer":
        # The refund above already cleared custody. Nothing reaches the seller,
        # so no revenue is recognised and no commission accrues: there was no
        # completed sale to earn one on.
        order.status = "refunded"
        order.escrow_released_at = datetime.now(timezone.utc)
    else:
        # What the buyer kept still carries the markup, so the seller's price
        # and Kinjy's revenue shrink together — the sponsor's commission with
        # them. Paying a full commission on a partly refunded order would pay
        # out of money that went back to the buyer.
        kept = order.customer_price - Decimal(order.refunded_amount or 0)
        markup = Decimal("1") + settings.MARKETPLACE_MARKUP_PCT / Decimal("100")
        vendor_price = economy.money(kept / markup)
        try:
            _settle(db, order, vendor_price=vendor_price)
        except httpx.HTTPError as exc:
            log.error("settlement after dispute %s failed: %s", dispute.id, exc)
            raise HTTPException(status_code=503, detail="Could not settle the order; the case stays open")

    dispute.status = "resolved"
    dispute.outcome = outcome
    dispute.refund_amount = refund_amount
    dispute.resolution_note = note
    dispute.resolved_by = resolved_by
    dispute.resolved_at = datetime.now(timezone.utc)

    notify_many(
        [order.buyer_id, order.vendor_id],
        "dispute_resolved",
        "Your dispute has been resolved",
        body=note,
        link=f"/market/orders/{order.id}/dispute",
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
def resolve_dispute(
    dispute_id: str, payload: ResolveIn, admin: AdminUser, db: OrmSession = Depends(get_db)
):
    """Kinjy arbitrates. The note is mandatory and is shown to both parties."""
    dispute = db.get(models.Dispute, dispute_id)
    if dispute is None:
        raise HTTPException(status_code=404, detail="Dispute not found")
    if dispute.status in ("resolved", "withdrawn"):
        raise HTTPException(status_code=409, detail="This dispute is closed")
    order = db.get(models.Order, dispute.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if payload.outcome == "split" and payload.refund_amount is None:
        raise HTTPException(status_code=400, detail="A split outcome needs a refund amount")

    _resolve(
        db,
        dispute,
        order,
        outcome=payload.outcome,
        refund_amount=payload.refund_amount or Decimal("0"),
        note=payload.note,
        resolved_by=admin.user_id,
    )
    db.commit()
    return _dispute_out(dispute)


@app.post("/internal/escrow/sweep", tags=["internal"])
def escrow_sweep(db: OrmSession = Depends(get_db)):
    """Release escrows whose window closed, and escalate silent disputes.

    Idempotent and safe to run on a schedule. A buyer who neither confirms nor
    disputes must not strand the seller's money forever, and a seller who never
    answers a dispute must not stall it forever either.
    """
    now = datetime.now(timezone.utc)
    released, escalated, failed = [], [], []

    due = db.scalars(
        select(models.Order).where(
            models.Order.status.in_(("in_escrow", "delivered")),
            models.Order.dispute_window_ends.is_not(None),
            models.Order.dispute_window_ends <= now,
        )
    ).all()
    for order in due:
        try:
            _settle(db, order, vendor_price=order.vendor_price)
            db.commit()
            released.append(order.id)
            notify(
                order.vendor_id,
                "order_settled",
                "Payment released to you",
                body="The buyer's confirmation window closed with no dispute.",
                link="/market/seller",
            )
        except Exception as exc:
            db.rollback()
            log.error("auto-release failed for %s: %s", order.id, exc)
            failed.append(order.id)

    stale = db.scalars(
        select(models.Dispute).where(
            models.Dispute.status == "open",
            models.Dispute.respond_by.is_not(None),
            models.Dispute.respond_by <= now,
        )
    ).all()
    for dispute in stale:
        dispute.status = "arbitration"
        escalated.append(dispute.id)
        notify_many(
            [dispute.opened_by, dispute.against],
            "dispute_arbitration",
            "A dispute has gone to Kinjy arbitration",
            body="The response deadline passed. Kinjy will decide the outcome.",
            link=f"/app/disputes/{dispute.id}",
        )
    db.commit()

    return {
        "released": released,
        "escalated": escalated,
        "failed": failed,
        "checked_at": now,
    }


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
