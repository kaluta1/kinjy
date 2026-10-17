"""Kinjy · memorial-service — the Digital Graveyard."""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import date, datetime, timedelta, timezone

import httpx
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session as OrmSession

from common import ageclient, classifier, events, mediasign, notify, permissions, settings
from common.agesafety import engine
from common.auth import AdminUser, ModeratorUser, CurrentUser, MaybeUser
from common.database import SessionLocal, get_db
from common.ids import new_id
from common.service import create_app

import models

log = logging.getLogger("memorial-service")

USER_URL = "http://user-service:8000"
MEDIA_URL = "http://media-service:8000"

# Columns added after the first deploy; create_all only creates missing tables.
MIGRATIONS = [
    f"ALTER TABLE {models.SCHEMA}.reminders ADD COLUMN IF NOT EXISTS offset_hours INTEGER",
    f"ALTER TABLE {models.SCHEMA}.memorials ADD COLUMN IF NOT EXISTS grave_accuracy_m DOUBLE PRECISION",
]

# A grave location is shown as "captured at the grave" only if the device said it
# was this close. A phone in the open reads a few metres; a desktop browser
# answers the same question from its network address, kilometres from anywhere.
VERIFIED_ACCURACY_M = 50

# How often due anniversary reminders are looked for. The smallest offset is six
# hours, so five minutes late is invisible to anyone receiving one.
REMINDER_SWEEP_SECONDS = 300

_background: set[asyncio.Task] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

FAITH_STYLE = "^[a-z_]{2,40}$"
# A media id is put into a URL on another service, so it must not be able to be
# a path; the columns that store ids are 40 characters wide.
MEDIA_ID = r"^[A-Za-z0-9_-]{1,64}$"
TRIBUTE_KINDS = ("message", "flower", "candle", "photo")

# The gallery. A page families fill from their phones and strangers read from a
# grave: bounded so one memorial cannot take the disk, and so a gallery page is a
# page and not a download. A video's ticket lasts longer than a picture's because
# it is played, paused and rewound over minutes; five would cut it off.
GALLERY_MAX_ITEMS = 60
GALLERY_MAX_BYTES = 500 * 1024 * 1024
VIDEO_TICKET_SECONDS = 3600
GALLERY_PROVENANCE = ("original", "edited", "ai_assisted", "ai_generated", "verified_source")


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if len(value) < 2:
        raise ValueError("A memorial needs the person's name")
    return value


class MemorialIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    person_id: str | None = Field(default=None, max_length=40)
    birth_date: date | None = None
    death_date: date | None = None
    biography: str | None = Field(default=None, max_length=20000)
    # "family" is accepted and treated as private until the family tree, which
    # would say who the family is, is open again. Nobody is ever let in by guess.
    visibility: str = Field(default="public", pattern="^(public|family|private)$")
    # Faith styling must carry its source; an unsourced value is refused.
    faith_style: str = Field(default="none", pattern=FAITH_STYLE)
    faith_style_source: str | None = Field(default=None, pattern="^(documented_wish|admin_choice)$")

    @field_validator("full_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _clean_name(value)


class MemorialPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    birth_date: date | None = None
    death_date: date | None = None
    biography: str | None = Field(default=None, max_length=20000)
    visibility: str | None = Field(default=None, pattern="^(public|family|private)$")
    moderation: str | None = Field(default=None, pattern="^(open|pending_approval)$")
    faith_style: str | None = Field(default=None, pattern=FAITH_STYLE)
    faith_style_source: str | None = Field(default=None, pattern="^(documented_wish|admin_choice)$")
    # Files the administrator uploaded to media-service. An explicit null
    # removes the current one; leaving the key out keeps it.
    photo_media_id: str | None = Field(default=None, pattern=MEDIA_ID)
    cover_media_id: str | None = Field(default=None, pattern=MEDIA_ID)
    audio_media_id: str | None = Field(default=None, pattern=MEDIA_ID)
    audio_autoplay: bool | None = None

    @field_validator("full_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return _clean_name(value)


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    label: str | None = Field(default=None, max_length=255)
    captured_on_site: bool = False
    # How close the device says it was, in metres. Without it a claim of having
    # been on site cannot be told from a guess, and is not believed.
    accuracy_m: float | None = Field(default=None, ge=0, le=100000)


class TributeIn(BaseModel):
    kind: str = Field(pattern="^(message|flower|candle|photo)$")
    # Only read from a signed-out visitor: a member always signs with their own
    # name, so nobody can leave a message as somebody else.
    author_name: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, max_length=4000)
    media_id: str | None = Field(default=None, pattern=MEDIA_ID)


class EventIn(BaseModel):
    year: int = Field(ge=1800, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    day: int | None = Field(default=None, ge=1, le=31)
    title: str = Field(min_length=1, max_length=200)
    body: str | None = Field(default=None, max_length=4000)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A moment needs a title")
        return value


class DeathReportIn(BaseModel):
    evidence: str = Field(min_length=10, max_length=4000)
    document_media_id: str | None = Field(default=None, pattern=MEDIA_ID)


# ---------------------------------------------------------------------------
# Who may see and who may manage
# ---------------------------------------------------------------------------
#
# A public memorial is open to anyone, signed in or not: it exists to be
# visited, and a QR code on a headstone is scanned by people with no account.
# A private one exists only for its administrators — to everyone else it is a
# 404, so its existence is not confirmed either.

def _admin_ids(db: OrmSession, memorial_id: str) -> list[str]:
    return list(
        db.scalars(
            select(models.MemorialAdmin.user_id)
            .where(models.MemorialAdmin.memorial_id == memorial_id)
            .order_by(models.MemorialAdmin.succession_order)
        ).all()
    )


def _admin_row(db: OrmSession, memorial_id: str, user_id: str | None) -> models.MemorialAdmin | None:
    if not user_id:
        return None
    return db.scalar(
        select(models.MemorialAdmin).where(
            models.MemorialAdmin.memorial_id == memorial_id,
            models.MemorialAdmin.user_id == user_id,
        )
    )


def _is_admin_of(db: OrmSession, memorial_id: str, user_id: str | None) -> bool:
    return _admin_row(db, memorial_id, user_id) is not None


def _can_view(db: OrmSession, memorial: models.Memorial, viewer: str | None) -> bool:
    return memorial.visibility == "public" or _is_admin_of(db, memorial.id, viewer)


def _viewable(db: OrmSession, memorial_id: str, viewer: str | None) -> models.Memorial:
    memorial = db.get(models.Memorial, memorial_id)
    if memorial is None or not _can_view(db, memorial, viewer):
        raise HTTPException(status_code=404, detail="Memorial not found")
    return memorial


def _managed(db: OrmSession, memorial_id: str, user_id: str, action: str) -> models.Memorial:
    """The memorial, if this member administers it. 404 when they cannot even
    see it, 403 when they can see it but it is not theirs to change."""
    memorial = _viewable(db, memorial_id, user_id)
    if not _is_admin_of(db, memorial_id, user_id):
        raise HTTPException(status_code=403, detail=f"Only a memorial administrator can {action}")
    return memorial


# ---------------------------------------------------------------------------
# Media and text
# ---------------------------------------------------------------------------

def _asset_id(url: str | None) -> str | None:
    base = settings.MEDIA_PUBLIC_BASE.rstrip("/")
    if url and url.startswith(base + "/"):
        return url[len(base) + 1:].split("?")[0] or None
    return None


def _signed(url: str | None, viewer: str | None, ttl: int | None = None) -> str | None:
    """A viewing ticket for this viewer — minted only after the memorial has been
    found viewable for them, like every other media ticket on the platform."""
    asset = _asset_id(url)
    if not (url and asset):
        return url
    return mediasign.sign_url(url, asset, viewer, ttl=ttl or mediasign.DEFAULT_TTL_SECONDS)


def _own_media_info(media_id: str, owner_id: str, kinds: tuple[str, ...]) -> dict:
    """What media-service says about a file this member uploaded, of an accepted
    kind — after it has been made to need a ticket.

    Someone else's asset id is refused with the same words as a missing one:
    the difference would tell a prober which ids exist. Once attached, the file
    needs a ticket to be fetched, so its visibility follows the memorial's.
    """
    try:
        response = httpx.get(f"{MEDIA_URL}/internal/media/{media_id}", timeout=4)
    except Exception as exc:
        log.warning("media lookup failed for %s: %s", media_id, exc)
        raise HTTPException(status_code=503, detail="Could not check that file right now. Try again.")
    info = response.json() if response.status_code == 200 else None
    if not info or info.get("owner_id") != owner_id:
        raise HTTPException(status_code=400, detail="That file was not found among your uploads.")
    if info.get("kind") not in kinds:
        raise HTTPException(status_code=400, detail=f"That file must be: {', '.join(kinds)}.")
    # Fail closed. A portrait that could not be restricted would stay reachable
    # by its bare URL — on a private memorial, to anyone who guessed it.
    try:
        httpx.post(f"{MEDIA_URL}/internal/media/{media_id}/restrict", timeout=4).raise_for_status()
    except Exception as exc:
        log.error("could not restrict media %s: %s", media_id, exc)
        raise HTTPException(status_code=503, detail="Could not secure that file right now. Try again.")
    return info


def _own_media(media_id: str, owner_id: str, kinds: tuple[str, ...]) -> str:
    """The stored URL of a file this member uploaded, of an accepted kind."""
    return _own_media_info(media_id, owner_id, kinds)["url"]


def _asset_info(media_id: str) -> dict | None:
    """What media-service says about any asset, for attaching a file that is
    already on the memorial (a tribute photo) — no ownership claim is made."""
    try:
        response = httpx.get(f"{MEDIA_URL}/internal/media/{media_id}", timeout=4)
    except Exception as exc:
        log.warning("media lookup failed for %s: %s", media_id, exc)
        raise HTTPException(status_code=503, detail="Could not check that file right now. Try again.")
    return response.json() if response.status_code == 200 else None


def _screen(text: str | None, author_id: str | None, media_kinds: list[str] | None = None) -> None:
    """Refuse what the platform refuses everywhere else, before it is stored.

    Held-for-approval is not enough on its own: a pending tribute is still read
    by the family, and a grieving family is exactly who must not find abuse in
    their queue.
    """
    if not text and not media_kinds:
        return
    verdict = classifier.classify(
        body=text or "",
        media_kinds=media_kinds or [],
        author_is_minor=ageclient.age_profile(author_id).is_minor,
    )
    if verdict.block_publication:
        raise HTTPException(
            status_code=403,
            detail="This cannot be published. If you believe this is a mistake, contact support.",
        )


def _may_name_admin(sender_id: str, target_id: str) -> None:
    """Naming someone an administrator tells them so, in words the namer chose
    (the memorial's name), and puts a role and a stream of reminders on them.
    That is unsolicited contact, so it follows the rules a message does: the
    member's own privacy setting and blocks, and — the channel AGE-SAFETY.md §19
    closes everywhere else — no adult reaches a minor they are not connected to.

    The member is told what they can act on — connect first — except where
    saying more would hand over what they should not have: a block, or the
    age rule, get one plain sentence that names neither. A failed lookup
    refuses; it never lets anyone through.
    """
    data = permissions.get(sender_id, target_id)
    if data is None:
        raise HTTPException(
            status_code=503, detail="Could not verify permission to name them an administrator. Try again shortly."
        )
    refusal = HTTPException(status_code=403, detail="You cannot name this member an administrator.")
    if data.get("reason") == "blocked":
        raise refusal
    if data.get("pending"):
        raise HTTPException(
            status_code=403,
            detail="Your connection invitation is still pending, so you cannot name them an administrator yet.",
        )
    if not data.get("can_message"):
        raise HTTPException(
            status_code=403,
            detail="This member only accepts this from people they are connected to. Send a connection invitation first.",
        )
    verdict = engine.can_message_user(
        ageclient.age_profile(sender_id),
        ageclient.age_profile(target_id),
        connected=bool(data.get("connected")),
    )
    if not verdict.allowed:
        raise refusal


def _profiles(ids: set[str]) -> dict[str, dict]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    try:
        response = httpx.post(f"{USER_URL}/internal/profiles", json={"ids": sorted(ids)}, timeout=5)
        response.raise_for_status()
        return response.json().get("profiles", {})
    except Exception as exc:
        log.warning("could not resolve profiles: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Dates and anniversary reminders
# ---------------------------------------------------------------------------

def _check_dates(birth: date | None, death: date | None) -> None:
    today = date.today()
    if birth and birth > today:
        raise HTTPException(status_code=400, detail="The date of birth is in the future.")
    if death and death > today:
        raise HTTPException(status_code=400, detail="The date of death is in the future.")
    if birth and death and death < birth:
        raise HTTPException(status_code=400, detail="The date of death is before the date of birth.")


def _anniversary(death: date, year: int) -> datetime:
    """That year's anniversary, at midnight UTC. A death on 29 February is
    remembered on the 28th in other years — `replace(year=…)` used to raise
    there, and the memorial could not be created at all."""
    try:
        day = date(year, death.month, death.day)
    except ValueError:
        day = date(year, 2, 28)
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _schedule(db: OrmSession, memorial: models.Memorial, user_id: str, after: datetime | None = None) -> None:
    """Reminders for the next anniversary that still has one ahead.

    Only reminders still in the future are written: one created three days
    before an anniversary must not announce "in 10 days". If none of this
    year's are left, the next year's are, so the series never stops.
    """
    if not memorial.death_date:
        return
    now = after or _now()
    year = now.year
    for _ in range(3):
        anniversary = _anniversary(memorial.death_date, year)
        ahead = [
            hours for hours in models.Reminder.OFFSETS_HOURS
            if anniversary - timedelta(hours=hours) > now
        ]
        if anniversary > now and ahead:
            for hours in ahead:
                db.add(models.Reminder(
                    memorial_id=memorial.id,
                    user_id=user_id,
                    due_at=anniversary - timedelta(hours=hours),
                    offset_hours=hours,
                ))
            return
        year += 1


def _reschedule(db: OrmSession, memorial: models.Memorial) -> None:
    """Rewrite the pending reminders — after the date of death changes, or the
    list of administrators who receive them does."""
    db.execute(
        delete(models.Reminder).where(
            models.Reminder.memorial_id == memorial.id, models.Reminder.sent_at.is_(None)
        )
    )
    for user_id in _admin_ids(db, memorial.id):
        _schedule(db, memorial, user_id)


def _reminder_text(memorial: models.Memorial, hours: int, anniversary: datetime) -> tuple[str, str]:
    when = {240: "In 10 days", 72: "In 3 days", 6: "Very soon"}.get(hours, "Soon")
    years = anniversary.year - memorial.death_date.year
    title = f"{when}: the anniversary of {memorial.full_name}"
    body = f"{years} year{'s' if years != 1 else ''} since {memorial.death_date.strftime('%d %B %Y')}."
    return title, body


def sweep_reminders(now: datetime | None = None) -> int:
    """Send every reminder that has come due. Returns how many were sent.

    Safe with several workers: rows are claimed with FOR UPDATE SKIP LOCKED, so
    two processes never send the same one. Reminders whose anniversary has
    already passed — the service was down — are marked sent without notifying:
    a message announcing an anniversary that is over is worse than none.
    """
    now = now or _now()
    outbox: list[tuple[str, str, str, str]] = []
    with SessionLocal() as db:
        rows = db.scalars(
            select(models.Reminder)
            .where(models.Reminder.sent_at.is_(None), models.Reminder.due_at <= now)
            .order_by(models.Reminder.due_at)
            .limit(200)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.sent_at = now
            memorial = db.get(models.Memorial, row.memorial_id)
            if memorial is None or not memorial.death_date or not _is_admin_of(db, memorial.id, row.user_id):
                continue
            hours = row.offset_hours or 0
            anniversary = row.due_at + timedelta(hours=hours)
            if anniversary > now:
                title, body = _reminder_text(memorial, hours, anniversary)
                outbox.append((row.user_id, title, body, f"/graveyard?open={memorial.id}"))
            # The last of this anniversary's reminders schedules next year's.
            # The session does not autoflush, so the rows already marked sent in
            # this loop must be written before they are counted: otherwise a
            # sweep that finds several due at once — after an outage — sees the
            # others as still pending, schedules nothing, and the series ends.
            db.flush()
            remaining = db.scalar(
                select(func.count()).select_from(models.Reminder).where(
                    models.Reminder.memorial_id == memorial.id,
                    models.Reminder.user_id == row.user_id,
                    models.Reminder.sent_at.is_(None),
                    models.Reminder.id != row.id,
                )
            ) or 0
            if not remaining:
                _schedule(db, memorial, row.user_id, after=anniversary)
        db.commit()
    # Sent after the commit: a notification for a row that then failed to save
    # would be sent again on the next sweep.
    for user_id, title, body, link in outbox:
        notify.notify(user_id, kind="memorial_reminder", title=title, body=body, link=link)
    return len(outbox)


async def _reminder_loop() -> None:
    while True:
        try:
            sent = await asyncio.to_thread(sweep_reminders)
            if sent:
                log.info("sent %d anniversary reminder(s)", sent)
        except Exception:
            log.exception("reminder sweep failed")
        await asyncio.sleep(REMINDER_SWEEP_SECONDS)


async def _start_reminders() -> None:
    task = asyncio.get_running_loop().create_task(_reminder_loop())
    _background.add(task)
    task.add_done_callback(_background.discard)


app = create_app(
    name="memorial-service",
    schema=models.SCHEMA,
    description="Memorials, tributes, candles and flowers, QR codes, anniversary reminders.",
    on_startup=[_start_reminders],
    migrations=MIGRATIONS,
)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _memorial_out(db: OrmSession, memorial: models.Memorial, viewer: str | None) -> dict:
    admin = _admin_row(db, memorial.id, viewer)
    is_admin = admin is not None
    counts = dict(
        db.execute(
            select(models.Tribute.kind, func.count())
            .where(models.Tribute.memorial_id == memorial.id, models.Tribute.status == "approved")
            .group_by(models.Tribute.kind)
        ).all()
    )
    out = {
        "id": memorial.id,
        "full_name": memorial.full_name,
        "birth_date": memorial.birth_date,
        "death_date": memorial.death_date,
        "biography": memorial.biography,
        "photo_url": _signed(memorial.photo_url, viewer),
        "cover_url": _signed(memorial.cover_url, viewer),
        "faith_style": memorial.faith_style,
        "faith_style_source": memorial.faith_style_source,
        "grave": {
            "lat": memorial.grave_lat,
            "lng": memorial.grave_lng,
            "label": memorial.grave_label,
            "verified": memorial.location_verified,
            "accuracy_m": memorial.grave_accuracy_m,
        },
        "audio": {"url": _signed(memorial.memorial_audio_url, viewer), "autoplay": memorial.audio_autoplay},
        "qr_code": memorial.qr_code,
        "qr_url": f"{settings.FRONTEND_URL}/memorial/{memorial.qr_code}",
        "visibility": "public" if memorial.visibility == "public" else "private",
        "moderation": memorial.moderation,
        "death_status": memorial.death_status,
        "verified_at": memorial.verified_at,
        "tribute_counts": counts,
        "is_admin": is_admin,
        "created_at": memorial.created_at,
    }
    if admin is not None:
        # Where they stand in the succession decides what they may do to the
        # others, and whether the delete button is theirs.
        out["admin_rank"] = admin.succession_order
        out["pending_tributes"] = db.scalar(
            select(func.count()).select_from(models.Tribute).where(
                models.Tribute.memorial_id == memorial.id, models.Tribute.status == "pending"
            )
        ) or 0
    return out


def _tribute_out(row: models.Tribute, viewer: str | None, *, with_status: bool = False) -> dict:
    out = {
        "id": row.id,
        "kind": row.kind,
        "author_name": row.author_name,
        "body": row.body,
        "media_url": _signed(row.media_url, viewer),
        "paid": row.paid,
        "created_at": row.created_at,
    }
    if with_status:
        out["status"] = row.status
    return out


# ---------------------------------------------------------------------------
# Memorials
# ---------------------------------------------------------------------------

@app.post("/memorials", status_code=201, tags=["memorials"])
async def create_memorial(payload: MemorialIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if payload.faith_style != "none" and not payload.faith_style_source:
        raise HTTPException(
            status_code=400,
            detail="A faith style must state its source (documented_wish or admin_choice). It is never inferred.",
        )
    _check_dates(payload.birth_date, payload.death_date)
    # The name is public and searchable, so it is read like the life story is.
    _screen(f"{payload.full_name}\n{payload.biography or ''}", principal.user_id)

    memorial = models.Memorial(
        id=new_id("mem"),
        created_by=principal.user_id,
        qr_code=secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12],
        **{**payload.model_dump(), "biography": (payload.biography or "").strip() or None},
    )
    db.add(memorial)
    db.flush()
    db.add(models.MemorialAdmin(memorial_id=memorial.id, user_id=principal.user_id, succession_order=1))
    _schedule(db, memorial, principal.user_id)
    db.commit()
    db.refresh(memorial)
    await events.publish("memorial.created", {"memorial_id": memorial.id, "by": principal.user_id})
    return _memorial_out(db, memorial, principal.user_id)


@app.get("/memorials", tags=["memorials"])
def list_memorials(
    q: str | None = None,
    mine: bool = False,
    principal: MaybeUser = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: OrmSession = Depends(get_db),
):
    """Browse the graveyard — public memorials only.

    ``mine`` lists the memorials you administer instead, private ones included.
    Ordered by date of death, most recent first, because that is how people
    look for someone they have just lost.
    """
    viewer = principal.user_id if principal else None
    if mine:
        if viewer is None:
            raise HTTPException(status_code=401, detail="Sign in to see the memorials you look after")
        administered = select(models.MemorialAdmin.memorial_id).where(models.MemorialAdmin.user_id == viewer)
        stmt = select(models.Memorial).where(models.Memorial.id.in_(administered))
    else:
        stmt = select(models.Memorial).where(models.Memorial.visibility == "public")
    if q and len(q.strip()) >= 2:
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(models.Memorial.full_name.ilike(f"%{term}%", escape="\\"))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Memorial.death_date.desc().nullslast(), models.Memorial.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"total": total, "items": [_memorial_out(db, m, viewer) for m in rows]}


# Declared before /memorials/{id}: FastAPI matches in order, so a literal path
# must come first or it is read as an id.
@app.get("/memorials/qr/{qr_code}", tags=["memorials"])
def get_by_qr(qr_code: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """The code engraved on a headstone resolves here."""
    viewer = principal.user_id if principal else None
    memorial = db.scalar(select(models.Memorial).where(models.Memorial.qr_code == qr_code))
    if memorial is None or not _can_view(db, memorial, viewer):
        raise HTTPException(status_code=404, detail="Unknown memorial code")
    return _memorial_out(db, memorial, viewer)


@app.get("/memorials/{memorial_id}", tags=["memorials"])
def get_memorial(memorial_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    viewer = principal.user_id if principal else None
    return _memorial_out(db, _viewable(db, memorial_id, viewer), viewer)


@app.patch("/memorials/{memorial_id}", tags=["memorials"])
def update_memorial(memorial_id: str, payload: MemorialPatch, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    memorial = _managed(db, memorial_id, principal.user_id, "edit it")
    fields = payload.model_fields_set

    birth = payload.birth_date if "birth_date" in fields else memorial.birth_date
    death = payload.death_date if "death_date" in fields else memorial.death_date
    _check_dates(birth, death)

    style = payload.faith_style if "faith_style" in fields and payload.faith_style else memorial.faith_style
    source = payload.faith_style_source if "faith_style_source" in fields else memorial.faith_style_source
    if style != "none" and not source:
        raise HTTPException(
            status_code=400,
            detail="A faith style must state its source (documented_wish or admin_choice). It is never inferred.",
        )

    if "biography" in fields:
        _screen(payload.biography, principal.user_id)
        memorial.biography = (payload.biography or "").strip() or None
    if payload.full_name is not None:
        _screen(payload.full_name, principal.user_id)
        memorial.full_name = payload.full_name
    if payload.visibility is not None:
        memorial.visibility = payload.visibility
    if payload.moderation is not None:
        memorial.moderation = payload.moderation
    if payload.audio_autoplay is not None:
        memorial.audio_autoplay = payload.audio_autoplay
    memorial.faith_style = style
    memorial.faith_style_source = source if style != "none" else None

    for key, column, kinds in (
        ("photo_media_id", "photo_url", ("image",)),
        ("cover_media_id", "cover_url", ("image",)),
        ("audio_media_id", "memorial_audio_url", ("audio",)),
    ):
        if key in fields:
            media_id = getattr(payload, key)
            setattr(memorial, column, _own_media(media_id, principal.user_id, kinds) if media_id else None)

    dates_changed = death != memorial.death_date
    memorial.birth_date = birth
    memorial.death_date = death
    if dates_changed:
        _reschedule(db, memorial)
    db.commit()
    db.refresh(memorial)
    return _memorial_out(db, memorial, principal.user_id)


@app.delete("/memorials/{memorial_id}", status_code=204, tags=["memorials"])
def delete_memorial(memorial_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Only the first administrator in the succession may delete — the others
    can step down, but not take the memorial away from the family."""
    _managed(db, memorial_id, principal.user_id, "delete it")
    if _admin_ids(db, memorial_id)[0] != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the first administrator can delete a memorial")
    # The gallery's own files go with it; a tribute's photo stays the tribute's.
    own_files = [(r.media_url, r.added_by) for r in _gallery_rows(db, memorial_id) if r.source_tribute_id is None]
    for model in (
        models.Tribute, models.Reminder, models.DeathReport, models.MemorialEvent,
        models.MemorialMedia, models.MemorialAdmin,
    ):
        db.execute(delete(model).where(model.memorial_id == memorial_id))
    db.execute(delete(models.Memorial).where(models.Memorial.id == memorial_id))
    db.commit()
    for url, owner in own_files:
        if not _still_referenced(db, url):
            _release_file(url, owner)


# ---------------------------------------------------------------------------
# Grave location
# ---------------------------------------------------------------------------

@app.post("/memorials/{memorial_id}/location", tags=["memorials"])
def set_location(memorial_id: str, payload: LocationIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Record grave coordinates.

    ``verified`` is only granted when the capture happened on site *and* the
    device was precise enough to mean it — the blueprint is explicit that a
    grave location must never be fabricated, and neither an address typed from
    memory nor a browser's network guess from a desk is a verified location.
    """
    memorial = _managed(db, memorial_id, principal.user_id, "set the location")
    label = (payload.label or "").strip() or None
    _screen(label, principal.user_id)
    memorial.grave_lat = payload.lat
    memorial.grave_lng = payload.lng
    memorial.grave_label = label
    memorial.grave_accuracy_m = payload.accuracy_m if payload.captured_on_site else None
    memorial.location_verified = (
        payload.captured_on_site and payload.accuracy_m is not None and payload.accuracy_m <= VERIFIED_ACCURACY_M
    )
    db.commit()
    return {
        "verified": memorial.location_verified,
        "lat": payload.lat,
        "lng": payload.lng,
        "label": memorial.grave_label,
        "accuracy_m": memorial.grave_accuracy_m,
    }


@app.delete("/memorials/{memorial_id}/location", status_code=204, tags=["memorials"])
def clear_location(memorial_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    memorial = _managed(db, memorial_id, principal.user_id, "clear the location")
    memorial.grave_lat = memorial.grave_lng = None
    memorial.grave_label = None
    memorial.grave_accuracy_m = None
    memorial.location_verified = False
    db.commit()


# ---------------------------------------------------------------------------
# Administrators
# ---------------------------------------------------------------------------

@app.get("/memorials/{memorial_id}/admins", tags=["memorials"])
def list_admins(memorial_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _managed(db, memorial_id, principal.user_id, "see its administrators")
    rows = db.scalars(
        select(models.MemorialAdmin)
        .where(models.MemorialAdmin.memorial_id == memorial_id)
        .order_by(models.MemorialAdmin.succession_order)
    ).all()
    profiles = _profiles({r.user_id for r in rows})
    return {
        "max": models.MemorialAdmin.MAX_ADMINS,
        "items": [
            {
                "user_id": r.user_id,
                "succession_order": r.succession_order,
                "handle": profiles.get(r.user_id, {}).get("handle"),
                "display_name": profiles.get(r.user_id, {}).get("display_name"),
                "avatar_url": profiles.get(r.user_id, {}).get("avatar_url"),
            }
            for r in rows
        ],
    }


@app.post("/memorials/{memorial_id}/admins/{user_id}", status_code=201, tags=["memorials"])
def add_admin(memorial_id: str, user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    memorial = _managed(db, memorial_id, principal.user_id, "add another")
    admins = _admin_ids(db, memorial_id)
    if user_id in admins:
        return {"added": False, "already": True}
    if len(admins) >= models.MemorialAdmin.MAX_ADMINS:
        raise HTTPException(
            status_code=400, detail=f"A memorial has at most {models.MemorialAdmin.MAX_ADMINS} administrators"
        )
    if user_id not in _profiles({user_id}):
        raise HTTPException(status_code=404, detail="Member not found")
    _may_name_admin(principal.user_id, user_id)
    db.add(models.MemorialAdmin(memorial_id=memorial_id, user_id=user_id, succession_order=len(admins) + 1))
    _schedule(db, memorial, user_id)
    db.commit()
    notify.notify(
        user_id,
        kind="memorial_admin",
        title=f"You now look after the memorial of {memorial.full_name}",
        body="You can approve tributes, edit the page and receive anniversary reminders.",
        link=f"/graveyard?open={memorial.id}",
    )
    return {"added": True, "succession_order": len(admins) + 1}


@app.delete("/memorials/{memorial_id}/admins/{user_id}", status_code=204, tags=["memorials"])
def remove_admin(memorial_id: str, user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Step down yourself, or remove someone who comes after you in the
    succession. The last administrator cannot go: a memorial nobody can moderate
    would collect tributes nobody approves.

    The order is a ranking and not a courtesy. Were every administrator free to
    remove every other, the third could remove the first and the second, become
    the first, and delete the memorial — which only the first may do.
    """
    _managed(db, memorial_id, principal.user_id, "remove one")
    admins = _admin_ids(db, memorial_id)
    if user_id not in admins:
        raise HTTPException(status_code=404, detail="Not an administrator of this memorial")
    if len(admins) == 1:
        raise HTTPException(status_code=400, detail="A memorial needs at least one administrator")
    if user_id != principal.user_id and admins.index(user_id) < admins.index(principal.user_id):
        raise HTTPException(
            status_code=403,
            detail="You can step down, or remove an administrator who comes after you — not one ahead of you.",
        )
    db.execute(
        delete(models.MemorialAdmin).where(
            models.MemorialAdmin.memorial_id == memorial_id, models.MemorialAdmin.user_id == user_id
        )
    )
    db.execute(
        delete(models.Reminder).where(
            models.Reminder.memorial_id == memorial_id,
            models.Reminder.user_id == user_id,
            models.Reminder.sent_at.is_(None),
        )
    )
    # The succession closes up behind them.
    for order, row in enumerate(
        db.scalars(
            select(models.MemorialAdmin)
            .where(models.MemorialAdmin.memorial_id == memorial_id)
            .order_by(models.MemorialAdmin.succession_order)
        ).all(),
        start=1,
    ):
        row.succession_order = order
    db.commit()


# ---------------------------------------------------------------------------
# Tributes
# ---------------------------------------------------------------------------

@app.post("/memorials/{memorial_id}/tributes", status_code=201, tags=["tributes"])
def add_tribute(memorial_id: str, payload: TributeIn, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    viewer = principal.user_id if principal else None
    memorial = _viewable(db, memorial_id, viewer)

    body = (payload.body or "").strip() or None
    media_url = None
    if payload.kind == "message" and not body:
        raise HTTPException(status_code=400, detail="A message needs some words")
    if payload.kind == "photo":
        if viewer is None:
            raise HTTPException(status_code=401, detail="Sign in to share a photo")
        if not payload.media_id:
            raise HTTPException(status_code=400, detail="A photo tribute needs a photo")
        media_url = _own_media(payload.media_id, viewer, ("image",))
    if payload.kind in ("candle", "flower"):
        body = None  # a gesture, not a message

    if viewer:
        # A member signs with their own name, never one they type. If the name
        # cannot be read right now the tribute is refused rather than signed
        # "A member" for good: it would stay that way on the family's page.
        author_name = _profiles({viewer}).get(viewer, {}).get("display_name")
        if not author_name:
            raise HTTPException(status_code=503, detail="Could not sign your tribute right now. Try again.")
    elif payload.kind == "message":
        author_name = " ".join((payload.author_name or "").split())[:120] or "A visitor"
    else:
        # A candle carries no words, so it carries no typed name either.
        author_name = "A visitor"

    # The typed name is shown beside the message once approved, and the family
    # reads it in the queue: it is read as the message is.
    typed_name = author_name if payload.kind == "message" and not viewer else ""
    _screen(f"{typed_name}\n{body or ''}".strip() or None, viewer, ["image"] if media_url else None)

    # What carries words or pictures waits for the family whenever its author
    # is unknown, whatever the memorial's setting: an open memorial trusts its
    # members, not every passer-by with a phone. The family's own words do not
    # wait for the family — an administrator would be approving themself.
    carries_content = payload.kind in ("message", "photo")
    if payload.kind in ("candle", "flower"):
        status = "approved"
    elif viewer is not None and (memorial.moderation == "open" or _is_admin_of(db, memorial_id, viewer)):
        status = "approved"
    else:
        status = "pending"

    tribute = models.Tribute(
        id=new_id("trb"),
        memorial_id=memorial_id,
        author_id=viewer,
        author_name=author_name,
        kind=payload.kind,
        body=body,
        media_url=media_url,
        status=status,
    )
    db.add(tribute)
    db.commit()

    if status == "pending" and carries_content:
        for admin_id in _admin_ids(db, memorial_id):
            if admin_id != viewer:
                # A stranger's words are not pushed at a minor. The memorial
                # page is where the family reads them, with its own controls;
                # a notification would deliver them unasked.
                preview = (
                    "Open the memorial to read it."
                    if ageclient.age_profile(admin_id).is_minor
                    else (body or "A photo")[:140]
                )
                notify.notify(
                    admin_id,
                    kind="memorial_tribute",
                    title=f"A tribute to {memorial.full_name} is waiting for you",
                    body=preview,
                    link=f"/graveyard?open={memorial.id}",
                )
    return {"id": tribute.id, "status": tribute.status}


@app.get("/memorials/{memorial_id}/tributes", tags=["tributes"])
def list_tributes(
    memorial_id: str,
    principal: MaybeUser,
    kind: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100000),
    db: OrmSession = Depends(get_db),
):
    """Approved tributes, newest first. ``kind`` takes one kind or several,
    comma-separated: a guest book asks for ``message,photo``, because a few
    hundred candles must not push the words out of the first page. ``counts``
    always covers everything; ``total`` covers the filter."""
    viewer = principal.user_id if principal else None
    _viewable(db, memorial_id, viewer)
    kinds = [k.strip() for k in (kind or "").split(",") if k.strip()]
    if (kind and not kinds) or any(k not in TRIBUTE_KINDS for k in kinds):
        raise HTTPException(status_code=422, detail=f"kind must be one or more of: {', '.join(TRIBUTE_KINDS)}")
    stmt = select(models.Tribute).where(
        models.Tribute.memorial_id == memorial_id, models.Tribute.status == "approved"
    )
    if kinds:
        stmt = stmt.where(models.Tribute.kind.in_(kinds))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Tribute.created_at.desc(), models.Tribute.id.desc()).limit(limit).offset(offset)
    ).all()
    counts = dict(
        db.execute(
            select(models.Tribute.kind, func.count())
            .where(models.Tribute.memorial_id == memorial_id, models.Tribute.status == "approved")
            .group_by(models.Tribute.kind)
        ).all()
    )
    return {"total": total, "counts": counts, "items": [_tribute_out(r, viewer) for r in rows]}


@app.get("/memorials/{memorial_id}/tributes/pending", tags=["tributes"])
def pending_tributes(memorial_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What is waiting for the family — oldest first, so nothing waits longest."""
    _managed(db, memorial_id, principal.user_id, "moderate tributes")
    rows = db.scalars(
        select(models.Tribute)
        .where(models.Tribute.memorial_id == memorial_id, models.Tribute.status == "pending")
        .order_by(models.Tribute.created_at)
        .limit(200)
    ).all()
    return {"items": [_tribute_out(r, principal.user_id, with_status=True) for r in rows]}


@app.post("/memorials/{memorial_id}/tributes/{tribute_id}/moderate", tags=["tributes"])
def moderate_tribute(
    memorial_id: str,
    tribute_id: str,
    decision: str,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Approve or reject — also an approved tribute, which is how one that
    should not have gone up comes down again."""
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")
    memorial = _managed(db, memorial_id, principal.user_id, "moderate tributes")
    tribute = db.get(models.Tribute, tribute_id)
    if tribute is None or tribute.memorial_id != memorial_id:
        raise HTTPException(status_code=404, detail="Tribute not found")
    was = tribute.status
    tribute.status = decision
    db.commit()
    if decision == "approved" and was == "pending" and tribute.author_id and tribute.author_id != principal.user_id:
        notify.notify(
            tribute.author_id,
            kind="memorial_tribute_approved",
            title=f"Your tribute to {memorial.full_name} is on the memorial",
            link=f"/memorial/{memorial.qr_code}",
        )
    return {"id": tribute_id, "status": decision}


# ---------------------------------------------------------------------------
# Life timeline
# ---------------------------------------------------------------------------

def _event_out(row: models.MemorialEvent) -> dict:
    return {
        "id": row.id,
        "year": row.year,
        "month": row.month,
        "day": row.day,
        "title": row.title,
        "body": row.body,
    }


@app.get("/memorials/{memorial_id}/events", tags=["timeline"])
def list_events(memorial_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    _viewable(db, memorial_id, principal.user_id if principal else None)
    rows = db.scalars(
        select(models.MemorialEvent)
        .where(models.MemorialEvent.memorial_id == memorial_id)
        .order_by(
            models.MemorialEvent.year,
            models.MemorialEvent.month.nullsfirst(),
            models.MemorialEvent.day.nullsfirst(),
            models.MemorialEvent.created_at,
        )
    ).all()
    return {"items": [_event_out(r) for r in rows]}


@app.post("/memorials/{memorial_id}/events", status_code=201, tags=["timeline"])
def add_event(memorial_id: str, payload: EventIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _managed(db, memorial_id, principal.user_id, "edit the timeline")
    if payload.day and not payload.month:
        raise HTTPException(status_code=400, detail="A day needs its month")
    if payload.month and payload.day:
        try:
            moment = date(payload.year, payload.month, payload.day)
        except ValueError:
            raise HTTPException(status_code=400, detail="That date does not exist")
        if moment > date.today():
            raise HTTPException(status_code=400, detail="That date is in the future")
    elif payload.year > date.today().year:
        raise HTTPException(status_code=400, detail="That year is in the future")
    body = (payload.body or "").strip() or None
    _screen(f"{payload.title}\n{body or ''}", principal.user_id)
    row = models.MemorialEvent(
        memorial_id=memorial_id,
        year=payload.year,
        month=payload.month,
        day=payload.day if payload.month else None,
        title=payload.title,
        body=body,
        created_by=principal.user_id,
    )
    db.add(row)
    db.commit()
    return _event_out(row)


@app.delete("/memorials/{memorial_id}/events/{event_id}", status_code=204, tags=["timeline"])
def delete_event(memorial_id: str, event_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _managed(db, memorial_id, principal.user_id, "edit the timeline")
    row = db.get(models.MemorialEvent, event_id)
    if row is None or row.memorial_id != memorial_id:
        raise HTTPException(status_code=404, detail="Moment not found")
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Gallery: photos and videos
# ---------------------------------------------------------------------------

class GalleryAdd(BaseModel):
    media_id: str = Field(pattern=MEDIA_ID)
    caption: str | None = Field(default=None, max_length=500)
    sensitive: bool = False


class GalleryPatch(BaseModel):
    caption: str | None = Field(default=None, max_length=500)
    sensitive: bool | None = None
    provenance: str | None = Field(default=None, pattern="^(original|edited|ai_assisted|ai_generated|verified_source)$")


class GalleryOrder(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=GALLERY_MAX_ITEMS)


def _gallery_rows(db: OrmSession, memorial_id: str) -> list[models.MemorialMedia]:
    return list(
        db.scalars(
            select(models.MemorialMedia)
            .where(models.MemorialMedia.memorial_id == memorial_id)
            .order_by(models.MemorialMedia.position, models.MemorialMedia.created_at)
        ).all()
    )


def _gallery_item(db: OrmSession, memorial_id: str, item_id: str) -> models.MemorialMedia:
    row = db.get(models.MemorialMedia, item_id)
    if row is None or row.memorial_id != memorial_id:
        raise HTTPException(status_code=404, detail="Photo or video not found")
    return row


def _gallery_out(row: models.MemorialMedia, viewer: str | None) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "url": _signed(row.media_url, viewer, ttl=VIDEO_TICKET_SECONDS if row.kind == "video" else None),
        "caption": row.caption,
        "provenance": row.provenance,
        "sensitive": row.sensitive,
        "position": row.position,
        "size_bytes": row.size_bytes,
        "from_tribute": row.source_tribute_id is not None,
    }


def _gallery_add(
    db: OrmSession,
    memorial_id: str,
    info: dict,
    *,
    caption: str | None,
    sensitive: bool,
    added_by: str,
    tribute_id: str | None = None,
) -> models.MemorialMedia:
    """Put a file in the gallery, last in line — within the limits."""
    url = info.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="That file cannot be shown.")
    rows = _gallery_rows(db, memorial_id)
    size = int(info.get("size_bytes") or 0)
    if len(rows) >= GALLERY_MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"A gallery holds at most {GALLERY_MAX_ITEMS} photos and videos.")
    if sum(r.size_bytes or 0 for r in rows) + size > GALLERY_MAX_BYTES:
        raise HTTPException(status_code=400, detail=f"A gallery holds at most {GALLERY_MAX_BYTES // 1024 // 1024} MB.")
    if any(r.media_url == url for r in rows):
        raise HTTPException(status_code=409, detail="That photo or video is already in the gallery.")
    provenance = info.get("provenance") if info.get("provenance") in GALLERY_PROVENANCE else "original"
    row = models.MemorialMedia(
        memorial_id=memorial_id,
        kind=info["kind"],
        media_url=url,
        caption=caption,
        provenance=provenance,
        sensitive=sensitive,
        position=(rows[-1].position + 1) if rows else 1,
        size_bytes=size,
        source_tribute_id=tribute_id,
        added_by=added_by,
    )
    db.add(row)
    db.flush()
    return row


def _still_referenced(db: OrmSession, url: str) -> bool:
    """Whether anything else on any memorial still points at this file. The same
    bytes uploaded twice by the same member are one asset, so two memorials — or
    a memorial's portrait and its gallery — can share one."""
    for stmt in (
        select(func.count()).select_from(models.MemorialMedia).where(models.MemorialMedia.media_url == url),
        select(func.count()).select_from(models.Memorial).where(
            or_(models.Memorial.photo_url == url, models.Memorial.cover_url == url, models.Memorial.memorial_audio_url == url)
        ),
        select(func.count()).select_from(models.Tribute).where(models.Tribute.media_url == url),
        select(func.count()).select_from(models.DeathReport).where(models.DeathReport.document_url == url),
    ):
        if db.scalar(stmt):
            return True
    return False


def _release_file(url: str | None, owner_id: str) -> None:
    """Ask media-service to delete a gallery file nobody points at any more.

    media-service only deletes files that carry a purpose, for the owner named —
    a gallery file's, never a post's — so a stale or wrong call cannot take
    somebody's picture. A failure leaves an orphan on disk and is logged: the
    gallery has already changed and must not fail because of it.
    """
    asset = _asset_id(url)
    if not asset:
        return
    try:
        httpx.post(f"{MEDIA_URL}/internal/media/{asset}/discard", json={"owner_id": owner_id}, timeout=6)
    except Exception as exc:
        log.warning("could not release gallery file %s: %s", asset, exc)


@app.get("/memorials/{memorial_id}/media", tags=["gallery"])
def list_media(memorial_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """The gallery, in the family's order. Anyone who may see the memorial may see it."""
    viewer = principal.user_id if principal else None
    _viewable(db, memorial_id, viewer)
    rows = _gallery_rows(db, memorial_id)
    out = {
        "limit": GALLERY_MAX_ITEMS,
        "max_bytes": GALLERY_MAX_BYTES,
        "items": [_gallery_out(r, viewer) for r in rows],
    }
    if _is_admin_of(db, memorial_id, viewer):
        out["bytes_used"] = sum(r.size_bytes or 0 for r in rows)
    return out


@app.post("/memorials/{memorial_id}/media", status_code=201, tags=["gallery"])
def add_media(memorial_id: str, payload: GalleryAdd, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _managed(db, memorial_id, principal.user_id, "add to the gallery")
    info = _own_media_info(payload.media_id, principal.user_id, ("image", "video"))
    # Only a file uploaded *for a memorial* has been through the memorial's
    # checks — its own size caps, and a look at what the bytes really are.
    if info.get("purpose") != "memorial":
        raise HTTPException(
            status_code=400,
            detail="Upload that file for a memorial first: the gallery only takes files that went through its checks.",
        )
    caption = (payload.caption or "").strip() or None
    _screen(caption, principal.user_id)
    row = _gallery_add(
        db, memorial_id, info, caption=caption, sensitive=payload.sensitive, added_by=principal.user_id
    )
    db.commit()
    return _gallery_out(row, principal.user_id)


@app.patch("/memorials/{memorial_id}/media/{item_id}", tags=["gallery"])
def update_media(
    memorial_id: str, item_id: str, payload: GalleryPatch, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    _managed(db, memorial_id, principal.user_id, "edit the gallery")
    row = _gallery_item(db, memorial_id, item_id)
    if "caption" in payload.model_fields_set:
        caption = (payload.caption or "").strip() or None
        _screen(caption, principal.user_id)
        row.caption = caption
    if payload.sensitive is not None:
        row.sensitive = payload.sensitive
    if payload.provenance is not None:
        row.provenance = payload.provenance
    db.commit()
    return _gallery_out(row, principal.user_id)


@app.post("/memorials/{memorial_id}/media/reorder", tags=["gallery"])
def reorder_media(memorial_id: str, payload: GalleryOrder, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Set the whole order at once. Every item must be named exactly once: a
    partial list would leave the rest in an order nobody chose."""
    _managed(db, memorial_id, principal.user_id, "edit the gallery")
    rows = _gallery_rows(db, memorial_id)
    if sorted(payload.ids) != sorted(r.id for r in rows):
        raise HTTPException(status_code=400, detail="Send every photo and video once, in the order you want.")
    by_id = {r.id: r for r in rows}
    for position, item_id in enumerate(payload.ids, start=1):
        by_id[item_id].position = position
    db.commit()
    return {"items": [_gallery_out(by_id[i], principal.user_id) for i in payload.ids]}


@app.delete("/memorials/{memorial_id}/media/{item_id}", status_code=204, tags=["gallery"])
def delete_media(memorial_id: str, item_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _managed(db, memorial_id, principal.user_id, "edit the gallery")
    row = _gallery_item(db, memorial_id, item_id)
    url, owner, from_tribute = row.media_url, row.added_by, row.source_tribute_id is not None
    db.delete(row)
    db.flush()
    for position, rest in enumerate(_gallery_rows(db, memorial_id), start=1):
        rest.position = position
    db.commit()
    # A promoted tribute photo still belongs to the tribute; anything else the
    # family uploaded goes, unless the same file is in use somewhere else.
    if not from_tribute and not _still_referenced(db, url):
        _release_file(url, owner)


@app.post("/memorials/{memorial_id}/media/from-tribute/{tribute_id}", status_code=201, tags=["gallery"])
def promote_tribute_photo(
    memorial_id: str, tribute_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Put a visitor's approved photo in the gallery. The item points at the same
    file as the tribute, so neither can take it from the other."""
    _managed(db, memorial_id, principal.user_id, "edit the gallery")
    tribute = db.get(models.Tribute, tribute_id)
    if (
        tribute is None or tribute.memorial_id != memorial_id or tribute.kind != "photo"
        or tribute.status != "approved" or not tribute.media_url
    ):
        raise HTTPException(status_code=404, detail="That photo is not among this memorial's approved tributes.")
    asset = _asset_id(tribute.media_url)
    info = _asset_info(asset) if asset else None
    if not info or info.get("kind") != "image":
        raise HTTPException(status_code=404, detail="That photo could not be found.")
    caption = (tribute.body or "").strip()[:500] or None
    row = _gallery_add(
        db, memorial_id, {**info, "url": tribute.media_url}, caption=caption, sensitive=False,
        added_by=principal.user_id, tribute_id=tribute.id,
    )
    db.commit()
    return _gallery_out(row, principal.user_id)


# ---------------------------------------------------------------------------
# Death verification: unconfirmed -> reported -> under_review -> verified
# ---------------------------------------------------------------------------

@app.post("/memorials/{memorial_id}/report-death", status_code=201, tags=["verification"])
async def report_death(
    memorial_id: str,
    payload: DeathReportIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Submit evidence of the death for review by the platform."""
    memorial = _viewable(db, memorial_id, principal.user_id)
    if memorial.death_status == "verified":
        raise HTTPException(status_code=400, detail="This death is already verified")
    evidence = payload.evidence.strip()
    if len(evidence) < 10:
        raise HTTPException(status_code=400, detail="Say a little more about the evidence")
    # One open report per person: the queue is read by people, and the same
    # sentence sent ten times is ten things to read, not ten witnesses.
    if db.scalar(
        select(func.count()).select_from(models.DeathReport).where(
            models.DeathReport.memorial_id == memorial_id,
            models.DeathReport.reported_by == principal.user_id,
            models.DeathReport.outcome.is_(None),
        )
    ):
        raise HTTPException(status_code=409, detail="You have already reported this death. Kinjy will review it.")
    document_url = (
        _own_media(payload.document_media_id, principal.user_id, ("image", "document"))
        if payload.document_media_id else None
    )
    db.add(
        models.DeathReport(
            memorial_id=memorial_id,
            reported_by=principal.user_id,
            evidence=evidence,
            document_url=document_url,
        )
    )
    if memorial.death_status == "unconfirmed":
        memorial.death_status = "reported"
    db.commit()
    await events.publish("memorial.death_reported", {"memorial_id": memorial_id})
    return {"death_status": memorial.death_status}


@app.get("/admin/memorials/death-reports", tags=["verification"])
def death_report_queue(admin: ModeratorUser, db: OrmSession = Depends(get_db)):
    """Memorials waiting for a decision, oldest report first."""
    memorials = db.scalars(
        select(models.Memorial).where(models.Memorial.death_status.in_(("reported", "under_review")))
    ).all()
    reports = db.scalars(
        select(models.DeathReport)
        .where(
            models.DeathReport.memorial_id.in_([m.id for m in memorials] or [""]),
            models.DeathReport.outcome.is_(None),
        )
        .order_by(models.DeathReport.created_at)
    ).all()
    profiles = _profiles({r.reported_by for r in reports})
    by_memorial: dict[str, list[dict]] = {}
    for r in reports:
        by_memorial.setdefault(r.memorial_id, []).append({
            "id": r.id,
            "evidence": r.evidence,
            "document_url": _signed(r.document_url, admin.user_id),
            "reported_by": profiles.get(r.reported_by, {}).get("display_name") or r.reported_by,
            "created_at": r.created_at,
        })
    items = [
        {
            "id": m.id,
            "full_name": m.full_name,
            "death_date": m.death_date,
            "death_status": m.death_status,
            "qr_code": m.qr_code,
            "reports": by_memorial.get(m.id, []),
        }
        for m in memorials
    ]
    items.sort(key=lambda m: m["reports"][0]["created_at"] if m["reports"] else datetime.max.replace(tzinfo=timezone.utc))
    return {"items": items}


@app.post("/admin/memorials/{memorial_id}/review", tags=["verification"])
def start_review(memorial_id: str, admin: ModeratorUser, db: OrmSession = Depends(get_db)):
    """Take a reported death under review, so a second reviewer does not
    duplicate the work and the family sees that someone is looking."""
    memorial = db.get(models.Memorial, memorial_id)
    if memorial is None:
        raise HTTPException(status_code=404, detail="Memorial not found")
    if memorial.death_status != "reported":
        raise HTTPException(status_code=400, detail=f"Nothing to review: the death is {memorial.death_status}")
    memorial.death_status = "under_review"
    db.commit()
    return {"memorial_id": memorial_id, "death_status": memorial.death_status}


@app.post("/admin/memorials/{memorial_id}/verify-death", tags=["verification"])
def verify_death(memorial_id: str, outcome: str, admin: ModeratorUser, db: OrmSession = Depends(get_db)):
    if outcome not in ("verified", "rejected"):
        raise HTTPException(status_code=400, detail="outcome must be verified or rejected")
    memorial = db.get(models.Memorial, memorial_id)
    if memorial is None:
        raise HTTPException(status_code=404, detail="Memorial not found")
    if memorial.death_status not in ("reported", "under_review"):
        raise HTTPException(status_code=400, detail=f"Nothing to decide: the death is {memorial.death_status}")
    memorial.death_status = "verified" if outcome == "verified" else "unconfirmed"
    memorial.verified_at = _now() if outcome == "verified" else None
    for report in db.scalars(
        select(models.DeathReport).where(
            models.DeathReport.memorial_id == memorial_id, models.DeathReport.outcome.is_(None)
        )
    ).all():
        report.reviewed_by = admin.user_id
        report.outcome = outcome
    db.commit()
    for admin_id in _admin_ids(db, memorial_id):
        notify.notify(
            admin_id,
            kind="memorial_verification",
            title=(
                f"The death of {memorial.full_name} is verified"
                if outcome == "verified"
                else f"The evidence for {memorial.full_name} was not enough to verify the death"
            ),
            link=f"/graveyard?open={memorial.id}",
        )
    return {"memorial_id": memorial_id, "death_status": memorial.death_status}


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

@app.get("/memorials/{memorial_id}/reminders", tags=["reminders"])
def reminders(memorial_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Your coming anniversary reminders for a memorial you look after."""
    _managed(db, memorial_id, principal.user_id, "see its reminders")
    rows = db.scalars(
        select(models.Reminder)
        .where(
            models.Reminder.memorial_id == memorial_id,
            models.Reminder.user_id == principal.user_id,
            models.Reminder.sent_at.is_(None),
        )
        .order_by(models.Reminder.due_at)
    ).all()
    return {
        "items": [
            {"occasion": r.occasion, "due_at": r.due_at, "offset_hours": r.offset_hours, "sent": False}
            for r in rows
        ]
    }


@app.post("/internal/reminders/sweep", tags=["internal"])
def run_sweep(now: datetime | None = None):
    """Run the reminder sweep now — for operations, and for tests that cannot
    wait for an anniversary. ``now`` lets a test stand at a chosen moment."""
    moment = now.astimezone(timezone.utc) if now and now.tzinfo else (now.replace(tzinfo=timezone.utc) if now else None)
    return {"sent": sweep_reminders(moment)}
