"""Groups in Messages, the 27/10 upgrade, end to end against the local stack.

Roles and their limits, invitations and privacy settings, blocks, the link's
limits, announcement mode, moderation, pins, saved messages, search, the
history rule, receipts, notification levels, @everyone, polls, events, tasks,
reports, the audit trail, ownership transfer, deletion and the Community link.

    python backend/tests/e2e_group_upgrade.py
"""
import random
import string
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8200/api"
PASSWORD = "Sup3rStrong!Pass"
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
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": PASSWORD,
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"],
            "email": f"{t}@example.com", "handle": f"u{t}", "name": name}


def connect(a, b):
    c.post(f"/connections/{b['id']}", headers=a["h"], json={"message": None})
    c.post(f"/connections/{a['id']}/respond?accept=true", headers=b["h"])


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def send(user, cid, body, **extra):
    return c.post(f"/conversations/{cid}/messages", headers=user["h"], json={"body": body, **extra})


def thread(user, cid):
    return c.get(f"/conversations/{cid}/messages", headers=user["h"]).json()


def notes(user, kind=None):
    items = c.get("/notifications", headers=user["h"]).json()["items"]
    return [n for n in items if kind is None or n["kind"] == kind]


print("== people, settings and blocks ==")
owner, admin, mod, member, late = (register(n) for n in ("Demo Owner", "Demo Admin", "Demo Mod", "Demo Member", "Demo Late"))
stranger, closed, blocker = register("Demo Stranger"), register("Demo Closed"), register("Demo Blocker")
for person in (admin, mod, member, late):
    connect(owner, person)
connect(admin, member)
r = c.patch("/preferences", headers=closed["h"], json={"who_can_add_group": "nobody", "who_can_invite_group": "nobody"})
check("the group settings are saved", r.status_code == 200 and r.json().get("who_can_invite_group") == "nobody", r.text)
r = c.post(f"/users/{owner['id']}/block", headers=blocker["h"])
check("a block is set", r.status_code == 201, r.text)

print("== creation: added, invited, refused ==")
rules = "Be kind. No spam."
r = c.post("/conversations", headers=owner["h"], json={
    "participant_ids": [admin["id"], mod["id"], member["id"], stranger["id"]], "kind": "group",
    "title": "Demo Group", "description": "A demo group", "rules": rules, "encrypted": False})
check("a group is created with rules", r.status_code == 201, r.text)
gid = r.json()["id"]
check("a stranger who allows invitations is invited, not added", r.json().get("invited") == [stranger["id"]], r.text)
r = c.post("/conversations", headers=owner["h"], json={"participant_ids": [closed["id"]], "kind": "group", "title": "Nope", "encrypted": False})
check("somebody who allows neither is refused", r.status_code == 403, r.text)
r = c.post("/conversations", headers=owner["h"], json={"participant_ids": [blocker["id"]], "kind": "group", "title": "Nope", "encrypted": False})
check("somebody with a block is refused", r.status_code == 403, r.text)
r = c.post("/conversations", headers=owner["h"], json={"participant_ids": [admin["id"]], "kind": "group", "title": " ", "encrypted": False})
check("a group needs a name", r.status_code == 422, r.text)

r = c.get(f"/conversations/{gid}/messages", headers=stranger["h"])
check("an invitee reads nothing before accepting", r.status_code == 404, r.status_code)
inv = c.get("/conversations/invites", headers=stranger["h"]).json()["items"]
check("the invitee sees the invitation with its rules", len(inv) == 1 and inv[0]["rules"] == rules, inv)
r = c.post(f"/conversations/invites/{inv[0]['id']}", headers=member["h"], json={"action": "accept"})
check("nobody else can answer it", r.status_code == 404, r.status_code)
r = c.post(f"/conversations/invites/{inv[0]['id']}", headers=stranger["h"], json={"action": "accept"})
check("accepting joins the group", r.status_code == 200 and r.json()["status"] == "member", r.text)
check("and the thread says who invited them", any("invited by Demo Owner" in (m["body"] or "") for m in thread(owner, gid)["items"]))

print("== roles ==")
r = c.post(f"/conversations/{gid}/participants/{admin['id']}/role", headers=owner["h"], json={"role": "admin"})
check("the owner appoints an admin", r.status_code == 200, r.text)
r = c.post(f"/conversations/{gid}/participants/{mod['id']}/role", headers=admin["h"], json={"role": "moderator"})
check("an admin appoints a moderator", r.status_code == 200 and r.json()["role"] == "moderator", r.text)
r = c.post(f"/conversations/{gid}/participants/{member['id']}/role", headers=admin["h"], json={"role": "admin"})
check("an admin cannot appoint an admin", r.status_code == 403, r.text)
r = c.delete(f"/conversations/{gid}/participants/{member['id']}", headers=mod["h"])
check("a moderator cannot remove members", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/participants/{member['id']}/role", headers=mod["h"], json={"role": "moderator"})
check("a moderator cannot appoint", r.status_code == 403, r.text)

print("== member list visibility ==")
c.patch(f"/conversations/{gid}/settings", headers=owner["h"], json={"members_visible": "admins"})
seen = c.get(f"/conversations/{gid}/participants", headers=member["h"]).json()
ids = {p["user_id"] for p in seen["items"]}
check("a member sees who runs it and themself", seen["limited"] and ids == {owner["id"], admin["id"], mod["id"], member["id"]}, seen)
check("and the real size", seen["members_count"] == 5, seen)
full = c.get(f"/conversations/{gid}/participants", headers=admin["h"]).json()
check("an admin sees everybody", not full["limited"] and len(full["items"]) == 5, full)
r = c.patch(f"/conversations/{gid}/settings", headers=member["h"], json={"members_visible": "all"})
check("a member cannot change settings", r.status_code == 403, r.text)
c.patch(f"/conversations/{gid}/settings", headers=owner["h"], json={"members_visible": "all"})

print("== announcement mode and moderation ==")
r = send(member, gid, "hello from a member")
mid_member = r.json()["id"]
c.patch(f"/conversations/{gid}/settings", headers=owner["h"], json={"posting": "admins"})
r = send(member, gid, "can I talk?")
check("announcement mode stops members writing", r.status_code == 403, r.text)
r = send(mod, gid, "moderators can")
check("moderators still write", r.status_code == 201, r.text)
mid_mod = r.json()["id"]
r = c.post(f"/conversations/{gid}/messages/{mid_mod}/reactions", headers=member["h"], json={"emoji": "\U0001F44D"})
check("members still react", r.status_code == 200, r.text)
c.patch(f"/conversations/{gid}/settings", headers=owner["h"], json={"posting": "all"})
r = c.delete(f"/conversations/{gid}/messages/{mid_mod}", headers=member["h"])
check("a member cannot delete somebody else's message", r.status_code == 403, r.text)
mid_owner = send(owner, gid, "owner speaking").json()["id"]
r = c.delete(f"/conversations/{gid}/messages/{mid_owner}", headers=mod["h"])
check("a moderator cannot remove the owner's message", r.status_code == 403, r.text)
r = c.delete(f"/conversations/{gid}/messages/{mid_member}", headers=mod["h"])
check("a moderator removes a member's message", r.status_code == 200 and r.json()["removed"], r.text)
gone = next(m for m in thread(owner, gid)["items"] if m["id"] == mid_member)
check("it shows as removed, with no words", gone["removed"] and gone["body"] is None, gone)

print("== pins, saved, search, replies ==")
mid_old = send(owner, gid, "the pineapple plan, before the newcomer").json()["id"]
r = c.post(f"/conversations/{gid}/messages/{mid_old}/pin", headers=member["h"])
check("a member cannot pin", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/messages/{mid_old}/pin", headers=mod["h"])
check("a moderator pins", r.status_code == 200, r.text)
pins = c.get(f"/conversations/{gid}/pins", headers=member["h"]).json()["items"]
check("members see the pins", [p["id"] for p in pins] == [mid_old], pins)
r = c.post(f"/conversations/{gid}/messages/{mid_old}/save", headers=member["h"])
check("a member saves a message", r.status_code == 200, r.text)
saved = c.get("/conversations/saved", headers=member["h"]).json()["items"]
check("it is in their saved messages", any(s["id"] == mid_old for s in saved), saved)
check("nobody else sees it saved", not any(s["id"] == mid_old for s in c.get("/conversations/saved", headers=admin["h"]).json()["items"]))
hits = c.get(f"/conversations/{gid}/search", headers=member["h"], params={"q": "PINEAPPLE"}).json()
check("search finds it, whatever the case", [h["id"] for h in hits["items"]] == [mid_old], hits)
r = c.get(f"/conversations/{gid}/search", headers=stranger["h"], params={"q": "x"})
check("a search needs two characters", r.status_code == 422, r.text)
r = c.get(f"/conversations/{gid}/search", headers=closed["h"], params={"q": "pineapple"})
check("nobody outside searches the group", r.status_code == 404, r.text)
reply = send(admin, gid, "replying to the plan", reply_to_id=mid_old).json()["id"]
side = c.get(f"/conversations/{gid}/messages/{mid_old}/replies", headers=member["h"]).json()
check("the replies view holds the reply", [i["id"] for i in side["items"]] == [reply], side)
row = next(m for m in thread(member, gid)["items"] if m["id"] == mid_old)
check("the thread counts replies and marks pins and saves", row.get("reply_count") == 1 and row.get("pinned") and row.get("saved"), row)
check("a reply notifies the author", any("replied to you" in n["title"] for n in notes(owner)), notes(owner)[:3])
send(owner, gid, "see https://example.com/demo-page for the details")
links = c.get(f"/conversations/{gid}/files", headers=member["h"], params={"kind": "links"}).json()["items"]
check("the links view lists shared links", any(l["url"] == "https://example.com/demo-page" for l in links), links)

print("== the history rule, applied everywhere ==")
c.patch(f"/conversations/{gid}/history", headers=owner["h"], json={"history": "from_join"})
r = c.post(f"/conversations/{gid}/participants", headers=owner["h"], json={"user_ids": [late["id"]]})
check("a latecomer is added", r.json().get("added") == [late["id"]], r.text)
late_ids = {m["id"] for m in thread(late, gid)["items"]}
check("the latecomer does not read earlier messages", mid_old not in late_ids, late_ids)
check("nor their pins", c.get(f"/conversations/{gid}/pins", headers=late["h"]).json()["items"] == [])
check("nor their search", c.get(f"/conversations/{gid}/search", headers=late["h"], params={"q": "pineapple"}).json()["items"] == [])
check("nor their links", c.get(f"/conversations/{gid}/files", headers=late["h"], params={"kind": "links"}).json()["items"] == [])
r = c.post(f"/conversations/{gid}/messages/{mid_old}/save", headers=late["h"])
check("nor can they save them", r.status_code == 404, r.text)
r = c.get(f"/conversations/{gid}/messages/{mid_old}/replies", headers=late["h"])
check("nor open their replies", r.status_code == 404, r.text)
c.patch(f"/conversations/{gid}/history", headers=owner["h"], json={"history": "all"})

print("== receipts and notification levels ==")
mine = send(owner, gid, "who read this?").json()["id"]
c.post(f"/conversations/{gid}/read", headers=member["h"])
c.patch(f"/conversations/{gid}/notifications", headers=admin["h"], json={"read_receipts": False})
c.post(f"/conversations/{gid}/read", headers=admin["h"])
rec = c.get(f"/conversations/{gid}/messages/{mine}/receipts", headers=owner["h"]).json()
names = {p["user_id"] for p in rec["readers"] or []}
check("receipts count a reader", member["id"] in names, rec)
check("and leave out a member who keeps theirs private", admin["id"] not in names, rec)
r = c.get(f"/conversations/{gid}/messages/{mine}/receipts", headers=member["h"])
check("only the sender sees receipts", r.status_code == 403, r.text)

c.post("/notifications/read", headers=member["h"])
r = c.patch(f"/conversations/{gid}/notifications", headers=member["h"], json={"level": "mentions"})
check("a member chooses mentions only", r.json()["level"] == "mentions", r.text)
send(owner, gid, "plain chatter")
check("plain messages do not notify them", not [n for n in notes(member, "message") if n.get("read") is False], notes(member)[:3])
send(owner, gid, f"hey @{member['handle']} look")
check("a mention does", any("mentioned you" in n["title"] for n in notes(member, "mention")), notes(member)[:3])
c.patch(f"/conversations/{gid}/notifications", headers=member["h"], json={"mute": "forever"})
before = len(notes(member, "mention"))
send(owner, gid, f"again @{member['handle']}")
check("muted forever, nothing", len(notes(member, "mention")) == before, notes(member)[:3])
r = c.get("/conversations", headers=member["h"]).json()["items"]
mine_row = next(i for i in r if i["id"] == gid)
check("the list shows the member's own settings", mine_row["notifications"]["muted_forever"] and mine_row["notifications"]["level"] == "mentions", mine_row["notifications"])
c.patch(f"/conversations/{gid}/notifications", headers=member["h"], json={"mute": "off", "level": "all"})

r = send(member, gid, "@everyone listen")
check("@everyone is not a member's", r.json().get("everyone") == "not_allowed", r.text)
r = send(owner, gid, "@everyone meeting now")
check("@everyone from the owner goes out", r.json().get("everyone") == "sent", r.text)
check("and reaches members", any("notified everyone" in n["title"] for n in notes(late)), notes(late)[:3])
r = send(admin, gid, "@everyone again")
check("@everyone is limited to once an hour", r.json().get("everyone") == "rate_limited", r.text)

ann = send(owner, gid, "Important: the venue changed").json()["id"]
c.patch(f"/conversations/{gid}/notifications", headers=late["h"], json={"level": "announcements"})
r = c.post(f"/conversations/{gid}/messages/{ann}/announcement", headers=mod["h"])
check("a moderator cannot mark announcements", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/messages/{ann}/announcement", headers=admin["h"])
check("an admin marks an announcement", r.status_code == 200, r.text)
check("members who want announcements are told", any(n["kind"] == "group_announcement" for n in notes(late)), notes(late)[:3])
check("the thread marks it", next(m for m in thread(member, gid)["items"] if m["id"] == ann)["announcement"])

print("== polls ==")
r = c.post(f"/conversations/{gid}/polls", headers=member["h"], json={
    "question": "Where shall we meet?", "options": ["Cafe", "Park", "Online"], "results": "after_vote"})
check("a member starts a poll", r.status_code == 201, r.text)
poll = r.json()["poll"]
pid = poll["id"]
view = c.get(f"/conversations/{gid}/polls/{pid}", headers=late["h"]).json()
check("results stay hidden until you vote", view["results_visible"] is False and view["options"][0]["votes"] is None, view)
r = c.post(f"/conversations/{gid}/polls/{pid}/vote", headers=late["h"], json={"option_ids": [poll["options"][0]["id"], poll["options"][1]["id"]]})
check("a single-answer poll takes one answer", r.status_code == 422, r.text)
r = c.post(f"/conversations/{gid}/polls/{pid}/vote", headers=late["h"], json={"option_ids": [poll["options"][1]["id"]]})
check("a vote counts", r.status_code == 200 and r.json()["options"][1]["votes"] == 1, r.text)
r = c.post(f"/conversations/{gid}/polls/{pid}/close", headers=late["h"])
check("a member cannot close somebody else's poll", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/polls/{pid}/close", headers=member["h"])
check("its creator closes it", r.status_code == 200 and r.json()["closed"], r.text)
r = c.post(f"/conversations/{gid}/polls/{pid}/vote", headers=owner["h"], json={"option_ids": [poll["options"][0]["id"]]})
check("a closed poll takes no votes", r.status_code == 409, r.text)
card = next(m for m in thread(owner, gid)["items"] if m.get("poll", {}).get("id") == pid)
check("the poll sits in the thread", card["kind"] == "poll" and card["poll"]["closed"], card)
c.patch(f"/conversations/{gid}/settings", headers=owner["h"], json={"polls_by": "admins"})
r = c.post(f"/conversations/{gid}/polls", headers=member["h"], json={"question": "Again?", "options": ["Yes", "No"]})
check("polls can be kept to admins", r.status_code == 403, r.text)

print("== events ==")
starts = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
r = c.post(f"/conversations/{gid}/events", headers=member["h"], json={
    "title": "Demo meetup", "starts_at": starts.isoformat(), "timezone": "Africa/Abidjan",
    "location": "Demo Street 1", "online_url": "https://example.com/meet"})
check("a member creates an event", r.status_code == 201 and r.json()["event"]["timezone"] == "Africa/Abidjan", r.text)
eid = r.json()["event"]["id"]
r = c.post(f"/conversations/{gid}/events", headers=member["h"], json={"title": "Bad", "starts_at": starts.isoformat(), "online_url": "javascript:alert(1)"})
check("a meeting link must be a web address", r.status_code == 422, r.text)
r = c.post(f"/conversations/{gid}/events/{eid}/response", headers=late["h"], json={"response": "maybe"})
check("members answer", r.status_code == 200 and r.json()["counts"]["maybe"] == 1, r.text)
r = c.post(f"/conversations/{gid}/events/{eid}/cancel", headers=late["h"])
check("a member cannot cancel somebody else's event", r.status_code == 403, r.text)
events = c.get(f"/conversations/{gid}/events", headers=late["h"]).json()["items"]
check("upcoming events are listed", any(e["id"] == eid for e in events), events)

print("== tasks ==")
r = c.post(f"/conversations/{gid}/tasks", headers=owner["h"], json={"title": "Book the room", "assignee_id": member["id"],
                                                                     "due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
check("a task is given to a member", r.status_code == 201, r.text)
tid = r.json()["task"]["id"]
check("who is told", any(n["kind"] == "group_task" for n in notes(member)), notes(member)[:3])
r = c.patch(f"/conversations/{gid}/tasks/{tid}", headers=late["h"], json={"done": True})
check("somebody else cannot tick it", r.status_code == 403, r.text)
r = c.patch(f"/conversations/{gid}/tasks/{tid}", headers=member["h"], json={"done": True})
check("the assignee ticks it", r.status_code == 200 and r.json()["done"], r.text)
r = c.post(f"/conversations/{gid}/tasks", headers=owner["h"], json={"title": "x", "assignee_id": closed["id"]})
check("a task goes only to a member", r.status_code == 422, r.text)

print("== the link ==")
c.patch(f"/conversations/{gid}/privacy", headers=owner["h"], json={"privacy": "public"})
code = c.post(f"/conversations/{gid}/share-link", headers=member["h"]).json()["code"]
r = c.patch(f"/conversations/{gid}/invite-link", headers=member["h"], json={"max_uses": 1})
check("a member cannot change the link", r.status_code == 403, r.text)
c.patch(f"/conversations/{gid}/invite-link", headers=admin["h"], json={"max_uses": 1})
first, second = register("Demo First"), register("Demo Second")
preview = c.get(f"/conversations/join/{code}", headers=first["h"]).json()
check("the preview shows the rules", preview["rules"] == rules and preview["unavailable"] is None, preview)
r = c.post(f"/conversations/join/{code}", headers=first["h"])
check("the link lets one in", r.json().get("status") == "member", r.text)
r = c.post(f"/conversations/join/{code}", headers=second["h"])
check("and no more than it allows", r.status_code == 410, r.text)
check("the preview says why", c.get(f"/conversations/join/{code}", headers=second["h"]).json()["unavailable"], "")
new = c.post(f"/conversations/{gid}/invite-link/reset", headers=admin["h"]).json()
check("a reset gives a new link", new["path"] and code not in new["path"] and new["uses"] == 0, new)
r = c.post(f"/conversations/join/{code}", headers=second["h"])
check("the old link leads nowhere", r.status_code == 404, r.text)
c.patch(f"/conversations/{gid}/invite-link", headers=admin["h"], json={"enabled": False, "max_uses": None})
r = c.post(f"/conversations/{gid}/share-link", headers=member["h"])
check("a link that is off cannot be shared", r.status_code == 409, r.text)
r = c.patch(f"/conversations/{gid}/invite-link", headers=admin["h"], json={"enabled": True, "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
check("an expiry must be in the future", r.status_code == 422, r.text)

print("== reports and the audit trail ==")
r = c.post(f"/conversations/{gid}/reports", headers=member["h"], json={"target_kind": "message", "target_id": mid_owner, "reason": "spam"})
check("a member reports a message", r.status_code == 201, r.text)
r = c.post(f"/conversations/{gid}/reports", headers=closed["h"], json={"target_kind": "group", "target_id": gid, "reason": "spam"})
check("an outsider cannot report the group", r.status_code == 404, r.text)
r = c.get("/admin/message-reports", headers=member["h"])
check("members do not see the report queue", r.status_code == 403, r.text)
psql(f"update auth.users set role='admin' where id='{late['id']}'")
staff = c.post("/auth/login", json={"email": late["email"], "password": PASSWORD}).json()
staff_h = {"Authorization": f"Bearer {staff['tokens']['access_token']}"}
queue = c.get("/admin/message-reports", headers=staff_h).json()["items"]
mine_report = next((q for q in queue if q["target_id"] == mid_owner), None)
check("Kinjy's moderators see it with the message", mine_report and mine_report["message"]["body"] == "owner speaking", queue[:2])
r = c.post(f"/admin/message-reports/{mine_report['id']}/close", headers=staff_h, json={"resolution": "Spam", "remove_message": True})
check("a moderator closes it and removes the message", r.status_code == 200 and r.json()["removed"], r.text)
psql(f"update auth.users set role='member' where id='{late['id']}'")
audit = c.get(f"/conversations/{gid}/audit", headers=admin["h"]).json()["items"]
actions = {a["action"] for a in audit}
check("the audit trail holds the sensitive changes",
      {"role_changed", "message_removed", "setting_changed", "link_reset", "invited", "message_removed_by_kinjy"} <= actions, actions)
r = c.get(f"/conversations/{gid}/audit", headers=member["h"])
check("members do not read the audit trail", r.status_code == 403, r.text)

print("== the Community link ==")
community = c.post("/communities", headers=owner["h"], json={"name": f"Demo Community {tag()}", "kind": "private"}).json()
handle = community.get("slug") or community["id"]
r = c.post(f"/communities/{handle}/group-chats", headers=owner["h"], json={"conversation_id": gid})
check("the owner of both links the chat", r.status_code == 201, r.text)
listed = c.get(f"/communities/{handle}/group-chats", headers=owner["h"]).json()
check("the community lists it", [g["id"] for g in listed["items"]] == [gid], listed)
r = c.get(f"/communities/{handle}/group-chats", headers=member["h"])
check("a non-member of a private community cannot see its chats", r.status_code == 403, r.text)
member_of = c.get(f"/communities/{community['id']}/membership", headers=member["h"])
check("being in the chat does not make anybody a community member",
      member_of.status_code in (403, 404) or member_of.json().get("member") is False, member_of.text)
r = c.post(f"/communities/{handle}/group-chats", headers=member["h"], json={"conversation_id": gid})
check("a member cannot link chats", r.status_code == 403, r.text)
r = c.delete(f"/communities/{handle}/group-chats/{gid}", headers=owner["h"])
check("it can be unlinked", r.status_code == 200, r.text)

print("== ownership and deletion ==")
r = c.delete(f"/conversations/{gid}/participants/{owner['id']}", headers=owner["h"])
check("the owner cannot leave without handing on", r.status_code == 409, r.text)
r = c.post(f"/conversations/{gid}/transfer", headers=admin["h"], json={"user_id": admin["id"]})
check("only the owner hands on", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/transfer", headers=owner["h"], json={"user_id": admin["id"]})
check("the owner hands the group on", r.status_code == 200 and r.json()["your_role"] == "admin", r.text)
r = c.post(f"/conversations/{gid}/delete", headers=owner["h"], json={"confirm_title": "Demo Group"})
check("the former owner cannot delete it", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/delete", headers=admin["h"], json={"confirm_title": "demo group"})
check("deleting needs the exact name", r.status_code == 422, r.text)
r = c.post(f"/conversations/{gid}/delete", headers=admin["h"], json={"confirm_title": "Demo Group"})
check("the owner deletes it", r.status_code == 200, r.text)
check("nobody reads it any more", c.get(f"/conversations/{gid}/messages", headers=member["h"]).status_code == 404)
check("members are told", any(n["kind"] == "group_deleted" for n in notes(member)), notes(member)[:3])
check("its messages are gone from the database", psql(f"select count(*) from messaging.messages where conversation_id='{gid}'") == "0")
check("its audit trail stays", int(psql(f"select count(*) from messaging.group_audit where conversation_id='{gid}'") or 0) > 5)
check("the saved message went with it", not any(s["id"] == mid_old for s in c.get("/conversations/saved", headers=member["h"]).json()["items"]))

print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
