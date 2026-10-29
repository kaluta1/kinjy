"""Kinjy · user-service — profiles, follows, circles, preferences, blocks."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from common import ageclient, notify
from common.agesafety import engine
from common.auth import CurrentUser, MaybeUser
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import agediscovery
import models
import parental
import profilefields

AUTH_URL = "http://auth-service:8000"
MEDIA_URL = "http://media-service:8000"

log = logging.getLogger("user-service")

# Columns added to Preferences after the first deploy. create_all never
# alters an existing table, so without these the privacy work shipped a model
# the database did not have - and /internal/permissions returned 500 for every
# pair of members, which silently turned every permission check on the platform
# into "could not verify".
MIGRATIONS = [
    f"ALTER TABLE {models.SCHEMA}.preferences "
    "ADD COLUMN IF NOT EXISTS who_can_see_family VARCHAR(20) DEFAULT 'family'",
    f"ALTER TABLE {models.SCHEMA}.preferences "
    "ADD COLUMN IF NOT EXISTS family_tree_shared BOOLEAN DEFAULT TRUE",
    f"ALTER TABLE {models.SCHEMA}.preferences "
    "ADD COLUMN IF NOT EXISTS who_can_add_group VARCHAR(20) DEFAULT 'connections'",
    f"ALTER TABLE {models.SCHEMA}.preferences "
    "ADD COLUMN IF NOT EXISTS who_can_invite_group VARCHAR(20) DEFAULT 'everyone'",
    f"ALTER TABLE {models.SCHEMA}.profiles ADD COLUMN IF NOT EXISTS avatar_asset_id VARCHAR(40)",
    f"ALTER TABLE {models.SCHEMA}.profiles ADD COLUMN IF NOT EXISTS cover_asset_id VARCHAR(40)",
    # Dark mode was removed. Cloud is the nearest thing to it, so a member who
    # chose Dark keeps a dark page rather than being dropped back onto a light
    # one - and the column stops holding a value the API no longer accepts.
    f"UPDATE {models.SCHEMA}.preferences SET display_mode = 'cloud' WHERE display_mode = 'dark'",
    # Circles, 27/10: a private description, an icon, archiving, last use.
    f"ALTER TABLE {models.SCHEMA}.circles ADD COLUMN IF NOT EXISTS description TEXT",
    f"ALTER TABLE {models.SCHEMA}.circles ADD COLUMN IF NOT EXISTS icon VARCHAR(30)",
    f"ALTER TABLE {models.SCHEMA}.circles ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ",
    f"ALTER TABLE {models.SCHEMA}.circles ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ",
]

app = create_app(
    name="user-service",
    schema=models.SCHEMA,
    migrations=MIGRATIONS,
    description="Profiles, follow graph, Circles, preferences.",
)


# --- schemas ---------------------------------------------------------------

class ProfileOut(BaseModel):
    user_id: str
    handle: str
    display_name: str
    bio: str | None = None
    avatar_url: str | None = None
    cover_url: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    languages: str = "en"
    is_creator: bool = False
    verified: bool = False
    followers_count: int = 0
    following_count: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class MyProfileOut(ProfileOut):
    """What only the owner sees. The neighbourhood is a precise location and is
    never shown to other members; it feeds local discovery server-side."""

    neighborhood: str | None = None
    lang: str = "en"


class ProfileUpdate(BaseModel):
    """Every field optional. Absent = unchanged; null or blank = cleared, for the
    fields that may be empty. Images are referenced by media-service asset id,
    never by URL: a URL chosen by the client would let anyone embed any address
    (a tracking pixel, for one) on a page every visitor loads."""

    model_config = {"extra": "forbid"}

    display_name: str | None = None
    bio: str | None = None
    avatar_asset_id: str | None = None
    cover_asset_id: str | None = None
    country: str | None = None
    state: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    languages: str | None = None
    lang: str | None = None

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str | None) -> str | None:
        return None if value is None else profilefields.clean_display_name(value)

    @field_validator("bio")
    @classmethod
    def _bio(cls, value: str | None) -> str | None:
        return profilefields.clean_text(value, profilefields.BIO_MAX, multiline=True)

    @field_validator("state")
    @classmethod
    def _state(cls, value: str | None) -> str | None:
        return profilefields.clean_text(value, profilefields.STATE_MAX)

    @field_validator("city")
    @classmethod
    def _city(cls, value: str | None) -> str | None:
        return profilefields.clean_text(value, profilefields.CITY_MAX)

    @field_validator("neighborhood")
    @classmethod
    def _neighborhood(cls, value: str | None) -> str | None:
        return profilefields.clean_text(value, profilefields.NEIGHBOURHOOD_MAX)

    @field_validator("country")
    @classmethod
    def _country(cls, value: str | None) -> str | None:
        return profilefields.clean_country(value)

    @field_validator("languages")
    @classmethod
    def _languages(cls, value: str | None) -> str | None:
        return None if value is None else profilefields.clean_languages(value)

    @field_validator("lang")
    @classmethod
    def _lang(cls, value: str | None) -> str | None:
        return None if value is None else profilefields.clean_lang(value)


CIRCLE_KINDS = "^(family|close_friends|business|customers|smart|custom)$"


class CircleRule(BaseModel):
    """What a smart circle is made of.

    A smart circle has no member list of its own: it is a question asked again
    at every read — "my connections in Nairobi", "the people I follow in
    Kenya". Nothing has to be kept in sync when someone moves, connects or
    unfollows, because nothing was copied.

    ``source`` is the relationship the members come from; ``country`` and
    ``city`` narrow it to where they live, as their own profile states it.
    """

    source: str = Field(pattern="^(connections|followers|following|mutuals)$")
    # Two letters, as profiles store it — "1!" is not a country.
    country: str | None = Field(default=None, pattern="^[A-Za-z]{2}$")
    city: str | None = Field(default=None, max_length=120)

    @field_validator("country")
    @classmethod
    def _upper_country(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("city")
    @classmethod
    def _trim_city(cls, value: str | None) -> str | None:
        # A city of spaces is no city: stored as one, it would be a filter
        # nobody can match, silently emptying the circle.
        value = (value or "").strip()
        return value or None


def _circle_name(value: str | None) -> str | None:
    """Trimmed, and never blank: a circle called "   " is unpickable in every
    menu that lists it."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("A circle needs a name")
    return value


class CircleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="custom", pattern=CIRCLE_KINDS)
    color: str | None = Field(default=None, max_length=20)
    rule: CircleRule | None = None
    description: str | None = Field(default=None, max_length=500)
    icon: str | None = Field(default=None, max_length=30)

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str | None) -> str | None:
        return _circle_name(value)


class CirclePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: str | None = Field(default=None, pattern=CIRCLE_KINDS)
    color: str | None = Field(default=None, max_length=20)
    rule: CircleRule | None = None
    description: str | None = Field(default=None, max_length=500)
    icon: str | None = Field(default=None, max_length=30)
    # True archives it (kept, no longer offered for new posts); False restores it.
    archived: bool | None = None

    @field_validator("name")
    @classmethod
    def _trim_name(cls, value: str | None) -> str | None:
        return _circle_name(value)


class CircleOut(BaseModel):
    id: str
    owner_id: str
    name: str
    kind: str
    color: str | None
    rule: dict | None
    members_count: int
    created_at: datetime
    description: str | None = None
    icon: str | None = None
    archived: bool = False
    last_used_at: datetime | None = None
    # Smart circles: the people the owner left out of the rule.
    excluded_count: int = 0


class CircleMemberOut(BaseModel):
    user_id: str
    handle: str | None
    display_name: str | None
    avatar_url: str | None
    city: str | None
    verified: bool
    # False when the circle can no longer reach them — a block, or the age rule
    # after a disconnection. Listed rather than hidden so the owner can tidy up.
    active: bool


class CircleDetailOut(CircleOut):
    members: list[CircleMemberOut]


class PreferencesIn(BaseModel):
    default_feed_mode: str | None = None
    algorithm_id: str | None = None
    display_mode: str | None = Field(default=None, pattern="^(cloud|light|system)$")
    data_saver: bool | None = None
    reduced_motion: bool | None = None
    autoplay_media: bool | None = None
    wellbeing_enabled: bool | None = None
    daily_limit_minutes: int | None = None
    age_mode: str | None = Field(default=None, pattern="^(child|teen|adult)$")
    translation_auto: bool | None = None
    interest_topics: list[str] | None = None
    who_can_invite: str | None = Field(default=None, pattern="^(everyone|connections|nobody)$")
    who_can_message: str | None = Field(default=None, pattern="^(everyone|connections|nobody)$")
    who_can_add_family: str | None = Field(default=None, pattern="^(everyone|connections|nobody)$")
    who_can_add_community: str | None = Field(default=None, pattern="^(everyone|connections|nobody)$")
    who_can_add_group: str | None = Field(default=None, pattern="^(everyone|connections|nobody)$")
    who_can_invite_group: str | None = Field(default=None, pattern="^(everyone|connections|nobody)$")
    who_can_see_family: str | None = Field(default=None, pattern="^(family|connections|everyone)$")
    family_tree_shared: bool | None = None
    discoverable: bool | None = None
    assistant_visible: bool | None = None


# --- helpers ---------------------------------------------------------------

def _ensure_profile(db: OrmSession, user_id: str) -> models.Profile:
    """Profiles are materialised on demand from auth-service so a member never
    hits a 404 on their own profile because an event was missed."""
    profile = db.get(models.Profile, user_id)
    if profile is not None:
        return profile
    try:
        response = httpx.get(f"{AUTH_URL}/internal/users/{user_id}", timeout=5)
        response.raise_for_status()
        data = response.json()
    except Exception:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile = models.Profile(
        user_id=user_id,
        handle=data["handle"],
        display_name=data["display_name"],
        country=data.get("country"),
        lang=data.get("lang", "en"),
        verified=bool(data.get("kyc_verified")),
    )
    db.add(profile)
    # Only when absent. Preferences and Profile are separate rows with separate
    # lifetimes: a member can have saved settings while their profile row was
    # never materialised, and inserting blindly then violates the primary key
    # and fails the whole request — which is how one unmaterialised author
    # blanked out every author on the page.
    if db.get(models.Preferences, user_id) is None:
        db.add(models.Preferences(user_id=user_id))
    db.commit()
    db.refresh(profile)
    return profile


# --- profiles --------------------------------------------------------------

@app.get("/users/me", response_model=MyProfileOut, tags=["profiles"])
def my_profile(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    return MyProfileOut.model_validate(_ensure_profile(db, principal.user_id))


@app.get("/users/me/eligibility", tags=["profiles"])
def my_profile_eligibility(principal: CurrentUser):
    """Which age-gated profile fields this account may fill, before it tries.

    A readout for the editor, so it can leave a field out rather than refuse it
    after the member typed it. PATCH /users/me enforces the rule on its own and
    does not consult this. Kept off GET /users/me because that is called on
    every page, and the age lookup is a call to auth-service. An age lookup
    that fails reads as a minor, so the field is simply not offered.
    """
    who = ageclient.age_profile(principal.user_id)
    return {"neighborhood": profilefields.may_set_neighbourhood(who)}


# Fields that exist on every profile and cannot be emptied.
_REQUIRED_PROFILE_FIELDS = ("display_name", "languages", "lang")
_PROFILE_IMAGES = ("avatar", "cover")


def _ready_image_url(asset_id: str, owner_id: str, purpose: str) -> str:
    """The URL of an asset this member may put on their profile.

    Fails closed: if media-service cannot confirm the asset, the change is
    refused rather than applied. Not found and not yours get the same answer,
    so asset ids cannot be probed.
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
    """Delete a replaced image. Best effort: the profile change already stands."""
    try:
        httpx.post(f"{MEDIA_URL}/internal/media/{asset_id}/discard", json={"owner_id": owner_id}, timeout=10)
    except httpx.HTTPError as exc:
        log.warning("could not discard replaced image %s: %s", asset_id, exc)


def _sync_identity(user_id: str, changes: dict) -> None:
    """Keep auth-service's copy of the name and interface language in step.

    Deliberately not the country: auth-service's country is the jurisdiction
    the age rules are applied under, set at registration. A profile field that
    moved it would let a minor pick the country with the lowest minimum age.

    Called before the profile is committed and fails closed: if auth-service
    cannot take the change, nothing changes, rather than the two copies of the
    name silently drifting apart.
    """
    if not changes:
        return
    try:
        response = httpx.patch(f"{AUTH_URL}/internal/users/{user_id}", json=changes, timeout=5)
    except httpx.HTTPError as exc:
        log.warning("could not sync identity for %s: %s", user_id, exc)
        response = None
    if response is None or response.status_code != 200:
        if response is not None:
            log.warning("identity sync for %s refused: %s %s", user_id, response.status_code, response.text[:200])
        raise HTTPException(status_code=503, detail="Your profile could not be saved right now. Please try again.")


@app.patch("/users/me", response_model=MyProfileOut, tags=["profiles"])
def update_profile(payload: ProfileUpdate, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True)
    for field in _REQUIRED_PROFILE_FIELDS:
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be empty")

    if changes.get("neighborhood") or changes.get("bio"):
        who = ageclient.age_profile(principal.user_id)
        if changes.get("neighborhood") and not profilefields.may_set_neighbourhood(who):
            raise HTTPException(status_code=403, detail="A neighbourhood cannot be added to this account.")
        if changes.get("bio"):
            problem = profilefields.bio_problem(changes["bio"], author_is_minor=who.is_minor)
            if problem:
                raise HTTPException(status_code=422, detail=problem)

    profile = _ensure_profile(db, principal.user_id)

    replaced: list[str] = []
    for purpose in _PROFILE_IMAGES:
        key = f"{purpose}_asset_id"
        if key not in changes:
            continue
        asset_id = changes.pop(key)
        url = None if asset_id is None else _ready_image_url(asset_id, principal.user_id, purpose)
        previous = getattr(profile, key)
        if previous and previous != asset_id:
            replaced.append(previous)
        setattr(profile, key, asset_id)
        setattr(profile, f"{purpose}_url", url)

    identity = {
        field: changes[field]
        for field in ("display_name", "lang")
        if field in changes and changes[field] != getattr(profile, field)
    }
    _sync_identity(principal.user_id, identity)
    for field, value in changes.items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)

    for asset_id in replaced:
        _discard_image(asset_id, principal.user_id)
    return MyProfileOut.model_validate(profile)


# Declared before /users/{handle}: FastAPI matches in order, and the
# parameterised route would otherwise read "suggestions" as a handle.
@app.get("/users/suggestions", tags=["discovery"])
def people_suggestions(principal: CurrentUser, limit: int = 5, db: OrmSession = Depends(get_db)):
    """People worth following.

    Ranked by follower count, which is the only signal available before there is
    a real graph to mine — and stated as such in the UI rather than dressed up as
    personalisation. Anyone already followed, blocked, or blocking is excluded.

    Minors are not suggested to unrelated adults. Suggestion is where most
    unwanted contact starts, so the cheapest place to stop it is before the
    suggestion is made rather than after somebody acts on it.
    """
    following = set(
        db.scalars(select(models.Follow.followee_id).where(models.Follow.follower_id == principal.user_id)).all()
    )
    blocked = set(
        db.scalars(select(models.Block.blocked_id).where(models.Block.user_id == principal.user_id)).all()
    )
    blocking = set(
        db.scalars(select(models.Block.user_id).where(models.Block.blocked_id == principal.user_id)).all()
    )
    excluded = following | blocked | blocking | {principal.user_id}

    # Where this viewer already stands with each of them. Without it the rail
    # offered "Connect" to somebody you had invited an hour earlier: the button
    # knew it had been pressed, but only until the page was reloaded, because
    # that knowledge lived in the browser and nowhere else. Sending the same
    # invitation twice is refused by the server, so the member was told their
    # request failed for asking again.
    #
    # Not an exclusion: somebody you have asked still belongs in the list, with
    # the answer pending on it.
    standing: dict[str, str] = {}
    for other, status, requester in db.execute(
        select(
            case(
                (models.Connection.requester_id == principal.user_id, models.Connection.addressee_id),
                else_=models.Connection.requester_id,
            ),
            models.Connection.status,
            models.Connection.requester_id,
        ).where(
            or_(
                models.Connection.requester_id == principal.user_id,
                models.Connection.addressee_id == principal.user_id,
            ),
            models.Connection.status.in_(("pending", "accepted")),
        )
    ).all():
        if status == "accepted":
            standing[other] = "connected"
        elif requester == principal.user_id:
            standing[other] = "invited"
        else:
            # They asked us. The button accepts rather than asks again, which
            # is what the invite endpoint already does with such a pair.
            standing[other] = "invites_you"

    # Counted from the follow rows themselves, not profiles.followers_count.
    # That column is only moved by the follow and unfollow buttons; a block
    # deletes follows in both directions and an erased member's follows are
    # deleted with them, neither touching it, so it drifted upwards and the
    # rail put members with fewer real followers above members with more.
    # Every follow row counts - the rule the follow button applies. (It used
    # to need the follower to have a profile row too; profiles are made on
    # first use and the imported members' were never made, so followers who
    # had not opened a profile yet did not count, and somebody with two
    # followers outranked people with more.)
    # Turning `discoverable` off keeps a member out of this rail, as the privacy
    # screen promises - the same rule as /users/search: hidden from strangers,
    # not from the people they already know. Known means connected either way
    # or following either way; those this viewer follows are excluded above,
    # so what is left to add is those who follow the viewer.
    known = {other for other, s in standing.items() if s == "connected"} | set(
        db.scalars(select(models.Follow.follower_id).where(models.Follow.followee_id == principal.user_id)).all()
    )

    counts = (
        select(models.Follow.followee_id.label("user_id"), func.count().label("total"))
        .group_by(models.Follow.followee_id)
        .subquery()
    )
    total = func.coalesce(counts.c.total, 0)
    query = (
        select(models.Profile, total)
        .outerjoin(counts, counts.c.user_id == models.Profile.user_id)
        # Outer join: a member whose preferences row was never materialised is
        # still discoverable by default.
        .outerjoin(models.Preferences, models.Preferences.user_id == models.Profile.user_id)
        .where(
            models.Profile.user_id.not_in(excluded) if excluded else True,
            or_(
                models.Preferences.discoverable.is_(True),
                models.Preferences.user_id.is_(None),
                models.Profile.user_id.in_(known or {""}),
            ),
            # Only members who uploaded a photo. An avatar is set solely from a
            # media asset they own (PATCH /users/me), so both the asset and its
            # URL are there; a blank one is the initials placeholder.
            models.Profile.avatar_asset_id.is_not(None),
            func.coalesce(func.trim(models.Profile.avatar_url), "") != "",
        )
        # user_id last, so equal totals always come back in the same order.
        .order_by(total.desc(), models.Profile.created_at.desc(), models.Profile.user_id)
    )

    # Minors are trimmed out for adult viewers after the query, so the next
    # batch is read whenever trimming left the page short: the rail shows the
    # most-followed members this viewer may see, not the survivors of the
    # first sixty. Bounded, because each candidate costs an age lookup (cached),
    # but far enough down that a page of trimmed candidates does not stop it.
    wanted = min(limit, 20)
    batch = agediscovery.widened(wanted, 60)
    rows: list = []
    followers: dict[str, int] = {}
    for page in range(max(5, 600 // batch)):
        found = db.execute(query.offset(page * batch).limit(batch)).all()
        followers.update({p.user_id: n for p, n in found})
        rows += agediscovery.filter_profiles(principal.user_id, [p for p, _ in found], wanted - len(rows))
        if len(rows) >= wanted or len(found) < batch:
            break

    return {
        "reason": "most_followed",
        "items": [
            {
                "user_id": r.user_id,
                "handle": r.handle,
                "display_name": r.display_name,
                "bio": r.bio,
                "avatar_url": r.avatar_url,
                "city": r.city,
                "country": r.country,
                "followers_count": followers[r.user_id],
                "verified": r.verified,
                # none | invited | invites_you | connected
                "connection": standing.get(r.user_id, "none"),
            }
            for r in rows
        ],
    }


# Declared before /users/{handle} — FastAPI matches in order, and the
# parameterised route would otherwise read "search" as a handle.
@app.get("/users/search", tags=["discovery"])
def search_people(
    q: str,
    principal: CurrentUser,
    limit: int = 10,
    db: OrmSession = Depends(get_db),
):
    """Find someone by handle or name.

    Starting a conversation used to require pasting a raw ``usr_…`` id, which no
    member has and no screen shows — so the messenger was effectively unusable
    for anyone you had not already talked to.

    `discoverable` is honoured here, not just by the suggestions rail: a member
    who turned it off should not surface in a stranger's search. Blocks work in
    both directions — someone you blocked is hidden, and so is someone who
    blocked you, because appearing in their search is exactly what they refused.

    And an adult searching does not find minors at all. A teenager searching
    still finds everybody, including other teenagers: the restriction protects
    the people being listed, not the person looking.
    """
    query = q.strip().lower()
    if len(query) < 2:
        return {"items": []}

    blocked = set(
        db.scalars(select(models.Block.blocked_id).where(models.Block.user_id == principal.user_id)).all()
    )
    blocking = set(
        db.scalars(select(models.Block.user_id).where(models.Block.blocked_id == principal.user_id)).all()
    )
    excluded = blocked | blocking | {principal.user_id}

    # Prefix, not substring: typing "ez" is looking for Ezekiel, and a substring
    # match buried him under every Lopez and Mezu. Any word of the name counts,
    # so a first name and a surname are both a way in; a handle matches at its
    # start or after one of the separators a handle may contain.
    #
    # The member's text is escaped because `%` and `_` are LIKE wildcards: an
    # unescaped "%%" listed every discoverable member on the platform.
    term = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    name = func.lower(models.Profile.display_name)
    handle = func.lower(models.Profile.handle)
    starts = or_(
        name.like(f"{term}%", escape="\\"),
        handle.like(f"{term}%", escape="\\"),
    )
    word_starts = or_(
        name.like(f"% {term}%", escape="\\"),
        name.like(f"%-{term}%", escape="\\"),
        handle.like(f"%.{term}%", escape="\\"),
        handle.like(f"%\\_{term}%", escape="\\"),
    )
    # Turning `discoverable` off hides a member from strangers, not from the
    # people they already know. Before, a member who switched it off could not
    # even be @mentioned by their own connections - the mention picker is this
    # search. Known means: connected either way, or following either way.
    known = set(
        db.scalars(
            select(models.Connection.addressee_id).where(
                models.Connection.requester_id == principal.user_id, models.Connection.status == "accepted"
            )
        ).all()
    ) | set(
        db.scalars(
            select(models.Connection.requester_id).where(
                models.Connection.addressee_id == principal.user_id, models.Connection.status == "accepted"
            )
        ).all()
    ) | set(
        db.scalars(select(models.Follow.followee_id).where(models.Follow.follower_id == principal.user_id)).all()
    ) | set(
        db.scalars(select(models.Follow.follower_id).where(models.Follow.followee_id == principal.user_id)).all()
    )

    # `discoverable` lives on Preferences, not Profile, so this is an outer join:
    # a member whose preferences row was never materialised must still be
    # findable, and an inner join would silently hide them.
    rows = db.scalars(
        select(models.Profile)
        .outerjoin(models.Preferences, models.Preferences.user_id == models.Profile.user_id)
        .where(
            or_(
                models.Preferences.discoverable.is_(True),
                models.Preferences.user_id.is_(None),
                models.Profile.user_id.in_(known or {""}),
            ),
            models.Profile.user_id.not_in(excluded),
            or_(starts, word_starts),
        )
        # Whole-name matches first: "ez" should put Ezekiel above Grace Ezeh.
        .order_by(
            case((starts, 0), else_=1),
            models.Profile.followers_count.desc(),
            name,
        )
        .limit(agediscovery.widened(min(limit, 25), 75))
    ).all()
    rows = agediscovery.filter_profiles(principal.user_id, list(rows), min(limit, 25))

    return {
        "items": [
            {
                "user_id": r.user_id,
                "handle": r.handle,
                "display_name": r.display_name,
                "avatar_url": r.avatar_url,
                "verified": r.verified,
                "city": r.city,
            }
            for r in rows
        ]
    }


@app.get("/users/{handle}", response_model=ProfileOut, tags=["profiles"])
def public_profile(handle: str, db: OrmSession = Depends(get_db)):
    profile = db.scalar(select(models.Profile).where(models.Profile.handle == handle.lower()))
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ProfileOut.model_validate(profile)


# --- follow graph ----------------------------------------------------------

@app.post("/users/{user_id}/follow", status_code=201, tags=["follows"])
def follow(user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot follow yourself")
    # Somebody has to be on the other end. Without this a follow row, and a
    # bump to a followers_count, were written for any string in the path.
    _ensure_profile(db, user_id)
    if db.scalar(
        select(models.Block).where(
            models.Block.user_id == user_id, models.Block.blocked_id == principal.user_id
        )
    ):
        raise HTTPException(status_code=403, detail="You cannot follow this account")

    exists = db.scalar(
        select(models.Follow).where(
            models.Follow.follower_id == principal.user_id, models.Follow.followee_id == user_id
        )
    )
    if exists:
        return {"following": True, "already": True}

    db.add(models.Follow(follower_id=principal.user_id, followee_id=user_id))
    for uid, column in ((user_id, "followers_count"), (principal.user_id, "following_count")):
        profile = db.get(models.Profile, uid)
        if profile is not None:
            setattr(profile, column, getattr(profile, column) + 1)
    db.commit()

    mine = db.get(models.Profile, principal.user_id)
    notify.notify(
        user_id,
        kind="follow",
        title=f"{mine.display_name if mine else 'Someone'} started following you",
        link=f"/u/{mine.handle}" if mine else None,
    )
    return {"following": True}


@app.delete("/users/{user_id}/follow", status_code=204, tags=["follows"])
def unfollow(user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    removed = db.execute(
        delete(models.Follow).where(
            models.Follow.follower_id == principal.user_id, models.Follow.followee_id == user_id
        )
    ).rowcount
    if removed:
        for uid, column in ((user_id, "followers_count"), (principal.user_id, "following_count")):
            profile = db.get(models.Profile, uid)
            if profile is not None:
                setattr(profile, column, max(0, getattr(profile, column) - 1))
    db.commit()


@app.get("/users/me/following", tags=["follows"])
def following(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    ids = db.scalars(
        select(models.Follow.followee_id).where(models.Follow.follower_id == principal.user_id)
    ).all()
    return {"count": len(ids), "user_ids": list(ids)}


# --- circles ---------------------------------------------------------------
#
# A circle is a private list. Its owner decides who is in it; nobody is asked
# and nobody is told, the way a contact group in a phone works. What it controls
# is reach: a post shared to a circle is read by its members and its author, and
# by no one else.
#
# Membership is therefore checked when a post is *read*, not copied anywhere
# when it is written. social-service asks /internal/viewer-audience on every
# feed, profile and post read, so removing someone, blocking them or
# disconnecting from a teenager takes effect on the very next request.

# One sentence for every refusal. Telling an adult *why* a teenager cannot be
# added — their age, a block, a missing connection — tells them which of those
# to work around.
CIRCLE_REFUSAL = "This member cannot be added to a circle."

# Bounds, because every read of a circle post recomputes its audience: an owner
# with ten thousand circles, or a circle of a million rows, would make every
# feed request of every member pay for it. Generous for a person; tight for a
# script.
MAX_CIRCLES_PER_OWNER = 100
MAX_MEMBERS_PER_CIRCLE = 5000


def _own_circle(db: OrmSession, circle_id: str, owner_id: str) -> models.Circle:
    circle = db.get(models.Circle, circle_id)
    if circle is None or circle.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Circle not found")
    return circle


def _rule(circle: models.Circle) -> dict | None:
    if not circle.rule:
        return None
    try:
        data = json.loads(circle.rule)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _blocked_with(db: OrmSession, user_id: str) -> set[str]:
    """Everyone this member blocked, and everyone who blocked them."""
    rows = db.execute(
        select(models.Block.user_id, models.Block.blocked_id).where(
            or_(models.Block.user_id == user_id, models.Block.blocked_id == user_id)
        )
    ).all()
    return {blocked if owner == user_id else owner for owner, blocked in rows}


def _connections_of(db: OrmSession, user_id: str) -> set[str]:
    rows = db.execute(
        select(models.Connection.requester_id, models.Connection.addressee_id).where(
            models.Connection.status == "accepted",
            or_(models.Connection.requester_id == user_id, models.Connection.addressee_id == user_id),
        )
    ).all()
    return {b if a == user_id else a for a, b in rows}


def _followers_of(db: OrmSession, user_id: str) -> set[str]:
    return set(db.scalars(select(models.Follow.follower_id).where(models.Follow.followee_id == user_id)).all())


def _following_of(db: OrmSession, user_id: str) -> set[str]:
    return set(db.scalars(select(models.Follow.followee_id).where(models.Follow.follower_id == user_id)).all())


def _rule_source(db: OrmSession, owner_id: str, source: str) -> set[str]:
    """The people a smart circle's ``source`` names, before any place filter."""
    if source == "connections":
        return _connections_of(db, owner_id)
    if source == "followers":
        return _followers_of(db, owner_id)
    if source == "following":
        return _following_of(db, owner_id)
    if source == "mutuals":
        return _followers_of(db, owner_id) & _following_of(db, owner_id)
    return set()


def _in_place(profile: models.Profile | None, rule: dict) -> bool:
    """Whether a member lives where the rule asks, by their own profile.

    A member who has not said where they live matches no place: a rule for
    "Nairobi" must not quietly include everyone who left the field empty.
    """
    country, city = rule.get("country"), rule.get("city")
    if not country and not city:
        return True
    if profile is None:
        return False
    if country and (profile.country or "").upper() != country.upper():
        return False
    if city and (profile.city or "").strip().casefold() != city.strip().casefold():
        return False
    return True


def _reachable(owner_id: str, member_id: str, connected: bool) -> bool:
    """Whether ``owner``'s circles may carry posts to ``member``.

    The messaging rule, applied to the other private channel. A circle post
    lands in its members' feeds unasked, which is exactly what an unsolicited
    message does — so an adult reaches an unconnected minor through neither.
    Ages come from the identity record; a failed lookup is treated as a minor,
    so an outage narrows reach instead of widening it.
    """
    owner = ageclient.age_profile(owner_id)
    member = ageclient.age_profile(member_id)
    return engine.can_message_user(owner, member, connected=connected).allowed


def _circle_members(db: OrmSession, circle: models.Circle) -> list[tuple[str, bool]]:
    """``(member_id, active)`` for this circle.

    A static circle lists everyone its owner added, flagging those it can no
    longer reach. A smart circle lists only the people its rule reaches right
    now — it has nobody else to show, because it holds no list.
    """
    blocked = _blocked_with(db, circle.owner_id)
    connections = _connections_of(db, circle.owner_id)

    if circle.kind == "smart":
        rule = _rule(circle) or {}
        excluded = set(db.scalars(select(models.CircleExclusion.member_id).where(models.CircleExclusion.circle_id == circle.id)).all())
        candidates = _rule_source(db, circle.owner_id, rule.get("source", "")) - {circle.owner_id} - blocked - excluded
        if not candidates:
            return []
        profiles = {
            p.user_id: p
            for p in db.scalars(select(models.Profile).where(models.Profile.user_id.in_(candidates))).all()
        }
        return [
            (member_id, True)
            for member_id in sorted(candidates)
            if _in_place(profiles.get(member_id), rule)
            and _reachable(circle.owner_id, member_id, member_id in connections)
        ]

    member_ids = db.scalars(
        select(models.CircleMember.member_id)
        .where(models.CircleMember.circle_id == circle.id)
        .order_by(models.CircleMember.added_at)
    ).all()
    return [
        (
            member_id,
            member_id not in blocked and _reachable(circle.owner_id, member_id, member_id in connections),
        )
        for member_id in member_ids
    ]


def _circles_reaching(db: OrmSession, viewer_id: str) -> set[str]:
    """Every circle whose posts this member may read."""
    reached: set[str] = set()
    blocked = _blocked_with(db, viewer_id)
    connections = _connections_of(db, viewer_id)

    # Static circles that list them.
    for circle_id, owner_id in db.execute(
        select(models.Circle.id, models.Circle.owner_id)
        .join(models.CircleMember, models.CircleMember.circle_id == models.Circle.id)
        .where(models.CircleMember.member_id == viewer_id, models.Circle.kind != "smart")
    ).all():
        if owner_id not in blocked and _reachable(owner_id, viewer_id, owner_id in connections):
            reached.add(circle_id)

    # Smart circles whose question they answer. Only owners related to the
    # viewer can have one, so only those circles are read.
    following = _following_of(db, viewer_id)  # owners whose followers include the viewer
    followers = _followers_of(db, viewer_id)  # owners who follow the viewer
    owners = (connections | following | followers) - blocked - {viewer_id}
    if owners:
        me = db.get(models.Profile, viewer_id)
        left_out = set(db.scalars(select(models.CircleExclusion.circle_id).where(models.CircleExclusion.member_id == viewer_id)).all())
        for circle in db.scalars(
            select(models.Circle).where(models.Circle.kind == "smart", models.Circle.owner_id.in_(owners))
        ).all():
            if circle.id in left_out:
                continue
            rule = _rule(circle) or {}
            owner_id = circle.owner_id
            related = {
                "connections": owner_id in connections,
                "followers": owner_id in following,
                "following": owner_id in followers,
                "mutuals": owner_id in following and owner_id in followers,
            }.get(rule.get("source", ""), False)
            if related and _in_place(me, rule) and _reachable(owner_id, viewer_id, owner_id in connections):
                reached.add(circle.id)
    return reached


def _circle_out(db: OrmSession, circle: models.Circle, members: list[tuple[str, bool]] | None = None) -> dict:
    members = _circle_members(db, circle) if members is None else members
    return {
        "id": circle.id,
        "owner_id": circle.owner_id,
        "name": circle.name,
        "kind": circle.kind,
        "color": circle.color,
        "rule": _rule(circle),
        # Who a post shared to it would actually reach, not who was ever added.
        "members_count": sum(1 for _, active in members if active),
        "created_at": circle.created_at,
        "description": circle.description,
        "icon": circle.icon,
        "archived": circle.archived_at is not None,
        "last_used_at": circle.last_used_at,
        "excluded_count": db.scalar(
            select(func.count()).select_from(models.CircleExclusion).where(models.CircleExclusion.circle_id == circle.id)
        ) or 0 if circle.kind == "smart" else 0,
    }


def _circle_detail(db: OrmSession, circle: models.Circle) -> dict:
    members = _circle_members(db, circle)
    profiles = {
        p.user_id: p
        for p in db.scalars(
            select(models.Profile).where(models.Profile.user_id.in_([m for m, _ in members]))
        ).all()
    } if members else {}
    return {
        **_circle_out(db, circle, members),
        "members": [
            {
                "user_id": member_id,
                "handle": profiles[member_id].handle if member_id in profiles else None,
                "display_name": profiles[member_id].display_name if member_id in profiles else None,
                "avatar_url": profiles[member_id].avatar_url if member_id in profiles else None,
                "city": profiles[member_id].city if member_id in profiles else None,
                "verified": bool(profiles[member_id].verified) if member_id in profiles else False,
                "active": active,
            }
            for member_id, active in members
        ],
    }


def _stored_rule(rule: CircleRule) -> str:
    # The validators have already upper-cased the country and dropped a blank city.
    return json.dumps(rule.model_dump(exclude_none=True))


@app.get("/circles", response_model=list[CircleOut], tags=["circles"])
def list_circles(
    principal: CurrentUser,
    sort: str = "created",
    archived: bool | None = None,
    db: OrmSession = Depends(get_db),
):
    """My circles, sorted by name, recent use, size or creation. ``archived``
    true/false narrows to archived or active ones; absent, both."""
    stmt = select(models.Circle).where(models.Circle.owner_id == principal.user_id)
    if archived is True:
        stmt = stmt.where(models.Circle.archived_at.is_not(None))
    elif archived is False:
        stmt = stmt.where(models.Circle.archived_at.is_(None))
    rows = db.scalars(stmt.order_by(models.Circle.created_at)).all()
    out = [_circle_out(db, row) for row in rows]
    if sort == "name":
        out.sort(key=lambda c: c["name"].casefold())
    elif sort == "recent":
        out.sort(key=lambda c: (c["last_used_at"] is None, -(c["last_used_at"].timestamp() if c["last_used_at"] else 0)))
    elif sort == "size":
        out.sort(key=lambda c: -c["members_count"])
    return out


@app.post("/circles", response_model=CircleDetailOut, status_code=201, tags=["circles"])
def create_circle(payload: CircleIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if payload.kind == "smart" and payload.rule is None:
        raise HTTPException(status_code=400, detail="A smart circle needs a rule: who it is made of.")
    if payload.kind != "smart" and payload.rule is not None:
        raise HTTPException(status_code=400, detail="Only a smart circle has a rule.")
    owned = db.scalar(
        select(func.count()).select_from(models.Circle).where(models.Circle.owner_id == principal.user_id)
    ) or 0
    if owned >= MAX_CIRCLES_PER_OWNER:
        raise HTTPException(
            status_code=400, detail=f"You can have up to {MAX_CIRCLES_PER_OWNER} circles. Delete one to make another."
        )
    circle = models.Circle(
        id=new_id("cir"),
        owner_id=principal.user_id,
        name=payload.name,
        kind=payload.kind,
        color=payload.color,
        rule=_stored_rule(payload.rule) if payload.rule else None,
        description=(payload.description or "").strip() or None,
        icon=payload.icon,
    )
    db.add(circle)
    db.commit()
    db.refresh(circle)
    return _circle_detail(db, circle)


@app.get("/circles/{circle_id}", response_model=CircleDetailOut, tags=["circles"])
def get_circle(circle_id: str, principal: CurrentUser, q: str | None = None, db: OrmSession = Depends(get_db)):
    """One circle and its members — for its owner only. Being in someone's
    circle does not entitle you to see who else is. ``q`` filters the members
    by name, handle or city."""
    detail = _circle_detail(db, _own_circle(db, circle_id, principal.user_id))
    if q and q.strip():
        needle = q.strip().casefold()
        detail["members"] = [
            m for m in detail["members"]
            if needle in (m["display_name"] or "").casefold() or needle in (m["handle"] or "").casefold() or needle in (m["city"] or "").casefold()
        ]
    return detail


@app.patch("/circles/{circle_id}", response_model=CircleDetailOut, tags=["circles"])
def update_circle(circle_id: str, payload: CirclePatch, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Rename, recolour, change kind, or rewrite a smart circle's rule.

    Turning a circle smart replaces its member list with the rule — the list is
    deleted rather than kept dormant, because a list nobody can see would come
    back to life the day the circle is turned static again. Turning a smart
    circle static starts it empty for the same reason.
    """
    circle = _own_circle(db, circle_id, principal.user_id)
    if payload.name is not None:
        circle.name = payload.name
    if "color" in payload.model_fields_set:
        circle.color = payload.color
    if "description" in payload.model_fields_set:
        circle.description = (payload.description or "").strip() or None
    if "icon" in payload.model_fields_set:
        circle.icon = payload.icon
    if payload.archived is not None:
        circle.archived_at = (circle.archived_at or datetime.now(timezone.utc)) if payload.archived else None

    kind = payload.kind or circle.kind
    if kind == "smart":
        if payload.rule is None and circle.kind != "smart":
            raise HTTPException(status_code=400, detail="A smart circle needs a rule: who it is made of.")
        if circle.kind != "smart":
            db.execute(delete(models.CircleMember).where(models.CircleMember.circle_id == circle.id))
            circle.members_count = 0
        if payload.rule is not None:
            circle.rule = _stored_rule(payload.rule)
    else:
        if payload.rule is not None:
            raise HTTPException(status_code=400, detail="Only a smart circle has a rule.")
        circle.rule = None
    circle.kind = kind

    db.commit()
    db.refresh(circle)
    return _circle_detail(db, circle)


@app.post("/circles/{circle_id}/members/{member_id}", status_code=201, tags=["circles"])
def add_to_circle(circle_id: str, member_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    circle = _own_circle(db, circle_id, principal.user_id)
    if circle.kind == "smart":
        raise HTTPException(
            status_code=400, detail="A smart circle is filled by its rule — change the rule instead."
        )
    if member_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You already see everything you share to your circles.")
    try:
        _ensure_profile(db, member_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Member not found")

    connected = member_id in _connections_of(db, principal.user_id)
    if member_id in _blocked_with(db, principal.user_id) or not _reachable(principal.user_id, member_id, connected):
        raise HTTPException(status_code=403, detail=CIRCLE_REFUSAL)

    if db.scalar(
        select(models.CircleMember).where(
            models.CircleMember.circle_id == circle_id, models.CircleMember.member_id == member_id
        )
    ):
        return {"added": False, "already": True, "members_count": _circle_out(db, circle)["members_count"]}
    size = db.scalar(
        select(func.count()).select_from(models.CircleMember).where(models.CircleMember.circle_id == circle_id)
    ) or 0
    if size >= MAX_MEMBERS_PER_CIRCLE:
        raise HTTPException(
            status_code=400, detail=f"A circle holds up to {MAX_MEMBERS_PER_CIRCLE} people."
        )
    db.add(models.CircleMember(circle_id=circle_id, member_id=member_id))
    circle.members_count += 1
    try:
        db.commit()
    except IntegrityError:
        # Two taps in flight at once: the unique constraint let one through and
        # the other is simply "already there", not a server error.
        db.rollback()
        return {"added": False, "already": True, "members_count": _circle_out(db, circle)["members_count"]}
    return {"added": True, "members_count": _circle_out(db, circle)["members_count"]}


@app.delete("/circles/{circle_id}/members/{member_id}", status_code=204, tags=["circles"])
def remove_from_circle(circle_id: str, member_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    circle = _own_circle(db, circle_id, principal.user_id)
    removed = db.execute(
        delete(models.CircleMember).where(
            models.CircleMember.circle_id == circle_id, models.CircleMember.member_id == member_id
        )
    ).rowcount
    if removed:
        circle.members_count = max(0, circle.members_count - 1)
    db.commit()


@app.delete("/circles/{circle_id}", status_code=204, tags=["circles"])
def delete_circle(circle_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Posts already shared to it stay with their author and reach nobody else:
    the circle they named no longer reaches anyone."""
    circle = _own_circle(db, circle_id, principal.user_id)
    db.delete(circle)
    db.commit()


class BulkMembersIn(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=500)
    remove: list[str] = Field(default_factory=list, max_length=500)


@app.post("/circles/{circle_id}/bulk-members", tags=["circles"])
def bulk_members(circle_id: str, payload: BulkMembersIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Add and remove several people at once. Each person is checked as a single
    add would be; the ones that cannot be added are named in the answer.
    Nobody is told they were added or removed."""
    circle = _own_circle(db, circle_id, principal.user_id)
    if circle.kind == "smart" and payload.add:
        raise HTTPException(status_code=400, detail="A smart circle is filled by its rule — change the rule instead.")
    blocked = _blocked_with(db, principal.user_id)
    connections = _connections_of(db, principal.user_id)
    present = set(db.scalars(select(models.CircleMember.member_id).where(models.CircleMember.circle_id == circle_id)).all())
    added, refused = [], []
    for member_id in dict.fromkeys(payload.add):
        if member_id in present or member_id == principal.user_id:
            continue
        try:
            _ensure_profile(db, member_id)
        except HTTPException:
            refused.append(member_id)
            continue
        if member_id in blocked or not _reachable(principal.user_id, member_id, member_id in connections):
            refused.append(member_id)
            continue
        if len(present) + len(added) >= MAX_MEMBERS_PER_CIRCLE:
            refused.append(member_id)
            continue
        db.add(models.CircleMember(circle_id=circle_id, member_id=member_id))
        added.append(member_id)
    removed = 0
    if payload.remove:
        removed = db.execute(
            delete(models.CircleMember).where(models.CircleMember.circle_id == circle_id, models.CircleMember.member_id.in_(payload.remove))
        ).rowcount or 0
    circle.members_count = max(0, circle.members_count + len(added) - removed)
    db.commit()
    return {"added": added, "removed": removed, "refused": refused, "members_count": _circle_out(db, circle)["members_count"]}


class DuplicateIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@app.post("/circles/{circle_id}/duplicate", response_model=CircleDetailOut, status_code=201, tags=["circles"])
def duplicate_circle(circle_id: str, payload: DuplicateIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """A new circle starting from one of mine: the same people (or rule and
    exclusions). Only an owner can copy their own circle."""
    source = _own_circle(db, circle_id, principal.user_id)
    owned = db.scalar(select(func.count()).select_from(models.Circle).where(models.Circle.owner_id == principal.user_id)) or 0
    if owned >= MAX_CIRCLES_PER_OWNER:
        raise HTTPException(status_code=400, detail=f"You can have up to {MAX_CIRCLES_PER_OWNER} circles. Delete one to make another.")
    copy = models.Circle(
        id=new_id("cir"), owner_id=principal.user_id, name=_circle_name(payload.name), kind=source.kind,
        color=source.color, rule=source.rule, description=source.description, icon=source.icon,
    )
    db.add(copy)
    db.flush()
    for member_id in db.scalars(select(models.CircleMember.member_id).where(models.CircleMember.circle_id == source.id)).all():
        db.add(models.CircleMember(circle_id=copy.id, member_id=member_id))
        copy.members_count += 1
    for member_id in db.scalars(select(models.CircleExclusion.member_id).where(models.CircleExclusion.circle_id == source.id)).all():
        db.add(models.CircleExclusion(circle_id=copy.id, member_id=member_id))
    db.commit()
    db.refresh(copy)
    return _circle_detail(db, copy)


@app.post("/circles/{circle_id}/exclusions/{member_id}", status_code=201, tags=["circles"])
def exclude_from_smart(circle_id: str, member_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Leave somebody out of a smart circle whatever its rule says."""
    circle = _own_circle(db, circle_id, principal.user_id)
    if circle.kind != "smart":
        raise HTTPException(status_code=400, detail="Only a smart circle has exclusions: remove the person instead.")
    if not db.scalar(select(models.CircleExclusion).where(models.CircleExclusion.circle_id == circle_id, models.CircleExclusion.member_id == member_id)):
        db.add(models.CircleExclusion(circle_id=circle_id, member_id=member_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {"excluded": True, "members_count": _circle_out(db, circle)["members_count"]}


@app.delete("/circles/{circle_id}/exclusions/{member_id}", status_code=204, tags=["circles"])
def include_in_smart(circle_id: str, member_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _own_circle(db, circle_id, principal.user_id)
    db.execute(delete(models.CircleExclusion).where(models.CircleExclusion.circle_id == circle_id, models.CircleExclusion.member_id == member_id))
    db.commit()


@app.get("/circles/{circle_id}/exclusions", tags=["circles"])
def smart_exclusions(circle_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    _own_circle(db, circle_id, principal.user_id)
    ids = db.scalars(select(models.CircleExclusion.member_id).where(models.CircleExclusion.circle_id == circle_id)).all()
    profiles = {p.user_id: p for p in db.scalars(select(models.Profile).where(models.Profile.user_id.in_(ids))).all()} if ids else {}
    return {"items": [{"user_id": i, "display_name": profiles[i].display_name if i in profiles else None, "handle": profiles[i].handle if i in profiles else None} for i in ids]}


@app.post("/circles/smart-preview", tags=["circles"])
def smart_preview(rule: CircleRule, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Who a smart rule would reach right now, before it is saved. Only the
    owner's own relationships and what members state on their own profile."""
    probe = models.Circle(id="preview", owner_id=principal.user_id, name="preview", kind="smart", rule=_stored_rule(rule))
    members = _circle_members(db, probe)
    profiles = {p.user_id: p for p in db.scalars(select(models.Profile).where(models.Profile.user_id.in_([m for m, _ in members]))).all()} if members else {}
    return {
        "count": len(members),
        "items": [
            {"user_id": m, "display_name": profiles[m].display_name if m in profiles else None, "handle": profiles[m].handle if m in profiles else None, "city": profiles[m].city if m in profiles else None}
            for m, _ in members[:200]
        ],
    }


def _resolve_audience(db: OrmSession, owner_id: str, circle_ids: list[str], include_ids: list[str], exclude_ids: list[str]) -> dict:
    """Who a post with this audience reaches right now: everybody in the chosen
    circles (once each), plus the people added by name, minus the people left
    out - an exclusion wins over any circle. Blocks and the age rule apply to
    the people added by name exactly as to circle members."""
    circles = [c for c in db.scalars(select(models.Circle).where(models.Circle.id.in_(circle_ids), models.Circle.owner_id == owner_id)).all()]
    if len(circles) != len(set(circle_ids)):
        raise HTTPException(status_code=404, detail="Circle not found")
    eligible: dict[str, list[str]] = {}
    for circle in circles:
        for member_id, active in _circle_members(db, circle):
            if active:
                eligible.setdefault(member_id, []).append(circle.id)
    blocked = _blocked_with(db, owner_id)
    connections = _connections_of(db, owner_id)
    refused = []
    for member_id in dict.fromkeys(include_ids):
        if member_id == owner_id:
            continue
        try:
            _ensure_profile(db, member_id)
        except HTTPException:
            refused.append(member_id)
            continue
        if member_id in blocked or not _reachable(owner_id, member_id, member_id in connections):
            refused.append(member_id)
            continue
        eligible.setdefault(member_id, []).append("person")
    excluded = [m for m in dict.fromkeys(exclude_ids) if m in eligible]
    for member_id in exclude_ids:
        eligible.pop(member_id, None)
    return {
        "ids": sorted(eligible),
        "excluded": excluded,
        "refused": refused,
        "smart": any(c.kind == "smart" for c in circles),
        "archived": [c.id for c in circles if c.archived_at is not None],
        "names": {c.id: c.name for c in circles},
    }


class AudienceIn(BaseModel):
    circle_ids: list[str] = Field(default_factory=list, max_length=20)
    include_ids: list[str] = Field(default_factory=list, max_length=500)
    exclude_ids: list[str] = Field(default_factory=list, max_length=500)


@app.post("/circles/audience-preview", tags=["circles"])
def audience_preview(payload: AudienceIn, principal: CurrentUser, q: str | None = None, db: OrmSession = Depends(get_db)):
    """Who would see a post with this audience: the count, the people (searchable),
    who is left out, and whether it can change later. For the author only."""
    out = _resolve_audience(db, principal.user_id, payload.circle_ids, payload.include_ids, payload.exclude_ids)
    ids = out["ids"]
    profiles = {p.user_id: p for p in db.scalars(select(models.Profile).where(models.Profile.user_id.in_(ids + out["excluded"] + out["refused"]))).all()} if ids or out["excluded"] or out["refused"] else {}

    def person(i: str) -> dict:
        p = profiles.get(i)
        return {"user_id": i, "display_name": p.display_name if p else None, "handle": p.handle if p else None, "avatar_url": p.avatar_url if p else None}

    people = [person(i) for i in ids]
    if q and q.strip():
        needle = q.strip().casefold()
        people = [x for x in people if needle in (x["display_name"] or "").casefold() or needle in (x["handle"] or "").casefold()]
    return {
        "count": len(ids),
        "people": people[:300],
        "excluded": [person(i) for i in out["excluded"]],
        "refused": [person(i) for i in out["refused"]],
        "includes_smart_circle": out["smart"],
        "archived_circles": out["archived"],
    }


class ResolveIn(AudienceIn):
    owner_id: str


@app.post("/internal/audience/resolve", tags=["internal"])
def internal_resolve(payload: ResolveIn, db: OrmSession = Depends(get_db)):
    """social-service: who an audience reaches now (snapshots, previews, checks)."""
    out = _resolve_audience(db, payload.owner_id, payload.circle_ids, payload.include_ids, payload.exclude_ids)
    return {k: out[k] for k in ("ids", "excluded", "refused", "smart", "archived", "names")}


class CirclesUsedIn(BaseModel):
    owner_id: str
    circle_ids: list[str] = Field(default_factory=list, max_length=20)


@app.post("/internal/circles/check", tags=["internal"])
def internal_circles_check(payload: CirclesUsedIn, db: OrmSession = Depends(get_db)):
    """Whether these are the author's own, usable circles - and mark them used.
    An archived circle is not offered for new posts."""
    rows = db.scalars(select(models.Circle).where(models.Circle.id.in_(payload.circle_ids))).all()
    if len(rows) != len(set(payload.circle_ids)) or any(r.owner_id != payload.owner_id for r in rows):
        raise HTTPException(status_code=404, detail="Circle not found")
    if any(r.archived_at for r in rows):
        raise HTTPException(status_code=409, detail="An archived circle cannot be chosen for a new post. Restore it first.")
    now = datetime.now(timezone.utc)
    for r in rows:
        r.last_used_at = now
    db.commit()
    return {"ok": True, "names": {r.id: r.name for r in rows}}


@app.get("/internal/circles/names", tags=["internal"])
def internal_circle_names(owner_id: str, ids: str, db: OrmSession = Depends(get_db)):
    """Names of an owner's circles, for that owner's own screens only."""
    wanted = [i for i in ids.split(",") if i][:50]
    rows = db.scalars(select(models.Circle).where(models.Circle.id.in_(wanted), models.Circle.owner_id == owner_id)).all()
    return {"names": {r.id: r.name for r in rows}}


@app.get("/internal/circles/{circle_id}/owner", tags=["internal"])
def circle_owner(circle_id: str, db: OrmSession = Depends(get_db)):
    """Who owns a circle — so a post can only be shared to its author's own.
    Without this check, knowing another member's circle id was enough to
    publish into their circle."""
    circle = db.get(models.Circle, circle_id)
    if circle is None:
        raise HTTPException(status_code=404, detail="Circle not found")
    return {"owner_id": circle.owner_id}


@app.get("/internal/viewer-audience/{user_id}", tags=["internal"])
def viewer_audience(user_id: str, db: OrmSession = Depends(get_db)):
    """Whose restricted posts this member may read.

    ``following`` opens followers-only posts, ``circles`` opens circle posts.
    social-service asks on every read; both answers are computed here, where
    the follow graph, the blocks and the circles live, rather than copied into
    the post rows where they would go stale.
    """
    blocked = _blocked_with(db, user_id)
    return {
        "following": sorted(_following_of(db, user_id) - blocked),
        "circles": sorted(_circles_reaching(db, user_id)),
        # Connections-only posts, and who has a block with this member (a post
        # kept for its audience at publication still closes on a block).
        "connections": sorted(_connections_of(db, user_id) - blocked),
        "blocked": sorted(blocked),
    }


# --- preferences -----------------------------------------------------------

# ---------------------------------------------------------------------------
# Parental supervision
# ---------------------------------------------------------------------------

class SupervisionInviteIn(BaseModel):
    """Either side may start it; the other has to agree."""

    other_handle: str = Field(min_length=3, max_length=40)


class SupervisionAnswerIn(BaseModel):
    approve: bool


class TimeLimitIn(BaseModel):
    daily_limit_minutes: int | None = Field(default=None, ge=0, le=1440)


@app.get("/supervision/disclosure", tags=["supervision"])
def supervision_disclosure():
    """What a parent can and cannot see, in plain words.

    Public, and returned by the API rather than buried in a policy document,
    so neither the parent nor the teenager has to take anybody's word for what
    was agreed. Read it before inviting, read it before accepting.
    """
    return parental.disclosure()


@app.get("/supervision", tags=["supervision"])
def my_supervision(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Every supervision link this member is part of, from either side."""
    links = parental.links_for(db, principal.user_id)
    # A teenager sees their own requests and the answers, including the refusals.
    # A setting that will not move with no record of why is how a teenager learns
    # to look for a way round the product instead of asking.
    requests = db.scalars(
        select(models.SupervisionRequest)
        .where(
            or_(
                models.SupervisionRequest.teen_id == principal.user_id,
                models.SupervisionRequest.parent_id == principal.user_id,
            )
        )
        .order_by(models.SupervisionRequest.created_at.desc())
        .limit(50)
    ).all()
    return {
        "items": [parental.link_out(link, principal.user_id) for link in links],
        "requests": [
            {
                "id": r.id, "setting": r.setting, "requested_value": r.requested_value,
                "status": r.status, "created_at": r.created_at, "answered_at": r.answered_at,
                "role": "parent" if r.parent_id == principal.user_id else "teen",
            }
            for r in requests
        ],
        "disclosure": parental.disclosure(),
    }


@app.post("/supervision/invite", status_code=201, tags=["supervision"])
def invite_supervision(
    payload: SupervisionInviteIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Invite somebody into a supervision link.

    Whoever is the minor becomes the supervised party; the ages decide the
    roles, not whoever happened to send the invitation. A parent cannot
    nominate themselves as the teenager, and a teenager inviting an adult is
    asking to be supervised rather than to supervise.
    """
    other = db.scalar(
        select(models.Profile).where(models.Profile.handle == payload.other_handle.strip().lower())
    )
    if other is None or other.user_id == principal.user_id:
        raise HTTPException(status_code=404, detail="No such member")

    me = ageclient.age_profile(principal.user_id)
    them = ageclient.age_profile(other.user_id)

    if me.is_minor and them.is_minor:
        raise HTTPException(
            status_code=400,
            detail="A supervising adult has to be an adult account.",
        )
    if not me.is_minor and not them.is_minor:
        raise HTTPException(
            status_code=400,
            detail="Supervision is for accounts under 18.",
        )

    teen_id, parent_id = (
        (principal.user_id, other.user_id) if me.is_minor else (other.user_id, principal.user_id)
    )

    if parental.active_link(db, teen_id) is not None:
        raise HTTPException(status_code=409, detail="This account already has a supervising adult.")

    existing = db.scalar(
        select(models.ParentalSupervision).where(
            models.ParentalSupervision.teen_id == teen_id,
            models.ParentalSupervision.parent_id == parent_id,
            models.ParentalSupervision.status == "invited",
        )
    )
    if existing is not None:
        return {**parental.link_out(existing, principal.user_id), "existing": True}

    link = models.ParentalSupervision(
        id=new_id("sup"), teen_id=teen_id, parent_id=parent_id,
        status="invited", invited_by=principal.user_id,
    )
    db.add(link)
    db.commit()

    other_id = teen_id if principal.user_id == parent_id else parent_id
    notify.notify(
        other_id, "supervision_invite",
        "Someone invited you to a supervision link",
        body="Read what it does and does not share before you accept.",
        link="/settings/supervision",
    )
    return {**parental.link_out(link, principal.user_id), "existing": False}


@app.post("/supervision/{link_id}/answer", tags=["supervision"])
def answer_supervision(
    link_id: str, payload: SupervisionAnswerIn, principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """Accept or decline an invitation. Only the invited side may answer."""
    link = db.get(models.ParentalSupervision, link_id)
    if link is None or principal.user_id not in (link.teen_id, link.parent_id):
        raise HTTPException(status_code=404, detail="Not found")
    if principal.user_id == link.invited_by:
        raise HTTPException(status_code=403, detail="The other person has to answer this.")
    if link.status != "invited":
        raise HTTPException(status_code=409, detail="This invitation has already been answered.")

    link.status = "active" if payload.approve else "declined"
    if payload.approve:
        link.accepted_at = parental.now()
    db.commit()

    notify.notify(
        link.invited_by, "supervision_answer",
        "Your supervision invitation was accepted" if payload.approve
        else "Your supervision invitation was declined",
        link="/settings/supervision",
    )
    return parental.link_out(link, principal.user_id)


@app.post("/supervision/{link_id}/end", tags=["supervision"])
def end_supervision(link_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """End supervision. Either side may, and the other is told.

    The teenager can do this themselves on purpose: supervision somebody cannot
    leave is not supervision. What leaving does **not** do is unlock anything —
    the account returns to the defaults for its age, so removing a parent is
    never the way to get permissions.
    """
    link = db.get(models.ParentalSupervision, link_id)
    if link is None or principal.user_id not in (link.teen_id, link.parent_id):
        raise HTTPException(status_code=404, detail="Not found")
    if link.status not in ("invited", "active"):
        raise HTTPException(status_code=409, detail="This link has already ended.")

    link.status = "ended"
    link.ended_at = parental.now()
    link.ended_by = principal.user_id
    parental.revert_to_strictest(db, link.teen_id)
    db.commit()

    other = link.parent_id if principal.user_id == link.teen_id else link.teen_id
    notify.notify(
        other, "supervision_ended", "A supervision link has ended",
        link="/settings/supervision",
    )
    return {
        **parental.link_out(link, principal.user_id),
        "note": "Settings were returned to the defaults for this account's age group.",
    }


@app.get("/supervision/{link_id}/view", tags=["supervision"])
def supervised_view(link_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """What the parent is shown. Exactly the list in the disclosure.

    There is no endpoint here for messages, contacts, posts or searches, and
    that is the design rather than an omission: a teenager who believes their
    parent can read their private messages stops using private messages for the
    things private messages are for, including telling somebody that an adult
    is pressuring them.
    """
    link = db.get(models.ParentalSupervision, link_id)
    if link is None or link.parent_id != principal.user_id or link.status != "active":
        raise HTTPException(status_code=404, detail="Not found")

    prefs = db.get(models.Preferences, link.teen_id)
    usage = db.scalar(
        select(models.Usage).where(
            models.Usage.user_id == link.teen_id,
            models.Usage.day == date.today(),
        )
    )
    pending = db.scalars(
        select(models.SupervisionRequest).where(
            models.SupervisionRequest.supervision_id == link.id
        ).order_by(models.SupervisionRequest.created_at.desc()).limit(25)
    ).all()

    return {
        "supervision": parental.link_out(link, principal.user_id),
        "settings": {
            "who_can_message": getattr(prefs, "who_can_message", None),
            "who_can_invite": getattr(prefs, "who_can_invite", None),
            "discoverable": getattr(prefs, "discoverable", None),
            "sensitive_content": "blocked_for_this_age_group",
        },
        "time": {
            "daily_limit_minutes": link.daily_limit_minutes
            or getattr(prefs, "daily_limit_minutes", None),
            "minutes_today": getattr(usage, "minutes", 0) if usage else 0,
        },
        "requests": [
            {
                "id": r.id, "setting": r.setting, "requested_value": r.requested_value,
                "status": r.status, "created_at": r.created_at,
            }
            for r in pending
        ],
        "not_included": parental.CANNOT_SEE,
    }


@app.post("/supervision/{link_id}/time-limit", tags=["supervision"])
def set_time_limit(
    link_id: str, payload: TimeLimitIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """The one setting a parent may change directly."""
    link = db.get(models.ParentalSupervision, link_id)
    if link is None or link.parent_id != principal.user_id or link.status != "active":
        raise HTTPException(status_code=404, detail="Not found")
    link.daily_limit_minutes = payload.daily_limit_minutes
    prefs = db.get(models.Preferences, link.teen_id)
    if prefs is not None:
        prefs.daily_limit_minutes = payload.daily_limit_minutes
    db.commit()
    notify.notify(
        link.teen_id, "supervision_time_limit",
        "Your daily time limit was changed",
        link="/settings/supervision",
    )
    return {"daily_limit_minutes": link.daily_limit_minutes}


@app.post("/supervision/requests/{request_id}", tags=["supervision"])
def answer_request(
    request_id: str, payload: SupervisionAnswerIn, principal: CurrentUser,
    db: OrmSession = Depends(get_db),
):
    """A parent answering a request to loosen a safety setting."""
    request = db.get(models.SupervisionRequest, request_id)
    if request is None or request.parent_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Not found")
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="This has already been answered.")

    request.status = "approved" if payload.approve else "declined"
    request.answered_at = parental.now()

    if payload.approve:
        prefs = db.get(models.Preferences, request.teen_id)
        if prefs is not None and hasattr(prefs, request.setting):
            value = request.requested_value
            if value in ("True", "False"):
                value = value == "True"
            setattr(prefs, request.setting, value)
    db.commit()

    notify.notify(
        request.teen_id, "supervision_request_answered",
        "Your request was approved" if payload.approve else "Your request was declined",
        link="/settings/supervision",
    )
    return {"id": request.id, "status": request.status}


@app.get("/preferences", tags=["preferences"])
def get_preferences(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    prefs = db.get(models.Preferences, principal.user_id)
    if prefs is None:
        prefs = models.Preferences(user_id=principal.user_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return _prefs_out(prefs)


def _prefs_out(prefs: models.Preferences) -> dict:
    data = {c.name: getattr(prefs, c.name) for c in models.Preferences.__table__.columns}
    # Sent as a list because that is what it is; the csv is a storage detail.
    data["interest_topics"] = [t for t in (prefs.interest_topics or "").split(",") if t]
    return data


@app.patch("/preferences", tags=["preferences"])
def set_preferences(payload: PreferencesIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    prefs = db.get(models.Preferences, principal.user_id)
    if prefs is None:
        prefs = models.Preferences(user_id=principal.user_id)
        db.add(prefs)
    # A supervised younger teen may always make their own account *stricter*
    # without asking. Loosening a safety setting goes to their parent instead
    # of being applied — and it is recorded either way, so the teenager can see
    # what was refused rather than finding a setting that will not move.
    held: list[dict] = []

    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        refused = parental.refusal(db, principal.user_id, key, value)
        if refused:
            raise HTTPException(status_code=403, detail=refused)
        if parental.needs_approval(db, principal.user_id, key, value):
            link = parental.active_link(db, principal.user_id)
            request = parental.open_request(db, link, key, value)
            held.append({"setting": key, "requested_value": str(value), "request_id": request.id})
            notify.notify(
                link.parent_id, "supervision_request",
                "A setting change is waiting for you",
                link="/settings/supervision",
            )
            continue
        if key == "interest_topics":
            # Stored as csv, normalised the same way post topics are, so a
            # declared interest and a hashtag on a post compare as equal.
            value = ",".join(
                sorted({t.strip().lower() for t in value if t.strip()})
            ) or None
        setattr(prefs, key, value)
    db.commit()
    db.refresh(prefs)
    out = _prefs_out(prefs)
    if held:
        out["awaiting_approval"] = held
        out["note"] = "Some changes need approval from the adult supervising this account."
    return out


# --- blocks ----------------------------------------------------------------

@app.post("/users/{user_id}/block", status_code=201, tags=["safety"])
def block(user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    if user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot block yourself")
    # As with follow: a block is a statement about a member, so there has to be
    # one. Unblocking deliberately does not check - a member who has since been
    # deleted must still be removable from your block list.
    _ensure_profile(db, user_id)
    if not db.scalar(
        select(models.Block).where(
            models.Block.user_id == principal.user_id, models.Block.blocked_id == user_id
        )
    ):
        db.add(models.Block(user_id=principal.user_id, blocked_id=user_id))
    # Blocking severs the follow relation in both directions - and the stored
    # counts follow, as they do on unfollow. They did not, and the followers
    # shown on a profile drifted above the follows that exist.
    for follower_id, followee_id in ((principal.user_id, user_id), (user_id, principal.user_id)):
        severed = db.execute(
            delete(models.Follow).where(
                models.Follow.follower_id == follower_id, models.Follow.followee_id == followee_id
            )
        ).rowcount
        if severed:
            for uid, column in ((followee_id, "followers_count"), (follower_id, "following_count")):
                profile = db.get(models.Profile, uid)
                if profile is not None:
                    setattr(profile, column, max(0, getattr(profile, column) - 1))
    db.commit()
    return {"blocked": True}


@app.delete("/users/{user_id}/block", status_code=204, tags=["safety"])
def unblock(user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    db.execute(
        delete(models.Block).where(
            models.Block.user_id == principal.user_id, models.Block.blocked_id == user_id
        )
    )
    db.commit()


# --- internal --------------------------------------------------------------

@app.get("/internal/preferences/{user_id}", tags=["internal"])
def internal_preferences(user_id: str, db: OrmSession = Depends(get_db)):
    """The settings other services need to honour.

    Enforcement lives with whoever serves the content — a feed that ignores
    `age_mode` is not made safe by a client that hides what it was sent.
    """
    prefs = _prefs(db, user_id)
    return {
        "age_mode": prefs.age_mode,
        "data_saver": prefs.data_saver,
        "autoplay_media": prefs.autoplay_media,
        "reduced_motion": prefs.reduced_motion,
        "wellbeing_enabled": prefs.wellbeing_enabled,
        "daily_limit_minutes": prefs.daily_limit_minutes,
        "translation_auto": prefs.translation_auto,
        "who_can_see_family": prefs.who_can_see_family,
        "family_tree_shared": prefs.family_tree_shared,
        # The feed ranker reads these to score a post up, and to open on the
        # mode and algorithm the member actually chose.
        "interest_topics": [t for t in (prefs.interest_topics or "").split(",") if t],
        "default_feed_mode": prefs.default_feed_mode,
        "algorithm_id": prefs.algorithm_id,
    }


# --- wellbeing --------------------------------------------------------------

class HeartbeatIn(BaseModel):
    """Minutes of *active* use since the last beat, clamped by the server."""

    minutes: float = Field(default=1.0, ge=0, le=10)


# The client beats once a minute while the tab is visible; the ceiling for a
# beat with nothing to compare against.
BEAT_MINUTES = 1.0


@app.post("/wellbeing/heartbeat", tags=["wellbeing"])
def wellbeing_heartbeat(payload: HeartbeatIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Count time against the member's daily limit.

    Kept server-side so the limit survives a reload, a second tab and a new
    device — a counter in localStorage is undone by anything that clears it,
    which makes the limit a suggestion rather than a limit.

    The increment is clamped: a client claiming 600 minutes in one beat cannot
    burn the day's allowance, and one claiming 0 cannot stop the clock, because
    the server also refuses beats that arrive faster than they could be real.
    """
    prefs = _prefs(db, principal.user_id)
    today = datetime.now(timezone.utc).date()
    row = db.get(models.Usage, (principal.user_id, today))
    if row is None:
        row = models.Usage(user_id=principal.user_id, day=today, minutes=0.0)
        db.add(row)

    now = datetime.now(timezone.utc)
    if row.last_beat_at is not None:
        elapsed = (now - row.last_beat_at).total_seconds() / 60
        # Never credit more time than has actually passed since the last beat.
        payload.minutes = min(payload.minutes, max(0.0, elapsed))
    else:
        # First beat of the day has nothing to measure against, so cap it at one
        # beat interval. Otherwise a client could open the tab and immediately
        # claim the whole allowance — or claim it was never used.
        payload.minutes = min(payload.minutes, BEAT_MINUTES)

    row.minutes = float(row.minutes) + payload.minutes
    row.last_beat_at = now
    db.commit()

    limit = prefs.daily_limit_minutes
    over = bool(prefs.wellbeing_enabled and limit and row.minutes >= limit)
    return {
        "enabled": prefs.wellbeing_enabled,
        "minutes_today": round(row.minutes, 1),
        "limit_minutes": limit,
        "remaining_minutes": round(max(0.0, limit - row.minutes), 1) if limit else None,
        "over_limit": over,
    }


@app.get("/wellbeing", tags=["wellbeing"])
def wellbeing_status(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    prefs = _prefs(db, principal.user_id)
    today = datetime.now(timezone.utc).date()
    row = db.get(models.Usage, (principal.user_id, today))
    minutes = float(row.minutes) if row else 0.0
    limit = prefs.daily_limit_minutes
    return {
        "enabled": prefs.wellbeing_enabled,
        "minutes_today": round(minutes, 1),
        "limit_minutes": limit,
        "remaining_minutes": round(max(0.0, limit - minutes), 1) if limit else None,
        "over_limit": bool(prefs.wellbeing_enabled and limit and minutes >= limit),
    }


@app.get("/internal/audience/{user_id}", tags=["internal"])
def audience(user_id: str, db: OrmSession = Depends(get_db)):
    """This member's place in the graph — used by social-service to build feeds.

    ``following`` is who they follow: the Following feed and the ranker's
    affinity term both read it. It is served here, on the private network, so
    social-service does not need the member's token to ask.
    """
    followers = db.scalars(select(models.Follow.follower_id).where(models.Follow.followee_id == user_id)).all()
    following = db.scalars(select(models.Follow.followee_id).where(models.Follow.follower_id == user_id)).all()
    circles = db.scalars(select(models.Circle.id).where(models.Circle.owner_id == user_id)).all()
    blocked = db.scalars(select(models.Block.blocked_id).where(models.Block.user_id == user_id)).all()
    return {
        "followers": list(followers),
        "following": list(following),
        "circles": list(circles),
        "blocked": list(blocked),
    }


# --- connections ------------------------------------------------------------

PRIVACY_CHOICES = ("everyone", "connections", "nobody")


class ConnectIn(BaseModel):
    message: str | None = Field(default=None, max_length=500)


def _connection_between(db: OrmSession, a: str, b: str) -> models.Connection | None:
    """The row for this pair, whichever way round it was created."""
    return db.scalar(
        select(models.Connection).where(
            or_(
                and_(models.Connection.requester_id == a, models.Connection.addressee_id == b),
                and_(models.Connection.requester_id == b, models.Connection.addressee_id == a),
            )
        )
    )


def _prefs(db: OrmSession, user_id: str) -> models.Preferences:
    """This member's settings, creating the row only for a member who exists.

    It used to create one for whatever id it was handed, and it is handed ids
    from URL paths and from other services. Two consequences, both seen:

    * a request naming a string that is nobody left a settings row behind for
      it, because reading the row was how the privacy check was done; and
    * a service asking about a deleted member recreated theirs, so an erased
      account came back as a row minutes after it was erased.

    Reading settings should not write anything. Where there is no profile the
    defaults are returned without being stored - the object is never added to
    the session, so nothing is persisted and the caller still gets real values
    to check rather than a row of None.
    """
    prefs = db.get(models.Preferences, user_id)
    if prefs is not None:
        return prefs

    blank = models.Preferences(user_id=user_id)
    if db.get(models.Profile, user_id) is None:
        # Column defaults are applied by the database on insert, and this one
        # is never inserted - so they are filled in here, or every setting
        # would read as None and a privacy check would have nothing to test.
        for column in models.Preferences.__table__.columns:
            default = column.default
            if default is not None and not default.is_callable and getattr(blank, column.name) is None:
                setattr(blank, column.name, default.arg)
        return blank

    db.add(blank)
    db.commit()
    return blank


def _allowed(setting: str, connected: bool) -> bool:
    if setting == "everyone":
        return True
    if setting == "connections":
        return connected
    return False


@app.post("/connections/{user_id}", status_code=201, tags=["connections"])
def send_invitation(
    user_id: str, payload: ConnectIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Ask to connect. The other person decides."""
    if user_id == principal.user_id:
        raise HTTPException(status_code=400, detail="You cannot connect with yourself")

    # The addressee has to be somebody. Nothing below checked that, and the
    # checks that follow are no help: _prefs() *creates* a settings row for an
    # id it has never seen, so a request naming any string at all wrote two
    # rows - a pending invitation and a preferences record - for a member who
    # does not exist. It was found by a typo, POST /connections/invite, which
    # matched this route with user_id="invite" and duly invited it.
    #
    # _ensure_profile is the existing answer: it materialises the profile from
    # auth-service if the event that should have created it was missed, and
    # raises 404 when auth-service has never heard of them either. So a real
    # member is still invitable the moment they register, and a string is not.
    _ensure_profile(db, user_id)

    if db.scalar(
        select(models.Block).where(
            or_(
                and_(models.Block.user_id == user_id, models.Block.blocked_id == principal.user_id),
                and_(models.Block.user_id == principal.user_id, models.Block.blocked_id == user_id),
            )
        )
    ):
        raise HTTPException(status_code=403, detail="You cannot invite this member")

    # The addressee's setting decides whether the invitation may even be sent.
    if not _allowed(_prefs(db, user_id).who_can_invite, connected=False):
        raise HTTPException(
            status_code=403, detail="This member is not accepting connection invitations"
        )

    existing = _connection_between(db, principal.user_id, user_id)
    if existing:
        if existing.status == "accepted":
            return {"status": "accepted", "already": True}
        if existing.status == "pending":
            # They already invited us — accepting is the obvious intent.
            if existing.addressee_id == principal.user_id:
                existing.status = "accepted"
                existing.responded_at = datetime.now(timezone.utc)
                db.commit()
                return {"status": "accepted", "note": "They had already invited you."}
            return {"status": "pending", "already": True}
        # A previous decline can be retried.
        existing.requester_id = principal.user_id
        existing.addressee_id = user_id
        existing.status = "pending"
        existing.message = payload.message
        existing.responded_at = None
        db.commit()
        return {"status": "pending"}

    db.add(
        models.Connection(
            requester_id=principal.user_id, addressee_id=user_id, message=payload.message
        )
    )
    db.commit()

    mine = db.get(models.Profile, principal.user_id)
    notify.notify(
        user_id,
        kind="invitation",
        title=f"{mine.display_name if mine else 'Someone'} wants to connect",
        body=payload.message,
        # Where the invitation can actually be answered. It used to point at
        # the privacy tab of the dashboard, which is where you decide *who may
        # invite you* - a settings page, not a request. Somebody told they had
        # an invitation arrived at a page with no invitation on it.
        link="/connections?tab=incoming",
    )
    return {"status": "pending"}


@app.get("/connections", tags=["connections"])
def list_connections(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.Connection).where(
            or_(
                models.Connection.requester_id == principal.user_id,
                models.Connection.addressee_id == principal.user_id,
            )
        )
    ).all()

    ids = {r.requester_id for r in rows} | {r.addressee_id for r in rows}
    profiles = {
        p.user_id: {"handle": p.handle, "display_name": p.display_name, "avatar_url": p.avatar_url}
        for p in db.scalars(select(models.Profile).where(models.Profile.user_id.in_(ids))).all()
    }

    def shape(row: models.Connection, other: str) -> dict:
        return {
            "id": row.id,
            "user_id": other,
            "profile": profiles.get(other),
            "status": row.status,
            "message": row.message,
            "created_at": row.created_at,
        }

    return {
        "incoming": [
            shape(r, r.requester_id)
            for r in rows
            if r.status == "pending" and r.addressee_id == principal.user_id
        ],
        "outgoing": [
            shape(r, r.addressee_id)
            for r in rows
            if r.status == "pending" and r.requester_id == principal.user_id
        ],
        "accepted": [
            shape(r, r.addressee_id if r.requester_id == principal.user_id else r.requester_id)
            for r in rows
            if r.status == "accepted"
        ],
    }


@app.post("/connections/{user_id}/respond", tags=["connections"])
def respond(user_id: str, accept: bool, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    row = _connection_between(db, principal.user_id, user_id)
    # Only the person who was invited may answer.
    if row is None or row.status != "pending" or row.addressee_id != principal.user_id:
        raise HTTPException(status_code=404, detail="No invitation to answer")
    row.status = "accepted" if accept else "declined"
    row.responded_at = datetime.now(timezone.utc)
    db.commit()

    # Only an acceptance is announced. Telling someone they were declined
    # serves nobody, and the blueprint's connection model is opt-in on both
    # sides — a refusal is allowed to be quiet.
    if accept:
        mine = db.get(models.Profile, principal.user_id)
        notify.notify(
            row.requester_id,
            kind="connection_accepted",
            title=f"{mine.display_name if mine else 'Someone'} accepted your invitation",
            body="You can now message each other.",
            link=f"/u/{mine.handle}" if mine else "/messages",
        )
    return {"status": row.status}


@app.delete("/connections/{user_id}", status_code=204, tags=["connections"])
def disconnect(user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    row = _connection_between(db, principal.user_id, user_id)
    if row is not None:
        db.delete(row)
        db.commit()


@app.get("/internal/connections/{user_id}", tags=["internal"])
def internal_connections(user_id: str, db: OrmSession = Depends(get_db)):
    """Ids of everyone ``user_id`` has an accepted connection with.

    messaging-service uses it to decide who is told when this member comes
    online or goes offline — presence is shown to connections, not to anyone
    who happens to know an id.
    """
    rows = db.scalars(
        select(models.Connection).where(
            models.Connection.status == "accepted",
            or_(
                models.Connection.requester_id == user_id,
                models.Connection.addressee_id == user_id,
            ),
        )
    ).all()
    return {
        "ids": sorted(
            {r.addressee_id if r.requester_id == user_id else r.requester_id for r in rows}
        )
    }


@app.get("/internal/blocks/{user_id}", tags=["internal"])
def internal_blocks(user_id: str, db: OrmSession = Depends(get_db)):
    """Everyone on either side of a block with ``user_id``.

    A block is mutual in effect: neither side sees the other's presence or
    typing, whoever pressed the button.
    """
    rows = db.scalars(
        select(models.Block).where(
            or_(models.Block.user_id == user_id, models.Block.blocked_id == user_id)
        )
    ).all()
    return {"ids": sorted({r.blocked_id if r.user_id == user_id else r.user_id for r in rows})}


@app.get("/internal/permissions/{actor_id}/{target_id}", tags=["internal"])
def permissions(actor_id: str, target_id: str, db: OrmSession = Depends(get_db)):
    """What ``actor`` may do to ``target``.

    The single place those rules live. messaging-service, family-service and
    community-service ask here rather than each re-deriving "are they
    connected?" — three copies of that logic would drift, and the one that
    drifts is the one that leaks.
    """
    if actor_id == target_id:
        return {
            "connected": True, "can_message": True, "can_add_family": True,
            "can_add_community": True, "can_add_group": True, "can_invite_group": True,
            "can_invite": False, "reason": "self",
        }

    blocked = db.scalar(
        select(models.Block).where(
            or_(
                and_(models.Block.user_id == target_id, models.Block.blocked_id == actor_id),
                and_(models.Block.user_id == actor_id, models.Block.blocked_id == target_id),
            )
        )
    )
    if blocked:
        return {
            "connected": False, "can_message": False, "can_add_family": False,
            "can_add_community": False, "can_add_group": False, "can_invite_group": False,
            "can_invite": False, "reason": "blocked",
        }

    row = _connection_between(db, actor_id, target_id)
    connected = row is not None and row.status == "accepted"
    prefs = _prefs(db, target_id)

    return {
        "connected": connected,
        "pending": row.status == "pending" if row else False,
        "invited_by_me": bool(row and row.requester_id == actor_id and row.status == "pending"),
        "can_message": _allowed(prefs.who_can_message, connected),
        "can_add_family": _allowed(prefs.who_can_add_family, connected),
        "can_add_community": _allowed(prefs.who_can_add_community, connected),
        "can_add_group": _allowed(prefs.who_can_add_group, connected),
        "can_invite_group": _allowed(prefs.who_can_invite_group, connected),
        "can_invite": not row and _allowed(prefs.who_can_invite, False),
        "reason": "connected" if connected else "not_connected",
    }


@app.get("/connections/with/{user_id}", tags=["connections"])
def my_permissions(user_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The same answer, for the signed-in member's own UI.

    Plus whether a follow already exists, in both directions. A profile page
    that does not ask this can only start at "Follow", and that is what every
    profile did: arriving from a notification or from Connections showed
    "Follow" for somebody followed weeks ago, as if the click had never
    counted.
    """
    answer = dict(permissions(principal.user_id, user_id, db))
    answer["following"] = _follows(db, principal.user_id, user_id)
    answer["follows_you"] = _follows(db, user_id, principal.user_id)
    return answer


def _follows(db: OrmSession, follower_id: str, followee_id: str) -> bool:
    return db.scalar(
        select(models.Follow.follower_id).where(
            models.Follow.follower_id == follower_id, models.Follow.followee_id == followee_id
        )
    ) is not None


class ProfileIds(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=300)


class ProfileHandles(BaseModel):
    # Capped like the id lookup: a body full of handles is a body full of
    # work, and a post mentioning three hundred people is not a mention.
    handles: list[str] = Field(default_factory=list, max_length=50)


class ProfileSeed(BaseModel):
    user_id: str
    handle: str
    display_name: str
    country: str | None = None
    lang: str = "en"
    verified: bool = False


@app.post("/internal/profiles/seed", tags=["internal"])
def seed_profile(payload: ProfileSeed, db: OrmSession = Depends(get_db)):
    """Create a profile from data the caller already has.

    auth-service calls this the moment an account exists. It **pushes** the
    fields rather than asking us to fetch them, because the pull version
    deadlocks: user-service would call back into auth-service while auth-service
    is still inside the registration request, and a single-worker service then
    waits on itself until the timeout.
    """
    if db.get(models.Profile, payload.user_id) is not None:
        return {"created": False}
    db.add(
        models.Profile(
            user_id=payload.user_id,
            handle=payload.handle,
            display_name=payload.display_name,
            country=payload.country,
            lang=payload.lang,
            verified=payload.verified,
        )
    )
    if db.get(models.Preferences, payload.user_id) is None:
        db.add(models.Preferences(user_id=payload.user_id))
    db.commit()
    return {"created": True}


@app.post("/internal/profiles/by-handle", tags=["internal"])
def profiles_by_handle(payload: ProfileHandles, db: OrmSession = Depends(get_db)):
    """Resolve @handles to the people behind them.

    The mirror of the id lookup, and social-service's half of a mention: it
    reads "@ama" out of a post and has to learn who that is before it can tell
    them. Keyed on the lower-cased handle in the answer, because that is what
    the caller matched on - a handle typed @Ama must find the same person.

    A handle nobody has is simply absent from the result rather than an error:
    a post may mention an email address, a price, or somebody who has since
    left, and none of those should fail the post.
    """
    wanted = {h.strip().lstrip("@").lower() for h in payload.handles if h and h.strip()}
    if not wanted:
        return {"profiles": {}}
    rows = db.scalars(
        select(models.Profile).where(func.lower(models.Profile.handle).in_(wanted))
    ).all()
    return {
        "profiles": {
            row.handle.lower(): {
                "user_id": row.user_id,
                "handle": row.handle,
                "display_name": row.display_name,
            }
            for row in rows
        }
    }


@app.get("/internal/profiles/ids", tags=["internal"])
def internal_profile_ids(after: str | None = None, limit: int = 500, db: OrmSession = Depends(get_db)):
    """Every member's id, a page at a time.

    For the one job that genuinely needs the whole list: putting everybody who
    was already here into the welcome group, which otherwise only ever
    contains people who registered after it existed. Paged by id rather than
    by offset so a registration during the sweep cannot make it skip somebody.
    """
    query = select(models.Profile.user_id).order_by(models.Profile.user_id)
    if after:
        query = query.where(models.Profile.user_id > after)
    ids = db.scalars(query.limit(max(1, min(limit, 1000)))).all()
    return {"items": list(ids), "next": ids[-1] if ids else None}


class VerifiedIn(BaseModel):
    verified: bool


@app.post("/internal/profiles/{user_id}/verified", tags=["internal"])
def set_profile_verified(user_id: str, payload: VerifiedIn, db: OrmSession = Depends(get_db)):
    """The verified badge, from the identity check (auth-service calls this).

    The profile copied the flag once, when it was created, so somebody who
    verified afterwards never got their badge.
    """
    profile = _ensure_profile(db, user_id)
    profile.verified = payload.verified
    db.commit()
    return {"user_id": user_id, "verified": profile.verified}


@app.post("/internal/profiles", tags=["internal"])
def profiles_by_id(payload: ProfileIds, db: OrmSession = Depends(get_db)):
    """Resolve many ids at once.

    Comment threads need a handle per author; asking for them one by one would
    be a request per row.
    """
    if not payload.ids:
        return {"profiles": {}}
    rows = db.scalars(select(models.Profile).where(models.Profile.user_id.in_(payload.ids))).all()

    # Profiles are materialised lazily, and only by the member's *own* request.
    # An author who had not yet opened their profile therefore resolved to
    # nothing here, and every surface showing their content — the feed, comment
    # threads, the shorts reel — fell back to a raw `usr_…` id and a blank
    # avatar. Materialising on read fixes all of them at once.
    missing = set(payload.ids) - {r.user_id for r in rows}
    for user_id in missing:
        try:
            rows = [*rows, _ensure_profile(db, user_id)]
        except HTTPException:
            # No such account upstream: skip it rather than fail the whole batch,
            # which would blank out every other author on the page.
            log.warning("could not materialise profile for %s", user_id)
    return {
        "profiles": {
            r.user_id: {
                "handle": r.handle,
                "display_name": r.display_name,
                "avatar_url": r.avatar_url,
                "verified": r.verified,
            }
            for r in rows
        }
    }


@app.get("/internal/stats", tags=["internal"])
def stats(db: OrmSession = Depends(get_db)):
    return {
        "profiles": db.scalar(select(func.count()).select_from(models.Profile)) or 0,
        "follows": db.scalar(select(func.count()).select_from(models.Follow)) or 0,
        "circles": db.scalar(select(func.count()).select_from(models.Circle)) or 0,
    }
