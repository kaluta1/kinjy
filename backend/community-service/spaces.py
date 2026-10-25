"""Community templates and spaces.

A *template* is a starting configuration, not a separate kind of community:
every community is the same model with the same permissions, and a template
only chooses its defaults - which spaces are on, how private it starts, whether
posts wait for approval. Changing a template later never changes anybody's
access by itself.

A *space* is a part of a community a member can open: announcements,
discussions, articles... Each one is switched on or off by the owner, and the
server enforces it - a post addressed to a space that is off, or to the
announcements of somebody who is not a steward, is refused, whatever the page
shows.

Spaces whose feature is not built yet are listed with ``available: False`` so
an owner can see what is coming, but they cannot be switched on.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

import models


@dataclass(frozen=True)
class Space:
    id: str
    label: str
    description: str
    # Who may publish in it: "members", "stewards" (owner and moderators), or
    # None for a space that is not a stream of posts (the directory).
    posters: str | None
    available: bool


SPACES: tuple[Space, ...] = (
    Space("announcements", "Announcements", "News from the people who run the community.", "stewards", True),
    Space("discussions", "Discussions", "Questions and conversations between members.", "members", True),
    Space("articles", "Articles & resources", "Longer pieces, guides and useful links.", "members", True),
    Space("directory", "Member directory", "Who is in the community.", None, True),
    Space("courses", "Courses", "Structured lessons.", "stewards", False),
    Space("events", "Events", "Meetings, online or in person.", None, True),
    Space("marketplace", "Marketplace", "Members' products and services from the Kinjy marketplace.", None, True),
    Space("opportunities", "Opportunities", "Jobs, mentoring, collaborations and requests for quotes.", "members", True),
    Space("chat", "Chat", "A group conversation for members.", "members", False),
    Space("family", "Family archive", "Albums, documents and stories of the family.", "members", False),
    Space("memorial", "Memorial", "Tributes and memories.", "members", False),
)
BY_ID = {s.id: s for s in SPACES}
POST_SPACES = tuple(s.id for s in SPACES if s.posters and s.available)


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    description: str
    kind: str  # the privacy it starts with
    public_content: bool
    spaces: tuple[str, ...]
    post_approval: str  # none|new_members|all


TEMPLATES: tuple[Template, ...] = (
    Template("interest", "Interest group", "People who share a passion talk, ask and share.",
             "public", True, ("announcements", "discussions", "articles", "directory"), "none"),
    Template("paid_membership", "Membership community", "A members-only space. Paid access arrives with memberships.",
             "private", False, ("announcements", "discussions", "articles", "directory"), "none"),
    Template("learning", "Learning community", "Learners and teachers around a subject.",
             "public", True, ("announcements", "discussions", "articles", "directory"), "none"),
    Template("business", "Business & professional", "A network for a trade, a sector or a company.",
             "public", True, ("announcements", "discussions", "articles", "directory"), "none"),
    Template("family", "Private family", "A private place for one family. Hidden from search.",
             "secret", False, ("announcements", "discussions", "articles", "directory"), "none"),
    Template("memorial", "Memorial", "Remembering someone together. Tributes are approved before they appear.",
             "private", False, ("announcements", "discussions", "articles"), "all"),
    Template("association", "Association & alumni", "Members of an association, a school or a club.",
             "private", False, ("announcements", "discussions", "articles", "directory"), "none"),
)
TEMPLATE_IDS = tuple(t.id for t in TEMPLATES)
TEMPLATE_BY_ID = {t.id: t for t in TEMPLATES}
DEFAULT_TEMPLATE = "interest"


def template_of(community: models.Community) -> Template:
    return TEMPLATE_BY_ID.get(community.template or DEFAULT_TEMPLATE, TEMPLATE_BY_ID[DEFAULT_TEMPLATE])


def enabled_spaces(db: OrmSession, community: models.Community) -> set[str]:
    """The spaces switched on. Stored rows win; with none, the template's defaults.

    Communities made before spaces existed have no rows, and read as an
    interest group: announcements, discussions, articles and the directory -
    which is what they already offered (posts, and a member list).
    """
    rows = db.scalars(
        select(models.CommunitySpace).where(models.CommunitySpace.community_id == community.id)
    ).all()
    if not rows:
        return {s for s in template_of(community).spaces if BY_ID[s].available}
    return {r.space for r in rows if r.enabled and r.space in BY_ID and BY_ID[r.space].available}


def materialize(db: OrmSession, community: models.Community) -> None:
    """Write the current spaces as rows, so a later template change cannot move them."""
    have = {
        r.space
        for r in db.scalars(
            select(models.CommunitySpace).where(models.CommunitySpace.community_id == community.id)
        ).all()
    }
    if have:
        return
    on = enabled_spaces(db, community)
    for position, space in enumerate(SPACES):
        db.add(models.CommunitySpace(community_id=community.id, space=space.id, enabled=space.id in on,
                                     position=position))
    db.flush()


def spaces_out(on: set[str]) -> list[dict]:
    return [
        {"id": s.id, "label": s.label, "description": s.description, "enabled": s.id in on,
         "available": s.available, "posters": s.posters}
        for s in SPACES
    ]


def paid_spaces(db: OrmSession, community: models.Community) -> set[str]:
    """Spaces marked for paying members (meaningful in a mixed community)."""
    return {
        r.space
        for r in db.scalars(
            select(models.CommunitySpace).where(
                models.CommunitySpace.community_id == community.id, models.CommunitySpace.paid_only.is_(True))
        ).all()
    }


def can_post(space_id: str, role: str | None, on: set[str]) -> bool:
    space = BY_ID.get(space_id)
    if space is None or not space.available or space.posters is None or space_id not in on:
        return False
    if space.posters == "stewards":
        return role in ("owner", "moderator")
    return role in ("owner", "moderator", "member")


def templates_out() -> list[dict]:
    return [
        {"id": t.id, "label": t.label, "description": t.description, "kind": t.kind,
         "public_content": t.public_content, "spaces": list(t.spaces), "post_approval": t.post_approval}
        for t in TEMPLATES
    ]
