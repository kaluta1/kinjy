"""Ask user-service what one member may do to another.

There is exactly one implementation of "are they connected, and does their
privacy setting allow this?" — it lives in user-service. Every other service
asks. Three copies of that logic would drift, and the copy that drifts is the
one that leaks.

Every helper here **fails closed**: if user-service cannot be reached we refuse.
An outage must not become a way around someone's privacy setting.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("permissions")

USER_URL = "http://user-service:8000"


def get(actor: str, target: str) -> dict | None:
    """The full permission record, or ``None`` when it cannot be established."""
    if actor == target:
        return {
            "connected": True,
            "can_message": True,
            "can_add_family": True,
            "can_add_community": True,
            "can_add_group": True,
            "can_invite_group": True,
            "reason": "self",
        }
    try:
        response = httpx.get(f"{USER_URL}/internal/permissions/{actor}/{target}", timeout=4)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log.warning("permission lookup failed %s -> %s: %s", actor, target, exc)
        return None


def check(actor: str, target: str, capability: str, subject: str) -> tuple[bool, str]:
    """``(allowed, reason)`` for one capability.

    ``subject`` names the action in the refusal, so the member reads something
    they can act on instead of a bare 403.
    """
    data = get(actor, target)
    if data is None:
        return False, f"Could not verify permission to {subject}. Try again shortly."
    if data.get(capability):
        return True, ""
    if data.get("reason") == "blocked":
        return False, f"You cannot {subject}."
    if data.get("pending"):
        return False, f"Your connection invitation is still pending, so you cannot {subject} yet."
    return False, f"This member only allows accepted connections to {subject}. Send an invitation first."
