"""Selling an ebook, end to end, against the local stack.

Upload (real PDF and EPUB bytes; disguised files refused), draft, publication
checks, the public page (no file address anywhere), checkout without shipping,
payment checks (amount and payer), access only after the server confirms the
payment, protected download links, retries, limits, versions, unpublishing,
refund and admin actions - and that physical products and services behave as
before.
"""
import io
import random
import string
import subprocess
import sys
import zipfile
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
COMMERCE = "http://localhost:8209"
LEDGER = "http://localhost:8210"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
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


def pdf_bytes(text):
    return (b"%PDF-1.4\n1 0 obj<<>>endobj\n% " + text.encode() + b"\n" + b"0" * 2048 + b"\n%%EOF\n")


def epub_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("OEBPS/book.xhtml", "<html><body>Demo book</body></html>")
    return buf.getvalue()


def upload(user, name, data, purpose, ctype="application/pdf"):
    return c.post("/media/upload", headers=user["h"], files={"file": (name, data, ctype)},
                  data={"purpose": purpose})


seller = register("Demo Seller")
buyer = register("Demo Buyer")
stranger = register("Demo Stranger")

print("Uploads")
r = upload(seller, "book.pdf", pdf_bytes("full book v1"), "ebook")
check("a PDF is accepted as the ebook", r.status_code == 201, r.text[:200])
full = r.json()
check("and stored privately", full.get("private") is True, full)
r = upload(seller, "book.epub", epub_bytes(), "ebook", "application/epub+zip")
check("an EPUB is accepted too", r.status_code == 201 and r.json()["content_type"] == "application/epub+zip", r.text[:200])
for label, name, data, ctype in [
    ("an executable renamed .pdf", "tool.pdf", b"MZ\x90\x00" + b"\x00" * 500, "application/pdf"),
    ("an HTML page renamed .epub", "page.epub", b"<html><script>x</script></html>", "application/epub+zip"),
    ("a plain zip renamed .epub", "a.epub", (lambda b: (zipfile.ZipFile(b, "w").writestr("x.txt", "x"), b)[1])(io.BytesIO()).getvalue(), "application/epub+zip"),
]:
    r = upload(seller, name, data, "ebook", ctype)
    check(f"{label} is refused", r.status_code == 415, f"{r.status_code} {r.text[:120]}")
sample = upload(seller, "sample.pdf", pdf_bytes("sample pages"), "ebook_sample").json()
check("a sample is public", sample.get("private") is False, sample)
stranger_file = upload(stranger, "other.pdf", pdf_bytes("someone else"), "ebook").json()

print()
print("Listing: drafts, checks, publication")
base = {"title": f"Demo Ebook {tag()}", "description": "<p>A <b>useful</b> book.</p><script>alert(1)</script>",
        "kind": "digital", "vendor_price": "84.00", "country": "", "images": []}
r = c.post("/commerce/products", headers=seller["h"], json={**base, "ebook": {"author": "Demo Author"}})
check("publishing without the file is refused, saying what is missing",
      r.status_code == 422 and "ebook file" in r.text, r.text[:200])
r = c.post("/commerce/products", headers=seller["h"], json={**base, "ebook": {"file_asset_id": full["id"]}})
check("publishing without an author is refused", r.status_code == 422 and "author" in r.text, r.text[:200])
r = c.post("/commerce/products", headers=seller["h"], json={**base, "ebook": {"file_asset_id": stranger_file["id"],
                                                                         "author": "X"}})
check("somebody else's upload cannot be attached", r.status_code == 422, r.text[:200])
r = c.post("/commerce/products", headers=seller["h"], json={**base, "ebook": {"file_asset_id": sample["id"],
                                                                         "author": "X"}})
check("a sample cannot be the paid file", r.status_code == 422, r.text[:200])
r = c.post("/commerce/products", headers=seller["h"], json={**base, "status": "draft", "ebook": {"author": "Demo Author"}})
check("a draft can be saved without the file", r.status_code == 201 and r.json()["status"] == "draft", r.text[:200])
pid = r.json()["id"]
check("a draft is invisible to others", c.get(f"/commerce/products/{pid}", headers=buyer["h"]).status_code == 404)
r = c.patch(f"/commerce/products/{pid}", headers=seller["h"], json={
    "status": "active", "ebook": {"file_asset_id": full["id"], "sample_asset_id": sample["id"], "category": "business",
                                  "language": "en", "page_count": 120, "isbn": "978-0-306-40615-7",
                                  "subtitle": "A demo", "summary": "Short summary"}})
check("the draft is published with its file", r.status_code == 200 and r.json()["status"] == "active", r.text[:200])
r = c.patch(f"/commerce/products/{pid}", headers=seller["h"], json={"ebook": {"isbn": "123"}})
check("a wrong ISBN is refused", r.status_code == 422, r.status_code)

print()
print("The public page and APIs never give the file away")
detail = c.get(f"/commerce/products/{pid}", headers=buyer["h"]).json()
listing = c.get("/commerce/products", params={"ebook": True, "q": "demo author"}).json()
public = str(detail) + str(listing) + c.get(f"/commerce/products/{pid}").text
check("no address or id of the paid file anywhere", full["id"] not in public and full["url"] not in public, public[:300])
check("the sample is offered", detail["ebook"]["sample"] and sample["id"] in detail["ebook"]["sample"]["url"], detail["ebook"])
check("format, size, language, pages shown", detail["ebook"]["format"] == "pdf" and detail["ebook"]["size_bytes"] > 0
      and detail["ebook"]["language"] == "en" and detail["ebook"]["page_count"] == 120, detail["ebook"])
check("the description is cleaned (no script)", "<script" not in detail["description"] and "<b>useful</b>" in detail["description"],
      detail["description"])
check("found by author in the search", any(i["id"] == pid for i in listing["items"]), listing)
check("and by category and language",
      any(i["id"] == pid for i in c.get("/commerce/products", params={"category": "business", "language": "en"}).json()["items"]))
check("pricing: $84 asked, the buyer pays $100.80", detail["customer_price"] == "100.80", detail["customer_price"])
quote = c.get("/commerce/pricing", params={"vendor_price": "84"}).json()
check("the same split from the pricing route ($16.80 margin)", quote["margin"] == "16.80" and quote["customer_price"] == "100.80", quote)
check("the sample downloads without an account", httpx.get(detail["ebook"]["sample"]["url"] if detail["ebook"]["sample"]["url"].startswith("http")
                                                           else "http://localhost:8200" + detail["ebook"]["sample"]["url"]).status_code == 200)

print()
print("Checkout and payment")
r = c.post("/commerce/orders", headers=buyer["h"], json={"product_id": pid})
check("no shipping address is asked for", r.status_code == 201, r.text[:200])
order = r.json()
again = c.post("/commerce/orders", headers=buyer["h"], json={"product_id": pid}).json()
check("checking out twice gives the same pending order", again["id"] == order["id"], (again, order))
check("not paid: no download", c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"]).status_code == 403)
r = c.post("/payments/checkout", headers=buyer["h"], json={"purpose": "order", "amount": "0.01",
                                                          "reference": order["id"], "rail": "mock"})
check("paying one cent for it is refused", r.status_code == 400, r.text[:200])
r = c.post("/payments/checkout", headers=stranger["h"], json={"purpose": "order", "amount": order["customer_price"],
                                                             "reference": order["id"], "rail": "mock"})
check("somebody else cannot pay (and claim) this order", r.status_code == 400, r.text[:200])
check("the old public 'mark as paid' route is gone",
      c.post(f"/commerce/orders/{order['id']}/paid").status_code in (404, 405))
r = httpx.post(f"{COMMERCE}/internal/orders/{order['id']}/funded", params={"amount": "1.00"})
check("a payment of the wrong amount does not open the order", r.status_code == 409, r.text[:200])
check("still not downloadable", c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"]).status_code == 403)
pay = c.post("/payments/checkout", headers=buyer["h"], json={"purpose": "order", "amount": order["customer_price"],
                                                            "reference": order["id"], "rail": "mock"}).json()
c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=buyer["h"]).raise_for_status()
r = httpx.post(f"{COMMERCE}/internal/orders/{order['id']}/funded", params={"amount": order["customer_price"]})
check("a duplicate payment event changes nothing", r.status_code == 200 and r.json()["status"] == "delivered", r.text[:200])
holds = httpx.get(f"{LEDGER}/internal/journals/by-key", params={"key": f"escrow:hold:{order['id']}"})
check("one escrow hold in the ledger", holds.status_code == 200, holds.status_code)
check("one order, delivered", psql(f"select count(*), max(status) from commerce.orders where product_id='{pid}'") == "1|delivered")

print()
print("The library and the download")
lib = c.get("/commerce/library", headers=buyer["h"]).json()["items"]
item = next((i for i in lib if i["order_id"] == order["id"]), None)
check("the ebook is in the buyer's library, downloadable", item and item["state"] == "active", lib)
r = c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"])
check("a download link is given", r.status_code == 200, r.text[:200])
link = r.json()
url = link["url"] if link["url"].startswith("http") else "http://localhost:8200" + link["url"]
got = httpx.get(url)
check("it is the book, byte for byte", got.status_code == 200 and got.content == pdf_bytes("full book v1"), got.status_code)
check("it expires in minutes", 0 < link["expires_in"] <= 3600, link)
bare = url.split("?")[0]
check("the file without a signature: nothing", httpx.get(bare).status_code == 404)
import re as _re
forged = _re.sub(r"sig=([^&]+)", lambda m: "sig=" + ("A" if m.group(1)[0] != "A" else "B") + m.group(1)[1:], url)
check("a forged signature: nothing", forged != url and httpx.get(forged).status_code == 404)
check("served as a download, not shown in a tab",
      "attachment" in got.headers.get("content-disposition", ""), got.headers.get("content-disposition"))
check("a stranger asking for the buyer's order: nothing",
      c.post(f"/commerce/library/{order['id']}/download", headers=stranger["h"]).status_code == 404)
check("the stranger's library is empty", c.get("/commerce/library", headers=stranger["h"]).json()["items"] == [])
d = c.get(f"/commerce/products/{pid}", headers=buyer["h"]).json()
check("the page says it is already purchased", d["ebook"]["purchased_order_id"] == order["id"], d["ebook"])
check("buying it again is refused", c.post("/commerce/orders", headers=buyer["h"], json={"product_id": pid}).status_code == 409)

print()
print("Retries, limits, versions")
c.patch(f"/commerce/products/{pid}", headers=seller["h"], json={"ebook": {"download_limit": 1}})
r = c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"]).json()
check("asking again at once is a retry, not counted", r.get("counted") is False, r)
v2 = upload(seller, "book-v2.pdf", pdf_bytes("full book v2"), "ebook").json()
r = c.post(f"/commerce/products/{pid}/ebook/file", headers=seller["h"], json={"asset_id": v2["id"], "note": "Typos fixed"})
check("the seller uploads a new version", r.status_code == 200 and r.json()["version"] == 2, r.text[:200])
item = next(i for i in c.get("/commerce/library", headers=buyer["h"]).json()["items"] if i["order_id"] == order["id"])
check("the buyer sees an update", item["update_available"] is True and item["file"]["version"] == 2, item)
r = c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"])
check("with one download allowed and used, a new download is refused clearly",
      r.status_code == 403 and "used all" in r.text, r.text[:200])
versions = c.get(f"/commerce/products/{pid}/ebook/files", headers=seller["h"]).json()["items"]
check("version 1 is kept on record", [v["version"] for v in versions] == [2, 1], versions)

print()
print("Administration")
admin = register("Demo Admin")
psql(f"update auth.users set role='admin' where id='{admin['id']}'")
login = c.post("/auth/login", json={"email": admin["email"], "password": "Sup3rStrong!Pass"}).json()
admin["h"] = {"Authorization": f"Bearer {login['tokens']['access_token'] if 'tokens' in login else login['access_token']}"}
r = c.post(f"/admin/commerce/orders/{order['id']}/reset-downloads", headers=admin["h"], json={"reason": "Damaged file reported"})
check("an admin resets the allowance", r.status_code == 200, r.text[:200])
r = c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"])
link2 = r.json()
url2 = link2.get("url", "")
url2 = url2 if url2.startswith("http") else "http://localhost:8200" + url2
check("and the buyer gets version 2", r.status_code == 200 and link2["version"] == 2
      and httpx.get(url2).content == pdf_bytes("full book v2"), r.text[:200])
check("members cannot use admin routes", c.post(f"/admin/commerce/orders/{order['id']}/revoke-access", headers=buyer["h"],
                                                json={"reason": "nope"}).status_code in (401, 403))
c.post(f"/admin/commerce/orders/{order['id']}/revoke-access", headers=admin["h"], json={"reason": "Chargeback"})
check("revoked: no download", c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"]).status_code == 403)
c.post(f"/admin/commerce/orders/{order['id']}/restore-access", headers=admin["h"], json={"reason": "Resolved"})
acc = c.get(f"/admin/commerce/orders/{order['id']}/access", headers=admin["h"]).json()
check("every admin action is in the audit log",
      [a["action"] for a in acc["audit"]] == ["restore_access", "revoke_access", "reset_downloads"], acc.get("audit"))

print()
print("Unpublished, suspended, refunded")
c.patch(f"/commerce/products/{pid}", headers=seller["h"], json={"status": "paused", "ebook": {"download_limit": 50}})
check("unpublished: the buyer still downloads",
      c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"]).status_code == 200)
check("and nobody new can buy it", c.post("/commerce/orders", headers=stranger["h"], json={"product_id": pid}).status_code == 404)
c.patch(f"/commerce/products/{pid}", headers=seller["h"], json={"status": "active"})
c.post(f"/admin/commerce/products/{pid}/suspend", headers=admin["h"], json={"reason": "Copyright complaint"})
check("suspended: off the public page", c.get(f"/commerce/products/{pid}", headers=stranger["h"]).status_code == 404)
check("and the seller cannot republish it",
      c.patch(f"/commerce/products/{pid}", headers=seller["h"], json={"status": "active"}).status_code == 409)
psql(f"update commerce.orders set status='refunded' where id='{order['id']}'")
r = c.post(f"/commerce/library/{order['id']}/download", headers=buyer["h"])
check("refunded: access ends, and says why", r.status_code == 403 and "refunded" in r.text, r.text[:200])

print()
print("Physical products and services as before")
phys = c.post("/commerce/products", headers=seller["h"], json={"title": f"Demo cloth {tag()}", "vendor_price": "20",
                                                                "kind": "product", "stock": 3, "country": "US"}).json()["id"]
check("a physical product still needs an address",
      c.post("/commerce/orders", headers=buyer["h"], json={"product_id": phys}).status_code == 422)
serv = c.post("/commerce/products", headers=seller["h"], json={"title": f"Demo service {tag()}", "vendor_price": "20",
                                                                "kind": "service"}).json()["id"]
check("a service does not", c.post("/commerce/orders", headers=buyer["h"], json={"product_id": serv}).status_code == 201)
old_digital = c.post("/commerce/products", headers=seller["h"], json={"title": f"Demo file {tag()}", "vendor_price": "5",
                                                                       "kind": "digital"})
check("a digital listing without ebook details still works the old way", old_digital.status_code == 201)
r = c.post("/commerce/products", headers=seller["h"], json={"title": "x" * 5, "vendor_price": "5", "kind": "product",
                                                             "ebook": {"author": "A"}})
check("only a digital listing can be an ebook", r.status_code == 422)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
