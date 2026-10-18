"""Feed ranking — transparent by construction.

Every score is the sum of named, inspectable components. The "Why am I seeing
this?" endpoint returns exactly those components, so the explanation is the real
computation rather than a plausible-sounding story generated after the fact.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import models


@dataclass
class Context:
    """Everything the ranker knows about the viewer."""

    user_id: str | None = None
    following: set[str] = field(default_factory=set)
    # False when user-service could not be asked: an empty `following` then
    # means "unknown", not "follows nobody", and must not be reported as such.
    graph_known: bool = False
    family: set[str] = field(default_factory=set)
    country: str | None = None
    city: str | None = None
    seen_authors: set[str] = field(default_factory=set)
    penalties: dict[str, float] = field(default_factory=dict)   # "author:usr_x" / "topic:ai" -> weight
    muted_authors: set[str] = field(default_factory=set)
    muted_topics: set[str] = field(default_factory=set)
    # Posts the member said "show less like this" about: gone from every feed
    # of theirs, whatever the mode, not merely ranked lower.
    hidden_posts: set[str] = field(default_factory=set)
    # Topics the member asked for in their settings. The counterweight to
    # `penalties`: until now the ranker could only be told what to show less of,
    # which meant a new member had no way to shape the feed except by waiting
    # for it to guess.
    interests: set[str] = field(default_factory=set)


@dataclass
class Scored:
    post: models.Post
    score: float
    components: dict[str, float]

    def why(self) -> list[dict]:
        """Human-readable breakdown, biggest contributor first."""
        labels = {
            "recency": "Posted recently",
            "affinity": "You follow the author",
            "family": "From your family circle",
            "locality": "Near you",
            "engagement": "Other people engaged with it",
            "new_creator": "A creator you have not seen before",
            "interest": "A topic you follow in your settings",
            "signal_penalty": "You asked for less like this",
        }
        return [
            {"factor": key, "label": labels.get(key, key), "contribution": round(value, 3)}
            for key, value in sorted(self.components.items(), key=lambda kv: -abs(kv[1]))
            if value
        ]


# Deliberately close to a follow (weight_affinity is 1.0 on most algorithms):
# a declared interest should compete with the follow graph without erasing it.
INTEREST_WEIGHT = 0.8


def _recency(post: models.Post) -> float:
    """1.0 at post time, halving roughly every 12 hours."""
    created = post.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    hours = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 3600)
    return math.exp(-hours / 17.31)  # ln(2)*12 ≈ 8.32 -> half-life 12h


def _engagement(post: models.Post) -> float:
    """Log-damped so a viral post cannot bury everything else."""
    raw = post.likes_count + 2 * post.comments_count + 3 * post.reposts_count
    return math.log1p(raw) / 10


def score_post(post: models.Post, algorithm: models.Algorithm, ctx: Context) -> Scored:
    components: dict[str, float] = {}

    if algorithm.weight_recency:
        components["recency"] = algorithm.weight_recency * _recency(post)
    if algorithm.weight_affinity and post.author_id in ctx.following:
        components["affinity"] = algorithm.weight_affinity
    if algorithm.weight_family and post.author_id in ctx.family:
        components["family"] = algorithm.weight_family
    if algorithm.weight_engagement:
        components["engagement"] = algorithm.weight_engagement * _engagement(post)
    if algorithm.weight_locality:
        local = 0.0
        if ctx.city and post.city and post.city.lower() == ctx.city.lower():
            local = 1.0
        elif ctx.country and post.country and post.country == ctx.country:
            local = 0.5
        # A negative locality weight (Global Discovery) rewards distance instead.
        components["locality"] = algorithm.weight_locality * (local if algorithm.weight_locality > 0 else 1 - local)
    if ctx.interests:
        post_topics = {t.strip().lower() for t in (post.topics or "").split(",") if t.strip()}
        matched = post_topics & ctx.interests
        if matched:
            # Flat, not proportional to how many matched: three of a member's
            # topics on one post says it is on-topic, not three times as good.
            components["interest"] = INTEREST_WEIGHT
    if algorithm.weight_new_creator and post.author_id not in ctx.seen_authors:
        components["new_creator"] = algorithm.weight_new_creator

    penalty = ctx.penalties.get(f"author:{post.author_id}", 0.0)
    for topic in (post.topics or "").split(","):
        topic = topic.strip().lower()
        if topic:
            penalty += ctx.penalties.get(f"topic:{topic}", 0.0)
    if penalty:
        components["signal_penalty"] = penalty

    return Scored(post=post, score=sum(components.values()), components=components)


def passes_filters(post: models.Post, algorithm: models.Algorithm, ctx: Context) -> bool:
    if post.author_id in ctx.muted_authors:
        return False

    post_topics = {t.strip().lower() for t in (post.topics or "").split(",") if t.strip()}
    if post_topics & ctx.muted_topics:
        return False

    if algorithm.topic_filter:
        wanted = {t.strip().lower() for t in algorithm.topic_filter.split(",") if t.strip()}
        if not (post_topics & wanted):
            return False

    if algorithm.format_filter:
        if post.format.lower() not in wanted_formats(algorithm):
            return False

    return True


def wanted_formats(algorithm: models.Algorithm) -> set[str]:
    """The post formats an algorithm keeps. A short is a video: "Video Only"
    that left out the vertical reel would be leaving out most of the video."""
    formats = {f.strip().lower() for f in (algorithm.format_filter or "").split(",") if f.strip()}
    if "video" in formats:
        formats.add("short")
    return formats


def wanted_topics(algorithm: models.Algorithm) -> set[str]:
    return {t.strip().lower() for t in (algorithm.topic_filter or "").split(",") if t.strip()}


def rank(posts: list[models.Post], algorithm: models.Algorithm, ctx: Context) -> list[Scored]:
    scored = [score_post(p, algorithm, ctx) for p in posts if passes_filters(p, algorithm, ctx)]
    scored.sort(key=lambda item: -item.score)
    return scored
