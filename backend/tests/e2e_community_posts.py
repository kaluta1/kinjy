"""A community behaves like a group: you join it, you post in it, members read it.

What was wrong before: `community_id` on a post was a free text field nobody
checked, the audience rule excluded community posts from every feed, and no
endpoint listed them. So a post written into a group was accepted, hidden from
everyone, and unreachable - and anybody could address a post to a private group
they had been refused.

The checks below are the group, stated: a non-member cannot post, a member can,
members see each other's posts in their own feed and on the community, and a
private community does not leak to outsiders.

Run against the local stack:  python backend/tests/e2e_community_posts.py
"""
from __future__ import annotations

import random
import string
import sys
import time
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=30)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    ok = ok and bool(cond)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def register(prefix):
    t = "".join(random.choices(string.ascii_lowercase, k=8))
    today = date.today()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"{prefix}{t}", "display_name": f"{prefix} {t}",
        "password": "Sup3rStrong!Pass",
        "date_of_birth": date(today.year - 30, today.month, min(today.day, 28)).isoformat(),
        "country": "US"})
    r.raise_for_status()
    return r.json()["tokens"]["access_token"]


owner = register("own")
member = register("mem")
outsider = register("out")

print("== a public community")
r = c.post("/communities", headers=auth(owner),
           json={"name": f"Hikers {random.randint(1000, 9999)}", "kind": "public"})
check("the community is created", r.status_code == 201, (r.status_code, r.text[:160]))
community = r.json()["id"]

print("\n== posting without joining")
r = c.post("/posts", headers=auth(outsider),
           json={"body": "I did not join this", "visibility": "community",
                 "community_id": community})
check("a non-member cannot post into it", r.status_code == 403, (r.status_code, r.text[:160]))

r = c.post("/posts", headers=auth(outsider),
           json={"body": "nor into one that does not exist", "visibility": "community",
                 "community_id": "cmy_does_not_exist"})
check("nor into a community that does not exist", r.status_code in (403, 404),
      (r.status_code, r.text[:160]))

print("\n== joining, then posting")
r = c.post(f"/communities/{community}/join", headers=auth(member))
check("a member can join a public community", r.status_code == 200 and r.json().get("joined"),
      (r.status_code, r.text[:160]))

r = c.post("/posts", headers=auth(member),
           json={"body": "First walk of the season, who is coming?",
                 "visibility": "community", "community_id": community})
check("a member can post into it", r.status_code == 201, (r.status_code, r.text[:200]))
post_id = r.json().get("id", "") if r.status_code == 201 else ""
time.sleep(0.5)

r = c.post("/posts", headers=auth(owner),
           json={"body": "Trail notes from the ridge", "visibility": "community",
                 "community_id": community})
check("the owner can post into their own community", r.status_code == 201,
      (r.status_code, r.text[:200]))
owner_post = r.json().get("id", "") if r.status_code == 201 else ""
time.sleep(0.5)

print("\n== reading the community")
r = c.get(f"/feed/community/{community}", headers=auth(owner))
check("the owner sees the post on the community", r.status_code == 200 and any(
    i["id"] == post_id for i in r.json().get("items", [])), (r.status_code, r.text[:200]))
check("the community is named in the response",
      r.status_code == 200 and r.json().get("community", {}).get("id") == community,
      r.text[:160])

r = c.get(f"/feed/community/{community}")
check("a public community can be read by a visitor", r.status_code == 200,
      (r.status_code, r.text[:160]))

print("\n== and in the member's own feed")
r = c.get("/feed?mode=new", headers=auth(member))
check("the author sees it in their feed", r.status_code == 200 and any(
    i["id"] == post_id for i in r.json().get("items", [])), (r.status_code, r.text[:200]))

r = c.get("/feed?mode=new", headers=auth(owner))
check("another member sees it in their feed", r.status_code == 200 and any(
    i["id"] == post_id for i in r.json().get("items", [])), (r.status_code, r.text[:200]))

r = c.get("/feed?mode=new", headers=auth(member))
check("and each sees the other's, not just their own", r.status_code == 200 and any(
    i["id"] == owner_post for i in r.json().get("items", [])), (r.status_code, r.text[:200]))

r = c.get("/feed?mode=new", headers=auth(outsider))
check("somebody who never joined does not", r.status_code == 200 and not any(
    i["id"] == post_id for i in r.json().get("items", [])), (r.status_code, r.text[:200]))

# An open community publishes publicly by default, so a link to one of its
# posts opens for somebody who never joined - the same answer its page has
# always given a visitor. This file used to assert the opposite two lines
# after asserting the page was readable, which is the inconsistency the
# setting exists to end. What is *not* open is taking part.
r = c.get(f"/posts/{post_id}", headers=auth(outsider))
check("and a link to the post opens, as its page already did",
      r.status_code == 200, (r.status_code, r.text[:160]))
r = c.post(f"/posts/{post_id}/like", headers=auth(outsider))
check("but somebody who never joined cannot like it",
      r.status_code == 403, (r.status_code, r.text[:160]))
r = c.post(f"/posts/{post_id}/comments", headers=auth(outsider), json={"body": "hello"})
check("nor comment on it", r.status_code == 403, (r.status_code, r.text[:160]))

# Closed, the link closes with the page. Both halves of the switch are
# covered in full by tests/e2e_open_community.py.
r = c.patch(f"/communities/{community}/public-content", headers=auth(owner),
            json={"public_content": False})
check("the owner can close the content", r.status_code == 200, (r.status_code, r.text[:160]))
r = c.get(f"/posts/{post_id}", headers=auth(outsider))
check("and then the link answers 404 again", r.status_code == 404, (r.status_code, r.text[:160]))
c.patch(f"/communities/{community}/public-content", headers=auth(owner),
        json={"public_content": True})

print("\n== leaving takes the posts away again")
r = c.post(f"/communities/{community}/leave", headers=auth(member))
check("a member can leave", r.status_code == 200 and r.json().get("left"),
      (r.status_code, r.text[:160]))

r = c.get("/feed?mode=new", headers=auth(member))
# Their own post stays visible to them - an author reads what they wrote, and
# leaving a room does not unwrite it. What goes is everybody else's.
check("and stops seeing the other members' posts",
      r.status_code == 200 and not any(i["id"] == owner_post for i in r.json().get("items", [])),
      (r.status_code, r.text[:200]))
check("while still seeing what they wrote themselves",
      r.status_code == 200 and any(i["id"] == post_id for i in r.json().get("items", [])),
      (r.status_code, r.text[:200]))

r = c.post("/posts", headers=auth(member),
           json={"body": "still here?", "visibility": "community", "community_id": community})
check("and cannot post into it any more", r.status_code == 403, (r.status_code, r.text[:160]))

r = c.post(f"/communities/{community}/leave", headers=auth(owner))
check("the owner cannot leave their own community", r.status_code == 403,
      (r.status_code, r.text[:160]))

print("\n== a private community keeps itself to itself")
r = c.post("/communities", headers=auth(owner),
           json={"name": f"Quiet {random.randint(1000, 9999)}", "kind": "private"})
private = r.json()["id"] if r.status_code == 201 else ""
r = c.post("/posts", headers=auth(owner),
           json={"body": "members only", "visibility": "community", "community_id": private})
check("the owner can post in their private community", r.status_code == 201,
      (r.status_code, r.text[:160]))
private_post = r.json().get("id", "") if r.status_code == 201 else ""
time.sleep(0.4)

r = c.post(f"/communities/{private}/join", headers=auth(outsider))
check("joining a private community only asks",
      r.status_code == 200 and r.json().get("status") == "pending", (r.status_code, r.text[:160]))

r = c.post("/posts", headers=auth(outsider),
           json={"body": "let me in", "visibility": "community", "community_id": private})
check("a pending request does not let them post", r.status_code == 403, (r.status_code, r.text[:160]))

r = c.get(f"/feed/community/{private}", headers=auth(outsider))
check("nor read the community", r.status_code == 404, (r.status_code, r.text[:160]))

r = c.get("/feed?mode=new", headers=auth(outsider))
check("nor see its posts in their feed", r.status_code == 200 and not any(
    i["id"] == private_post for i in r.json().get("items", [])), r.text[:200])

print("\n== a public post is not quietly a community post")
r = c.post("/posts", headers=auth(owner),
           json={"body": "hello world", "visibility": "public", "community_id": community})
check("a public post drops the community pointer", r.status_code == 201 and
      r.json().get("community_id") in (None, ""), (r.status_code, r.text[:200]))

print("\nALL CHECKS PASSED" if ok else "\nTHERE ARE FAILURES")
sys.exit(0 if ok else 1)
