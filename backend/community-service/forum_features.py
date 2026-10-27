"""Kinjy · community-service — forums beyond threads and replies (27/10).

Settings and moderators, following forums, threads and topics, search across
what a member may read, related questions, drafts, editing and reopening,
moderation (pin, lock, hide, move, merge, reports, the audit trail),
knowledge articles that are edited, reviewed and traced back to their
sources, polls, expert question sessions, contributor profiles, the Community
page's forum activity, and the weekly digest.

Every read here goes through the same doors as the rest of the forums:
`_forum_access` (the forum chain and each forum's own visibility) and the age
engine. A result the member could not open is never in an answer.

Imported at the bottom of main.py, once what it borrows from there exists.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

import agecommunity
import models
from common import crypto, notify
from common.auth import CurrentUser, MaybeUser
from common.database import get_db
from common.ids import new_id
from main import (
    AI_URL,
    USER_URL,
    _can_enter,
    _community_door,
    _curated_entries,
    _forum_access,
    _get_forum_or_404,
    _like,
    _live,
    _membership,
    _moderates_forum,
    _profiles,
    _resolve,
    _thread_for,
    _visible_replies,
    _visible_threads,
)

log = logging.getLogger("community-service.forums")
router = APIRouter()
MEDIA_URL = "http://media-service:8000"

THREAD_KINDS = ("question", "discussion", "guide", "announcement")
VISIBILITY_LABELS = {
    "public": "Public",
    "community": "Community: as the community allows",
    "members": "Community members only",
    "private": "Private: moderators only",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _csv(value: str | None) -> list[str]:
    return [v for v in (value or "").split(",") if v]


def clean_tags(tags: list[str] | None) -> str:
    """Lower case, letters, digits and dashes, at most 8 of 40 characters."""
    out: list[str] = []
    for raw in tags or []:
        tag = re.sub(r"[^a-z0-9\-]+", "-", (raw or "").strip().lower()).strip("-")[:40]
        if tag and tag not in out:
            out.append(tag)
    return ",".join(out[:8])


def _readable_thread(db: OrmSession, thread: models.Thread, principal) -> bool:
    """Whether this member could open this thread right now: the forum's doors,
    the thread being open, and its rating for their age."""
    if thread.status != "open":
        return False
    forum = db.get(models.Forum, thread.forum_id)
    if forum is None or not _can_enter(db, forum, principal):
        return False
    age = agecommunity.viewer(principal.user_id if principal else None)
    return agecommunity.visible(db, age, thread.id)


def _as(user_id: str):
    return SimpleNamespace(user_id=user_id)


# --- settings checks used by main.create_forum ----------------------------------

def check_forum_settings(db: OrmSession, community_id: str | None, visibility: str, post_policy: str, reply_policy: str) -> None:
    if community_id is None:
        if visibility in ("members",) or "members" in (post_policy, reply_policy):
            raise HTTPException(status_code=422, detail="Only a forum of a community can be kept to its members")
        return
    community = db.get(models.Community, community_id)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    if visibility == "public" and community.kind not in ("public", "private"):
        raise HTTPException(
            status_code=422,
            detail="A forum of a secret or paid community cannot be public: it would open what members joined for",
        )


def _policy_ok(db: OrmSession, forum: models.Forum, user_id: str, policy: str) -> bool:
    if policy == "moderators":
        return _moderates_forum(db, forum, user_id)
    if policy == "members":
        if _moderates_forum(db, forum, user_id):
            return True
        membership = _membership(db, forum.community_id, user_id) if forum.community_id else None
        return membership is not None and membership.status == "active"
    return True


def may_post(db: OrmSession, forum: models.Forum, user_id: str, kind: str, session_id: str | None) -> None:
    if not _policy_ok(db, forum, user_id, forum.post_policy or "everyone"):
        raise HTTPException(status_code=403, detail="In this forum only some members can start threads")
    if kind == "announcement" and not _moderates_forum(db, forum, user_id):
        raise HTTPException(status_code=403, detail="Only the forum's moderators post announcements")
    if session_id:
        session = db.get(models.ExpertSession, session_id)
        now = _now()
        if session is None or session.forum_id != forum.id or session.cancelled_at:
            raise HTTPException(status_code=404, detail="Session not found")
        if not (session.starts_at <= now <= session.ends_at):
            raise HTTPException(status_code=409, detail="This session is not open for questions now")
        if kind != "question":
            raise HTTPException(status_code=422, detail="A session takes questions")


def may_reply(db: OrmSession, thread: models.Thread, user_id: str) -> None:
    forum = db.get(models.Forum, thread.forum_id)
    if forum is not None and not _policy_ok(db, forum, user_id, forum.reply_policy or "everyone"):
        raise HTTPException(status_code=403, detail="In this forum only some members can reply")


def resolve_attachments(ids: list[str], owner_id: str) -> str:
    """The files a member attaches, as media-service describes them.

    Only their own uploads, and only private ones (uploaded as attachments):
    those are served on signed links handed out after the forum's door, so a
    file in a closed forum cannot be fetched by its address alone.
    """
    out = []
    for asset_id in list(dict.fromkeys(ids or []))[:6]:
        try:
            response = httpx.get(f"{MEDIA_URL}/internal/assets/{asset_id}", timeout=5)
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="Attachments cannot be checked right now")
        if response.status_code != 200:
            raise HTTPException(status_code=422, detail="Attachment not found")
        asset = response.json()
        if asset.get("owner_id") != owner_id:
            raise HTTPException(status_code=422, detail="You can only attach files you uploaded")
        if not asset.get("private"):
            raise HTTPException(status_code=422, detail="Attach files uploaded as attachments")
        out.append(
            {
                "id": asset["id"],
                "url": asset.get("url"),
                "kind": asset.get("kind"),
                "name": asset.get("filename"),
                "type": asset.get("content_type"),
                "size": asset.get("size_bytes"),
            }
        )
    return json.dumps(out)


def _signed(attachments: str | None) -> list[dict]:
    items = json.loads(attachments or "[]")
    return [
        {**a, "url": crypto.sign_media_url(a["url"], a["id"]) if a.get("url") else None}
        for a in items
    ]


def check_event(db: OrmSession, forum: models.Forum, event_id: str) -> None:
    event = db.get(models.CommunityEvent, event_id)
    if event is None or event.community_id != forum.community_id or not forum.community_id:
        raise HTTPException(status_code=422, detail="Link an event of this forum's community")


def add_poll(db: OrmSession, thread: models.Thread, poll) -> None:
    options = [o.strip()[:200] for o in poll.options if o.strip()]
    if len(options) < 2 or len({o.casefold() for o in options}) != len(options):
        raise HTTPException(status_code=422, detail="A poll needs at least two different answers")
    closes = poll.closes_at
    if closes is not None:
        closes = closes if closes.tzinfo else closes.replace(tzinfo=timezone.utc)
        if closes <= _now():
            raise HTTPException(status_code=422, detail="The poll must close in the future")
    db.add(models.ForumPoll(thread_id=thread.id, question=poll.question.strip(), options=json.dumps(options), multiple=poll.multiple, closes_at=closes))


def _follow_row(db: OrmSession, thread_id: str, user_id: str) -> models.ThreadFollow:
    row = db.scalar(select(models.ThreadFollow).where(models.ThreadFollow.thread_id == thread_id, models.ThreadFollow.user_id == user_id))
    if row is None:
        row = models.ThreadFollow(thread_id=thread_id, user_id=user_id)
        db.add(row)
    return row


def thread_published(db: OrmSession, thread: models.Thread, forum: models.Forum, user_id: str, draft_id: str | None) -> None:
    """In the publishing transaction: the author follows their thread, and the
    draft it was written from goes."""
    _follow_row(db, thread.id, user_id).following = True
    if draft_id:
        db.execute(delete(models.ThreadDraft).where(models.ThreadDraft.id == draft_id, models.ThreadDraft.user_id == user_id))


# --- notifications --------------------------------------------------------------

MENTION_RE = re.compile(r"(?:^|(?<=\s))@([a-z0-9_.]{2,40})", re.IGNORECASE)
MAX_MENTIONS = 10
FAN_OUT_CAP = 200


def _prefs(db: OrmSession, user_id: str) -> models.ForumPrefs:
    row = db.get(models.ForumPrefs, user_id)
    return row if row is not None else models.ForumPrefs(user_id=user_id, replies=True, mentions=True, followed_threads=True, topics=True, digests=True, show_activity=True)


def _mentioned(text: str) -> list[str]:
    handles = []
    for raw in MENTION_RE.findall(text or ""):
        handle = raw.rstrip(".").lower()
        if handle not in handles:
            handles.append(handle)
    handles = handles[:MAX_MENTIONS]
    if not handles:
        return []
    try:
        response = httpx.post(f"{USER_URL}/internal/profiles/by-handle", json={"handles": handles}, timeout=5)
        response.raise_for_status()
        profiles = response.json().get("profiles", {}) or {}
    except Exception as exc:
        log.warning("could not resolve forum mentions: %s", exc)
        return []
    return [p["user_id"] for p in profiles.values() if p and p.get("user_id")]


def _blocked_pairs(author_id: str) -> set[str]:
    try:
        response = httpx.get(f"{USER_URL}/internal/blocks/{author_id}", timeout=3)
        response.raise_for_status()
        return set(response.json()["ids"])
    except Exception:
        return set()


def _send(db: OrmSession, recipients: dict[str, str], thread: models.Thread, author_id: str, titles: dict[str, str], body: str | None) -> None:
    """One notification per person, to those who can still read the thread and
    have no block with the author."""
    blocked = _blocked_pairs(author_id) if recipients else set()
    for uid, why in list(recipients.items())[:FAN_OUT_CAP]:
        if uid == author_id or uid in blocked:
            continue
        if not _readable_thread(db, thread, _as(uid)):
            continue
        notify.notify(uid, kind=f"forum_{why}", title=titles[why], body=body, link=f"/forums?thread={thread.id}")


def announce_thread(db: OrmSession, thread: models.Thread, forum: models.Forum, author_id: str) -> None:
    """Mentions, the forum's followers who asked for each thread, and whoever
    follows one of its topics - by each person's own settings."""
    name = (_profiles({author_id}).get(author_id) or {}).get("display_name") or "Someone"
    recipients: dict[str, str] = {}
    for uid in _mentioned(f"{thread.title} {thread.body}"):
        if _prefs(db, uid).mentions:
            recipients.setdefault(uid, "mention")
    for uid in db.scalars(select(models.ForumFollow.user_id).where(models.ForumFollow.forum_id == forum.id, models.ForumFollow.notify_new.is_(True))).all():
        recipients.setdefault(uid, "new_thread")
    tags = _csv(thread.tags)
    if tags:
        for uid in db.scalars(select(models.TopicFollow.user_id).where(models.TopicFollow.tag.in_(tags))).all():
            if _prefs(db, uid).topics:
                recipients.setdefault(uid, "topic")
    _send(
        db, recipients, thread, author_id,
        {
            "mention": f"{name} mentioned you in a forum thread",
            "new_thread": f"New thread in {forum.name}",
            "topic": f"New thread on a topic you follow: {', '.join(tags[:2])}",
        },
        None,
    )


def notify_reply(db: OrmSession, thread: models.Thread, reply: models.Reply, author_id: str) -> None:
    _follow_row(db, thread.id, author_id).following = True
    db.commit()
    name = (_profiles({author_id}).get(author_id) or {}).get("display_name") or "Someone"
    recipients: dict[str, str] = {}
    for uid in _mentioned(reply.body):
        if _prefs(db, uid).mentions:
            recipients.setdefault(uid, "mention")
    parent = db.get(models.Reply, reply.parent_id) if reply.parent_id else None
    for uid in (parent.author_id if parent else None, thread.author_id):
        if uid and _prefs(db, uid).replies:
            recipients.setdefault(uid, "reply")
    for uid in db.scalars(select(models.ThreadFollow.user_id).where(models.ThreadFollow.thread_id == thread.id, models.ThreadFollow.following.is_(True))).all():
        if _prefs(db, uid).followed_threads:
            recipients.setdefault(uid, "followed")
    _send(
        db, recipients, thread, author_id,
        {
            "mention": f"{name} mentioned you in a forum reply",
            "reply": f"{name} replied to you" if parent else f"{name} replied in a thread you are in",
            "followed": f"{name} replied in a thread you follow",
        },
        reply.body[:140],
    )


# --- what lists and pages carry (called by main) ------------------------------------

def _visibility_label(db: OrmSession, forum: models.Forum) -> str:
    v = forum.visibility or "inherit"
    if v == "private":
        return "private"
    if not forum.community_id:
        return "public"
    if v == "members":
        return "members"
    community = db.get(models.Community, forum.community_id)
    if v == "public" and community is not None and community.kind in ("public", "private"):
        return "public"
    return "public" if community is not None and community.kind == "public" else "members"


def categories(db: OrmSession) -> list[str]:
    rows = db.scalars(
        select(models.Forum.category).where(models.Forum.category.is_not(None), models.Forum.visibility != "private").distinct()
    ).all()
    return sorted({r for r in rows if r})


def forum_facts(db: OrmSession, rows, principal, communities) -> dict[str, dict]:
    ids = [r.id for r in rows]
    if not ids:
        return {}
    open_threads = (models.Thread.forum_id.in_(ids), models.Thread.status == "open")
    unanswered = dict(
        db.execute(
            select(models.Thread.forum_id, func.count())
            .where(*open_threads, models.Thread.replies_count == 0, models.Thread.kind.in_(("question", "discussion")))
            .group_by(models.Thread.forum_id)
        ).all()
    )
    last = dict(db.execute(select(models.Thread.forum_id, func.max(models.Thread.last_activity_at)).where(*open_threads).group_by(models.Thread.forum_id)).all())
    followed = set()
    if principal:
        followed = set(db.scalars(select(models.ForumFollow.forum_id).where(models.ForumFollow.user_id == principal.user_id, models.ForumFollow.forum_id.in_(ids))).all())
    return {
        r.id: {
            "category": r.category,
            "tags": _csv(r.tags),
            "lang": r.lang,
            "country": r.country,
            "visibility": _visibility_label(db, r),
            "unanswered_count": unanswered.get(r.id, 0),
            "last_activity_at": last.get(r.id),
            "following": r.id in followed,
            "post_policy": r.post_policy or "everyone",
            "reply_policy": r.reply_policy or "everyone",
        }
        for r in rows
    }


def card_extras(db: OrmSession, rows, principal) -> dict[str, dict]:
    polls = set(db.scalars(select(models.ForumPoll.thread_id).where(models.ForumPoll.thread_id.in_([r.id for r in rows]))).all()) if rows else set()
    return {
        r.id: {
            "kind": r.kind or "question",
            "tags": _csv(r.tags),
            "lang": r.lang,
            "country": r.country,
            "region": r.region,
            "edited_at": r.edited_at,
            "locked": r.locked,
            "commercial": r.commercial,
            "has_poll": r.id in polls,
            "session_id": r.session_id,
            # The date of the latest reply, when there is one.
            "last_reply_at": r.last_activity_at if r.replies_count else None,
            "attachments_count": len(json.loads(r.attachments or "[]")),
        }
        for r in rows
    }


def merged_target(db: OrmSession, thread_id: str, principal) -> str | None:
    thread = db.get(models.Thread, thread_id)
    if thread is None or thread.status != "merged" or not thread.merged_into:
        return None
    # Only for somebody who could read the original too: a merge is not a door.
    forum = db.get(models.Forum, thread.forum_id)
    if forum is None or not _can_enter(db, forum, principal):
        return None
    try:
        _thread_for(db, thread.merged_into, principal)
    except HTTPException:
        return None
    return thread.merged_into


BADGES = (
    # (key, label, test on the stats)
    ("helpful", "Helpful answers", lambda s: s["helpful_votes"] >= 10),
    ("solver", "Accepted answers", lambda s: s["accepted"] >= 3),
    ("knowledge", "Knowledge contributor", lambda s: s["knowledge"] >= 1),
)


def _stats(db: OrmSession, user_ids: set[str], forum_ids: list[str] | None = None) -> dict[str, dict]:
    """Answers, accepted answers, helpful votes and knowledge contributions,
    counted only in the given forums (those the viewer may read)."""
    if not user_ids:
        return {}
    base = select(models.Reply.author_id, func.count(), func.coalesce(func.sum(models.Reply.upvotes), 0)).join(
        models.Thread, models.Thread.id == models.Reply.thread_id
    ).where(models.Reply.author_id.in_(user_ids), models.Reply.status == "published", models.Thread.status == "open")
    if forum_ids is not None:
        base = base.where(models.Thread.forum_id.in_(forum_ids))
    answers = {uid: (n, votes) for uid, n, votes in db.execute(base.group_by(models.Reply.author_id)).all()}
    acc = select(models.Reply.author_id, func.count()).join(models.Thread, models.Thread.id == models.Reply.thread_id).where(
        models.Reply.author_id.in_(user_ids), models.Reply.accepted_answer.is_(True), models.Reply.status == "published", models.Thread.status == "open"
    )
    if forum_ids is not None:
        acc = acc.where(models.Thread.forum_id.in_(forum_ids))
    accepted = dict(db.execute(acc.group_by(models.Reply.author_id)).all())
    kstmt = select(models.KnowledgeEntry.contributors).where(models.KnowledgeEntry.status == "published")
    if forum_ids is not None:
        kstmt = kstmt.where(models.KnowledgeEntry.forum_id.in_(forum_ids))
    knowledge: dict[str, int] = {}
    for contributors in db.scalars(kstmt).all():
        for uid in _csv(contributors):
            if uid in user_ids:
                knowledge[uid] = knowledge.get(uid, 0) + 1
    out = {}
    for uid in user_ids:
        stats = {
            "answers": answers.get(uid, (0, 0))[0],
            "helpful_votes": int(answers.get(uid, (0, 0))[1] or 0),
            "accepted": accepted.get(uid, 0),
            "knowledge": knowledge.get(uid, 0),
        }
        stats["badges"] = [{"key": k, "label": label} for k, label, test in BADGES if test(stats)]
        out[uid] = stats
    return out


def _readable_forum_ids(db: OrmSession, principal) -> list[str]:
    return [f.id for f in db.scalars(select(models.Forum)).all() if _can_enter(db, f, principal)]


def thread_extras(db: OrmSession, thread: models.Thread, replies, principal) -> dict:
    forum = db.get(models.Forum, thread.forum_id)
    user_id = principal.user_id if principal else None
    moderates = bool(user_id) and _moderates_forum(db, forum, user_id)
    follow = db.scalar(select(models.ThreadFollow).where(models.ThreadFollow.thread_id == thread.id, models.ThreadFollow.user_id == user_id)) if user_id else None
    poll = db.scalar(select(models.ForumPoll).where(models.ForumPoll.thread_id == thread.id))
    session = db.get(models.ExpertSession, thread.session_id) if thread.session_id else None
    event = None
    if thread.event_id and forum.community_id:
        row = db.get(models.CommunityEvent, thread.event_id)
        try:
            _community_door(db, forum.community_id, principal)
            visible_event = row is not None
        except HTTPException:
            visible_event = False
        if visible_event:
            event = {"id": row.id, "title": row.title, "starts_at": row.starts_at, "timezone": row.timezone, "status": row.status}
    authors = {thread.author_id} | {r.author_id for r in replies}
    people = {uid: p for uid, p in db.execute(select(models.ForumPrefs.user_id, models.ForumPrefs.background).where(models.ForumPrefs.user_id.in_(authors))).all()}
    solved = any(r.accepted_answer for r in replies)
    return {
        "thread": {
            "kind": thread.kind or "question",
            "tags": _csv(thread.tags),
            "country": thread.country,
            "region": thread.region,
            "pinned": thread.pinned,
            "edited_at": thread.edited_at,
            "reopened_at": thread.reopened_at,
            "commercial": thread.commercial,
            "solved": solved,
            "replies_count": thread.replies_count,
            "views_count": thread.views_count,
            "last_activity_at": thread.last_activity_at,
            "moved_from": thread.moved_from,
            "attachments": _signed(thread.attachments),
            "poll": _poll_out(db, poll, user_id) if poll else None,
            "event": event,
            "session": {"id": session.id, "title": session.title, "expert_id": session.expert_id} if session else None,
            "forum": {"id": forum.id, "name": forum.name, "rules": forum.rules, "community_id": forum.community_id},
            "following": bool(follow and follow.following),
            "saved": bool(follow and follow.saved),
            "can_reply": bool(user_id) and not thread.locked and _policy_ok(db, forum, user_id, forum.reply_policy or "everyone"),
            "can_edit": bool(user_id) and user_id == thread.author_id and not thread.locked,
            "can_moderate": moderates,
            "can_accept": bool(user_id) and user_id == thread.author_id and (thread.kind or "question") == "question" and not thread.locked,
            "can_reopen": bool(user_id) and user_id == thread.author_id and solved,
            "author_background": people.get(thread.author_id),
        },
        "replies": {
            r.id: {
                "edited_at": r.edited_at,
                "commercial": r.commercial,
                "attachments": _signed(r.attachments),
                "is_author": r.author_id == thread.author_id,
                "is_expert": bool(session and r.author_id == session.expert_id),
                "author_background": people.get(r.author_id),
                "can_edit": bool(user_id) and user_id == r.author_id and not thread.locked,
            }
            for r in replies
        },
    }


def entry_extras(db: OrmSession, entry: models.KnowledgeEntry) -> dict:
    forum = db.get(models.Forum, entry.forum_id)
    review_days = (forum.review_days if forum else None) or 180
    checked = entry.reviewed_at or entry.published_at or entry.created_at
    contributors = _csv(entry.contributors)
    names = _profiles(set(contributors)) if contributors else {}
    return {
        "status": entry.status or "published",
        "ai_generated": bool(entry.ai_generated),
        "source_reply_ids": _csv(entry.source_reply_ids),
        "source_links": json.loads(entry.source_links or "[]"),
        "contributors": [
            {"user_id": uid, "display_name": (names.get(uid) or {}).get("display_name"), "handle": (names.get(uid) or {}).get("handle")}
            for uid in contributors
        ],
        "tags": _csv(entry.tags),
        "created_at": entry.created_at,
        "published_at": entry.published_at or entry.created_at,
        "updated_at": entry.updated_at,
        "reviewed_at": entry.reviewed_at,
        # Not looked at for longer than the forum allows.
        "needs_review": checked is not None and checked < _now() - timedelta(days=review_days),
    }


# --- polls --------------------------------------------------------------------------

def _poll_out(db: OrmSession, poll: models.ForumPoll, user_id: str | None) -> dict:
    options = json.loads(poll.options)
    votes = db.execute(select(models.ForumPollVote.option, models.ForumPollVote.user_id).where(models.ForumPollVote.poll_id == poll.id)).all()
    counts = [0] * len(options)
    for option, _ in votes:
        if 0 <= option < len(counts):
            counts[option] += 1
    closed = poll.closes_at is not None and poll.closes_at <= _now()
    return {
        "id": poll.id,
        "question": poll.question,
        "options": [{"index": i, "text": t, "votes": counts[i]} for i, t in enumerate(options)],
        "multiple": poll.multiple,
        "closes_at": poll.closes_at,
        "closed": closed,
        "voters": len({u for _, u in votes}),
        "my_votes": sorted({o for o, u in votes if u == user_id}),
    }


class VoteIn(BaseModel):
    options: list[int] = Field(default_factory=list, max_length=10)


@router.post("/threads/{thread_id}/poll/vote", tags=["forums"])
def vote_thread_poll(thread_id: str, payload: VoteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    thread = _thread_for(db, thread_id, principal)
    if not _readable_thread(db, thread, principal):
        raise HTTPException(status_code=404, detail="Thread not found")
    if thread.locked:
        raise HTTPException(status_code=403, detail="This thread is locked")
    poll = db.scalar(select(models.ForumPoll).where(models.ForumPoll.thread_id == thread_id))
    if poll is None:
        raise HTTPException(status_code=404, detail="This thread has no poll")
    if poll.closes_at is not None and poll.closes_at <= _now():
        raise HTTPException(status_code=409, detail="This poll is closed")
    n = len(json.loads(poll.options))
    chosen = sorted(set(payload.options))
    if any(o < 0 or o >= n for o in chosen) or (not poll.multiple and len(chosen) > 1):
        raise HTTPException(status_code=422, detail="Choose one of the poll's answers")
    db.execute(delete(models.ForumPollVote).where(models.ForumPollVote.poll_id == poll.id, models.ForumPollVote.user_id == principal.user_id))
    for o in chosen:
        db.add(models.ForumPollVote(poll_id=poll.id, option=o, user_id=principal.user_id))
    db.commit()
    return _poll_out(db, poll, principal.user_id)


# --- search and suggestions ----------------------------------------------------------

def _search_threads(db: OrmSession, principal, forum_ids: list[str], words: list[str], *, filters: dict, limit: int):
    age = agecommunity.viewer(principal.user_id if principal else None)
    stmt = select(models.Thread).where(models.Thread.forum_id.in_(forum_ids), models.Thread.status == "open")
    clauses = []
    for word in words:
        pattern = _like(word)
        matches_reply = models.Thread.id.in_(
            select(models.Reply.thread_id).where(models.Reply.status == "published", func.lower(models.Reply.body).like(pattern, escape="\\"))
        )
        clauses.append(or_(func.lower(models.Thread.title).like(pattern, escape="\\"), func.lower(models.Thread.body).like(pattern, escape="\\"), matches_reply))
    if clauses:
        stmt = stmt.where(or_(*clauses))
    if filters.get("kind"):
        stmt = stmt.where(models.Thread.kind == filters["kind"])
    if filters.get("lang"):
        stmt = stmt.where(models.Thread.lang == filters["lang"])
    if filters.get("country"):
        stmt = stmt.where(models.Thread.country == filters["country"].upper())
    if filters.get("tag"):
        stmt = stmt.where(func.lower(models.Thread.tags).like(_like(filters["tag"].lower()), escape="\\"))
    stmt = agecommunity.restrict_query(stmt, models.Thread, age)
    rows = agecommunity.readable_page(db, stmt.order_by(models.Thread.last_activity_at.desc(), models.Thread.id.desc()), age, limit=limit, offset=0)
    return _visible_threads(db, age, rows)


def _brief_threads(db: OrmSession, rows) -> list[dict]:
    forums = {f.id: f for f in db.scalars(select(models.Forum).where(models.Forum.id.in_({r.forum_id for r in rows}))).all()} if rows else {}
    solved = set(db.scalars(select(models.Reply.thread_id).where(models.Reply.thread_id.in_([r.id for r in rows]), models.Reply.accepted_answer.is_(True), models.Reply.status == "published")).all()) if rows else set()
    profiles = _profiles({r.author_id for r in rows})
    return [
        {
            "id": r.id,
            "title": r.title,
            "excerpt": (r.body or "")[:200],
            "kind": r.kind or "question",
            "forum_id": r.forum_id,
            "forum_name": forums[r.forum_id].name if r.forum_id in forums else None,
            "solved": r.id in solved,
            "replies_count": r.replies_count,
            "author": profiles.get(r.author_id),
            "last_activity_at": r.last_activity_at,
        }
        for r in rows
    ]


@router.get("/forums/search", tags=["forums"])
def search_forums(
    principal: MaybeUser,
    q: str = Query(min_length=2, max_length=100),
    forum_id: str | None = None,
    kind: str | None = Query(default=None, pattern="^(question|discussion|guide|announcement)$"),
    lang: str | None = Query(default=None, max_length=5),
    country: str | None = Query(default=None, max_length=2),
    tag: str | None = Query(default=None, max_length=40),
    db: OrmSession = Depends(get_db),
):
    """Questions, answers and knowledge articles the member may read: in one
    forum, or across every forum they can enter."""
    if forum_id:
        forum = _get_forum_or_404(db, forum_id)
        _forum_access(db, forum, principal)
        forum_ids = [forum_id]
    else:
        forum_ids = _readable_forum_ids(db, principal)
    if not forum_ids:
        return {"threads": [], "knowledge": []}
    filters = {"kind": kind, "lang": lang, "country": country, "tag": tag}
    rows = _search_threads(db, principal, forum_ids, [q.strip()], filters=filters, limit=30)
    age = agecommunity.viewer(principal.user_id if principal else None)
    with_entries = db.scalars(
        select(models.KnowledgeEntry.forum_id).where(
            models.KnowledgeEntry.forum_id.in_(forum_ids),
            models.KnowledgeEntry.status == "published",
            or_(func.lower(models.KnowledgeEntry.question).like(_like(q), escape="\\"), func.lower(models.KnowledgeEntry.answer).like(_like(q), escape="\\")),
        ).distinct().limit(10)
    ).all()
    knowledge = []
    for fid in with_entries:
        for entry in _curated_entries(db, fid, age, q, 5):
            knowledge.append({**entry, "forum_id": fid})
    return {"threads": _brief_threads(db, rows), "knowledge": knowledge[:20]}


STOP = {"what", "when", "where", "which", "with", "this", "that", "from", "have", "your", "about", "there", "does", "should", "would", "could", "pour", "dans", "avec", "comment", "quel", "quelle"}


@router.get("/forums/related", tags=["forums"])
def related_threads(principal: MaybeUser, title: str = Query(min_length=3, max_length=300), forum_id: str | None = None, db: OrmSession = Depends(get_db)):
    """Existing threads that look like the one being written - before it is
    posted. Only threads the member may read."""
    words = [w for w in re.findall(r"[\w'-]{4,}", title.lower()) if w not in STOP][:6]
    if not words:
        return {"items": []}
    forum_ids = [forum_id] if forum_id else _readable_forum_ids(db, principal)
    if forum_id:
        _forum_access(db, _get_forum_or_404(db, forum_id), principal)
    rows = _search_threads(db, principal, forum_ids, words, filters={}, limit=40)
    scored = sorted(rows, key=lambda r: -sum(w in f"{r.title} {r.body}".lower() for w in words))
    return {"items": _brief_threads(db, [r for r in scored if sum(w in r.title.lower() for w in words) >= 1][:5])}


@router.get("/forums/recommended", tags=["forums"])
def recommended_forums(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Forums the member does not follow yet, with why: the same subject as one
    they follow, one of their communities, or simply busy this month."""
    followed = set(db.scalars(select(models.ForumFollow.forum_id).where(models.ForumFollow.user_id == principal.user_id)).all())
    mine = set(db.scalars(select(models.Membership.community_id).where(models.Membership.user_id == principal.user_id, models.Membership.status == "active")).all())
    cats = set(db.scalars(select(models.Forum.category).where(models.Forum.id.in_(followed), models.Forum.category.is_not(None))).all()) if followed else set()
    month = _now() - timedelta(days=30)
    busy = dict(db.execute(select(models.Thread.forum_id, func.count()).where(models.Thread.created_at >= month, models.Thread.status == "open").group_by(models.Thread.forum_id)).all())
    out = []
    for f in db.scalars(select(models.Forum).where(models.Forum.id.not_in(followed) if followed else True)).all():
        if not _can_enter(db, f, principal):
            continue
        if f.category and f.category in cats:
            reason, rank = f"Like the forums you follow: {f.category}", 0
        elif f.community_id in mine:
            reason, rank = "In one of your communities", 1
        elif busy.get(f.id, 0) >= 2:
            reason, rank = f"{busy[f.id]} new threads this month", 2
        else:
            continue
        out.append((rank, -busy.get(f.id, 0), {"id": f.id, "slug": f.slug, "name": f.name, "description": f.description, "category": f.category, "threads_count": f.threads_count, "reason": reason}))
    out.sort(key=lambda x: (x[0], x[1]))
    return {"items": [o for _, _, o in out[:8]]}


# --- one forum: detail, settings, moderators, follow ---------------------------------

def _can_configure(db: OrmSession, forum: models.Forum, user_id: str) -> bool:
    """Settings and moderators: the community's owner and moderators, or the
    creator of a forum with no community."""
    if forum.community_id:
        membership = _membership(db, forum.community_id, user_id)
        return membership is not None and membership.status == "active" and membership.role in ("owner", "moderator")
    return forum.created_by == user_id


def _mod_log(db: OrmSession, forum_id: str, actor: str, action: str, kind: str | None = None, target: str | None = None, reason: str | None = None) -> None:
    db.add(models.ForumModAction(forum_id=forum_id, actor_id=actor, action=action[:30], target_kind=kind, target_id=target, reason=(reason or None) and reason[:2000]))


@router.get("/forums/{forum_id}", tags=["forums"])
def forum_detail(forum_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    communities = {}
    if forum.community_id:
        community = db.get(models.Community, forum.community_id)
        if community is not None:
            communities[community.id] = community
    facts = forum_facts(db, [forum], principal, communities).get(forum.id, {})
    mods = db.scalars(select(models.ForumModerator).where(models.ForumModerator.forum_id == forum.id)).all()
    people = _profiles({m.user_id for m in mods})
    user_id = principal.user_id if principal else None
    follow = db.scalar(select(models.ForumFollow).where(models.ForumFollow.forum_id == forum.id, models.ForumFollow.user_id == user_id)) if user_id else None
    now = _now()
    sessions = db.scalars(
        select(models.ExpertSession).where(models.ExpertSession.forum_id == forum.id, models.ExpertSession.cancelled_at.is_(None), models.ExpertSession.ends_at >= now - timedelta(days=7)).order_by(models.ExpertSession.starts_at)
    ).all()
    experts = _profiles({s.expert_id for s in sessions})
    return {
        "id": forum.id,
        "slug": forum.slug,
        "name": forum.name,
        "description": forum.description,
        "rules": forum.rules,
        "threads_count": forum.threads_count,
        "visibility_setting": forum.visibility or "inherit",
        "review_days": forum.review_days or 180,
        "community": (
            {"id": c.id, "slug": c.slug, "name": c.name, "kind": c.kind} if (c := communities.get(forum.community_id)) else None
        ),
        **facts,
        "follow": {"notify_new": follow.notify_new, "digest": follow.digest} if follow else None,
        "moderators": [
            {
                "user_id": m.user_id,
                "display_name": (people.get(m.user_id) or {}).get("display_name"),
                "handle": (people.get(m.user_id) or {}).get("handle"),
                "can_moderate": m.can_moderate,
                "can_edit_knowledge": m.can_edit_knowledge,
            }
            for m in mods
        ],
        "can_post": bool(user_id) and _policy_ok(db, forum, user_id, forum.post_policy or "everyone"),
        "can_announce": bool(user_id) and _moderates_forum(db, forum, user_id),
        "can_moderate": bool(user_id) and _moderates_forum(db, forum, user_id),
        "can_configure": bool(user_id) and _can_configure(db, forum, user_id),
        "can_edit_knowledge": bool(user_id) and _moderates_forum(db, forum, user_id, knowledge=True),
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "description": s.description,
                "expert_id": s.expert_id,
                "expert": experts.get(s.expert_id),
                "expert_intro": s.expert_intro,
                "starts_at": s.starts_at,
                "ends_at": s.ends_at,
                "open": s.starts_at <= now <= s.ends_at,
            }
            for s in sessions
        ],
    }


class ForumSettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=140)
    description: str | None = Field(default=None, max_length=4_000)
    category: str | None = Field(default=None, max_length=60)
    tags: list[str] | None = Field(default=None, max_length=10)
    lang: str | None = Field(default=None, max_length=5)
    country: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    visibility: str | None = Field(default=None, pattern="^(inherit|public|members|private)$")
    post_policy: str | None = Field(default=None, pattern="^(everyone|members|moderators)$")
    reply_policy: str | None = Field(default=None, pattern="^(everyone|members|moderators)$")
    rules: str | None = Field(default=None, max_length=8_000)
    review_days: int | None = Field(default=None, ge=7, le=3650)

    model_config = {"extra": "forbid"}


@router.patch("/forums/{forum_id}", tags=["forums"])
def update_forum(forum_id: str, payload: ForumSettingsIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not _can_configure(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the community's owner or moderators can change this forum")
    sent = payload.model_dump(exclude_unset=True)
    check_forum_settings(
        db, forum.community_id,
        sent.get("visibility") or forum.visibility or "inherit",
        sent.get("post_policy") or forum.post_policy or "everyone",
        sent.get("reply_policy") or forum.reply_policy or "everyone",
    )
    changed = []
    for key, value in sent.items():
        if key == "tags":
            value = clean_tags(value)
        elif isinstance(value, str):
            value = value.strip() or None if key in ("category", "rules", "lang") else value.strip()
        if getattr(forum, key) != value:
            setattr(forum, key, value)
            changed.append(key)
    if changed:
        _mod_log(db, forum.id, principal.user_id, "settings_changed", reason=", ".join(changed))
    db.commit()
    return {"changed": changed}


class ModeratorIn(BaseModel):
    user_id: str = Field(min_length=3, max_length=40)
    can_moderate: bool = True
    can_edit_knowledge: bool = True


@router.get("/forums/{forum_id}/moderators", tags=["forums"])
def forum_moderators(forum_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    return {"items": forum_detail(forum_id, principal, db)["moderators"]}


@router.post("/forums/{forum_id}/moderators", tags=["forums"])
def appoint_moderator(forum_id: str, payload: ModeratorIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not _can_configure(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the community's owner or moderators appoint forum moderators")
    if forum.community_id:
        membership = _membership(db, forum.community_id, payload.user_id)
        if membership is None or membership.status != "active":
            raise HTTPException(status_code=422, detail="A forum moderator must be a member of the community")
    row = db.scalar(select(models.ForumModerator).where(models.ForumModerator.forum_id == forum.id, models.ForumModerator.user_id == payload.user_id))
    if row is None:
        row = models.ForumModerator(forum_id=forum.id, user_id=payload.user_id, appointed_by=principal.user_id)
        db.add(row)
    row.can_moderate, row.can_edit_knowledge = payload.can_moderate, payload.can_edit_knowledge
    _mod_log(db, forum.id, principal.user_id, "moderator_set", "member", payload.user_id, f"moderate={payload.can_moderate}, knowledge={payload.can_edit_knowledge}")
    db.commit()
    notify.notify(payload.user_id, kind="forum_moderator", title=f"You are now a moderator of the forum {forum.name}", link=f"/forums?forum={forum.id}")
    return {"user_id": payload.user_id, "can_moderate": row.can_moderate, "can_edit_knowledge": row.can_edit_knowledge}


@router.delete("/forums/{forum_id}/moderators/{user_id}", tags=["forums"])
def remove_moderator(forum_id: str, user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not _can_configure(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the community's owner or moderators remove forum moderators")
    removed = db.execute(delete(models.ForumModerator).where(models.ForumModerator.forum_id == forum.id, models.ForumModerator.user_id == user_id)).rowcount
    if removed:
        _mod_log(db, forum.id, principal.user_id, "moderator_removed", "member", user_id)
    db.commit()
    return {"removed": bool(removed)}


class FollowIn(BaseModel):
    notify_new: bool | None = None
    digest: bool | None = None


@router.post("/forums/{forum_id}/follow", tags=["forums"])
def follow_forum(forum_id: str, principal: CurrentUser, payload: FollowIn | None = None, db: OrmSession = Depends(get_db)):
    """Follow a forum (or change how). Following grants nothing: the forum's
    own door still decides what is read, and joining its community is separate."""
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    row = db.scalar(select(models.ForumFollow).where(models.ForumFollow.forum_id == forum.id, models.ForumFollow.user_id == principal.user_id))
    if row is None:
        row = models.ForumFollow(forum_id=forum.id, user_id=principal.user_id)
        db.add(row)
    if payload is not None:
        if payload.notify_new is not None:
            row.notify_new = payload.notify_new
        if payload.digest is not None:
            row.digest = payload.digest
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return {"following": True, "notify_new": row.notify_new, "digest": row.digest}


@router.delete("/forums/{forum_id}/follow", tags=["forums"])
def unfollow_forum(forum_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    db.execute(delete(models.ForumFollow).where(models.ForumFollow.forum_id == forum_id, models.ForumFollow.user_id == principal.user_id))
    db.commit()
    return {"following": False}


# --- members' own settings, topics, saved threads, drafts ---------------------------------

class PrefsIn(BaseModel):
    replies: bool | None = None
    mentions: bool | None = None
    followed_threads: bool | None = None
    topics: bool | None = None
    digests: bool | None = None
    background: str | None = Field(default=None, max_length=1_000)
    show_activity: bool | None = None

    model_config = {"extra": "forbid"}


def _prefs_out(row: models.ForumPrefs) -> dict:
    return {
        "replies": row.replies is not False,
        "mentions": row.mentions is not False,
        "followed_threads": row.followed_threads is not False,
        "topics": row.topics is not False,
        "digests": row.digests is not False,
        "background": row.background,
        "show_activity": row.show_activity is not False,
    }


@router.get("/forum-prefs", tags=["forums"])
def get_prefs(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    out = _prefs_out(_prefs(db, principal.user_id))
    out["topics_followed"] = sorted(db.scalars(select(models.TopicFollow.tag).where(models.TopicFollow.user_id == principal.user_id)).all())
    return out


@router.patch("/forum-prefs", tags=["forums"])
def set_prefs(payload: PrefsIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    row = db.get(models.ForumPrefs, principal.user_id)
    if row is None:
        row = models.ForumPrefs(user_id=principal.user_id)
        db.add(row)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value.strip() or None if isinstance(value, str) else value)
    row.updated_at = _now()
    db.commit()
    return _prefs_out(row)


@router.post("/forum-topics/{tag}/follow", tags=["forums"])
def follow_topic(tag: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    clean = clean_tags([tag])
    if not clean:
        raise HTTPException(status_code=422, detail="Not a topic")
    if not db.scalar(select(models.TopicFollow).where(models.TopicFollow.tag == clean, models.TopicFollow.user_id == principal.user_id)):
        db.add(models.TopicFollow(tag=clean, user_id=principal.user_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"tag": clean, "following": True}


@router.delete("/forum-topics/{tag}/follow", tags=["forums"])
def unfollow_topic(tag: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    db.execute(delete(models.TopicFollow).where(models.TopicFollow.tag == clean_tags([tag]), models.TopicFollow.user_id == principal.user_id))
    db.commit()
    return {"tag": tag, "following": False}


class ThreadFollowIn(BaseModel):
    following: bool | None = None
    saved: bool | None = None


@router.post("/threads/{thread_id}/follow", tags=["forums"])
def follow_thread(thread_id: str, payload: ThreadFollowIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    thread = _thread_for(db, thread_id, principal)
    if not _readable_thread(db, thread, principal):
        raise HTTPException(status_code=404, detail="Thread not found")
    row = _follow_row(db, thread.id, principal.user_id)
    if payload.following is not None:
        row.following = payload.following
    if payload.saved is not None:
        row.saved = payload.saved
    db.commit()
    return {"following": row.following, "saved": row.saved}


@router.get("/forum-saved", tags=["forums"])
def saved_threads(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """My saved threads - only those I can still open."""
    ids = db.scalars(select(models.ThreadFollow.thread_id).where(models.ThreadFollow.user_id == principal.user_id, models.ThreadFollow.saved.is_(True))).all()
    rows = [t for t in db.scalars(select(models.Thread).where(models.Thread.id.in_(ids))).all() if _readable_thread(db, t, principal)] if ids else []
    return {"items": _brief_threads(db, rows)}


class DraftIn(BaseModel):
    id: str | None = Field(default=None, max_length=40)
    forum_id: str = Field(max_length=40)
    title: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=40_000)
    kind: str = Field(default="question", pattern="^(question|discussion|guide|announcement)$")
    tags: list[str] = Field(default_factory=list, max_length=8)
    lang: str | None = Field(default=None, max_length=5)
    country: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=120)


def _draft_out(d: models.ThreadDraft) -> dict:
    return {"id": d.id, "forum_id": d.forum_id, "title": d.title, "body": d.body, "kind": d.kind, "tags": _csv(d.tags), "lang": d.lang, "country": d.country, "region": d.region, "updated_at": d.updated_at}


@router.get("/forum-drafts", tags=["forums"])
def my_drafts(principal: CurrentUser, forum_id: str | None = None, db: OrmSession = Depends(get_db)):
    stmt = select(models.ThreadDraft).where(models.ThreadDraft.user_id == principal.user_id)
    if forum_id:
        stmt = stmt.where(models.ThreadDraft.forum_id == forum_id)
    return {"items": [_draft_out(d) for d in db.scalars(stmt.order_by(models.ThreadDraft.updated_at.desc()).limit(50)).all()]}


@router.post("/forum-drafts", tags=["forums"])
def save_draft(payload: DraftIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Keep a thread being written. Only its author ever sees a draft."""
    forum = _get_forum_or_404(db, payload.forum_id)
    _forum_access(db, forum, principal)
    draft = db.get(models.ThreadDraft, payload.id) if payload.id else None
    if draft is not None and draft.user_id != principal.user_id:
        draft = None
    if draft is None:
        if db.scalar(select(func.count()).select_from(models.ThreadDraft).where(models.ThreadDraft.user_id == principal.user_id)) >= 50:
            raise HTTPException(status_code=409, detail="You have 50 drafts. Publish or delete some first.")
        draft = models.ThreadDraft(user_id=principal.user_id, forum_id=forum.id)
        db.add(draft)
    draft.forum_id, draft.title, draft.body, draft.kind = forum.id, payload.title, payload.body, payload.kind
    draft.tags, draft.lang, draft.country, draft.region = clean_tags(payload.tags), payload.lang, payload.country, payload.region
    draft.updated_at = _now()
    db.commit()
    return _draft_out(draft)


@router.delete("/forum-drafts/{draft_id}", tags=["forums"])
def delete_draft(draft_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    db.execute(delete(models.ThreadDraft).where(models.ThreadDraft.id == draft_id, models.ThreadDraft.user_id == principal.user_id))
    db.commit()
    return {"deleted": True}


# --- editing and reopening --------------------------------------------------------------

class ThreadEditIn(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    body: str | None = Field(default=None, min_length=1, max_length=40_000)
    tags: list[str] | None = Field(default=None, max_length=8)
    lang: str | None = Field(default=None, max_length=5)
    country: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=120)
    commercial: bool | None = None

    model_config = {"extra": "forbid"}


def _reclassify(db: OrmSession, content_id: str, kind: str, text: str, user_id: str) -> None:
    verdict = agecommunity.classify_and_store(db, content_id, kind, text, user_id)
    if verdict.block_publication:
        db.rollback()
        raise HTTPException(status_code=403, detail="This cannot be published. If you believe this is a mistake, contact support.")


@router.patch("/threads/{thread_id}", tags=["forums"])
def edit_thread(thread_id: str, payload: ThreadEditIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The author adds details or corrects their thread. Marked as edited."""
    thread = _thread_for(db, thread_id, principal)
    if thread.author_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the author can edit a thread")
    if thread.locked:
        raise HTTPException(status_code=403, detail="This thread is locked")
    sent = payload.model_dump(exclude_unset=True)
    for key, value in sent.items():
        if key == "tags":
            value = clean_tags(value)
        setattr(thread, key, value)
    thread.edited_at = _now()
    if "title" in sent or "body" in sent:
        _reclassify(db, thread.id, "thread", f"{thread.title} {thread.body}", principal.user_id)
    db.commit()
    _live(f"thread:{thread.id}", "thread_edited", thread_id=thread.id)
    return {"id": thread.id, "edited_at": thread.edited_at}


class ReplyEditIn(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    commercial: bool | None = None


@router.patch("/replies/{reply_id}", tags=["forums"])
def edit_reply(reply_id: str, payload: ReplyEditIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    reply = db.get(models.Reply, reply_id)
    if reply is None or reply.status != "published":
        raise HTTPException(status_code=404, detail="Reply not found")
    thread = _thread_for(db, reply.thread_id, principal)
    if reply.author_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the author can edit a reply")
    if thread.locked:
        raise HTTPException(status_code=403, detail="This thread is locked")
    reply.body = payload.body
    if payload.commercial is not None:
        reply.commercial = payload.commercial
    reply.edited_at = _now()
    _reclassify(db, reply.id, "reply", reply.body, principal.user_id)
    db.commit()
    _live(f"thread:{thread.id}", "reply_edited", thread_id=thread.id, reply_id=reply.id)
    return {"id": reply.id, "edited_at": reply.edited_at}


class ReopenIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1_000)


@router.post("/threads/{thread_id}/reopen", tags=["forums"])
def reopen_question(thread_id: str, payload: ReopenIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The asker says the accepted answer no longer settles it. The answer
    stays in the discussion; it is no longer marked as the solution."""
    thread = _thread_for(db, thread_id, principal)
    if thread.author_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Only the author can reopen a question")
    db.scalar(select(models.Thread.id).where(models.Thread.id == thread.id).with_for_update())
    changed = db.execute(
        update(models.Reply).where(models.Reply.thread_id == thread.id, models.Reply.accepted_answer.is_(True)).values(accepted_answer=False)
    ).rowcount
    if not changed:
        raise HTTPException(status_code=409, detail="This question is not marked as solved")
    thread.reopened_at = _now()
    thread.last_activity_at = _now()
    if payload.reason:
        db.add(models.Reply(id=new_id("rpl"), thread_id=thread.id, author_id=principal.user_id, body=f"Reopened: {payload.reason.strip()}", lang=thread.lang))
        thread.replies_count += 1
    db.commit()
    _live(f"thread:{thread.id}", "thread_reopened", thread_id=thread.id)
    return {"reopened": True}


# --- moderation ----------------------------------------------------------------------------

class ModerateIn(BaseModel):
    action: str = Field(pattern="^(pin|unpin|lock|unlock|hide|restore|mark_commercial|unmark_commercial)$")
    reason: str | None = Field(default=None, max_length=2_000)


def _thread_any(db: OrmSession, thread_id: str, principal) -> tuple[models.Thread, models.Forum]:
    """A thread whatever its status, for its forum's moderators."""
    thread = db.get(models.Thread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    forum = _get_forum_or_404(db, thread.forum_id)
    try:
        _forum_access(db, forum, principal)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Thread not found")
    if not _moderates_forum(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the forum's moderators can do that")
    return thread, forum


def _tell_author(user_id: str, what: str, reason: str | None, link: str) -> None:
    notify.notify(
        user_id,
        kind="forum_moderation",
        title=what,
        body=(f"Reason given: {reason}. " if reason else "") + "If you think this was a mistake, you can ask for it to be reviewed from the link.",
        link=link,
    )


@router.post("/threads/{thread_id}/moderate", tags=["moderation"])
def moderate_thread(thread_id: str, payload: ModerateIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    thread, forum = _thread_any(db, thread_id, principal)
    if payload.action in ("hide",) and not (payload.reason or "").strip():
        raise HTTPException(status_code=422, detail="Say why: the author is told")
    if payload.action == "pin":
        thread.pinned = True
    elif payload.action == "unpin":
        thread.pinned = False
    elif payload.action == "lock":
        thread.locked = True
    elif payload.action == "unlock":
        thread.locked = False
    elif payload.action == "mark_commercial":
        thread.commercial = True
    elif payload.action == "unmark_commercial":
        thread.commercial = False
    elif payload.action == "hide":
        if thread.status == "open":
            forum.threads_count = max(0, forum.threads_count - 1)
        thread.status, thread.moderation_reason = "hidden", payload.reason.strip()
    elif payload.action == "restore":
        if thread.status == "hidden":
            forum.threads_count += 1
            thread.status, thread.moderation_reason = "open", None
    _mod_log(db, forum.id, principal.user_id, f"thread_{payload.action}", "thread", thread.id, payload.reason)
    db.commit()
    if payload.action == "hide":
        _tell_author(thread.author_id, "A moderator hid your forum thread", payload.reason, f"/forums?review=thread:{thread.id}")
    elif payload.action == "lock":
        _tell_author(thread.author_id, "A moderator locked your forum thread", payload.reason, f"/forums?thread={thread.id}")
    _live(f"thread:{thread.id}", "thread_moderated", thread_id=thread.id, action=payload.action)
    return {"id": thread.id, "pinned": thread.pinned, "locked": thread.locked, "status": thread.status, "commercial": thread.commercial}


class ReplyModerateIn(BaseModel):
    action: str = Field(pattern="^(hide|restore|mark_commercial|unmark_commercial)$")
    reason: str | None = Field(default=None, max_length=2_000)


@router.post("/replies/{reply_id}/moderate", tags=["moderation"])
def moderate_reply(reply_id: str, payload: ReplyModerateIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    reply = db.get(models.Reply, reply_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    thread, forum = _thread_any(db, reply.thread_id, principal)
    if payload.action == "hide":
        if not (payload.reason or "").strip():
            raise HTTPException(status_code=422, detail="Say why: the author is told")
        if reply.status == "published":
            thread.replies_count = max(0, thread.replies_count - 1)
        reply.status, reply.moderation_reason = "hidden", payload.reason.strip()
    elif payload.action == "restore":
        if reply.status == "hidden":
            thread.replies_count += 1
            reply.status, reply.moderation_reason = "published", None
    else:
        reply.commercial = payload.action == "mark_commercial"
    _mod_log(db, forum.id, principal.user_id, f"reply_{payload.action}", "reply", reply.id, payload.reason)
    db.commit()
    if payload.action == "hide":
        _tell_author(reply.author_id, "A moderator hid your forum reply", payload.reason, f"/forums?review=reply:{reply.id}")
    _live(f"thread:{thread.id}", "reply_moderated", thread_id=thread.id, reply_id=reply.id)
    return {"id": reply.id, "status": reply.status, "commercial": reply.commercial}


def _audience(db: OrmSession, forum: models.Forum) -> tuple:
    """Who can read a forum, as a comparable key: the doors on its chain."""
    from main import _forum_chain

    chain = _forum_chain(db, forum) or [forum]
    return tuple(sorted({(f.community_id, f.visibility or "inherit") for f in chain}))


class MoveIn(BaseModel):
    forum_id: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=2_000)
    # Moving to a forum read by different people is a decision, made out loud.
    acknowledge_audience_change: bool = False


@router.post("/threads/{thread_id}/move", tags=["moderation"])
def move_thread(thread_id: str, payload: MoveIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Move a thread to a better forum. The mover must moderate both, and a move
    to a forum with different readers is refused unless acknowledged - then
    recorded and the author told."""
    thread, source = _thread_any(db, thread_id, principal)
    target = _get_forum_or_404(db, payload.forum_id)
    _forum_access(db, target, principal)
    if not _moderates_forum(db, target, principal.user_id):
        raise HTTPException(status_code=403, detail="You must moderate the forum you move it to")
    if target.id == source.id:
        raise HTTPException(status_code=409, detail="It is already there")
    if _audience(db, source) != _audience(db, target) and not payload.acknowledge_audience_change:
        raise HTTPException(
            status_code=409,
            detail="The other forum is read by different people. Confirm that the thread and its replies may be read there.",
        )
    thread.forum_id, thread.moved_from = target.id, source.id
    if thread.status == "open":
        source.threads_count = max(0, source.threads_count - 1)
        target.threads_count += 1
    for forum in (source, target):
        _mod_log(db, forum.id, principal.user_id, "thread_moved", "thread", thread.id, f"{source.id} -> {target.id}: {payload.reason}")
    db.commit()
    _tell_author(thread.author_id, f"Your thread was moved to the forum {target.name}", payload.reason, f"/forums?thread={thread.id}")
    return {"id": thread.id, "forum_id": target.id}


class MergeIn(BaseModel):
    into_thread_id: str = Field(max_length=40)
    reason: str = Field(min_length=1, max_length=2_000)
    acknowledge_audience_change: bool = False


@router.post("/threads/{thread_id}/merge", tags=["moderation"])
def merge_thread(thread_id: str, payload: MergeIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Fold a duplicate question into another. Its replies move with their
    authors and votes; its own question becomes a reply in the other thread,
    under its author's name; the original leads to the merged thread."""
    source, source_forum = _thread_any(db, thread_id, principal)
    target, target_forum = _thread_any(db, payload.into_thread_id, principal)
    if source.id == target.id or source.status != "open" or target.status != "open":
        raise HTTPException(status_code=409, detail="Merge two different open threads")
    if _audience(db, source_forum) != _audience(db, target_forum) and not payload.acknowledge_audience_change:
        raise HTTPException(status_code=409, detail="The other thread is read by different people. Confirm the merge.")
    has_answer = db.scalar(select(models.Reply.id).where(models.Reply.thread_id == target.id, models.Reply.accepted_answer.is_(True)))
    moved = db.scalars(select(models.Reply).where(models.Reply.thread_id == source.id)).all()
    for r in moved:
        r.thread_id = target.id
        if has_answer:
            r.accepted_answer = False
    db.add(models.Reply(
        id=new_id("rpl"), thread_id=target.id, author_id=source.author_id, lang=source.lang,
        body=f"[Merged question] {source.title}\n\n{source.body}", created_at=source.created_at,
    ))
    published = sum(1 for r in moved if r.status == "published")
    target.replies_count += published + 1
    target.last_activity_at = max(target.last_activity_at, source.last_activity_at)
    for f in db.scalars(select(models.ThreadFollow).where(models.ThreadFollow.thread_id == source.id)).all():
        if db.scalar(select(models.ThreadFollow.id).where(models.ThreadFollow.thread_id == target.id, models.ThreadFollow.user_id == f.user_id)):
            db.delete(f)
        else:
            f.thread_id = target.id
    source.status, source.merged_into, source.duplicate_of = "merged", target.id, target.id
    source_forum.threads_count = max(0, source_forum.threads_count - 1)
    for forum in {source_forum.id: source_forum, target_forum.id: target_forum}.values():
        _mod_log(db, forum.id, principal.user_id, "thread_merged", "thread", source.id, f"into {target.id}: {payload.reason}")
    db.commit()
    _tell_author(source.author_id, "Your question was merged into a similar one", payload.reason, f"/forums?thread={target.id}")
    return {"merged_into": target.id, "replies_moved": len(moved)}


REPORT_REASONS = "^(spam|harassment|hate|violence|sexual|child_safety|misinformation|private_info|other)$"


class ForumReportIn(BaseModel):
    target_kind: str = Field(pattern="^(thread|reply|knowledge)$")
    target_id: str = Field(max_length=40)
    reason: str = Field(pattern=REPORT_REASONS)
    note: str | None = Field(default=None, max_length=2_000)


@router.post("/forum-reports", status_code=201, tags=["moderation"])
def report_forum_content(payload: ForumReportIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Report a thread, a reply or a knowledge article to the forum's moderators.
    Only what the member can read; twice is once."""
    if payload.target_kind == "thread":
        forum_id = _thread_for(db, payload.target_id, principal).forum_id
    elif payload.target_kind == "reply":
        reply = db.get(models.Reply, payload.target_id)
        if reply is None or reply.status != "published":
            raise HTTPException(status_code=404, detail="Reply not found")
        forum_id = _thread_for(db, reply.thread_id, principal).forum_id
    else:
        entry = db.get(models.KnowledgeEntry, payload.target_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Article not found")
        _forum_access(db, _get_forum_or_404(db, entry.forum_id), principal)
        forum_id = entry.forum_id
    if not db.scalar(select(models.ForumReport.id).where(models.ForumReport.reporter_id == principal.user_id, models.ForumReport.target_kind == payload.target_kind, models.ForumReport.target_id == payload.target_id)):
        db.add(models.ForumReport(forum_id=forum_id, reporter_id=principal.user_id, target_kind=payload.target_kind, target_id=payload.target_id, reason=payload.reason, note=(payload.note or "").strip() or None))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"received": True}


class ReviewIn(BaseModel):
    target_kind: str = Field(pattern="^(thread|reply)$")
    target_id: str = Field(max_length=40)
    note: str = Field(min_length=1, max_length=2_000)


@router.post("/forum-reviews", status_code=201, tags=["moderation"])
def request_review(payload: ReviewIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The author of hidden content asks the moderators to look again."""
    row = db.get(models.Thread if payload.target_kind == "thread" else models.Reply, payload.target_id)
    if row is None or row.author_id != principal.user_id or row.status != "hidden":
        raise HTTPException(status_code=404, detail="Nothing of yours is hidden here")
    forum_id = row.forum_id if payload.target_kind == "thread" else db.get(models.Thread, row.thread_id).forum_id
    existing = db.scalar(select(models.ForumReport).where(models.ForumReport.reporter_id == principal.user_id, models.ForumReport.target_kind == "review", models.ForumReport.target_id == payload.target_id))
    if existing is None:
        db.add(models.ForumReport(forum_id=forum_id, reporter_id=principal.user_id, target_kind="review", target_id=payload.target_id, reason="review", note=payload.note.strip()))
    elif existing.status == "closed":
        existing.status, existing.note, existing.resolution = "open", payload.note.strip(), None
    db.commit()
    return {"received": True, "reason_given": row.moderation_reason}


def _repeated_posts(db: OrmSession, forum_id: str) -> list[dict]:
    """The same text posted again and again by one account this week, or posts
    stuffed with links: signals for a person to look at, not verdicts."""
    since = _now() - timedelta(days=7)
    items = db.execute(
        select(models.Reply.id, models.Reply.author_id, models.Reply.body, models.Reply.thread_id)
        .join(models.Thread, models.Thread.id == models.Reply.thread_id)
        .where(models.Thread.forum_id == forum_id, models.Reply.created_at >= since, models.Reply.status == "published")
    ).all()
    items += db.execute(
        select(models.Thread.id, models.Thread.author_id, models.Thread.body, models.Thread.id)
        .where(models.Thread.forum_id == forum_id, models.Thread.created_at >= since, models.Thread.status == "open")
    ).all()
    seen: dict[tuple[str, str], list[str]] = {}
    flagged = []
    for item_id, author, body, thread_id in items:
        key = (author, re.sub(r"\s+", " ", (body or "").strip().lower())[:300])
        seen.setdefault(key, []).append(item_id)
        if len(re.findall(r"https?://", body or "")) >= 4:
            flagged.append({"id": item_id, "thread_id": thread_id, "author_id": author, "signal": "many links", "excerpt": (body or "")[:200]})
    for (author, text), ids in seen.items():
        if len(ids) >= 3 and text:
            flagged.append({"id": ids[0], "thread_id": None, "author_id": author, "signal": f"same text posted {len(ids)} times", "excerpt": text[:200]})
    return flagged[:50]


@router.get("/forums/{forum_id}/moderation", tags=["moderation"])
def moderation_queue(forum_id: str, principal: CurrentUser, status: str = "open", db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not _moderates_forum(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the forum's moderators can do that")
    reports = db.scalars(select(models.ForumReport).where(models.ForumReport.forum_id == forum.id, models.ForumReport.status == status).order_by(models.ForumReport.created_at.desc()).limit(200)).all()

    def target_text(r: models.ForumReport) -> dict:
        if r.target_kind == "thread" or (r.target_kind == "review" and db.get(models.Thread, r.target_id)):
            t = db.get(models.Thread, r.target_id)
            return {"kind": "thread", "title": t.title if t else None, "excerpt": (t.body or "")[:300] if t else None, "status": t.status if t else "gone", "thread_id": t.id if t else None, "author_id": t.author_id if t else None, "moderation_reason": t.moderation_reason if t else None}
        if r.target_kind in ("reply", "review"):
            x = db.get(models.Reply, r.target_id)
            return {"kind": "reply", "excerpt": (x.body or "")[:300] if x else None, "status": x.status if x else "gone", "thread_id": x.thread_id if x else None, "author_id": x.author_id if x else None, "moderation_reason": x.moderation_reason if x else None}
        e = db.get(models.KnowledgeEntry, r.target_id)
        return {"kind": "knowledge", "title": e.question if e else None, "excerpt": (e.answer or "")[:300] if e else None, "status": e.status if e else "gone"}

    names = _profiles({r.reporter_id for r in reports})
    return {
        "reports": [
            {
                "id": r.id,
                "target_kind": r.target_kind,
                "target_id": r.target_id,
                "reason": r.reason,
                "note": r.note,
                "reporter": (names.get(r.reporter_id) or {}).get("display_name"),
                "status": r.status,
                "resolution": r.resolution,
                "created_at": r.created_at,
                "target": target_text(r),
            }
            for r in reports
        ],
        "suspected_spam": _repeated_posts(db, forum.id),
    }


class ResolveIn(BaseModel):
    resolution: str = Field(min_length=1, max_length=2_000)


@router.post("/forum-reports/{report_id}/resolve", tags=["moderation"])
def resolve_report(report_id: int, payload: ResolveIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    report = db.get(models.ForumReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    forum = _get_forum_or_404(db, report.forum_id)
    _forum_access(db, forum, principal)
    if not _moderates_forum(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the forum's moderators can do that")
    report.status, report.resolution, report.resolved_by, report.resolved_at = "closed", payload.resolution.strip(), principal.user_id, _now()
    _mod_log(db, forum.id, principal.user_id, "report_resolved", report.target_kind, report.target_id, payload.resolution)
    db.commit()
    if report.target_kind == "review":
        notify.notify(report.reporter_id, kind="forum_moderation", title="Your review request was answered", body=payload.resolution.strip()[:300], link=f"/forums?forum={forum.id}")
    return {"closed": True}


@router.get("/forums/{forum_id}/audit", tags=["moderation"])
def forum_audit(forum_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not (_moderates_forum(db, forum, principal.user_id) or _can_configure(db, forum, principal.user_id)):
        raise HTTPException(status_code=403, detail="Only the forum's moderators can do that")
    rows = db.scalars(select(models.ForumModAction).where(models.ForumModAction.forum_id == forum.id).order_by(models.ForumModAction.created_at.desc()).limit(300)).all()
    names = _profiles({r.actor_id for r in rows})
    return {"items": [{"id": r.id, "actor": (names.get(r.actor_id) or {}).get("display_name"), "action": r.action, "target_kind": r.target_kind, "target_id": r.target_id, "reason": r.reason, "created_at": r.created_at} for r in rows]}


# --- knowledge articles -------------------------------------------------------------------

class SourceLink(BaseModel):
    url: str = Field(max_length=500)
    label: str | None = Field(default=None, max_length=200)


class ArticleIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    answer: str = Field(min_length=10, max_length=40_000)
    source_thread_ids: list[str] = Field(default_factory=list, max_length=10)
    source_reply_ids: list[str] = Field(default_factory=list, max_length=20)
    source_links: list[SourceLink] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=8)
    publish: bool = True


def _links(links: list[SourceLink]) -> str:
    out = []
    for link in links:
        if not re.match(r"^https?://", link.url, re.IGNORECASE):
            raise HTTPException(status_code=422, detail="A source link must start with http:// or https://")
        out.append({"url": link.url, "label": (link.label or "").strip() or None})
    return json.dumps(out)


def _sources_ok(db: OrmSession, forum: models.Forum, thread_ids: list[str], reply_ids: list[str], principal) -> set[str]:
    """Threads and replies an article cites: in this forum, readable by the
    editor. Returns the people who wrote them."""
    authors: set[str] = set()
    for tid in thread_ids:
        thread = db.get(models.Thread, tid)
        if thread is None or thread.forum_id != forum.id or not _readable_thread(db, thread, principal):
            raise HTTPException(status_code=422, detail="A source thread must be an open thread of this forum")
        authors.add(thread.author_id)
    for rid in reply_ids:
        reply = db.get(models.Reply, rid)
        if reply is None or reply.status != "published" or reply.thread_id not in thread_ids:
            raise HTTPException(status_code=422, detail="A source reply must belong to one of the source threads")
        authors.add(reply.author_id)
    return authors


def _editor_forum(db: OrmSession, forum_id: str, principal) -> models.Forum:
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not _moderates_forum(db, forum, principal.user_id, knowledge=True):
        raise HTTPException(status_code=403, detail="Only the forum's knowledge editors can do that")
    return forum


def _entry_for(db: OrmSession, entry_id: str, principal) -> tuple[models.KnowledgeEntry, models.Forum]:
    entry = db.get(models.KnowledgeEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Article not found")
    forum = _get_forum_or_404(db, entry.forum_id)
    try:
        _forum_access(db, forum, principal)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Article not found")
    # An article is as readable as everything it cites.
    for tid in _csv(entry.source_thread_ids):
        thread = db.get(models.Thread, tid)
        if thread is None or not _readable_thread(db, thread, principal):
            raise HTTPException(status_code=404, detail="Article not found")
    return entry, forum


def _revision(db: OrmSession, entry: models.KnowledgeEntry, editor: str, note: str | None) -> None:
    db.add(models.KnowledgeRevision(entry_id=entry.id, editor_id=editor, question=entry.question, answer=entry.answer, source_links=entry.source_links, tags=entry.tags, note=note))


@router.post("/forums/{forum_id}/knowledge", status_code=201, tags=["knowledge"])
def create_article(forum_id: str, payload: ArticleIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Write a knowledge article from the forum's discussions. Its sources are
    cited, its contributors credited, and it is readable only by whoever can
    read every thread it cites."""
    forum = _editor_forum(db, forum_id, principal)
    authors = _sources_ok(db, forum, payload.source_thread_ids, payload.source_reply_ids, principal)
    now = _now()
    entry = models.KnowledgeEntry(
        id=new_id("kbe"), forum_id=forum.id, question=payload.question.strip(), answer=payload.answer.strip(),
        lang=forum.lang or "en", source_thread_ids=",".join(payload.source_thread_ids), source_reply_ids=",".join(payload.source_reply_ids),
        source_links=_links(payload.source_links), contributors=",".join(sorted(authors | {principal.user_id})), tags=clean_tags(payload.tags),
        status="published" if payload.publish else "draft", created_by=principal.user_id, updated_by=principal.user_id, updated_at=now,
        published_at=now if payload.publish else None, reviewed_at=now if payload.publish else None, reviewed_by=principal.user_id if payload.publish else None,
    )
    db.add(entry)
    db.flush()
    _revision(db, entry, principal.user_id, "Created")
    _mod_log(db, forum.id, principal.user_id, "knowledge_created", "knowledge", entry.id)
    db.commit()
    return {"id": entry.id, "status": entry.status}


class ArticleEditIn(BaseModel):
    question: str | None = Field(default=None, min_length=3, max_length=500)
    answer: str | None = Field(default=None, min_length=10, max_length=40_000)
    source_links: list[SourceLink] | None = Field(default=None, max_length=20)
    tags: list[str] | None = Field(default=None, max_length=8)
    note: str | None = Field(default=None, max_length=500)
    # "I checked it is still right" - sets the review date.
    mark_reviewed: bool = False


@router.patch("/knowledge/{entry_id}", tags=["knowledge"])
def edit_article(entry_id: str, payload: ArticleEditIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    entry, forum = _entry_for(db, entry_id, principal)
    if not _moderates_forum(db, forum, principal.user_id, knowledge=True):
        raise HTTPException(status_code=403, detail="Only the forum's knowledge editors can do that")
    sent = payload.model_dump(exclude_unset=True)
    if "question" in sent:
        entry.question = sent["question"].strip()
    if "answer" in sent:
        entry.answer = sent["answer"].strip()
    if payload.source_links is not None:
        entry.source_links = _links(payload.source_links)
    if payload.tags is not None:
        entry.tags = clean_tags(payload.tags)
    now = _now()
    entry.updated_at, entry.updated_by = now, principal.user_id
    if principal.user_id not in _csv(entry.contributors):
        entry.contributors = ",".join(_csv(entry.contributors) + [principal.user_id])
    if payload.mark_reviewed:
        entry.reviewed_at, entry.reviewed_by = now, principal.user_id
    _revision(db, entry, principal.user_id, payload.note or ("Reviewed" if payload.mark_reviewed and len(sent) <= 2 else "Edited"))
    _mod_log(db, forum.id, principal.user_id, "knowledge_edited", "knowledge", entry.id, payload.note)
    db.commit()
    return {"id": entry.id, "updated_at": entry.updated_at, "reviewed_at": entry.reviewed_at}


@router.post("/knowledge/{entry_id}/publish", tags=["knowledge"])
def publish_article(entry_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Publish a draft. For an AI draft this is the human review: whoever
    publishes it vouches that it reflects its sources - not that it is true."""
    entry, forum = _entry_for(db, entry_id, principal)
    if not _moderates_forum(db, forum, principal.user_id, knowledge=True):
        raise HTTPException(status_code=403, detail="Only the forum's knowledge editors can do that")
    now = _now()
    entry.status, entry.published_at, entry.reviewed_at, entry.reviewed_by = "published", entry.published_at or now, now, principal.user_id
    _revision(db, entry, principal.user_id, "Published after review")
    _mod_log(db, forum.id, principal.user_id, "knowledge_published", "knowledge", entry.id)
    db.commit()
    return {"id": entry.id, "status": entry.status}


@router.get("/knowledge/{entry_id}/revisions", tags=["knowledge"])
def article_revisions(entry_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    entry, forum = _entry_for(db, entry_id, principal)
    if not _moderates_forum(db, forum, principal.user_id, knowledge=True):
        raise HTTPException(status_code=403, detail="Only the forum's knowledge editors can read the revisions")
    rows = db.scalars(select(models.KnowledgeRevision).where(models.KnowledgeRevision.entry_id == entry.id).order_by(models.KnowledgeRevision.created_at.desc())).all()
    names = _profiles({r.editor_id for r in rows})
    return {"items": [{"id": r.id, "editor": (names.get(r.editor_id) or {}).get("display_name"), "question": r.question, "answer": r.answer, "source_links": json.loads(r.source_links or "[]"), "tags": _csv(r.tags), "note": r.note, "created_at": r.created_at} for r in rows]}


class SuggestionIn(BaseModel):
    kind: str = Field(pattern="^(correction|outdated)$")
    note: str = Field(min_length=5, max_length=4_000)


@router.post("/knowledge/{entry_id}/suggestions", status_code=201, tags=["knowledge"])
def suggest_correction(entry_id: str, payload: SuggestionIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    entry, forum = _entry_for(db, entry_id, principal)
    if entry.status != "published":
        raise HTTPException(status_code=404, detail="Article not found")
    db.add(models.KnowledgeSuggestion(entry_id=entry.id, user_id=principal.user_id, kind=payload.kind, note=payload.note.strip()))
    db.commit()
    return {"received": True}


@router.get("/knowledge/{entry_id}/suggestions", tags=["knowledge"])
def article_suggestions(entry_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    entry, forum = _entry_for(db, entry_id, principal)
    if not _moderates_forum(db, forum, principal.user_id, knowledge=True):
        raise HTTPException(status_code=403, detail="Only the forum's knowledge editors can read suggestions")
    rows = db.scalars(select(models.KnowledgeSuggestion).where(models.KnowledgeSuggestion.entry_id == entry.id).order_by(models.KnowledgeSuggestion.created_at.desc())).all()
    names = _profiles({r.user_id for r in rows})
    return {"items": [{"id": r.id, "kind": r.kind, "note": r.note, "status": r.status, "by": (names.get(r.user_id) or {}).get("display_name"), "created_at": r.created_at} for r in rows]}


@router.post("/knowledge-suggestions/{suggestion_id}/resolve", tags=["knowledge"])
def resolve_suggestion(suggestion_id: int, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    row = db.get(models.KnowledgeSuggestion, suggestion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    entry, forum = _entry_for(db, row.entry_id, principal)
    if not _moderates_forum(db, forum, principal.user_id, knowledge=True):
        raise HTTPException(status_code=403, detail="Only the forum's knowledge editors can do that")
    row.status, row.resolved_by = "closed", principal.user_id
    db.commit()
    notify.notify(row.user_id, kind="forum_knowledge", title="Your suggestion on a knowledge article was handled", link=f"/forums?forum={forum.id}&view=knowledge")
    return {"closed": True}


@router.post("/threads/{thread_id}/knowledge-draft", status_code=201, tags=["knowledge"])
def ai_draft_article(thread_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """An AI draft of an article from one discussion, for an editor to check.

    It stays a draft - labelled AI-generated, citing the thread and the replies
    it read - until an editor publishes it. Refused when no real model is
    configured: a placeholder is not a draft. The text is sent to the AI
    provider ai-service is configured with, so this is the editor's choice.
    """
    thread = _thread_for(db, thread_id, principal)
    forum = _editor_forum(db, thread.forum_id, principal)
    replies = db.scalars(
        select(models.Reply).where(models.Reply.thread_id == thread.id, models.Reply.status == "published", models.Reply.parent_id.is_(None))
        .order_by(models.Reply.accepted_answer.desc(), models.Reply.upvotes.desc(), models.Reply.created_at).limit(8)
    ).all()
    age = agecommunity.viewer(principal.user_id)
    replies = _visible_replies(db, age, replies)
    if not replies:
        raise HTTPException(status_code=409, detail="This discussion has no answer to draw from yet")
    excerpt = "\n\n".join([f"QUESTION: {thread.title}\n{thread.body[:1500]}"] + [f"ANSWER {i + 1}{' (accepted by the asker)' if r.accepted_answer else ''}: {r.body[:1500]}" for i, r in enumerate(replies)])
    try:
        response = httpx.post(
            f"{AI_URL}/ai/run",
            json={
                "task": "summarize",
                "prompt": (
                    "Write a short knowledge-base answer to the question below, using only what the answers say. "
                    "Do not add facts, sources or citations that are not in the answers. If the answers disagree or "
                    "are uncertain, say so." + chr(10) * 2 + excerpt
                ),
                "lang": thread.lang or "en",
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        log.warning("knowledge draft failed for %s: %s", thread_id, exc)
        raise HTTPException(status_code=503, detail="No AI model is available to draft this. Write the article yourself.")
    if result.get("mock") or not (result.get("output") or result.get("text")):
        raise HTTPException(status_code=503, detail="No AI model is configured here, so no draft was made. Write the article yourself.")
    now = _now()
    entry = models.KnowledgeEntry(
        id=new_id("kbe"), forum_id=forum.id, question=thread.title, answer=(result.get("output") or result.get("text")).strip(),
        lang=thread.lang or "en", source_thread_ids=thread.id, source_reply_ids=",".join(r.id for r in replies),
        source_links="[]", contributors=",".join(sorted({thread.author_id} | {r.author_id for r in replies})), tags=thread.tags or "",
        status="draft", ai_generated=True, created_by=principal.user_id, updated_by=principal.user_id, updated_at=now,
    )
    db.add(entry)
    db.flush()
    _revision(db, entry, principal.user_id, f"AI draft ({result.get('provider') or 'model'})")
    db.commit()
    return {"id": entry.id, "status": "draft", "ai_generated": True}


# --- expert question sessions ----------------------------------------------------------------

class SessionIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4_000)
    expert_id: str = Field(max_length=40)
    expert_intro: str = Field(default="", max_length=1_000)
    starts_at: datetime
    ends_at: datetime


@router.post("/forums/{forum_id}/sessions", status_code=201, tags=["forums"])
def create_session(forum_id: str, payload: SessionIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    forum = _get_forum_or_404(db, forum_id)
    _forum_access(db, forum, principal)
    if not _moderates_forum(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the forum's moderators organise sessions")
    starts = payload.starts_at if payload.starts_at.tzinfo else payload.starts_at.replace(tzinfo=timezone.utc)
    ends = payload.ends_at if payload.ends_at.tzinfo else payload.ends_at.replace(tzinfo=timezone.utc)
    if ends <= starts or ends <= _now():
        raise HTTPException(status_code=422, detail="A session ends after it starts, in the future")
    if not _can_enter(db, forum, _as(payload.expert_id)):
        raise HTTPException(status_code=422, detail="The expert must be able to read this forum")
    session = models.ExpertSession(forum_id=forum.id, title=payload.title.strip(), description=payload.description.strip(), expert_id=payload.expert_id, expert_intro=payload.expert_intro.strip(), organizer_id=principal.user_id, starts_at=starts, ends_at=ends)
    db.add(session)
    _mod_log(db, forum.id, principal.user_id, "session_created", "session", None, payload.title)
    db.commit()
    notify.notify(payload.expert_id, kind="forum_session", title=f"You are the expert of a question session: {session.title}", link=f"/forums?forum={forum.id}")
    return {"id": session.id}


@router.post("/expert-sessions/{session_id}/cancel", tags=["forums"])
def cancel_session(session_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    session = db.get(models.ExpertSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    forum = _get_forum_or_404(db, session.forum_id)
    _forum_access(db, forum, principal)
    if not _moderates_forum(db, forum, principal.user_id):
        raise HTTPException(status_code=403, detail="Only the forum's moderators can do that")
    session.cancelled_at = _now()
    _mod_log(db, forum.id, principal.user_id, "session_cancelled", "session", session.id)
    db.commit()
    return {"cancelled": True}


# --- contributor profiles ---------------------------------------------------------------------

@router.get("/forum-profiles/{user_id}", tags=["forums"])
def contributor_profile(user_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """A member's forum activity, counted only in forums the viewer can read,
    and only if the member shows it. Background is what they say about
    themselves; "verified" is Kinjy's own account verification, nothing more."""
    prefs = _prefs(db, user_id)
    profile = _profiles({user_id}).get(user_id) or {}
    out = {
        "user_id": user_id,
        "display_name": profile.get("display_name"),
        "handle": profile.get("handle"),
        "account_verified": bool(profile.get("verified")),
        "background": prefs.background,
        "background_note": "Described by the member. Kinjy has not checked it." if prefs.background else None,
        "show_activity": prefs.show_activity is not False,
        "stats": None,
        "recognition_note": "Recognition for taking part. It does not mean the advice is right.",
    }
    if prefs.show_activity is not False or (principal and principal.user_id == user_id):
        out["stats"] = _stats(db, {user_id}, _readable_forum_ids(db, principal)).get(user_id)
    return out


# --- the Community page ------------------------------------------------------------------------

@router.get("/communities/{handle}/forum-activity", tags=["communities"])
def community_forum_activity(handle: str, principal: MaybeUser, limit: int = 8, db: OrmSession = Depends(get_db)):
    """Recent threads from a community's forums, for its page - each one only
    if the viewer could open it."""
    community = _resolve(db, handle)
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    _community_door(db, community.id, principal)
    forums = [f for f in db.scalars(select(models.Forum).where(models.Forum.community_id == community.id)).all() if _can_enter(db, f, principal)]
    if not forums:
        return {"items": []}
    age = agecommunity.viewer(principal.user_id if principal else None)
    stmt = agecommunity.restrict_query(
        select(models.Thread).where(models.Thread.forum_id.in_([f.id for f in forums]), models.Thread.status == "open"), models.Thread, age
    )
    rows = agecommunity.readable_page(db, stmt.order_by(models.Thread.last_activity_at.desc()), age, limit=min(max(limit, 1), 20), offset=0)
    return {"items": _brief_threads(db, _visible_threads(db, age, rows))}


# --- the weekly digest (run by main._due_loop) -----------------------------------------------

DIGEST_EVERY = timedelta(days=7)


def run_digests(db: OrmSession) -> int:
    """Once a week per follower who asked for it: how many new discussions and
    unanswered questions their followed forums have - counting only what they
    can read. Claimed row by row, so two workers never send the same one."""
    now = _now()
    due = db.scalars(
        select(models.ForumFollow)
        .where(models.ForumFollow.digest.is_(True), or_(models.ForumFollow.digest_sent_at.is_(None), models.ForumFollow.digest_sent_at <= now - DIGEST_EVERY))
        .limit(500)
        .with_for_update(skip_locked=True)
    ).all()
    by_user: dict[str, list[models.ForumFollow]] = {}
    for row in due:
        by_user.setdefault(row.user_id, []).append(row)
    sent = 0
    for user_id, rows in by_user.items():
        for row in rows:
            row.digest_sent_at = now
        if not _prefs(db, user_id).digests:
            continue
        who = _as(user_id)
        age = agecommunity.viewer(user_id)
        new = unanswered = 0
        for row in rows:
            forum = db.get(models.Forum, row.forum_id)
            if forum is None or not _can_enter(db, forum, who):
                continue
            stmt = agecommunity.restrict_query(select(models.Thread).where(models.Thread.forum_id == forum.id, models.Thread.status == "open", models.Thread.created_at >= now - DIGEST_EVERY), models.Thread, age)
            threads = _visible_threads(db, age, list(db.scalars(stmt.limit(200)).all()))
            new += len(threads)
            unanswered += sum(1 for t in threads if t.replies_count == 0 and (t.kind or "question") in ("question", "discussion"))
        if new:
            notify.notify(user_id, kind="forum_digest", title=f"This week in the forums you follow: {new} new {'discussion' if new == 1 else 'discussions'}, {unanswered} unanswered", link="/forums?followed=1")
            sent += 1
    db.commit()
    return sent

