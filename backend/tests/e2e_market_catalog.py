"""Marketplace catalog, seller workspace and storefront, against the live API.

The rule under test is who sees what: a buyer reads the customer price and
nothing else, the seller reads the whole split, and nobody edits a listing that
is not theirs.
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
    t0 = date.today()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": f"Demo {t}",
        "password": "Sup3rStrong!Pass", "date_of_birth": date(t0.year - 30, t0.month, min(t0.day, 28)).isoformat(),
        "country": "US",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def fund(buyer_tok, order_id, amount):
    p = c.post("/payments/checkout", headers=auth(buyer_tok),
               json={"purpose": "order", "reference": order_id, "amount": amount})
    p.raise_for_status()
    s = c.post(f"/payments/{p.json()['payment_id']}/mock-settle", headers=auth(buyer_tok))
    return s.status_code


seller_tok, seller = register()
buyer_tok, buyer = register()
other_tok, _ = register()
city = f"Town{tag()}"

print("\n== creating ==")
r = c.post("/commerce/products", headers=auth(seller_tok), json={
    "title": "Hand woven basket", "description": "Sisal", "vendor_price": "100.00", "stock": 5,
    "country": "ke", "city": city, "images": ["https://cdn.example.com/a.jpg", "/media/b.jpg"],
})
check("create -> 201 with pricing", r.status_code == 201 and r.json()["pricing"]["customer_price"] == "120.00", r.text[:150])
pid = r.json().get("id")

r = c.post("/commerce/products", headers=auth(seller_tok), json={
    "title": "Logo design", "vendor_price": "50.00", "kind": "service", "country": "KE", "city": city,
    "service_terms": "Three logo proposals, then the chosen one in vector formats."})
check("a service may have no stock", r.status_code == 201, r.text[:150])
service_id = r.json().get("id")

bad = [
    ("6 images", {"images": [f"/m/{i}.jpg" for i in range(6)]}),
    ("javascript: url", {"images": ["javascript:alert(1)"]}),
    ("url over 500 chars", {"images": ["https://x.example/" + "a" * 500]}),
    ("3-letter country", {"country": "KEN"}),
    ("numeric country", {"country": "12"}),
    ("negative stock", {"stock": -1}),
]
for label, extra in bad:
    r = c.post("/commerce/products", headers=auth(seller_tok),
               json={"title": "Bad listing", "vendor_price": "10.00", **extra})
    check(f"create rejects {label}", r.status_code == 422, f"{r.status_code} {r.text[:100]}")

print("\n== product detail ==")
r = c.get(f"/commerce/products/{pid}")
d = r.json()
check("public detail -> 200", r.status_code == 200, r.text[:150])
check("country stored uppercase", d.get("country") == "KE", d.get("country"))
check("images are a list", d.get("images") == ["https://cdn.example.com/a.jpg", "/media/b.jpg"], d.get("images"))
check("buyer sees customer_price", d.get("customer_price") == "120.00", d)
check("vendor_price and margin hidden from the public", "vendor_price" not in d and "margin" not in d, d)
check("vendor brief carries the user id", d.get("vendor", {}).get("user_id") == seller["id"], d.get("vendor"))
check("vendor_stats present", set(d.get("vendor_stats", {})) == {"listings_active", "sales_settled"}, d.get("vendor_stats"))
r = c.get(f"/commerce/products/{pid}", headers=auth(other_tok))
check("a stranger still gets no vendor_price", "vendor_price" not in r.json() and "margin" not in r.json())
r = c.get(f"/commerce/products/{pid}", headers=auth(seller_tok))
check("the owner gets the full split",
      r.json().get("vendor_price") == "100.00" and r.json().get("margin") == "20.00"
      and r.json().get("customer_price") == "120.00", r.text[:200])
check("unknown id -> 404", c.get("/commerce/products/prd_nope").status_code == 404)

print("\n== listing ==")
r = c.get("/commerce/products", params={"city": city.upper(), "sort": "price_asc"})
items = r.json().get("items", [])
check("city filter (case-insensitive) finds both", [i["id"] for i in items] == [service_id, pid],
      [i["id"] for i in items])
check("items carry a vendor brief", all(i.get("vendor", {}).get("user_id") == seller["id"] for i in items))
check("items never carry vendor_price", all("vendor_price" not in i for i in items))
r = c.get("/commerce/products", params={"city": city, "sort": "price_desc"})
check("price_desc reverses", [i["id"] for i in r.json()["items"]] == [pid, service_id])
r = c.get("/commerce/products", params={"city": city, "min_price": "120.00"})
check("min_price is on the customer price (inclusive)", [i["id"] for i in r.json()["items"]] == [pid],
      r.text[:150])
r = c.get("/commerce/products", params={"city": city, "max_price": "119.99"})
check("max_price 119.99 excludes the 120.00 listing", [i["id"] for i in r.json()["items"]] == [service_id],
      r.text[:150])
r = c.get("/commerce/products", params={"city": city, "min_price": "60.00", "max_price": "60.00"})
check("a bound of exactly 60.00 matches the 50.00 vendor price", [i["id"] for i in r.json()["items"]] == [service_id],
      r.text[:150])
check("kind=nonsense -> 422", c.get("/commerce/products", params={"kind": "nonsense"}).status_code == 422)
check("sort=nonsense -> 422", c.get("/commerce/products", params={"sort": "nonsense"}).status_code == 422)
r = c.get("/commerce/products", params={"city": city, "kind": "service"})
check("kind filter still works", [i["id"] for i in r.json()["items"]] == [service_id])

print("\n== seller workspace ==")
check("mine needs a login", c.get("/commerce/products/mine").status_code == 401)
r = c.get("/commerce/products/mine", headers=auth(seller_tok))
mine = {i["id"]: i for i in r.json().get("items", [])}
check("mine lists my listings with the split",
      r.status_code == 200 and mine.get(pid, {}).get("vendor_price") == "100.00"
      and mine[pid].get("margin") == "20.00" and mine[pid].get("stock") == 5, r.text[:200])
r = c.get("/commerce/products/mine", headers=auth(other_tok))
check("mine is private to the caller", pid not in {i["id"] for i in r.json().get("items", [])})

r = c.patch(f"/commerce/products/{pid}", headers=auth(other_tok), json={"title": "Hijacked"})
check("a stranger cannot edit -> 403", r.status_code == 403, r.status_code)
r = c.delete(f"/commerce/products/{pid}", headers=auth(other_tok))
check("a stranger cannot delete -> 403", r.status_code == 403, r.status_code)
r = c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok), json={"kind": "digital"})
check("kind is immutable -> 422", r.status_code == 422, r.status_code)
r = c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok), json={"vendor_price": "0"})
check("price must be > 0", r.status_code == 422, r.status_code)
r = c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok), json={"images": [f"/m/{i}.jpg" for i in range(6)]})
check("patch caps images at 5", r.status_code == 422, r.status_code)
r = c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok), json={"status": "removed"})
check("status can only be active|paused", r.status_code == 422, r.status_code)

# An order placed at 100.00 must keep its own price when the listing changes.
# A physical product needs a delivery address (see e2e_market_orders.py).
SHIPPING = {"full_name": "Ada Buyer", "line1": "1 Main St", "city": "Austin", "country": "US"}
r = c.post("/commerce/orders", headers=auth(buyer_tok), json={"acknowledge_policy": True, "policy_version": "2026-10-27", "product_id": pid, "quantity": 2, "shipping": SHIPPING})
check("buyer orders 2 -> 201", r.status_code == 201 and r.json()["customer_price"] == "240.00", r.text[:150])
order_id = r.json().get("id")
r = c.post("/commerce/orders", headers=auth(seller_tok), json={"acknowledge_policy": True, "policy_version": "2026-10-27", "product_id": pid})
check("own-listing purchase is still refused -> 400", r.status_code == 400, r.status_code)

r = c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok),
            json={"title": "Basket v2", "vendor_price": "200.00", "stock": 9, "city": "Nairobi", "status": "active"})
check("owner edits -> 200 with the new pricing",
      r.status_code == 200 and r.json()["pricing"]["customer_price"] == "240.00", r.text[:150])
r = c.get(f"/commerce/orders/{order_id}", headers=auth(buyer_tok))
check("the existing order keeps its price", r.json().get("customer_price") == "240.00", r.text[:150])

r = c.delete(f"/commerce/products/{pid}", headers=auth(seller_tok))
check("delete with a pending order -> 409", r.status_code == 409, f"{r.status_code} {r.text[:100]}")

print("\n== pause hides it ==")
c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok), json={"status": "paused"})
check("paused: public 404", c.get(f"/commerce/products/{pid}").status_code == 404)
check("paused: stranger 404", c.get(f"/commerce/products/{pid}", headers=auth(other_tok)).status_code == 404)
check("paused: owner 200", c.get(f"/commerce/products/{pid}", headers=auth(seller_tok)).status_code == 200)
r = c.get("/commerce/products", params={"city": "Nairobi"})
check("paused: not in the listing", pid not in {i["id"] for i in r.json()["items"]})
r = c.get("/commerce/products/mine", headers=auth(seller_tok))
check("paused: still in mine", any(i["id"] == pid and i["status"] == "paused" for i in r.json()["items"]))
c.patch(f"/commerce/products/{pid}", headers=auth(seller_tok), json={"status": "active"})

print("\n== sales and finances ==")
r = c.get("/commerce/sales", headers=auth(seller_tok))
sale = next((i for i in r.json().get("items", []) if i["id"] == order_id), None)
check("sales lists the order with a buyer brief",
      sale is not None and sale["buyer"]["user_id"] == buyer["id"], r.text[:200])
check("pending is not to_ship", sale is not None and sale["to_ship"] is False)
check("the buyer has no sales", c.get("/commerce/sales", headers=auth(buyer_tok)).json()["items"] == [])
check("sales status filter validates", c.get("/commerce/sales?status=bogus", headers=auth(seller_tok)).status_code == 422)
r = c.get("/commerce/seller/finances", headers=auth(seller_tok))
f0 = r.json()
check("pending order is not escrow money yet", f0.get("escrow_pending") == "0.00", f0)

fs = fund(buyer_tok, order_id, "240.00")
check("order funded into escrow", fs == 200, fs)
r = c.get("/commerce/sales?status=in_escrow", headers=auth(seller_tok))
sale = next((i for i in r.json().get("items", []) if i["id"] == order_id), None)
check("in_escrow and undelivered -> to_ship", sale is not None and sale["to_ship"] is True, r.text[:200])
f1 = c.get("/commerce/seller/finances", headers=auth(seller_tok)).json()
check("escrow_pending = vendor price of the whole order (2 x 100.00)", f1.get("escrow_pending") == "200.00", f1)
check("finances counts", f1.get("counts", {}).get("to_ship") == 1, f1)

r = c.delete(f"/commerce/products/{pid}", headers=auth(seller_tok))
check("delete with an order in escrow -> 409", r.status_code == 409, r.status_code)

r = c.post(f"/commerce/orders/{order_id}/delivered", headers=auth(seller_tok), json={"note": "sent"})
check("seller marks delivered", r.status_code == 200, r.text[:100])
sale = next((i for i in c.get("/commerce/sales", headers=auth(seller_tok)).json()["items"] if i["id"] == order_id), None)
check("delivered is no longer to_ship", sale is not None and sale["to_ship"] is False)
r = c.post(f"/commerce/orders/{order_id}/confirm-delivery", headers=auth(buyer_tok),
           json={"acknowledge_final": True, "policy_version": "2026-10-27"})
check("buyer confirms", r.status_code == 200, r.text[:100])
f2 = c.get("/commerce/seller/finances", headers=auth(seller_tok)).json()
check("released = 200.00, nothing pending", f2.get("released") == "200.00" and f2.get("escrow_pending") == "0.00", f2)

print("\n== storefront ==")
r = c.get(f"/commerce/vendors/{seller['id']}")
d = r.json()
check("storefront -> 200", r.status_code == 200, r.text[:150])
check("storefront vendor brief and stats",
      d.get("vendor", {}).get("user_id") == seller["id"] and d.get("stats", {}).get("sales_settled") == 1
      and d["stats"]["listings_active"] == 2, d.get("stats"))
check("storefront lists active listings only", {i["id"] for i in d.get("items", [])} == {pid, service_id})
r = c.get(f"/commerce/vendors/{seller['id']}", params={"limit": 1, "offset": 1})
check("storefront paginates", len(r.json()["items"]) == 1 and r.json()["total"] == 2)
check("a user with nothing -> 404", c.get(f"/commerce/vendors/{buyer['id']}").status_code == 404)

print("\n== soft delete ==")
check("delete the service -> 200", c.delete(f"/commerce/products/{service_id}", headers=auth(seller_tok)).status_code == 200)
check("removed: public 404", c.get(f"/commerce/products/{service_id}").status_code == 404)
check("removed: owner 404", c.get(f"/commerce/products/{service_id}", headers=auth(seller_tok)).status_code == 404)
r = c.get("/commerce/products/mine", headers=auth(seller_tok))
check("removed: gone from mine", service_id not in {i["id"] for i in r.json()["items"]})
check("settled order lets the listing go", c.delete(f"/commerce/products/{pid}", headers=auth(seller_tok)).status_code == 200)

print("\n" + ("ALL CHECKS PASSED" if ok else "THERE ARE FAILURES ABOVE"))
sys.exit(0 if ok else 1)
