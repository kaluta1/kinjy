"""Kinjy · social-service — posts, feed modes, algorithm marketplace, reactions."""
from __future__ import annotations

import hashlib
import html as html_module
import json
import logging
import re
import threading
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session as OrmSession

from common import events, notify, settings
from common.auth import AdminUser, CurrentUser, MaybeUser
from common.database import SessionLocal, get_db
from common.ids import new_id
from common.service import create_app

from common import ageclient, classifier, mediasign, stickers
from common.agesafety import engine as age_engine, rating_strictness

import agefilter
import linkpreview
import moderation
import models
import ranking

log = logging.getLogger("social-service")
USER_URL = "http://user-service:8000"
MEDIA_URL = "http://media-service:8000"
MESSAGING_URL = "http://messaging-service:8000"
COMMUNITY_URL = "http://community-service:8000"


def live(_topic: str, _event: str, **data) -> None:
    """Push a live update to whoever is watching this topic.

    The two positional parameters are underscore-prefixed so they cannot
    collide with a payload key: `live(topic, "activity", kind="comment")` used
    to raise `got multiple values for argument 'kind'` — after the write had
    committed, so the comment was saved and the caller still saw a 500.

    Deliberately fire-and-forget and deliberately short-timeout: liveness is a
    nicety, and a hub that is slow or down must never make posting a comment
    slow or fail. The write has already been committed by the time we get here,
    so a dropped notification costs a refresh, nothing more.
    """
    try:
        httpx.post(
            f"{MESSAGING_URL}/internal/broadcast",
            json={"topic": _topic, "type": _event, "data": data},
            timeout=1.5,
        )
    except Exception as exc:
        log.debug("live update dropped (%s %s): %s", _topic, _event, exc)

# Blueprint §1 — the newsfeed conflict is resolved by having explicit modes
# rather than one contested algorithm.
FEED_MODES = [
    "following", "for_you", "circles", "friends", "local",
    "country", "global", "topics", "trending", "new",
]


def seed_algorithms() -> None:
    """Insert the built-in algorithms once."""
    db = SessionLocal()
    try:
        existing = set(db.scalars(select(models.Algorithm.id)).all())
        for spec in models.BUILTIN_ALGORITHMS:
            if spec["id"] not in existing:
                db.add(models.Algorithm(builtin=True, **spec))
        db.commit()
    except Exception:
        log.exception("could not seed algorithms")
        db.rollback()
    finally:
        db.close()


# content_safety is a new table; create_all makes it, but existing posts have
# no row. restrict_query treats a missing row as UNCLASSIFIED, i.e. hidden from
# minors, so the gap is closed in the safe direction until the backfill runs.
MIGRATIONS: list[str] = [
    # create_all creates tables and never alters one, so a new column on an
    # existing table has to be stated here.
    f"ALTER TABLE {models.SCHEMA}.comments ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.comment_reactions ADD COLUMN IF NOT EXISTS sticker_id VARCHAR(40)",
]

app = create_app(
    name="social-service",
    schema=models.SCHEMA,
    description="Posts, multiple feed modes, the algorithm marketplace, reactions and comments.",
    migrations=MIGRATIONS,
    on_startup=[seed_algorithms],
)


# --- schemas ---------------------------------------------------------------

class PostIn(BaseModel):
    body: str = Field(default="", max_length=20000)
    # "short" is declared, never inferred. A portrait clip posted to the feed is
    # still a feed post; what makes a short is the author choosing that surface.
    format: str = Field(default="text", pattern="^(text|image|video|audio|article|carousel|short)$")
    visibility: str = Field(default="public", pattern="^(public|followers|circle|community)$")
    circle_id: str | None = None
    community_id: str | None = None
    lang: str = "en"
    country: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    topics: list[str] = Field(default_factory=list)
    provenance: str = Field(default="original", pattern="^(original|edited|ai_assisted|ai_generated|verified_source)$")
    series_id: str | None = None
    episode_number: int | None = None
    media: list[dict] = Field(default_factory=list)
    mature: bool = False


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    parent_id: str | None = None
    lang: str = "en"


class SignalIn(BaseModel):
    kind: str = Field(pattern="^(less_like_this|more_like_this|mute_topic|mute_author)$")
    target_type: str = Field(pattern="^(post|author|topic)$")
    target_id: str


HASHTAG_RE = re.compile(r"#([\wÀ-ɏ؀-ۿ一-鿿][\wÀ-ɏ؀-ۿ一-鿿-]{1,49})")

# A handle as somebody types it into a post. Deliberately the same shape the
# client highlights, so what gets notified is what the author saw turn gold.
# How long a post or comment may still be edited after it is written.
EDIT_WINDOW = timedelta(hours=24)


def _aware(value: datetime) -> datetime:
    """A stored timestamp as an aware one.

    Rows written before the columns were timezone-aware come back naive, and
    subtracting a naive datetime from an aware one raises - which would turn
    "edit your post" into a 500 for exactly the oldest posts.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


MENTION_RE = re.compile(r"@([a-z0-9_.]{2,40})", re.IGNORECASE)


def _mentioned_handles(body: str) -> list[str]:
    """The handles named in a body, lower-cased and de-duplicated.

    Capped: a post naming fifty people is an announcement, and past that the
    cost is a notification storm rather than a mention. The cap matches the one
    user-service puts on the lookup.
    """
    seen: list[str] = []
    for handle in MENTION_RE.findall(body or ""):
        # A handle may contain a dot; it does not end with one. "Thanks
        # @patricko." captured the full stop as part of the name, so the
        # lookup missed and a mention at the end of a sentence - which is
        # where most of them are - never got its owner's name or a
        # notification.
        lowered = handle.lower().rstrip(".")
        if len(lowered) < 2:
            continue
        if lowered not in seen:
            seen.append(lowered)
        if len(seen) >= 50:
            break
    return seen


# handle -> display name, for a minute. A thread is full of the same few
# mentions and a feed repeats them down the page; without this, rendering one
# screen asks user-service the same question twenty times. Short enough that a
# renamed member is right again within a minute.
_MENTION_TTL = 60.0
_mention_cache: dict[str, tuple[str, float]] = {}
_mention_lock = threading.Lock()


def _mention_names(bodies: Iterable[str]) -> dict[str, str]:
    """The names behind the @handles in these bodies.

    A mention is stored as the handle, because that is what the author typed
    and what has to survive an edit. But a handle is an address, and a reader
    wants the person: "@k_mbeki3" means nothing next to "Kofi Mbeki". So the
    body keeps the handle and the response carries the names to draw instead.

    Only handles that resolve are returned. "@everyone", an email address and
    a price all look like mentions to a regex, and a name that cannot be found
    is left as the handle rather than invented.
    """
    handles: list[str] = []
    for body in bodies:
        for handle in _mentioned_handles(body):
            if handle not in handles:
                handles.append(handle)
        if len(handles) >= 50:
            break
    if not handles:
        return {}

    now = time.time()
    names: dict[str, str] = {}
    ask: list[str] = []
    with _mention_lock:
        for handle in handles:
            hit = _mention_cache.get(handle)
            if hit and hit[1] > now:
                names[handle] = hit[0]
            else:
                ask.append(handle)
    if not ask:
        return names

    try:
        response = httpx.post(
            f"{USER_SERVICE}/internal/profiles/by-handle", json={"handles": ask}, timeout=5
        )
        response.raise_for_status()
        people = response.json().get("profiles", {})
    except Exception as exc:
        # The handle is still a working link and still readable. Losing the
        # name is a worse post, not a broken one.
        log.warning("could not resolve mention names: %s", exc)
        return names

    with _mention_lock:
        if len(_mention_cache) > 5000:
            _mention_cache.clear()
        for key, profile in people.items():
            handle = (profile.get("handle") or key or "").lower()
            name = profile.get("display_name")
            if handle and name:
                names[handle] = name
                _mention_cache[handle] = (name, now + _MENTION_TTL)
    return names


def _name_of(user_id: str) -> str:
    """What to call somebody in a notification: their name, never their handle.

    A notification is read on a lock screen, out of context. "@k_mbeki3 liked
    your post" asks the reader to decode a handle before they know whether
    they care; "Kofi Mbeki liked your post" is the same sentence with the
    answer already in it. The handle is still how you *reach* a profile - it
    is just not how a person is introduced.

    Falls back to "Someone" rather than to the handle, because a notification
    that names nobody is better than one that names an id.
    """
    profile = _authors({user_id}).get(user_id) or {}
    return profile.get("display_name") or "Someone"


def _notify_mentions(body: str, actor_id: str, post_id: str, where: str) -> None:
    """Tell the people named in a body that they were named.

    Best-effort on purpose, like the other notifications here: the post is
    already committed by the time this runs, and a user-service hiccup must
    cost a notification rather than the post.

    The author is skipped - mentioning yourself is not news - and so is anyone
    the lookup does not recognise, because "@everyone", an email address and a
    price all look like mentions to a regex and none of them is a person.
    """
    handles = _mentioned_handles(body)
    if not handles:
        return
    try:
        response = httpx.post(
            f"{USER_SERVICE}/internal/profiles/by-handle", json={"handles": handles}, timeout=5
        )
        response.raise_for_status()
        people = response.json().get("profiles", {})
    except Exception as exc:
        log.warning("could not resolve mentions: %s", exc)
        return

    actor = _authors({actor_id}).get(actor_id) or {}
    who = actor.get("display_name") or actor.get("handle") or "Someone"
    # The excerpt is shown to a reader, not stored, so the handles in it are
    # drawn as names - the same substitution the post itself gets. A
    # notification saying '@k_mbeki3 look at this' tells you nothing about who
    # that is, which is the whole point of a notification.
    excerpt = body or ""
    for handle, name in _mention_names([excerpt]).items():
        excerpt = re.sub(rf"@{re.escape(handle)}", f"@{name}", excerpt, flags=re.IGNORECASE)
    for profile in people.values():
        target = profile.get("user_id")
        if not target or target == actor_id:
            continue
        notify.notify(
            target,
            kind="mention",
            title=f"{who} mentioned you in a {where}",
            body=excerpt[:140],
            # /p/{id} opens the post in the feed and is the same address a
            # shared link uses, so one route has to keep working rather than
            # two.
            link=f"/p/{post_id}",
        )



def extract_hashtags(body: str) -> list[str]:
    r"""Pull #tags out of a body.

    Hashtags and the composer's topic field are the same thing to the ranker, so
    they are merged rather than tracked separately — a member who writes
    "#agriculture" gets the same reach as one who filled the topics box, which is
    the behaviour anyone would expect.

    The pattern accepts accented, Arabic and CJK characters: an English-only
    \w would silently drop a Swahili or Arabic tag.
    """
    return [tag.lower() for tag in HASHTAG_RE.findall(body or "")]


def _classify_and_store(
    db: OrmSession, post: models.Post, media_kinds: list[str], author_is_minor: bool
) -> classifier.Classification:
    """Run the classifier and write the result alongside the post.

    Inline rather than queued. A post that is published first and classified a
    few seconds later is a post that was visible to everybody for those
    seconds, and "a few seconds" is the entire lifetime of most feed
    impressions. The classifier is a regex pass over one body of text; it costs
    less than the insert it accompanies.
    """
    result = classifier.classify(
        body=post.body or "",
        media_kinds=media_kinds,
        author_is_minor=author_is_minor,
        declared_mature=bool(post.mature),
    )

    row = db.scalar(
        select(models.ContentSafetyClassification).where(
            models.ContentSafetyClassification.content_id == post.id
        )
    )
    if row is None:
        row = models.ContentSafetyClassification(id=new_id("csc"), content_id=post.id)
        db.add(row)
    for key, value in result.as_payload().items():
        setattr(row, key, value)

    if result.block_publication:
        # Never published, not published-and-then-hidden. The difference is
        # whether anybody saw it.
        post.status = "withheld"
        # Written down so the author can see what happened and contest it. A
        # refusal nobody recorded cannot be appealed and cannot be counted.
        moderation.record_decision(
            db, subject_id=post.author_id, content_id=post.id, content_kind="post",
            action="refused_publication", age_rating=result.age_rating,
            # Withheld posts do survive in the posts table, but the snapshot is
            # kept anyway: "withheld" is the one state an author may later edit
            # their way out of, and a reviewer needs the text as it was judged.
            body_snapshot=post.body, appealable=not result.escalate_child_safety,
        )
    elif result.age_rating not in ("GENERAL", "TEEN_13_PLUS"):
        # Not a punishment — the post is published and adults see it normally.
        # Recorded anyway, because "why does nobody see my posts" is otherwise
        # unanswerable, and a rating is the thing most often gotten wrong.
        # No snapshot: the post exists and a reviewer can read it from the
        # posts table. A snapshot is for content that was never stored.
        moderation.record_decision(
            db, subject_id=post.author_id, content_id=post.id, content_kind="post",
            action="restricted_by_rating", age_rating=result.age_rating,
            appealable=not result.escalate_child_safety,
        )
    if result.escalate_child_safety:
        # Out of the ordinary queue entirely. Logged at error level so it
        # surfaces in alerting rather than waiting to be noticed in a backlog.
        log.error(
            "child-safety escalation on %s by %s (risk %s)",
            post.id, post.author_id, result.exploitation_risk,
        )
    return result


def _reclassify_after_edit(
    db: OrmSession, post: models.Post, author_is_minor: bool
) -> classifier.Classification:
    """Classify an edited post again. The result can tighten what is in force, never loosen it.

    Classification used to run at creation only, so a post published as
    something harmless and then edited into adult or exploitative text kept its
    first rating and went on reaching minors. The edit is now judged like a new
    post, with one difference: the rating, every per-category level and the
    exploitation risk only ever go up. Loosening is a reviewer's or an
    appeal's to decide, for the reason reports and the author's own "mature"
    flag only tighten too: otherwise wording a post blandly for the classifier
    and then back again would be a way around any rating, a human one included.

    Anything tightened goes back to "pending": whatever a reviewer confirmed,
    it was not the text that is there now. A refusal withholds the post and is
    recorded, exactly as at creation; a withheld post is never released by an
    edit. The caller commits.
    """
    media_kinds = list(
        db.scalars(select(models.PostMedia.kind).where(models.PostMedia.post_id == post.id)).all()
    )
    # Locked until the commit, and read fresh: the comparison below must be
    # against the rating in force, not a copy from before a reviewer's answer
    # landed. Unlocked, an edit racing a review would write its older rating
    # over the reviewer's stricter one.
    row = db.scalar(
        select(models.ContentSafetyClassification)
        .where(models.ContentSafetyClassification.content_id == post.id)
        .with_for_update()
    )
    if row is None:
        # Never classified (a post from before classification existed): it is
        # in effect a new post, and is judged as one.
        return _classify_and_store(db, post, media_kinds, author_is_minor)

    result = classifier.classify(
        body=post.body or "",
        media_kinds=media_kinds,
        author_is_minor=author_is_minor,
        declared_mature=bool(post.mature),
    )

    tightened = rating_raised = False
    if rating_strictness(result.age_rating) > rating_strictness(row.age_rating):
        row.age_rating = result.age_rating
        tightened = rating_raised = True
    for field_name, level in result.levels.items():
        if level > (getattr(row, field_name) or 0):
            setattr(row, field_name, level)
            tightened = True
    if result.exploitation_risk > (row.exploitation_risk or 0):
        row.exploitation_risk = result.exploitation_risk
        tightened = True
    if tightened:
        row.human_review_status = "pending"
        row.classifier_source = f"edit:{result.classifier_source}"
        row.classifier_confidence = result.classifier_confidence

    # A decision is recorded when something the author can see changes - the
    # post is taken down, or its rating goes up - not on every edit that nudges
    # a category: each decision can be appealed once, and re-minting one per
    # edit would turn "once" into "as often as you like".
    if result.block_publication:
        newly_withheld = post.status not in ("withheld", "removed")
        if post.status != "removed":
            post.status = "withheld"
        if newly_withheld or result.escalate_child_safety:
            moderation.record_decision(
                db, subject_id=post.author_id, content_id=post.id, content_kind="post",
                action="refused_publication", age_rating=result.age_rating,
                # The text as it was judged: the author may edit it again, and
                # the reviewer needs what was refused, not what replaced it.
                body_snapshot=post.body, appealable=not result.escalate_child_safety,
            )
    elif rating_raised and row.age_rating not in ("GENERAL", "TEEN_13_PLUS"):
        moderation.record_decision(
            db, subject_id=post.author_id, content_id=post.id, content_kind="post",
            action="restricted_by_rating", age_rating=row.age_rating,
            appealable=not result.escalate_child_safety,
        )
    if result.escalate_child_safety:
        log.error(
            "child-safety escalation on edited %s by %s (risk %s)",
            post.id, post.author_id, result.exploitation_risk,
        )
    return result


NOT_YOUR_MEDIA = "One of the attached files does not exist or is not yours to attach"


def _verified_media(items: list[dict], author_id: str) -> list[dict]:
    """The media a post may carry, as media-service says they are.

    A client used to name any media_id, with any url and any kind, and the post
    took its word. The age gate depends on that link: a viewer is handed a ticket
    for each asset of a post they may see, and the decision is made from the
    *post's* rating. Attach someone else's adult video to a post of your own
    that rates General, and a teenager who may see your post is given a ticket
    for the video. So an asset is attached only if it is the author's own post
    media (not an avatar, not a chat attachment) and finished uploading, and its
    url and kind are taken from media-service rather than from the request.

    Failing to ask is a refusal, not a pass: unlike marking media restricted
    afterwards, this is a check, and a check that is skipped when its service
    hiccups is not one.
    """
    checked: list[dict] = []
    for item in items:
        media_id = item.get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise HTTPException(status_code=400, detail="Every media item needs a media_id")
        try:
            response = httpx.get(f"{MEDIA_URL}/internal/media/{media_id}", timeout=5)
        except httpx.HTTPError as exc:
            log.error("could not check media %s: %s", media_id, exc)
            raise HTTPException(status_code=503, detail="Media is unavailable right now; try again") from exc
        if response.status_code == 404:
            raise HTTPException(status_code=400, detail=NOT_YOUR_MEDIA)
        if response.status_code >= 400:
            log.error("media-service answered %s for media %s", response.status_code, media_id)
            raise HTTPException(status_code=503, detail="Media is unavailable right now; try again")
        asset = response.json()
        if (
            asset.get("owner_id") != author_id
            or asset.get("purpose") is not None
            or asset.get("private")
            or asset.get("status") != "ready"
            or not asset.get("url")
        ):
            # The same answer whether it is missing or somebody else's: telling
            # them apart would confirm which ids exist.
            raise HTTPException(status_code=400, detail=NOT_YOUR_MEDIA)
        # width and height come from the asset for the same reason url and kind
        # do: media-service read them off the file's own header, so they are a
        # measurement rather than a claim. A client's numbers decide how much
        # room the feed reserves for a picture, and a wrong pair - careless or
        # deliberate - is a post that reserves a screen and a half.
        #
        # Only when the asset actually has them: nothing was measured before
        # this existed, and video and audio have no header to read, so the
        # client's value is still better than dropping to none.
        measured = {
            key: asset[key]
            for key in ("width", "height")
            if isinstance(asset.get(key), int) and asset[key] > 0
        }
        checked.append({
            **item,
            "media_id": asset["id"],
            "url": asset["url"],
            "kind": asset.get("kind") or "image",
            **measured,
        })
    return checked


def _restrict_attached_media(media_ids: list[str | None]) -> None:
    """Tell media-service these assets now need a ticket.

    Best-effort and logged loudly on failure. The alternative - refusing the
    post because one HTTP call did not land - would take posting down whenever
    media-service hiccups, and the asset is already unreferenced and
    unguessable in the meantime. The reconciliation job in NOT-DONE.md is the
    proper backstop.
    """
    for media_id in {m for m in media_ids if m}:
        try:
            httpx.post(f"{MEDIA_URL}/internal/media/{media_id}/restrict", timeout=4).raise_for_status()
        except Exception as exc:
            log.error("could not restrict media %s: %s", media_id, exc)


def _post_out(
    post: models.Post,
    db: OrmSession,
    why: list[dict] | None = None,
    viewer: str | None = None,
    authors: dict[str, dict] | None = None,
    reposted: set[str] | None = None,
) -> dict:
    media = db.scalars(
        select(models.PostMedia).where(models.PostMedia.post_id == post.id).order_by(models.PostMedia.position)
    ).all()
    # Every media URL leaves here as a short-lived ticket bound to this viewer.
    # Minted only at this point, which is downstream of the age check that
    # selected the post in the first place - so a URL cannot exist for a viewer
    # who was never allowed the post it belongs to.
    signed_media = {
        m.media_id: mediasign.sign_url(m.url, m.media_id, viewer) for m in media
    }
    # The names behind any @handles in the body, so the card can draw the
    # person rather than the address. Resolved here rather than at the twelve
    # call sites, for the same reason `authors` is: four of them had already
    # forgotten to pass it. Costs nothing for the posts that mention nobody,
    # which is most of them.
    mentions = _mention_names([post.body or ""])
    # A feed passes the batch it resolved for the whole page. A single-post
    # response - creating one, editing one, opening one by link - passed
    # nothing, so `author` came back null and the card had no name to show
    # until something refetched it. Whoever posted saw the wrong name on their
    # own post until they reloaded. Resolving it here rather than at each call
    # site, because four of them had already forgotten.
    if authors is None:
        wanted = {post.author_id}
        if post.repost_of:
            original = db.get(models.Post, post.repost_of)
            if original is not None:
                wanted.add(original.author_id)
        authors = _resolve_authors(wanted)
    return {
        "id": post.id,
        "author_id": post.author_id,
        # Resolved in one batch per response; without a handle the card has no
        # profile to link its avatar to.
        "author": (authors or {}).get(post.author_id),
        "body": post.body,
        # handle -> name, for the @mentions inside that body.
        "mentions": mentions,
        "format": post.format,
        "lang": post.lang,
        "visibility": post.visibility,
        "circle_id": post.circle_id,
        "community_id": post.community_id,
        "country": post.country,
        "city": post.city,
        "topics": [t for t in (post.topics or "").split(",") if t],
        "provenance": post.provenance,
        "mature": post.mature,
        "series_id": post.series_id,
        "episode_number": post.episode_number,
        "likes_count": post.likes_count,
        "comments_count": post.comments_count,
        "reposts_count": post.reposts_count,
        "views_count": post.views_count,
        # The shared post travels inside the card. Depth is capped at one by
        # repost() pointing at the original, so this never recurses.
        "repost_of": (
            _post_out(original, db, viewer=viewer, authors=authors, reposted=reposted)
            if post.repost_of
            and (original := db.get(models.Post, post.repost_of))
            # Reposts of restricted posts are refused now, but rows from before
            # that rule must not carry a circle post into a stranger's feed.
            and (original.visibility == "public" or original.author_id == viewer)
            and original.status not in ("removed", "draft")
            else None
        ),
        # Resolved once per response by _reposted_by(); a per-card lookup here
        # would be one extra query for every post on the page.
        "reposted_by_me": post.id in (reposted if reposted is not None else set()),
        "created_at": post.created_at,
        "edited_at": post.edited_at,
        "media": [
            {
                "url": signed_media[m.media_id],
                # A video's cover, behind the same ticket (media-service
                # answers 404 when it has none and the player draws its own).
                "poster_url": (
                    f"{signed_media[m.media_id]}&poster=1"
                    if m.kind in ("video", "short") and signed_media[m.media_id]
                    else None
                ),
                # Lets an article place this file in its text by id.
                "media_id": m.media_id,
                "kind": m.kind,
                "alt_text": m.alt_text,
                "width": m.width,
                "height": m.height,
                "duration_seconds": m.duration_seconds,
            }
            for m in media
        ],
        # Included inline so a feed of 20 posts does not fire 20 extra requests
        # just to colour the reaction buttons.
        "reactions": _reaction_summary(db, post.id, viewer),
        "why": why,
    }


def _author_ids(db: OrmSession, posts: list[models.Post]) -> set[str]:
    """Every author on the page, including the authors of shared originals.

    One extra query for the whole page rather than a card falling back to a
    raw `usr_…` id because its author was never resolved.
    """
    ids = {p.author_id for p in posts}
    shared = [p.repost_of for p in posts if p.repost_of]
    if shared:
        ids |= set(
            db.scalars(select(models.Post.author_id).where(models.Post.id.in_(shared))).all()
        )
    return ids


def _with_actors(item: dict, actors: dict[str, dict]) -> dict:
    """Attach "people you follow who were here" to a post on its way out.

    Read from the post the card actually shows: on a repost that is the
    original, which is where the reactions and comments live.
    """
    source = item.get("repost_of") or item
    found = actors.get(source.get("id") or "")
    if found:
        item["known_actors"] = found
    return item


def _known_actors(
    db: OrmSession,
    viewer: str | None,
    following: set[str] | frozenset[str],
    posts: list[models.Post],
    per_post: int = 3,
) -> dict[str, dict]:
    """People this viewer follows who have reacted to or commented on each post.

    "Three people liked this" is noise; "Ama and two others liked this" is a
    reason to look. So only people the viewer actually follows are named, and
    the rest are a count.

    One query for reactions and one for comments across the whole page, rather
    than two per post: a feed of twenty was forty round trips the moment this
    was written per-card.

    Returns {post_id: {"people": [{id, name, avatar, action}], "others": n}}.
    `others` counts everybody else who acted, so a post can say "Ama and 40
    others" without naming forty strangers.
    """
    if not posts:
        return {}
    ids = [p.id for p in posts] + [p.repost_of for p in posts if p.repost_of]
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return {}

    # Totals first, for everyone - the "and N others" part does not depend on
    # who the viewer knows. Kept per verb as well as in total, because
    # "1 person reacted or commented" is what a program says when it knows the
    # count and not the action, and the action is right here.
    totals: dict[str, set[str]] = {}
    reacted: dict[str, set[str]] = {}
    commented: dict[str, set[str]] = {}
    for post_id, user_id in db.execute(
        select(models.Reaction.post_id, models.Reaction.user_id)
        .where(models.Reaction.post_id.in_(ids))
    ).all():
        totals.setdefault(post_id, set()).add(user_id)
        reacted.setdefault(post_id, set()).add(user_id)
    for post_id, user_id in db.execute(
        select(models.Comment.post_id, models.Comment.author_id)
        .where(models.Comment.post_id.in_(ids), models.Comment.status == "published")
    ).all():
        totals.setdefault(post_id, set()).add(user_id)
        commented.setdefault(post_id, set()).add(user_id)

    known = set(following)
    named: dict[str, list[tuple[str, str]]] = {}
    if known:
        for post_id, user_id in db.execute(
            select(models.Reaction.post_id, models.Reaction.user_id)
            .where(models.Reaction.post_id.in_(ids), models.Reaction.user_id.in_(known))
            .order_by(models.Reaction.created_at.desc())
        ).all():
            bucket = named.setdefault(post_id, [])
            if len(bucket) < per_post and user_id not in [u for u, _ in bucket]:
                bucket.append((user_id, "reacted"))
        for post_id, user_id in db.execute(
            select(models.Comment.post_id, models.Comment.author_id)
            .where(
                models.Comment.post_id.in_(ids),
                models.Comment.author_id.in_(known),
                models.Comment.status == "published",
            )
            .order_by(models.Comment.created_at.desc())
        ).all():
            bucket = named.setdefault(post_id, [])
            if len(bucket) < per_post and user_id not in [u for u, _ in bucket]:
                bucket.append((user_id, "commented"))

    profiles = _resolve_authors({u for bucket in named.values() for u, _ in bucket})
    out: dict[str, dict] = {}
    for post_id in ids:
        bucket = named.get(post_id, [])
        everyone = totals.get(post_id, set())
        # The viewer's own action is not news to them.
        everyone = {u for u in everyone if u != viewer}
        if not bucket and not everyone:
            continue
        people = []
        for user_id, action in bucket:
            profile = profiles.get(user_id)
            people.append({
                "id": user_id,
                "name": (profile or {}).get("display_name") or (profile or {}).get("handle") or "Someone",
                "handle": (profile or {}).get("handle"),
                "avatar_url": (profile or {}).get("avatar_url"),
                "action": action,
            })
        named_ids = {u for u, _ in bucket}
        rest = everyone - named_ids
        # What the unnamed ones did. Only "both" when it is genuinely both -
        # otherwise the line says the thing that actually happened.
        rest_reacted = bool(rest & reacted.get(post_id, set()))
        rest_commented = bool(rest & commented.get(post_id, set()))
        action = (
            "both" if rest_reacted and rest_commented
            else "commented" if rest_commented
            else "reacted"
        )
        out[post_id] = {
            "people": people,
            "others": max(0, len(rest)),
            "others_action": action,
        }
    return out


def _reposted_by(db: OrmSession, viewer: str | None, posts: list[models.Post]) -> set[str]:
    """Which of these posts the viewer has already reposted — in one query.

    Includes the posts nested inside a share: the repost button on a shared card
    acts on the original, so it is the original's state the card needs.
    """
    post_ids = [p.id for p in posts] + [p.repost_of for p in posts if p.repost_of]
    if not viewer or not post_ids:
        return set()
    return set(
        db.scalars(
            select(models.Post.repost_of).where(
                models.Post.author_id == viewer,
                models.Post.repost_of.in_(post_ids),
                models.Post.body == "",
                models.Post.status == "published",
            )
        ).all()
    )


def _merge_topics(topics: list[str], body: str) -> str | None:
    """Explicit topics plus the hashtags found in the body, de-duplicated."""
    seen: list[str] = []
    for value in [t.strip().lower() for t in topics if t.strip()] + extract_hashtags(body):
        if value not in seen:
            seen.append(value)
    return ",".join(seen) or None


def _resolve_authors(ids: set[str]) -> dict[str, dict]:
    """One call for every author on the page rather than one per post."""
    if not ids:
        return {}
    try:
        response = httpx.post(f"{USER_URL}/internal/profiles", json={"ids": sorted(ids)}, timeout=5)
        response.raise_for_status()
        return response.json()["profiles"]
    except Exception as exc:
        log.warning("could not resolve post authors: %s", exc)
        return {}


def _refuse_or_404(profile, detail: str = "Post not found") -> None:
    """Raise for a viewer who may not see something.

    Fails closed either way; only the explanation differs. A degraded age
    lookup is a fault on our side and is retryable, so it must not be dressed
    up as a missing post - a member sent looking for content that is in front
    of them learns to distrust the whole surface.
    """
    if getattr(profile, "degraded", False):
        raise HTTPException(
            status_code=503,
            detail="We could not check your account just now. Please try again.",
        )
    raise HTTPException(status_code=404, detail=detail)


def _viewer_age(user_id: str | None):
    """The viewer's authoritative age profile.

    Not a preference. ``age_mode`` used to come from the member's own settings,
    which meant the age gate was a checkbox the person being gated could clear.
    This comes from the identity record, and a failed lookup returns UNKNOWN —
    which the policy engine treats as a minor.
    """
    return ageclient.age_profile(user_id)


def _viewer_prefs(user_id: str | None) -> dict:
    """The viewer's *display* settings — data saver, autoplay.

    Age is deliberately no longer in here. These are preferences a member is
    entitled to set for themselves; age is not one of them.
    """
    if not user_id:
        return {"data_saver": False, "autoplay_media": True, "degraded": False}
    try:
        response = httpx.get(f"{USER_URL}/internal/preferences/{user_id}", timeout=4)
        response.raise_for_status()
        return {**response.json(), "degraded": False}
    except Exception as exc:
        log.warning("preferences lookup failed for %s: %s", user_id, exc)
        return {"data_saver": True, "autoplay_media": False, "degraded": True}


# Media kinds a data-saver feed will not carry inline.
HEAVY_MEDIA = {"video", "audio"}


def _apply_prefs(item: dict, prefs: dict) -> dict:
    """Trim a serialised post to what the viewer's settings allow.

    Done here rather than in the client because this is where the bytes are
    decided. A client that merely hides a video has already downloaded it.
    """
    if prefs.get("data_saver"):
        item["media"] = [
            {**m, "url": None, "deferred": True} if m["kind"] in HEAVY_MEDIA else m
            for m in item["media"][:1]
        ]
        item["data_saver"] = True
    if not prefs.get("autoplay_media", True):
        item["autoplay"] = False
    return item


def _context(db: OrmSession, principal) -> ranking.Context:
    ctx = ranking.Context(user_id=principal.user_id if principal else None)
    if principal is None:
        return ctx

    try:
        response = httpx.get(f"{USER_URL}/internal/audience/{principal.user_id}", timeout=4)
        if response.status_code == 200:
            data = response.json()
            ctx.muted_authors = set(data.get("blocked", []))
            ctx.following = set(data.get("following", []))
            ctx.graph_known = True
        else:
            log.warning("audience lookup for %s answered %s", principal.user_id, response.status_code)
    except Exception as exc:
        # A user-service hiccup degrades personalisation; it must not empty the feed.
        log.warning("audience lookup failed for %s: %s", principal.user_id, exc)

    # Declared interests come from preferences, which user-service owns.
    ctx.interests = {t.lower() for t in _viewer_prefs(principal.user_id).get("interest_topics", [])}

    for signal in db.scalars(
        select(models.FeedSignal).where(models.FeedSignal.user_id == principal.user_id)
    ).all():
        if signal.kind == "mute_author":
            ctx.muted_authors.add(signal.target_id)
        elif signal.kind == "mute_topic":
            ctx.muted_topics.add(signal.target_id.lower())
        else:
            key = f"{'author' if signal.target_type == 'author' else 'topic'}:{signal.target_id.lower()}"
            ctx.penalties[key] = ctx.penalties.get(key, 0.0) + signal.weight
    return ctx


# ---------------------------------------------------------------------------
# Posts
# ---------------------------------------------------------------------------

@app.post("/posts", status_code=201, tags=["posts"])
async def create_post(payload: PostIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if payload.visibility == "circle":
        _require_own_circle(principal.user_id, payload.circle_id)
    if payload.visibility == "community":
        if not payload.community_id:
            raise HTTPException(status_code=400, detail="A community post needs community_id")
        _require_membership(principal.user_id, payload.community_id)
    if not payload.body.strip() and not payload.media:
        raise HTTPException(status_code=400, detail="A post needs a body or media")

    media = _verified_media(payload.media, principal.user_id)
    data = payload.model_dump(exclude={"topics", "media"})
    # A circle id only means something on a circle post; elsewhere it would be
    # a stray pointer the audience rule might one day read.
    if payload.visibility != "circle":
        data["circle_id"] = None
    # Same reasoning for the community pointer: left on a public post it is a
    # stray id the audience rule might one day read, and a post that is public
    # has no business claiming to belong to a group.
    if payload.visibility != "community":
        data["community_id"] = None
    post = models.Post(
        id=new_id("pst"),
        author_id=principal.user_id,
        topics=_merge_topics(payload.topics, payload.body),
        **data,
    )
    db.add(post)
    db.flush()

    for index, item in enumerate(media):
        db.add(
            models.PostMedia(
                post_id=post.id,
                media_id=item["media_id"],
                url=item["url"],
                kind=item["kind"],
                alt_text=item.get("alt_text"),
                position=index,
                width=item.get("width"),
                height=item.get("height"),
                duration_seconds=item.get("duration_seconds"),
            )
        )
    # Classified before it is committed as published: the age filter reads the
    # classification row, so a post that reaches the feed without one would be
    # invisible to minors at best and unrated at worst.
    verdict = _classify_and_store(
        db, post, [m["kind"] for m in media],
        _viewer_age(principal.user_id).is_minor,
    )
    db.commit()
    db.refresh(post)

    if verdict.block_publication:
        # Deliberately vague, and identical whatever the reason. A message that
        # explains which rule was tripped is a message that explains how to get
        # around it next time.
        raise HTTPException(
            status_code=403,
            detail="This post cannot be published. If you believe this is a mistake, contact support.",
        )

    # Attached media stops being publicly fetchable from this moment. Marked
    # here, server-side, rather than declared by the uploader: a client that
    # could label its own media "public" would be the age gate.
    _restrict_attached_media([m["media_id"] for m in media])
    db.refresh(post)

    await events.publish(
        "post.published",
        {"post_id": post.id, "author_id": post.author_id, "format": post.format, "lang": post.lang},
    )
    # Only public posts announce themselves on the shared channels: a followers-
    # only or circle post must not surface on a stranger's screen just because
    # the feed is live.
    if post.visibility == "public":
        live("shorts" if post.format == "short" else "feed", "post",
             post_id=post.id, author_id=post.author_id, format=post.format,
             mature=post.mature)

    # After the write, so a mention can never cost the post.
    _notify_mentions(post.body, principal.user_id, post.id, "post")
    return _post_out(post, db, viewer=principal.user_id)


# Declared before /posts/{post_id}: FastAPI matches in order, and the
# parameterised route would otherwise read "by" as a post id.
@app.get("/link-preview", tags=["posts"])
def link_preview(url: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The card for a link somebody pasted into a post.

    Signed in only. An endpoint that fetches an arbitrary URL on request is a
    small open proxy if anybody may call it: the request would come from this
    server's address, with this server's reputation attached.

    Answers are cached, successes and failures alike. Without caching the
    failure, a link to a site that is down is re-fetched by every viewer of
    that post, every time - this platform pointing a crowd at somebody else's
    server.
    """
    key = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
    cached = db.get(models.LinkPreview, key)
    now = datetime.now(timezone.utc)
    if cached:
        age = now - cached.fetched_at
        fresh_for = timedelta(hours=1) if cached.failed_at else timedelta(days=7)
        if age < fresh_for:
            if cached.failed_at:
                raise HTTPException(status_code=422, detail="That link has no preview")
            return {
                "url": cached.url, "title": cached.title, "description": cached.description,
                "image": cached.image_url, "site_name": cached.site_name, "cached": True,
            }

    try:
        card = linkpreview.fetch(url)
    except linkpreview.UnsafeURL as exc:
        # The reason is not returned. "That link points inside a private
        # network" is a yes/no oracle for what exists on the private network,
        # answered one guess at a time.
        log.info("link preview refused: %s", exc)
        row = cached or models.LinkPreview(url_hash=key, url=url[:2000])
        row.failed_at = now
        row.fetched_at = now
        db.merge(row)
        db.commit()
        raise HTTPException(status_code=422, detail="That link has no preview")

    row = cached or models.LinkPreview(url_hash=key, url=url[:2000])
    row.url = card["url"][:2000]
    row.title = card["title"]
    row.description = card["description"]
    row.image_url = card["image"]
    row.site_name = card["site_name"]
    row.failed_at = None
    row.fetched_at = now
    db.merge(row)
    db.commit()
    return {**card, "cached": False}


@app.get("/share/p/{post_id}", response_class=HTMLResponse, tags=["posts"])
def post_share_card(post_id: str, request: Request, db: OrmSession = Depends(get_db)):
    """The page a link unfurler sees for a shared post.

    The app is a single-page build: one index.html with one set of meta tags,
    written before any post existed. A crawler does not run JavaScript, so
    every shared post previewed as the same generic Kinjy card no matter what
    was in it.

    This renders the tags for one post. Caddy sends crawler requests for /p/<id>
    here and everyone else to the app, so a person still gets the real page.

    Only a public post gets a card, and only one that a signed-out visitor could
    read anyway. A crawler has no account and no age, so it is treated exactly
    as the most restricted visitor: anything else would turn a preview into a
    way of reading a post the link's recipient could not open - and would put
    the first line of a followers-only post into a chat app's preview.
    """
    post = db.get(models.Post, post_id)
    fallback = f"{settings.FRONTEND_URL.rstrip('/')}/og-image.png"
    site = settings.FRONTEND_URL.rstrip("/")

    title, description, image = "Kinjy", DEFAULT_SHARE_DESCRIPTION, fallback
    if (
        post is not None
        and post.status == "published"
        and post.visibility == "public"
        and agefilter.visible_to(db, _viewer_age(None), post.id)
    ):
        author = _resolve_authors({post.author_id}).get(post.author_id) or {}
        who = author.get("display_name") or author.get("handle") or "Someone"
        title = f"{who} on Kinjy"
        body = htmlish_to_text(post.body).strip()
        description = (body[:197] + "…") if len(body) > 200 else (body or DEFAULT_SHARE_DESCRIPTION)
        # The post's own picture if it has one, Kinjy's otherwise - a card with
        # no image is a grey rectangle in every chat app.
        shot = db.scalar(
            select(models.PostMedia)
            .where(models.PostMedia.post_id == post.id, models.PostMedia.kind == "image")
            .order_by(models.PostMedia.position)
        )
        if shot is not None and shot.url:
            # Signed, like every other read of post media. A bare media URL is
            # a 404: the bytes are served against a ticket, so an unsigned
            # og:image is a blank rectangle in every chat app. `sign_url` binds
            # an absent viewer as the literal "anon", which is exactly what a
            # crawler is. Long-lived because an unfurler may come back to the
            # link days later - it is a public post's picture either way.
            image = mediasign.sign_url(shot.url, shot.media_id, None, ttl=60 * 60 * 24 * 14)

    url = f"{site}/p/{post_id}"
    return HTMLResponse(_share_html(title, description, image, url))


DEFAULT_SHARE_DESCRIPTION = (
    "Social, forums, family trees, memorials, commerce and AI agents — in one elegant ecosystem."
)


def htmlish_to_text(body: str) -> str:
    """An article body is HTML; a preview description is not."""
    return html_module.unescape(re.sub(r"<[^>]+>", " ", body or "")).replace("\xa0", " ")


def _share_html(title: str, description: str, image: str, url: str) -> str:
    """The smallest page that previews correctly and still works if a person
    lands on it - a crawler that passes the link to a human must not leave them
    staring at a blank document."""
    esc = html_module.escape
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Kinjy">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{esc(image)}">
<link rel="canonical" href="{esc(url)}">
<meta http-equiv="refresh" content="0; url={esc(url)}">
</head>
<body><p><a href="{esc(url)}">{esc(title)}</a></p></body>
</html>"""


@app.get("/posts/by/{author_id}", tags=["posts"])
def posts_by_author(
    author_id: str,
    principal: MaybeUser,
    limit: int = 20,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    """A member's own posts, for their profile page.

    Visibility is applied here rather than in the client: a visitor sees only
    public posts, and follower-only or circle-only posts stay out of the payload
    entirely instead of being fetched and then hidden.
    """
    viewer = principal.user_id if principal else None
    prefs = _viewer_prefs(viewer)
    age = _viewer_age(viewer)
    stmt = select(models.Post).where(
        models.Post.author_id == author_id, models.Post.status == "published"
    )
    # Before ordering and before paging: restricted rows are never fetched.
    stmt = agefilter.restrict_query(stmt, age)
    if viewer != author_id:
        # The same audience rule as the feed: a follower sees the followers-only
        # posts, a circle member the posts shared with them, a visitor neither.
        stmt = stmt.where(_audience_clause(_viewer_audience(viewer)))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Post.created_at.desc()).limit(min(limit, 50)).offset(offset)
    ).all()
    authors = _resolve_authors(_author_ids(db, list(rows)))
    reposted = _reposted_by(db, viewer, list(rows))
    items = [
        _apply_prefs(_post_out(p, db, viewer=viewer, authors=authors, reposted=reposted), prefs)
        for p in rows
    ]
    return {"total": total, "items": agefilter.filter_items(db, age, items)}


class ClassificationIn(BaseModel):
    """A safety classification, as the pipeline or a reviewer writes it."""

    age_rating: str = Field(
        default="UNCLASSIFIED",
        pattern="^(GENERAL|TEEN_13_PLUS|TEEN_16_PLUS|ADULT_18_PLUS|PROHIBITED|UNCLASSIFIED)$",
    )
    sexual_content_level: int = Field(default=0, ge=0, le=3)
    nudity_level: int = Field(default=0, ge=0, le=3)
    violence_level: int = Field(default=0, ge=0, le=3)
    graphic_content_level: int = Field(default=0, ge=0, le=3)
    drugs_level: int = Field(default=0, ge=0, le=3)
    alcohol_level: int = Field(default=0, ge=0, le=3)
    gambling_level: int = Field(default=0, ge=0, le=3)
    dangerous_activity_level: int = Field(default=0, ge=0, le=3)
    self_harm_risk: int = Field(default=0, ge=0, le=3)
    hate_or_abuse_risk: int = Field(default=0, ge=0, le=3)
    exploitation_risk: int = Field(default=0, ge=0, le=3)
    classifier_source: str = ""
    classifier_confidence: float = 0.0
    human_review_status: str = "none"
    jurisdiction_overrides: dict = {}


@app.post("/internal/classify/{post_id}", tags=["internal"])
def classify_post(post_id: str, payload: ClassificationIn, db: OrmSession = Depends(get_db)):
    """Record or update a post's safety classification.

    Internal only: the classifier, a reviewer's tooling and the child-safety
    workflow call it. It is not reachable through the gateway, because a
    classification a member could set for their own post is not a
    classification at all.

    Upserted rather than appended so there is exactly one current answer per
    item, and the read path never has to decide which of several rows wins.

    A rating a reviewer has settled is not overwritten from here: an automatic
    pass must not quietly undo a human's answer, overturned appeals included
    (moderation.file_report keeps the same rule for reports). The one thing
    that still gets through is an exploitation signal, which withholds the
    post whatever the rating says. Reviewers re-rate through
    /admin/classification/{post_id}/review.
    """
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = _classification_row(db, post_id)
    if existing is not None and existing.human_review_status == "confirmed":
        if payload.exploitation_risk >= 2 or payload.age_rating == "PROHIBITED":
            post.status = "removed"
            db.commit()
            log.error("content %s withheld pending child-safety review (rating was reviewer-confirmed)", post_id)
            return {"content_id": post_id, "age_rating": existing.age_rating, "status": post.status}
        log.info("classification of %s left alone: already settled by a reviewer", post_id)
        raise HTTPException(status_code=409, detail="A reviewer has settled this rating; only a reviewer can change it.")

    row = _apply_classification(db, post, payload)
    db.commit()
    return {"content_id": post_id, "age_rating": row.age_rating, "status": post.status}


def _classification_row(db: OrmSession, post_id: str) -> models.ContentSafetyClassification | None:
    return db.scalar(
        select(models.ContentSafetyClassification).where(
            models.ContentSafetyClassification.content_id == post_id
        )
    )


def _apply_classification(
    db: OrmSession, post: models.Post, payload: ClassificationIn
) -> models.ContentSafetyClassification:
    """Write a classification onto the post's one row; the caller commits."""
    row = _classification_row(db, post.id)
    if row is None:
        row = models.ContentSafetyClassification(id=new_id("csc"), content_id=post.id)
        db.add(row)

    data = payload.model_dump()
    overrides = data.pop("jurisdiction_overrides", {}) or {}
    for key, value in data.items():
        setattr(row, key, value)
    row.jurisdiction_overrides = json.dumps(overrides)

    # Exploitation risk is not an ordinary moderation outcome. It takes the
    # post out of circulation immediately and hands it to the child-safety
    # process rather than leaving it queued behind everything else.
    if payload.exploitation_risk >= 2 or payload.age_rating == "PROHIBITED":
        post.status = "removed"
        log.error("content %s withheld pending child-safety review", post.id)
    return row


@app.get("/internal/classification/{post_id}", tags=["internal"])
def read_classification(post_id: str, db: OrmSession = Depends(get_db)):
    row = db.scalar(
        select(models.ContentSafetyClassification).where(
            models.ContentSafetyClassification.content_id == post_id
        )
    )
    if row is None:
        # Not an error: "unrated" is a real state, and the engine treats it as
        # adult-only. Saying so plainly is better than a 404 the caller has to
        # interpret.
        return {"content_id": post_id, "age_rating": "UNCLASSIFIED", "classified": False}
    return {
        "content_id": post_id,
        "age_rating": row.age_rating,
        "classified": True,
        "human_review_status": row.human_review_status,
        "classifier_source": row.classifier_source,
    }


# ---------------------------------------------------------------------------
# Reports, decisions and appeals
# ---------------------------------------------------------------------------

class ReportIn(BaseModel):
    reason: str = Field(default="other", max_length=30)
    note: str | None = Field(default=None, max_length=1000)


class AppealIn(BaseModel):
    grounds: str | None = Field(default=None, max_length=2000)


class AppealVerdictIn(BaseModel):
    overturn: bool
    note: str | None = Field(default=None, max_length=2000)


def _report(
    content_id: str, content_kind: str, payload: ReportIn,
    principal: CurrentUser, db: OrmSession,
):
    """Shared by posts and comments: the handling is identical."""
    author_id = None
    if content_kind == "post":
        post = db.get(models.Post, content_id)
        if post is None:
            raise HTTPException(status_code=404, detail="Not found")
        author_id = post.author_id
    else:
        comment = db.get(models.Comment, content_id)
        if comment is None:
            raise HTTPException(status_code=404, detail="Not found")
        author_id = comment.author_id

    moderation.file_report(
        db, content_id=content_id, content_kind=content_kind,
        reporter_id=principal.user_id, author_id=author_id,
        reason=payload.reason, note=payload.note,
    )
    # Deliberately the same answer every time. Telling a reporter whether their
    # report moved a rating turns reporting into a probe for the threshold.
    return {"status": "received"}


@app.post("/posts/{post_id}/report", status_code=201, tags=["moderation"])
def report_post(
    post_id: str, payload: ReportIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Report a post. Reporting twice is the same as reporting once."""
    return _report(post_id, "post", payload, principal, db)


@app.post("/comments/{comment_id}/report", status_code=201, tags=["moderation"])
def report_comment(
    comment_id: str, payload: ReportIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    return _report(comment_id, "comment", payload, principal, db)


class CommentEdit(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@app.patch("/comments/{comment_id}", tags=["comments"])
def edit_comment(
    comment_id: str, payload: CommentEdit, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Fix a comment, within the same day the post itself allows.

    The same window as a post, for the same reason: a reply people have
    answered should not turn into different words underneath them. Measured
    from when it was written, so editing repeatedly cannot hold the window
    open.
    """
    comment = db.get(models.Comment, comment_id)
    if comment is None or comment.status != "published" or comment.author_id != principal.user_id:
        # The same answer whether it is missing or somebody else's: telling
        # them apart would confirm which ids exist.
        raise HTTPException(status_code=404, detail="Comment not found")

    if datetime.now(timezone.utc) - _aware(comment.created_at) > EDIT_WINDOW:
        raise HTTPException(
            status_code=403,
            detail="A comment can only be edited in the first 24 hours.",
        )

    comment.body = payload.body
    comment.edited_at = datetime.now(timezone.utc)
    db.commit()
    # Anyone newly named hears about it. Mentions already notified are not
    # re-sent, because _notify_mentions is only given the new text - somebody
    # named in both versions would otherwise be told twice for one comment.
    _notify_mentions(payload.body, principal.user_id, comment.post_id, "comment")
    return {"id": comment.id, "body": comment.body, "edited_at": comment.edited_at}


@app.delete("/comments/{comment_id}", status_code=204, tags=["comments"])
def delete_comment(comment_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Remove a comment. The author's, or the post author's to moderate.

    The row is marked rather than dropped, because replies hang off it: a hard
    delete would orphan every answer to it, and a thread with holes in it reads
    as data loss. The body is cleared, so "deleted" means the words are gone
    rather than merely hidden behind a flag some other reader might not honour.

    No time limit, deliberately. An edit window stops a conversation being
    rewritten; it must never stop somebody taking back something they regret.
    """
    comment = db.get(models.Comment, comment_id)
    if comment is None or comment.status != "published":
        raise HTTPException(status_code=404, detail="Comment not found")

    post = db.get(models.Post, comment.post_id)
    owns_post = post is not None and post.author_id == principal.user_id
    if comment.author_id != principal.user_id and not owns_post:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment.status = "deleted"
    comment.body = ""
    db.commit()

    # The counter on the post has to follow, or the card says 3 replies and
    # shows 2 for as long as anybody looks at it.
    if post is not None:
        post.comments_count = max(0, (post.comments_count or 0) - 1)
        db.commit()
    return None


def _comment_reaction_counts(db: OrmSession, comment_ids: list[str]) -> dict:
    """How a page of comments was answered: likes, and stickers by id.

    Two queries for the whole page rather than two per comment - a thread of
    eighty replies would otherwise be a hundred and sixty round trips to draw
    one screen. A plain like has no sticker id, so the two are counted apart
    and a comment can carry both.
    """
    if not comment_ids:
        return {"likes": {}, "stickers": {}}
    likes: dict[str, int] = {}
    by_sticker: dict[str, dict[str, int]] = {}
    for cid, sticker_id, n in db.execute(
        select(
            models.CommentReaction.comment_id,
            models.CommentReaction.sticker_id,
            func.count(),
        )
        .where(models.CommentReaction.comment_id.in_(comment_ids))
        .group_by(models.CommentReaction.comment_id, models.CommentReaction.sticker_id)
    ).all():
        likes[cid] = likes.get(cid, 0) + n
        if sticker_id:
            by_sticker.setdefault(cid, {})[sticker_id] = n
    return {"likes": likes, "stickers": by_sticker}


class CommentReactionIn(BaseModel):
    """A sticker to answer a comment with, or nothing for a plain like."""

    sticker_id: str | None = None


@app.post("/comments/{comment_id}/reaction", tags=["comments"])
def toggle_comment_like(
    comment_id: str,
    principal: CurrentUser,
    payload: CommentReactionIn | None = None,
    db: OrmSession = Depends(get_db),
):
    """Answer a comment with a like or a sticker, or take it back. One call.

    A toggle rather than a like and an unlike endpoint: the client knows
    whether it is currently liked, but the server is the one that can say so
    without a race, and two endpoints invite the double-like that the primary
    key would then reject as an error rather than as the no-op it is.

    One reaction per person per comment, so sending a different sticker
    replaces the one before it rather than stacking - the same rule as a
    message reaction, because a row of six stickers all from one person is a
    way to shout in somebody else's thread.
    """
    comment = db.get(models.Comment, comment_id)
    if comment is None or comment.status != "published":
        raise HTTPException(status_code=404, detail="Comment not found")

    sticker_id = (payload.sticker_id if payload else None) or None
    # Checked against the catalogue, never stored as given: an id the server
    # does not recognise would be drawn as a blank by every client that reads
    # this thread afterwards.
    if sticker_id is not None and stickers.get(sticker_id) is None:
        raise HTTPException(status_code=422, detail="That sticker does not exist")

    existing = db.get(models.CommentReaction, (comment_id, principal.user_id))
    if existing is not None and existing.sticker_id == sticker_id:
        db.delete(existing)
        liked = False
    elif existing is not None:
        existing.sticker_id = sticker_id
        liked = True
    else:
        db.add(
            models.CommentReaction(
                comment_id=comment_id, user_id=principal.user_id, sticker_id=sticker_id
            )
        )
        liked = True
    db.commit()

    # The author of the comment hears who answered it, the same as a post.
    if liked and comment.author_id != principal.user_id:
        try:
            notify.notify(
                comment.author_id,
                kind="reaction",
                title=f"{_name_of(principal.user_id)} reacted to your comment",
                body=_excerpt(comment.body or "")[:140] or None,
                link=f"/hub?post={comment.post_id}",
            )
        except Exception as exc:
            log.warning("could not notify the author of a comment reaction: %s", exc)

    counts = _comment_reaction_counts(db, [comment_id])
    return {
        "liked": liked,
        "likes": counts["likes"].get(comment_id, 0),
        "stickers": counts["stickers"].get(comment_id, {}),
        "my_sticker": sticker_id if liked else None,
    }


@app.get("/moderation/decisions", tags=["moderation"])
def my_decisions(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What has been restricted on this account, and where each appeal stands.

    A member who cannot see this has to infer moderation from their reach, which
    is how people conclude they are shadowbanned when they are not - and how
    they miss it when they are.
    """
    decisions = moderation.decisions_for(db, principal.user_id)
    appeals = {
        a.decision_id: a
        for a in db.scalars(
            select(models.ModerationAppeal).where(
                models.ModerationAppeal.decision_id.in_([d.id for d in decisions] or [""])
            )
        ).all()
    }
    return {
        "items": [moderation.decision_out(d, appeals.get(d.id)) for d in decisions],
    }


@app.post("/moderation/decisions/{decision_id}/appeal", status_code=201, tags=["moderation"])
def appeal_decision(
    decision_id: str, payload: AppealIn, principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Contest a decision. Once, by the person it was made about."""
    decision = db.get(models.ModerationDecision, decision_id)
    if decision is None or decision.subject_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Not found")
    if not decision.appealable:
        # No detail about why. This path is reached by child-safety
        # escalations, and explaining the boundary explains how to sit outside
        # it next time.
        raise HTTPException(
            status_code=403,
            detail="This decision cannot be reviewed here. Contact support.",
        )
    if moderation.appeal_for(db, decision.id) is not None:
        raise HTTPException(status_code=409, detail="This has already been appealed.")

    appeal = moderation.open_appeal(db, decision, principal.user_id, payload.grounds)
    db.commit()
    return {
        "id": appeal.id,
        "status": appeal.status,
        "due_at": appeal.due_at,
        "note": "Someone will look at this again. You will be told either way.",
    }


@app.get("/admin/moderation/appeals", tags=["admin"])
def appeal_queue(_: AdminUser, limit: int = 50, db: OrmSession = Depends(get_db)):
    """Open appeals, the late ones first, with the overturn rate beside them.

    The rate is here rather than on a separate dashboard on purpose: a reviewer
    who can see that a third of these decisions are being overturned is being
    told something about the classifier, not about the appellants.
    """
    appeals = moderation.queue(db, limit)
    decisions = {
        d.id: d
        for d in db.scalars(
            select(models.ModerationDecision).where(
                models.ModerationDecision.id.in_([a.decision_id for a in appeals] or [""])
            )
        ).all()
    }
    # The live text, for the decisions that restricted content rather than
    # refusing it. Those have no snapshot - the post still exists, so copying
    # it would be duplicating data - but "open pst_01M3... to read it" is not a
    # workable instruction, and a reviewer who cannot see the content will
    # either guess or skip.
    content_ids = [d.content_id for d in decisions.values()]
    live_bodies = {
        p.id: p.body
        for p in db.scalars(
            select(models.Post).where(models.Post.id.in_(content_ids or [""]))
        ).all()
    }
    live_bodies.update({
        cm.id: cm.body
        for cm in db.scalars(
            select(models.Comment).where(models.Comment.id.in_(content_ids or [""]))
        ).all()
    })

    now = moderation.now()
    return {
        "stats": moderation.stats(db),
        "items": [
            {
                "id": a.id,
                "decision_id": a.decision_id,
                "appellant_id": a.appellant_id,
                "grounds": a.grounds,
                "due_at": a.due_at,
                "overdue": a.due_at < now,
                "content_id": getattr(decisions.get(a.decision_id), "content_id", None),
                "content_kind": getattr(decisions.get(a.decision_id), "content_kind", None),
                "action": getattr(decisions.get(a.decision_id), "action", None),
                "age_rating": getattr(decisions.get(a.decision_id), "age_rating", None),
                "body_snapshot": (
                    getattr(decisions.get(a.decision_id), "body_snapshot", None)
                    or live_bodies.get(
                        getattr(decisions.get(a.decision_id), "content_id", None)
                    )
                ),
                "decided_by": getattr(decisions.get(a.decision_id), "decided_by", None),
            }
            for a in appeals
        ],
    }


@app.post("/admin/moderation/appeals/{appeal_id}", tags=["admin"])
def decide_appeal(
    appeal_id: str, payload: AppealVerdictIn, admin: AdminUser,
    db: OrmSession = Depends(get_db),
):
    """Uphold or overturn. Not by whoever made the decision being appealed."""
    # Locked until the commit: two reviewers (or one double click) answering
    # at once would otherwise both see "open", both act, and the second
    # answer would silently replace the first in the record.
    appeal = db.scalar(
        select(models.ModerationAppeal).where(models.ModerationAppeal.id == appeal_id).with_for_update()
    )
    if appeal is None:
        raise HTTPException(status_code=404, detail="Not found")
    if appeal.status != "open":
        raise HTTPException(status_code=409, detail="This appeal has already been answered.")

    decision = db.get(models.ModerationDecision, appeal.decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Not found")

    blocked = moderation.may_review(decision, admin.user_id)
    if blocked:
        raise HTTPException(status_code=403, detail=blocked)

    appeal.status = "overturned" if payload.overturn else "upheld"
    appeal.reviewer_id = admin.user_id
    appeal.reviewer_note = payload.note
    appeal.answered_at = moderation.now()

    if payload.overturn:
        moderation.overturn(db, decision, admin.user_id)
    else:
        # Recorded as a human decision so a second appeal, if the product ever
        # allows one, cannot be answered by this same reviewer.
        decision.decided_by = admin.user_id

    db.commit()
    notify.notify(
        appeal.appellant_id,
        "moderation_appeal",
        "Your appeal was reviewed"
        if not payload.overturn
        else "Your appeal was upheld and the restriction removed",
        body=payload.note or None,
        link="/moderation",
    )
    return {"id": appeal.id, "status": appeal.status}


@app.get("/admin/classification-queue", tags=["admin"])
def classification_queue(_: AdminUser, limit: int = 50, db: OrmSession = Depends(get_db)):
    """Content the classifier would not settle on its own.

    Mostly posts carrying media, which no text pass can see into. Until a
    reviewer or a vision model rates them they stay restricted, so the queue
    being long costs reach, not safety.
    """
    rows = db.scalars(
        select(models.ContentSafetyClassification)
        .where(models.ContentSafetyClassification.human_review_status == "pending")
        .order_by(models.ContentSafetyClassification.created_at)
        .limit(min(limit, 200))
    ).all()

    # The body, because a reviewer cannot review what they cannot see. The
    # queue used to return ids and a rating, which meant the only way to work
    # it was to look every item up by hand somewhere else - and a queue that is
    # tedious to work is a queue that does not get worked, which shows up as
    # teenagers seeing nothing rather than as a backlog anybody notices.
    posts = {
        p.id: p
        for p in db.scalars(
            select(models.Post).where(models.Post.id.in_([r.content_id for r in rows] or [""]))
        ).all()
    }
    comments = {
        c.id: c
        for c in db.scalars(
            select(models.Comment).where(
                models.Comment.id.in_([r.content_id for r in rows] or [""])
            )
        ).all()
    }
    total_pending = db.scalar(
        select(func.count()).select_from(models.ContentSafetyClassification).where(
            models.ContentSafetyClassification.human_review_status == "pending"
        )
    ) or 0

    # Media lives in its own table, so it is counted in one query rather than
    # read off the post. An earlier version of this guessed at a `media_ids`
    # column that does not exist and reported zero attachments on everything -
    # in a queue that exists *because* nothing can see into pictures, "0 media"
    # on every row is the most misleading value it could have shown.
    media_counts = dict(
        db.execute(
            select(models.PostMedia.post_id, func.count())
            .where(models.PostMedia.post_id.in_([r.content_id for r in rows] or [""]))
            .group_by(models.PostMedia.post_id)
        ).all()
    )

    def _content(r):
        item = posts.get(r.content_id) or comments.get(r.content_id)
        if item is None:
            return None, None
        return item.body, item.author_id

    items = []
    for r in rows:
        body, author_id = _content(r)
        items.append({
            "content_id": r.content_id,
            "content_kind": r.content_kind,
            "age_rating": r.age_rating,
            "classifier_source": r.classifier_source,
            "confidence": r.classifier_confidence,
            "exploitation_risk": r.exploitation_risk,
            "levels": {
                "sexual": r.sexual_content_level, "nudity": r.nudity_level,
                "violence": r.violence_level, "graphic": r.graphic_content_level,
                "drugs": r.drugs_level, "alcohol": r.alcohol_level,
                "gambling": r.gambling_level, "dangerous": r.dangerous_activity_level,
                "self_harm": r.self_harm_risk, "hate": r.hate_or_abuse_risk,
            },
            "body": body,
            "author_id": author_id,
            "media_count": media_counts.get(r.content_id, 0),
            "reports": db.scalar(
                select(func.count()).select_from(models.ContentReport).where(
                    models.ContentReport.content_id == r.content_id
                )
            ) or 0,
            "created_at": r.created_at,
        })

    return {"pending": total_pending, "shown": len(rows), "items": items}


@app.get("/admin/moderation/overview", tags=["admin"])
def moderation_overview(_: AdminUser, db: OrmSession = Depends(get_db)):
    """The real numbers, for a console that used to show invented ones.

    Every figure here is a count of rows. That sounds too obvious to say, but
    the admin page it replaces carried "4.2M items screened / day" and "96 open
    to humans" as hard-coded strings, which is worse than showing nothing: a
    fabricated queue depth is indistinguishable from an empty one right up
    until somebody trusts it.
    """
    since = datetime.now(timezone.utc) - timedelta(days=1)
    pending = db.scalar(
        select(func.count()).select_from(models.ContentSafetyClassification).where(
            models.ContentSafetyClassification.human_review_status == "pending"
        )
    ) or 0
    escalations = db.scalar(
        select(func.count()).select_from(models.ContentSafetyClassification).where(
            models.ContentSafetyClassification.exploitation_risk > 1
        )
    ) or 0
    classified = db.scalar(
        select(func.count()).select_from(models.ContentSafetyClassification)
    ) or 0
    reports_day = db.scalar(
        select(func.count()).select_from(models.ContentReport).where(
            models.ContentReport.created_at >= since
        )
    ) or 0
    reports_total = db.scalar(
        select(func.count()).select_from(models.ContentReport)
    ) or 0
    return {
        "classified_total": classified,
        "pending_review": pending,
        "child_safety_escalations": escalations,
        "reports_24h": reports_day,
        "reports_total": reports_total,
        "appeals": moderation.stats(db),
    }


@app.post("/admin/classification/{post_id}/review", tags=["admin"])
def review_classification(
    post_id: str, payload: ClassificationIn, admin: AdminUser, db: OrmSession = Depends(get_db)
):
    """A human settles a rating. Their answer outranks the classifier's."""
    post = db.get(models.Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    # Straight to the write, not through classify_post: a reviewer may re-rate
    # a rating that is already settled, which is exactly what that endpoint
    # refuses to an automatic caller.
    row = _apply_classification(db, post, payload)
    result = {"content_id": post_id, "age_rating": row.age_rating, "status": post.status}
    if row is not None:
        row.human_review_status = "confirmed"
        row.classifier_source = f"human:{admin.user_id}"
        row.classifier_confidence = 1.0
        post = db.get(models.Post, post_id)
        if post is not None and row.age_rating not in ("GENERAL", "TEEN_13_PLUS"):
            # A human restricting something is a decision like any other, and
            # recording it is what makes "an appeal is not decided by whoever
            # made the decision" mean anything: without this the rule guards a
            # path that cannot be reached, because every decision in the table
            # would be automatic.
            moderation.record_decision(
                db, subject_id=post.author_id, content_id=post_id, content_kind="post",
                action="human_review", age_rating=row.age_rating,
                decided_by=admin.user_id,
                appealable=row.exploitation_risk <= 1,
            )
        db.commit()
    return result


# Not /internal/classify/backfill: that path is swallowed by
# /internal/classify/{post_id}, which matched "backfill" as an id and
# then demanded a classification body.
@app.post("/internal/classification-backfill", tags=["internal"])
def backfill_classifications(limit: int = 500, db: OrmSession = Depends(get_db)):
    """Classify posts that predate the classifier.

    They are currently unrated, which means restricted - safe, and invisible to
    every minor. This releases the ones that can be released and queues the
    rest. Idempotent: a post that already has a row is skipped.
    """
    classified = select(models.ContentSafetyClassification.content_id)
    rows = db.scalars(
        select(models.Post).where(models.Post.id.not_in(classified)).limit(min(limit, 2000))
    ).all()

    # Comments too. They were never classified, so a minor currently sees every
    # one of them - the listing excludes only what is classified out, which for
    # an unclassified comment is nothing.
    unrated_comments = db.scalars(
        select(models.Comment).where(models.Comment.id.not_in(classified)).limit(min(limit, 2000))
    ).all()
    comment_count = 0
    for comment in unrated_comments:
        verdict = classifier.classify(
            body=comment.body or "",
            media_kinds=[],
            author_is_minor=_viewer_age(comment.author_id).is_minor,
        )
        row = models.ContentSafetyClassification(
            id=new_id("csc"), content_id=comment.id, content_kind="comment"
        )
        for key, value in verdict.as_payload().items():
            setattr(row, key, value)
        db.add(row)
        comment_count += 1

    done, blocked, queued = 0, 0, 0
    for post in rows:
        kinds = db.scalars(
            select(models.PostMedia.kind).where(models.PostMedia.post_id == post.id)
        ).all()
        # The author's age today, not at the time of writing. Their tier may
        # have changed, and the current one is the one that governs.
        result = _classify_and_store(db, post, list(kinds), _viewer_age(post.author_id).is_minor)
        done += 1
        blocked += 1 if result.block_publication else 0
        queued += 1 if result.human_review_status == "pending" else 0
    db.commit()
    return {
        "classified": done,
        "comments_classified": comment_count,
        "withheld": blocked,
        "queued_for_review": queued,
    }


@app.get("/posts/{post_id}", tags=["posts"])
def get_post(post_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    viewer = principal.user_id if principal else None
    post = _readable_post(db, post_id, viewer)
    prefs = _viewer_prefs(viewer)
    age = _viewer_age(viewer)
    # A direct link bypasses the feed entirely, so the same gate runs here.
    # 404 rather than 403: confirming that a post exists but is out of reach
    # tells somebody exactly which links are worth passing to a minor.
    if not agefilter.visible_to(db, age, post.id):
        _refuse_or_404(age)
    post.views_count += 1
    db.commit()
    return _apply_prefs(_post_out(post, db, viewer=viewer), prefs)


# --- link previews -------------------------------------------------------
#
# A shared post's address is read by crawlers that do not run the app, so its
# title, description and picture have to be in the HTML. Only what a signed-out
# visitor may see is put there - a preview is shown to whoever the link reaches,
# a child included - so everything is judged as for an anonymous viewer, by the
# same two checks the post's own page uses. Anything else gets the generic
# card, without the author's name or a word of the text.

OG_INDEX_URL = os.environ.get("OG_INDEX_URL", "http://web/index.html")
# The site's public address, for the absolute links a preview needs. Taken
# from where media is served (".../media"), because the gateway rewrites Host.
OG_SITE = os.environ.get("OG_SITE_URL") or (
    settings.MEDIA_PUBLIC_BASE.rsplit("/media", 1)[0]
    if settings.MEDIA_PUBLIC_BASE.startswith("http")
    else "https://kinjy.com"
)
_og_index_cache: tuple[float, str] | None = None
_OG_STRIP = re.compile(
    r'\s*<(?:meta\s+(?:property|name)="(?:og:[^"]*|twitter:[^"]*|description)"[^>]*|link\s+rel="canonical"[^>]*)>'
    r"|\s*<title>.*?</title>",
    re.IGNORECASE | re.DOTALL,
)


def _og_index() -> str | None:
    """The app's index.html, kept a minute: it names this release's bundle."""
    global _og_index_cache
    now = time.time()
    if _og_index_cache and now - _og_index_cache[0] < 60:
        return _og_index_cache[1]
    try:
        # Host: localhost, which nginx (server_name _) ignores and the Vite
        # dev server requires - it refuses an unknown host name.
        response = httpx.get(OG_INDEX_URL, timeout=5, headers={"Host": "localhost"})
        response.raise_for_status()
    except Exception as exc:
        log.warning("link preview could not read index.html: %s", exc)
        return _og_index_cache[1] if _og_index_cache else None
    _og_index_cache = (now, response.text)
    return response.text


def _og_post(db: OrmSession, post_id: str) -> models.Post | None:
    """The post if a signed-out visitor may see it, else None."""
    try:
        post = _readable_post(db, post_id, None)
    except HTTPException:
        return None
    if not agefilter.visible_to(db, _viewer_age(None), post.id):
        return None
    return post


def _og_text(post: models.Post) -> tuple[str | None, str]:
    """(headline, plain text) of a post, for a preview."""
    body = post.body or ""
    headline = None
    if post.format == "article":
        match = re.match(r"\s*<h2>(.*?)</h2>", body, re.IGNORECASE | re.DOTALL)
        if match:
            headline = html_module.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() or None
            body = body[match.end():]
    text = html_module.unescape(re.sub(r"<[^>]+>", " ", body))
    return headline, re.sub(r"\s+", " ", text).strip()


def _og_media(db: OrmSession, post: models.Post) -> models.PostMedia | None:
    return db.scalar(
        select(models.PostMedia)
        .where(models.PostMedia.post_id == post.id, models.PostMedia.kind.in_(("image", "video", "short")))
        .order_by(models.PostMedia.position)
    )


@app.get("/og/p/{post_id}", tags=["share"], include_in_schema=False)
def og_page(post_id: str, request: Request, db: OrmSession = Depends(get_db)):
    """The app's page, with this post's own title, description and picture."""
    index = _og_index()
    site = OG_SITE
    url = f"{site}/p/{post_id}"
    post = _og_post(db, post_id)
    image = f"{site}/og-image.png"
    if post is None:
        title = "A post on Kinjy"
        description = "Sign in to Kinjy to see this post."
    else:
        authors = _resolve_authors({post.author_id})
        name = (authors.get(post.author_id) or {}).get("display_name") or "A member"
        headline, text = _og_text(post)
        media = _og_media(db, post)
        title = headline or f"{name} on Kinjy"
        if not text:
            text = {
                "video": f"A video by {name}", "short": f"A video by {name}", "image": f"A photo by {name}",
            }.get(media.kind if media else "", f"A post by {name}")
        description = text if len(text) <= 200 else text[:197].rstrip() + "..."
        if media is not None:
            image = f"{site}/api/og/image/{post.id}"
        else:
            avatar = (authors.get(post.author_id) or {}).get("avatar_url")
            if avatar and avatar.startswith("http"):
                image = avatar
    esc = html_module.escape
    head = "\n".join([
        f"<title>{esc(title)} | Kinjy</title>",
        f'<meta name="description" content="{esc(description)}" />',
        f'<link rel="canonical" href="{esc(url)}" />',
        '<meta property="og:type" content="article" />',
        '<meta property="og:site_name" content="Kinjy" />',
        f'<meta property="og:title" content="{esc(title)}" />',
        f'<meta property="og:description" content="{esc(description)}" />',
        f'<meta property="og:url" content="{esc(url)}" />',
        f'<meta property="og:image" content="{esc(image)}" />',
        f'<meta property="og:image:alt" content="{esc(title)}" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        f'<meta name="twitter:title" content="{esc(title)}" />',
        f'<meta name="twitter:description" content="{esc(description)}" />',
        f'<meta name="twitter:image" content="{esc(image)}" />',
    ])
    if index is None:
        # The app could not be read: still answer the crawler, and send a
        # person to the app.
        page = f'<!doctype html><html><head><meta charset="utf-8" />{head}</head><body><a href="{esc(url)}">{esc(title)}</a></body></html>'
    else:
        page = _OG_STRIP.sub("", index).replace("</head>", f"{head}\n</head>", 1)
    return Response(content=page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


@app.get("/og/image/{post_id}", tags=["share"], include_in_schema=False)
def og_image(post_id: str, request: Request, db: OrmSession = Depends(get_db)):
    """The preview picture: the post's first picture, or its video's cover.

    A stable address (a crawler fetches it long after the page), re-checked on
    every request, and converted to a JPEG no wider than 1200 px - the format
    every preview reader takes. Anything not for a signed-out visitor, or not
    available, sends the generic Kinjy card instead.
    """
    site = OG_SITE
    fallback = RedirectResponse(f"{site}/og-image.png", status_code=302)
    post = _og_post(db, post_id)
    media = _og_media(db, post) if post else None
    if media is None:
        return fallback
    which = "poster=1" if media.kind in ("video", "short") else "p=1"
    source = f"{MEDIA_URL}/media/{media.media_id}?{mediasign.query_string(media.media_id, None)}&{which}"
    try:
        response = httpx.get(source, timeout=15)
        response.raise_for_status()
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(response.content)) as picture:
            picture = picture.convert("RGB")
            picture.thumbnail((1200, 1200))
            out = BytesIO()
            picture.save(out, "JPEG", quality=85, optimize=True)
    except Exception as exc:
        log.info("no preview picture for %s: %s", post_id, exc)
        return fallback
    return Response(
        content=out.getvalue(),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/posts/{post_id}/media", tags=["posts"])
def get_post_media(post_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """The full media list, ignoring data saver.

    Data saver withholds heavy URLs so nothing downloads on its own; this is how
    a member says "yes, load it anyway" for one post without turning the setting
    off. The age rule is *not* relaxed here — that one is not the viewer's to
    waive.
    """
    viewer_id = principal.user_id if principal else None
    post = _readable_post(db, post_id, viewer_id)
    prefs = _viewer_prefs(viewer_id)
    # The bytes themselves. Withholding the URL is the only protection that
    # actually works - a client told "do not display this" has already
    # downloaded it.
    viewer_age = _viewer_age(viewer_id)
    if not agefilter.visible_to(db, viewer_age, post.id):
        _refuse_or_404(viewer_age)
    rows = db.scalars(
        select(models.PostMedia)
        .where(models.PostMedia.post_id == post.id)
        .order_by(models.PostMedia.position)
    ).all()
    return {
        "post_id": post.id,
        # Signed here too: this route is reached by "load it anyway" after data
        # saver withheld the inline URL, and it is exactly the route somebody
        # would try if the feed's URLs stopped working unsigned.
        "media": [
            {
                "url": mediasign.sign_url(m.url, m.media_id, viewer_id),
                "poster_url": (
                    mediasign.sign_url(m.url, m.media_id, viewer_id) + "&poster=1"
                    if m.kind in ("video", "short")
                    else None
                ),
                # Lets an article place this file in its text by id.
                "media_id": m.media_id,
                "kind": m.kind,
                "alt_text": m.alt_text,
            }
            for m in rows
        ],
    }


@app.patch("/posts/{post_id}", tags=["posts"])
def edit_post(post_id: str, payload: PostIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    post = db.get(models.Post, post_id)
    if post is None or post.author_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Post not found")

    # A day to fix it, and then it stands.
    #
    # An edit with no limit means the thing people replied to can be replaced
    # by something else entirely, and every reply underneath becomes a reply to
    # words that were never said. A day is long enough for the typo and the
    # wrong link, and short enough that a conversation cannot be rewritten
    # under the people having it.
    #
    # Measured from created_at, not from the last edit: otherwise editing once
    # an hour keeps the window open forever.
    age = datetime.now(timezone.utc) - _aware(post.created_at)
    if age > EDIT_WINDOW:
        raise HTTPException(
            status_code=403,
            detail="A post can only be edited in the first 24 hours. Older posts stand as written.",
        )
    previous_body = post.body or ""
    post.body = payload.body
    post.topics = _merge_topics(payload.topics, payload.body)
    post.edited_at = datetime.now(timezone.utc)
    # An article's pictures and clips live in its text, so editing the text is
    # where they are added and taken out. Only when the client says so (it sent
    # `media`): a plain correction leaves the attachments exactly as they were.
    added_media: list[str] = []
    if post.format == "article" and "media" in payload.model_fields_set:
        added_media = _sync_article_media(db, post, previous_body, payload.media, principal.user_id)
    elif "media" in payload.model_fields_set:
        # Any other post: `media` is the list it should carry now, in order.
        added_media = _replace_post_media(db, post, payload.media, principal.user_id)
        if not (post.body or "").strip() and not added_media and not db.scalar(
            select(func.count()).select_from(models.PostMedia).where(models.PostMedia.post_id == post.id)
        ):
            raise HTTPException(status_code=400, detail="A post needs a body or media")
    # An edit of original content becomes "edited" — provenance must stay honest.
    if post.provenance == "original":
        post.provenance = "edited"
    # Judged again before the commit, like a new post: the age filter reads the
    # classification row, so an edit must never be served under the rating of
    # the text it replaced.
    verdict = _reclassify_after_edit(db, post, _viewer_age(principal.user_id).is_minor)
    db.commit()
    if added_media:
        # Like at creation: attached media stops being publicly fetchable.
        _restrict_attached_media(added_media)
    if verdict.block_publication:
        # The same deliberately vague refusal as create_post.
        raise HTTPException(
            status_code=403,
            detail="This post cannot be published. If you believe this is a mistake, contact support.",
        )
    # Signed for the author, who is looking at it - not for an anonymous
    # visitor, which is what a missing viewer means.
    return _post_out(post, db, viewer=principal.user_id)


# Pictures and clips one ordinary post may carry: the composer's own limit.
MAX_POST_MEDIA = 4
# Formats whose name follows what is attached; the others (article, short,
# carousel) say something else and are left as they are.
MEDIA_FORMATS = {"text", "image", "video"}


def _replace_post_media(db: OrmSession, post: models.Post, items: list[dict], author_id: str) -> list[str]:
    """Make a post carry exactly `items`, in that order.

    A file the post already carries is kept as it is (it was checked when it
    was attached). A new one is checked exactly as at creation - the author's
    own finished post media - so an edit cannot attach somebody else's file.
    One left out is detached. The format follows: a post that now carries a
    video is a video post, so "Video Only" finds it, and one with nothing left
    is text. Returns the ids attached, for the caller to restrict after the
    commit; the caller reclassifies, so a new picture waits for a human.
    """
    wanted: list[dict] = []
    seen: set[str] = set()
    for item in items:
        media_id = item.get("media_id") if isinstance(item, dict) else None
        if isinstance(media_id, str) and media_id and media_id not in seen:
            seen.add(media_id)
            wanted.append(item)
    if len(wanted) > MAX_POST_MEDIA:
        raise HTTPException(status_code=400, detail=f"A post can carry at most {MAX_POST_MEDIA} pictures or clips")

    rows = {r.media_id: r for r in db.scalars(select(models.PostMedia).where(models.PostMedia.post_id == post.id)).all()}
    new_items = [i for i in wanted if i["media_id"] not in rows]
    checked = {c["media_id"]: c for c in _verified_media(new_items, author_id)} if new_items else {}

    for media_id, row in rows.items():
        if media_id not in seen:
            db.delete(row)
    for position, item in enumerate(wanted):
        row = rows.get(item["media_id"])
        if row is not None:
            row.position = position
            continue
        c = checked[item["media_id"]]
        db.add(
            models.PostMedia(
                post_id=post.id,
                media_id=c["media_id"],
                url=c["url"],
                kind=c["kind"],
                alt_text=c.get("alt_text"),
                position=position,
                width=c.get("width"),
                height=c.get("height"),
                duration_seconds=c.get("duration_seconds"),
            )
        )
    if post.format in MEDIA_FORMATS:
        kinds = [(rows.get(i["media_id"]).kind if i["media_id"] in rows else checked[i["media_id"]]["kind"]) for i in wanted]
        post.format = "video" if "video" in kinds else "image" if kinds else "text"
    db.flush()
    return list(checked)


ARTICLE_MEDIA_RE = re.compile(r'data-media-id="(mda_[A-Za-z0-9]{6,60})"')
# A long article can carry a lot of pictures; a body naming hundreds of files
# is not an article.
MAX_ARTICLE_MEDIA = 30


def _article_media_ids(body: str) -> list[str]:
    return list(dict.fromkeys(ARTICLE_MEDIA_RE.findall(body or "")))


def _sync_article_media(
    db: OrmSession, post: models.Post, previous_body: str, items: list[dict], author_id: str
) -> list[str]:
    """Make an article's attachments match the pictures and clips in its text.

    - A file the text places and the post does not have yet is attached - and
      checked exactly as at creation (the author's own finished post media),
      so an edit cannot place somebody else's file.
    - A file that was in the text and has been taken out of it is detached.
    - A file attached to the post but never placed in the text (the grid under
      the post) is left alone: this edit is about the text.

    The caller reclassifies after this, which reads the attachments: a picture
    added by an edit waits for a human like one posted at creation. Returns
    the ids attached, so the caller can restrict them after the commit.
    """
    wanted = _article_media_ids(post.body)
    if len(wanted) > MAX_ARTICLE_MEDIA:
        raise HTTPException(status_code=400, detail=f"An article can carry at most {MAX_ARTICLE_MEDIA} pictures and clips")
    rows = list(db.scalars(select(models.PostMedia).where(models.PostMedia.post_id == post.id)).all())
    have = {r.media_id for r in rows}
    was_inline = set(_article_media_ids(previous_body))

    for row in rows:
        if row.media_id in was_inline and row.media_id not in wanted:
            db.delete(row)

    new_ids = [m for m in wanted if m not in have]
    if not new_ids:
        db.flush()
        return []
    offered = {i.get("media_id"): i for i in items if isinstance(i, dict)}
    missing = [m for m in new_ids if m not in offered]
    if missing:
        raise HTTPException(status_code=400, detail=NOT_YOUR_MEDIA)
    checked = _verified_media([offered[m] for m in new_ids], author_id)
    position = max((r.position for r in rows), default=-1) + 1
    for item in checked:
        db.add(
            models.PostMedia(
                post_id=post.id,
                media_id=item["media_id"],
                url=item["url"],
                kind=item["kind"],
                alt_text=item.get("alt_text"),
                position=position,
                width=item.get("width"),
                height=item.get("height"),
                duration_seconds=item.get("duration_seconds"),
            )
        )
        position += 1
    db.flush()
    return [item["media_id"] for item in checked]


def _media_to_discard(db: OrmSession, post: models.Post, removed_by: str, previous_status: str) -> list[str]:
    """The media of a post its author has just removed, that may go with it.

    The post row is only marked removed, so what could outlive it is the file.
    Deleting it is the author's intent, and leaving a copy at a third party after
    "delete" is not what anyone expects. But a file is also evidence, so it is
    kept when:

    - somebody else removed the post: that is moderation, not the author's choice;
    - the post was already withheld or hidden: the platform took it down, and
      whoever reviews or appeals that may need the original;
    - the post was reported, or was rated as exploitative or prohibited:
      whatever happens to it next may need the original;
    - another live post still shows the same asset.

    Called inside the delete request; the actual deletion runs after the response.
    """
    if removed_by != post.author_id or previous_status not in ("published", "draft"):
        return []
    reported = db.scalar(
        select(func.count()).select_from(models.ContentReport).where(models.ContentReport.content_id == post.id)
    )
    rating = db.scalar(
        select(models.ContentSafetyClassification).where(models.ContentSafetyClassification.content_id == post.id)
    )
    # Not human_review_status: every post with media is "pending" (the classifier
    # cannot see inside a file, source heuristic:media_opaque), so it would keep
    # everything and delete nothing. A report or a rating is a signal; that is not.
    if reported or (rating is not None and (rating.exploitation_risk > 0 or rating.age_rating == "PROHIBITED")):
        return []
    media_ids = list(db.scalars(select(models.PostMedia.media_id).where(models.PostMedia.post_id == post.id)).all())
    if not media_ids:
        return []
    still_shown = set(
        db.scalars(
            select(models.PostMedia.media_id)
            .join(models.Post, models.Post.id == models.PostMedia.post_id)
            .where(
                models.PostMedia.media_id.in_(media_ids),
                models.PostMedia.post_id != post.id,
                models.Post.status != "removed",
            )
        ).all()
    )
    return [m for m in dict.fromkeys(media_ids) if m and m not in still_shown]


def _discard_post_media(author_id: str, media_ids: list[str]) -> None:
    """Ask media-service to delete files whose post is gone. Best effort, logged.

    A failure leaves an orphaned file, which is a tidiness problem and is
    recoverable; it must never turn a member's "delete" into an error.
    """
    for media_id in media_ids:
        try:
            httpx.post(
                f"{MEDIA_URL}/internal/media/{media_id}/discard-post-media",
                json={"owner_id": author_id},
                timeout=30,
            ).raise_for_status()
        except Exception as exc:
            log.error("could not discard media %s of a removed post: %s", media_id, exc)


@app.delete("/posts/{post_id}", status_code=204, tags=["posts"])
def delete_post(
    post_id: str,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    post = db.get(models.Post, post_id)
    if post is None or (post.author_id != principal.user_id and not principal.is_admin):
        raise HTTPException(status_code=404, detail="Post not found")
    previous_status = post.status
    post.status = "removed"
    discard = _media_to_discard(db, post, removed_by=principal.user_id, previous_status=previous_status)
    author_id = post.author_id
    db.commit()
    if discard:
        background.add_task(_discard_post_media, author_id, discard)


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Audience:
    """Whose restricted posts a viewer may read, as the other services answer it.

    ``following`` opens followers-only posts, ``circles`` opens posts shared to
    those circles, and ``communities`` opens the posts written inside groups
    this member has joined. Empty for a visitor — and empty when the service
    that owns the answer cannot be reached, so an outage hides restricted posts
    instead of showing them.
    """

    viewer: str | None = None
    following: frozenset[str] = frozenset()
    circles: frozenset[str] = frozenset()
    communities: frozenset[str] = frozenset()


def _viewer_audience(viewer: str | None) -> Audience:
    """Asked once per request, on every surface that returns posts.

    Not cached: removing someone from a circle, unfollowing or blocking has to
    close the door on their next request, not a minute later.
    """
    if not viewer:
        return Audience()
    following: frozenset[str] = frozenset()
    circles: frozenset[str] = frozenset()
    try:
        response = httpx.get(f"{USER_URL}/internal/viewer-audience/{viewer}", timeout=4)
        response.raise_for_status()
        data = response.json()
        following = frozenset(data.get("following", []))
        circles = frozenset(data.get("circles", []))
    except Exception as exc:
        log.warning("audience lookup failed for %s: %s", viewer, exc)
    # Asked separately, and allowed to fail on its own: a community-service
    # outage should cost this member their group posts, not their whole feed.
    # Either failure narrows what is shown; neither widens it.
    return Audience(viewer, following, circles, _viewer_communities(viewer))


def _viewer_communities(viewer: str) -> frozenset[str]:
    """The communities this member belongs to, from the service that owns them.

    Not cached and not copied into this service's tables. A membership list
    that lags is somebody still reading a group they were removed from, which
    is the failure a group must not have.
    """
    try:
        response = httpx.get(f"{COMMUNITY_URL}/internal/member-communities/{viewer}", timeout=4)
        response.raise_for_status()
        return frozenset(response.json().get("communities", []))
    except Exception as exc:
        log.warning("community lookup failed for %s: %s", viewer, exc)
        return frozenset()


def _can_see(post: models.Post, audience: Audience) -> bool:
    """Whether this viewer may read this post, by its audience.

    The single-post form of :func:`_audience_clause`. The two are one rule
    spelled twice — once for Python, once for SQL — and sit side by side so
    they cannot drift.

    A community post is readable by the members of that community, which is
    what makes a community a group rather than a mailing list nobody receives:
    before this, such a post was addressed to a community and then visible to
    its author alone.
    """
    if post.visibility == "public":
        return True
    if audience.viewer is None:
        return False
    if post.author_id == audience.viewer:
        return True
    if post.visibility == "followers":
        return post.author_id in audience.following
    if post.visibility == "circle":
        return post.circle_id in audience.circles
    if post.visibility == "community":
        return post.community_id in audience.communities
    return False


def _audience_clause(audience: Audience):
    """The SQL form of :func:`_can_see`."""
    if audience.viewer is None:
        return models.Post.visibility == "public"
    clauses = [models.Post.visibility == "public", models.Post.author_id == audience.viewer]
    if audience.following:
        clauses.append(
            and_(models.Post.visibility == "followers", models.Post.author_id.in_(audience.following))
        )
    if audience.circles:
        clauses.append(and_(models.Post.visibility == "circle", models.Post.circle_id.in_(audience.circles)))
    if audience.communities:
        clauses.append(
            and_(models.Post.visibility == "community",
                 models.Post.community_id.in_(audience.communities))
        )
    return or_(*clauses)


def _require_membership(author_id: str, community_id: str) -> None:
    """A post may only be addressed to a community its author has joined.

    Asked of community-service, which owns membership. Fails closed for the
    same reason the circle check does: without this, `community_id` was a free
    text field and anybody could write into any group - including a private one
    they had been refused, or a secret one whose id they guessed.

    A pending request is not membership. That distinction is the only thing a
    private community has.
    """
    try:
        response = httpx.get(
            f"{COMMUNITY_URL}/internal/membership/{community_id}/{author_id}", timeout=4)
    except Exception as exc:
        log.warning("membership lookup failed for %s in %s: %s", author_id, community_id, exc)
        raise HTTPException(
            status_code=503, detail="Could not check that community right now. Try again.")
    if response.status_code != 200:
        raise HTTPException(status_code=404, detail="Community not found")
    row = response.json()
    if not row.get("member"):
        if row.get("status") == "pending":
            raise HTTPException(
                status_code=403, detail="Your request to join is still waiting to be answered")
        if row.get("status") == "banned":
            raise HTTPException(status_code=403, detail="You cannot post in this community")
        # Not a member and never asked. Says "join first" rather than "no such
        # community", because a public community is not a secret.
        raise HTTPException(status_code=403, detail="Join this community before posting in it")


def _community_summary(community_id: str) -> dict:
    try:
        response = httpx.get(f"{COMMUNITY_URL}/internal/communities/{community_id}", timeout=4)
    except Exception as exc:
        log.warning("community lookup failed for %s: %s", community_id, exc)
        raise HTTPException(
            status_code=503, detail="Could not reach that community right now. Try again.")
    if response.status_code != 200:
        raise HTTPException(status_code=404, detail="Community not found")
    return response.json()


def _require_own_circle(author_id: str, circle_id: str | None) -> None:
    """A post may only be shared to a circle its author owns.

    Asked of user-service, which owns circles. Fails closed: if the owner
    cannot be confirmed, the post is not published into a circle at all.
    """
    if not circle_id:
        raise HTTPException(status_code=400, detail="A circle post needs circle_id")
    try:
        response = httpx.get(f"{USER_URL}/internal/circles/{circle_id}/owner", timeout=4)
    except Exception as exc:
        log.warning("circle owner lookup failed for %s: %s", circle_id, exc)
        raise HTTPException(status_code=503, detail="Could not check that circle right now. Try again.")
    if response.status_code != 200 or response.json().get("owner_id") != author_id:
        raise HTTPException(status_code=404, detail="Circle not found")


def _readable_post(db: OrmSession, post_id: str, viewer: str | None) -> models.Post:
    """A post this viewer may read, or a 404.

    Every route that takes a post id goes through here — opening a link,
    loading its media, reacting, commenting, sharing. A circle post must not be
    reachable by a side door just because the feed hides it: an id copied from
    a screenshot is still an id. 404 rather than 403, so the answer does not
    confirm that a restricted post exists.
    """
    post = db.get(models.Post, post_id)
    if post is None or post.status in ("removed", "draft"):
        raise HTTPException(status_code=404, detail="Post not found")
    if not _can_see(post, _viewer_audience(viewer)):
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def _visible_posts(principal, age, audience: Audience):
    """The base feed query: published, age-appropriate, and audience-allowed.

    Factored out so the shorts reel cannot drift from the feed's rules. A second
    hand-written query is how a post a minor must not see ends up visible on one
    surface and hidden on another.

    ``age`` is the authoritative profile from the identity record, never the
    viewer's own settings. ``audience`` says which followers-only and circle
    posts are this viewer's to read: until it existed, every signed-in member
    could read every followers-only post on the platform, and circle posts
    reached nobody but their author.
    """
    stmt = select(models.Post).where(models.Post.status == "published")
    # Age eligibility enters the SQL here, before ranking and before paging,
    # so restricted rows are never candidates in the first place.
    stmt = agefilter.restrict_query(stmt, age)
    return stmt.where(_audience_clause(audience))


@app.get("/feed/community/{community_id}", tags=["feed"])
def community_feed(
    community_id: str,
    principal: MaybeUser,
    limit: int = 20,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    """What has been posted inside one community.

    Lives here rather than in community-service because posts live here, and
    under /feed because the gateway sends /api/communities to the other
    service. It is the surface a group needs and did not have: posts addressed
    to a community were excluded from every feed and listed by nothing, so they
    went in and were seen by their author alone.

    Who may read it follows the kind of community, which is the promise its
    members joined under:

    * **public** - anyone, signed in or not. That is what public means, and a
      public group nobody can read before joining cannot be judged worth
      joining.
    * **private, secret, paid** - active members only. A pending request is not
      membership, which is the one thing a private community has.

    Age filtering is applied exactly as on every other surface, before paging.
    A community is not a way around it.
    """
    viewer = principal.user_id if principal else None
    community = _community_summary(community_id)

    if community["kind"] != "public":
        if viewer is None:
            # 404, not 403: for a secret community, "you may not read this"
            # confirms it exists to whoever guessed the id.
            raise HTTPException(status_code=404, detail="Community not found")
        if community_id not in _viewer_communities(viewer):
            raise HTTPException(status_code=404, detail="Community not found")

    prefs = _viewer_prefs(viewer)
    age = _viewer_age(viewer)
    stmt = select(models.Post).where(
        models.Post.community_id == community_id,
        models.Post.visibility == "community",
        models.Post.status == "published",
    )
    stmt = agefilter.restrict_query(stmt, age)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(models.Post.created_at.desc()).limit(min(limit, 50)).offset(offset)
    ).all()
    authors = _resolve_authors(_author_ids(db, list(rows)))
    reposted = _reposted_by(db, viewer, list(rows))
    items = [
        _apply_prefs(_post_out(p, db, viewer=viewer, authors=authors, reposted=reposted), prefs)
        for p in rows
    ]
    return {
        "community": {"id": community["id"], "name": community["name"],
                      "slug": community["slug"], "kind": community["kind"],
                      "members_count": community["members_count"]},
        "total": total,
        "items": agefilter.filter_items(db, age, items),
    }


class ReadableIn(BaseModel):
    viewer: str | None = None
    post_ids: list[str] = Field(default_factory=list, max_length=200)


@app.post("/internal/readable-posts", tags=["internal"])
def readable_posts(payload: ReadableIn, db: OrmSession = Depends(get_db)):
    """Which of these posts this member may read, by audience and by age.

    For the realtime hub. A ``post:<id>`` topic carries who commented and when;
    without this check, anyone holding a circle post's id — a member since
    removed, for one — could keep listening to it. One query for the whole
    batch, the same rule as every other read.
    """
    ids = list(dict.fromkeys(i for i in payload.post_ids if i))
    if not ids:
        return {"post_ids": []}
    stmt = select(models.Post.id).where(
        models.Post.id.in_(ids),
        models.Post.status == "published",
        _audience_clause(_viewer_audience(payload.viewer)),
    )
    age = _viewer_age(payload.viewer)
    stmt = agefilter.restrict_query(stmt, age)
    # The SQL filter is the coarse one; the engine that decides whether the post
    # opens has the last word, or a teenager could listen to a post they cannot read.
    kept = agefilter.filter_items(db, age, [{"id": post_id} for post_id in db.scalars(stmt).all()])
    return {"post_ids": [item["id"] for item in kept]}


@app.get("/shorts", tags=["shorts"])
def shorts(
    principal: MaybeUser,
    limit: int = Query(default=12, le=40),
    offset: int = 0,
    author: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """The vertical reel.

    Only posts the author published *as* a short — a landscape clip dropped into
    the feed is not silently promoted into a full-screen reel it was never
    framed for.

    Data saver is deliberately **not** applied here the way it is on the feed.
    A reel with every clip withheld is not a reel; the surface exists to play
    video, and asking for it is consent to load it. The autoplay preference is
    still honoured, so a member on a metered connection can open a short and
    have it wait for a tap.
    """
    viewer = principal.user_id if principal else None
    prefs = _viewer_prefs(viewer)
    age = _viewer_age(viewer)

    stmt = _visible_posts(principal, age, _viewer_audience(viewer)).where(models.Post.format == "short")
    if author:
        stmt = stmt.where(models.Post.author_id == author)

    ctx = _context(db, principal)
    if ctx.muted_authors:
        stmt = stmt.where(models.Post.author_id.not_in(ctx.muted_authors))

    rows = db.scalars(
        stmt.order_by(models.Post.created_at.desc()).limit(limit).offset(offset)
    ).all()
    authors = _resolve_authors(_author_ids(db, list(rows)))
    reposted = _reposted_by(db, viewer, list(rows))

    items = [_post_out(p, db, viewer=viewer, authors=authors, reposted=reposted) for p in rows]
    for item in items:
        if not prefs.get("autoplay_media", True):
            item["autoplay"] = False

    return {
        "items": items,
        "has_more": len(rows) == limit,
        "applied_settings": {
            # The tier, so a client can explain why a surface looks the way it
            # does. It is a readout, never an input.
            "age_tier": agefilter.tier_of(age),
            "autoplay_media": prefs.get("autoplay_media", True),
            "degraded": prefs.get("degraded", False) or age.degraded,
        },
    }


@app.get("/feed", tags=["feed"])
def feed(
    principal: MaybeUser,
    mode: str = Query(default="following"),
    algorithm_id: str | None = None,
    country: str | None = None,
    city: str | None = None,
    topic: str | None = None,
    limit: int = Query(default=25, le=100),
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    """One endpoint, explicit modes.

    ``following`` is strictly reverse-chronological and never ranked — that is
    the promise the blueprint makes, and running it through the ranker would
    quietly break it.
    """
    if mode not in FEED_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown feed mode. Available: {', '.join(FEED_MODES)}")

    viewer = principal.user_id if principal else None
    ctx = _context(db, principal)
    prefs = _viewer_prefs(principal.user_id if principal else None)
    age = _viewer_age(principal.user_id if principal else None)

    # Shared with the shorts reel: published, age-appropriate, audience-allowed.
    # The age rule is applied in the query rather than after - a post a child
    # must not see should never be selected, never serialised and never sent.
    audience = _viewer_audience(viewer)
    stmt = _visible_posts(principal, age, audience)

    # Who the viewer follows came with the rest of their graph in _context().
    # It used to be fetched here from /users/me/following with an empty bearer
    # token, which always failed silently: the Following feed was empty for
    # everybody and the ranker's affinity term never applied.
    following = ctx.following

    # An algorithm does two things: it *filters* (Video Only keeps videos) and
    # it *ranks*. The date-ordered modes refuse the ranking by design, but they
    # used to refuse the filter with it - so a member who chose Video Only and
    # read New got every text and photo post, with a note saying the choice was
    # ignored. The filter now applies in every mode; only the order differs.
    picked = db.get(models.Algorithm, algorithm_id) if algorithm_id else None
    if mode in ("following", "new") and picked is not None:
        stmt = _algorithm_filter(stmt, picked)
    unranked_algorithm = picked.id if picked is not None and _filters(picked) else "chronological"

    if mode == "following":
        if principal is not None and not ctx.graph_known:
            # Saying "you follow nobody" would be a lie the member cannot see
            # through; an error they can retry is the honest answer.
            raise HTTPException(status_code=503, detail="Your feed could not be loaded right now. Please try again.")
        # Your own posts belong in your own feed. They were excluded because the
        # audience was exactly the set of people you follow, and nobody follows
        # themselves - so a member could publish something, reload, and find the
        # feed empty, which is what this looked like from the outside: posting
        # into a void.
        audience = set(following) | ({viewer} if viewer else set())
        if not audience:
            return {"mode": mode, "algorithm": unranked_algorithm, "items": [],
                    "age_tier": agefilter.tier_of(age), "degraded": age.degraded,
                    "empty_reason": "not_following_anyone"}
        rows = db.scalars(
            stmt.where(models.Post.author_id.in_(audience))
            .order_by(models.Post.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        if not rows and not following:
            # Still empty, and the reason is the one the member can act on.
            return {"mode": mode, "algorithm": unranked_algorithm, "items": [],
                    "age_tier": agefilter.tier_of(age), "degraded": age.degraded,
                    "empty_reason": "not_following_anyone"}
        authors = _resolve_authors(_author_ids(db, list(rows)))
        reposted = _reposted_by(db, viewer, list(rows))
        actors = _known_actors(db, viewer, following, list(rows))
        return {
            "mode": mode,
            "algorithm": unranked_algorithm,
            "filtered": _filters(picked) if picked is not None else None,
            "ranked": False,
            "age_tier": agefilter.tier_of(age),
            "degraded": age.degraded,
            # A full page means there is probably another; the next request
            # settles it by coming back empty. Cheaper than counting the table
            # on every scroll.
            "has_more": len(rows) == limit,
            "items": [
                _with_actors(
                    _apply_prefs(_post_out(p, db, viewer=viewer, authors=authors, reposted=reposted), prefs),
                    actors,
                )
                for p in rows
            ],
        }

    if mode == "new":
        rows = db.scalars(stmt.order_by(models.Post.created_at.desc()).limit(limit).offset(offset)).all()
        authors = _resolve_authors(_author_ids(db, list(rows)))
        reposted = _reposted_by(db, viewer, list(rows))
        actors = _known_actors(db, viewer, following, list(rows))
        return {
            "mode": mode,
            "algorithm": unranked_algorithm,
            "filtered": _filters(picked) if picked is not None else None,
            "ranked": False,
            "age_tier": agefilter.tier_of(age),
            "degraded": age.degraded,
            # A full page means there is probably another; the next request
            # settles it by coming back empty. Cheaper than counting the table
            # on every scroll.
            "has_more": len(rows) == limit,
            "items": [
                _with_actors(
                    _apply_prefs(_post_out(p, db, viewer=viewer, authors=authors, reposted=reposted), prefs),
                    actors,
                )
                for p in rows
            ],
        }

    if mode == "local" and city:
        stmt = stmt.where(func.lower(models.Post.city) == city.lower())
    elif mode == "country" and country:
        stmt = stmt.where(models.Post.country == country.upper())
    elif mode == "topics" and topic:
        stmt = stmt.where(models.Post.topics.like(f"%{topic.lower()}%"))
    elif mode == "circles":
        if principal is None:
            raise HTTPException(status_code=401, detail="Sign in to see your circles")
        # The base query already narrowed circle posts to the circles that reach
        # this viewer, plus their own; this keeps only those.
        stmt = stmt.where(models.Post.visibility == "circle")

    chosen_id = algorithm_id or {
        "for_you": "friends_first",
        "trending": "entertainment",
        "local": "local_news",
        "global": "global_discovery",
    }.get(mode, "chronological")

    algorithm = db.get(models.Algorithm, chosen_id)
    if algorithm is None:
        raise HTTPException(status_code=404, detail=f"Unknown algorithm '{chosen_id}'")

    ctx.country = country
    ctx.city = city

    # Filtered in the query too, not only by the ranker: the window below is
    # the 500 newest posts, and when few of them are videos a Video Only feed
    # ran dry while older videos sat outside the window.
    stmt = _algorithm_filter(stmt, algorithm)

    # Rank a bounded candidate window rather than the whole table.
    candidates = db.scalars(stmt.order_by(models.Post.created_at.desc()).limit(500)).all()
    scored = ranking.rank(list(candidates), algorithm, ctx)
    page = scored[offset : offset + limit]
    ranked_authors = _resolve_authors(_author_ids(db, [item.post for item in page]))
    reposted = _reposted_by(db, viewer, [item.post for item in page])
    actors = _known_actors(db, viewer, following, [item.post for item in page])

    return {
        "mode": mode,
        "algorithm": algorithm.id,
        "algorithm_name": algorithm.name,
        "ranked": True,
        "total_candidates": len(scored),
        # Top level, as in every mode: the age lookup failed, so this feed was
        # filtered as for a minor and may be missing posts the member can see.
        "degraded": age.degraded,
        "applied_settings": {
            "age_tier": agefilter.tier_of(age),
            "data_saver": bool(prefs.get("data_saver")),
            "degraded": bool(prefs.get("degraded")) or age.degraded,
        },
        # Exact here: the whole candidate pool was scored, so we know.
        "has_more": offset + limit < len(scored),
        "items": [
            _with_actors(
                _apply_prefs(
                    _post_out(item.post, db, why=item.why(), viewer=viewer, authors=ranked_authors, reposted=reposted),
                    prefs,
                ),
                actors,
            )
            for item in page
        ],
    }


def _filters(algorithm: models.Algorithm) -> dict | None:
    """What an algorithm keeps, said plainly, or None when it keeps everything."""
    formats, topics = ranking.wanted_formats(algorithm), ranking.wanted_topics(algorithm)
    if not formats and not topics:
        return None
    return {"name": algorithm.name, "formats": sorted(formats), "topics": sorted(topics)}


def _algorithm_filter(stmt, algorithm: models.Algorithm):
    """An algorithm's format and topic filters, pushed into the query.

    Topics are stored as a comma list that may carry spaces; it is compared
    whole-word (",video," inside ",tech,video,") so "art" does not match
    "party".
    """
    formats = ranking.wanted_formats(algorithm)
    if formats:
        stmt = stmt.where(func.lower(models.Post.format).in_(formats))
    topics = ranking.wanted_topics(algorithm)
    if topics:
        padded = func.concat(",", func.replace(func.lower(func.coalesce(models.Post.topics, "")), " ", ""), ",")
        stmt = stmt.where(or_(*[padded.like(f"%,{t.replace(' ', '')},%") for t in topics]))
    return stmt


@app.get("/feed/modes", tags=["feed"])
def feed_modes():
    return {
        "modes": [
            {"id": "following", "label": "Following", "ranked": False, "description": "Strict reverse-chronological."},
            {"id": "for_you", "label": "For You", "ranked": True, "description": "Personalised by your chosen algorithm."},
            {"id": "circles", "label": "Circles", "ranked": True, "description": "Posts shared to your circles."},
            {"id": "friends", "label": "Friends", "ranked": True, "description": "Mutual connections."},
            {"id": "local", "label": "Local", "ranked": True, "description": "Your city and neighborhood."},
            {"id": "country", "label": "Country", "ranked": True, "description": "Your country."},
            {"id": "global", "label": "Global", "ranked": True, "description": "Beyond your country."},
            {"id": "topics", "label": "Topics", "ranked": True, "description": "A single topic."},
            {"id": "trending", "label": "Trending", "ranked": True, "description": "Gaining traction now."},
            {"id": "new", "label": "New", "ranked": False, "description": "Everything, newest first."},
        ]
    }


RANKED_MODES = {"for_you", "friends", "trending", "circles"}


def _inclusion_reasons(post: models.Post, mode: str, viewer: str | None, db: OrmSession) -> list[dict]:
    """Why this post is in the feed at all, before any ranking.

    This is the question members actually ask. A score alone does not answer it:
    on an unranked mode nothing was scored, and even on a ranked one the first
    thing that matters is which rule *selected* the post.
    """
    reasons: list[dict] = []

    if post.author_id == viewer:
        reasons.append({"kind": "own", "label": "You wrote it."})

    if mode == "following":
        reasons.append({"kind": "following", "label": "You follow the author."})
    elif mode == "local" and post.city:
        reasons.append({"kind": "place", "label": f"Posted in {post.city}."})
    elif mode == "country" and post.country:
        reasons.append({"kind": "place", "label": f"Posted from {post.country}."})
    elif mode == "topics" and post.topics:
        reasons.append({"kind": "topic", "label": f"Tagged {', '.join('#' + t for t in post.topics.split(',')[:3])}."})
    elif mode == "circles" and post.circle_id:
        reasons.append({
            "kind": "circle",
            "label": "You shared it to one of your circles." if post.author_id == viewer
            else "The author shared it with a circle you are in.",
        })
    elif mode == "new":
        reasons.append({"kind": "recent", "label": "It is one of the most recent posts on Kinjy."})
    elif mode == "global":
        reasons.append({"kind": "public", "label": "It is public, and you are browsing everything."})
    elif mode == "trending":
        reasons.append({"kind": "trending", "label": "It is getting engagement right now."})
    elif mode == "for_you":
        reasons.append({"kind": "ranked", "label": "Your algorithm placed it here."})

    if post.visibility == "public":
        reasons.append({"kind": "visibility", "label": "The author made it public."})
    elif post.visibility == "followers":
        reasons.append({"kind": "visibility", "label": "The author shared it with their followers."})
    elif post.visibility == "circle" and post.author_id != viewer:
        reasons.append({"kind": "visibility", "label": "The author shared it with a circle you are in."})

    return reasons


@app.get("/feed/why/{post_id}", tags=["feed"])
def why_am_i_seeing_this(
    post_id: str,
    principal: MaybeUser,
    algorithm_id: str = "friends_first",
    mode: str = "new",
    db: OrmSession = Depends(get_db),
):
    """Why this post is in this feed, for this viewer.

    Two separate answers, because conflating them misleads: *selection* (which
    feed mode picked it up) and, only when the mode actually ranks, *ordering*
    (the score and its components). Reporting "ranked by chronological, score
    0.46" on an unranked feed described something that never happened.
    """
    viewer = principal.user_id if principal else None
    post = _readable_post(db, post_id, viewer)
    ranked = mode in RANKED_MODES
    reasons = _inclusion_reasons(post, mode, viewer, db)

    payload: dict = {
        "post_id": post_id,
        "mode": mode,
        "ranked": ranked,
        "reasons": reasons,
        "actions": ["show_less_like_this", "mute_author", "mute_topic", "change_algorithm"],
    }

    if not ranked:
        payload["explanation"] = "This feed is in date order — nothing was ranked or personalised."
        payload["factors"] = []
        return payload

    algorithm = db.get(models.Algorithm, algorithm_id)
    if algorithm is None:
        # A missing algorithm must not blank the whole answer: the selection
        # reasons above are still true and still worth showing.
        payload["explanation"] = f"Unknown algorithm '{algorithm_id}', so ordering cannot be explained."
        payload["factors"] = []
        return payload

    scored = ranking.score_post(post, algorithm, _context(db, principal))
    payload.update(
        {
            "algorithm": algorithm.id,
            "algorithm_name": algorithm.name,
            "score": round(scored.score, 4),
            "factors": scored.why(),
            "explanation": f"Ordered by “{algorithm.name}”, the algorithm you chose.",
        }
    )
    return payload


@app.post("/feed/signals", status_code=201, tags=["feed"])
def add_signal(payload: SignalIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """"Show less like this" — recorded and honoured on the next feed build."""
    weight = 1.0 if payload.kind == "more_like_this" else -1.0
    target_id = payload.target_id
    if payload.kind == "less_like_this" and payload.target_type == "post":
        post = db.get(models.Post, target_id)
        if post is None:
            raise HTTPException(status_code=404, detail="Post not found")
        # Penalise the author and the topics, not the (already-seen) post.
        db.add(
            models.FeedSignal(
                user_id=principal.user_id,
                kind=payload.kind,
                target_type="author",
                target_id=post.author_id,
                weight=weight,
            )
        )
        for topic in (post.topics or "").split(","):
            if topic.strip():
                db.add(
                    models.FeedSignal(
                        user_id=principal.user_id,
                        kind=payload.kind,
                        target_type="topic",
                        target_id=topic.strip().lower(),
                        weight=weight / 2,
                    )
                )
    else:
        db.add(
            models.FeedSignal(
                user_id=principal.user_id,
                kind=payload.kind,
                target_type=payload.target_type,
                target_id=target_id,
                weight=weight,
            )
        )
    db.commit()
    return {"recorded": True, "applies_from": "next feed request"}


# ---------------------------------------------------------------------------
# Algorithm marketplace
# ---------------------------------------------------------------------------

@app.get("/algorithms", tags=["algorithms"])
def list_algorithms(db: OrmSession = Depends(get_db)):
    rows = db.scalars(select(models.Algorithm).order_by(models.Algorithm.builtin.desc(), models.Algorithm.name)).all()
    return {
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "builtin": r.builtin,
                "author_id": r.author_id,
                "installs": r.installs,
                "weights": {
                    "recency": r.weight_recency,
                    "affinity": r.weight_affinity,
                    "engagement": r.weight_engagement,
                    "locality": r.weight_locality,
                    "family": r.weight_family,
                    "new_creator": r.weight_new_creator,
                },
                "filters": {"topics": r.topic_filter, "formats": r.format_filter},
            }
            for r in rows
        ]
    }


class AlgorithmIn(BaseModel):
    id: str = Field(min_length=3, max_length=60, pattern="^[a-z0-9_]+$")
    name: str
    description: str = ""
    weight_recency: float = 1.0
    weight_affinity: float = 0.0
    weight_engagement: float = 0.0
    weight_locality: float = 0.0
    weight_family: float = 0.0
    weight_new_creator: float = 0.0
    topic_filter: str | None = None
    format_filter: str | None = None


@app.post("/algorithms", status_code=201, tags=["algorithms"])
def publish_algorithm(payload: AlgorithmIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Developers publish feed algorithms (blueprint §2)."""
    if db.get(models.Algorithm, payload.id):
        raise HTTPException(status_code=409, detail="That algorithm id is taken")
    algorithm = models.Algorithm(author_id=principal.user_id, builtin=False, **payload.model_dump())
    db.add(algorithm)
    db.commit()
    return {"id": algorithm.id, "published": True}


# ---------------------------------------------------------------------------
# Reactions & comments
# ---------------------------------------------------------------------------


# One reaction per member per post: picking a new one replaces the old, the way
# people expect. `likes_count` stays the total across every kind, so existing
# ranking signals keep working without a migration.
REACTION_KINDS = ("like", "celebrate", "support", "insightful", "love")

# How each reaction reads in a sentence about somebody doing it.
REACTION_VERBS = {
    "like": "liked",
    "celebrate": "celebrated",
    "support": "supported",
    "insightful": "found something insightful in",
    "love": "loved",
}


def _notify_reacted(db: OrmSession, post: models.Post, actor_id: str, kind: str) -> None:
    """Tell an author that somebody reacted to what they wrote, and who.

    The reaction was silent: a post could be liked by twenty people and its
    author would only find out by going back and counting. Naming the person
    is the whole content of the notification - "somebody reacted" is a number,
    "Kofi Mbeki liked your post" is news about a person.

    Reacting to your own post is not news, and a reaction taken back is not
    either, so neither is sent. Best-effort like the rest of the notifications
    here: the reaction is already committed.
    """
    if post.author_id == actor_id:
        return
    verb = REACTION_VERBS.get(kind, "reacted to")
    try:
        notify.notify(
            post.author_id,
            kind="reaction",
            title=f"{_name_of(actor_id)} {verb} your post",
            body=_excerpt(post.body or "")[:140] or None,
            link=f"/hub?post={post.id}",
        )
    except Exception as exc:
        log.warning("could not notify the author of a reaction: %s", exc)


class ReactIn(BaseModel):
    kind: str = Field(default="like")


def _reaction_summary(db: OrmSession, post_id: str, viewer: str | None) -> dict:
    rows = db.execute(
        select(models.Reaction.kind, func.count())
        .where(models.Reaction.post_id == post_id)
        .group_by(models.Reaction.kind)
    ).all()
    mine = None
    if viewer:
        mine = db.scalar(
            select(models.Reaction.kind).where(
                models.Reaction.post_id == post_id, models.Reaction.user_id == viewer
            )
        )
    return {
        "counts": {kind: count for kind, count in rows},
        "total": sum(count for _, count in rows),
        "mine": mine,
    }


@app.post("/posts/{post_id}/react", tags=["engagement"])
def react(post_id: str, payload: ReactIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if payload.kind not in REACTION_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown reaction. Available: {', '.join(REACTION_KINDS)}")

    post = _readable_post(db, post_id, principal.user_id)

    existing = db.scalar(
        select(models.Reaction).where(
            models.Reaction.post_id == post_id, models.Reaction.user_id == principal.user_id
        )
    )

    if existing and existing.kind == payload.kind:
        # Tapping the same reaction again clears it.
        db.delete(existing)
        post.likes_count = max(0, post.likes_count - 1)
    elif existing:
        existing.kind = payload.kind  # switch, total unchanged
    else:
        db.add(models.Reaction(post_id=post_id, user_id=principal.user_id, kind=payload.kind))
        post.likes_count += 1

    db.commit()
    _broadcast_reactions(db, post, principal.user_id)
    # Only when one was added or changed: taking a reaction back is not news.
    if not (existing and existing.kind == payload.kind):
        _notify_reacted(db, post, principal.user_id, payload.kind)
    return {"post_id": post_id, "reactions": _reaction_summary(db, post_id, principal.user_id)}


@app.get("/posts/{post_id}/reactions", tags=["engagement"])
def post_reactions(post_id: str, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    viewer = principal.user_id if principal else None
    _readable_post(db, post_id, viewer)
    return _reaction_summary(db, post_id, viewer)


@app.get("/posts/{post_id}/reactions/people", tags=["engagement"])
def post_reaction_people(post_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Who reacted, for the person who wrote it.

    The counter said "11" and there was no way to find out who the eleven
    were. The author can now look.

    Only the author. Who liked something is information about eleven other
    people, not about the post: a reader seeing the list learns who else is
    here and what they do, which is nobody's to hand out. The author already
    knows their post was read - this only tells them by whom.

    Newest first, because the question behind "who reacted" is usually "who
    just reacted".
    """
    post = _readable_post(db, post_id, principal.user_id)
    if post.author_id != principal.user_id:
        # 404 rather than 403, like the rest of this file: distinguishing
        # "not yours" from "does not exist" tells somebody which ids are worth
        # trying.
        raise HTTPException(status_code=404, detail="Not found")

    rows = db.execute(
        select(models.Reaction.user_id, models.Reaction.kind, models.Reaction.created_at)
        .where(models.Reaction.post_id == post_id)
        .order_by(models.Reaction.created_at.desc())
        .limit(200)
    ).all()
    people = _authors({uid for uid, _, _ in rows})
    return {
        "total": len(rows),
        "items": [
            {
                "user_id": uid,
                "kind": kind,
                "at": at,
                "handle": (people.get(uid) or {}).get("handle"),
                "display_name": (people.get(uid) or {}).get("display_name") or uid[:12],
                "avatar_url": (people.get(uid) or {}).get("avatar_url"),
            }
            for uid, kind, at in rows
        ],
    }


@app.get("/comments/{comment_id}/likes", tags=["comments"])
def comment_like_people(comment_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Who liked a comment, for whoever wrote the comment. Same rule as a post."""
    comment = db.get(models.Comment, comment_id)
    if comment is None or comment.status != "published":
        raise HTTPException(status_code=404, detail="Not found")
    if comment.author_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Not found")
    # The post still gates it: a comment on something you may no longer read
    # is not readable either, author or not.
    _readable_post(db, comment.post_id, principal.user_id)

    rows = db.execute(
        select(models.CommentReaction.user_id, models.CommentReaction.created_at)
        .where(models.CommentReaction.comment_id == comment_id)
        .order_by(models.CommentReaction.created_at.desc())
        .limit(200)
    ).all()
    people = _authors({uid for uid, _ in rows})
    return {
        "total": len(rows),
        "items": [
            {
                "user_id": uid,
                "kind": "like",
                "at": at,
                "handle": (people.get(uid) or {}).get("handle"),
                "display_name": (people.get(uid) or {}).get("display_name") or uid[:12],
                "avatar_url": (people.get(uid) or {}).get("avatar_url"),
            }
            for uid, at in rows
        ],
    }


def _broadcast_reactions(db: OrmSession, post: models.Post, actor: str) -> None:
    """Announce a post's reaction state.

    `like` and `react` are two routes onto the same counter, so both send the
    same shape — otherwise a client would need one handler per route and a
    plain like would leave the reaction pill stale.

    `mine` is deliberately not included: it is per-viewer, and broadcasting the
    actor's own choice would paint it on everyone else's card.
    """
    summary = _reaction_summary(db, post.id, None)
    live(f"post:{post.id}", "reactions", post_id=post.id,
         counts=summary["counts"], total=summary["total"],
         likes_count=post.likes_count, actor=actor)


@app.post("/posts/{post_id}/like", tags=["engagement"])
def like(post_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    post = _readable_post(db, post_id, principal.user_id)
    existing = db.scalar(
        select(models.Reaction).where(
            models.Reaction.post_id == post_id, models.Reaction.user_id == principal.user_id
        )
    )
    if existing:
        db.delete(existing)
        post.likes_count = max(0, post.likes_count - 1)
        db.commit()
        _broadcast_reactions(db, post, principal.user_id)
        return {"liked": False, "likes_count": post.likes_count}
    db.add(models.Reaction(post_id=post_id, user_id=principal.user_id))
    post.likes_count += 1
    db.commit()
    _broadcast_reactions(db, post, principal.user_id)
    _notify_reacted(db, post, principal.user_id, "like")
    return {"liked": True, "likes_count": post.likes_count}


class RepostIn(BaseModel):
    """A comment turns a repost into a quote — the blueprint's two are one route."""

    body: str = Field(default="", max_length=5000)
    visibility: str = Field(default="public", pattern="^(public|followers|circle|community)$")
    circle_id: str | None = None


@app.post("/posts/{post_id}/repost", status_code=201, tags=["engagement"])
def repost(post_id: str, payload: RepostIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Share someone's post to your own audience.

    The repost carries the original's topics so it can still be found by tag,
    but keeps its own author, timestamp and audience. Reposting a repost points
    at the original rather than building a chain — three levels of "X shared Y
    sharing Z" tells the reader nothing.
    """
    original = _readable_post(db, post_id, principal.user_id)

    target = db.get(models.Post, original.repost_of) if original.repost_of else original
    if target is None:
        raise HTTPException(status_code=404, detail="Post not found")
    # A repost carries the original inside it, so sharing a followers-only or
    # circle post would hand it to the resharer's whole audience. Only what the
    # author made public may travel further.
    if target.visibility != "public":
        raise HTTPException(status_code=400, detail="Only public posts can be shared.")
    if payload.visibility == "circle":
        _require_own_circle(principal.user_id, payload.circle_id)
    if target.author_id == principal.user_id and not payload.body.strip():
        raise HTTPException(status_code=400, detail="Reposting your own post needs a comment")

    existing = db.scalar(
        select(models.Post).where(
            models.Post.author_id == principal.user_id,
            models.Post.repost_of == target.id,
            models.Post.body == "",
            models.Post.status == "published",
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="You already reposted this")

    post = models.Post(
        id=new_id("pst"),
        author_id=principal.user_id,
        body=payload.body.strip(),
        format="text",
        visibility=payload.visibility,
        circle_id=payload.circle_id if payload.visibility == "circle" else None,
        repost_of=target.id,
        topics=target.topics,
        lang=target.lang,
        # Inherited so a repost of adult content stays out of a minor's feed —
        # the sharer cannot launder it by resharing.
        mature=target.mature,
        provenance="original" if payload.body.strip() else "verified_source",
    )
    db.add(post)
    target.reposts_count += 1
    db.commit()
    live(f"post:{target.id}", "reposts", post_id=target.id,
         reposts_count=target.reposts_count, actor=principal.user_id)
    # The same rule as a new post: only public reposts announce themselves on
    # the shared channel.
    if post.visibility == "public":
        live("feed", "post", post_id=post.id, author_id=principal.user_id, format="text")
    return _post_out(post, db, viewer=principal.user_id)


@app.delete("/posts/{post_id}/repost", status_code=204, tags=["engagement"])
def undo_repost(post_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Undo a plain repost. A quote is a post of your own — delete it as one."""
    mine = db.scalar(
        select(models.Post).where(
            models.Post.author_id == principal.user_id,
            models.Post.repost_of == post_id,
            models.Post.body == "",
            models.Post.status == "published",
        )
    )
    if mine is None:
        raise HTTPException(status_code=404, detail="You have not reposted this")
    original = db.get(models.Post, post_id)
    if original is not None:
        original.reposts_count = max(0, original.reposts_count - 1)
    db.delete(mine)
    db.commit()
    if original is not None:
        live(f"post:{post_id}", "reposts", post_id=post_id,
             reposts_count=original.reposts_count, actor=principal.user_id)


class ViewsIn(BaseModel):
    post_ids: list[str] = Field(default_factory=list, max_length=100)


@app.post("/posts/views", tags=["engagement"])
def record_views(payload: ViewsIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Record that these posts were actually seen.

    Batched because the client reports whatever came into view since the last
    call — one request per card would be one request per scroll tick. A member's
    own post does not count as a view of it, and each pair is stored once, so
    the figure means "people who saw this", not "times a card rendered".
    """
    ids = [pid for pid in dict.fromkeys(payload.post_ids) if pid]
    if not ids:
        return {"recorded": 0}

    already = set(
        db.scalars(
            select(models.PostView.post_id).where(
                models.PostView.user_id == principal.user_id, models.PostView.post_id.in_(ids)
            )
        ).all()
    )
    audience = _viewer_audience(principal.user_id)
    fresh = [
        post
        for post in db.scalars(
            select(models.Post).where(models.Post.id.in_([i for i in ids if i not in already]))
        ).all()
        if post.author_id != principal.user_id and _can_see(post, audience)
    ]
    for post in fresh:
        db.add(models.PostView(post_id=post.id, user_id=principal.user_id))
        post.views_count += 1
    db.commit()
    for post in fresh:
        live(f"post:{post.id}", "views", post_id=post.id, views_count=post.views_count)
    return {"recorded": len(fresh), "counts": {p.id: p.views_count for p in fresh}}


# Two visual levels: 0 = a comment, 1 = a reply to it. Anything deeper joins the
# depth-1 branch and names its addressee instead of indenting further.
#
# Unbounded nesting turns a conversation into a staircase that runs out of
# horizontal room; the @mention carries "who answered whom" once indentation
# can no longer show it.
MAX_COMMENT_DEPTH = 1

USER_SERVICE = "http://user-service:8000"


def _authors(ids: set[str]) -> dict[str, dict]:
    """Resolve author ids to handles for display and for @mentions.

    Falls back to the raw id when user-service cannot be reached: a comment
    thread that renders with ugly names still beats one that fails to load.
    """
    if not ids:
        return {}
    try:
        response = httpx.post(f"{USER_SERVICE}/internal/profiles", json={"ids": sorted(ids)}, timeout=5)
        response.raise_for_status()
        return response.json()["profiles"]
    except Exception as exc:
        log.warning("could not resolve comment authors: %s", exc)
        return {}


@app.post("/posts/{post_id}/comments", status_code=201, tags=["engagement"])
def add_comment(post_id: str, payload: CommentIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    post = _readable_post(db, post_id, principal.user_id)

    # You cannot comment on what you are not allowed to read. Without this a
    # minor could write on an adult post by posting the id directly, and the
    # comment would then carry their handle into a thread they cannot see.
    commenter = _viewer_age(principal.user_id)
    if not agefilter.visible_to(db, commenter, post.id):
        _refuse_or_404(commenter)

    parent = db.get(models.Comment, payload.parent_id) if payload.parent_id else None
    if payload.parent_id and (parent is None or parent.post_id != post_id):
        raise HTTPException(status_code=400, detail="That comment is not on this post")

    depth = 0
    parent_id = None
    reply_to = None

    if parent is not None:
        reply_to = parent.author_id
        if parent.depth < MAX_COMMENT_DEPTH:
            depth = parent.depth + 1
            parent_id = parent.id
        else:
            # Too deep to indent: hang it off the deepest allowed ancestor so it
            # stays in the right branch, and keep `reply_to` so the client can
            # show who it answers.
            depth = MAX_COMMENT_DEPTH
            cursor = parent
            while cursor.depth > MAX_COMMENT_DEPTH - 1 and cursor.parent_id:
                nxt = db.get(models.Comment, cursor.parent_id)
                if nxt is None:
                    break
                cursor = nxt
            parent_id = cursor.id

    comment = models.Comment(
        id=new_id("cmt"),
        post_id=post_id,
        author_id=principal.user_id,
        parent_id=parent_id,
        depth=depth,
        reply_to=reply_to,
        body=payload.body,
        lang=payload.lang,
    )
    db.add(comment)
    db.flush()

    # Classified before it is committed, like a post. A comment that appears
    # first and is rated a moment later was readable for that moment, and a
    # comment thread is refreshed far more often than a post is.
    verdict = classifier.classify(
        body=payload.body or "", media_kinds=[], author_is_minor=commenter.is_minor
    )
    row = models.ContentSafetyClassification(
        id=new_id("csc"), content_id=comment.id, content_kind="comment"
    )
    for key, value in verdict.as_payload().items():
        setattr(row, key, value)
    db.add(row)

    if verdict.block_publication:
        # The rollback throws away the comment and its classification, which is
        # right - it was never published - but it used to throw away every trace
        # of the refusal with them. The author was told to contact support about
        # something that no longer existed anywhere. So the decision is written
        # after the rollback, in its own transaction, carrying the text the
        # reviewer will need because the comment itself is gone.
        # Both read before the rollback: it expires every instance in the
        # session, so touching comment.id afterwards would go looking for a row
        # that the rollback just removed.
        refused_body = payload.body or ""
        refused_id = comment.id
        db.rollback()
        if verdict.escalate_child_safety:
            log.error("child-safety escalation on a comment by %s", principal.user_id)
        decision = moderation.record_decision(
            db, subject_id=principal.user_id, content_id=refused_id, content_kind="comment",
            action="refused_publication", age_rating=verdict.age_rating,
            body_snapshot=refused_body,
            appealable=not verdict.escalate_child_safety,
        )
        db.commit()
        raise HTTPException(
            status_code=403,
            detail=(
                "This cannot be published. If you think that is wrong you can ask for "
                "it to be looked at again."
                if decision.appealable
                else "This cannot be published."
            ),
            headers={"X-Moderation-Decision": decision.id},
        )

    if verdict.age_rating not in ("GENERAL", "TEEN_13_PLUS"):
        # Posts already recorded this; comments did not, which meant an adult
        # whose comment was rated out of every minor's view was told nothing at
        # all about it. Same content, same consequence, same record.
        moderation.record_decision(
            db, subject_id=principal.user_id, content_id=comment.id,
            content_kind="comment", action="restricted_by_rating",
            age_rating=verdict.age_rating,
            appealable=not verdict.escalate_child_safety,
        )

    post.comments_count += 1
    db.commit()
    live(f"post:{post_id}", "comment",
         post_id=post_id, comment_id=comment.id, author_id=principal.user_id,
         parent_id=parent_id, reply_to=reply_to, depth=depth,
         comments_count=post.comments_count)
    # The author, and whoever is being replied to, hear about it on their own
    # channel even when they are not looking at the post — but only while they
    # may still read it. Someone removed from a circle keeps their old comment
    # in the thread; a reply to it must not carry the circle's words to them.
    targets = {post.author_id, reply_to} - {principal.user_id, None}
    if post.visibility != "public":
        targets = {
            t for t in targets
            if t == post.author_id or _can_see(post, _viewer_audience(t))
        }
    actor_name = _name_of(principal.user_id)
    for target in targets:
        live(f"user:{target}", "activity", event_kind="comment",
             post_id=post_id, comment_id=comment.id, actor=principal.user_id)
        notify.notify(
            target,
            kind="comment" if target == post.author_id else "reply",
            # Named, not "Someone". Whose comment it is decides whether you
            # open it, and the name was known here all along.
            title=(
                f"{actor_name} replied to you"
                if target == reply_to
                else f"{actor_name} commented on your post"
            ),
            body=payload.body[:140],
            link=f"/hub?post={post_id}",
        )
    # Anyone named in the comment hears about it, whether or not they are the
    # author of the post or the person being replied to - those two already
    # have their own notification above, and _notify_mentions skips the actor.
    _notify_mentions(payload.body, principal.user_id, post_id, "comment")
    return {
        "id": comment.id,
        "created_at": comment.created_at,
        "depth": depth,
        "parent_id": parent_id,
        "reply_to": reply_to,
        # The names for anyone named in it. The page adds this comment to the
        # thread itself rather than refetching, so without this the one
        # comment you just wrote is the one that shows a handle where every
        # other comment shows a name.
        "mentions": _mention_names([payload.body or ""]),
    }


@app.get("/posts/{post_id}/comments", tags=["engagement"])
def list_comments(
    post_id: str,
    principal: MaybeUser,
    limit: int = 100,
    offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    """Comments on a post, age-filtered.

    This took no viewer at all until now, so it gave a 13-year-old and an adult
    the same answer — and comments are where adult material most easily reaches
    a minor, because the post carrying them can be perfectly ordinary.
    """
    age = _viewer_age(principal.user_id if principal else None)

    # The post gates first: comments on something you may not read are not
    # yours to read either — by audience, then by age.
    _readable_post(db, post_id, principal.user_id if principal else None)
    if not agefilter.visible_to(db, age, post_id):
        _refuse_or_404(age)

    stmt = select(models.Comment).where(
        models.Comment.post_id == post_id, models.Comment.status == "published"
    )
    if age.is_minor:
        forbidden = (
            ("ADULT_18_PLUS", "PROHIBITED", "UNCLASSIFIED", "TEEN_16_PLUS")
            if (age.age is None or age.age < 16)
            else ("ADULT_18_PLUS", "PROHIBITED", "UNCLASSIFIED")
        )
        blocked = select(models.ContentSafetyClassification.content_id).where(
            models.ContentSafetyClassification.age_rating.in_(forbidden)
        )
        # Only the classified-out are excluded here, not the unclassified:
        # comments written before the classifier existed have no row, and
        # hiding every one of them would empty long threads for teenagers
        # while telling them nothing. They are backfilled below instead.
        stmt = stmt.where(models.Comment.id.not_in(blocked))

    rows = db.scalars(
        stmt.order_by(models.Comment.created_at).limit(min(limit, 300)).offset(offset)
    ).all()

    people = _authors({r.author_id for r in rows} | {r.reply_to for r in rows if r.reply_to})

    # Likes for the comments on this page, in two queries rather than two per
    # comment. A thread of eighty replies would otherwise be a hundred and
    # sixty round trips to render one screen.
    ids = [r.id for r in rows]
    counts = _comment_reaction_counts(db, ids)
    like_counts = counts["likes"]
    sticker_counts = counts["stickers"]
    mine: set[str] = set()
    # Which sticker this reader chose, per comment, so their own is drawn as
    # theirs rather than as one more in the pile.
    my_sticker: dict[str, str] = {}
    if ids and principal is not None:
        for cid, sticker_id in db.execute(
            select(models.CommentReaction.comment_id, models.CommentReaction.sticker_id).where(
                models.CommentReaction.comment_id.in_(ids),
                models.CommentReaction.user_id == principal.user_id,
            )
        ).all():
            mine.add(cid)
            if sticker_id:
                my_sticker[cid] = sticker_id

    def describe(uid: str | None) -> dict | None:
        if not uid:
            return None
        profile = people.get(uid)
        return {
            "id": uid,
            "handle": profile["handle"] if profile else uid[:12],
            "display_name": profile["display_name"] if profile else uid[:12],
            "avatar_url": profile.get("avatar_url") if profile else None,
        }

    return {
        "max_depth": MAX_COMMENT_DEPTH,
        # One map for the whole thread: the same handful of people are named
        # over and over in a conversation.
        "mentions": _mention_names([r.body or "" for r in rows]),
        "items": [
            {
                "id": r.id,
                "author_id": r.author_id,
                "author": describe(r.author_id),
                "parent_id": r.parent_id,
                "depth": r.depth,
                "reply_to": r.reply_to,
                "reply_to_user": describe(r.reply_to),
                "body": r.body,
                "lang": r.lang,
                "created_at": r.created_at,
                "edited_at": r.edited_at,
                "likes": like_counts.get(r.id, 0),
                "liked": r.id in mine,
                "stickers": sticker_counts.get(r.id, {}),
                "my_sticker": my_sticker.get(r.id),
            }
            for r in rows
        ],
    }


def _excerpt(body: str | None, limit: int = 120) -> str:
    """A line of a post, for a list that is not showing the post.

    An article's body is HTML, so taken raw it puts "<p>An article body..."
    in front of somebody as though that were what they had written. Tags out,
    entities back to characters, whitespace collapsed.
    """
    text = re.sub(r"<[^>]+>", " ", body or "")
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


@app.get("/stats/me", tags=["engagement"])
def my_stats(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What this member's writing has done.

    Their own, and only their own: these are the numbers behind somebody's
    posts, and a leaderboard of who gets read is not something this platform
    hands out.

    Counted from the posts rather than by walking the reaction and comment
    tables - the counters on a post are what every other screen already shows,
    so a total that disagreed with them would be the one that is wrong.
    """
    published = and_(models.Post.author_id == principal.user_id, models.Post.status == "published")
    totals = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(models.Post.likes_count), 0),
            func.coalesce(func.sum(models.Post.comments_count), 0),
            func.coalesce(func.sum(models.Post.reposts_count), 0),
            func.coalesce(func.sum(models.Post.views_count), 0),
        ).where(published)
    ).one()

    since = datetime.now(timezone.utc) - timedelta(days=30)
    recent = db.scalar(
        select(func.count()).select_from(models.Post).where(published, models.Post.created_at >= since)
    ) or 0

    # The five that travelled furthest, by the sum of what people did with
    # them rather than by views: a post seen by many and answered by nobody is
    # not the one somebody wants to be shown.
    top = db.scalars(
        select(models.Post)
        .where(published)
        .order_by((models.Post.likes_count + models.Post.comments_count + models.Post.reposts_count).desc())
        .limit(5)
    ).all()

    comments_written = db.scalar(
        select(func.count())
        .select_from(models.Comment)
        .where(models.Comment.author_id == principal.user_id, models.Comment.status == "published")
    ) or 0

    # Thirty days, one row each, including the days with nothing on them: a
    # chart drawn only from the days somebody posted says they post every day.
    day = func.date_trunc("day", models.Post.created_at)
    posted = {
        d.date(): n
        for d, n in db.execute(
            select(day, func.count()).where(published, models.Post.created_at >= since).group_by(day)
        ).all()
    }
    reaction_day = func.date_trunc("day", models.Reaction.created_at)
    received = {
        d.date(): n
        for d, n in db.execute(
            select(reaction_day, func.count())
            .select_from(models.Reaction)
            .join(models.Post, models.Post.id == models.Reaction.post_id)
            .where(
                models.Post.author_id == principal.user_id,
                models.Reaction.created_at >= since,
            )
            .group_by(reaction_day)
        ).all()
    }
    today = datetime.now(timezone.utc).date()
    daily = [
        {
            "date": (today - timedelta(days=offset)).isoformat(),
            "posts": posted.get(today - timedelta(days=offset), 0),
            "reactions": received.get(today - timedelta(days=offset), 0),
        }
        for offset in range(29, -1, -1)
    ]

    posts, likes, comments, reposts, views = totals
    return {
        "posts": posts,
        "posts_last_30_days": recent,
        "reactions_received": int(likes),
        "comments_received": int(comments),
        "reposts_received": int(reposts),
        "views": int(views),
        "comments_written": comments_written,
        "daily": daily,
        "top_posts": [
            {
                "id": p.id,
                "body": _excerpt(p.body),
                "created_at": p.created_at,
                "likes": p.likes_count,
                "comments": p.comments_count,
                "reposts": p.reposts_count,
                "views": p.views_count,
            }
            for p in top
        ],
    }


@app.get("/internal/stats", tags=["internal"])
def stats(db: OrmSession = Depends(get_db)):
    return {
        "posts": db.scalar(select(func.count()).select_from(models.Post)) or 0,
        "comments": db.scalar(select(func.count()).select_from(models.Comment)) or 0,
        "algorithms": db.scalar(select(func.count()).select_from(models.Algorithm)) or 0,
    }


@app.post("/internal/posts/backfill-media-dimensions", tags=["internal"])
def backfill_media_dimensions(limit: int = 200, dry_run: bool = True, db: OrmSession = Depends(get_db)):
    """Copy sizes media-service has measured onto the post media that lack them.

    The feed lays a picture out from what is stored on the post, not from the
    asset, so backfilling media-service alone changes nothing a reader sees.
    This is the second half: for every post image with no size, ask
    media-service what it now knows and write it down.

    Asked rather than joined across the schema, for the same reason attaching
    does: what the size is, is media-service's answer to give.

    Only rows with no size, so running it again finds nothing left to do.
    Reaching this needs access to the container; /internal is not routed
    through the gateway.
    """
    rows = db.scalars(
        select(models.PostMedia)
        .where(models.PostMedia.kind == "image", models.PostMedia.width.is_(None))
        .limit(max(1, min(limit, 1000)))
    ).all()

    # One call per distinct asset: a picture posted twice is one question.
    wanted = {row.media_id for row in rows if row.media_id}
    sizes: dict[str, tuple[int, int]] = {}
    unknown = 0
    for media_id in wanted:
        try:
            response = httpx.get(f"{MEDIA_URL}/internal/media/{media_id}", timeout=5)
        except httpx.HTTPError as exc:
            log.warning("backfill could not ask about media %s: %s", media_id, exc)
            continue
        if response.status_code != 200:
            unknown += 1
            continue
        asset = response.json()
        width, height = asset.get("width"), asset.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            sizes[media_id] = (width, height)
        else:
            unknown += 1

    updated = 0
    for row in rows:
        size = sizes.get(row.media_id or "")
        if size is None:
            continue
        if not dry_run:
            row.width, row.height = size
        updated += 1
    if not dry_run:
        db.commit()

    remaining = db.scalar(
        select(func.count())
        .select_from(models.PostMedia)
        .where(models.PostMedia.kind == "image", models.PostMedia.width.is_(None))
    )
    return {
        "dry_run": dry_run,
        "examined": len(rows),
        "assets_asked": len(wanted),
        "updated": updated,
        "asset_had_no_size": unknown,
        "still_without_size": remaining,
    }
