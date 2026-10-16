"""Kinjy · community-service — communities, groups, forums, knowledge base."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm.exc import StaleDataError

import logging

import httpx

from common import events
from common import notify
from common import permissions
from common.auth import CurrentUser, MaybeUser
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import agecommunity
import models

log = logging.getLogger("community-service")
MESSAGING_URL = "http://messaging-service:8000"


USER_URL = "http://user-service:8000"
AI_URL = "http://ai-service:8000"

# Below this a thread is short enough to read. Summarising six replies wastes a
# model call and tells the reader nothing they could not get by scrolling.
SUMMARY_MIN_REPLIES = 8

# "Trending" favours threads somebody touched this week, then the ones people
# are actually talking in. Older threads still appear, after those.
TRENDING_WINDOW = timedelta(days=7)

ThreadSort = Literal["recent", "trending", "solved", "unanswered"]

# A reply becomes a knowledge entry once its asker accepted it and this many
# members agreed. One vote is a thank-you; two is a consensus.
KNOWLEDGE_MIN_VOTES = 2
# Entries a viewer may not read are dropped after the query, so the query is read
# in batches until enough readable ones are found; a hidden entry then never
# decides what `limit` returns.
KNOWLEDGE_BATCH = 50
KNOWLEDGE_MAX_BATCHES = 6
KNOWLEDGE_QUERY_MAX = 100


def _profiles(user_ids: set[str]) -> dict[str, dict]:
    """Resolve everyone on the page in one call rather than one call per row."""
    if not user_ids:
        return {}
    try:
        response = httpx.post(
            f"{USER_URL}/internal/profiles", json={"ids": sorted(user_ids)}, timeout=5
        )
        response.raise_for_status()
        return response.json()["profiles"]
    except Exception as exc:
        log.warning("could not resolve community members: %s", exc)
        return {}


def _live(topic: str, event: str, **data) -> None:
    """Push a live update. Best-effort: a hub outage costs liveness, not a reply."""
    try:
        httpx.post(
            f"{MESSAGING_URL}/internal/broadcast",
            json={"topic": topic, "type": event, "data": data},
            timeout=1.5,
        )
    except Exception as exc:
        log.debug("live update dropped (%s %s): %s", topic, event, exc)


MIGRATIONS = [
    # The secret-community invite behind a shared link. Additive and nullable.
    f"ALTER TABLE {models.SCHEMA}.communities ADD COLUMN IF NOT EXISTS invite_code VARCHAR(32)",
    f"CREATE INDEX IF NOT EXISTS ix_communities_invite_code ON {models.SCHEMA}.communities (invite_code)",
]

app = create_app(
    name="community-service",
    schema=models.SCHEMA,
    migrations=MIGRATIONS,
    description="Communities and groups, geographic + topic forums, forum-to-knowledge.",
)

GEO_SCOPES = ["global", "continent", "region", "country", "state", "district", "city", "neighborhood"]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or new_id("c")[:12]


def _resolve(db: OrmSession, handle: str) -> models.Community | None:
    """A community by its id or by its slug.

    Both, so a link can be /communities/lagos-hikers rather than
    /communities/cmy_01M46... - a URL somebody can read, say aloud and
    recognise before they click it.

    The two cannot be confused: an id is prefixed `cmy_`, and a slug never
    contains an underscore because _slugify turns every non-alphanumeric
    character into a dash.
    """
    if handle.startswith("cmy_"):
        return db.get(models.Community, handle)
    return db.scalar(select(models.Community).where(models.Community.slug == handle))


class CommunityIn(BaseModel):
    name: str = Field(min_length=2, max_length=140)
    description: str = ""
    kind: str = Field(default="public", pattern="^(public|private|secret|paid)$")
    price_usd: float | None = None
    lang: str = "en"
    country: str | None = None
    city: str | None = None


class ForumIn(BaseModel):
    name: str = Field(min_length=2, max_length=140)
    description: str = ""
    parent_id: str | None = None
    hierarchy: str = Field(default="topic", pattern="^(topic|geographic)$")
    scope: str | None = None
    scope_value: str | None = None
    community_id: str | None = None


class ThreadIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=1)
    lang: str = "en"


class ReplyIn(BaseModel):
    body: str = Field(min_length=1)
    parent_id: str | None = None
    lang: str = "en"


# --- communities -----------------------------------------------------------

@app.post("/communities", status_code=201, tags=["communities"])
async def create_community(payload: CommunityIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if payload.kind == "paid" and not payload.price_usd:
        raise HTTPException(status_code=400, detail="A paid community needs a price")

    slug = _slugify(payload.name)
    if db.scalar(select(models.Community).where(models.Community.slug == slug)):
        slug = f"{slug}-{new_id('x')[-4:].lower()}"

    community = models.Community(
        id=new_id("cmy"), slug=slug, owner_id=principal.user_id, members_count=1, **payload.model_dump()
    )
    db.add(community)
    db.flush()
    db.add(
        models.Membership(
            community_id=community.id, user_id=principal.user_id, role="owner", status="active"
        )
    )
    db.flush()
    agecommunity.classify_community(db, community)
    db.commit()
    await events.publish("community.created", {"community_id": community.id, "kind": community.kind})
    return {"id": community.id, "slug": community.slug, "kind": community.kind}


@app.get("/communities", tags=["communities"])
def list_communities(
    principal: MaybeUser,
    q: str | None = None,
    country: str | None = None,
    limit: int = 30,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    # Secret communities are never listed — only reachable by direct link for
    # members. Filtering them here rather than in the client is the whole point.
    stmt = select(models.Community).where(models.Community.kind != "secret")
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(models.Community.name).like(like), func.lower(models.Community.description).like(like))
        )
    if country:
        stmt = stmt.where(models.Community.country == country.upper())

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Community.members_count.desc()).limit(min(limit, 100)).offset(offset)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "slug": r.slug,
                "name": r.name,
                "description": r.description,
                "kind": r.kind,
                "price_usd": str(r.price_usd) if r.price_usd else None,
                "members_count": r.members_count,
                "country": r.country,
                "city": r.city,
            }
            for r in rows
        ],
    }


class InviteIn(BaseModel):
    user_id: str


@app.post("/communities/{community_id}/invite", status_code=201, tags=["communities"])
def invite_member(
    community_id: str, payload: InviteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Pull someone into a community — only if they allow it.

    Joining yourself is your own decision; being *added* by someone else is
    theirs, which is what who_can_add_community protects.
    """
    community = db.get(models.Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")

    inviter = db.scalar(
        select(models.Membership).where(
            models.Membership.community_id == community_id,
            models.Membership.user_id == principal.user_id,
            models.Membership.status == "active",
        )
    )
    if inviter is None:
        raise HTTPException(status_code=403, detail="Join the community before inviting others")

    allowed, reason = permissions.check(
        principal.user_id, payload.user_id, "can_add_community", "add this member to a community"
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    existing = db.scalar(
        select(models.Membership).where(
            models.Membership.community_id == community_id,
            models.Membership.user_id == payload.user_id,
        )
    )
    if existing:
        return {"invited": False, "status": existing.status, "already": True}

    db.add(models.Membership(community_id=community_id, user_id=payload.user_id, status="active"))
    community.members_count += 1
    db.commit()
    return {"invited": True, "status": "active"}


@app.post("/communities/{community_id}/join", tags=["communities"])
def join(
    community_id: str,
    principal: CurrentUser,
    invite: str | None = None,
    db: OrmSession = Depends(get_db),
):
    community = db.get(models.Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    if community.kind == "paid":
        raise HTTPException(
            status_code=402,
            detail="This community requires a purchase. Complete checkout in commerce-service first.",
        )

    existing = db.scalar(
        select(models.Membership).where(
            models.Membership.community_id == community_id,
            models.Membership.user_id == principal.user_id,
        )
    )
    if existing:
        if existing.status == "banned":
            raise HTTPException(status_code=403, detail="You are banned from this community")
        return {"joined": True, "status": existing.status, "already": True}

    if community.kind == "secret" and not _invite_matches(community, invite):
        # Without the invite a secret community does not exist, here as on
        # its page. Before this check, knowing the id was enough to walk in.
        raise HTTPException(status_code=404, detail="Community not found")

    # A shared invite to a secret community knocks; it does not open the
    # door. Links get forwarded, and the members decide who comes in.
    status = "pending" if community.kind in ("private", "secret") else "active"
    db.add(models.Membership(community_id=community_id, user_id=principal.user_id, status=status))
    if status == "active":
        community.members_count += 1
    db.commit()

    # A pending request nobody is told about is a request into a void. The
    # owner and every moderator hear it.
    if status == "pending":
        stewards = db.scalars(
            select(models.Membership.user_id).where(
                models.Membership.community_id == community_id,
                models.Membership.role.in_(("owner", "moderator")),
                models.Membership.status == "active",
            )
        ).all()
        notify.notify_many(
            stewards,
            kind="community_request",
            title=f"Someone asked to join {community.name}",
            link=f"/communities?open={community_id}&tab=requests",
        )
    return {"joined": status == "active", "status": status}


@app.post("/communities/{community_id}/leave", tags=["communities"])
def leave(community_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Leave a community.

    A group you can join and not leave is not a group. There was no way out
    short of asking an owner to ban you, which is a humiliating way to express
    "this is not for me".

    The owner cannot leave: the community would be left with nobody answerable
    for it, and the honest options are handing it to somebody else or deleting
    it, neither of which this pretends to do. Leaving also withdraws a pending
    request, because changing your mind before the answer comes is the same
    act.
    """
    community = db.get(models.Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    membership = _membership(db, community_id, principal.user_id)
    if membership is None:
        return {"left": True, "already": True}
    if membership.role == "owner":
        raise HTTPException(
            status_code=403,
            detail="The owner cannot leave. Hand the community to someone else first.",
        )
    # A ban is not a membership to resign from - letting someone leave would
    # clear the row and let them walk back in.
    if membership.status == "banned":
        raise HTTPException(status_code=403, detail="You are banned from this community")
    if membership.status == "active":
        community.members_count = max(0, community.members_count - 1)
    db.delete(membership)
    db.commit()
    return {"left": True}


# --- governance ------------------------------------------------------------
#
# "Real governance" was a promise with no mechanism: a private community turned
# every request into a `pending` row and offered nobody a way to answer it, so
# joining a private community was a request into a void. These are the answers.


def _membership(db: OrmSession, community_id: str, user_id: str) -> models.Membership | None:
    return db.scalar(
        select(models.Membership).where(
            models.Membership.community_id == community_id,
            models.Membership.user_id == user_id,
        )
    )


def _require_steward(db: OrmSession, community_id: str, user_id: str) -> models.Community:
    """Only an owner or moderator may act on the membership of others."""
    community = db.get(models.Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    membership = _membership(db, community_id, user_id)
    if membership is None or membership.role not in ("owner", "moderator") or membership.status != "active":
        raise HTTPException(status_code=403, detail="Only the owner or a moderator can do that")
    return community


MAX_FORUM_DEPTH = 8


def _forum_chain(db: OrmSession, forum: models.Forum) -> list[models.Forum] | None:
    """The forum and its ancestors, nearest first.

    None when the chain is broken: a parent that no longer exists, a cycle, or
    nesting deeper than any real forum. The caller treats that as closed.
    """
    chain, seen = [forum], {forum.id}
    while chain[-1].parent_id:
        parent = db.get(models.Forum, chain[-1].parent_id)
        if parent is None or parent.id in seen or len(chain) >= MAX_FORUM_DEPTH:
            return None
        chain.append(parent)
        seen.add(parent.id)
    return chain


def _forum_access(db: OrmSession, forum: models.Forum, principal) -> None:
    """Raise unless `principal` may enter `forum`.

    A sub-forum has its parent's door: every community along the chain up to the
    root must admit the viewer. A forum with no community anywhere on its chain
    is open. Reading the whole chain, rather than trusting the child's own
    `community_id`, is what stops a sub-forum with no community of its own from
    sitting open under a secret parent.
    """
    chain = _forum_chain(db, forum)
    if chain is None:
        # A broken chain is a data fault. Fail closed.
        raise HTTPException(status_code=404, detail="Forum not found")
    for community_id in {f.community_id for f in chain if f.community_id}:
        _community_door(db, community_id, principal)


def _community_door(db: OrmSession, community_id: str, principal) -> None:
    """Raise unless `principal` may enter a community's forums.

    Public stays open, private and paid are for active members, secret answers
    404 to everyone else — the same rule `get_community` applies, for the same
    reason: a 403 would confirm that the forum exists.

    Banned members lose even a public community's forums. Without that, a ban
    only removed the member from a list and left every thread open to them.
    """
    community = db.get(models.Community, community_id)
    if community is None:
        # A forum pointing at nothing is a data fault. Fail closed.
        raise HTTPException(status_code=404, detail="Forum not found")
    membership = _membership(db, community.id, principal.user_id) if principal else None
    active = membership is not None and membership.status == "active"
    banned = membership is not None and membership.status == "banned"
    if active or (community.kind == "public" and not banned):
        return
    if community.kind == "secret":
        raise HTTPException(status_code=404, detail="Forum not found")
    raise HTTPException(status_code=403, detail="This forum is for members of its community")


def _get_forum_or_404(db: OrmSession, forum_id: str) -> models.Forum:
    forum = db.get(models.Forum, forum_id)
    if forum is None:
        raise HTTPException(status_code=404, detail="Forum not found")
    return forum


def _thread_for(db: OrmSession, thread_id: str, principal) -> models.Thread:
    """Load a thread the viewer is allowed to reach, or 404."""
    thread = db.get(models.Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    forum = db.get(models.Forum, thread.forum_id)
    if forum is None or thread.status != "open":
        # No forum is a data fault, and a thread that is not open (removed or
        # held back) is not served by id either. Both fail closed.
        raise HTTPException(status_code=404, detail="Thread not found")
    try:
        _forum_access(db, forum, principal)
    except HTTPException as exc:
        # Whatever the reason, a thread that is out of reach does not exist.
        raise HTTPException(status_code=404, detail="Thread not found") from exc
    return thread


def _can_enter(db: OrmSession, forum: models.Forum, principal) -> bool:
    try:
        _forum_access(db, forum, principal)
    except HTTPException:
        return False
    return True


def _can_enter_or_listed(db: OrmSession, forum: models.Forum, principal) -> bool:
    """Whether a forum appears in the plain listing.

    Looser than entering on purpose: a private community's forum is listed so
    a member of the public can see it exists and ask to join, as the community
    itself is. A secret one, or a child of one, is not.
    """
    chain = _forum_chain(db, forum)
    if chain is None:
        return False
    for community_id in {f.community_id for f in chain if f.community_id}:
        community = db.get(models.Community, community_id)
        if community is None:
            return False
        if community.kind != "secret":
            continue
        membership = _membership(db, community_id, principal.user_id) if principal else None
        if membership is None or membership.status != "active":
            return False
    return True


def _visible_replies(db: OrmSession, age, replies: list[models.Reply]) -> list[models.Reply]:
    """Replies a viewer of this age may read. Rated one by one: an unsuitable
    answer removes that answer, not the whole discussion."""
    if not age.is_minor:
        return replies
    from common.agesafety import engine as _engine
    ratings = agecommunity.classifications_for(db, [r.id for r in replies])
    return [r for r in replies if _engine.can_view_content(age, ratings.get(r.id)).allowed]


def _visible_threads(db: OrmSession, age, threads: list[models.Thread]) -> list[models.Thread]:
    """Threads a viewer of this age may read, by the same engine `get_thread` uses.

    `restrict_query` is a coarser filter pushed into SQL; it is right for paging
    but looser than the engine (it ignores category ceilings and unknown ages),
    so anything that shows a thread's text re-checks with this.
    """
    if not age.is_minor:
        return threads
    from common.agesafety import engine as _engine
    ratings = agecommunity.classifications_for(db, [t.id for t in threads])
    return [t for t in threads if _engine.can_view_content(age, ratings.get(t.id)).allowed]


@app.get("/communities/{community_id}", tags=["communities"])
def get_community(
    community_id: str,
    principal: MaybeUser,
    invite: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """One community, by id or by slug.

    A secret community is reachable by direct link *for its members only* —
    that is what makes it secret rather than merely unlisted. Everyone else
    gets a 404 rather than a 403: confirming that an id exists would leak the
    very fact the tier is meant to hide.
    """
    community = _resolve(db, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")

    membership = _membership(db, community.id, principal.user_id) if principal else None
    if (
        community.kind == "secret"
        and (membership is None or membership.status not in ("active", "pending"))
        and not _invite_matches(community, invite)
    ):
        raise HTTPException(status_code=404, detail="Community not found")

    return {
        "id": community.id,
        "slug": community.slug,
        "name": community.name,
        "description": community.description,
        "kind": community.kind,
        "price_usd": str(community.price_usd) if community.price_usd is not None else None,
        "country": community.country,
        "city": community.city,
        "members_count": community.members_count,
        "avatar_url": community.avatar_url,
        "my_role": membership.role if membership else None,
        "my_status": membership.status if membership else None,
    }


@app.get("/communities/{community_id}/members", tags=["governance"])
def list_members(
    community_id: str,
    principal: CurrentUser,
    status: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """The roll, and the queue waiting on it. Stewards only."""
    _require_steward(db, community_id, principal.user_id)
    stmt = select(models.Membership).where(models.Membership.community_id == community_id)
    if status:
        stmt = stmt.where(models.Membership.status == status)
    rows = db.scalars(stmt.order_by(models.Membership.joined_at.desc())).all()

    profiles = _profiles({r.user_id for r in rows})
    return {
        "items": [
            {
                "user_id": r.user_id,
                "role": r.role,
                "status": r.status,
                "joined_at": r.joined_at,
                "profile": profiles.get(r.user_id),
            }
            for r in rows
        ]
    }


class MemberActionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|ban|unban|promote|demote)$")


@app.post("/communities/{community_id}/members/{user_id}", tags=["governance"])
def act_on_member(
    community_id: str,
    user_id: str,
    payload: MemberActionIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Approve, reject, ban or change the role of one member."""
    community = _require_steward(db, community_id, principal.user_id)
    membership = _membership(db, community_id, user_id)
    if membership is None:
        raise HTTPException(status_code=404, detail="That person is not in this community")

    # The owner is not answerable to a moderator, and not to themselves either.
    if membership.role == "owner":
        raise HTTPException(status_code=403, detail="The owner cannot be acted on")

    action = payload.action
    if action == "approve":
        if membership.status != "pending":
            raise HTTPException(status_code=400, detail="That request is not pending")
        membership.status = "active"
        community.members_count += 1
    elif action == "reject":
        db.delete(membership)
    elif action == "ban":
        if membership.status == "active":
            community.members_count = max(0, community.members_count - 1)
        membership.status = "banned"
    elif action == "unban":
        membership.status = "active"
        community.members_count += 1
    elif action == "promote":
        membership.role = "moderator"
    elif action == "demote":
        membership.role = "member"

    db.commit()

    if action == "approve":
        notify.notify(
            user_id,
            kind="community_approved",
            title=f"You were admitted to {community.name}",
            link=f"/communities?open={community_id}",
        )
    return {"ok": True, "action": action}


# --- privacy and sharing ----------------------------------------------------


def _invite_matches(community: models.Community, invite: str | None) -> bool:
    if not invite or not community.invite_code:
        return False
    return secrets.compare_digest(invite, community.invite_code)


class PrivacyIn(BaseModel):
    # Paid is not on the list: it is a price, not a privacy setting, and
    # turning it on or off would change what members already paid for.
    kind: str = Field(pattern="^(public|private|secret)$")


@app.patch("/communities/{community_id}/privacy", tags=["governance"])
def set_privacy(
    community_id: str, payload: PrivacyIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Make a community public, private or secret. Owner and moderators.

    Opening a private community answers everybody who was waiting at its door:
    the door is open now, so they are in, and they are told. Closing a public
    one keeps the members it has - nobody is put out for a setting they did
    not choose.
    """
    community = _require_steward(db, community_id, principal.user_id)
    if community.kind == "paid":
        raise HTTPException(
            status_code=409,
            detail="A paid community stays paid: its members bought their place.",
        )
    before = community.kind
    if before == payload.kind:
        return {"kind": before, "changed": False, "admitted": 0}

    admitted: list[str] = []
    if payload.kind == "public":
        waiting = db.scalars(
            select(models.Membership).where(
                models.Membership.community_id == community.id,
                models.Membership.status == "pending",
            )
        ).all()
        for row in waiting:
            row.status = "active"
            admitted.append(row.user_id)
        community.members_count += len(admitted)

    community.kind = payload.kind
    db.commit()

    if admitted:
        notify.notify_many(
            admitted,
            kind="community_approved",
            title=f"{community.name} is public now - you are in",
            link=f"/communities/{community.slug}",
        )
    _live(f"community:{community.id}", "community.privacy", kind=community.kind)
    return {"kind": community.kind, "changed": True, "admitted": len(admitted)}


@app.post("/communities/{community_id}/share-link", tags=["communities"])
def share_link(community_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """The address to send somebody, whatever kind of community it is.

    Public and private communities already open from their plain address - a
    private one shows its name and a way to ask in. A secret one answers 404
    to anybody outside it, so its link carries an invite that lets the page
    open and the request be made. Only a member can get that invite: handing
    it to whoever asks would make the community merely unlisted.
    """
    community = _resolve(db, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    path = f"/communities/{community.slug}"
    if community.kind != "secret":
        return {"path": path, "invite": None}

    membership = _membership(db, community.id, principal.user_id) if principal else None
    if membership is None or membership.status != "active":
        raise HTTPException(status_code=404, detail="Community not found")
    if not community.invite_code:
        community.invite_code = secrets.token_urlsafe(12)
        db.commit()
    # `access`, not `invite`: the app already reads `?invite=` as a referral
    # code, and the two must not be mistaken for each other.
    return {"path": f"{path}?access={community.invite_code}", "invite": community.invite_code}


# --- forums ----------------------------------------------------------------

@app.post("/forums", status_code=201, tags=["forums"])
def create_forum(payload: ForumIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if payload.hierarchy == "geographic" and payload.scope not in GEO_SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of: {', '.join(GEO_SCOPES)}")

    data = payload.model_dump()

    if payload.parent_id:
        parent = db.get(models.Forum, payload.parent_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent forum not found")
        # Whoever cannot enter the parent cannot add to it, and is told no more
        # than a reader would be: a secret parent is a 404.
        _forum_access(db, parent, principal)
        chain = _forum_chain(db, parent)
        if chain is None or len(chain) >= MAX_FORUM_DEPTH:
            raise HTTPException(status_code=400, detail="Forums cannot be nested that deeply")
        inherited = next((f.community_id for f in chain if f.community_id), None)
        # Rights first, consistency second: someone with no say in the named
        # community is refused as such, not told how it differs from the parent.
        if payload.community_id:
            _require_steward(db, payload.community_id, principal.user_id)
        if payload.community_id and payload.community_id != inherited:
            raise HTTPException(
                status_code=400,
                detail="A sub-forum belongs to the same community as its parent",
            )
        # Written down as well as inherited at read time, so the plain
        # community_id filters in the listings stay true for new rows.
        data["community_id"] = inherited

    # A forum may only be attached to a community by someone who runs it.
    # Without this, anyone could hang a forum on somebody else's community.
    if data["community_id"]:
        _require_steward(db, data["community_id"], principal.user_id)

    slug = _slugify(payload.name)
    if db.scalar(select(models.Forum).where(models.Forum.slug == slug)):
        slug = f"{slug}-{new_id('x')[-4:].lower()}"
    forum = models.Forum(id=new_id("frm"), slug=slug, **data)
    db.add(forum)
    db.commit()
    return {"id": forum.id, "slug": forum.slug}


@app.get("/discover", tags=["discovery"])
def suggestions(principal: MaybeUser, limit: int = 4, db: OrmSession = Depends(get_db)):
    """Communities and forums the member has not joined yet.

    Secret communities never appear here — they are reachable by direct link
    only, and a suggestion list is exactly the leak that would undo that.
    """
    joined: set[str] = set()
    if principal:
        joined = set(
            db.scalars(
                select(models.Membership.community_id).where(models.Membership.user_id == principal.user_id)
            ).all()
        )

    communities = db.scalars(
        select(models.Community)
        .where(
            models.Community.kind != "secret",
            models.Community.id.not_in(joined) if joined else True,
        )
        .order_by(models.Community.members_count.desc(), models.Community.created_at.desc())
        .limit(min(limit, 10))
    ).all()

    # A suggestion is an invitation, so only forums anyone may enter are offered:
    # free-standing ones, those of public communities, and the viewer's own.
    open_door = or_(
        models.Forum.community_id.is_(None),
        models.Forum.community_id.in_(
            select(models.Community.id).where(models.Community.kind == "public")
        ),
    )
    if principal:
        open_door = or_(
            open_door,
            models.Forum.community_id.in_(
                select(models.Membership.community_id).where(
                    models.Membership.user_id == principal.user_id,
                    models.Membership.status == "active",
                )
            ),
        )
    forums = db.scalars(
        select(models.Forum)
        .where(open_door)
        .order_by(models.Forum.threads_count.desc(), models.Forum.created_at.desc())
        .limit(min(limit, 10) * 3)
    ).all()
    # Over-fetched, then judged on the whole chain: a child of a private or
    # secret forum, or a forum from a community the viewer is banned from, is
    # not an invitation they can accept.
    forums = [f for f in forums if _can_enter(db, f, principal)][: min(limit, 10)]

    # A minor is not shown a community whose whole subject is adult. Judged on
    # what the community says about itself, not on any one message inside it:
    # a door is adult because of what it advertises, and one heated thread in a
    # gardening group does not make the group adult.
    age = agecommunity.viewer(principal.user_id if principal else None)
    communities = [c for c in communities if agecommunity.community_is_listable(db, age, c)]

    return {
        "reason": "most_active",
        "communities": [
            {
                "id": c.id,
                "slug": c.slug,
                "name": c.name,
                "description": c.description,
                "kind": c.kind,
                "members_count": c.members_count,
                "country": c.country,
            }
            for c in communities
        ],
        "forums": [
            {
                "id": f.id,
                "slug": f.slug,
                "name": f.name,
                "hierarchy": f.hierarchy,
                "scope": f.scope,
                "threads_count": f.threads_count,
            }
            for f in forums
        ],
    }


@app.get("/forums", tags=["forums"])
def list_forums(
    principal: MaybeUser,
    hierarchy: str | None = None,
    parent_id: str | None = None,
    scope: str | None = None,
    db: OrmSession = Depends(get_db),
):
    stmt = select(models.Forum)
    # A secret community's forum is not listed to outsiders, only to its members.
    secret = select(models.Community.id).where(models.Community.kind == "secret")
    if principal:
        mine = select(models.Membership.community_id).where(
            models.Membership.user_id == principal.user_id,
            models.Membership.status == "active",
        )
        stmt = stmt.where(
            or_(
                models.Forum.community_id.is_(None),
                models.Forum.community_id.not_in(secret),
                models.Forum.community_id.in_(mine),
            )
        )
    else:
        stmt = stmt.where(
            or_(models.Forum.community_id.is_(None), models.Forum.community_id.not_in(secret))
        )
    if hierarchy:
        stmt = stmt.where(models.Forum.hierarchy == hierarchy)
    if parent_id:
        stmt = stmt.where(models.Forum.parent_id == parent_id)
    elif parent_id is None and scope is None:
        stmt = stmt.where(models.Forum.parent_id.is_(None))
    if scope:
        stmt = stmt.where(models.Forum.scope == scope)

    rows = db.scalars(stmt.order_by(models.Forum.threads_count.desc())).all()
    # The query above only knows each row's own community. A sub-forum's door
    # is its parent's, so the chain is walked here: a child of a secret forum
    # is not listed to someone who could not open the parent.
    rows = [r for r in rows if _can_enter_or_listed(db, r, principal)]
    return {
        "geo_scopes": GEO_SCOPES,
        "items": [
            {
                "id": r.id,
                "slug": r.slug,
                "name": r.name,
                "hierarchy": r.hierarchy,
                "scope": r.scope,
                "scope_value": r.scope_value,
                "parent_id": r.parent_id,
                "threads_count": r.threads_count,
            }
            for r in rows
        ],
    }


@app.post("/forums/{forum_id}/threads", status_code=201, tags=["forums"])
async def create_thread(
    forum_id: str, payload: ThreadIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)

    thread = models.Thread(
        id=new_id("thr"), forum_id=forum_id, author_id=principal.user_id, **payload.model_dump()
    )
    db.add(thread)
    db.flush()

    # Classified before it is committed. A thread that appears first and is
    # rated a moment later was readable by everybody for that moment.
    verdict = agecommunity.classify_and_store(
        db, thread.id, "thread", f"{thread.title} {thread.body}", principal.user_id
    )
    if verdict.block_publication:
        db.rollback()
        if verdict.escalate_child_safety:
            log.error("child-safety escalation on a thread by %s", principal.user_id)
        raise HTTPException(
            status_code=403,
            detail="This cannot be published. If you believe this is a mistake, contact support.",
        )

    forum.threads_count += 1
    db.commit()

    # ai-service picks this up to suggest duplicates and a summary.
    await events.publish(
        "forum.thread_created",
        {"thread_id": thread.id, "forum_id": forum_id, "title": thread.title, "lang": thread.lang},
    )
    return {"id": thread.id, "created_at": thread.created_at}


def _accepted_replies():
    """Every accepted reply still on show, before any viewer's age is applied."""
    return select(models.Reply).where(
        models.Reply.accepted_answer.is_(True), models.Reply.status == "published"
    )


def _solved_threads(db: OrmSession, age, *, among: list[str] | None = None, forum_id: str | None = None) -> set[str]:
    """Threads whose accepted answer this viewer is allowed to read.

    "Solved" is a statement about a reply, so it is only true for a viewer who
    can see that reply. Otherwise a minor would learn that a reply they cannot
    read exists, and what it is: the answer.
    """
    stmt = _accepted_replies()
    if among is not None:
        if not among:
            return set()
        stmt = stmt.where(models.Reply.thread_id.in_(among))
    if forum_id is not None:
        stmt = stmt.join(models.Thread, models.Thread.id == models.Reply.thread_id).where(
            models.Thread.forum_id == forum_id
        )
    replies = _visible_replies(db, age, list(db.scalars(stmt)))
    return {r.thread_id for r in replies}


def _solved_filter(db: OrmSession, age, forum_id: str):
    """What `sort=solved` matches against. Adults: a subquery, nothing loaded.
    Minors: the threads whose accepted answer they may read, which needs the ratings."""
    if not age.is_minor:
        return _accepted_replies().with_only_columns(models.Reply.thread_id)
    return _solved_threads(db, age, forum_id=forum_id)


def _thread_card(row: models.Thread, profiles: dict[str, dict], solved: set[str]) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "author_id": row.author_id,
        "author": profiles.get(row.author_id),
        "solved": row.id in solved,
        "replies_count": row.replies_count,
        "views_count": row.views_count,
        "pinned": row.pinned,
        "ai_summary": row.ai_summary,
        "duplicate_of": row.duplicate_of,
        "last_activity_at": row.last_activity_at,
        "created_at": row.created_at,
    }


def _thread_order(sort: str) -> list:
    """Pinned threads stay on top whatever the sort."""
    if sort == "trending":
        cutoff = datetime.now(timezone.utc) - TRENDING_WINDOW
        return [
            models.Thread.pinned.desc(),
            case((models.Thread.last_activity_at >= cutoff, 1), else_=0).desc(),
            models.Thread.replies_count.desc(),
            models.Thread.last_activity_at.desc(),
            models.Thread.id.desc(),
        ]
    # `id` last: threads created in the same instant must not swap places
    # between two pages of the same listing.
    return [models.Thread.pinned.desc(), models.Thread.last_activity_at.desc(), models.Thread.id.desc()]


@app.get("/forums/{forum_id}/threads", tags=["forums"])
def list_threads(
    forum_id: str,
    principal: MaybeUser,
    limit: int = 30,
    offset: int = 0,
    sort: ThreadSort = "recent",
    db: OrmSession = Depends(get_db),
):
    """Threads in a forum, age-filtered before ordering and paging.

    `sort` narrows or reorders what the viewer may already see; it never widens
    it. The age filter and the forum's door both run before it.

    This endpoint took no viewer at all until now, which is why it needed
    fixing: a surface with no idea who is asking cannot decide what they may
    see, and the answer it gave was the same for a 13-year-old and an adult.
    """
    # Unknown and out-of-reach forums answer the same 404, so the answer cannot
    # be used to tell a secret forum from one that was never created.
    _forum_access(db, _get_forum_or_404(db, forum_id), principal)
    limit, offset = min(max(limit, 1), 100), max(offset, 0)

    age = agecommunity.viewer(principal.user_id if principal else None)
    stmt = select(models.Thread).where(
        models.Thread.forum_id == forum_id, models.Thread.status == "open"
    )
    stmt = agecommunity.restrict_query(stmt, models.Thread, age)
    if sort == "solved":
        stmt = stmt.where(models.Thread.id.in_(_solved_filter(db, age, forum_id)))
    elif sort == "unanswered":
        stmt = stmt.where(models.Thread.replies_count == 0)
    rows = agecommunity.readable_page(db, stmt.order_by(*_thread_order(sort)), age, limit=limit, offset=offset)
    profiles = _profiles({r.author_id for r in rows})
    solved = _solved_threads(db, age, among=[r.id for r in rows])
    return {"items": [_thread_card(r, profiles, solved) for r in rows]}


@app.get("/threads/{thread_id}", tags=["forums"])
def get_thread(thread_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    thread = _thread_for(db, thread_id, principal)

    # A direct link bypasses the listing entirely, so the gate runs here too.
    # 404 rather than 403: confirming a thread exists but is out of reach tells
    # somebody which links are worth passing to a minor.
    age = agecommunity.viewer(principal.user_id if principal else None)
    if not agecommunity.visible(db, age, thread.id):
        raise HTTPException(status_code=404, detail="Thread not found")

    thread.views_count += 1
    replies = db.scalars(
        select(models.Reply)
        .where(models.Reply.thread_id == thread_id, models.Reply.status == "published")
        .order_by(models.Reply.accepted_answer.desc(), models.Reply.created_at)
    ).all()
    # Replies are separately rated: one unsuitable answer in an otherwise fine
    # thread should remove that answer, not the whole discussion.
    replies = _visible_replies(db, age, replies)
    profiles = _profiles({thread.author_id} | {r.author_id for r in replies})
    voted = _voted_by(db, principal.user_id if principal else None, [r.id for r in replies])
    db.commit()
    return {
        "id": thread.id,
        "forum_id": thread.forum_id,
        "title": thread.title,
        "body": thread.body,
        "author_id": thread.author_id,
        "author": profiles.get(thread.author_id),
        "lang": thread.lang,
        "ai_summary": thread.ai_summary,
        "locked": thread.locked,
        "created_at": thread.created_at,
        "replies": [
            {
                "id": r.id,
                "author_id": r.author_id,
                "author": profiles.get(r.author_id),
                "parent_id": r.parent_id,
                "body": r.body,
                "upvotes": r.upvotes,
                "voted_by_me": r.id in voted,
                "accepted_answer": r.accepted_answer,
                "created_at": r.created_at,
            }
            for r in replies
        ],
    }


@app.post("/threads/{thread_id}/replies", status_code=201, tags=["forums"])
def add_reply(thread_id: str, payload: ReplyIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    thread = _thread_for(db, thread_id, principal)
    if thread.locked:
        raise HTTPException(status_code=403, detail="This thread is locked")

    # A reply may only answer a reply in the same thread. Otherwise a member of
    # one thread could aim a reply at an id from another, and the author of that
    # other reply would be notified with this body.
    if payload.parent_id:
        target = db.get(models.Reply, payload.parent_id)
        if target is None or target.thread_id != thread_id:
            raise HTTPException(status_code=400, detail="That reply is not in this thread")

    reply = models.Reply(
        id=new_id("rpl"), thread_id=thread_id, author_id=principal.user_id, **payload.model_dump()
    )
    db.add(reply)
    db.flush()

    verdict = agecommunity.classify_and_store(
        db, reply.id, "reply", reply.body, principal.user_id
    )
    if verdict.block_publication:
        db.rollback()
        if verdict.escalate_child_safety:
            log.error("child-safety escalation on a reply by %s", principal.user_id)
        raise HTTPException(
            status_code=403,
            detail="This cannot be published. If you believe this is a mistake, contact support.",
        )

    thread.replies_count += 1
    thread.last_activity_at = datetime.now(timezone.utc)
    db.commit()

    # Live, on the thread anyone reading it is subscribed to. A forum where a
    # reply only appears on refresh is a mailing list with extra steps.
    _live(
        f"thread:{thread_id}",
        "reply",
        thread_id=thread_id,
        reply_id=reply.id,
        author_id=principal.user_id,
        parent_id=reply.parent_id,
        replies_count=thread.replies_count,
    )

    # The thread's author, and the person being replied to, hear about it even
    # when they are not looking at the page.
    parent = db.get(models.Reply, reply.parent_id) if reply.parent_id else None
    targets = {thread.author_id, parent.author_id if parent else None} - {principal.user_id, None}
    # Named, not "somebody": whose reply it is decides whether you go and read
    # it, and the name costs one lookup for the whole fan-out.
    author = (
        (_profiles({principal.user_id}).get(principal.user_id) or {}).get("display_name")
        if targets
        else None
    )
    for target in targets:
        notify.notify(
            target,
            kind="forum_reply",
            title=f"{author} replied in a thread you are in"
            if author
            else "New reply in a thread you are in",
            body=payload.body[:140],
            link=f"/forums?thread={thread_id}",
        )
    return {"id": reply.id}


def _voted_by(db: OrmSession, user_id: str | None, reply_ids: list[str]) -> set[str]:
    """Which of these replies this member has upvoted. One query, not one per reply."""
    if not user_id or not reply_ids:
        return set()
    return set(
        db.scalars(
            select(models.ReplyVote.reply_id).where(
                models.ReplyVote.user_id == user_id, models.ReplyVote.reply_id.in_(reply_ids)
            )
        )
    )


def _reply_in_reach(db: OrmSession, reply_id: str, principal) -> tuple[models.Reply, models.Thread]:
    """A reply the member could read right now, with its thread, or 404.

    Voting and accepting act on content, so they follow the same gates as
    reading it: the forum's door, the thread's rating and the reply's own
    rating. Anything out of reach is a 404, the same as an id that never
    existed, so these endpoints cannot be used to probe for hidden replies.
    A locked thread refuses both, with a 403 once it is known to be readable.
    """
    reply = db.get(models.Reply, reply_id)
    if reply is None or reply.status != "published":
        raise HTTPException(status_code=404, detail="Reply not found")
    try:
        thread = _thread_for(db, reply.thread_id, principal)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail="Reply not found") from exc
    age = agecommunity.viewer(principal.user_id)
    if not agecommunity.visible(db, age, thread.id) or not _visible_replies(db, age, [reply]):
        raise HTTPException(status_code=404, detail="Reply not found")
    # Locked means frozen: no new replies, and no change to which reply ranks
    # first or is the answer. Said only once the viewer is known to be able to
    # read the thread, so it tells nobody anything they could not already see.
    if thread.locked:
        raise HTTPException(status_code=403, detail="This thread is locked")
    return reply, thread


def _recount_votes(db: OrmSession, reply: models.Reply) -> int:
    """Rebuild the cached count from the votes themselves."""
    reply.upvotes = db.scalar(
        select(func.count()).select_from(models.ReplyVote).where(models.ReplyVote.reply_id == reply.id)
    ) or 0
    return reply.upvotes


@app.post("/replies/{reply_id}/upvote", tags=["forums"])
def toggle_upvote(reply_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Upvote a reply, or take the vote back if the member already gave one.

    One vote per member, and never on your own reply: a vote you give yourself
    says nothing to the people deciding which answer to trust.
    """
    reply, thread = _reply_in_reach(db, reply_id, principal)
    if reply.author_id == principal.user_id:
        raise HTTPException(status_code=403, detail="You cannot vote on your own reply")

    # Votes on one reply take turns. Without the lock two members voting at the
    # same moment each count the other's vote as missing, and the cached total
    # ends one short until somebody votes again.
    db.scalar(select(models.Reply.id).where(models.Reply.id == reply.id).with_for_update())

    existing = db.scalar(
        select(models.ReplyVote).where(
            models.ReplyVote.reply_id == reply.id, models.ReplyVote.user_id == principal.user_id
        )
    )
    if existing is not None:
        db.delete(existing)
    else:
        db.add(models.ReplyVote(reply_id=reply.id, user_id=principal.user_id))
    try:
        db.flush()
    except (IntegrityError, StaleDataError):
        # A double tap: the other request got there first. Report what is true
        # now rather than failing the member for being quick.
        db.rollback()
    voted = reply.id in _voted_by(db, principal.user_id, [reply.id])
    upvotes = _recount_votes(db, reply)
    db.commit()

    _live(
        f"thread:{thread.id}",
        "reply_voted",
        thread_id=thread.id,
        reply_id=reply.id,
        upvotes=upvotes,
    )
    return {"upvotes": upvotes, "voted": voted}


@app.post("/replies/{reply_id}/accept", tags=["forums"])
def accept_reply(reply_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Mark a reply as the answer. Only whoever asked the question can.

    One answer per thread: accepting another moves the mark. Accepting the one
    already accepted changes nothing and tells nobody again.
    """
    reply, thread = _reply_in_reach(db, reply_id, principal)
    if thread.author_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the author of the thread can accept an answer")
    # One accept at a time per thread, so two quick taps cannot leave two answers.
    db.scalar(select(models.Thread.id).where(models.Thread.id == thread.id).with_for_update())
    db.refresh(reply)
    if reply.accepted_answer:
        return {"accepted": True, "reply_id": reply.id}

    db.execute(
        update(models.Reply)
        .where(models.Reply.thread_id == thread.id, models.Reply.accepted_answer.is_(True))
        .values(accepted_answer=False)
    )
    reply.accepted_answer = True
    db.commit()

    _live(f"thread:{thread.id}", "answer_accepted", thread_id=thread.id, reply_id=reply.id)
    if reply.author_id != principal.user_id:
        notify.notify(
            reply.author_id,
            kind="forum_answer_accepted",
            title="Your reply was accepted as the answer",
            # No thread title: the recipient may have lost access to this thread
            # since replying, and a notification would outlive that.
            link=f"/forums?thread={thread.id}",
        )
    return {"accepted": True, "reply_id": reply.id}


@app.get("/threads/{thread_id}/summary", tags=["forums"])
def thread_summary(
    thread_id: str, principal: MaybeUser, lang: str = "en", db: OrmSession = Depends(get_db)
):
    """Digest a long thread.

    The blueprint's promise is "two honest lines", and *honest* is the load-
    bearing word: the summary is generated from the replies themselves and
    carries where it came from, so a reader can tell a machine wrote it and how
    many posts it stands on.

    Short threads are refused rather than summarised. A digest of six replies is
    a worse version of scrolling, and pretending otherwise would put a
    machine-written paragraph in front of text nobody needed help with.
    """
    thread = _thread_for(db, thread_id, principal)

    # The summary is built from the text itself, so it must obey the same gates
    # as reading the thread: a digest of a thread you cannot open is a way to
    # open it.
    age = agecommunity.viewer(principal.user_id if principal else None)
    if not agecommunity.visible(db, age, thread.id):
        raise HTTPException(status_code=404, detail="Thread not found")

    replies = _visible_replies(
        db,
        age,
        db.scalars(
            select(models.Reply)
            .where(models.Reply.thread_id == thread_id, models.Reply.status == "published")
            .order_by(models.Reply.created_at)
        ).all(),
    )

    if len(replies) < SUMMARY_MIN_REPLIES:
        return {
            "summarised": False,
            "reason": f"only {len(replies)} replies — short enough to read",
            "replies_counted": len(replies),
        }

    # A bounded window: the opening frames the question, the tail carries where
    # it landed, and the middle of a hundred-post thread is mostly repetition.
    excerpt = "\n".join(
        [f"{thread.title}"]
        + [r.body[:400] for r in replies[:15]]
        + (["…"] + [r.body[:400] for r in replies[-10:]] if len(replies) > 25 else [])
    )

    try:
        response = httpx.post(
            f"{AI_URL}/ai/run",
            json={
                "task": "summarize",  # the gateway's spelling, not ours
                "prompt": (
                    "Summarise this forum discussion in at most two sentences. "
                    "State what was asked and what was concluded. If no conclusion "
                    "was reached, say so rather than inventing one." + chr(10) * 2 + excerpt
                ),
                "lang": lang,
            },
            timeout=25,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        # The upstream reason travels with the error. A flat "unavailable"
        # hid a plain spelling mistake in the task name behind what looked
        # like an outage.
        detail = "The summariser is unavailable right now"
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                detail = exc.response.json().get("detail", detail)
            except Exception:
                pass
        log.warning("thread summary failed for %s: %s", thread_id, exc)
        raise HTTPException(status_code=503, detail=detail)

    return {
        "summarised": True,
        "summary": result.get("output") or result.get("text"),
        "replies_counted": len(replies),
        # Named so the reader can weigh it. A mock provider says so here rather
        # than passing its output off as a real model's.
        "provider": result.get("provider"),
        "model": result.get("model"),
        "mock": bool(result.get("mock")),
        "provenance": "ai_generated",
    }


# --- knowledge base --------------------------------------------------------

def _like(term: str) -> str:
    """A LIKE pattern that matches the term as typed: `%` and `_` are not wildcards here."""
    escaped = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _curated_entries(db: OrmSession, forum_id: str, age, q: str | None, limit: int) -> list[dict]:
    stmt = select(models.KnowledgeEntry).where(models.KnowledgeEntry.forum_id == forum_id)
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            or_(
                func.lower(models.KnowledgeEntry.question).like(pattern, escape="\\"),
                func.lower(models.KnowledgeEntry.answer).like(pattern, escape="\\"),
            )
        )
    rows = db.scalars(
        stmt.order_by(models.KnowledgeEntry.confidence.desc(), models.KnowledgeEntry.id)
        .limit(KNOWLEDGE_BATCH * KNOWLEDGE_MAX_BATCHES)
    ).all()
    source_ids = {s for r in rows for s in r.source_thread_ids.split(",") if s}
    sources = {
        th.id: th
        for th in db.scalars(select(models.Thread).where(models.Thread.id.in_(source_ids))).all()
        if th.status == "open" and th.forum_id == forum_id
    } if source_ids else {}
    readable = {th.id for th in _visible_threads(db, age, list(sources.values()))}
    entries = []
    for r in rows:
        cited = [s for s in r.source_thread_ids.split(",") if s]
        # An entry quotes the threads it was distilled from. It is only as
        # readable as all of them, and an entry that cites nothing cannot be
        # shown to anyone who needs the age check.
        if age.is_minor:
            shown = bool(cited) and all(s in readable for s in cited)
        else:
            shown = all(s in sources for s in cited)
        if not shown:
            continue
        entries.append(
            {
                "id": r.id,
                "origin": "curated",
                "question": r.question,
                "answer": r.answer,
                "lang": r.lang,
                "confidence": float(r.confidence),
                "votes": 0,
                "sources": cited,
                "source_reply_id": None,
                "reviewed": r.reviewed_by is not None,
            }
        )
        if len(entries) == limit:
            break
    return entries


def _community_entry(reply: models.Reply, thread: models.Thread) -> dict:
    return {
        "id": reply.id,
        "origin": "community",
        "question": thread.title,
        "answer": reply.body,
        "lang": thread.lang,
        "confidence": None,
        "votes": reply.upvotes,
        "sources": [thread.id],
        "source_reply_id": reply.id,
        "reviewed": False,
    }


def _community_entries(db: OrmSession, forum_id: str, age, q: str | None, limit: int) -> list[dict]:
    """Answers the asker accepted and enough members upvoted, as read-time entries.

    Nothing is copied or rewritten: the entry is the thread's title and the
    reply's own words, so it cannot say anything the community did not. Both
    the thread and the reply must be readable by the viewer, each by the engine.
    """
    stmt = (
        select(models.Reply, models.Thread)
        .join(models.Thread, models.Thread.id == models.Reply.thread_id)
        .where(
            models.Thread.forum_id == forum_id,
            models.Thread.status == "open",
            models.Reply.accepted_answer.is_(True),
            models.Reply.status == "published",
            models.Reply.upvotes >= KNOWLEDGE_MIN_VOTES,
        )
    )
    stmt = agecommunity.restrict_query(stmt, models.Thread, age)
    if q:
        pattern = _like(q)
        stmt = stmt.where(
            or_(
                func.lower(models.Thread.title).like(pattern, escape="\\"),
                func.lower(models.Reply.body).like(pattern, escape="\\"),
            )
        )
    stmt = stmt.order_by(models.Reply.upvotes.desc(), models.Reply.created_at.desc(), models.Reply.id)
    entries: list[dict] = []
    for batch in range(KNOWLEDGE_MAX_BATCHES):
        pairs = db.execute(stmt.limit(KNOWLEDGE_BATCH).offset(batch * KNOWLEDGE_BATCH)).all()
        threads_ok = {th.id for th in _visible_threads(db, age, [th for _, th in pairs])}
        replies_ok = {r.id for r in _visible_replies(db, age, [r for r, _ in pairs])}
        for reply, thread in pairs:
            if thread.id in threads_ok and reply.id in replies_ok:
                entries.append(_community_entry(reply, thread))
                if len(entries) == limit:
                    return entries
        if len(pairs) < KNOWLEDGE_BATCH:
            break
    return entries


@app.get("/forums/{forum_id}/knowledge", tags=["knowledge"])
def knowledge(
    forum_id: str,
    principal: MaybeUser,
    q: str | None = Query(default=None, max_length=KNOWLEDGE_QUERY_MAX),
    limit: int = 20,
    db: OrmSession = Depends(get_db),
):
    """What this forum knows, always with the discussion it came from.

    Curated entries first, then answers the community settled. Both follow the
    forum's door and the viewer's age, like the discussions they came from.
    """
    _forum_access(db, _get_forum_or_404(db, forum_id), principal)
    age = agecommunity.viewer(principal.user_id if principal else None)
    limit = min(max(limit, 1), 100)
    curated = _curated_entries(db, forum_id, age, q, limit)
    community = _community_entries(db, forum_id, age, q, limit)
    # Curated first, but never all of it: half the page stays open to what the
    # community settled, so a long curated list cannot hide it.
    keep_curated = limit - min(len(community), limit // 2)
    items = curated[:keep_curated]
    return {"items": items + community[: limit - len(items)]}


# ---------------------------------------------------------------------------
# Internal — consumed by social-service, never exposed through the gateway
# ---------------------------------------------------------------------------
#
# Posts live in social-service and membership lives here, so the two have to
# talk for a community to behave like a group at all: social-service cannot
# decide who may read a community post without knowing who is in the community,
# and it must not keep its own copy of that - a stale copy of a membership list
# is somebody reading a group they were removed from.

class ReadableThreadsIn(BaseModel):
    viewer: str | None = None
    thread_ids: list[str] = Field(default_factory=list)


@app.post("/internal/readable-threads", tags=["internal"])
def readable_threads(payload: ReadableThreadsIn, db: OrmSession = Depends(get_db)):
    """Which of these threads this member may read: the same rules as opening one.

    For the realtime hub. A `thread:<id>` topic carries who replied, voted or had
    an answer accepted, and when; subscribing used to need nothing but the id.
    The forum's door (a secret community's thread is not for outsiders) and the
    age engine both apply, and a thread that is not open, or does not exist, is
    simply absent from the answer. One call for the whole batch.
    """
    ids = list(dict.fromkeys(i for i in payload.thread_ids if i))[:200]
    if not ids:
        return {"thread_ids": []}
    threads = db.scalars(
        select(models.Thread).where(models.Thread.id.in_(ids), models.Thread.status == "open")
    ).all()
    viewer = SimpleNamespace(user_id=payload.viewer) if payload.viewer else None
    doors: dict[str, bool] = {}
    through = []
    for thread in threads:
        if thread.forum_id not in doors:
            forum = db.get(models.Forum, thread.forum_id)
            doors[thread.forum_id] = forum is not None and _can_enter(db, forum, viewer)
        if doors[thread.forum_id]:
            through.append(thread)
    age = agecommunity.viewer(payload.viewer)
    return {"thread_ids": [t.id for t in _visible_threads(db, age, through)]}


@app.get("/internal/member-communities/{user_id}", tags=["internal"])
def member_communities(user_id: str, db: OrmSession = Depends(get_db)):
    """Every community this member actually belongs to.

    Active memberships only. A pending request is not membership - that is the
    whole point of a private community - and a banned row is the opposite of it.
    """
    ids = db.scalars(
        select(models.Membership.community_id).where(
            models.Membership.user_id == user_id,
            models.Membership.status == "active",
        )
    ).all()
    return {"communities": list(ids)}


@app.get("/internal/communities/{community_id}", tags=["internal"])
def community_summary(community_id: str, db: OrmSession = Depends(get_db)):
    """What another service needs to judge access: the kind, and who runs it.

    No member list: a caller that wants to know whether one person is in one
    community should ask that, not receive everybody's name to search through.
    """
    community = db.get(models.Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    return {
        "id": community.id,
        "slug": community.slug,
        "name": community.name,
        "kind": community.kind,
        "owner_id": community.owner_id,
        "members_count": community.members_count,
    }


@app.get("/internal/membership/{community_id}/{user_id}", tags=["internal"])
def membership_of(community_id: str, user_id: str, db: OrmSession = Depends(get_db)):
    """One person, one community. Answers for a community that does not exist
    too, so a caller cannot tell a missing community from a non-membership."""
    row = _membership(db, community_id, user_id)
    if row is None:
        return {"member": False, "role": None, "status": None}
    return {"member": row.status == "active", "role": row.role, "status": row.status}
