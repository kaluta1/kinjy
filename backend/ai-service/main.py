"""Kinjy · ai-service — the model gateway, translation, and the Kinjy Assistant."""
from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from common import settings
from common.auth import AdminUser, MaybeUser
from common.database import get_db
from common.ids import new_id
from common.service import create_app

import gateway
import models

app = create_app(
    name="ai-service",
    schema=models.SCHEMA,
    description="Provider-independent AI gateway, translation, the Kinjy Assistant.",
)


class RunIn(BaseModel):
    task: str
    prompt: str = Field(min_length=1, max_length=50000)
    lang: str | None = None
    target_lang: str | None = None
    sensitive: bool = False
    quality_floor: float = 0.0


class TranslateIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    source_lang: str | None = None
    target_lang: str
    # Refuse rather than return placeholder text when no real model is set up.
    require_live: bool = False


class AssistIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    module: str | None = None
    lang: str | None = None


def _record(db: OrmSession, result: dict, user_id: str | None, sensitive: bool) -> None:
    db.add(
        models.AiCall(
            id=new_id("aic"),
            task=result["task"],
            provider=result["provider"],
            model=result["model"],
            lang=result.get("lang"),
            user_id=user_id,
            latency_ms=result["latency_ms"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cost_usd=result["cost_usd"],
            sensitive=sensitive,
        )
    )


@app.get("/ai/providers", tags=["gateway"])
def providers():
    return {
        "default": settings.AI_DEFAULT_PROVIDER,
        "tasks": list(gateway.TASKS),
        "providers": gateway.describe(),
        "routing": [
            "Sensitive data is only routed to local providers.",
            "A provider that does not declare the language is skipped.",
            "Among the rest, cheapest that meets the quality floor wins.",
        ],
    }


@app.get("/internal/ai/capabilities", tags=["internal"])
def capabilities():
    """Which tasks a real model performs here - for services that must not
    accept placeholder output."""
    provider = gateway.live_provider("translate")
    return {
        "translate": {
            "live": provider is not None,
            "provider": provider.name if provider else None,
            "model": provider.models.get("translate") if provider else None,
        }
    }


@app.post("/ai/run", tags=["gateway"])
def run_task(payload: RunIn, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    try:
        result = gateway.run(
            task=payload.task,
            prompt=payload.prompt,
            lang=payload.lang,
            target_lang=payload.target_lang,
            sensitive=payload.sensitive,
            quality_floor=payload.quality_floor,
        )
    except gateway.NoProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    _record(db, result, principal.user_id if principal else None, payload.sensitive)
    db.commit()
    return result


@app.post("/ai/translate", tags=["translation"])
def translate(payload: TranslateIn, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """One-click translation with a cache. Auto-detects the source when omitted."""
    if payload.target_lang not in settings.SUPPORTED_LANGS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language. Available: {', '.join(settings.SUPPORTED_LANGS)}"
        )

    source_lang = payload.source_lang or _detect(payload.text)
    if source_lang == payload.target_lang:
        return {"translated": payload.text, "source_lang": source_lang, "cached": False, "noop": True}

    if payload.require_live and gateway.live_provider("translate", source_lang) is None:
        raise HTTPException(status_code=503, detail="No translation model is configured")

    digest = hashlib.sha256(f"{source_lang}:{payload.target_lang}:{payload.text}".encode()).hexdigest()
    # A placeholder cached before a model was configured is not a translation.
    cached = db.scalar(
        select(models.Translation).where(
            models.Translation.source_hash == digest, models.Translation.provider != "mock"
        )
    )
    if cached:
        return {
            "translated": cached.translated_text,
            "source_lang": cached.source_lang,
            "provider": cached.provider,
            "mock": False,
            "cached": True,
        }

    try:
        result = gateway.run(
            task="translate", prompt=payload.text, lang=source_lang, target_lang=payload.target_lang
        )
    except gateway.NoProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except gateway.ProviderError as exc:
        raise HTTPException(status_code=502 if exc.retryable else 422, detail=f"Translation failed: {exc}")
    if payload.require_live and result["mock"]:
        raise HTTPException(status_code=503, detail="No translation model is configured")

    if not result["mock"]:
        # The cache holds translations only; a placeholder would outlive the
        # day a real model is configured.
        existing = db.scalar(select(models.Translation).where(models.Translation.source_hash == digest))
        if existing is not None:
            existing.translated_text = result["output"]
            existing.provider = result["provider"]
        else:
            db.add(
                models.Translation(
                    source_hash=digest,
                    source_lang=source_lang,
                    target_lang=payload.target_lang,
                    source_text=payload.text,
                    translated_text=result["output"],
                    provider=result["provider"],
                )
            )
    _record(db, result, principal.user_id if principal else None, False)
    db.commit()
    return {
        "translated": result["output"],
        "source_lang": source_lang,
        "provider": result["provider"],
        "mock": result["mock"],
        "cached": False,
    }


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so "cimetiere" matches "cimetière"."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# Function words are the cheapest reliable signal for the Latin-script
# languages Kinjy supports; they appear in almost any real sentence and rarely
# cross over between these three.
_STOPWORDS: dict[str, set[str]] = {
    "fr": {
        "comment", "pourquoi", "quel", "quelle", "est", "sont", "les", "des", "une",
        "dans", "pour", "avec", "mon", "ma", "mes", "je", "vous", "ça", "cela",
        "puis", "peux", "fonctionne", "fait", "que", "qui", "sur", "aux", "cette",
    },
    "sw": {
        "je", "jinsi", "gani", "nini", "kwa", "nini", "yangu", "wangu", "ninaweza",
        "naweza", "kufanya", "ya", "wa", "na", "katika", "hii", "hiyo", "kama",
        "ninawezaje", "zinafanyaje", "unafanyaje", "mimi", "sisi",
    },
    "en": {
        "how", "why", "what", "the", "does", "do", "is", "are", "my", "your",
        "can", "work", "works", "with", "for", "about", "and", "this", "that",
    },
}


def _detect(text: str, fallback: str | None = None) -> str:
    """Detect the language a question was written in.

    Script ranges settle Arabic and Chinese outright. For the Latin-script
    languages a function-word vote is used: guessing from the alphabet alone
    would answer a French question in English, which breaks the blueprint's
    promise to reply in the language the member used. When nothing scores, the
    caller's hint wins over a blind default.
    """
    for char in text:
        code = ord(char)
        if 0x0600 <= code <= 0x06FF:
            return "ar"
        if 0x4E00 <= code <= 0x9FFF:
            return "zh"

    words = {w.strip(".,?!¿¡:;()").lower() for w in text.split()}
    scores = {lang: len(words & stops) for lang, stops in _STOPWORDS.items()}
    best = max(scores, key=lambda lang: scores[lang])

    if scores[best] == 0:
        return fallback if fallback in settings.SUPPORTED_LANGS else settings.DEFAULT_LANG
    # A tie between English and another language goes to the other one: English
    # stopwords ("do", "is") also appear inside borrowed phrasing.
    if scores[best] == scores.get("en", 0) and best != "en":
        return best
    return best


# --- the Kinjy Assistant --------------------------------------------------

@app.post("/assistant/ask", tags=["assistant"])
def ask(payload: AssistIn, principal: MaybeUser, db: OrmSession = Depends(get_db)):
    """Role-aware, language-matching, grounded in the KB.

    The reply language follows the *question*, not the UI setting, which is what
    the blueprint asks for. Answers come from KB entries the caller's role is
    allowed to see; when nothing matches, the assistant says so instead of
    inventing an answer.
    """
    role = principal.role if principal else "visitor"
    # Detection wins over the client hint: the reply must follow the language of
    # the question, not the language the interface happens to be set to.
    lang = _detect(payload.question, fallback=payload.lang)

    session_id = payload.session_id
    if not session_id:
        session = models.AssistantSession(
            id=new_id("ast"),
            user_id=principal.user_id if principal else None,
            role=role,
            lang=lang,
            module=payload.module,
        )
        db.add(session)
        db.flush()
        session_id = session.id

    db.add(models.AssistantMessage(session_id=session_id, role="user", body=payload.question, lang=lang))

    # Accent-folded on both sides: people type "cimetiere" and "genealogie"
    # without the diacritics, and a keyword match that insists on them simply
    # fails for most real French and Swahili questions.
    question = _fold(payload.question)
    words = {w.strip(".,?!¿¡") for w in question.split() if len(w) > 2}

    entries = db.scalars(
        select(models.KbEntry).where(models.KbEntry.roles.like(f"%{role}%"), models.KbEntry.lang == lang)
    ).all()
    if not entries:
        entries = db.scalars(select(models.KbEntry).where(models.KbEntry.roles.like(f"%{role}%"))).all()

    best, best_score = None, 0
    for entry in entries:
        keywords = [_fold(k) for k in entry.keywords.split(",") if k.strip()]
        # Multi-word keywords ("why am i seeing") never survive a word-set
        # intersection, and languages without spaces (zh) tokenise to nothing —
        # so match phrases against the raw question as well.
        score = 0
        for keyword in keywords:
            if " " in keyword or not keyword.isascii():
                if keyword in question:
                    score += 2
            elif keyword in words:
                score += 1
        if _fold(entry.title) in question:
            score += 3
        if score > best_score:
            best, best_score = entry, score

    # Fallback: score against the localized answer text.
    #
    # The keyword lists were authored mostly in English with a few translations,
    # so a perfectly reasonable French question ("Comment mon paiement est-il
    # calculé ?") matches nothing even though the French answer is right there
    # and contains the word. Rather than hand-maintain 245 keyword lists, fall
    # back to the prose. Scored lower than a keyword hit so curated keywords
    # still win, and it needs several content words to avoid matching on noise.
    if best is None or best_score == 0:
        content_words = {w for w in words if len(w) > 3}
        for entry in entries:
            haystack = _fold(entry.answer + " " + entry.title)
            hits = sum(1 for w in content_words if w in haystack)
            if hits >= 2 and hits > best_score:
                best, best_score = entry, hits

    if best is None or best_score == 0:
        answer = {
            "en": "I don't have a grounded answer for that yet. Ask about feeds, circles, family tree, memorials, earnings, ads or privacy.",
            "fr": "Je n'ai pas encore de réponse fondée à ce sujet.",
            "sw": "Sina jibu lenye msingi kwa hilo bado.",
            "ar": "لا أملك إجابة موثقة عن ذلك بعد.",
            "zh": "我还没有关于此问题的可靠答案。",
        }.get(lang, "I don't have a grounded answer for that yet.")
        db.add(models.AssistantMessage(session_id=session_id, role="assistant", body=answer, lang=lang, grounded=False))
        db.commit()
        return {
            "session_id": session_id,
            "lang": lang,
            "role": role,
            "grounded": False,
            "answer": answer,
            "source": None,
        }

    db.add(
        models.AssistantMessage(
            session_id=session_id, role="assistant", body=best.answer, lang=lang,
            kb_entry_id=best.id, grounded=True,
        )
    )
    db.commit()
    return {
        "session_id": session_id,
        "lang": lang,
        "role": role,
        "grounded": True,
        "answer": best.answer,
        "title": best.title,
        "module": best.module,
        "source": best.source,
        "steps": [s for s in (best.steps or "").split("\n") if s.strip()],
        "image": best.image_url,
        "image_alt": best.image_alt,
        "deep_link": best.deep_link,
        "deep_link_label": best.deep_link_label,
        "kb_version": best.version,
        "formats": ["written", "video_clip"],
        "match_score": best_score,
    }


class KbIn(BaseModel):
    id: str
    module: str
    title: str
    answer: str
    keywords: list[str]
    lang: str = "en"
    roles: list[str] = Field(default_factory=lambda: ["visitor", "member", "admin"])
    admin_only: bool = False
    steps: list[str] = Field(default_factory=list)
    image_url: str | None = None
    image_alt: str | None = None
    source: str | None = None
    deep_link: str | None = None
    deep_link_label: str | None = None
    version: str = "v1"


@app.post("/admin/assistant/kb", status_code=201, tags=["assistant"])
def upsert_kb(payload: KbIn, _: AdminUser, db: OrmSession = Depends(get_db)):
    """Self-updating knowledge: the changelog ingestion pipeline writes here."""
    entry = db.get(models.KbEntry, (payload.id, payload.lang))
    data = payload.model_dump()
    data["keywords"] = ",".join(payload.keywords)
    data["roles"] = ",".join(payload.roles)
    data["steps"] = "\n".join(payload.steps) or None
    if entry is None:
        db.add(models.KbEntry(**data))
    else:
        for key, value in data.items():
            setattr(entry, key, value)
    db.commit()
    return {"id": payload.id, "lang": payload.lang, "stored": True}


@app.get("/assistant/kb/stats", tags=["assistant"])
def kb_stats(db: OrmSession = Depends(get_db)):
    """What the assistant actually knows — so an empty KB is never mistaken for
    a broken assistant."""
    rows = db.execute(
        select(models.KbEntry.lang, func.count()).group_by(models.KbEntry.lang)
    ).all()
    total = db.scalar(select(func.count()).select_from(models.KbEntry)) or 0
    modules = db.scalar(select(func.count(func.distinct(models.KbEntry.module)))) or 0
    return {
        "entries": total,
        "distinct_topics": db.scalar(select(func.count(func.distinct(models.KbEntry.id)))) or 0,
        "modules": modules,
        "by_language": {lang: count for lang, count in rows},
    }


# --- observability ---------------------------------------------------------

@app.get("/admin/ai/observability", tags=["observability"])
def observability(_: AdminUser, hours: int = 24, db: OrmSession = Depends(get_db)):
    """Per-provider cost, latency and error rate over a window."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(
            models.AiCall.provider,
            models.AiCall.task,
            func.count(),
            func.avg(models.AiCall.latency_ms),
            func.sum(models.AiCall.cost_usd),
            func.sum(func.cast(models.AiCall.succeeded, __import__("sqlalchemy").Integer)),
        )
        .where(models.AiCall.created_at >= since)
        .group_by(models.AiCall.provider, models.AiCall.task)
    ).all()
    return {
        "window_hours": hours,
        "rows": [
            {
                "provider": provider,
                "task": task,
                "calls": calls,
                "avg_latency_ms": round(float(latency or 0), 1),
                "cost_usd": str(cost or 0),
                "success_rate": round((ok or 0) / calls, 3) if calls else 0,
            }
            for provider, task, calls, latency, cost, ok in rows
        ],
    }
