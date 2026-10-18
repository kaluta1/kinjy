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
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from common import economy, events, settings
from common import agefeatures
from common.auth import AdminUser, CurrentUser
from common.database import get_db
from common.ids import new_id
from common.notify import notify
from common.service import create_app

import kaluta
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
]


def _start_reconciler():
    # Withdrawals a webhook never closed - Sonco Pay down at the wrong moment,
    # a delivery lost - are read back every five minutes.
    asyncio.get_event_loop().create_task(payouts.reconcile_loop())


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
    "referral_pool": "Kinjy referral pool seat",
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

    # Sonco Pay, once configured, takes every payment: a client asking for
    # another rail does not get to pick a different door.
    rail = "soncopay" if sonco.enabled() else rails.choose_rail(
        payload.rail if payload.rail != "soncopay" else None, "USD"
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
        if rail == "soncopay":
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
    if sonco.enabled() or rails.nowpayments_enabled() or rails.mangopay_enabled():
        raise HTTPException(status_code=403, detail="A live payment rail is configured; mock settlement is disabled")

    intent = db.get(models.PaymentIntent, payment_id)
    if intent is None or intent.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    intent.status = "finished"
    intent.settled_at = datetime.now(timezone.utc)
    await _on_settled(intent, db)
    db.commit()
    return {"payment_id": payment_id, "status": intent.status, "mock": True}


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
    if intent.rail != "soncopay" or not intent.external_id:
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
    if intent.rail == "soncopay" and intent.settled_at is None and intent.status in ("waiting", "confirming"):
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
        return sonco.offer(intent.external_id)
    except sonco.SoncoError as exc:
        log.error("sonco pay methods for %s failed: %s", intent.id, exc)
        raise HTTPException(status_code=503, detail="The payment methods could not be loaded. Try again.")


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
        # Seat first, revenue second. If the seat cannot be granted — the pool
        # filled up between checkout and settlement — no revenue is recognised
        # and the payment is refunded instead of quietly kept.
        try:
            response = httpx.post(
                f"{AUTH_URL}/internal/referral-pool/seats",
                json={
                    "member_id": intent.user_id,
                    "amount": str(intent.amount),
                    "currency": "USD",
                    "payment_ref": intent.id,
                },
                timeout=8,
            )
            if response.status_code == 409:
                intent.status = "refund_due"
                log.error("referral pool full; payment %s must be refunded", intent.id)
            else:
                response.raise_for_status()
                httpx.post(
                    f"{LEDGER_URL}/internal/post/referral-pool-entry",
                    json={
                        "member_id": intent.user_id,
                        "amount": str(intent.amount),
                        "reference": intent.id,
                        "idempotency_key": f"payment:{intent.id}",
                    },
                    timeout=10,
                ).raise_for_status()
        except Exception as exc:
            log.error("referral pool seat failed for payment %s: %s", intent.id, exc)

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


@app.post("/payments/referral-pool/join", status_code=201, tags=["referral-pool"])
def join_referral_pool(
    principal: CurrentUser, rail: str | None = None, db: OrmSession = Depends(get_db)
):
    """Start payment for a seat.

    The pool is checked before taking money, and again at settlement: the gap
    between the two is exactly where the last seat gets sold to somebody else,
    and a member should not discover that after paying.
    """
    agefeatures.require(principal.user_id, "referral_pool")

    try:
        state = httpx.get(
            f"{AUTH_URL}/auth/referral-pool",
            headers={"X-Internal-For": principal.user_id},
            timeout=5,
        ).json()
    except Exception as exc:
        log.error("referral pool status unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="The referral pool is unavailable right now")

    if not state.get("open"):
        raise HTTPException(
            status_code=409,
            detail=f"The referral pool is full ({state.get('cap'):,} seats) and closed to new members.",
        )

    return checkout(
        CheckoutIn(
            purpose="referral_pool",
            amount=Decimal(str(settings.REFERRAL_POOL_ENTRY_USD)),
            reference="referral_pool",
            rail=rail,
            return_path="/earn",
        ),
        principal,
        db,
    )


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
