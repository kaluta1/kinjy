"""Community marketplace listings and the owner's analytics.

Listings are the members' own products from the Kinjy marketplace, shown in a
community: checkout, delivery, reviews and disputes stay the marketplace's.

Analytics are counts over a window, nothing about any one person: the owner and
moderators see activity; money (sales, renewals, refunds) is the owner's only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from common.auth import CurrentUser, MaybeUser
from common.database import get_db

import events as event_rules
import models
import paid
import spaces as space_rules

log = logging.getLogger("community-service.insights")
router = APIRouter()

COMMERCE_URL = "http://commerce-service:8000"
SOCIAL_URL = "http://social-service:8000"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- listings -----------------------------------------------------------------

def _products(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    try:
        response = httpx.post(f"{COMMERCE_URL}/internal/products/batch", json={"ids": ids}, timeout=6)
        response.raise_for_status()
        return {p["id"]: p for p in response.json().get("items", [])}
    except Exception as exc:
        log.warning("marketplace unavailable for listings: %s", exc)
        raise HTTPException(status_code=503, detail="The marketplace cannot be reached right now")


@router.get("/communities/{community_id}/listings", tags=["marketplace"])
def list_listings(community_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    community = paid._community(db, community_id)
    viewer = principal.user_id if principal else None
    paid._visible(db, community, viewer)
    if not event_rules._readable(db, community, viewer):
        raise HTTPException(status_code=404, detail="Community not found")
    if "marketplace" not in space_rules.enabled_spaces(db, community):
        return {"items": []}
    rows = db.scalars(select(models.CommunityListing).where(models.CommunityListing.community_id == community.id)
                      .order_by(models.CommunityListing.created_at.desc()).limit(60)).all()
    products = _products([r.product_id for r in rows])
    # A listing whose product was paused or removed in the marketplace is not shown.
    return {"items": [{**products[r.product_id], "listed_by": r.added_by} for r in rows if r.product_id in products]}


class ListingIn(BaseModel):
    product_id: str


@router.post("/communities/{community_id}/listings", status_code=201, tags=["marketplace"])
def add_listing(community_id: str, payload: ListingIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """A member shows one of their own active marketplace products here."""
    community = paid._community(db, community_id)
    if paid._role(db, community.id, principal.user_id) is None:
        raise HTTPException(status_code=403, detail="Join the community first")
    if "marketplace" not in space_rules.enabled_spaces(db, community):
        raise HTTPException(status_code=409, detail="This community has no marketplace space")
    product = _products([payload.product_id]).get(payload.product_id)
    if product is None or product.get("vendor_id") != principal.user_id:
        raise HTTPException(status_code=404, detail="Pick one of your own active listings")
    exists = db.scalar(select(models.CommunityListing).where(
        models.CommunityListing.community_id == community.id, models.CommunityListing.product_id == payload.product_id))
    if exists is None:
        db.add(models.CommunityListing(community_id=community.id, product_id=payload.product_id,
                                       added_by=principal.user_id))
        db.commit()
    return {"listed": True}


@router.delete("/communities/{community_id}/listings/{product_id}", tags=["marketplace"])
def remove_listing(community_id: str, product_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = paid._community(db, community_id)
    row = db.scalar(select(models.CommunityListing).where(
        models.CommunityListing.community_id == community.id, models.CommunityListing.product_id == product_id))
    if row is None:
        return {"listed": False}
    if row.added_by != principal.user_id and paid._role(db, community.id, principal.user_id) not in ("owner", "moderator"):
        raise HTTPException(status_code=403, detail="Only who listed it, or a moderator, can take it off")
    db.delete(row)
    db.commit()
    return {"listed": False}


# --- analytics ----------------------------------------------------------------

DEFINITIONS = {
    "new_members": "Members whose membership started in the window.",
    "via_invitations": "New members who joined through a member's invitation link.",
    "activated": "New members who posted or commented in the window.",
    "active_contributors": "Distinct members who posted or commented in the window.",
    "posts": "Posts published in the window.",
    "comments": "Comments written in the window on this community's posts.",
    "unanswered": "Discussions older than a day with no comment (all time).",
    "pending_posts": "Posts waiting for approval now.",
    "moderation_actions": "Actions in the activity log in the window.",
    "event_registrations": "Places taken at events in the window (cancellations excluded).",
    "upcoming_events": "Scheduled events still to come.",
    "sales": "Payments in the window (memberships and tickets).",
    "renewals": "Membership payments in the window that continued an earlier period.",
    "cancellations": "Paid accesses that ended by the member's choice in the window.",
    "refunds": "Amount refunded to members in the window.",
}


@router.get("/communities/{community_id}/analytics", tags=["analytics"])
def analytics(community_id: str, principal: CurrentUser, days: int = 30, db: OrmSession = Depends(get_db)):
    """Counts over the last ``days`` days. Owner and moderators; money for the owner only."""
    community = paid._community(db, community_id)
    role = paid._role(db, community.id, principal.user_id)
    if role not in ("owner", "moderator"):
        raise HTTPException(status_code=403, detail="Only the people who run the community see this")
    days = max(1, min(days, 365))
    since = _now() - timedelta(days=days)
    new_ids = set(db.scalars(select(models.Membership.user_id).where(
        models.Membership.community_id == community.id, models.Membership.joined_at >= since,
        models.Membership.status == "active")).all())
    via = db.scalar(select(func.count()).select_from(models.Membership).where(
        models.Membership.community_id == community.id, models.Membership.joined_at >= since,
        models.Membership.invited_by.is_not(None))) or 0
    try:
        social = httpx.get(f"{SOCIAL_URL}/internal/community-stats/{community.id}", params={"days": days},
                           timeout=8).json()
    except Exception as exc:
        log.warning("community stats unavailable: %s", exc)
        social = {}
    authors = set(social.get("author_ids", []))
    regs = db.scalar(select(func.count()).select_from(models.EventRegistration)
                     .join(models.CommunityEvent, models.CommunityEvent.id == models.EventRegistration.event_id)
                     .where(models.CommunityEvent.community_id == community.id,
                            models.EventRegistration.created_at >= since,
                            models.EventRegistration.status == "registered")) or 0
    upcoming = db.scalar(select(func.count()).select_from(models.CommunityEvent).where(
        models.CommunityEvent.community_id == community.id, models.CommunityEvent.status == "scheduled",
        models.CommunityEvent.starts_at >= _now())) or 0
    actions = db.scalar(select(func.count()).select_from(models.CommunityAudit).where(
        models.CommunityAudit.community_id == community.id, models.CommunityAudit.created_at >= since)) or 0
    out = {
        "window_days": days,
        "definitions": DEFINITIONS,
        "members_total": community.members_count,
        "new_members": len(new_ids),
        "via_invitations": via,
        "activated": len(new_ids & authors),
        "active_contributors": len(authors),
        "posts": social.get("posts"),
        "comments": social.get("comments"),
        "unanswered": social.get("unanswered"),
        "pending_posts": social.get("pending"),
        "moderation_actions": actions,
        "event_registrations": regs,
        "upcoming_events": upcoming,
        "money": None,
    }
    if role == "owner":
        pays = db.scalars(select(models.CommunityPayment).where(
            models.CommunityPayment.community_id == community.id, models.CommunityPayment.created_at >= since)).all()
        first_by_user: dict[str, datetime] = {}
        for p in db.scalars(select(models.CommunityPayment).where(
                models.CommunityPayment.community_id == community.id,
                models.CommunityPayment.kind == "membership").order_by(models.CommunityPayment.created_at)).all():
            first_by_user.setdefault(p.user_id, p.created_at)
        renewals = sum(1 for p in pays if p.kind == "membership" and first_by_user.get(p.user_id) != p.created_at)
        cancellations = db.scalar(select(func.count()).select_from(models.CommunityEntitlement).where(
            models.CommunityEntitlement.community_id == community.id,
            models.CommunityEntitlement.status == "cancelled",
            models.CommunityEntitlement.updated_at >= since)) or 0
        refunded = db.scalar(select(func.coalesce(func.sum(models.CommunityRefund.amount), 0))
                             .join(models.CommunityPayment, models.CommunityPayment.id == models.CommunityRefund.payment_id)
                             .where(models.CommunityPayment.community_id == community.id,
                                    models.CommunityRefund.status == "done",
                                    models.CommunityRefund.created_at >= since)) or 0
        out["money"] = {
            "sales": len(pays),
            "owner_gross": str(sum((paid.money(p.owner_price) for p in pays), Decimal("0"))),
            "renewals": renewals,
            "cancellations": cancellations,
            "refunds": str(paid.money(refunded)),
            "currency": "USD",
        }
    return out
