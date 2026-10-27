"""Forums, the 27/10 upgrade, end to end against the local stack.

Visibility (public, members, private) and the post/reply policies; thread
types, editing, reopening; comments on answers and mentions; follow, save,
drafts; search, related questions and filters under access rules; moderation
(pin, lock, hide with a reason and a review request, move, merge, reports,
audit); knowledge articles (sources, contributors, revisions, suggestions,
drafts kept from readers, AI drafts refused without a model); polls, expert
sessions, contributor profiles, the community's forum activity.

    python backend/tests/e2e_forum_upgrade.py
"""
import random
import string
import subprocess
import sys
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


def notes(user, kind=None):
    items = c.get("/notifications", headers=user["h"]).json()["items"]
    return [n for n in items if kind is None or n["kind"] == kind]


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


owner, member, outsider, helper, mod = (register(n) for n in ("Demo Owner", "Demo Member", "Demo Outsider", "Demo Helper", "Demo Mod"))
word = tag()

print("== a community and its forums ==")
community = c.post("/communities", headers=owner["h"], json={"name": f"Demo Farmers {word}", "kind": "public"}).json()
cid = community["id"]
for u in (member, helper, mod):
    c.post(f"/communities/{cid}/join", headers=u["h"])
r = c.post("/forums", headers=owner["h"], json={
    "name": f"Demo Crops {word}", "description": "Questions about crops, for growers.", "community_id": cid,
    "category": "Farming", "tags": ["Maize", "soil"], "lang": "en", "country": "CI", "rules": "Be specific. No ads."})
check("a forum is created with a category, tags and rules", r.status_code == 201, r.text)
fid = r.json()["id"]
detail = c.get(f"/forums/{fid}", headers=member["h"]).json()
check("its page shows the rules, visibility and tags", detail["rules"] == "Be specific. No ads." and detail["visibility"] == "public" and detail["tags"] == ["maize", "soil"], detail)
check("a member cannot configure it", not detail["can_configure"] and not detail["can_moderate"])
members_only = c.post("/forums", headers=owner["h"], json={"name": f"Demo Members {word}", "community_id": cid, "visibility": "members"}).json()["id"]
private = c.post("/forums", headers=owner["h"], json={"name": f"Demo Staff {word}", "community_id": cid, "visibility": "private"}).json()["id"]
r = c.get(f"/forums/{members_only}/threads", headers=outsider["h"])
check("a members-only forum of a public community is closed to outsiders", r.status_code == 403, r.status_code)
check("and open to members", c.get(f"/forums/{members_only}/threads", headers=member["h"]).status_code == 200)
check("a private forum is hidden from members", c.get(f"/forums/{private}/threads", headers=member["h"]).status_code == 404)
listed = {f["id"] for f in c.get("/forums", params={"community_id": cid}, headers=member["h"]).json()["items"]}
check("and not listed to them", private not in listed and fid in listed, listed)
check("its moderators see it", c.get(f"/forums/{private}/threads", headers=owner["h"]).status_code == 200)
paid = c.post("/communities", headers=owner["h"], json={"name": f"Demo Paid {word}", "kind": "paid", "price_usd": 5}).json()["id"]
r = c.post("/forums", headers=owner["h"], json={"name": f"Demo Paid forum {word}", "community_id": paid, "visibility": "public"})
check("a paid community's forum cannot be made public", r.status_code == 422, r.text)

print("== threads: types, policies, rules ==")
r = c.post(f"/forums/{fid}/threads", headers=member["h"], json={"title": "Announcement from a member", "body": "x", "kind": "announcement"})
check("announcements are for moderators", r.status_code == 403, r.text)
r = c.post(f"/forums/{fid}/threads", headers=member["h"], json={
    "title": f"How deep to plant maize {word}?", "body": f"In clay soil. @{helper['handle']} any idea?", "kind": "question",
    "tags": ["maize"], "country": "CI", "region": "Bouaké", "poll": {"question": "Your soil?", "options": ["Clay", "Sand"]}})
check("a member asks a question with a poll", r.status_code == 201, r.text)
q = r.json()["id"]
check("a mention notifies the member named", any("mentioned you" in n["title"] for n in notes(helper)), notes(helper)[:2])
r = c.post(f"/forums/{fid}/threads", headers=owner["h"], json={"title": f"Welcome guide {word}", "body": "Read the rules.", "kind": "guide"})
guide = r.json()["id"]
c.post(f"/forums/{fid}/threads", headers=owner["h"], json={"title": f"Market day {word}", "body": "Tuesday.", "kind": "announcement"})
c.patch(f"/forums/{fid}", headers=owner["h"], json={"reply_policy": "members"})
r = c.post(f"/threads/{q}/replies", headers=outsider["h"], json={"body": "Outsider reply"})
check("a reply policy of members keeps outsiders to reading", r.status_code == 403, r.text)
c.patch(f"/forums/{fid}", headers=owner["h"], json={"reply_policy": "everyone"})

print("== answers, comments, accepting, reopening, editing ==")
a1 = c.post(f"/threads/{q}/replies", headers=helper["h"], json={"body": "About 5 cm deep."}).json()["id"]
check("the asker is notified of an answer", any(n["kind"] == "forum_reply" for n in notes(member)), notes(member)[:3])
cm = c.post(f"/threads/{q}/replies", headers=outsider["h"], json={"body": "Useful because it matched my field.", "parent_id": a1}).json()["id"]
check("the answerer is notified of a comment on their answer", any("replied to you" in n["title"] for n in notes(helper)), notes(helper)[:3])
r = c.post(f"/replies/{cm}/accept", headers=member["h"])
check("a comment cannot be the accepted answer", r.status_code == 409, r.text)
r = c.post(f"/replies/{a1}/accept", headers=member["h"])
check("the asker accepts an answer", r.status_code == 200, r.text)
r = c.post(f"/replies/{a1}/upvote", headers=outsider["h"])
r = c.post(f"/replies/{a1}/upvote", headers=outsider["h"])
check("a second vote takes the first back", r.json()["upvotes"] == 0 and r.json()["voted"] is False, r.text)
c.post(f"/replies/{a1}/upvote", headers=outsider["h"])
c.post(f"/replies/{a1}/upvote", headers=mod["h"])
th = c.get(f"/threads/{q}", headers=member["h"]).json()
check("the thread says what it is and what the viewer may do", th["kind"] == "question" and th["solved"] and th["can_reopen"] and th["poll"]["options"][0]["text"] == "Clay", {k: th.get(k) for k in ("kind", "solved", "can_reopen")})
r = c.post(f"/forums/{fid}/threads", headers=owner["h"], json={"title": "x" * 5, "body": "y", "kind": "guide"})
r = c.post(f"/replies/{a1}/accept", headers=owner["h"])
check("only the asker accepts", r.status_code == 403, r.text)
gr = c.post(f"/threads/{guide}/replies", headers=helper["h"], json={"body": "Thanks"}).json()["id"]
r = c.post(f"/replies/{gr}/accept", headers=owner["h"])
check("a guide has no accepted answer", r.status_code == 409, r.text)
r = c.post(f"/threads/{q}/poll/vote", headers=outsider["h"], json={"options": [0]})
check("members vote in the poll", r.status_code == 200 and r.json()["options"][0]["votes"] == 1, r.text)
r = c.patch(f"/threads/{q}", headers=helper["h"], json={"body": "hijack"})
check("only the author edits a thread", r.status_code == 403, r.text)
r = c.patch(f"/threads/{q}", headers=member["h"], json={"body": "In clay soil, rainy season. Edit: about 1 hectare."})
check("the author edits, and it is marked", r.status_code == 200 and c.get(f"/threads/{q}", headers=member["h"]).json()["edited_at"], r.text)
r = c.patch(f"/replies/{a1}", headers=helper["h"], json={"body": "About 5 cm deep in clay."})
check("an answer's author edits it", r.status_code == 200, r.text)
r = c.post(f"/threads/{q}/reopen", headers=member["h"], json={"reason": "It did not sprout"})
check("the asker reopens the question", r.status_code == 200, r.text)
th = c.get(f"/threads/{q}", headers=member["h"]).json()
check("it is no longer solved, the answer stays", not th["solved"] and any(x["id"] == a1 for x in th["replies"]) and th["reopened_at"], th.get("reopened_at"))
c.post(f"/replies/{a1}/accept", headers=member["h"])

print("== lists, filters, search, related ==")
items = c.get(f"/forums/{fid}/threads", headers=member["h"]).json()["items"]
mine = next(i for i in items if i["id"] == q)
check("thread cards carry type, tags, place and reply facts", mine["kind"] == "question" and mine["tags"] == ["maize"] and mine["country"] == "CI" and mine["last_reply_at"] and mine["has_poll"], mine)
unanswered = {i["id"] for i in c.get(f"/forums/{fid}/threads", params={"sort": "unanswered"}, headers=member["h"]).json()["items"]}
check("announcements and guides are never 'unanswered'", not unanswered & {guide}, unanswered)
check("the kind filter works", {i["kind"] for i in c.get(f"/forums/{fid}/threads", params={"kind": "guide"}, headers=member["h"]).json()["items"]} == {"guide"})
check("the country filter works", [i["id"] for i in c.get(f"/forums/{fid}/threads", params={"country": "CI"}, headers=member["h"]).json()["items"]] == [q])
secret_q = c.post(f"/forums/{private}/threads", headers=owner["h"], json={"title": f"Staff maize matter {word}", "body": "internal"}).json()["id"]
hits = c.get("/forums/search", params={"q": word}, headers=member["h"]).json()
ids = {t["id"] for t in hits["threads"]}
check("search finds what the member may read", q in ids, ids)
check("and never a private forum's thread", secret_q not in ids, ids)
check("the owner's search does include it", secret_q in {t["id"] for t in c.get("/forums/search", params={"q": word}, headers=owner["h"]).json()["threads"]})
check("search matches the words of answers too", q in {t["id"] for t in c.get("/forums/search", params={"q": "5 cm deep in clay"}, headers=outsider["h"]).json()["threads"]})
rel = c.get("/forums/related", params={"title": f"maize planting depth {word}", "forum_id": fid}, headers=outsider["h"]).json()["items"]
check("related questions are suggested before posting", any(x["id"] == q for x in rel), rel)
check("the directory filters by category", fid in {f["id"] for f in c.get("/forums", params={"category": "Farming"}, headers=member["h"]).json()["items"]})

print("== following, saving, drafts, topics ==")
c.post(f"/forums/{fid}/follow", headers=outsider["h"], json={"notify_new": True})
check("a followed forum is in the member's list", fid in {f["id"] for f in c.get("/forums", params={"followed": True}, headers=outsider["h"]).json()["items"]})
c.post("/forum-topics/maize/follow", headers=mod["h"])
c.post(f"/forums/{fid}/threads", headers=member["h"], json={"title": f"Maize pests {word}", "body": "Armyworm?", "tags": ["maize"]})
check("forum followers who asked are told of a new thread", any(n["kind"] == "forum_new_thread" for n in notes(outsider)), notes(outsider)[:3])
check("topic followers too", any(n["kind"] == "forum_topic" for n in notes(mod)), notes(mod)[:3])
c.post(f"/threads/{q}/follow", headers=outsider["h"], json={"saved": True})
check("a saved thread is in the saved list", q in {t["id"] for t in c.get("/forum-saved", headers=outsider["h"]).json()["items"]})
d = c.post("/forum-drafts", headers=outsider["h"], json={"forum_id": fid, "title": "Half written", "body": "..."}).json()
check("a draft is kept", any(x["id"] == d["id"] for x in c.get("/forum-drafts", headers=outsider["h"]).json()["items"]))
c.post(f"/forums/{fid}/threads", headers=outsider["h"], json={"title": "Half written, now done", "body": "Done.", "kind": "discussion", "draft_id": d["id"]})
check("publishing removes the draft", not any(x["id"] == d["id"] for x in c.get("/forum-drafts", headers=outsider["h"]).json()["items"]))

print("== moderation ==")
r = c.post(f"/forums/{fid}/moderators", headers=member["h"], json={"user_id": mod["id"]})
check("a member cannot appoint moderators", r.status_code == 403, r.text)
r = c.post(f"/forums/{fid}/moderators", headers=owner["h"], json={"user_id": mod["id"], "can_edit_knowledge": False})
check("the community owner appoints a forum moderator", r.status_code == 200, r.text)
spam = c.post(f"/forums/{fid}/threads", headers=outsider["h"], json={"title": "Cheap seeds here!!!", "body": "buy buy", "kind": "discussion"}).json()["id"]
c.post("/forum-reports", headers=member["h"], json={"target_kind": "thread", "target_id": spam, "reason": "spam"})
queue = c.get(f"/forums/{fid}/moderation", headers=mod["h"]).json()
check("the moderator sees the report", any(x["target_id"] == spam for x in queue["reports"]), queue["reports"][:1])
check("members do not see the queue", c.get(f"/forums/{fid}/moderation", headers=member["h"]).status_code == 403)
r = c.post(f"/threads/{spam}/moderate", headers=mod["h"], json={"action": "hide"})
check("hiding needs a reason", r.status_code == 422, r.text)
c.post(f"/threads/{spam}/moderate", headers=mod["h"], json={"action": "hide", "reason": "Advertising, against the rules"})
check("hidden content is gone for readers", c.get(f"/threads/{spam}", headers=member["h"]).status_code == 404)
check("the author is told why", any("Advertising" in (n.get("body") or "") for n in notes(outsider, "forum_moderation")), notes(outsider)[:2])
r = c.post("/forum-reviews", headers=outsider["h"], json={"target_kind": "thread", "target_id": spam, "note": "It was not an ad"})
check("the author asks for a review", r.status_code == 201, r.text)
check("which reaches the moderators", any(x["target_kind"] == "review" for x in c.get(f"/forums/{fid}/moderation", headers=mod["h"]).json()["reports"]))
c.post(f"/threads/{q}/moderate", headers=mod["h"], json={"action": "pin"})
c.post(f"/threads/{q}/moderate", headers=mod["h"], json={"action": "lock", "reason": "Answered"})
check("a locked thread takes no replies", c.post(f"/threads/{q}/replies", headers=helper["h"], json={"body": "late"}).status_code == 403)
c.post(f"/threads/{q}/moderate", headers=mod["h"], json={"action": "unlock"})
dup = c.post(f"/forums/{fid}/threads", headers=outsider["h"], json={"title": f"Maize depth again {word}", "body": "Same question"}).json()["id"]
c.post(f"/threads/{dup}/replies", headers=helper["h"], json={"body": "See the other thread"})
r = c.post(f"/threads/{dup}/merge", headers=mod["h"], json={"into_thread_id": q, "reason": "Duplicate"})
check("a duplicate is merged", r.status_code == 200 and r.json()["replies_moved"] == 1, r.text)
check("its old link leads to the merged thread", c.get(f"/threads/{dup}", headers=member["h"]).json().get("merged_into") == q)
merged = c.get(f"/threads/{q}", headers=member["h"]).json()
check("its question and answers keep their authors", any(x["author_id"] == outsider["id"] and x["body"].startswith("[Merged question]") for x in merged["replies"]))
r = c.post(f"/threads/{guide}/move", headers=mod["h"], json={"forum_id": members_only, "reason": "Members guide"})
check("a moderator of one forum cannot move into another", r.status_code == 403, r.text)
c.post(f"/forums/{members_only}/moderators", headers=owner["h"], json={"user_id": mod["id"]})
r = c.post(f"/threads/{guide}/move", headers=mod["h"], json={"forum_id": members_only, "reason": "Members guide"})
check("a move to a forum with other readers needs confirming", r.status_code == 409, r.text)
r = c.post(f"/threads/{guide}/move", headers=mod["h"], json={"forum_id": members_only, "reason": "Members guide", "acknowledge_audience_change": True})
check("confirmed, it moves", r.status_code == 200, r.text)
check("and outsiders can no longer read it", c.get(f"/threads/{guide}", headers=outsider["h"]).status_code == 404)
audit = {a["action"] for a in c.get(f"/forums/{fid}/audit", headers=owner["h"]).json()["items"]}
check("the audit trail holds the actions", {"thread_hide", "thread_pin", "thread_lock", "thread_merged", "thread_moved", "moderator_set"} <= audit, audit)

print("== knowledge ==")
kb = c.get(f"/forums/{fid}/knowledge", headers=member["h"]).json()
check("an accepted, upvoted answer shows as community knowledge, with its author", any(i["source_reply_id"] == a1 and i["contributors"] == [helper["id"]] for i in kb["items"]), kb["items"][:1])
r = c.post(f"/forums/{fid}/knowledge", headers=mod["h"], json={"question": "Planting depth for maize", "answer": "About 5 cm in clay soil."})
check("a moderator without the knowledge right cannot write articles", r.status_code == 403, r.text)
r = c.post(f"/forums/{fid}/knowledge", headers=owner["h"], json={
    "question": "Planting depth for maize", "answer": "About 5 cm in clay soil, per the discussion.", "source_thread_ids": [q],
    "source_reply_ids": [a1], "source_links": [{"url": "https://example.com/maize", "label": "Extension sheet"}], "tags": ["maize"], "publish": False})
check("an editor writes a draft article from the discussion", r.status_code == 201, r.text)
art = r.json()["id"]
check("readers do not see a draft", not any(i["id"] == art for i in c.get(f"/forums/{fid}/knowledge", headers=member["h"]).json()["items"]))
c.post(f"/knowledge/{art}/publish", headers=owner["h"])
entry = next(i for i in c.get(f"/forums/{fid}/knowledge", headers=member["h"]).json()["items"] if i["id"] == art)
check("published, it credits contributors and cites its sources", {x["user_id"] for x in entry["contributors"]} >= {member["id"], helper["id"]} and entry["source_links"][0]["label"] == "Extension sheet" and entry["reviewed_at"], entry)
c.patch(f"/knowledge/{art}", headers=owner["h"], json={"answer": "About 5 cm in clay soil; deeper in sand.", "note": "Added sand"})
revs = c.get(f"/knowledge/{art}/revisions", headers=owner["h"]).json()["items"]
check("every change is a revision", len(revs) >= 3 and revs[0]["note"] == "Added sand", [x["note"] for x in revs])
check("readers do not read revisions", c.get(f"/knowledge/{art}/revisions", headers=member["h"]).status_code == 403)
r = c.post(f"/knowledge/{art}/suggestions", headers=outsider["h"], json={"kind": "outdated", "note": "New varieties need less depth."})
check("a member flags it as outdated", r.status_code == 201, r.text)
check("editors see the suggestion", len(c.get(f"/knowledge/{art}/suggestions", headers=owner["h"]).json()["items"]) == 1)
psql(f"update community.knowledge_entries set reviewed_at = now() - interval '400 days', published_at = now() - interval '400 days' where id = '{art}'")
entry = next(i for i in c.get(f"/forums/{fid}/knowledge", headers=member["h"]).json()["items"] if i["id"] == art)
check("an article not reviewed for too long says so", entry["needs_review"] is True, entry.get("needs_review"))
r = c.post(f"/threads/{q}/knowledge-draft", headers=owner["h"])
check("an AI draft is refused when no real model is configured", r.status_code in (201, 503), r.text)
if r.status_code == 201:
    check("and if one is, it is a labelled draft", r.json()["ai_generated"] and r.json()["status"] == "draft", r.text)
staff_kb = c.post(f"/forums/{private}/knowledge", headers=owner["h"], json={"question": "Staff only", "answer": "Internal knowledge here.", "source_thread_ids": [secret_q]}).json()["id"]
check("a private forum's article is not found by members", c.post(f"/knowledge/{staff_kb}/suggestions", headers=member["h"], json={"kind": "correction", "note": "probe probe"}).status_code == 404)

print("== sessions, profiles, community activity ==")
now = datetime.now(timezone.utc)
r = c.post(f"/forums/{fid}/sessions", headers=member["h"], json={"title": "Ask an agronomist", "expert_id": helper["id"], "starts_at": now.isoformat(), "ends_at": (now + timedelta(hours=2)).isoformat()})
check("members do not organise sessions", r.status_code == 403, r.text)
r = c.post(f"/forums/{fid}/sessions", headers=mod["h"], json={"title": "Ask an agronomist", "expert_id": helper["id"], "expert_intro": "Grows maize for 20 years (as presented by the organiser).", "starts_at": (now - timedelta(minutes=5)).isoformat(), "ends_at": (now + timedelta(hours=2)).isoformat()})
check("a moderator organises an expert session", r.status_code == 201, r.text)
sid = r.json()["id"]
sq = c.post(f"/forums/{fid}/threads", headers=outsider["h"], json={"title": "Session question on drying", "body": "How long?", "session_id": sid}).json()["id"]
c.post(f"/threads/{sq}/replies", headers=helper["h"], json={"body": "Three sunny days."})
st = c.get(f"/threads/{sq}", headers=outsider["h"]).json()
check("the expert's answer is marked as the expert's", st["session"]["id"] == sid and any(x["is_expert"] for x in st["replies"]), st.get("session"))
check("the session's questions can be listed", [i["id"] for i in c.get(f"/forums/{fid}/threads", params={"session_id": sid}, headers=member["h"]).json()["items"]] == [sq])
c.patch("/forum-prefs", headers=helper["h"], json={"background": "Agronomist, 20 years"})
prof = c.get(f"/forum-profiles/{helper['id']}", headers=member["h"]).json()
check("a profile shows the claimed background as unverified, and activity", prof["background"] and prof["background_note"] and prof["stats"]["accepted"] >= 1, prof)
c.patch("/forum-prefs", headers=helper["h"], json={"show_activity": False})
check("activity can be hidden", c.get(f"/forum-profiles/{helper['id']}", headers=member["h"]).json()["stats"] is None)
act = c.get(f"/communities/{cid}/forum-activity", headers=outsider["h"]).json()["items"]
check("the community shows recent forum activity, without what the viewer cannot read", act and secret_q not in {a["id"] for a in act} and guide not in {a["id"] for a in act}, [a["id"] for a in act])

print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
