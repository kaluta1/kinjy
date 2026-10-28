"""Paying a marketplace order from the Kinjy balance, end to end (28/10).

Local stack, test rail, no provider. The option is switched off in .env; the
test checks that first, then switches it on for payment-service with
tests/compose.wallet-test.yml and puts the services back at the end.

    python backend/tests/e2e_wallet_pay.py

Balances are funded the way a refund reaches a balance (a balanced
escrow-refund journal), so the ledger and the wallets reconcile throughout.
"""
import random
import string
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
LEDGER = "http://localhost:8210"
POLICY = "2026-10-27"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:400]}"))
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
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def compose(*files):
    args = ["docker", "compose", "-f", "docker-compose.yml", *sum((["-f", f] for f in files), []),
            "up", "-d", "--no-deps", "commerce-service", "payment-service"]
    subprocess.run(args, capture_output=True, text=True, timeout=300)
    for port in (8209, 8211):
        for _ in range(90):
            try:
                if httpx.get(f"http://localhost:{port}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)


def verify(user, days=300):
    """Identity verified until today + days (negative: already expired)."""
    psql(f"update auth.users set kyc_verified=true, kyc_verified_until='{date.today() + timedelta(days=days)}' "
         f"where id='{user['id']}'")


def available(user):
    return Decimal(httpx.get(f"{LEDGER}/wallet", headers=user["h"], timeout=20).json()["available"])


def fund(user, amount):
    httpx.post(f"{LEDGER}/internal/post/escrow", json={
        "order_id": f"demo-fund-{tag()}", "amount": str(amount), "action": "refund_to_balance",
        "beneficiary_id": user["id"], "idempotency_key": f"demo-fund-{tag()}"}, timeout=20).raise_for_status()


def order(user, oid):
    return c.get(f"/commerce/orders/{oid}", headers=user["h"]).json()


SHIP = {"full_name": "Demo Buyer", "line1": "1 Demo Street", "city": "Demo City", "country": "US"}


def product(seller, price="20.00"):
    r = c.post("/commerce/products", headers=seller["h"], json={
        "title": f"Demo product {tag()}", "description": "A demo listing.", "kind": "product",
        "vendor_price": price, "country": "US", "images": [], "stock": 500})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def buy(buyer, pid):
    r = c.post("/commerce/orders", headers=buyer["h"], json={
        "product_id": pid, "quantity": 1, "shipping": SHIP, "acknowledge_policy": True, "policy_version": POLICY})
    assert r.status_code == 201, r.text
    return r.json()


def pay(user, oid):
    return c.post("/payments/wallet/pay", headers=user["h"], json={"order_id": oid})


def reconciles(*users):
    """Each wallet equals its ledger lines minus what was paid out, as /admin/reconcile computes it."""
    for u in users:
        row = psql("select coalesce(sum(credit-debit),0) from ledger.entries where beneficiary_id='%s' and account in "
                   "('liabilities:member_payable','liabilities:vendor_payable')" % u["id"])
        w = psql(f"select available||'|'||lifetime_paid from ledger.wallets where owner_id='{u['id']}'").split("|")
        if Decimal(row) - Decimal(w[1]) != Decimal(w[0]):
            return False
    return True


def balanced(journal_id):
    return psql(f"select sum(debit)=sum(credit) from ledger.entries where journal_id='{journal_id}'") == "t"


seller, buyer, other = register("Demo Seller"), register("Demo Buyer"), register("Demo Other")
pid = product(seller)
o = buy(buyer, pid)
price = Decimal(o["customer_price"])

print("== switched off ==")
r = c.get(f"/payments/wallet/quote?order_id={o['id']}", headers=buyer["h"])
off = r.status_code == 200 and r.json() == {"enabled": False}
if off:
    check("the checkout does not offer it", True)
    check("and paying is refused", pay(buyer, o["id"]).status_code == 404)
else:
    print("  (WALLET_PAY_ENABLED is already on locally: skipped)")
compose("backend/tests/compose.wallet-test.yml")
try:
    print("== identity verification required ==")
    fund(buyer, price + Decimal("5.00"))
    q = c.get(f"/payments/wallet/quote?order_id={o['id']}", headers=buyer["h"]).json()
    check("unverified: the quote says so", q.get("verified") is False, q)
    have = available(buyer)
    r = pay(buyer, o["id"])
    check("unverified: refused, nothing taken", r.status_code == 403 and r.json()["detail"] == "kyc_required"
          and available(buyer) == have, r.text)
    verify(buyer, -1)
    r = pay(buyer, o["id"])
    check("verification expired: refused too", r.status_code == 403 and available(buyer) == have, r.text)
    check("and the order still waits for payment", order(buyer, o["id"])["status"] == "pending")
    verify(buyer)
    verify(other)

    print("== paying ==")
    before = available(buyer)
    q = c.get(f"/payments/wallet/quote?order_id={o['id']}", headers=buyer["h"]).json()
    check("the quote is the server's: verified, order total, balance, enough",
          q["enabled"] and q["verified"] and Decimal(q["amount"]) == price and Decimal(q["available"]) == before and q["enough"], q)
    r = pay(buyer, o["id"])
    body = r.json()
    check("paid", r.status_code == 200 and body["status"] == "paid" and body["payment_id"].startswith("pmt_"), r.text)
    check("exactly the order total left the balance", before - available(buyer) == price)
    v = order(buyer, o["id"])
    check("the order is paid and held, through the existing escrow", v["status"] == "in_escrow" and v["payment_rail"] == "wallet", v)
    j = psql(f"select id from ledger.journals where idempotency_key='wallet-pay:order:{o['id']}'")
    lines = psql(f"select account||'|'||coalesce(beneficiary_id,'')||'|'||debit||'|'||credit from ledger.entries "
                 f"where journal_id='{j}' order by debit desc").splitlines()
    check("one balanced journal: the buyer's payable debited, cash credited",
          balanced(j) and lines == [f"liabilities:member_payable|{buyer['id']}|{price}|0.00", f"assets:cash||0.00|{price}"], lines)
    check("the escrow hold is the usual one", psql(
        f"select count(*) from ledger.journals where kind='escrow_hold' and reference='{o['id']}'") == "1")
    check("the payment record carries the reference", psql(
        f"select rail||'|'||status from payment.payment_intents where id='{body['payment_id']}'") == "wallet|finished")

    print("== repeated submissions ==")
    again = pay(buyer, o["id"])
    check("the same request again: same payment, nothing taken twice",
          again.status_code == 200 and again.json()["payment_id"] == body["payment_id"] and before - available(buyer) == price)
    o_same = buy(buyer, pid)
    fund(buyer, Decimal(o_same["customer_price"]))
    start = available(buyer)
    res = []
    ts = [threading.Thread(target=lambda: res.append(pay(buyer, o_same["id"]))) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = {x.json().get("payment_id") for x in res if x.status_code == 200}
    check("five taps on one order: one payment, one debit", len(ids) == 1 and start - available(buyer) == price
          and psql(f"select count(*) from payment.payment_intents where reference='{o_same['id']}' and rail='wallet'") == "1",
          [x.status_code for x in res])

    print("== insufficient funds ==")
    o2 = buy(buyer, pid)
    have = available(buyer)
    q = c.get(f"/payments/wallet/quote?order_id={o2['id']}", headers=buyer["h"]).json()
    check("the quote says it is not enough", q.get("enough") is False, q)
    r = pay(buyer, o2["id"])
    check("refused as insufficient, nothing taken", r.status_code == 409 and r.json()["detail"] == "insufficient_funds"
          and available(buyer) == have, r.text)
    check("the order is still waiting for payment", order(buyer, o2["id"])["status"] == "pending")
    r = c.post("/payments/checkout", headers=buyer["h"], json={"purpose": "order", "amount": o2["customer_price"], "reference": o2["id"]})
    check("the existing payment is still offered", r.status_code == 201, r.text)
    fund(buyer, price)
    r = pay(buyer, o2["id"])
    check("once the balance is enough, the same order pays", r.status_code == 200 and r.json()["status"] == "paid", r.text)

    print("== concurrent purchases ==")
    rich = register("Demo Rich")
    verify(rich)
    fund(rich, price + Decimal("1.00"))  # enough for one, not two
    a1, a2 = buy(rich, pid), buy(rich, pid)
    res = []
    ts = [threading.Thread(target=lambda oid=oid: res.append(pay(rich, oid))) for oid in (a1["id"], a2["id"])]
    [t.start() for t in ts]
    [t.join() for t in ts]
    codes = sorted(x.status_code for x in res)
    check("two orders at once, money for one: one paid, one refused", codes == [200, 409], codes)
    check("never below zero", available(rich) == Decimal("1.00"), available(rich))

    print("== a purchase against a withdrawal ==")
    racer = register("Demo Racer")
    verify(racer)
    fund(racer, price)
    b1 = buy(racer, pid)
    res = {}
    t1 = threading.Thread(target=lambda: res.__setitem__("pay", pay(racer, b1["id"])))
    t2 = threading.Thread(target=lambda: res.__setitem__("withdraw", httpx.post(
        f"{LEDGER}/internal/payouts/reserve", json={"member_id": racer["id"], "amount": str(price), "key": f"wd_demo_{tag()}"},
        timeout=30)))
    t1.start(); t2.start(); t1.join(); t2.join()
    won = [k for k, v in res.items() if v.status_code == 200]
    check("the same money, spent or withdrawn - exactly one", len(won) == 1, {k: v.status_code for k, v in res.items()})
    check("the balance is zero, not negative", available(racer) == Decimal("0.00"))

    print("== who may ==")
    o3 = buy(buyer, pid)
    fund(other, price)
    check("another member cannot pay my order", pay(other, o3["id"]).status_code == 404)
    check("nor see its quote", c.get(f"/payments/wallet/quote?order_id={o3['id']}", headers=other["h"]).status_code == 404)
    check("signed out: refused", httpx.post(f"{BASE}/payments/wallet/pay", json={"order_id": o3["id"]}).status_code == 401)
    mine = c.get("/wallet/history", headers=other["h"]).json()["items"]
    check("a history shows only its owner's lines", all(i["reference"] != o["id"] for i in mine) and len(mine) == 1, mine)
    check("an amount or member sent by the client is ignored", c.post("/payments/wallet/pay", headers=other["h"], json={
        "order_id": o3["id"], "amount": "0.01", "member_id": buyer["id"]}).status_code == 404)

    print("== paid another way: the debit comes back ==")
    o4 = buy(buyer, pid)
    fund(buyer, price)
    have = available(buyer)
    card = c.post("/payments/checkout", headers=buyer["h"], json={"purpose": "order", "amount": o4["customer_price"], "reference": o4["id"]}).json()
    # A balance payment that had started before the card payment landed: debited, not yet funded.
    pmt = f"pmt_demo{tag()}"
    psql(f"insert into payment.payment_intents (id,user_id,rail,purpose,reference,amount,currency,external_id,status,created_at) "
         f"values ('{pmt}','{buyer['id']}','wallet','order','{o4['id']}',{price},'USD','order:{o4['id']}','processing',now()-interval '5 minutes')")
    httpx.post(f"{LEDGER}/internal/wallet/pay", json={"member_id": buyer["id"], "amount": str(price), "key": f"order:{o4['id']}",
                                                      "reference": o4["id"], "payment_id": pmt}, timeout=20).raise_for_status()
    c.post(f"/payments/{card['payment_id']}/mock-settle", headers=buyer["h"])
    check("(set-up: the card opened the escrow, the balance was debited)",
          order(buyer, o4["id"])["status"] == "in_escrow" and have - available(buyer) == price)
    subprocess.run(["docker", "exec", "-w", "/app", "kaluta-payment", "python", "-c",
                    "import walletpay; walletpay.reconcile_once()"], capture_output=True, text=True,
                   env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    check("the reconciler gives it back: status returned", psql(f"select status from payment.payment_intents where id='{pmt}'") == "returned")
    check("the whole amount is back on the balance", available(buyer) == have)
    rev = psql(f"select id from ledger.journals where idempotency_key='wallet-pay-reversal:order:{o4['id']}'")
    check("by a balanced compensating journal; the payment stays on record",
          rev and balanced(rev) and psql(f"select count(*) from ledger.journals where idempotency_key='wallet-pay:order:{o4['id']}'") == "1")
    r = httpx.post(f"{LEDGER}/internal/wallet/pay/reverse", json={"member_id": buyer["id"], "amount": str(price),
                                                                   "key": f"order:{o4['id']}", "reference": o4["id"], "payment_id": pmt})
    check("reversing twice gives back once", r.json().get("reversed") is False and available(buyer) == have, r.text)

    print("== recovery: a payment left processing ==")
    o5 = buy(buyer, pid)
    fund(buyer, price)
    have = available(buyer)
    pmt5 = f"pmt_demo{tag()}"
    psql(f"insert into payment.payment_intents (id,user_id,rail,purpose,reference,amount,currency,external_id,status,created_at) "
         f"values ('{pmt5}','{buyer['id']}','wallet','order','{o5['id']}',{price},'USD','order:{o5['id']}','processing',now()-interval '5 minutes')")
    subprocess.run(["docker", "exec", "-w", "/app", "kaluta-payment", "python", "-c",
                    "import walletpay; walletpay.reconcile_once()"], capture_output=True, text=True,
                   env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    check("the reconciler finishes it: debited once, order held",
          psql(f"select status from payment.payment_intents where id='{pmt5}'") == "finished"
          and have - available(buyer) == price and order(buyer, o5["id"])["status"] == "in_escrow")

    print("== refund to the original balance ==")
    have = available(buyer)
    r = c.post(f"/commerce/orders/{o['id']}/cancel", headers=seller["h"], json={"reason": "cannot_supply", "note": "Out of stock"})
    check("the seller cancels a balance-paid order", r.status_code == 200, r.text)
    for _ in range(30):
        if order(buyer, o["id"])["status"] == "refunded":
            break
        time.sleep(1)
    v = order(buyer, o["id"])
    check("refunded to the buyer's balance, in full", v["status"] == "refunded" and available(buyer) - have == price, (v["status"], available(buyer) - have))
    check("allocated against Kinjy's balance", psql(
        f"select backing from payment.fund_allocations where reference='refund-{o['id']}'") == "kinjy_balance")
    c.post(f"/commerce/orders/{o['id']}/cancel", headers=seller["h"], json={"reason": "cannot_supply"})
    time.sleep(2)
    check("a second cancellation refunds nothing more", available(buyer) - have == price and psql(
        f"select count(*) from ledger.journals where idempotency_key='escrow:refund-balance:{o['id']}'") == "1")

    print("== settlement follows the existing rules ==")
    s_before = available(seller)
    c.post(f"/commerce/orders/{o_same['id']}/delivered", headers=seller["h"], json={"carrier": "Demo Post", "tracking_number": "DP1", "note": ""})
    check("nothing reaches the seller before the buyer confirms", available(seller) == s_before)
    c.post(f"/commerce/orders/{o_same['id']}/confirm-delivery", headers=buyer["h"], json={"acknowledge_final": True, "policy_version": POLICY})
    for _ in range(30):
        if order(seller, o_same["id"])["settlement"]["status"] == "confirmed":
            break
        time.sleep(1)
    check("after confirmation the seller is paid their price, allocated against Kinjy's balance",
          available(seller) - s_before == Decimal("20.00") and psql(
              f"select backing from payment.fund_allocations where reference='settle-{o_same['id']}'") == "kinjy_balance")

    print("== reconciliation ==")
    check("every wallet in this test equals its ledger lines", reconciles(buyer, seller, other, rich, racer))
    check("the whole ledger still balances", psql("select sum(debit)=sum(credit) from ledger.entries") == "t")
    hist = c.get("/wallet/history?limit=100", headers=buyer["h"]).json()["items"]
    kinds = {i["kind"] for i in hist}
    check("the buyer's history shows purchases, the returned one and the refund",
          {"wallet_payment", "wallet_payment_reversal", "escrow_refund_balance"} <= kinds, kinds)
    check("with dates, amounts, currency, status and the order reference",
          all(i["date"] and i["amount"] and i["currency"] == "USD" and i["status"] for i in hist)
          and any(i["kind"] == "wallet_payment" and i["reference"] == o["id"] and Decimal(i["amount"]) == -price for i in hist))
    total = sum(Decimal(i["amount"]) for i in hist if i["kind"] != "withdrawal")
    check("and adds up to the balance", total == available(buyer), (total, available(buyer)))
finally:
    compose()

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
