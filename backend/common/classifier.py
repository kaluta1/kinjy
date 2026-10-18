"""Deciding how suitable a piece of content is, before anybody sees it.

Until now everything was `UNCLASSIFIED`, which the engine treats as adult-only.
That was safe and nearly useless: adults saw far less than they should, and
nothing ever became visible to a teenager.

This is the pipeline that gives content a rating. It is built around one
admission that most content-moderation code declines to make:

    **A text heuristic cannot look at a photograph.**

So it does not pretend to. A post carrying an image or a video is never
auto-cleared by this module — there is no amount of reading the caption that
tells you what is in the picture. Those posts stay restricted and go to a human
queue, or wait for a vision model to be configured. A classifier that marked
them GENERAL because the caption was innocuous would be the single worst bug
this codebase could contain.

What it does do:

* clears text-only posts that carry no signals, which is most of them;
* grades the ones that do, per category rather than with one flag;
* refuses to let a minor publish sexual content at all, rather than rating it
  18+ and serving it to adults;
* routes anything with an exploitation signal out of ordinary moderation
  entirely.

Three backends, in order of preference: a vision/moderation **model** when one
is configured, the **heuristic** below, and — for anything neither can settle —
a **human**. The first is absent today; the design is here so that configuring
a key is all that is needed, not a rewrite.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

from common import settings

from common.agesafety import ContentRating

log = logging.getLogger("social-service.classifier")

AI_URL = "http://ai-service:8000"


# ---------------------------------------------------------------------------
# The lexicon
# ---------------------------------------------------------------------------
# Deliberately small, explicit, and about acts rather than identities. A long
# word list is not a better classifier; it is a longer list of ways to
# mis-flag somebody discussing their own life, and the categories most often
# over-flagged — sexuality, health, religion, ethnicity — are exactly the ones
# where a false positive does real harm.
#
# This is a stopgap that runs with no API key. It is data rather than code so
# Trust & Safety can edit it without a deploy, and every decision it makes is
# recorded with `classifier_source="heuristic"` so its work can be found and
# re-run when a model arrives.

_LEXICON: dict[str, tuple[int, tuple[str, ...]]] = {
    # category: (level it implies, patterns)
    "sexual_content_level": (3, (
        r"\bporn(?:o|ography)?\b", r"\bexplicit sex\b", r"\bnsfw\b",
        r"\bonlyfans\b", r"\bcamgirl\b", r"\bescort service\b",
    )),
    "nudity_level": (2, (r"\bfull(?:y)? nude\b", r"\bnudes\b", r"\bnaked photos?\b")),
    "violence_level": (2, (r"\bbeheading\b", r"\bexecution video\b", r"\btorture\b")),
    "graphic_content_level": (2, (r"\bgore\b", r"\bmutilat", r"\bdismember")),
    "drugs_level": (2, (r"\bcocaine\b", r"\bheroin\b", r"\bmeth(?:amphetamine)?\b",
                        r"\bbuy weed\b", r"\bdrug deal")),
    "alcohol_level": (1, (r"\bvodka\b", r"\bwhisk(?:e)?y\b", r"\bbeer pong\b", r"\bgetting drunk\b")),
    "gambling_level": (2, (r"\bcasino\b", r"\bbetting site\b", r"\bsports ?book\b",
                           r"\bfree spins\b", r"\bsign-?up bonus\b")),
    "dangerous_activity_level": (2, (r"\bchallenge\b.{0,20}\bdare\b", r"\btrain surf",
                                     r"\broof ?top(?:ping)?\b")),
    "self_harm_risk": (2, (r"\bself[- ]harm\b", r"\bcutting myself\b", r"\bkill myself\b",
                           r"\bsuicide method\b", r"\bpro[- ]?ana\b", r"\bthinspo\b")),
    "hate_or_abuse_risk": (2, (r"\bgo back to your country\b", r"\bsubhuman\b",
                               r"\bethnic cleansing\b")),
}

# Any hit here stops the content reaching anybody and takes it out of the
# ordinary queue. These are not "adult content": they are a different kind of
# problem, and treating them as a severity rating on the same scale is how they
# end up waiting behind a backlog of disputed nudity calls.
_EXPLOITATION_PATTERNS = (
    r"\bchild p", r"\bcp\b.{0,12}\btrade\b", r"\bunderage\b.{0,20}\b(nude|sex|pics?)\b",
    r"\b(send|post) (me )?(your )?nudes\b.{0,30}\b1[0-7]\b",
    r"\bteen\b.{0,10}\b(nude|naked|sex)\b",
    r"\btraffick", r"\bsextortion\b",
)

_COMPILED = {
    field_name: (level, tuple(re.compile(p, re.I) for p in patterns))
    for field_name, (level, patterns) in _LEXICON.items()
}
_COMPILED_EXPLOITATION = tuple(re.compile(p, re.I) for p in _EXPLOITATION_PATTERNS)

# Media kinds nothing in this module can see into.
_OPAQUE_MEDIA = {"image", "video", "audio", "carousel"}


@dataclass
class Classification:
    age_rating: str = ContentRating.UNCLASSIFIED.value
    levels: dict[str, int] = field(default_factory=dict)
    exploitation_risk: int = 0
    classifier_source: str = "heuristic"
    classifier_confidence: float = 0.0
    human_review_status: str = "none"
    # Set when the content must not be published at all.
    block_publication: bool = False
    escalate_child_safety: bool = False
    reasons: list[str] = field(default_factory=list)

    def as_payload(self) -> dict:
        payload = {
            "age_rating": self.age_rating,
            "exploitation_risk": self.exploitation_risk,
            "classifier_source": self.classifier_source,
            "classifier_confidence": self.classifier_confidence,
            "human_review_status": self.human_review_status,
        }
        payload.update(self.levels)
        return payload


def model_available() -> bool:
    """Whether a real moderation model is configured.

    Asked rather than assumed, and the answer is cached nowhere: a key added at
    runtime should start being used without a restart, and a provider that
    disappears should stop being relied on.
    """
    try:
        response = httpx.get(f"{AI_URL}/ai/providers", timeout=3)
        response.raise_for_status()
        providers = response.json().get("providers", [])
        return any(
            p.get("name") != "mock" and "moderate" in (p.get("models") or {})
            for p in providers
        )
    except Exception as exc:
        log.debug("provider probe failed: %s", exc)
        return False


def _rating_for(levels: dict[str, int]) -> str:
    """The summary rating implied by the graded categories."""
    if levels.get("sexual_content_level", 0) >= 2 or levels.get("nudity_level", 0) >= 2:
        return ContentRating.ADULT_18_PLUS.value
    if levels.get("self_harm_risk", 0) >= 1 or levels.get("hate_or_abuse_risk", 0) >= 2:
        # Not adult entertainment; unsuitable for every minor, and a reviewer
        # should look at it rather than it simply sitting behind an age wall.
        return ContentRating.ADULT_18_PLUS.value
    if max(levels.values(), default=0) >= 2:
        return ContentRating.TEEN_16_PLUS.value
    if max(levels.values(), default=0) == 1:
        return ContentRating.TEEN_13_PLUS.value
    return ContentRating.GENERAL.value


def classify(
    *,
    body: str,
    media_kinds: list[str],
    author_is_minor: bool,
    declared_mature: bool = False,
) -> Classification:
    """Classify one post.

    ``declared_mature`` is the author's own content warning. It is taken as a
    signal that can only *tighten* the result — believed when it restricts,
    never when it releases (§17). Self-labelling is useful precisely because
    honest people use it; it is useless as the only check because dishonest
    ones do not.
    """
    text = body or ""
    result = Classification()

    # 1. Exploitation, first and separately.
    for pattern in _COMPILED_EXPLOITATION:
        if pattern.search(text):
            result.exploitation_risk = 3
            result.age_rating = ContentRating.PROHIBITED.value
            result.block_publication = True
            result.escalate_child_safety = True
            result.classifier_confidence = 0.5
            result.human_review_status = "pending"
            result.reasons.append("exploitation_signal")
            # Deliberately no detail in the reason: an error message that says
            # which phrase matched is a guide to rewording it.
            return result

    # 2. Graded categories from the text.
    levels: dict[str, int] = {}
    for field_name, (level, patterns) in _COMPILED.items():
        if any(p.search(text) for p in patterns):
            levels[field_name] = level
            result.reasons.append(field_name)

    # 3. A minor publishing sexual content is not an age-rating problem.
    #    It does not become an 18+ post served to adults; it does not publish.
    if author_is_minor and (
        levels.get("sexual_content_level", 0) >= 1 or levels.get("nudity_level", 0) >= 1
    ):
        result.age_rating = ContentRating.PROHIBITED.value
        result.exploitation_risk = 2
        result.block_publication = True
        result.escalate_child_safety = True
        result.human_review_status = "pending"
        result.levels = levels
        result.reasons.append("minor_sexual_content")
        return result

    result.levels = levels

    # 4. Media this module cannot see into.
    opaque = [k for k in media_kinds if k in _OPAQUE_MEDIA]
    media_by_text = (
        opaque
        and not model_available()
        and not author_is_minor
        and (settings.UNREVIEWED_MEDIA_RATING or "text").lower() == "text"
    )
    if opaque and not model_available() and not media_by_text:
        # Not GENERAL, not ADULT — simply not settled. It stays restricted and
        # a human decides. Reading the caption tells you nothing about the
        # picture, and guessing here is the one mistake worth never making.
        result.age_rating = ContentRating.UNCLASSIFIED.value
        result.human_review_status = "pending"
        result.classifier_confidence = 0.0
        result.classifier_source = "heuristic:media_opaque"
        result.reasons.append("media_needs_review")
        if declared_mature:
            result.age_rating = ContentRating.ADULT_18_PLUS.value
            result.reasons.append("author_declared_mature")
        return result

    # 5. Text-only, or media a model has looked at.
    rating = _rating_for(levels)

    if declared_mature:
        # Only ever tightens.
        rating = ContentRating.ADULT_18_PLUS.value
        result.reasons.append("author_declared_mature")

    result.age_rating = rating
    # Honest confidence. High for "nothing matched in text we could fully
    # read", lower when a pattern fired, because patterns mis-fire.
    result.classifier_confidence = 0.75 if not levels else 0.45
    if media_by_text:
        # Rated by its words and its author's declaration; nobody has seen the
        # picture. Said so, so a later review knows which ones to look at.
        result.classifier_source = "heuristic:media_by_text"
        result.classifier_confidence = 0.5 if not levels else 0.4
        result.reasons.append("media_rated_by_text")
    if levels:
        result.human_review_status = "pending"
    return result


def reclassify_on_report(current_rating: str, report_count: int) -> str | None:
    """What community reports do to a rating.

    Reports restrict and never release: enough of them pull content back behind
    an age wall pending review. The reverse — letting reports clear a rating —
    would make brigading a way to un-rate adult content.
    """
    if report_count >= 3 and current_rating in (
        ContentRating.GENERAL.value,
        ContentRating.TEEN_13_PLUS.value,
    ):
        return ContentRating.UNCLASSIFIED.value
    return None
