"""Who may open a private conversation with whom, and what they may send.

The privacy layer already asks the recipient's own setting through
``permissions.check``. This adds the layer a member cannot set for themselves:
a 14-year-old who has left their messages open to anyone has not thereby
consented to unknown adults, and a platform that treats that setting as the
whole answer has made a child responsible for their own safeguarding.

Two rules, and one signal.

**The rules.** An adult reaching an unconnected minor is refused. A younger
teen reached by anyone they are not connected to is refused. Both come from the
shared engine, so the answer here is the same one the feed and the media origin
would give.

**The signal.** Refusals are recorded. One adult mistyping a handle is noise;
the same account refused by eleven different minors in a week is not, and the
only way to see the second is to have kept the first. The score this produces
feeds back into the engine, which lowers the bar for refusal as it rises — it
never concludes anything about the person on its own.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from common import ageclient, permissions
from common.agesafety import AgeProfile, Reason, engine

import models

log = logging.getLogger("messaging-service.agecheck")

# How far back the risk score looks. Long enough to see a pattern, short enough
# that somebody who was refused once a year ago is not carrying it forever.
RISK_WINDOW = timedelta(days=30)

# Refusals against *distinct* minors, not refusals in total. Ten attempts at
# one person is a different behaviour from one attempt at ten people, and it is
# the second that this is built to see.
_RISK_STEPS = ((10, 1.0), (5, 0.8), (3, 0.6), (1, 0.3))


def risk_score(db: OrmSession, sender_id: str) -> float:
    """How concerning this account's recent contact attempts look.

    Deliberately coarse. A score is an input to a refusal and a reason to put a
    case in front of a human; it is not a finding, and nothing here should be
    read as one.
    """
    since = datetime.now(timezone.utc) - RISK_WINDOW
    distinct_minors = db.scalar(
        select(func.count(func.distinct(models.ContactAttempt.recipient_id))).where(
            models.ContactAttempt.sender_id == sender_id,
            models.ContactAttempt.outcome == "refused_adult_to_minor",
            models.ContactAttempt.created_at >= since,
        )
    ) or 0
    for threshold, score in _RISK_STEPS:
        if distinct_minors >= threshold:
            return score
    return 0.0


def record(
    db: OrmSession,
    sender_id: str,
    recipient_id: str,
    outcome: str,
    *,
    sender_tier: str = "",
    recipient_tier: str = "",
) -> None:
    """Keep the attempt. Never raises: a failure to log must not open a door."""
    try:
        db.add(
            models.ContactAttempt(
                sender_id=sender_id,
                recipient_id=recipient_id,
                outcome=outcome,
                sender_tier=sender_tier,
                recipient_tier=recipient_tier,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive
        log.error("could not record contact attempt: %s", exc)


def is_connected(db: OrmSession, actor: str, target: str) -> bool:
    """Whether these two already have a relationship that permits contact.

    An accepted conversation counts, because replying to somebody is a clearer
    statement of consent than any setting. A pending request does not: that is
    the state this whole mechanism exists to govern.

    A *direct* conversation, and only that. Sharing a group is not consent to
    be written to privately: the welcome group holds every member of the
    platform, accepted, so counting groups here made every adult "connected"
    to every minor and switched the adult-to-minor rule off for everybody.
    """
    accepted = db.scalar(
        select(func.count())
        .select_from(models.Participant)
        .join(
            models.Conversation,
            models.Conversation.id == models.Participant.conversation_id,
        )
        .where(
            models.Conversation.kind == "direct",
            models.Participant.user_id == target,
            models.Participant.accepted_at.is_not(None),
            models.Participant.conversation_id.in_(
                select(models.Participant.conversation_id).where(
                    models.Participant.user_id == actor
                )
            ),
        )
    ) or 0
    if accepted:
        return True

    data = permissions.get(actor, target)
    # None means the permission service could not answer. Not connected is the
    # conservative reading, and it is the one that restricts.
    return bool(data and data.get("connected"))


def may_open_conversation(
    db: OrmSession, sender_id: str, recipient_id: str
) -> tuple[bool, str]:
    """The age gate on starting a private conversation.

    Returns ``(allowed, member-facing reason)``. The reason never names the
    recipient's age: telling a refused sender "that person is 14" hands them
    the one fact they should not have.
    """
    sender = ageclient.age_profile(sender_id)
    recipient = ageclient.age_profile(recipient_id)
    connected = is_connected(db, sender_id, recipient_id)
    score = risk_score(db, sender_id)

    verdict = engine.can_message_user(
        sender, recipient, connected=connected, sender_risk_score=score
    )
    if verdict.allowed:
        record(db, sender_id, recipient_id, "allowed",
               sender_tier=sender.tier.value, recipient_tier=recipient.tier.value)
        return True, ""

    outcome = (
        "refused_adult_to_minor"
        if verdict.reason is Reason.ADULT_TO_MINOR
        else "refused_not_connected"
    )
    record(db, sender_id, recipient_id, outcome,
           sender_tier=sender.tier.value, recipient_tier=recipient.tier.value)

    if verdict.reason is Reason.ADULT_TO_MINOR:
        log.info("adult-to-minor contact refused (risk %.1f)", score)
        # Same wording whether the refusal was the age rule or the risk score,
        # so the sender cannot tell which one they tripped.
        return False, "You cannot start a conversation with this member."
    return False, "This member only accepts messages from people they are connected to."


def may_send_media(db: OrmSession, sender_id: str, conversation_id: str) -> tuple[bool, str]:
    """Attachments before the other side has accepted (§20).

    Text first. An image that arrives before consent has already been seen by
    the time anybody can report it, and "delete for everyone" does not unsee
    it. The rule applies to every recipient in the room who has not accepted.
    """
    others = db.scalars(
        select(models.Participant).where(
            models.Participant.conversation_id == conversation_id,
            models.Participant.user_id != sender_id,
        )
    ).all()

    for participant in others:
        if participant.accepted_at is not None:
            continue
        recipient = ageclient.age_profile(participant.user_id)
        verdict = engine.can_receive_media_in_request(recipient, connected=False)
        if not verdict.allowed:
            return False, (
                "You can send text until this member accepts the conversation. "
                "Attachments become available after that."
            )
    return True, ""


def profiles_for(sender_id: str, recipient_id: str) -> tuple[AgeProfile, AgeProfile]:
    return ageclient.age_profile(sender_id), ageclient.age_profile(recipient_id)
