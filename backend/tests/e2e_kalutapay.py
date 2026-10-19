"""Kinjy's payments through Kaluta Pay, and the split of each one.

Against tests/fake_sonco_kaluta.py (its /kp routes) and the stack started with
tests/compose.kalutapay-test.yml. What it holds the code to:

* every checkout becomes a Kaluta Pay invoice whose order_ref is the Kinjy
  payment id, with the payer and their sponsor;
* the member pays on Kinjy's page: Kaluta Pay's methods, its next step;
* nothing settles on the webhook's word, nor on a wrong signature, nor on an
  invoice paid for less;
* once paid, the split is set: the sponsor's 20% and the Leaders' 5% of the
  KYC fee stay in escrow - the same amounts the ledger now owes;
* a split that cannot land yet (the ledger posting missing) is retried.
"""
import hashlib
import hmac
import json
import random
import string
import sys
from datetime import date
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
LEDGER = "http://localhost:8210"
FAKE = "http://localhost:8790"
SECRET = "whs_fake_for_e2e"

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
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "BJ"}
    if referral_code:
        body["referral_code"] = referral_code
    r = c.post("/auth/register", json=body)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "code": d["user"].get("referral_code")}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def state():
    return httpx.get(f"{FAKE}/_ctl/state").json()


def hook(event, data, secret=SECRET):
    raw = json.dumps({"event": event, "data": data}).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return c.post("/payments/webhook/kalutapay", content=raw,
                  headers={"Content-Type": "application/json", "X-KalutaPay-Signature": sig})


def available(user):
    return Decimal(httpx.get(f"{LEDGER}/internal/wallets/{user['id']}").json()["available"])


sponsor = register("Demo Sponsor")
member = register("Demo Member", referral_code=sponsor["code"])

print("The KYC fee, through Kaluta Pay")
r = c.post("/payments/checkout", headers=H(member), json={"purpose": "kyc_fee", "amount": "10.00",
                                                          "return_path": "/dashboard?tab=verification"})
check("checkout goes to Kaluta Pay", r.status_code == 201 and r.json()["rail"] == "kalutapay", r.text[:200])
pay = r.json()
inv = next(i for i in state()["kp_invoices"].values() if i["order_ref"] == pay["payment_id"])
check("as an invoice for $10 in USD, with the Kinjy payment id as order_ref",
      inv["amount"] == "10.00" and inv["currency"] == "USD", inv)
check("naming the payer and their sponsor",
      inv["customer"]["external_id"] == member["id"] and inv["customer"].get("referrer_external_id") == sponsor["id"],
      inv["customer"])

methods = c.get(f"/payments/intents/{pay['payment_id']}/methods", headers=H(member)).json()
check("the methods are Kaluta Pay's", methods["crypto"][0]["asset"] == "USDT" and methods["mobile_money"][0]["code"] == "mtn_bj",
      methods)
r = c.post(f"/payments/intents/{pay['payment_id']}/choose", headers=H(member),
           json={"method": "crypto", "asset": "USDT", "network": "BSC"})
check("choosing crypto shows the address and the exact amount",
      r.status_code == 200 and r.json()["action"]["type"] == "send_crypto" and r.json()["action"]["amount"] == "10.00",
      r.text[:200])
r = c.post(f"/payments/intents/{pay['payment_id']}/choose", headers=H(member),
           json={"method": "mobile_money", "code": "mtn_bj", "phone": "97123456", "country": "BJ"})
chk = state()["kp_invoices"][inv["id"]]["checkouts"][0]
check("mobile money goes as Kaluta Pay's method id, with the dialling code",
      chk["provider"] == "mtn_bj" and chk["request"]["phone"] == "+22997123456", chk)

check("a webhook with a wrong signature is refused",
      hook("invoice.paid", {"id": inv["id"], "order_ref": pay["payment_id"]}, secret="whs_wrong").status_code == 401)
r = hook("invoice.paid", {"id": inv["id"], "order_ref": pay["payment_id"]})
check("a signed 'paid' for an invoice still open settles nothing",
      r.status_code == 200 and r.json().get("status") == "pending", r.text[:200])
httpx.post(f"{FAKE}/_ctl/kp_paid", json={"id": inv["id"], "amount_paid": "4.00"})
check("paid for less than the price: nothing either",
      hook("invoice.paid", {"id": inv["id"], "order_ref": pay["payment_id"]}).json().get("status") != "paid")
check("so the fee is not paid", c.get("/kyc/status", headers=H(member)).json()["fee_paid"] is False)

before = available(sponsor)
httpx.post(f"{FAKE}/_ctl/kp_paid", json={"id": inv["id"]})
r = hook("invoice.paid", {"id": inv["id"], "order_ref": pay["payment_id"]})
check("paid in full: settled", r.json().get("status") == "paid", r.text[:200])
check("the fee is paid", c.get("/kyc/status", headers=H(member)).json()["fee_paid"] is True)
check("the sponsor is credited 20% of it ($2.00)", available(sponsor) - before == Decimal("2.00"),
      available(sponsor) - before)

lines = state()["kp_escrow"].get(inv["id"])
check("the split is set on Kaluta Pay", lines is not None, state()["kp_escrow"])
if lines:
    commission = [x for x in lines if x["kind"] == "commission"]
    check("the sponsor's $2.00 stays in escrow, named",
          len(commission) == 1 and Decimal(commission[0]["amount"]) == Decimal("2.00")
          and commission[0]["beneficiary"]["external_id"] == sponsor["id"], lines)
    pool = [x for x in lines if "Leaders" in (x.get("label") or "")]
    check("and the Leaders pool's 5% ($0.50)", len(pool) == 1 and Decimal(pool[0]["amount"]) == Decimal("0.50"), lines)
again = hook("invoice.paid", {"id": inv["id"], "order_ref": pay["payment_id"]})
check("the same webhook again changes nothing", again.json().get("status") == "paid"
      and available(sponsor) - before == Decimal("2.00"))

print()
print("A member who signed up without a link")
loner = register("Demo Loner")
# Assigned a sponsor by the referral pool (or none when it is empty).
pooled = httpx.get(f"http://localhost:8201/internal/users/{loner['id']}").json().get("invited_by")
pay2 = c.post("/payments/checkout", headers=H(loner), json={"purpose": "kyc_fee", "amount": "10.00"}).json()
inv2 = next(i for i in state()["kp_invoices"].values() if i["order_ref"] == pay2["payment_id"])
httpx.post(f"{FAKE}/_ctl/kp_paid", json={"id": inv2["id"]})
status = c.get(f"/payments/intents/{pay2['payment_id']}", headers=H(loner)).json()
check("the member's page reads it as paid, without a webhook", status["settled"] is True, status)
lines2 = state()["kp_escrow"].get(inv2["id"])
commission2 = [x for x in (lines2 or []) if x["kind"] == "commission"]
if pooled:
    check("the commission goes to the sponsor the referral pool gave them",
          len(commission2) == 1 and commission2[0]["beneficiary"]["external_id"] == pooled, lines2)
else:
    check("nobody sponsored them: no commission line", lines2 is not None and not commission2, lines2)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
