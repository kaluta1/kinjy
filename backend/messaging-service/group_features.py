"""Kinjy · messaging-service — the group features beyond talking.

Ownership and deletion, the share link's limits, invitations, pinned and saved
messages, search, the files view, read receipts, notification levels,
announcements, reply threads, polls, events, tasks, reports, the audit trail,
the link to a Community, and the reminders worker.

A group here is a group chat in Messages - separate from Communities and
Forums. Linking one to a Community is a pointer and nothing more: it grants
nobody membership of either.

Imported at the bottom of main.py, once everything it borrows from there is
defined, and registers its routes on the same app.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

import agecheck
import models
from common import permissions
from common.auth import CurrentUser, ModeratorUser
from common.database import SessionLocal, get_db
from common.ids import new_id
from main import (
    ANNOUNCE_PUSH_LIMIT,
    MODERATES,
    RUNS,
    STARTUP,
    WITHHELD_MEDIA,
    _admit,
    _announce_joined,
    _audit,
    _blocked_with,
    _discard_image,
    _display_names,
    _drop_attached,
    _group,
    _how_to_bring_in,
    _media_fields,
    _member,
    _moderates,
    _participant_ids,
    _plain,
    _profiles,
    _publish_to_users,
    _purge_expired,
    _runs_group,
    _seal,
    _system_message,
    _tell_invited,
    _tell_room,
    _visible_from,
    _wants,
    _withheld_from,
    app,
    internal_notify,
)

log = logging.getLogger("messaging-service.groups")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _room_and_me(db: OrmSession, conversation_id: str, user_id: str) -> tuple[models.Conversation, models.Participant]:
    me = _member(db, conversation_id, user_id)
    room = db.get(models.Conversation, conversation_id)
    if room is None or room.deleted_at:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return room, me


def _visible_message(
    db: OrmSession, room: models.Conversation, me: models.Participant, message_id: str
) -> models.Message:
    """A message of this room this member may read, or 404.

    Same answer for "not here" and "from before you joined" (when the group
    keeps its history from newcomers): either way it is not theirs to see.
    """
    message = db.scalar(
        select(models.Message).where(
            models.Message.id == message_id,
            models.Message.conversation_id == room.id,
        )
    )
    since = _visible_from(room, me)
    if message is None or (since is not None and message.created_at < since):
        raise HTTPException(status_code=404, detail="Message not found")
    return message


async def _changed(db: OrmSession, conversation_id: str) -> None:
    """Tell every member's open tabs the group itself changed."""
    await _publish_to_users(
        _participant_ids(db, conversation_id), {"type": "conversation", "conversation_id": conversation_id}
    )


def _name(user_id: str) -> str:
    return _display_names({user_id})[user_id]


# --- settings: announcement mode, member list, polls ------------------------

class SettingsIn(BaseModel):
    posting: str | None = Field(default=None, pattern="^(all|admins)$")
    members_visible: str | None = Field(default=None, pattern="^(all|admins)$")
    polls_by: str | None = Field(default=None, pattern="^(all|admins)$")

    model_config = {"extra": "forbid"}


SETTING_LINES = {
    ("posting", "admins"): " turned on announcement mode: only admins and moderators can write.",
    ("posting", "all"): " turned off announcement mode: everybody can write.",
    ("members_visible", "admins"): " made the member list visible to admins only.",
    ("members_visible", "all"): " made the member list visible to every member.",
    ("polls_by", "admins"): " set the group so only admins and moderators start polls.",
    ("polls_by", "all"): " set the group so every member can start polls.",
}


@app.patch("/conversations/{conversation_id}/settings", tags=["groups"])
async def group_settings(
    conversation_id: str, payload: SettingsIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Announcement mode, who sees the member list, who starts polls. Admins only."""
    room = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    who = _name(principal.user_id)
    changed = False
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None or (getattr(room, key) or "all") == value:
            continue
        setattr(room, key, value)
        _system_message(db, conversation_id, who + SETTING_LINES[(key, value)])
        _audit(db, conversation_id, principal.user_id, "setting_changed", detail=f"{key}={value}")
        changed = True
    db.commit()
    if changed:
        await _changed(db, conversation_id)
    return {
        "posting": room.posting or "all",
        "members_visible": room.members_visible or "all",
        "polls_by": room.polls_by or "all",
    }


# --- the share link: on, off, expiry, uses, a new code ----------------------

class InviteLinkIn(BaseModel):
    enabled: bool | None = None
    # Null removes the limit. Sent as absolute times by the client.
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1, le=100_000)

    model_config = {"extra": "forbid"}


def _link_out(room: models.Conversation) -> dict:
    return {
        "enabled": room.invite_enabled is not False,
        "expires_at": room.invite_expires_at,
        "max_uses": room.invite_max_uses,
        "uses": room.invite_uses or 0,
        "path": f"/messages/join/{room.invite_code}" if room.invite_code else None,
    }


@app.patch("/conversations/{conversation_id}/invite-link", tags=["groups"])
async def set_invite_link(
    conversation_id: str, payload: InviteLinkIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Turn the link on or off, or limit it in time or in uses. Admins only."""
    room = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    if room.is_welcome:
        raise HTTPException(status_code=400, detail="The welcome group has no link")
    sent = payload.model_dump(exclude_unset=True)
    if "expires_at" in sent and sent["expires_at"] is not None:
        expires = sent["expires_at"]
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= _now():
            raise HTTPException(status_code=422, detail="The expiry must be in the future")
        sent["expires_at"] = expires
    details = []
    for key, column in (("enabled", "invite_enabled"), ("expires_at", "invite_expires_at"), ("max_uses", "invite_max_uses")):
        if key in sent and (key != "enabled" or sent[key] is not None):
            setattr(room, column, sent[key])
            details.append(f"{key}={sent[key]}")
    if details:
        _audit(db, conversation_id, principal.user_id, "link_changed", detail=", ".join(details))
        if "enabled" in sent and sent["enabled"] is not None:
            _system_message(
                db, conversation_id,
                _name(principal.user_id) + (" turned the group's link on." if sent["enabled"] else " turned the group's link off."),
            )
    db.commit()
    await _changed(db, conversation_id)
    return _link_out(room)


@app.post("/conversations/{conversation_id}/invite-link/reset", tags=["groups"])
async def reset_invite_link(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Replace the link: the old one stops working at once. Admins only."""
    import secrets

    room = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    if room.is_welcome:
        raise HTTPException(status_code=400, detail="The welcome group has no link")
    room.invite_code = secrets.token_urlsafe(12)
    room.invite_uses = 0
    _audit(db, conversation_id, principal.user_id, "link_reset")
    _system_message(db, conversation_id, _name(principal.user_id) + " replaced the group's link. The old one no longer works.")
    db.commit()
    await _changed(db, conversation_id)
    return _link_out(room)


# --- ownership and deletion ---------------------------------------------------

class TransferIn(BaseModel):
    user_id: str = Field(min_length=3, max_length=40)


@app.post("/conversations/{conversation_id}/transfer", tags=["groups"])
async def transfer_ownership(
    conversation_id: str, payload: TransferIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Hand the group on. The owner only; they stay on as an admin."""
    room = _group(db, conversation_id, principal.user_id)
    me = _member(db, conversation_id, principal.user_id)
    if me.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can hand the group on")
    if room.is_welcome:
        raise HTTPException(status_code=403, detail="The welcome group's owner is set by Kinjy")
    if payload.user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You already own this group")
    target = _member(db, conversation_id, payload.user_id)
    if target.accepted_at is None:
        raise HTTPException(status_code=409, detail="This member has not accepted the group yet")
    previous = target.role
    target.role = "owner"
    me.role = "admin"
    room.created_by = payload.user_id
    names = _display_names({principal.user_id, payload.user_id})
    _system_message(db, conversation_id, f"{names[principal.user_id]} made {names[payload.user_id]} the owner of the group.")
    _audit(db, conversation_id, principal.user_id, "ownership_transferred", target_id=payload.user_id, detail=previous)
    db.commit()
    await _changed(db, conversation_id)
    return {"owner": payload.user_id, "your_role": "admin"}


class DeleteIn(BaseModel):
    # The group's name typed back, which is what makes the deletion deliberate.
    confirm_title: str = Field(min_length=1, max_length=140)


@app.post("/conversations/{conversation_id}/delete", tags=["groups"])
async def delete_group(
    conversation_id: str,
    payload: DeleteIn,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Delete the group for everybody. The owner only, by typing its name.

    Kinjy's deletion rule for messages applies to all of them at once: the
    words, attachments' references, reactions, pins, polls, events and tasks
    are deleted from the database, not hidden. What stays is the bare group
    row and its audit trail, so it can still be said who deleted what and when.
    """
    room = _group(db, conversation_id, principal.user_id)
    me = _member(db, conversation_id, principal.user_id)
    if me.role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can delete the group")
    if room.is_welcome:
        raise HTTPException(status_code=403, detail="The welcome group cannot be deleted")
    if payload.confirm_title.strip() != (room.title or "").strip():
        raise HTTPException(status_code=422, detail="Type the group's name exactly to delete it")

    members = _participant_ids(db, conversation_id)
    messages = select(models.Message.id).where(models.Message.conversation_id == conversation_id)
    _drop_attached(db, messages)
    db.execute(delete(models.MessageReaction).where(models.MessageReaction.conversation_id == conversation_id))
    db.execute(delete(models.Message).where(models.Message.conversation_id == conversation_id))
    db.execute(delete(models.Participant).where(models.Participant.conversation_id == conversation_id))
    db.execute(delete(models.JoinRequest).where(models.JoinRequest.conversation_id == conversation_id))
    db.execute(delete(models.GroupInvite).where(models.GroupInvite.conversation_id == conversation_id))
    images = [a for a in (room.avatar_asset_id, room.cover_asset_id) if a]
    title = room.title or "a group"
    room.deleted_at = _now()
    room.invite_code = None
    room.community_id = None
    room.avatar_asset_id = room.avatar_url = room.cover_asset_id = room.cover_url = None
    room.description = room.rules = None
    _audit(db, conversation_id, principal.user_id, "group_deleted", detail=f"{len(members)} members")
    who = _name(principal.user_id)
    for uid in members:
        if uid != principal.user_id:
            db.add(models.Notification(user_id=uid, kind="group_deleted", title=f"{who} deleted the group {title}", link="/messages"))
    db.commit()
    for asset_id in images:
        _discard_image(asset_id, principal.user_id)
    await _publish_to_users(members, {"type": "conversation", "conversation_id": conversation_id, "deleted": True})
    return {"deleted": True}


# --- invitations --------------------------------------------------------------

class InviteIn(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=50)


@app.post("/conversations/{conversation_id}/invites", status_code=201, tags=["groups"])
def invite_members(conversation_id: str, payload: InviteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Invite people, who then accept or decline. Admins only.

    Each person's own setting for group invitations decides, and the same age
    rule as adding somebody applies. All or nothing, like adding.
    """
    room = _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    if room.is_welcome:
        raise HTTPException(status_code=400, detail="Everybody is in the welcome group already")
    present = set(_participant_ids(db, conversation_id))
    wanted = [uid for uid in dict.fromkeys(payload.user_ids) if uid not in present and uid != principal.user_id]
    for uid in wanted:
        how, reason = _how_to_bring_in(principal.user_id, uid)
        if how == "refused":
            raise HTTPException(status_code=403, detail=reason)
        age_ok, age_reason = agecheck.may_open_conversation(db, principal.user_id, uid)
        if not age_ok:
            db.commit()
            raise HTTPException(status_code=403, detail=age_reason)
    invited = []
    for uid in wanted:
        row = db.scalar(
            select(models.GroupInvite).where(
                models.GroupInvite.conversation_id == conversation_id, models.GroupInvite.invitee_id == uid
            )
        )
        if row is not None and row.status == "pending":
            continue
        if row is None:
            db.add(models.GroupInvite(conversation_id=conversation_id, invitee_id=uid, inviter_id=principal.user_id))
        else:
            row.status, row.inviter_id, row.created_at, row.answered_at = "pending", principal.user_id, _now(), None
        _audit(db, conversation_id, principal.user_id, "invited", target_id=uid)
        invited.append(uid)
    db.commit()
    for uid in invited:
        _tell_invited(room, uid, principal.user_id)
    return {"invited": invited}


@app.get("/conversations/{conversation_id}/invites", tags=["groups"])
def group_invites(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Invitations not answered yet. Admins only."""
    _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    rows = db.scalars(
        select(models.GroupInvite)
        .where(models.GroupInvite.conversation_id == conversation_id, models.GroupInvite.status == "pending")
        .order_by(models.GroupInvite.created_at)
    ).all()
    people = _profiles({r.invitee_id for r in rows})
    return {
        "items": [
            {
                "user_id": r.invitee_id,
                "invited_by": r.inviter_id,
                "created_at": r.created_at,
                "display_name": (people.get(r.invitee_id) or {}).get("display_name") or r.invitee_id[:12],
                "handle": (people.get(r.invitee_id) or {}).get("handle"),
                "avatar_url": (people.get(r.invitee_id) or {}).get("avatar_url"),
            }
            for r in rows
        ]
    }


@app.delete("/conversations/{conversation_id}/invites/{user_id}", tags=["groups"])
def cancel_invite(conversation_id: str, user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Withdraw an invitation nobody has answered. Admins only."""
    _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    row = db.scalar(
        select(models.GroupInvite).where(
            models.GroupInvite.conversation_id == conversation_id,
            models.GroupInvite.invitee_id == user_id,
            models.GroupInvite.status == "pending",
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No invitation waiting for this member")
    row.status, row.answered_at = "cancelled", _now()
    _audit(db, conversation_id, principal.user_id, "invite_cancelled", target_id=user_id)
    db.commit()
    return {"cancelled": True}


@app.get("/conversations/invites", tags=["groups"])
def my_invites(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The invitations waiting for me, with what each group is and its rules -
    never its members or its messages."""
    rows = db.scalars(
        select(models.GroupInvite)
        .where(models.GroupInvite.invitee_id == principal.user_id, models.GroupInvite.status == "pending")
        .order_by(models.GroupInvite.created_at.desc())
    ).all()
    rooms = {
        r.id: r
        for r in db.scalars(
            select(models.Conversation).where(models.Conversation.id.in_([i.conversation_id for i in rows]))
        ).all()
    }
    rows = [r for r in rows if r.conversation_id in rooms and not rooms[r.conversation_id].deleted_at]
    names = _display_names({r.inviter_id for r in rows}) if rows else {}
    counts = dict(
        db.execute(
            select(models.Participant.conversation_id, func.count())
            .where(models.Participant.conversation_id.in_([r.conversation_id for r in rows]))
            .group_by(models.Participant.conversation_id)
        ).all()
    ) if rows else {}
    return {
        "items": [
            {
                "id": r.id,
                "conversation_id": r.conversation_id,
                "title": rooms[r.conversation_id].title,
                "description": rooms[r.conversation_id].description,
                "rules": rooms[r.conversation_id].rules,
                "avatar_url": rooms[r.conversation_id].avatar_url,
                "members_count": counts.get(r.conversation_id, 0),
                "invited_by": names.get(r.inviter_id),
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


class InviteAnswerIn(BaseModel):
    action: str = Field(pattern="^(accept|decline)$")


@app.post("/conversations/invites/{invite_id}", tags=["groups"])
async def answer_invite(
    invite_id: int,
    payload: InviteAnswerIn,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Accept or decline an invitation. Only the person invited."""
    row = db.get(models.GroupInvite, invite_id)
    if row is None or row.invitee_id != principal.user_id or row.status != "pending":
        raise HTTPException(status_code=404, detail="Invitation not found")
    room = db.get(models.Conversation, row.conversation_id)
    if room is None or room.deleted_at:
        row.status, row.answered_at = "cancelled", _now()
        db.commit()
        raise HTTPException(status_code=404, detail="This group no longer exists")
    if payload.action == "decline":
        row.status, row.answered_at = "declined", _now()
        db.commit()
        return {"status": "declined"}

    # Things may have changed since the invitation: a block, or the age rule.
    inviter_still = permissions.get(row.inviter_id, principal.user_id)
    if inviter_still is None:
        raise HTTPException(status_code=503, detail="Could not check this invitation. Try again shortly.")
    if inviter_still.get("reason") == "blocked":
        row.status, row.answered_at = "cancelled", _now()
        db.commit()
        raise HTTPException(status_code=403, detail="This invitation can no longer be accepted")
    age_ok, age_reason = agecheck.may_open_conversation(db, row.inviter_id, principal.user_id)
    if not age_ok:
        db.commit()
        raise HTTPException(status_code=403, detail=age_reason)
    if principal.user_id not in set(_participant_ids(db, room.id)):
        _admit(db, room.id, principal.user_id)
        names = _display_names({principal.user_id, row.inviter_id})
        _system_message(db, room.id, f"{names[principal.user_id]} joined, invited by {names[row.inviter_id]}.")
    row.status, row.answered_at = "accepted", _now()
    db.commit()
    await _announce_joined(db, room.id, [principal.user_id], background)
    return {"status": "member", "conversation_id": room.id}


# --- notifications and read receipts, per member -----------------------------

MUTE_FOR = {
    "1h": timedelta(hours=1),
    "8h": timedelta(hours=8),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


class NotificationsIn(BaseModel):
    level: str | None = Field(default=None, pattern="^(all|mentions|announcements)$")
    mute: str | None = Field(default=None, pattern="^(1h|8h|1d|1w|forever|off)$")
    read_receipts: bool | None = None

    model_config = {"extra": "forbid"}


@app.patch("/conversations/{conversation_id}/notifications", tags=["groups"])
def set_notifications(
    conversation_id: str, payload: NotificationsIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """What this member hears from the room, and whether they share "Seen"."""
    from main import _my_notifications

    me = _member(db, conversation_id, principal.user_id)
    if payload.level is not None:
        me.notify_level = payload.level
    if payload.mute is not None:
        me.muted_until = (
            None if payload.mute == "off"
            else models.MUTED_FOREVER if payload.mute == "forever"
            else _now() + MUTE_FOR[payload.mute]
        )
    if payload.read_receipts is not None:
        me.read_receipts = payload.read_receipts
    db.commit()
    return _my_notifications(me)


@app.get("/conversations/{conversation_id}/messages/{message_id}/receipts", tags=["groups"])
def message_receipts(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Who has read one of my messages. The sender only.

    Counts members whose reading is past the message and who share their
    receipts. Names are listed for groups up to RECEIPT_NAMES members; a
    bigger room gets the count. "Delivered" is not tracked separately: a
    message is stored on the server the moment it is sent.
    """
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    message = _visible_message(db, room, me, message_id)
    if message.sender_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the sender sees who read a message")
    rows = db.scalars(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id != principal.user_id,
        )
    ).all()
    readers = [
        r.user_id
        for r in rows
        if r.read_receipts is not False and r.last_read_at is not None and r.last_read_at >= message.created_at
    ]
    RECEIPT_NAMES = 256
    names = _profiles(set(readers)) if len(rows) <= RECEIPT_NAMES else {}
    return {
        "read": len(readers),
        "members": len(rows),
        "readers": [
            {"user_id": uid, "display_name": (names.get(uid) or {}).get("display_name") or uid[:12]}
            for uid in readers
        ] if names or not readers else None,
    }


# --- pinned messages ----------------------------------------------------------

MAX_PINS = 50


@app.post("/conversations/{conversation_id}/messages/{message_id}/pin", tags=["groups"])
async def pin_message(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Pin a message for everybody. The owner, admins and moderators."""
    room = _group(db, conversation_id, principal.user_id)
    me = _moderates(db, conversation_id, principal.user_id)
    message = _visible_message(db, room, me, message_id)
    if message.deleted_at or message.kind == "system":
        raise HTTPException(status_code=404, detail="Message not found")
    if db.scalar(select(models.PinnedMessage).where(models.PinnedMessage.message_id == message_id)):
        return {"pinned": True, "already": True}
    count = db.scalar(
        select(func.count()).select_from(models.PinnedMessage).where(models.PinnedMessage.conversation_id == conversation_id)
    ) or 0
    if count >= MAX_PINS:
        raise HTTPException(status_code=409, detail=f"A group keeps at most {MAX_PINS} pinned messages. Unpin one first.")
    db.add(models.PinnedMessage(conversation_id=conversation_id, message_id=message_id, pinned_by=principal.user_id))
    _system_message(db, conversation_id, _name(principal.user_id) + " pinned a message.")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"pinned": True, "already": True}
    await _tell_room(db, conversation_id, {"type": "message_pinned", "conversation_id": conversation_id, "message_id": message_id, "pinned": True})
    return {"pinned": True}


@app.delete("/conversations/{conversation_id}/messages/{message_id}/pin", tags=["groups"])
async def unpin_message(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _group(db, conversation_id, principal.user_id)
    _moderates(db, conversation_id, principal.user_id)
    removed = db.execute(
        delete(models.PinnedMessage).where(
            models.PinnedMessage.conversation_id == conversation_id, models.PinnedMessage.message_id == message_id
        )
    ).rowcount
    db.commit()
    if removed:
        await _tell_room(db, conversation_id, {"type": "message_pinned", "conversation_id": conversation_id, "message_id": message_id, "pinned": False})
    return {"pinned": False}


def _brief(db: OrmSession, rows: list[models.Message]) -> list[dict]:
    """A message as a list shows it: who, when, a line of it, its file."""
    return [
        {
            "id": r.id,
            "conversation_id": r.conversation_id,
            "sender_id": r.sender_id,
            "kind": r.kind,
            "body": None if r.encrypted or r.deleted_at else _plain(r)[0],
            "encrypted": r.encrypted,
            "deleted": bool(r.deleted_at),
            **_media_fields(r),
            "created_at": r.created_at,
        }
        for r in rows
    ]


def _withhold_others_media(db: OrmSession, room: models.Conversation, me: models.Participant) -> bool:
    return me.accepted_at is None and me.user_id in _withheld_from(db, room)


@app.get("/conversations/{conversation_id}/pins", tags=["groups"])
def list_pins(conversation_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The room's pinned messages, newest pin first - those this member may read."""
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    pins = db.scalars(
        select(models.PinnedMessage)
        .where(models.PinnedMessage.conversation_id == conversation_id)
        .order_by(models.PinnedMessage.pinned_at.desc())
    ).all()
    since = _visible_from(room, me)
    by_id = {
        m.id: m
        for m in db.scalars(select(models.Message).where(models.Message.id.in_([p.message_id for p in pins]))).all()
    }
    shown = [by_id[p.message_id] for p in pins if p.message_id in by_id and (since is None or by_id[p.message_id].created_at >= since)]
    items = _brief(db, shown)
    if _withhold_others_media(db, room, me):
        items = [{**i, **WITHHELD_MEDIA} if i["media_kind"] and i["sender_id"] != me.user_id else i for i in items]
    return {"items": items, "profiles": _profiles({m.sender_id for m in shown if m.sender_id})}


# --- saved messages -------------------------------------------------------------

@app.post("/conversations/{conversation_id}/messages/{message_id}/save", tags=["messages"])
def save_message(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Bookmark a message, privately. Any conversation."""
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    message = _visible_message(db, room, me, message_id)
    if message.deleted_at or message.kind == "system":
        raise HTTPException(status_code=404, detail="Message not found")
    if not db.scalar(
        select(models.SavedMessage).where(
            models.SavedMessage.user_id == principal.user_id, models.SavedMessage.message_id == message_id
        )
    ):
        db.add(models.SavedMessage(user_id=principal.user_id, conversation_id=conversation_id, message_id=message_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"saved": True}


@app.delete("/conversations/{conversation_id}/messages/{message_id}/save", tags=["messages"])
def unsave_message(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    db.execute(
        delete(models.SavedMessage).where(
            models.SavedMessage.user_id == principal.user_id, models.SavedMessage.message_id == message_id
        )
    )
    db.commit()
    return {"saved": False}


@app.get("/conversations/saved", tags=["messages"])
def saved_messages(principal: CurrentUser, limit: int = 100, db: OrmSession = Depends(get_db)):
    """My bookmarks - only those I can still read.

    A bookmark is not a copy: leaving the room, the history rule, a deletion or
    an expiry takes the message out of this list as it takes it out of the
    thread.
    """
    rows = db.scalars(
        select(models.SavedMessage)
        .where(models.SavedMessage.user_id == principal.user_id)
        .order_by(models.SavedMessage.saved_at.desc())
        .limit(min(max(limit, 1), 300))
    ).all()
    mine = {
        p.conversation_id: p
        for p in db.scalars(
            select(models.Participant).where(
                models.Participant.user_id == principal.user_id,
                models.Participant.conversation_id.in_([r.conversation_id for r in rows]),
            )
        ).all()
    }
    rooms = {
        c.id: c
        for c in db.scalars(select(models.Conversation).where(models.Conversation.id.in_(list(mine)))).all()
    }
    messages = {
        m.id: m
        for m in db.scalars(select(models.Message).where(models.Message.id.in_([r.message_id for r in rows]))).all()
    }
    now = _now()
    shown: list[tuple[models.SavedMessage, models.Message]] = []
    for r in rows:
        m, me, room = messages.get(r.message_id), mine.get(r.conversation_id), rooms.get(r.conversation_id)
        if m is None or me is None or room is None or room.deleted_at or m.deleted_at:
            continue
        if m.expires_at is not None and m.expires_at <= now:
            continue
        since = _visible_from(room, me)
        if since is not None and m.created_at < since:
            continue
        if m.media_kind and m.sender_id != principal.user_id and _withhold_others_media(db, room, me):
            continue
        shown.append((r, m))
    briefs = _brief(db, [m for _, m in shown])
    return {
        "items": [
            {
                **b,
                "saved_at": r.saved_at,
                "conversation_title": rooms[r.conversation_id].title,
                "conversation_kind": rooms[r.conversation_id].kind,
            }
            for (r, _), b in zip(shown, briefs)
        ],
        "profiles": _profiles({m.sender_id for _, m in shown if m.sender_id}),
    }


# --- search and the files view ----------------------------------------------------

# Message text is sealed at rest per message, so the database cannot search it:
# the server opens the most recent messages and looks. This many, at most.
SEARCH_SCAN = 5_000


def _scan(db: OrmSession, room: models.Conversation, me: models.Participant, extra=None, cap: int = SEARCH_SCAN):
    """The readable, non-deleted messages of a room, newest first, in batches."""
    since = _visible_from(room, me)
    stmt = select(models.Message).where(
        models.Message.conversation_id == room.id,
        models.Message.deleted_at.is_(None),
        models.Message.encrypted.is_(False),
        models.Message.kind != "system",
    )
    if since is not None:
        stmt = stmt.where(models.Message.created_at >= since)
    if extra is not None:
        stmt = stmt.where(extra)
    seen = 0
    before = None
    while seen < cap:
        page = stmt if before is None else stmt.where(models.Message.created_at < before)
        rows = db.scalars(page.order_by(models.Message.created_at.desc()).limit(500)).all()
        if not rows:
            return
        for row in rows:
            yield row
        seen += len(rows)
        before = rows[-1].created_at


def _snippet(text: str, needle: str, width: int = 140) -> str:
    at = text.casefold().find(needle)
    start = max(0, at - width // 3)
    piece = text[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


@app.get("/conversations/{conversation_id}/search", tags=["messages"])
def search_messages(conversation_id: str, q: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Find messages in one conversation, under the same rules as reading it."""
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    needle = q.strip().casefold()
    if len(needle) < 2:
        raise HTTPException(status_code=422, detail="Search for at least two characters")
    _purge_expired(db, conversation_id)
    withhold = _withhold_others_media(db, room, me)
    hits: list[dict] = []
    scanned = 0
    for m in _scan(db, room, me):
        scanned += 1
        body, name = _plain(m)
        text = body or ""
        if needle in text.casefold() or (name and needle in name.casefold() and not (withhold and m.sender_id != me.user_id)):
            hits.append(
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    "kind": m.kind,
                    "snippet": _snippet(text, needle) if needle in text.casefold() else (name or ""),
                    "media_kind": m.media_kind,
                    "created_at": m.created_at,
                }
            )
            if len(hits) >= 50:
                break
    return {
        "items": hits,
        "profiles": _profiles({h["sender_id"] for h in hits if h["sender_id"]}),
        # True when the room is longer than the search looks back.
        "partial": scanned >= SEARCH_SCAN,
    }


LINK_RE = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)


@app.get("/conversations/{conversation_id}/files", tags=["messages"])
def conversation_files(
    conversation_id: str,
    principal: CurrentUser,
    kind: str = "media",
    db: OrmSession = Depends(get_db),
):
    """What was shared: photos and videos, files (documents, audio), or links.

    The same rules as the thread: the history rule, and a minor who has not
    accepted the group sees no attachment but their own. The links come back
    signed, as in the thread - a link copied out of here expires like those.
    """
    if kind not in ("media", "files", "links"):
        raise HTTPException(status_code=422, detail="kind is media, files or links")
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    withhold = _withhold_others_media(db, room, me)
    items: list[dict] = []
    if kind == "links":
        for m in _scan(db, room, me, cap=2_000):
            for url in LINK_RE.findall(_plain(m)[0] or ""):
                items.append({"id": m.id, "sender_id": m.sender_id, "url": url.rstrip(".,);"), "created_at": m.created_at})
            if len(items) >= 200:
                break
    else:
        wanted = ("image", "video") if kind == "media" else ("document", "file", "audio")
        for m in _scan(db, room, me, extra=models.Message.media_kind.in_(wanted), cap=1_000):
            if withhold and m.sender_id != me.user_id:
                continue
            items.append(
                {
                    "id": m.id,
                    "sender_id": m.sender_id,
                    **_media_fields(m),
                    "created_at": m.created_at,
                }
            )
            if len(items) >= 200:
                break
    return {"items": items, "profiles": _profiles({i["sender_id"] for i in items if i["sender_id"]})}


# --- replies to one message (a thread) and announcements ------------------------

@app.get("/conversations/{conversation_id}/messages/{message_id}/replies", tags=["messages"])
def message_replies(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """A message and every reply to it, oldest first: a side thread that keeps
    the main conversation readable."""
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    root = _visible_message(db, room, me, message_id)
    since = _visible_from(room, me)
    stmt = select(models.Message).where(
        models.Message.conversation_id == conversation_id, models.Message.reply_to_id == message_id
    )
    if since is not None:
        stmt = stmt.where(models.Message.created_at >= since)
    replies = db.scalars(stmt.order_by(models.Message.created_at).limit(300)).all()
    shown = [root, *replies]
    items = _brief(db, shown)
    if _withhold_others_media(db, room, me):
        items = [{**i, **WITHHELD_MEDIA} if i["media_kind"] and i["sender_id"] != me.user_id else i for i in items]
    return {"root": items[0], "items": items[1:], "profiles": _profiles({m.sender_id for m in shown if m.sender_id})}


async def _fan_out(
    db: OrmSession,
    conversation_id: str,
    recipients: list[str],
    kind: str,
    title: str,
    link: str,
    background: BackgroundTasks,
) -> None:
    """Notify many members once each, without a push per member in a big room.

    Up to ANNOUNCE_PUSH_LIMIT people each get the full treatment (stored, live,
    pushed to their devices). Beyond that the notification is stored and sent
    live to whoever is connected, and nobody's phone is rung by a crowd.
    """
    if len(recipients) <= ANNOUNCE_PUSH_LIMIT:
        for uid in recipients:
            await internal_notify(user_id=uid, background=background, kind=kind, title=title, body=None, link=link, db=db)
        return
    for uid in recipients:
        db.add(models.Notification(user_id=uid, kind=kind, title=title, link=link))
    db.commit()
    await _publish_to_users(recipients, {"type": "notification", "kind": kind, "title": title, "link": link})


@app.post("/conversations/{conversation_id}/messages/{message_id}/announcement", tags=["groups"])
async def mark_announcement(
    conversation_id: str,
    message_id: str,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    """Mark a message as an announcement. Admins only.

    Members who want announcements (their level, not muted) are told once.
    """
    room = _group(db, conversation_id, principal.user_id)
    me = _runs_group(db, conversation_id, principal.user_id)
    message = _visible_message(db, room, me, message_id)
    if message.deleted_at or message.kind == "system":
        raise HTTPException(status_code=404, detail="Message not found")
    if message.announcement_at:
        return {"announcement": True, "already": True}
    message.announcement_at = _now()
    _audit(db, conversation_id, principal.user_id, "announcement_marked", detail=message_id)
    db.commit()
    await _tell_room(db, conversation_id, {"type": "message_announcement", "conversation_id": conversation_id, "message_id": message_id, "announcement": True})
    rows = db.scalars(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id != principal.user_id,
        )
    ).all()
    blocked = await _blocked_with(principal.user_id) or set()
    recipients = [r.user_id for r in rows if _wants(r, "announcement") and r.user_id not in blocked]
    await _fan_out(
        db, conversation_id, recipients, "group_announcement",
        f"Announcement in {room.title or 'a group'}", f"/messages?c={conversation_id}", background,
    )
    return {"announcement": True, "notified": len(recipients)}


@app.delete("/conversations/{conversation_id}/messages/{message_id}/announcement", tags=["groups"])
async def unmark_announcement(conversation_id: str, message_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    room = _group(db, conversation_id, principal.user_id)
    me = _runs_group(db, conversation_id, principal.user_id)
    message = _visible_message(db, room, me, message_id)
    if message.announcement_at:
        message.announcement_at = None
        _audit(db, conversation_id, principal.user_id, "announcement_unmarked", detail=message_id)
        db.commit()
        await _tell_room(db, conversation_id, {"type": "message_announcement", "conversation_id": conversation_id, "message_id": message_id, "announcement": False})
    return {"announcement": False}


# --- cards: polls, events and tasks posted into the thread -----------------------

def _may_post(room: models.Conversation, me: models.Participant) -> None:
    if room.kind != "group":
        raise HTTPException(status_code=400, detail="Polls, events and tasks are for groups")
    if (room.posting or "all") == "admins" and me.role not in MODERATES:
        raise HTTPException(status_code=403, detail="Only the group's admins and moderators can post here")


async def _post_card(db: OrmSession, room: models.Conversation, sender_id: str, kind: str, text: str, card_of, attach) -> models.Message:
    """Store the message a card lives in, then show it to the room.

    `attach(message)` creates the card's own row in the same transaction, so a
    card never exists without its message or the other way round. `card_of()`
    draws the card as nobody in particular sees it (no votes of their own, no
    buttons): each member's own view comes with the thread.
    """
    message = models.Message(
        id=new_id("msg"),
        conversation_id=room.id,
        sender_id=sender_id,
        encrypted=False,
        kind=kind,
        expires_at=(_now() + timedelta(seconds=room.disappear_after_seconds) if room.disappear_after_seconds else None),
    )
    _seal(message, text, None)
    db.add(message)
    db.flush()
    attach(message)
    room.last_message_at = _now()
    sender = _member(db, room.id, sender_id)
    sender.last_read_at = _now()
    db.commit()
    await _publish_to_users(
        _participant_ids(db, room.id),
        {
            "type": "message",
            "conversation_id": room.id,
            "message_id": message.id,
            "client_id": None,
            "sender_id": sender_id,
            "encrypted": False,
            "body": text,
            "kind": kind,
            "reply_to_id": None,
            "sticker_id": None,
            "media_url": None,
            "media_kind": None,
            "media_name": None,
            "media_type": None,
            "media_size": None,
            "created_at": message.created_at.isoformat(),
            kind: _jsonable(card_of()),
        },
    )
    return message


def _jsonable(value):
    """Datetimes as ISO strings, for a frame sent over the socket."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# Polls ---------------------------------------------------------------------------

class PollIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(min_length=2, max_length=10)
    multiple: bool = False
    results: str = Field(default="always", pattern="^(always|after_vote|after_close)$")
    closes_at: datetime | None = None


def _poll_closed(poll: models.Poll) -> bool:
    return bool(poll.closed_at) or (poll.closes_at is not None and poll.closes_at <= _now())


def _poll_out(db: OrmSession, poll: models.Poll, viewer: str, role: str, options=None, votes=None) -> dict:
    """A poll as one member sees it. Who voted for what is never shown: the
    results are counts, and only when the poll's own setting allows."""
    options = options if options is not None else db.scalars(
        select(models.PollOption).where(models.PollOption.poll_id == poll.id).order_by(models.PollOption.position)
    ).all()
    votes = votes if votes is not None else db.execute(
        select(models.PollVote.option_id, models.PollVote.user_id).where(models.PollVote.poll_id == poll.id)
    ).all()
    closed = _poll_closed(poll)
    mine = sorted({o for o, u in votes if u == viewer})
    show = poll.results == "always" or (poll.results == "after_vote" and (mine or closed)) or closed
    counts: dict[int, int] = {}
    for option_id, _ in votes:
        counts[option_id] = counts.get(option_id, 0) + 1
    return {
        "id": poll.id,
        "question": poll.question,
        "multiple": poll.multiple,
        "results": poll.results,
        "closes_at": poll.closes_at,
        "closed": closed,
        "options": [{"id": o.id, "text": o.text, "votes": counts.get(o.id, 0) if show else None} for o in options],
        "voters": len({u for _, u in votes}) if show else None,
        "my_votes": mine,
        "results_visible": bool(show),
        "can_close": not closed and (poll.created_by == viewer or role in MODERATES),
    }


@app.post("/conversations/{conversation_id}/polls", status_code=201, tags=["groups"])
async def create_poll(conversation_id: str, payload: PollIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _may_post(room, me)
    if (room.polls_by or "all") == "admins" and me.role not in MODERATES:
        raise HTTPException(status_code=403, detail="In this group only admins and moderators start polls")
    options = [o.strip() for o in payload.options if o.strip()]
    if len(options) < 2 or len({o.casefold() for o in options}) != len(options):
        raise HTTPException(status_code=422, detail="A poll needs at least two different answers")
    if any(len(o) > 200 for o in options):
        raise HTTPException(status_code=422, detail="An answer is at most 200 characters")
    closes = payload.closes_at
    if closes is not None:
        closes = closes if closes.tzinfo else closes.replace(tzinfo=timezone.utc)
        if closes <= _now():
            raise HTTPException(status_code=422, detail="The closing date must be in the future")
    poll_id = new_id("pol")

    def attach(message: models.Message) -> None:
        db.add(models.Poll(
            id=poll_id, conversation_id=conversation_id, message_id=message.id, created_by=principal.user_id,
            question=payload.question.strip(), multiple=payload.multiple, results=payload.results, closes_at=closes,
        ))
        for position, text in enumerate(options):
            db.add(models.PollOption(poll_id=poll_id, position=position, text=text))

    message = await _post_card(
        db, room, principal.user_id, "poll", payload.question.strip(),
        lambda: _poll_out(db, db.get(models.Poll, poll_id), "", "member"), attach,
    )
    poll = db.get(models.Poll, poll_id)
    return {"message_id": message.id, "poll": _poll_out(db, poll, principal.user_id, me.role)}


def _poll_in_room(db: OrmSession, room: models.Conversation, me: models.Participant, poll_id: str) -> models.Poll:
    poll = db.get(models.Poll, poll_id)
    if poll is None or poll.conversation_id != room.id:
        raise HTTPException(status_code=404, detail="Poll not found")
    _visible_message(db, room, me, poll.message_id)
    return poll


@app.get("/conversations/{conversation_id}/polls/{poll_id}", tags=["groups"])
def get_poll(conversation_id: str, poll_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    return _poll_out(db, _poll_in_room(db, room, me, poll_id), principal.user_id, me.role)


class VoteIn(BaseModel):
    # Empty takes the vote back.
    option_ids: list[int] = Field(default_factory=list, max_length=10)


@app.post("/conversations/{conversation_id}/polls/{poll_id}/vote", tags=["groups"])
async def vote_poll(conversation_id: str, poll_id: str, payload: VoteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    poll = _poll_in_room(db, room, me, poll_id)
    if _poll_closed(poll):
        raise HTTPException(status_code=409, detail="This poll is closed")
    valid = set(db.scalars(select(models.PollOption.id).where(models.PollOption.poll_id == poll_id)).all())
    chosen = list(dict.fromkeys(payload.option_ids))
    if any(o not in valid for o in chosen):
        raise HTTPException(status_code=422, detail="That is not an answer of this poll")
    if not poll.multiple and len(chosen) > 1:
        raise HTTPException(status_code=422, detail="This poll takes one answer")
    db.execute(delete(models.PollVote).where(models.PollVote.poll_id == poll_id, models.PollVote.user_id == principal.user_id))
    for option_id in chosen:
        db.add(models.PollVote(poll_id=poll_id, option_id=option_id, user_id=principal.user_id))
    db.commit()
    await _tell_room(db, conversation_id, {"type": "poll_updated", "conversation_id": conversation_id, "poll_id": poll_id, "message_id": poll.message_id})
    return _poll_out(db, poll, principal.user_id, me.role)


@app.post("/conversations/{conversation_id}/polls/{poll_id}/close", tags=["groups"])
async def close_poll(conversation_id: str, poll_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Close a poll: whoever started it, or the group's moderators."""
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    poll = _poll_in_room(db, room, me, poll_id)
    if poll.created_by != principal.user_id and me.role not in MODERATES:
        raise HTTPException(status_code=403, detail="Only whoever started the poll, or a moderator, can close it")
    if not poll.closed_at:
        poll.closed_at, poll.closed_by = _now(), principal.user_id
        db.commit()
        await _tell_room(db, conversation_id, {"type": "poll_updated", "conversation_id": conversation_id, "poll_id": poll_id, "message_id": poll.message_id})
    return _poll_out(db, poll, principal.user_id, me.role)


# Events --------------------------------------------------------------------------

TZ_RE = re.compile(r"^(UTC|[A-Za-z]+(?:/[A-Za-z0-9_+\-]+){1,2})$")


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    starts_at: datetime
    ends_at: datetime | None = None
    # Where it happens, as an IANA zone ("Africa/Abidjan"): the instant is
    # starts_at; this is only how it is written for the people there.
    timezone: str = Field(default="UTC", max_length=64)
    location: str | None = Field(default=None, max_length=300)
    online_url: str | None = Field(default=None, max_length=500)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _event_out(db: OrmSession, event: models.GroupEvent, viewer: str, role: str, responses=None) -> dict:
    responses = responses if responses is not None else db.execute(
        select(models.EventResponse.user_id, models.EventResponse.response).where(models.EventResponse.event_id == event.id)
    ).all()
    counts = {"going": 0, "maybe": 0, "not_going": 0}
    mine = None
    for uid, response in responses:
        counts[response] = counts.get(response, 0) + 1
        if uid == viewer:
            mine = response
    return {
        "id": event.id,
        "title": event.title,
        "description": event.description,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "timezone": event.timezone,
        "location": event.location,
        "online_url": event.online_url,
        "cancelled": bool(event.cancelled_at),
        "counts": counts,
        "my_response": mine,
        "can_manage": not event.cancelled_at and (event.created_by == viewer or role in MODERATES),
    }


@app.post("/conversations/{conversation_id}/events", status_code=201, tags=["groups"])
async def create_event(conversation_id: str, payload: EventIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _may_post(room, me)
    starts, ends = _aware(payload.starts_at), _aware(payload.ends_at)
    if starts < _now() - timedelta(hours=1):
        raise HTTPException(status_code=422, detail="The event must start in the future")
    if ends is not None and ends <= starts:
        raise HTTPException(status_code=422, detail="The event must end after it starts")
    if not TZ_RE.match(payload.timezone):
        raise HTTPException(status_code=422, detail="Unknown time zone")
    url = (payload.online_url or "").strip() or None
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(status_code=422, detail="The meeting link must start with http:// or https://")
    event_id = new_id("gev")

    def attach(message: models.Message) -> None:
        db.add(models.GroupEvent(
            id=event_id, conversation_id=conversation_id, message_id=message.id, created_by=principal.user_id,
            title=payload.title.strip(), description=(payload.description or "").strip() or None,
            starts_at=starts, ends_at=ends, timezone=payload.timezone,
            location=(payload.location or "").strip() or None, online_url=url,
        ))
        # Whoever creates an event is going to it.
        db.add(models.EventResponse(event_id=event_id, user_id=principal.user_id, response="going"))

    message = await _post_card(
        db, room, principal.user_id, "event", payload.title.strip(),
        lambda: _event_out(db, db.get(models.GroupEvent, event_id), "", "member"), attach,
    )
    return {"message_id": message.id, "event": _event_out(db, db.get(models.GroupEvent, event_id), principal.user_id, me.role)}


def _event_in_room(db: OrmSession, room: models.Conversation, me: models.Participant, event_id: str) -> models.GroupEvent:
    event = db.get(models.GroupEvent, event_id)
    if event is None or event.conversation_id != room.id:
        raise HTTPException(status_code=404, detail="Event not found")
    _visible_message(db, room, me, event.message_id)
    return event


class ResponseIn(BaseModel):
    response: str = Field(pattern="^(going|maybe|not_going)$")


@app.post("/conversations/{conversation_id}/events/{event_id}/response", tags=["groups"])
async def respond_event(conversation_id: str, event_id: str, payload: ResponseIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    event = _event_in_room(db, room, me, event_id)
    if event.cancelled_at:
        raise HTTPException(status_code=409, detail="This event was cancelled")
    row = db.scalar(select(models.EventResponse).where(models.EventResponse.event_id == event_id, models.EventResponse.user_id == principal.user_id))
    if row is None:
        db.add(models.EventResponse(event_id=event_id, user_id=principal.user_id, response=payload.response))
    else:
        row.response, row.updated_at = payload.response, _now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Try that again")
    await _tell_room(db, conversation_id, {"type": "event_updated", "conversation_id": conversation_id, "event_id": event_id, "message_id": event.message_id})
    return _event_out(db, event, principal.user_id, me.role)


@app.post("/conversations/{conversation_id}/events/{event_id}/cancel", tags=["groups"])
async def cancel_event(
    conversation_id: str, event_id: str, principal: CurrentUser, background: BackgroundTasks, db: OrmSession = Depends(get_db)
):
    """Cancel an event: whoever created it, or the group's moderators. Those
    who said they would come are told."""
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    event = _event_in_room(db, room, me, event_id)
    if event.created_by != principal.user_id and me.role not in MODERATES:
        raise HTTPException(status_code=403, detail="Only whoever created the event, or a moderator, can cancel it")
    if event.cancelled_at:
        return _event_out(db, event, principal.user_id, me.role)
    event.cancelled_at = _now()
    db.commit()
    await _tell_room(db, conversation_id, {"type": "event_updated", "conversation_id": conversation_id, "event_id": event_id, "message_id": event.message_id})
    coming = db.scalars(
        select(models.EventResponse.user_id).where(
            models.EventResponse.event_id == event_id,
            models.EventResponse.response.in_(("going", "maybe")),
            models.EventResponse.user_id != principal.user_id,
        )
    ).all()
    rows = {p.user_id: p for p in db.scalars(select(models.Participant).where(models.Participant.conversation_id == conversation_id, models.Participant.user_id.in_(coming))).all()}
    await _fan_out(
        db, conversation_id, [u for u in coming if _wants(rows.get(u), "reminder")], "group_event",
        f"Cancelled: {event.title}", f"/messages?c={conversation_id}", background,
    )
    return _event_out(db, event, principal.user_id, me.role)


@app.get("/conversations/{conversation_id}/events", tags=["groups"])
def list_events(conversation_id: str, principal: CurrentUser, upcoming: bool = True, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    stmt = select(models.GroupEvent).where(models.GroupEvent.conversation_id == conversation_id)
    if upcoming:
        stmt = stmt.where(models.GroupEvent.starts_at >= _now() - timedelta(hours=6), models.GroupEvent.cancelled_at.is_(None))
    events = db.scalars(stmt.order_by(models.GroupEvent.starts_at).limit(100)).all()
    since = _visible_from(room, me)
    created = dict(db.execute(select(models.Message.id, models.Message.created_at).where(models.Message.id.in_([e.message_id for e in events]))).all())
    events = [e for e in events if e.message_id in created and (since is None or created[e.message_id] >= since)]
    return {"items": [{**_event_out(db, e, principal.user_id, me.role), "message_id": e.message_id} for e in events]}


# Tasks ---------------------------------------------------------------------------

class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    assignee_id: str | None = Field(default=None, max_length=40)
    due_at: datetime | None = None


class TaskPatch(BaseModel):
    done: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    assignee_id: str | None = Field(default=None, max_length=40)
    due_at: datetime | None = None

    model_config = {"extra": "forbid"}


def _task_out(task: models.GroupTask, viewer: str, role: str, names: dict[str, str] | None = None) -> dict:
    names = names or {}
    return {
        "id": task.id,
        "title": task.title,
        "assignee_id": task.assignee_id,
        "assignee_name": names.get(task.assignee_id) if task.assignee_id else None,
        "due_at": task.due_at,
        "done": bool(task.done_at),
        "done_at": task.done_at,
        "can_complete": task.assignee_id in (None, viewer) or task.created_by == viewer or role in MODERATES,
        "can_edit": task.created_by == viewer or role in MODERATES,
    }


def _assignee_ok(db: OrmSession, conversation_id: str, user_id: str | None) -> None:
    if user_id and user_id not in set(_participant_ids(db, conversation_id)):
        raise HTTPException(status_code=422, detail="A task can only be given to a member of the group")


async def _tell_assignee(db, room, task, actor_id, background) -> None:
    if not task.assignee_id or task.assignee_id == actor_id:
        return
    row = db.scalar(select(models.Participant).where(models.Participant.conversation_id == room.id, models.Participant.user_id == task.assignee_id))
    if _wants(row, "reminder"):
        await internal_notify(
            user_id=task.assignee_id, background=background, kind="group_task",
            title=f"{_name(actor_id)} gave you a task in {room.title or 'a group'}",
            body=None, link=f"/messages?c={room.id}", db=db,
        )


@app.post("/conversations/{conversation_id}/tasks", status_code=201, tags=["groups"])
async def create_task(
    conversation_id: str, payload: TaskIn, principal: CurrentUser, background: BackgroundTasks, db: OrmSession = Depends(get_db)
):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _may_post(room, me)
    _assignee_ok(db, conversation_id, payload.assignee_id)
    task_id = new_id("gtk")
    due = _aware(payload.due_at)

    def attach(message: models.Message) -> None:
        db.add(models.GroupTask(
            id=task_id, conversation_id=conversation_id, message_id=message.id, created_by=principal.user_id,
            title=payload.title.strip(), assignee_id=payload.assignee_id, due_at=due,
        ))

    names = _display_names({payload.assignee_id}) if payload.assignee_id else {}
    message = await _post_card(
        db, room, principal.user_id, "task", payload.title.strip(),
        lambda: _task_out(db.get(models.GroupTask, task_id), "", "member", names), attach,
    )
    task = db.get(models.GroupTask, task_id)
    await _tell_assignee(db, room, task, principal.user_id, background)
    return {"message_id": message.id, "task": _task_out(task, principal.user_id, me.role, names)}


@app.patch("/conversations/{conversation_id}/tasks/{task_id}", tags=["groups"])
async def update_task(
    conversation_id: str, task_id: str, payload: TaskPatch, principal: CurrentUser, background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    task = db.get(models.GroupTask, task_id)
    if task is None or task.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail="Task not found")
    _visible_message(db, room, me, task.message_id)
    view = _task_out(task, principal.user_id, me.role)
    sent = payload.model_dump(exclude_unset=True)
    if "done" in sent and sent["done"] is not None:
        if not view["can_complete"]:
            raise HTTPException(status_code=403, detail="Only the person the task is for, its creator or a moderator can tick it")
        task.done_at, task.done_by = (_now(), principal.user_id) if sent["done"] else (None, None)
    edits = {k: v for k, v in sent.items() if k != "done"}
    reassigned = False
    if edits:
        if not view["can_edit"]:
            raise HTTPException(status_code=403, detail="Only the task's creator or a moderator can change it")
        if "title" in edits and edits["title"]:
            task.title = edits["title"].strip()
        if "assignee_id" in edits:
            _assignee_ok(db, conversation_id, edits["assignee_id"])
            reassigned = edits["assignee_id"] != task.assignee_id
            task.assignee_id = edits["assignee_id"]
        if "due_at" in edits:
            task.due_at = _aware(edits["due_at"])
            task.reminded_at = None
    db.commit()
    await _tell_room(db, conversation_id, {"type": "task_updated", "conversation_id": conversation_id, "task_id": task_id, "message_id": task.message_id})
    if reassigned:
        await _tell_assignee(db, room, task, principal.user_id, background)
    names = _display_names({task.assignee_id}) if task.assignee_id else {}
    return _task_out(task, principal.user_id, me.role, names)


@app.get("/conversations/{conversation_id}/tasks", tags=["groups"])
def list_tasks(conversation_id: str, principal: CurrentUser, open_only: bool = False, db: OrmSession = Depends(get_db)):
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    _purge_expired(db, conversation_id)
    stmt = select(models.GroupTask).where(models.GroupTask.conversation_id == conversation_id)
    if open_only:
        stmt = stmt.where(models.GroupTask.done_at.is_(None))
    tasks = db.scalars(stmt.order_by(models.GroupTask.done_at.is_not(None), models.GroupTask.due_at.asc().nulls_last(), models.GroupTask.created_at).limit(200)).all()
    since = _visible_from(room, me)
    created = dict(db.execute(select(models.Message.id, models.Message.created_at).where(models.Message.id.in_([t.message_id for t in tasks]))).all())
    tasks = [t for t in tasks if t.message_id in created and (since is None or created[t.message_id] >= since)]
    names = _display_names({t.assignee_id for t in tasks if t.assignee_id}) if tasks else {}
    return {"items": [{**_task_out(t, principal.user_id, me.role, names), "message_id": t.message_id} for t in tasks]}


# --- what a page of the thread carries for groups (called by list_messages) --------

def page_extras(db: OrmSession, room: models.Conversation | None, me: models.Participant, rows: list[models.Message]) -> dict[str, dict]:
    """Per message id: its card, whether it is pinned, whether I saved it, and
    how many replies it has. A handful of queries for the whole page."""
    if not rows:
        return {}
    ids = [r.id for r in rows]
    out: dict[str, dict] = {r.id: {} for r in rows}
    saved = set(db.scalars(select(models.SavedMessage.message_id).where(models.SavedMessage.user_id == me.user_id, models.SavedMessage.message_id.in_(ids))).all())
    for mid in saved:
        out[mid]["saved"] = True
    if room is None or room.kind != "group":
        return out
    pinned = set(db.scalars(select(models.PinnedMessage.message_id).where(models.PinnedMessage.message_id.in_(ids))).all())
    since = _visible_from(room, me)
    replies_stmt = (
        select(models.Message.reply_to_id, func.count())
        .where(models.Message.conversation_id == room.id, models.Message.reply_to_id.in_(ids), models.Message.deleted_at.is_(None))
        .group_by(models.Message.reply_to_id)
    )
    if since is not None:
        replies_stmt = replies_stmt.where(models.Message.created_at >= since)
    for mid, count in db.execute(replies_stmt).all():
        out[mid]["reply_count"] = count
    for mid in pinned:
        out[mid]["pinned"] = True

    card_ids = [r.id for r in rows if r.kind in ("poll", "event", "task") and not r.deleted_at]
    if not card_ids:
        return out
    polls = db.scalars(select(models.Poll).where(models.Poll.message_id.in_(card_ids))).all()
    if polls:
        poll_ids = [p.id for p in polls]
        options = db.scalars(select(models.PollOption).where(models.PollOption.poll_id.in_(poll_ids)).order_by(models.PollOption.position)).all()
        votes = db.execute(select(models.PollVote.poll_id, models.PollVote.option_id, models.PollVote.user_id).where(models.PollVote.poll_id.in_(poll_ids))).all()
        for p in polls:
            out[p.message_id]["poll"] = _poll_out(
                db, p, me.user_id, me.role,
                options=[o for o in options if o.poll_id == p.id],
                votes=[(o, u) for pid, o, u in votes if pid == p.id],
            )
    events = db.scalars(select(models.GroupEvent).where(models.GroupEvent.message_id.in_(card_ids))).all()
    if events:
        responses = db.execute(select(models.EventResponse.event_id, models.EventResponse.user_id, models.EventResponse.response).where(models.EventResponse.event_id.in_([e.id for e in events]))).all()
        for e in events:
            out[e.message_id]["event"] = _event_out(db, e, me.user_id, me.role, responses=[(u, r) for eid, u, r in responses if eid == e.id])
    tasks = db.scalars(select(models.GroupTask).where(models.GroupTask.message_id.in_(card_ids))).all()
    if tasks:
        names = _display_names({t.assignee_id for t in tasks if t.assignee_id}) if any(t.assignee_id for t in tasks) else {}
        for t in tasks:
            out[t.message_id]["task"] = _task_out(t, me.user_id, me.role, names)
    return out


# --- reports --------------------------------------------------------------------

class ReportIn(BaseModel):
    target_kind: str = Field(pattern="^(message|member|group)$")
    # A message id, a member's user id, or the group's own id.
    target_id: str = Field(min_length=3, max_length=40)
    reason: str = Field(pattern="^(spam|harassment|hate|violence|sexual|child_safety|scam|other)$")
    note: str | None = Field(default=None, max_length=2_000)


@app.post("/conversations/{conversation_id}/reports", status_code=201, tags=["moderation"])
def report_in_conversation(conversation_id: str, payload: ReportIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Report a message, a member or the group to Kinjy's moderators.

    Members only, about what they can see. Reporting twice is reporting once,
    and the answer is the same whatever happens next.
    """
    room, me = _room_and_me(db, conversation_id, principal.user_id)
    if payload.target_kind == "message":
        _visible_message(db, room, me, payload.target_id)
    elif payload.target_kind == "member":
        if payload.target_id == principal.user_id or payload.target_id not in set(_participant_ids(db, conversation_id)):
            raise HTTPException(status_code=404, detail="Member not found")
    elif payload.target_id != conversation_id:
        raise HTTPException(status_code=404, detail="Group not found")
    exists = db.scalar(
        select(models.MessageReport).where(
            models.MessageReport.reporter_id == principal.user_id,
            models.MessageReport.target_kind == payload.target_kind,
            models.MessageReport.target_id == payload.target_id,
            models.MessageReport.conversation_id == conversation_id,
        )
    )
    if exists is None:
        db.add(models.MessageReport(
            reporter_id=principal.user_id, conversation_id=conversation_id, target_kind=payload.target_kind,
            target_id=payload.target_id, reason=payload.reason, note=(payload.note or "").strip() or None,
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"received": True}


@app.get("/admin/message-reports", tags=["admin"])
def admin_message_reports(_: ModeratorUser, status: str = "open", limit: int = 100, db: OrmSession = Depends(get_db)):
    """Reports from Messages, for Kinjy's moderators.

    A reported message is shown in the clear when the server can read it (group
    messages are not end-to-end encrypted). An end-to-end encrypted message
    cannot be read by anybody here, and the report says so.
    """
    stmt = select(models.MessageReport)
    if status in ("open", "closed"):
        stmt = stmt.where(models.MessageReport.status == status)
    rows = db.scalars(stmt.order_by(models.MessageReport.created_at.desc()).limit(min(limit, 300))).all()
    rooms = {c.id: c for c in db.scalars(select(models.Conversation).where(models.Conversation.id.in_({r.conversation_id for r in rows}))).all()}
    messages = {
        m.id: m
        for m in db.scalars(select(models.Message).where(models.Message.id.in_([r.target_id for r in rows if r.target_kind == "message"]))).all()
    }
    people = _profiles({r.reporter_id for r in rows} | {r.target_id for r in rows if r.target_kind == "member"} | {m.sender_id for m in messages.values() if m.sender_id})

    def message_view(mid: str) -> dict | None:
        m = messages.get(mid)
        if m is None:
            return {"gone": True}
        return {
            "id": m.id,
            "sender_id": m.sender_id,
            "sender_name": (people.get(m.sender_id) or {}).get("display_name"),
            "body": None if m.encrypted or m.deleted_at else _plain(m)[0],
            "encrypted": m.encrypted,
            "deleted": bool(m.deleted_at),
            "media_kind": m.media_kind,
            "created_at": m.created_at,
        }

    return {
        "items": [
            {
                "id": r.id,
                "conversation_id": r.conversation_id,
                "conversation_title": rooms[r.conversation_id].title if r.conversation_id in rooms else None,
                "conversation_kind": rooms[r.conversation_id].kind if r.conversation_id in rooms else None,
                "target_kind": r.target_kind,
                "target_id": r.target_id,
                "target_name": (people.get(r.target_id) or {}).get("display_name") if r.target_kind == "member" else None,
                "message": message_view(r.target_id) if r.target_kind == "message" else None,
                "reason": r.reason,
                "note": r.note,
                "reporter_name": (people.get(r.reporter_id) or {}).get("display_name"),
                "status": r.status,
                "resolution": r.resolution,
                "created_at": r.created_at,
                "closed_at": r.closed_at,
            }
            for r in rows
        ]
    }


class ReportCloseIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=2_000)
    remove_message: bool = False


@app.post("/admin/message-reports/{report_id}/close", tags=["admin"])
async def admin_close_report(report_id: int, payload: ReportCloseIn, principal: ModeratorUser, db: OrmSession = Depends(get_db)):
    """Close a report, and take the reported message down if it breaks the rules."""
    report = db.get(models.MessageReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    removed = False
    if payload.remove_message:
        if report.target_kind != "message":
            raise HTTPException(status_code=422, detail="Only a reported message can be removed from here")
        message = db.get(models.Message, report.target_id)
        if message is not None and not message.deleted_at:
            message.deleted_at = _now()
            message.removed_by = principal.user_id
            message.body = message.ciphertext = message.media_url = message.media_id = None
            message.media_kind = message.media_name = message.media_type = None
            message.media_size = None
            message.sticker_id = message.sealed_with = None
            message.announcement_at = None
            db.execute(delete(models.MessageReaction).where(models.MessageReaction.message_id == message.id))
            _drop_attached(db, [message.id])
            _audit(db, report.conversation_id, principal.user_id, "message_removed_by_kinjy", target_id=message.sender_id, detail=message.id)
            removed = True
    report.status, report.resolution, report.closed_by, report.closed_at = "closed", payload.resolution.strip(), principal.user_id, _now()
    # Every open report of the same thing is answered by the same decision.
    for other in db.scalars(
        select(models.MessageReport).where(
            models.MessageReport.target_kind == report.target_kind,
            models.MessageReport.target_id == report.target_id,
            models.MessageReport.status == "open",
        )
    ).all():
        other.status, other.resolution, other.closed_by, other.closed_at = "closed", payload.resolution.strip(), principal.user_id, _now()
    db.commit()
    if removed:
        await _tell_room(db, report.conversation_id, {"type": "message_deleted", "conversation_id": report.conversation_id, "message_id": report.target_id, "removed": True})
    return {"closed": True, "removed": removed}


# --- the audit trail ------------------------------------------------------------

AUDIT_WORDS = {
    "created": "created the group",
    "added": "added",
    "invited": "invited",
    "invite_cancelled": "withdrew the invitation of",
    "removed": "removed",
    "role_changed": "changed the role of",
    "ownership_transferred": "made the owner",
    "profile_changed": "changed the group's profile",
    "privacy_changed": "changed who can join with the link",
    "history_changed": "changed what new members can read",
    "setting_changed": "changed a setting",
    "link_changed": "changed the link",
    "link_reset": "replaced the link",
    "request_approved": "let in",
    "request_rejected": "turned away",
    "disappearing_changed": "changed disappearing messages",
    "message_removed": "removed a message from",
    "message_removed_by_kinjy": "(Kinjy) removed a message from",
    "announcement_marked": "marked a message as an announcement",
    "announcement_unmarked": "unmarked an announcement",
    "community_linked": "linked the group to a community",
    "community_unlinked": "unlinked the group from its community",
    "group_deleted": "deleted the group",
}


@app.get("/conversations/{conversation_id}/audit", tags=["groups"])
def group_audit(conversation_id: str, principal: CurrentUser, limit: int = 200, db: OrmSession = Depends(get_db)):
    """The group's sensitive changes, newest first. The owner and admins."""
    _group(db, conversation_id, principal.user_id)
    _runs_group(db, conversation_id, principal.user_id)
    rows = db.scalars(
        select(models.GroupAudit)
        .where(models.GroupAudit.conversation_id == conversation_id)
        .order_by(models.GroupAudit.created_at.desc())
        .limit(min(limit, 500))
    ).all()
    names = _display_names({r.actor_id for r in rows} | {r.target_id for r in rows if r.target_id}) if rows else {}
    return {
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "summary": f"{names.get(r.actor_id, 'Someone')} {AUDIT_WORDS.get(r.action, r.action)}"
                + (f" {names.get(r.target_id, '')}" if r.target_id else ""),
                "detail": r.detail,
                "actor_id": r.actor_id,
                "target_id": r.target_id,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


# --- the link to a Community (called by community-service) -----------------------

class CommunityLinkIn(BaseModel):
    actor_id: str
    community_id: str | None = None
    # Unlinking from the Community's side: its steward takes a chat off the
    # Community's page without having to run the chat. Only from that Community.
    unlink_from: str | None = None


@app.post("/internal/groups/{conversation_id}/community", tags=["internal"])
def link_community(conversation_id: str, payload: CommunityLinkIn, db: OrmSession = Depends(get_db)):
    """Link a group chat to a Community, or unlink it.

    community-service has already checked that the actor runs the Community;
    linking also needs them to run the group. Internal - never routed by the
    gateway.
    """
    room = db.get(models.Conversation, conversation_id)
    if room is None or room.kind != "group" or room.deleted_at:
        raise HTTPException(status_code=404, detail="Group not found")
    if room.is_welcome:
        raise HTTPException(status_code=403, detail="The welcome group cannot be linked")
    if payload.unlink_from:
        if room.community_id != payload.unlink_from:
            raise HTTPException(status_code=404, detail="This group is not linked to this community")
        room.community_id = None
        _audit(db, conversation_id, payload.actor_id, "community_unlinked")
        _system_message(db, conversation_id, "The community this group was linked to removed the link.")
        db.commit()
        return {"linked": False}
    me = db.scalar(select(models.Participant).where(models.Participant.conversation_id == conversation_id, models.Participant.user_id == payload.actor_id))
    if me is None or me.role not in RUNS:
        raise HTTPException(status_code=403, detail="Only the group's owner or an admin can link it")
    if payload.community_id is None:
        if room.community_id is None:
            return {"linked": False}
        room.community_id = None
        _audit(db, conversation_id, payload.actor_id, "community_unlinked")
        _system_message(db, conversation_id, _name(payload.actor_id) + " unlinked the group from its community.")
    else:
        if room.community_id and room.community_id != payload.community_id:
            raise HTTPException(status_code=409, detail="This group is linked to another community. Unlink it there first.")
        if room.community_id == payload.community_id:
            return {"linked": True, "already": True}
        room.community_id = payload.community_id
        _audit(db, conversation_id, payload.actor_id, "community_linked", detail=payload.community_id)
        _system_message(
            db, conversation_id,
            _name(payload.actor_id) + " linked the group to a community. Joining one does not make anybody a member of the other.",
        )
    db.commit()
    return {"linked": room.community_id is not None}


@app.get("/internal/communities/{community_id}/groups", tags=["internal"])
def community_groups(community_id: str, viewer: str | None = None, db: OrmSession = Depends(get_db)):
    """The group chats linked to a Community, as somebody allowed to see the
    Community sees them: each group's face, the viewer's own standing, and
    the way in when the group's link is open. Never its members or messages.

    community-service decides who may ask (the Community's own door rule).
    """
    import secrets

    rooms = db.scalars(
        select(models.Conversation).where(
            models.Conversation.community_id == community_id,
            models.Conversation.kind == "group",
            models.Conversation.deleted_at.is_(None),
        ).order_by(models.Conversation.created_at)
    ).all()
    ids = [r.id for r in rooms]
    counts = dict(db.execute(select(models.Participant.conversation_id, func.count()).where(models.Participant.conversation_id.in_(ids)).group_by(models.Participant.conversation_id)).all()) if ids else {}
    mine: set[str] = set()
    pending: set[str] = set()
    if viewer and ids:
        mine = set(db.scalars(select(models.Participant.conversation_id).where(models.Participant.user_id == viewer, models.Participant.conversation_id.in_(ids))).all())
        pending = set(db.scalars(select(models.JoinRequest.conversation_id).where(models.JoinRequest.user_id == viewer, models.JoinRequest.conversation_id.in_(ids))).all())
    from main import _link_problem

    items = []
    for r in rooms:
        usable = _link_problem(r) is None
        if usable and not r.invite_code:
            r.invite_code = secrets.token_urlsafe(12)
        items.append(
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "avatar_url": r.avatar_url,
                "privacy": r.privacy or "private",
                "members_count": counts.get(r.id, 0),
                "my_state": "member" if r.id in mine else "pending" if r.id in pending else "none",
                "join_path": f"/messages/join/{r.invite_code}" if usable and r.id not in mine else None,
            }
        )
    db.commit()
    return {"items": items}


# --- reminders: events and tasks ---------------------------------------------------

REMINDER_EVERY_S = 60


def _due_reminders() -> list[tuple[str, str, str, str]]:
    """Claim the reminders that are due, mark them sent, and return
    (user_id, conversation_id, title, what) for each. Rows are locked and
    skipped by any other worker, so a reminder is sent once."""
    now = _now()
    out: list[tuple[str, str, str, str]] = []
    with SessionLocal() as db:
        for window, column, label in (
            (timedelta(hours=1), "reminded_hour_at", "in an hour"),
            (timedelta(days=1), "reminded_day_at", "tomorrow"),
        ):
            events = db.scalars(
                select(models.GroupEvent)
                .where(
                    models.GroupEvent.cancelled_at.is_(None),
                    models.GroupEvent.starts_at > now,
                    models.GroupEvent.starts_at <= now + window,
                    getattr(models.GroupEvent, column).is_(None),
                )
                .limit(200)
                .with_for_update(skip_locked=True)
            ).all()
            for e in events:
                setattr(e, column, now)
                if column == "reminded_hour_at":
                    # The hour reminder stands for the day one when both are due.
                    e.reminded_day_at = e.reminded_day_at or now
                coming = db.scalars(
                    select(models.EventResponse.user_id).where(
                        models.EventResponse.event_id == e.id, models.EventResponse.response.in_(("going", "maybe"))
                    )
                ).all()
                rows = {p.user_id: p for p in db.scalars(select(models.Participant).where(models.Participant.conversation_id == e.conversation_id, models.Participant.user_id.in_(coming))).all()}
                for uid in coming:
                    if _wants(rows.get(uid), "reminder"):
                        out.append((uid, e.conversation_id, f"{e.title} starts {label}", "group_event"))
        tasks = db.scalars(
            select(models.GroupTask)
            .where(
                models.GroupTask.done_at.is_(None),
                models.GroupTask.assignee_id.is_not(None),
                models.GroupTask.due_at.is_not(None),
                models.GroupTask.due_at <= now,
                models.GroupTask.reminded_at.is_(None),
            )
            .limit(200)
            .with_for_update(skip_locked=True)
        ).all()
        for t in tasks:
            t.reminded_at = now
            row = db.scalar(select(models.Participant).where(models.Participant.conversation_id == t.conversation_id, models.Participant.user_id == t.assignee_id))
            if _wants(row, "reminder"):
                out.append((t.assignee_id, t.conversation_id, f"Task due: {t.title}", "group_task"))
        db.commit()
    return out


async def _reminder_worker() -> None:
    while True:
        await asyncio.sleep(REMINDER_EVERY_S)
        try:
            due = await asyncio.to_thread(_due_reminders)
            for uid, cid, title, kind in due:
                background = BackgroundTasks()
                with SessionLocal() as db:
                    await internal_notify(user_id=uid, background=background, kind=kind, title=title, body=None, link=f"/messages?c={cid}", db=db)
                await background()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("group reminders failed; trying again next round")


_worker_task: list[asyncio.Task] = []


def _start_reminders() -> None:
    _worker_task.append(asyncio.get_running_loop().create_task(_reminder_worker()))


STARTUP.append(_start_reminders)


# --- what the client may offer ------------------------------------------------

AI_URL = "http://ai-service:8000"


@app.get("/conversations/capabilities", tags=["messages"])
def messaging_capabilities(principal: CurrentUser):
    """What works on this installation, so the client offers nothing that would
    fail: translation needs a real model behind ai-service; group calls need a
    media server, which is not deployed."""
    import httpx

    translate = False
    try:
        response = httpx.get(f"{AI_URL}/internal/ai/capabilities", timeout=3)
        response.raise_for_status()
        translate = bool(response.json().get("translate", {}).get("live"))
    except Exception as exc:
        log.info("translation availability unknown: %s", exc)
    return {"translate": translate, "group_calls": False, "summaries": False}


# --- withdrawing notifications (social-service) --------------------------------

class ForgetIn(BaseModel):
    user_ids: list[str] = Field(default_factory=list, max_length=5000)
    links: list[str] = Field(default_factory=list, max_length=500)


@app.post("/internal/notifications/forget", tags=["internal"])
def forget_notifications(payload: ForgetIn, db: OrmSession = Depends(get_db)):
    """Delete stored notifications about content these people can no longer
    read (an audience narrowed, an opt-out). Internal - never routed."""
    if not payload.user_ids or not payload.links:
        return {"deleted": 0}
    deleted = db.execute(
        delete(models.Notification).where(
            models.Notification.user_id.in_(payload.user_ids), models.Notification.link.in_(payload.links)
        )
    ).rowcount or 0
    db.commit()
    return {"deleted": deleted}
