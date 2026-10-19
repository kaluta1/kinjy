"""A community's settings are its owner's; its requests are answered; its members can be seen.

* the owner renames it and changes its description; a moderator or a member
  cannot; an image id that is not the owner's own upload is refused;
* privacy and "anyone can read" are the owner's;
* a private community: joining is a request, the owner sees how many wait and
  admits them; the notification leads to the queue;
* the member list: members see it, so does anybody on a public community,
  nobody else on a private one.
"""
import random
import string
import sys
import time
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG"})
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"]}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


owner = register("Demo Owner")
mod = register("Demo Moderator")
member = register("Demo Member")
asker = register("Demo Asker")
outsider = register("Demo Outsider")

club = c.post("/communities", headers=H(owner), json={"name": f"Demo Circle {random.randint(1000, 9999)}",
                                                       "kind": "public"}).json()
cid = club["id"]
for u in (mod, member):
    c.post(f"/communities/{cid}/join", headers=H(u))
c.post(f"/communities/{cid}/members/{mod['id']}", headers=H(owner), json={"action": "promote"})

print("Settings")
r = c.patch(f"/communities/{cid}", headers=H(owner), json={"name": "Demo Circle renamed", "description": "What we do"})
check("the owner renames it and describes it", r.status_code == 200 and r.json()["name"] == "Demo Circle renamed", r.text[:160])
check("a moderator cannot", c.patch(f"/communities/{cid}", headers=H(mod), json={"name": "Mine"}).status_code == 403)
check("nor a member", c.patch(f"/communities/{cid}", headers=H(member), json={"name": "Mine"}).status_code == 403)
r = c.patch(f"/communities/{cid}", headers=H(owner), json={"cover_asset_id": "mda_notmine0000000"})
check("an image that is not the owner's upload is refused", r.status_code == 422, r.status_code)
r = c.patch(f"/communities/{cid}", headers=H(owner), json={"cover_asset_id": None, "avatar_asset_id": None})
check("taking the images off works", r.status_code == 200 and r.json()["cover_url"] is None, r.text[:160])
check("unknown fields are refused", c.patch(f"/communities/{cid}", headers=H(owner), json={"owner_id": "x"}).status_code == 422)

print()
print("Members list")
roster = c.get(f"/communities/{cid}/roster", headers=H(outsider))
check("anybody sees who is in a public community", roster.status_code == 200 and roster.json()["total"] == 3, roster.text[:160])
roles = [i["role"] for i in roster.json()["items"]]
check("the owner first, then the moderator", roles[:2] == ["owner", "moderator"], roles)
check("with names", all(i["profile"] and i["profile"]["display_name"] for i in roster.json()["items"]))

print()
print("Private: requests, answered by the owner")
check("the moderator cannot change who can join",
      c.patch(f"/communities/{cid}/privacy", headers=H(mod), json={"kind": "private"}).status_code == 403)
r = c.patch(f"/communities/{cid}/privacy", headers=H(owner), json={"kind": "private"})
check("the owner makes it private", r.status_code == 200, r.text[:160])
check("an outsider no longer sees its members", c.get(f"/communities/{cid}/roster", headers=H(outsider)).status_code == 404)
check("a member still does", c.get(f"/communities/{cid}/roster", headers=H(member)).status_code == 200)
r = c.post(f"/communities/{cid}/join", headers=H(asker))
check("joining is a request", r.status_code == 200 and r.json()["status"] == "pending", r.text[:160])
detail = c.get(f"/communities/{cid}", headers=H(owner)).json()
check("the owner sees one waiting", detail.get("pending_count") == 1, detail.get("pending_count"))
check("a member is not told", c.get(f"/communities/{cid}", headers=H(member)).json().get("pending_count") is None)
time.sleep(1.5)
notes = c.get("/notifications", headers=H(owner)).json()
items = notes.get("items", notes if isinstance(notes, list) else [])
link = next((n.get("link") for n in items if n.get("kind") == "community_request"), None)
check("the owner is notified, with a link to the requests",
      bool(link) and link.endswith("?manage=requests") and "/communities/" in link, link)
r = c.post(f"/communities/{cid}/members/{asker['id']}", headers=H(owner), json={"action": "approve"})
check("the owner admits them", r.status_code == 200, r.text[:160])
check("and they are a member", c.get(f"/communities/{cid}", headers=H(asker)).json()["my_status"] == "active")
check("nobody waiting any more", c.get(f"/communities/{cid}", headers=H(owner)).json().get("pending_count") == 0)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
