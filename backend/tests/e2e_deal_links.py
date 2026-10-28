"""Personal links to marketplace deals (28/10), end to end against the local stack.

A deal link is an ordinary Kinjy short link to /market/products/<id> with the
member's invitation on. What is held to: the deal is checked on the server;
the link opens the deal and carries only an opaque token; a new visitor who
signs up through it is linked to the sharer by the existing rules; an existing
member is not, and a purchase by them pays the sharer nothing; a purchase by
the new member pays the sharer the existing marketplace commission once.

    python backend/tests/e2e_deal_links.py
"""
import random
import string
import subprocess
import sys
import time
from datetime import date, timedelta
from decimal import Decimal

import httpx

BASE = "http://localhost:8200/api"
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


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


def register(client, name, **extra):
    t = tag()
    r = client.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US", **extra})
    assert r.status_code == 201, r.text
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"],
            "code": d["user"]["referral_code"], "sponsor": d["user"].get("invited_by")}


def product(seller, price="20.00"):
    r = c.post("/commerce/products", headers=seller["h"], json={
        "title": f"Demo deal {tag()}", "description": "<p>A <b>demo</b> deal.</p>", "kind": "product",
        "vendor_price": price, "country": "US", "images": [], "stock": 50})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def deal_link(member, pid):
    return c.post("/auth/share-links", headers=member["h"],
                  json={"destination": f"/market/products/{pid}", "include_invitation": True})


def buy_and_settle(buyer, seller, pid):
    o = c.post("/commerce/orders", headers=buyer["h"], json={
        "product_id": pid, "quantity": 1, "acknowledge_policy": True, "policy_version": POLICY,
        "shipping": {"full_name": "Demo Buyer", "line1": "1 Demo Street", "city": "Demo City", "country": "US"}}).json()
    pay = c.post("/payments/checkout", headers=buyer["h"],
                 json={"purpose": "order", "amount": o["customer_price"], "reference": o["id"]}).json()
    c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=buyer["h"])
    c.post(f"/commerce/orders/{o['id']}/delivered", headers=seller["h"], json={"carrier": "Demo", "tracking_number": "D1", "note": ""})
    c.post(f"/commerce/orders/{o['id']}/confirm-delivery", headers=buyer["h"],
           json={"acknowledge_final": True, "policy_version": POLICY})
    for _ in range(40):
        if c.get(f"/commerce/orders/{o['id']}", headers=buyer["h"]).json()["settlement"]["status"] == "confirmed":
            break
        time.sleep(1)
    return o


def commissions(member_id, order_id):
    return psql(f"select coalesce(sum(amount),0) from ledger.commissions where member_id='{member_id}' "
                f"and source_ref='{order_id}' and status <> 'reversed'")


sharer, seller, existing = register(c, "Demo Sharer"), register(c, "Demo Seller"), register(c, "Demo Existing")
# The sharer is verified, so the existing rule pays them on their referrals' purchases.
psql(f"update auth.users set kyc_verified=true, kyc_verified_until='{date.today() + timedelta(days=300)}' where id='{sharer['id']}'")
pid = product(seller)

print("== making the link ==")
r = deal_link(sharer, pid)
link = r.json()
check("a signed-in member gets a short personal link to the deal", r.status_code == 200 and "/s/" in link["url"]
      and link["destination"] == f"/market/products/{pid}" and link["include_invitation"] is True, r.text)
check("it names nobody: no code, id or name in it",
      sharer["code"] not in link["url"] and sharer["id"] not in link["url"] and "Sharer" not in link["url"])
again = deal_link(sharer, pid).json()
check("asking again returns the same link", again["token"] == link["token"] and again.get("created") is False)
check("signed out: no link", httpx.post(f"{BASE}/auth/share-links", json={
    "destination": f"/market/products/{pid}", "include_invitation": True}).status_code == 401)
check("an unknown deal is refused", deal_link(sharer, "prd_doesnotexist").status_code == 404)
paused = product(seller)
psql(f"update commerce.products set status='paused' where id='{paused}'")
check("a paused deal is refused", deal_link(sharer, paused).status_code == 404)
r = c.post("/auth/share-links", headers=sharer["h"], json={
    "destination": f"https://evil.example/market/products/{pid}", "include_invitation": True})
check("another site's address is refused (no open redirect)", r.status_code == 400, r.text)
r = c.post("/auth/share-links", headers=sharer["h"], json={
    "destination": f"/market/products/{pid}?ref={existing['code']}&next=https://evil.example", "include_invitation": True})
check("a referral code or a redirect on the address is not kept",
      r.status_code in (200, 400) and (r.status_code == 400 or ("ref=" not in r.json()["destination"])), r.text)

print("== opening it ==")
r = httpx.get(f"{BASE}/auth/s/{link['token']}", follow_redirects=False)
check("a browser is sent to the deal with only the link's token",
      r.status_code == 302 and r.headers["location"] == f"/market/products/{pid}?via={link['token']}", r.headers.get("location"))
r = httpx.get(f"{BASE}/auth/s/{link['token']}", headers={"User-Agent": "WhatsApp/2.23"}, follow_redirects=False)
check("a chat app's preview shows the deal, in plain words, not the sharer",
      r.status_code == 200 and "Demo deal" in r.text and "A demo deal." in r.text and "<b>" not in r.text
      and "Demo Sharer" not in r.text, r.text[:300])
psql(f"update commerce.products set status='paused' where id='{pid}'")
r = httpx.get(f"{BASE}/auth/s/{link['token']}", headers={"User-Agent": "WhatsApp/2.23"}, follow_redirects=False)
check("a deal paused since: the preview says it is no longer available", "no longer available" in r.text)
r = httpx.get(f"{BASE}/auth/s/{link['token']}", follow_redirects=False)
check("and the link still opens (the deal page says it is unavailable)", r.status_code == 302)
psql(f"update commerce.products set status='active' where id='{pid}'")
r = c.get(f"/auth/share-links/{link['token']}")
check("the page asking about the link learns only the sharer's display name",
      r.json()["inviter"] == {"display_name": "Demo Sharer"} and r.json()["destination"] == f"/market/products/{pid}", r.text)

print("== a new visitor signs up through it ==")
b = httpx.Client(base_url=BASE, timeout=60)
r = b.post("/auth/invitation/capture", json={"link": link["token"]})
check("the link is held for this browser", r.status_code == 200 and r.json().get("captured") is True, r.text)
newbie = register(b, "Demo Newbie")
check("linked to the sharer", newbie["sponsor"] == sharer["id"], newbie)
check("recorded with the link as the method",
      psql(f"select method from auth.referral_attributions where user_id='{newbie['id']}'") in ("captured", "link"),
      psql(f"select method from auth.referral_attributions where user_id='{newbie['id']}'"))
check("counted once as a registration from the link",
      psql(f"select count(*) from auth.share_link_conversions where token='{link['token']}' and kind='registration'") == "1")
second = register(b, "Demo Second")
check("a second account in the same browser is not linked (the hold is spent)", second["sponsor"] != sharer["id"], second)

print("== existing members, self-referral ==")
r = c.post("/auth/invitation/capture", headers=existing["h"], json={"link": link["token"]})
check("an existing member following it is not captured", r.json().get("captured") is not True, r.text)
check("their sponsor is unchanged", psql(f"select coalesce(invited_by,'') from auth.users where id='{existing['id']}'")
      == (existing["sponsor"] or ""))
r = c.post("/auth/invitation/capture", headers=sharer["h"], json={"link": link["token"]})
check("the sharer following their own link is not captured", r.json().get("captured") is not True, r.text)

print("== purchases and commissions ==")
o1 = buy_and_settle(newbie, seller, pid)
margin = Decimal("20.00") * Decimal("0.20")
check("the new member's purchase pays the sharer the existing commission: 20% of Kinjy's margin",
      Decimal(commissions(sharer["id"], o1["id"])) == (margin * Decimal("0.20")).quantize(Decimal("0.01")),
      commissions(sharer["id"], o1["id"]))
check("once (one settlement journal)", psql(f"select count(*) from ledger.journals where idempotency_key='order:{o1['id']}'") == "1")
o2 = buy_and_settle(existing, seller, pid)
check("an existing member's purchase through the link pays the sharer nothing",
      Decimal(commissions(sharer["id"], o2["id"])) == 0)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
