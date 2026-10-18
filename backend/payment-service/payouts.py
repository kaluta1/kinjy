"""The payout wallet and withdrawals: commissions out, in USDT on BSC.

The wallet goes through two checks before a withdrawal may use it:

1. **The member confirms it from their e-mail.** A session that was stolen,
   or a phone left unlocked, must not be enough to send somebody's earnings
   to another address.
2. **An admin approves it**, and which admin is kept with the wallet. An admin
   cannot approve their own.

Changing the address starts both checks again, and withdrawals wait until it
is approved.

A withdrawal is, in this order: a row (one in flight per member, enforced by
the database), the amount reserved in the ledger under the withdrawal id, then
a Sonco Pay payout whose reference is that same id. Each step can be run again
without doing anything twice, which is what makes it safe to retry the whole
thing after a timeout. The amount goes back to the member only when Sonco Pay
has said no or that the payout failed - never on a timeout, when the money may
be on its way.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from common import agefeatures, economy, mailer, settings
from common.auth import AdminUser, CurrentUser
from common.database import SessionLocal, get_db
from common.ids import new_id
from common.notify import notify, notify_many

import models
import sonco

log = logging.getLogger("payment-service.payouts")
router = APIRouter()

AUTH_URL = "http://auth-service:8000"
LEDGER_URL = "http://ledger-service:8000"

RAIL = "soncopay"
BSC_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
RESEND_AFTER = timedelta(seconds=60)
IN_FLIGHT = ("reserved", "submitted")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _identity(user_id: str) -> dict:
    try:
        response = httpx.get(f"{AUTH_URL}/internal/users/{user_id}/identity", timeout=6)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log.error("identity of %s unavailable: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Your account details are unavailable right now")


def _wallet(db: OrmSession, user_id: str, *, lock: bool = False) -> models.PayoutDestination | None:
    stmt = select(models.PayoutDestination).where(
        models.PayoutDestination.user_id == user_id, models.PayoutDestination.rail == RAIL
    )
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def _wallet_out(w: models.PayoutDestination | None) -> dict | None:
    if w is None:
        return None
    return {
        "id": w.id,
        "address": w.address,
        "asset": settings.PAYOUT_ASSET,
        "network": settings.PAYOUT_NETWORK,
        "status": w.status,
        "email_confirmed_at": w.email_confirmed_at,
        "reviewed_by_name": w.reviewed_by_name,
        "reviewed_at": w.reviewed_at,
        "review_note": w.review_note if w.status == "rejected" else None,
        "updated_at": w.updated_at or w.created_at,
    }


def _send_confirmation(w: models.PayoutDestination, identity: dict) -> None:
    """Mail the link, or raise. The token is set here and nowhere is it logged."""
    if not mailer.configured():
        raise HTTPException(status_code=503, detail="E-mail is not available, so the wallet cannot be confirmed yet")
    token = secrets.token_urlsafe(32)
    w.confirm_token_hash = _hash(token)
    w.confirm_expires_at = _now() + timedelta(hours=settings.PAYOUT_WALLET_CONFIRM_HOURS)
    link = f"{settings.FRONTEND_URL.rstrip('/')}/payout-wallet/confirm?token={token}"
    short = f"{w.address[:6]}…{w.address[-4:]}"
    body = (
        f"Hello {identity.get('display_name') or ''},\n\n"
        f"Somebody - we hope you - asked Kinjy to pay your commissions to this wallet:\n\n"
        f"    {w.address}\n    {settings.PAYOUT_ASSET} on {settings.PAYOUT_NETWORK}\n\n"
        f"If it was you, confirm it here (the link works for {settings.PAYOUT_WALLET_CONFIRM_HOURS} hours):\n\n"
        f"    {link}\n\n"
        f"An admin then checks it, and withdrawals open once it is approved.\n\n"
        f"If it was not you, do not click the link, and change your password: "
        f"nothing is paid to {short} unless you confirm it.\n\n"
        f"- Kinjy"
    )
    try:
        mailer.send(identity["email"], "Confirm your Kinjy payout wallet", body)
    except mailer.MailError as exc:
        log.error("wallet confirmation mail to %s failed: %s", w.user_id, exc)
        raise HTTPException(status_code=503, detail="The confirmation e-mail could not be sent. Try again in a moment.")


def _staff_ids() -> list[str]:
    try:
        response = httpx.get(f"{AUTH_URL}/internal/staff", params={"min_role": "admin"}, timeout=5)
        response.raise_for_status()
        return [row["user_id"] for row in response.json().get("items", [])]
    except Exception as exc:
        log.warning("could not list admins to notify: %s", exc)
        return []


# --- the member's wallet ----------------------------------------------------------

class WalletIn(BaseModel):
    address: str = Field(min_length=42, max_length=42)


class ConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.get("/payments/payout-wallet", tags=["payouts"])
def my_payout_wallet(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    return {
        "wallet": _wallet_out(_wallet(db, principal.user_id)),
        "asset": settings.PAYOUT_ASSET,
        "network": settings.PAYOUT_NETWORK,
    }


@router.put("/payments/payout-wallet", tags=["payouts"])
def set_payout_wallet(payload: WalletIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Save the wallet and mail the confirmation link.

    The same address again changes nothing, so saving twice does not undo an
    approval. A different one goes back to the start.
    """
    agefeatures.require(principal.user_id, "monetization")
    address = payload.address.strip()
    if not BSC_ADDRESS.match(address):
        raise HTTPException(status_code=422, detail="That is not a BSC address: it starts with 0x and has 40 characters after it")

    w = _wallet(db, principal.user_id, lock=True)
    if w is not None and w.address.lower() == address.lower() and w.status != "rejected":
        return {"wallet": _wallet_out(w), "changed": False}

    identity = _identity(principal.user_id)
    if not identity.get("email"):
        raise HTTPException(status_code=409, detail="Your account has no e-mail address to confirm the wallet with")

    if w is None:
        w = models.PayoutDestination(
            user_id=principal.user_id, rail=RAIL, address=address,
            currency=f"{settings.PAYOUT_ASSET}{settings.PAYOUT_NETWORK}".lower(), is_default=True,
        )
        db.add(w)
    w.address = address
    w.status = "pending_email"
    w.whitelisted = False
    w.email_confirmed_at = None
    w.reviewed_by = w.reviewed_by_name = w.reviewed_at = w.review_note = None
    w.updated_at = _now()
    _send_confirmation(w, identity)
    db.commit()
    return {"wallet": _wallet_out(w), "changed": True}


@router.post("/payments/payout-wallet/resend", tags=["payouts"])
def resend_confirmation(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    w = _wallet(db, principal.user_id, lock=True)
    if w is None or w.status != "pending_email":
        raise HTTPException(status_code=409, detail="There is no wallet waiting for e-mail confirmation")
    if w.updated_at and _now() - w.updated_at < RESEND_AFTER:
        raise HTTPException(status_code=429, detail="An e-mail was just sent. Wait a minute before asking again.")
    _send_confirmation(w, _identity(principal.user_id))
    w.updated_at = _now()
    db.commit()
    return {"sent": True}


@router.post("/payments/payout-wallet/confirm", tags=["payouts"])
def confirm_wallet(payload: ConfirmIn, db: OrmSession = Depends(get_db)):
    """The link from the e-mail. No sign-in needed: having the mail is the proof.

    One use: the token is cleared, so a forwarded or leaked link does nothing
    once it has been clicked.
    """
    w = db.scalar(
        select(models.PayoutDestination)
        .where(models.PayoutDestination.confirm_token_hash == _hash(payload.token))
        .with_for_update()
    )
    if w is None or w.status != "pending_email":
        raise HTTPException(status_code=404, detail="This link is not valid any more. Save the wallet again to get a new one.")
    if w.confirm_expires_at is None or w.confirm_expires_at < _now():
        raise HTTPException(status_code=410, detail="This link has expired. Ask for a new one from your earnings page.")
    w.status = "pending_admin"
    w.email_confirmed_at = _now()
    w.confirm_token_hash = None
    w.confirm_expires_at = None
    w.updated_at = _now()
    db.commit()
    notify_many(
        _staff_ids(), "payout_wallet_review", "A payout wallet is waiting for approval",
        link="/admin/wallets",
    )
    return {"wallet": _wallet_out(w)}


# --- admin review -----------------------------------------------------------------

class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


@router.get("/admin/payouts/wallets", tags=["admin"])
def wallets_to_review(_: AdminUser, status: str = "pending_admin", db: OrmSession = Depends(get_db)):
    stmt = select(models.PayoutDestination).where(models.PayoutDestination.rail == RAIL)
    if status != "all":
        stmt = stmt.where(models.PayoutDestination.status == status)
    rows = db.scalars(stmt.order_by(models.PayoutDestination.updated_at.desc().nullslast()).limit(200)).all()
    items = []
    for w in rows:
        try:
            who = httpx.get(f"{AUTH_URL}/internal/users/{w.user_id}", timeout=4).json()
        except Exception:
            who = {}
        items.append({
            **_wallet_out(w),
            "user_id": w.user_id,
            "display_name": who.get("display_name"),
            "handle": who.get("handle"),
            "kyc_verified": who.get("kyc_verified"),
            "reviewed_by": w.reviewed_by,
        })
    return {"items": items}


def _review(wallet_id: int, admin, approve: bool, note: str | None, db: OrmSession) -> dict:
    w = db.scalar(
        select(models.PayoutDestination)
        .where(models.PayoutDestination.id == wallet_id, models.PayoutDestination.rail == RAIL)
        .with_for_update()
    )
    if w is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    if w.user_id == admin.user_id:
        raise HTTPException(status_code=403, detail="Another admin has to review your own wallet")
    if w.status != "pending_admin":
        raise HTTPException(status_code=409, detail="This wallet is not waiting for review (the member may have changed it)")
    try:
        admin_name = httpx.get(f"{AUTH_URL}/internal/users/{admin.user_id}", timeout=4).json().get("display_name")
    except Exception:
        admin_name = None
    w.status = "confirmed" if approve else "rejected"
    w.whitelisted = approve
    w.reviewed_by = admin.user_id
    w.reviewed_by_name = (admin_name or admin.handle or admin.user_id)[:120]
    w.reviewed_at = _now()
    w.review_note = (note or "").strip()[:300] or None
    w.updated_at = _now()
    db.commit()
    if approve:
        notify(w.user_id, "payout_wallet_approved", "Your payout wallet is approved",
               body="You can withdraw your commissions now.", link="/dashboard?tab=earnings")
    else:
        notify(w.user_id, "payout_wallet_rejected", "Your payout wallet was not approved",
               body=w.review_note or "Check the address and save it again.", link="/dashboard?tab=earnings")
    return {"wallet": {**_wallet_out(w), "reviewed_by": w.reviewed_by}}


@router.post("/admin/payouts/wallets/{wallet_id}/approve", tags=["admin"])
def approve_wallet(wallet_id: int, admin: AdminUser, payload: ReviewIn | None = None, db: OrmSession = Depends(get_db)):
    return _review(wallet_id, admin, True, payload.note if payload else None, db)


@router.post("/admin/payouts/wallets/{wallet_id}/reject", tags=["admin"])
def reject_wallet(wallet_id: int, admin: AdminUser, payload: ReviewIn | None = None, db: OrmSession = Depends(get_db)):
    return _review(wallet_id, admin, False, payload.note if payload else None, db)


# --- withdrawals --------------------------------------------------------------------

def _available(user_id: str) -> Decimal:
    try:
        response = httpx.get(f"{LEDGER_URL}/internal/wallets/{user_id}", timeout=6)
        response.raise_for_status()
        return economy.money(response.json()["available"])
    except Exception as exc:
        log.error("balance of %s unavailable: %s", user_id, exc)
        raise HTTPException(status_code=503, detail="Your balance is unavailable right now")


def _split(gross: Decimal, fee: Decimal) -> tuple[Decimal, Decimal]:
    """(what is sent, what Kinjy's Sonco Pay balance pays on top) for a gross amount."""
    if settings.PAYOUT_FEE_PAID_BY == "platform":
        return gross, fee
    return economy.money(gross - fee), Decimal("0")


def _quote(principal, db: OrmSession) -> dict:
    available = _available(principal.user_id)
    wallet = _wallet(db, principal.user_id)
    identity = _identity(principal.user_id)
    blockers: list[str] = []
    fee = minimum_sent = None
    if not sonco.enabled():
        blockers.append("payouts_unavailable")
    else:
        try:
            terms = sonco.payout_terms()
            fee, minimum_sent = terms["fee"], terms["minimum"]
        except sonco.SoncoError:
            blockers.append("payouts_unavailable")
    # The member's balance must cover what is sent, plus the fee when it is
    # theirs - and never less than Kinjy's own threshold.
    minimum = economy.money(settings.PAYOUT_THRESHOLD_USD)
    if fee is not None:
        floor = minimum_sent + (fee if settings.PAYOUT_FEE_PAID_BY != "platform" else 0)
        minimum = max(minimum, economy.money(floor))
    if not identity.get("kyc_verified"):
        blockers.append("kyc_required")
    if wallet is None:
        blockers.append("wallet_required")
    elif wallet.status != "confirmed":
        blockers.append(f"wallet_{wallet.status}")
    if available < minimum:
        blockers.append("below_minimum")
    in_flight = db.scalar(
        select(models.Withdrawal).where(
            models.Withdrawal.user_id == principal.user_id, models.Withdrawal.status.in_(IN_FLIGHT)
        )
    )
    if in_flight is not None:
        blockers.append("withdrawal_in_progress")
    sent, extra = _split(available, fee or Decimal("0"))
    return {
        "available": str(available),
        "minimum": str(minimum),
        "fee": str(fee) if fee is not None else None,
        "fee_paid_by": settings.PAYOUT_FEE_PAID_BY,
        "you_receive": str(max(sent, Decimal("0"))) if fee is not None else None,
        "asset": settings.PAYOUT_ASSET,
        "network": settings.PAYOUT_NETWORK,
        "wallet": _wallet_out(wallet),
        "eligible": not blockers,
        "blocked_by": blockers,
        "_fee": fee,
        "_wallet": wallet,
    }


@router.get("/payments/withdraw/quote", tags=["payouts"])
def withdraw_quote(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    quote = _quote(principal, db)
    return {k: v for k, v in quote.items() if not k.startswith("_")}


def _withdrawal_out(w: models.Withdrawal) -> dict:
    return {
        "id": w.id,
        "amount": str(w.amount),
        "fee": str(w.fee),
        "net": str(w.net),
        "asset": w.asset,
        "network": w.network,
        "address": w.address,
        "status": w.status,
        "tx_hash": w.tx_hash,
        "failure_reason": w.failure_reason if w.status == "failed" else None,
        "created_at": w.created_at,
        "completed_at": w.completed_at,
    }


def _ledger(action: str, w: models.Withdrawal) -> httpx.Response:
    return httpx.post(
        f"{LEDGER_URL}/internal/payouts/{action}",
        json={"member_id": w.user_id, "amount": str(w.amount), "key": w.id},
        timeout=10,
    )


def _release(w: models.Withdrawal, reason: str, db: OrmSession) -> None:
    """Give the amount back and close the withdrawal as failed. Runs at most once in effect."""
    try:
        response = _ledger("release", w)
        if response.status_code not in (200, 404):
            response.raise_for_status()
    except Exception as exc:
        # Left in flight: the reconciler tries the release again. Closing it
        # as failed without the money back would lose the member's balance.
        log.error("release of %s failed, will retry: %s", w.id, exc)
        w.failure_reason = f"Refund pending: {reason}"[:300]
        w.updated_at = _now()
        db.commit()
        return
    w.status = "failed"
    w.failure_reason = reason[:300]
    w.updated_at = _now()
    db.commit()
    notify(w.user_id, "withdrawal_failed", "Your withdrawal did not go through",
           body=f"${w.amount} is back in your balance. {reason}"[:200], link="/dashboard?tab=earnings")


def _apply_payout(w: models.Withdrawal, payout: dict, db: OrmSession) -> None:
    """Bring the withdrawal in line with what Sonco Pay says about its payout."""
    if payout.get("id") and not w.soncopay_payout_id:
        w.soncopay_payout_id = str(payout["id"])
    status = str(payout.get("status") or "").lower()
    if status in sonco.PAYOUT_DONE:
        if w.status != "completed":
            w.status = "completed"
            w.tx_hash = payout.get("tx_hash") or w.tx_hash
            w.completed_at = _now()
            w.updated_at = _now()
            db.commit()
            notify(w.user_id, "withdrawal_completed", f"${w.net} {w.asset} is on its way to your wallet",
                   body=(f"Transaction {w.tx_hash}" if w.tx_hash else None), link="/dashboard?tab=earnings")
        elif payout.get("tx_hash") and not w.tx_hash:
            w.tx_hash = payout["tx_hash"]
            db.commit()
    elif status in sonco.PAYOUT_FAILED:
        if w.status in IN_FLIGHT:
            reason = payout.get("last_error") or payout.get("error") or payout.get("failure_reason")
            _release(w, str(reason or "The payout was refused by the payment provider."), db)
    else:
        if w.status == "reserved":
            w.status = "submitted"
        w.updated_at = _now()
        db.commit()


def _advance(w: models.Withdrawal, db: OrmSession) -> None:
    """Take an in-flight withdrawal one step further. Safe to call any number of times."""
    if w.status == "reserved":
        # Reserve again: the same key moves nothing if it already happened.
        try:
            response = _ledger("reserve", w)
        except Exception as exc:
            log.warning("ledger unreachable for %s: %s", w.id, exc)
            return
        if response.status_code == 409:
            # The balance was not there. Nothing was taken, nothing was sent.
            w.status = "failed"
            w.failure_reason = (response.json().get("detail") or "Insufficient balance")[:300]
            w.updated_at = _now()
            db.commit()
            return
        if response.status_code >= 400:
            log.error("ledger refused reserve of %s: %s", w.id, response.text[:200])
            return
        if response.json().get("status") == "released":
            # Already given back once - this withdrawal is over, whatever our
            # row says. Paying it now would pay money the member still has.
            w.status = "failed"
            w.failure_reason = w.failure_reason or "The payout did not go through."
            w.updated_at = _now()
            db.commit()
            return
        try:
            payout = sonco.create_payout(amount=w.net, address=w.address, reference=w.id)
        except sonco.SoncoError as exc:
            if exc.definite:
                log.error("sonco pay refused payout %s: %s (%s)", w.id, exc, exc.code)
                _release(w, "Withdrawals are unavailable right now. Try again later.", db)
            else:
                w.failure_reason = None
                w.updated_at = _now()
                db.commit()
            return
        _apply_payout(w, payout, db)
    elif w.status == "submitted":
        if w.failure_reason and w.failure_reason.startswith("Refund pending"):
            _release(w, w.failure_reason.removeprefix("Refund pending: "), db)
            return
        try:
            payout = (sonco.retrieve_payout(w.soncopay_payout_id) if w.soncopay_payout_id
                      else sonco.create_payout(amount=w.net, address=w.address, reference=w.id))
        except sonco.SoncoError as exc:
            log.warning("could not read payout of %s: %s", w.id, exc)
            return
        _apply_payout(w, payout, db)


@router.post("/payments/withdraw", status_code=201, tags=["payouts"])
def withdraw(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Withdraw the whole available balance to the approved wallet."""
    agefeatures.require(principal.user_id, "monetization")
    quote = _quote(principal, db)
    if not quote["eligible"]:
        raise HTTPException(status_code=409, detail={"blocked_by": quote["blocked_by"], "minimum": quote["minimum"]})
    gross = economy.money(quote["available"])
    sent, _ = _split(gross, quote["_fee"])
    wallet = quote["_wallet"]
    w = models.Withdrawal(
        id=new_id("wdr"), user_id=principal.user_id, destination_id=wallet.id,
        amount=gross, fee=quote["_fee"], net=sent,
        asset=settings.PAYOUT_ASSET, network=settings.PAYOUT_NETWORK, address=wallet.address,
        status="reserved",
    )
    db.add(w)
    try:
        db.commit()
    except IntegrityError:
        # The partial unique index: another withdrawal of this member got in first.
        db.rollback()
        raise HTTPException(status_code=409, detail={"blocked_by": ["withdrawal_in_progress"]})
    _advance(w, db)
    db.refresh(w)
    return {"withdrawal": _withdrawal_out(w)}


@router.get("/payments/withdrawals", tags=["payouts"])
def my_withdrawals(principal: CurrentUser, db: OrmSession = Depends(get_db)):
    rows = db.scalars(
        select(models.Withdrawal)
        .where(models.Withdrawal.user_id == principal.user_id)
        .order_by(models.Withdrawal.created_at.desc())
        .limit(50)
    ).all()
    # The one in flight, if any, is read back now: the member looking at the
    # page is when an answer matters, and webhooks can be late.
    for w in rows:
        if w.status in IN_FLIGHT and w.updated_at and _now() - w.updated_at > timedelta(seconds=20):
            _advance(w, db)
            db.refresh(w)
    return {"items": [_withdrawal_out(w) for w in rows]}


@router.get("/admin/payouts/withdrawals", tags=["admin"])
def all_withdrawals(_: AdminUser, status: str | None = None, db: OrmSession = Depends(get_db)):
    stmt = select(models.Withdrawal).order_by(models.Withdrawal.created_at.desc())
    if status:
        stmt = stmt.where(models.Withdrawal.status == status)
    rows = db.scalars(stmt.limit(200)).all()
    return {"items": [{**_withdrawal_out(w), "user_id": w.user_id, "soncopay_payout_id": w.soncopay_payout_id}
                      for w in rows]}


def reconcile_once() -> int:
    """Advance every withdrawal still in flight. Returns how many were looked at."""
    with SessionLocal() as db:
        rows = db.scalars(
            select(models.Withdrawal).where(models.Withdrawal.status.in_(IN_FLIGHT)).limit(100)
        ).all()
        for w in rows:
            try:
                _advance(w, db)
            except Exception as exc:
                db.rollback()
                log.error("reconcile of %s failed: %s", w.id, exc)
        return len(rows)


@router.post("/admin/payouts/withdrawals/reconcile", tags=["admin"])
def reconcile_now(_: AdminUser):
    return {"checked": reconcile_once()}


async def reconcile_loop() -> None:
    while True:
        await asyncio.sleep(300)
        if sonco.enabled():
            try:
                await asyncio.to_thread(reconcile_once)
            except Exception as exc:
                log.error("withdrawal reconciler: %s", exc)


def apply_event(event, db: OrmSession) -> bool:
    """A verified payout webhook. The payout is read back before anything moves."""
    data = event.data or {}
    w = None
    if data.get("reference"):
        w = db.get(models.Withdrawal, str(data["reference"]))
    if w is None and data.get("id"):
        w = db.scalar(select(models.Withdrawal).where(models.Withdrawal.soncopay_payout_id == str(data["id"])))
    if w is None:
        return False
    try:
        payout = sonco.retrieve_payout(str(data.get("id") or w.soncopay_payout_id))
    except sonco.SoncoError:
        payout = data
    _apply_payout(w, payout, db)
    return True
