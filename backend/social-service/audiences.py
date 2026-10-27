"""Kinjy · social-service — circle audiences (27/10).

A circle post goes to one or more of its author's circles, plus people added
by name, minus people left out (an exclusion wins over every circle). Its
audience either follows the circles (live: whoever is in them when a reader
opens the post - every circle post written before 27/10 works this way) or is
the people it reached when it was published (snapshot: later additions to a
circle do not get it; blocks, exclusions and opt-outs still close it).

A recipient never learns a circle's name or who else is in it. From a post
they can receive, they can mute notifications from that circle, hide it from
their feeds, or opt out of it - for new posts, and if they choose, for the
earlier ones too. A circle they opted out of closes a post even when another
of the post's circles also includes them.

Imported by main.py (lazily inside its functions, and as a router at the end).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, exists, false, func, not_, or_, select, true
from sqlalchemy.orm import Session as OrmSession, object_session

import models
from common import notify, settings
from common.auth import CurrentUser
from common.database import SessionLocal, get_db

log = logging.getLogger("social-service.audiences")
router = APIRouter()
USER_URL = "http://user-service:8000"
MESSAGING_URL = "http://messaging-service:8000"
NOTIFY_CAP = 1000
# Below this, an owner sees "fewer than 3" rather than a count that could
# point at one person.
OPT_OUT_MIN_SHOWN = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- the viewer's own choices --------------------------------------------------

def viewer_prefs(viewer: str | None) -> tuple[frozenset, frozenset, tuple]:
    if not viewer:
        return frozenset(), frozenset(), ()
    try:
        with SessionLocal() as db:
            rows = db.scalars(select(models.CircleAudiencePref).where(models.CircleAudiencePref.user_id == viewer)).all()
    except Exception as exc:
        log.warning("circle preferences unreadable for %s: %s", viewer, exc)
        return frozenset(), frozenset(), ()
    return (
        frozenset(r.circle_id for r in rows if r.hidden),
        frozenset(r.circle_id for r in rows if r.muted),
        tuple((r.circle_id, r.opted_out_at, bool(r.opt_out_past)) for r in rows if r.opted_out_at),
    )


# --- who may read a circle post ---------------------------------------------------

def _post_circles(db: OrmSession, post: models.Post) -> list[str]:
    ids = list(db.scalars(select(models.PostCircle.circle_id).where(models.PostCircle.post_id == post.id)).all())
    return ids or ([post.circle_id] if post.circle_id else [])


def can_see_circle_post(post: models.Post, audience) -> bool:
    """The single-post form of circle_clause (main._can_see calls it)."""
    viewer = audience.viewer
    if not viewer:
        return False
    db = object_session(post)
    own = db is None
    if own:
        db = SessionLocal()
    try:
        circles = _post_circles(db, post)
        roles = set(
            db.scalars(select(models.PostPerson.role).where(models.PostPerson.post_id == post.id, models.PostPerson.user_id == viewer)).all()
        )
    finally:
        if own:
            db.close()
    if "exclude" in roles or post.author_id in audience.blocked:
        return False
    created = post.created_at
    for circle_id, since, past in audience.opted_out:
        if circle_id in circles and (past or (since is not None and created is not None and created >= since)):
            return False
    if (post.audience_mode or "live") == "snapshot":
        return "snapshot" in roles
    return bool(set(circles) & set(audience.circles)) or "include" in roles


def circle_clause(audience):
    """The SQL form of can_see_circle_post, for feeds, profiles and search."""
    viewer = audience.viewer
    if not viewer:
        return false()
    P, PC, PP = models.Post, models.PostCircle, models.PostPerson

    def person(role: str):
        return exists().where(PP.post_id == P.id, PP.user_id == viewer, PP.role == role)

    in_circle = exists().where(PC.post_id == P.id, PC.circle_id.in_(audience.circles)) if audience.circles else false()
    live = and_(or_(P.audience_mode.is_(None), P.audience_mode == "live"), or_(in_circle, person("include")))
    snapshot = and_(P.audience_mode == "snapshot", person("snapshot"))
    clause = and_(P.visibility == "circle", or_(live, snapshot), not_(person("exclude")))
    if audience.blocked:
        clause = and_(clause, P.author_id.not_in(audience.blocked))
    for circle_id, since, past in audience.opted_out:
        uses = exists().where(PC.post_id == P.id, PC.circle_id == circle_id)
        clause = and_(clause, not_(and_(uses, true() if past else P.created_at >= since)))
    return clause


def not_hidden(audience):
    """Feeds only: leave out posts from circles the viewer chose to hide."""
    if not audience.viewer or not audience.hidden_circles:
        return true()
    P, PC = models.Post, models.PostCircle
    return or_(
        P.author_id == audience.viewer,
        P.visibility != "circle",
        not_(exists().where(PC.post_id == P.id, PC.circle_id.in_(audience.hidden_circles))),
    )


# --- publishing ---------------------------------------------------------------------

def _resolve(owner_id: str, circle_ids: list[str], include_ids: list[str], exclude_ids: list[str]) -> dict:
    try:
        response = httpx.post(
            f"{USER_URL}/internal/audience/resolve",
            json={"owner_id": owner_id, "circle_ids": circle_ids, "include_ids": include_ids, "exclude_ids": exclude_ids},
            timeout=8,
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Could not check that audience right now. Try again.")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Circle not found")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Could not check that audience right now. Try again.")
    return response.json()


def check_new_audience(owner_id: str, circle_id: str | None, circle_ids: list[str], include_ids: list[str]) -> list[str]:
    """The circles of a new circle post: the author's own and not archived.
    Fails closed when user-service cannot answer."""
    ids = list(dict.fromkeys([c for c in [circle_id, *circle_ids] if c]))
    if not ids and not include_ids:
        raise HTTPException(status_code=400, detail="Choose at least one circle or one person")
    if ids:
        try:
            response = httpx.post(f"{USER_URL}/internal/circles/check", json={"owner_id": owner_id, "circle_ids": ids}, timeout=5)
        except httpx.HTTPError:
            raise HTTPException(status_code=503, detail="Could not check that circle right now. Try again.")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Circle not found")
        if response.status_code == 409:
            raise HTTPException(status_code=409, detail=response.json().get("detail"))
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="Could not check that circle right now. Try again.")
    if include_ids:
        refused = _resolve(owner_id, ids, include_ids, [])["refused"]
        if refused:
            raise HTTPException(status_code=403, detail="Some of the people you added cannot receive your posts.")
    return ids


def store_audience(db: OrmSession, post: models.Post, circle_ids: list[str], include_ids: list[str], exclude_ids: list[str]) -> None:
    excluded = set(exclude_ids)
    for circle_id in circle_ids:
        db.add(models.PostCircle(post_id=post.id, circle_id=circle_id))
    for user_id in dict.fromkeys(include_ids):
        if user_id not in excluded and user_id != post.author_id:
            db.add(models.PostPerson(post_id=post.id, user_id=user_id, role="include"))
    for user_id in dict.fromkeys(exclude_ids):
        if user_id != post.author_id:
            db.add(models.PostPerson(post_id=post.id, user_id=user_id, role="exclude"))


def _people(db: OrmSession, post_id: str, role: str) -> list[str]:
    return list(db.scalars(select(models.PostPerson.user_id).where(models.PostPerson.post_id == post_id, models.PostPerson.role == role)).all())


def _eligible_now(db: OrmSession, post: models.Post) -> list[str]:
    """Who the post reaches as its author sees it now (before each reader's own
    opt-outs, which only that reader's request knows)."""
    if (post.audience_mode or "live") == "snapshot":
        excluded = set(_people(db, post.id, "exclude"))
        return [u for u in _people(db, post.id, "snapshot") if u not in excluded]
    out = _resolve(post.author_id, _post_circles(db, post), _people(db, post.id, "include"), _people(db, post.id, "exclude"))
    return out["ids"]


def _record_snapshot(db: OrmSession, post: models.Post) -> list[str]:
    ids = _resolve(post.author_id, _post_circles(db, post), _people(db, post.id, "include"), _people(db, post.id, "exclude"))["ids"]
    db.execute(delete(models.PostPerson).where(models.PostPerson.post_id == post.id, models.PostPerson.role == "snapshot"))
    for user_id in ids:
        db.add(models.PostPerson(post_id=post.id, user_id=user_id, role="snapshot"))
    post.audience_fixed_at = _now()
    return ids


def on_published(post_id: str) -> None:
    """At publication: record a fixed audience, then (if the author asked) tell
    the audience - each person once, respecting their mutes and opt-outs, and
    never with the circle's name or the post's words."""
    with SessionLocal() as db:
        post = db.get(models.Post, post_id)
        if post is None or post.visibility != "circle":
            return
        try:
            if (post.audience_mode or "live") == "snapshot":
                recipients = _record_snapshot(db, post)
                db.commit()
            else:
                recipients = _eligible_now(db, post) if post.notify_audience else []
        except HTTPException as exc:
            log.error("could not fix the audience of %s at publication: %s", post_id, exc.detail)
            return
        if not post.notify_audience or not recipients:
            return
        circles = _post_circles(db, post)
        quiet = set(
            db.scalars(
                select(models.CircleAudiencePref.user_id).where(
                    models.CircleAudiencePref.circle_id.in_(circles),
                    or_(models.CircleAudiencePref.muted.is_(True), models.CircleAudiencePref.opted_out_at.is_not(None)),
                )
            ).all()
        )
        author_id = post.author_id
    targets = [u for u in recipients if u not in quiet and u != author_id][:NOTIFY_CAP]

    def send() -> None:
        try:
            name = httpx.post(f"{USER_URL}/internal/profiles", json={"ids": [author_id]}, timeout=5).json()["profiles"].get(author_id, {}).get("display_name") or "Someone"
        except Exception:
            name = "Someone"
        for user_id in targets:
            notify.notify(user_id, kind="circle_post", title=f"{name} shared a post with you", link=f"/p/{post_id}")

    threading.Thread(target=send, daemon=True).start()


def _forget_notifications(user_ids: list[str], post_ids: list[str]) -> None:
    """Take back the stored notifications about these posts from people who can
    no longer read them. Best effort: the post itself is already closed to them."""
    if not user_ids or not post_ids:
        return
    try:
        httpx.post(
            f"{MESSAGING_URL}/internal/notifications/forget",
            json={"user_ids": user_ids[:5000], "links": [f"/p/{p}" for p in post_ids][:500]},
            timeout=8,
        )
    except Exception as exc:
        log.warning("could not withdraw notifications: %s", exc)


# --- the author: who can see a post, and changing it ------------------------------------

def _own_post(db: OrmSession, post_id: str, user_id: str) -> models.Post:
    post = db.get(models.Post, post_id)
    if post is None or post.author_id != user_id or post.status == "removed":
        raise HTTPException(status_code=404, detail="Post not found")
    return post


def _profiles(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    try:
        response = httpx.post(f"{USER_URL}/internal/profiles", json={"ids": sorted(set(ids))[:300]}, timeout=5)
        response.raise_for_status()
        return response.json()["profiles"]
    except Exception:
        return {}


def _circle_names(owner_id: str, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        response = httpx.get(f"{USER_URL}/internal/circles/names", params={"owner_id": owner_id, "ids": ",".join(ids)}, timeout=5)
        response.raise_for_status()
        return response.json()["names"]
    except Exception:
        return {}


def _opt_out_count(db: OrmSession, circle_ids: list[str]) -> int | None:
    n = db.scalar(
        select(func.count(func.distinct(models.CircleAudiencePref.user_id))).where(
            models.CircleAudiencePref.circle_id.in_(circle_ids), models.CircleAudiencePref.opted_out_at.is_not(None)
        )
    ) or 0 if circle_ids else 0
    return n if n >= OPT_OUT_MIN_SHOWN else None


def _person(profiles: dict, uid: str) -> dict:
    p = profiles.get(uid) or {}
    return {"user_id": uid, "display_name": p.get("display_name"), "handle": p.get("handle"), "avatar_url": p.get("avatar_url")}


@router.get("/posts/{post_id}/audience", tags=["posts"])
def post_audience(post_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """For the author: who this post goes to, and who can see it now."""
    post = _own_post(db, post_id, principal.user_id)
    out: dict = {"visibility": post.visibility, "audience_mode": post.audience_mode or "live", "audience_fixed_at": post.audience_fixed_at, "notify_audience": post.notify_audience}
    if post.visibility != "circle":
        return out
    circles = _post_circles(db, post)
    names = _circle_names(post.author_id, circles)
    includes, excludes = _people(db, post.id, "include"), _people(db, post.id, "exclude")
    eligible = _eligible_now(db, post)
    profiles = _profiles(eligible[:300] + includes + excludes)
    out.update(
        {
            # A deleted circle has no name: its members no longer get the post.
            "circles": [{"id": c, "name": names.get(c), "deleted": c not in names} for c in circles],
            "include": [_person(profiles, u) for u in includes],
            "exclude": [_person(profiles, u) for u in excludes],
            "eligible_count": len(eligible),
            "eligible": [_person(profiles, u) for u in eligible[:300]],
            # Counted without naming anybody, and only when it cannot point at one person.
            "opted_out_count": _opt_out_count(db, circles),
            "note": (
                "Fixed when the post was published: people added to your circles later do not get it. "
                "People who block you, whom you leave out, or who opt out stop seeing it."
                if (post.audience_mode or "live") == "snapshot"
                else "Follows your circles: people you add to them later can see it; people you remove can no longer see it."
            ),
        }
    )
    return out


class AudienceChangeIn(BaseModel):
    visibility: str = Field(pattern="^(public|followers|connections|circle|private)$")
    circle_ids: list[str] = Field(default_factory=list, max_length=20)
    include_ids: list[str] = Field(default_factory=list, max_length=500)
    exclude_ids: list[str] = Field(default_factory=list, max_length=500)
    audience_mode: str = Field(default="live", pattern="^(live|snapshot)$")
    # The author saw who would be added and agreed.
    confirm_added: bool = False


@router.patch("/posts/{post_id}/audience", tags=["posts"])
def change_audience(post_id: str, payload: AudienceChangeIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Change who a post goes to. A change that would let new people see it is
    shown to the author first (409 with who) and applied only once confirmed.
    People who lose access stop seeing it and its notifications; what they
    already read or copied cannot be taken back."""
    post = _own_post(db, post_id, principal.user_id)
    if post.visibility == "community":
        raise HTTPException(status_code=409, detail="A community post keeps the community's audience")
    before = set(_eligible_now(db, post)) if post.visibility == "circle" else None
    if payload.visibility == "circle":
        circle_ids = check_new_audience(principal.user_id, None, payload.circle_ids, payload.include_ids)
        after = set(_resolve(principal.user_id, circle_ids, payload.include_ids, payload.exclude_ids)["ids"])
    else:
        after = None
    broader = payload.visibility in ("public", "followers", "connections") and post.visibility in ("circle", "private", "connections", "followers") and payload.visibility != post.visibility
    if after is not None and before is not None:
        added = after - before
    elif after is not None:
        # From "only me", followers or connections to circles: nobody can say who
        # is new, so everyone is shown as possibly new. From public, it narrows.
        added = set() if post.visibility == "public" else set(after)
    else:
        added = set()
    if (added or broader) and not payload.confirm_added:
        profiles = _profiles(sorted(added)[:20])
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This change lets more people see the post. Confirm to apply it.",
                "added_count": None if broader else len(added),
                "added": [_person(profiles, u) for u in sorted(added)[:20]],
                "becomes": payload.visibility,
            },
        )
    removed = sorted(before - after) if (before is not None and after is not None) else (sorted(before) if before is not None else [])
    db.execute(delete(models.PostCircle).where(models.PostCircle.post_id == post.id))
    db.execute(delete(models.PostPerson).where(models.PostPerson.post_id == post.id, models.PostPerson.role.in_(("include", "exclude"))))
    post.visibility = payload.visibility
    if payload.visibility == "circle":
        post.circle_id = circle_ids[0] if circle_ids else None
        post.audience_mode = payload.audience_mode
        store_audience(db, post, circle_ids, payload.include_ids, payload.exclude_ids)
        db.flush()
        if payload.audience_mode == "snapshot":
            db.execute(delete(models.PostPerson).where(models.PostPerson.post_id == post.id, models.PostPerson.role == "snapshot"))
            for user_id in sorted(after):
                db.add(models.PostPerson(post_id=post.id, user_id=user_id, role="snapshot"))
            post.audience_fixed_at = _now()
        else:
            db.execute(delete(models.PostPerson).where(models.PostPerson.post_id == post.id, models.PostPerson.role == "snapshot"))
    else:
        post.circle_id = None
        post.audience_mode = "live"
        db.execute(delete(models.PostPerson).where(models.PostPerson.post_id == post.id, models.PostPerson.role == "snapshot"))
    db.commit()
    if payload.visibility in ("circle", "private") and removed:
        _forget_notifications(removed, [post.id])
    return {"visibility": post.visibility, "removed": len(removed), "added": len(added)}


@router.get("/me/circle-posts", tags=["posts"])
def my_circle_posts(circle_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What I shared with one of my circles, scheduled posts included."""
    ids = db.scalars(select(models.PostCircle.post_id).where(models.PostCircle.circle_id == circle_id)).all()
    rows = db.scalars(
        select(models.Post)
        .where(models.Post.id.in_(ids), models.Post.author_id == principal.user_id, models.Post.status.in_(("published", "scheduled", "pending")))
        .order_by(models.Post.created_at.desc())
        .limit(100)
    ).all() if ids else []
    return {
        "items": [
            {
                "id": p.id,
                "excerpt": (p.body or "")[:160],
                "format": p.format,
                "status": p.status,
                "publish_at": p.publish_at,
                "created_at": p.created_at,
                "audience_mode": p.audience_mode or "live",
            }
            for p in rows
        ],
        "opted_out_count": _opt_out_count(db, [circle_id]),
    }


# --- the recipient: their own choices about somebody's circle ------------------------------

def _token(viewer: str, circle_id: str) -> str:
    """A stable label for one of somebody's circles, different for every reader,
    that says nothing about the circle itself."""
    return hmac.new(settings.JWT_SECRET.encode(), f"audience:{viewer}:{circle_id}".encode(), hashlib.sha256).hexdigest()[:6].upper()


def _circles_for_controls(db: OrmSession, post: models.Post, audience) -> list[str]:
    circles = _post_circles(db, post)
    reaching = [c for c in circles if c in audience.circles]
    return reaching or circles


def _pref_out(row: models.CircleAudiencePref | None) -> dict:
    return {
        "muted": bool(row and row.muted),
        "hidden": bool(row and row.hidden),
        "opted_out": bool(row and row.opted_out_at),
        "opt_out_past": bool(row and row.opt_out_past),
        "opted_out_at": row.opted_out_at if row else None,
    }


@router.get("/posts/{post_id}/audience-controls", tags=["posts"])
def audience_controls(post_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """For a reader of a circle post: the private audiences it came through,
    by a label only they see, and their choices for each."""
    from main import _readable_post, _resolve_authors, _viewer_audience

    post = _readable_post(db, post_id, principal.user_id)
    if post.visibility != "circle" or post.author_id == principal.user_id:
        return {"items": []}
    audience = _viewer_audience(principal.user_id)
    circles = _circles_for_controls(db, post, audience)
    author = (_resolve_authors({post.author_id}).get(post.author_id) or {}).get("display_name") or "this member"
    prefs = {r.circle_id: r for r in db.scalars(select(models.CircleAudiencePref).where(models.CircleAudiencePref.user_id == principal.user_id, models.CircleAudiencePref.circle_id.in_(circles))).all()}
    return {
        "author": author,
        "items": [
            {"token": _token(principal.user_id, c), "label": f"Private audience from {author} · {_token(principal.user_id, c)}", **_pref_out(prefs.get(c))}
            for c in circles
        ],
        "explanation": (
            "Each label stands for one private list of this author's. You will not see its name or who else is in it. "
            "Muting, hiding or opting out applies to every post shared through that list, even when another of the author's lists also includes you."
        ),
    }


class AudiencePrefIn(BaseModel):
    post_id: str
    token: str = Field(min_length=6, max_length=6)
    muted: bool | None = None
    hidden: bool | None = None
    opt_out: bool | None = None
    # With opt_out: also stop seeing what was shared through it before.
    opt_out_past: bool | None = None


def _apply(db: OrmSession, row: models.CircleAudiencePref, muted, hidden, opt_out, opt_out_past) -> list[str]:
    """Apply a choice; returns posts to withdraw notifications for."""
    if muted is not None:
        row.muted = muted
    if hidden is not None:
        row.hidden = hidden
    withdrawn: list[str] = []
    if opt_out is True:
        row.opted_out_at = row.opted_out_at or _now()
        row.opt_out_past = bool(opt_out_past)
        posts = select(models.PostCircle.post_id).where(models.PostCircle.circle_id == row.circle_id)
        stmt = select(models.Post.id).where(models.Post.id.in_(posts))
        if not row.opt_out_past:
            stmt = stmt.where(models.Post.created_at >= row.opted_out_at)
        withdrawn = list(db.scalars(stmt.limit(500)).all())
    elif opt_out is False:
        row.opted_out_at, row.opt_out_past = None, False
    row.updated_at = _now()
    return withdrawn


@router.put("/audience-prefs", tags=["posts"])
def set_audience_pref(payload: AudiencePrefIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    from main import _readable_post, _viewer_audience

    post = _readable_post(db, payload.post_id, principal.user_id)
    if post.visibility != "circle" or post.author_id == principal.user_id:
        raise HTTPException(status_code=404, detail="Nothing to manage on this post")
    circle_id = next((c for c in _circles_for_controls(db, post, _viewer_audience(principal.user_id)) if _token(principal.user_id, c) == payload.token), None)
    if circle_id is None:
        raise HTTPException(status_code=404, detail="Audience not found")
    row = db.scalar(select(models.CircleAudiencePref).where(models.CircleAudiencePref.user_id == principal.user_id, models.CircleAudiencePref.circle_id == circle_id))
    if row is None:
        row = models.CircleAudiencePref(user_id=principal.user_id, circle_id=circle_id, owner_id=post.author_id)
        db.add(row)
    withdrawn = _apply(db, row, payload.muted, payload.hidden, payload.opt_out, payload.opt_out_past)
    db.commit()
    _forget_notifications([principal.user_id], withdrawn)
    return _pref_out(row)


@router.get("/audience-prefs", tags=["posts"])
def my_audience_prefs(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Every private audience I made a choice about, to review or reverse."""
    from main import _resolve_authors

    rows = db.scalars(select(models.CircleAudiencePref).where(models.CircleAudiencePref.user_id == principal.user_id).order_by(models.CircleAudiencePref.updated_at.desc())).all()
    authors = _resolve_authors({r.owner_id for r in rows}) if rows else {}
    items = []
    for r in rows:
        name = (authors.get(r.owner_id) or {}).get("display_name") or "a member"
        items.append({"id": r.id, "author": authors.get(r.owner_id), "label": f"Private audience from {name} · {_token(principal.user_id, r.circle_id)}", **_pref_out(r)})
    return {"items": items}


class PrefChangeIn(BaseModel):
    muted: bool | None = None
    hidden: bool | None = None
    opt_out: bool | None = None
    opt_out_past: bool | None = None


@router.patch("/audience-prefs/{pref_id}", tags=["posts"])
def change_audience_pref(pref_id: int, payload: PrefChangeIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    row = db.get(models.CircleAudiencePref, pref_id)
    if row is None or row.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Not found")
    withdrawn = _apply(db, row, payload.muted, payload.hidden, payload.opt_out, payload.opt_out_past)
    if not (row.muted or row.hidden or row.opted_out_at):
        db.delete(row)
        db.commit()
        return {"removed": True}
    db.commit()
    _forget_notifications([principal.user_id], withdrawn)
    return _pref_out(row)


@router.get("/me/circle-opt-outs", tags=["posts"])
def my_circle_opt_outs(principal: CurrentUser, ids: str, db: OrmSession = Depends(get_db)):
    """How many people opted out of each of my circles - only when there are
    enough not to point at anyone, never who."""
    wanted = [i for i in ids.split(",") if i][:100]
    mine = set(db.scalars(select(models.CircleAudiencePref.circle_id).where(models.CircleAudiencePref.owner_id == principal.user_id, models.CircleAudiencePref.circle_id.in_(wanted))).all())
    return {"counts": {c: _opt_out_count(db, [c]) for c in wanted if c in mine}}
