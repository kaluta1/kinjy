"""Kinjy · creator-service — creator studio, publishing engine, subscriptions."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from common import agefeatures, events, settings
from common.article_html import sanitize_article
from common.agesafety import engine as age_engine
from common.auth import CurrentUser
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import models
import onetomany

AI_URL = "http://ai-service:8000"
LEDGER_URL = "http://ledger-service:8000"

_S = models.SCHEMA
MIGRATIONS = [
    # One-to-Many as a durable queue (25/10). Additive: older rows keep their
    # values, and `pipeline` stays NULL on them so the worker leaves them alone.
    f"ALTER TABLE {_S}.publishing_jobs ADD COLUMN IF NOT EXISTS idem_key VARCHAR(120)",
    f"ALTER TABLE {_S}.publishing_jobs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_publishing_jobs_idem ON {_S}.publishing_jobs (idem_key) WHERE idem_key IS NOT NULL",
    f"CREATE INDEX IF NOT EXISTS ix_publishing_jobs_source ON {_S}.publishing_jobs (creator_id, source_ref)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS pipeline VARCHAR(10)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 3",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS source_revision VARCHAR(40)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS provider VARCHAR(40)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS title VARCHAR(300)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS preview_text VARCHAR(300)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS content_html TEXT",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS user_edited BOOLEAN NOT NULL DEFAULT FALSE",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS media_id VARCHAR(40)",
    f"ALTER TABLE {_S}.publishing_outputs ADD COLUMN IF NOT EXISTS duration_s DOUBLE PRECISION",
    f"CREATE UNIQUE INDEX IF NOT EXISTS uq_publishing_outputs_slot ON {_S}.publishing_outputs (job_id, target, lang)",
    f"CREATE INDEX IF NOT EXISTS ix_publishing_outputs_due ON {_S}.publishing_outputs (status, next_attempt_at) WHERE pipeline = 'v2'",
]

app = create_app(
    name="creator-service",
    schema=models.SCHEMA,
    description="Creator profiles, one-to-many publishing, series, subscriptions, badges.",
    migrations=MIGRATIONS,
    on_startup=[onetomany.start_worker],
)


class PublishIn(BaseModel):
    # The post to produce from. Its text, language and pictures are read from
    # social-service; the fields below are accepted from older clients and ignored.
    source_ref: str = Field(min_length=1, max_length=120)
    targets: list[str] = Field(min_length=1, max_length=5)
    source_kind: str | None = None
    source_text: str | None = None
    source_lang: str | None = None
    target_langs: list[str] = Field(default_factory=list)


class NewsletterEditIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    preview_text: str = Field(default="", max_length=300)
    html: str = Field(min_length=1, max_length=100_000)


class SubscribeIn(BaseModel):
    target_id: str = "platform"
    tier: str = Field(pattern="^(free|basic|premium|creator)$")
    period: str = Field(default="monthly", pattern="^(monthly|yearly)$")


class SeriesIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    description: str = ""
    cover_url: str | None = None
    cadence: str | None = None


@app.get("/creators/plans", tags=["subscriptions"])
def plans():
    """Blueprint §12 tiers. The free tier is deliberately useful, not a trap."""
    return {
        "plans": [
            {
                "tier": "free",
                "price": models.PLANS["free"],
                "features": ["All core modules", "Standard video", "Basic translation", "Family tree", "Memorials"],
            },
            {
                "tier": "basic",
                "price": models.PLANS["basic"],
                "features": ["HD video", "Advanced translation", "Scheduled posts", "More AI credits", "Newsletters"],
            },
            {
                "tier": "premium",
                "price": models.PLANS["premium"],
                "features": [
                    "4K video", "AI Creator Studio", "AI dubbing + lip-sync", "AI clips",
                    "Brand kit", "Custom algorithm feeds", "API allowance", "Premium themes",
                ],
            },
        ],
        "one_off_multiplier": "2-4x the implied subscription unit cost",
    }


# The features whose availability depends on age. Listed once so the readout
# below and the gates that enforce them cannot drift apart.
AGE_GATED_FEATURES = [
    "livestream",
    "livestream_gifting",
    "monetization",
    "marketplace_sell",
    "marketplace_buy",
    "payments",
    "referral_pool",
]


@app.get("/creators/eligibility", tags=["creators"])
def feature_eligibility(principal: CurrentUser):
    """What this account may and may not do, before it tries.

    So a client can grey a button out and say why, rather than letting somebody
    fill in a payment form and refusing at the end. It is a readout: the gates
    on the endpoints themselves are what actually enforce anything, and they do
    not consult this.
    """
    return agefeatures.eligibility(principal.user_id, AGE_GATED_FEATURES)


@app.get("/live/eligibility", tags=["livestream"])
def livestream_eligibility(principal: CurrentUser):
    """Whether this account may start a livestream, and under what rules.

    There is no streaming backend yet - no ingest, no session, no key. What
    exists is the decision, exposed at the address a streaming stack would have
    to ask. Building the gate first is deliberate: the alternative is shipping
    the stack and remembering the age rule afterwards, which is how a
    13-year-old ends up live to strangers.
    """
    who = agefeatures.profile(principal.user_id)
    verdict = age_engine.can_start_livestream(who)
    policy = age_engine.policy_for(who)
    return {
        "allowed": verdict.allowed,
        "age_tier": who.tier.value,
        "policy": {
            "jurisdiction": policy.jurisdiction,
            "minimum_age": age_engine.FEATURE_MINIMUM_AGES["livestream"],
            "parental_approval_required": policy.parental_consent_required,
            "gifting_allowed": bool(age_engine.can_use_feature(who, "livestream_gifting")),
            "monetization_allowed": bool(age_engine.can_monetize(who)),
            "policy_version": policy.policy_version,
        },
        "reason": None if verdict.allowed else agefeatures.REFUSAL,
        "note": "Streaming infrastructure is not built. This is the age decision it must ask.",
    }


@app.post("/creators/enable", tags=["creators"])
def enable_creator(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    # Becoming a creator is opting into being paid.
    agefeatures.require(principal.user_id, "monetization")

    profile = db.get(models.CreatorProfile, principal.user_id)
    if profile is None:
        profile = models.CreatorProfile(user_id=principal.user_id)
        db.add(profile)
    profile.monetization_enabled = True
    db.commit()
    return {"user_id": principal.user_id, "monetization_enabled": True}


# --- publishing engine -----------------------------------------------------
#
# One-to-Many: the composer asks, once the post exists, for a short video, a
# newsletter draft and/or the post in the four other languages. The request is
# recorded as queued rows; onetomany.py's worker produces them. Only the
# author of the post can ask, and only they can see what comes out.

def _own_source(post_id: str, principal_id: str) -> dict:
    try:
        source = onetomany.fetch_source(post_id)
    except onetomany.Retryable:
        raise HTTPException(status_code=503, detail="The post cannot be read right now. Try again in a moment.")
    if onetomany.source_deleted(source) or source["author_id"] != principal_id:
        raise HTTPException(status_code=404, detail="Post not found")
    return source


def _own_job(db: OrmSession, job_id: str, principal_id: str) -> models.PublishingJob:
    job = db.get(models.PublishingJob, job_id)
    if job is None or job.creator_id != principal_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _own_output(db: OrmSession, output_id: int, principal_id: str) -> tuple[models.PublishingOutput, models.PublishingJob]:
    output = db.get(models.PublishingOutput, output_id)
    job = db.get(models.PublishingJob, output.job_id) if output else None
    if output is None or job is None or job.creator_id != principal_id:
        raise HTTPException(status_code=404, detail="Output not found")
    return output, job


@app.get("/creators/publish/capabilities", tags=["publishing"])
def publish_capabilities(principal: CurrentUser, lang: str | None = None):
    """What One-to-Many can really produce right now, for a post in ``lang``.

    The composer greys out what is not available and says why, instead of
    accepting a request nothing will fulfil.
    """
    return {"targets": onetomany.capabilities(lang if lang in settings.SUPPORTED_LANGS else None)}


@app.post("/creators/publish", status_code=202, tags=["publishing"])
async def publish(payload: PublishIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Queue One-to-Many outputs for a post the caller wrote.

    Idempotent per post: asking again returns the same job, with any target not
    asked for before added to it. A target that cannot be produced on this
    server is refused, and nothing is queued.
    """
    targets = list(dict.fromkeys(payload.targets))
    unknown = [t for t in targets if t not in onetomany.TARGETS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Not available: {', '.join(unknown)}. One-to-Many produces {', '.join(onetomany.TARGETS)}.",
        )
    source = _own_source(payload.source_ref, principal.user_id)
    lang = source.get("lang") if source.get("lang") in settings.SUPPORTED_LANGS else "en"
    caps = onetomany.capabilities(lang)
    refused = {t: caps[t]["reason"] for t in targets if not caps[t]["available"]}
    if refused:
        raise HTTPException(
            status_code=409,
            detail="Not available right now: " + " ".join(f"{t.replace('_', ' ')}: {r}" for t, r in refused.items()),
        )

    key = f"{principal.user_id}:{payload.source_ref}"
    job = db.scalar(select(models.PublishingJob).where(models.PublishingJob.idem_key == key).with_for_update())
    created = job is None
    if job is None:
        job = models.PublishingJob(
            id=new_id("pub"),
            creator_id=principal.user_id,
            source_kind="post",
            source_ref=payload.source_ref,
            source_lang=lang,
            targets=",".join(targets),
            target_langs=",".join(sorted({l for t, l in onetomany.planned_outputs(targets, lang)})),
            status="queued",
            idem_key=key,
        )
        db.add(job)
        try:
            db.flush()
        except IntegrityError:
            # The same request, a moment earlier, from another tab or a retry.
            db.rollback()
            job = db.scalar(select(models.PublishingJob).where(models.PublishingJob.idem_key == key).with_for_update())
            created = False
    else:
        job.targets = ",".join(dict.fromkeys([*(job.targets or "").split(","), *targets]).keys()).strip(",")

    existing = {(o.target, o.lang) for o in db.scalars(select(models.PublishingOutput).where(models.PublishingOutput.job_id == job.id))}
    added = 0
    for target, out_lang in onetomany.planned_outputs(targets, lang):
        if (target, out_lang) in existing:
            continue
        db.add(
            models.PublishingOutput(
                job_id=job.id,
                target=target,
                lang=out_lang,
                status="queued",
                pipeline="v2",
                attempts=0,
                max_attempts=onetomany.MAX_ATTEMPTS,
                provenance="ai_generated" if target == "translation" else "ai_assisted",
                updated_at=onetomany.now(),
            )
        )
        added += 1
    db.flush()
    onetomany.refresh_job(db, job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        job = db.scalar(select(models.PublishingJob).where(models.PublishingJob.idem_key == key))
        added = 0

    await events.publish(
        "creator.publish_requested",
        {"job_id": job.id, "creator_id": principal.user_id, "targets": targets, "post_id": payload.source_ref},
    )
    return {**onetomany.job_view(db, job, principal.user_id), "created": created, "added": added}


@app.get("/creators/publish/by-post/{post_id}", tags=["publishing"])
def publish_for_post(post_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The One-to-Many job of one of the caller's posts, or ``{"job": null}``."""
    job = db.scalar(
        select(models.PublishingJob)
        .where(models.PublishingJob.creator_id == principal.user_id, models.PublishingJob.source_ref == post_id)
        .order_by(models.PublishingJob.idem_key.is_(None), models.PublishingJob.created_at.desc())
        .limit(1)
    )
    return {"job": onetomany.job_view(db, job, principal.user_id) if job else None}


@app.get("/creators/publish/{job_id}", tags=["publishing"])
def publish_status(job_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    return onetomany.job_view(db, _own_job(db, job_id, principal.user_id), principal.user_id)


def _requeue(output: models.PublishingOutput) -> None:
    output.status = "queued"
    output.pipeline = "v2"
    output.attempts = 0
    output.max_attempts = onetomany.MAX_ATTEMPTS
    output.error = None
    output.lease_until = None
    output.next_attempt_at = None
    output.updated_at = onetomany.now()


@app.post("/creators/publish/outputs/{output_id}/retry", tags=["publishing"])
def retry_output(output_id: int, principal: CurrentUser, force: bool = False, db: OrmSession = Depends(get_db)):
    """Produce one output again: a failed one, or a completed one whose post has
    changed since. A newsletter draft the author edited is only replaced when
    they say so (``force``)."""
    output, job = _own_output(db, output_id, principal.user_id)
    _own_source(job.source_ref or "", principal.user_id)
    status = onetomany.LEGACY_STATUS.get(output.status, output.status)
    if status in onetomany.ACTIVE:
        raise HTTPException(status_code=409, detail="This is already being produced.")
    if output.target not in onetomany.TARGETS:
        raise HTTPException(status_code=409, detail="Nothing produces this format.")
    if output.target == "translation" and output.lang == job.source_lang:
        raise HTTPException(status_code=409, detail="This is the post's own language.")
    if output.user_edited and not force:
        raise HTTPException(status_code=409, detail="You edited this draft. Regenerating replaces your edits.")
    caps = onetomany.capabilities(job.source_lang)
    if not caps[output.target]["available"]:
        raise HTTPException(status_code=409, detail=caps[output.target]["reason"])
    _requeue(output)
    output.user_edited = False if force else output.user_edited
    onetomany.refresh_job(db, job)
    db.commit()
    return onetomany.job_view(db, job, principal.user_id)


@app.post("/creators/publish/{job_id}/retry-failed", tags=["publishing"])
def retry_failed(job_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Try again every output of this job that failed - and only those."""
    job = _own_job(db, job_id, principal.user_id)
    _own_source(job.source_ref or "", principal.user_id)
    caps = onetomany.capabilities(job.source_lang)
    requeued = 0
    for output in db.scalars(select(models.PublishingOutput).where(models.PublishingOutput.job_id == job.id)):
        if onetomany.LEGACY_STATUS.get(output.status, output.status) != "failed":
            continue
        if output.target not in onetomany.TARGETS or not caps[output.target]["available"]:
            continue
        _requeue(output)
        requeued += 1
    onetomany.refresh_job(db, job)
    db.commit()
    return {**onetomany.job_view(db, job, principal.user_id), "requeued": requeued}


@app.post("/creators/publish/outputs/{output_id}/cancel", tags=["publishing"])
def cancel_output(output_id: int, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Stop an output that has not been produced yet."""
    output, job = _own_output(db, output_id, principal.user_id)
    if output.status != "queued":
        raise HTTPException(status_code=409, detail="Only an output still waiting can be cancelled.")
    output.status = "cancelled"
    output.error = "Cancelled by you."
    output.updated_at = onetomany.now()
    onetomany.refresh_job(db, job)
    db.commit()
    return onetomany.job_view(db, job, principal.user_id)


@app.patch("/creators/publish/outputs/{output_id}", tags=["publishing"])
def edit_newsletter(output_id: int, payload: NewsletterEditIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Edit a newsletter draft. Saved, sanitized, marked as yours - and not sent:
    Kinjy does not e-mail newsletters on anyone's behalf."""
    output, job = _own_output(db, output_id, principal.user_id)
    _own_source(job.source_ref or "", principal.user_id)
    if output.target != "newsletter" or output.status != "completed":
        raise HTTPException(status_code=409, detail="Only a finished newsletter draft can be edited.")
    safe = sanitize_article(payload.html)
    text = onetomany.plain_text({"format": "article", "body": safe})
    if not text:
        raise HTTPException(status_code=422, detail="The newsletter needs some text.")
    output.title = payload.subject.strip()[:300]
    output.preview_text = payload.preview_text.strip()[:300]
    output.content_html = safe
    output.content = f"{output.title}\n\n{text}"
    output.user_edited = True
    output.updated_at = onetomany.now()
    db.commit()
    return onetomany.job_view(db, job, principal.user_id)


@app.post("/internal/publish/backfill", tags=["internal"])
def backfill_publishing(limit: int = 20, dry_run: bool = True, db: OrmSession = Depends(get_db)):
    """Bring jobs recorded before the durable pipeline into it - by hand, never on startup.

    Those jobs produced placeholder translations and marked short videos
    "unsupported". For each one whose post still exists, its short video,
    newsletter and translation rows are queued again (a translation into the
    post's own language is cancelled), and the job gets its idempotency key -
    unless the post already has a newer job, in which case it is left as history.
    ``dry_run`` (the default) only reports what would change.
    """
    legacy = db.scalars(
        select(models.PublishingJob)
        .where(models.PublishingJob.idem_key.is_(None), models.PublishingJob.source_ref.isnot(None))
        .order_by(models.PublishingJob.created_at)
        .limit(max(1, min(limit, 200)))
    ).all()
    report = []
    for job in legacy:
        key = f"{job.creator_id}:{job.source_ref}"
        entry = {"job_id": job.id, "post_id": job.source_ref, "action": None, "outputs": 0}
        if db.scalar(select(models.PublishingJob.id).where(models.PublishingJob.idem_key == key)):
            entry["action"] = "skipped: the post has a newer job"
            report.append(entry)
            continue
        try:
            source = onetomany.fetch_source(job.source_ref)
        except onetomany.Retryable:
            entry["action"] = "skipped: social-service unreachable"
            report.append(entry)
            continue
        outputs = db.scalars(select(models.PublishingOutput).where(models.PublishingOutput.job_id == job.id)).all()
        if onetomany.source_deleted(source) or source["author_id"] != job.creator_id:
            entry["action"] = "withdrawn: the post is gone"
            if not dry_run:
                for o in outputs:
                    o.pipeline = "v2"
                    o.status = "cancelled"
                    o.error = "The post was deleted, so this was withdrawn."
                job.idem_key = key
                onetomany.refresh_job(db, job)
        else:
            lang = source.get("lang") or job.source_lang
            entry["action"] = "requeued"
            for o in outputs:
                if o.target not in onetomany.TARGETS:
                    continue
                entry["outputs"] += 1
                if dry_run:
                    continue
                if o.target == "translation" and o.lang == lang:
                    o.pipeline = "v2"
                    o.status = "cancelled"
                    o.error = "The post is already in this language."
                else:
                    _requeue(o)
            if not dry_run:
                job.idem_key = key
                job.source_lang = lang
                onetomany.refresh_job(db, job)
        report.append(entry)
    if not dry_run:
        db.commit()
    return {"dry_run": dry_run, "jobs": report}


# --- series ----------------------------------------------------------------

@app.post("/creators/series", status_code=201, tags=["series"])
def create_series(payload: SeriesIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    series = models.Series(id=new_id("srs"), creator_id=principal.user_id, **payload.model_dump())
    db.add(series)
    db.commit()
    return {"id": series.id, "title": series.title}


@app.get("/creators/{creator_id}/series", tags=["series"])
def list_series(creator_id: str, db: OrmSession = Depends(get_db)):
    rows = db.scalars(select(models.Series).where(models.Series.creator_id == creator_id)).all()
    return {
        "items": [
            {"id": r.id, "title": r.title, "episodes_count": r.episodes_count, "cadence": r.cadence}
            for r in rows
        ]
    }


# --- subscriptions ---------------------------------------------------------

@app.post("/subscriptions", status_code=201, tags=["subscriptions"])
def subscribe(payload: SubscribeIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    price = Decimal(models.PLANS.get(payload.tier, {}).get(payload.period, "0.00"))
    if payload.target_id != "platform":
        profile = db.get(models.CreatorProfile, payload.target_id)
        if profile is None or not profile.monetization_enabled:
            raise HTTPException(status_code=404, detail="This creator does not offer subscriptions")
        price = profile.subscription_price_usd or Decimal("0")

    existing = db.scalar(
        select(models.Subscription).where(
            models.Subscription.subscriber_id == principal.user_id,
            models.Subscription.target_id == payload.target_id,
        )
    )
    renews = date.today() + timedelta(days=365 if payload.period == "yearly" else 30)
    if existing:
        existing.tier = payload.tier
        existing.period = payload.period
        existing.price_usd = price
        existing.status = "active"
        existing.renews_on = renews
        existing.cancelled_at = None
        subscription = existing
    else:
        subscription = models.Subscription(
            id=new_id("sub"),
            subscriber_id=principal.user_id,
            target_id=payload.target_id,
            tier=payload.tier,
            period=payload.period,
            price_usd=price,
            renews_on=renews,
        )
        db.add(subscription)
    db.commit()

    return {
        "id": subscription.id,
        "tier": subscription.tier,
        "price_usd": str(price),
        "renews_on": renews,
        "note": "Billing is handled by payment-service; this records the entitlement.",
    }


@app.delete("/subscriptions/{subscription_id}", tags=["subscriptions"])
def cancel(subscription_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    subscription = db.get(models.Subscription, subscription_id)
    if subscription is None or subscription.subscriber_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")
    subscription.status = "cancelled"
    subscription.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    # Access runs to the end of the paid period rather than stopping instantly.
    return {"cancelled": True, "access_until": subscription.renews_on}


@app.get("/subscriptions/me", tags=["subscriptions"])
def my_subscriptions(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.Subscription).where(models.Subscription.subscriber_id == principal.user_id)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "target_id": r.target_id,
                "tier": r.tier,
                "period": r.period,
                "price_usd": str(r.price_usd),
                "status": r.status,
                "renews_on": r.renews_on,
            }
            for r in rows
        ]
    }


# --- earnings --------------------------------------------------------------

@app.get("/creators/earnings", tags=["earnings"])
def earnings(principal: CurrentUser):
    """Proxy the ledger so the creator dashboard has one place to call."""
    try:
        response = httpx.post(
            f"{LEDGER_URL}/ledger/simulate",
            json={"kind": "creator_revenue", "amount": "100", "creator_id": principal.user_id},
            timeout=5,
        )
        response.raise_for_status()
        model = response.json()
    except Exception:
        model = None
    return {
        "creator_id": principal.user_id,
        "share_pct": str(settings.CREATOR_SHARE_PCT),
        "example_per_100": model,
        "wallet_endpoint": "/api/ledger/wallet",
    }


@app.get("/badges/{user_id}", tags=["badges"])
def badges(user_id: str, db: OrmSession = Depends(get_db)):
    rows = db.scalars(select(models.Badge).where(models.Badge.user_id == user_id)).all()
    return {
        "available": models.BADGES,
        "earned": [{"badge": r.badge, "reason": r.reason, "awarded_at": r.awarded_at} for r in rows],
    }
