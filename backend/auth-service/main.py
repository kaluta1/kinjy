"""Kinjy · auth-service

Accounts, sessions, passkeys, self-service deletion, the direct sponsor link
resolver every money service depends on.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import logging
import re

import httpx
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as OrmSession

from common import events, mailer, security, settings, sharelinks
from common.auth import AdminUser, ModeratorUser, CurrentUser, MaybeUser, SuperAdminUser
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import models
import passkeys
import agegate
import attribution
import referral_pool
import schemas

log = logging.getLogger("auth-service")

# `create_all` only ever creates missing *tables*, so a column added after the
# first deploy has to be stated here or it exists in the model and nowhere else.
MIGRATIONS = [
    f"ALTER TABLE {models.SCHEMA}.users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ",
    # Short links: the invitation became an option (links made before it all
    # carried one, hence the backfill default), links can be disabled, and
    # destinations keep their parameters. The old one-per-page constraint
    # gives way to one *active* link per page and choice.
    f"ALTER TABLE {models.SCHEMA}.share_links ADD COLUMN IF NOT EXISTS with_invitation BOOLEAN NOT NULL DEFAULT TRUE",
    f"ALTER TABLE {models.SCHEMA}.share_links ALTER COLUMN with_invitation SET DEFAULT FALSE",
    f"ALTER TABLE {models.SCHEMA}.share_links ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.share_links ALTER COLUMN destination TYPE VARCHAR(1000)",
    f"ALTER TABLE {models.SCHEMA}.share_links DROP CONSTRAINT IF EXISTS uq_share_link_member_destination",
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_share_link_active ON {models.SCHEMA}.share_links "
    "(member_id, destination, with_invitation) WHERE disabled_at IS NULL",
    f"CREATE INDEX IF NOT EXISTS ix_share_links_member_created ON {models.SCHEMA}.share_links (member_id, created_at)",
    f"ALTER TABLE {models.SCHEMA}.share_links ADD COLUMN IF NOT EXISTS opens INTEGER NOT NULL DEFAULT 0",
    # Referral pool units and rounds. The new tables come from create_all; the
    # assignment audit gains its round columns (null on older rows, which are
    # kept as they were).
    f"ALTER TABLE {models.SCHEMA}.pool_assignments ADD COLUMN IF NOT EXISTS pool_id VARCHAR(20)",
    f"ALTER TABLE {models.SCHEMA}.pool_assignments ADD COLUMN IF NOT EXISTS round_id VARCHAR(40)",
    f"ALTER TABLE {models.SCHEMA}.pool_assignments ADD COLUMN IF NOT EXISTS round_number INTEGER",
    f"ALTER TABLE {models.SCHEMA}.pool_assignments ADD COLUMN IF NOT EXISTS quota_total INTEGER",
    f"ALTER TABLE {models.SCHEMA}.pool_assignments ADD COLUMN IF NOT EXISTS quota_remaining_before INTEGER",
    f"CREATE INDEX IF NOT EXISTS ix_auth_pool_assignments_round_id ON {models.SCHEMA}.pool_assignments (round_id)",
    # Seats bought before units: one confirmed $100 entry is one unit. A seat
    # with no payment reference, another price or another currency cannot be
    # matched to a confirmed entry: it is migrated under review (counting for
    # nothing) for an administrator to settle. A refunded seat becomes a
    # reversed unit. Runs once per seat (the payment_ref is unique).
    rf"""INSERT INTO {models.SCHEMA}.referral_pool_purchases
          (id, pool_id, member_id, seat_id, payment_ref, units, units_reversed, unit_price, amount, currency,
           status, source, active_from_round, review_note, created_at)
        SELECT 'rpp_mig_' || s.id, 'main', s.member_id, s.id, COALESCE(s.payment_ref, 'migration:' || s.id),
               1, CASE WHEN s.status = 'refunded' THEN 1 ELSE 0 END, {settings.REFERRAL_POOL_ENTRY_USD},
               CASE WHEN s.price_paid ~ '^[0-9]+(\.[0-9]+)?$' THEN s.price_paid::numeric ELSE 0 END, s.currency,
               CASE WHEN s.payment_ref IS NULL OR s.currency <> 'USD' OR s.price_paid !~ '^[0-9]+(\.[0-9]+)?$'
                         OR s.price_paid::numeric <> {settings.REFERRAL_POOL_ENTRY_USD} THEN 'review'
                    WHEN s.status = 'refunded' THEN 'reversed'
                    ELSE 'credited' END,
               'migration', 1,
               CASE WHEN s.payment_ref IS NULL THEN 'migrated seat with no payment reference'
                    WHEN s.currency <> 'USD' OR s.price_paid !~ '^[0-9]+(\.[0-9]+)?$'
                         OR s.price_paid::numeric <> {settings.REFERRAL_POOL_ENTRY_USD}
                    THEN 'migrated seat paid ' || s.price_paid || ' ' || s.currency
                    ELSE NULL END,
               s.created_at
          FROM {models.SCHEMA}.referral_pool_seats s
         WHERE NOT EXISTS (SELECT 1 FROM {models.SCHEMA}.referral_pool_purchases p WHERE p.seat_id = s.id)""",
    f"""INSERT INTO {models.SCHEMA}.referral_pool_unit_ledger
          (pool_id, member_id, purchase_id, delta, kind, reason, actor, reference, created_at)
        SELECT p.pool_id, p.member_id, p.id, CASE WHEN p.status = 'review' THEN 0 ELSE p.units END, 'migration',
               COALESCE(p.review_note, 'one confirmed seat = one unit'), 'migration', 'migration:' || p.id,
               p.created_at
          FROM {models.SCHEMA}.referral_pool_purchases p
         WHERE p.source = 'migration'
           AND NOT EXISTS (SELECT 1 FROM {models.SCHEMA}.referral_pool_unit_ledger l WHERE l.reference = 'migration:' || p.id)""",
    f"""INSERT INTO {models.SCHEMA}.referral_pool_unit_ledger
          (pool_id, member_id, purchase_id, delta, kind, reason, actor, reference, created_at)
        SELECT p.pool_id, p.member_id, p.id, -1, 'refund', 'seat refunded before units', 'migration',
               'migration-refund:' || p.id, p.created_at
          FROM {models.SCHEMA}.referral_pool_purchases p
         WHERE p.source = 'migration' AND p.status = 'reversed'
           AND NOT EXISTS (SELECT 1 FROM {models.SCHEMA}.referral_pool_unit_ledger l WHERE l.reference = 'migration-refund:' || p.id)""",
]

app = create_app(
    name="auth-service",
    schema=models.SCHEMA,
    migrations=MIGRATIONS,
    description="Accounts, sessions, passkeys, account lifecycle, direct sponsor and referral pool.",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _referral_code() -> str:
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()


def _client(request: Request) -> tuple[str | None, str | None]:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    return ip, request.headers.get("user-agent")


def _issue(db: OrmSession, user: models.User, request: Request, label: str | None) -> schemas.TokenOut:
    ip, agent = _client(request)
    session = models.Session(
        id=new_id("ses"),
        user_id=user.id,
        device_label=label,
        user_agent=agent,
        ip=ip,
        expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
    )
    db.add(session)
    refresh, _ = security.create_refresh_token(user_id=user.id, session_id=session.id)
    access = security.create_access_token(
        user_id=user.id,
        role=user.role,
        handle=user.handle,
        kyc_verified=user.kyc_verified,
        lang=user.lang,
    )
    return schemas.TokenOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_TTL_MIN * 60,
    )


def _log_event(db: OrmSession, user_id: str, kind: str, request: Request, ok: bool = True) -> None:
    ip, agent = _client(request)
    db.add(models.LoginEvent(user_id=user_id, kind=kind, ip=ip, user_agent=agent, succeeded=ok))


def _active_user(db: OrmSession, user_id: str) -> models.User:
    user = db.get(models.User, user_id)
    if user is None or user.status in ("deleted",):
        raise HTTPException(status_code=404, detail="Account not found")
    return user


# ---------------------------------------------------------------------------
# Registration & login
# ---------------------------------------------------------------------------

@app.post("/auth/register", response_model=schemas.AuthOut, status_code=201, tags=["auth"])
async def register(payload: schemas.RegisterIn, request: Request, response: Response, db: OrmSession = Depends(get_db)):
    email = payload.email.lower().strip()
    clash = db.scalar(
        select(models.User).where(
            or_(func.lower(models.User.email) == email, models.User.handle == payload.handle)
        )
    )
    if clash is not None:
        field = "email" if clash.email.lower() == email else "handle"
        raise HTTPException(status_code=409, detail=f"This {field} is already taken")

    # --- age gate ---------------------------------------------------------
    # Before anything else is written. A refused registration must not leave an
    # account, a profile or a referral credit behind it.
    subject = agegate.subject_hash(email, (payload.country or "").upper())
    client_ip = request.client.host if request.client else None

    if agegate.in_cooldown(db, subject):
        # Deliberately the same wording and the same status as a plain refusal.
        # Telling someone "you are locked out for 24 hours" tells them the
        # refusal was age-based and that a different answer would have worked.
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="We cannot create an account with these details at the moment.",
        )

    age_engine = agegate.engine_for(db)
    verdict, tier, policy = age_engine.registration_eligibility(
        payload.date_of_birth, agegate._today(), payload.country
    )
    if not verdict.allowed:
        agegate.record_attempt(
            db, subject, "under_minimum",
            declared_age=None, jurisdiction=payload.country, ip=client_ip,
        )
        db.commit()
        # No mention of the required age, and no hint that a different date
        # would succeed. The message is the same for every refusal reason.
        raise HTTPException(
            status_code=403,
            detail=(
                "You do not currently meet the minimum age requirement to join Kinjy. "
                "If you believe this is a mistake, you can contact support."
            ),
        )

    inviter_id: str | None = None
    credited_link: str | None = None
    # An invitation link followed in the last REFERRAL_ATTRIBUTION_DAYS, held
    # by the server (see attribution.py), wins over whatever the form says:
    # clearing or changing the code field does not drop the inviter.
    held = attribution.held_capture(db, request)
    method = "none"
    if held is not None:
        inviter_id = held.referrer_id
        credited_link = held.link_token
        method = "captured"
    elif payload.referral_code:
        inviter = db.scalar(
            select(models.User).where(models.User.referral_code == payload.referral_code.strip().upper())
        )
        # Only a member who can still be credited: the same rule as a short
        # link's sharer (a deleted or closed account's code is not a referral).
        if attribution.eligible_referrer(db, inviter.id if inviter else None) is None:
            raise HTTPException(status_code=400, detail="Unknown referral code")
        inviter_id = inviter.id
        method = "code"
    elif payload.referral_link:
        # An invitation short link stands for the invitation of the member who
        # made it, exactly as their code would - same rules from here on. A
        # neutral link, a disabled one, or one whose sharer has left credits
        # nobody rather than refusing the account: the pool draws a sponsor
        # below, as for anybody who arrived without an invitation.
        found = _live_link(db, payload.referral_link.strip())
        sharer = _link_sharer(db, found[0]) if found else None
        if sharer is not None:
            inviter_id = sharer.id
            credited_link = found[0].token
            method = "link"
    # No personal invitation link: the referral pool draws a sponsor for them.
    # Resolved below, once the account has an id to record the assignment under.

    # Referral codes are short; retry on the (rare) collision instead of failing.
    for _ in range(5):
        code = _referral_code()
        if not db.scalar(select(models.User.id).where(models.User.referral_code == code)):
            break
    else:
        raise HTTPException(status_code=503, detail="Could not allocate a referral code, retry")

    user = models.User(
        id=new_id("usr"),
        email=email,
        handle=payload.handle,
        display_name=payload.display_name,  # cleaned by RegisterIn
        password_hash=security.hash_password(payload.password),
        lang=payload.lang if payload.lang in settings.SUPPORTED_LANGS else settings.DEFAULT_LANG,
        country=(payload.country or "").upper() or None,
        invited_by=inviter_id,
        referral_code=code,
    )
    db.add(user)
    db.flush()

    # The authoritative age record, written in the same transaction as the
    # account. An account that exists without one would be an account whose
    # tier every other service has to guess at.
    agegate.create_age_profile(db, user.id, payload.date_of_birth, tier, policy)
    agegate.record_attempt(
        db, subject, "allowed", jurisdiction=payload.country, ip=client_ip,
    )

    # The pool only after invitation attribution has found nobody: a valid
    # personal invitation always wins, and an established sponsor is never
    # replaced (this is the account's first and only attribution).
    if inviter_id is None:
        assignment = referral_pool.allocate(db, user.id)
        if assignment is not None:
            user.invited_by = assignment.sponsor_id
            inviter_id = assignment.sponsor_id
            method = "pool"
    attribution.record(db, user, method, held)
    if held is not None:
        attribution.clear_cookie(response)

    # No device label: the member's *name* is not a device name, and passing it
    # here made the device manager list "Demo K." instead of the browser.
    # Left None so the client derives it from the user agent.
    tokens = _issue(db, user, request, None)
    _log_event(db, user.id, "password", request)
    if credited_link:
        _record_conversion(db, credited_link, "registration", user.id)
    db.commit()
    db.refresh(user)

    await events.publish(
        "user.registered",
        {"user_id": user.id, "handle": user.handle, "invited_by": inviter_id, "country": user.country},
    )

    # Materialise the profile now rather than on the member's first visit to
    # their own page. Lazy creation meant a brand-new member existed to
    # auth-service and to nobody else: their posts showed a raw `usr_…` id and
    # they could not be found in people search at all — invisible until they
    # happened to open their own profile. Best-effort: a user-service hiccup
    # must not fail a registration, and the lazy path still covers it.
    try:
        httpx.post(
            "http://user-service:8000/internal/profiles/seed",
            json={
                "user_id": user.id,
                "handle": user.handle,
                "display_name": user.display_name,
                "country": user.country,
                "lang": user.lang,
                "verified": bool(user.kyc_verified),
            },
            timeout=3,
        )
    except Exception as exc:
        log.warning("could not seed profile for %s: %s", user.id, exc)

    # Into the welcome group, where everybody is. An internal call rather than
    # the event published above, for the same reason the profile is seeded
    # here: the broker has no durable queue behind it, so an event would be a
    # member who silently never joined. Best-effort - a messaging hiccup must
    # not cost somebody their account - and the join is idempotent, so a
    # sweep can pick up anyone this missed.
    try:
        httpx.post(
            "http://messaging-service:8000/internal/welcome/join",
            json={"user_id": user.id},
            timeout=3,
        )
    except Exception as exc:
        log.warning("could not add %s to the welcome group: %s", user.id, exc)

    return schemas.AuthOut(user=schemas.UserOut.model_validate(user), tokens=tokens)


@app.post("/auth/login", response_model=schemas.AuthOut, tags=["auth"])
def login(payload: schemas.LoginIn, request: Request, db: OrmSession = Depends(get_db)):
    user = db.scalar(select(models.User).where(func.lower(models.User.email) == payload.email.lower().strip()))
    # Same error and roughly the same work whether the email exists or not.
    if user is None or not user.password_hash or not security.verify_password(payload.password, user.password_hash):
        if user is not None:
            _log_event(db, user.id, "failed", request, ok=False)
            db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if user.status == "deleted":
        raise HTTPException(status_code=403, detail="This account has been deleted")
    if user.status in ("deactivated", "pending_deletion"):
        # Logging back in cancels a pending deletion — the cooling period exists
        # precisely so the member can change their mind.
        user.status = "active"
        user.deactivated_at = None
        user.deletion_requested_at = None
        user.deletion_effective_at = None

    user.last_login_at = _now()
    tokens = _issue(db, user, request, payload.device_label)
    _log_event(db, user.id, "password", request)
    db.commit()
    db.refresh(user)
    return schemas.AuthOut(user=schemas.UserOut.model_validate(user), tokens=tokens)


@app.post("/auth/refresh", response_model=schemas.TokenOut, tags=["auth"])
def refresh(payload: schemas.RefreshIn, db: OrmSession = Depends(get_db)):
    claims = security.decode_token(payload.refresh_token, expected_type=security.REFRESH)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    session = db.get(models.Session, claims.get("sid", ""))
    if session is None or not session.active or session.user_id != claims["sub"]:
        raise HTTPException(status_code=401, detail="Session revoked, sign in again")

    user = _active_user(db, session.user_id)
    session.last_seen_at = _now()
    db.commit()
    return schemas.TokenOut(
        access_token=security.create_access_token(
            user_id=user.id,
            role=user.role,
            handle=user.handle,
            kyc_verified=user.kyc_verified,
            lang=user.lang,
        ),
        refresh_token=payload.refresh_token,
        expires_in=settings.ACCESS_TOKEN_TTL_MIN * 60,
    )


@app.post("/auth/logout", status_code=204, tags=["auth"])
def logout(payload: schemas.RefreshIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    claims = security.decode_token(payload.refresh_token, expected_type=security.REFRESH)
    if claims:
        session = db.get(models.Session, claims.get("sid", ""))
        if session is not None and session.user_id == principal.user_id:
            session.revoked_at = _now()
            db.commit()


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

# How many codes one address may look up in a minute. An invitation code is
# meant to be passed around, and this answers only "does it exist, and whose
# is it" - but a lookup that answers as fast as you can ask it is a way to map
# the whole space, so it is capped. Generous enough that nobody browsing ever
# meets it.
REF_LOOKUPS_PER_MINUTE = 30
_ref_lookups: dict[str, list[float]] = {}
_ref_lock = threading.Lock()


def _ref_lookup_allowed(ip: str | None) -> bool:
    if not ip:
        return True
    now = time.time()
    with _ref_lock:
        recent = [t for t in _ref_lookups.get(ip, []) if now - t < 60]
        # Keeping only the live window also keeps this dict from growing
        # without bound, which is the usual way an in-process limiter becomes
        # the leak it was added to prevent.
        if len(_ref_lookups) > 5000:
            _ref_lookups.clear()
        if len(recent) >= REF_LOOKUPS_PER_MINUTE:
            _ref_lookups[ip] = recent
            return False
        recent.append(now)
        _ref_lookups[ip] = recent
    return True


@app.get("/auth/referrals", tags=["auth"])
def my_referrals(principal: CurrentUser, limit: int = 200, db: OrmSession = Depends(get_db)):
    """The people this member brought in. One level, which is the programme.

    The direct programme pays on these and nobody below them, so this is the
    whole of what a member earns from - and until now there was no way to see
    it: you could copy your link and never learn whether anyone had used it.

    Only your own. Deleted accounts are left out; a deactivated one stays,
    because they were still a real sign-up and may come back.
    """
    rows = db.scalars(
        select(models.User)
        .where(models.User.invited_by == principal.user_id, models.User.status != "deleted")
        .order_by(models.User.created_at.desc())
        .limit(max(1, min(limit, 500)))
    ).all()
    return {
        "total": len(rows),
        # What the programme actually pays on, counted here rather than left
        # for the page to work out from a list it may have truncated.
        "verified": sum(1 for r in rows if r.kyc_verified),
        "items": [
            {
                "user_id": r.id,
                "handle": r.handle,
                "display_name": r.display_name,
                "joined_at": r.created_at,
                "kyc_verified": r.kyc_verified,
                "status": r.status,
            }
            for r in rows
        ],
    }


@app.get("/auth/referral/{code}", tags=["auth"])
def referral_lookup(code: str, request: Request, db: OrmSession = Depends(get_db)):
    """Who an invitation code belongs to.

    The browser asks this before it remembers a code off a link, which is what
    stops `?ref=whatsapp` from a mangled share from displacing the code of the
    member who actually did the inviting. Guessing the shape client-side
    cannot do that: a real code is six to eight characters of A-Z0-9 and so is
    the word "whatsapp".

    It also lets the sign-up page say who invited you, which is the difference
    between a code in a box and a name you recognise.

    Only the public half of a profile, and 404 for anything unknown - the same
    answer a wrong code has always got at registration.
    """
    ip, _ = _client(request)
    if not _ref_lookup_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many lookups. Wait a moment.")
    cleaned = (code or "").strip().upper()
    if not cleaned or len(cleaned) > 16:
        raise HTTPException(status_code=404, detail="Unknown referral code")
    user = db.scalar(select(models.User).where(models.User.referral_code == cleaned))
    if user is None or user.status == "deleted":
        raise HTTPException(status_code=404, detail="Unknown referral code")
    return {"code": user.referral_code, "display_name": user.display_name, "handle": user.handle}


# ---------------------------------------------------------------------------
# Short links: kinjy.com/s/<token>
# ---------------------------------------------------------------------------

# A member makes links by hand now, so the limits are per member and generous
# for a person, tight for a script. Opening is per address, and only there to
# stop somebody walking the token space.
LINKS_PER_MINUTE = 20
LINKS_PER_DAY = 300
LINK_OPENS_PER_MINUTE = 120
_link_windows: dict[str, dict[str, list[float]]] = {"minute": {}, "day": {}, "open": {}}
_link_lock = threading.Lock()


def _within(bucket: str, key: str | None, limit: int, seconds: int) -> bool:
    """The same in-process sliding window as the referral lookups."""
    if not key:
        return True
    now = time.time()
    with _link_lock:
        window = _link_windows[bucket]
        recent = [t for t in window.get(key, []) if now - t < seconds]
        if len(window) > 5000:
            window.clear()
        if len(recent) >= limit:
            window[key] = recent
            return False
        recent.append(now)
        window[key] = recent
        return True


class ShareLinkIn(BaseModel):
    destination: str = Field(min_length=1, max_length=sharelinks.MAX_DESTINATION)
    # Off unless the member turns it on: a link is neutral by default.
    include_invitation: bool = False


def _link_out(link: models.ShareLink, created: bool | None = None) -> dict:
    out = {
        "token": link.token,
        "url": sharelinks.short_url(link.token),
        "destination": link.destination,
        "destination_url": f"{sharelinks.site_url()}{link.destination}",
        "kind": link.kind,
        "include_invitation": link.with_invitation,
        "active": link.disabled_at is None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "disabled_at": link.disabled_at.isoformat() if link.disabled_at else None,
        "opens": link.opens or 0,
    }
    if created is not None:
        out["created"] = created
    return out


def _active_link_for(db: OrmSession, member_id: str, destination: str, with_invitation: bool) -> models.ShareLink | None:
    return db.scalar(
        select(models.ShareLink).where(
            models.ShareLink.member_id == member_id,
            models.ShareLink.destination == destination,
            models.ShareLink.with_invitation.is_(with_invitation),
            models.ShareLink.disabled_at.is_(None),
        )
    )


def _live_link(db: OrmSession, token: str) -> tuple[models.ShareLink, str] | None:
    """An active stored link whose destination still passes the rules, and that destination."""
    if not sharelinks.TOKEN_RE.match(token or ""):
        return None
    link = db.get(models.ShareLink, token)
    if link is None or link.disabled_at is not None:
        return None
    try:
        destination, _ = sharelinks.normalize(link.destination)
    except sharelinks.BadDestination:
        return None
    return link, destination


_DEAL_PATH = re.compile(r"^/market/products/([A-Za-z0-9_-]{3,80})$")


def _deal(destination: str) -> dict | None:
    """The marketplace deal a destination names, in its public shape, if it is
    active; None when it is not a deal page or the deal is paused or gone.

    Asked of commerce-service, which only ever returns active listings here.
    Raises httpx.HTTPError when commerce-service does not answer.
    """
    match = _DEAL_PATH.match(destination.split("#", 1)[0].split("?", 1)[0])
    if match is None:
        return None
    response = httpx.post("http://commerce-service:8000/internal/products/batch",
                          json={"ids": [match.group(1)]}, timeout=6)
    response.raise_for_status()
    items = response.json().get("items") or []
    return items[0] if items else None


def _link_sharer(db: OrmSession, link: models.ShareLink) -> models.User | None:
    """Whose invitation a link carries: nobody for a neutral one, or once they have left."""
    if not link.with_invitation:
        return None
    sharer = db.get(models.User, link.member_id)
    return sharer if sharer is not None and sharer.status == "active" else None


@app.post("/auth/share-links", tags=["share-links"])
def create_share_link(payload: ShareLinkIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Shorten a Kinjy page, with or without the member's invitation.

    The invitation is whoever is signed in - never a code or an id from the
    request, and never one found on the pasted address (those are stripped).
    Asking again for the same page with the same choice returns the same
    active link; concurrent requests meet on the partial unique index.
    """
    user = _active_user(db, principal.user_id)
    if user.status != "active":
        raise HTTPException(status_code=403, detail="Your account cannot create links right now")

    raw = payload.destination
    nested = sharelinks.short_token_in(raw)
    if nested is not None:
        # A short link to a short link is one hop too many: shorten what it
        # points to instead, without the invitation it may have carried.
        found = _live_link(db, nested)
        if found is None:
            raise HTTPException(status_code=400, detail="That short link does not work")
        raw = found[1]
    try:
        destination, kind = sharelinks.normalize(raw)
    except sharelinks.BadDestination as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if _DEAL_PATH.match(destination.split("?", 1)[0]):
        # A deal link is made for a deal that can be bought now: a paused,
        # removed or unknown listing gets an answer instead of a dead link.
        try:
            if _deal(destination) is None:
                raise HTTPException(status_code=404, detail="This deal is not available to share")
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="The marketplace did not answer. Try again.")
    with_invitation = payload.include_invitation
    if with_invitation and not user.referral_code:
        raise HTTPException(status_code=400, detail="Your account has no invitation to attach")

    existing = _active_link_for(db, user.id, destination, with_invitation)
    if existing is not None:
        return _link_out(existing, created=False)
    if not _within("minute", user.id, LINKS_PER_MINUTE, 60) or not _within("day", user.id, LINKS_PER_DAY, 86400):
        raise HTTPException(status_code=429, detail="You have created a lot of links. Wait a moment and try again.")

    for _ in range(6):
        token = sharelinks.new_token()
        result = db.execute(
            pg_insert(models.ShareLink)
            .values(token=token, member_id=user.id, destination=destination, kind=kind,
                    with_invitation=with_invitation, created_at=_now())
            .on_conflict_do_nothing()
        )
        db.commit()
        if result.rowcount:
            return _link_out(db.get(models.ShareLink, token), created=True)
        # Either a concurrent request wrote the same link first, or the token
        # collided with somebody else's: take theirs, or draw again.
        row = _active_link_for(db, user.id, destination, with_invitation)
        if row is not None:
            return _link_out(row, created=False)
    log.error("short link: six token collisions in a row for %s", user.id)
    raise HTTPException(status_code=503, detail="Could not create the link, try again")


@app.get("/auth/share-links", tags=["share-links"])
def my_share_links(
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """The member's own links, newest first. Nobody else's, whoever asks."""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    base = select(models.ShareLink).where(models.ShareLink.member_id == principal.user_id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.order_by(models.ShareLink.created_at.desc(), models.ShareLink.token).offset(offset).limit(limit)
    ).all()
    counts: dict[tuple[str, str], int] = {}
    if rows:
        for token, kind, n in db.execute(
            select(models.ShareLinkConversion.token, models.ShareLinkConversion.kind, func.count())
            .where(models.ShareLinkConversion.token.in_([r.token for r in rows]))
            .group_by(models.ShareLinkConversion.token, models.ShareLinkConversion.kind)
        ).all():
            counts[(token, kind)] = n
    items = []
    for r in rows:
        out = _link_out(r)
        # Kept apart on purpose: an open is not a sign-up, and a sign-up is
        # not a join. Purchases and commissions come with paid features.
        out["registrations"] = counts.get((r.token, "registration"), 0)
        out["community_joins"] = counts.get((r.token, "community_join"), 0)
        items.append(out)
    return {"items": items, "total": total}


@app.get("/internal/share-links/{token}", tags=["internal"])
def internal_share_link(token: str, db: OrmSession = Depends(get_db)):
    """A link's maker and what it carries, for other services (community joins)."""
    link = db.get(models.ShareLink, token) if sharelinks.TOKEN_RE.match(token or "") else None
    if link is None:
        raise HTTPException(status_code=404, detail="No such link")
    return {"token": link.token, "member_id": link.member_id, "with_invitation": link.with_invitation,
            "destination": link.destination, "kind": link.kind, "active": link.disabled_at is None}


class ConversionIn(BaseModel):
    kind: str = Field(pattern="^(registration|community_join)$")
    subject_id: str = Field(min_length=1, max_length=40)


@app.post("/internal/share-links/{token}/conversions", tags=["internal"])
def record_conversion(token: str, payload: ConversionIn, db: OrmSession = Depends(get_db)):
    """Count what a link led to, once per subject (retries included)."""
    if not sharelinks.TOKEN_RE.match(token or "") or db.get(models.ShareLink, token) is None:
        raise HTTPException(status_code=404, detail="No such link")
    _record_conversion(db, token, payload.kind, payload.subject_id)
    db.commit()
    return {"ok": True}


def _record_conversion(db: OrmSession, token: str, kind: str, subject_id: str) -> None:
    db.execute(
        pg_insert(models.ShareLinkConversion)
        .values(token=token, kind=kind, subject_id=subject_id, created_at=_now())
        .on_conflict_do_nothing()
    )


@app.post("/auth/share-links/{token}/disable", tags=["share-links"])
def disable_share_link(token: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Switch a link off for good. Somebody else's link is a 404, not a 403:
    whether a token exists is not something a stranger gets to learn."""
    link = db.get(models.ShareLink, token) if sharelinks.TOKEN_RE.match(token or "") else None
    if link is None or link.member_id != principal.user_id:
        raise HTTPException(status_code=404, detail="This link does not exist")
    if link.disabled_at is None:
        link.disabled_at = _now()
        db.commit()
    return _link_out(link)


@app.get("/auth/share-links/{token}", tags=["share-links"])
def resolve_share_link(token: str, request: Request, db: OrmSession = Depends(get_db)):
    """Where a short link goes, and - for an invitation link - who shared it.

    Asked by the browser that followed it, so the sign-up page can say who
    invited them. Never the sharer's code, id or address: the token is what
    the browser keeps, and the server turns it back into a member at sign-up.
    """
    ip, _ = _client(request)
    if not _ref_lookup_allowed(ip):
        raise HTTPException(status_code=429, detail="Too many lookups. Wait a moment.")
    found = _live_link(db, token)
    if found is None:
        raise HTTPException(status_code=404, detail="This link does not exist")
    link, destination = found
    sharer = _link_sharer(db, link)
    return {
        "token": link.token,
        "destination": destination,
        "kind": link.kind,
        "invitation": sharer is not None,
        "inviter": {"display_name": sharer.display_name} if sharer else None,
    }


def _preview_html(title: str, description: str, image: str, url: str, go_to: str) -> str:
    esc = html_lib.escape
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Kinjy">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{esc(image)}">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={esc(go_to)}">
</head>
<body><p><a href="{esc(go_to)}">{esc(title)}</a></p></body>
</html>"""


_META_RE = re.compile(r'<meta\s+(?:property|name)="(og:title|og:description|og:image)"\s+content="([^"]*)"', re.I)


def _preview_for(link: models.ShareLink, destination: str) -> tuple[str, str, str]:
    """(title, description, image) a crawler should see. Public information only."""
    site = sharelinks.site_url()
    title = "Kinjy"
    description = "Social, forums, family trees, memorials, commerce and AI agents - in one elegant ecosystem."
    image = f"{site}/og-image.png"
    path = destination.split("#", 1)[0].split("?", 1)[0]
    try:
        if link.kind == "post":
            # The post's own preview, drawn by social-service with its rules
            # (as an anonymous viewer, picture by the article's own rule): a
            # post that is not public gets the generic card there too.
            post_id = path.split("/p/", 1)[1]
            page = httpx.get(f"http://social-service:8000/og/p/{post_id}", timeout=6).text
            found = {k.lower(): html_lib.unescape(v) for k, v in _META_RE.findall(page)}
            title = found.get("og:title") or "A post on Kinjy"
            description = found.get("og:description") or description
            image = found.get("og:image") or image
        elif link.kind == "community" and path != "/communities":
            slug = path.split("/communities/", 1)[1]
            info = httpx.get(f"http://community-service:8000/internal/communities/{slug}", timeout=6)
            community = info.json() if info.status_code == 200 else {}
            # A secret community's name is what it keeps from strangers.
            if community and community.get("kind") != "secret":
                title = f"{community.get('name')} on Kinjy"
                description = (community.get("description") or "A community on Kinjy.")[:200]
                picture = community.get("cover_url") or community.get("avatar_url")
                if picture and picture.startswith("/"):
                    picture = f"{site}{picture}"
                if picture and picture.startswith("http"):
                    image = picture
            else:
                title, description = "A community on Kinjy", "You have been invited to a community on Kinjy."
        elif _DEAL_PATH.match(path):
            # The deal's public card: title, a line of description, its first
            # picture. Nothing about who shared it.
            deal = _deal(path)
            if deal:
                title = f"{deal.get('title')} on Kinjy"
                # An ebook's description is rich text: the preview takes its words only.
                text = html_lib.unescape(re.sub(r"<[^>]+>", " ", deal.get("description") or ""))
                description = " ".join(text.split())[:200] or "A deal on the Kinjy marketplace."
                picture = (deal.get("images") or [None])[0]
                if picture and picture.startswith("/"):
                    picture = f"{site}{picture}"
                if picture and picture.startswith("http"):
                    image = picture
            else:
                title, description = "A deal on Kinjy", "This deal is no longer available on the Kinjy marketplace."
        elif link.kind == "chat":
            title = "Join a conversation on Kinjy"
            description = "You have been invited to a group conversation on Kinjy."
        elif link.kind == "profile":
            title = "A member on Kinjy"
        elif path == "/join":
            title = "Join Kinjy"
        elif link.kind == "invite":
            title = "You are invited to Kinjy" if link.with_invitation else "Kinjy"
    except Exception as exc:  # a preview is best effort; the link still works
        log.warning("short link preview for %s failed: %s", link.token, exc)
    return title, description, image


# Never stored by a shared cache: the answer depends on the user agent (a
# preview or a redirect) and is checked against the link on every open.
_NO_STORE = {"Cache-Control": "private, no-store, max-age=0", "Vary": "User-Agent"}


@app.api_route("/auth/s/{token}", methods=["GET", "HEAD"], tags=["share-links"], include_in_schema=False)
def open_share_link(token: str, request: Request, db: OrmSession = Depends(get_db)):
    """What kinjy.com/s/<token> answers (the edge sends /s/* here).

    A link unfurler gets a small page with the destination's public preview -
    no sign-in, no JavaScript, no invitation in it. Everybody else gets a 302
    to the destination; an invitation link adds its token, which the browser
    holds until sign-up. The destination page applies its own rules as it
    always does: a short link opens nothing its address would not.

    Read-only either way: opening a link, by a crawler or a person, creates no
    registration, commission or reward, and records nothing about the visitor.
    """
    ip, agent = _client(request)
    if not _within("open", ip, LINK_OPENS_PER_MINUTE, 60):
        return HTMLResponse("Too many requests. Wait a moment.", status_code=429, headers=_NO_STORE)
    found = _live_link(db, token)
    if found is None:
        # Said plainly, on a page of its own - never a quiet redirect home.
        return RedirectResponse("/link-unavailable", status_code=302, headers=_NO_STORE)
    link, destination = found
    if sharelinks.CRAWLER_RE.search(agent or ""):
        title, description, image = _preview_for(link, destination)
        return HTMLResponse(
            _preview_html(title, description, image, sharelinks.short_url(link.token), destination),
            headers=_NO_STORE,
        )
    # Counted, not recorded: the number goes up, nothing about the visitor is kept.
    db.execute(update(models.ShareLink).where(models.ShareLink.token == link.token)
               .values(opens=models.ShareLink.opens + 1))
    db.commit()
    # A neutral link adds nothing, so whatever invitation the visitor already
    # holds is left exactly as it was. An invitation link whose sharer has
    # left still opens; it just credits nobody.
    go_to = sharelinks.with_via(destination, link.token) if _link_sharer(db, link) else destination
    # 302, not 301: a permanent redirect is cached by the browser, and the
    # link's state (and the sharer's account) is checked on each open.
    return RedirectResponse(go_to, status_code=302, headers=_NO_STORE)


# ---------------------------------------------------------------------------
# Me / sessions / passkeys
# ---------------------------------------------------------------------------

@app.get("/auth/me", response_model=schemas.UserOut, tags=["auth"])
def me(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    return schemas.UserOut.model_validate(_active_user(db, principal.user_id))


@app.get("/auth/sessions", response_model=list[schemas.SessionOut], tags=["auth"])
def list_sessions(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.Session)
        .where(models.Session.user_id == principal.user_id)
        .order_by(models.Session.last_seen_at.desc())
    ).all()
    return [schemas.SessionOut.model_validate(row) for row in rows]


@app.delete("/auth/sessions/{session_id}", status_code=204, tags=["auth"])
def revoke_session(session_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    session = db.get(models.Session, session_id)
    if session is None or session.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    session.revoked_at = _now()
    db.commit()


# How many wrong guesses at the current password before this route stops
# answering, and over what window. The caller already holds a valid token, so
# this is not the front door - but it is the one place where guessing the
# password of an account you are already inside pays off, which is exactly the
# position somebody with a borrowed laptop is in.
PASSWORD_CHANGE_MAX_FAILURES = 5
PASSWORD_CHANGE_WINDOW_MIN = 15


@app.post("/auth/password", tags=["auth"])
async def change_password(
    payload: schemas.PasswordChangeIn, principal: CurrentUser, request: Request,
    db: OrmSession = Depends(get_db),
):
    """Change your own password, and sign the other devices out.

    Signing the others out is the behaviour, not an option, because the reason
    a member changes a password is usually that somebody else has it. Leaving
    those sessions alive would make the change cosmetic.

    One limit worth being honest about: access tokens are stateless and are not
    invalidated here, so a token already issued keeps working until it expires
    (ACCESS_TOKEN_TTL_MIN, 30 minutes by default). Revoking the sessions stops
    it being *renewed*, which closes the hole within that window rather than
    instantly.
    """
    user = _active_user(db, principal.user_id)

    if not user.password_hash:
        # Nothing to change. Inventing a "set your first password" flow here
        # would let anyone holding a passkey session add a second way in, which
        # is a decision for the account's owner to make knowingly.
        raise HTTPException(
            status_code=409,
            detail="This account signs in with a passkey and has no password to change.",
        )

    since = _now() - timedelta(minutes=PASSWORD_CHANGE_WINDOW_MIN)
    recent_failures = db.scalar(
        select(func.count())
        .select_from(models.LoginEvent)
        .where(
            models.LoginEvent.user_id == user.id,
            models.LoginEvent.kind == "password_change",
            models.LoginEvent.succeeded.is_(False),
            models.LoginEvent.created_at >= since,
        )
    ) or 0
    if recent_failures >= PASSWORD_CHANGE_MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect attempts. Wait a few minutes and try again.",
        )

    if not security.verify_password(payload.current_password, user.password_hash):
        _log_event(db, user.id, "password_change", request, ok=False)
        db.commit()
        raise HTTPException(status_code=400, detail="That is not your current password")

    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="The new password is the same as the current one")

    now = _now()
    user.password_hash = security.hash_password(payload.new_password)
    user.password_changed_at = now

    # The session to keep, if the caller named one and it is theirs. An
    # unparseable or foreign token keeps nothing rather than failing the
    # change: the password is the thing that had to change.
    keep_session_id = None
    if payload.refresh_token:
        claims = security.decode_token(payload.refresh_token, expected_type=security.REFRESH)
        if claims and claims.get("sub") == user.id:
            keep_session_id = claims.get("sid")

    ended = 0
    for session in user.sessions:
        if session.id == keep_session_id or not session.active:
            continue
        session.revoked_at = now
        ended += 1

    _log_event(db, user.id, "password_change", request)
    db.commit()

    await events.publish(
        "user.password_changed",
        {"user_id": user.id, "sessions_ended": ended, "at": now.isoformat()},
    )
    return {
        "changed_at": now,
        "sessions_ended": ended,
        "signed_out_here": keep_session_id is None,
    }


# --- Forgotten passwords ------------------------------------------------------
#
# The one flow that has to reach somebody who cannot sign in, which is why it is
# the only thing on the platform that sends mail.
#
# How long a link lives. Long enough to survive a slow mail server and somebody
# reading their mail on the way home; short enough that a message left open on a
# shared screen stops being a key fairly soon.
RESET_TTL_MINUTES = 60
# Per address and per asking IP, over the same window. Both cap how much mail
# one source can cause: the address limit stops a member being mail-bombed
# through our server, the IP limit stops one caller doing it to many members at
# once. Neither limits probing for *unknown* addresses, because an address with
# no account creates no row - that is what the identical answers are for.
RESET_MAX_PER_EMAIL = 3
RESET_MAX_PER_IP = 10
RESET_WINDOW_MINUTES = 60


def _reset_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@app.get("/auth/password/reset-available", tags=["auth"])
def reset_available():
    """Whether a reset can actually be delivered.

    The sign-in page asks before offering the link. Without this it would offer
    a form that can only ever answer "not set up", which reads as a fault on the
    member's side. Says nothing about any account - only whether this
    installation can send mail at all.
    """
    return {"available": mailer.configured()}


@app.post("/auth/password/reset-request", status_code=202, tags=["auth"])
def request_password_reset(
    payload: schemas.PasswordResetRequestIn, request: Request, db: OrmSession = Depends(get_db),
):
    """Ask for a reset link.

    Answers 202 whether or not the address belongs to an account, and whether or
    not the send succeeded. That is the point: any difference - a status code, a
    message, a noticeably faster reply - turns this into a way of asking "does
    this person have a Kinjy account", which for some members is a dangerous
    question to let a stranger answer.

    The one thing it does not hide is whether the platform can send mail at all,
    because that is true of everybody equally and a member deserves to know they
    are waiting for nothing.
    """
    if not mailer.configured():
        raise HTTPException(
            status_code=503,
            detail="Password reset is not available on this installation yet.",
        )

    ip, agent = _client(request)
    email = payload.email.lower().strip()
    now = _now()
    since = now - timedelta(minutes=RESET_WINDOW_MINUTES)

    user = db.scalar(select(models.User).where(func.lower(models.User.email) == email))

    # Counted before the account is known to exist, so a rate-limited address
    # and an unknown one still answer the same way.
    by_ip = 0
    if ip:
        by_ip = db.scalar(
            select(func.count()).select_from(models.PasswordReset)
            .where(models.PasswordReset.requested_ip == ip,
                   models.PasswordReset.created_at >= since)
        ) or 0

    if user is not None and user.status != "deleted" and by_ip < RESET_MAX_PER_IP:
        by_email = db.scalar(
            select(func.count()).select_from(models.PasswordReset)
            .where(models.PasswordReset.user_id == user.id, models.PasswordReset.created_at >= since)
        ) or 0
        if by_email < RESET_MAX_PER_EMAIL:
            # A new link retires the ones before it: two live links are two
            # chances for the wrong person, and the member is looking at the
            # newest mail anyway.
            for old in db.scalars(
                select(models.PasswordReset).where(
                    models.PasswordReset.user_id == user.id,
                    models.PasswordReset.used_at.is_(None),
                    models.PasswordReset.invalidated_at.is_(None),
                )
            ).all():
                old.invalidated_at = now

            token = secrets.token_urlsafe(32)
            db.add(models.PasswordReset(
                id=new_id("pwr"),
                user_id=user.id,
                token_hash=_reset_token_hash(token),
                expires_at=now + timedelta(minutes=RESET_TTL_MINUTES),
                requested_ip=ip,
                requested_user_agent=agent,
            ))
            db.commit()

            link = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={token}"
            try:
                mailer.send(user.email, "Reset your Kinjy password",
                            mailer.password_reset_body(user.display_name, link))
            except mailer.MailError:
                # Already logged, without the address. The caller is told
                # nothing: a failure that only happens for real accounts would
                # be exactly the signal this endpoint refuses to give.
                log.error("reset mail could not be sent for an account")

    return {"status": "accepted",
            "note": "If that address has an account, a link is on its way."}


@app.post("/auth/password/reset", tags=["auth"])
async def confirm_password_reset(
    payload: schemas.PasswordResetConfirmIn, request: Request, db: OrmSession = Depends(get_db),
):
    """Set a new password from a link.

    Every session goes, with no exception for the caller: whoever is doing this
    could not sign in a minute ago, so there is no session of theirs worth
    keeping, and any that exist belong to whoever had the account before.
    """
    row = db.scalar(
        select(models.PasswordReset)
        .where(models.PasswordReset.token_hash == _reset_token_hash(payload.token))
    )
    # One answer for every way of being wrong - unknown, used, expired,
    # superseded - so a caller cannot sort real tokens from invented ones.
    if row is None or not row.usable:
        raise HTTPException(
            status_code=400,
            detail="This link has expired or has already been used. Ask for a new one.",
        )

    user = db.get(models.User, row.user_id)
    if user is None or user.status == "deleted":
        raise HTTPException(status_code=400, detail="This link is no longer valid.")

    now = _now()
    user.password_hash = security.hash_password(payload.new_password)
    user.password_changed_at = now
    row.used_at = now

    ended = 0
    for session in user.sessions:
        if session.active:
            session.revoked_at = now
            ended += 1

    # Signing in again cancels a pending deletion elsewhere in this service; a
    # member recovering an account is doing the same thing by another door.
    if user.status in ("deactivated", "pending_deletion"):
        user.status = "active"
        user.deactivated_at = None
        user.deletion_requested_at = None
        user.deletion_effective_at = None

    _log_event(db, user.id, "password_reset", request)
    db.commit()

    await events.publish(
        "user.password_reset", {"user_id": user.id, "sessions_ended": ended, "at": now.isoformat()},
    )
    return {"status": "reset", "sessions_ended": ended,
            "note": "Sign in with your new password."}


@app.get("/auth/passkeys", response_model=list[schemas.PasskeyOut], tags=["passkeys"])
def list_passkeys(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(select(models.Passkey).where(models.Passkey.user_id == principal.user_id)).all()
    return [schemas.PasskeyOut.model_validate(row) for row in rows]


@app.post("/auth/passkeys/register/options", tags=["passkeys"])
def passkey_register_options(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Step 1 of registration — the browser passes this to navigator.credentials.create()."""
    user = _active_user(db, principal.user_id)
    existing = [k.credential_id for k in user.passkeys]
    return passkeys.registration_options(
        user_id=user.id, handle=user.handle, display_name=user.display_name, existing=existing
    )


class PasskeyVerifyIn(BaseModel):
    credential: dict
    label: str | None = None


@app.post("/auth/passkeys/register/verify", status_code=201, tags=["passkeys"])
def passkey_register_verify(
    payload: PasskeyVerifyIn,
    principal: CurrentUser,
    request: Request,
    db: OrmSession = Depends(get_db),
):
    """Step 2 — verify the attestation and keep only the public key."""
    user = _active_user(db, principal.user_id)
    try:
        credential_id, public_key, sign_count = passkeys.verify_registration(
            user_id=user.id, credential=payload.credential
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Passkey registration failed: {exc}")

    if db.scalar(select(models.Passkey).where(models.Passkey.credential_id == credential_id)):
        raise HTTPException(status_code=409, detail="This passkey is already registered")

    agent = request.headers.get("user-agent", "")
    default_label = (
        "iPhone" if "iPhone" in agent else
        "Android" if "Android" in agent else
        "Mac" if "Mac OS X" in agent else
        "Windows" if "Windows" in agent else
        "Passkey"
    )
    key = models.Passkey(
        id=new_id("pky"),
        user_id=user.id,
        credential_id=credential_id,
        public_key=public_key,
        sign_count=sign_count,
        label=payload.label or default_label,
    )
    db.add(key)
    _log_event(db, user.id, "passkey", request)
    db.commit()
    return {"id": key.id, "label": key.label}


class PasskeyLoginStart(BaseModel):
    email: str


@app.post("/auth/passkeys/login/options", tags=["passkeys"])
def passkey_login_options(payload: PasskeyLoginStart, db: OrmSession = Depends(get_db)):
    user = db.scalar(select(models.User).where(func.lower(models.User.email) == payload.email.lower().strip()))
    # Same shape whether or not the account exists: revealing "no passkey here"
    # would turn this endpoint into an account-enumeration oracle.
    allow = [k.credential_id for k in user.passkeys] if user else []
    handle = user.handle if user else passkeys.new_handle()
    return {"handle": handle, **passkeys.authentication_options(handle=handle, allow=allow)}


class PasskeyLoginFinish(BaseModel):
    handle: str
    credential: dict


@app.post("/auth/passkeys/login/verify", response_model=schemas.AuthOut, tags=["passkeys"])
def passkey_login_verify(
    payload: PasskeyLoginFinish, request: Request, db: OrmSession = Depends(get_db)
):
    user = db.scalar(select(models.User).where(models.User.handle == payload.handle))
    if user is None:
        raise HTTPException(status_code=401, detail="Passkey not recognised")

    raw_id = payload.credential.get("rawId") or payload.credential.get("id") or ""
    try:
        credential_id = passkeys.unb64(raw_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed credential")

    key = db.scalar(
        select(models.Passkey).where(
            models.Passkey.user_id == user.id, models.Passkey.credential_id == credential_id
        )
    )
    if key is None:
        raise HTTPException(status_code=401, detail="Passkey not recognised")

    try:
        new_count = passkeys.verify_authentication(
            handle=payload.handle,
            credential=payload.credential,
            public_key=key.public_key,
            sign_count=key.sign_count,
        )
    except Exception as exc:
        _log_event(db, user.id, "passkey", request, ok=False)
        db.commit()
        raise HTTPException(status_code=401, detail=f"Passkey rejected: {exc}")

    # A counter that fails to advance is the classic cloned-authenticator signal.
    key.sign_count = new_count
    key.last_used_at = _now()
    user.last_login_at = _now()
    tokens = _issue(db, user, request, None)
    _log_event(db, user.id, "passkey", request)
    db.commit()
    db.refresh(user)
    return schemas.AuthOut(user=schemas.UserOut.model_validate(user), tokens=tokens)


@app.delete("/auth/passkeys/{passkey_id}", status_code=204, tags=["passkeys"])
def delete_passkey(passkey_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    key = db.get(models.Passkey, passkey_id)
    if key is None or key.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Passkey not found")
    db.delete(key)
    db.commit()


# ---------------------------------------------------------------------------
# Account lifecycle — self-service (blueprint §4)
# ---------------------------------------------------------------------------

@app.post("/auth/account/delete", tags=["lifecycle"])
async def delete_account(
    payload: schemas.DeleteAccountIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    user = _active_user(db, principal.user_id)
    if not user.password_hash or not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Identity confirmation failed")

    now = _now()
    if payload.mode == "deactivate":
        user.status = "deactivated"
        user.deactivated_at = now
    else:
        user.status = "pending_deletion"
        user.deletion_requested_at = now
        user.deletion_effective_at = now + timedelta(days=payload.cooling_period_days)

    for session in user.sessions:
        session.revoked_at = now
    db.commit()

    await events.publish(
        "user.deletion_requested",
        {"user_id": user.id, "mode": payload.mode, "effective_at": str(user.deletion_effective_at)},
    )
    return {
        "status": user.status,
        "effective_at": user.deletion_effective_at,
        "reversible_until": user.deletion_effective_at,
        "note": "Signing in again before the effective date cancels the deletion.",
    }


# ---------------------------------------------------------------------------
# Internal — consumed by other services, never exposed through the gateway
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Referral pool
# ---------------------------------------------------------------------------

@app.get("/auth/referral-pool", tags=["referral-pool"])
def referral_pool_state(principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """Unit price, units sold, the round in progress, and this member's units.

    Readable signed out so the programme page can show the real counter rather
    than a number baked into the page.
    """
    return referral_pool.state(db, principal.user_id if principal else None)


@app.get("/auth/referral-pool/assignments", tags=["referral-pool"])
def referral_pool_assignments(
    principal: CurrentUser, limit: int = 50, db: OrmSession = Depends(get_db)
):
    """Who the pool has sent this member, newest first."""
    rows = db.scalars(
        select(models.PoolAssignment)
        .where(models.PoolAssignment.sponsor_id == principal.user_id)
        .order_by(models.PoolAssignment.created_at.desc())
        .limit(min(limit, 200))
    ).all()
    members = {
        u.id: u
        for u in db.scalars(
            select(models.User).where(models.User.id.in_([r.new_member_id for r in rows] or [""]))
        ).all()
    }
    return {
        "items": [
            {
                "member_id": r.new_member_id,
                "handle": members[r.new_member_id].handle if r.new_member_id in members else None,
                "display_name": members[r.new_member_id].display_name
                if r.new_member_id in members
                else None,
                "seats_in_draw": r.seats_in_draw,
                "round_number": r.round_number,
                "assigned_at": r.created_at,
            }
            for r in rows
        ]
    }


class PoolPurchaseIn(BaseModel):
    """A settled payment for pool units, handed over by payment-service."""

    member_id: str
    payment_ref: str = Field(min_length=1, max_length=80)
    units: int
    amount: str
    currency: str = "USD"


class PoolReverseIn(BaseModel):
    units: int = Field(ge=1)
    kind: str = Field(pattern="^(refund|reversal)$")
    reason: str = Field(default="", max_length=300)
    # The rail's own id for the refund, so the same notification moves units once.
    reference: str | None = Field(default=None, max_length=80)


_POOL_ERRORS = {"pool_full": 409, "mismatch": 409, "too_many": 409}


def _pool_error(exc: referral_pool.PoolError) -> HTTPException:
    return HTTPException(status_code=_POOL_ERRORS.get(exc.code, 400), detail=str(exc))


def _purchase_out(p: models.ReferralPoolPurchase) -> dict:
    return {
        "id": p.id,
        "member_id": p.member_id,
        "payment_ref": p.payment_ref,
        "units": p.units,
        "units_reversed": p.units_reversed,
        "active_units": p.active_units,
        "unit_price": str(p.unit_price),
        "amount": str(p.amount),
        "currency": p.currency,
        "status": p.status,
        "source": p.source,
        "active_from_round": p.active_from_round,
        "review_note": p.review_note,
        "created_at": p.created_at,
    }


@app.get("/internal/referral-pool/can-buy/{member_id}", tags=["internal"])
def referral_pool_can_buy(member_id: str, db: OrmSession = Depends(get_db)):
    """Asked by payment-service before taking money: the cap counts members."""
    allowed, why = referral_pool.can_buy(db, member_id)
    return {"allowed": allowed, "reason": why}


@app.post("/internal/referral-pool/purchases", tags=["internal"])
def credit_referral_pool_purchase(payload: PoolPurchaseIn, db: OrmSession = Depends(get_db)):
    """Credit units for a settled payment. Called by payment-service only.

    Idempotent: the same payment twice credits once (``created`` false).
    """
    if db.get(models.User, payload.member_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        purchase, created = referral_pool.credit_purchase(
            db, payload.member_id, payment_ref=payload.payment_ref, units=payload.units,
            amount=payload.amount, currency=payload.currency,
        )
    except referral_pool.PoolError as exc:
        db.rollback()
        raise _pool_error(exc)
    db.commit()
    return {**_purchase_out(purchase), "created": created}


@app.post("/internal/referral-pool/seats", tags=["internal"])
def grant_referral_pool_seat(payload: schemas.PoolSeatIn, db: OrmSession = Depends(get_db)):
    """The pre-units call (one $100 seat): one unit. Kept so a payment-service
    still running the old code during a deploy credits correctly."""
    if not payload.payment_ref:
        raise HTTPException(status_code=400, detail="A payment reference is required")
    result = credit_referral_pool_purchase(
        PoolPurchaseIn(member_id=payload.member_id, payment_ref=payload.payment_ref, units=1,
                       amount=payload.amount, currency=payload.currency),
        db,
    )
    return {**result, "already_seated": not result["created"]}


@app.post("/internal/referral-pool/purchases/{payment_ref}/reverse", tags=["internal"])
def reverse_referral_pool_purchase(payment_ref: str, payload: PoolReverseIn, db: OrmSession = Depends(get_db)):
    """A rail reported a refund or a reversal of a pool payment."""
    purchase = db.scalar(
        select(models.ReferralPoolPurchase).where(models.ReferralPoolPurchase.payment_ref == payment_ref)
    )
    if purchase is None:
        raise HTTPException(status_code=404, detail="No pool purchase for that payment")
    try:
        entry, created = referral_pool.reverse_units(
            db, purchase, units=payload.units, kind=payload.kind, reason=payload.reason or payload.kind,
            actor="payment-service", reference=payload.reference,
        )
    except referral_pool.PoolError as exc:
        db.rollback()
        raise _pool_error(exc)
    db.commit()
    return {**_purchase_out(purchase), "reversal_created": created}


@app.post("/admin/referral-pool/seats/{seat_id}/status", tags=["admin"])
def set_seat_status(seat_id: str, status: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Suspend or restore a member's place in the pool. A suspended member
    stops receiving assignments at once - the unused part of their current
    round goes - but keeps their history: fraud review needs the record."""
    if status not in ("active", "suspended", "refunded"):
        raise HTTPException(status_code=400, detail="status must be active, suspended or refunded")
    seat = db.get(models.ReferralPoolSeat, seat_id)
    if seat is None:
        raise HTTPException(status_code=404, detail="Seat not found")
    seat.status = status
    if status != "active":
        referral_pool.exclude_member(db, seat.member_id, f"seat_{status}")
    db.commit()
    log.info("referral pool: %s set seat %s to %s", admin.user_id, seat.id, status)
    return {"seat_id": seat.id, "status": seat.status}


@app.get("/admin/referral-pool/overview", tags=["admin"])
def admin_pool_overview(_: AdminUser, db: OrmSession = Depends(get_db)):
    """The pool at a glance, and what needs a decision."""
    base = referral_pool.state(db)
    review = db.scalar(
        select(func.count()).select_from(models.ReferralPoolPurchase).where(models.ReferralPoolPurchase.status == "review")
    ) or 0
    unassigned = db.scalar(
        select(func.count()).select_from(models.ReferralPoolUnassigned).where(
            models.ReferralPoolUnassigned.resolved_at.is_(None)
        )
    ) or 0
    members = db.scalar(select(func.count()).select_from(models.ReferralPoolSeat)) or 0
    return {**base, "members": members, "purchases_under_review": review, "unassigned_open": unassigned}


@app.get("/admin/referral-pool/members", tags=["admin"])
def admin_pool_members(_: AdminUser, limit: int = 100, offset: int = 0, db: OrmSession = Depends(get_db)):
    """Every pool member: units, current round share, referrals received."""
    limit = max(1, min(limit, 200))
    current = referral_pool.open_round(db)
    seats = db.scalars(
        select(models.ReferralPoolSeat).order_by(models.ReferralPoolSeat.seat_number).offset(max(0, offset)).limit(limit)
    ).all()
    ids = [s.member_id for s in seats] or [""]
    users = {u.id: u for u in db.scalars(select(models.User).where(models.User.id.in_(ids))).all()}
    quotas = {}
    if current is not None:
        quotas = {
            q.member_id: q
            for q in db.scalars(
                select(models.ReferralPoolQuota).where(
                    models.ReferralPoolQuota.round_id == current.id, models.ReferralPoolQuota.member_id.in_(ids)
                )
            ).all()
        }
    items = []
    for seat in seats:
        quota = quotas.get(seat.member_id)
        units = referral_pool.member_units(db, seat.member_id)
        user = users.get(seat.member_id)
        items.append({
            "seat_id": seat.id,
            "seat_number": seat.seat_number,
            "member_id": seat.member_id,
            "handle": user.handle if user else None,
            "display_name": user.display_name if user else None,
            "status": seat.status,
            **units,
            "round_quota_total": quota.quota_total if quota else 0,
            "round_assigned": quota.assigned if quota else 0,
            "round_excluded_reason": quota.excluded_reason if quota else None,
        })
    total = db.scalar(select(func.count()).select_from(models.ReferralPoolSeat)) or 0
    return {"items": items, "total": total}


@app.get("/admin/referral-pool/purchases", tags=["admin"])
def admin_pool_purchases(
    _: AdminUser, status: str | None = None, member_id: str | None = None,
    limit: int = 100, offset: int = 0, db: OrmSession = Depends(get_db),
):
    q = select(models.ReferralPoolPurchase)
    if status:
        q = q.where(models.ReferralPoolPurchase.status == status)
    if member_id:
        q = q.where(models.ReferralPoolPurchase.member_id == member_id)
    rows = db.scalars(
        q.order_by(models.ReferralPoolPurchase.created_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))
    ).all()
    return {"items": [_purchase_out(p) for p in rows]}


class PoolAdminReverseIn(BaseModel):
    units: int = Field(ge=1)
    kind: str = Field(pattern="^(refund|reversal)$")
    reason: str = Field(min_length=3, max_length=300)


@app.post("/admin/referral-pool/purchases/{purchase_id}/reverse", tags=["admin"])
def admin_reverse_purchase(
    purchase_id: str, payload: PoolAdminReverseIn, admin: AdminUser, db: OrmSession = Depends(get_db)
):
    """Record a refund or a payment reversal against one purchase's units.

    Units only: the money itself is refunded on the rail, and the revenue
    journal reversed in the ledger, as for any other payment.
    """
    purchase = db.get(models.ReferralPoolPurchase, purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")
    try:
        referral_pool.reverse_units(
            db, purchase, units=payload.units, kind=payload.kind, reason=payload.reason, actor=admin.user_id
        )
    except referral_pool.PoolError as exc:
        db.rollback()
        raise _pool_error(exc)
    db.commit()
    log.info("referral pool: %s reversed %s units of %s (%s)", admin.user_id, payload.units, purchase.id, payload.kind)
    return _purchase_out(purchase)


class PoolReviewIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(min_length=3, max_length=300)


@app.post("/admin/referral-pool/purchases/{purchase_id}/review", tags=["admin"])
def admin_review_purchase(purchase_id: str, payload: PoolReviewIn, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Settle a migrated record that could not be matched to one confirmed $100
    entry. Approved units join at the next round boundary."""
    referral_pool._lock(db)
    purchase = db.get(models.ReferralPoolPurchase, purchase_id, with_for_update=True)
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")
    if purchase.status != "review":
        raise HTTPException(status_code=409, detail="This purchase is not waiting for review")
    current = referral_pool.open_round(db)
    if payload.decision == "approve":
        purchase.status = "credited"
        purchase.active_from_round = (current.number if current else referral_pool._last_round_number(db)) + 1
        delta = purchase.units
    else:
        purchase.status = "rejected"
        delta = 0
    purchase.review_note = f"{payload.decision}: {payload.note}"[:300]
    db.add(models.ReferralPoolUnitEntry(
        pool_id=purchase.pool_id, member_id=purchase.member_id, purchase_id=purchase.id, delta=delta,
        kind="review", reason=purchase.review_note, actor=admin.user_id,
    ))
    db.commit()
    return _purchase_out(purchase)


@app.get("/admin/referral-pool/rounds", tags=["admin"])
def admin_pool_rounds(_: AdminUser, limit: int = 30, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.ReferralPoolRound).order_by(models.ReferralPoolRound.number.desc()).limit(max(1, min(limit, 200)))
    ).all()
    return {
        "items": [
            {
                "id": r.id, "number": r.number, "status": r.status, "members": r.members,
                "units_snapshot": r.units_snapshot, "assigned": r.assigned,
                "remaining": referral_pool.round_remaining(db, r.id), "started_at": r.started_at,
                "closed_at": r.closed_at, "close_reason": r.close_reason,
            }
            for r in rows
        ]
    }


@app.get("/admin/referral-pool/rounds/{round_id}/quotas", tags=["admin"])
def admin_pool_round_quotas(round_id: str, _: AdminUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.ReferralPoolQuota).where(models.ReferralPoolQuota.round_id == round_id)
        .order_by(models.ReferralPoolQuota.units_snapshot.desc())
    ).all()
    users = {u.id: u for u in db.scalars(select(models.User).where(models.User.id.in_([r.member_id for r in rows] or [""]))).all()}
    return {
        "items": [
            {
                "member_id": q.member_id, "handle": users[q.member_id].handle if q.member_id in users else None,
                "units_snapshot": q.units_snapshot, "quota_total": q.quota_total, "assigned": q.assigned,
                "remaining": q.quota_total - q.assigned, "excluded_at": q.excluded_at,
                "excluded_reason": q.excluded_reason,
            }
            for q in rows
        ]
    }


@app.get("/admin/referral-pool/assignments", tags=["admin"])
def admin_pool_assignments(
    _: AdminUser, round_number: int | None = None, limit: int = 100, offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    q = select(models.PoolAssignment)
    if round_number is not None:
        q = q.where(models.PoolAssignment.round_number == round_number)
    rows = db.scalars(
        q.order_by(models.PoolAssignment.created_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))
    ).all()
    ids = {r.sponsor_id for r in rows} | {r.new_member_id for r in rows}
    users = {u.id: u for u in db.scalars(select(models.User).where(models.User.id.in_(ids or {""}))).all()}
    handle = lambda i: users[i].handle if i in users else None  # noqa: E731
    return {
        "items": [
            {
                "new_member_id": r.new_member_id, "new_member_handle": handle(r.new_member_id),
                "sponsor_id": r.sponsor_id, "sponsor_handle": handle(r.sponsor_id),
                "round_number": r.round_number, "quota_total": r.quota_total,
                "quota_remaining_before": r.quota_remaining_before, "eligible_quota_in_draw": r.seats_in_draw,
                "assigned_at": r.created_at,
            }
            for r in rows
        ]
    }


@app.get("/admin/referral-pool/ledger", tags=["admin"])
def admin_pool_ledger(
    _: AdminUser, member_id: str | None = None, kind: str | None = None, limit: int = 100,
    db: OrmSession = Depends(get_db),
):
    """The unit ledger: purchases, migrations, reviews, refunds and reversals."""
    q = select(models.ReferralPoolUnitEntry)
    if member_id:
        q = q.where(models.ReferralPoolUnitEntry.member_id == member_id)
    if kind:
        q = q.where(models.ReferralPoolUnitEntry.kind == kind)
    rows = db.scalars(q.order_by(models.ReferralPoolUnitEntry.id.desc()).limit(max(1, min(limit, 500)))).all()
    return {
        "items": [
            {
                "id": e.id, "member_id": e.member_id, "purchase_id": e.purchase_id, "delta": e.delta,
                "kind": e.kind, "reason": e.reason, "actor": e.actor, "reference": e.reference,
                "created_at": e.created_at,
            }
            for e in rows
        ]
    }


@app.get("/admin/referral-pool/unassigned", tags=["admin"])
def admin_pool_unassigned(_: AdminUser, include_resolved: bool = False, db: OrmSession = Depends(get_db)):
    q = select(models.ReferralPoolUnassigned)
    if not include_resolved:
        q = q.where(models.ReferralPoolUnassigned.resolved_at.is_(None))
    rows = db.scalars(q.order_by(models.ReferralPoolUnassigned.created_at.desc()).limit(200)).all()
    return {
        "items": [
            {
                "new_member_id": r.new_member_id, "reason": r.reason, "attempts": r.attempts,
                "created_at": r.created_at, "last_attempt_at": r.last_attempt_at, "resolved_at": r.resolved_at,
            }
            for r in rows
        ]
    }


@app.post("/admin/referral-pool/unassigned/{member_id}/retry", tags=["admin"])
def admin_pool_retry(member_id: str, admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Try the pool again for a sign-up it could not place. Never for a member
    who has a sponsor: an established relationship is not replaced."""
    user = db.get(models.User, member_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    db.refresh(user, with_for_update=True)
    if user.invited_by:
        raise HTTPException(status_code=409, detail="This member already has a sponsor")
    assignment = referral_pool.allocate(db, member_id)
    if assignment is not None:
        user.invited_by = assignment.sponsor_id
    db.commit()
    log.info("referral pool: %s retried %s -> %s", admin.user_id, member_id, assignment.sponsor_id if assignment else None)
    return {"assigned": assignment is not None, "sponsor_id": assignment.sponsor_id if assignment else None}


@app.get("/admin/fraud/signals", tags=["admin"])
def admin_fraud_signals(_: AdminUser, days: int = 30, db: OrmSession = Depends(get_db)):
    """Signals worth a human look, computed from what Kinjy actually records.

    Signals, not findings: two people in one household share an address, and
    a school can register a class in an afternoon. Each list says what was
    measured so the reviewer can judge it.

    * sponsor_shared_ip - a member and their sponsor signed in from the same
      address: the shape of a self-referral (one person, two accounts).
    * signup_bursts - five or more new accounts first seen from one address in
      24 hours: the shape of a sign-up farm.
    * age_probing - one identity refused at the age gate several times: the
      shape of somebody trying dates until one passes.

    Addresses come from sessions, which record the visitor's forwarded address
    (registration attempts record the gateway's, and are not used for this).
    """
    days = max(1, min(days, 90))
    since = _now() - timedelta(days=days)
    shared = db.execute(
        text(
            f"""
            SELECT u.id AS member_id, u.handle AS member_handle, u.created_at,
                   sp.id AS sponsor_id, sp.handle AS sponsor_handle, s1.ip,
                   EXISTS (SELECT 1 FROM {models.SCHEMA}.pool_assignments pa WHERE pa.new_member_id = u.id) AS via_pool
              FROM {models.SCHEMA}.users u
              JOIN {models.SCHEMA}.users sp ON sp.id = u.invited_by
              JOIN {models.SCHEMA}.sessions s1 ON s1.user_id = u.id AND s1.ip IS NOT NULL
              JOIN {models.SCHEMA}.sessions s2 ON s2.user_id = sp.id AND s2.ip = s1.ip
             WHERE u.created_at >= :since
             GROUP BY u.id, u.handle, u.created_at, sp.id, sp.handle, s1.ip
             ORDER BY u.created_at DESC
             LIMIT 200
            """
        ),
        {"since": since},
    ).mappings().all()
    bursts = db.execute(
        text(
            f"""
            WITH first_seen AS (
                SELECT DISTINCT ON (s.user_id) s.user_id, s.ip, u.created_at
                  FROM {models.SCHEMA}.sessions s JOIN {models.SCHEMA}.users u ON u.id = s.user_id
                 WHERE u.created_at >= :since AND s.ip IS NOT NULL
                 ORDER BY s.user_id, s.created_at
            )
            SELECT ip, date_trunc('day', created_at) AS day, count(*) AS accounts,
                   min(created_at) AS first_at, max(created_at) AS last_at
              FROM first_seen
             GROUP BY ip, date_trunc('day', created_at)
            HAVING count(*) >= 5
             ORDER BY count(*) DESC
             LIMIT 100
            """
        ),
        {"since": since},
    ).mappings().all()
    probing = db.execute(
        text(
            f"""
            SELECT left(subject_hash, 10) AS subject, count(*) AS refusals,
                   max(jurisdiction) AS jurisdiction, max(created_at) AS last_at,
                   bool_or(EXISTS (SELECT 1 FROM {models.SCHEMA}.registration_attempts a2
                                    WHERE a2.subject_hash = a.subject_hash AND a2.outcome = 'allowed')) AS later_allowed
              FROM {models.SCHEMA}.registration_attempts a
             WHERE outcome = 'under_minimum' AND created_at >= :since
             GROUP BY subject_hash
            HAVING count(*) >= 2
             ORDER BY count(*) DESC
             LIMIT 100
            """
        ),
        {"since": since},
    ).mappings().all()
    return {
        "window_days": days,
        "sponsor_shared_ip": [dict(r) for r in shared],
        "signup_bursts": [dict(r) for r in bursts],
        "age_probing": [dict(r) for r in probing],
        "counts": {"sponsor_shared_ip": len(shared), "signup_bursts": len(bursts), "age_probing": len(probing)},
        "note": "Signals for review, not findings about anybody.",
    }


# ---------------------------------------------------------------------------
# Age safety
# ---------------------------------------------------------------------------

@app.get("/internal/age-profile/{user_id}", response_model=schemas.AgeProfileOut, tags=["internal"])
def internal_age_profile(user_id: str, db: OrmSession = Depends(get_db)):
    """The authoritative tier for a member, for every other service.

    A 404 here means the caller must treat the viewer as UNKNOWN, which the
    policy engine treats as a minor. There is no response from this endpoint
    that results in somebody being assumed adult by default.
    """
    profile = db.scalar(
        select(models.UserAgeProfile).where(models.UserAgeProfile.user_id == user_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="No age profile")
    return agegate.profile_payload(profile)


@app.get("/auth/age-status", tags=["age"])
def my_age_status(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What the member themselves may see about their own age record.

    Their tier and whether a review is open - not the internal policy version
    or the assurance plumbing, which would only tell them what to attack.
    """
    profile = db.scalar(
        select(models.UserAgeProfile).where(models.UserAgeProfile.user_id == principal.user_id)
    )
    if profile is None:
        return {"tier": "UNKNOWN", "under_review": False, "can_correct": True}
    payload = agegate.profile_payload(profile)
    return {
        "tier": payload["tier"],
        "under_review": payload["under_review"],
        "can_correct": profile.dob_change_count < 2,
    }


@app.post("/auth/age-correction", tags=["age"])
def correct_date_of_birth(
    payload: schemas.DobCorrectionIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """A legitimate route for somebody who mistyped their birthday.

    A correction that would move the account from a minor tier to adult does
    not take effect on being asked: it opens an age review and the account
    stays where it is until that resolves. Otherwise this endpoint would be
    the bypass that the registration gate is not.
    """
    profile = db.scalar(
        select(models.UserAgeProfile).where(models.UserAgeProfile.user_id == principal.user_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="No age profile")
    if profile.dob_change_count >= 2:
        raise HTTPException(
            status_code=429,
            detail="This date has already been corrected. Contact support to change it again.",
        )

    age_engine = agegate.engine_for(db)
    verdict, new_tier, policy = age_engine.registration_eligibility(
        payload.date_of_birth, agegate._today(), profile.jurisdiction
    )
    current_tier = agegate.effective_tier(profile)

    profile.dob_change_count += 1
    profile.last_dob_change_at = datetime.now(timezone.utc)

    if not verdict.allowed:
        # The correction puts them below the minimum age. Their own statement
        # is evidence, so the account is suspended into review rather than
        # simply refused the edit.
        profile.under_review = True
        profile.review_opened_at = datetime.now(timezone.utc)
        db.add(models.AgeReviewCase(
            id=new_id("arc"), user_id=principal.user_id, source="dob_change",
            detail="Correction places the account below the minimum age.",
        ))
        db.commit()
        return {"status": "under_review", "tier": "AGE_REVIEW_REQUIRED"}

    if age_engine.requires_age_verification(
        current_tier=current_tier, proposed_tier=new_tier, jurisdiction=profile.jurisdiction
    ):
        profile.under_review = True
        profile.review_opened_at = datetime.now(timezone.utc)
        db.add(models.AgeVerification(
            id=new_id("agv"), user_id=principal.user_id, method="pending_assurance",
            threshold_age=policy.adult_age, result="pending", requested_by="age_correction",
        ))
        db.add(models.AgeReviewCase(
            id=new_id("arc"), user_id=principal.user_id, source="dob_change",
            detail=f"Correction would move {current_tier.value} to {new_tier.value}.",
        ))
        db.commit()
        return {"status": "verification_required", "tier": current_tier.value}

    # A correction that does not loosen anything applies immediately.
    profile.date_of_birth = payload.date_of_birth
    profile.tier = new_tier.value
    profile.policy_version = policy.policy_version
    profile.next_transition_on = agegate.next_transition_date(
        payload.date_of_birth, policy, agegate._today()
    )
    db.commit()
    return {"status": "updated", "tier": new_tier.value}


@app.post("/internal/age-review/open", tags=["internal"])
def open_age_review(user_id: str, source: str, detail: str = "", db: OrmSession = Depends(get_db)):
    """Put an account into age review, from any service that finds a signal.

    The account is treated as a minor from this moment, not from whenever a
    reviewer opens the case.
    """
    profile = db.scalar(
        select(models.UserAgeProfile).where(models.UserAgeProfile.user_id == user_id)
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="No age profile")
    profile.under_review = True
    profile.review_opened_at = datetime.now(timezone.utc)
    case = models.AgeReviewCase(
        id=new_id("arc"), user_id=user_id, source=source[:40], detail=detail[:2000]
    )
    db.add(case)
    db.commit()
    return {"case_id": case.id, "tier": "AGE_REVIEW_REQUIRED"}


@app.post("/admin/age/transitions/run", tags=["admin"])
def run_age_transitions(_: AdminUser, db: OrmSession = Depends(get_db)):
    """Move accounts whose birthday has changed their tier.

    Idempotent: an account already in the right tier is recomputed and not
    reported as changed. Safe to run on a schedule and safe to run twice.
    """
    changed = agegate.apply_age_transitions(db)
    db.commit()
    return {"transitioned": len(changed), "changes": changed[:100]}


@app.get("/admin/age/review-queue", tags=["admin"])
def age_review_queue(
    _: ModeratorUser, status: str = "open", limit: int = 50, db: OrmSession = Depends(get_db)
):
    rows = db.scalars(
        select(models.AgeReviewCase)
        .where(models.AgeReviewCase.status == status)
        .order_by(models.AgeReviewCase.created_at)
        .limit(min(limit, 200))
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "user_id": r.user_id,
                "source": r.source,
                "detail": r.detail,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


def _kyc_active(user: models.User) -> bool:
    """Verified, and not past the date the verification runs out."""
    if not user.kyc_verified:
        return False
    until = user.kyc_verified_until
    if until is None:
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


@app.get("/internal/sponsor/{user_id}", response_model=schemas.SponsorOut, tags=["internal"])
def sponsor(user_id: str, db: OrmSession = Depends(get_db)):
    """The one member paid on this member's activity.

    The direct affiliate programme has a single level, so this is a lookup, not
    a walk: there is no chain above the sponsor to resolve. A sponsor who has
    since deleted their account resolves to None rather than to a dead id — the
    commission is then recorded as unclaimed instead of credited to a ghost.
    """
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.invited_by or user.invited_by == user_id:
        return schemas.SponsorOut(user_id=user_id, sponsor_id=None, source="none")

    parent = db.get(models.User, user.invited_by)
    if parent is None or parent.status == "deleted":
        return schemas.SponsorOut(user_id=user_id, sponsor_id=None, source="none")
    # Earning starts with the sponsor's own verification: until it is active
    # the commission is Kinjy's, booked as unclaimed so it stays visible.
    if settings.COMMISSION_REQUIRES_KYC and not _kyc_active(parent):
        return schemas.SponsorOut(user_id=user_id, sponsor_id=None, source="unverified", withheld_from=parent.id)

    assigned = db.scalar(
        select(models.PoolAssignment).where(models.PoolAssignment.new_member_id == user_id)
    )
    return schemas.SponsorOut(
        user_id=user_id,
        sponsor_id=parent.id,
        source="referral_pool" if assigned is not None else "invitation",
    )


@app.get("/internal/users/by-email/{email}", tags=["internal"])
def internal_user_by_email(email: str, db: OrmSession = Depends(get_db)):
    """One account, found by the address it registered with. Internal only.

    Exists so another service can be configured with a person rather than with
    an id: an id is meaningless in a settings file and wrong the moment the
    database is rebuilt, while an address is something an operator can read and
    check. Never gateway-exposed - an endpoint that turns an address into an
    account is an endpoint that confirms who has signed up.
    """
    user = db.scalar(select(models.User).where(func.lower(models.User.email) == email.strip().lower()))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"id": user.id, "handle": user.handle, "display_name": user.display_name}


@app.get("/internal/users/{user_id}", tags=["internal"])
def internal_user(user_id: str, db: OrmSession = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "handle": user.handle,
        "display_name": user.display_name,
        "role": user.role,
        "lang": user.lang,
        "country": user.country,
        "kyc_verified": user.kyc_verified,
        "status": user.status,
        "invited_by": user.invited_by,
    }


@app.get("/internal/staff", tags=["internal"])
def internal_staff(min_role: str = "moderator", db: OrmSession = Depends(get_db)):
    """Who to tell when something waits for the staff - a payout wallet to approve."""
    roles = {"moderator": STAFF_ROLES, "admin": ("admin", "superadmin"), "superadmin": ("superadmin",)}
    rows = db.scalars(
        select(models.User).where(models.User.role.in_(roles.get(min_role, STAFF_ROLES)), models.User.status == "active")
    ).all()
    return {"items": [{"user_id": u.id, "role": u.role} for u in rows]}


@app.get("/internal/users/{user_id}/identity", tags=["internal"])
def internal_identity(user_id: str, db: OrmSession = Depends(get_db)):
    """What payment-service needs to write to a member and to check who they are.

    Kept apart from /internal/users/{id}, which a dozen services read: the
    e-mail address and the date of birth go only to the one that needs them.
    """
    user = _active_user(db, user_id)
    profile = db.scalar(select(models.UserAgeProfile).where(models.UserAgeProfile.user_id == user_id))
    return {
        "id": user.id,
        "email": user.email,
        "email_verified": user.email_verified,
        "display_name": user.display_name,
        "country": user.country,
        "lang": user.lang,
        "kyc_verified": user.kyc_verified,
        "date_of_birth": profile.date_of_birth.isoformat() if profile else None,
    }


@app.patch("/internal/users/{user_id}", tags=["internal"])
def sync_identity(user_id: str, payload: schemas.IdentitySyncIn, db: OrmSession = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.lang is not None and payload.lang not in settings.SUPPORTED_LANGS:
        raise HTTPException(status_code=422, detail="Unsupported language")
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.lang is not None:
        user.lang = payload.lang
    db.commit()
    return {"id": user.id, "display_name": user.display_name, "lang": user.lang}


@app.get("/internal/referral-counts", tags=["internal"])
def referral_counts(db: OrmSession = Depends(get_db)):
    """Verified direct referral counts per member.

    Kinjy Leaders ranks on commission earned, not on head count, so this is a
    reporting and fraud-review figure — not a payout input. Only KYC-verified
    referrals are counted.
    """
    rows = db.execute(
        select(models.User.invited_by, func.count(models.User.id))
        .where(
            models.User.invited_by.is_not(None),
            models.User.kyc_verified.is_(True),
            models.User.status == "active",
        )
        .group_by(models.User.invited_by)
    ).all()
    return {"referrals": {uid: count for uid, count in rows}}


@app.post("/internal/users/{user_id}/kyc", tags=["internal"])
def set_kyc(user_id: str, verified: bool, until: datetime | None = None, db: OrmSession = Depends(get_db)):
    """Called by payment-service once KinjyKYC returns a result."""
    user = _active_user(db, user_id)
    user.kyc_verified = verified
    user.kyc_verified_until = until
    db.commit()
    _push_badge(user_id, verified)
    return {"user_id": user_id, "kyc_verified": verified, "until": until}


def _push_badge(user_id: str, verified: bool) -> bool:
    """Put the verified badge on (or off) the public profile. Best effort, logged."""
    try:
        httpx.post(
            f"http://user-service:8000/internal/profiles/{user_id}/verified",
            json={"verified": verified}, timeout=5,
        ).raise_for_status()
        return True
    except Exception as exc:
        log.error("could not update the verified badge of %s: %s", user_id, exc)
        return False


@app.post("/internal/kyc/resync-badges", tags=["internal"])
def resync_badges(db: OrmSession = Depends(get_db)):
    """Badges for everybody already verified - those who verified before the
    profile followed the identity check. Safe to run again."""
    rows = db.scalars(select(models.User).where(models.User.kyc_verified.is_(True))).all()
    done = sum(1 for u in rows if _push_badge(u.id, True))
    return {"verified": len(rows), "badges_set": done}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

# --- the team ----------------------------------------------------------------
#
# Who may use the console, and how far. Only a superadmin changes it. The role
# travels in the access token and is re-read from here at every refresh, so a
# change reaches the person within one token lifetime without signing out.

STAFF_ROLES = ("moderator", "admin", "superadmin")


class TeamMemberIn(BaseModel):
    # An email address or a @handle, as the superadmin knows the person.
    who: str = Field(min_length=2, max_length=320)
    role: str = Field(pattern="^(moderator|admin|superadmin)$")


class TeamRoleIn(BaseModel):
    role: str = Field(pattern="^(moderator|admin|superadmin)$")


def _team_out(user: models.User) -> dict:
    return {
        "user_id": user.id,
        "email": user.email,
        "handle": user.handle,
        "display_name": user.display_name,
        "role": user.role,
    }


def _superadmins(db: OrmSession) -> int:
    return db.scalar(select(func.count()).select_from(models.User).where(models.User.role == "superadmin")) or 0


@app.get("/admin/team", tags=["admin"])
def team(principal: ModeratorUser, db: OrmSession = Depends(get_db)):
    """Who is on the staff. Any staff member may see it; only a superadmin changes it."""
    rows = db.scalars(
        select(models.User).where(models.User.role.in_(STAFF_ROLES)).order_by(models.User.role, models.User.handle)
    ).all()
    return {"items": [_team_out(u) for u in rows], "can_manage": principal.is_superadmin}


@app.post("/admin/team", status_code=201, tags=["admin"])
def add_to_team(payload: TeamMemberIn, principal: SuperAdminUser, db: OrmSession = Depends(get_db)):
    """Give somebody a staff role, found by email or @handle."""
    who = payload.who.strip().lstrip("@").lower()
    user = db.scalar(
        select(models.User).where(or_(func.lower(models.User.email) == who, func.lower(models.User.handle) == who))
    )
    if user is None or user.status != "active":
        raise HTTPException(status_code=404, detail="No active account with that email or username")
    if user.id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    if user.role == "superadmin" and payload.role != "superadmin" and _superadmins(db) <= 1:
        raise HTTPException(status_code=400, detail="Kinjy must keep at least one superadmin")
    user.role = payload.role
    db.commit()
    log.info("team: %s set %s to %s", principal.user_id, user.id, payload.role)
    return _team_out(user)


@app.patch("/admin/team/{user_id}", tags=["admin"])
def change_team_role(user_id: str, payload: TeamRoleIn, principal: SuperAdminUser, db: OrmSession = Depends(get_db)):
    user = db.get(models.User, user_id)
    if user is None or user.role not in STAFF_ROLES:
        raise HTTPException(status_code=404, detail="Not on the team")
    if user.id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    if user.role == "superadmin" and payload.role != "superadmin" and _superadmins(db) <= 1:
        raise HTTPException(status_code=400, detail="Kinjy must keep at least one superadmin")
    user.role = payload.role
    db.commit()
    log.info("team: %s changed %s to %s", principal.user_id, user.id, payload.role)
    return _team_out(user)


@app.delete("/admin/team/{user_id}", tags=["admin"])
def remove_from_team(user_id: str, principal: SuperAdminUser, db: OrmSession = Depends(get_db)):
    """Take somebody off the staff: they are an ordinary member again."""
    user = db.get(models.User, user_id)
    if user is None or user.role not in STAFF_ROLES:
        raise HTTPException(status_code=404, detail="Not on the team")
    if user.id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot remove yourself")
    if user.role == "superadmin" and _superadmins(db) <= 1:
        raise HTTPException(status_code=400, detail="Kinjy must keep at least one superadmin")
    user.role = "member"
    db.commit()
    log.info("team: %s removed %s", principal.user_id, user.id)
    return {"user_id": user.id, "role": "member"}


class IdsIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)


@app.post("/admin/users/lookup", tags=["admin"])
def admin_users_lookup(payload: IdsIn, _: AdminUser, db: OrmSession = Depends(get_db)):
    """Names for the account ids admin screens show: the journal, KYC, the
    Leaders ranking, payouts. Ids that match no account are simply absent."""
    ids = list({i for i in payload.ids if i})[:500]
    if not ids:
        return {"users": {}}
    rows = db.scalars(select(models.User).where(models.User.id.in_(ids))).all()
    return {"users": {
        u.id: {"display_name": u.display_name, "handle": u.handle, "email": u.email, "status": u.status}
        for u in rows
    }}


@app.get("/admin/users", tags=["admin"])
def admin_users(
    _: AdminUser,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    stmt = select(models.User).order_by(models.User.created_at.desc())
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(models.User.email).like(like),
                func.lower(models.User.handle).like(like),
                func.lower(models.User.display_name).like(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.limit(min(limit, 200)).offset(offset)).all()
    return {"total": total, "items": [schemas.UserOut.model_validate(r) for r in rows]}


app.include_router(attribution.router)
