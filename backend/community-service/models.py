from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.database import Base
from common.ids import new_id

SCHEMA = "community"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Community(Base):
    """Blueprint module E — public / private / secret / paid."""

    __tablename__ = "communities"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("cmy"))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="public")  # public|private|secret|paid
    # An open community's content is readable by anybody, including people who
    # have not joined and visitors who are not signed in: its page opens, a
    # link to one of its posts opens, and a preview is drawn when that link is
    # shared off Kinjy. Reading only - joining is still what lets somebody
    # like, comment or post.
    #
    # Defaults to True because that is already what an open community does: its
    # page has always been readable by anyone, and a default of False would
    # quietly close a door every existing public community already has open.
    # Only meaningful for kind == "public"; a private, secret or paid community
    # ignores it, since its whole promise is that content is for members.
    public_content: Mapped[bool] = mapped_column(Boolean, default=True)
    price_usd: Mapped[float | None] = mapped_column(Numeric(18, 2))
    lang: Mapped[str] = mapped_column(String(5), default="en")
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(120))
    members_count: Mapped[int] = mapped_column(Integer, default=0)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    # The photo and the banner, uploaded by the owner through media-service and
    # applied by id; the urls are what the pages draw.
    avatar_asset_id: Mapped[str | None] = mapped_column(String(40))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    cover_asset_id: Mapped[str | None] = mapped_column(String(40))
    # What lets a link to a *secret* community open for somebody outside it.
    # Minted the first time a member asks for a share link; public and private
    # communities never need one, their plain address already opens.
    invite_code: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("community_id", "user_id", name="uq_membership"),
        {"schema": SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    community_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # member|moderator|owner
    status: Mapped[str] = mapped_column(String(20), default="active")  # pending|active|banned
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Pinned to the member's sidebar, and when. On the membership because a pin
    # belongs to somebody *in* the group: leaving deletes the row and the pin
    # with it, and a ban keeps the row but the pinned list reads active only.
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Forum(Base):
    """Blueprint module C — forums nest by geography *and* by topic."""

    __tablename__ = "forums"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("frm"))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(140))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[str | None] = mapped_column(String(40), index=True)
    hierarchy: Mapped[str] = mapped_column(String(20), default="topic")  # topic|geographic
    scope: Mapped[str | None] = mapped_column(String(40))  # global|continent|country|state|city|neighborhood
    scope_value: Mapped[str | None] = mapped_column(String(120))
    community_id: Mapped[str | None] = mapped_column(String(40), index=True)
    threads_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("thr"))
    forum_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    replies_count: Mapped[int] = mapped_column(Integer, default=0)
    views_count: Mapped[int] = mapped_column(Integer, default=0)
    # Filled by ai-service; kept nullable so a thread is never blocked on the AI.
    ai_summary: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Reply(Base):
    __tablename__ = "replies"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rpl"))
    thread_id: Mapped[str] = mapped_column(String(40), index=True)
    author_id: Mapped[str] = mapped_column(String(40), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    accepted_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    upvotes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReplyVote(Base):
    """One member's upvote on one reply.

    `Reply.upvotes` is only a cached count. The votes live here so a member can
    vote once, take the vote back, and so the count can always be rebuilt from
    what actually happened instead of trusting an increment.
    """

    __tablename__ = "reply_votes"
    __table_args__ = (
        UniqueConstraint("reply_id", "user_id", name="uq_reply_vote"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rvt"))
    reply_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KnowledgeEntry(Base):
    """Forum-to-Knowledge transformation (blueprint §18).

    Every entry keeps its source thread and reply ids: the knowledge base cites
    the discussion it came from instead of asserting facts anonymously.
    """

    __tablename__ = "knowledge_entries"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("kbe"))
    forum_id: Mapped[str] = mapped_column(String(40), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(5), default="en")
    source_thread_ids: Mapped[str] = mapped_column(Text)  # csv
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    reviewed_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ContentSafetyClassification(Base):
    """Age suitability for a thread, a reply or a community.

    Same shape as the social schema's table on purpose: one vocabulary across
    the platform means one engine can read both, and a rating means the same
    thing wherever it was written.
    """

    __tablename__ = "content_safety"
    __table_args__ = (
        Index("ix_community_safety_rating", "age_rating"),
        Index("ix_community_safety_review", "human_review_status"),
        UniqueConstraint("content_id", name="uq_community_safety_content"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("csc"))
    content_id: Mapped[str] = mapped_column(String(40), index=True)
    content_kind: Mapped[str] = mapped_column(String(20), default="thread")  # thread|reply|community

    age_rating: Mapped[str] = mapped_column(String(20), default="UNCLASSIFIED")

    sexual_content_level: Mapped[int] = mapped_column(Integer, default=0)
    nudity_level: Mapped[int] = mapped_column(Integer, default=0)
    violence_level: Mapped[int] = mapped_column(Integer, default=0)
    graphic_content_level: Mapped[int] = mapped_column(Integer, default=0)
    drugs_level: Mapped[int] = mapped_column(Integer, default=0)
    alcohol_level: Mapped[int] = mapped_column(Integer, default=0)
    gambling_level: Mapped[int] = mapped_column(Integer, default=0)
    dangerous_activity_level: Mapped[int] = mapped_column(Integer, default=0)
    self_harm_risk: Mapped[int] = mapped_column(Integer, default=0)
    hate_or_abuse_risk: Mapped[int] = mapped_column(Integer, default=0)
    exploitation_risk: Mapped[int] = mapped_column(Integer, default=0)

    classifier_source: Mapped[str] = mapped_column(String(60), default="")
    classifier_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    human_review_status: Mapped[str] = mapped_column(String(20), default="none")
    jurisdiction_overrides: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
