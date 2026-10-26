"""A community's forums: listed on its page, opened by its stewards, read by whoever its door admits."""
import random
import string
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
                                       "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(),
                                       "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


owner = register("Demo Owner")
member = register("Demo Member")
outsider = register("Demo Outsider")

private = c.post("/communities", headers=owner["h"], json={"name": "Demo Private Forums", "kind": "private"}).json()
public = c.post("/communities", headers=owner["h"], json={"name": "Demo Public Forums", "kind": "public"}).json()
c.post(f"/communities/{private['id']}/join", headers=member["h"])
c.post(f"/communities/{private['id']}/members/{member['id']}", headers=owner["h"], json={"action": "approve"})

print("Who opens a community forum")
r = c.post("/forums", headers=member["h"], json={"name": "Member attempt", "community_id": private["id"]})
check("a member who does not run it cannot", r.status_code == 403, r.text)
r = c.post("/forums", headers=outsider["h"], json={"name": "Outsider attempt", "community_id": private["id"]})
check("nor an outsider", r.status_code in (403, 404), r.text)
r = c.post("/forums", headers=owner["h"], json={"name": "Private questions", "description": "Ask here",
                                                 "community_id": private["id"]})
check("its owner can", r.status_code == 201, r.text)
pf = r.json()
pub = c.post("/forums", headers=owner["h"], json={"name": "Public questions", "community_id": public["id"]}).json()

print()
print("The community's list")
r = c.get("/forums", headers=member["h"], params={"community_id": private["id"]})
items = r.json().get("items", [])
check("a member sees its forums", r.status_code == 200 and [i["id"] for i in items] == [pf["id"]], r.text)
check("each says which community it belongs to",
      items and items[0]["community"] == {"id": private["id"], "slug": private["slug"], "name": "Demo Private Forums"}, items)
check("and what it is for", items and items[0]["description"] == "Ask here", items)
check("an outsider is refused", c.get("/forums", headers=outsider["h"], params={"community_id": private["id"]}).status_code == 403)
check("so is a visitor", c.get("/forums", params={"community_id": private["id"]}).status_code == 403)
r = c.get("/forums", params={"community_id": public["id"]})
check("a public community's forums are open to a visitor",
      r.status_code == 200 and [i["id"] for i in r.json()["items"]] == [pub["id"]], r.text)
check("only that community's", pf["id"] not in [i["id"] for i in r.json()["items"]])

print()
print("Reading and writing in it")
t = c.post(f"/forums/{pf['id']}/threads", headers=member["h"], json={"title": "First question here", "body": "How does this work?"})
check("a member starts a thread", t.status_code == 201, t.text)
check("a member reads it", c.get(f"/threads/{t.json()['id']}", headers=member["h"]).status_code == 200)
check("a visitor does not", c.get(f"/threads/{t.json()['id']}").status_code in (403, 404))
check("an outsider cannot post", c.post(f"/forums/{pf['id']}/threads", headers=outsider["h"],
                                         json={"title": "Let me in please", "body": "Hello"}).status_code == 403)

print()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
