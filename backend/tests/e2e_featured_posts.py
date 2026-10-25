"""A Kinjy admin pins a public post above everything, in everybody's feed."""
import random
import string
import subprocess
import sys
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


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{t}@example.com"
    r = c.post("/auth/register", json={"email": email, "handle": f"u{t}", "display_name": name,
                                       "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(),
                                       "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"], "email": email}


def first_ids(user, mode, offset=0):
    r = c.get("/feed", headers=user["h"], params={"mode": mode, "limit": 10, "offset": offset})
    return [i["id"] for i in r.json().get("items", [])], r.json()


# Anything pinned for everyone by an earlier run is cleared first.
psql("update social.posts set featured_at=null, featured_by=null where featured_at is not null")
admin = register("Demo Admin")
psql(f"update auth.users set role='admin' where id='{admin['id']}'")
login = c.post("/auth/login", json={"email": admin["email"], "password": "Sup3rStrong!Pass"}).json()
admin["h"] = {"Authorization": f"Bearer {login['tokens']['access_token'] if 'tokens' in login else login['access_token']}"}
author = register("Demo Author")
reader = register("Demo Reader")

old = c.post("/posts", headers=author["h"], json={"body": "An older announcement worth pinning", "visibility": "public"}).json()
for n in range(3):
    c.post("/posts", headers=author["h"], json={"body": f"Newer everyday post {n}", "visibility": "public"})
private = c.post("/posts", headers=author["h"], json={"body": "For followers only", "visibility": "followers"}).json()

print("Who can pin")
check("a member cannot", c.post(f"/posts/{old['id']}/feature", headers=reader["h"]).status_code in (401, 403))
check("a followers-only post cannot be pinned for everyone",
      c.post(f"/posts/{private['id']}/feature", headers=admin["h"]).status_code == 409)
r = c.post(f"/posts/{old['id']}/feature", headers=admin["h"])
check("an admin pins a public post", r.status_code == 200 and r.json()["featured"] is True, r.text[:200])

print()
print("Above everything")
for mode in ("new", "for_you", "trending"):
    ids, page = first_ids(reader, mode)
    check(f"first in the {mode} feed", ids[:1] == [old["id"]], ids[:3])
    check(f"marked as pinned ({mode})", page["items"][0].get("featured") is True)
    check(f"and only once ({mode})", ids.count(old["id"]) == 1)
ids, page = first_ids(reader, "following")
check("even in Following, for somebody who follows nobody", ids[:1] == [old["id"]], page)
ids2, _ = first_ids(reader, "new", offset=10)
check("not repeated on the next pages", old["id"] not in ids2)

print()
print("Limits and unpinning")
extra = [c.post("/posts", headers=author["h"], json={"body": f"Pin candidate {n}", "visibility": "public"}).json()
         for n in range(3)]
codes = [c.post(f"/posts/{p['id']}/feature", headers=admin["h"]).status_code for p in extra]
check("three at most", codes == [200, 200, 409], codes)
c.delete(f"/posts/{old['id']}/feature", headers=admin["h"])
ids, _ = first_ids(reader, "new")
check("unpinned: no longer first", ids[:1] != [old["id"]], ids[:3])
psql("update social.posts set featured_at=null, featured_by=null where featured_at is not null")

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
