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


# --- paying on Kinjy's own page ---------------------------------------------------
# The payment is created as before, then Kinjy asks Sonco Pay which methods
# THIS payment can use, passes on the member's choice, and shows the step that
# comes back (approve on the phone, send crypto to an address, ...). Nothing
# on that page settles anything: settlement is still the payment read back.

# Dialling codes of the countries where Sonco Pay offers mobile money. A
# country missing here gets an empty prefix and the member types the code.
DIALLING = {
    "BJ": "+229", "TG": "+228", "CI": "+225", "SN": "+221", "BF": "+226", "ML": "+223",
    "NE": "+227", "GN": "+224", "CM": "+237", "CG": "+242", "GA": "+241", "CD": "+243",
    "TD": "+235", "CF": "+236", "GH": "+233", "NG": "+234", "KE": "+254", "RW": "+250",
    "UG": "+256", "TZ": "+255", "ZM": "+260", "MZ": "+258", "SL": "+232", "LR": "+231",
    "GM": "+220", "MW": "+265", "ET": "+251", "MG": "+261",
}


def _mobile_entries(o: dict) -> list[dict]:
    """One option per country: an operator can cover several."""
    out = []
    for country in o.get("countries") or [""]:
        out.append({
            "code": o.get("code"), "operator": o.get("operator") or o.get("label"),
            "label": o.get("label"), "country": country, "prefix": DIALLING.get(country, ""),
            "currency": o.get("pay_currency") or o.get("currency"),
            "estimated_pay_amount": o.get("estimated_pay_amount"),
            "estimated_payer_total": o.get("estimated_payer_total"),
            "redirect": bool(o.get("redirect")),
            "needs_operator": "operator" in (o.get("fields") or []),
        })
    return out


def offer(sonco_id: str) -> dict:
    """The methods open for this payment, sorted for Kinjy's screen."""
    raw = _call(_client().payments.methods, sonco_id)
    mobile, crypto, other = [], [], []
    for o in raw.get("methods") or []:
        kind = o.get("method")
        if kind == "mobile_money":
            mobile.extend(_mobile_entries(o))
        elif kind == "crypto":
            crypto.append({
                "asset": o.get("asset"), "network": o.get("network"),
                "network_label": o.get("network_label") or o.get("network"),
                "label": o.get("label"), "pay_currency": o.get("pay_currency") or o.get("asset"),
                "estimated_pay_amount": o.get("estimated_pay_amount"),
            })
        elif kind in ("card", "binance_pay"):
            other.append({"method": kind, "code": o.get("code"), "label": o.get("label"),
                          "pay_currency": o.get("pay_currency"),
                          "estimated_pay_amount": o.get("estimated_pay_amount")})
    return {"status": raw.get("status"), "amount": raw.get("amount"), "currency": raw.get("currency"),
            "expires_at": raw.get("expires_at"), "mobile_money": mobile, "crypto": crypto, "other": other}


def international_phone(number: str, country: str) -> str:
    """The number with its dialling code. Sonco Pay drops a national 0 itself
    where the operator wants it; only a missing code is added here."""
    digits = "".join(c for c in (number or "") if c.isdigit())
    code = DIALLING.get((country or "").upper(), "").lstrip("+")
    if (number or "").strip().startswith("+") or (code and digits.startswith(code)):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    return f"+{code}{digits}" if code else "+" + digits


def choose(
    sonco_id: str, *, method: str, payer: dict, code: str | None = None, asset: str | None = None,
    network: str | None = None, phone: str | None = None, country: str | None = None,
    operator: str | None = None, return_url: str | None = None,
) -> dict:
    """Pass on the member's choice. Called again, it changes the method or
    sends a new request after a declined one."""
    sp = _client()
    if method == "crypto":
        return _call(sp.payments.choose_crypto, sonco_id, asset or "", network or "", payer=payer)
    if method == "mobile_money":
        number = international_phone(phone, country or "") if phone else None
        return _call(sp.payments.choose_mobile_money, sonco_id, code or "", phone=number,
                     operator=operator, payer=payer, return_url=return_url)
    if method == "card":
        return _call(sp.payments.choose_card, sonco_id, code=code, payer=payer, return_url=return_url)
    if method == "binance_pay":
        return _call(sp.payments.choose_binance_pay, sonco_id, payer=payer)
    raise SoncoError("Unknown payment method", definite=True, code="bad_method")


def qr_url(sonco_id: str) -> str:
    """Sonco Pay's public QR code (SVG) for the payment's address."""
    return f"{(settings.SONCOPAY_BASE_URL or 'https://soncopay.com').rstrip('/')}/pay/{sonco_id}/qr.svg"


def action(sp_payment: dict) -> dict | None:
    """The step to show, without what Kinjy's screen does not use."""
    step = sp_payment.get("next_action") or None
    if not step:
        return None
    kind = step.get("type")
    out = {"type": kind, "expires_at": step.get("expires_at")}
    if kind == "send_crypto":
        out.update(asset=step.get("asset"), network=step.get("network"),
                   network_label=step.get("network_label") or step.get("network"),
                   address=step.get("address"), memo=step.get("memo"), amount=step.get("amount"),
                   amount_rule=step.get("amount_rule"), underpaid=bool(step.get("underpaid")),
                   received_amount=step.get("received_amount"),
                   qr_url=qr_url(sp_payment.get("id") or ""))
    elif kind == "approve_on_phone":
        out.update(phone=step.get("phone"), operator=step.get("operator"), amount=step.get("amount"),
                   currency=step.get("currency"), instructions=step.get("instructions"))
    elif kind in ("redirect", "open_wallet"):
        out.update(url=step.get("url"), amount=step.get("amount"),
                   currency=step.get("currency") or step.get("asset"))
    elif kind == "wait_for_confirmation":
        out.update(tx_hash=step.get("tx_hash"))
    elif kind == "choose_method":
        out.update(retry=bool(step.get("retry")), last_error=sp_payment.get("last_error"))
    return out
