"""Sonco Pay: every payment Kinjy takes, and the commissions it pays out.

Through the official SDK (vendored, see backend/vendor). Two directions:

* **In.** A checkout creates a Sonco Pay payment whose ``order_id`` is the
  Kinjy payment id, so sending it again returns the same payment. The member
  pays on Sonco Pay's page and comes back. Nothing is granted on the member's
  word, nor on the webhook's: the payment is read back from Sonco Pay and must
  be *this* payment, in dollars, for the full amount - see ``conforms``.

* **Out.** A withdrawal is a Sonco Pay payout in USDT on BSC, from Kinjy's own
  Sonco Pay balance to the member's approved wallet. Its ``reference`` is the
  Kinjy withdrawal id, so a retry is the same payout, never a second one.

A live key (sp_live_) only answers from an IP address declared in the Sonco Pay
dashboard; from anywhere else every call is a 403 ``api_key_ip_not_allowed``.
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from common import settings

log = logging.getLogger("payment-service.sonco")

PAID = "paid"
CLOSED_UNPAID = ("expired", "failed", "cancelled", "canceled")

# Payout states, as Sonco Pay names them. Anything else is still on its way.
PAYOUT_DONE = ("completed",)
PAYOUT_FAILED = ("failed", "rejected", "review_rejected", "cancelled", "canceled", "returned")


class SoncoError(RuntimeError):
    """Sonco Pay could not do it.

    ``definite`` is the line that matters for money going out: True means Sonco
    Pay answered and refused, so nothing was sent and the amount can go back
    to the member. False means we do not know - a timeout, a 5xx - and the
    payout may well exist; the amount stays reserved until it is read back.
    """

    def __init__(self, message: str, *, definite: bool, code: str | None = None):
        super().__init__(message)
        self.definite = definite
        self.code = code


def enabled() -> bool:
    return bool((settings.SONCOPAY_API_KEY or "").strip())


def _client():
    from soncopay import SoncoPay

    if not enabled():
        raise SoncoError("Sonco Pay is not configured", definite=True, code="not_configured")
    base_url = (settings.SONCOPAY_BASE_URL or "https://soncopay.com").rstrip("/")
    return SoncoPay(settings.SONCOPAY_API_KEY.strip(), base_url=base_url, timeout=25, max_retries=2)


def _call(fn, *args, **kwargs):
    """Run one SDK call and sort its failure into refused or unknown."""
    from soncopay import ApiConnectionError, ApiError

    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        # 4xx is an answer: Sonco Pay looked at the request and said no. Except
        # 408 and 429 (try again later) and 409 (the same reference with other
        # details - the first one may exist), which say nothing about whether
        # something was created.
        definite = 400 <= exc.status < 500 and exc.status not in (408, 409, 429)
        log.warning("sonco pay %s: %s", getattr(fn, "__qualname__", fn), exc)
        raise SoncoError(exc.message or str(exc), definite=definite, code=exc.code) from exc
    except ApiConnectionError as exc:
        log.warning("sonco pay unreachable: %s", exc)
        raise SoncoError("Sonco Pay did not answer", definite=False, code="unreachable") from exc


def webhook_url() -> str:
    return f"{settings.GATEWAY_URL.rstrip('/')}/api/payments/webhook/soncopay"


# --- payments in ---------------------------------------------------------------

def create_payment(
    *, payment_id: str, amount: Decimal, description: str, email: str | None,
    name: str | None, user_id: str, return_url: str,
) -> dict:
    sp = _client()
    payer: dict[str, Any] = {"reference": user_id}
    if email:
        payer["email"] = email
    if name:
        payer["name"] = name[:80]
    return _call(
        sp.payments.create,
        amount=str(amount), currency="USD", order_id=payment_id,
        description=description[:120], customer_email=email or None,
        return_url=return_url, callback_url=webhook_url(),
        metadata={"kinjy_payment": payment_id}, payer=payer,
    )


def retrieve_payment(sonco_id: str) -> dict:
    return _call(_client().payments.retrieve, sonco_id)


def conforms(intent, sp_payment: dict) -> bool:
    """Is this Sonco Pay payment *this* Kinjy payment, paid in full?

    The id, the order id, the currency and the amount are all read back. A
    payment for another purchase, in another currency or for less settles
    nothing, whatever its status says.
    """
    if not sp_payment or sp_payment.get("status") != PAID:
        return False
    if intent.external_id and str(sp_payment.get("id") or "") != str(intent.external_id):
        return False
    if str(sp_payment.get("order_id") or "") != intent.id:
        return False
    if (sp_payment.get("currency") or "").upper() != "USD":
        return False
    try:
        return Decimal(str(sp_payment.get("amount"))) == Decimal(str(intent.amount))
    except (InvalidOperation, TypeError, ValueError):
        return False


# --- payouts out -----------------------------------------------------------------

_terms_cache: tuple[float, dict] | None = None


def payout_terms() -> dict:
    """The network fee and minimum for a USDT/BSC payout, as Sonco Pay sets them.

    Read from Sonco Pay rather than written here, and kept five minutes: the
    fee follows the network, and a quote should not be a guess from last month.
    """
    global _terms_cache
    if _terms_cache and time.monotonic() - _terms_cache[0] < 300:
        return _terms_cache[1]
    data = _call(_client().payouts.fees)
    key = f"{settings.PAYOUT_ASSET}:{settings.PAYOUT_NETWORK}"
    fees = data.get("fees") or {}
    if key not in fees:
        raise SoncoError(f"Sonco Pay does not pay out {key}", definite=True, code="unsupported")
    terms = {
        "fee": Decimal(str(fees[key])),
        "minimum": Decimal(str(data.get("min_payout_usd") or "0")),
    }
    _terms_cache = (time.monotonic(), terms)
    return terms


def create_payout(*, amount: Decimal, address: str, reference: str) -> dict:
    return _call(
        _client().payouts.create,
        amount=str(amount), asset=settings.PAYOUT_ASSET, network=settings.PAYOUT_NETWORK,
        address=address, reference=reference,
    )


def retrieve_payout(payout_id: str) -> dict:
    return _call(_client().payouts.retrieve, payout_id)


def construct_event(raw: bytes, headers) -> Any:
    """The verified webhook, or SignatureVerificationError."""
    from soncopay import construct_event as _construct

    return _construct(raw, headers, settings.SONCOPAY_WEBHOOK_SECRET)
