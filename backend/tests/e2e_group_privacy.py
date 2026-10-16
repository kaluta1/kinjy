"""A group's privacy, changed after the fact, and a link to it whatever it is.

The owner (or a moderator) moves a group between public, private and secret.
Opening it answers whoever was waiting; closing it keeps the members it has.
A paid group stays paid. And every group has a link worth sending: a public or
private one opens from its plain address, a secret one from a link carrying an
access code that only a member can obtain - and that link only knocks.

Every "refused" below is checked against community-service itself first: a
404 from the wrong service would pass the same assertion.
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


def register(name):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(),
        "country": "UG",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


print("Group privacy and share links")

health = httpx.get("http://localhost:8204/health", timeout=5).json()
check("port 8204 is community-service", "community" in str(health), health)

owner_t, owner = register("Test Owner")
mod_t, mod = register("Test Moderator")
member_t, member = register("Test Member")
waiting_t, waiting = register("Test Waiting")
stranger_t, stranger = register("Test Stranger")

r = c.post("/communities", headers=H(owner_t), json={"name": f"Privacy {tag()}", "kind": "private"})
gid, slug = r.json()["id"], r.json()["slug"]

# A moderator and a member, admitted the ordinary way.
for t, u in ((mod_t, mod), (member_t, member)):
    c.post(f"/communities/{gid}/join", headers=H(t))
    c.post(f"/communities/{gid}/members/{u['id']}", headers=H(owner_t), json={"action": "approve"})
c.post(f"/communities/{gid}/members/{mod['id']}", headers=H(owner_t), json={"action": "promote"})
r = c.post(f"/communities/{gid}/join", headers=H(waiting_t))
check("a request to a private group waits", r.json().get("status") == "pending", r.text)

# --- who may change it --------------------------------------------------------
r = c.patch(f"/communities/{gid}/privacy", headers=H(member_t), json={"kind": "public"})
check("a plain member cannot change the privacy", r.status_code == 403, r.text)
r = c.patch(f"/communities/{gid}/privacy", headers=H(stranger_t), json={"kind": "public"})
check("an outsider cannot change the privacy", r.status_code == 403, r.text)
r = c.patch(f"/communities/{gid}/privacy", headers=H(owner_t), json={"kind": "paid"})
check("privacy cannot be set to paid", r.status_code == 422, r.text)
r = c.patch(f"/communities/{gid}/privacy", json={"kind": "public"})
check("nobody signed in cannot change it", r.status_code == 401, r.text)

# --- private -> public ----------------------------------------------------------
before = c.get(f"/communities/{gid}", headers=H(owner_t)).json()["members_count"]
r = c.patch(f"/communities/{gid}/privacy", headers=H(mod_t), json={"kind": "public"})
check("a moderator makes it public", r.status_code == 200 and r.json()["kind"] == "public", r.text)
check("the waiting request is admitted", r.json().get("admitted") == 1, r.text)
d = c.get(f"/communities/{gid}", headers=H(waiting_t)).json()
check("the waiting member is now active", d["my_status"] == "active", d)
check("the count follows", d["members_count"] == before + 1, (before, d["members_count"]))
check("an anonymous visitor reads it", c.get(f"/communities/{slug}").status_code == 200)
r = c.post(f"/communities/{gid}/join", headers=H(stranger_t))
check("a stranger walks straight in", r.json().get("status") == "active", r.text)
c.post(f"/communities/{gid}/leave", headers=H(stranger_t))
r = c.patch(f"/communities/{gid}/privacy", headers=H(owner_t), json={"kind": "public"})
check("setting the same value changes nothing", r.json().get("changed") is False, r.text)

# --- public -> private ----------------------------------------------------------
r = c.patch(f"/communities/{gid}/privacy", headers=H(owner_t), json={"kind": "private"})
check("the owner makes it private again", r.json().get("kind") == "private", r.text)
d = c.get(f"/communities/{gid}", headers=H(member_t)).json()
check("existing members stay", d["my_status"] == "active", d)
r = c.post(f"/communities/{gid}/join", headers=H(stranger_t))
check("a newcomer now has to ask", r.json().get("status") == "pending", r.text)
c.post(f"/communities/{gid}/leave", headers=H(stranger_t))
listed = [x["id"] for x in c.get(f"/communities?q={slug.split('-')[1]}").json()["items"]]
check("a private group is still listed", gid in listed, listed)

# --- share links: public and private ------------------------------------------
r = c.post(f"/communities/{gid}/share-link", headers=H(stranger_t))
check("a private group's link is its plain address",
      r.status_code == 200 and r.json() == {"path": f"/communities/{slug}", "invite": None}, r.text)
d = c.get(f"/communities/{slug}", headers=H(stranger_t))
check("and that address opens for an outsider", d.status_code == 200 and d.json()["kind"] == "private", d.text)

# --- secret ---------------------------------------------------------------------
r = c.patch(f"/communities/{gid}/privacy", headers=H(owner_t), json={"kind": "secret"})
check("the owner makes it secret", r.json().get("kind") == "secret", r.text)
check("a secret group is 404 to an outsider",
      c.get(f"/communities/{slug}", headers=H(stranger_t)).status_code == 404)
check("and 404 anonymously", c.get(f"/communities/{slug}").status_code == 404)
listed = [x["id"] for x in c.get(f"/communities?q={slug.split('-')[1]}").json()["items"]]
check("a secret group is not listed", gid not in listed, listed)
r = c.post(f"/communities/{gid}/share-link", headers=H(stranger_t))
check("an outsider cannot get the secret link", r.status_code == 404, r.text)
r = c.post(f"/communities/{gid}/join", headers=H(stranger_t))
check("an outsider cannot join a secret group by its id", r.status_code == 404, r.text)

r = c.post(f"/communities/{gid}/share-link", headers=H(member_t))
check("a member gets the secret link", r.status_code == 200 and r.json()["invite"], r.text)
code, path = r.json()["invite"], r.json()["path"]
check("the link carries ?access=", path == f"/communities/{slug}?access={code}", path)
again = c.post(f"/communities/{gid}/share-link", headers=H(owner_t)).json()["invite"]
check("every member shares the same code", again == code, (again, code))

check("a wrong code is still 404",
      c.get(f"/communities/{slug}?invite=nope{code}", headers=H(stranger_t)).status_code == 404)
d = c.get(f"/communities/{slug}?invite={code}", headers=H(stranger_t))
check("the right code opens the page", d.status_code == 200 and d.json()["name"], d.text)
check("without revealing the code back", "invite_code" not in d.text and code not in d.text, d.text)
check("anonymously too", c.get(f"/communities/{slug}?invite={code}").status_code == 200)
feed = c.get(f"/feed/community/{gid}", headers=H(stranger_t))
check("the code does not open the posts", feed.status_code in (403, 404), feed.status_code)
r = c.post(f"/communities/{gid}/join?invite={code}", headers=H(stranger_t))
check("joining by the link only knocks", r.json().get("status") == "pending", r.text)
d = c.get(f"/communities/{slug}", headers=H(stranger_t))
check("the person waiting still sees the page they knocked on",
      d.status_code == 200 and d.json()["my_status"] == "pending", d.text)
r = c.post(f"/communities/{gid}/members/{stranger['id']}", headers=H(mod_t), json={"action": "approve"})
check("a moderator lets them in", r.status_code == 200, r.text)

# --- paid stays paid ------------------------------------------------------------
r = c.post("/communities", headers=H(owner_t), json={"name": f"Paid {tag()}", "kind": "paid", "price_usd": 5})
pid = r.json()["id"]
r = c.patch(f"/communities/{pid}/privacy", headers=H(owner_t), json={"kind": "public"})
check("a paid group cannot be made free from here", r.status_code == 409, r.text)
r = c.post(f"/communities/{pid}/share-link", headers=H(stranger_t))
check("a paid group still has a link", r.status_code == 200 and r.json()["invite"] is None, r.text)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
