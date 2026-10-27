"""Parental supervision — and, just as deliberately, its limits.

The specification is unusually direct about this feature, and it is right to
be: supervision that quietly becomes surveillance does more harm than no
supervision at all. A teenager who believes their parent can read their private
messages stops using private messages for the things private messages are for —
including telling somebody that an adult is pressuring them.

So this module is built around a list of what a parent **cannot** do, and that
list is served to both of them at an endpoint. Not buried in a policy document:
returned by the API, in plain words, to the parent and to the teenager, before
either of them agrees to anything.

**What a parent can see:** that supervision is active, the teen's privacy and
safety settings, time-limit settings and daily usage totals, and a record of
requests the teen has made to weaken a safety setting.

**What a parent cannot see, ever, through any endpoint here:** the content of
private messages, who the teen messages, their posts, their searches, their
browsing, their location, their friends list, or anything they have read. None
of it is exposed, and none of it is stored for this purpose.

Two design choices that carry most of the weight:

*Either side can end it, and the other is told.* A teenager cannot be held
under supervision they did not agree to and cannot escape. Silent revocation
would be its own problem, so the parent is notified.

*Ending supervision buys no permissions.* Where the jurisdiction requires
parental consent, an account that leaves supervision reverts to the strictest
settings rather than becoming free. Otherwise "remove your parent" would be the
documented way for a 14-year-old to unlock everything, and this feature would
be a lock with the key taped to it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as OrmSession

from common import ageclient
from common.agesafety import AgeTier, engine, may_change_setting, privacy_defaults_for
from common.ids import new_id

import models

log = logging.getLogger("user-service.parental")


# What supervision covers. Served to both parties verbatim, so neither has to
# take anybody's word for what was agreed.
CAN_SEE = [
    "That supervision is active, and when it started.",
    "Your privacy and safety settings, as they are now.",
    "Your daily time limit and how much time you have used today.",
    "Requests you make to loosen a safety setting, and whether they were granted.",
]

CANNOT_SEE = [
    "The content of your private messages. Not now, and not later.",
    "Who you message, or how often.",
    "Your posts, comments or likes.",
    "What you search for, or what you read.",
    "Your location.",
    "Your list of friends or followers.",
]

CAN_DO = [
    "Set or change your daily time limit.",
    "Approve or decline a request you make to loosen a safety setting.",
    "End the supervision.",
]

CANNOT_DO = [
    "Read or delete your messages.",
    "Post, comment or message as you.",
    "See your account password or sign in as you.",
    "Stop you from ending the supervision yourself.",
]


def disclosure() -> dict:
    """The plain-words statement, for both the parent and the teenager."""
    return {
        "can_see": CAN_SEE,
        "cannot_see": CANNOT_SEE,
        "can_do": CAN_DO,
        "cannot_do": CANNOT_DO,
        "note": (
            "Either of you can end supervision at any time, and the other is told. "
            "Ending it does not unlock settings that your age already restricts."
        ),
    }


# Settings a supervised younger teen cannot loosen alone. Loosening only: a
# teenager may always make their own account *stricter* without asking, because
# a protection nobody has to approve is a protection they will actually use.
SUPERVISED_SETTINGS = {
    "who_can_message": {"everyone"},
    "who_can_invite": {"everyone"},
    "who_can_add_family": {"everyone"},
    "who_can_add_community": {"everyone"},
    "who_can_add_group": {"everyone"},
    "who_can_invite_group": {"everyone"},
    "discoverable": {True},
    "who_can_see_family": {"everyone"},
}


def active_link(db: OrmSession, teen_id: str) -> models.ParentalSupervision | None:
    return db.scalar(
        select(models.ParentalSupervision).where(
            models.ParentalSupervision.teen_id == teen_id,
            models.ParentalSupervision.status == "active",
        )
    )


def links_for(db: OrmSession, user_id: str) -> list[models.ParentalSupervision]:
    return list(
        db.scalars(
            select(models.ParentalSupervision).where(
                or_(
                    models.ParentalSupervision.teen_id == user_id,
                    models.ParentalSupervision.parent_id == user_id,
                ),
                models.ParentalSupervision.status.in_(("invited", "active")),
            )
        ).all()
    )


def loosening(field: str, value) -> bool:
    """Whether this change makes the account less protected."""
    allowed = SUPERVISED_SETTINGS.get(field)
    return bool(allowed) and value in allowed


def needs_approval(db: OrmSession, teen_id: str, field: str, value) -> bool:
    """Whether this change has to go to a parent first.

    Two things have to hold: the change loosens something, and the account is on
    a tier that needs an adult to agree. A younger teen with no supervising adult
    does not get the change applied either — `refusal` turns that into a plain
    refusal before this is reached, so the answer is never "ask nobody".
    """
    if not loosening(field, value):
        return False
    profile = ageclient.age_profile(teen_id)
    if profile.tier not in NEEDS_ADULT_TO_LOOSEN and not engine.requires_parental_approval(
        profile, f"{field}:{value}"
    ):
        return False
    return active_link(db, teen_id) is not None


# Settings no minor may set for themselves at all, supervised or not. age_mode
# mirrors the authoritative age tier: a 14-year-old writing "adult" into it
# would be promoting themselves.
HARD_LOCKED_FOR_MINORS = {"age_mode"}

# Tiers that cannot loosen a safety setting without an adult saying so. A
# 16-year-old has their own say over who may message them; a 13-year-old does
# not, and the tiers exist so that "minor" does not have to mean one thing for
# both of them. UNKNOWN and AGE_REVIEW_REQUIRED are in here because they are
# treated as the youngest tier everywhere else.
NEEDS_ADULT_TO_LOOSEN = frozenset({
    AgeTier.TEEN_HIGH_PROTECTION,
    AgeTier.AGE_REVIEW_REQUIRED,
    AgeTier.UNKNOWN,
    AgeTier.UNDER_MINIMUM,
})


def refusal(db: OrmSession, user_id: str, field: str, value) -> str | None:
    """Why this change is refused outright, or None if it may proceed.

    This is the half of the feature that makes the other half honest. Without
    it, a supervised 14-year-old who wants to be messageable by everyone has an
    obvious route: end the supervision, and the request that needed a parent
    needs nobody. `revert_to_strictest` would put the setting back, and the
    teenager would simply set it again a second later.

    So the tier is the lock and supervision is the key. An unsupervised minor is
    refused; a supervised one can ask. Ending supervision therefore removes the
    only way to get the permission rather than granting it.
    """
    profile = ageclient.age_profile(user_id)
    if not profile.is_minor:
        return None

    if field in HARD_LOCKED_FOR_MINORS:
        return "This is set by your account's age and cannot be changed here."

    if not may_change_setting(profile.tier, field):
        return "This setting is not available on this account."

    if profile.tier in NEEDS_ADULT_TO_LOOSEN and loosening(field, value):
        if active_link(db, user_id) is None:
            return (
                "This account needs an adult's approval for that. "
                "Invite a parent or guardian to supervise the account to ask."
            )
    return None


def open_request(
    db: OrmSession, link: models.ParentalSupervision, field: str, value: str
) -> models.SupervisionRequest:
    """Record a request to loosen a setting, for the parent to answer.

    One pending request per setting. A teenager who taps the same toggle five
    times is asking once; five rows and five notifications would train the
    parent to ignore the notification.
    """
    pending = db.scalar(
        select(models.SupervisionRequest).where(
            models.SupervisionRequest.supervision_id == link.id,
            models.SupervisionRequest.setting == field,
            models.SupervisionRequest.status == "pending",
        )
    )
    if pending is not None:
        pending.requested_value = str(value)
        return pending

    request = models.SupervisionRequest(
        id=new_id("spr"),
        supervision_id=link.id,
        teen_id=link.teen_id,
        parent_id=link.parent_id,
        setting=field,
        requested_value=str(value),
    )
    db.add(request)
    return request


def revert_to_strictest(db: OrmSession, teen_id: str) -> None:
    """Put an account back to the defaults for its tier.

    Called when supervision ends. This is what stops "remove your parent" from
    being the way to unlock the account: leaving supervision returns you to
    where an unsupervised account of your age would be, not to where an adult
    would be.
    """
    profile = ageclient.age_profile(teen_id)
    if not profile.is_minor:
        return
    defaults = privacy_defaults_for(profile.tier)
    prefs = db.get(models.Preferences, teen_id)
    if prefs is None:
        return
    # The policy engine speaks in its own vocabulary ("friends"); the column
    # speaks in the product's ("connections"). And several of these settings have
    # no entry in the tier defaults at all, so they need a stated safe value
    # here — without one, a setting a parent had granted would survive the
    # supervision ending, which is the one thing this function exists to prevent.
    mapping = {
        "who_can_message": {"friends": "connections", "connections": "connections"},
    }
    fallbacks = {
        "who_can_message": "connections",
        "who_can_invite": "connections",
        "who_can_add_family": "connections",
        "who_can_add_community": "connections",
        "who_can_add_group": "connections",
        "who_can_invite_group": "connections",
        "who_can_see_family": "family",
        "discoverable": False,
    }
    for field in SUPERVISED_SETTINGS:
        wanted = defaults.get(field, fallbacks.get(field))
        wanted = mapping.get(field, {}).get(wanted, wanted)
        if wanted is None:
            continue
        if hasattr(prefs, field):
            setattr(prefs, field, wanted)
    log.info("supervision ended for %s; settings returned to tier defaults", teen_id)


def link_out(link: models.ParentalSupervision, viewer_id: str) -> dict:
    return {
        "id": link.id,
        "role": "parent" if link.parent_id == viewer_id else "teen",
        "parent_id": link.parent_id,
        "teen_id": link.teen_id,
        "status": link.status,
        "invited_by": link.invited_by,
        "created_at": link.created_at,
        "accepted_at": link.accepted_at,
        "ended_at": link.ended_at,
        "ended_by": link.ended_by,
    }


def now() -> datetime:
    return datetime.now(timezone.utc)
