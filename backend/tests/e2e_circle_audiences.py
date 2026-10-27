"""Circles, the 27/10 upgrade, end to end against the local stack.

Several circles per post, people added and left out (an exclusion wins), the
two audience modes when membership changes, existing posts kept as they were,
the recipient's mute / hide / opt-out (future and past) and its reversal, the
privacy of circle names, the author's preview and changes after publication
(with confirmation before anybody new can see), mentions that no longer reach
outside the audience, notifications withdrawn, archive / duplicate / bulk /
smart exclusions, connections-only posts, scheduled posts.

    python backend/tests/e2e_circle_audiences.py
"""
import random
import string
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=60)
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
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"], "handle": f"u{t}"}


def connect(a, b):
    c.post(f"/connections/{b['id']}", headers=a["h"], json={"message": None})
    c.post(f"/connections/{a['id']}/respond?accept=true", headers=b["h"])


def sees(user, post_id):
    return c.get(f"/posts/{post_id}", headers=user["h"]).status_code == 200


def feed(user, mode="circles"):
    return {p["id"] for p in c.get(f"/feed?mode={mode}&limit=100", headers=user["h"]).json().get("items", [])}


def notes(user):
    return c.get("/notifications", headers=user["h"]).json()["items"]


def post(user, body, **audience):
    r = c.post("/posts", headers=user["h"], json={"body": body, "visibility": "circle", **audience})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


owner, a, b, cc, outsider = (register(n) for n in ("Demo Owner", "Demo A", "Demo B", "Demo C", "Demo Outsider"))
for x in (a, b, cc):
    connect(owner, x)

print("== circles: create, describe, bulk, duplicate, archive ==")
friends = c.post("/circles", headers=owner["h"], json={"name": "Secret Friends Name", "description": "Old school friends", "icon": "heart"}).json()["id"]
work = c.post("/circles", headers=owner["h"], json={"name": "Secret Work Name"}).json()["id"]
r = c.post(f"/circles/{friends}/bulk-members", headers=owner["h"], json={"add": [a["id"], b["id"], "usr_doesnotexist"]})
check("people are added in bulk; anyone who cannot be reached is named", r.status_code == 200 and set(r.json()["added"]) == {a["id"], b["id"]} and r.json()["refused"] == ["usr_doesnotexist"], r.text)
c.post(f"/circles/{work}/bulk-members", headers=owner["h"], json={"add": [b["id"], cc["id"]]})
listing = {x["id"]: x for x in c.get("/circles", headers=owner["h"], params={"sort": "name"}).json()}
check("circles carry their description and icon", listing[friends]["description"] == "Old school friends" and listing[friends]["icon"] == "heart", listing[friends])
dup = c.post(f"/circles/{work}/duplicate", headers=owner["h"], json={"name": "Work copy"})
check("a circle can be duplicated by its owner", dup.status_code == 201 and dup.json()["members_count"] == 2, dup.text)
check("but not by anybody else", c.post(f"/circles/{work}/duplicate", headers=a["h"], json={"name": "x"}).status_code == 404)
found = c.get(f"/circles/{work}", headers=owner["h"], params={"q": "Demo C"}).json()["members"]
check("members can be searched", [m["user_id"] for m in found] == [cc["id"]], found)
c.patch(f"/circles/{dup.json()['id']}", headers=owner["h"], json={"archived": True})
r = c.post("/posts", headers=owner["h"], json={"body": "x", "visibility": "circle", "circle_ids": [dup.json()["id"]]})
check("an archived circle is not offered for new posts", r.status_code == 409, r.text)

print("== several circles, people added, people left out ==")
prev = c.post("/circles/audience-preview", headers=owner["h"], json={"circle_ids": [friends, work], "exclude_ids": [cc["id"]]}).json()
check("the preview counts each person once and applies the exclusion", prev["count"] == 2 and {p["user_id"] for p in prev["people"]} == {a["id"], b["id"]}, prev)
check("and lists who is left out", [p["user_id"] for p in prev["excluded"]] == [cc["id"]], prev["excluded"])
p1 = post(owner, "To friends and work, not C", circle_ids=[friends, work], exclude_ids=[cc["id"]])
check("members of either circle see it", sees(a, p1) and sees(b, p1))
check("an exclusion wins over a circle", not sees(cc, p1))
check("an outsider does not", not sees(outsider, p1))
ids_b = [i["id"] for i in c.get("/feed?mode=circles&limit=100", headers=b["h"]).json()["items"]]
check("it is in the circle feed once", ids_b.count(p1) == 1, ids_b)
pp = post(owner, "Just for the outsider by name", include_ids=[outsider["id"]])
check("a person added by name sees it (no circle)", sees(outsider, pp) and not sees(a, pp))
r = c.post(f"/posts/{p1}/repost", headers=b["h"], json={"body": ""})
check("a circle post cannot be reshared", r.status_code == 400, r.text)

print("== membership changes: follow the circles vs fixed at publication ==")
live = post(owner, "Live to friends", circle_ids=[friends])
fixed = post(owner, "Fixed to friends", circle_ids=[friends], audience_mode="snapshot")
c.post(f"/circles/{friends}/members/{cc['id']}", headers=owner["h"])
check("someone added later sees the post that follows the circle", sees(cc, live))
check("but not the one fixed at publication", not sees(cc, fixed))
c.delete(f"/circles/{friends}/members/{a['id']}", headers=owner["h"])
check("someone removed loses the post that follows the circle", not sees(a, live))
check("and keeps the fixed one, recorded at publication", sees(a, fixed))
c.post(f"/users/{owner['id']}/block", headers=a["h"])
check("a block closes even the fixed one", not sees(a, fixed))
c.delete(f"/users/{owner['id']}/block", headers=a["h"])
c.post(f"/circles/{friends}/members/{a['id']}", headers=owner["h"])
aud = c.get(f"/posts/{fixed}/audience", headers=owner["h"]).json()
check("the author sees who it reaches and the rule that applies", aud["audience_mode"] == "snapshot" and aud["eligible_count"] >= 2 and "Fixed" in aud["note"], aud)
check("and the circle names - only the author", [x["name"] for x in aud["circles"]] == ["Secret Friends Name"])
check("nobody else reads a post's audience", c.get(f"/posts/{fixed}/audience", headers=b["h"]).status_code == 404)

print("== existing posts keep their audience ==")
legacy = c.post("/posts", headers=owner["h"], json={"body": "Old client post", "visibility": "circle", "circle_id": work}).json()["id"]
check("a post with one circle_id still works, following the circle", sees(cc, legacy) and not sees(outsider, legacy))
check("every older circle post is 'follows the circle'", psql("select count(*) from social.posts where visibility='circle' and audience_mode <> 'live' and id not in (select post_id from social.post_people where role='snapshot')") == "0")
check("and has its circle recorded", psql("select count(*) from social.posts p where visibility='circle' and circle_id is not null and not exists (select 1 from social.post_circles pc where pc.post_id=p.id)") == "0")

print("== the recipient's choices ==")
ctl = c.get(f"/posts/{p1}/audience-controls", headers=b["h"]).json()
check("B can manage the two audiences the post came through", len(ctl["items"]) == 2, ctl)
check("labelled without the circles' names", all("Secret" not in i["label"] and "Private audience from Demo Owner" in i["label"] for i in ctl["items"]), ctl["items"])
check("an outsider gets nothing to manage", c.get(f"/posts/{p1}/audience-controls", headers=outsider["h"]).status_code == 404)
ctl_live = c.get(f"/posts/{live}/audience-controls", headers=b["h"]).json()["items"]
friends_token = ctl_live[0]["token"]
work_token = next(i["token"] for i in ctl["items"] if i["token"] != friends_token)
r = c.put("/audience-prefs", headers=b["h"], json={"post_id": p1, "token": work_token, "opt_out": True})
check("B opts out of one audience, for new posts", r.status_code == 200 and r.json()["opted_out"], r.text)
check("a post from before stays", sees(b, p1))
only_friends = post(owner, "Friends only after opt-out", circle_ids=[friends])
both = post(owner, "Both circles after opt-out", circle_ids=[friends, work])
check("a new post through another circle still reaches B", sees(b, only_friends))
check("a new post that also uses the circle B left does not", not sees(b, both))
check("nor in B's feed", both not in feed(b))
r = c.put("/audience-prefs", headers=b["h"], json={"post_id": only_friends, "token": friends_token, "opt_out": False})
pref_rows = c.get("/audience-prefs", headers=b["h"]).json()["items"]
work_pref = next(x for x in pref_rows if x["opted_out"])
r = c.patch(f"/audience-prefs/{work_pref['id']}", headers=b["h"], json={"opt_out": True, "opt_out_past": True})
check("opting out of earlier posts too closes them", not sees(b, p1), r.text)
r = c.patch(f"/audience-prefs/{work_pref['id']}", headers=b["h"], json={"opt_out": False})
check("and the opt-out can be reversed", sees(b, p1) and sees(b, both), r.text)
c.put("/audience-prefs", headers=a["h"], json={"post_id": live, "token": c.get(f"/posts/{live}/audience-controls", headers=a["h"]).json()["items"][0]["token"], "hidden": True, "muted": True})
check("hiding keeps it out of A's feed", live not in feed(a))
check("but a link still opens it", sees(a, live))
c.post("/notifications/read", headers=a["h"])
c.post("/notifications/read", headers=b["h"])
told = post(owner, "Tell everyone", circle_ids=[friends], notify_audience=True)
time.sleep(3)
b_notes = [n for n in notes(b) if n["link"] == f"/p/{told}"]
check("the audience is notified when the author asks", len(b_notes) == 1 and "Secret" not in b_notes[0]["title"], b_notes)
check("except whoever muted that audience", not [n for n in notes(a) if n["link"] == f"/p/{told}"])

print("== mentions, changes after publication, withdrawn notifications ==")
named = post(owner, f"Hey @{outsider['handle']} have a look", circle_ids=[friends])
time.sleep(1)
check("naming an outsider in a circle post does not tell them", not [n for n in notes(outsider) if n["link"] == f"/p/{named}"])
r = c.patch(f"/posts/{p1}/audience", headers=owner["h"], json={"visibility": "circle", "circle_ids": [friends, work]})
check("a change that lets somebody new see it asks first", r.status_code == 409 and r.json()["detail"]["added_count"] == 1, r.text)
r = c.patch(f"/posts/{p1}/audience", headers=owner["h"], json={"visibility": "circle", "circle_ids": [friends, work], "confirm_added": True})
check("confirmed, C can see it", r.status_code == 200 and sees(cc, p1), r.text)
r = c.patch(f"/posts/{told}/audience", headers=owner["h"], json={"visibility": "private"})
check("making it 'only me' narrows without asking", r.status_code == 200 and not sees(b, told), r.text)
check("and takes back the notification B had", not [n for n in notes(b) if n["link"] == f"/p/{told}"])
r = c.patch(f"/posts/{told}/audience", headers=owner["h"], json={"visibility": "public"})
check("making it public asks first", r.status_code == 409, r.text)

print("== smart circles, connections-only, deletion ==")
smart = c.post("/circles", headers=owner["h"], json={"name": "My connections", "kind": "smart", "rule": {"source": "connections"}}).json()
prev = c.post("/circles/smart-preview", headers=owner["h"], json={"source": "connections"}).json()
check("a smart rule can be previewed before saving", prev["count"] == 3, prev)
c.post(f"/circles/{smart['id']}/exclusions/{cc['id']}", headers=owner["h"])
sp = post(owner, "To my connections but C", circle_ids=[smart["id"]])
check("a smart circle leaves out whom its owner excluded", sees(a, sp) and not sees(cc, sp))
cp = c.post("/posts", headers=owner["h"], json={"body": "Connections only", "visibility": "connections"}).json()["id"]
check("a connections-only post reaches connections", sees(a, cp) and not sees(outsider, cp))
mine = c.get("/me/circle-posts", headers=owner["h"], params={"circle_id": friends}).json()
check("the owner lists what was shared with a circle", live in {x["id"] for x in mine["items"]} and mine["opted_out_count"] is None, mine)
c.delete(f"/circles/{friends}", headers=owner["h"])
check("deleting a circle narrows its posts, never widens them", not sees(a, live) and not sees(outsider, live))

print("== scheduled: the audience is fixed when it is published ==")
sched = c.post("/posts", headers=owner["h"], json={"body": "Scheduled fixed", "visibility": "circle", "circle_ids": [work], "audience_mode": "snapshot",
                                                     "publish_at": (datetime.now(timezone.utc) + timedelta(seconds=65)).isoformat()}).json()["id"]
c.post(f"/circles/{work}/members/{a['id']}", headers=owner["h"])
for _ in range(30):
    if psql(f"select status from social.posts where id='{sched}'") == "published":
        break
    time.sleep(5)
time.sleep(2)
check("someone added before publication is in the fixed audience", sees(a, sched))
check("and the time it was fixed is recorded", psql(f"select audience_fixed_at is not null from social.posts where id='{sched}'") == "t")

print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
