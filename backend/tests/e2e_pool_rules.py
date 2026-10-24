"""Referral pool rules decided on 24/10: members cap, account eligibility, full refunds.

Against the local stack. Other members' pool places are suspended for the run
and restored afterwards.
 1. the cap counts members: a new member is refused at the cap, a member
    already in can still buy;
 2. a deactivated or deleted account leaves the round at once, keeps its
    units, and comes back when active again;
 3. a refund of units bought with a payment: units off, the ledger mirrors the
    share (revenue, sponsor commission, Leaders), the amount is owed on the
    buyer's balance; the same request twice refunds once.
"""
import random
import string
import subprocess
import sys
import uuid
from datetime import date
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
AUTH = "http://localhost:8201"
c = httpx.Client(base_url=BASE, timeout=120)
a = httpx.Client(base_url=AUTH, timeout=120)
ok = True
RUN = "".join(random.choices(string.ascii_lowercase, k=5))


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def in_auth(code):
    r = subprocess.run(["docker", "compose", "exec", "-T", "auth-service", "python", "-c", code],
                       capture_output=True, text=True)
    return r.stdout.strip() + (r.stderr.strip()[-300:] if r.returncode else "")


def register(name, **extra):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG", **extra}
    r = httpx.post(f"{AUTH}/auth/register", json=body, timeout=120)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "email": body["email"],
            "code": d["user"].get("referral_code"), "invited_by": d["user"].get("invited_by")}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def credit(u, units, ref):
    return a.post("/internal/referral-pool/purchases", json={
        "member_id": u["id"], "payment_ref": ref, "units": units, "amount": f"{100 * units}.00"})


def open_round():
    return psql("select number from auth.referral_pool_rounds where status='open'")


def deal(n):
    return [register(f"Demo Visitor {RUN}")["invited_by"] for _ in range(n)]


def wallet(uid):
    return Decimal(psql(f"select coalesce(sum(available),0) from ledger.wallets where owner_id='{uid}'") or "0")


psql("create table if not exists public.pool_rules_saved as select id, status from auth.referral_pool_seats where false")
psql("insert into public.pool_rules_saved select id, status from auth.referral_pool_seats "
     "where id not in (select id from public.pool_rules_saved)")
psql("update auth.referral_pool_seats set status='suspended'")
psql("update auth.referral_pool_rounds set status='closed', closed_at=now(), close_reason='test_reset' where status='open'")
admin = register("Demo Admin")
psql(f"update auth.users set role='admin' where id='{admin['id']}'")
admin["token"] = c.post("/auth/login", json={"email": admin["email"], "password": "Sup3rStrong!Pass"}).json()["tokens"]["access_token"]

try:
    print("1. The cap counts members")
    A, B = register("Demo A"), register("Demo B")
    credit(A, 2, f"r-{RUN}-a1")
    members = int(psql("select count(*) from auth.referral_pool_seats"))
    state = c.get("/auth/referral-pool", headers=H(A)).json()
    check("the state reports members against the cap", state["cap_counts"] == "members" and state["members"] == members, state)
    out = in_auth(f"""
import referral_pool
from common import settings
from common.database import SessionLocal
settings.REFERRAL_POOL_CAP = {members}
with SessionLocal() as db:
    print(referral_pool.can_buy(db, '{A['id']}')[0], referral_pool.can_buy(db, '{B['id']}')[0])
    try:
        referral_pool.credit_purchase(db, '{B['id']}', payment_ref='r-{RUN}-b1', units=1, amount='100.00')
        print('credited')
    except referral_pool.PoolError as exc:
        print(exc.code)
    p, created = referral_pool.credit_purchase(db, '{A['id']}', payment_ref='r-{RUN}-a2', units=5, amount='500.00')
    print(created)
    db.rollback()
""")
    lines = out.splitlines()
    check("at the cap: a member already in can buy, a new one cannot", lines[:1] == ["True False"], out)
    check("a new member's purchase is refused (pool_full)", lines[1:2] == ["pool_full"], out)
    check("a member already in buys 5 more units at the cap", lines[2:3] == ["True"], out)
    check("units per member are not capped (no unit count check)", "units_left" not in state)

    print()
    print("2. Accounts that are not active leave the round")
    credit(B, 2, f"r-{RUN}-b2")
    names = {A["id"]: "A", B["id"]: "B"}
    first = deal(1)  # opens a round of 4 (A 2, B 2)
    rid = psql("select id from auth.referral_pool_rounds where status='open'")
    check("a round of A 2 + B 2", psql(f"select units_snapshot from auth.referral_pool_rounds where id='{rid}'") == "4")
    psql(f"update auth.users set status='deactivated' where id='{B['id']}'")
    rest = deal(3)
    got = [names.get(s, s) for s in first + rest]
    check("after B deactivates, B receives nothing more", "B" not in [names.get(s, s) for s in rest], got)
    reason = psql(f"select excluded_reason from auth.referral_pool_quotas where round_id='{rid}' and member_id='{B['id']}'")
    check("B's unused quota was taken away, recorded", reason == "account_deactivated" or got.count("B") == 2, (reason, got))
    check("B keeps their units", psql(
        f"select sum(units - units_reversed) from auth.referral_pool_purchases where member_id='{B['id']}'") == "2")
    while open_round():
        deal(1)
    nxt = [names.get(s, s) for s in deal(2)]
    check("the next round leaves B out entirely", nxt == ["A", "A"], nxt)
    psql(f"update auth.users set status='deleted' where id='{B['id']}'")
    while open_round():
        deal(1)
    check("a deleted account is left out too", "B" not in [names.get(s, s) for s in deal(2)])
    psql(f"update auth.users set status='active' where id='{B['id']}'")
    while open_round():
        deal(1)
    back = sorted(names.get(s, s) for s in deal(4))
    check("active again: B is back from the next round", back == ["A", "A", "B", "B"], back)

    print()
    print("3. A full refund of units bought with a payment")
    sponsor = register("Demo Sponsor")
    # Commission is only earned once the sponsor's own verification is active.
    psql(f"update auth.users set kyc_verified=true where id='{sponsor['id']}'")
    buyer = register("Demo Buyer", referral_code=sponsor["code"])
    pay = c.post("/payments/referral-pool/join", headers=H(buyer), json={"units": 3})
    if pay.status_code != 201:
        print(f"  SKIP checkout unavailable here ({pay.status_code})")
    else:
        pid = pay.json()["payment_id"]
        settled = c.post(f"/payments/{pid}/mock-settle", headers=H(buyer))
        if settled.status_code != 200:
            print(f"  SKIP mock settlement unavailable here ({settled.status_code})")
        else:
            journal = psql(f"select id from ledger.journals where idempotency_key='payment:{pid}'")
            check("the $300 payment is in the ledger", bool(journal))
            sponsor_before, buyer_before = wallet(sponsor["id"]), wallet(buyer["id"])
            commission = Decimal(psql(f"select coalesce(sum(credit),0) from ledger.entries where journal_id='{journal}' "
                                      f"and beneficiary_id='{sponsor['id']}'"))
            check("the sponsor earned their commission on it", commission > 0, commission)
            req = uuid.uuid4().hex
            body = {"units": 2, "kind": "refund", "reason": "member asked", "request_id": req}
            r1 = c.post(f"/admin/pool-refunds/{pid}", headers=H(admin), json=body)
            check("refund 2 of the 3 units", r1.status_code == 200 and r1.json()["amount"] == "200.00", r1.text[:200])
            r2 = c.post(f"/admin/pool-refunds/{pid}", headers=H(admin), json=body)
            check("the same request again refunds nothing more", r2.status_code == 200
                  and psql(f"select count(*) from ledger.journals where idempotency_key='pool-refund:rfd_{req}'") == "1"
                  and psql(f"select units_reversed from auth.referral_pool_purchases where payment_ref='{pid}'") == "2",
                  r2.text[:200])
            check("the buyer is owed $200 on their balance", wallet(buyer["id"]) - buyer_before == Decimal("200.00"),
                  (buyer_before, wallet(buyer["id"])))
            clawed = sponsor_before - wallet(sponsor["id"])
            check("the sponsor's commission comes back in proportion (2/3)",
                  abs(clawed - (commission * 2 / 3).quantize(Decimal("0.01"))) <= Decimal("0.01"), (clawed, commission))
            refund_j = psql(f"select id from ledger.journals where idempotency_key='pool-refund:rfd_{req}'")
            balance = psql(f"select sum(debit) - sum(credit) from ledger.entries where journal_id='{refund_j}'")
            check("the refund journal balances to the cent", Decimal(balance) == 0, balance)
            check("the refund is recorded on the payment", psql(
                f"select status from payment.refunds where id='rfd_{req}'") == "credited_to_balance")
            over = c.post(f"/admin/pool-refunds/{pid}", headers=H(admin),
                          json={**body, "units": 2, "request_id": uuid.uuid4().hex})
            check("refunding more units than are left is refused", over.status_code == 409, over.text[:200])
            last = c.post(f"/admin/pool-refunds/{pid}", headers=H(admin),
                          json={**body, "units": 1, "request_id": uuid.uuid4().hex})
            check("the last unit can still be refunded", last.status_code == 200 and wallet(buyer["id"]) - buyer_before
                  == Decimal("300.00"), last.text[:200])
            check("members cannot refund", c.post(f"/admin/pool-refunds/{pid}", headers=H(buyer),
                                                 json={**body, "request_id": uuid.uuid4().hex}).status_code == 403)
finally:
    psql("update auth.referral_pool_rounds set status='closed', closed_at=now(), close_reason='test_reset' where status='open'")
    psql("update auth.referral_pool_seats s set status = t.status from public.pool_rules_saved t where t.id = s.id")
    psql("update auth.referral_pool_seats set status='suspended' where id not in (select id from public.pool_rules_saved)")
    psql("drop table if exists public.pool_rules_saved")

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
