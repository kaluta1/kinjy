"""A verified member gets the badge; their sponsor sees what they earned.

Run in mock mode (no payment provider configured locally): the fee is settled
with the test shortcut, then the identity check's result is posted the way
payment-service posts it.
"""
import random
import string
import sys
import time
from datetime import date
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
PAYMENT = "http://localhost:8211"
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
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "handle": f"u{t}",
            "code": d["user"].get("referral_code")}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


sponsor = register("Demo Sponsor")
member = register("Demo Invited", referral_code=sponsor["code"])

print("The sponsor sees what they earned")
# A sponsor earns only once their own verification is active
# (e2e_commission_requires_kyc.py covers the other side).
httpx.post(f"http://localhost:8201/internal/users/{sponsor['id']}/kyc", params={"verified": "true"}).raise_for_status()
pay = c.post("/payments/checkout", headers=H(member), json={"purpose": "kyc_fee", "amount": "10.00"}).json()
r = c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=H(member))
check("the fee is paid (test mode)", r.status_code == 200, r.text[:160])
time.sleep(1.5)
items = c.get("/notifications", headers=H(sponsor)).json()
items = items.get("items", items if isinstance(items, list) else [])
earned = next((n for n in items if n.get("kind") == "commission_earned"), None)
check("the sponsor is notified of $2.00", earned and earned["title"] == "You earned $2.00", earned)
check("naming the member and what they did",
      earned and "Demo Invited" in (earned.get("body") or "") and "verification fee" in earned["body"], earned)
page = c.get("/commissions?limit=10", headers=H(sponsor)).json()
first = page["items"][0] if page["items"] else {}
check("the earnings list shows it, from whom",
      Decimal(first.get("amount", "0")) == Decimal("2.00") and first.get("from_name") == "Demo Invited", first)
check("under verification fees", page["by_source"].get("kyc_fee") in ("2.00", "2"), page["by_source"])

print()
print("The badge")
check("not verified yet: no badge", c.get(f"/users/{member['handle']}").json()["verified"] is False)
r = httpx.post(f"{PAYMENT}/internal/kyc/callback", params={"user_id": member["id"], "outcome": "verified"})
check("the identity check passes", r.status_code == 200, r.text[:160])
check("the public profile shows the badge", c.get(f"/users/{member['handle']}").json()["verified"] is True)
post = c.post("/posts", headers=H(member), json={"body": "Verified now", "visibility": "public"}).json()
author = c.get(f"/posts/{post['id']}").json().get("author") or {}
check("and so do their posts", author.get("verified") is True, author)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
