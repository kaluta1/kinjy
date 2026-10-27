"""One-to-Many: a post, also made into a short video, a newsletter draft and four translations.

The composer asks for it once the post exists (`POST /creators/publish`). What
happens next is here:

* **A durable queue.** Each thing to produce is a ``publishing_outputs`` row
  (``pipeline = 'v2'``). A worker loop in this service claims one at a time with
  ``FOR UPDATE SKIP LOCKED`` and a lease, so two workers never take the same
  row and a worker that dies mid-way leaves a row that is taken again once its
  lease runs out. Attempts are counted and bounded; a row that runs out fails
  for good with its last error, and the others carry on.
* **From the post, not from the request.** The text and pictures come from
  social-service at the moment of production. Nothing produced can say more
  than the post does, and an edit, a hide or a delete is seen.
* **Checked before it counts.** A translation must come from a real model and
  differ from the original; a newsletter must have a subject and a body; a
  video must be probed as a playable vertical H.264 file. Otherwise the row is
  failed, never "completed" with something empty or fake.
* **Never sent.** A newsletter is a draft. There is no code path here that
  e-mails anybody.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as OrmSession

from common import settings
from common.article_html import sanitize_article
from common.database import SessionLocal

import models

log = logging.getLogger("creator-service.onetomany")

AI_URL = "http://ai-service:8000"
SOCIAL_URL = "http://social-service:8000"
MEDIA_URL = "http://media-service:8000"

# What the composer offers. The other targets the model knows about (article,
# long video, audio, carousel) have no producer and are refused, not queued.
TARGETS = ("short_video", "newsletter", "translation")

# Server settings, not code: how hard to try and how long a claim lasts.
MAX_ATTEMPTS = int(os.getenv("ONE_TO_MANY_MAX_ATTEMPTS", "3"))
LEASE_S = int(os.getenv("ONE_TO_MANY_LEASE_S", "300"))
POLL_S = float(os.getenv("ONE_TO_MANY_POLL_S", "3"))
RETRY_BASE_S = int(os.getenv("ONE_TO_MANY_RETRY_BASE_S", "30"))
WORKER_ENABLED = os.getenv("ONE_TO_MANY_WORKER", "1") not in ("0", "false", "off")

ACTIVE = ("queued", "processing")
TERMINAL = ("completed", "failed", "cancelled")

# Rows written before this pipeline used other words for the same states.
LEGACY_STATUS = {"ready": "completed", "done": "completed", "unsupported": "failed", "running": "processing"}


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class Permanent(Exception):
    """Trying again would give the same answer."""


class Retryable(Exception):
    """A service was down or slow; worth another attempt."""


class SourceGone(Exception):
    """The post was deleted or taken down."""


# --- the source post ---------------------------------------------------------------

def fetch_source(post_id: str) -> dict | None:
    """The post as social-service has it now; None when it does not exist."""
    try:
        response = httpx.get(f"{SOCIAL_URL}/internal/posts/{post_id}/source", timeout=10)
    except httpx.HTTPError as exc:
        raise Retryable("the post could not be read") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise Retryable(f"the post could not be read ({response.status_code})")
    return response.json()


def source_deleted(source: dict | None) -> bool:
    return source is None or source.get("status") in ("removed", "hidden")


def revision_of(source: dict) -> str:
    """A short fingerprint of the post's text and pictures as they stand."""
    material = "|".join(
        [source.get("revision") or "", source.get("body") or "", ",".join(m["media_id"] for m in source.get("media") or [])]
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


class _Text(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "figure", "tr", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(source: dict) -> str:
    body = source.get("body") or ""
    if source.get("format") == "article" or re.search(r"<(p|h[1-6]|div|br)\b", body):
        parser = _Text()
        parser.feed(body)
        body = "".join(parser.parts)
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in body.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text.strip()


def post_url(post_id: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/p/{post_id}"


# --- what is on offer --------------------------------------------------------------

def capabilities(source_lang: str | None) -> dict:
    """Which targets can really be produced right now, and why not when not."""
    langs = [lang for lang in settings.SUPPORTED_LANGS if lang != source_lang]
    out = {
        "newsletter": {"available": True, "reason": None},
        "translation": {"available": False, "reason": "Translation is not set up on this server yet.", "langs": langs},
        "short_video": {"available": False, "reason": "Video rendering is not set up on this server yet."},
    }
    try:
        ai = httpx.get(f"{AI_URL}/internal/ai/capabilities", timeout=5).json()
        if (ai.get("translate") or {}).get("live"):
            out["translation"] = {"available": True, "reason": None, "langs": langs}
    except Exception as exc:
        log.warning("ai capabilities unreachable: %s", exc)
        out["translation"]["reason"] = "The translation service cannot be reached right now."
    try:
        media = httpx.get(f"{MEDIA_URL}/internal/media/render-capabilities", timeout=5).json()["short_video"]
        if not media.get("available"):
            out["short_video"]["reason"] = "Video rendering is not set up on this server yet."
        elif source_lang in (media.get("unsupported_langs") or []):
            out["short_video"]["reason"] = "Title cards cannot be drawn in this language yet."
        elif not media.get("narration"):
            # Not offered as a silent video: a short video is a narrated one.
            out["short_video"]["reason"] = (
                "Voice narration is not set up on this server yet, so videos cannot be made."
                if "narration" in media else "The video renderer needs updating before it can narrate videos."
            )
        elif (source_lang or "en") not in (media.get("narration_langs") or []):
            out["short_video"]["reason"] = "Voice narration is not available in this language yet."
        else:
            out["short_video"] = {"available": True, "reason": None}
    except Exception as exc:
        log.warning("media capabilities unreachable: %s", exc)
        out["short_video"]["reason"] = "The video renderer cannot be reached right now."
    return out


def planned_outputs(targets: list[str], source_lang: str) -> list[tuple[str, str]]:
    """(target, lang) pairs for a request. Translations: every platform
    language except the post's own - the "×4"."""
    pairs: list[tuple[str, str]] = []
    for target in TARGETS:  # a fixed order, whatever order they were asked in
        if target not in targets:
            continue
        if target == "translation":
            pairs.extend(("translation", lang) for lang in settings.SUPPORTED_LANGS if lang != source_lang)
        else:
            pairs.append((target, source_lang))
    return pairs


# --- producers ---------------------------------------------------------------------

def produce_translation(source: dict, lang: str) -> dict:
    text = plain_text(source)
    if not text:
        raise Permanent("The post has no text to translate.")
    try:
        response = httpx.post(
            f"{AI_URL}/ai/translate",
            json={"text": text[:20000], "source_lang": source.get("lang"), "target_lang": lang, "require_live": True},
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise Retryable("The translation service did not answer.") from exc
    if response.status_code == 503:
        raise Permanent("Translation is not set up on this server.")
    if response.status_code in (400, 422):
        raise Permanent(_detail(response) or "This text cannot be translated.")
    if response.status_code != 200:
        raise Retryable(f"The translation failed ({response.status_code}).")
    data = response.json()
    translated = (data.get("translated") or "").strip()
    # Checked here, whatever ai-service says: an empty answer, the original back,
    # or a placeholder is not a translation.
    if data.get("mock") or not translated or translated.startswith(f"[{lang}] "):
        raise Permanent("No real translation was produced.")
    if len(text) > 24 and translated == text:
        raise Retryable("The translation came back unchanged.")
    return {"content": translated, "provider": data.get("provider"), "provenance": "ai_generated"}


def build_newsletter(source: dict) -> dict:
    """A newsletter draft in the author's own words.

    The first line is the subject, the next is the preview line and the
    introduction, the rest is the body; a link back to the post closes it when
    the post is public. Restructured, not rewritten.
    """
    text = plain_text(source)
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        raise Permanent("The post has no text for a newsletter.")
    first = paragraphs[0]
    if len(first) > 120:
        cut = first.rfind(" ", 0, 110)
        subject = first[: cut if cut > 30 else 110].rstrip(" ,;:") + "…"
        rest = paragraphs
    else:
        subject = first
        rest = paragraphs[1:] or paragraphs
    intro, body = rest[0], rest[1:]
    preview = intro if len(intro) <= 140 else intro[: max(intro.rfind(" ", 0, 137), 60)].rstrip(" ,;:") + "…"
    public = source.get("visibility") == "public"
    link = post_url(source["id"]) if public else None

    parts = [f"<h2>{html.escape(subject)}</h2>", f"<p>{html.escape(intro)}</p>"]
    parts.extend(f"<p>{html.escape(p)}</p>" for p in body)
    if link:
        parts.append(f'<p><a href="{html.escape(link)}">Read it on Kinjy</a></p>')
    byline = source.get("author_name")
    if byline:
        parts.append(f"<p>— {html.escape(byline)}</p>")
    safe = sanitize_article("\n".join(parts))
    plain = "\n\n".join([subject, intro, *body, *([f"Read it on Kinjy: {link}"] if link else []), *([f"— {byline}"] if byline else [])])
    return {"title": subject[:300], "preview_text": preview[:300], "content_html": safe, "content": plain}


def validate_newsletter(draft: dict) -> None:
    if not (draft.get("title") or "").strip():
        raise Permanent("The draft has no subject.")
    if not (draft.get("content") or "").strip() or not re.search(r"<p\b", draft.get("content_html") or ""):
        raise Permanent("The draft has no body.")


def produce_short(source: dict, output: models.PublishingOutput, revision: str) -> dict:
    pictures = [m["media_id"] for m in source.get("media") or [] if m.get("kind") == "image"]
    text = plain_text(source)
    if not text and not pictures:
        raise Permanent("The post has neither text nor pictures for a video.")
    try:
        response = httpx.post(
            f"{MEDIA_URL}/internal/media/render-short",
            json={
                "owner_id": source["author_id"],
                "job_ref": f"{output.job_id}-{output.id}-{revision}",
                "text": text[:20000],
                "image_ids": pictures[:10],
                "byline": (source.get("author_name") or "")[:120] or None,
                # Read aloud in the post's own language.
                "lang": source.get("lang") or output.lang,
                "narrate": True,
            },
            timeout=300,
        )
    except httpx.HTTPError as exc:
        raise Retryable("The video renderer did not answer.") from exc
    if response.status_code == 422:
        raise Permanent(_detail(response) or "This post cannot be made into a video.")
    if response.status_code != 200:
        raise Retryable(f"The video could not be rendered ({response.status_code}).")
    data = response.json()
    if not data.get("asset_id") or not data.get("duration_s"):
        raise Retryable("The renderer returned no video.")
    if not data.get("narrated"):
        # Checked here too: a video without its voice is not the output asked for.
        discard_video(source["author_id"], data["asset_id"])
        raise Permanent("The video came back without narration. It was not kept.")
    return {
        "media_id": data["asset_id"],
        "media_url": data["url"],
        "duration_s": data["duration_s"],
        "provenance": "ai_assisted",
        # "+voice": read aloud. Videos made before narration existed say
        # "kinjy-render" alone, which is how the panel tells them apart.
        "provider": "kinjy-render+voice",
    }


def _detail(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except Exception:
        return None
    return detail if isinstance(detail, str) else None


def discard_video(owner_id: str, media_id: str | None) -> None:
    if not media_id:
        return
    try:
        httpx.post(f"{MEDIA_URL}/internal/media/{media_id}/discard", json={"owner_id": owner_id}, timeout=10)
    except httpx.HTTPError as exc:
        log.warning("video %s not discarded: %s", media_id, exc)


# --- state -------------------------------------------------------------------------

def status_of(statuses: list[str]) -> str:
    """A job's state from its outputs' states."""
    statuses = [LEGACY_STATUS.get(s, s) for s in statuses]
    if any(s in ACTIVE for s in statuses):
        return "processing" if "processing" in statuses or "completed" in statuses else "queued"
    if statuses and all(s == "completed" for s in statuses):
        return "completed"
    if "completed" in statuses:
        return "partial"
    if statuses and all(s == "cancelled" for s in statuses):
        return "cancelled"
    return "failed"


def refresh_job(db: OrmSession, job: models.PublishingJob) -> None:
    db.flush()  # the sessions here do not autoflush; count what was just changed
    job.status = status_of(
        list(db.scalars(select(models.PublishingOutput.status).where(models.PublishingOutput.job_id == job.id)))
    )
    job.updated_at = now()


def withdraw(db: OrmSession, job: models.PublishingJob, reason: str) -> int:
    """The post is gone: stop what is pending, and take the generated videos down."""
    changed = 0
    for output in db.scalars(select(models.PublishingOutput).where(models.PublishingOutput.job_id == job.id)):
        if output.status in ACTIVE or (output.status == "completed" and output.target == "short_video"):
            if output.target == "short_video" and output.media_id:
                discard_video(job.creator_id, output.media_id)
                output.media_id = None
                output.media_url = None
            output.status = "cancelled"
            output.error = reason
            output.lease_until = None
            output.updated_at = now()
            changed += 1
    refresh_job(db, job)
    return changed


def claim(db: OrmSession) -> int | None:
    """Take the next row that is due. Returns its id, committed as processing."""
    moment = now()
    row = db.scalar(
        select(models.PublishingOutput)
        .where(
            models.PublishingOutput.pipeline == "v2",
            or_(
                (models.PublishingOutput.status == "queued")
                & or_(models.PublishingOutput.next_attempt_at.is_(None), models.PublishingOutput.next_attempt_at <= moment),
                (models.PublishingOutput.status == "processing") & (models.PublishingOutput.lease_until < moment),
            ),
        )
        .order_by(models.PublishingOutput.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        db.rollback()
        return None
    if row.status == "processing" and (row.attempts or 0) >= (row.max_attempts or MAX_ATTEMPTS):
        # Abandoned: the worker holding it stopped, and it has had its tries.
        row.status = "failed"
        row.error = (row.error or "The work stopped before it finished.")[:300]
        row.lease_until = None
        row.updated_at = moment
        job = db.get(models.PublishingJob, row.job_id)
        if job is not None:
            refresh_job(db, job)
        db.commit()
        return None
    row.status = "processing"
    row.attempts = (row.attempts or 0) + 1
    row.lease_until = moment + timedelta(seconds=LEASE_S)
    row.started_at = row.started_at or moment
    row.updated_at = moment
    job = db.get(models.PublishingJob, row.job_id)
    if job is not None:
        refresh_job(db, job)
    db.commit()
    return row.id


def process(output_id: int) -> str:
    """Produce one claimed row. Returns its new status."""
    with SessionLocal() as db:
        output = db.get(models.PublishingOutput, output_id)
        if output is None or output.status != "processing":
            return "skipped"
        job = db.get(models.PublishingJob, output.job_id)
        attempt = output.attempts
        try:
            source = fetch_source(job.source_ref or "")
            if source_deleted(source):
                raise SourceGone()
            if source["author_id"] != job.creator_id:
                raise Permanent("This post belongs to somebody else.")
            revision = revision_of(source)
            if output.target == "translation":
                result = produce_translation(source, output.lang)
            elif output.target == "newsletter":
                result = build_newsletter(source)
                validate_newsletter(result)
                result["provenance"] = "edited"
                result["provider"] = "kinjy"
            elif output.target == "short_video":
                result = produce_short(source, output, revision)
            else:
                raise Permanent("Nothing produces this format.")
        except SourceGone:
            db.refresh(output)
            withdraw(db, job, "The post was deleted, so this was withdrawn.")
            db.commit()
            return "cancelled"
        except Permanent as exc:
            return _fail(db, output_id, attempt, str(exc), final=True)
        except Retryable as exc:
            return _fail(db, output_id, attempt, str(exc), final=False)
        except Exception as exc:  # a bug must not wedge the queue
            log.exception("output %s failed unexpectedly", output_id)
            return _fail(db, output_id, attempt, f"Unexpected error ({type(exc).__name__}).", final=False)

        db.refresh(output)
        if output.status != "processing" or output.attempts != attempt:
            # Cancelled, or re-claimed after our lease ran out: not ours to write.
            if output.target == "short_video" and result.get("media_id") and result["media_id"] != output.media_id:
                discard_video(job.creator_id, result["media_id"])
            return "skipped"
        previous_video = output.media_id
        for key, value in result.items():
            setattr(output, key, value)
        if output.target == "newsletter":
            output.user_edited = False
        output.status = "completed"
        output.error = None
        output.source_revision = revision
        output.lease_until = None
        output.next_attempt_at = None
        output.completed_at = output.updated_at = now()
        refresh_job(db, job)
        db.commit()
        if previous_video and previous_video != output.media_id:
            discard_video(job.creator_id, previous_video)
        return "completed"


def _fail(db: OrmSession, output_id: int, attempt: int, message: str, *, final: bool) -> str:
    db.rollback()
    output = db.get(models.PublishingOutput, output_id)
    if output is None or output.status != "processing" or output.attempts != attempt:
        return "skipped"
    exhausted = output.attempts >= (output.max_attempts or MAX_ATTEMPTS)
    output.error = message[:300]
    output.lease_until = None
    output.updated_at = now()
    if final or exhausted:
        output.status = "failed"
    else:
        output.status = "queued"
        output.next_attempt_at = now() + timedelta(seconds=RETRY_BASE_S * 2 ** (output.attempts - 1))
    job = db.get(models.PublishingJob, output.job_id)
    if job is not None:
        refresh_job(db, job)
    db.commit()
    return output.status


def drain_once() -> bool:
    """Claim and produce one row. False when nothing was due."""
    with SessionLocal() as db:
        output_id = claim(db)
    if output_id is None:
        return False
    process(output_id)
    return True


async def worker_loop() -> None:
    from starlette.concurrency import run_in_threadpool

    log.info("one-to-many worker started (lease %ss, %s attempts)", LEASE_S, MAX_ATTEMPTS)
    while True:
        try:
            busy = await run_in_threadpool(drain_once)
        except Exception as exc:
            log.error("one-to-many worker: %s", exc)
            busy = False
        if not busy:
            await asyncio.sleep(POLL_S)


def start_worker() -> None:
    if WORKER_ENABLED:
        asyncio.get_event_loop().create_task(worker_loop())


# --- reading -----------------------------------------------------------------------

def output_view(output: models.PublishingOutput, viewer: str, current_revision: str | None, public: bool) -> dict:
    from common import mediasign

    status = LEGACY_STATUS.get(output.status, output.status)
    view = {
        "id": output.id,
        "target": output.target,
        "lang": output.lang,
        "rtl": output.lang in settings.RTL_LANGS,
        "status": status,
        "error": output.error,
        "attempts": output.attempts or 0,
        "max_attempts": output.max_attempts or MAX_ATTEMPTS,
        "provenance": output.provenance,
        "provider": output.provider,
        "updated_at": (output.updated_at or output.created_at).isoformat() if (output.updated_at or output.created_at) else None,
        "completed_at": output.completed_at.isoformat() if output.completed_at else None,
        "stale": bool(status == "completed" and output.source_revision and current_revision and output.source_revision != current_revision),
        # A short video made before narration existed has no voice track.
        "narrated": output.target == "short_video" and (output.provider or "").endswith("+voice"),
        "legacy": output.pipeline != "v2",
        "user_edited": bool(output.user_edited),
        "content": None,
        "title": None,
        "preview_text": None,
        "html": None,
        "video_url": None,
        "download_url": None,
        "duration_s": None,
        "warning": None,
    }
    if status != "completed":
        return view
    if output.target == "short_video" and output.media_id and output.media_url:
        # A ticket for the author alone: the video is never easier to reach than the post.
        signed = mediasign.sign_url(output.media_url, output.media_id, viewer)
        view.update(video_url=signed, download_url=f"{signed}&dl=1", duration_s=output.duration_s)
    elif output.target == "newsletter":
        view.update(content=output.content, title=output.title, preview_text=output.preview_text, html=output.content_html)
        if not public:
            view["warning"] = "This post is not public: people you send this draft to may not be able to open its link."
    else:
        view["content"] = output.content
    return view


def job_view(db: OrmSession, job: models.PublishingJob, viewer: str) -> dict:
    source = None
    reachable = True
    try:
        source = fetch_source(job.source_ref) if job.source_ref else None
    except Retryable:
        reachable = False
    deleted = reachable and job.source_ref is not None and source_deleted(source)
    if deleted and db.scalar(
        select(func.count()).select_from(models.PublishingOutput).where(
            models.PublishingOutput.job_id == job.id,
            or_(
                models.PublishingOutput.status.in_(ACTIVE),
                (models.PublishingOutput.target == "short_video") & models.PublishingOutput.media_id.isnot(None),
            ),
        )
    ):
        withdraw(db, job, "The post was deleted, so this was withdrawn.")
        db.commit()
    revision = revision_of(source) if source and not deleted else None
    public = bool(source and source.get("visibility") == "public")
    outputs = db.scalars(
        select(models.PublishingOutput).where(models.PublishingOutput.job_id == job.id).order_by(models.PublishingOutput.id)
    ).all()
    views = [output_view(o, viewer, revision, public) for o in outputs]
    if deleted:
        # Nothing made from a deleted post is shown, only that it was withdrawn.
        for v in views:
            v.update(content=None, html=None, title=None, preview_text=None, video_url=None, download_url=None)
    return {
        "job_id": job.id,
        "post_id": job.source_ref,
        # From the outputs below, not from the job row read before them: the
        # worker may have finished one in between.
        "status": status_of([o.status for o in outputs]),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "source": {
            "deleted": deleted,
            "reachable": reachable,
            "visibility": source.get("visibility") if source else None,
            "lang": source.get("lang") if source else job.source_lang,
        },
        "outputs": views,
    }
