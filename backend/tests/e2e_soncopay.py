"""Sonco Pay payments, Kaluta KYC, the payout wallet and withdrawals, end to end.

Against tests/fake_sonco_kaluta.py (run it first) and the stack started with
tests/compose.sonco-test.yml. What it holds the code to:

* a payment is settled only when Sonco Pay, read back, says this payment was
  paid in full - a forged or unsigned webhook settles nothing;
* "verified" comes from Kaluta's own answer, never from the unsigned webhook;
* a wallet is usable only after the e-mail link AND an admin, and the admin
  is recorded; nobody approves their own; a new address starts again;
* a withdrawal pays once: a double click, five parallel requests or a retry
  are one payout; a refusal or a failed payout gives the balance back, once.
"""
import json
import random
import re
import string
import subprocess
import sys
import threading
import time
from datetime import date
from decimal import Decimal

import httpx

sys.path.insert(0, r"C:\Users\godso\trans-proxy\sdks\python\src")
from soncopay.webhooks import sign_v2  # noqa: E402

BASE = "http://localhost:8200/api"
LEDGER = "http://localhost:8210"
FAKE = "http://localhost:8790"
SECRET = "whsec_fake_for_e2e"
PASSWORD = "Sup3rStrong!Pass"

c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{t}@example.com"
    r = c.post("/auth/register", json={
        "email": email, "handle": f"u{t}", "display_name": name,
        "password": PASSWORD, "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG",
    })
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "email": email}


def login(user):
    r = c.post("/auth/login", json={"email": user["email"], "password": PASSWORD})
    r.raise_for_status()
    user["token"] = r.json()["tokens"]["access_token"]


def H(user):
    return {"Authorization": f"Bearer {user['token']}"}


def psql(sql):
    subprocess.run(
        ["docker", "exec", "kaluta-postgres", "sh", "-c", f'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "{sql}"'],
        check=True, capture_output=True,
    )


def mail_token(raw):
    """The confirmation token out of a captured message, decoded as a mail client would."""
    import email
    from email import policy
    text = email.message_from_string(raw, policy=policy.default).get_content()
    return re.search(r"token=([A-Za-z0-9_\-]+)", text).group(1)


def fake_state():
    return httpx.get(f"{FAKE}/_ctl/state").json()


def ctl(path, body):
    httpx.post(f"{FAKE}/_ctl/{path}", json=body).raise_for_status()


def webhook(event, data, secret=SECRET):
    raw = json.dumps({"event": event, "created_at": None, "data": data}).encode()
    return c.post("/payments/webhook/soncopay", content=raw, headers={
        "Content-Type": "application/json", "X-SoncoPay-Event": event,
        "X-SoncoPay-Signature": sign_v2(raw, secret),
    })


def available(user):
    return Decimal(httpx.get(f"{LEDGER}/internal/wallets/{user['id']}").json()["available"])


def credit(user, amount):
    httpx.post(f"{LEDGER}/internal/post/creator-revenue", json={
        "creator_id": user["id"], "amount": str(amount),
        "idempotency_key": "e2e-" + "".join(random.choices(string.ascii_lowercase, k=10)),
    }).raise_for_status()


member = register("Demo Member")
admin = register("Demo Admin")
psql(f"UPDATE auth.users SET role='admin' WHERE id='{admin['id']}'")
login(admin)

print("Paying through Sonco Pay")
r = c.post("/payments/checkout", headers=H(member), json={
    "purpose": "kyc_fee", "amount": "10.00", "rail": "mock", "return_path": "/dashboard?tab=verification",
})
check("checkout opens a Sonco Pay payment", r.status_code == 201 and r.json()["rail"] == "soncopay", r.text[:200])
pay = r.json()
check("and hands back its page", (pay.get("checkout_url") or "").startswith("https://fake.soncopay.test/"), pay)
sp = next(p for p in fake_state()["payments"].values() if p["order_id"] == pay["payment_id"])
check("the return lands on Kinjy with the payment id",
      sp["return_url"].endswith(f"/dashboard?tab=verification&payment={pay['payment_id']}"), sp["return_url"])
check("Sonco Pay is told where to ring", sp["callback_url"].endswith("/api/payments/webhook/soncopay"), sp["callback_url"])
check("an outside return path is refused",
      c.post("/payments/checkout", headers=H(member), json={
          "purpose": "kyc_fee", "amount": "10.00", "return_path": "//evil.example"}).status_code == 422)
check("the mock shortcut is off", c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=H(member)).status_code == 403)

paid_claim = {**sp, "status": "paid"}
check("a webhook with a wrong signature is refused",
      webhook("payment.paid", paid_claim, secret="whsec_wrong").status_code == 400)
r = webhook("payment.paid", paid_claim)
check("a signed 'paid' for a payment Sonco Pay still has as pending settles nothing",
      r.status_code == 200 and r.json().get("status") == "pending", r.text[:200])
check("so the fee is not paid", c.get("/kyc/status", headers=H(member)).json()["fee_paid"] is False)

ctl("payment", {"id": sp["id"], "set": {"amount": "1.00", "status": "paid"}})
webhook("payment.paid", {**sp, "status": "paid"})
check("paid, but for $1 instead of $10: still nothing", c.get("/kyc/status", headers=H(member)).json()["fee_paid"] is False)

ctl("payment", {"id": sp["id"], "set": {"amount": "10.00", "status": "paid"}})
r = webhook("payment.paid", {"id": sp["id"], "order_id": pay["payment_id"]})
check("paid in full: settled", r.json().get("status") == "paid", r.text[:200])
check("the fee is paid", c.get("/kyc/status", headers=H(member)).json()["fee_paid"] is True)
check("the same webhook again changes nothing",
      webhook("payment.paid", {"id": sp["id"], "order_id": pay["payment_id"]}).json().get("status") == "paid")
check("the member's page reads it as paid",
      c.get(f"/payments/intents/{pay['payment_id']}", headers=H(member)).json()["status"] == "paid")

print()
print("Kaluta KYC")
r = c.post("/kyc/start", headers=H(member))
check("starting opens a Kaluta session", r.status_code == 200 and "fake.kalutakyc.test" in (r.json().get("verification_url") or ""), r.text[:200])
first = r.json()
session = next(iter(s for s in fake_state()["sessions"].values() if s["verification_url"] == first["verification_url"]))
check("with the member's name and date of birth",
      session["request"].get("first_name") == "Demo" and session["request"].get("date_of_birth") == "1990-02-02", session["request"])
again = c.post("/kyc/start", headers=H(member)).json()
check("starting again reopens it, without spending an attempt",
      again.get("resumed") and again["attempt"] == first["attempt"] == 1, again)
r = c.post("/kyc/webhook/kaluta", json={"session_id": session["session_id"], "status": "approved"})
check("an unsigned 'approved' while Kaluta says 'created' verifies nobody",
      r.status_code == 200 and c.get("/kyc/status", headers=H(member)).json()["status"] == "pending")
check("an invented session is ignored",
      c.post("/kyc/webhook/kaluta", json={"session_id": "ses_invented"}).json().get("ignored") is True)
ctl("session", {"id": session["session_id"], "status": "approved"})
c.post("/kyc/webhook/kaluta", json={"session_id": session["session_id"]})
check("Kaluta says approved: verified", c.get("/kyc/status", headers=H(member)).json()["status"] == "verified")

print()
print("The payout wallet")
check("not an address: refused",
      c.put("/payments/payout-wallet", headers=H(member), json={"address": "0x" + "z" * 40}).status_code == 422)
addr = "0x" + "".join(random.choices("0123456789abcdef", k=40))
mails_before = len(fake_state()["mails"])
r = c.put("/payments/payout-wallet", headers=H(member), json={"address": addr})
check("saved, waiting for the e-mail", r.status_code == 200 and r.json()["wallet"]["status"] == "pending_email", r.text[:200])
mails = fake_state()["mails"][mails_before:]
check("one confirmation e-mail went out", len(mails) == 1, len(mails))
token = mail_token(mails[0]) if mails else ""
check("saving the same address again sends nothing new",
      c.put("/payments/payout-wallet", headers=H(member), json={"address": addr}).json()["changed"] is False
      and len(fake_state()["mails"]) == mails_before + 1)
check("withdrawing is blocked until it is approved",
      "wallet_pending_email" in c.get("/payments/withdraw/quote", headers=H(member)).json()["blocked_by"])
check("a wrong token confirms nothing",
      c.post("/payments/payout-wallet/confirm", json={"token": "x" * 40}).status_code == 404)
r = c.post("/payments/payout-wallet/confirm", json={"token": token})
check("the link from the e-mail confirms it, signed out", r.status_code == 200 and r.json()["wallet"]["status"] == "pending_admin", r.text[:200])
check("and only once", c.post("/payments/payout-wallet/confirm", json={"token": token}).status_code == 404)
check("a member cannot approve", c.get("/admin/payouts/wallets", headers=H(member)).status_code == 403)
queue = c.get("/admin/payouts/wallets", headers=H(admin)).json()["items"]
mine = next((w for w in queue if w["user_id"] == member["id"]), None)
check("the admin sees it in the queue, with the member's name", mine and mine["display_name"] == "Demo Member", queue[:2])

# The admin's own wallet, which they may not approve themselves.
c.put("/payments/payout-wallet", headers=H(admin), json={"address": "0x" + "1" * 40})
c.post("/payments/payout-wallet/confirm", json={"token": mail_token(fake_state()["mails"][-1])})
own = next(w for w in c.get("/admin/payouts/wallets", headers=H(admin)).json()["items"] if w["user_id"] == admin["id"])
check("an admin cannot approve their own wallet",
      c.post(f"/admin/payouts/wallets/{own['id']}/approve", headers=H(admin)).status_code == 403)

r = c.post(f"/admin/payouts/wallets/{mine['id']}/approve", headers=H(admin))
check("approved", r.status_code == 200 and r.json()["wallet"]["status"] == "confirmed", r.text[:200])
check("and it says which admin", r.json()["wallet"]["reviewed_by"] == admin["id"]
      and r.json()["wallet"]["reviewed_by_name"] == "Demo Admin", r.json())
check("approving twice is refused",
      c.post(f"/admin/payouts/wallets/{mine['id']}/approve", headers=H(admin)).status_code == 409)

print()
print("Withdrawing")
quote = c.get("/payments/withdraw/quote", headers=H(member)).json()
check("with nothing earned, below the minimum", quote["blocked_by"] == ["below_minimum"], quote)
check("the minimum is Sonco Pay's $5 plus the fee", quote["minimum"] == "5.50", quote["minimum"])
credit(member, 100)
balance = available(member)
quote = c.get("/payments/withdraw/quote", headers=H(member)).json()
check("with earnings, eligible", quote["eligible"] and Decimal(quote["available"]) == balance, quote)
check("the member receives the balance minus the fee",
      Decimal(quote["you_receive"]) == balance - Decimal("0.50"), quote)

calls_before = fake_state()["payout_calls"]
results = []


def hit():
    results.append(c.post("/payments/withdraw", headers=H(member)).status_code)


threads = [threading.Thread(target=hit) for _ in range(5)]
[t.start() for t in threads]
[t.join() for t in threads]
check("five withdrawals at once: exactly one goes through", sorted(results).count(201) == 1, results)
check("one payout asked of Sonco Pay", fake_state()["payout_calls"] - calls_before == 1,
      fake_state()["payout_calls"] - calls_before)
check("the balance is taken once", available(member) == 0, available(member))
history = c.get("/payments/withdrawals", headers=H(member)).json()["items"]
wdr = history[0]
check("submitted, net of the fee, to the approved address",
      wdr["status"] == "submitted" and wdr["address"] == addr and Decimal(wdr["net"]) == balance - Decimal("0.50"), wdr)
po = next(p for p in fake_state()["payouts"].values() if p["reference"] == wdr["id"])
check("Sonco Pay got USDT on BSC with the withdrawal id as reference",
      po["asset"] == "USDT" and po["network"] == "BSC" and Decimal(po["amount"]) == Decimal(wdr["net"]), po)

r = webhook("payout.completed", {"id": po["id"], "reference": wdr["id"], "status": "completed", "tx_hash": "0xforged"})
check("a 'completed' webhook while Sonco Pay says pending completes nothing",
      c.get("/payments/withdrawals", headers=H(member)).json()["items"][0]["status"] == "submitted")
ctl("payout", {"id": po["id"], "set": {"status": "completed", "tx_hash": "0xabc123"}})
webhook("payout.completed", {"id": po["id"], "reference": wdr["id"]})
done = c.get("/payments/withdrawals", headers=H(member)).json()["items"][0]
check("completed, with the real transaction hash", done["status"] == "completed" and done["tx_hash"] == "0xabc123", done)

print()
print("When the payout fails")
credit(member, 50)
balance = available(member)
r = c.post("/payments/withdraw", headers=H(member))
check("a second withdrawal goes through", r.status_code == 201, r.text[:200])
second = r.json()["withdrawal"]
po2 = next(p for p in fake_state()["payouts"].values() if p["reference"] == second["id"])
ctl("payout", {"id": po2["id"], "set": {"status": "failed", "last_error": "Address rejected"}})
webhook("payout.failed", {"id": po2["id"], "reference": second["id"]})
webhook("payout.failed", {"id": po2["id"], "reference": second["id"]})
failed = c.get("/payments/withdrawals", headers=H(member)).json()["items"][0]
check("failed, with the reason", failed["status"] == "failed" and "Address rejected" in (failed["failure_reason"] or ""), failed)
check("the balance is back, once, even with the webhook twice", available(member) == balance, (available(member), balance))

ctl("mode", {"payout_mode": "refuse"})
r = c.post("/payments/withdraw", headers=H(member))
refused = r.json().get("withdrawal", {})
check("Sonco Pay refusing outright: failed at once", r.status_code == 201 and refused.get("status") == "failed", r.text[:200])
check("and the balance untouched", available(member) == balance, available(member))

ctl("mode", {"payout_mode": "unreachable"})
r = c.post("/payments/withdraw", headers=H(member))
unsure = r.json().get("withdrawal", {})
check("Sonco Pay not answering: the amount stays reserved, nothing given back yet",
      unsure.get("status") == "reserved" and available(member) == 0, (unsure, available(member)))
ctl("mode", {"payout_mode": "ok"})
c.post("/admin/payouts/withdrawals/reconcile", headers=H(admin))
after = c.get("/payments/withdrawals", headers=H(member)).json()["items"][0]
check("once it answers, the same withdrawal is sent - once", after["id"] == unsure["id"] and after["status"] == "submitted",
      after)
check("one payout for it at Sonco Pay",
      sum(1 for p in fake_state()["payouts"].values() if p["reference"] == unsure["id"]) == 1)

print()
print("Changing the wallet")
r = c.put("/payments/payout-wallet", headers=H(member), json={"address": "0x" + "2" * 40})
check("a new address goes back to e-mail confirmation", r.json()["wallet"]["status"] == "pending_email", r.text[:200])
check("and the approval is gone", r.json()["wallet"]["reviewed_by_name"] is None)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
