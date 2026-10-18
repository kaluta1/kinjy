""""Only me" posts: the author sees them, nobody else does, anywhere.

Not a follower, not somebody named in it, not a signed-out visitor through
the share link. Asked for by a member on 18/10: posting something visible only
to yourself.
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
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG",
    })
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "handle": f"u{t}"}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def ids(items):
    return {p["id"] for p in items}


author = register("Demo Author")
follower = register("Demo Follower")
named = register("Demo Named")
c.post(f"/users/{author['id']}/follow", headers=H(follower))

print('"Only me" posts')
r = c.post("/posts", headers=H(author), json={
    "body": f"A note to myself, mentioning @{named['handle']}", "visibility": "private",
})
check("an only-me post can be written", r.status_code == 201 and r.json()["visibility"] == "private", r.text[:200])
post = r.json()["id"]
public = c.post("/posts", headers=H(author), json={"body": "Hello everyone", "visibility": "public"}).json()["id"]

check("the author opens it", c.get(f"/posts/{post}", headers=H(author)).status_code == 200)
check("a follower cannot", c.get(f"/posts/{post}", headers=H(follower)).status_code in (403, 404))
check("the person named in it cannot", c.get(f"/posts/{post}", headers=H(named)).status_code in (403, 404))
check("nor a signed-out visitor with the link", c.get(f"/posts/{post}").status_code in (401, 403, 404))

mine = ids(c.get(f"/posts/by/{author['id']}", headers=H(author)).json().get("items", []))
check("it is on the author's own profile", post in mine, mine)
theirs = ids(c.get(f"/posts/by/{author['id']}", headers=H(follower)).json().get("items", []))
check("not on the profile as a follower sees it", post not in theirs and public in theirs, theirs)
for mode in ("following", "new", "global"):
    seen = ids(c.get(f"/feed?mode={mode}&limit=100", headers=H(follower)).json().get("items", []))
    check(f"not in a follower's {mode} feed", post not in seen)
check("no comment from somebody else",
      c.post(f"/posts/{post}/comments", headers=H(follower), json={"body": "hi"}).status_code in (403, 404))
check("cannot be reposted, even by the author",
      c.post(f"/posts/{post}/repost", headers=H(author), json={"body": "look"}).status_code == 400)

time.sleep(1.5)
notes = c.get("/notifications", headers=H(named)).json()
items = notes.get("items", notes if isinstance(notes, list) else [])
check("the person named is not notified", not any(post in str(n) for n in items), items[:2])

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
