"""Kinjy · payment-service — checkout into custody, KYC, refunds, payout batches.

A buyer paying for an order is not paying Kinjy. The money goes to the
custodian — an authorised financial institution holding it in a segregated
client-money account — and is released to the seller only once the buyer
confirms receipt or a dispute is resolved. This service is what moves money in
and out of that account; commerce-service decides when.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from common import economy, events, settings
from common import agefeatures
from common.auth import AdminUser, CurrentUser
from common.database import get_db
from common.ids import new_id
from common.notify import notify
from common.service import create_app

import kaluta
import kalutapay
import models
import payouts
import rails
import sonco

log = logging.getLogger("payment-service")
AUTH_URL = "http://auth-service:8000"
LEDGER_URL = "http://ledger-service:8000"
COMMERCE_URL = "http://commerce-service:8000"

_T = models.SCHEMA
# Columns added after the first deploy. create_all cannot add a column to a
# table that already exists, so a live database gets them here.
MIGRATIONS = [
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending_email'",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS confirm_token_hash VARCHAR(64)",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS confirm_expires_at TIMESTAMPTZ",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS email_confirmed_at TIMESTAMPTZ",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS reviewed_by VARCHAR(40)",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS reviewed_by_name VARCHAR(120)",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS review_note VARCHAR(300)",
    f"ALTER TABLE {_T}.payout_destinations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
    f"CREATE INDEX IF NOT EXISTS ix_payout_destinations_confirm_token_hash ON {_T}.payout_destinations (confirm_token_hash)",
    f"ALTER TABLE {_T}.kyc_records ADD COLUMN IF NOT EXISTS verification_url VARCHAR(500)",
    f"ALTER TABLE {_T}.kyc_records ADD COLUMN IF NOT EXISTS session_expires_at TIMESTAMPTZ",
    f"ALTER TABLE {_T}.payment_intents ADD COLUMN IF NOT EXISTS split_status VARCHAR(20)",
    # A settled referral-pool payment whose units (and revenue) are recorded.
    # Null on a settled one means the reconciler still owes it the credit.
    f"ALTER TABLE {_T}.payment_intents ADD COLUMN IF NOT EXISTS fulfilled_at TIMESTAMPTZ",
]


def _start_reconciler():
    # Withdrawals a webhook never closed - Sonco Pay down at the wrong moment,
    # a delivery lost - are read back every five minutes.
    asyncio.get_event_loop().create_task(payouts.reconcile_loop())
    asyncio.get_event_loop().create_task(_splits_loop())
    asyncio.get_event_loop().create_task(_pool_loop())


async def _pool_loop() -> None:
    # Pool payments that settled while auth-service or the ledger was down:
    # their units are credited here. Both calls are idempotent.
    while True:
        await asyncio.sleep(120)
        try:
            await asyncio.to_thread(reconcile_pool_purchases)
        except Exception as exc:
            log.error("pool reconciler: %s", exc)


async def _splits_loop() -> None:
    # Kaluta Pay splits that did not land at payment time.
    while True:
        await asyncio.sleep(300)
        if kalutapay.enabled():
            try:
                await asyncio.to_thread(reconcile_splits)
            except Exception as exc:
                log.error("split reconciler: %s", exc)


app = create_app(
    name="payment-service",
    schema=models.SCHEMA,
    description="Checkout through Sonco Pay, refunds, KinjyKYC by Kaluta, member withdrawals.",
    migrations=MIGRATIONS,
    on_startup=[_start_reconciler],
)
app.include_router(payouts.router)


class CheckoutIn(BaseModel):
    purpose: str = Field(pattern="^(order|subscription|ad_credit|kyc_fee|tribute|referral_pool)$")
    amount: Decimal = Field(gt=0)
    reference: str | None = None
    rail: str | None = Field(default=None, pattern="^(soncopay|nowpayments|mangopay|mock)$")
    pay_currency: str = "usdtbsc"
    # Where the member lands back on Kinjy after paying on Sonco Pay's page.
    # A path on this site only - an open redirect is a phishing tool.
    return_path: str | None = Field(default=None, max_length=300, pattern=r"^/([^/\\\s][^\\\s]*)?$")
    # ("/" then anything but a second slash or a backslash: browsers read both
    # "//host" and "/\host" as another site.)


PURPOSE_LABELS = {
    "order": "Kinjy marketplace order",
    "subscription": "Kinjy subscription",
    "ad_credit": "Kinjy ad credit",
    "kyc_fee": "KinjyKYC annual verification",
    "tribute": "Kinjy tribute",
    "referral_pool": "Kinjy referral pool units",
}


class DestinationIn(BaseModel):
    rail: str = Field(default="nowpayments", pattern="^(nowpayments|mangopay)$")
    address: str = Field(min_length=6, max_length=255)
    currency: str = "usdtbsc"
    label: str | None = None


@app.get("/payments/rails", tags=["payments"])
def available_rails():
    return {
        "nowpayments": {
            "enabled": rails.nowpayments_enabled(),
            "mode": "live" if rails.nowpayments_enabled() else "mock",
            "payout_currency": settings.NOWPAYMENTS_PAYOUT_CURRENCY,
        },
        "mangopay": {
            "enabled": rails.mangopay_enabled(),
            "mode": "live" if rails.mangopay_enabled() else "mock",
        },
        "payout_threshold_usd": str(settings.PAYOUT_THRESHOLD_USD),
        "custody_withdrawal_floor_usd": "50.00",
        "note": (
            "Member payouts clear at $1 because they are batched off-chain from the "
            "custody balance; the $50 floor applies only to sweeping custody to the "
            "company's own wallet."
        ),
    }


@app.post("/payments/checkout", status_code=201, tags=["payments"])
def checkout(payload: CheckoutIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    # The single door every payment goes through, which is why the gate sits
    # here rather than only on the features that lead to it. A surface that
    # forgot its own check still cannot take a minor's money.
    agefeatures.require(principal.user_id, "payments")

    if payload.purpose == "kyc_fee":
        # The fee has one price, set here, not by the client. Before this a
        # checkout for one cent marked the year as paid - and a paid fee now
        # unlocks things (more than three pinned groups), so it has to cost
        # what it says.
        if economy.money(payload.amount) != economy.money(settings.KYC_ANNUAL_FEE_USD):
            raise HTTPException(
                status_code=400,
                detail=f"The verification fee is ${settings.KYC_ANNUAL_FEE_USD}",
            )
        paid = db.scalar(
            select(models.KycRecord).where(
                models.KycRecord.user_id == principal.user_id,
                models.KycRecord.year == date.today().year,
                models.KycRecord.fee_paid.is_(True),
            )
        )
        if paid is not None:
            raise HTTPException(status_code=409, detail="This year's verification fee is already paid")

    if payload.purpose == "referral_pool":
        # The quantity is in the reference, the price is the server's: a
        # client cannot buy units for less, or claim more than it paid for.
        units = pool_units(payload.reference)
        if units is None or economy.money(payload.amount) != pool_price(units):
            raise HTTPException(status_code=400, detail="Pool units are bought from the pool page")

    # Sonco Pay, once configured, takes every payment: a client asking for
    # another rail does not get to pick a different door.
    # Kaluta Pay, once configured, takes every payment (and splits it); else
    # Sonco Pay directly. A client asking for another rail does not get to
    # pick a different door.
    rail = (
        "kalutapay" if kalutapay.enabled()
        else "soncopay" if sonco.enabled()
        else rails.choose_rail(payload.rail if payload.rail not in ("soncopay", "kalutapay") else None, "USD")
    )
    intent = models.PaymentIntent(
        id=new_id("pmt"),
        user_id=principal.user_id,
        rail=rail,
        purpose=payload.purpose,
        reference=payload.reference,
        amount=economy.money(payload.amount),
        pay_currency=payload.pay_currency,
    )
    db.add(intent)
    db.flush()

    callback = f"{settings.GATEWAY_URL}/api/payments/ipn/nowpayments"
    try:
        if rail == "kalutapay":
            identity = payouts._identity(principal.user_id)
            back = payload.return_path or "/hub"
            joiner = "&" if "?" in back else "?"
            invoice = kalutapay.create_invoice(
                payment_id=intent.id, amount=intent.amount,
                title=PURPOSE_LABELS.get(payload.purpose, "Kinjy payment"),
                customer_id=principal.user_id, email=identity.get("email"),
                name=identity.get("display_name"), referrer_id=_sponsor_of(principal.user_id),
                metadata={"purpose": payload.purpose, "reference": payload.reference, "kinjy_payment": intent.id},
                success_url=f"{settings.FRONTEND_URL.rstrip('/')}{back}{joiner}payment={intent.id}",
            )
            result = {"external_id": invoice.get("id"), "checkout_url": invoice.get("url"), "raw": invoice}
        elif rail == "soncopay":
            identity = payouts._identity(principal.user_id)
            back = payload.return_path or "/hub"
            joiner = "&" if "?" in back else "?"
            created = sonco.create_payment(
                payment_id=intent.id, amount=intent.amount,
                description=PURPOSE_LABELS.get(payload.purpose, "Kinjy payment"),
                email=identity.get("email"), name=identity.get("display_name"),
                user_id=principal.user_id,
                return_url=f"{settings.FRONTEND_URL.rstrip('/')}{back}{joiner}payment={intent.id}",
            )
            result = {
                "external_id": created.get("id"),
                "checkout_url": created.get("checkout_url") or created.get("payment_url"),
                "raw": created,
            }
        elif rail == "mangopay":
            result = rails.create_fiat_payment(
                amount=intent.amount, currency="USD", user_id=principal.user_id, reference=intent.id
            )
        else:
            result = rails.create_crypto_payment(
                amount=intent.amount,
                currency="USD",
                pay_currency=payload.pay_currency,
                order_id=intent.id,
                callback_url=callback,
            )
    except rails.RailError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=str(exc))
    except sonco.SoncoError as exc:
        db.rollback()
        log.error("sonco pay checkout failed (%s): %s", exc.code, exc)
        raise HTTPException(status_code=503, detail="The payment service is unavailable right now. Try again in a moment.")
    except kalutapay.KalutaPayError as exc:
        db.rollback()
        log.error("kaluta pay checkout failed (%s): %s", exc.status, exc)
        raise HTTPException(status_code=503, detail="The payment service is unavailable right now. Try again in a moment.")

    intent.external_id = result.get("external_id")
    intent.pay_address = result.get("pay_address")
    intent.raw_payload = json.dumps(result.get("raw", result))
    db.commit()

    return {
        "payment_id": intent.id,
        "rail": rail,
        "mock": result.get("mock", False),
        "amount": str(intent.amount),
        "pay_address": intent.pay_address,
        "pay_currency": intent.pay_currency,
        "checkout_url": result.get("checkout_url"),
        "status": intent.status,
    }


@app.post("/payments/ipn/nowpayments", tags=["payments"])
async def nowpayments_ipn(
    request: Request,
    x_nowpayments_sig: str | None = Header(default=None, alias="x-nowpayments-sig"),
    db: OrmSession = Depends(get_db),
):
    """Provider callback. Rejects anything that is not correctly signed."""
    raw = await request.body()
    if not rails.verify_ipn_signature(raw, x_nowpayments_sig):
        raise HTTPException(status_code=401, detail="Invalid IPN signature")

    payload = json.loads(raw)
    intent = db.scalar(
        select(models.PaymentIntent).where(models.PaymentIntent.id == payload.get("order_id"))
    )
    if intent is None:
        raise HTTPException(status_code=404, detail="Unknown order_id")

    intent.status = payload.get("payment_status", intent.status)
    intent.raw_payload = raw.decode()
    if intent.status == "finished" and intent.settled_at is None:
        intent.settled_at = datetime.now(timezone.utc)
        await _on_settled(intent, db)
    db.commit()
    return {"ok": True}


@app.post("/payments/{payment_id}/mock-settle", tags=["payments"])
async def mock_settle(payment_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Dev-only shortcut so the full flow is testable without a provider.

    Refuses to run once a real rail is configured — otherwise it would be a way
    to mint settled payments in production.
    """
    if kalutapay.enabled() or sonco.enabled() or rails.nowpayments_enabled() or rails.mangopay_enabled():
        raise HTTPException(status_code=403, detail="A live payment rail is configured; mock settlement is disabled")

    intent = db.get(models.PaymentIntent, payment_id)
    if intent is None or intent.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    intent.status = "finished"
    intent.settled_at = datetime.now(timezone.utc)
    await _on_settled(intent, db)
    db.commit()
    return {"payment_id": payment_id, "status": intent.status, "mock": True}


def _sponsor_of(user_id: str) -> str | None:
    """Who is paid on this member's activity - given to Kaluta Pay with the payer.

    The same answer the ledger gets: nobody while the sponsor is not verified.
    """
    try:
        response = httpx.get(f"{AUTH_URL}/internal/sponsor/{user_id}", timeout=5)
        response.raise_for_status()
        return response.json().get("sponsor_id")
    except Exception:
        return None


def _escrow_lines(intent: models.PaymentIntent) -> list[dict] | None:
    """What this payment owes members, to hold in escrow. None when not knowable yet.

    Read from the ledger journal the payment wrote (``payment:{id}``), so the
    escrow holds exactly what the ledger owes: each sponsor's commission, and
    the Leaders pool's share. A marketplace order is held whole: it reaches
    the seller only after delivery. A payment that owes nobody (no sponsor, a
    purpose with no programme) gets an empty split - all of it to Kinjy.
    """
    if intent.purpose == "order":
        return [{
            "reference": f"order-{intent.id}", "kind": "other", "amount": str(intent.amount),
            "label": "Marketplace order held until the buyer confirms delivery",
            "source": {"external_id": intent.user_id},
        }]
    if intent.purpose not in ("kyc_fee", "referral_pool", "ad_credit"):
        return []
    if intent.purpose == "kyc_fee" and not settings.KYC_FEE_COMMISSIONABLE:
        return []
    try:
        response = httpx.get(f"{LEDGER_URL}/internal/journals/by-key",
                             params={"key": f"payment:{intent.id}"}, timeout=8)
    except Exception as exc:
        log.warning("ledger unreachable for the split of %s: %s", intent.id, exc)
        return None
    if response.status_code == 404:
        return None  # not posted yet; the reconciler comes back
    if response.status_code >= 400:
        return None
    lines = []
    for n, entry in enumerate(response.json().get("entries", [])):
        amount = Decimal(entry.get("credit") or "0")
        if amount <= 0:
            continue
        if entry.get("account") == "liabilities:member_payable" and entry.get("beneficiary_id"):
            lines.append({
                "reference": f"{intent.id}-c{n}", "kind": "commission", "level": 1,
                "amount": str(amount), "label": "Sponsor commission, 20% of Kinjy's revenue",
                "beneficiary": {"external_id": entry["beneficiary_id"]},
                "source": {"external_id": intent.user_id},
            })
        elif entry.get("account") == "liabilities:leaders_pool":
            lines.append({
                "reference": f"{intent.id}-l{n}", "kind": "other", "amount": str(amount),
                "label": "Kinjy Leaders pool, 5% of Kinjy's revenue",
                "source": {"external_id": intent.user_id},
            })
    return lines


def _split(intent: models.PaymentIntent, db: OrmSession) -> None:
    """Set the Kaluta Pay split of a paid payment. Retried by the reconciler until it lands."""
    if intent.rail != "kalutapay" or intent.settled_at is None or intent.split_status == "done":
        return
    lines = _escrow_lines(intent)
    if lines is None:
        intent.split_status = "pending"
        db.commit()
        return
    try:
        kalutapay.put_escrow(intent.external_id, lines)
        intent.split_status = "done"
    except kalutapay.KalutaPayError as exc:
        log.error("kaluta pay split of %s failed: %s", intent.id, exc)
        intent.split_status = "pending"
    db.commit()


async def _settle_from_kalutapay(intent_id: str, invoice: dict | None, db: OrmSession) -> str:
    """Apply what Kaluta Pay says about one invoice. The intent is locked, so a
    webhook and the member's page settle it once."""
    intent = db.scalar(
        select(models.PaymentIntent).where(models.PaymentIntent.id == intent_id).with_for_update()
    )
    if intent is None or intent.rail != "kalutapay":
        db.rollback()
        return "unknown"
    if intent.settled_at is not None:
        db.rollback()
        _split(intent, db)
        return "paid"
    if invoice is None:
        try:
            invoice = kalutapay.get_invoice(intent.external_id)
        except kalutapay.KalutaPayError:
            db.rollback()
            return "pending"
    if kalutapay.conforms(intent, invoice):
        intent.status = "finished"
        intent.settled_at = datetime.now(timezone.utc)
        intent.split_status = "pending"
        intent.raw_payload = json.dumps(invoice)[:20000]
        await _on_settled(intent, db)
        db.commit()
        _split(intent, db)
        return "paid"
    if invoice.get("status") == kalutapay.PAID:
        log.error("payment %s: kaluta pay says paid but it does not match (%s %s, ref %s)",
                  intent.id, invoice.get("amount"), invoice.get("currency"), invoice.get("order_ref"))
    elif invoice.get("status") in ("void", "expired", "canceled", "cancelled"):
        intent.status = "failed"
        db.commit()
        return "failed"
    db.rollback()
    return "pending"


@app.post("/payments/webhook/kalutapay", tags=["payments"])
async def kalutapay_webhook(request: Request, db: OrmSession = Depends(get_db)):
    """Signed notification from Kaluta Pay (HMAC-SHA512 of the raw body).

    Even signed, the invoice is read back from Kaluta Pay before anything
    moves; the notification only says which one to read.
    """
    if not settings.KALUTAPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    raw = await request.body()
    if not kalutapay.signature_ok(raw, request.headers.get("X-KalutaPay-Signature")):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unreadable body")
    event = str(body.get("event") or "")
    data = body.get("data") or {}
    if not event.startswith("invoice.") or not data.get("order_ref"):
        return {"ok": True, "ignored": True}
    try:
        invoice = kalutapay.get_invoice(str(data.get("id")))
    except kalutapay.KalutaPayError:
        invoice = None
    verdict = await _settle_from_kalutapay(str(data["order_ref"]), invoice, db)
    return {"ok": True, "status": verdict}


def reconcile_splits() -> int:
    """Splits that did not land (Kaluta Pay or the ledger down at the time)."""
    from common.database import SessionLocal

    with SessionLocal() as db:
        rows = db.scalars(
            select(models.PaymentIntent).where(
                models.PaymentIntent.rail == "kalutapay",
                models.PaymentIntent.settled_at.is_not(None),
                models.PaymentIntent.split_status == "pending",
            ).limit(100)
        ).all()
        for intent in rows:
            try:
                _split(intent, db)
            except Exception as exc:
                db.rollback()
                log.error("split retry of %s failed: %s", intent.id, exc)
        return len(rows)


@app.post("/admin/payments/splits/reconcile", tags=["admin"])
def reconcile_splits_now(_: AdminUser):
    return {"checked": reconcile_splits()}


async def _settle_from_sonco(intent_id: str, sp_payment: dict | None, db: OrmSession) -> str:
    """Apply what Sonco Pay says about one payment. Shared by the webhook and the read-back.

    The intent is locked, so a webhook and a member's page asking at the same
    moment settle it once: the second finds ``settled_at`` already set.
    """
    intent = db.scalar(
        select(models.PaymentIntent).where(models.PaymentIntent.id == intent_id).with_for_update()
    )
    if intent is None or intent.rail != "soncopay":
        db.rollback()
        return "unknown"
    if intent.settled_at is not None:
        db.rollback()
        return "paid"
    if sp_payment is None:
        try:
            sp_payment = sonco.retrieve_payment(intent.external_id)
        except sonco.SoncoError:
            db.rollback()
            return "pending"
    if sonco.conforms(intent, sp_payment):
        intent.status = "finished"
        intent.settled_at = datetime.now(timezone.utc)
        intent.raw_payload = json.dumps(sp_payment)[:20000]
        await _on_settled(intent, db)
        db.commit()
        return "paid"
    status = sp_payment.get("status")
    if status == sonco.PAID:
        # Paid at Sonco Pay but not this payment in full: grant nothing, and say so.
        log.error("payment %s: sonco pay says paid but it does not match (%s %s, order %s)",
                  intent.id, sp_payment.get("amount"), sp_payment.get("currency"), sp_payment.get("order_id"))
        db.rollback()
        return "pending"
    if status in sonco.CLOSED_UNPAID:
        intent.status = "expired" if status == "expired" else "failed"
        db.commit()
        return "failed"
    db.rollback()
    return "pending"


@app.post("/payments/webhook/soncopay", tags=["payments"])
async def soncopay_webhook(request: Request, db: OrmSession = Depends(get_db)):
    """Signed notification from Sonco Pay.

    The signature is checked on the raw body (and a replay older than five
    minutes is refused) - and even then, the payment or payout is read back
    from Sonco Pay before anything moves. The signed copy is only used when
    that read fails.
    """
    from soncopay import SignatureVerificationError

    if not settings.SONCOPAY_WEBHOOK_SECRET:
        # Nothing to check it with. 503 makes Sonco Pay deliver it again later.
        raise HTTPException(status_code=503, detail="Webhook not configured")
    raw = await request.body()
    try:
        event = sonco.construct_event(raw, request.headers)
    except SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = event.data or {}
    if event.event.startswith("payout."):
        return {"ok": True, "matched": payouts.apply_event(event, db)}
    if not event.event.startswith("payment.") or not data.get("order_id"):
        return {"ok": True, "ignored": True}
    try:
        sp_payment = sonco.retrieve_payment(str(data.get("id")))
    except sonco.SoncoError:
        sp_payment = data
    verdict = await _settle_from_sonco(str(data["order_id"]), sp_payment, db)
    return {"ok": True, "status": verdict}


def _my_open_sonco_intent(payment_id: str, principal, db: OrmSession) -> models.PaymentIntent:
    intent = db.get(models.PaymentIntent, payment_id)
    if intent is None or intent.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    if intent.rail not in ("soncopay", "kalutapay") or not intent.external_id:
        raise HTTPException(status_code=409, detail="This payment is not paid on Kinjy's page")
    return intent


@app.get("/payments/intents/{payment_id}", tags=["payments"])
async def payment_status(payment_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Where a payment stands, read back from Sonco Pay, with the step to show.

    What the payment page asks every few seconds, and what the page asks on
    the member's return. The webhook usually got there first, but it does not
    have to for the member to see their payment go through.
    """
    intent = db.get(models.PaymentIntent, payment_id)
    if intent is None or intent.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    verdict, step = None, None
    if intent.rail == "kalutapay" and intent.settled_at is None and intent.status in ("waiting", "confirming"):
        invoice = None
        try:
            invoice = kalutapay.get_invoice(intent.external_id)
        except kalutapay.KalutaPayError:
            pass
        if invoice is not None:
            verdict = await _settle_from_kalutapay(intent.id, invoice, db)
            step = _kp_step(kalutapay.open_action(invoice))
        db.expire_all()
        intent = db.get(models.PaymentIntent, payment_id)
    elif intent.rail == "soncopay" and intent.settled_at is None and intent.status in ("waiting", "confirming"):
        sp_payment = None
        try:
            sp_payment = sonco.retrieve_payment(intent.external_id)
        except sonco.SoncoError:
            pass
        if sp_payment is not None:
            verdict = await _settle_from_sonco(intent.id, sp_payment, db)
            step = sonco.action(sp_payment)
        db.expire_all()
        intent = db.get(models.PaymentIntent, payment_id)
    settled = intent.settled_at is not None
    return {
        "payment_id": intent.id,
        "purpose": intent.purpose,
        "reference": intent.reference,
        "amount": str(intent.amount),
        "status": "paid" if settled else (verdict or intent.status),
        "settled": settled,
        "action": None if settled else step,
        # A declined request (mobile money refused, card declined) leaves the
        # payment open: the member picks a method again.
        "retryable": bool(step and step.get("type") == "choose_method" and step.get("retry")),
    }


@app.get("/payments/intents/{payment_id}/methods", tags=["payments"])
def payment_methods(payment_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The ways this payment can be paid on Kinjy's own page, from Sonco Pay."""
    intent = _my_open_sonco_intent(payment_id, principal, db)
    if intent.settled_at is not None:
        return {"status": "paid", "mobile_money": [], "crypto": [], "other": []}
    try:
        if intent.rail == "kalutapay":
            return kalutapay.offer()
        return sonco.offer(intent.external_id)
    except (sonco.SoncoError, kalutapay.KalutaPayError) as exc:
        log.error("payment methods for %s failed: %s", intent.id, exc)
        raise HTTPException(status_code=503, detail="The payment methods could not be loaded. Try again.")


def _kp_step(next_action: dict | None) -> dict | None:
    """Kaluta Pay passes the provider's step through; drawn the same way. Its
    QR code is not Sonco Pay's public one, so the address alone is shown."""
    step = sonco.action({"next_action": next_action, "id": ""}) if next_action else None
    if step:
        step.pop("qr_url", None)
    return step


class ChooseIn(BaseModel):
    method: str = Field(pattern="^(mobile_money|crypto|card|binance_pay)$")
    code: str | None = Field(default=None, max_length=40)
    asset: str | None = Field(default=None, max_length=20)
    network: str | None = Field(default=None, max_length=20)
    phone: str | None = Field(default=None, max_length=24)
    country: str | None = Field(default=None, max_length=2)
    operator: str | None = Field(default=None, max_length=40)
    # Where a card or operator page sends the member back. A path on this
    # site only, as for checkout.
    return_path: str | None = Field(default=None, max_length=300, pattern=r"^/([^/\\\s][^\\\s]*)?$")


@app.post("/payments/intents/{payment_id}/choose", tags=["payments"])
async def choose_payment_method(
    payment_id: str, payload: ChooseIn, principal: CurrentUser, db: OrmSession = Depends(get_db)
):
    """Pass the member's choice to Sonco Pay and return the step to show.

    Called again, it changes the method or sends a new request after a
    declined one. The answer says what to do next; it never says "paid" on its
    own - the payment is read back, as everywhere else.
    """
    intent = _my_open_sonco_intent(payment_id, principal, db)
    if intent.settled_at is not None:
        return {"status": "paid", "settled": True, "action": None}
    if payload.method == "mobile_money" and not payload.code:
        raise HTTPException(status_code=422, detail="Choose a mobile money operator")
    if payload.method == "crypto" and not (payload.asset and payload.network):
        raise HTTPException(status_code=422, detail="Choose a currency and a network")
    identity = payouts._identity(principal.user_id)
    if intent.rail == "kalutapay":
        # Kaluta Pay names a method by one id: "crypto:USDT:BSC", or the
        # mobile money / card code.
        method_id = (
            f"crypto:{payload.asset}:{payload.network}" if payload.method == "crypto"
            else payload.code or payload.method
        )
        phone = sonco.international_phone(payload.phone, payload.country or "") if payload.phone else None
        try:
            started = kalutapay.start_checkout(
                intent.external_id, method=method_id, email=identity.get("email"),
                phone=phone, operator=payload.operator,
            )
        except kalutapay.KalutaPayError as exc:
            if exc.definite:
                raise HTTPException(status_code=400, detail=str(exc) or "That payment method was refused")
            raise HTTPException(status_code=503, detail="The payment service did not answer. Try again.")
        checkout = started.get("checkout") or {}
        verdict = "pending"
        if (started.get("invoice") or {}).get("status") == kalutapay.PAID:
            verdict = await _settle_from_kalutapay(intent.id, None, db)
        return {"status": verdict, "settled": verdict == "paid", "action": _kp_step(checkout.get("next_action"))}
    payer = {"reference": principal.user_id}
    if identity.get("email"):
        payer["email"] = identity["email"]
    if identity.get("display_name"):
        payer["name"] = identity["display_name"][:80]
    back = payload.return_path or "/hub"
    joiner = "&" if "?" in back else "?"
    try:
        sp_payment = sonco.choose(
            intent.external_id, method=payload.method, payer=payer, code=payload.code,
            asset=payload.asset, network=payload.network, phone=payload.phone,
            country=payload.country, operator=payload.operator,
            return_url=f"{settings.FRONTEND_URL.rstrip('/')}{back}{joiner}payment={intent.id}",
        )
    except sonco.SoncoError as exc:
        if exc.definite:
            # Sonco Pay's own words: a wrong number, a method closed, a
            # request still waiting on the phone. Worth showing as they are.
            raise HTTPException(status_code=400, detail=str(exc) or "That payment method was refused")
        raise HTTPException(status_code=503, detail="The payment service did not answer. Try again.")
    verdict = await _settle_from_sonco(intent.id, sp_payment, db)
    return {"status": verdict, "settled": verdict == "paid", "action": sonco.action(sp_payment)}


async def _on_settled(intent: models.PaymentIntent, db: OrmSession) -> None:
    """Fan out the consequences of a confirmed payment."""
    if intent.purpose == "order" and intent.reference:
        # The money is with the custodian now, not with the seller. This tells
        # commerce-service to open the escrow and start the confirmation window.
        try:
            httpx.post(
                f"{COMMERCE_URL}/internal/orders/{intent.reference}/funded",
                params={"custody_ref": intent.id},
                timeout=8,
            ).raise_for_status()
        except Exception as exc:
            log.error("could not open escrow for order %s: %s", intent.reference, exc)

    elif intent.purpose == "referral_pool":
        fulfil_pool_purchase(intent)

    elif intent.purpose == "kyc_fee":
        year = date.today().year
        record = db.scalar(
            select(models.KycRecord).where(
                models.KycRecord.user_id == intent.user_id, models.KycRecord.year == year
            )
        )
        if record is None:
            record = models.KycRecord(user_id=intent.user_id, year=year)
            db.add(record)
        record.fee_paid = True
        record.fee_payment_id = intent.id
        # Paying does not verify anyone — the identity check still has to pass.
        if settings.KYC_FEE_COMMISSIONABLE:
            # Kinjy revenue, so the payer's sponsor earns their 20% on it.
            try:
                httpx.post(
                    f"{LEDGER_URL}/internal/post/kyc-fee",
                    json={
                        "member_id": intent.user_id,
                        "amount": str(intent.amount),
                        "reference": intent.id,
                        "idempotency_key": f"payment:{intent.id}",
                    },
                    timeout=10,
                ).raise_for_status()
            except Exception as exc:
                log.error("kyc fee posting failed for payment %s: %s", intent.id, exc)

    elif intent.purpose == "ad_credit":
        try:
            httpx.post(
                f"{LEDGER_URL}/internal/post/ad-purchase",
                json={
                    "advertiser_id": intent.user_id,
                    "amount": str(intent.amount),
                    "campaign_id": intent.reference,
                    "idempotency_key": f"payment:{intent.id}",
                },
                timeout=10,
            ).raise_for_status()
        except Exception as exc:
            log.error("ad purchase posting failed for payment %s: %s", intent.id, exc)

    await events.publish(
        "payment.settled",
        {"payment_id": intent.id, "user_id": intent.user_id, "purpose": intent.purpose, "amount": str(intent.amount)},
    )


# --- referral pool ---------------------------------------------------------

@app.get("/payments/referral-pool", tags=["referral-pool"])
def referral_pool_status(principal: CurrentUser):
    """What a seat costs and whether any are left."""
    try:
        response = httpx.get(f"{AUTH_URL}/auth/referral-pool", timeout=5)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log.error("referral pool status unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="The referral pool is unavailable right now")


class JoinPoolIn(BaseModel):
    units: int = Field(default=1, ge=1)


@app.post("/payments/referral-pool/join", status_code=201, tags=["referral-pool"])
def join_referral_pool(
    principal: CurrentUser,
    payload: JoinPoolIn | None = None,
    rail: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """Start payment for one or more pool units: units x the unit price.

    Quantity and price are settled here, not by the client, and checked again
    when the units are credited. Room for a new member is checked before
    taking money and again at settlement (the cap counts members): the gap
    between the two is exactly where the last place goes to somebody else.
    """
    agefeatures.require(principal.user_id, "referral_pool")
    units = (payload or JoinPoolIn()).units
    if units > settings.REFERRAL_POOL_MAX_UNITS_PER_PURCHASE:
        raise HTTPException(
            status_code=400,
            detail=f"Buy at most {settings.REFERRAL_POOL_MAX_UNITS_PER_PURCHASE} units at a time",
        )

    try:
        verdict = httpx.get(f"{AUTH_URL}/internal/referral-pool/can-buy/{principal.user_id}", timeout=5).json()
    except Exception as exc:
        log.error("referral pool status unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="The referral pool is unavailable right now")
    if not verdict.get("allowed"):
        raise HTTPException(status_code=409, detail=verdict.get("reason") or "The referral pool is full")

    return checkout(
        CheckoutIn(
            purpose="referral_pool",
            amount=pool_price(units),
            reference=f"referral_pool:{units}",
            rail=rail,
            return_path="/earn",
        ),
        principal,
        db,
    )


def pool_units(reference: str | None) -> int | None:
    """Units a pool payment is for. "referral_pool" alone is a pre-units seat: one."""
    if reference == "referral_pool":
        return 1
    if not reference or not reference.startswith("referral_pool:"):
        return None
    raw = reference.split(":", 1)[1]
    if not raw.isdigit():
        return None
    units = int(raw)
    return units if 1 <= units <= settings.REFERRAL_POOL_MAX_UNITS_PER_PURCHASE else None


def pool_price(units: int) -> Decimal:
    return economy.money(Decimal(str(settings.REFERRAL_POOL_ENTRY_USD)) * units)


def fulfil_pool_purchase(intent: models.PaymentIntent) -> bool:
    """Credit a settled pool payment's units, then post its revenue.

    Units first, revenue second: if the units cannot be credited (the pool
    filled up between checkout and settlement) no revenue is recognised and
    the payment is marked for refund instead of quietly kept. Both calls are
    idempotent on the payment, so a duplicate notification or a reconciler
    pass credits nothing twice. Returns True once both have landed.
    """
    if intent.fulfilled_at is not None or intent.status == "refund_due":
        return intent.fulfilled_at is not None
    units = pool_units(intent.reference)
    if units is None:
        intent.status = "refund_due"
        log.error("pool payment %s has no readable quantity (%r)", intent.id, intent.reference)
        return False
    try:
        response = httpx.post(
            f"{AUTH_URL}/internal/referral-pool/purchases",
            json={"member_id": intent.user_id, "payment_ref": intent.id, "units": units,
                  "amount": str(intent.amount), "currency": "USD"},
            timeout=8,
        )
        if response.status_code in (400, 409):
            intent.status = "refund_due"
            log.error("pool units refused for payment %s (%s): must be refunded", intent.id, response.text[:200])
            return False
        response.raise_for_status()
        httpx.post(
            f"{LEDGER_URL}/internal/post/referral-pool-entry",
            json={"member_id": intent.user_id, "amount": str(intent.amount), "reference": intent.id,
                  "idempotency_key": f"payment:{intent.id}"},
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        log.error("pool units for payment %s not credited yet: %s", intent.id, exc)
        return False
    intent.fulfilled_at = datetime.now(timezone.utc)
    return True


def reconcile_pool_purchases() -> int:
    """Settled pool payments whose units were never credited."""
    from common.database import SessionLocal

    done = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(models.PaymentIntent).where(
                models.PaymentIntent.purpose == "referral_pool",
                models.PaymentIntent.settled_at.is_not(None),
                models.PaymentIntent.fulfilled_at.is_(None),
                models.PaymentIntent.status == "finished",
            ).limit(100)
        ).all()
        for intent in rows:
            done += fulfil_pool_purchase(intent)
            db.commit()
    return done


class PoolRefundIn(BaseModel):
    units: int = Field(ge=1)
    kind: str = Field(default="refund", pattern="^(refund|reversal)$")
    reason: str = Field(min_length=3, max_length=300)
    # Made once per refund form by the admin page: the same request sent
    # twice (a double click, a retry after a timeout) refunds once.
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,36}$")


@app.post("/admin/pool-refunds/{payment_id}", tags=["admin"])
def refund_pool_units(
    payment_id: str, payload: PoolRefundIn, admin: AdminUser, db: OrmSession = Depends(get_db)
):
    """Refund pool units bought with one payment, all the way.

    1. The units come off (auth-service): unused quota in the current round
       goes with them; referrals already received stay.
    2. The ledger mirrors the same share of the payment: revenue, the buyer's
       sponsor's commission and the Leaders share come back pro rata.
    3. The money is owed to the buyer on their Kinjy balance, which they
       withdraw like any balance. Neither Kaluta Pay nor Sonco Pay can send a
       payment back to the payer, so this is the refund path that exists.

    Each step is idempotent on the request, so a retry finishes what a
    failure interrupted and never refunds twice.
    """
    intent = db.get(models.PaymentIntent, payment_id)
    if intent is None or intent.purpose != "referral_pool" or intent.settled_at is None:
        raise HTTPException(status_code=404, detail="No settled pool payment with that id")
    refund_id = f"rfd_{payload.request_id}"[:40]
    refund = db.get(models.Refund, refund_id)
    if refund is not None and refund.status == "credited_to_balance":
        return {"refund_id": refund.id, "status": refund.status, "amount": str(refund.amount)}

    try:
        reversed_ = httpx.post(
            f"{AUTH_URL}/internal/referral-pool/purchases/{payment_id}/reverse",
            json={"units": payload.units, "kind": payload.kind, "reason": payload.reason, "reference": refund_id},
            timeout=10,
        )
    except Exception as exc:
        log.error("pool refund %s: auth-service unreachable: %s", refund_id, exc)
        raise HTTPException(status_code=503, detail="Could not reach the pool. Nothing was refunded; try again.")
    if reversed_.status_code >= 400:
        detail = reversed_.json().get("detail") if reversed_.headers.get("content-type", "").startswith("application/json") else None
        raise HTTPException(status_code=reversed_.status_code if reversed_.status_code < 500 else 502,
                            detail=detail or "The pool refused the refund")
    unit_price = Decimal(str(reversed_.json()["unit_price"]))
    amount = economy.money(unit_price * payload.units)

    if refund is None:
        refund = models.Refund(
            id=refund_id, order_id=payment_id, user_id=intent.user_id, amount=amount, currency="USD",
            rail="kinjy_balance", custody_ref=intent.id, reason=f"{payload.kind}: {payload.reason}"[:500],
            status="pending",
        )
        db.add(refund)
        db.commit()
    try:
        httpx.post(
            f"{LEDGER_URL}/internal/post/payment-refund",
            json={"payment_id": payment_id, "member_id": intent.user_id, "amount": str(amount),
                  "reason": f"{payload.units} pool unit(s), {payload.kind}: {payload.reason}",
                  "idempotency_key": f"pool-refund:{refund_id}"},
            timeout=10,
        ).raise_for_status()
    except Exception as exc:
        refund.status = "failed"
        refund.failure_reason = f"ledger: {exc}"[:200]
        db.commit()
        log.error("pool refund %s: units off but the ledger refused: %s", refund_id, exc)
        raise HTTPException(
            status_code=502,
            detail="The units are off, but the accounting step failed. Send the same refund again to finish it.",
        )
    refund.status = "credited_to_balance"
    refund.settled_at = datetime.now(timezone.utc)
    refund.failure_reason = None
    db.commit()
    notify(intent.user_id, "pool_refund", f"{usd_text(amount)} refunded to your Kinjy balance",
           body=f"{payload.units} referral pool unit(s) were refunded. You can withdraw the amount like any balance.",
           link="/earn")
    log.info("pool refund %s by %s: %s units, %s", refund_id, admin.user_id, payload.units, amount)
    return {"refund_id": refund.id, "status": refund.status, "amount": str(amount)}


def usd_text(amount: Decimal) -> str:
    return f"${amount:,.2f}"


@app.get("/admin/kyc", tags=["admin"])
def admin_kyc(
    _: AdminUser, status: str | None = None, year: int | None = None, limit: int = 100, offset: int = 0,
    db: OrmSession = Depends(get_db),
):
    """KinjyKYC records: fee, attempts and result per member and year. Only the
    result is held here - the documents stay with Kaluta KYC."""
    q = select(models.KycRecord)
    if status:
        q = q.where(models.KycRecord.status == status)
    if year:
        q = q.where(models.KycRecord.year == year)
    rows = db.scalars(q.order_by(models.KycRecord.id.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
    counts = dict(
        db.execute(
            select(models.KycRecord.status, func.count()).where(
                models.KycRecord.year == (year or date.today().year)
            ).group_by(models.KycRecord.status)
        ).all()
    )
    fees = db.scalar(
        select(func.count()).select_from(models.KycRecord).where(
            models.KycRecord.year == (year or date.today().year), models.KycRecord.fee_paid.is_(True)
        )
    ) or 0
    return {
        "year": year or date.today().year,
        "counts": {**counts, "fee_paid": fees},
        "items": [
            {
                "user_id": r.user_id, "year": r.year, "fee_paid": r.fee_paid, "fee_payment_id": r.fee_payment_id,
                "attempts": r.attempts, "max_attempts": r.MAX_ATTEMPTS_PER_YEAR, "status": r.status,
                "provider_reference": r.provider_reference,
            }
            for r in rows
        ],
    }


# --- escrow ----------------------------------------------------------------

class EscrowRefundIn(BaseModel):
    order_id: str
    buyer_id: str
    amount: Decimal = Field(gt=0)
    custody_ref: str | None = None
    reason: str = ""


@app.post("/internal/escrow/refund", tags=["internal"])
def escrow_refund(payload: EscrowRefundIn, db: OrmSession = Depends(get_db)):
    """Instruct the custodian to return money to the buyer.

    Called by commerce-service once a dispute resolves in the buyer's favour.
    Recorded as a row of its own so a refund that the rail never confirmed is
    visible rather than inferred from an absence.
    """
    refund = models.Refund(
        id=new_id("rfd"),
        order_id=payload.order_id,
        user_id=payload.buyer_id,
        amount=economy.money(payload.amount),
        custody_ref=payload.custody_ref,
        reason=payload.reason[:500],
        rail=rails.choose_rail(None, "USD"),
    )
    db.add(refund)
    db.flush()

    try:
        result = rails.refund_from_custody(
            amount=refund.amount,
            user_id=payload.buyer_id,
            custody_ref=payload.custody_ref,
            reference=refund.id,
        )
        refund.status = result.get("status", "submitted")
        refund.external_id = result.get("id")
        refund.settled_at = datetime.now(timezone.utc) if refund.status == "finished" else None
    except Exception as exc:
        refund.status = "failed"
        refund.failure_reason = str(exc)[:200]
        db.commit()
        log.error("custodial refund failed for order %s: %s", payload.order_id, exc)
        raise HTTPException(status_code=502, detail="The custodian refused the refund instruction")

    db.commit()
    return {"refund_id": refund.id, "status": refund.status, "amount": str(refund.amount)}


@app.get("/admin/refunds", tags=["admin"])
def list_refunds(_: AdminUser, status: str | None = None, limit: int = 100, db: OrmSession = Depends(get_db)):
    stmt = select(models.Refund).order_by(models.Refund.created_at.desc())
    if status:
        stmt = stmt.where(models.Refund.status == status)
    rows = db.scalars(stmt.limit(min(limit, 500))).all()
    return {
        "items": [
            {
                "id": r.id,
                "order_id": r.order_id,
                "user_id": r.user_id,
                "amount": str(r.amount),
                "status": r.status,
                "rail": r.rail,
                "failure_reason": r.failure_reason,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


# --- payout destinations ---------------------------------------------------

@app.post("/payments/destinations", status_code=201, tags=["payouts"])
def add_destination(payload: DestinationIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    # Where money would be sent. A payout destination on a minor's account is
    # the step before a minor being paid, so it is gated at the same age.
    agefeatures.require(principal.user_id, "monetization")
    if sonco.enabled():
        raise HTTPException(status_code=410, detail="Use PUT /payments/payout-wallet: it is confirmed by e-mail, then by an admin")

    existing = db.scalar(
        select(models.PayoutDestination).where(
            models.PayoutDestination.user_id == principal.user_id,
            models.PayoutDestination.rail == payload.rail,
        )
    )
    if existing:
        existing.address = payload.address
        existing.currency = payload.currency
        existing.label = payload.label
        # Changing the address drops the whitelist flag: a freshly-typed address
        # must be re-whitelisted with the provider before it can receive funds.
        existing.whitelisted = False
        destination = existing
    else:
        destination = models.PayoutDestination(user_id=principal.user_id, **payload.model_dump())
        db.add(destination)
    db.commit()
    return {
        "rail": destination.rail,
        "address": destination.address,
        "whitelisted": destination.whitelisted,
        "note": "The address must be whitelisted with the provider before the first payout.",
    }


@app.get("/payments/eligibility", tags=["payouts"])
def eligibility(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """The two gates plus the threshold, in one answer the UI can act on."""
    stmt = select(models.PayoutDestination).where(models.PayoutDestination.user_id == principal.user_id)
    if sonco.enabled():
        stmt = stmt.where(
            models.PayoutDestination.rail == payouts.RAIL, models.PayoutDestination.status == "confirmed"
        )
    destination = db.scalar(stmt)
    try:
        response = httpx.get(
            f"{LEDGER_URL}/internal/payout-candidates", timeout=6
        )
        response.raise_for_status()
        candidates = {item["member_id"]: Decimal(item["amount"]) for item in response.json()["items"]}
        accrued = candidates.get(principal.user_id, Decimal("0"))
    except Exception:
        accrued = Decimal("0")

    eligible, blocker = economy.is_payout_eligible(
        accrued_usd=accrued,
        kyc_verified=principal.kyc_verified,
        wallet_on_file=destination is not None,
    )
    return {
        "eligible": eligible,
        "blocked_by": blocker,
        "accrued_usd": str(accrued),
        "threshold_usd": str(settings.PAYOUT_THRESHOLD_USD),
        "kyc_verified": principal.kyc_verified,
        "wallet_on_file": destination is not None,
        "action_required": None if eligible else {
            "kyc_required": "Complete KinjyKYC verification",
            "wallet_required": "Add a payout wallet address",
            "below_threshold": f"Reach ${settings.PAYOUT_THRESHOLD_USD} in accrued commissions",
        }.get(blocker or ""),
    }


@app.post("/admin/payouts/batch", tags=["payouts"])
def build_batch(admin: AdminUser, db: OrmSession = Depends(get_db)):
    """Build and submit a payout batch from everyone who clears all three gates."""
    if sonco.enabled():
        # Members withdraw themselves now, through a reserve in the ledger.
        # This batch never debited the ledger, so running it as well would pay
        # the same commissions twice.
        raise HTTPException(status_code=410, detail="Payouts are member withdrawals through Sonco Pay now")
    try:
        response = httpx.get(f"{LEDGER_URL}/internal/payout-candidates", timeout=15)
        response.raise_for_status()
        candidates = response.json()["items"]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not load payout candidates: {exc}")

    included, skipped = [], []
    for candidate in candidates:
        member_id = candidate["member_id"]
        destination = db.scalar(
            select(models.PayoutDestination).where(models.PayoutDestination.user_id == member_id)
        )
        try:
            user = httpx.get(f"{AUTH_URL}/internal/users/{member_id}", timeout=5).json()
            kyc_ok = bool(user.get("kyc_verified"))
        except Exception:
            kyc_ok = False

        ok, blocker = economy.is_payout_eligible(
            accrued_usd=Decimal(candidate["amount"]),
            kyc_verified=kyc_ok,
            wallet_on_file=destination is not None,
        )
        if ok:
            included.append(
                {"address": destination.address, "currency": destination.currency, "amount": candidate["amount"]}
            )
        else:
            skipped.append({"member_id": member_id, "reason": blocker})

    if not included:
        return {"submitted": False, "included": 0, "skipped": skipped}

    try:
        result = rails.mass_payout(included)
    except rails.RailError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "submitted": True,
        "payout_id": result.get("payout_id"),
        "mock": result.get("mock", False),
        "included": len(included),
        "skipped": skipped,
        "next_step": "Verify the payout with 2FA at the provider before funds move.",
    }


# --- KYC -------------------------------------------------------------------

def _apply_kaluta(record: models.KycRecord, db: OrmSession) -> None:
    """Read the session back from Kaluta and act on that answer only."""
    if record.status != "pending" or not record.provider_reference or not kaluta.enabled():
        return
    try:
        state = kaluta.read_session(record.provider_reference)
    except kaluta.KalutaError as exc:
        log.warning("kaluta session %s unreadable: %s", record.provider_reference, exc)
        return
    status = str(state.get("status") or "").lower()
    if status == kaluta.APPROVED:
        _set_outcome(record, "verified", db)
        notify(record.user_id, "kyc_verified", "Your identity is verified", link="/dashboard?tab=earnings")
    elif status == kaluta.REJECTED:
        _set_outcome(record, "rejected", db)
        notify(record.user_id, "kyc_rejected", "Your identity check did not pass",
               body="You can try again from the verification page.", link="/dashboard?tab=verification")
    elif status == kaluta.EXPIRED:
        # Not a failed check: the session lapsed. The attempt is given back.
        record.status = "unverified"
        record.attempts = max(0, record.attempts - 1)
        record.verification_url = None
        record.session_expires_at = None
        db.commit()


@app.get("/kyc/status", tags=["kyc"])
def kyc_status(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    year = date.today().year
    record = db.scalar(
        select(models.KycRecord).where(
            models.KycRecord.user_id == principal.user_id, models.KycRecord.year == year
        )
    )
    if record is not None and record.status == "pending":
        _apply_kaluta(record, db)
    if record is None:
        return {
            "year": year,
            "fee_paid": False,
            "status": "unverified",
            "attempts_used": 0,
            "attempts_allowed": models.KycRecord.MAX_ATTEMPTS_PER_YEAR,
            "fee_usd": str(settings.KYC_ANNUAL_FEE_USD),
        }
    return {
        "year": year,
        "fee_paid": record.fee_paid,
        "status": record.status,
        "attempts_used": record.attempts,
        "attempts_allowed": models.KycRecord.MAX_ATTEMPTS_PER_YEAR,
        "verified_on": record.verified_on,
        "expires_on": record.expires_on,
        "fee_usd": str(settings.KYC_ANNUAL_FEE_USD),
    }


@app.post("/kyc/start", tags=["kyc"])
def kyc_start(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Begin an identity check. Requires the fee to be paid for the current year
    and leaves attempts intact if the provider is unreachable."""
    year = date.today().year
    record = db.scalar(
        select(models.KycRecord).where(
            models.KycRecord.user_id == principal.user_id, models.KycRecord.year == year
        )
    )
    if record is None or not record.fee_paid:
        raise HTTPException(
            status_code=402,
            detail=f"Pay the ${settings.KYC_ANNUAL_FEE_USD} annual verification fee first",
        )
    if record.status == "verified":
        return {"status": "verified", "already": True}
    if not kaluta.enabled():
        raise HTTPException(status_code=503, detail="Identity verification is not available yet")

    now = datetime.now(timezone.utc)
    if (record.status == "pending" and record.verification_url
            and record.session_expires_at and record.session_expires_at > now):
        # A session is already open: reopen it, it costs no attempt.
        _apply_kaluta(record, db)
        if record.status == "pending":
            return {"status": "pending", "attempt": record.attempts,
                    "verification_url": record.verification_url, "resumed": True}
        if record.status == "verified":
            return {"status": "verified", "already": True}
    if record.attempts >= models.KycRecord.MAX_ATTEMPTS_PER_YEAR:
        raise HTTPException(
            status_code=429,
            detail=f"All {models.KycRecord.MAX_ATTEMPTS_PER_YEAR} verification attempts for {year} are used",
        )

    identity = payouts._identity(principal.user_id)
    first, last = kaluta.split_name(identity.get("display_name"))
    try:
        session = kaluta.start_session(
            external_id=f"{principal.user_id}:{year}:{record.attempts + 1}",
            redirect_url=f"{settings.FRONTEND_URL.rstrip('/')}/dashboard?tab=verification&kyc=done",
            first_name=first, last_name=last,
            date_of_birth=identity.get("date_of_birth"), country=identity.get("country"),
        )
    except kaluta.KalutaError as exc:
        # The attempt is not spent: the member did not get to try.
        log.error("kaluta session for %s failed: %s", principal.user_id, exc)
        raise HTTPException(status_code=503, detail="The verification service is unavailable right now. Try again in a moment.")

    record.attempts += 1
    record.status = "pending"
    record.provider_reference = str(session["session_id"])
    record.verification_url = session["verification_url"]
    try:
        record.session_expires_at = datetime.fromisoformat(str(session.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        record.session_expires_at = now + timedelta(hours=1)
    db.commit()
    return {
        "status": "pending",
        "attempt": record.attempts,
        "verification_url": record.verification_url,
        "note": "KinjyKYC stores only the verification result on-platform, never the documents.",
    }


@app.post("/kyc/webhook/kaluta", tags=["kyc"])
async def kaluta_webhook(request: Request, db: OrmSession = Depends(get_db)):
    """A doorbell, not a verdict: Kaluta's session webhooks are not signed.

    The session is looked up in Kinjy's own records - an invented id matches
    nobody - and its real state is read back from Kaluta before anything
    changes. Somebody posting "approved" gets, at most, a read of the truth.
    """
    raw = await request.body()
    # Signed (a company webhook): the signature must be right. Unsigned (a
    # session webhook): accepted as a doorbell. Either way, nothing below
    # trusts the body - the session is read back from Kaluta.
    if kaluta.signature_ok(raw, request.headers.get("X-Kaluta-Signature")) is False:
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Unreadable body")
    session_id = body.get("session_id") or body.get("id") or (body.get("data") or {}).get("session_id")
    if not session_id:
        return {"ok": True, "ignored": True}
    record = db.scalar(
        select(models.KycRecord).where(models.KycRecord.provider_reference == str(session_id))
    )
    if record is None:
        return {"ok": True, "ignored": True}
    _apply_kaluta(record, db)
    return {"ok": True}


@app.get("/internal/kyc/{user_id}", tags=["internal"])
def kyc_fee_of(user_id: str, db: OrmSession = Depends(get_db)):
    """Whether this member paid this year's verification fee, for other services.

    community-service asks it before a fourth pinned group. Paying is what is
    asked, not passing: the fee is the commitment, the check is the provider's.
    """
    year = date.today().year
    record = db.scalar(
        select(models.KycRecord).where(models.KycRecord.user_id == user_id, models.KycRecord.year == year)
    )
    return {
        "year": year,
        "fee_paid": bool(record and record.fee_paid),
        "status": record.status if record else "unverified",
        "fee_usd": str(settings.KYC_ANNUAL_FEE_USD),
    }


@app.post("/internal/kyc/callback", tags=["internal"])
def kyc_callback(user_id: str, outcome: str, db: OrmSession = Depends(get_db)):
    """Provider result. Propagates the verified flag to auth-service so it lands
    in the next access token."""
    if outcome not in ("verified", "rejected"):
        raise HTTPException(status_code=400, detail="outcome must be verified or rejected")

    year = date.today().year
    record = db.scalar(
        select(models.KycRecord).where(models.KycRecord.user_id == user_id, models.KycRecord.year == year)
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No KYC record for this year")
    _set_outcome(record, outcome, db)
    return {"user_id": user_id, "status": record.status, "expires_on": record.expires_on}


def _set_outcome(record: models.KycRecord, outcome: str, db: OrmSession) -> None:
    year = record.year
    user_id = record.user_id
    record.status = outcome
    record.verification_url = None
    record.session_expires_at = None
    if outcome == "verified":
        record.verified_on = date.today()
        record.expires_on = date(year + 1, date.today().month, date.today().day)
        try:
            httpx.post(
                f"{AUTH_URL}/internal/users/{user_id}/kyc",
                params={"verified": True, "until": record.expires_on.isoformat()},
                timeout=6,
            ).raise_for_status()
        except Exception as exc:
            log.error("could not propagate KYC status for %s: %s", user_id, exc)
    db.commit()
