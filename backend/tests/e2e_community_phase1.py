"""Communities, phase 1: templates, spaces, publishing, moderation, invitations.

Against the local stack. Covers:
 1. templates set defaults (a family community starts secret); existing communities read as before;
 2. spaces: owner-only switches, unavailable spaces refused, posting refused in an off space,
    announcements for stewards only, directory off hides the member list;
 3. post approval: pending posts invisible to others, queue for stewards, approve / reject, audit with reasons;
 4. scheduled posts: hidden until due, published once (atomic claim);
 5. pins, bookmarks (lost with access), edit history, accepted answers, top / latest views;
 6. cross-community isolation: a steward of one community has no power in another;
 7. community inviter kept apart from the account sponsor; link analytics (opens, registrations, joins);
 8. welcome message sent once per membership; invitation rate limit.
"""
import random
import string
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8200/api"
AUTH = "http://localhost:8201"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True
RUN = "".join(random.choices(string.ascii_lowercase, k=6))


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def register(name, **extra):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG", **extra}
    r = httpx.post(f"{AUTH}/auth/register", json=body, timeout=120)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "code": d["user"]["referral_code"],
            "invited_by": d["user"].get("invited_by")}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def community(owner, name, **extra):
    r = c.post("/communities", headers=H(owner), json={"name": name, **extra})
    r.raise_for_status()
    return r.json()


def detail(u, cid):
    return c.get(f"/communities/{cid}", headers=H(u) if u else None).json()


def post(u, cid, body, **extra):
    return c.post("/posts", headers=H(u), json={"body": body, "visibility": "community", "community_id": cid, **extra})


def feed(u, cid, **params):
    return c.get(f"/feed/community/{cid}", headers=H(u) if u else None, params=params)


def join(u, cid, **params):
    return c.post(f"/communities/{cid}/join", headers=H(u), params=params)


owner, mod, member, outsider = register("Demo Owner"), register("Demo Mod"), register("Demo Member"), register("Demo Out")

print("1. Templates")
tpl = c.get("/community-templates").json()
check("seven templates and every space listed", len(tpl["templates"]) == 7 and len(tpl["spaces"]) == 11, tpl)
fam = community(owner, f"Demo Family {RUN}", template="family")
check("a family community starts secret even though the form said public", fam["kind"] == "secret", fam)
open_c = community(owner, f"Demo Open {RUN}")
d = detail(owner, open_c["id"])
check("no template: an interest group, public, its four spaces on",
      d["template"] == "interest" and d["kind"] == "public"
      and {s["id"] for s in d["spaces"] if s["enabled"]} == {"announcements", "discussions", "articles", "directory"}, d)
legacy = psql(f"select count(*) from community.community_spaces where community_id='{open_c['id']}'")
check("spaces are written down at creation", legacy == "11", legacy)
old_id = psql("select c.id from community.communities c where not exists (select 1 from community.community_spaces s "
              "where s.community_id=c.id) and c.kind='public' limit 1")
if old_id:
    od = c.get(f"/communities/{old_id}").json()
    check("a community from before spaces reads as before: public, posts and member list on",
          od["kind"] == "public" and od["directory_open"] and od["template"] == "interest", od)
else:
    print("  SKIP no community from before spaces in this database")

for u in (mod, member):
    join(u, open_c["id"]).raise_for_status()
c.post(f"/communities/{open_c['id']}/members/{mod['id']}", headers=H(owner), json={"action": "promote"}).raise_for_status()

print()
print("2. Spaces")
r = c.patch(f"/communities/{open_c['id']}/spaces", headers=H(member), json={"spaces": {"articles": False}})
check("a member cannot switch spaces", r.status_code == 403, r.status_code)
r = c.patch(f"/communities/{open_c['id']}/spaces", headers=H(owner), json={"spaces": {"courses": True}})
check("a space not built yet cannot be switched on", r.status_code == 409, r.text)
r = c.patch(f"/communities/{open_c['id']}/spaces", headers=H(owner), json={"spaces": {"articles": False, "directory": False}})
check("the owner switches articles and the directory off", r.status_code == 200
      and not next(s for s in r.json()["spaces"] if s["id"] == "articles")["enabled"], r.text[:200])
r = post(member, open_c["id"], "<h2>Demo</h2><p>An article</p>", format="article")
check("an article cannot be posted while articles are off", r.status_code == 403, r.text)
r = post(member, open_c["id"], "Demo news", space="announcements")
check("a member cannot post an announcement", r.status_code == 403, r.text)
r = post(mod, open_c["id"], "Demo announcement from a moderator", space="announcements")
check("a moderator can", r.status_code == 201 and r.json()["community_space"] == "announcements", r.text[:200])
check("with the directory off, a member cannot list members", c.get(f"/communities/{open_c['id']}/roster", headers=H(member)).status_code == 403)
check("a steward still can", c.get(f"/communities/{open_c['id']}/roster", headers=H(mod)).status_code == 200)
c.patch(f"/communities/{open_c['id']}/spaces", headers=H(owner), json={"spaces": {"articles": True, "directory": True}})
r = c.patch(f"/communities/{open_c['id']}/settings", headers=H(owner), json={"template": "business"})
d = detail(owner, open_c["id"])
check("changing the template changes no space and no access", d["template"] == "business" and d["kind"] == "public"
      and {s["id"] for s in d["spaces"] if s["enabled"]} == {"announcements", "discussions", "articles", "directory"}, d)

print()
print("3. Post approval and the audit log")
c.patch(f"/communities/{open_c['id']}/settings", headers=H(owner),
        json={"post_approval": "all", "welcome_message": "Demo welcome", "rules": "Be kind"}).raise_for_status()
r = post(member, open_c["id"], f"Demo waiting post {RUN}")
waiting = r.json()
check("a member's post waits for approval", r.status_code == 201 and waiting["status"] == "pending", r.text[:200])
check("it is not in the feed", all(p["id"] != waiting["id"] for p in feed(owner, open_c["id"]).json()["items"]))
check("others cannot open it", c.get(f"/posts/{waiting['id']}", headers=H(mod)).status_code == 404)
check("its author sees it in their queue", any(p["id"] == waiting["id"] for p in feed(member, open_c["id"]).json()["my_queued"]))
q = c.get(f"/feed/community/{open_c['id']}/pending", headers=H(mod))
check("stewards see the queue", q.status_code == 200 and any(p["id"] == waiting["id"] for p in q.json()["items"]))
check("members do not", c.get(f"/feed/community/{open_c['id']}/pending", headers=H(member)).status_code == 403)
r = c.post(f"/posts/{waiting['id']}/moderate", headers=H(mod), json={"action": "approve", "reason": "fine"})
check("a moderator approves it", r.status_code == 200 and r.json()["status"] == "published", r.text[:200])
check("approving twice is refused", c.post(f"/posts/{waiting['id']}/moderate", headers=H(mod),
                                           json={"action": "approve"}).status_code == 409)
check("now it is in the feed", any(p["id"] == waiting["id"] for p in feed(member, open_c["id"]).json()["items"]))
r2 = post(member, open_c["id"], f"Demo rejected post {RUN}").json()
r = c.post(f"/posts/{r2['id']}/moderate", headers=H(owner), json={"action": "reject", "reason": "off topic"})
check("a rejected post is removed", r.status_code == 200 and r.json()["status"] == "removed")
own = post(owner, open_c["id"], "Demo owner post").json()
check("stewards' own posts do not wait", own["status"] == "published")
audit = c.get(f"/communities/{open_c['id']}/audit", headers=H(owner)).json()
actions = [a["action"] for a in audit["items"]]
check("the audit log has approvals, rejections, spaces, settings and the promotion",
      {"post_approve", "post_reject", "spaces", "settings", "promote"} <= set(actions), actions)
check("with their reasons", any(a["action"] == "post_reject" and a["reason"] == "off topic" for a in audit["items"]))
check("members cannot read the audit log", c.get(f"/communities/{open_c['id']}/audit", headers=H(member)).status_code == 403)
c.patch(f"/communities/{open_c['id']}/settings", headers=H(owner), json={"post_approval": "none"})

print()
print("4. Scheduled posts")
when = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
r = post(member, open_c["id"], f"Demo scheduled {RUN}", publish_at=when)
sched = r.json()
check("a scheduled post is held", r.status_code == 201 and sched["status"] == "scheduled", r.text[:200])
check("not in the feed and not readable by others", all(p["id"] != sched["id"] for p in feed(owner, open_c["id"]).json()["items"])
      and c.get(f"/posts/{sched['id']}", headers=H(owner)).status_code == 404)
check("a time in the past is refused", post(member, open_c["id"], "x", publish_at="2020-01-01T00:00:00Z").status_code == 400)
psql(f"update social.posts set publish_at = now() - interval '1 second' where id='{sched['id']}'")
run = "import asyncio, main; print(asyncio.run(main.publish_due_posts()))"
first = subprocess.run(["docker", "compose", "exec", "-T", "social-service", "python", "-c", run], capture_output=True, text=True).stdout.strip()
second = subprocess.run(["docker", "compose", "exec", "-T", "social-service", "python", "-c", run], capture_output=True, text=True).stdout.strip()
status = psql(f"select status from social.posts where id='{sched['id']}'")
check("due: published once (a second run publishes nothing)", status == "published" and second.endswith("0"), (first, second, status))
check("now it is in the feed", any(p["id"] == sched["id"] for p in feed(owner, open_c["id"]).json()["items"]))

print()
print("5. Pins, bookmarks, history, answers, views")
p1 = post(member, open_c["id"], f"Demo question {RUN}?").json()
check("a member cannot pin", c.post(f"/posts/{p1['id']}/pin", headers=H(member)).status_code == 403)
check("a moderator pins", c.post(f"/posts/{p1['id']}/pin", headers=H(mod)).status_code == 200)
items = feed(member, open_c["id"]).json()["items"]
check("the pinned post comes first", items and items[0]["id"] == p1["id"] and items[0]["pinned"], [i["id"] for i in items[:3]])
check("the top view answers", feed(member, open_c["id"], sort="top").status_code == 200)
check("a space filter answers", all(i["community_space"] == "announcements"
                                    for i in feed(member, open_c["id"], space="announcements").json()["items"]))
c.post(f"/posts/{p1['id']}/bookmark", headers=H(member)).raise_for_status()
c.post(f"/posts/{p1['id']}/bookmark", headers=H(member)).raise_for_status()
saved = c.get("/bookmarks", headers=H(member)).json()["items"]
check("bookmarked once, listed", [s["id"] for s in saved].count(p1["id"]) == 1 and saved[0]["bookmarked"], saved[:1])
r = c.patch(f"/posts/{p1['id']}", headers=H(member), json={"body": f"Demo question {RUN}? (edited)", "visibility": "community",
                                                            "community_id": open_c["id"]})
revs = c.get(f"/posts/{p1['id']}/revisions", headers=H(owner)).json()["items"]
check("an edit keeps the previous text", r.status_code == 200 and revs and revs[0]["body"] == f"Demo question {RUN}?", revs)
cm = c.post(f"/posts/{p1['id']}/comments", headers=H(mod), json={"body": "Demo answer"}).json()
check("someone else cannot mark the answer", c.post(f"/posts/{p1['id']}/accept-answer", headers=H(outsider),
                                                    json={"comment_id": cm["id"]}).status_code in (403, 404))
r = c.post(f"/posts/{p1['id']}/accept-answer", headers=H(member), json={"comment_id": cm["id"]})
check("the asker marks the answer", r.status_code == 200 and r.json()["accepted_comment_id"] == cm["id"], r.text)
check("it shows on the post", c.get(f"/posts/{p1['id']}", headers=H(owner)).json()["accepted_comment_id"] == cm["id"])
priv = community(owner, f"Demo Private {RUN}", kind="private")
c.post(f"/communities/{priv['id']}/join", headers=H(member))
c.post(f"/communities/{priv['id']}/members/{member['id']}", headers=H(owner), json={"action": "approve"})
# Written by the owner: an author can always read their own post.
pp = post(owner, priv["id"], "Demo private words").json()
c.post(f"/posts/{pp['id']}/bookmark", headers=H(member)).raise_for_status()
c.post(f"/communities/{priv['id']}/leave", headers=H(member))
check("a bookmark of a private post disappears with access",
      all(s["id"] != pp["id"] for s in c.get("/bookmarks", headers=H(member)).json()["items"]))

print()
print("6. One community's stewards have no power in another")
other = community(outsider, f"Demo Other {RUN}")
op = post(outsider, other["id"], "Demo other post").json()
check("cannot pin there", c.post(f"/posts/{op['id']}/pin", headers=H(mod)).status_code in (403, 404))
check("cannot remove there", c.post(f"/posts/{op['id']}/moderate", headers=H(owner),
                                    json={"action": "remove"}).status_code in (403, 404))
check("cannot read its queue", c.get(f"/feed/community/{other['id']}/pending", headers=H(owner)).status_code in (403, 404))
check("cannot read its audit", c.get(f"/communities/{other['id']}/audit", headers=H(owner)).status_code in (403, 404))
check("cannot change its spaces", c.patch(f"/communities/{other['id']}/spaces", headers=H(owner),
                                          json={"spaces": {"articles": False}}).status_code in (403, 404))
check("a private community's feed is closed to strangers", feed(outsider, priv["id"]).status_code == 404)
r = c.post(f"/posts/{op['id']}/moderate", headers=H(outsider), json={"action": "remove", "reason": "demo"})
check("its own owner can remove a post", r.status_code == 200 and r.json()["status"] == "removed")

print()
print("7. Invitations: community inviter apart from the account sponsor")
link = c.post("/auth/share-links", headers=H(member),
              json={"destination": f"/communities/{open_c['slug']}", "include_invitation": True}).json()
httpx.get(f"{BASE}/auth/s/{link['token']}", headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False)
httpx.get(f"{BASE}/auth/s/{link['token']}", headers={"User-Agent": "facebookexternalhit/1.1"}, follow_redirects=False)
newbie = register("Demo Newbie", referral_code=owner["code"])
r = join(newbie, open_c["id"], via=link["token"])
check("a new member joins through Demo Member's invitation link", r.status_code == 200 and r.json()["joined"])
row = psql(f"select invited_by from community.memberships where community_id='{open_c['id']}' and user_id='{newbie['id']}'")
check("the community records Demo Member as inviter", row == member["id"], row)
check("the account sponsor stays the owner (not replaced)", psql(
    f"select invited_by from auth.users where id='{newbie['id']}'") == owner["id"])
self_link = c.post("/auth/share-links", headers=H(outsider),
                   json={"destination": f"/communities/{open_c['slug']}", "include_invitation": True}).json()
join(outsider, open_c["id"], via=self_link["token"])
check("a link from somebody not in the community credits nobody", psql(
    f"select coalesce(invited_by,'') from community.memberships where community_id='{open_c['id']}' and user_id='{outsider['id']}'") == "")
neutral = c.post("/auth/share-links", headers=H(member), json={"destination": f"/communities/{open_c['slug']}"}).json()
third = register("Demo Third")
join(third, open_c["id"], via=neutral["token"])
check("a neutral link credits nobody", psql(
    f"select coalesce(invited_by,'') from community.memberships where community_id='{open_c['id']}' and user_id='{third['id']}'") == "")
fourth = register("Demo Fourth", referral_link=link["token"])
join(fourth, open_c["id"], via=link["token"])
mine = {l["token"]: l for l in c.get("/auth/share-links", headers=H(member)).json()["items"]}
stats = mine[link["token"]]
check("the link counts opens by people only (crawler excluded)", stats["opens"] == 1, stats)
check("registrations and community joins are counted apart",
      stats["registrations"] == 1 and stats["community_joins"] == 2, stats)
join(fourth, open_c["id"], via=link["token"])
check("a repeated join is not counted twice", c.get("/auth/share-links", headers=H(member)).json()["items"][0]["community_joins"] in (2,)
      or {l["token"]: l for l in c.get("/auth/share-links", headers=H(member)).json()["items"]}[link["token"]]["community_joins"] == 2)

print()
print("8. Welcome once, and the invitation limit")
welcomed = psql(f"select count(*) from messaging.notifications where user_id='{newbie['id']}' and kind='community_welcome'")
check("the welcome message was sent", welcomed == "1", welcomed)
c.post(f"/communities/{open_c['id']}/members/{newbie['id']}", headers=H(owner), json={"action": "ban", "reason": "demo"})
c.post(f"/communities/{open_c['id']}/members/{newbie['id']}", headers=H(owner), json={"action": "unban"})
again = psql(f"select count(*) from messaging.notifications where user_id='{newbie['id']}' and kind='community_welcome'")
check("ban then unban does not send it again", again == "1", again)
limit = subprocess.run(["docker", "compose", "exec", "-T", "community-service", "python", "-c",
                        "import main\nprint([main._invite_allowed('demo-limit') for _ in range(main.INVITES_PER_HOUR + 1)][-2:])"],
                       capture_output=True, text=True).stdout.strip()
check("invitations past the hourly limit are refused", limit == "[True, False]", limit)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
