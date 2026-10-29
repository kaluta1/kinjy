"""People to follow: ranked by real followers, only members with a photo (28/10, 29/10).

    python backend/tests/e2e_people_to_follow.py

Local stack only: the follow rows and photos of the Demo members it creates are
written straight into the local database (thousands of registrations would be
needed otherwise), and removed at the end.
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
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:400]}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def sql(q):
    out = subprocess.run(
        ["docker", "exec", "kaluta-postgres", "sh", "-c", f'psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "{q}"'],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def register(name, born=date(1990, 2, 2)):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": born.isoformat(), "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


def photo(member):
    sql(f"update users.profiles set avatar_asset_id='med_test_{tag()}', avatar_url='https://cdn.example.com/{tag()}.jpg' "
        f"where user_id='{member['id']}'")


def followers(member, n):
    """n real members (existing profiles) following them."""
    sql(f"insert into users.follows (follower_id, followee_id, created_at) select user_id, '{member['id']}', now() "
        f"from users.profiles where user_id not like 'usr_ghost%' and user_id <> '{member['id']}' "
        f"order by user_id limit {n} on conflict do nothing")


def suggested(viewer, limit=20):
    r = c.get(f"/users/suggestions?limit={limit}", headers=viewer["h"])
    r.raise_for_status()
    return r.json()


viewer = register("Demo Viewer")
a, b, b2, e = (register(f"Demo {x}") for x in ("Alpha", "Bravo", "Bravo2", "Echo"))
nophoto, blank, minor = register("Demo NoPhoto"), register("Demo Blank"), register("Demo Teen", date(2011, 5, 5))
tests = [a, b, b2, e, nophoto, blank, minor]
ids = ",".join(f"'{m['id']}'" for m in tests)
try:
    for m in (a, b, b2, e, minor):
        photo(m)
    sql(f"update users.profiles set avatar_url='   ', avatar_asset_id=null where user_id='{blank['id']}'")
    followers(a, 40)
    followers(b, 30)
    followers(b2, 30)
    followers(e, 20)
    followers(nophoto, 90)
    followers(blank, 80)
    followers(minor, 70)
    # Stale counters both ways; and Echo's 60 more followers are members whose
    # profile row was never made (imported, not yet opened) - they still count.
    sql(f"update users.profiles set followers_count=100000 where user_id='{b['id']}'")
    sql(f"update users.profiles set followers_count=0 where user_id='{a['id']}'")
    sql("insert into users.follows (follower_id, followee_id, created_at) select 'usr_ghost' || g, "
        f"'{e['id']}', now() from generate_series(1, 60) g")
    # Echo is the oldest of them, so the default order (newest first) puts it last.
    sql(f"update users.profiles set created_at = now() - interval '400 days' where user_id='{e['id']}'")

    print("== ranking ==")
    got = suggested(viewer)
    items = got["items"]
    order = [i["user_id"] for i in items]
    mine = [i for i in order if i in {m["id"] for m in tests}]
    ties = [b2["id"], b["id"]]  # equal totals: the newer member first, as before
    check("most real followers first, whatever the stored counter says",
          mine == [e["id"], a["id"], *ties], [(i["display_name"], i["followers_count"]) for i in items[:8]])
    check("the totals shown are the follows that exist (80, 40, 30, 30)",
          [next(i["followers_count"] for i in items if i["user_id"] == x) for x in mine] == [80, 40, 30, 30])
    check("followers without a profile row yet count (the rule of the follow button)",
          next(i for i in items if i["user_id"] == e["id"])["followers_count"] == 80)
    check("the oldest member, last in the default order, is still found first", order[0] == e["id"] or mine[0] == e["id"])
    check("the whole list is in descending order", all(x["followers_count"] >= y["followers_count"] for x, y in zip(items, items[1:])))
    check("the reason shown is unchanged", got["reason"] == "most_followed")

    print("== photos ==")
    check("no photo: left out, even with the most followers", nophoto["id"] not in order)
    check("a blank photo field: left out", blank["id"] not in order)
    check("everybody listed has an uploaded photo", all(i["avatar_url"] and i["avatar_url"].strip() for i in items))
    without = sql("select count(*) from users.profiles where user_id in (" + ",".join(f"'{x}'" for x in order) + ") and avatar_asset_id is null")
    check("...from the upload logic, not a placeholder", without == "0", without)

    print("== limits and ties ==")
    check("limit 1: the top member, not the first of an arbitrary set", [i["user_id"] for i in suggested(viewer, 1)["items"]] == [e["id"]])
    two = [i["user_id"] for i in suggested(viewer, 2)["items"]]
    check("a member with two followers cannot take a slot from those with more",
          all(next(i["followers_count"] for i in items if i["user_id"] == x) >= 40 for x in two), two)
    three = [i["user_id"] for i in suggested(viewer, 3)["items"]]
    check("limit 3 is the head of the full list", three == order[:3], (three, order[:3]))
    runs = {tuple(i["user_id"] for i in suggested(viewer)["items"]) for _ in range(4)}
    check("equal totals come back in the same order every time", len(runs) == 1 and order.index(ties[0]) < order.index(ties[1]))
    check("fewer than asked when fewer qualify (no filler)", len(items) <= 20 and all(i["avatar_url"] for i in items))

    print("== discoverable off ==")
    hidden, friend, fan = (register(f"Demo {x}") for x in ("Hidden", "Friend", "Fan"))
    tests += [hidden, friend, fan]
    ids = ",".join(f"'{m['id']}'" for m in tests)
    for m in (hidden, friend, fan):
        photo(m)
        followers(m, 25)
    sql(f"delete from users.preferences where user_id='{hidden['id']}'")
    check("no preferences row at all: still suggested", hidden["id"] in [i["user_id"] for i in suggested(viewer)["items"]])
    for m in (hidden, friend, fan):
        r = c.patch("/preferences", headers=m["h"], json={"discoverable": False})
        check(f"{m['id']} switches discoverable off", r.status_code == 200, r.text)
    stranger = register("Demo Stranger")
    tests.append(stranger)
    ids = ",".join(f"'{m['id']}'" for m in tests)
    seen = [i["user_id"] for i in suggested(stranger)["items"]]
    check("a stranger is no longer offered them", not {hidden["id"], friend["id"], fan["id"]} & set(seen), seen)
    check("discoverable members are still offered", b2["id"] in seen, seen)
    c.post(f"/connections/{friend['id']}", headers=stranger["h"], json={})
    r = c.post(f"/connections/{stranger['id']}/respond?accept=true", headers=friend["h"])
    check("connection accepted", r.status_code in (200, 201), r.text)
    r = c.post(f"/users/{stranger['id']}/follow", headers=fan["h"])
    check("Fan follows the viewer", r.status_code == 201, r.text)
    seen = [i["user_id"] for i in suggested(stranger)["items"]]
    check("a connection is still offered", friend["id"] in seen, seen)
    check("somebody who follows you is still offered", fan["id"] in seen, seen)
    check("the hidden stranger stays out", hidden["id"] not in seen, seen)

    print("== existing rules ==")
    check("an adult does not see a minor, however followed", minor["id"] not in order)
    # limit 1 reads three at a time: four teenagers above Alpha fill the whole
    # first batch, and Alpha has to come from the next one.
    teens = [register(f"Demo Teen{n}", date(2011, 5, 5)) for n in range(4)]
    tests += teens
    ids = ",".join(f"'{m['id']}'" for m in tests)
    for t in teens:
        photo(t)
        followers(t, 50)
    for t in teens:
        followers(t, 90)  # above Echo now: the whole first batch is teenagers
    check("a page trimmed empty reads on: the most-followed adult still comes back",
          [i["user_id"] for i in suggested(viewer, 1)["items"]] == [e["id"]])
    teen_view = [i["user_id"] for i in suggested(minor)["items"]]
    check("a minor still sees everybody eligible", a["id"] in teen_view and minor["id"] not in teen_view)
    r = c.post(f"/users/{a['id']}/follow", headers=viewer["h"])
    check("Follow still works", r.status_code == 201, r.text)
    check("somebody followed leaves the list", a["id"] not in [i["user_id"] for i in suggested(viewer)["items"]])
    r = c.post(f"/users/{b['id']}/block", headers=viewer["h"])
    check("somebody blocked leaves the list", r.status_code in (200, 201) and b["id"] not in [i["user_id"] for i in suggested(viewer)["items"]])
    c.post(f"/users/{viewer['id']}/block", headers=e["h"])
    check("somebody who blocked you leaves the list", e["id"] not in [i["user_id"] for i in suggested(viewer)["items"]])

    print("== the profile's count agrees ==")
    fan2 = register("Demo Follower")
    tests.append(fan2)
    ids = ",".join(f"'{m['id']}'" for m in tests)
    c.post(f"/users/{b2['id']}/follow", headers=fan2["h"])
    before = sql(f"select followers_count from users.profiles where user_id='{b2['id']}'")
    c.post(f"/users/{fan2['id']}/block", headers=b2["h"])
    after = sql(f"select followers_count from users.profiles where user_id='{b2['id']}'")
    check("a block that severs a follow now lowers the stored count too", int(after) == int(before) - 1, (before, after))
    import json as _json
    dry = _json.loads(subprocess.run(["docker", "exec", "-w", "/app", "kaluta-user", "python", "recount_follow_counters.py"],
                                     capture_output=True, text=True, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"}).stdout)
    drifted = {d["user_id"] for d in dry["drifted"]}
    check("the recount's dry run finds the stale counts, and changes nothing",
          {a["id"], b["id"]} <= drifted and sql(f"select followers_count from users.profiles where user_id='{b['id']}'") == "100000", dry["count"])
    subprocess.run(["docker", "exec", "-w", "/app", "kaluta-user", "python", "recount_follow_counters.py", "--apply"],
                   capture_output=True, text=True, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"})
    rail = {i["user_id"]: i["followers_count"] for i in suggested(stranger)["items"]}

    check("after it, the profile and the rail give the same number",
          all(int(sql(f"select followers_count from users.profiles where user_id='{x}'")) == n for x, n in rail.items()), rail)
    again = _json.loads(subprocess.run(["docker", "exec", "-w", "/app", "kaluta-user", "python", "recount_follow_counters.py"],
                                       capture_output=True, text=True, env={**__import__("os").environ, "MSYS_NO_PATHCONV": "1"}).stdout)
    check("a second run finds nothing", again["count"] == 0, again["count"])
    r = c.post(f"/connections/{b2['id']}", headers=viewer["h"], json={})
    check("Connect still works", r.status_code in (200, 201), r.text)
    row = next((i for i in suggested(viewer)["items"] if i["user_id"] == b2["id"]), None)
    check("and the rail knows the request was sent", row is not None and row["connection"] == "invited", row)
finally:
    sql(f"delete from users.follows where followee_id in ({ids}) or follower_id in ({ids}) or follower_id like 'usr_ghost%'")
    sql(f"delete from users.blocks where user_id in ({ids}) or blocked_id in ({ids}) or user_id='{viewer['id']}'")
    sql(f"delete from users.connections where requester_id in ({ids},'{viewer['id']}') or addressee_id in ({ids})")

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
