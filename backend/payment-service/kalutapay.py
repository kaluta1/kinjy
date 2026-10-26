"""Kaluta Pay: Kinjy's payments, and the split of each one.

Kaluta Pay (kalutagroup.com) sits in front of the payment provider. Kinjy is a
tenant there ("Kinji", escrow on). For every payment:

1. an invoice is created whose ``order_ref`` is the Kinjy payment id - the
   same ref again returns the same invoice;
2. the member pays on Kinjy's own page: the methods come from Kaluta Pay, the
   choice starts a checkout, and its ``next_action`` (approve on the phone,
   send crypto to an address...) is shown as it is;
3. once the invoice is paid - read back from Kaluta Pay, never taken from the
   webhook - the split is set: what Kinjy owes members out of this payment
   (sponsor commission, the Leaders pool, an order held until delivery) stays
   in escrow; the rest goes to Kinjy's own address.

Webhooks are signed: ``X-KalutaPay-Signature`` is the hex HMAC-SHA512 of the
raw body with the tenant's ``whs_`` secret.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from common import settings

log = logging.getLogger("payment-service.kalutapay")

PAID = "paid"


class KalutaPayError(RuntimeError):
    """Kaluta Pay could not do it. ``definite`` as for Sonco Pay: True when it
    answered and refused, False when we do not know."""

    def __init__(self, message: str, *, status: int | None = None, definite: bool = True):
        super().__init__(message)
        self.status = status
        self.definite = definite


def enabled() -> bool:
    return bool((settings.KALUTAPAY_API_KEY or "").strip())


def _base() -> str:
    return (settings.KALUTAPAY_BASE_URL or "https://kalutagroup.com").rstrip("/")


def _request(method: str, path: str, json: Any = None, timeout: float = 25) -> Any:
    if not enabled():
        raise KalutaPayError("Kaluta Pay is not configured")
    try:
        response = httpx.request(
            method, f"{_base()}{path}", json=json, timeout=timeout,
            headers={"X-Api-Key": settings.KALUTAPAY_API_KEY.strip(), "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        raise KalutaPayError(f"Kaluta Pay did not answer: {exc}", definite=False) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:200]
        definite = response.status_code < 500 and response.status_code not in (408, 409, 429)
        log.warning("kaluta pay %s %s -> %s %s", method, path, response.status_code, detail)
        raise KalutaPayError(str(detail or f"HTTP {response.status_code}"), status=response.status_code,
                             definite=definite)
    return response.json()


# --- invoices ---------------------------------------------------------------------

def create_invoice(
    *, payment_id: str, amount: Decimal, title: str, customer_id: str, email: str | None,
    name: str | None, referrer_id: str | None, metadata: dict, success_url: str | None,
) -> dict:
    customer: dict[str, Any] = {"external_id": customer_id}
    if email:
        customer["email"] = email
    if name:
        customer["name"] = name[:120]
    if referrer_id:
        customer["referrer_external_id"] = referrer_id
    body: dict[str, Any] = {
        "title": title[:160], "amount": str(amount), "currency": "USD", "order_ref": payment_id,
        "customer": customer, "metadata": metadata,
    }
    if success_url:
        body["success_url"] = success_url
    return _request("POST", "/v1/invoices", body)


def get_invoice(invoice_id: str) -> dict:
    return _request("GET", f"/v1/invoices/{invoice_id}")


def conforms(intent, invoice: dict) -> bool:
    """Is this invoice *this* Kinjy payment, paid in full, in dollars?"""
    if not invoice or invoice.get("status") != PAID:
        return False
    if intent.external_id and str(invoice.get("id") or "") != str(intent.external_id):
        return False
    if str(invoice.get("order_ref") or "") != intent.id:
        return False
    if (invoice.get("currency") or "").upper() != "USD":
        return False
    try:
        return Decimal(str(invoice.get("amount"))) == Decimal(str(intent.amount)) and \
            Decimal(str(invoice.get("amount_paid") or 0)) >= Decimal(str(intent.amount))
    except (InvalidOperation, TypeError, ValueError):
        return False


# --- paying on Kinjy's page ---------------------------------------------------------

def offer() -> dict:
    """Kaluta Pay's methods, in the shape Kinjy's payment screen draws."""
    from sonco import DIALLING

    raw = _request("GET", "/v1/payment-methods")
    mobile, crypto, other = [], [], []
    for m in raw if isinstance(raw, list) else raw.get("items", []):
        if not m.get("enabled", True):
            continue
        kind = m.get("kind") or m.get("method")
        if kind == "crypto":
            crypto.append({"id": m["id"], "asset": m.get("asset"), "network": m.get("network"),
                           "network_label": m.get("network"), "label": m.get("label"),
                           "pay_currency": m.get("asset")})
        elif kind == "mobile_money":
            for country in m.get("countries") or [""]:
                mobile.append({"id": m["id"], "code": m.get("code") or m["id"], "operator": m.get("operator") or m.get("label"),
                               "label": m.get("label"), "country": country, "prefix": DIALLING.get(country, ""),
                               "currency": m.get("currency"), "redirect": False,
                               "needs_operator": "operator" in (m.get("fields") or [])})
        else:
            other.append({"id": m["id"], "method": kind if kind in ("card", "binance_pay") else "card",
                          "code": m.get("code"), "label": m.get("label")})
    return {"mobile_money": mobile, "crypto": crypto, "other": other}


def start_checkout(invoice_id: str, *, method: str, email: str | None, phone: str | None,
                   operator: str | None) -> dict:
    body: dict[str, Any] = {"method": method}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    if operator:
        body["operator"] = operator
    return _request("POST", f"/v1/invoices/{invoice_id}/checkout", body)


def open_action(invoice: dict) -> dict | None:
    """The step of the invoice's open checkout, if any, as Sonco Pay names it."""
    for checkout in invoice.get("checkouts") or []:
        if checkout.get("status") == "pending" and checkout.get("next_action"):
            return checkout["next_action"]
    return None


# --- the split ------------------------------------------------------------------------

def put_escrow(invoice_id: str, lines: list[dict]) -> dict:
    return _request("PUT", f"/v1/invoices/{invoice_id}/escrow", {"lines": lines})


def list_categories() -> list[dict]:
    return _request("GET", "/v1/escrow/categories").get("items", [])


def create_category(code: str, label: str, holding: str, description: str | None = None) -> dict:
    return _request("POST", "/v1/escrow/categories",
                    {"code": code, "label": label, "holding": holding, "description": description})


def create_distribution(*, pool: str, category: str, reference: str, allocations: list[dict],
                        label: str | None = None, currency: str = "USD") -> dict:
    """Move part of an escrow pool to named members. Idempotent on ``reference``:
    the same reference again returns the same distribution."""
    return _request("POST", "/v1/escrow/distributions", {
        "pool": pool, "category": category, "reference": reference, "label": label,
        "currency": currency, "allocations": allocations,
    })


# --- webhooks -------------------------------------------------------------------------

def signature_ok(raw: bytes, header: str | None) -> bool:
    secret = (settings.KALUTAPAY_WEBHOOK_SECRET or "").strip()
    if not secret or not header:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, header.strip())
