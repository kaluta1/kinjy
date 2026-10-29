"""An administrator deletes a marketplace deal, with a reason the seller is told.

Deleting is the soft delete the marketplace already has (``status="removed"``,
what a seller's own delete does): the row stays, so orders, escrow, payments,
commissions and the ledger keep pointing at a real listing, and buyers keep
what they bought. Removed listings are already out of every public path - the
listings and search, the deal page, ordering, and the personal deal links
(only active deals can be linked or opened as a deal).

What this adds is the record and the message:

* one ``listing_removals`` row per deal, written in the same transaction as
  the status change, with the seller as they were before (vendor id and
  title), the admin, the exact reason and the time; plus the existing commerce
  audit row;
* the seller is told in the app and, when mail is configured, by e-mail. The
  row is the outbox: delivery is attempted right after the commit and retried
  in the background until it lands, each channel at most once marked done -
  so a retry never deletes again, and a request repeated never notifies again.

"Email sent" means handed to the mail server; nothing here can know that it
was read or even delivered, and nothing says so.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as OrmSession

from common import mailer
from common.auth import AdminUser
from common.database import SessionLocal, get_db

import models

log = logging.getLogger("commerce-service.listing_removal")
router = APIRouter()

AUTH_URL = "http://auth-service:8000"
USER_URL = "http://user-service:8000"
MESSAGING_URL = "http://messaging-service:8000"
MAX_ATTEMPTS = 20
REASON_MIN, REASON_MAX = 10, 1000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DeleteDealIn(BaseModel):
    reason: str = Field(max_length=REASON_MAX + 200)

    @field_validator("reason")
    @classmethod
    def _meaningful(cls, value: str) -> str:
        value = value.strip()
        if len(value) < REASON_MIN:
            raise ValueError(f"Give a reason of at least {REASON_MIN} characters")
        if len(value) > REASON_MAX:
            raise ValueError(f"Keep the reason under {REASON_MAX} characters")
        return value


def _out(row: models.ListingRemoval) -> dict:
    return {
        "product_id": row.product_id, "title": row.title, "vendor_id": row.vendor_id, "reason": row.reason,
        "removed_at": row.removed_at, "removed_by": row.admin_id,
        "seller_notified_in_app": row.notified_at is not None,
        # sent = handed to the mail server; skipped = mail not configured or no address.
        "email": row.email_status or "pending",
    }


def _message(row: models.ListingRemoval) -> tuple[str, str]:
    day = row.removed_at.strftime("%d %B %Y")
    title = f"Your deal “{row.title}” has been deleted by a Kinjy administrator"
    body = f"Reason: {row.reason}\nDate: {day}\nReference: {row.product_id}"
    return title, body


def deliver(db: OrmSession, row: models.ListingRemoval) -> None:
    """Tell the seller, on each channel not done yet. Commits what landed."""
    row.attempts = (row.attempts or 0) + 1
    title, body = _message(row)
    if row.notified_at is None:
        try:
            httpx.post(f"{MESSAGING_URL}/internal/notify", params={
                "user_id": row.vendor_id, "kind": "listing_deleted", "title": title, "body": body,
                "link": "/market/seller"}, timeout=5).raise_for_status()
            row.notified_at = _now()
        except httpx.HTTPError as exc:
            row.last_error = f"in-app: {exc}"[:300]
    if row.email_status in (None, "failed"):
        if not mailer.configured():
            row.email_status = "skipped"
        else:
            try:
                identity = httpx.get(f"{AUTH_URL}/internal/users/{row.vendor_id}/identity", timeout=5)
                email = identity.json().get("email") if identity.status_code == 200 else None
                if identity.status_code == 404 or (identity.status_code == 200 and not email):
                    row.email_status = "skipped"  # no account or no address left to write to
                elif email:
                    # Plain text, so the reason is never markup in anybody's mail client.
                    mailer.send(email, title, f"{title}.\n\n{body}\n\nKinjy")
                    row.email_status, row.email_sent_at = "sent", _now()
                else:
                    row.email_status, row.last_error = "failed", f"identity: HTTP {identity.status_code}"
            except (httpx.HTTPError, mailer.MailError) as exc:
                row.email_status, row.last_error = "failed", f"email: {exc}"[:300]
    db.commit()


@router.get("/admin/commerce/products", tags=["admin"])
def admin_products(_: AdminUser, q: str | None = None, limit: int = 50, db: OrmSession = Depends(get_db)):
    """Deals that exist (any status but deleted), newest first, to find one to act on."""
    stmt = select(models.Product).where(models.Product.status != "removed")
    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(models.Product.title.ilike(term), models.Product.id == q.strip(),
                              models.Product.vendor_id == q.strip()))
    rows = db.scalars(stmt.order_by(models.Product.created_at.desc()).limit(max(1, min(limit, 100)))).all()
    names: dict = {}
    if rows:
        try:
            response = httpx.post(f"{USER_URL}/internal/profiles", json={"ids": sorted({r.vendor_id for r in rows})},
                                  timeout=5)
            names = response.json().get("profiles", {}) if response.status_code == 200 else {}
        except httpx.HTTPError:
            pass
    return {"items": [{
        "id": r.id, "title": r.title, "status": r.status, "kind": r.kind, "created_at": r.created_at,
        "vendor_id": r.vendor_id, "vendor_name": (names.get(r.vendor_id) or {}).get("display_name"),
        "vendor_handle": (names.get(r.vendor_id) or {}).get("handle"),
    } for r in rows]}


@router.post("/admin/commerce/products/{product_id}/delete", tags=["admin"])
def admin_delete_product(product_id: str, payload: DeleteDealIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Delete a deal (soft) and tell its seller why. Repeating it changes nothing."""
    product = db.scalar(select(models.Product).where(models.Product.id == product_id).with_for_update())
    if product is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    done = db.get(models.ListingRemoval, product.id)
    if done is not None:
        db.rollback()
        return {**_out(done), "already": True}
    if product.status == "removed":
        raise HTTPException(status_code=409, detail="Its seller already deleted this deal")
    open_orders = db.scalar(select(models.Order.id).where(
        models.Order.product_id == product.id,
        models.Order.status.in_(("pending", "in_escrow", "delivered"))).limit(1)) is not None
    row = models.ListingRemoval(product_id=product.id, title=product.title, vendor_id=product.vendor_id,
                                admin_id=admin.user_id, reason=payload.reason, removed_at=_now(),
                                previous_status=product.status)
    product.status = "removed"
    db.add(row)
    db.add(models.CommerceAudit(actor_id=admin.user_id, action="delete_listing", target_type="product",
                                target_id=product.id, reason=payload.reason))
    db.commit()
    deliver(db, row)
    # Orders already placed carry on under their own rules; this only says so.
    return {**_out(row), "already": False, "open_orders": open_orders}


def retry_once() -> int:
    """Deliver what did not land yet (a service down at the time)."""
    with SessionLocal() as db:
        due = (
            or_(models.ListingRemoval.notified_at.is_(None), models.ListingRemoval.email_status.is_(None),
                models.ListingRemoval.email_status == "failed"),
            models.ListingRemoval.attempts < MAX_ATTEMPTS,
            models.ListingRemoval.removed_at < _now() - timedelta(seconds=30),
        )
        ids = db.scalars(select(models.ListingRemoval.product_id).where(*due).limit(50)).all()
        db.rollback()
        for product_id in ids:
            # One row at a time, locked and read afresh: deliver() commits, which
            # ends the lock - a row held from an earlier read could then be
            # delivered here and by another worker at once (seen: the seller told
            # twice).
            row = db.scalar(select(models.ListingRemoval).where(models.ListingRemoval.product_id == product_id, *due)
                            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if row is None:
                db.rollback()
                continue
            try:
                deliver(db, row)
            except Exception as exc:  # one bad row must not stop the others
                db.rollback()
                log.error("listing removal %s: delivery failed: %s", product_id, exc)
        return len(ids)


async def _loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(retry_once)
        except Exception as exc:
            log.error("listing removal retries: %s", exc)


def start_worker() -> None:
    asyncio.get_event_loop().create_task(_loop())
