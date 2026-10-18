"""Kinjy · messaging-service — private messenger and notifications."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import logging

import httpx
from fastapi import BackgroundTasks, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

import threading

from common import crypto, permissions, settings
from common.auth import AdminUser, ModeratorUser, CurrentUser
from common.database import SessionLocal, create_all, get_db
from common.ids import new_id
from common.security import decode_token, ACCESS
from common.service import create_app

import agecheck
from common import ageclient
import agenotify
import models
from common import stickers
import webpush

log = logging.getLogger("messaging-service")
USER_URL = "http://user-service:8000"
MEDIA_URL = "http://media-service:8000"
AUTH_URL = "http://auth-service:8000"


def _profiles(user_ids: set[str]) -> dict[str, dict]:
    """Resolve everyone on the page in one call.

    The conversation list used to render `@usr_01M08QR6…` — a truncated raw id,
    which identifies nobody. One batch per response rather than one call per
    conversation.
    """
    if not user_ids:
        return {}
    # user-service accepts 300 ids at a time and refuses a longer list. Asking
    # for a whole group's worth in one body therefore returned nothing at all,
    # and the room rendered with no names - so the batch is split rather than
    # trusted to be small. Callers should still ask for the few people they are
    # about to draw; this only stops a large ask from failing silently.
    CAP = 300
    ordered = sorted(user_ids)
    resolved: dict[str, dict] = {}
    for start in range(0, len(ordered), CAP):
        chunk = ordered[start : start + CAP]
        try:
            response = httpx.post(f"{USER_URL}/internal/profiles", json={"ids": chunk}, timeout=5)
            response.raise_for_status()
            resolved.update(response.json()["profiles"])
        except Exception as exc:
            log.warning("could not resolve conversation participants: %s", exc)
    return resolved

MIGRATIONS = [
    # Group privacy, share links and pins. Additive; existing groups are
    # private, which is what they were: nobody could join without an admin.
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS privacy VARCHAR(10) DEFAULT 'private'",
    f"UPDATE {models.SCHEMA}.conversations SET privacy = 'private' WHERE privacy IS NULL",
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS invite_code VARCHAR(32)",
    f"CREATE INDEX IF NOT EXISTS ix_conversations_invite_code ON {models.SCHEMA}.conversations (invite_code)",
    f"ALTER TABLE {models.SCHEMA}.participants ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.participants ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ",
    # Disappearing messages shipped their model without these, so every attempt
    # to create a conversation raised UndefinedColumn - and the caller saw it
    # as "could not verify permission", which points nowhere near the cause.
    f"ALTER TABLE {models.SCHEMA}.conversations "
    "ADD COLUMN IF NOT EXISTS disappear_after_seconds INTEGER DEFAULT 0",
    f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS reply_to_id VARCHAR(40)",
    f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS sticker_id VARCHAR(40)",
    # Groups: what they are for, and which one everybody belongs to.
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS description TEXT",
    f"ALTER TABLE {models.SCHEMA}.conversations "
    "ADD COLUMN IF NOT EXISTS is_welcome BOOLEAN NOT NULL DEFAULT FALSE",
    # A group's photo and banner.
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500)",
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS avatar_asset_id VARCHAR(40)",
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS cover_url VARCHAR(500)",
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS cover_asset_id VARCHAR(40)",
    f"ALTER TABLE {models.SCHEMA}.conversations ADD COLUMN IF NOT EXISTS history_for_new VARCHAR(10) DEFAULT 'all'",
    # Conversations that already exist predate the request model, so everyone
    # in them is treated as having accepted. Retro-fitting a request state onto
    # live threads would silently block attachments between people who have
    # been talking for months.
    #
    # Bounded to rows from before the request model existed. Unbounded, this
    # ran on every boot and accepted every request made since - so each deploy
    # quietly accepted every pending conversation, a minor's included, and
    # unlocked attachments to people who had never answered.
    f"UPDATE {models.SCHEMA}.participants SET accepted_at = joined_at "
    "WHERE accepted_at IS NULL AND joined_at < TIMESTAMPTZ '2026-09-25 12:00:00+00'",
    # Idempotent sends (a retried message is not delivered twice) and attachments.
    f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_message_sender_client_id "
    f"ON {models.SCHEMA}.messages (sender_id, client_id) WHERE client_id IS NOT NULL",
    *(
        f"ALTER TABLE {models.SCHEMA}.messages ADD COLUMN IF NOT EXISTS {column}"
        for column in (
            "media_id VARCHAR(40)",
            "media_kind VARCHAR(20)",
            "media_name VARCHAR(255)",
            "media_type VARCHAR(100)",
            "media_size BIGINT",
            # Which key sealed the row's text and file name; NULL is plaintext.
            "sealed_with VARCHAR(16)",
        )
    ),
    # A sealed name is longer than the name it hides.
    f"ALTER TABLE {models.SCHEMA}.messages ALTER COLUMN media_name TYPE TEXT",
    # Reactions used to point at a sticker (a `sticker_id` column); they are emoji
    # now. create_all never changes a table that exists, so a database that still
    # has the old shape would fail on the first reaction. The old table is set
    # aside - renamed, never dropped, so what it holds can still be read - and
    # _rebuild_reactions builds the new one right after these statements.
    f"""DO $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '{models.SCHEMA}' AND table_name = 'message_reactions' AND column_name = 'sticker_id'
      ) THEN
        ALTER TABLE {models.SCHEMA}.message_reactions RENAME CONSTRAINT message_reactions_pkey TO message_reactions_legacy_pkey;
        ALTER INDEX IF EXISTS {models.SCHEMA}.ix_messaging_message_reactions_message_id RENAME TO ix_message_reactions_legacy_message_id;
        ALTER SEQUENCE IF EXISTS {models.SCHEMA}.message_reactions_id_seq RENAME TO message_reactions_legacy_id_seq;
        ALTER TABLE {models.SCHEMA}.message_reactions RENAME TO message_reactions_legacy;
      END IF;
    END $$""",
]


def _rebuild_reactions() -> None:
    """Runs after the migrations: builds the reactions table if they set the old one aside.

    Never fatal: a service that refuses to start over this is worse than one that
    starts and says what needs attention, which is the rule the migrations follow.
    """
    try:
        create_all(models.SCHEMA)
    except Exception:
        log.exception("could not rebuild the reactions table after the migrations")


app = create_app(
    name="messaging-service",
    schema=models.SCHEMA,
    migrations=MIGRATIONS,
    description="End-to-end encrypted direct messages, group conversations, notifications.",
    on_startup=[_rebuild_reactions, lambda: _start_sealing()],
)

# --- the realtime hub ------------------------------------------------------
#
# This service owns the only WebSocket the browser opens, so it is where every
# live update converges rather than each service growing its own socket. A
# client subscribes to *topics*; other services push into them over the private
# network via /internal/broadcast, which the gateway refuses to proxy.
#
# Topics in use:
#   user:<id>   — implicit on connect: messages, notifications, invitations
#   post:<id>   — reactions, comments, reposts and views on one post
#   feed        — a post was published
#   shorts      — a short was published
#
# A socket only ever receives what it asked for: broadcasting every post event
# to every connection would make a busy instance flood idle tabs.
#
# Every delivery goes through `_publish`. Chat used to write to a separate
# per-user socket registry, so a frame could reach a socket by one path and not
# the other; one path is also the only place Redis fan-out has to be added when
# this service runs more than one replica.
_topics: dict[str, set[WebSocket]] = {}


SOCIAL_URL = "http://social-service:8000"
COMMUNITY_URL = "http://community-service:8000"

# Topics anyone signed in may listen to: they carry no member's content. Anything
# else must be gated below; a topic nobody wrote a rule for is refused, so a new
# kind of topic starts closed instead of open.
OPEN_TOPICS = frozenset({"feed", "shorts"})


async def _readable_ids(url: str, key: str, user_id: str, ids: list[str]) -> set[str]:
    """Ask the service that owns these items which ones this member may read.

    One call for the whole batch. If the service cannot answer, nothing is
    allowed: live updates wait for a reload, nothing leaks.
    """
    if not ids:
        return set()
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            response = await client.post(url, json={"viewer": user_id, key: ids[:200]})
        response.raise_for_status()
        return set(response.json().get(key, []))
    except Exception as exc:
        log.warning("topic check %s failed for %s: %s", key, user_id, exc)
        return set()


async def _readable_topics(user_id: str, topics: list[str]) -> list[str]:
    """Keep the topics this member may listen to, drop the rest.

    ``post:<id>`` and ``thread:<id>`` carry who commented, replied or voted, and
    when. Subscribing used to be open to anyone holding the id, so a member
    removed from a circle could keep listening to its posts, and anyone could
    listen to a secret community's threads. The service that owns the item
    answers with the same rule as opening it (audience, forum door, age).
    """
    def ids_of(prefix: str) -> list[str]:
        return list(dict.fromkeys(t[len(prefix):] for t in topics if t.startswith(prefix) and len(t) > len(prefix)))

    posts, threads = await asyncio.gather(
        _readable_ids(f"{SOCIAL_URL}/internal/readable-posts", "post_ids", user_id, ids_of("post:")),
        _readable_ids(f"{COMMUNITY_URL}/internal/readable-threads", "thread_ids", user_id, ids_of("thread:")),
    )
    allowed: list[str] = []
    for topic in topics:
        if topic in OPEN_TOPICS or topic == f"user:{user_id}":
            allowed.append(topic)
        elif topic.startswith("post:") and topic[5:] in posts:
            allowed.append(topic)
        elif topic.startswith("thread:") and topic[7:] in threads:
            allowed.append(topic)
    return allowed


def _subscribe(socket: WebSocket, topics: list[str]) -> None:
    for topic in topics[:200]:
        _topics.setdefault(topic, set()).add(socket)


def _unsubscribe(socket: WebSocket, topics: list[str] | None = None) -> None:
    for topic in list(_topics) if topics is None else topics:
        members = _topics.get(topic)
        if not members:
            continue
        members.discard(socket)
        if not members:
            _topics.pop(topic, None)


async def _publish(topic: str, payload: dict) -> int:
    """Fan a payload out to a topic. Returns how many sockets received it."""
    delivered = 0
    for socket in list(_topics.get(topic, ())):
        try:
            await socket.send_json(payload)
            delivered += 1
        except Exception:
            # A socket that fails a send is gone; drop it rather than retry.
            _unsubscribe(socket, [topic])
    return delivered


async def _publish_to_users(user_ids, payload: dict) -> dict[str, int]:
    """The same frame on each member's own channel. Returns deliveries per member."""
    return {
        uid: await _publish(f"user:{uid}", {**payload, "topic": f"user:{uid}"})
        for uid in dict.fromkeys(user_ids)
    }


def _participant_ids(db: OrmSession, conversation_id: str) -> list[str]:
    return list(
        db.scalars(
            select(models.Participant.user_id).where(
                models.Participant.conversation_id == conversation_id
            )
        ).all()
    )


# --- presence ----------------------------------------------------------------
#
# Online means "has at least one authenticated socket open", counted here
# because this service holds every socket. Kept in process, like the topic
# registry: a second replica needs both moved to Redis together (phase 4).
# `last_seen` is lost on restart and then reads as unknown, never as a
# fabricated time.
#
# Who may see it: accepted connections and anyone you share a conversation
# with. Presence is personal information; a stranger who knows your id learns
# nothing from it.
_online: dict[str, int] = {}
_last_seen: dict[str, datetime] = {}


async def _presence_audience(user_id: str) -> set[str]:
    with SessionLocal() as db:
        mine = select(models.Participant.conversation_id).where(
            models.Participant.user_id == user_id
        )
        audience = set(
            db.scalars(
                select(models.Participant.user_id).where(
                    models.Participant.conversation_id.in_(mine)
                )
            ).all()
        )
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{USER_URL}/internal/connections/{user_id}")
            response.raise_for_status()
            audience.update(response.json()["ids"])
    except Exception as exc:
        # Conversation partners still get told; connections without a thread
        # just see a stale dot until the next change.
        log.warning("presence audience incomplete for %s: %s", user_id, exc)
    blocked = await _blocked_with(user_id)
    if blocked is None:
        # Cannot tell who is blocked, so tell nobody: a stale dot is the
        # lesser harm next to showing a blocked person you are online.
        return set()
    audience -= blocked
    audience.discard(user_id)
    return audience


async def _blocked_with(user_id: str) -> set[str] | None:
    """Everyone on either side of a block with this member; None if unknown."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{USER_URL}/internal/blocks/{user_id}")
            response.raise_for_status()
            return set(response.json()["ids"])
    except Exception as exc:
        log.warning("block list unavailable for %s: %s", user_id, exc)
        return None


async def _announce_presence(user_id: str, online: bool) -> None:
    seen = _last_seen.get(user_id)
    await _publish_to_users(
        await _presence_audience(user_id),
        {
            "type": "presence",
            "user_id": user_id,
            "online": online,
            "last_seen": seen.isoformat() if seen else None,
        },
    )


@app.get("/presence", tags=["presence"])
async def presence(ids: str, principal: CurrentUser):
    """Online state for a comma-separated list of members.

    Anyone outside the caller's audience is simply left out of the answer —
    not reported offline, which would itself be a claim about them.
    """
    wanted = [i for i in ids.split(",") if i][:200]
    audience = await _presence_audience(principal.user_id)
    return {
        "items": {
            uid: {
                "online": _online.get(uid, 0) > 0,
                "last_seen": _last_seen[uid].isoformat() if uid in _last_seen else None,
            }
            for uid in wanted
            if uid in audience
        }
    }


class ConversationIn(BaseModel):
    participant_ids: list[str] = Field(min_length=1)
    kind: str = Field(default="direct", pattern="^(direct|group)$")
    title: str | None = Field(default=None, max_length=140)
    description: str | None = Field(default=None, max_length=2_000)
    encrypted: bool = True


class MessageIn(BaseModel):
    ciphertext_b64: str | None = None
    body: str | None = Field(default=None, max_length=10_000)
    kind: str = Field(default="text", pattern="^(text|media|sticker|call_event)$")
    # An uploaded asset's id. Its URL, type, name and size are looked up from
    # media-service, which is why no client-supplied URL is accepted: a message
    # must not be able to embed an arbitrary address as a "photo".
    media_id: str | None = Field(default=None, max_length=40)
    lang: str | None = None
    # See models.Message.client_id: makes a retried send idempotent.
    client_id: str | None = Field(default=None, max_length=64)
    # The message this one answers; must belong to the same conversation.
    reply_to_id: str | None = Field(default=None, max_length=40)
    # For kind "sticker" only: an id from the catalogue (stickers.py).
    sticker_id: str | None = Field(default=None, max_length=64)


def _purge_expired(db: OrmSession, conversation_id: str | None = None) -> int:
    """Delete what has expired, for real.

    Called on every read and every write of a conversation rather than from a
    cron job: a sweeper that runs every five minutes leaves a five-minute window
    in which a "disappeared" message is still in the database and still
    returned by the API. Deleting on the path that would otherwise serve it
    closes that window.
    """
    expired = select(models.Message.id).where(
        models.Message.expires_at.is_not(None),
        models.Message.expires_at <= datetime.now(timezone.utc),
    )
    stmt = delete(models.Message).where(
        models.Message.expires_at.is_not(None),
        models.Message.expires_at <= datetime.now(timezone.utc),
    )
    if conversation_id:
        expired = expired.where(models.Message.conversation_id == conversation_id)
        stmt = stmt.where(models.Message.conversation_id == conversation_id)
    # A reaction must not outlive the message it is under.
    db.execute(delete(models.MessageReaction).where(models.MessageReaction.message_id.in_(expired)))
    removed = db.execute(stmt).rowcount or 0
    if removed:
        db.commit()
    return removed


def _member(db: OrmSession, conversation_id: str, user_id: str) -> models.Participant:
    participant = db.scalar(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id == user_id,
        )
    )
    if participant is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return participant


def _arrival_accepted(user_id: str) -> bool:
    """Whether somebody put into a group counts as having accepted it.

    An adult, yes: a room nobody sees until they accept it is a room nobody
    sees. A minor, no - or anybody whose age cannot be read, which is treated
    as a minor's. They read the room's text at once; attachments from others
    are withheld from them until they accept.
    """
    return not ageclient.age_profile(user_id).is_minor


def _withheld_from(db: OrmSession, conversation: models.Conversation | None) -> set[str]:
    """The members of a group who must not be handed others' attachments yet:
    minors who have not accepted it. Only unaccepted rows are looked at, so
    this costs nothing in a room where everybody has accepted."""
    if conversation is None or conversation.kind != "group":
        return set()
    waiting = db.scalars(
        select(models.Participant.user_id).where(
            models.Participant.conversation_id == conversation.id,
            models.Participant.accepted_at.is_(None),
        )
    ).all()
    return {uid for uid in waiting if ageclient.age_profile(uid).is_minor}


WITHHELD_MEDIA = {
    "media_url": None,
    "media_name": None,
    "media_type": None,
    "media_size": None,
    # The kind stays, so the bubble can say "a photo is hidden" rather than
    # showing an empty message.
    "media_withheld": True,
}


def _accept(db: OrmSession, conversation_id: str, user_id: str) -> None:
    """Mark a participant as having accepted. Idempotent."""
    row = db.scalar(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id == user_id,
        )
    )
    if row is not None and row.accepted_at is None:
        row.accepted_at = datetime.now(timezone.utc)


@app.post("/conversations/{conversation_id}/accept", tags=["messages"])
def accept_conversation(
    conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Accept a message request, which unlocks attachments from the sender."""
    _member(db, conversation_id, principal.user_id)
    _accept(db, conversation_id, principal.user_id)
    db.commit()
    return {"id": conversation_id, "accepted": True}


@app.get("/admin/contact-risk", tags=["admin"])
def contact_risk(_: ModeratorUser, limit: int = 50, db: OrmSession = Depends(get_db)):
    """Accounts whose contact attempts towards minors look like a pattern.

    A queue for humans, not a verdict. Ranked by how many *distinct* minors
    refused them, because ten attempts at one person is a different behaviour
    from one attempt at ten people.
    """
    since = datetime.now(timezone.utc) - agecheck.RISK_WINDOW
    rows = db.execute(
        select(
            models.ContactAttempt.sender_id,
            func.count(func.distinct(models.ContactAttempt.recipient_id)),
            func.count(),
        )
        .where(
            models.ContactAttempt.outcome == "refused_adult_to_minor",
            models.ContactAttempt.created_at >= since,
        )
        .group_by(models.ContactAttempt.sender_id)
        .order_by(func.count(func.distinct(models.ContactAttempt.recipient_id)).desc())
        .limit(min(limit, 200))
    ).all()
    return {
        "window_days": agecheck.RISK_WINDOW.days,
        "items": [
            {
                "sender_id": sender,
                "distinct_minors_refused": distinct,
                "total_attempts": total,
                "risk_score": agecheck.risk_score(db, sender),
                "note": "A signal for review. Not a finding about the person.",
            }
            for sender, distinct, total in rows
        ],
    }


@app.post("/conversations", status_code=201, tags=["messages"])
def create_conversation(payload: ConversationIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    members = sorted({*payload.participant_ids, principal.user_id})
    if payload.kind == "direct" and len(members) != 2:
        raise HTTPException(status_code=400, detail="A direct conversation has exactly two participants")

    # Whether you may message someone is their decision, not ours. user-service
    # owns that rule; asking it here is what makes the privacy setting real
    # rather than a checkbox the UI honours and the API ignores.
    for member in members:
        if member == principal.user_id:
            continue
        allowed, reason = permissions.check(principal.user_id, member, 'can_message', 'message this member')
        if not allowed:
            raise HTTPException(status_code=403, detail=reason)

        # And the layer the recipient cannot set for themselves. A teenager who
        # has left their messages open to anyone has not thereby agreed to
        # unknown adults, and their own setting is not the whole answer.
        age_ok, age_reason = agecheck.may_open_conversation(db, principal.user_id, member)
        if not age_ok:
            db.commit()   # keep the attempt record even though the call fails
            raise HTTPException(status_code=403, detail=age_reason)

    if payload.kind == "direct":
        # Reuse the existing thread rather than creating a duplicate.
        existing = db.execute(
            select(models.Participant.conversation_id)
            .where(models.Participant.user_id.in_(members))
            .group_by(models.Participant.conversation_id)
            .having(func.count() == 2)
        ).scalars().all()
        for cid in existing:
            conversation = db.get(models.Conversation, cid)
            if conversation and conversation.kind == "direct":
                return {"id": cid, "existing": True}

    conversation = models.Conversation(
        id=new_id("cnv"),
        kind=payload.kind,
        title=payload.title,
        description=payload.description if payload.kind == "group" else None,
        created_by=principal.user_id,
        # A group is never end-to-end encrypted, whatever the client asks for.
        # End-to-end for three or more people needs a group key agreement, and
        # there is none here - so a group that claimed it would be claiming
        # something untrue, which is worse than a group that does not claim
        # it. Group messages are encrypted at rest like everything else, and
        # the thread header says exactly that.
        encrypted=payload.encrypted if payload.kind == "direct" else False,
    )
    db.add(conversation)
    db.flush()
    for uid in members:
        db.add(
            models.Participant(
                conversation_id=conversation.id,
                user_id=uid,
                role="owner" if uid == principal.user_id else "member",
                # Starting a conversation is consent to it; being added to one
                # is not. Everybody else accepts by replying or by accepting.
                #
                # A group is different: the check above already asked whether
                # each member allows this person to message them, and a room
                # that sits in everybody's requests is a room nobody sees.
                #
                # Except a minor: being put in a room by somebody else is not
                # consent, and their attachments wait until they accept (they
                # read the text; images are withheld from them, not refused to
                # the room). See _arrival_accepted.
                accepted_at=(
                    datetime.now(timezone.utc)
                    if uid == principal.user_id
                    or (payload.kind == "group" and _arrival_accepted(uid))
                    else None
                ),
            )
        )
    db.commit()
    return {"id": conversation.id, "encrypted": conversation.encrypted, "existing": False}


# --- calls ------------------------------------------------------------------
#
# Voice and video, honestly scoped.
#
# What this does: carries the WebRTC handshake — offer, answer, ICE candidates,
# hang-up — between two members over the socket they already have. That is
# *signalling*, and it is genuinely all a 1:1 call needs from a server, because
# the audio and video travel directly between the two browsers and never touch
# this machine.
#
# What this does NOT do, and what would be dishonest to imply:
#   * **No TURN server.** Two peers behind symmetric NATs (many mobile networks,
#     most corporate firewalls) cannot reach each other directly, and the call
#     will fail to connect. A TURN relay is the fix and it is not deployed.
#   * **No group calls.** Three or more participants need a media server (SFU);
#     mesh peer-to-peer collapses past three people.
#   * **No recording, no fallback to PSTN.**
#
# The client is expected to say so when a connection fails rather than spinning.

CALL_SIGNALS = ("offer", "answer", "candidate", "hangup", "reject", "busy")


class CallSignalIn(BaseModel):
    conversation_id: str
    signal: str = Field(pattern="^(offer|answer|candidate|hangup|reject|busy)$")
    # Opaque to the server: SDP or an ICE candidate, passed through untouched.
    payload: dict = Field(default_factory=dict)
    media: str = Field(default="audio", pattern="^(audio|video)$")



# --- groups -----------------------------------------------------------------
#
# A group is a conversation with a name, a description and roles. The rules are
# the ones people already expect from every messenger they use: whoever made it
# owns it, owners and admins rename it and bring people in, and anybody can walk
# out. Nothing here is a new idea, which is the point - a group that behaves
# unlike every other group is a group people get wrong.


WELCOME_TITLE = "Welcome to Kinjy"
WELCOME_DESCRIPTION = (
    "Everybody on Kinjy is here. Say hello, ask anything, and meet the people "
    "who joined with you."
)


GROUP_IMAGES = ("avatar", "cover")


def _ready_image_url(asset_id: str, owner_id: str, purpose: str) -> str:
    """The url of an uploaded image, once media-service vouches for it.

    Fails closed: if media-service cannot confirm the asset the change is
    refused rather than applied. Not found and not yours give the same answer,
    so asset ids cannot be probed by trying them.
    """
    try:
        response = httpx.get(f"{MEDIA_URL}/internal/media/{asset_id}", timeout=5)
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Images cannot be checked right now. Please try again.")
    if response.status_code == 404:
        raise HTTPException(status_code=422, detail="Image not found")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Images cannot be checked right now. Please try again.")

    asset = response.json()
    if asset.get("owner_id") != owner_id:
        raise HTTPException(status_code=422, detail="Image not found")
    if asset.get("purpose") != purpose:
        raise HTTPException(status_code=422, detail=f"This image was not uploaded as a {purpose}")
    if asset.get("status") != "ready" or not asset.get("url"):
        raise HTTPException(status_code=409, detail="This image is still being processed")
    return asset["url"]


def _discard_image(asset_id: str, owner_id: str) -> None:
    """Delete a replaced image. Best effort: the group change already stands."""
    try:
        httpx.post(f"{MEDIA_URL}/internal/media/{asset_id}/discard", json={"owner_id": owner_id}, timeout=10)
    except httpx.HTTPError as exc:
        log.warning("could not discard replaced group image %s: %s", asset_id, exc)


class GroupEdit(BaseModel):
    title: str | None = Field(default=None, max_length=140)
    description: str | None = Field(default=None, max_length=2_000)
    # Uploaded through the same flow as a profile photo, and applied here by
    # id. None takes the picture off the group.
    avatar_asset_id: str | None = None
    cover_asset_id: str | None = None

    model_config = {"extra": "forbid"}


class MembersIn(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=50)


class RoleIn(BaseModel):
    role: str = Field(pattern="^(admin|member)$")


def _group(db: OrmSession, conversation_id: str, user_id: str) -> models.Conversation:
    """The group, and proof that this member is in it."""
    _member(db, conversation_id, user_id)
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None or conversation.kind != "group":
        raise HTTPException(status_code=404, detail="Group not found")
    return conversation


def _runs_group(db: OrmSession, conversation_id: str, user_id: str) -> models.Participant:
    """Owner or admin. Everybody else is a member, and may read and write."""
    participant = _member(db, conversation_id, user_id)
    if participant.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only the group's admins can do that")
    return participant


def _display_names(user_ids: set[str]) -> dict[str, str]:
    people = _profiles(user_ids)
    return {uid: (people.get(uid) or {}).get("display_name") or uid[:12] for uid in user_ids}


def _system_message(db: OrmSession, conversation_id: str, text: str) -> None:
    """A line in the thread about the group itself.

    Stored as a message so it appears in order with the conversation rather
    than as a banner that is true until the next reload. The sender is empty:
    nobody said it, the room did.
    """
    db.add(
        models.Message(
            id=new_id("msg"),
            conversation_id=conversation_id,
            sender_id="",
            encrypted=False,
            body=text,
            kind="system",
        )
    )


@app.get("/conversations/{conversation_id}/participants", tags=["groups"])
def group_participants(
    conversation_id: str,
    principal: CurrentUser,
    limit: int = 60,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    """Who is in the room, and who runs it. Members only.

    A page at a time. The welcome group holds every member of the platform, so
    the whole list is neither something anybody scrolls nor something that can
    be resolved to names in one request.
    """
    _member(db, conversation_id, principal.user_id)
    rows = db.scalars(
        select(models.Participant).where(models.Participant.conversation_id == conversation_id)
    ).all()
    # Admins first, then by name, which is the order this list is read in - so
    # the sort has to happen before the page is cut, and the names needed for
    # it come from the page's own rows. Roles are few: admins are fetched in
    # full, and the rest of the page is whoever the offset lands on.
    order = {"owner": 0, "admin": 1, "member": 2}
    # By id after the role, not by joined_at: the column is nullable and
    # timezone-aware, and comparing it against a fallback is a TypeError
    # waiting for the first row that has none. Ids are time-ordered anyway.
    rows = sorted(rows, key=lambda r: (order.get(r.role, 3), r.user_id))
    total = len(rows)
    page = rows[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
    people = _profiles({r.user_id for r in page})
    items = sorted(
        (
            {
                "user_id": r.user_id,
                "role": r.role,
                "joined_at": r.joined_at,
                "handle": (people.get(r.user_id) or {}).get("handle"),
                "display_name": (people.get(r.user_id) or {}).get("display_name") or r.user_id[:12],
                "avatar_url": (people.get(r.user_id) or {}).get("avatar_url"),
            }
            for r in page
        ),
        key=lambda p: (order.get(p["role"], 3), (p["display_name"] or "").lower()),
    )
    return {"total": total, "items": items, "offset": offset, "has_more": offset + len(page) < total}


@app.patch("/conversations/{conversation_id}", tags=["groups"])
async def edit_group(
    conversation_id: str,
    payload: GroupEdit,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Rename a group, or change what it says it is for. Admins only."""
    conversation = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)

    sent = payload.model_dump(exclude_unset=True)
    changed = []
    if payload.title is not None and payload.title.strip() != (conversation.title or ""):
        conversation.title = payload.title.strip() or None
        changed.append("renamed the group to " + (conversation.title or "no name"))
    if payload.description is not None and payload.description.strip() != (conversation.description or ""):
        conversation.description = payload.description.strip() or None
        changed.append("changed the description")

    # The photo and the banner. Checked against media-service before they are
    # applied, and the one they replace is deleted afterwards - an image nobody
    # references is a file nobody will ever come back for.
    replaced: list[str] = []
    for purpose in GROUP_IMAGES:
        key = f"{purpose}_asset_id"
        if key not in sent:
            continue
        asset_id = sent[key]
        url = None if asset_id is None else _ready_image_url(asset_id, principal.user_id, purpose)
        previous = getattr(conversation, key)
        if previous and previous != asset_id:
            replaced.append(previous)
        setattr(conversation, key, asset_id)
        setattr(conversation, f"{purpose}_url", url)
        if previous != asset_id:
            changed.append(
                ("changed the group photo" if purpose == "avatar" else "changed the group cover")
                if asset_id
                else ("removed the group photo" if purpose == "avatar" else "removed the group cover")
            )

    if changed:
        who = _display_names({principal.user_id})[principal.user_id]
        _system_message(db, conversation_id, who + " " + " and ".join(changed) + ".")
    db.commit()
    for asset_id in replaced:
        _discard_image(asset_id, principal.user_id)
    await _publish_to_users(
        _participant_ids(db, conversation_id),
        {"type": "conversation", "conversation_id": conversation_id},
    )
    return {
        "id": conversation.id,
        "title": conversation.title,
        "description": conversation.description,
        "avatar_url": conversation.avatar_url,
        "cover_url": conversation.cover_url,
    }


# Above this many people, an arrival is written into the thread and not pushed
# to anybody. A room of a few dozen wants to know somebody joined; a room of
# thousands does not want a notification each time, and neither does the member
# who would receive one for every stranger who signs up.
ANNOUNCE_PUSH_LIMIT = 50


async def _announce_joined(
    db: OrmSession,
    conversation_id: str,
    joined: list[str],
    background: BackgroundTasks | None = None,
) -> None:
    """Tell the room that somebody new is in it.

    Everybody who was already there hears about it - which is the request, and
    also how a group stops feeling like a list of strangers.

    Two things this must not do, both of which it did:

    * **Notify a whole platform.** The welcome group holds every member, so an
      arrival there was a notification to everybody about a stranger, every
      time anybody registered. Past ANNOUNCE_PUSH_LIMIT the system line in the
      thread is the record, and the live `conversation` event still refreshes
      whoever has the room open.
    * **Block the server while it does it.** Each push opens a database session
      and talks to the push service, and this ran inline, once per member,
      inside an async handler - so registering one account stopped
      messaging-service answering *anything* for thirty seconds. They are
      background tasks now, which is what the helper was always written for.

    The new arrivals are not told about themselves.
    """
    conversation = db.get(models.Conversation, conversation_id)
    members = _participant_ids(db, conversation_id)
    names = ", ".join(_display_names(set(joined)).values())
    title = (conversation.title if conversation else None) or "a group"

    await _publish_to_users(members, {"type": "conversation", "conversation_id": conversation_id})
    if background is None or len(members) > ANNOUNCE_PUSH_LIMIT:
        return
    arrivals = set(joined)
    for uid in members:
        if uid in arrivals:
            continue
        background.add_task(
            _push_to_member,
            uid,
            {
                "kind": "group_join",
                "title": names + " joined " + title,
                "link": "/messages?c=" + conversation_id,
            },
        )


@app.post("/conversations/{conversation_id}/participants", status_code=201, tags=["groups"])
async def add_participants(
    conversation_id: str,
    payload: MembersIn,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Bring people into a group. Admins only.

    Each person's own setting still decides: running a room is not permission
    to put somebody in it who does not accept messages from you.
    """
    _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)

    present = set(_participant_ids(db, conversation_id))
    added = []
    for uid in dict.fromkeys(payload.user_ids):
        if uid in present:
            continue
        allowed, reason = permissions.check(principal.user_id, uid, "can_message", "add this member")
        if not allowed:
            raise HTTPException(status_code=403, detail=reason)
        age_ok, age_reason = agecheck.may_open_conversation(db, principal.user_id, uid)
        if not age_ok:
            db.commit()
            raise HTTPException(status_code=403, detail=age_reason)
        db.add(
            models.Participant(
                conversation_id=conversation_id,
                user_id=uid,
                role="member",
                accepted_at=datetime.now(timezone.utc) if _arrival_accepted(uid) else None,
            )
        )
        added.append(uid)

    if added:
        names = ", ".join(_display_names(set(added)).values())
        who = _display_names({principal.user_id})[principal.user_id]
        _system_message(db, conversation_id, who + " added " + names + ".")
    db.commit()

    if added:
        await _announce_joined(db, conversation_id, added, background)
    return {"added": added}


@app.delete("/conversations/{conversation_id}/participants/{user_id}", tags=["groups"])
async def remove_participant(
    conversation_id: str,
    user_id: str,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Remove somebody, or leave yourself.

    Leaving is always allowed - a room you cannot walk out of is not a room.
    Removing somebody else is an admin's job, and nobody removes the owner.
    """
    conversation = _group(db, conversation_id, principal.user_id)
    leaving = user_id == principal.user_id
    if not leaving:
        _runs_group(db, conversation_id, principal.user_id)

    target = _member(db, conversation_id, user_id)
    if target.role == "owner" and not leaving:
        raise HTTPException(status_code=403, detail="The owner cannot be removed")
    if conversation.is_welcome and not leaving:
        # Everybody belongs to the welcome group by definition. Removing
        # somebody from it would be putting them outside the platform's front
        # door on somebody else's say-so.
        raise HTTPException(status_code=403, detail="Nobody is removed from the welcome group")

    names = _display_names({user_id, principal.user_id})
    db.delete(target)
    _system_message(
        db,
        conversation_id,
        names[user_id] + " left."
        if leaving
        else names[principal.user_id] + " removed " + names[user_id] + ".",
    )
    db.commit()
    await _publish_to_users(
        _participant_ids(db, conversation_id) + [user_id],
        {"type": "conversation", "conversation_id": conversation_id},
    )
    return {"removed": user_id}


@app.post("/conversations/{conversation_id}/participants/{user_id}/role", tags=["groups"])
async def set_participant_role(
    conversation_id: str,
    user_id: str,
    payload: RoleIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Make somebody an admin, or take it back. Admins only; the owner stays the owner."""
    _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    target = _member(db, conversation_id, user_id)
    if target.role == "owner":
        raise HTTPException(status_code=403, detail="The owner's role cannot be changed")

    if target.role != payload.role:
        target.role = payload.role
        names = _display_names({user_id, principal.user_id})
        _system_message(
            db,
            conversation_id,
            names[principal.user_id] + " made " + names[user_id] + " an admin."
            if payload.role == "admin"
            else names[principal.user_id] + " removed " + names[user_id] + " as an admin.",
        )
    db.commit()
    await _publish_to_users(
        _participant_ids(db, conversation_id),
        {"type": "conversation", "conversation_id": conversation_id},
    )
    return {"user_id": user_id, "role": target.role}


# --- privacy, share links, join requests, pins --------------------------------

PAYMENT_URL = "http://payment-service:8000"
# Groups anybody may pin. More need this year's verification (KYC) fee.
FREE_PINS = 3
# One sentence for every refusal to join by link, whatever the reason, so the
# refusal never tells anybody that a minor is in the room.
LINK_REFUSED = "You cannot join this group by link. Ask one of its admins to add you."


class PrivacyIn(BaseModel):
    privacy: str = Field(pattern="^(public|private)$")


class HistoryIn(BaseModel):
    history: str = Field(pattern="^(all|from_join)$")


class RequestActionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")


def _by_code(db: OrmSession, code: str) -> models.Conversation:
    row = db.scalar(
        select(models.Conversation).where(
            models.Conversation.invite_code == code, models.Conversation.kind == "group"
        )
    ) if code else None
    if row is None or row.is_welcome:
        raise HTTPException(status_code=404, detail="This link does not lead anywhere")
    return row


def _may_join_by_link(db: OrmSession, conversation_id: str, user_id: str) -> bool:
    """The age gate on walking into a room through a link.

    Being added by an admin goes through the per-person checks, between the
    admin and the person added. A link has nobody vouching for the newcomer,
    and once inside they can write to everybody in the room. So:

    * a minor, or anybody whose age is not confirmed, does not join by link -
      an admin they are connected to can add them;
    * an adult does not join by link a room that has a minor in it.

    Fails closed: an age that cannot be read counts as a minor's.
    """
    joiner = ageclient.age_profile(user_id)
    if joiner.is_minor:
        return False
    for uid in _participant_ids(db, conversation_id):
        if ageclient.age_profile(uid).is_minor:
            return False
    return True


def _admit(db: OrmSession, conversation_id: str, user_id: str) -> None:
    db.add(
        models.Participant(
            conversation_id=conversation_id,
            user_id=user_id,
            role="member",
            accepted_at=datetime.now(timezone.utc),
        )
    )
    db.execute(
        delete(models.JoinRequest).where(
            models.JoinRequest.conversation_id == conversation_id,
            models.JoinRequest.user_id == user_id,
        )
    )


@app.patch("/conversations/{conversation_id}/history", tags=["groups"])
async def set_group_history(
    conversation_id: str, payload: HistoryIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Whether members added from now on read the earlier messages. Admins only.

    Applies to everybody who is not an admin, by the date they joined - so
    turning it on also hides from a member the messages from before they came.
    """
    conversation = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    if (conversation.history_for_new or "all") == payload.history:
        return {"history": payload.history, "changed": False}
    conversation.history_for_new = payload.history
    who = _display_names({principal.user_id})[principal.user_id]
    _system_message(
        db,
        conversation_id,
        who + (" set the group so new members see the earlier messages."
               if payload.history == "all"
               else " set the group so new members only see messages from when they join."),
    )
    db.commit()
    await _publish_to_users(
        _participant_ids(db, conversation_id), {"type": "conversation", "conversation_id": conversation_id}
    )
    return {"history": conversation.history_for_new, "changed": True}


@app.patch("/conversations/{conversation_id}/privacy", tags=["groups"])
async def set_group_privacy(
    conversation_id: str,
    payload: PrivacyIn,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Public or private. Admins only; the welcome group is everybody's already.

    Opening a group answers everybody waiting at its door - except anybody the
    age rule would keep out, whose request stays for an admin to see.
    """
    conversation = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    if conversation.is_welcome:
        raise HTTPException(status_code=403, detail="The welcome group's privacy does not change")
    if (conversation.privacy or "private") == payload.privacy:
        return {"privacy": payload.privacy, "changed": False, "admitted": 0}

    conversation.privacy = payload.privacy
    who = _display_names({principal.user_id})[principal.user_id]
    _system_message(
        db,
        conversation_id,
        who + (" made the group public: anyone with its link can join."
               if payload.privacy == "public"
               else " made the group private: joining by link now needs an admin's approval."),
    )
    admitted: list[str] = []
    if payload.privacy == "public":
        waiting = db.scalars(
            select(models.JoinRequest).where(models.JoinRequest.conversation_id == conversation_id)
        ).all()
        for row in waiting:
            if _may_join_by_link(db, conversation_id, row.user_id):
                _admit(db, conversation_id, row.user_id)
                admitted.append(row.user_id)
    db.commit()
    if admitted:
        await _announce_joined(db, conversation_id, admitted, background)
    else:
        await _publish_to_users(
            _participant_ids(db, conversation_id),
            {"type": "conversation", "conversation_id": conversation_id},
        )
    return {"privacy": conversation.privacy, "changed": True, "admitted": len(admitted)}


@app.post("/conversations/{conversation_id}/share-link", tags=["groups"])
def group_share_link(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The link to send, public or private. Any member may share it."""
    conversation = _group(db, conversation_id, principal.user_id)
    if conversation.is_welcome:
        raise HTTPException(status_code=400, detail="Everybody is in the welcome group already")
    if not conversation.invite_code:
        conversation.invite_code = secrets.token_urlsafe(12)
        db.commit()
    return {"path": f"/messages/join/{conversation.invite_code}", "code": conversation.invite_code}


@app.get("/internal/groups/by-code/{code}", tags=["internal"])
def group_by_code(code: str, db: OrmSession = Depends(get_db)):
    """A group's face for a link preview: what the join page itself shows,
    never its members or messages. Internal - not routed by the gateway."""
    conversation = _by_code(db, code)
    return {
        "title": conversation.title,
        "description": conversation.description,
        "avatar_url": conversation.avatar_url,
        "cover_url": conversation.cover_url,
        "privacy": conversation.privacy or "private",
        "members_count": len(_participant_ids(db, conversation.id)),
    }


@app.get("/conversations/join/{code}", tags=["groups"])
def preview_by_link(code: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What somebody holding the link sees before they decide: the group's face
    and what it is for, never its messages or its members."""
    conversation = _by_code(db, code)
    member = db.scalar(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation.id,
            models.Participant.user_id == principal.user_id,
        )
    )
    pending = db.scalar(
        select(models.JoinRequest).where(
            models.JoinRequest.conversation_id == conversation.id,
            models.JoinRequest.user_id == principal.user_id,
        )
    )
    return {
        "id": conversation.id if member else None,
        "title": conversation.title,
        "description": conversation.description,
        "avatar_url": conversation.avatar_url,
        "cover_url": conversation.cover_url,
        "privacy": conversation.privacy or "private",
        "members_count": len(_participant_ids(db, conversation.id)),
        "my_state": "member" if member else "pending" if pending else "none",
    }


@app.post("/conversations/join/{code}", tags=["groups"])
async def join_by_link(
    code: str,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Walk in (public) or ask to (private)."""
    conversation = _by_code(db, code)
    present = set(_participant_ids(db, conversation.id))
    if principal.user_id in present:
        return {"status": "member", "conversation_id": conversation.id}
    if not _may_join_by_link(db, conversation.id, principal.user_id):
        raise HTTPException(status_code=403, detail=LINK_REFUSED)

    if (conversation.privacy or "private") == "public":
        _admit(db, conversation.id, principal.user_id)
        who = _display_names({principal.user_id})[principal.user_id]
        _system_message(db, conversation.id, who + " joined with the group's link.")
        db.commit()
        await _announce_joined(db, conversation.id, [principal.user_id], background)
        return {"status": "member", "conversation_id": conversation.id}

    exists = db.scalar(
        select(models.JoinRequest).where(
            models.JoinRequest.conversation_id == conversation.id,
            models.JoinRequest.user_id == principal.user_id,
        )
    )
    if exists is None:
        db.add(models.JoinRequest(conversation_id=conversation.id, user_id=principal.user_id))
        db.commit()
        admins = db.scalars(
            select(models.Participant.user_id).where(
                models.Participant.conversation_id == conversation.id,
                models.Participant.role.in_(("owner", "admin")),
            )
        ).all()
        who = _display_names({principal.user_id})[principal.user_id]
        for uid in admins:
            background.add_task(
                _push_to_member,
                uid,
                {
                    "kind": "group_request",
                    "title": who + " asked to join " + (conversation.title or "your group"),
                    "link": "/messages?c=" + conversation.id,
                },
            )
        await _publish_to_users(admins, {"type": "conversation", "conversation_id": conversation.id})
    return {"status": "pending", "conversation_id": None}


@app.get("/conversations/{conversation_id}/requests", tags=["groups"])
def group_requests(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Who is waiting at a private group's door. Admins only."""
    _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    rows = db.scalars(
        select(models.JoinRequest)
        .where(models.JoinRequest.conversation_id == conversation_id)
        .order_by(models.JoinRequest.created_at)
    ).all()
    people = _profiles({r.user_id for r in rows})
    return {
        "items": [
            {
                "user_id": r.user_id,
                "created_at": r.created_at,
                "handle": (people.get(r.user_id) or {}).get("handle"),
                "display_name": (people.get(r.user_id) or {}).get("display_name") or r.user_id[:12],
                "avatar_url": (people.get(r.user_id) or {}).get("avatar_url"),
            }
            for r in rows
        ]
    }


@app.post("/conversations/{conversation_id}/requests/{user_id}", tags=["groups"])
async def answer_request(
    conversation_id: str,
    user_id: str,
    payload: RequestActionIn,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Let somebody in, or turn them away. Admins only.

    Approving is checked again against the age rule: an admin's yes does not
    lift it, because the admin may not know who else is in the room.
    """
    conversation = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    row = db.scalar(
        select(models.JoinRequest).where(
            models.JoinRequest.conversation_id == conversation_id,
            models.JoinRequest.user_id == user_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No request from this member")
    if payload.action == "reject":
        db.delete(row)
        db.commit()
        return {"user_id": user_id, "status": "rejected"}

    if not _may_join_by_link(db, conversation_id, user_id):
        raise HTTPException(
            status_code=403,
            detail="This member cannot be let in from a link. Add them from the member list instead.",
        )
    _admit(db, conversation_id, user_id)
    names = _display_names({user_id, principal.user_id})
    _system_message(db, conversation_id, names[principal.user_id] + " let " + names[user_id] + " in.")
    db.commit()
    background.add_task(
        _push_to_member,
        user_id,
        {
            "kind": "group_approved",
            "title": "You were let into " + (conversation.title or "a group"),
            "link": "/messages?c=" + conversation_id,
        },
    )
    await _announce_joined(db, conversation_id, [user_id], background)
    return {"user_id": user_id, "status": "member"}


def _kyc_fee_paid(user_id: str) -> tuple[bool, str]:
    """(paid this year, fee). Fails closed: an extra pin waits rather than
    being given away when payment-service cannot answer."""
    try:
        response = httpx.get(f"{PAYMENT_URL}/internal/kyc/{user_id}", timeout=5)
        response.raise_for_status()
        body = response.json()
        return bool(body.get("fee_paid")), str(body.get("fee_usd", ""))
    except Exception as exc:
        log.warning("could not read the KYC fee of %s: %s", user_id, exc)
        return False, ""


@app.post("/conversations/{conversation_id}/pin", tags=["groups"])
async def pin_group(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Pin a conversation to the top of your list - a favourite.

    Any conversation, a one-to-one chat as much as a group. Three *groups*
    are free and more ask for the KYC fee; a pinned one-to-one chat does not
    count towards that, since it is what members asked for a favourites list
    to hold.
    """
    me = _member(db, conversation_id, principal.user_id)
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if me.pinned_at is not None:
        return {"pinned": True, "already": True}
    count = db.scalar(
        select(func.count())
        .select_from(models.Participant)
        .join(models.Conversation, models.Conversation.id == models.Participant.conversation_id)
        .where(
            models.Participant.user_id == principal.user_id,
            models.Participant.pinned_at.is_not(None),
            models.Conversation.kind == "group",
        )
    ) or 0
    if conversation.kind == "group" and count >= FREE_PINS:
        paid, fee = _kyc_fee_paid(principal.user_id)
        if not paid:
            price = f"${fee} " if fee else ""
            raise HTTPException(
                status_code=402,
                detail=f"You can pin {FREE_PINS} groups. To pin more, pay your {price}annual verification (KYC) fee.",
            )
    me.pinned_at = datetime.now(timezone.utc)
    db.commit()
    await _publish_to_users([principal.user_id], {"type": "conversation", "conversation_id": conversation_id})
    return {"pinned": True, "count": count + 1}


@app.delete("/conversations/{conversation_id}/pin", tags=["groups"])
async def unpin_group(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    me = _member(db, conversation_id, principal.user_id)
    if me.pinned_at is not None:
        me.pinned_at = None
        db.commit()
    await _publish_to_users([principal.user_id], {"type": "conversation", "conversation_id": conversation_id})
    return {"pinned": False}


def _welcome_admin_id() -> str | None:
    """The account configured to run the welcome group, if there is one.

    Resolved from the address every time rather than stored: the setting can be
    pointed at somebody else without a migration, and a typo in it leaves the
    room with no admin instead of handing it to whoever happens to hold a
    guessed id.
    """
    email = (settings.WELCOME_GROUP_ADMIN_EMAIL or "").strip()
    if not email:
        return None
    try:
        response = httpx.get(f"{AUTH_URL}/internal/users/by-email/{email}", timeout=5)
        if response.status_code == 404:
            log.warning("no account for the configured welcome-group admin %s", email)
            return None
        response.raise_for_status()
        return response.json()["id"]
    except Exception as exc:
        log.warning("could not resolve the welcome-group admin: %s", exc)
        return None


def _ensure_welcome_admin(db: OrmSession, conversation: models.Conversation) -> None:
    """Make the configured account the owner of the welcome group.

    Done on the way past rather than only at creation, because the room exists
    on every environment already and because the setting can change. Idempotent
    and cheap: it touches a row only when the role is actually wrong.
    """
    admin_id = _welcome_admin_id()
    if admin_id is None:
        return
    row = db.scalar(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation.id,
            models.Participant.user_id == admin_id,
        )
    )
    if row is None:
        db.add(
            models.Participant(
                conversation_id=conversation.id,
                user_id=admin_id,
                role="owner",
                accepted_at=datetime.now(timezone.utc),
            )
        )
    elif row.role != "owner":
        row.role = "owner"
    if conversation.created_by != admin_id:
        conversation.created_by = admin_id


def _welcome_group(db: OrmSession) -> models.Conversation:
    """The group everybody is in, created the first time somebody needs it.

    Found by its flag rather than by a hard-coded id, so the id lives in one
    place - this table - instead of in two services that can disagree about it.
    """
    conversation = db.scalar(
        select(models.Conversation).where(models.Conversation.is_welcome.is_(True))
    )
    if conversation is not None:
        _ensure_welcome_admin(db, conversation)
        return conversation
    conversation = models.Conversation(
        id=new_id("cnv"),
        kind="group",
        title=WELCOME_TITLE,
        description=WELCOME_DESCRIPTION,
        created_by="system",
        encrypted=False,
        is_welcome=True,
    )
    db.add(conversation)
    db.flush()
    _ensure_welcome_admin(db, conversation)
    return conversation


class WelcomeIn(BaseModel):
    user_id: str


@app.post("/internal/welcome/join", tags=["internal"])
async def welcome_join(
    payload: WelcomeIn,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Put a new member in the welcome group.

    Called by auth-service the moment an account exists. An internal HTTP call
    rather than an event, deliberately: the broker on this platform has no
    durable queues behind it, so an event published here would be a message
    nobody receives and a member who silently never joined.

    Idempotent - a retry after a timeout must not add a second row or announce
    the same person twice.
    """
    conversation = _welcome_group(db)
    already = db.scalar(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation.id,
            models.Participant.user_id == payload.user_id,
        )
    )
    if already is not None:
        return {"conversation_id": conversation.id, "joined": False}

    db.add(
        models.Participant(
            conversation_id=conversation.id,
            user_id=payload.user_id,
            role="member",
            accepted_at=datetime.now(timezone.utc) if _arrival_accepted(payload.user_id) else None,
        )
    )
    _system_message(
        db, conversation.id, _display_names({payload.user_id})[payload.user_id] + " joined Kinjy."
    )
    db.commit()
    await _announce_joined(db, conversation.id, [payload.user_id], background)
    return {"conversation_id": conversation.id, "joined": True}



@app.post("/internal/welcome/backfill", tags=["internal"])
def welcome_backfill(limit: int = 1000, db: OrmSession = Depends(get_db)):
    """Put everybody who was already here into the welcome group.

    The join at registration only ever catches people who register after it
    exists, so without this the room everybody is supposed to be in holds
    whoever happened to sign up since it shipped. Run once after deploying;
    safe to run again, since a member who is already in it is skipped.

    Deliberately quiet: no system line and no notification for a sweep. A
    thousand "X joined" lines would bury the room on the day it opens, and the
    arrivals being announced did not arrive today.
    """
    conversation = _welcome_group(db)
    present = set(
        db.scalars(
            select(models.Participant.user_id).where(
                models.Participant.conversation_id == conversation.id
            )
        ).all()
    )

    added = 0
    after: str | None = None
    now = datetime.now(timezone.utc)
    while added < limit:
        try:
            response = httpx.get(
                "http://user-service:8000/internal/profiles/ids",
                params={"after": after, "limit": 500} if after else {"limit": 500},
                timeout=10,
            )
            response.raise_for_status()
            page = response.json()
        except Exception as exc:
            log.warning("welcome backfill could not read the member list: %s", exc)
            break

        ids = page.get("items") or []
        if not ids:
            break
        for uid in ids:
            if uid in present:
                continue
            db.add(
                models.Participant(
                    conversation_id=conversation.id,
                    user_id=uid,
                    role="member",
                    accepted_at=now if _arrival_accepted(uid) else None,
                )
            )
            present.add(uid)
            added += 1
        after = page.get("next")
        if not after:
            break

    db.commit()
    return {"conversation_id": conversation.id, "added": added, "members": len(present)}

@app.post("/calls/signal", tags=["calls"])
async def call_signal(
    body: CallSignalIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Relay one WebRTC signal to the other people in the conversation.

    The server never inspects the payload — an SDP offer is opaque to it, which
    is the point: signalling a call must not become a place where the platform
    learns what is in it.
    """
    _member(db, body.conversation_id, principal.user_id)

    recipients = db.scalars(
        select(models.Participant.user_id).where(
            models.Participant.conversation_id == body.conversation_id,
            models.Participant.user_id != principal.user_id,
        )
    ).all()
    if len(recipients) > 1 and body.signal == "offer":
        raise HTTPException(
            status_code=400,
            detail="Group calls need a media server, which is not deployed. One-to-one only for now.",
        )

    delivered = 0
    for uid in recipients:
        delivered += await _publish(
            f"user:{uid}",
            {
                "type": "call",
                "topic": f"user:{uid}",
                "signal": body.signal,
                "conversation_id": body.conversation_id,
                "from": principal.user_id,
                "media": body.media,
                "payload": body.payload,
            },
        )

    # A ring nobody is holding a socket for is a call that will never be picked
    # up; the caller should hear that immediately rather than after 30 seconds.
    return {"delivered": delivered, "reachable": delivered > 0}


class DisappearIn(BaseModel):
    """0 keeps messages. Otherwise a whole number of seconds."""

    seconds: int = Field(ge=0, le=60 * 60 * 24 * 30)


@app.post("/conversations/{conversation_id}/disappearing", tags=["messages"])
async def set_disappearing(
    conversation_id: str,
    payload: DisappearIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Turn disappearing messages on or off for this room.

    Any participant may set it, and everyone is told: a timer one person can
    change silently is a trap rather than a privacy feature. Messages already
    sent are left alone — retroactively deleting what people believed was kept
    would be the same betrayal in the other direction.
    """
    _member(db, conversation_id, principal.user_id)
    conversation = db.get(models.Conversation, conversation_id)
    conversation.disappear_after_seconds = payload.seconds
    db.commit()

    recipients = db.scalars(
        select(models.Participant.user_id).where(
            models.Participant.conversation_id == conversation_id
        )
    ).all()
    for uid in recipients:
        await _publish(
            f"user:{uid}",
            {
                "type": "disappearing_changed",
                "topic": f"user:{uid}",
                "conversation_id": conversation_id,
                "seconds": payload.seconds,
                "actor": principal.user_id,
            },
        )
    return {"seconds": payload.seconds}


def _unread_by_conversation(db: OrmSession, user_id: str, ids) -> dict[str, int]:
    """Unread messages per conversation, for one member.

    Unread = messages from someone else, newer than my last read — in one
    grouped query. The cutoff differs per conversation, so it comes from the
    join on my own participant row rather than from a Python loop issuing a
    count per conversation. The conversation list and the unread badge both
    read this, so the two can never disagree.
    """
    rows = db.execute(
        select(models.Message.conversation_id, func.count())
        .join(
            models.Participant,
            (models.Participant.conversation_id == models.Message.conversation_id)
            & (models.Participant.user_id == user_id),
        )
        .where(
            models.Message.conversation_id.in_(ids),
            models.Message.sender_id != user_id,
            # The room's own lines ("X joined Kinjy", "Y renamed the group")
            # are bookkeeping, not somebody talking to you. The welcome group
            # writes one per registration, so counting them lit every badge
            # with hundreds.
            models.Message.kind != "system",
            # Nothing said before you arrived is unread *for you*: a member
            # who joins a busy room starts at zero, not at its whole history.
            models.Message.created_at
            >= func.coalesce(models.Participant.joined_at, models.Message.created_at),
            or_(
                models.Participant.last_read_at.is_(None),
                models.Message.created_at > models.Participant.last_read_at,
            ),
            # A message that has expired is gone as far as the thread is
            # concerned (it is purged on the next read or write), so it must not
            # keep a badge lit that opening the thread could never explain.
            or_(
                models.Message.expires_at.is_(None),
                models.Message.expires_at > datetime.now(timezone.utc),
            ),
        )
        .group_by(models.Message.conversation_id)
    ).all()
    return {conversation_id: count for conversation_id, count in rows}


@app.get("/conversations/unread-count", tags=["messages"])
def unread_count(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What the Messages badge shows: unread messages, and in how many threads.

    The badge refetches this when the socket says a message arrived or a thread
    was read (in this tab or another), rather than keeping its own tally that
    could drift from the conversation list.
    """
    ids = db.scalars(
        select(models.Participant.conversation_id).where(models.Participant.user_id == principal.user_id)
    ).all()
    unread = _unread_by_conversation(db, principal.user_id, ids) if ids else {}
    return {"messages": sum(unread.values()), "conversations": sum(1 for n in unread.values() if n)}


@app.get("/conversations", tags=["messages"])
def list_conversations(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    ids = db.scalars(
        select(models.Participant.conversation_id).where(models.Participant.user_id == principal.user_id)
    ).all()
    rows = db.scalars(
        select(models.Conversation)
        .where(models.Conversation.id.in_(ids))
        .order_by(models.Conversation.last_message_at.desc())
    ).all()

    # Participants for every conversation in one query, not one per row.
    memberships = db.scalars(
        select(models.Participant).where(models.Participant.conversation_id.in_(ids))
    ).all()
    group_ids = {r.id for r in rows if r.kind == "group"}
    members: dict[str, list[str]] = {}
    read_state: dict[str, dict[str, str | None]] = {}
    # This viewer's own role in each room, picked out of the same rows rather
    # than fetched again per conversation.
    my_role: dict[str, str] = {}
    my_pin: dict[str, datetime] = {}
    my_accepted: dict[str, bool] = {}
    for m in memberships:
        members.setdefault(m.conversation_id, []).append(m.user_id)
        if m.user_id == principal.user_id:
            my_role[m.conversation_id] = m.role
            my_accepted[m.conversation_id] = m.accepted_at is not None
            if m.pinned_at is not None:
                my_pin[m.conversation_id] = m.pinned_at
        # "Seen" is a thing you say about a direct conversation. In a group it
        # would be a row per member, which is both meaningless to draw and the
        # same payload problem as the profiles above.
        if m.conversation_id not in group_ids:
            read_state.setdefault(m.conversation_id, {})[m.user_id] = (
                m.last_read_at.isoformat() if m.last_read_at else None
            )

    # A group now holds everybody - the welcome group is the whole platform -
    # so resolving every participant of every conversation meant one profile
    # per member on every poll of this list, and a response carrying thousands
    # of them. The list only ever draws a few faces per row and a count, so
    # that is all it asks for. A direct conversation is two people and keeps
    # both; a thread's own senders come back with the messages.
    FACES = 8
    shown: dict[str, list[str]] = {
        cid: (uids if cid not in group_ids else uids[:FACES])
        for cid, uids in members.items()
    }
    profiles = _profiles({uid for uids in shown.values() for uid in uids})

    unread = _unread_by_conversation(db, principal.user_id, ids)

    # The newest message of each thread in one query (Postgres DISTINCT ON),
    # so the list can say what was last said rather than only when.
    latest = {
        m.conversation_id: m
        for m in db.scalars(
            select(models.Message)
            .where(
                models.Message.conversation_id.in_(ids),
                or_(
                    models.Message.expires_at.is_(None),
                    models.Message.expires_at > datetime.now(timezone.utc),
                ),
            )
            .order_by(models.Message.conversation_id, models.Message.created_at.desc())
            .distinct(models.Message.conversation_id)
        ).all()
    }

    # Pinned groups first, in the order they were pinned; then everything by
    # its latest message, as before.
    rows = sorted(rows, key=lambda r: (0, my_pin[r.id].timestamp()) if r.id in my_pin else (1, 0))

    return {
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "title": r.title,
                "privacy": (r.privacy or "private") if r.kind == "group" else None,
                "history_for_new": (r.history_for_new or "all") if r.kind == "group" else None,
                "pinned": r.id in my_pin,
                # False while this is a request the viewer has not answered.
                "accepted": my_accepted.get(r.id, True),
                # Groups only, and the one thing a group has that a direct
                # conversation does not: what it is for.
                "description": r.description,
                "is_welcome": r.is_welcome,
                "avatar_url": r.avatar_url,
                "cover_url": r.cover_url,
                # What this viewer may do in it, so the page does not have to
                # fetch the member list to decide whether to show a settings
                # button.
                "my_role": my_role.get(r.id, "member"),
                "encrypted": r.encrypted,
                "last_message_at": r.last_message_at,
                # Everybody for a direct conversation; a few faces for a group,
                # with the real size beside it.
                "participants": shown.get(r.id, []),
                "participant_count": len(members.get(r.id, [])),
                # Resolved names and avatars, so the list can show people.
                "profiles": {
                    uid: profiles.get(uid) for uid in shown.get(r.id, []) if profiles.get(uid)
                },
                "unread": unread.get(r.id, 0),
                # How far each participant has read — what "Seen" is drawn from.
                "read_state": read_state.get(r.id, {}),
                # Encryption at rest is on: stored sealed, but readable by the
                # server. Distinct from `encrypted` (end to end) — the UI must
                # never let one pass for the other.
                "sealed_at_rest": crypto.enabled(),
                "last_message": (
                    {
                        "sender_id": latest[r.id].sender_id,
                        "preview": _preview(latest[r.id])[:120],
                        "media_kind": latest[r.id].media_kind,
                        "created_at": latest[r.id].created_at,
                    }
                    if r.id in latest
                    else None
                ),
            }
            for r in rows
        ]
    }


async def _attachment(media_id: str, sender_id: str) -> dict:
    """The asset as media-service knows it — and only if the sender uploaded it."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{MEDIA_URL}/internal/assets/{media_id}")
    except Exception as exc:
        log.warning("media-service unreachable for %s: %s", media_id, exc)
        raise HTTPException(status_code=503, detail="Could not verify the attachment. Try again shortly.")
    if response.status_code == 404:
        raise HTTPException(status_code=400, detail="That attachment does not exist")
    response.raise_for_status()
    asset = response.json()
    # Someone else's upload is not yours to forward by id, however it was found.
    if asset.get("owner_id") != sender_id:
        raise HTTPException(status_code=403, detail="You can only attach files you uploaded")
    return asset


# --- encryption at rest ---------------------------------------------------------
#
# The text of a message and the name of its attachment are sealed on the way
# into the database and opened on the way out; nothing else in this service
# handles ciphertext. See common/crypto.py for what this does and does not
# protect against.

def _seal(message: models.Message, body: str | None, name: str | None) -> None:
    """Set body and attachment name — sealed when a key is configured."""
    if not crypto.enabled() or (body is None and name is None):
        message.body, message.media_name, message.sealed_with = body, name, None
        return
    key_id = None
    message.body = message.media_name = None
    if body is not None:
        key_id, message.body = crypto.seal_text(body, f"msg:{message.id}:body")
    if name is not None:
        key_id, message.media_name = crypto.seal_text(name, f"msg:{message.id}:name")
    message.sealed_with = key_id


def _plain(message: models.Message, strict: bool = False) -> tuple[str | None, str | None]:
    """(body, attachment name) in the clear.

    A value that cannot be opened — its key was removed from the keyring —
    comes back as None rather than as ciphertext, and is logged; ``strict``
    raises instead, for callers that would otherwise overwrite it.
    """
    if not message.sealed_with:
        return message.body, message.media_name
    try:
        body = (
            crypto.open_text(message.sealed_with, message.body, f"msg:{message.id}:body")
            if message.body is not None
            else None
        )
        name = (
            crypto.open_text(message.sealed_with, message.media_name, f"msg:{message.id}:name")
            if message.media_name is not None
            else None
        )
        return body, name
    except crypto.DecryptionError as exc:
        if strict:
            raise
        log.error("message %s could not be decrypted: %s", message.id, exc)
        return None, None


def _start_sealing() -> None:
    crypto.require_in_production("messaging-service")
    if crypto.enabled():
        # In the background: a large backlog must not hold the service's boot.
        threading.Thread(target=_seal_backlog, name="seal-backlog", daemon=True).start()


def _seal_backlog() -> None:
    """Seal what was written before encryption was on, and re-seal what an older
    key sealed, in batches. Idempotent: every boot runs it, and a finished
    backlog costs one empty query."""
    active = crypto.keyring().active_id
    sealed = failed_total = 0
    unreadable: set[str] = set()
    while True:
        with SessionLocal() as db:
            rows = db.scalars(
                select(models.Message)
                .where(
                    models.Message.encrypted.is_(False),
                    models.Message.id.not_in(unreadable) if unreadable else true(),
                    or_(
                        (models.Message.sealed_with.is_(None))
                        & (models.Message.body.is_not(None) | models.Message.media_name.is_not(None)),
                        models.Message.sealed_with != active,
                    ),
                )
                .limit(500)
                .with_for_update(skip_locked=True)
            ).all()
            if not rows:
                break
            for message in rows:
                try:
                    body, name = _plain(message, strict=True)
                except crypto.DecryptionError:
                    # Its key is gone. Leave it exactly as it is — re-sealing
                    # "nothing" over it would destroy the only copy.
                    unreadable.add(message.id)
                    failed_total += 1
                    continue
                _seal(message, body, name)
                sealed += 1
            db.commit()

    # Notification previews used to carry message text in the clear.
    with SessionLocal() as db:
        db.execute(
            models.Notification.__table__.update()
            .where(models.Notification.kind == "message", models.Notification.body.is_not(None))
            .values(body=None)
        )
        db.commit()
    if sealed or failed_total:
        log.info("sealed %d existing messages with key %s; %d could not be opened", sealed, active, failed_total)


def _media_fields(message: models.Message) -> dict:
    """The attachment as every response and frame describes it.

    The link is signed: attachments are private, and only someone who can read
    this message is ever handed one (common/crypto.py, signed media links).
    """
    return {
        "media_url": (
            crypto.sign_media_url(message.media_url, message.media_id)
            if message.media_url and message.media_id
            else message.media_url
        ),
        "media_kind": message.media_kind,
        "media_name": _plain(message)[1],
        "media_type": message.media_type,
        "media_size": message.media_size,
    }


MEDIA_LABELS = {"image": "a photo", "video": "a video", "audio": "a voice or audio message",
                "document": "a document", "file": "a file"}


def _preview(message: models.Message) -> str:
    """One line for a notification or the conversation list — never ciphertext."""
    if message.kind == "sticker":
        # A public catalogue id: nothing to hide, in encrypted rooms either.
        return "Sent a sticker"
    if message.encrypted:
        return "Encrypted message"
    text = (_plain(message)[0] or "").strip()
    if message.media_kind:
        label = MEDIA_LABELS.get(message.media_kind, "a file")
        return f"Sent {label}" + (f": {text}" if text else "")
    return text


async def _tell_room(db: OrmSession, conversation_id: str, payload: dict) -> None:
    """Tell everyone in the room, the actor included.

    Their other tabs and devices have the same thread open and must not keep
    showing a message that has just been edited, deleted or reacted to.
    """
    await _publish_to_users(_participant_ids(db, conversation_id), payload)


class MessageEditIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class ReactionIn(BaseModel):
    # A short allowlist rather than "any string": an emoji field that accepts
    # arbitrary text is a second message box with no length limit and no
    # moderation, sitting under every message.
    emoji: str = Field(pattern="^(\U0001F44D|\U0001F44E|\u2764\ufe0f|\U0001F602|\U0001F62E|\U0001F622|\U0001F64F|\U0001F525)$")


@app.get("/stickers", tags=["messages"])
def sticker_catalogue(principal: CurrentUser):
    """The sticker packs. Signed in only, like everything else in here."""
    return {"packs": stickers.catalogue()}


@app.patch("/conversations/{conversation_id}/messages/{message_id}", tags=["messages"])
async def edit_message(
    conversation_id: str,
    message_id: str,
    payload: MessageEditIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Change the text of a message you sent.

    Only your own, only text, and never silently: `edited_at` is returned with
    the message and the client marks it. An edit nobody can see is a way to
    change what somebody appears to have agreed to after they agreed to it.

    An end-to-end encrypted message cannot be edited here - the server holds
    ciphertext it cannot open, so there is nothing to replace. Saying so is
    better than appearing to accept the edit and discarding it.
    """
    # The same standing check as sending: somebody who was blocked, or whose
    # messages were closed to this member, must not be able to rewrite what
    # this member sees by going back to an old message.
    message = await _react_guard(db, conversation_id, message_id, principal)
    if message.deleted_at:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != principal.user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own messages")
    if message.encrypted:
        raise HTTPException(
            status_code=409, detail="An end-to-end encrypted message cannot be edited")
    if message.kind == "sticker":
        raise HTTPException(status_code=409, detail="A sticker has no text to edit")

    # The attachment's name is kept: editing a caption must not erase what the file is called.
    _seal(message, payload.body, _plain(message)[1])
    message.edited_at = datetime.now(timezone.utc)
    db.commit()

    await _tell_room(db, conversation_id, {
        "type": "message_edited", "conversation_id": conversation_id,
        "message_id": message.id, "body": payload.body,
        "edited_at": message.edited_at.isoformat(),
    })
    return {"id": message.id, "body": payload.body, "edited_at": message.edited_at}


@app.delete("/conversations/{conversation_id}/messages/{message_id}", tags=["messages"])
async def delete_message(
    conversation_id: str,
    message_id: str,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Delete a message you sent.

    The row stays and the words go. The row is what lets the thread say "this
    was deleted" rather than silently resequencing a conversation somebody is
    reading - but the text, the ciphertext and the attachment reference are
    cleared from the database, not merely hidden, which is the same promise
    disappearing messages make a few lines up. A message that vanishes from the
    screen while sitting in the database has not been deleted.

    Reactions to it go too: a row of thumbs-ups attached to nothing is a
    reminder of what was there, which is the opposite of deleting it.
    """
    _member(db, conversation_id, principal.user_id)
    message = db.get(models.Message, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != principal.user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own messages")
    if message.deleted_at:
        return {"id": message.id, "deleted": True, "already": True}

    message.deleted_at = datetime.now(timezone.utc)
    message.body = None
    message.ciphertext = None
    message.media_url = None
    message.media_id = None
    message.media_kind = None
    message.media_name = None
    message.media_type = None
    message.media_size = None
    message.sticker_id = None
    message.sealed_with = None
    db.execute(delete(models.MessageReaction).where(models.MessageReaction.message_id == message.id))
    db.commit()

    await _tell_room(db, conversation_id, {
        "type": "message_deleted", "conversation_id": conversation_id, "message_id": message.id,
    })
    return {"id": message.id, "deleted": True}


@app.post("/conversations/{conversation_id}/messages/{message_id}/reactions", tags=["messages"])
async def react_to_message(
    conversation_id: str,
    message_id: str,
    payload: ReactionIn,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """React, change the reaction, or take it back.

    Tapping the one already there removes it; tapping a different one replaces
    it. One per person per message, so a reaction stays a quiet acknowledgement
    rather than a way to fill somebody's thread.
    """
    message = await _react_guard(db, conversation_id, message_id, principal)
    if message.deleted_at:
        raise HTTPException(status_code=404, detail="Message not found")

    for attempt in (1, 2):
        existing = db.scalar(
            select(models.MessageReaction).where(
                models.MessageReaction.message_id == message_id,
                models.MessageReaction.user_id == principal.user_id,
            )
        )
        mine: str | None = payload.emoji
        if existing and existing.emoji == payload.emoji:
            db.delete(existing)
            mine = None
        elif existing:
            existing.emoji = payload.emoji
        else:
            db.add(models.MessageReaction(
                message_id=message_id, conversation_id=conversation_id,
                user_id=principal.user_id, emoji=payload.emoji,
            ))
        try:
            db.commit()
            break
        except IntegrityError:
            # Two taps raced past the lookup and the unique constraint kept one.
            # Look again: the second tap now finds the first and acts on it.
            db.rollback()
            if attempt == 2:
                raise HTTPException(status_code=409, detail="Try that reaction again")

    counts = _reaction_counts(db, [message_id]).get(message_id, {})
    await _tell_room(db, conversation_id, {
        "type": "message_reaction", "conversation_id": conversation_id,
        "message_id": message_id, "counts": counts,
    })
    return {"message_id": message_id, "counts": counts, "mine": mine}


def _reaction_counts(db: OrmSession, message_ids: list[str]) -> dict[str, dict[str, int]]:
    """How many of each emoji, per message, in one query for the page."""
    if not message_ids:
        return {}
    rows = db.execute(
        select(models.MessageReaction.message_id, models.MessageReaction.emoji, func.count())
        .where(models.MessageReaction.message_id.in_(message_ids))
        .group_by(models.MessageReaction.message_id, models.MessageReaction.emoji)
    ).all()
    out: dict[str, dict[str, int]] = {}
    for message_id, emoji, count in rows:
        out.setdefault(message_id, {})[emoji] = count
    return out


def _my_reactions(db: OrmSession, viewer: str, message_ids: list[str]) -> dict[str, str]:
    if not message_ids:
        return {}
    rows = db.execute(
        select(models.MessageReaction.message_id, models.MessageReaction.emoji).where(
            models.MessageReaction.message_id.in_(message_ids),
            models.MessageReaction.user_id == viewer,
        )
    ).all()
    return {message_id: emoji for message_id, emoji in rows}


@app.post("/conversations/{conversation_id}/messages", status_code=201, tags=["messages"])
async def send_message(
    conversation_id: str,
    payload: MessageIn,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    sender = _member(db, conversation_id, principal.user_id)
    conversation = db.get(models.Conversation, conversation_id)
    _purge_expired(db, conversation_id)

    if conversation.kind == "direct":
        # Asked on every message, not only when the thread was created: a
        # block, or `who_can_message` turned to "nobody", has to end an
        # existing conversation too — otherwise the setting protects nobody
        # from the one person they already talk to. Fails closed, like every
        # other permission check.
        other = next(
            (uid for uid in _participant_ids(db, conversation_id) if uid != principal.user_id), None
        )
        if other:
            allowed, reason = await asyncio.to_thread(
                permissions.check, principal.user_id, other, "can_message", "message this member"
            )
            if not allowed:
                raise HTTPException(status_code=403, detail=reason)

    if payload.client_id:
        # A retry of a send whose response was lost: answer with what is
        # already stored rather than storing it — and announcing it — twice.
        existing = db.scalar(
            select(models.Message).where(
                models.Message.sender_id == principal.user_id,
                models.Message.client_id == payload.client_id,
            )
        )
        if existing is not None:
            return {
                "id": existing.id,
                "created_at": existing.created_at,
                "client_id": existing.client_id,
                "duplicate": True,
            }

    if payload.sticker_id and "kind" not in payload.model_fields_set:
        # A client that names a sticker and no kind means a sticker. Naming a
        # different kind with a sticker_id is still refused below.
        payload.kind = "sticker"

    if payload.kind == "sticker":
        # A sticker message is a catalogue id and nothing else: no text, no
        # ciphertext, no attachment, no URL. That is what lets it be sent in an
        # end-to-end encrypted room too - there is nothing private in it to
        # protect, and nothing the client chose that the server has not checked.
        if stickers.get(payload.sticker_id) is None:
            raise HTTPException(status_code=400, detail="Unknown sticker")
        if payload.body or payload.ciphertext_b64 or payload.media_id:
            raise HTTPException(status_code=400, detail="A sticker message carries only a sticker id")
    elif payload.sticker_id:
        raise HTTPException(status_code=400, detail="sticker_id is only for sticker messages")
    elif conversation.encrypted:
        if payload.media_id:
            # The file would sit on the server in the clear, which is exactly
            # what an encrypted room promises will not happen.
            raise HTTPException(
                status_code=400,
                detail="Attachments cannot be sent in an end-to-end encrypted conversation yet.",
            )
        if not payload.ciphertext_b64:
            raise HTTPException(
                status_code=400,
                detail="This conversation is end-to-end encrypted: send ciphertext_b64, not plaintext.",
            )
        if payload.body:
            raise HTTPException(
                status_code=400,
                detail="Refusing to store plaintext in an encrypted conversation.",
            )
    elif not (payload.body and payload.body.strip()) and not payload.media_id:
        raise HTTPException(status_code=400, detail="A message needs text or an attachment")

    if payload.reply_to_id:
        # Looked up inside this conversation, never by id alone: a bare lookup
        # would confirm that a message exists in a room the sender is not in.
        # An expired message was purged above, so it is refused like any other
        # id that is not here.
        quoted = db.scalar(
            select(models.Message.id).where(
                models.Message.id == payload.reply_to_id,
                models.Message.conversation_id == conversation_id,
            )
        )
        if quoted is None:
            raise HTTPException(status_code=400, detail="The message you are replying to is not in this conversation")

    attachment = await _attachment(payload.media_id, principal.user_id) if payload.media_id else None

    # Text first. An attachment sent before the other side accepted has already
    # been seen by the time anybody can report it.
    # A sticker is our own catalogue artwork, not a file the member supplied, so
    # it is not held back like an attachment.
    if payload.media_id or payload.kind not in ("text", "", "sticker"):
        media_ok, media_reason = agecheck.may_send_media(db, principal.user_id, conversation_id)
        if not media_ok:
            raise HTTPException(status_code=403, detail=media_reason)

    # Replying accepts the conversation: it is a clearer statement of consent
    # than any button, and it is what people actually do.
    _accept(db, conversation_id, principal.user_id)

    message = models.Message(
        id=new_id("msg"),
        conversation_id=conversation_id,
        sender_id=principal.user_id,
        encrypted=conversation.encrypted,
        ciphertext=base64.b64decode(payload.ciphertext_b64) if payload.ciphertext_b64 else None,
        kind="media" if attachment else payload.kind,
        media_url=attachment["url"] if attachment else None,
        media_id=attachment["id"] if attachment else None,
        media_kind=attachment["kind"] if attachment else None,
        media_type=attachment["content_type"] if attachment else None,
        media_size=attachment["size_bytes"] if attachment else None,
        lang=payload.lang,
        client_id=payload.client_id,
        reply_to_id=payload.reply_to_id,
        sticker_id=payload.sticker_id,
        # The deadline is computed from the room's setting, not sent by the
        # client: letting the sender choose would let them set a shorter timer
        # than the room agreed to, or none at all.
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=conversation.disappear_after_seconds)
            if conversation.disappear_after_seconds
            else None
        ),
    )
    _seal(
        message,
        None if conversation.encrypted else payload.body,
        attachment["filename"] if attachment else None,
    )
    db.add(message)
    now = datetime.now(timezone.utc)
    conversation.last_message_at = now
    # Writing into a thread means having read it: the sender's own message
    # must never count as unread for them.
    sender.last_read_at = now
    try:
        db.commit()
    except IntegrityError:
        # Two retries raced past the lookup above; the unique index kept one.
        db.rollback()
        existing = db.scalar(
            select(models.Message).where(
                models.Message.sender_id == principal.user_id,
                models.Message.client_id == payload.client_id,
            )
        )
        return {
            "id": existing.id,
            "created_at": existing.created_at,
            "client_id": existing.client_id,
            "duplicate": True,
        }

    # Everyone in the room, the sender included: their other tabs and devices
    # must show what they just sent, not only the one that sent it.
    participants = _participant_ids(db, conversation_id)
    frame = {
            "mentions": (
                _mention_names([_plain(message)[0]])
                if not message.encrypted and "@" in (_plain(message)[0] or "")
                else {}
            ),
            "type": "message",
            "conversation_id": conversation_id,
            "message_id": message.id,
            # Lets the sending tab swap its pending bubble for this one.
            "client_id": message.client_id,
            "sender_id": principal.user_id,
            "encrypted": message.encrypted,
            # The body travels only for conversations that are not
            # end-to-end encrypted; for E2E ones the server has no
            # plaintext to send and the client fetches the ciphertext.
            "body": None if message.encrypted else _plain(message)[0],
            "kind": message.kind,
            # The id only. Whoever receives this already holds the original
            # (or shows "older message"); the server never echoes its text.
            "reply_to_id": message.reply_to_id,
            "sticker_id": message.sticker_id,
            **_media_fields(message),
            "sticker": stickers.get(message.sticker_id),
            "created_at": message.created_at.isoformat(),
    }
    # The same frame, without the attachment, for a minor who has not accepted
    # this group: the link is signed and would open the file for them.
    withheld = (
        _withheld_from(db, db.get(models.Conversation, conversation_id)) - {principal.user_id}
        if message.media_kind
        else set()
    )
    deliveries = await _publish_to_users([uid for uid in participants if uid not in withheld], frame)
    if withheld:
        deliveries.update(await _publish_to_users(list(withheld), {**frame, **WITHHELD_MEDIA}))

    # Someone with no socket open would otherwise learn about this only by
    # opening Messages on the off chance. One waiting notification per thread
    # is enough: ten messages in a row are one reason to come back, not ten.
    link = f"/messages?c={conversation_id}"
    offline = [uid for uid in participants if uid != principal.user_id and not deliveries.get(uid)]
    # Resolved once for the whole fan-out, and only when there is somebody to
    # notify: a name lookup per recipient would be a request per member.
    # `_profiles` rather than `_display_names`, which falls back to a truncated
    # raw id - and "New message from usr_01M4TW" is worse than no name at all.
    sender_name = (
        ((_profiles({principal.user_id}).get(principal.user_id) or {}).get("display_name") or None)
        if offline
        else None
    )
    for uid in offline:
        waiting = db.scalar(
            select(models.Notification.id).where(
                models.Notification.user_id == uid,
                models.Notification.kind == "message",
                models.Notification.link == link,
                models.Notification.read_at.is_(None),
            )
        )
        if waiting is None:
            db.add(
                models.Notification(
                    user_id=uid,
                    kind="message",
                    # The sender's name, not their handle. The token carries
                    # only the handle, so this is the one lookup it takes to
                    # stop every message notification reading "@uqg5ptjlh" -
                    # which on a lock screen names nobody.
                    title=f"New message from {sender_name}" if sender_name else "New message",
                    # Never the text of an encrypted message: the server has none.
                    # Nor any text at all while encryption at rest is on: the
                    # notification table would be a plaintext copy of the
                    # messages the key is protecting.
                    body=None if message.encrypted or crypto.enabled() else _preview(message)[:140],
                    link=link,
                )
            )
    if offline:
        db.commit()

    await _notify_mentions(db, conversation, message, principal.user_id, sender_name, participants, background)

    return {"id": message.id, "created_at": message.created_at, "client_id": message.client_id}


# How a handle is written in a message: the same shape the post composer and
# the mention picker use. Preceded by the start or a space, so an email address
# is not a mention of anybody.
MENTION_RE = re.compile(r"(?:^|(?<=\s))@([a-z0-9_.]{2,40})", re.IGNORECASE)
# A message naming more people than this is a broadcast, not a mention.
MAX_MENTIONS = 10


def _handles_in(texts, limit: int = 50) -> list[str]:
    handles: list[str] = []
    for text in texts:
        for raw in MENTION_RE.findall(text or ""):
            handle = raw.rstrip(".").lower()
            if handle and handle not in handles:
                handles.append(handle)
    return handles[:limit]


def _profiles_by_handle(handles: list[str]) -> dict[str, dict]:
    if not handles:
        return {}
    try:
        response = httpx.post(f"{USER_URL}/internal/profiles/by-handle", json={"handles": handles}, timeout=5)
        response.raise_for_status()
        return response.json().get("profiles", {}) or {}
    except Exception as exc:
        log.warning("could not resolve mentions: %s", exc)
        return {}


def _mentioned_ids(text: str) -> dict[str, str]:
    """{user_id: handle} for the @handles in a message, resolved by user-service."""
    profiles = _profiles_by_handle(_handles_in([text], MAX_MENTIONS))
    return {p["user_id"]: h for h, p in profiles.items() if p and p.get("user_id")}


def _mention_names(texts) -> dict[str, str]:
    """{handle: display name} for the @handles in some messages, so a thread
    can show who was named as well as their username. A handle nobody has is
    absent, and is drawn as typed."""
    profiles = _profiles_by_handle(_handles_in(texts))
    return {h: p.get("display_name") for h, p in profiles.items() if p and p.get("display_name")}


async def _notify_mentions(
    db: OrmSession,
    conversation: models.Conversation,
    message: models.Message,
    sender_id: str,
    sender_name: str | None,
    participants: list[str],
    background: BackgroundTasks,
) -> None:
    """Tell whoever was @mentioned, if they are in the room.

    Somebody outside the conversation is never told: a mention is not a way to
    reach a person the room's own rules keep out, nor to announce to them that
    the room exists. Inside it, a mention is delivered even where ordinary
    message notifications are not - the welcome group pushes nothing for its
    traffic, and being named is the case where you do want to hear.

    The server reads the text only when it can: an end-to-end encrypted
    message carries no plaintext here, so its mentions stay between the
    devices. The notification never quotes the message either.
    """
    if message.encrypted or message.kind == "system":
        return
    text = _plain(message)[0] or ""
    if "@" not in text:
        return
    present = set(participants)
    named = {uid: h for uid, h in _mentioned_ids(text).items() if uid in present and uid != sender_id}
    if not named:
        return
    who = sender_name or (_profiles({sender_id}).get(sender_id) or {}).get("display_name") or "Someone"
    where = conversation.title if conversation.kind == "group" and conversation.title else None
    for uid in named:
        await internal_notify(
            user_id=uid,
            background=background,
            kind="mention",
            title=f"{who} mentioned you" + (f" in {where}" if where else ""),
            body=None,
            link=f"/messages?c={conversation.id}",
            db=db,
        )


# --- reactions -----------------------------------------------------------------
#
# One emoji per member per message (see react_to_message above). Members of the
# conversation only, for reading and for writing: a reaction is a small piece of
# information about who is in the room and how they took a message, so it is
# held to the same rule as the message itself.

async def _react_guard(
    db: OrmSession, conversation_id: str, message_id: str, principal
) -> models.Message:
    """Membership, then that the message is in this room, then who may talk to whom."""
    _member(db, conversation_id, principal.user_id)
    conversation = db.get(models.Conversation, conversation_id)
    _purge_expired(db, conversation_id)
    message = db.scalar(
        select(models.Message).where(
            models.Message.id == message_id,
            models.Message.conversation_id == conversation_id,
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if conversation.kind == "direct":
        # The same standing check as sending: a block, or "nobody can message
        # me", ends reactions as much as it ends messages. Fails closed.
        other = next(
            (uid for uid in _participant_ids(db, conversation_id) if uid != principal.user_id), None
        )
        if other:
            allowed, reason = await asyncio.to_thread(
                permissions.check, principal.user_id, other, "can_message", "message this member"
            )
            if not allowed:
                raise HTTPException(status_code=403, detail=reason)
    return message


@app.post("/conversations/{conversation_id}/read", tags=["messages"])
async def mark_conversation_read(
    conversation_id: str,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Record that the member has read the thread up to now, and say so.

    Everyone in the room is told — the other side to draw "Seen", the reader's
    own other tabs to clear the unread badge they are still showing.
    """
    participant = _member(db, conversation_id, principal.user_id)
    participant.last_read_at = datetime.now(timezone.utc)
    db.commit()
    await _publish_to_users(
        _participant_ids(db, conversation_id),
        {
            "type": "read",
            "conversation_id": conversation_id,
            "user_id": principal.user_id,
            "read_at": participant.last_read_at.isoformat(),
        },
    )
    return {"read_at": participant.last_read_at}


@app.get("/conversations/{conversation_id}/messages", tags=["messages"])
def list_messages(
    conversation_id: str,
    principal: CurrentUser,
    limit: int = 50,
    before: str | None = None,
    after: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """A page of the thread, oldest first.

    ``before`` pages back through history. ``after`` is the catch-up a client
    makes when its socket comes back: everything sent while it was away, so a
    reconnect does not silently drop what arrived during the gap.
    """
    me = _member(db, conversation_id, principal.user_id)
    # A minor who has not accepted a group reads its text, and is handed no
    # attachment but their own until they do.
    withhold = me.accepted_at is None and principal.user_id in _withheld_from(
        db, db.get(models.Conversation, conversation_id)
    )
    # Before serving, not after: an expired message must never be in a response.
    _purge_expired(db, conversation_id)
    stmt = select(models.Message).where(models.Message.conversation_id == conversation_id)
    # A group that keeps its history from newcomers: what was said before this
    # member joined is not theirs to read. Admins read everything.
    room = db.get(models.Conversation, conversation_id)
    if (
        room is not None
        and room.kind == "group"
        and (room.history_for_new or "all") == "from_join"
        and me.role not in ("owner", "admin")
        and me.joined_at is not None
    ):
        stmt = stmt.where(models.Message.created_at >= me.joined_at)
    if before:
        stmt = stmt.where(models.Message.id < before)
    if after:
        # Compared on time, not id: ids share a millisecond prefix but their
        # random tail does not order two messages sent in the same millisecond.
        # The anchor itself comes back too; the client already de-duplicates.
        anchor = db.scalar(
            select(models.Message.created_at).where(
                models.Message.id == after,
                models.Message.conversation_id == conversation_id,
            )
        )
        stmt = stmt.where(
            models.Message.created_at >= anchor if anchor else models.Message.id > after
        )
        # Catch-up wants the oldest missed messages first, not the newest page.
        rows = db.scalars(stmt.order_by(models.Message.created_at.asc()).limit(min(limit, 100))).all()
    else:
        rows = list(reversed(db.scalars(
            stmt.order_by(models.Message.created_at.desc()).limit(min(limit, 100))
        ).all()))

    # One query for the whole page: which of the quoted messages still exist.
    # A message deleted by its sender keeps its row but not its words, so it
    # counts as gone here: a reply must not keep pointing at something that was
    # deleted.
    quoted_ids = {r.reply_to_id for r in rows if r.reply_to_id}
    alive = (
        set(db.scalars(
            select(models.Message.id).where(
                models.Message.id.in_(quoted_ids),
                models.Message.conversation_id == conversation_id,
                models.Message.deleted_at.is_(None),
            )
        ).all())
        if quoted_ids
        else set()
    )

    # How many of each emoji under each message of this page, and which one is
    # the reader's own: two queries for the whole page.
    ids = [r.id for r in rows]
    counts = _reaction_counts(db, ids)
    mine = _my_reactions(db, principal.user_id, ids)

    # Fetching is not reading. A catch-up in a background tab, or a page of
    # history, must not tell the other side "Seen": the client posts /read
    # when the thread is actually on screen.
    return {
        "items": [
            {
                "id": r.id,
                "sender_id": r.sender_id,
                "encrypted": r.encrypted,
                # The server hands back what it stored; only the client can decrypt.
                "ciphertext_b64": base64.b64encode(r.ciphertext).decode() if r.ciphertext else None,
                "body": None if r.deleted_at else _plain(r)[0],
                "kind": r.kind,
                "reply_to_id": r.reply_to_id,
                "sticker_id": r.sticker_id,
                # Whether the original is gone (expired). Never its content.
                "reply_to_deleted": bool(r.reply_to_id) and r.reply_to_id not in alive,
                **_media_fields(r),
                **(WITHHELD_MEDIA if withhold and r.media_kind and r.sender_id != principal.user_id else {}),
                "sticker": stickers.get(r.sticker_id),
                "edited_at": r.edited_at,
                # A deleted message keeps its place in the thread and loses its
                # words; the client draws the tombstone.
                "deleted": bool(r.deleted_at),
                "reactions": counts.get(r.id, {}),
                "my_reaction": mine.get(r.id),
                "created_at": r.created_at,
            }
            for r in rows
        ],
        # Who wrote this page. One batch for the page, so a thread can name its
        # senders without the conversation list having to carry a profile for
        # every member of the room - which, for a group that holds the whole
        # platform, is most of the platform. A system line has no sender.
        "profiles": _profiles({r.sender_id for r in rows if r.sender_id}),
        # handle -> name for everybody @mentioned on this page, in one call.
        "mentions": _mention_names(
            _plain(r)[0] for r in rows if not r.encrypted and not r.deleted_at and r.kind != "system"
        ),
    }


# Close codes the client acts on. 4401 means "get a fresh access token and
# come back", so it must only ever mean that.
WS_UNAUTHORIZED = 4401
AUTH_FRAME_TIMEOUT_S = 10
# A socket closing is not a member leaving: token refreshes and page reloads
# reconnect within a second or two, and announcing "offline, online" for each
# would make every dot flicker. Only a gap longer than this is announced.
OFFLINE_GRACE_S = 5
# Typing frames from one socket for one thread are relayed at most this often.
TYPING_MIN_INTERVAL_S = 2.0

_announced_online: set[str] = set()
# Strong references to fire-and-forget tasks; asyncio only keeps weak ones.
_background: set[asyncio.Task] = set()


def _in_background(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _socket_opened(user_id: str) -> None:
    _online[user_id] = _online.get(user_id, 0) + 1
    if user_id not in _announced_online:
        _announced_online.add(user_id)
        # Telling the audience involves a call to user-service; the socket
        # should not wait on it before it can deliver anything.
        _in_background(_announce_presence(user_id, True))


async def _socket_closed(user_id: str) -> None:
    _online[user_id] = max(0, _online.get(user_id, 0) - 1)
    if _online[user_id]:
        return
    left_at = datetime.now(timezone.utc)
    await asyncio.sleep(OFFLINE_GRACE_S)
    if _online.get(user_id, 0) == 0 and user_id in _announced_online:
        _announced_online.discard(user_id)
        _last_seen[user_id] = left_at
        await _announce_presence(user_id, False)


async def _relay_typing(socket_state: dict, user_id: str, conversation_id: str) -> None:
    """Tell the others in a thread that this member is typing.

    Membership is checked once per thread per socket, then remembered: a
    typing frame arrives every couple of seconds while someone writes.
    """
    now = time.monotonic()
    last = socket_state["typing_at"].get(conversation_id, 0.0)
    if now - last < TYPING_MIN_INTERVAL_S:
        return
    socket_state["typing_at"][conversation_id] = now

    members = socket_state["members"].get(conversation_id)
    if members is None:
        with SessionLocal() as db:
            members = _participant_ids(db, conversation_id)
        if user_id not in members:
            return  # not your conversation: say nothing, not even "no"
        socket_state["members"][conversation_id] = members
    # Re-read at most every 30 s, so a block made mid-conversation takes hold
    # without a lookup on every keystroke burst.
    if now - socket_state.get("blocked_at", -1e9) > 30:
        socket_state["blocked"] = await _blocked_with(user_id)
        socket_state["blocked_at"] = now
    blocked = socket_state["blocked"]
    if blocked is None:
        return  # unknown: typing is a courtesy, not worth the risk
    await _publish_to_users(
        [m for m in members if m != user_id and m not in blocked],
        {"type": "typing", "conversation_id": conversation_id, "user_id": user_id},
    )


async def _close_quietly(socket: WebSocket, code: int) -> None:
    """Close, unless the other side already has — which is not an error."""
    try:
        await socket.close(code=code)
    except RuntimeError:
        pass


@app.websocket("/ws")
async def websocket(socket: WebSocket):
    """Live delivery.

    The access token arrives in the **first frame** — ``{"action": "auth",
    "token": …}`` — not in the URL. Browsers cannot set headers on a WebSocket
    handshake, and a token in the query string is written to every proxy's
    access log on the way here.

    The socket lives only as long as the token it was opened with. At expiry
    the server closes it with 4401; the client refreshes and reconnects, then
    catches up on anything sent in between. Otherwise a socket opened once
    would keep delivering long after the session behind it was gone.
    """
    await socket.accept()
    try:
        first = json.loads(await asyncio.wait_for(socket.receive_text(), AUTH_FRAME_TIMEOUT_S))
    except WebSocketDisconnect:
        return  # gone before saying who it was; nothing to close
    except (asyncio.TimeoutError, ValueError):
        await _close_quietly(socket, WS_UNAUTHORIZED)
        return
    claims = (
        decode_token(str(first.get("token", "")), expected_type=ACCESS)
        if isinstance(first, dict) and first.get("action") == "auth"
        else None
    )
    if not claims:
        await _close_quietly(socket, WS_UNAUTHORIZED)
        return

    user_id = str(claims["sub"])
    expires_at = float(claims.get("exp", 0)) or None
    # Your own channel is not opt-in: a member should not have to ask to be
    # told they were messaged.
    _subscribe(socket, [f"user:{user_id}"])
    state: dict = {"typing_at": {}, "members": {}}
    _socket_opened(user_id)

    try:
        await socket.send_json({"type": "ready", "topics": [f"user:{user_id}"]})
        while True:
            remaining = expires_at - time.time() if expires_at else None
            if remaining is not None and remaining <= 0:
                await _close_quietly(socket, WS_UNAUTHORIZED)
                break
            try:
                raw = await asyncio.wait_for(socket.receive_text(), remaining)
            except asyncio.TimeoutError:
                # The token ran out while the socket sat idle.
                await _close_quietly(socket, WS_UNAUTHORIZED)
                break
            # Anything unparseable is treated as a keep-alive ping rather than
            # a reason to drop the connection.
            try:
                frame = json.loads(raw)
            except Exception:
                continue
            if not isinstance(frame, dict):
                continue
            action = frame.get("action")
            if action == "typing":
                conversation_id = frame.get("conversation_id")
                if isinstance(conversation_id, str) and conversation_id:
                    await _relay_typing(state, user_id, conversation_id)
                continue
            topics = [t for t in frame.get("topics", []) if isinstance(t, str)]
            # A client may not subscribe to another member's private channel.
            topics = [t for t in topics if not (t.startswith("user:") and t != f"user:{user_id}")]
            if action == "subscribe":
                topics = await _readable_topics(user_id, topics)
                _subscribe(socket, topics)
                await socket.send_json({"type": "subscribed", "topics": topics})
            elif action == "unsubscribe":
                _unsubscribe(socket, topics)
    except WebSocketDisconnect:
        pass
    finally:
        _unsubscribe(socket)
        # In the background: the grace period must not hold this handler open.
        _in_background(_socket_closed(user_id))


class BroadcastIn(BaseModel):
    topic: str
    type: str
    data: dict = Field(default_factory=dict)


@app.post("/internal/broadcast", tags=["internal"])
async def internal_broadcast(payload: BroadcastIn):
    """Push a live update into a topic.

    Internal: the gateway never proxies ``/internal/*``, so only services on the
    private network can reach it. Callers treat it as best-effort — a hub that
    is down must slow nothing and fail nothing, it only costs liveness.
    """
    delivered = await _publish(
        payload.topic, {"type": payload.type, "topic": payload.topic, **payload.data}
    )
    return {"delivered": delivered}


# --- notifications ---------------------------------------------------------

@app.get("/notifications", tags=["notifications"])
def notifications(principal: CurrentUser, unread_only: bool = False, limit: int = 50, db: OrmSession = Depends(get_db)):
    stmt = select(models.Notification).where(models.Notification.user_id == principal.user_id)
    if unread_only:
        stmt = stmt.where(models.Notification.read_at.is_(None))
    rows = db.scalars(stmt.order_by(models.Notification.created_at.desc()).limit(min(limit, 200))).all()
    unread = db.scalar(
        select(func.count())
        .select_from(models.Notification)
        .where(models.Notification.user_id == principal.user_id, models.Notification.read_at.is_(None))
    ) or 0
    return {
        "unread": unread,
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "title": r.title,
                "body": r.body,
                "link": r.link,
                "read": r.read_at is not None,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


@app.post("/notifications/read", tags=["notifications"])
def mark_read(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    for row in db.scalars(
        select(models.Notification).where(
            models.Notification.user_id == principal.user_id, models.Notification.read_at.is_(None)
        )
    ).all():
        row.read_at = now
    db.commit()
    return {"read": True}


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    p256dh: str = Field(min_length=10, max_length=255)
    auth: str = Field(min_length=4, max_length=255)


@app.get("/push/key", tags=["push"])
def push_public_key():
    """The VAPID public key, which a browser needs to create a subscription.

    Not a secret - it identifies this server to the push services and nothing
    else. `available` lets the client ask once rather than offer a switch that
    cannot work: an installation with no keys configured should say so, not
    collect subscriptions it can never send to.
    """
    return {
        "available": webpush.available(),
        "public_key": settings.VAPID_PUBLIC_KEY if webpush.available() else None,
    }


@app.post("/push/subscribe", status_code=201, tags=["push"])
def push_subscribe(
    payload: PushSubscriptionIn,
    principal: CurrentUser,
    request: Request,
    db: OrmSession = Depends(get_db),
):
    """Remember this browser so it can be reached when the app is closed.

    Keyed on the endpoint, which is the browser's own identifier for the
    subscription. Re-subscribing the same browser updates the row rather than
    adding one, because two rows for one browser is every notification arriving
    twice.

    An endpoint that already belongs to somebody else is reassigned, not
    refused: it means this browser was signed in as another member and is now
    signed in as this one, and the notifications must follow who is actually
    using it.
    """
    if not webpush.available():
        raise HTTPException(
            status_code=503, detail="Push notifications are not available on this installation")
    if not webpush.endpoint_allowed(payload.endpoint):
        # The server will POST to this address, so only a browser vendor's push
        # service is accepted (see webpush.ALLOWED_PUSH_HOSTS).
        raise HTTPException(status_code=400, detail="That is not a supported push service")

    existing = db.scalar(
        select(models.PushSubscription).where(models.PushSubscription.endpoint == payload.endpoint)
    )
    if existing is None:
        existing = models.PushSubscription(endpoint=payload.endpoint)
        db.add(existing)
    existing.user_id = principal.user_id
    existing.p256dh = payload.p256dh
    existing.auth = payload.auth
    existing.user_agent = request.headers.get("user-agent")
    existing.failures = 0
    db.commit()
    # Keep a member's most recent devices only.
    surplus = db.scalars(
        select(models.PushSubscription.id)
        .where(models.PushSubscription.user_id == principal.user_id)
        .order_by(models.PushSubscription.id.desc())
        .offset(webpush.MAX_SUBSCRIPTIONS_PER_MEMBER)
    ).all()
    if surplus:
        db.execute(delete(models.PushSubscription).where(models.PushSubscription.id.in_(surplus)))
        db.commit()
    return {"subscribed": True}


@app.delete("/push/subscribe", status_code=204, tags=["push"])
def push_unsubscribe(
    endpoint: str,
    principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Forget this browser.

    Only your own: the endpoint is not a secret worth relying on, so the
    member is checked rather than the string.
    """
    db.execute(
        delete(models.PushSubscription).where(
            models.PushSubscription.endpoint == endpoint,
            models.PushSubscription.user_id == principal.user_id,
        )
    )
    db.commit()


def _push_to_member(user_id: str, payload: dict) -> None:
    """Send one notification to every browser this member has registered.

    Runs in a background task with its own session: it talks to three or four
    external services over the network, and a notification must never be what
    makes storing a message slow.

    A subscription the push service calls gone is deleted at once. Anything
    else is counted, and counted enough times it is deleted too - a browser
    that was uninstalled looks exactly like one that is briefly unreachable,
    and the only difference is how long it keeps failing.
    """
    if not webpush.available():
        return
    session = SessionLocal()
    try:
        rows = session.scalars(
            select(models.PushSubscription).where(models.PushSubscription.user_id == user_id)
        ).all()
        for row in rows:
            if not webpush.endpoint_allowed(row.endpoint):
                # Registered before the allowlist existed, or by hand: never sent to.
                session.delete(row)
                continue
            subscription = {
                "endpoint": row.endpoint,
                "keys": {"p256dh": row.p256dh, "auth": row.auth},
            }
            try:
                webpush.send(subscription, payload)
                row.failures = 0
                row.last_sent_at = datetime.now(timezone.utc)
            except webpush.Gone:
                session.delete(row)
            except Exception as exc:
                row.failures += 1
                log.info("push failed for a subscription (%s): %s", row.failures, type(exc).__name__)
                if row.failures >= webpush.MAX_FAILURES:
                    session.delete(row)
        session.commit()
    except Exception as exc:
        log.warning("push delivery failed: %s", exc)
        session.rollback()
    finally:
        session.close()


@app.post("/internal/notify", status_code=201, tags=["internal"])
async def internal_notify(
    user_id: str,
    background: BackgroundTasks,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    lang: str = "en",
    db: OrmSession = Depends(get_db),
):
    """Store a notification, then deliver it live.

    Stored first, pushed second, and in that order deliberately: a socket that
    is not open must not lose the notification, and one that is open should not
    have to poll to learn about it. A member who was offline finds it waiting;
    a member who is looking sees it arrive.
    """
    # A notification quotes. "X replied: <their first line>" carries the reply
    # onto a lock screen, past every gate the reply itself sits behind - the
    # content was filtered and the notification about it was not.
    #
    # Screened before the row is built, so the stored copy and the live push
    # below carry the same text. Screening only one of them would mean the
    # socket showed what the notification list did not.
    title, body, redacted = agenotify.screen(user_id, kind, title, body)

    notification = models.Notification(
        user_id=user_id, kind=kind, title=title, body=body, link=link, lang=lang
    )
    db.add(notification)
    db.commit()

    unread = db.scalar(
        select(func.count())
        .select_from(models.Notification)
        .where(models.Notification.user_id == user_id, models.Notification.read_at.is_(None))
    ) or 0

    await _publish(
        f"user:{user_id}",
        {
            "type": "notification",
            "topic": f"user:{user_id}",
            "id": notification.id,
            "kind": kind,
            "title": title,
            "body": body,
            "link": link,
            "unread": unread,
            "created_at": notification.created_at.isoformat(),
        },
    )
    # And to the devices that are not looking. Queued rather than awaited: this
    # reaches out to the browser vendors' push services, and the caller is a
    # service that has already committed the thing being announced.
    #
    # The screened title and body, not the originals - a notification that
    # arrives on a lock screen must not carry what the age gate removed from
    # the one in the app.
    background.add_task(
        _push_to_member,
        user_id,
        {
            "title": title,
            "body": body or "",
            "link": link or "/",
            "kind": kind,
            "unread": unread,
            "tag": f"{kind}:{notification.id}",
        },
    )
    return {"id": notification.id, "unread": unread, "redacted": redacted}
