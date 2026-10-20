"""A sponsor earns only while their own verification is active.

- sponsor not verified: the member's payment pays the sponsor nothing; the 20%
  is Kinjy's, booked as unclaimed; no "You earned" notification;
- sponsor verified: the next payment pays them their 20%;
- verification lapsed (date passed) or withdrawn: back to Kinjy;
- the sponsor link itself is untouched throughout - it is the earning that
  waits, not the relationship.

Mock payments (no provider configured locally). auth-service on 8201.
"""
import random
import string
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
AUTH = "http://localhost:8201"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name, referral_code=None):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG"}
    if referral_code:
        body["referral_code"] = referral_code
    r = c.post("/auth/register", json=body)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "code": d["user"].get("referral_code")}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def set_kyc(user, verified, until=None):
    params = {"verified": str(verified).lower()}
    if until:
        params["until"] = until.isoformat()
    httpx.post(f"{AUTH}/internal/users/{user['id']}/kyc", params=params, timeout=10).raise_for_status()


def pay(member=None):
    # A member pays the verification fee once: each payment is a new invitee.
    member = member or register("Demo Invited", referral_code=sponsor["code"])
    pay = c.post("/payments/checkout", headers=H(member), json={"purpose": "kyc_fee", "amount": "10.00"}).json()
    r = c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=H(member))
    time.sleep(1.5)
    return r.status_code


def earned(sponsor):
    page = c.get("/commissions?limit=50", headers=H(sponsor)).json()
    return sum((Decimal(i["amount"]) for i in page["items"]), Decimal("0"))


def earned_notes(sponsor):
    items = c.get("/notifications", headers=H(sponsor)).json()
    items = items.get("items", items if isinstance(items, list) else [])
    return [n for n in items if n.get("kind") == "commission_earned"]


sponsor = register("Demo Sponsor")
member = register("Demo Invited", referral_code=sponsor["code"])

print("Sponsor not verified")
link = httpx.get(f"{AUTH}/internal/sponsor/{member['id']}").json()
check("the ledger is told there is nobody to pay", link["sponsor_id"] is None and link["source"] == "unverified", link)
check("the member's payment goes through", pay(member) == 200)
check("the sponsor earns nothing", earned(sponsor) == 0, earned(sponsor))
check("and is not told they earned", earned_notes(sponsor) == [])
user = httpx.get(f"{AUTH}/internal/users/{member['id']}").json()
check("the sponsor link is kept", user.get("invited_by") == sponsor["id"], user)

print()
print("Sponsor verified")
set_kyc(sponsor, True, datetime.now(timezone.utc) + timedelta(days=365))
link = httpx.get(f"{AUTH}/internal/sponsor/{member['id']}").json()
check("now they are the one paid", link["sponsor_id"] == sponsor["id"], link)
pay()
check("they earn their 20% ($2.00)", earned(sponsor) == Decimal("2.00"), earned(sponsor))
check("and are told", len(earned_notes(sponsor)) == 1, earned_notes(sponsor))

print()
print("Verification lapsed, then withdrawn")
set_kyc(sponsor, True, datetime.now(timezone.utc) - timedelta(days=1))
check("past its date: nobody to pay",
      httpx.get(f"{AUTH}/internal/sponsor/{member['id']}").json()["sponsor_id"] is None)
pay()
check("nothing more earned", earned(sponsor) == Decimal("2.00"), earned(sponsor))
set_kyc(sponsor, True)
check("verified with no end date: paid again",
      httpx.get(f"{AUTH}/internal/sponsor/{member['id']}").json()["sponsor_id"] == sponsor["id"])
set_kyc(sponsor, False)
check("withdrawn: nobody to pay", httpx.get(f"{AUTH}/internal/sponsor/{member['id']}").json()["sponsor_id"] is None)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
