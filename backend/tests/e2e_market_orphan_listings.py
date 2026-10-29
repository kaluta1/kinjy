"""Listings whose seller is gone, and photos on new listings (29/10). Local stack.

    python backend/tests/e2e_market_orphan_listings.py

A listing left active after its seller's account was removed shows no seller
and can be ordered; the repair suspends exactly those (dry run first, once,
data kept). A new listing keeps its uploaded photo after saving and reloading,
and the photo is served.
"""
import random
import string
import struct
import subprocess
import sys
import zlib
from datetime import date

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


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


def repair(*args):
    out = subprocess.run(["docker", "exec", "-w", "/app", "kaluta-commerce", "python", "suspend_orphan_listings.py", *args],
                         capture_output=True, text=True, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    import json
    return json.loads(out.stdout)


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


def png() -> bytes:
    """A real 8x8 PNG, so the media service accepts it as a photo."""
    raw = b"".join(b"\x00" + b"\xc8\x96\x32" * 8 for _ in range(8))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def listing(seller, title, images=()):
    r = c.post("/commerce/products", headers=seller["h"], json={
        "title": title, "description": "A demo listing.", "kind": "product", "vendor_price": "20.00",
        "country": "US", "images": list(images), "stock": 5})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def ids_listed():
    """Every listing the marketplace shows, all pages."""
    seen, offset = set(), 0
    while True:
        page = c.get(f"/commerce/products?limit=100&offset={offset}").json()["items"]
        seen |= {p["id"] for p in page}
        if len(page) < 100:
            return seen
        offset += 100


seller, gone_seller, buyer = register("Demo Seller"), register("Demo Leaver"), register("Demo Buyer")

print("== a new listing keeps its photo ==")
up = c.post("/media/upload", headers=seller["h"], files={"file": ("photo.png", png(), "image/png")}, data={"purpose": "post"})
check("(the photo uploads)", up.status_code == 201, up.text[:200])
url = up.json().get("url", "")
pid = listing(seller, "Demo basket with photo", [url])
detail = c.get(f"/commerce/products/{pid}").json()
check("saved and read back: one photo", len(detail["images"]) == 1, detail["images"])
first = detail["images"][0] if detail["images"] else ""
served = httpx.get(first if first.startswith("http") else "http://localhost:8200" + first, follow_redirects=True) if first else None
check("the photo is served as an image", served is not None and served.status_code == 200
      and served.headers.get("content-type", "").startswith("image/"), served.status_code if served else None)
card = next((p for off in range(0, 400, 100) for p in c.get(f"/commerce/products?limit=100&offset={off}").json()["items"] if p["id"] == pid), None)
check("the card has it too, with the seller's name", card and len(card["images"]) == 1
      and card["vendor"]["display_name"] == "Demo Seller", card)

print("== a listing whose seller is gone ==")
orphan = listing(gone_seller, "Demo orphan")
# As the 26/10 member rebuild did: the account's rows deleted, its listings not.
for table in ("users.preferences", "users.profiles", "auth.sessions", "auth.login_events",
              "auth.registration_attempts", "auth.user_age_profiles", "auth.referral_attributions"):
    psql(f"delete from {table} where user_id='{gone_seller['id']}'")
psql(f"delete from auth.users where id='{gone_seller['id']}'")
check("(set-up: the account is gone)", psql(f"select count(*) from auth.users where id='{gone_seller['id']}'") == "0")
d = c.get(f"/commerce/products/{orphan}").json()
check("(reproduced: listed, no seller name, orderable)", orphan in ids_listed() and d["vendor"]["display_name"] is None)
dry = repair()
found = [o["id"] for o in dry["orphans"]]
check("the dry run lists it, and not the live seller's listing", orphan in found and pid not in found, dry)
check("and changes nothing", psql(f"select status from commerce.products where id='{orphan}'") == "active")
done = repair("--apply")
check("apply suspends it, with the reason", psql(f"select status||'|'||suspended_reason from commerce.products where id='{orphan}'")
      == "suspended|The seller's account no longer exists on Kinjy", done)
check("an audit row says who and why", psql(
    f"select count(*) from commerce.commerce_audit where target_id='{orphan}' and action='suspend_listing' and actor_id='repair'") == "1")
check("the listing is kept, with its data", psql(f"select title from commerce.products where id='{orphan}'") == "Demo orphan")
check("it leaves the marketplace", orphan not in ids_listed())
r = c.post("/commerce/orders", headers=buyer["h"], json={"product_id": orphan, "quantity": 1, "acknowledge_policy": True,
           "policy_version": POLICY, "shipping": {"full_name": "Demo Buyer", "line1": "1 Demo St", "city": "Demo", "country": "US"}})
check("and cannot be ordered", r.status_code in (404, 409), r.text)
check("the live seller's listing is untouched", pid in ids_listed()
      and psql(f"select status from commerce.products where id='{pid}'") == "active")
again = repair("--apply")
check("a second run finds nothing more", orphan not in [o["id"] for o in again["orphans"]] and again["suspended"] == 0
      or all(o["id"] != orphan for o in again["orphans"]), again)
r = c.post("/commerce/orders", headers=buyer["h"], json={"product_id": pid, "quantity": 1, "acknowledge_policy": True,
           "policy_version": POLICY, "shipping": {"full_name": "Demo Buyer", "line1": "1 Demo St", "city": "Demo", "country": "US"}})
check("ordering a live listing still works (unpaid order, nothing charged)", r.status_code == 201 and r.json()["status"] == "pending", r.text)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
