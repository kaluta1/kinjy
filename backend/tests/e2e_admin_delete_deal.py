"""An administrator deletes a deal with a reason; its seller is told (29/10). Local stack.

    python backend/tests/e2e_admin_delete_deal.py

Briefly stops the local messaging-service to check that a notification that
could not be delivered is retried - once, without deleting again.
"""
import random
import string
import subprocess
import sys
import time
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
POLICY = "2026-10-27"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True
REASON = "Counterfeit goods reported by three buyers <script>alert(1)</script>"


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def register(name, role=None):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{t}@example.com"
    r = c.post("/auth/register", json={
        "email": email, "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
    r.raise_for_status()
    uid = r.json()["user"]["id"]
    if role:
        psql(f"update auth.users set role='{role}' where id='{uid}'")
    token = c.post("/auth/login", json={"email": email, "password": "Sup3rStrong!Pass"}).json()["tokens"]["access_token"]
    return {"h": {"Authorization": f"Bearer {token}"}, "id": uid}


def listing(seller, title):
    r = c.post("/commerce/products", headers=seller["h"], json={
        "title": title, "description": "A demo listing.", "kind": "product", "vendor_price": "20.00",
        "country": "US", "images": [], "stock": 50})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def order(buyer, pid):
    return c.post("/commerce/orders", headers=buyer["h"], json={
        "product_id": pid, "quantity": 1, "acknowledge_policy": True, "policy_version": POLICY,
        "shipping": {"full_name": "Demo Buyer", "line1": "1 Demo St", "city": "Demo", "country": "US"}})


def delete(user, pid, reason=REASON):
    return c.post(f"/admin/commerce/products/{pid}/delete", headers=user["h"], json={"reason": reason})


def listed(pid):
    offset = 0
    while True:
        page = c.get(f"/commerce/products?limit=100&offset={offset}").json()["items"]
        if any(p["id"] == pid for p in page):
            return True
        if len(page) < 100:
            return False
        offset += 100


def notes(uid):
    return psql(f"select count(*) from messaging.notifications where user_id='{uid}' and kind='listing_deleted'")


admin, mod = register("Demo Admin", "admin"), register("Demo Moderator", "moderator")
seller, buyer = register("Demo Seller"), register("Demo Buyer")
d1 = listing(seller, "Demo deal to delete")
d2 = listing(seller, "Demo deal with an order")
keep = listing(seller, "Demo deal that stays")

print("== who may ==")
check("the seller cannot delete through the admin route", delete(seller, d1).status_code == 403)
check("a moderator cannot (admin permission)", delete(mod, d1).status_code == 403)
check("signed out: refused", httpx.post(f"{BASE}/admin/commerce/products/{d1}/delete", json={"reason": REASON}).status_code == 401)

print("== the reason ==")
for bad, why in (("", "empty"), ("          ", "whitespace only"), ("  too short ", "under 10 characters"), ("x" * 1001, "over 1000")):
    r = delete(admin, d1, bad)
    check(f"refused: {why}", r.status_code == 422, r.text[:200])
check("after refusals (and a cancelled dialog: no request) the deal is untouched",
      psql(f"select status from commerce.products where id='{d1}'") == "active" and listed(d1)
      and psql(f"select count(*) from commerce.listing_removals where product_id='{d1}'") == "0")

print("== deleting ==")
r = delete(admin, d1, "   " + REASON + "  ")
body = r.json()
check("an admin deletes it", r.status_code == 200 and body["already"] is False, r.text[:300])
check("soft: the row is kept, status removed", psql(f"select status||'|'||title from commerce.products where id='{d1}'")
      == "removed|Demo deal to delete")
rec = psql(f"select vendor_id||'|'||admin_id||'|'||reason from commerce.listing_removals where product_id='{d1}'")
check("the record: seller, admin, exact reason (trimmed)", rec == f"{seller['id']}|{admin['id']}|{REASON}", rec)
check("an audit row", psql(f"select count(*) from commerce.commerce_audit where target_id='{d1}' and action='delete_listing' "
                           f"and actor_id='{admin['id']}' and reason='{REASON.replace(chr(39), chr(39)*2)}'") == "1")
n = psql(f"select title||' || '||body from messaging.notifications where user_id='{seller['id']}' and kind='listing_deleted'")
check("the seller is told: the title, the reason, the date, the reference", "Demo deal to delete" in n and REASON in n
      and "Date: " in n and d1 in n, n)
check("the reason goes to nobody else", psql(
    f"select count(*) from messaging.notifications where kind='listing_deleted' and user_id<>'{seller['id']}' and body like '%{d1}%'") == "0")
check("in-app reported done; e-mail reported honestly", body["seller_notified_in_app"] is True
      and body["email"] in ("sent", "skipped", "failed", "pending"), body)

print("== gone from the marketplace ==")
check("not listed", not listed(d1))
check("not found by search", all(p["id"] != d1 for p in c.get("/commerce/products?q=Demo deal to delete").json()["items"]))
check("its page is gone", c.get(f"/commerce/products/{d1}").status_code == 404)
check("it cannot be ordered", order(buyer, d1).status_code in (404, 409))
r = c.post("/auth/share-links", headers=buyer["h"], json={"destination": f"/market/products/{d1}", "include_invitation": True})
check("no personal deal link can be made for it", r.status_code == 404, r.text[:200])

print("== repeated ==")
again = delete(admin, d1, "A different reason given the second time")
check("deleting again: the same record, nothing new", again.status_code == 200 and again.json()["already"] is True
      and again.json()["reason"] == REASON)
check("one notification, one audit row", notes(seller["id"]) == "1"
      and psql(f"select count(*) from commerce.commerce_audit where target_id='{d1}' and action='delete_listing'") == "1")

print("== orders and money untouched ==")
o = order(buyer, d2).json()
pay = c.post("/payments/checkout", headers=buyer["h"], json={"purpose": "order", "amount": o["customer_price"], "reference": o["id"]}).json()
c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=buyer["h"])
before = psql(f"select status||'|'||coalesce(custody_ref,'') from commerce.orders where id='{o['id']}'")
journals = psql(f"select count(*) from ledger.journals where reference='{o['id']}'")
subprocess.run(["docker", "stop", "kaluta-messaging"], capture_output=True)
try:
    r = delete(admin, d2, "Listing breaks the marketplace rules on weapons")
    check("deleted while notifications are down: the deletion stands", r.status_code == 200 and r.json()["seller_notified_in_app"] is False
          and r.json().get("open_orders") is True, r.text[:300])
finally:
    subprocess.run(["docker", "start", "kaluta-messaging"], capture_output=True)
check("the paid order is unchanged", psql(f"select status||'|'||coalesce(custody_ref,'') from commerce.orders where id='{o['id']}'") == before, before)
check("its ledger journals are unchanged", psql(f"select count(*) from ledger.journals where reference='{o['id']}'") == journals)
check("the buyer still sees their order", c.get(f"/commerce/orders/{o['id']}", headers=buyer["h"]).status_code == 200)
for _ in range(60):
    try:
        if httpx.get("http://localhost:8200/api/notifications", headers=seller["h"], timeout=3).status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(1)
psql(f"update commerce.listing_removals set removed_at = removed_at - interval '1 minute' where product_id='{d2}'")
# Three retry passes at the same moment (and the service's own loop may join in).
passes = [subprocess.Popen(["docker", "exec", "-w", "/app", "kaluta-commerce", "python", "-c",
                            "import listing_removal; listing_removal.retry_once()"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"}) for _ in range(3)]
[p.wait() for p in passes]
check("the retry delivers it once (concurrent retries, one notification)", notes(seller["id"]) == "2"
      and psql(f"select notified_at is not null from commerce.listing_removals where product_id='{d2}'") == "t")
check("and did not delete anything again", psql(f"select count(*) from commerce.commerce_audit where target_id='{d2}' and action='delete_listing'") == "1")

print("== other actions ==")
check("other deals stay listed", listed(keep))
r = c.post(f"/admin/commerce/products/{keep}/suspend", headers=admin["h"], json={"reason": "Checking the suspend action"})
check("the existing suspend still works", r.status_code == 200 and r.json()["status"] == "suspended", r.text[:200])
check("the admin list leaves deleted deals out", all(d["id"] not in (d1, d2) for d in
      c.get(f"/admin/commerce/products?q={seller['id']}", headers=admin["h"]).json()["items"]))
check("and a non-admin cannot read it", c.get("/admin/commerce/products", headers=seller["h"]).status_code == 403)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
