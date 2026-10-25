"""Paid communities: tiers, coupons, quotes, entitlements, payments, refunds.

Money rules (decided 25/10, see docs/COMMUNITIES.md):

* The owner sets a price; Kinjy adds its markup on top
  (``community_markup_pct``, an admin setting, 20 by default). The member pays
  price + markup.
* Kinjy's revenue is the markup only; the buyer's sponsor's commission and the
  Leaders share come out of it (ledger ``revenue_split``, unchanged).
* The owner's price goes to the owner's wallet, and only theirs: changing who
  administers a community changes nothing about who was paid.
* Renewals are manual (Kaluta Pay cannot charge a card again by itself): a
  reminder before the end, then a grace period (``community_grace_days``, an
  admin setting, 7 by default), then access ends.

Payment state and access are separate: CommunityPayment records money,
CommunityEntitlement records access, and only the entitlement is read by the
access checks. Every money amount is computed here, on the server, in Decimal.

Paid features stay off until an administrator switches
``community_paid_enabled`` on.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as OrmSession

from common import notify, settings
from common.auth import AdminUser, CurrentUser, MaybeUser
from common.database import get_db
from common.ids import new_id

import models

log = logging.getLogger("community-service.paid")
router = APIRouter()

LEDGER_URL = "http://ledger-service:8000"
CENT = Decimal("0.01")
ACCESS_STATUSES = ("trial", "active", "grace")
QUOTE_MINUTES = 30

DEFAULTS = {
    "community_markup_pct": str(settings.MARKETPLACE_MARKUP_PCT),
    "community_grace_days": "7",
    "community_paid_enabled": "false",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# --- admin settings -----------------------------------------------------------

def setting(db: OrmSession, key: str) -> str:
    row = db.get(models.PlatformSetting, key)
    return row.value if row is not None else DEFAULTS[key]


def markup_pct(db: OrmSession) -> Decimal:
    return Decimal(setting(db, "community_markup_pct"))


def grace_days(db: OrmSession) -> int:
    return int(setting(db, "community_grace_days"))


def paid_enabled(db: OrmSession) -> bool:
    return setting(db, "community_paid_enabled") == "true"


class SettingsIn(BaseModel):
    community_markup_pct: Decimal | None = Field(default=None, ge=0, le=100)
    community_grace_days: int | None = Field(default=None, ge=0, le=60)
    community_paid_enabled: bool | None = None


@router.get("/admin/community-settings", tags=["admin"])
def get_settings(_: AdminUser, db: OrmSession = Depends(get_db)):
    rows = {r.key: r for r in db.scalars(select(models.PlatformSetting)).all()}
    return {
        key: {"value": setting(db, key), "default": DEFAULTS[key],
              "updated_by": rows[key].updated_by if key in rows else None,
              "updated_at": rows[key].updated_at if key in rows else None}
        for key in DEFAULTS
    }


@router.patch("/admin/community-settings", tags=["admin"])
def set_settings(payload: SettingsIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Markup on community sales, grace days, and the switch for paid features.

    A new markup applies to prices quoted from now on; a quote already made
    keeps the markup it was made with.
    """
    for key, value in payload.model_dump(exclude_none=True).items():
        text = ("true" if value else "false") if isinstance(value, bool) else str(value)
        row = db.get(models.PlatformSetting, key)
        if row is None:
            db.add(models.PlatformSetting(key=key, value=text, updated_by=admin.user_id, updated_at=_now()))
        else:
            row.value, row.updated_by, row.updated_at = text, admin.user_id, _now()
        log.info("community setting %s = %s by %s", key, text, admin.user_id)
    db.commit()
    return get_settings(admin, db)


# --- helpers ------------------------------------------------------------------

def _community(db: OrmSession, community_id: str) -> models.Community:
    community = db.get(models.Community, community_id)
    if community is None:
        community = db.scalar(select(models.Community).where(models.Community.slug == community_id))
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    return community


def _membership(db: OrmSession, community_id: str, user_id: str) -> models.Membership | None:
    return db.scalar(select(models.Membership).where(
        models.Membership.community_id == community_id, models.Membership.user_id == user_id))


def _role(db: OrmSession, community_id: str, user_id: str | None) -> str | None:
    if not user_id:
        return None
    m = _membership(db, community_id, user_id)
    return m.role if m is not None and m.status == "active" else None


def _require_owner(db: OrmSession, community_id: str, user_id: str) -> models.Community:
    community = _community(db, community_id)
    if _role(db, community.id, user_id) != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can do this")
    return community


def _visible(db: OrmSession, community: models.Community, user_id: str | None) -> None:
    """A secret community does not exist for people who are not in it."""
    if community.kind != "secret":
        return
    m = _membership(db, community.id, user_id) if user_id else None
    if m is None or m.status not in ("active", "pending", "lapsed"):
        raise HTTPException(status_code=404, detail="Community not found")


def entitlement(db: OrmSession, community_id: str, user_id: str | None) -> models.CommunityEntitlement | None:
    if not user_id:
        return None
    return db.scalar(select(models.CommunityEntitlement).where(
        models.CommunityEntitlement.community_id == community_id,
        models.CommunityEntitlement.user_id == user_id))


def entitled(db: OrmSession, community: models.Community, user_id: str | None) -> bool:
    """Whether this person may open what is paid in this community. Its owner and
    moderators always may."""
    if not user_id:
        return False
    if _role(db, community.id, user_id) in ("owner", "moderator"):
        return True
    ent = entitlement(db, community.id, user_id)
    return ent is not None and ent.status in ACCESS_STATUSES


def _add_period(start: datetime, period: str) -> datetime | None:
    if period == "one_time":
        return None
    months = 1 if period == "monthly" else 12
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _activate_membership(db: OrmSession, community: models.Community, user_id: str) -> None:
    m = _membership(db, community.id, user_id)
    if m is None:
        db.add(models.Membership(community_id=community.id, user_id=user_id, status="active"))
        community.members_count += 1
    elif m.status in ("pending", "lapsed"):
        m.status = "active"
        community.members_count += 1


def lapse_membership(db: OrmSession, community: models.Community, user_id: str) -> None:
    """Paid access ended: the member is out of a paid community (kept as lapsed,
    so they can come back by paying). Never touches the owner or moderators."""
    if community.access_model != "paid":
        return
    m = _membership(db, community.id, user_id)
    if m is not None and m.status == "active" and m.role == "member":
        m.status = "lapsed"
        community.members_count = max(0, community.members_count - 1)


def _tier_out(tier: models.CommunityTier, pct: Decimal) -> dict:
    price = money(tier.price)
    intro = money(tier.intro_price) if tier.intro_price is not None else None
    return {
        "id": tier.id, "name": tier.name, "description": tier.description,
        "benefits": [b for b in (tier.benefits or "").splitlines() if b.strip()],
        "period": tier.period, "trial_days": tier.trial_days, "active": tier.active,
        "owner_price": str(price),
        "price": str(price + money(price * pct / 100)),
        "intro_price": str(intro + money(intro * pct / 100)) if intro is not None else None,
    }


# --- owner: access model, tiers, coupons -------------------------------------

class AccessIn(BaseModel):
    access_model: str | None = Field(default=None, pattern="^(free|paid|mixed)$")
    paid_spaces: dict[str, bool] | None = None


@router.patch("/communities/{community_id}/access", tags=["paid"])
def set_access(community_id: str, payload: AccessIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Free, paid or mixed, and which spaces are paid in a mixed community.

    Members already in keep their place when a community becomes paid: access
    is only taken away by an entitlement that ends, never by this switch.
    """
    community = _require_owner(db, community_id, principal.user_id)
    if payload.access_model and payload.access_model != "free" and not paid_enabled(db):
        raise HTTPException(status_code=409, detail="Paid communities are not open on Kinjy yet")
    if payload.access_model:
        community.access_model = payload.access_model
    if payload.paid_spaces:
        import spaces as space_rules
        space_rules.materialize(db, community)
        rows = {r.space: r for r in db.scalars(select(models.CommunitySpace).where(
            models.CommunitySpace.community_id == community.id)).all()}
        for key, on in payload.paid_spaces.items():
            if key in rows:
                rows[key].paid_only = bool(on)
    db.commit()
    return {"access_model": community.access_model, "paid_spaces": sorted(
        r.space for r in db.scalars(select(models.CommunitySpace).where(
            models.CommunitySpace.community_id == community.id, models.CommunitySpace.paid_only.is_(True))).all())}


class TierIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=2000)
    benefits: list[str] = Field(default_factory=list, max_length=20)
    price: Decimal = Field(gt=0, le=100000)
    period: str = Field(pattern="^(monthly|annual|one_time)$")
    trial_days: int = Field(default=0, ge=0, le=60)
    intro_price: Decimal | None = Field(default=None, gt=0, le=100000)


class TierEdit(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    benefits: list[str] | None = Field(default=None, max_length=20)
    active: bool | None = None

    model_config = {"extra": "forbid"}


@router.get("/communities/{community_id}/tiers", tags=["paid"])
def list_tiers(community_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """The ways in, with what the member pays (owner's price + Kinjy's markup)."""
    community = _community(db, community_id)
    viewer = principal.user_id if principal else None
    _visible(db, community, viewer)
    pct = markup_pct(db)
    owner = _role(db, community.id, viewer) == "owner"
    q = select(models.CommunityTier).where(models.CommunityTier.community_id == community.id)
    if not owner:
        q = q.where(models.CommunityTier.active.is_(True))
    tiers = db.scalars(q.order_by(models.CommunityTier.position, models.CommunityTier.created_at)).all()
    ent = entitlement(db, community.id, viewer)
    return {
        "paid_enabled": paid_enabled(db),
        "access_model": community.access_model or "free",
        "markup_pct": str(pct),
        "grace_days": grace_days(db),
        "tiers": [_tier_out(t, pct) for t in tiers],
        "my_entitlement": _entitlement_out(ent) if ent else None,
    }


@router.post("/communities/{community_id}/tiers", status_code=201, tags=["paid"])
def create_tier(community_id: str, payload: TierIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = _require_owner(db, community_id, principal.user_id)
    if not paid_enabled(db):
        raise HTTPException(status_code=409, detail="Paid communities are not open on Kinjy yet")
    position = db.scalar(select(func.count()).select_from(models.CommunityTier).where(
        models.CommunityTier.community_id == community.id)) or 0
    tier = models.CommunityTier(
        id=new_id("tier"), community_id=community.id, name=payload.name.strip(),
        description=payload.description.strip(), benefits="\n".join(b.strip() for b in payload.benefits if b.strip()),
        price=money(payload.price), period=payload.period, trial_days=payload.trial_days,
        intro_price=money(payload.intro_price) if payload.intro_price else None, position=position,
    )
    db.add(tier)
    db.commit()
    return _tier_out(tier, markup_pct(db))


@router.patch("/communities/{community_id}/tiers/{tier_id}", tags=["paid"])
def edit_tier(community_id: str, tier_id: str, payload: TierEdit, principal: CurrentUser,
              db: OrmSession = Depends(get_db)):
    """Name, description, benefits, on/off. The price of a tier never changes:
    members paid for what it said; a new price is a new tier."""
    community = _require_owner(db, community_id, principal.user_id)
    tier = db.get(models.CommunityTier, tier_id)
    if tier is None or tier.community_id != community.id:
        raise HTTPException(status_code=404, detail="Tier not found")
    sent = payload.model_dump(exclude_unset=True)
    if "benefits" in sent:
        tier.benefits = "\n".join(b.strip() for b in (payload.benefits or []) if b.strip())
    for key in ("name", "description", "active"):
        if key in sent and sent[key] is not None:
            setattr(tier, key, sent[key].strip() if isinstance(sent[key], str) else sent[key])
    db.commit()
    return _tier_out(tier, markup_pct(db))


class CouponIn(BaseModel):
    code: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    percent_off: int = Field(ge=1, le=100)
    max_redemptions: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


@router.post("/communities/{community_id}/coupons", status_code=201, tags=["paid"])
def create_coupon(community_id: str, payload: CouponIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = _require_owner(db, community_id, principal.user_id)
    code = payload.code.upper()
    if db.scalar(select(models.CommunityCoupon).where(models.CommunityCoupon.community_id == community.id,
                                                      models.CommunityCoupon.code == code)):
        raise HTTPException(status_code=409, detail="That code exists already")
    coupon = models.CommunityCoupon(id=new_id("cpn"), community_id=community.id, code=code,
                                    percent_off=payload.percent_off, max_redemptions=payload.max_redemptions,
                                    expires_at=_aware(payload.expires_at))
    db.add(coupon)
    db.commit()
    return _coupon_out(coupon)


@router.get("/communities/{community_id}/coupons", tags=["paid"])
def list_coupons(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = _require_owner(db, community_id, principal.user_id)
    rows = db.scalars(select(models.CommunityCoupon).where(models.CommunityCoupon.community_id == community.id)
                      .order_by(models.CommunityCoupon.created_at.desc())).all()
    return {"items": [_coupon_out(c) for c in rows]}


@router.post("/communities/{community_id}/coupons/{coupon_id}/disable", tags=["paid"])
def disable_coupon(community_id: str, coupon_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = _require_owner(db, community_id, principal.user_id)
    coupon = db.get(models.CommunityCoupon, coupon_id)
    if coupon is None or coupon.community_id != community.id:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.active = False
    db.commit()
    return _coupon_out(coupon)


def _coupon_out(c: models.CommunityCoupon) -> dict:
    return {"id": c.id, "code": c.code, "percent_off": c.percent_off, "max_redemptions": c.max_redemptions,
            "redemptions": c.redemptions, "expires_at": c.expires_at, "active": c.active}


# --- member: quote, trial, billing, cancel ------------------------------------

class QuoteIn(BaseModel):
    tier_id: str | None = None
    event_id: str | None = None
    coupon: str | None = Field(default=None, max_length=32)


def _usable_coupon(db: OrmSession, community_id: str, code: str | None) -> models.CommunityCoupon | None:
    if not code:
        return None
    coupon = db.scalar(select(models.CommunityCoupon).where(
        models.CommunityCoupon.community_id == community_id, models.CommunityCoupon.code == code.strip().upper()))
    if (coupon is None or not coupon.active
            or (coupon.expires_at and _aware(coupon.expires_at) < _now())
            or (coupon.max_redemptions and coupon.redemptions >= coupon.max_redemptions)):
        raise HTTPException(status_code=400, detail="That code does not work")
    return coupon


def _first_payment(db: OrmSession, community_id: str, user_id: str) -> bool:
    return not db.scalar(select(models.CommunityPayment.id).where(
        models.CommunityPayment.community_id == community_id, models.CommunityPayment.user_id == user_id,
        models.CommunityPayment.kind == "membership").limit(1))


@router.post("/communities/{community_id}/quote", tags=["paid"])
def quote(community_id: str, payload: QuoteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The price for this member, computed here: what they pay, and how it splits."""
    if not paid_enabled(db):
        raise HTTPException(status_code=409, detail="Paid communities are not open on Kinjy yet")
    community = _community(db, community_id)
    _visible(db, community, principal.user_id)
    m = _membership(db, community.id, principal.user_id)
    if m is not None and m.status == "banned":
        raise HTTPException(status_code=403, detail="You are banned from this community")
    first = _first_payment(db, community.id, principal.user_id)
    if payload.tier_id:
        tier = db.get(models.CommunityTier, payload.tier_id)
        if tier is None or tier.community_id != community.id or not tier.active:
            raise HTTPException(status_code=404, detail="Tier not found")
        base = money(tier.intro_price) if (first and tier.intro_price is not None) else money(tier.price)
        kind, event_id, tier_id = "membership", None, tier.id
    elif payload.event_id:
        import events as event_rules
        event = event_rules.ticketed_event(db, community, payload.event_id, principal.user_id)
        base, kind, event_id, tier_id = money(event.ticket_price), "ticket", event.id, None
    else:
        raise HTTPException(status_code=400, detail="Choose a tier or an event")
    coupon = _usable_coupon(db, community.id, payload.coupon) if (kind == "membership" and first) else None
    if payload.coupon and coupon is None and kind == "membership" and not first:
        raise HTTPException(status_code=400, detail="Codes apply to a first payment only")
    owner_price = money(base * (100 - coupon.percent_off) / 100) if coupon else base
    pct = markup_pct(db)
    markup = money(owner_price * pct / 100)
    row = models.CommunityQuote(
        id=new_id("cq"), community_id=community.id, user_id=principal.user_id, kind=kind, tier_id=tier_id,
        event_id=event_id, coupon_id=coupon.id if coupon else None, owner_price=owner_price, markup_pct=pct,
        markup=markup, total=owner_price + markup, expires_at=_now() + timedelta(minutes=QUOTE_MINUTES),
    )
    db.add(row)
    db.commit()
    out = _quote_out(row)
    if row.total == 0:
        # Free after a 100% code: nothing to pay, credited at once.
        out["credited"] = credit_payment(db, row.id, f"free:{row.id}", Decimal("0"))
    return out


def _quote_out(q: models.CommunityQuote) -> dict:
    return {"quote_id": q.id, "kind": q.kind, "tier_id": q.tier_id, "event_id": q.event_id,
            "owner_price": str(money(q.owner_price)), "markup_pct": str(q.markup_pct),
            "markup": str(money(q.markup)), "total": str(money(q.total)), "expires_at": q.expires_at,
            "community_id": q.community_id, "user_id": q.user_id}


@router.post("/communities/{community_id}/trial", tags=["paid"])
def start_trial(community_id: str, payload: QuoteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """A tier's free trial, once per member and community."""
    if not paid_enabled(db):
        raise HTTPException(status_code=409, detail="Paid communities are not open on Kinjy yet")
    community = _community(db, community_id)
    _visible(db, community, principal.user_id)
    tier = db.get(models.CommunityTier, payload.tier_id or "")
    if tier is None or tier.community_id != community.id or not tier.active or tier.trial_days <= 0:
        raise HTTPException(status_code=404, detail="This tier has no trial")
    ent = entitlement(db, community.id, principal.user_id)
    if ent is not None and (ent.trial_used or ent.status in ACCESS_STATUSES):
        raise HTTPException(status_code=409, detail="You have already had a trial or access here")
    if ent is None:
        ent = models.CommunityEntitlement(id=new_id("ent"), community_id=community.id, user_id=principal.user_id,
                                          tier_id=tier.id)
        db.add(ent)
    ent.tier_id, ent.status, ent.trial_used = tier.id, "trial", True
    ent.period_end = _now() + timedelta(days=tier.trial_days)
    ent.grace_until, ent.cancel_at_period_end, ent.updated_at = None, False, _now()
    _activate_membership(db, community, principal.user_id)
    db.commit()
    return _entitlement_out(ent)


def _entitlement_out(e: models.CommunityEntitlement) -> dict:
    return {"id": e.id, "community_id": e.community_id, "tier_id": e.tier_id, "status": e.status,
            "has_access": e.status in ACCESS_STATUSES, "period_end": e.period_end, "grace_until": e.grace_until,
            "cancel_at_period_end": e.cancel_at_period_end, "started_at": e.started_at}


def _payment_out(p: models.CommunityPayment) -> dict:
    return {"id": p.id, "kind": p.kind, "tier_id": p.tier_id, "event_id": p.event_id,
            "owner_price": str(money(p.owner_price)), "kinjy_fee": str(money(p.markup)),
            "total": str(money(p.total)), "refunded": str(money(p.refunded or 0)), "status": p.status,
            "coupon_code": p.coupon_code, "period_from": p.period_from, "period_to": p.period_to,
            "payment_ref": p.payment_ref, "created_at": p.created_at, "user_id": p.user_id}


@router.get("/communities/{community_id}/billing", tags=["paid"])
def my_billing(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The member's access here and their receipts (billing history)."""
    community = _community(db, community_id)
    ent = entitlement(db, community.id, principal.user_id)
    rows = db.scalars(select(models.CommunityPayment).where(
        models.CommunityPayment.community_id == community.id, models.CommunityPayment.user_id == principal.user_id)
        .order_by(models.CommunityPayment.created_at.desc()).limit(100)).all()
    return {"entitlement": _entitlement_out(ent) if ent else None, "payments": [_payment_out(p) for p in rows],
            "grace_days": grace_days(db)}


@router.get("/me/community-memberships", tags=["paid"])
def my_memberships(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Every paid access the member has or had, for their own dashboard."""
    rows = db.scalars(select(models.CommunityEntitlement).where(
        models.CommunityEntitlement.user_id == principal.user_id)
        .order_by(models.CommunityEntitlement.updated_at.desc()).limit(100)).all()
    communities = {c.id: c for c in db.scalars(select(models.Community).where(
        models.Community.id.in_([r.community_id for r in rows] or [""]))).all()}
    tiers = {t.id: t for t in db.scalars(select(models.CommunityTier).where(
        models.CommunityTier.id.in_([r.tier_id for r in rows] or [""]))).all()}
    return {"items": [
        {**_entitlement_out(r),
         "community": {"name": communities[r.community_id].name, "slug": communities[r.community_id].slug}
         if r.community_id in communities else None,
         "tier": {"name": tiers[r.tier_id].name, "period": tiers[r.tier_id].period} if r.tier_id in tiers else None}
        for r in rows
    ]}


@router.post("/communities/{community_id}/membership/cancel", tags=["paid"])
def cancel_membership(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Stop at the end of the period paid for. Nothing renews by itself anyway
    (renewals are paid by hand); this says the member will not, and ends the
    reminders. Access stays until the period ends."""
    community = _community(db, community_id)
    ent = entitlement(db, community.id, principal.user_id)
    if ent is None or ent.status not in ACCESS_STATUSES:
        raise HTTPException(status_code=404, detail="You have no paid access here")
    if ent.period_end is None:
        raise HTTPException(status_code=409, detail="One-time access does not renew: there is nothing to cancel")
    ent.cancel_at_period_end, ent.updated_at = True, _now()
    db.commit()
    return _entitlement_out(ent)


@router.post("/communities/{community_id}/membership/resume", tags=["paid"])
def resume_membership(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = _community(db, community_id)
    ent = entitlement(db, community.id, principal.user_id)
    if ent is None or ent.status not in ACCESS_STATUSES:
        raise HTTPException(status_code=404, detail="You have no paid access here")
    ent.cancel_at_period_end, ent.updated_at = False, _now()
    db.commit()
    return _entitlement_out(ent)


# --- owner: subscribers, earnings, refunds ------------------------------------

@router.get("/communities/{community_id}/subscribers", tags=["paid"])
def subscribers(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = _require_owner(db, community_id, principal.user_id)
    rows = db.scalars(select(models.CommunityEntitlement).where(
        models.CommunityEntitlement.community_id == community.id)
        .order_by(models.CommunityEntitlement.updated_at.desc()).limit(500)).all()
    return {"items": [{**_entitlement_out(r), "user_id": r.user_id} for r in rows]}


@router.get("/communities/{community_id}/earnings", tags=["paid"])
def earnings(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What this community brought in, the owner only: what members paid, Kinjy's
    fee, refunds, and what is the owner's. Amounts from the payments recorded
    here; withdrawable balance is the owner's wallet (ledger)."""
    community = _require_owner(db, community_id, principal.user_id)
    rows = db.scalars(select(models.CommunityPayment).where(models.CommunityPayment.community_id == community.id)
                      .order_by(models.CommunityPayment.created_at.desc()).limit(500)).all()
    paid_total = sum((money(p.total) for p in rows), Decimal("0"))
    fees = sum((money(p.markup) for p in rows), Decimal("0"))
    owner_gross = sum((money(p.owner_price) for p in rows), Decimal("0"))
    refunded = sum((money(p.refunded or 0) for p in rows), Decimal("0"))
    # A refund takes back the owner's share and Kinjy's fee in proportion.
    owner_refunded = sum((money(Decimal(str(p.refunded or 0)) * money(p.owner_price) / money(p.total))
                          for p in rows if money(p.total) > 0), Decimal("0"))
    return {
        "currency": "USD",
        "members_paid": str(paid_total),
        "kinjy_fees": str(fees),
        "owner_gross": str(owner_gross),
        "refunded_to_members": str(refunded),
        "owner_refunded": str(owner_refunded),
        "owner_net": str(owner_gross - owner_refunded),
        "by_kind": {k: str(sum((money(p.owner_price) for p in rows if p.kind == k), Decimal("0")))
                    for k in ("membership", "ticket")},
        "payments": [_payment_out(p) for p in rows[:100]],
        "note": "Members pay the owner's price plus Kinjy's fee. The owner's share is credited to the owner's "
                "Kinjy balance, from which it is withdrawn like any earnings.",
    }


class RefundIn(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=300)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,36}$")


@router.post("/communities/{community_id}/payments/{payment_id}/refund", tags=["paid"])
def refund_payment(community_id: str, payment_id: str, payload: RefundIn, principal: CurrentUser,
                   db: OrmSession = Depends(get_db)):
    """Refund (part of) a payment onto the member's Kinjy balance. The owner, or a
    Kinjy admin. The owner's share and Kinjy's fee, with the sponsor commission,
    come back in proportion (ledger). A full refund ends the access or the
    ticket it paid for; a partial one does not."""
    community = _community(db, community_id)
    if _role(db, community.id, principal.user_id) != "owner" and principal.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Only the owner can refund")
    return do_refund(db, community, payment_id, money(payload.amount), payload.reason, principal.user_id,
                     payload.request_id)


def do_refund(db: OrmSession, community: models.Community, payment_id: str, amount: Decimal, reason: str,
              actor_id: str, request_id: str) -> dict:
    refund_id = f"crf_{request_id}"[:40]
    done = db.get(models.CommunityRefund, refund_id)
    if done is not None and done.status == "done":
        payment = db.get(models.CommunityPayment, done.payment_id)
        return {"refund_id": refund_id, "status": "done", "payment": _payment_out(payment)}
    payment = db.scalar(select(models.CommunityPayment).where(models.CommunityPayment.id == payment_id)
                        .with_for_update().execution_options(populate_existing=True))
    if payment is None or payment.community_id != community.id:
        raise HTTPException(status_code=404, detail="Payment not found")
    left = money(payment.total) - money(payment.refunded or 0)
    if amount > left:
        raise HTTPException(status_code=409, detail=f"Only ${left} of this payment is left to refund")
    if done is None:
        done = models.CommunityRefund(id=refund_id, payment_id=payment.id, amount=amount, reason=reason[:300],
                                      actor_id=actor_id)
        db.add(done)
        db.commit()
    if not payment.payment_ref.startswith("free:"):
        try:
            httpx.post(f"{LEDGER_URL}/internal/post/payment-refund", json={
                "payment_id": payment.payment_ref, "member_id": payment.user_id, "amount": str(amount),
                "reason": f"community {payment.kind}: {reason}", "idempotency_key": f"community-refund:{refund_id}",
            }, timeout=10).raise_for_status()
        except Exception as exc:
            done.status = "failed"
            db.commit()
            log.error("community refund %s: ledger refused: %s", refund_id, exc)
            raise HTTPException(status_code=502, detail="The accounting step failed. Send the same refund again.")
    payment = db.scalar(select(models.CommunityPayment).where(models.CommunityPayment.id == payment_id)
                        .with_for_update().execution_options(populate_existing=True))
    payment.refunded = money(payment.refunded or 0) + amount
    full = money(payment.refunded) >= money(payment.total)
    payment.status = "refunded" if full else "partially_refunded"
    done.status = "done"
    if full and payment.kind == "membership":
        ent = entitlement(db, community.id, payment.user_id)
        if ent is not None and ent.status in ACCESS_STATUSES:
            ent.status, ent.updated_at = "refunded", _now()
            lapse_membership(db, community, payment.user_id)
    if full and payment.kind == "ticket" and payment.event_id:
        import events as event_rules
        event_rules.release_paid_seat(db, payment.event_id, payment.user_id)
    db.commit()
    notify.notify(payment.user_id, kind="community_refund", title=f"${amount} refunded to your Kinjy balance",
                  body=f"From {community.name}. {reason}"[:300], link=f"/communities/{community.slug}")
    return {"refund_id": refund_id, "status": "done", "payment": _payment_out(payment)}


# --- internal: payment-service ------------------------------------------------

@router.get("/internal/community-quotes/{quote_id}", tags=["internal"])
def internal_quote(quote_id: str, db: OrmSession = Depends(get_db)):
    row = db.get(models.CommunityQuote, quote_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    community = db.get(models.Community, row.community_id)
    return {**_quote_out(row), "used": row.payment_ref is not None,
            "expired": _aware(row.expires_at) < _now(), "owner_id": community.owner_id if community else None}


class CreditIn(BaseModel):
    quote_id: str
    payment_ref: str = Field(min_length=1, max_length=80)
    amount: Decimal


@router.post("/internal/community-payments", tags=["internal"])
def internal_credit(payload: CreditIn, db: OrmSession = Depends(get_db)):
    """A settled payment for a quote: credited once, whatever the retries."""
    return credit_payment(db, payload.quote_id, payload.payment_ref, money(payload.amount))


def credit_payment(db: OrmSession, quote_id: str, payment_ref: str, amount: Decimal) -> dict:
    existing = db.scalar(select(models.CommunityPayment).where(models.CommunityPayment.payment_ref == payment_ref))
    if existing is not None:
        return _credit_out(db, existing, created=False)
    q = db.scalar(select(models.CommunityQuote).where(models.CommunityQuote.id == quote_id).with_for_update().execution_options(populate_existing=True))
    if q is None:
        raise HTTPException(status_code=404, detail="Quote not found")
    if q.payment_ref is not None and q.payment_ref != payment_ref:
        raise HTTPException(status_code=409, detail="This quote was paid by another payment")
    if amount != money(q.total):
        raise HTTPException(status_code=400, detail="The amount paid is not the quoted total")
    community = db.get(models.Community, q.community_id)
    q.payment_ref = payment_ref
    payment = models.CommunityPayment(
        id=new_id("cpay"), community_id=q.community_id, user_id=q.user_id, quote_id=q.id, payment_ref=payment_ref,
        kind=q.kind, tier_id=q.tier_id, event_id=q.event_id, owner_price=money(q.owner_price),
        markup=money(q.markup), total=money(q.total), status="paid",
    )
    needs_refund = False
    if q.coupon_id:
        coupon = db.get(models.CommunityCoupon, q.coupon_id)
        if coupon is not None:
            coupon.redemptions += 1
            payment.coupon_code = coupon.code
    if q.kind == "membership":
        tier = db.get(models.CommunityTier, q.tier_id)
        ent = entitlement(db, q.community_id, q.user_id)
        now = _now()
        if ent is None:
            ent = models.CommunityEntitlement(id=new_id("ent"), community_id=q.community_id, user_id=q.user_id,
                                              tier_id=tier.id, started_at=now)
            db.add(ent)
            start = now
        else:
            # A renewal continues the period it renews: from its end while the
            # member still has access (a trial, an active period, the grace
            # days), from now once access has ended.
            end = _aware(ent.period_end)
            if end and (ent.status == "grace" or (ent.status in ("active", "trial") and end > now)):
                start = end
            else:
                start = now
        ent.tier_id, ent.status = tier.id, "active"
        ent.period_end = _add_period(start, tier.period)
        ent.grace_until, ent.cancel_at_period_end, ent.reminded_for, ent.updated_at = None, False, None, now
        payment.period_from, payment.period_to = start, ent.period_end
        _activate_membership(db, community, q.user_id)
    else:
        import events as event_rules
        needs_refund = not event_rules.seat_paid_ticket(db, q.event_id, q.user_id, payment.id)
    db.add(payment)
    db.commit()
    notify.notify(q.user_id, kind="community_payment", title=f"Payment received for {community.name}",
                  body=f"${money(q.total)} - receipt in the community's billing page.",
                  link=f"/communities/{community.slug}")
    return _credit_out(db, payment, created=True, needs_refund=needs_refund)


def _credit_out(db: OrmSession, payment: models.CommunityPayment, created: bool, needs_refund: bool = False) -> dict:
    community = db.get(models.Community, payment.community_id)
    return {"payment_id": payment.id, "created": created, "kind": payment.kind, "buyer_id": payment.user_id,
            "owner_id": community.owner_id, "community_id": community.id, "owner_price": str(money(payment.owner_price)),
            "markup": str(money(payment.markup)), "total": str(money(payment.total)), "needs_refund": needs_refund}


@router.post("/internal/community-payments/{payment_id}/auto-refund", tags=["internal"])
def internal_auto_refund(payment_id: str, db: OrmSession = Depends(get_db)):
    """A ticket paid after the event filled up: refunded in full, at once."""
    payment = db.get(models.CommunityPayment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    community = db.get(models.Community, payment.community_id)
    return do_refund(db, community, payment.id, money(payment.total) - money(payment.refunded or 0),
                     "The event was full when the payment arrived", "system", f"auto-{payment.id}"[:36])


# --- renewals, grace, expiry (background) -------------------------------------

def run_due(db: OrmSession) -> dict:
    """Reminders, the end of periods, the end of grace. Each step claims its rows
    with a conditional update, so two workers (or a retry) never act twice."""
    now = _now()
    counts = {"reminded": 0, "grace": 0, "ended": 0}
    remind_window = now + timedelta(days=3)
    due = db.scalars(select(models.CommunityEntitlement).where(
        models.CommunityEntitlement.status.in_(("active", "trial")),
        models.CommunityEntitlement.period_end.is_not(None),
        models.CommunityEntitlement.period_end <= remind_window,
        models.CommunityEntitlement.period_end > now,
        models.CommunityEntitlement.cancel_at_period_end.is_(False)).limit(200)).all()
    for ent in due:
        claimed = db.execute(update(models.CommunityEntitlement).where(
            models.CommunityEntitlement.id == ent.id,
            (models.CommunityEntitlement.reminded_for.is_(None)) | (models.CommunityEntitlement.reminded_for != ent.period_end))
            .values(reminded_for=ent.period_end)).rowcount
        db.commit()
        if claimed:
            community = db.get(models.Community, ent.community_id)
            counts["reminded"] += 1
            notify.notify(ent.user_id, kind="community_renewal_due",
                          title=f"Your access to {community.name} ends {_aware(ent.period_end):%d %b}",
                          body="Renew from the community page to keep it.", link=f"/communities/{community.slug}?billing=1")
    ended = db.scalars(select(models.CommunityEntitlement).where(
        models.CommunityEntitlement.status.in_(("active", "trial")),
        models.CommunityEntitlement.period_end.is_not(None),
        models.CommunityEntitlement.period_end <= now).limit(200)).all()
    days = grace_days(db)
    for ent in ended:
        community = db.get(models.Community, ent.community_id)
        # A cancelled period ends; a trial ends (nothing was paid, so no grace);
        # a paid period goes into its grace days, if the admin set any.
        if ent.cancel_at_period_end:
            new_status, grace = "cancelled", None
        elif ent.status == "trial" or days == 0:
            new_status, grace = "expired", None
        else:
            new_status, grace = "grace", _aware(ent.period_end) + timedelta(days=days)
        claimed = db.execute(update(models.CommunityEntitlement).where(
            models.CommunityEntitlement.id == ent.id, models.CommunityEntitlement.status == ent.status)
            .values(status=new_status, grace_until=grace, updated_at=now)).rowcount
        if claimed and new_status != "grace":
            lapse_membership(db, community, ent.user_id)
        db.commit()
        if claimed:
            counts["grace" if new_status == "grace" else "ended"] += 1
            notify.notify(ent.user_id, kind="community_access_change",
                          title=(f"Payment due for {community.name}: access kept until {grace:%d %b}"
                                 if new_status == "grace" else f"Your access to {community.name} has ended"),
                          link=f"/communities/{community.slug}?billing=1")
    lapsed = db.scalars(select(models.CommunityEntitlement).where(
        models.CommunityEntitlement.status == "grace", models.CommunityEntitlement.grace_until <= now).limit(200)).all()
    for ent in lapsed:
        community = db.get(models.Community, ent.community_id)
        claimed = db.execute(update(models.CommunityEntitlement).where(
            models.CommunityEntitlement.id == ent.id, models.CommunityEntitlement.status == "grace")
            .values(status="expired", updated_at=now)).rowcount
        if claimed:
            lapse_membership(db, community, ent.user_id)
        db.commit()
        if claimed:
            counts["ended"] += 1
            notify.notify(ent.user_id, kind="community_access_change",
                          title=f"Your access to {community.name} has ended",
                          body="Pay again from the community page to come back.",
                          link=f"/communities/{community.slug}?billing=1")
    return counts
