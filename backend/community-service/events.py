"""Community events: online, in person or hybrid; free or with paid tickets.

Times are stored in UTC with the IANA zone the event happens in, so every
reader sees their own local time and daylight-saving changes are the
browser's job, not a stored offset's.

Capacity is never exceeded: a registration locks the event row and counts
under that lock, so concurrent registrations queue rather than oversell. A
free event with a waitlist promotes the earliest waiting member when a place
opens. A paid ticket takes its place when the payment is credited; if the
event filled up in between, the payment is refunded in full.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from common import notify
from common.auth import CurrentUser, MaybeUser
from common.database import get_db
from common.ids import new_id

import models
import paid
import spaces as space_rules

log = logging.getLogger("community-service.events")
router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _readable(db: OrmSession, community: models.Community, user_id: str | None) -> bool:
    """Who may see a community's events: as for its posts."""
    role = paid._role(db, community.id, user_id)
    if role:
        return True
    return community.kind == "public" and bool(community.public_content)


def _steward(db: OrmSession, community_id: str, user_id: str) -> bool:
    return paid._role(db, community_id, user_id) in ("owner", "moderator")


def _presenter(db: OrmSession, event_id: str, user_id: str | None) -> bool:
    return bool(user_id and db.scalar(select(models.EventPresenter.id).where(
        models.EventPresenter.event_id == event_id, models.EventPresenter.user_id == user_id)))


def _own_role(db: OrmSession, e: models.CommunityEvent, user_id: str | None) -> str | None:
    """'host' or 'presenter' when this member runs the event - they take part
    without a ticket or a registration - else None. Read from the stored event
    and the signed-in account, never from the request."""
    if not user_id:
        return None
    if user_id == e.host_id and paid._role(db, e.community_id, user_id):
        return "host"
    return "presenter" if _presenter(db, e.id, user_id) else None


def _event_out(db: OrmSession, e: models.CommunityEvent, viewer: str | None) -> dict:
    mine = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == e.id, models.EventRegistration.user_id == viewer)) if viewer else None
    role = _own_role(db, e, viewer)
    # The host and presenters take part as if registered, without paying.
    registered = bool(mine and mine.status == "registered") or role is not None
    steward = bool(viewer and _steward(db, e.community_id, viewer))
    price = paid.money(e.ticket_price) if e.ticket_price else None
    community = db.get(models.Community, e.community_id)
    return {
        "id": e.id, "community_id": e.community_id, "title": e.title, "description": e.description,
        "starts_at": e.starts_at, "ends_at": e.ends_at, "timezone": e.timezone, "mode": e.mode,
        "location": e.location,
        # The joining link is for the people who registered (and the hosts).
        "online_url": e.online_url if (registered or steward) else None,
        "capacity": e.capacity, "registered_count": e.registered_count,
        "places_left": max(e.capacity - e.registered_count, 0) if e.capacity else None,
        "waitlist": e.waitlist, "speakers": [s for s in (e.speakers or "").splitlines() if s.strip()],
        "recording_consent": e.recording_consent,
        "recording_url": e.recording_url if (registered or steward) else None,
        "status": e.status, "host_id": e.host_id,
        "ticket_owner_price": str(price) if price else None,
        "ticket_price": str(price + paid.money(price * paid.markup_pct(db) / 100)) if price else None,
        "my_status": mine.status if mine else None,
        # host | presenter | None: what the page offers instead of a ticket.
        "my_role": role,
        # Only ever returned to someone allowed to read the event, so the
        # community's name tells them nothing new.
        "community": {"id": community.id, "slug": community.slug, "name": community.name} if community else None,
        # The host, or the community's owner and moderators.
        "can_edit": bool(viewer and (steward or (viewer == e.host_id and paid._role(db, e.community_id, viewer)))),
    }


class EventIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=8000)
    starts_at: datetime
    ends_at: datetime | None = None
    timezone: str = Field(default="UTC", max_length=60)
    mode: str = Field(default="online", pattern="^(online|in_person|hybrid)$")
    location: str | None = Field(default=None, max_length=300)
    online_url: str | None = Field(default=None, max_length=500, pattern=r"^https://")
    capacity: int | None = Field(default=None, ge=1, le=100000)
    waitlist: bool = True
    ticket_price: float | None = Field(default=None, ge=0, le=100000)
    speakers: list[str] = Field(default_factory=list, max_length=20)
    recording_consent: bool = False


@router.post("/communities/{community_id}/events", status_code=201, tags=["events"])
def create_event(community_id: str, payload: EventIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    community = paid._community(db, community_id)
    if not _steward(db, community.id, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the owner and moderators create events")
    if "events" not in space_rules.enabled_spaces(db, community):
        raise HTTPException(status_code=409, detail="Switch the Events space on first")
    try:
        ZoneInfo(payload.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=400, detail="Unknown time zone")
    starts = _aware(payload.starts_at)
    if starts < _now():
        raise HTTPException(status_code=400, detail="The event must start in the future")
    if payload.ends_at and _aware(payload.ends_at) <= starts:
        raise HTTPException(status_code=400, detail="The event must end after it starts")
    if payload.ticket_price and not paid.paid_enabled(db):
        raise HTTPException(status_code=409, detail="Paid tickets are not open on Kinjy yet")
    if payload.ticket_price and not payload.capacity:
        raise HTTPException(status_code=400, detail="A paid event needs a number of places")
    event = models.CommunityEvent(
        id=new_id("evt"), community_id=community.id, host_id=principal.user_id, title=payload.title.strip(),
        description=payload.description.strip(), starts_at=starts, ends_at=_aware(payload.ends_at),
        timezone=payload.timezone, mode=payload.mode, location=payload.location, online_url=payload.online_url,
        capacity=payload.capacity, waitlist=payload.waitlist and not payload.ticket_price,
        ticket_price=paid.money(payload.ticket_price) if payload.ticket_price else None,
        speakers="\n".join(s.strip() for s in payload.speakers if s.strip()),
        recording_consent=payload.recording_consent,
    )
    db.add(event)
    db.commit()
    return _event_out(db, event, principal.user_id)


@router.get("/communities/{community_id}/events", tags=["events"])
def list_events(community_id: str, principal: MaybeUser, past: bool = False, db: OrmSession = Depends(get_db)):
    community = paid._community(db, community_id)
    viewer = principal.user_id if principal else None
    paid._visible(db, community, viewer)
    if not _readable(db, community, viewer):
        raise HTTPException(status_code=404, detail="Community not found")
    q = select(models.CommunityEvent).where(models.CommunityEvent.community_id == community.id)
    if past:
        q = q.where(models.CommunityEvent.starts_at < _now()).order_by(models.CommunityEvent.starts_at.desc())
    else:
        q = q.where(models.CommunityEvent.starts_at >= _now() - timedelta(hours=6)).order_by(
            models.CommunityEvent.starts_at)
    return {"items": [_event_out(db, e, viewer) for e in db.scalars(q.limit(50)).all()]}


def _event(db: OrmSession, event_id: str, viewer: str | None) -> tuple[models.CommunityEvent, models.Community]:
    event = db.get(models.CommunityEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    community = db.get(models.Community, event.community_id)
    paid._visible(db, community, viewer)
    if not _readable(db, community, viewer):
        raise HTTPException(status_code=404, detail="Event not found")
    return event, community


@router.get("/events/{event_id}", tags=["events"])
def get_event(event_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    viewer = principal.user_id if principal else None
    event, _ = _event(db, event_id, viewer)
    return _event_out(db, event, viewer)


@router.post("/events/{event_id}/register", tags=["events"])
def register(event_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Take a place at a free event, or the waitlist when it is full."""
    event, community = _event(db, event_id, principal.user_id)
    if paid._role(db, community.id, principal.user_id) is None:
        raise HTTPException(status_code=403, detail="Join the community to register")
    if _own_role(db, event, principal.user_id):
        # Running it is taking part: no ticket, and no place taken from attendees.
        return _event_out(db, event, principal.user_id)
    if event.ticket_price:
        raise HTTPException(status_code=402, detail="This event needs a ticket")
    event = db.scalar(select(models.CommunityEvent).where(models.CommunityEvent.id == event_id).with_for_update().execution_options(populate_existing=True))
    if event.status != "scheduled" or _aware(event.starts_at) < _now():
        raise HTTPException(status_code=409, detail="Registration is closed")
    mine = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event.id, models.EventRegistration.user_id == principal.user_id))
    if mine is not None and mine.status in ("registered", "waitlisted"):
        db.rollback()
        return _event_out(db, event, principal.user_id)
    full = bool(event.capacity and event.registered_count >= event.capacity)
    if full and not event.waitlist:
        db.rollback()
        raise HTTPException(status_code=409, detail="This event is full")
    status = "waitlisted" if full else "registered"
    if mine is None:
        mine = models.EventRegistration(event_id=event.id, user_id=principal.user_id, status=status)
        db.add(mine)
    else:
        mine.status, mine.created_at = status, _now()
    mine.updated_at = _now()
    if status == "registered":
        event.registered_count += 1
    db.commit()
    return _event_out(db, event, principal.user_id)


@router.post("/events/{event_id}/unregister", tags=["events"])
def unregister(event_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Give a place back (a paid ticket is refunded by the owner, not here)."""
    event, _ = _event(db, event_id, principal.user_id)
    if event.ticket_price:
        raise HTTPException(status_code=409, detail="Ask the organiser to refund a paid ticket")
    event = db.scalar(select(models.CommunityEvent).where(models.CommunityEvent.id == event_id).with_for_update().execution_options(populate_existing=True))
    mine = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event.id, models.EventRegistration.user_id == principal.user_id))
    if mine is None or mine.status == "cancelled":
        db.rollback()
        return _event_out(db, event, principal.user_id)
    was = mine.status
    mine.status, mine.updated_at = "cancelled", _now()
    promoted = None
    if was == "registered":
        event.registered_count = max(0, event.registered_count - 1)
        promoted = _promote(db, event)
    db.commit()
    if promoted:
        notify.notify(promoted, kind="event_place", title=f"A place opened for {event.title}: you are registered",
                      link=f"/events/{event.id}")
    return _event_out(db, event, principal.user_id)


def _promote(db: OrmSession, event: models.CommunityEvent) -> str | None:
    """The earliest waiting member takes the free place (event row is locked)."""
    if event.capacity and event.registered_count >= event.capacity:
        return None
    nxt = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event.id, models.EventRegistration.status == "waitlisted")
        .order_by(models.EventRegistration.created_at).limit(1).with_for_update(skip_locked=True).execution_options(populate_existing=True))
    if nxt is None:
        return None
    nxt.status, nxt.updated_at = "registered", _now()
    event.registered_count += 1
    return nxt.user_id


@router.post("/events/{event_id}/cancel", tags=["events"])
def cancel_event(event_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Cancel an event. Everybody registered is told; paid tickets are refunded in full."""
    event, community = _event(db, event_id, principal.user_id)
    if not _steward(db, community.id, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the owner and moderators cancel events")
    claimed = db.execute(update(models.CommunityEvent).where(
        models.CommunityEvent.id == event.id, models.CommunityEvent.status == "scheduled")
        .values(status="cancelled")).rowcount
    db.commit()
    if not claimed:
        raise HTTPException(status_code=409, detail="This event is already cancelled")
    people = db.scalars(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event.id,
        models.EventRegistration.status.in_(("registered", "waitlisted")))).all()
    for reg in people:
        if reg.payment_id:
            try:
                paid.do_refund(db, community, reg.payment_id,
                               paid.money(db.get(models.CommunityPayment, reg.payment_id).total),
                               "The event was cancelled", principal.user_id, f"evc-{reg.payment_id}"[:36])
            except HTTPException as exc:
                log.error("refund for cancelled event %s, payment %s: %s", event.id, reg.payment_id, exc.detail)
    notify.notify_many([r.user_id for r in people], kind="event_cancelled", title=f"{event.title} is cancelled",
                       body="Paid tickets are refunded to your Kinjy balance." if event.ticket_price else None)
    return {"cancelled": True, "told": len(people)}


@router.get("/events/{event_id}/attendees", tags=["events"])
def attendees(event_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    event, community = _event(db, event_id, principal.user_id)
    if not _steward(db, community.id, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the hosts see the list")
    rows = db.scalars(select(models.EventRegistration).where(models.EventRegistration.event_id == event.id)
                      .order_by(models.EventRegistration.created_at)).all()
    return {"items": [{"user_id": r.user_id, "status": r.status, "paid": bool(r.payment_id),
                       "created_at": r.created_at} for r in rows]}


# --- presenters -----------------------------------------------------------------
# The host names the members who present with them. A presenter joins without a
# ticket and gets the joining link; they do not edit, cancel or see the money,
# and it is for this one event only.

def _may_manage(db: OrmSession, event: models.CommunityEvent, user_id: str) -> bool:
    return _own_role(db, event, user_id) == "host" or _steward(db, event.community_id, user_id)


def _presenters_out(db: OrmSession, event_id: str) -> list[dict]:
    rows = db.scalars(select(models.EventPresenter).where(models.EventPresenter.event_id == event_id)
                      .order_by(models.EventPresenter.created_at)).all()
    names: dict[str, dict] = {}
    if rows:
        try:
            response = httpx.post("http://user-service:8000/internal/profiles",
                                  json={"ids": [r.user_id for r in rows]}, timeout=5)
            if response.status_code == 200:
                names = response.json().get("profiles", {})
        except httpx.HTTPError:
            pass
    return [{"user_id": r.user_id, "handle": names.get(r.user_id, {}).get("handle"),
             "display_name": names.get(r.user_id, {}).get("display_name"), "added_at": r.created_at} for r in rows]


@router.get("/events/{event_id}/presenters", tags=["events"])
def list_presenters(event_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    event, _ = _event(db, event_id, principal.user_id)
    if not _may_manage(db, event, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the host and the community's moderators see this")
    return {"items": _presenters_out(db, event.id)}


class PresenterIn(BaseModel):
    handle: str = Field(min_length=2, max_length=60)


@router.post("/events/{event_id}/presenters", status_code=201, tags=["events"])
def add_presenter(event_id: str, payload: PresenterIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Name a member of the community as a presenter of this event (host or moderators)."""
    event, community = _event(db, event_id, principal.user_id)
    if not _may_manage(db, event, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the host and the community's moderators name presenters")
    if event.status != "scheduled":
        raise HTTPException(status_code=409, detail="This event is cancelled")
    handle = payload.handle.strip().lstrip("@").lower()
    try:
        response = httpx.post("http://user-service:8000/internal/profiles/by-handle",
                              json={"handles": [handle]}, timeout=5)
        response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Members cannot be looked up right now. Try again.")
    person = response.json().get("profiles", {}).get(handle)
    # Somebody who is not an active member could not even read the event.
    if person is None or paid._role(db, community.id, person["user_id"]) is None:
        raise HTTPException(status_code=404, detail="No member of this community has that handle")
    if person["user_id"] == event.host_id:
        raise HTTPException(status_code=409, detail="The host already runs this event")
    if not _presenter(db, event.id, person["user_id"]):
        db.add(models.EventPresenter(event_id=event.id, user_id=person["user_id"], added_by=principal.user_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # named twice at the same moment: one row
        else:
            notify.notify(person["user_id"], kind="event_presenter", title=f"You are a presenter at {event.title}",
                          body="You join it without a ticket.", link=f"/events/{event.id}")
    return {"items": _presenters_out(db, event.id)}


@router.delete("/events/{event_id}/presenters/{user_id}", tags=["events"])
def remove_presenter(event_id: str, user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    event, _ = _event(db, event_id, principal.user_id)
    if not _may_manage(db, event, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the host and the community's moderators remove presenters")
    db.execute(delete(models.EventPresenter).where(
        models.EventPresenter.event_id == event.id, models.EventPresenter.user_id == user_id))
    db.commit()
    return {"items": _presenters_out(db, event.id)}


# --- paid tickets (called from paid.py) ---------------------------------------

def ticketed_event(db: OrmSession, community: models.Community, event_id: str, user_id: str) -> models.CommunityEvent:
    """An event a member may buy a ticket for now, or an explanation why not."""
    event = db.get(models.CommunityEvent, event_id)
    if event is None or event.community_id != community.id:
        raise HTTPException(status_code=404, detail="Event not found")
    if not event.ticket_price:
        raise HTTPException(status_code=400, detail="This event is free: register instead")
    if paid._role(db, community.id, user_id) is None:
        raise HTTPException(status_code=403, detail="Join the community to buy a ticket")
    own = _own_role(db, event, user_id)
    if own:
        raise HTTPException(status_code=409, detail=f"You are {'hosting' if own == 'host' else 'presenting at'} "
                                                   "this event: you take part without a ticket")
    if event.status != "scheduled" or _aware(event.starts_at) < _now():
        raise HTTPException(status_code=409, detail="Ticket sales are closed")
    mine = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event.id, models.EventRegistration.user_id == user_id))
    if mine is not None and mine.status == "registered":
        raise HTTPException(status_code=409, detail="You already have a place")
    if event.capacity and event.registered_count >= event.capacity:
        raise HTTPException(status_code=409, detail="Sold out")
    return event


def seat_paid_ticket(db: OrmSession, event_id: str, user_id: str, payment_id: str) -> bool:
    """Give the paying member their place, under the event lock. False when the
    event filled up (or was cancelled) since the quote: the caller refunds."""
    event = db.scalar(select(models.CommunityEvent).where(models.CommunityEvent.id == event_id).with_for_update().execution_options(populate_existing=True))
    if event is None or event.status != "scheduled" or (event.capacity and event.registered_count >= event.capacity):
        return False
    mine = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event.id, models.EventRegistration.user_id == user_id))
    if mine is not None and mine.status == "registered":
        return False  # already has a place: a second payment is refunded
    if mine is None:
        mine = models.EventRegistration(event_id=event.id, user_id=user_id, status="registered", payment_id=payment_id)
        db.add(mine)
    else:
        mine.status, mine.payment_id = "registered", payment_id
    mine.updated_at = _now()
    event.registered_count += 1
    return True


def release_paid_seat(db: OrmSession, event_id: str, user_id: str) -> None:
    event = db.scalar(select(models.CommunityEvent).where(models.CommunityEvent.id == event_id).with_for_update().execution_options(populate_existing=True))
    mine = db.scalar(select(models.EventRegistration).where(
        models.EventRegistration.event_id == event_id, models.EventRegistration.user_id == user_id))
    if event is not None and mine is not None and mine.status == "registered":
        mine.status, mine.updated_at = "cancelled", _now()
        event.registered_count = max(0, event.registered_count - 1)


# --- reminders (background) ---------------------------------------------------

def remind_upcoming(db: OrmSession) -> int:
    """One reminder per registration, a day before. Claimed atomically."""
    soon = _now() + timedelta(hours=24)
    rows = db.execute(select(models.EventRegistration.id, models.EventRegistration.user_id, models.CommunityEvent.id,
                             models.CommunityEvent.title, models.CommunityEvent.starts_at)
                      .join(models.CommunityEvent, models.CommunityEvent.id == models.EventRegistration.event_id)
                      .where(models.EventRegistration.status == "registered",
                             models.EventRegistration.reminded_at.is_(None),
                             models.CommunityEvent.status == "scheduled",
                             models.CommunityEvent.starts_at > _now(),
                             models.CommunityEvent.starts_at <= soon).limit(500)).all()
    sent = 0
    for reg_id, user_id, event_id, title, starts in rows:
        claimed = db.execute(update(models.EventRegistration).where(
            models.EventRegistration.id == reg_id, models.EventRegistration.reminded_at.is_(None))
            .values(reminded_at=_now())).rowcount
        db.commit()
        if claimed:
            sent += 1
            notify.notify(user_id, kind="event_reminder", title=f"Tomorrow: {title}", link=f"/events/{event_id}")
    return sent


# --- editing, and the events a member hosts -------------------------------------

class EventPatch(BaseModel):
    """What the creation form sets, every field optional: only what is sent changes."""

    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=60)
    mode: str | None = Field(default=None, pattern="^(online|in_person|hybrid)$")
    location: str | None = Field(default=None, max_length=300)
    online_url: str | None = Field(default=None, max_length=500, pattern=r"^(https://.*)?$")
    capacity: int | None = Field(default=None, ge=1, le=100000)
    waitlist: bool | None = None
    ticket_price: float | None = Field(default=None, ge=0, le=100000)
    speakers: list[str] | None = Field(default=None, max_length=20)
    recording_consent: bool | None = None


@router.patch("/events/{event_id}", tags=["events"])
def update_event(event_id: str, payload: EventPatch, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Change an event: its host, or the community's owner and moderators.
    Registrations, waitlist and tickets are kept; people registered are told
    when the time or the place changes."""
    event, community = _event(db, event_id, principal.user_id)
    host = principal.user_id == event.host_id and paid._role(db, community.id, principal.user_id)
    if not (host or _steward(db, community.id, principal.user_id)):
        raise HTTPException(status_code=403, detail="Only the host and the community's moderators edit this event")
    event = db.scalar(select(models.CommunityEvent).where(models.CommunityEvent.id == event.id).with_for_update())
    if event.status != "scheduled":
        raise HTTPException(status_code=409, detail="A cancelled event cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    if "title" in changes and changes["title"] is None:
        raise HTTPException(status_code=422, detail="An event needs a title")
    if changes.get("timezone"):
        try:
            ZoneInfo(changes["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=400, detail="Unknown time zone")
    starts = _aware(changes["starts_at"]) if changes.get("starts_at") else _aware(event.starts_at)
    if changes.get("starts_at") and starts != _aware(event.starts_at) and starts < _now():
        raise HTTPException(status_code=400, detail="The event must start in the future")
    ends = _aware(changes["ends_at"]) if "ends_at" in changes else _aware(event.ends_at)
    if ends and ends <= starts:
        raise HTTPException(status_code=400, detail="The event must end after it starts")
    if "capacity" in changes and changes["capacity"] is not None and changes["capacity"] < event.registered_count:
        raise HTTPException(
            status_code=400,
            detail=f"{event.registered_count} people are already registered: the places cannot go below that",
        )
    price_now = paid.money(event.ticket_price) if event.ticket_price else None
    if "ticket_price" in changes:
        new_price = paid.money(changes["ticket_price"]) if changes["ticket_price"] else None
        if new_price != price_now:
            sold = db.scalar(select(models.EventRegistration.id).where(
                models.EventRegistration.event_id == event.id, models.EventRegistration.payment_id.is_not(None)).limit(1))
            if sold:
                raise HTTPException(status_code=409, detail="Tickets were already sold: the price cannot change")
            if new_price and not paid.paid_enabled(db):
                raise HTTPException(status_code=409, detail="Paid tickets are not open on Kinjy yet")
            capacity = changes.get("capacity", event.capacity)
            if new_price and not capacity:
                raise HTTPException(status_code=400, detail="A paid event needs a number of places")
            event.ticket_price = new_price
    moved = any(
        k in changes and changes[k] != getattr(event, k)
        for k in ("mode", "location", "online_url")
    ) or ("starts_at" in changes and starts != _aware(event.starts_at))
    if "title" in changes:
        event.title = changes["title"].strip()
    if "description" in changes:
        event.description = (changes["description"] or "").strip()
    if "starts_at" in changes and changes["starts_at"]:
        event.starts_at = starts
    if "ends_at" in changes:
        event.ends_at = ends
    for key in ("timezone", "mode", "location", "capacity", "recording_consent"):
        if key in changes and (changes[key] is not None or key in ("location", "capacity")):
            setattr(event, key, changes[key])
    if "online_url" in changes:
        event.online_url = changes["online_url"] or None
    if "waitlist" in changes and changes["waitlist"] is not None:
        event.waitlist = changes["waitlist"] and not event.ticket_price
    if "speakers" in changes and changes["speakers"] is not None:
        event.speakers = "\n".join(s.strip() for s in changes["speakers"] if s.strip())
    # More places may let the waitlist in.
    promoted = []
    while (who := _promote(db, event)) is not None:
        promoted.append(who)
    db.commit()
    if moved:
        people = db.scalars(select(models.EventRegistration.user_id).where(
            models.EventRegistration.event_id == event.id,
            models.EventRegistration.status.in_(("registered", "waitlisted")))).all()
        notify.notify_many(list(people), kind="event_updated", title=f"{event.title}: the time or place changed",
                           body="Open the event to see the new details.", link=f"/events/{event.id}")
    if promoted:
        notify.notify_many(promoted, kind="event_place", title=f"You have a place at {event.title}",
                           link=f"/events/{event.id}")
    return _event_out(db, event, principal.user_id)


@router.get("/events/by-host/{user_id}", tags=["events"])
def hosted_events(user_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """Upcoming events a member hosts, for their profile: only scheduled ones,
    and only in communities this reader may read."""
    viewer = principal.user_id if principal else None
    rows = db.scalars(select(models.CommunityEvent).where(
        models.CommunityEvent.host_id == user_id,
        models.CommunityEvent.status == "scheduled",
        models.CommunityEvent.starts_at >= _now() - timedelta(hours=6),
    ).order_by(models.CommunityEvent.starts_at).limit(50)).all()
    items = []
    for e in rows:
        community = db.get(models.Community, e.community_id)
        if community is None:
            continue
        try:
            paid._visible(db, community, viewer)
        except HTTPException:
            continue
        if _readable(db, community, viewer):
            items.append(_event_out(db, e, viewer))
    return {"items": items[:20]}
