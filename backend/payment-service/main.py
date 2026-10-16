"""Kinjy · payment-service — checkout into custody, KYC, refunds, payout batches.

A buyer paying for an order is not paying Kinjy. The money goes to the
custodian — an authorised financial institution holding it in a segregated
client-money account — and is released to the seller only once the buyer
confirms receipt or a dispute is resolved. This service is what moves money in
and out of that account; commerce-service decides when.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
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
from common.service import create_app

import models
import rails

log = logging.getLogger("payment-service")
AUTH_URL = "http://auth-service:8000"
LEDGER_URL = "http://ledger-service:8000"
COMMERCE_URL = "http://commerce-service:8000"

app = create_app(
    name="payment-service",
    schema=models.SCHEMA,
    description="Checkout into custody, refunds, KinjyKYC, member payout batches.",
)


class CheckoutIn(BaseModel):
    purpose: str = Field(pattern="^(order|subscription|ad_credit|kyc_fee|tribute|referral_pool)$")
    amount: Decimal = Field(gt=0)
    reference: str | None = None
    rail: str | None = Field(default=None, pattern="^(nowpayments|mangopay|mock)$")
    pay_currency: str = "usdtbsc"


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

    rail = rails.choose_rail(payload.rail, "USD")
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
        if rail == "mangopay":
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
    if rails.nowpayments_enabled() or rails.mangopay_enabled():
        raise HTTPException(status_code=403, detail="A live payment rail is configured; mock settlement is disabled")

    intent = db.get(models.PaymentIntent, payment_id)
    if intent is None or intent.user_id != principal.user_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    intent.status = "finished"
    intent.settled_at = datetime.now(timezone.utc)
    await _on_settled(intent, db)
    db.commit()
    return {"payment_id": payment_id, "status": intent.status, "mock": True}


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
    destination = db.scalar(
        select(models.PayoutDestination).where(models.PayoutDestination.user_id == principal.user_id)
    )
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

@app.get("/kyc/status", tags=["kyc"])
def kyc_status(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    year = date.today().year
    record = db.scalar(
        select(models.KycRecord).where(
            models.KycRecord.user_id == principal.user_id, models.KycRecord.year == year
        )
    )
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
    if record.attempts >= models.KycRecord.MAX_ATTEMPTS_PER_YEAR:
        raise HTTPException(
            status_code=429,
            detail=f"All {models.KycRecord.MAX_ATTEMPTS_PER_YEAR} verification attempts for {year} are used",
        )

    record.attempts += 1
    record.status = "pending"
    record.provider_reference = f"kyc_{new_id('kv')}"
    db.commit()
    return {
        "status": "pending",
        "attempt": record.attempts,
        "provider_reference": record.provider_reference,
        "note": "KinjyKYC stores only the verification result on-platform, never the documents.",
    }


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

    record.status = outcome
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
    return {"user_id": user_id, "status": record.status, "expires_on": record.expires_on}
