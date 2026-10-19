"""A post in a group notifies its members.

- every active member is told, with the author's name, the group's name, an
  excerpt and a link to the post;
- never the author; a pending member (not let in yet) is not told;
- a member who turned notifications off for the group is not told, and is
  again once they turn them back on;
- somebody mentioned in the post gets the mention, not a second notification;
- a post elsewhere (public) tells the group nothing.
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
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG"}
    r = c.post("/auth/register", json=body)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "handle": f"u{t}"}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def group_posts(u):
    items = c.get("/notifications", headers=H(u)).json()
    items = items.get("items", items if isinstance(items, list) else [])
    return [n for n in items if n.get("kind") == "community_post"]


owner = register("Demo Owner")
member = register("Demo Member")
quiet = register("Demo Quiet")
named = register("Demo Named")
waiting = register("Demo Waiting")

slug = "g" + "".join(random.choices(string.ascii_lowercase, k=8))
r = c.post("/communities", headers=H(owner), json={"name": f"Demo Group {slug}", "slug": slug, "kind": "public",
                                                    "description": "A test group"})
check("group created", r.status_code in (200, 201), r.text[:200])
group = r.json()
for u in (member, quiet, named):
    r = c.post(f"/communities/{group['id']}/join", headers=H(u))
    check("joined", r.status_code == 200, r.text[:200])

detail = c.get(f"/communities/{group['id']}", headers=H(member)).json()
check("notifications are on by default", detail.get("my_notify_posts") is True, detail)
r = c.put(f"/communities/{group['id']}/notifications", headers=H(quiet), json={"notify_posts": False})
check("a member can turn them off", r.status_code == 200 and r.json()["notify_posts"] is False, r.text[:200])
r = c.put(f"/communities/{group['id']}/notifications", headers=H(waiting), json={"notify_posts": True})
check("a non-member cannot set them", r.status_code == 404, r.status_code)

print()
print("A post in the group")
r = c.post("/posts", headers=H(owner), json={"body": f"Hello group, welcome @{named['handle']}",
                                             "visibility": "community", "community_id": group["id"]})
check("published", r.status_code == 201, r.text[:200])
post = r.json()
time.sleep(2.5)
got = group_posts(member)
check("a member is notified", len(got) == 1, got)
n = got[0] if got else {}
check("by name, in the group", n.get("title") == f"Demo Owner posted in Demo Group {slug}", n)
check("with an excerpt", "Hello group" in (n.get("body") or ""), n)
check("and a link to the post", n.get("link") == f"/p/{post['id']}", n)
check("not the author", group_posts(owner) == [])
check("not the member who turned it off", group_posts(quiet) == [])
check("not somebody outside the group", group_posts(waiting) == [])
named_items = c.get("/notifications", headers=H(named)).json()
named_items = named_items.get("items", named_items if isinstance(named_items, list) else [])
check("the member mentioned gets the mention only",
      [x["kind"] for x in named_items if x["kind"] in ("mention", "community_post")] == ["mention"], named_items)

print()
print("Turned back on; a public post")
c.put(f"/communities/{group['id']}/notifications", headers=H(quiet), json={"notify_posts": True})
c.post("/posts", headers=H(owner), json={"body": "Second", "visibility": "community", "community_id": group["id"]})
c.post("/posts", headers=H(owner), json={"body": "Public, not the group", "visibility": "public"})
time.sleep(2.5)
check("notified again once turned back on", len(group_posts(quiet)) == 1, group_posts(quiet))
check("a public post tells the group nothing", len(group_posts(member)) == 2, group_posts(member))

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
