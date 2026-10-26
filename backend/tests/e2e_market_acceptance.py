"""Marketplace final acceptance, settlement and refunds, end to end (27/10).

Part A runs on the local test rail (no provider configured). Part B runs
payment-service through Kaluta Pay against tests/fake_sonco_kaluta.py, so the
held funds, the distributions and their failures are the real code paths.
No real money, provider or e-mail is involved.

    python backend/tests/fake_sonco_kaluta.py &
    python backend/tests/e2e_market_acceptance.py
"""
import random
import string
import subprocess
import sys
import threading
import time
from datetime import date
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
COMMERCE = "http://localhost:8209"
LEDGER = "http://localhost:8210"
FAKE = "http://localhost:8790"
POLICY = "2026-10-27"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def register(name):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"],
            "email": f"{t}@example.com"}


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def compose(*args):
    subprocess.run(["docker", "compose", *args], capture_output=True, text=True, timeout=300)


def healthy(port):
    for _ in range(60):
        try:
            if httpx.get(f"http://localhost:{port}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)


def wallet(user):
    w = httpx.get(f"{LEDGER}/wallet", headers=user["h"], timeout=20).json()
    return Decimal(w["available"]), Decimal(w["pending"])


def order(user, oid):
    return c.get(f"/commerce/orders/{oid}", headers=user["h"]).json()


def wait_for(fn, timeout=40):
    end = time.time() + timeout
    value = fn()
    while not value and time.time() < end:
        time.sleep(1)
        value = fn()
    return value


SHIP = {"full_name": "Demo Buyer", "line1": "1 Demo Street", "city": "Demo City", "country": "US"}


def product(seller, kind="product", price="20.00", **extra):
    body = {"title": f"Demo {kind} {tag()}", "description": "A demo listing.", "kind": kind,
            "vendor_price": price, "country": "US", "images": [], **extra}
    if kind == "product":
        body["stock"] = 50
    r = c.post("/commerce/products", headers=seller["h"], json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def buy(buyer, pid, *, ack=True, shipping=True):
    body = {"product_id": pid, "quantity": 1}
    if shipping:
        body["shipping"] = SHIP
    if ack:
        body.update(acknowledge_policy=True, policy_version=POLICY)
    return c.post("/commerce/orders", headers=buyer["h"], json=body)


def pay(buyer, o):
    r = c.post("/payments/checkout", headers=buyer["h"],
               json={"purpose": "order", "amount": o["customer_price"], "reference": o["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def confirm(user, oid, ack=True):
    return c.post(f"/commerce/orders/{oid}/confirm-delivery", headers=user["h"],
                  json={"acknowledge_final": ack, "policy_version": POLICY if ack else None})


# ================================================================== Part A: test rail
print("Part A - the test rail (development machine, no provider)")
seller, buyer, other = register("Demo Seller"), register("Demo Buyer"), register("Demo Other")
admin = register("Demo Admin")
psql(f"update auth.users set role='admin' where id='{admin['id']}'")
login = c.post("/auth/login", json={"email": admin["email"], "password": "Sup3rStrong!Pass"}).json()
admin["h"] = {"Authorization": f"Bearer {(login.get('tokens') or login)['access_token']}"}

print()
print("Listing disclosures")
pid = product(seller, guarantee={"scope": "Manufacturing defects", "duration": "6 months",
                                 "exclusions": "Water damage", "claim_contact": "seller@example.com"})
p = c.get(f"/commerce/products/{pid}", headers=buyer["h"]).json()
check("the guarantee is shown before purchase", (p["protection"]["guarantee"] or {}).get("duration") == "6 months", p)
check("insurance is stated as none", p["protection"]["insurance"] is None)
check("and that Kinjy does not arbitrate", p["protection"]["kinjy_arbitrates"] is False)
r = c.post("/commerce/products", headers=seller["h"], json={"title": "Demo service x", "kind": "service",
                                                              "vendor_price": "30.00", "images": []})
check("a service without completion terms is refused", r.status_code == 422, r.text)
sid = product(seller, "service", "30.00", service_terms="A one-hour consultation; the buyer receives notes.")
policy = c.get("/commerce/marketplace/policy").json()
check("the policy is public, versioned and says there is no automatic release",
      policy["version"] == POLICY and policy["automatic_release"] is False, policy)

print()
print("Checkout acknowledgement")
check("no order without acknowledging the policy", buy(buyer, pid, ack=False).status_code == 422)
o1 = buy(buyer, pid).json()
check("acknowledged, the order is placed", o1["status"] == "pending" and o1["policy_version"] == POLICY, o1)
check("the acknowledgement is stored with the buyer and version",
      psql(f"select buyer_id||'|'||policy_version from commerce.policy_acks where order_id='{o1['id']}' and stage='checkout'")
      == f"{buyer['id']}|{POLICY}")
pay1 = pay(buyer, o1)
check("the test rail is used on a development machine", pay1["rail"] == "mock" and pay1["mock"] is True, pay1)
c.post(f"/payments/{pay1['payment_id']}/mock-settle", headers=buyer["h"])
v = order(seller, o1["id"])
check("paid: held, not accepted, not settled", v["status"] == "in_escrow" and v["acceptance"]["accepted"] is False
      and v["settlement"]["status"] == "none", v)
check("the seller is told to keep the product",
      v["handover"] == "Awaiting buyer confirmation. Do not release the product or final service deliverables.", v)
check("the buyer does not see the seller's handover line", order(buyer, o1["id"])["handover"] is None)

print()
print("Only the buyer confirms")
before = wallet(seller)
check("the seller cannot confirm", confirm(seller, o1["id"]).status_code == 404)
check("an admin cannot confirm for the buyer", confirm(admin, o1["id"]).status_code == 404)
check("another member cannot", confirm(other, o1["id"]).status_code == 404)
check("nor can a stranger read the order", c.get(f"/commerce/orders/{o1['id']}", headers=other["h"]).status_code == 404)
check("the buyer must acknowledge what confirming means", confirm(buyer, o1["id"], ack=False).status_code == 422)
r = c.post(f"/commerce/orders/{o1['id']}/delivered", headers=seller["h"],
           json={"carrier": "Demo Post", "tracking_number": "DP123", "note": ""})
check("the seller marks it ready", r.status_code == 200 and r.json()["status"] == "delivered", r.text)
time.sleep(1)
check("which releases nothing", wallet(seller) == before and order(seller, o1["id"])["settlement"]["status"] == "none")
r = confirm(buyer, o1["id"])
check("the buyer confirms", r.status_code == 200 and r.json()["acceptance"]["accepted"] is True, r.text)
v = order(seller, o1["id"])
check("the seller sees: buyer confirmed receipt, server time, reference",
      v["acceptance"]["status"] == "buyer_confirmed" and v["acceptance"]["accepted_at"]
      and v["acceptance"]["reference"].startswith("ACC-"), v["acceptance"])
check("with the handover instruction",
      v["handover"] == "Buyer confirmation verified. Release the product or final service deliverables.")
check("settlement shown separately, and confirmed on the test rail", v["settlement"]["status"] == "confirmed", v)
after = wallet(seller)
check("the seller's price is now withdrawable", after[0] - before[0] == Decimal("20.00"), (before, after))
check("the buyer's view shows the same record", order(buyer, o1["id"])["acceptance"]["reference"] == v["acceptance"]["reference"])
notes = c.get("/notifications", headers=seller["h"]).json()
items = notes.get("items", notes if isinstance(notes, list) else [])
link = next((n for n in items if n.get("kind") == "order_accepted"), {})
check("the seller is notified, linked to this order", link.get("link") == f"/market/orders/{o1['id']}", link)
r = confirm(buyer, o1["id"])
check("confirming again changes nothing", r.status_code == 200 and r.json()["acceptance"]["reference"] == v["acceptance"]["reference"])
check("one settlement journal", psql(f"select count(*) from ledger.journals where idempotency_key='order:{o1['id']}'") == "1")

print()
print("Simultaneous confirmations")
o2 = buy(buyer, pid).json()
c.post(f"/payments/{pay(buyer, o2)['payment_id']}/mock-settle", headers=buyer["h"])
seller_before = wallet(seller)
results = []
threads = [threading.Thread(target=lambda: results.append(confirm(buyer, o2["id"]))) for _ in range(5)]
[t.start() for t in threads]
[t.join() for t in threads]
refs = {r.json()["acceptance"]["reference"] for r in results if r.status_code == 200}
check("five taps, one acceptance", len(refs) == 1 and all(r.status_code == 200 for r in results), [r.status_code for r in results])
wait_for(lambda: order(seller, o2["id"])["settlement"]["status"] == "confirmed")
check("one journal, one credit",
      psql(f"select count(*) from ledger.journals where idempotency_key='order:{o2['id']}'") == "1"
      and wallet(seller)[0] - seller_before[0] == Decimal("20.00"))

print()
print("Services")
o3 = buy(buyer, sid, shipping=False).json()
c.post(f"/payments/{pay(buyer, o3)['payment_id']}/mock-settle", headers=buyer["h"])
r = confirm(buyer, o3["id"])
check("a service cannot be confirmed before the seller marks it performed", r.status_code == 409, r.text)
c.post(f"/commerce/orders/{o3['id']}/delivered", headers=seller["h"], json={"note": "Consultation held; notes ready"})
check("after it is performed, the buyer confirms", confirm(buyer, o3["id"]).status_code == 200)

print()
print("Cancellations before the purchase is final")
o4 = buy(buyer, pid).json()
check("the buyer may cancel an unpaid order",
      c.post(f"/commerce/orders/{o4['id']}/cancel", headers=buyer["h"], json={"reason": "unpaid"}).json()["status"] == "cancelled")
o5 = buy(buyer, pid).json()
c.post(f"/payments/{pay(buyer, o5)['payment_id']}/mock-settle", headers=buyer["h"])
check("but not a paid one", c.post(f"/commerce/orders/{o5['id']}/cancel", headers=buyer["h"],
                                   json={"reason": "unpaid"}).status_code in (403, 409))
buyer_before = wallet(buyer)
r = c.post(f"/commerce/orders/{o5['id']}/cancel", headers=seller["h"], json={"reason": "cannot_supply", "note": "Out of stock"})
check("the seller cancels: cannot supply", r.status_code == 200, r.text)
v = wait_for(lambda: (lambda x: x if x["status"] == "refunded" else None)(order(buyer, o5["id"])))
check("the buyer's payment is back on their balance, confirmed",
      v and v["cancellation"]["refund_status"] == "confirmed" and wallet(buyer)[0] - buyer_before[0] == Decimal(o5["customer_price"]),
      (v, buyer_before, wallet(buyer)))
check("a final order cannot be cancelled", c.post(f"/commerce/orders/{o1['id']}/cancel", headers=seller["h"],
                                                   json={"reason": "cannot_supply"}).status_code == 409)

print()
print("Ordinary disputes are gone")
check("opening one is refused (410)", c.post(f"/commerce/orders/{o2['id']}/dispute", headers=buyer["h"],
                                              json={"category": "other", "reason": "x" * 20}).status_code == 410)
check("so is an admin resolution", c.post("/admin/disputes/dsp_x/resolve", headers=admin["h"], json={}).status_code == 410)
check("and the automatic release", httpx.post(f"{COMMERCE}/internal/escrow/sweep").status_code == 410)
r = c.post(f"/commerce/orders/{o1['id']}/support", headers=buyer["h"],
           json={"kind": "not_handed_over", "message": "I confirmed but the seller kept the item."})
check("operational support stays", r.status_code == 201 and r.json()["status"] == "open", r.text)
check("and admins see it", any(x["id"] == r.json()["id"] for x in c.get("/admin/commerce/support", headers=admin["h"]).json()["items"]))
check("a stranger cannot open one on the order", c.post(f"/commerce/orders/{o1['id']}/support", headers=other["h"],
                                                         json={"kind": "fraud", "message": "x" * 20}).status_code == 404)

print()
print("Ebooks: final at the first successful download")
pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\n% demo book\n" + b"0" * 4096 + b"\n%%EOF\n"
asset = c.post("/media/upload", headers=seller["h"], files={"file": ("book.pdf", pdf, "application/pdf")},
               data={"purpose": "ebook"}).json()
r = c.post("/commerce/products", headers=seller["h"], json={
    "title": f"Demo Ebook {tag()}", "description": "<p>A book.</p>", "kind": "digital", "vendor_price": "10.00",
    "images": [], "ebook": {"file_asset_id": asset["id"], "author": "Demo Author", "category": "business",
                            "language": "en"}})
check("an ebook is listed", r.status_code == 201, r.text)
eid = r.json()["id"]
o6 = buy(buyer, eid, shipping=False).json()
c.post(f"/payments/{pay(buyer, o6)['payment_id']}/mock-settle", headers=buyer["h"])
v = order(seller, o6["id"])
check("paid and in the library is not final", v["status"] == "in_escrow" and not v["acceptance"]["accepted"], v)
check("the buyer cannot 'confirm' an ebook", confirm(buyer, o6["id"]).status_code == 409)
r = c.post(f"/commerce/library/{o6['id']}/download", headers=buyer["h"], json={})
check("the first download needs the acknowledgement", r.status_code == 428, r.text)
link = c.post(f"/commerce/library/{o6['id']}/download", headers=buyer["h"],
              json={"acknowledge_final": True, "policy_version": POLICY}).json()["url"]
check("a link alone is not a download", not order(seller, o6["id"])["acceptance"]["accepted"])
forged = link.rsplit("&dls=", 1)[0] + "&dls=forged"
httpx.get(forged, timeout=30)
part = httpx.get(link, headers={"Range": "bytes=0-99"}, timeout=30)
check("a partial download is served", part.status_code == 206, part.status_code)
time.sleep(3)
check("a forged receipt or a partial download does not finalise", not order(seller, o6["id"])["acceptance"]["accepted"])
seller_before = wallet(seller)
full = httpx.get(link, timeout=30)
check("the full file is served", full.status_code == 200 and full.content == pdf, (full.status_code, len(full.content)))
v = wait_for(lambda: (lambda x: x if x["settlement"]["status"] == "confirmed" else None)(order(seller, o6["id"])))
check("the first successful download made it final and settled",
      v and v["acceptance"]["status"] == "first_download" and v["acceptance"]["reference"], v)
httpx.get(link, timeout=30)
c.post(f"/commerce/library/{o6['id']}/download", headers=buyer["h"], json={})
time.sleep(3)
check("later downloads settle nothing again",
      psql(f"select count(*) from ledger.journals where idempotency_key='order:{o6['id']}'") == "1"
      and wallet(seller)[0] - seller_before[0] == Decimal("10.00"))

print()
print("Removed providers")
check("a checkout cannot ask for NowPayments", c.post("/payments/checkout", headers=buyer["h"], json={
    "purpose": "order", "amount": "1", "reference": "x", "rail": "nowpayments"}).status_code == 422)
check("nor Mangopay", c.post("/payments/checkout", headers=buyer["h"], json={
    "purpose": "order", "amount": "1", "reference": "x", "rail": "mangopay"}).status_code == 422)
check("the NowPayments callback is gone", c.post("/payments/ipn/nowpayments", content=b"{}").status_code in (404, 405))
rails = c.get("/payments/rails").json()
check("the rails say so", set(rails.get("removed", [])) == {"nowpayments", "mangopay"} and "nowpayments" not in rails, rails)

# ================================================================== Part B: Kaluta Pay (fake)
print()
print("Part B - through Kaluta Pay (fake), held in its escrow")
try:
    httpx.get(f"{FAKE}/_ctl/state", timeout=3)
except httpx.HTTPError:
    print("  SKIP the fake is not running (python backend/tests/fake_sonco_kaluta.py)")
    print()
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)
compose("-f", "docker-compose.yml", "-f", "backend/tests/compose.market-test.yml",
        "up", "-d", "--no-deps", "--force-recreate", "commerce-service", "payment-service")
healthy(8209), healthy(8211)
time.sleep(3)


def kp_pay(buyer, o):
    p = pay(buyer, o)
    assert p["rail"] == "kalutapay", p
    state = httpx.get(f"{FAKE}/_ctl/state").json()
    inv = next(i for i in state["kp_invoices"].values() if i["order_ref"] == p["payment_id"])
    httpx.post(f"{FAKE}/_ctl/kp_paid", json={"id": inv["id"]})
    c.get(f"/payments/intents/{p['payment_id']}", headers=buyer["h"])
    return p, inv


def mode(m):
    httpx.post(f"{FAKE}/_ctl/kp_dist_mode", json={"mode": m})


try:
    mode("ok")
    o7 = buy(buyer, pid).json()
    p7, inv7 = kp_pay(buyer, o7)
    state = httpx.get(f"{FAKE}/_ctl/state").json()
    lines = state["kp_escrow"].get(inv7["id"], [])
    check("the whole order is held in the marketplace pool at Kaluta Pay",
          len(lines) == 1 and lines[0]["kind"] == "marketplace_orders" and Decimal(lines[0]["amount"]) == Decimal(o7["customer_price"]),
          lines)
    check("the escrow categories were created", state["kp_categories"].get("marketplace_orders") == "pool"
          and state["kp_categories"].get("marketplace_sales") == "member")
    check("the order records the rail", order(seller, o7["id"])["payment_rail"] == "kalutapay")

    print()
    print("Provider down: accepted, settlement pending, nothing withdrawable")
    mode("unreachable")
    before = wallet(seller)
    r = confirm(buyer, o7["id"])
    v = order(seller, o7["id"])
    check("the acceptance stands", r.status_code == 200 and v["acceptance"]["accepted"] is True, r.text)
    check("settlement pending, not 'paid'", v["settlement"]["status"] == "pending" and v["status"] == "accepted", v)
    w = wallet(seller)
    check("the seller's price is owed but not withdrawable", w[0] == before[0] and w[1] - before[1] == Decimal("20.00"), (before, w))
    mode("ok")
    v = wait_for(lambda: (lambda x: x if x["settlement"]["status"] == "confirmed" else None)(order(seller, o7["id"])), 60)
    check("the worker retries and settles once Kaluta Pay answers", v is not None, order(seller, o7["id"]))
    state = httpx.get(f"{FAKE}/_ctl/state").json()
    d = state["kp_distributions"].get(f"settle-{o7['id']}:marketplace_sales")
    check("through a Kaluta Pay distribution to the seller",
          d and d["allocations"][0]["member"]["external_id"] == seller["id"] and Decimal(d["total_due"]) == Decimal("20.00"), d)
    w2 = wallet(seller)
    check("and then it is withdrawable", w2[0] - before[0] == Decimal("20.00") and w2[1] == before[1], (before, w2))

    print()
    print("Provider refuses: a person is needed, then a retry")
    o8 = buy(buyer, pid).json()
    kp_pay(buyer, o8)
    mode("refuse")
    confirm(buyer, o8["id"])
    v = order(admin, o8["id"]) if False else c.get("/admin/commerce/orders", headers=admin["h"],
                                                    params={"attention": True}).json()["items"]
    row = next((x for x in v if x["id"] == o8["id"]), None)
    check("the order waits for a person, with the reason",
          row and row["settlement"]["status"] == "action_required" and row["settlement"]["error"], row)
    check("the seller still sees the acceptance", order(seller, o8["id"])["acceptance"]["accepted"] is True)
    mode("ok")
    r = c.post(f"/admin/commerce/orders/{o8['id']}/retry", headers=admin["h"])
    check("an admin retries once it is fixed", r.status_code == 200 and r.json()["settlement"]["status"] == "confirmed", r.text)

    print()
    print("Refund through Kaluta Pay")
    o9 = buy(buyer, pid).json()
    kp_pay(buyer, o9)
    before = wallet(buyer)
    c.post(f"/commerce/orders/{o9['id']}/cancel", headers=seller["h"], json={"reason": "delivery_failed"})
    v = wait_for(lambda: (lambda x: x if x["status"] == "refunded" else None)(order(buyer, o9["id"])), 30)
    state = httpx.get(f"{FAKE}/_ctl/state").json()
    d = state["kp_distributions"].get(f"refund-{o9['id']}:marketplace_refunds")
    check("returned to the buyer through a distribution, then their balance",
          v and d and wallet(buyer)[0] - before[0] == Decimal(o9["customer_price"]), (v, d))
    funds = c.get("/admin/payments/order-funds", headers=admin["h"]).json()["items"]
    check("every allocation is on record for admins",
          {f"settle-{o7['id']}", f"refund-{o9['id']}"} <= {f["reference"] for f in funds})
finally:
    mode("ok")
    compose("up", "-d", "--no-deps", "--force-recreate", "commerce-service", "payment-service")

print()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
