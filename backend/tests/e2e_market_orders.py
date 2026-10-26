"""Shipping and verified reviews, against the live API.

An order is funded through the same hook payment-service uses once the
custodian has the money, and settled by the buyer confirming receipt.
"""
import random
import string
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=30)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def register():
    t = tag()
    t_ = date.today()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": f"Demo {t}",
        "password": "Sup3rStrong!Pass", "date_of_birth": date(t_.year - 30, t_.month, min(t_.day, 28)).isoformat(),
        "country": "US",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


ADDRESS = {"full_name": "Ada Buyer", "line1": "1 Main St", "city": "Austin", "country": "us"}

seller_tok, seller = register()
buyer_tok, buyer = register()
stranger_tok, _ = register()


def product(kind, price="20.00"):
    r = c.post("/commerce/products", headers=auth(seller_tok),
               json={"title": f"{kind} {tag()}", "vendor_price": price, "kind": kind})
    r.raise_for_status()
    return r.json()["id"]


def place(pid, shipping=None):
    body = {"product_id": pid, "acknowledge_policy": True, "policy_version": "2026-10-27"}
    if shipping:
        body["shipping"] = shipping
    return c.post("/commerce/orders", headers=auth(buyer_tok), json=body)


def funded(pid, shipping=None):
    r = place(pid, shipping)
    r.raise_for_status()
    order = r.json()
    oid = order["id"]
    # Funded the way production does it: a payment settles and payment-service
    # tells commerce-service. There is no public "mark as paid" route any more:
    # it used to exist, reachable through the gateway, and let a buyer put their
    # own order in escrow without paying.
    pay = c.post("/payments/checkout", headers=auth(buyer_tok), json={
        "purpose": "order", "amount": order["customer_price"], "reference": oid, "rail": "mock"})
    pay.raise_for_status()
    c.post(f"/payments/{pay.json()['payment_id']}/mock-settle", headers=auth(buyer_tok)).raise_for_status()
    return oid


def settled(pid, shipping=None):
    oid = funded(pid, shipping)
    r = c.post(f"/commerce/orders/{oid}/confirm-delivery", headers=auth(buyer_tok),
               json={"acknowledge_final": True, "policy_version": "2026-10-27"})
    r.raise_for_status()
    return oid


physical = product("product")
digital = product("digital", "5.00")

print("\n== shipping at checkout ==")
r = place(physical)
check("a physical product without an address is refused", r.status_code in (400, 422), f"{r.status_code} {r.text[:120]}")
r = place(physical, {**ADDRESS, "country": "USA"})
check("a 3-letter country is refused", r.status_code == 422, f"{r.status_code}")
r = place(digital)
check("a digital product needs no address", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
pending_id = r.json().get("id")
pending_price = r.json().get("customer_price")
r = c.get(f"/commerce/orders/{pending_id}", headers=auth(buyer_tok))
check("a new order waits for payment", r.json().get("status") == "pending", r.text[:120])

print("\n== my orders carry the product bought, the other party and the status filter ==")
r = c.get("/commerce/orders/me", headers=auth(buyer_tok), params={"role": "buyer"})
mine = next((i for i in r.json().get("items", []) if i["id"] == pending_id), None)
check("the order is in the buyer's list", mine is not None, r.text[:160])
mine = mine or {}
check("it names the product bought", (mine.get("product") or {}).get("title", "").startswith("digital"),
      str(mine.get("product")))
check("with its kind and a list of images", (mine.get("product") or {}).get("kind") == "digital"
      and isinstance((mine.get("product") or {}).get("images"), list), str(mine.get("product")))
check("the other party is the seller", (mine.get("counterpart") or {}).get("user_id") == seller["id"],
      str(mine.get("counterpart")))
check("not reviewed yet", mine.get("reviewed") is False, str(mine.get("reviewed")))
r = c.get("/commerce/orders/me", headers=auth(buyer_tok), params={"status": "pending"})
items = r.json().get("items", [])
check("the status filter keeps only that stage", items and all(i["status"] == "pending" for i in items)
      and any(i["id"] == pending_id for i in items), r.text[:160])
r = c.get("/commerce/orders/me", headers=auth(seller_tok), params={"role": "seller"})
check("the seller sees it as a sale, the other party being the buyer",
      any(i["id"] == pending_id and i["role"] == "seller" and (i.get("counterpart") or {}).get("user_id") == buyer["id"]
          for i in r.json().get("items", [])), r.text[:160])
r = c.get("/commerce/orders/me", headers=auth(seller_tok), params={"role": "buyer"})
check("the seller's 'bought' list does not hold it", all(i["id"] != pending_id for i in r.json().get("items", [])))
r = c.get("/commerce/orders/me", headers=auth(buyer_tok), params={"status": "nonsense"})
check("an unknown status is refused", r.status_code == 422, f"{r.status_code}")
r = c.get("/commerce/orders/me", headers=auth(buyer_tok), params={"role": "nonsense"})
check("an unknown role is refused", r.status_code == 422, f"{r.status_code}")
r = c.get(f"/commerce/orders/{pending_id}", headers=auth(buyer_tok))
check("the order detail carries the product too", (r.json().get("product") or {}).get("id") == mine.get("product", {}).get("id"),
      r.text[:160])

print("\n== paying an order, in test mode ==")
r = c.post("/payments/checkout", headers=auth(buyer_tok),
           json={"purpose": "order", "amount": pending_price, "reference": pending_id, "rail": "mock"})
check("the buyer opens a checkout", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
check("in test mode it says so", r.json().get("mock") is True, r.text[:160])
c.post(f"/payments/{r.json().get('payment_id')}/mock-settle", headers=auth(buyer_tok)).raise_for_status()
r = c.get(f"/commerce/orders/{pending_id}", headers=auth(buyer_tok))
check("the settled payment puts the order in escrow", r.json().get("status") == "in_escrow", r.text[:120])

print("\n== who sees the address ==")
oid = funded(physical, ADDRESS)
r = c.get(f"/commerce/orders/{oid}", headers=auth(buyer_tok))
check("the buyer sees it, country upper-cased",
      r.status_code == 200 and (r.json().get("shipping") or {}).get("country") == "US", r.text[:200])
r = c.get(f"/commerce/orders/{oid}", headers=auth(seller_tok))
check("the vendor sees it", r.status_code == 200 and (r.json().get("shipping") or {}).get("city") == "Austin", r.text[:200])
r = c.get(f"/commerce/orders/{oid}", headers=auth(stranger_tok))
check("a stranger gets 404", r.status_code == 404, r.status_code)
mine = c.get("/commerce/orders/me", headers=auth(buyer_tok)).json()["items"]
check("orders/me carries it for the buyer",
      any(o["id"] == oid and o.get("shipping") for o in mine))
mine = c.get("/commerce/orders/me", headers=auth(seller_tok)).json()["items"]
check("orders/me carries it for the vendor",
      any(o["id"] == oid and o.get("shipping") for o in mine))
check("a stranger's orders/me does not list it",
      all(o["id"] != oid for o in c.get("/commerce/orders/me", headers=auth(stranger_tok)).json()["items"]))

print("\n== tracking on delivery ==")
r = c.post(f"/commerce/orders/{oid}/delivered", headers=auth(seller_tok), json={})
check("a physical delivery with nothing to follow is refused", r.status_code == 422, f"{r.status_code} {r.text[:120]}")
r = c.post(f"/commerce/orders/{oid}/delivered", headers=auth(seller_tok),
           json={"carrier": "DHL", "tracking_number": "JD0146", "tracking_url": "javascript:alert(1)"})
check("a non-http tracking url is refused", r.status_code == 422, f"{r.status_code}")
r = c.post(f"/commerce/orders/{oid}/delivered", headers=auth(seller_tok),
           json={"carrier": "DHL", "tracking_number": "JD0146", "tracking_url": "https://track.example/JD0146"})
body = r.json() if r.status_code == 200 else {}
check("delivery with tracking is accepted", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
check("tracking fields come back",
      (body.get("carrier"), body.get("tracking_number"), body.get("tracking_url"))
      == ("DHL", "JD0146", "https://track.example/JD0146"), body)
r = c.get(f"/commerce/orders/{oid}", headers=auth(buyer_tok))
check("and the buyer sees them", r.json().get("tracking_number") == "JD0146", r.text[:200])

print("\n== reviews ==")
r = c.post(f"/commerce/orders/{oid}/review", headers=auth(buyer_tok), json={"rating": 5})
check("no review before settlement (409)", r.status_code == 409, f"{r.status_code} {r.text[:120]}")
c.post(f"/commerce/orders/{oid}/confirm-delivery", headers=auth(buyer_tok),
       json={"acknowledge_final": True, "policy_version": "2026-10-27"}).raise_for_status()
r = c.post(f"/commerce/orders/{oid}/review", headers=auth(seller_tok), json={"rating": 5})
check("the seller cannot review (403)", r.status_code == 403, r.status_code)
r = c.post(f"/commerce/orders/{oid}/review", headers=auth(stranger_tok), json={"rating": 5})
check("a stranger cannot review (403)", r.status_code == 403, r.status_code)
for bad in (0, 6):
    r = c.post(f"/commerce/orders/{oid}/review", headers=auth(buyer_tok), json={"rating": bad})
    check(f"rating {bad} is refused", r.status_code == 422, r.status_code)
r = c.post("/commerce/orders/ord_nope/review", headers=auth(buyer_tok), json={"rating": 5})
check("an unknown order is 404", r.status_code == 404, r.status_code)
r = c.post(f"/commerce/orders/{oid}/review", headers=auth(buyer_tok), json={"rating": 5, "comment": "Great"})
check("the buyer reviews a settled order", r.status_code == 201, f"{r.status_code} {r.text[:120]}")
r = c.post(f"/commerce/orders/{oid}/review", headers=auth(buyer_tok), json={"rating": 1})
check("a second review is 409", r.status_code == 409, r.status_code)

oid2 = settled(physical, ADDRESS)
c.post(f"/commerce/orders/{oid2}/review", headers=auth(buyer_tok), json={"rating": 2}).raise_for_status()

r = c.get(f"/commerce/products/{physical}/reviews")
s = r.json() if r.status_code == 200 else {}
check("the product reviews are public", r.status_code == 200, r.status_code)
check("count is 2 and average 3.5", (s.get("count"), s.get("average")) == (2, 3.5), s)
check("distribution has a 5 and a 2",
      s.get("distribution") == {"1": 0, "2": 1, "3": 0, "4": 0, "5": 1}, s.get("distribution"))
check("newest first", [i["rating"] for i in s.get("items", [])] == [2, 5], s.get("items"))
check("each item has a reviewer key", all("reviewer" in i for i in s.get("items", [])))
r = c.get(f"/commerce/products/{physical}/reviews", params={"limit": 1, "offset": 1})
check("pagination", [i["rating"] for i in r.json().get("items", [])] == [5], r.text[:200])
r = c.get(f"/commerce/vendors/{seller['id']}/rating")
check("the vendor rating matches", r.status_code == 200 and (r.json().get("count"), r.json().get("average")) == (2, 3.5),
      r.text[:200])

# Disputes were retired on 27/10 (Kinjy no longer arbitrates ordinary
# marketplace disputes); e2e_market_acceptance.py checks they stay closed.

print("\n" + ("ALL CHECKS PASSED" if ok else "THERE ARE FAILURES ABOVE"))
sys.exit(0 if ok else 1)
