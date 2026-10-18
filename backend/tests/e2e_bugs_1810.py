"""The fixes for what the members reported in the bug group (18/10).

* a photo post is visible to a signed-out visitor, and its share preview shows
  the photo - not the author's avatar - unless it is marked adult;
* "show less like this" hides the post from every feed, the date-ordered ones
  included;
* the community list says where the viewer stands (joined, request sent) and
  who may manage;
* a member who is not discoverable can still be found - and so @mentioned -
  by people they are connected with, and only by them;
* a one-to-one chat can be pinned, and does not use up the free group pins;
* a group can keep its earlier messages from newcomers.
"""
import random
import string
import struct
import subprocess
import sys
import time
import zlib
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
SITE = "http://localhost:8200"
c = httpx.Client(base_url=BASE, timeout=90)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG",
    })
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "handle": f"u{t}", "name": name}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def png():
    raw = b"".join(b"\x00" + bytes([200, 120, 40] * 32) for _ in range(32))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def photo_post(user, mature=False):
    asset = c.post("/media/upload", headers=H(user), files={"file": ("p.png", png(), "image/png")},
                   data={"provenance": "original"}).json()
    r = c.post("/posts", headers=H(user), json={
        "body": "", "visibility": "public", "mature": mature,
        "media": [{"media_id": asset["id"], "url": asset["url"], "kind": "image"}],
    })
    r.raise_for_status()
    return r.json()["id"]


a = register("Demo Author")
b = register("Demo Reader")
stranger = register("Demo Stranger")

print("Photo posts, signed out and shared")
plain = photo_post(a)
adult = photo_post(a, mature=True)
check("a photo post opens for a signed-out visitor", c.get(f"/posts/{plain}").status_code == 200)
check("an adult-marked one does not", c.get(f"/posts/{adult}").status_code == 404)
og = httpx.get(f"{SITE}/api/og/p/{plain}", timeout=30).text
check("its share preview points at the post's own picture", f"/api/og/image/{plain}" in og, og[:300])
img = httpx.get(f"{SITE}/api/og/image/{plain}", timeout=30, follow_redirects=False)
check("and that picture is served as a JPEG", img.status_code == 200 and img.headers.get("content-type") == "image/jpeg",
      (img.status_code, img.headers.get("content-type")))

print()
print("Show less like this")
mark = c.post("/posts", headers=H(a), json={"body": f"hide me {random.random()}", "visibility": "public"}).json()["id"]
before = {p["id"] for p in c.get("/feed?mode=new&limit=50", headers=H(b)).json()["items"]}
check("the post is in the reader's New feed", mark in before)
r = c.post("/feed/signals", headers=H(b), json={"kind": "less_like_this", "target_type": "post", "target_id": mark})
check("show less is recorded", r.status_code == 201, r.text[:120])
for mode in ("new", "global", "for_you"):
    seen = {p["id"] for p in c.get(f"/feed?mode={mode}&limit=50", headers=H(b)).json().get("items", [])}
    check(f"and the post is gone from the {mode} feed", mark not in seen)
check("for the reader only", mark in {p["id"] for p in c.get("/feed?mode=new&limit=50", headers=H(a)).json()["items"]})

print()
print("Communities: Join shows, Manage only for those who manage")
slug_name = f"Demo Club {random.randint(1000, 9999)}"
club = c.post("/communities", headers=H(a), json={"name": slug_name, "kind": "public"}).json()


def card(user):
    items = c.get(f"/communities?q={slug_name}", headers=H(user)).json()["items"]
    return next(i for i in items if i["id"] == club["id"])


check("the owner sees it as theirs", card(a)["my_role"] == "owner", card(a))
check("somebody else, as not joined", card(b)["my_status"] is None and card(b)["my_role"] is None)
c.post(f"/communities/{club['id']}/join", headers=H(b))
check("after Join, as joined - a plain member", card(b)["my_status"] == "active" and card(b)["my_role"] == "member", card(b))

print()
print("Mentions of somebody who is not discoverable")
c.patch("/preferences", headers=H(a), json={"discoverable": False})
c.post(f"/connections/{a['id']}", headers=H(b), json={})
c.post(f"/connections/{b['id']}/respond", headers=H(a), params={"accept": True})
found = [p["user_id"] for p in c.get(f"/users/search?q={a['handle']}", headers=H(b)).json()["items"]]
check("a connection still finds them, so can mention them", a["id"] in found, found)
found = [p["user_id"] for p in c.get(f"/users/search?q={a['handle']}", headers=H(stranger)).json()["items"]]
check("a stranger still does not", a["id"] not in found, found)

print()
print("Pinning a one-to-one chat")
direct = c.post("/conversations", headers=H(b), json={"participant_ids": [a["id"]], "kind": "direct"}).json()
r = c.post(f"/conversations/{direct['id']}/pin", headers=H(b))
check("a one-to-one chat can be pinned", r.status_code == 200 and r.json()["pinned"], r.text[:150])
listing = c.get("/conversations", headers=H(b)).json()["items"]
check("and comes first, marked pinned", listing[0]["id"] == direct["id"] and listing[0]["pinned"], listing[:1])
groups = [c.post("/conversations", headers=H(b), json={"participant_ids": [a["id"]], "kind": "group",
                                                        "title": f"G{i}"}).json()["id"] for i in range(3)]
pins = [c.post(f"/conversations/{g}/pin", headers=H(b)).status_code for g in groups]
check("and does not use up the three free group pins", pins == [200, 200, 200], pins)

print()
print("A group's earlier messages and newcomers")
group = c.post("/conversations", headers=H(a), json={"participant_ids": [b["id"]], "kind": "group",
                                                       "title": "History"}).json()["id"]
c.post(f"/conversations/{group}/messages", headers=H(a), json={"body": "said before"})
r = c.patch(f"/conversations/{group}/history", headers=H(b), json={"history": "from_join"})
check("a member who does not run the group cannot change it", r.status_code == 403, r.status_code)
r = c.patch(f"/conversations/{group}/history", headers=H(a), json={"history": "from_join"})
check("an admin sets: newcomers see only what follows", r.status_code == 200 and r.json()["history"] == "from_join", r.text[:150])
time.sleep(1.1)
c.post(f"/connections/{a['id']}", headers=H(stranger), json={})
c.post(f"/connections/{stranger['id']}/respond", headers=H(a), params={"accept": True})
r = c.post(f"/conversations/{group}/participants", headers=H(a), json={"user_ids": [stranger["id"]]})
check("a newcomer is added", r.status_code == 201, r.text[:150])
c.post(f"/conversations/{group}/accept", headers=H(stranger))
c.post(f"/conversations/{group}/messages", headers=H(a), json={"body": "said after"})
bodies = [m.get("body") for m in c.get(f"/conversations/{group}/messages", headers=H(stranger)).json()["items"]]
check("the newcomer does not see what was said before", "said before" not in bodies, bodies)
check("but sees what follows", "said after" in bodies, bodies)
bodies = [m.get("body") for m in c.get(f"/conversations/{group}/messages", headers=H(a)).json()["items"]]
check("the admin still reads everything", "said before" in bodies, bodies)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
