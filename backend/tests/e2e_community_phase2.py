"""Communities, phase 2: paid memberships, events, listings, analytics.

Against the local stack with mock payment settlement. Covers:
 1. admin settings (markup, grace, paid switch) and who may change them;
 2. tiers (owner price + Kinjy markup), coupons on a first payment;
 3. payment: server-made quote, checkout at its total only, duplicate settlement,
    out-of-order / foreign credits, ledger split (owner, sponsor, Leaders);
 4. renewal continues the period; cancellation; grace then expiry; lapse and return;
 5. refunds: full ends access and claws back, partial keeps it, idempotent, bounded;
 6. trials once, ending without grace;
 7. mixed community: paid spaces closed to non-payers in feed, link and posting;
 8. events: capacity under concurrent registration, waitlist promotion, hidden link,
    cancellation, paid tickets and the refund when a sold-out event is paid late;
 9. community marketplace listings; analytics (money for the owner only); reminders once.
"""
import concurrent.futures
import random
import string
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
AUTH = "http://localhost:8201"
c = httpx.Client(base_url=BASE, timeout=120)
ok = True
RUN = "".join(random.choices(string.ascii_lowercase, k=6))


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def in_service(service, code):
    r = subprocess.run(["docker", "compose", "exec", "-T", service, "python", "-c", code], capture_output=True, text=True)
    return r.stdout.strip() + (r.stderr.strip()[-400:] if r.returncode else "")


def run_due():
    return in_service("community-service", "from common.database import SessionLocal\nimport paid\n"
                      "with SessionLocal() as db:\n    print(paid.run_due(db))")


def register(name, **extra):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG", **extra}
    r = httpx.post(f"{AUTH}/auth/register", json=body, timeout=120)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "email": body["email"],
            "code": d["user"]["referral_code"]}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def wallet(uid):
    return Decimal(psql(f"select coalesce(sum(available),0) from ledger.wallets where owner_id='{uid}'") or "0")


def buy(u, cid, **quote_in):
    q = c.post(f"/communities/{cid}/quote", headers=H(u), json=quote_in)
    if q.status_code != 200:
        return q, None
    pay = c.post("/payments/community/checkout", headers=H(u), json={"quote_id": q.json()["quote_id"]})
    if pay.status_code != 201:
        return pay, None
    s = c.post(f"/payments/{pay.json()['payment_id']}/mock-settle", headers=H(u))
    return s, pay.json()["payment_id"]


def ent(cid, uid):
    return psql(f"select status||'|'||coalesce(to_char(period_end,'YYYY-MM-DD HH24:MI'),'')||'|'||coalesce(to_char(grace_until,'YYYY-MM-DD'),'') "
                f"from community.community_entitlements where community_id='{cid}' and user_id='{uid}'")


def member_status(cid, uid):
    return psql(f"select status from community.memberships where community_id='{cid}' and user_id='{uid}'")


admin = register("Demo Admin")
psql(f"update auth.users set role='admin' where id='{admin['id']}'")
admin["token"] = c.post("/auth/login", json={"email": admin["email"], "password": "Sup3rStrong!Pass"}).json()["tokens"]["access_token"]
owner, mod = register("Demo Owner"), register("Demo Mod")
sponsor = register("Demo Sponsor")
psql(f"update auth.users set kyc_verified=true where id='{sponsor['id']}'")
buyer = register("Demo Buyer", referral_code=sponsor["code"])
other, third = register("Demo Other"), register("Demo Third")

print("1. Admin settings")
c.patch("/admin/community-settings", headers=H(admin), json={"community_paid_enabled": False})
comm = c.post("/communities", headers=H(owner), json={"name": f"Demo Paid {RUN}", "kind": "private"}).json()
cid = comm["id"]
r = c.post(f"/communities/{cid}/tiers", headers=H(owner), json={"name": "Monthly", "price": 10, "period": "monthly"})
check("with paid features off, no tier can be made", r.status_code == 409, r.status_code)
check("a member cannot change the settings", c.patch("/admin/community-settings", headers=H(owner),
                                                     json={"community_paid_enabled": True}).status_code == 403)
r = c.patch("/admin/community-settings", headers=H(admin),
            json={"community_paid_enabled": True, "community_markup_pct": 20, "community_grace_days": 7})
check("an admin switches paid on, markup 20, grace 7", r.status_code == 200
      and r.json()["community_paid_enabled"]["value"] == "true" and r.json()["community_grace_days"]["value"] == "7", r.text[:200])

print()
print("2. Tiers and coupons")
monthly = c.post(f"/communities/{cid}/tiers", headers=H(owner),
                 json={"name": "Monthly", "price": 10, "period": "monthly", "benefits": ["All posts", "Events"]}).json()
check("a monthly tier: owner $10, members pay $12.00", monthly["owner_price"] == "10.00" and monthly["price"] == "12.00", monthly)
check("a moderator cannot create tiers", c.post(f"/communities/{cid}/tiers", headers=H(mod),
                                                json={"name": "Extra", "price": 1, "period": "monthly"}).status_code == 403)
check("a price of 0 is refused", c.post(f"/communities/{cid}/tiers", headers=H(owner),
                                        json={"name": "Free", "price": 0, "period": "monthly"}).status_code == 422)
c.patch(f"/communities/{cid}/access", headers=H(owner), json={"access_model": "paid"}).raise_for_status()
check("joining a paid community without paying answers 402", c.post(f"/communities/{cid}/join", headers=H(other)).status_code == 402)
c.post(f"/communities/{cid}/coupons", headers=H(owner), json={"code": f"half{RUN}", "percent_off": 50}).raise_for_status()
q = c.post(f"/communities/{cid}/quote", headers=H(other), json={"tier_id": monthly["id"], "coupon": f"HALF{RUN}"}).json()
check("a 50% code on a first payment: owner 5.00 + fee 1.00 = 6.00",
      (q["owner_price"], q["markup"], q["total"]) == ("5.00", "1.00", "6.00"), q)
check("an unknown code is refused", c.post(f"/communities/{cid}/quote", headers=H(other),
                                           json={"tier_id": monthly["id"], "coupon": "NOPE"}).status_code == 400)

print()
print("3. Paying")
cheat = c.post("/payments/checkout", headers=H(buyer), json={"purpose": "community", "amount": "1.00", "reference": "cq:x"})
check("a direct checkout at another price is refused", cheat.status_code == 400, cheat.text[:150])
q = c.post(f"/communities/{cid}/quote", headers=H(buyer), json={"tier_id": monthly["id"]}).json()
check("the buyer's quote: 12.00", q["total"] == "12.00", q)
pay = c.post("/payments/community/checkout", headers=H(buyer), json={"quote_id": q["quote_id"]})
check("checkout at the quoted total", pay.status_code == 201 and Decimal(str(pay.json()["amount"])) == Decimal("12"), pay.text[:200])
pid = pay.json()["payment_id"]
other_q = c.post("/payments/community/checkout", headers=H(other), json={"quote_id": q["quote_id"]})
check("somebody else cannot pay this quote", other_q.status_code == 404)
owner_before, sponsor_before = wallet(owner["id"]), wallet(sponsor["id"])
s1 = c.post(f"/payments/{pid}/mock-settle", headers=H(buyer))
s2 = c.post(f"/payments/{pid}/mock-settle", headers=H(buyer))
check("settled twice: one payment recorded", s1.status_code == 200 and psql(
    f"select count(*) from community.community_payments where payment_ref='{pid}'") == "1", (s1.text[:150], s2.status_code))
check("one ledger journal for it", psql(f"select count(*) from ledger.journals where idempotency_key='payment:{pid}'") == "1")
check("access is active for a month", ent(cid, buyer["id"]).startswith("active|"), ent(cid, buyer["id"]))
check("and the buyer is a member now", member_status(cid, buyer["id"]) == "active")
check("the owner's wallet gets the owner price ($10)", wallet(owner["id"]) - owner_before == Decimal("10.00"),
      (owner_before, wallet(owner["id"])))
check("the buyer's sponsor gets 20% of Kinjy's fee ($0.40)", wallet(sponsor["id"]) - sponsor_before == Decimal("0.40"),
      (sponsor_before, wallet(sponsor["id"])))
balance = psql(f"select sum(debit)-sum(credit) from ledger.entries e join ledger.journals j on j.id=e.journal_id "
               f"where j.idempotency_key='payment:{pid}'")
check("the journal balances", Decimal(balance) == 0, balance)
quote_id = q["quote_id"]
dup = in_service("community-service", f"""
from decimal import Decimal
from fastapi import HTTPException
from common.database import SessionLocal
import paid
with SessionLocal() as db:
    print(paid.credit_payment(db, '{quote_id}', '{pid}', Decimal('12.00'))['created'])
    for ref, amount in (('other-{RUN}', '12.00'), ('{pid}', '12.00')):
        pass
    try:
        paid.credit_payment(db, '{quote_id}', 'other-{RUN}', Decimal('12.00'))
        print('credited twice')
    except HTTPException as exc:
        print(exc.status_code)
""")
check("the same payment credited again (a late webhook) changes nothing", dup.splitlines()[:1] == ["False"], dup)
check("another payment against a used quote is refused", dup.splitlines()[1:2] == ["409"], dup)

print()
print("4. Renewal, cancellation, grace, expiry")
end1 = psql(f"select period_end from community.community_entitlements where community_id='{cid}' and user_id='{buyer['id']}'")
s, pid2 = buy(buyer, cid, tier_id=monthly["id"])
end2 = psql(f"select period_end from community.community_entitlements where community_id='{cid}' and user_id='{buyer['id']}'")
check("an early renewal continues from the end of the period (not from today)",
      s.status_code == 200 and end2[:7] != end1[:7] and psql(
          f"select (period_end - interval '1 month')::date = '{end1[:10]}'::date from community.community_entitlements "
          f"where community_id='{cid}' and user_id='{buyer['id']}'") == "t", (end1, end2))
r = c.post(f"/communities/{cid}/membership/cancel", headers=H(buyer))
check("the member cancels: access until the end", r.status_code == 200 and r.json()["cancel_at_period_end"])
psql(f"update community.community_entitlements set period_end = now() - interval '1 minute' "
     f"where community_id='{cid}' and user_id='{buyer['id']}'")
run_due()
check("at the end: cancelled and out of the community", ent(cid, buyer["id"]).startswith("cancelled|")
      and member_status(cid, buyer["id"]) == "lapsed", (ent(cid, buyer["id"]), member_status(cid, buyer["id"])))
check("their feed access is gone", c.get(f"/feed/community/{cid}", headers=H(buyer)).status_code == 404)
s, pid3 = buy(buyer, cid, tier_id=monthly["id"])
check("paying again brings them back, from today", ent(cid, buyer["id"]).startswith("active|")
      and member_status(cid, buyer["id"]) == "active")
s, pid_o = buy(other, cid, tier_id=monthly["id"])
psql(f"update community.community_entitlements set period_end = now() - interval '1 minute', reminded_for = null "
     f"where community_id='{cid}' and user_id='{other['id']}'")
run_due()
e = ent(cid, other["id"])
check("an unpaid end goes into 7 days of grace, access kept", e.startswith("grace|") and member_status(cid, other["id"]) == "active", e)
psql(f"update community.community_entitlements set grace_until = now() - interval '1 minute' "
     f"where community_id='{cid}' and user_id='{other['id']}'")
run_due()
run_due()
check("after the grace: expired and lapsed (once)", ent(cid, other["id"]).startswith("expired|")
      and member_status(cid, other["id"]) == "lapsed")
check("the moderator / owner is never lapsed", member_status(cid, owner["id"]) == "active")

print()
print("5. Refunds")
r = c.post(f"/communities/{cid}/payments/x/refund", headers=H(buyer),
           json={"amount": 1, "reason": "demo", "request_id": uuid.uuid4().hex})
check("a member cannot refund", r.status_code == 403)
pay_row = psql(f"select id from community.community_payments where payment_ref='{pid3}'")
buyer_before, owner_before, sponsor_before = wallet(buyer["id"]), wallet(owner["id"]), wallet(sponsor["id"])
req = uuid.uuid4().hex
r1 = c.post(f"/communities/{cid}/payments/{pay_row}/refund", headers=H(owner),
            json={"amount": 12, "reason": "asked to leave", "request_id": req})
r2 = c.post(f"/communities/{cid}/payments/{pay_row}/refund", headers=H(owner),
            json={"amount": 12, "reason": "asked to leave", "request_id": req})
check("a full refund", r1.status_code == 200 and r1.json()["payment"]["status"] == "refunded", r1.text[:200])
check("sent twice, refunded once", r2.status_code == 200 and wallet(buyer["id"]) - buyer_before == Decimal("12.00"),
      (buyer_before, wallet(buyer["id"])))
check("the owner's $10 comes back out of their wallet", owner_before - wallet(owner["id"]) == Decimal("10.00"))
check("the sponsor's $0.40 comes back too", sponsor_before - wallet(sponsor["id"]) == Decimal("0.40"))
check("a full refund ends the access", ent(cid, buyer["id"]).startswith("refunded|") and member_status(cid, buyer["id"]) == "lapsed")
r = c.post(f"/communities/{cid}/payments/{pay_row}/refund", headers=H(owner),
           json={"amount": 1, "reason": "again", "request_id": uuid.uuid4().hex})
check("nothing left to refund: refused", r.status_code == 409)
s, pid4 = buy(third, cid, tier_id=monthly["id"])
row4 = psql(f"select id from community.community_payments where payment_ref='{pid4}'")
r = c.post(f"/communities/{cid}/payments/{row4}/refund", headers=H(owner),
           json={"amount": 3, "reason": "late start", "request_id": uuid.uuid4().hex})
check("a partial refund keeps the access", r.status_code == 200 and r.json()["payment"]["status"] == "partially_refunded"
      and ent(cid, third["id"]).startswith("active|"))
earn = c.get(f"/communities/{cid}/earnings", headers=H(owner)).json()
check("earnings keep gross, fees, refunds and net apart",
      all(k in earn for k in ("members_paid", "kinjy_fees", "owner_gross", "refunded_to_members", "owner_net")), earn)
check("only the owner sees earnings", c.get(f"/communities/{cid}/earnings", headers=H(mod)).status_code == 403)

print()
print("6. Trials")
trial_tier = c.post(f"/communities/{cid}/tiers", headers=H(owner),
                    json={"name": "Try", "price": 5, "period": "monthly", "trial_days": 7}).json()
t1 = register("Demo Trial")
r = c.post(f"/communities/{cid}/trial", headers=H(t1), json={"tier_id": trial_tier["id"]})
check("a 7-day trial opens access", r.status_code == 200 and r.json()["status"] == "trial" and member_status(cid, t1["id"]) == "active")
check("only once", c.post(f"/communities/{cid}/trial", headers=H(t1), json={"tier_id": trial_tier["id"]}).status_code == 409)
psql(f"update community.community_entitlements set period_end = now() - interval '1 minute' where community_id='{cid}' and user_id='{t1['id']}'")
run_due()
check("a trial that ends unpaid expires without grace", ent(cid, t1["id"]).startswith("expired|")
      and member_status(cid, t1["id"]) == "lapsed")

print()
print("7. Mixed community: paid spaces")
mixed = c.post("/communities", headers=H(owner), json={"name": f"Demo Mixed {RUN}"}).json()
mid = mixed["id"]
c.post(f"/communities/{mid}/tiers", headers=H(owner), json={"name": "Supporter", "price": 4, "period": "monthly"}).raise_for_status()
mtier = c.get(f"/communities/{mid}/tiers", headers=H(owner)).json()["tiers"][0]
c.patch(f"/communities/{mid}/access", headers=H(owner), json={"access_model": "mixed", "paid_spaces": {"articles": True}}).raise_for_status()
for u in (other, third):
    c.post(f"/communities/{mid}/join", headers=H(u)).raise_for_status()
art = c.post("/posts", headers=H(owner), json={"body": "<h2>Demo paid guide</h2><p>Members only</p>", "format": "article",
                                               "visibility": "community", "community_id": mid}).json()
disc = c.post("/posts", headers=H(owner), json={"body": f"Demo free talk {RUN}", "visibility": "community", "community_id": mid}).json()
items = [p["id"] for p in c.get(f"/feed/community/{mid}", headers=H(other)).json()["items"]]
check("a free member sees the discussion, not the paid article", disc["id"] in items and art["id"] not in items, items)
direct = c.get(f"/posts/{art['id']}", headers=H(other))
check("nor by a direct link (told it is for paying members)", direct.status_code == 403
      and "community_paid_only" in direct.text and "Members only" not in direct.text, direct.text[:200])
check("nor can they post an article", c.post("/posts", headers=H(other), json={"body": "<h2>x</h2><p>y</p>", "format": "article",
                                       "visibility": "community", "community_id": mid}).status_code == 403)
check("signed out, the article is closed too", art["id"] not in [p["id"] for p in c.get(f"/feed/community/{mid}").json()["items"]])
buy(third, mid, tier_id=mtier["id"])
items = [p["id"] for p in c.get(f"/feed/community/{mid}", headers=H(third)).json()["items"]]
check("a paying member sees the article", art["id"] in items and c.get(f"/posts/{art['id']}", headers=H(third)).status_code == 200)

print()
print("8. Events")
c.patch(f"/communities/{mid}/spaces", headers=H(owner), json={"spaces": {"events": True, "marketplace": True}}).raise_for_status()
start = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
r = c.post(f"/communities/{mid}/events", headers=H(other), json={"title": "Demo meetup", "starts_at": start, "timezone": "Africa/Kampala"})
check("a member cannot create an event", r.status_code == 403)
check("an unknown time zone is refused", c.post(f"/communities/{mid}/events", headers=H(owner),
      json={"title": "Demo meetup", "starts_at": start, "timezone": "Mars/Base"}).status_code == 400)
ev = c.post(f"/communities/{mid}/events", headers=H(owner), json={
    "title": "Demo meetup", "starts_at": start, "timezone": "Africa/Kampala", "mode": "online",
    "online_url": "https://meet.example.com/demo", "capacity": 3, "waitlist": True}).json()
check("the owner creates a free event (3 places)", ev.get("capacity") == 3, ev)
crowd = [register(f"Demo Guest {i}") for i in range(6)]
for u in crowd:
    c.post(f"/communities/{mid}/join", headers=H(u))
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    states = list(pool.map(lambda u: c.post(f"/events/{ev['id']}/register", headers=H(u)).json().get("my_status"), crowd))
check("6 at once for 3 places: 3 registered, 3 waitlisted, never more",
      states.count("registered") == 3 and states.count("waitlisted") == 3
      and psql(f"select registered_count from community.community_events where id='{ev['id']}'") == "3", states)
lucky = crowd[states.index("registered")]
seen = c.get(f"/events/{ev['id']}", headers=H(lucky)).json()
check("the joining link is for the registered", seen["online_url"] == "https://meet.example.com/demo"
      and c.get(f"/events/{ev['id']}", headers=H(other)).json()["online_url"] is None)
c.post(f"/events/{ev['id']}/unregister", headers=H(lucky))
first_wait = psql(f"select user_id from community.event_registrations where event_id='{ev['id']}' and status='registered' "
                  f"order by updated_at desc limit 1")
check("a place given back goes to the first on the waitlist", psql(
    f"select count(*) from community.event_registrations where event_id='{ev['id']}' and status='registered'") == "3"
      and first_wait in [u["id"] for u in crowd], first_wait)
paid_ev = c.post(f"/communities/{mid}/events", headers=H(owner), json={
    "title": "Demo workshop", "starts_at": start, "timezone": "UTC", "capacity": 1, "ticket_price": 8}).json()
check("a paid event: owner $8, ticket $9.60", paid_ev.get("ticket_price") == "9.60", paid_ev)
check("free registration is refused for it", c.post(f"/events/{paid_ev['id']}/register", headers=H(other)).status_code == 402)
qa = c.post(f"/communities/{mid}/quote", headers=H(other), json={"event_id": paid_ev["id"]}).json()
qb = c.post(f"/communities/{mid}/quote", headers=H(crowd[0]), json={"event_id": paid_ev["id"]}).json()
for user, qq in ((other, qa), (crowd[0], qb)):
    pp = c.post("/payments/community/checkout", headers=H(user), json={"quote_id": qq["quote_id"]}).json()
    c.post(f"/payments/{pp['payment_id']}/mock-settle", headers=H(user))
    qq["pid"] = pp["payment_id"]
check("the first payer has the one place", psql(
    f"select status from community.event_registrations where event_id='{paid_ev['id']}' and user_id='{other['id']}'") == "registered")
check("the second, paid after it sold out, is refunded in full to their balance",
      psql(f"select status from community.community_payments where payment_ref='{qb['pid']}'") == "refunded"
      and psql(f"select registered_count from community.community_events where id='{paid_ev['id']}'") == "1")
check("a third person cannot even get a price", c.post(f"/communities/{mid}/quote", headers=H(crowd[1]),
                                                       json={"event_id": paid_ev["id"]}).status_code == 409)
r = c.post(f"/events/{paid_ev['id']}/cancel", headers=H(owner))
check("cancelling refunds the paid tickets", r.status_code == 200 and psql(
    f"select status from community.community_payments where payment_ref='{qa['pid']}'") == "refunded")

print()
print("9. Listings, analytics, reminders")
prod = c.post("/commerce/products", headers=H(third), json={"title": f"Demo jam {RUN}", "description": "Homemade",
                                                            "kind": "product", "vendor_price": 5})
if prod.status_code == 201:
    r = c.post(f"/communities/{mid}/listings", headers=H(third), json={"product_id": prod.json()["id"]})
    check("a member lists their own product", r.status_code == 201)
    check("it shows in the community", any(i["id"] == prod.json()["id"] for i in c.get(f"/communities/{mid}/listings", headers=H(other)).json()["items"]))
    check("nobody lists somebody else's", c.post(f"/communities/{mid}/listings", headers=H(other),
                                                 json={"product_id": prod.json()["id"]}).status_code == 404)
else:
    print(f"  SKIP marketplace not available here ({prod.status_code})")
a_owner = c.get(f"/communities/{cid}/analytics", headers=H(owner)).json()
check("the owner's analytics include money", a_owner.get("money") and a_owner["money"]["sales"] >= 3, a_owner.get("money"))
c.post(f"/communities/{cid}/members/{mod['id']}", headers=H(owner), json={"action": "approve"})
psql(f"insert into community.memberships (community_id, user_id, role, status, joined_at, notify_posts) values "
     f"('{cid}', '{mod['id']}', 'moderator', 'active', now(), true) on conflict do nothing")
a_mod = c.get(f"/communities/{cid}/analytics", headers=H(mod)).json()
check("a moderator's analytics have no money", "new_members" in a_mod and a_mod.get("money") is None, a_mod)
check("members see no analytics", c.get(f"/communities/{cid}/analytics", headers=H(third)).status_code == 403)
psql(f"update community.community_entitlements set period_end = now() + interval '2 days', reminded_for = null "
     f"where community_id='{cid}' and user_id='{third['id']}'")
run_due()
run_due()
check("one renewal reminder per period", psql(
    f"select count(*) from messaging.notifications where user_id='{third['id']}' and kind='community_renewal_due'") == "1")
c.patch("/admin/community-settings", headers=H(admin), json={"community_markup_pct": 10})
q10 = c.post(f"/communities/{cid}/quote", headers=H(crowd[2]), json={"tier_id": monthly["id"]}).json()
check("a new markup applies to new quotes (10% -> $11.00)", q10.get("total") == "11.00", q10)
c.patch("/admin/community-settings", headers=H(admin), json={"community_markup_pct": 20})

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
