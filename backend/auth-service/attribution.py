"""Invitation attribution: who invited a new member, decided on the server.

Before this, the invitation lived only in the visitor's browser and reached
registration as an editable "referral code (optional)" field. Clearing the
field was enough to drop the inviter, and the referral pool then drew somebody
else - the member who did the inviting lost the referral.

Now, when somebody arrives on a member's invitation link, the server checks
the inviter and records a capture; the browser holds only an HttpOnly, signed
cookie naming that capture, for REFERRAL_ATTRIBUTION_DAYS. Registration reads
the cookie and applies it whatever the form says. Rules:

- Only a live, active member can be captured as an inviter.
- A visitor who is signed in is never captured: an existing member's sponsor
  does not move because they opened somebody's link.
- Several links in the window: REFERRAL_ATTRIBUTION_POLICY ("last", the rule
  Kinjy already applied in the browser, or "first").
- The capture is spent at registration and the cookie cleared, so it cannot
  follow the browser into a second account.
- Deleting the cookie deletes the invitation; nothing recreates it.
- After registration a sponsor changes only through an administrator, with a
  reason, recorded in sponsor_corrections.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from common import settings
from common.auth import AdminUser, MaybeUser
from common.database import get_db
from common.ids import new_id

import models

log = logging.getLogger("auth.attribution")
router = APIRouter()

COOKIE = "kinjy_ref"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sign(capture_id: str) -> str:
    mac = hmac.new(settings.JWT_SECRET.encode(), f"refcap:{capture_id}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{capture_id}.{mac}"


def _unsign(value: str | None) -> str | None:
    """The capture id in a cookie, if the cookie is ours and untouched."""
    if not value or "." not in value or len(value) > 120:
        return None
    capture_id, _, mac = value.rpartition(".")
    expected = _sign(capture_id).rpartition(".")[2]
    return capture_id if hmac.compare_digest(mac, expected) else None


def eligible_referrer(db: OrmSession, user_id: str | None) -> models.User | None:
    """An inviter who can still be credited: an existing, active account."""
    if not user_id:
        return None
    user = db.get(models.User, user_id)
    return user if user is not None and user.status == "active" else None


def held_capture(db: OrmSession, request: Request) -> models.ReferralCapture | None:
    """The capture this browser holds, if it is still good: ours, unspent,
    within its window, and its inviter still an active member."""
    capture_id = _unsign(request.cookies.get(COOKIE))
    if capture_id is None:
        return None
    capture = db.get(models.ReferralCapture, capture_id)
    if capture is None or capture.used_by is not None:
        return None
    expires = capture.expires_at if capture.expires_at.tzinfo else capture.expires_at.replace(tzinfo=timezone.utc)
    if expires <= _now():
        return None
    if eligible_referrer(db, capture.referrer_id) is None:
        return None
    return capture


def _set_cookie(response: Response, request: Request, capture: models.ReferralCapture) -> None:
    secure = settings.ENV != "development" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(
        COOKIE,
        _sign(capture.id),
        max_age=settings.REFERRAL_ATTRIBUTION_DAYS * 86400,
        httponly=True,
        secure=secure,
        # Lax: sent when somebody follows a link to Kinjy and on Kinjy's own
        # requests, never on a third party's embedded requests.
        samesite="lax",
        path="/",
        domain=settings.REFERRAL_COOKIE_DOMAIN or None,
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", domain=settings.REFERRAL_COOKIE_DOMAIN or None)


def _inviter_out(user: models.User) -> dict:
    # The public half of a profile only: the same as the code lookup returns.
    return {"display_name": user.display_name, "handle": user.handle}


class CaptureIn(BaseModel):
    code: str | None = Field(default=None, max_length=16)
    link: str | None = Field(default=None, max_length=16)


@router.post("/auth/invitation/capture", tags=["auth"])
def capture_referral(payload: CaptureIn, request: Request, response: Response, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """Hold an invitation for this browser, checked here rather than trusted
    from the page. Answers who it credits and until when."""
    from main import _client, _link_sharer, _live_link, _ref_lookup_allowed

    if principal is not None:
        # A member's sponsor was settled at their registration.
        return {"captured": False, "reason": "signed_in"}
    ip, _ = _client(request)
    if not _ref_lookup_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many lookups. Wait a moment.")

    referrer: models.User | None = None
    link_token: str | None = None
    if payload.code:
        found = db.scalar(select(models.User).where(models.User.referral_code == payload.code.strip().upper()))
        referrer = eligible_referrer(db, found.id) if found else None
    elif payload.link:
        live = _live_link(db, payload.link.strip())
        sharer = _link_sharer(db, live[0]) if live else None
        if sharer is not None:
            referrer, link_token = sharer, live[0].token
    if referrer is None:
        # Unknown, deleted or inactive: nothing is held, and whatever this
        # browser already holds stays exactly as it was.
        raise HTTPException(status_code=404, detail="Unknown referral code")

    held = held_capture(db, request)
    if held is not None and (held.referrer_id == referrer.id or settings.REFERRAL_ATTRIBUTION_POLICY == "first"):
        current = eligible_referrer(db, held.referrer_id)
        return {"captured": held.referrer_id == referrer.id, "inviter": _inviter_out(current), "expires_at": held.expires_at,
                "policy": settings.REFERRAL_ATTRIBUTION_POLICY}

    now = _now()
    capture = models.ReferralCapture(
        id=new_id("rcp"), referrer_id=referrer.id, source="link" if link_token else "code", link_token=link_token,
        captured_at=now, expires_at=now + timedelta(days=settings.REFERRAL_ATTRIBUTION_DAYS),
    )
    db.add(capture)
    db.commit()
    _set_cookie(response, request, capture)
    return {"captured": True, "inviter": _inviter_out(referrer), "expires_at": capture.expires_at,
            "policy": settings.REFERRAL_ATTRIBUTION_POLICY}


@router.get("/auth/invitation", tags=["auth"])
def held_referral(request: Request, db: OrmSession = Depends(get_db)):
    """The invitation this browser holds, so the sign-up page can say who the
    new account will be linked to. Nothing when there is none."""
    capture = held_capture(db, request)
    if capture is None:
        return {"inviter": None}
    inviter = eligible_referrer(db, capture.referrer_id)
    return {"inviter": _inviter_out(inviter), "expires_at": capture.expires_at}


def record(db: OrmSession, user: models.User, method: str, capture: models.ReferralCapture | None) -> None:
    """Write how the new member got their sponsor; spend the capture."""
    now = _now()
    if capture is not None:
        capture.used_by, capture.used_at = user.id, now
    db.add(models.ReferralAttribution(
        user_id=user.id, referrer_id=user.invited_by, method=method,
        capture_id=capture.id if capture else None,
        captured_at=capture.captured_at if capture else None,
        expires_at=capture.expires_at if capture else None,
        registered_at=now,
    ))


# --- administrators: the only way an established sponsor changes -------------

def _person(db: OrmSession, user_id: str | None) -> dict | None:
    if not user_id:
        return None
    u = db.get(models.User, user_id)
    return {"user_id": user_id, "display_name": u.display_name if u else None, "handle": u.handle if u else None,
            "status": u.status if u else "missing"}


@router.get("/admin/referrals/{user_id}", tags=["admin"])
def attribution_record(user_id: str, _: AdminUser, db: OrmSession = Depends(get_db)):
    """How a member got their sponsor, and every correction since."""
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    row = db.get(models.ReferralAttribution, user_id)
    corrections = db.scalars(
        select(models.SponsorCorrection).where(models.SponsorCorrection.user_id == user_id).order_by(models.SponsorCorrection.corrected_at)
    ).all()
    return {
        "user": _person(db, user_id),
        "sponsor": _person(db, user.invited_by),
        "registered_at": user.created_at,
        "attribution": None if row is None else {
            "method": row.method, "referrer": _person(db, row.referrer_id), "captured_at": row.captured_at,
            "expires_at": row.expires_at, "registered_at": row.registered_at,
        },
        "corrections": [
            {"from": _person(db, c.previous_sponsor_id), "to": _person(db, c.new_sponsor_id), "by": c.admin_id,
             "reason": c.reason, "at": c.corrected_at}
            for c in corrections
        ],
    }


class CorrectionIn(BaseModel):
    # A @handle, an email or an id, as the administrator knows the sponsor; empty removes the sponsor.
    sponsor: str | None = Field(default=None, max_length=254)
    reason: str = Field(min_length=10, max_length=500)


@router.post("/admin/referrals/{user_id}/sponsor", tags=["admin"])
def correct_sponsor(user_id: str, payload: CorrectionIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Correct a member's sponsor, with a reason, recorded for audit. Commissions
    already booked are not moved: from here on the new sponsor is used."""
    user = db.get(models.User, user_id)
    if user is None or user.status == "deleted":
        raise HTTPException(status_code=404, detail="User not found")
    new_sponsor: models.User | None = None
    if payload.sponsor and payload.sponsor.strip():
        key = payload.sponsor.strip()
        new_sponsor = db.scalar(
            select(models.User).where(
                (models.User.id == key) | (models.User.handle == key.lstrip("@")) | (models.User.email == key.lower())
            )
        )
        if eligible_referrer(db, new_sponsor.id if new_sponsor else None) is None:
            raise HTTPException(status_code=400, detail="The new sponsor must be an active member")
        if new_sponsor.id == user.id:
            raise HTTPException(status_code=400, detail="A member cannot sponsor themselves")
    new_id_ = new_sponsor.id if new_sponsor else None
    if new_id_ == user.invited_by:
        raise HTTPException(status_code=409, detail="That is already their sponsor")
    db.add(models.SponsorCorrection(
        user_id=user.id, previous_sponsor_id=user.invited_by, new_sponsor_id=new_id_,
        admin_id=admin.user_id, reason=payload.reason.strip(),
    ))
    user.invited_by = new_id_
    db.commit()
    log.info("sponsor correction: %s set the sponsor of %s", admin.user_id, user.id)
    return attribution_record(user_id, admin, db)
