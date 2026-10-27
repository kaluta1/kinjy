from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "users"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Profile(Base):
    """Public-facing identity. Created lazily on first read or via the
    ``user.registered`` event."""

    __tablename__ = "profiles"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    handle: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    # The media-service asset behind each image. The URL is never taken from
    # the client: it is copied from an asset this member owns and that
    # media-service reports ready. Kept so a replaced image can be deleted.
    avatar_asset_id: Mapped[str | None] = mapped_column(String(40))
    cover_asset_id: Mapped[str | None] = mapped_column(String(40))

    # Geographic discovery ladder (blueprint module B):
    # Global > Continent > Region > Country > State > District > City > Neighborhood
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    state: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    neighborhood: Mapped[str | None] = mapped_column(String(120))

    languages: Mapped[str] = mapped_column(String(60), default="en")  # csv, ordered by fluency
    lang: Mapped[str] = mapped_column(String(5), default="en")
    is_creator: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)

    followers_count: Mapped[int] = mapped_column(Integer, default=0)
    following_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("follower_id", "followee_id", name="uq_follow"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    follower_id: Mapped[str] = mapped_column(String(40), index=True)
    followee_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Circle(Base):
    """Blueprint module D — private/filtered networks.

    ``kind`` is one of family | close_friends | business | customers | smart |
    custom. A *smart* circle carries a ``rule`` (JSON string) evaluated at read
    time instead of a fixed member list.
    """

    __tablename__ = "circles"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cir"))
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(30), default="custom")
    color: Mapped[str | None] = mapped_column(String(20))
    rule: Mapped[str | None] = mapped_column(Text)
    members_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CircleMember(Base):
    __tablename__ = "circle_members"
    __table_args__ = (
        UniqueConstraint("circle_id", "member_id", name="uq_circle_member"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    circle_id: Mapped[str] = mapped_column(ForeignKey(f"{SCHEMA}.circles.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(String(40), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Connection(Base):
    """A mutual relationship, unlike a follow.

    Following is one-way and needs nobody's permission. A connection is an
    invitation the other person accepts, and that acceptance is what unlocks
    messaging, family links and community invites — so the row is the authority
    those services check, not a UI convention.

    Stored once per pair with the requester on the left; ``between`` normalises
    lookups so "did A and B connect?" never depends on who asked first.
    """

    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("requester_id", "addressee_id", name="uq_connection_pair"),
        Index("ix_connection_addressee_status", "addressee_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requester_id: Mapped[str] = mapped_column(String(40), index=True)
    addressee_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|accepted|declined
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Preferences(Base):
    """Feed modes, algorithm choice, wellbeing, data-saver, privacy."""

    __tablename__ = "preferences"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # "new", not "following": a member who has just joined follows nobody, so a
    # Following feed is empty by construction. Defaulting to it meant every new
    # account opened the app to a blank page - which reads as the posts having
    # disappeared rather than as a feed working exactly as specified.
    default_feed_mode: Mapped[str] = mapped_column(String(30), default="new")
    algorithm_id: Mapped[str] = mapped_column(String(60), default="chronological")
    display_mode: Mapped[str] = mapped_column(String(20), default="cloud")  # cloud|light|system
    data_saver: Mapped[bool] = mapped_column(Boolean, default=False)
    reduced_motion: Mapped[bool] = mapped_column(Boolean, default=False)
    autoplay_media: Mapped[bool] = mapped_column(Boolean, default=True)
    wellbeing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_limit_minutes: Mapped[int | None] = mapped_column(Integer)
    age_mode: Mapped[str] = mapped_column(String(20), default="adult")  # child|teen|adult
    translation_auto: Mapped[bool] = mapped_column(Boolean, default=True)
    # Comma-separated topics the member says they want more of. Declared
    # interests, not inferred ones: the ranker already learns from what you
    # penalise, and this is the other half — a way to ask for something
    # without having to engage with it first.
    interest_topics: Mapped[str | None] = mapped_column(Text)

    # --- Privacy (blueprint: the member decides who may reach them) ---
    # Each is one of: everyone | connections | nobody. The defaults are the
    # cautious reading: anyone may ask to connect, but only an accepted
    # connection may message, add you to a family tree, or pull you into a
    # community. Opening those up is a choice the member makes, not a default
    # they have to discover and undo.
    who_can_invite: Mapped[str] = mapped_column(String(20), default="everyone")
    who_can_message: Mapped[str] = mapped_column(String(20), default="connections")
    who_can_add_family: Mapped[str] = mapped_column(String(20), default="connections")
    who_can_add_community: Mapped[str] = mapped_column(String(20), default="connections")
    # Group chats in Messages. Being put straight into a group is the stronger
    # act, so it starts at connections; an invitation is a request the member
    # answers, so it starts open, like a connection invitation.
    who_can_add_group: Mapped[str] = mapped_column(String(20), default="connections")
    who_can_invite_group: Mapped[str] = mapped_column(String(20), default="everyone")
    # The family tree, which the member decides for themselves rather than the
    # code deciding for them. Default is the cautious reading: only people who
    # share the graph. `everyone` is a deliberate act, never a default.
    who_can_see_family: Mapped[str] = mapped_column(String(20), default="family")  # family|connections|everyone
    # Sharing the tree at all is separable from who may see it: a member can
    # close it entirely without changing the audience they would otherwise pick.
    family_tree_shared: Mapped[bool] = mapped_column(Boolean, default=True)
    discoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    # The floating assistant orb. Always-on-top UI should be dismissible, and a
    # setting is honest about it where an X that reappears next visit is not.
    assistant_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Usage(Base):
    """Daily active minutes, for the wellbeing limit.

    Server-side on purpose: a counter kept in the browser is reset by a reload,
    a private window or a second device, which turns a limit into a suggestion.
    """

    __tablename__ = "usage"
    __table_args__ = {"schema": SCHEMA}

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    minutes: Mapped[float] = mapped_column(Float, default=0.0)
    last_beat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        UniqueConstraint("user_id", "blocked_id", name="uq_block"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    blocked_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ParentalSupervision(Base):
    """A supervision link between a parent and a teenager.

    Both sides must agree: whoever starts it invites, and the other accepts.
    Either can end it afterwards and the other is told, because supervision
    somebody cannot leave is not supervision, and supervision that ends in
    silence is its own problem.

    No message content, no contact list, no browsing. What this row grants is
    written out in parental.CAN_SEE and parental.CANNOT_SEE, and served to both
    parties before either agrees.
    """

    __tablename__ = "parental_supervision"
    __table_args__ = (
        Index("ix_supervision_teen", "teen_id", "status"),
        Index("ix_supervision_parent", "parent_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("sup"))
    teen_id: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str] = mapped_column(String(40), index=True)
    # invited | active | declined | ended
    status: Mapped[str] = mapped_column(String(20), default="invited")
    invited_by: Mapped[str] = mapped_column(String(40))
    daily_limit_minutes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by: Mapped[str | None] = mapped_column(String(40))


class SupervisionRequest(Base):
    """A teenager asking to loosen a safety setting, and the answer.

    Kept after it is answered. A teenager should be able to see what was
    refused and when, rather than finding a setting that will not move and no
    record of why.
    """

    __tablename__ = "supervision_requests"
    __table_args__ = (
        Index("ix_supervision_request_state", "parent_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("spr"))
    supervision_id: Mapped[str] = mapped_column(String(40), index=True)
    teen_id: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str] = mapped_column(String(40), index=True)
    setting: Mapped[str] = mapped_column(String(60))
    requested_value: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|declined
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
