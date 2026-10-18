"""Kaluta KYC: the identity check behind KinjyKYC.

The member is sent to Kaluta's hosted flow; no document or selfie passes
through Kinjy, only the verdict comes back.

The session webhook is NOT signed - Kaluta sends session webhooks without a
secret. Granting "verified" on an unsigned POST would let anybody declare
themselves verified. So the webhook is a doorbell: Kinjy finds the session in
its own records (an invented id matches nobody) and reads the real state back
from Kaluta, and acts on that answer only.
"""
from __future__ import annotations

import logging

import httpx

from common import settings

log = logging.getLogger("payment-service.kaluta")

APPROVED = "approved"
REJECTED = "rejected"
# Expired is not a failed check: the member let the session lapse without
# really trying, so it gives the attempt back rather than counting a refusal.
EXPIRED = "expired"
IN_PROGRESS = (
    "created", "document_pending", "document_submitted",
    "face_pending", "face_submitted", "under_review",
)


class KalutaError(RuntimeError):
    pass


def enabled() -> bool:
    return bool((settings.KALUTA_KYC_API_KEY or "").strip())


def _base() -> str:
    return (settings.KALUTA_KYC_BASE_URL or "https://kalutakyc.com").rstrip("/")


def webhook_url() -> str:
    return f"{settings.GATEWAY_URL.rstrip('/')}/api/kyc/webhook/kaluta"


def start_session(
    *, external_id: str, redirect_url: str, first_name: str | None = None,
    last_name: str | None = None, date_of_birth: str | None = None, country: str | None = None,
) -> dict:
    """Open a session: {session_id, verification_url, expires_at}.

    The expected identity goes with it when Kinjy knows it; Kaluta compares it
    with the document and refuses a mismatch on its own.
    """
    if not enabled():
        raise KalutaError("Kaluta KYC is not configured")
    body = {"external_id": external_id, "redirect_url": redirect_url, "webhook_url": webhook_url()}
    for key, value in (("first_name", first_name), ("last_name", last_name),
                       ("date_of_birth", date_of_birth), ("country", country)):
        if value:
            body[key] = str(value)
    try:
        response = httpx.post(
            f"{_base()}/v1/sessions", json=body,
            headers={"X-API-Key": settings.KALUTA_KYC_API_KEY.strip()}, timeout=20,
        )
    except httpx.HTTPError as exc:
        raise KalutaError(f"Kaluta KYC did not answer: {exc}") from exc
    if response.status_code >= 400:
        log.error("kaluta session refused: %s %s", response.status_code, response.text[:300])
        raise KalutaError(f"Kaluta KYC refused the session (HTTP {response.status_code})")
    data = response.json()
    if not data.get("session_id") or not data.get("verification_url"):
        raise KalutaError("Kaluta KYC answered without a session")
    return data


def read_session(session_id: str) -> dict:
    """The session's real state, from Kaluta. This answer is the one that counts."""
    try:
        response = httpx.get(f"{_base()}/v1/sessions/{session_id}/public", timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise KalutaError(f"Could not read the Kaluta session: {exc}") from exc


def split_name(display_name: str | None) -> tuple[str | None, str | None]:
    parts = (display_name or "").split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])
