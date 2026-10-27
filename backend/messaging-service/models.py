from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "messaging"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cnv"))
    kind: Mapped[str] = mapped_column(String(20), default="direct")  # direct|group
    title: Mapped[str | None] = mapped_column(String(140))
    # What the group is for, shown on its information panel. Groups only: a
    # direct conversation is two people and needs no explaining.
    description: Mapped[str | None] = mapped_column(Text)
    # The one group everybody is in. Exactly one row carries this, and it is
    # how a new member is found a home without hard-coding an id in two
    # services.
    is_welcome: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # A group's own photo and banner. The url is stored beside the asset id so
    # drawing the list costs no call to media-service; the id is what lets a
    # replaced image be cleaned up, and what the ownership check runs against.
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    avatar_asset_id: Mapped[str | None] = mapped_column(String(40))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    cover_asset_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(40))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set on the conversation, not per message: "disappearing messages" is a
    # property of the room everyone in it can see, not a per-message trick one
    # participant can play on another. 0 means messages are kept.
    disappear_after_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # Groups: who may come in through a shared link. "public" lets them in at
    # once, "private" turns the link into a request an admin answers. Either
    # way a group is never listed anywhere - the link is the only door.
    privacy: Mapped[str] = mapped_column(String(10), default="private")
    # The code a group's link carries. Random rather than the group's id, so a
    # link cannot be guessed from another one. Minted on the first share.
    invite_code: Mapped[str | None] = mapped_column(String(32), index=True)
    # What somebody added later reads: "all" - the whole conversation so far -
    # or "from_join", only what is said after they arrive. A group's admins
    # choose; admins themselves always read everything.
    history_for_new: Mapped[str] = mapped_column(String(10), default="all")
    # The group's rules, shown before anybody joins or accepts an invitation.
    rules: Mapped[str | None] = mapped_column(Text)
    # Who may write: "all", or "admins" (owner, admins and moderators) - the
    # announcement mode, where everybody else reads and reacts.
    posting: Mapped[str] = mapped_column(String(10), default="all")
    # Who sees the member list: "all" members, or only "admins" (everybody
    # else sees the people who run the group and the count).
    members_visible: Mapped[str] = mapped_column(String(10), default="all")
    # Who may start a poll: "all" members or only "admins".
    polls_by: Mapped[str] = mapped_column(String(10), default="all")
    # The share link's own rules. Off means the code leads nowhere until an
    # admin turns it back on; the count is of people admitted through it.
    invite_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invite_max_uses: Mapped[int | None] = mapped_column(Integer)
    invite_uses: Mapped[int] = mapped_column(Integer, default=0)
    # The Community this group chat is linked to, set by somebody who runs
    # both. A pointer only: it grants nothing in either direction.
    community_id: Mapped[str | None] = mapped_column(String(40), index=True)
    # When an admin last used @everyone, which is limited so it cannot become
    # a way to ring every phone in the room on repeat.
    last_everyone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Deleted by its owner. The row stays (with no members and no messages)
    # so the audit trail of a deleted group still points somewhere.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Participant(Base):
    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_participant"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")
    # Null until this member has accepted the conversation. The person who
    # started it is accepted on creation; everybody else is in a request until
    # they reply or accept. Attachments wait for this, so an image cannot
    # arrive before anyone consented to hear from the sender at all.
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Pinned to the top of this member's list, and when. Groups only.
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # This member's notifications for this group: "all", "mentions"
    # (mentions and replies to them), or "announcements". Muting is separate
    # and wins over the level until it runs out; MUTED_FOREVER means until the
    # member turns it back on.
    notify_level: Mapped[str] = mapped_column(String(15), default="all")
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether this member's reading counts in "Seen by" for others.
    read_receipts: Mapped[bool] = mapped_column(Boolean, default=True)


class JoinRequest(Base):
    """Somebody who opened a private group's link and asked to come in.

    A table of its own rather than a participant row with a flag: every read
    path in this service treats "has a participant row" as "may read the
    room", and a pending flag that one of them forgot would hand the history
    to someone nobody admitted.
    """

    __tablename__ = "join_requests"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_join_request"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Message(Base):
    """End-to-end encrypted by default.

    When ``encrypted`` is true the server stores ``ciphertext`` and nothing else
    — no plaintext column exists for those messages, so a database dump cannot
    reveal them. ``body`` is only populated for conversations the user has
    explicitly opted out of E2E (e.g. a support thread needing moderation).
    """

    __tablename__ = "messages"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("msg"))
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    sender_id: Mapped[str] = mapped_column(String(40), index=True)
    encrypted: Mapped[bool] = mapped_column(Boolean, default=True)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    body: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="text")  # text|media|sticker|call_event
    media_url: Mapped[str | None] = mapped_column(String(500))
    # Attachment facts as media-service recorded them at upload — never as the
    # sending client described them.
    media_id: Mapped[str | None] = mapped_column(String(40))
    media_kind: Mapped[str | None] = mapped_column(String(20))  # image|video|audio|document|file
    media_name: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(100))
    media_size: Mapped[int | None] = mapped_column(BigInteger)
    lang: Mapped[str | None] = mapped_column(String(5))
    # The message this one answers, in the same conversation. A pointer rather
    # than a copy of the quoted text: a copy would outlive the original, so a
    # deleted or expired message would stay readable inside every reply to it.
    reply_to_id: Mapped[str | None] = mapped_column(String(40), index=True)
    # Chosen by the sending device before the request goes out. A retry after
    # a dropped response carries the same id, so it finds the message already
    # stored instead of sending it twice. Unique per sender (partial index in
    # the service's migrations).
    client_id: Mapped[str | None] = mapped_column(String(64))
    # Encryption at rest (common/crypto.py): the id of the key `body` and
    # `media_name` are sealed with, or None when they are stored as written.
    # Not to be confused with `encrypted`, which means end-to-end ciphertext
    # the server cannot open at all.
    sealed_with: Mapped[str | None] = mapped_column(String(16))
    # Disappearing messages. `expires_at` is a real deletion deadline, not a
    # display rule: a message that vanishes from the screen while sitting in the
    # database has not disappeared, it has only stopped being shown — which is
    # the opposite of what the promise means to the people in the room.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # An edit keeps the message and replaces its text, and says so. A silently
    # edited message is a way to change what somebody appears to have agreed
    # to after they agreed to it.
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A deleted message keeps its row and loses its words. The row is what lets
    # the thread say "this was deleted" instead of silently resequencing a
    # conversation; the words are gone from the database, not merely hidden,
    # which is the same promise `expires_at` makes above.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A sticker from the server's catalogue. Only the id is stored: the picture
    # is the server's, so a message cannot point at an arbitrary image, and a
    # sticker withdrawn from the catalogue stops rendering everywhere at once.
    sticker_id: Mapped[str | None] = mapped_column(String(40))
    # Marked as an announcement by an admin: drawn apart in the thread, and
    # what members who only want announcements are notified of.
    announcement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Removed by a moderator rather than deleted by its sender. Same effect on
    # the words (gone); the thread says it was removed.
    removed_by: Mapped[str | None] = mapped_column(String(40))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    lang: Mapped[str] = mapped_column(String(5), default="en")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ContactAttempt(Base):
    """Who tried to open a conversation with whom, and how it went.

    Exists for the pattern, not the individual row: one refusal is somebody
    mistyping a handle, and the same account refused by a dozen different
    minors in a month is the thing a Trust and Safety team needs to see. You
    cannot notice the second without having kept the first.

    Deliberately narrow - two ids, an outcome, two tiers and a timestamp. No
    message content, because a safety signal does not need to read what people
    wrote, and a table that did would be worth attacking.
    """

    __tablename__ = "contact_attempts"
    __table_args__ = (
        Index("ix_contact_sender_time", "sender_id", "created_at"),
        Index("ix_contact_outcome", "outcome", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_id: Mapped[str] = mapped_column(String(40), index=True)
    recipient_id: Mapped[str] = mapped_column(String(40), index=True)
    # allowed | refused_adult_to_minor | refused_not_connected
    outcome: Mapped[str] = mapped_column(String(40))
    sender_tier: Mapped[str] = mapped_column(String(30), default="")
    recipient_tier: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class MessageReaction(Base):
    """One person's reaction to one message.

    One per person per message, enforced by the constraint rather than by the
    client: tapping a second emoji replaces the first. Letting somebody stack
    reactions turns a quiet acknowledgement into a way to flood a thread.
    """

    __tablename__ = "message_reactions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_reaction"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(String(40), index=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    emoji: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PushSubscription(Base):
    """One browser that has agreed to receive notifications.

    A member has as many of these as they have devices, and each is an opaque
    endpoint at the browser vendor's push service plus the two keys that
    message is encrypted to. Those keys are what make Web Push private: the
    payload is sealed to this subscription, so Google or Mozilla relay it
    without being able to read it.

    `failures` is kept because a subscription does not announce that it is
    dead. A browser that is uninstalled, or permission that is revoked, shows
    up as a 404 or 410 from the push service, and those are deleted at once -
    but a run of softer failures means the same thing more slowly, and pushing
    forever to an endpoint that never answers is how a sending reputation is
    spent.
    """

    __tablename__ = "push_subscriptions"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    # The push service's URL for this browser. Unique: re-subscribing the same
    # browser must update the row rather than add a second one, or every
    # notification arrives twice.
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- groups: invitations, pins, saved, audit, reports ------------------------

MUTED_FOREVER = datetime(9999, 1, 1, tzinfo=timezone.utc)


class GroupInvite(Base):
    """An invitation to a group, which the invitee answers.

    Kept apart from Participant for the same reason JoinRequest is: having a
    participant row means reading the room, and nobody reads it before they
    said yes.
    """

    __tablename__ = "group_invites"
    __table_args__ = (
        UniqueConstraint("conversation_id", "invitee_id", name="uq_group_invite"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    invitee_id: Mapped[str] = mapped_column(String(40), index=True)
    inviter_id: Mapped[str] = mapped_column(String(40))
    # pending | accepted | declined | cancelled
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PinnedMessage(Base):
    __tablename__ = "pinned_messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_pinned_message"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    message_id: Mapped[str] = mapped_column(String(40))
    pinned_by: Mapped[str] = mapped_column(String(40))
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SavedMessage(Base):
    """A member's private bookmark. Nobody else is told, or can see it."""

    __tablename__ = "saved_messages"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_saved_message"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    message_id: Mapped[str] = mapped_column(String(40))
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GroupAudit(Base):
    """Who did what to the group: roles, removals, ownership, settings.

    Written in the same transaction as the change, so the log cannot say
    something happened that did not, or miss something that did. Never edited.
    """

    __tablename__ = "group_audit"
    __table_args__ = (
        Index("ix_group_audit_conversation", "conversation_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MessageReport(Base):
    """A member telling Kinjy's moderators about a message, a member or a group.

    One per reporter per target. A report goes to Kinjy, not to the group's
    admins: somebody reporting an admin must not have to send it to them.
    """

    __tablename__ = "message_reports"
    __table_args__ = (
        UniqueConstraint("reporter_id", "target_kind", "target_id", "conversation_id", name="uq_message_report"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_id: Mapped[str] = mapped_column(String(40), index=True)
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    # message | member | group
    target_kind: Mapped[str] = mapped_column(String(10))
    target_id: Mapped[str] = mapped_column(String(40))
    # spam | harassment | hate | violence | sexual | child_safety | scam | other
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    # open | closed
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(Text)
    closed_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- groups: polls, events, tasks ---------------------------------------------
#
# Each is posted into the thread as a message of its own kind, so it sits in
# order with the conversation and is held to the same rules as one: the same
# members, the same history rule, the same removal.


class Poll(Base):
    __tablename__ = "polls"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("pol"))
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    message_id: Mapped[str] = mapped_column(String(40), unique=True)
    created_by: Mapped[str] = mapped_column(String(40))
    question: Mapped[str] = mapped_column(Text)
    multiple: Mapped[bool] = mapped_column(Boolean, default=False)
    # always | after_vote | after_close
    results: Mapped[str] = mapped_column(String(12), default="always")
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PollOption(Base):
    __tablename__ = "poll_options"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(40), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(200))


class PollVote(Base):
    __tablename__ = "poll_votes"
    __table_args__ = (
        UniqueConstraint("poll_id", "option_id", "user_id", name="uq_poll_vote"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[str] = mapped_column(String(40), index=True)
    option_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GroupEvent(Base):
    """Something the group is doing at a time.

    `starts_at` is the instant (UTC); `timezone` is where it happens, so it can
    be shown in the event's own time as well as in each reader's.
    """

    __tablename__ = "group_events"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("gev"))
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    message_id: Mapped[str] = mapped_column(String(40), unique=True)
    created_by: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    location: Mapped[str | None] = mapped_column(String(300))
    online_url: Mapped[str | None] = mapped_column(String(500))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Reminders already sent, so a restart never sends one twice.
    reminded_day_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminded_hour_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EventResponse(Base):
    __tablename__ = "group_event_responses"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_group_event_response"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    # going | maybe | not_going
    response: Mapped[str] = mapped_column(String(10))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GroupTask(Base):
    __tablename__ = "group_tasks"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("gtk"))
    conversation_id: Mapped[str] = mapped_column(String(40), index=True)
    message_id: Mapped[str] = mapped_column(String(40), unique=True)
    created_by: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    assignee_id: Mapped[str | None] = mapped_column(String(40), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(40))
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
