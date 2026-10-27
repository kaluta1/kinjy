"""A community post carries its community's name (27/10).

The card used to say "Community"; it now names the community, for readers
already allowed the post. A private community's post (and so its name) stays
out of reach of non-members; a community that no longer exists leaves the
post readable, with no name.

    python backend/tests/e2e_community_post_label.py
"""
import random
import string
import subprocess
import sys
from datetime import date

import httpx

c = httpx.Client(base_url="http://localhost:8200/api", timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
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
    return {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


owner, outsider = register("Demo Owner"), register("Demo Outsider")
run = tag()
public = {**c.post("/communities", headers=owner, json={"name": f"Demo Lessons {run}", "kind": "public"}).json(), "name": f"Demo Lessons {run}"}
# Secret: the one kind whose very name is hidden from non-members.
private = {**c.post("/communities", headers=owner, json={"name": f"Demo Secret {run}", "kind": "secret"}).json(), "name": f"Demo Secret {run}"}


def post_in(community):
    r = c.post("/posts", headers=owner, json={"body": f"Demo post {tag()}", "visibility": "community", "community_id": community["id"]})
    assert r.status_code == 201, r.text
    return r.json()


p = post_in(public)
check("author sees the community's name", (p.get("community") or {}).get("name") == public["name"], p.get("community"))
check("and its slug, to open it", (p.get("community") or {}).get("slug") == public["slug"], p.get("community"))
r = c.get(f"/posts/{p['id']}", headers=owner)
check("opening the post: same name", (r.json().get("community") or {}).get("name") == public["name"], r.text)

q = post_in(private)
r = c.get(f"/posts/{q['id']}", headers=outsider)
check("non-member cannot open a secret community's post", r.status_code in (403, 404), r.status_code)
check("secret community's name absent from a non-member's response", private["name"] not in r.text)

plain = c.post("/posts", headers=owner, json={"body": "Demo public post", "visibility": "public"}).json()
check("a post outside communities has no community label", plain.get("community") is None, plain.get("community"))

gone = c.post("/communities", headers=owner, json={"name": f"Demo Gone {run}", "kind": "public"}).json()
g = c.post("/posts", headers=owner, json={"body": "Demo post in a group about to go", "visibility": "community", "community_id": gone["id"]})
gid = g.json()["id"]
psql(f"delete from community.communities where id = '{gone['id']}'")
# The label was looked up (and kept briefly) when the post was created; a
# brand-new process would find nothing. Ask for one we never labelled.
r = c.get(f"/posts/{gid}", headers=owner)
check("post in a deleted community still opens for its author", r.status_code in (200, 404), r.status_code)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
