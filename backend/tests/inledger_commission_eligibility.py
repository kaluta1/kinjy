"""Sponsor commissions and verification (28/10). Runs inside the ledger container,
which reaches the internal endpoints of auth, payment and the ledger itself:

    docker cp backend/tests/inledger_commission_eligibility.py kaluta-ledger:/tmp/
    docker exec -w /app kaluta-ledger python /tmp/inledger_commission_eligibility.py

- unverified sponsor: no commission, the 20% booked as Kinjy's unclaimed
  revenue, an audit row saying from whom and why, not in the Leaders ranking;
- a retried posting (same key) records nothing twice;
- verified sponsor: the commission, at the existing 20%;
- verified later: nothing paid back for what was withheld;
- verification withdrawn (provider rejection): auth is told, new commissions
  withheld, the ones earned while verified kept;
- verification expired: withheld;
- repair: dry run lists a commission credited without verification, apply
  corrects it once (ledger entries, wallet, commission reversed, audit row,
  out of the Leaders ranking), a second apply changes nothing.
"""
import random
import string
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select, text

sys.path.insert(0, "/app")
from common import economy  # noqa: E402
from common.database import SessionLocal  # noqa: E402
import models  # noqa: E402
import posting  # noqa: E402
import repair_commission_eligibility as repair  # noqa: E402

AUTH, PAY, LEDGER = "http://auth-service:8000", "http://payment-service:8000", "http://localhost:8000"
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def register(name, code=None):
    t = tag()
    r = httpx.post(f"{AUTH}/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": "1990-02-02", "country": "US", **({"referral_code": code} if code else {})}, timeout=30)
    r.raise_for_status()
    d = r.json()["user"]
    return {"id": d["id"], "code": d["referral_code"]}


def sql(q, **kw):
    with SessionLocal() as db:
        out = db.execute(text(q), kw)
        db.commit()
        try:
            return out.all()
        except Exception:
            return None


def fee(member, key, amount="10.00"):
    r = httpx.post(f"{LEDGER}/internal/post/kyc-fee", json={"member_id": member["id"], "amount": amount,
                                                           "reference": key, "idempotency_key": key}, timeout=30)
    r.raise_for_status()
    return r.json()


def commissions(sponsor):
    return sql("select amount, status, journal_id from ledger.commissions where member_id=:m and role='sponsor'", m=sponsor["id"])


def withheld(sponsor):
    return sql("select amount, reason, journal_id from ledger.withheld_commissions where member_id=:m", m=sponsor["id"])


def ranked(sponsor):
    items = httpx.get(f"{LEDGER}/leaders/standings?limit=500", timeout=30).json()["items"]
    return any(i["member_id"] == sponsor["id"] for i in items)


def kyc_record(user, status="pending", verified_on=None, expires_on=None):
    sql("insert into payment.kyc_records (user_id, year, fee_paid, attempts, status, verified_on, expires_on, created_at) "
        "values (:u, :y, true, 1, :s, :v, :e, now())", u=user["id"], y=date.today().year, s=status, v=verified_on, e=expires_on)


def callback(user, outcome):
    httpx.post(f"{PAY}/internal/kyc/callback", params={"user_id": user["id"], "outcome": outcome}, timeout=30).raise_for_status()


def auth_verified(user):
    return sql("select kyc_verified, kyc_verified_until from auth.users where id=:u", u=user["id"])[0]


print("== unverified sponsor ==")
sponsor = register("Demo Sponsor")
member = register("Demo Member", sponsor["code"])
k1 = f"test-fee-{tag()}"
fee(member, k1)
check("no commission for an unverified sponsor", not commissions(sponsor), commissions(sponsor))
w = withheld(sponsor)
check("audit row: withheld from them, why, how much", len(w) == 1 and w[0].reason == "sponsor_unverified" and w[0].amount == Decimal("2.00"), w)
lines = sql("select account, debit, credit from ledger.entries where journal_id=:j and reason='unclaimed_sponsor_commission'", j=w[0].journal_id)
check("booked to Kinjy's unclaimed commission revenue, balanced", sorted((l.account, str(l.debit), str(l.credit)) for l in lines) ==
      [("revenue:kyc_fee", "2.00", "0.00"), ("revenue:unclaimed_commission", "0.00", "2.00")], lines)
fee(member, k1)
check("a retried posting adds nothing", len(withheld(sponsor)) == 1 and not commissions(sponsor))
check("not in the Leaders ranking", not ranked(sponsor))

print("== verified, then later events ==")
kyc_record(sponsor)
callback(sponsor, "verified")
check("auth told: verified, with an end date", auth_verified(sponsor)[0] is True and auth_verified(sponsor)[1] is not None, auth_verified(sponsor))
k2 = f"test-fee-{tag()}"
fee(member, k2)
c = commissions(sponsor)
check("verified sponsor earns the 20% (2.00 on 10.00)", len(c) == 1 and c[0].amount == Decimal("2.00"), c)
check("verifying later pays nothing back for what was withheld", len(commissions(sponsor)) == 1 and len(withheld(sponsor)) == 1)
check("now in the Leaders ranking", ranked(sponsor))
callback(sponsor, "rejected")
check("verification withdrawn: auth told", auth_verified(sponsor)[0] is False, auth_verified(sponsor))
k3 = f"test-fee-{tag()}"
fee(member, k3)
check("withdrawn: the next commission is withheld", len(commissions(sponsor)) == 1 and len(withheld(sponsor)) == 2)
check("the commission earned while verified is kept", commissions(sponsor)[0].status != "reversed")
check("the withdrawn record keeps the period it was valid",
      sql("select verified_on, expires_on from payment.kyc_records where user_id=:u", u=sponsor["id"])[0].expires_on == date.today())

print("== expired ==")
old = register("Demo Old")
m2 = register("Demo Member2", old["code"])
httpx.post(f"{AUTH}/internal/users/{old['id']}/kyc", params={"verified": True, "until": (date.today() - timedelta(days=1)).isoformat()}, timeout=30)
fee(m2, f"test-fee-{tag()}")
check("expired verification: withheld", not commissions(old) and len(withheld(old)) == 1)

print("== repair ==")
ghost = register("Demo Ghost")  # never verified, no record
with SessionLocal() as db:   # what a posting looked like before the rule: credited anyway
    split = economy.revenue_split(Decimal("50.00"), ghost["id"], kind="service")
    j = posting.post_split(db, kind="service_fee", split=split, revenue_account=models.ACC_REVENUE_MARGIN,
                           reference="repair-test", idempotency_key=f"repair-test-{tag()}")
    db.commit()
    jid = j.id
wallet_before = sql("select available from ledger.wallets where owner_id=:m", m=ghost["id"])[0].available
check("set-up: the bad commission exists and ranks", len(commissions(ghost)) == 1 and ranked(ghost))
dry = repair.run(apply=False, members=[ghost["id"]], before_rule=True)
check("dry run lists it, changes nothing", len(dry["to_correct"]) == 1 and len(commissions(ghost)) == 1
      and commissions(ghost)[0].status == "accrued", dry)
done = repair.run(apply=True, members=[ghost["id"]], before_rule=True)
check("apply corrects it", len(done["corrected"]) == 1, done)
check("commission reversed (out of earnings)", commissions(ghost)[0].status == "reversed")
check("wallet reduced by exactly the commission", sql("select available from ledger.wallets where owner_id=:m", m=ghost["id"])[0].available == wallet_before - Decimal("10.00"))
check("audit row with the correcting journal", len([w for w in withheld(ghost) if w.reason == "repair_unverified_at_earning"]) == 1)
fix = done["corrected"][0]["correction_journal_id"]
bal = sql("select sum(debit) d, sum(credit) c from ledger.entries where journal_id=:j", j=fix)[0]
check("correcting journal balanced, booked to unclaimed revenue", bal.d == bal.c and
      sql("select count(*) from ledger.entries where journal_id=:j and account='revenue:unclaimed_commission' and credit=10", j=fix)[0][0] == 1)
check("out of the Leaders ranking", not ranked(ghost))
again = repair.run(apply=True, members=[ghost["id"]], before_rule=True)
check("a second run changes nothing", not again["corrected"] and not again["to_correct"] and
      sql("select count(*) from ledger.journals where idempotency_key=:k", k=f"repair-commission:{commissions(ghost)[0].journal_id}")[0][0] == 0
      and len(withheld(ghost)) == 1, again)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
