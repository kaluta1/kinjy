"""Find - and, with --apply, correct - sponsor commissions credited to a member
whose verification was not active when the commission was earned.

    docker exec kaluta-ledger python repair_commission_eligibility.py            # dry run: report only
    docker exec kaluta-ledger python repair_commission_eligibility.py --apply    # correct what is safe
    ... --member usr_X --member usr_Y        # only these sponsors
    ... --before-rule                        # also correct commissions earned before the rule existed

The rule (a sponsor earns only while verified) took effect with commit 18effec
on 2026-10-20 (COMMISSION_KYC_RULE_SINCE, to set to the real deployment date).
Commissions earned before it were paid under the rules of the time: they are
listed apart ("before_rule") and corrected only with --before-rule, because
applying the rule to them is a retroactive decision, not a fix.

"Earned" is what it has always been in the ledger: the moment the commission
was posted (``commissions.created_at``). The evidence is the verification
history kept by payment-service (``payment.kyc_records``: each year's
``verified_on`` .. ``expires_on``), not the member's current status: a
commission earned while verified stays, even if the verification has lapsed
since.

Each commission found is either
  * corrected: a correcting journal (the commission's liability and expense
    taken back; the amount booked to revenue:unclaimed_commission, as an
    unpaid commission is at posting), the commission marked reversed, the
    wallet reduced, and a withheld_commissions row as the audit trail; or
  * left for review, with the reason: verification evidence missing or
    contradictory, money already withdrawn (no negative balance is created),
    still held in escrow, or a Leaders month already paid (a finalised
    distribution is not rewritten).

Idempotent: a commission already corrected is skipped (the correcting journal
has a fixed idempotency key, and the commission is then marked reversed).
Unpaid Leaders snapshots of the months touched are recomputed; the live
standings are computed on read and need nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select, text

from common import settings
from common.database import SessionLocal
from common.ids import new_id

import models
import posting

RULE_SINCE = date.fromisoformat(__import__("os").getenv("COMMISSION_KYC_RULE_SINCE", "2026-10-20"))


def _windows(db, member_id: str) -> tuple[list[tuple[date, date | None, str]], bool]:
    """Verification periods [verified_on, expires_on) from payment's records,
    and whether any record exists at all."""
    rows = db.execute(
        text("SELECT status, verified_on, expires_on FROM payment.kyc_records WHERE user_id = :u"),
        {"u": member_id},
    ).all()
    windows = [(r.verified_on, r.expires_on, r.status) for r in rows if r.verified_on]
    return windows, bool(rows)


def _auth_verified(db, member_id: str) -> bool:
    row = db.execute(text("SELECT kyc_verified FROM auth.users WHERE id = :u"), {"u": member_id}).first()
    return bool(row and row.kyc_verified)


def _assess(db, c: models.Commission) -> dict:
    earned = c.created_at if c.created_at.tzinfo else c.created_at.replace(tzinfo=timezone.utc)
    day = earned.date()
    windows, any_record = _windows(db, c.member_id)
    covering = [w for w in windows if w[0] <= day and (w[1] is None or day < w[1])]
    evidence = [{"verified_on": str(w[0]), "expires_on": str(w[1]) if w[1] else None, "status": w[2]} for w in windows]
    base = {
        "commission_id": c.id, "member_id": c.member_id, "journal_id": c.journal_id, "source_kind": c.source_kind,
        "source_ref": c.source_ref, "amount": str(c.amount), "earned_at": earned.isoformat(), "evidence": evidence,
    }
    base["before_rule"] = day < RULE_SINCE
    if covering:
        return {**base, "eligible": True}
    if not any_record and _auth_verified(db, c.member_id):
        # Marked verified with nothing behind it (an import, a manual change): not guessed.
        return {**base, "eligible": None, "review": "verified flag without a verification record"}
    return {**base, "eligible": False}


def _period(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _blocker(db, c: models.Commission, journal: models.Journal) -> str | None:
    """Why this one must not be corrected automatically, if it must not."""
    if journal.funds_released_at is None and journal.kind == "marketplace_order":
        return "still held with the order (pending): correct after settlement"
    paid = db.scalar(select(models.LeaderSnapshot.id).where(
        models.LeaderSnapshot.period == _period(c.created_at), models.LeaderSnapshot.paid.is_(True)).limit(1))
    if paid:
        return f"Leaders month {_period(c.created_at)} already paid out: finalised distribution not rewritten"
    wallet = posting.get_wallet(db, c.member_id, c.currency)
    if wallet.available < c.amount:
        return f"only {wallet.available} left in the wallet (withdrawn?): no negative balance created"
    return None


def _correct(db, c: models.Commission, journal: models.Journal) -> models.Journal:
    key = f"repair-commission:{c.id}"
    existing = posting.find_existing(db, key)
    if existing is not None:
        return existing
    revenue = db.scalar(select(models.Entry.account).where(
        models.Entry.journal_id == journal.id, models.Entry.account.like("revenue:%"),
        models.Entry.account != models.ACC_UNCLAIMED, models.Entry.credit > 0).limit(1)) or models.ACC_REVENUE_MARGIN
    fix = models.Journal(
        id=new_id("jrn"), kind="commission_correction", reference=journal.reference,
        description=f"Commission {c.id} withheld: sponsor not verified when it was earned",
        currency=c.currency, amount=c.amount, reverses_journal_id=journal.id, idempotency_key=key,
        created_by="repair",
    )
    db.add(fix)
    db.flush()
    reason = "repair_unverified_at_earning"
    for account, debit, credit, beneficiary, role in (
        (models.ACC_MEMBER_PAYABLE, c.amount, Decimal("0"), c.member_id, "sponsor"),  # no longer owed to them
        (models.ACC_EXPENSE_AFFILIATE, Decimal("0"), c.amount, None, None),           # nor an affiliate cost
        (revenue, c.amount, Decimal("0"), None, None),                                # booked as Kinjy's
        (models.ACC_UNCLAIMED, Decimal("0"), c.amount, None, None),                   #   unclaimed commission
    ):
        db.add(models.Entry(id=new_id("led"), journal_id=fix.id, account=account, debit=debit, credit=credit,
                            beneficiary_id=beneficiary, role=role, currency=c.currency, reason=reason))
    wallet = posting.get_wallet(db, c.member_id, c.currency)
    wallet.available -= c.amount
    wallet.lifetime_earned -= c.amount
    c.status = "reversed"
    db.add(models.WithheldCommission(
        journal_id=journal.id, member_id=c.member_id, amount=c.amount, currency=c.currency,
        source_kind=c.source_kind, source_ref=c.source_ref, reason=reason, commission_id=c.id,
        correction_journal_id=fix.id,
    ))
    posting.assert_balanced(db, fix.id)
    return fix


def run(apply: bool, members: list[str] | None = None, before_rule: bool = False) -> dict:
    report: dict = {"mode": "apply" if apply else "dry-run", "rule_since": str(RULE_SINCE),
                    "corrected": [], "to_correct": [], "before_rule": [], "review": [],
                    "already_corrected": 0, "eligible": 0, "periods_recomputed": []}
    if not settings.COMMISSION_REQUIRES_KYC:
        report["note"] = "COMMISSION_REQUIRES_KYC is off on this server: no commission is ineligible."
        return report
    db = SessionLocal()
    try:
        touched: set[str] = set()
        q = select(models.Commission).where(models.Commission.role == "sponsor", models.Commission.status != "reversed")
        if members:
            q = q.where(models.Commission.member_id.in_(members))
        rows = db.scalars(q.order_by(models.Commission.created_at)).all()
        for c in rows:
            found = _assess(db, c)
            if found["eligible"]:
                report["eligible"] += 1
                continue
            if found["eligible"] is None:
                report["review"].append(found)
                continue
            journal = db.get(models.Journal, c.journal_id)
            if posting.find_existing(db, f"repair-commission:{c.id}") is not None:
                report["already_corrected"] += 1
                continue
            if found["before_rule"] and not before_rule:
                report["before_rule"].append(found)
                continue
            why = _blocker(db, c, journal)
            if why:
                report["review"].append({**found, "review": why})
                continue
            if apply:
                fix = _correct(db, c, journal)
                db.commit()
                touched.add(_period(c.created_at))
                report["corrected"].append({**found, "correction_journal_id": fix.id})
            else:
                report["to_correct"].append(found)
        if apply and touched:
            import main  # the snapshot logic, unchanged
            for period in sorted(touched):
                snapshot = db.scalars(select(models.LeaderSnapshot).where(models.LeaderSnapshot.period == period)).all()
                if snapshot and not any(r.paid for r in snapshot):
                    main.leaders_snapshot(period, SimpleNamespace(user_id="repair"), db)
                    report["periods_recomputed"].append(period)
        report["totals"] = {
            k: str(sum((Decimal(x["amount"]) for x in report[k]), Decimal("0")))
            for k in ("corrected", "to_correct", "before_rule", "review")
        }
        return report
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="correct what is safe (default: report only)")
    parser.add_argument("--member", action="append", help="only this sponsor (repeatable)")
    parser.add_argument("--before-rule", action="store_true", help="also correct commissions earned before the rule")
    args = parser.parse_args()
    print(json.dumps(run(args.apply, args.member, args.before_rule), indent=2, default=str))
    sys.exit(0)
