"""The referral pool: units, rounds, and proportional rotation.

A member buys **units** at ``REFERRAL_POOL_ENTRY_USD`` each - several in one
payment, and more later. Every sign-up that arrives **without a valid personal
invitation** is assigned to a pool member, who becomes their sponsor with
exactly the same direct commission as a personal invitation.

A member's weight is their active, fully paid units. Units are weights, not
credits: receiving a referral does not use one up.

Allocation - weighted rotation without replacement
--------------------------------------------------
Sign-ups are dealt in **rounds**:

1. A round opens with a snapshot: each eligible member's active units become
   their quota for the round (A 1, B 3, C 6 -> a round of 10).
2. Each eligible sign-up draws one member among those with quota left, with a
   probability proportional to the quota they have left (secure randomness,
   ``secrets``), and that member's quota drops by one.
3. When no quota is left the round closes and the next one is snapshot.

Over a complete round with unchanged eligibility A gets exactly 1, B 3 and
C 6, in a random order. Independent draws *with* replacement would only get
there on average, and could drift for a long time; this cannot.

The draw never builds a list with one entry per unit: it is a cumulative sum
over members (a window function), and one secure random number below the
total picks the member whose interval contains it.

During a round
--------------
* Units bought during a round join at the **next** round boundary; the round
  in progress is never reset by a purchase.
* A refund or reversal of units that are in the snapshot takes the same
  number off the member's unused quota (never below what they already
  received). A member who stops being eligible (seat suspended or refunded)
  loses all of their unused quota at once. Other members keep theirs, and no
  assignment already made is undone.
* Exact proportions are therefore a property of an unchanged snapshot: a
  round in which somebody was refunded or suspended is fair to everybody
  else, but not "complete" for the member who left it.

The pool's boundary (``REFERRAL_POOL_CAP``) counts units sold, as it counted
seats when one seat was one $100 entry: the pool closes to new purchases at
that number, and units already held keep working.

Integrity
---------
Every write here runs under one transaction-scoped advisory lock, so two
registrations cannot draw against the same quota, and two purchases cannot
both take the last units. The database backs that up on its own: one
assignment per new member (unique), ``assigned <= quota_total`` (check), one
open round per pool (partial unique index), one purchase per payment
(unique). An assignment, its quota use and its audit row are written in one
savepoint: either all of them exist, or none does.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session as OrmSession

from common import settings
from common.ids import new_id

import models

log = logging.getLogger("auth-service.referral_pool")

POOL = models.POOL_ID
SCHEMA = models.SCHEMA
CENT = Decimal("0.01")
ACTIVE = ("credited", "partially_reversed")

# Any 64-bit constant; it only has to be unique among this database's advisory
# locks, and it is released when the transaction ends.
_POOL_LOCK_KEY = 871_402_551


class PoolError(Exception):
    """A purchase or reversal the pool refuses, with a code the caller maps."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(db: OrmSession) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(_POOL_LOCK_KEY)))


def unit_price() -> Decimal:
    return Decimal(str(settings.REFERRAL_POOL_ENTRY_USD)).quantize(CENT, rounding=ROUND_HALF_UP)


def max_units_per_purchase() -> int:
    return max(1, int(settings.REFERRAL_POOL_MAX_UNITS_PER_PURCHASE))


_LIVE_UNITS = models.ReferralPoolPurchase.units - models.ReferralPoolPurchase.units_reversed


# --- reads ----------------------------------------------------------------

def units_sold(db: OrmSession) -> int:
    """Units counted against the cap: active ones, and migrated ones under review."""
    return int(
        db.scalar(
            select(func.coalesce(func.sum(_LIVE_UNITS), 0)).where(
                models.ReferralPoolPurchase.pool_id == POOL,
                models.ReferralPoolPurchase.status.in_((*ACTIVE, "review")),
            )
        )
        or 0
    )


def active_units(db: OrmSession, member_id: str | None = None) -> int:
    q = select(func.coalesce(func.sum(_LIVE_UNITS), 0)).where(
        models.ReferralPoolPurchase.pool_id == POOL, models.ReferralPoolPurchase.status.in_(ACTIVE)
    )
    if member_id:
        q = q.where(models.ReferralPoolPurchase.member_id == member_id)
    return int(db.scalar(q) or 0)


def open_round(db: OrmSession, *, for_update: bool = False) -> models.ReferralPoolRound | None:
    q = select(models.ReferralPoolRound).where(
        models.ReferralPoolRound.pool_id == POOL, models.ReferralPoolRound.status == "open"
    )
    if for_update:
        q = q.with_for_update()
    return db.scalar(q)


def _last_round_number(db: OrmSession) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.max(models.ReferralPoolRound.number), 0)).where(
                models.ReferralPoolRound.pool_id == POOL
            )
        )
        or 0
    )


def round_remaining(db: OrmSession, round_id: str) -> int:
    return int(
        db.scalar(
            select(
                func.coalesce(func.sum(models.ReferralPoolQuota.quota_total - models.ReferralPoolQuota.assigned), 0)
            ).where(models.ReferralPoolQuota.round_id == round_id)
        )
        or 0
    )


def member_units(db: OrmSession, member_id: str) -> dict:
    """A member's units, split the way the dashboard explains them."""
    total = active_units(db, member_id)
    current = open_round(db)
    in_round = remaining = 0
    if current is not None:
        quota = db.get(models.ReferralPoolQuota, (current.id, member_id))
        if quota is not None:
            in_round = quota.quota_total
            remaining = quota.quota_total - quota.assigned
        awaiting = int(
            db.scalar(
                select(func.coalesce(func.sum(_LIVE_UNITS), 0)).where(
                    models.ReferralPoolPurchase.pool_id == POOL,
                    models.ReferralPoolPurchase.member_id == member_id,
                    models.ReferralPoolPurchase.status.in_(ACTIVE),
                    models.ReferralPoolPurchase.active_from_round > current.number,
                )
            )
            or 0
        )
    else:
        # No round in progress: every active unit joins the next one, which
        # opens with the next eligible sign-up.
        awaiting = total
    received = int(
        db.scalar(
            select(func.count()).select_from(models.PoolAssignment).where(models.PoolAssignment.sponsor_id == member_id)
        )
        or 0
    )
    under_review = int(
        db.scalar(
            select(func.coalesce(func.sum(models.ReferralPoolPurchase.units), 0)).where(
                models.ReferralPoolPurchase.member_id == member_id, models.ReferralPoolPurchase.status == "review"
            )
        )
        or 0
    )
    return {
        "total_active_units": total,
        "units_in_current_round": in_round,
        "round_quota_remaining": remaining,
        "units_awaiting_next_round": awaiting,
        "units_under_review": under_review,
        "referrals_received": received,
    }


def state(db: OrmSession, member_id: str | None = None) -> dict:
    """Everything the pool screen needs."""
    sold = units_sold(db)
    cap = settings.REFERRAL_POOL_CAP
    current = open_round(db)
    live = active_units(db)
    mine = None
    if member_id:
        seat = db.scalar(select(models.ReferralPoolSeat).where(models.ReferralPoolSeat.member_id == member_id))
        if seat is not None:
            mine = {
                "seat_id": seat.id,
                "seat_number": seat.seat_number,
                "status": seat.status,
                "assigned_count": seat.assigned_count,
                "last_assigned_at": seat.last_assigned_at,
                "joined_at": seat.created_at,
                **member_units(db, member_id),
            }
    return {
        "entry_price": str(unit_price()),
        "unit_price": str(unit_price()),
        "currency": "USD",
        "max_units_per_purchase": max_units_per_purchase(),
        "cap": cap,
        # Kept under their old names for the pages that read them: a "seat"
        # was one $100 entry, which is what a unit is.
        "seats_taken": sold,
        "seats_left": max(cap - sold, 0),
        "units_sold": sold,
        "units_left": max(cap - sold, 0),
        "open": sold < cap,
        "active_seats": live,
        "active_units": live,
        "current_round": None
        if current is None
        else {
            "number": current.number,
            "started_at": current.started_at,
            "units_snapshot": current.units_snapshot,
            "assigned": current.assigned,
            "remaining": round_remaining(db, current.id),
        },
        "my_seat": mine,
        "note": (
            "More units give you a proportionally larger share of eligible pool referrals. Allocation depends on "
            "eligible registrations and does not guarantee a specific number of referrals within a particular period."
        ),
        "rotation_note": (
            "Referrals are dealt in rounds: each round gives every member as many referrals as they held units "
            "when it began, in a random order. Units bought during a round join the next one. The exact "
            "proportions hold for a round in which nobody's eligibility changed."
        ),
    }


# --- purchases ------------------------------------------------------------

def _seat_for(db: OrmSession, member_id: str) -> models.ReferralPoolSeat:
    """The member's place in the pool, created with their first units."""
    seat = db.scalar(select(models.ReferralPoolSeat).where(models.ReferralPoolSeat.member_id == member_id))
    if seat is None:
        number = int(db.scalar(select(func.coalesce(func.max(models.ReferralPoolSeat.seat_number), 0))) or 0) + 1
        seat = models.ReferralPoolSeat(
            id=new_id("seat"), member_id=member_id, seat_number=number, price_paid="0", status="active"
        )
        db.add(seat)
        db.flush()
    return seat


def credit_purchase(
    db: OrmSession,
    member_id: str,
    *,
    payment_ref: str,
    units: int,
    amount: Decimal | str,
    currency: str = "USD",
) -> tuple[models.ReferralPoolPurchase, bool]:
    """Credit a settled payment as units. Returns ``(purchase, created)``.

    Called after the money has settled, never before. The same payment
    notified again returns the purchase it already made (``created`` False).
    Quantity and price are checked here too, whatever the caller checked.
    """
    if not isinstance(units, int) or isinstance(units, bool) or units < 1 or units > max_units_per_purchase():
        raise PoolError("bad_quantity", f"Buy between 1 and {max_units_per_purchase()} units")
    price = unit_price()
    try:
        paid = Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)
    except Exception as exc:
        raise PoolError("bad_amount", "Unreadable amount") from exc
    if currency.upper() != "USD" or paid != price * units:
        raise PoolError("bad_amount", f"{units} units cost ${price * units} USD")

    _lock(db)
    existing = db.scalar(
        select(models.ReferralPoolPurchase).where(models.ReferralPoolPurchase.payment_ref == payment_ref)
    )
    if existing is not None:
        if existing.member_id != member_id or existing.units != units:
            raise PoolError("mismatch", "This payment was already credited differently")
        return existing, False

    if units_sold(db) + units > settings.REFERRAL_POOL_CAP:
        raise PoolError("pool_full", "The referral pool is full")

    seat = _seat_for(db, member_id)
    if seat.price_paid in (None, "", "0"):
        seat.price_paid = str(paid)
        seat.payment_ref = payment_ref
    current = open_round(db)
    purchase = models.ReferralPoolPurchase(
        id=new_id("rpp"),
        pool_id=POOL,
        member_id=member_id,
        seat_id=seat.id,
        payment_ref=payment_ref,
        units=units,
        units_reversed=0,
        unit_price=price,
        amount=paid,
        currency="USD",
        status="credited",
        source="payment",
        # The next round boundary: the round in progress is never reset.
        active_from_round=(current.number if current else _last_round_number(db)) + 1,
    )
    db.add(purchase)
    db.flush()
    db.add(
        models.ReferralPoolUnitEntry(
            pool_id=POOL, member_id=member_id, purchase_id=purchase.id, delta=units, kind="purchase",
            reason=f"{units} x ${price}", actor="payment-service", reference=f"purchase:{payment_ref}",
        )
    )
    db.flush()
    return purchase, True


def reverse_units(
    db: OrmSession,
    purchase: models.ReferralPoolPurchase,
    *,
    units: int,
    kind: str,
    reason: str,
    actor: str,
    reference: str | None = None,
) -> tuple[models.ReferralPoolUnitEntry, bool]:
    """Take refunded or reversed units off one purchase. Returns ``(entry, created)``.

    Idempotent on ``reference``. Never takes more than the purchase still has.
    Units in the current round's snapshot come off the member's unused quota
    at once; assignments already made stay where they are.
    """
    if kind not in ("refund", "reversal"):
        raise PoolError("bad_kind", "kind must be refund or reversal")
    _lock(db)
    if reference:
        done = db.scalar(select(models.ReferralPoolUnitEntry).where(models.ReferralPoolUnitEntry.reference == reference))
        if done is not None:
            return done, False
    db.refresh(purchase, with_for_update=True)
    left = purchase.active_units
    if units < 1:
        raise PoolError("bad_quantity", "Reverse at least one unit")
    if units > left:
        raise PoolError("too_many", f"This purchase has {left} active unit{'s' if left != 1 else ''} left")

    purchase.units_reversed += units
    purchase.status = "reversed" if purchase.units_reversed >= purchase.units else "partially_reversed"
    entry = models.ReferralPoolUnitEntry(
        pool_id=POOL, member_id=purchase.member_id, purchase_id=purchase.id, delta=-units, kind=kind,
        reason=reason[:300], actor=actor, reference=reference,
    )
    db.add(entry)

    current = open_round(db, for_update=True)
    if current is not None and purchase.active_from_round <= current.number:
        quota = db.scalar(
            select(models.ReferralPoolQuota)
            .where(
                models.ReferralPoolQuota.round_id == current.id,
                models.ReferralPoolQuota.member_id == purchase.member_id,
            )
            .with_for_update()
        )
        if quota is not None:
            quota.quota_total = max(quota.assigned, quota.quota_total - units)
    db.flush()
    return entry, True


def exclude_member(db: OrmSession, member_id: str, reason: str) -> None:
    """A member who stops being eligible loses the unused part of this round's quota."""
    _lock(db)
    current = open_round(db, for_update=True)
    if current is None:
        return
    quota = db.scalar(
        select(models.ReferralPoolQuota)
        .where(models.ReferralPoolQuota.round_id == current.id, models.ReferralPoolQuota.member_id == member_id)
        .with_for_update()
    )
    if quota is not None and quota.excluded_at is None:
        quota.quota_total = quota.assigned
        quota.excluded_at = _now()
        quota.excluded_reason = reason[:80]
        db.flush()


# --- rounds ---------------------------------------------------------------

def _close(db: OrmSession, rnd: models.ReferralPoolRound, reason: str) -> None:
    rnd.status = "closed"
    rnd.closed_at = _now()
    rnd.close_reason = reason
    db.flush()


def _start_round(db: OrmSession) -> models.ReferralPoolRound | None:
    """Snapshot every eligible member's active units into a new round.

    None when nobody has an eligible unit: no empty round is ever opened.
    """
    number = _last_round_number(db) + 1
    snapshot = db.execute(
        text(
            f"""
            SELECT p.member_id, s.id AS seat_id, SUM(p.units - p.units_reversed) AS units
              FROM {SCHEMA}.referral_pool_purchases p
              JOIN {SCHEMA}.referral_pool_seats s ON s.member_id = p.member_id
             WHERE p.pool_id = :pool AND p.status IN ('credited', 'partially_reversed')
               AND p.active_from_round <= :number AND s.status = 'active'
             GROUP BY p.member_id, s.id
            HAVING SUM(p.units - p.units_reversed) > 0
            """
        ),
        {"pool": POOL, "number": number},
    ).all()
    if not snapshot:
        return None
    rnd = models.ReferralPoolRound(
        id=new_id("rnd"), pool_id=POOL, number=number, status="open",
        members=len(snapshot), units_snapshot=sum(int(r.units) for r in snapshot), assigned=0,
    )
    db.add(rnd)
    db.flush()
    db.execute(
        pg_insert(models.ReferralPoolQuota),
        [
            {"round_id": rnd.id, "member_id": r.member_id, "seat_id": r.seat_id, "units_snapshot": int(r.units),
             "quota_total": int(r.units), "assigned": 0}
            for r in snapshot
        ],
    )
    log.info("referral pool: round %s opened, %s members, %s units", number, rnd.members, rnd.units_snapshot)
    return rnd


_CANDIDATES = f"""
    SELECT q.member_id, q.quota_total - q.assigned AS rem
      FROM {SCHEMA}.referral_pool_quotas q
      JOIN {SCHEMA}.referral_pool_seats s ON s.member_id = q.member_id
     WHERE q.round_id = :round AND q.excluded_at IS NULL AND q.quota_total > q.assigned
       AND s.status = 'active' AND q.member_id <> :new_member
"""


def _draw(db: OrmSession, rnd: models.ReferralPoolRound, new_member_id: str) -> tuple[str, int, int] | None:
    """(member, their remaining quota, the round's eligible total), drawn
    proportionally to remaining quota. None when nobody eligible has quota."""
    params = {"round": rnd.id, "new_member": new_member_id}
    total = int(db.scalar(text(f"SELECT COALESCE(SUM(rem), 0) FROM ({_CANDIDATES}) c"), params) or 0)
    if total <= 0:
        return None
    pick = secrets.randbelow(total)
    row = db.execute(
        text(
            f"""
            SELECT member_id, rem FROM (
                SELECT member_id, rem, SUM(rem) OVER (ORDER BY member_id) AS cum FROM ({_CANDIDATES}) c
            ) w
             WHERE cum > :pick
             ORDER BY member_id
             LIMIT 1
            """
        ),
        {**params, "pick": pick},
    ).first()
    return (row.member_id, int(row.rem), total) if row else None


def _exclude_ineligible(db: OrmSession, rnd: models.ReferralPoolRound) -> None:
    """Quota left on seats that are no longer active is taken away for good."""
    db.execute(
        text(
            f"""
            UPDATE {SCHEMA}.referral_pool_quotas q
               SET quota_total = q.assigned, excluded_at = now(), excluded_reason = 'seat_' || s.status
              FROM {SCHEMA}.referral_pool_seats s
             WHERE q.round_id = :round AND s.member_id = q.member_id AND s.status <> 'active'
               AND q.excluded_at IS NULL
            """
        ),
        {"round": rnd.id},
    )


def _record_unassigned(db: OrmSession, new_member_id: str, reason: str) -> None:
    db.execute(
        pg_insert(models.ReferralPoolUnassigned)
        .values(new_member_id=new_member_id, pool_id=POOL, reason=reason[:120], attempts=1,
                created_at=_now(), last_attempt_at=_now())
        .on_conflict_do_update(
            index_elements=["new_member_id"],
            set_={"reason": reason[:120], "attempts": models.ReferralPoolUnassigned.attempts + 1,
                  "last_attempt_at": _now(), "resolved_at": None},
        )
    )


def _allocate_locked(db: OrmSession, new_member_id: str) -> models.PoolAssignment | None:
    existing = db.scalar(select(models.PoolAssignment).where(models.PoolAssignment.new_member_id == new_member_id))
    if existing is not None:
        return existing  # a retry: the member is already placed, never twice

    for _ in range(3):
        rnd = open_round(db, for_update=True)
        if rnd is None:
            rnd = _start_round(db)
            if rnd is None:
                return None
        drawn = _draw(db, rnd, new_member_id)
        if drawn is None:
            # Whatever quota is left belongs to members who are no longer
            # eligible: it goes, and the round ends early.
            _exclude_ineligible(db, rnd)
            _close(db, rnd, "complete" if round_remaining(db, rnd.id) == 0 else "no_eligible_quota")
            continue
        member_id, remaining, eligible_total = drawn
        used = db.execute(
            text(
                f"""
                UPDATE {SCHEMA}.referral_pool_quotas SET assigned = assigned + 1
                 WHERE round_id = :round AND member_id = :member AND assigned < quota_total
                 RETURNING quota_total
                """
            ),
            {"round": rnd.id, "member": member_id},
        ).first()
        if used is None:  # cannot happen under the lock; the guard is the database's
            raise RuntimeError("quota changed under the pool lock")
        seat = db.scalar(select(models.ReferralPoolSeat).where(models.ReferralPoolSeat.member_id == member_id))
        seat.assigned_count += 1
        seat.last_assigned_at = _now()
        rnd.assigned += 1
        assignment = models.PoolAssignment(
            seat_id=seat.id,
            sponsor_id=member_id,
            new_member_id=new_member_id,
            seats_in_draw=eligible_total,
            pool_id=POOL,
            round_id=rnd.id,
            round_number=rnd.number,
            quota_total=int(used.quota_total),
            quota_remaining_before=remaining,
        )
        db.add(assignment)
        db.flush()
        if round_remaining(db, rnd.id) == 0:
            _close(db, rnd, "complete")
        return assignment
    return None


def allocate(db: OrmSession, new_member_id: str) -> models.PoolAssignment | None:
    """Place a sign-up that arrived with no valid personal invitation.

    Only ever called once invitation attribution has found nobody. Returns the
    assignment, or None when nobody in the pool is eligible - the existing
    fallback then applies (the member is unsponsored, their commission goes
    unclaimed) and the reason is recorded. Never raises: a failure here must
    not cost somebody their account; it is recorded for a retry.
    """
    try:
        with db.begin_nested():
            _lock(db)
            assignment = _allocate_locked(db, new_member_id)
    except Exception as exc:
        log.error("referral pool allocation failed for %s: %s", new_member_id, exc)
        try:
            with db.begin_nested():
                _record_unassigned(db, new_member_id, f"error:{type(exc).__name__}")
        except Exception:
            pass
        return None
    with db.begin_nested():
        if assignment is None:
            _record_unassigned(db, new_member_id, "pool_empty")
        else:
            db.execute(
                text(
                    f"UPDATE {SCHEMA}.referral_pool_unassigned SET resolved_at = now() "
                    "WHERE new_member_id = :m AND resolved_at IS NULL"
                ),
                {"m": new_member_id},
            )
    return assignment
