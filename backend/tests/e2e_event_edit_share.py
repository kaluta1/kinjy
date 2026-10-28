"""Community events: edit, own address, community page and creator's profile (28/10).

    python backend/tests/e2e_event_edit_share.py
"""
import random
import string
import sys
from datetime import date, datetime, timedelta, timezone

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
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


def later(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


owner, member, outsider = register("Demo Host"), register("Demo Member"), register("Demo Outsider")
run = tag()
pub = c.post("/communities", headers=owner["h"], json={"name": f"Demo Events {run}", "kind": "public"}).json()
priv = c.post("/communities", headers=owner["h"], json={"name": f"Demo Private {run}", "kind": "private"}).json()
for cm in (pub, priv):
    c.patch(f"/communities/{cm['id']}/spaces", headers=owner["h"], json={"spaces": {"events": True}}).raise_for_status()
c.post(f"/communities/{pub['id']}/join", headers=member["h"])

print("== create, open by its own address ==")
ev = c.post(f"/communities/{pub['id']}/events", headers=owner["h"], json={
    "title": "Demo meetup", "starts_at": later(3), "timezone": "Africa/Kampala", "capacity": 10, "description": "First"}).json()
check("created with an id", ev.get("id", "").startswith("evt_"), ev)
got = c.get(f"/events/{ev['id']}", headers=member["h"])
check("a member opens it by its address", got.status_code == 200 and got.json()["title"] == "Demo meetup", got.text)
check("it names its community, with the slug for its page",
      (got.json().get("community") or {}).get("slug") == pub["slug"], got.json().get("community"))
check("the host may edit it", c.get(f"/events/{ev['id']}", headers=owner["h"]).json().get("can_edit") is True)
check("a member may not", got.json().get("can_edit") is False)
check("a signed-out visitor can open a public community's event", httpx.get(f"http://localhost:8200/api/events/{ev['id']}").status_code == 200)

print("== edit ==")
reg = c.post(f"/events/{ev['id']}/register", headers=member["h"])
check("a member registers", reg.status_code == 200, reg.text)
r = c.patch(f"/events/{ev['id']}", headers=owner["h"], json={"title": "Demo meetup (moved)", "starts_at": later(5),
                                                              "location": "Demo hall", "mode": "in_person", "description": "Second"})
check("the host edits title, time, place", r.status_code == 200 and r.json()["title"] == "Demo meetup (moved)" and r.json()["location"] == "Demo hall", r.text)
again = c.get(f"/events/{ev['id']}", headers=member["h"]).json()
check("the edit is what everyone now sees", again["title"] == "Demo meetup (moved)" and again["description"] == "Second", again)
check("the registration is kept", again["my_status"] == "registered" and again["registered_count"] == 1, again)
r = c.patch(f"/events/{ev['id']}", headers=member["h"], json={"title": "Hijacked"})
check("a member cannot edit it (direct API)", r.status_code == 403, r.text)
r = c.patch(f"/events/{ev['id']}", headers=outsider["h"], json={"title": "Hijacked"})
check("an outsider cannot edit it", r.status_code in (403, 404), r.text)
check("the title is unchanged after those attempts", c.get(f"/events/{ev['id']}", headers=owner["h"]).json()["title"] == "Demo meetup (moved)")
r = c.patch(f"/events/{ev['id']}", headers=owner["h"], json={"starts_at": later(-1)})
check("a start in the past is refused", r.status_code == 400, r.text)
r = c.patch(f"/events/{ev['id']}", headers=owner["h"], json={"ends_at": later(1)})
check("an end before the start is refused", r.status_code == 400, r.text)
r = c.patch(f"/events/{ev['id']}", headers=owner["h"], json={"title": "x"})
check("a too-short title is refused", r.status_code == 422, r.text)
r = c.patch(f"/events/{ev['id']}", headers=owner["h"], json={"online_url": "http://not-secure"})
check("a non-https joining link is refused", r.status_code == 422, r.text)
full = c.post(f"/communities/{pub['id']}/events", headers=owner["h"], json={
    "title": "Demo small", "starts_at": later(4), "timezone": "UTC", "capacity": 1}).json()
c.post(f"/events/{full['id']}/register", headers=member["h"])
r = c.patch(f"/events/{full['id']}", headers=owner["h"], json={"capacity": 0})
check("places below zero refused", r.status_code == 422, r.text)
waiting = register("Demo Waiting")
c.post(f"/communities/{pub['id']}/join", headers=waiting["h"])
w = c.post(f"/events/{full['id']}/register", headers=waiting["h"]).json()
check("a second member waits when full", w.get("my_status") == "waitlisted", w)
r = c.patch(f"/events/{full['id']}", headers=owner["h"], json={"capacity": 2})
check("more places let the waitlist in", r.status_code == 200 and
      c.get(f"/events/{full['id']}", headers=waiting["h"]).json()["my_status"] == "registered", r.text)
r = c.patch(f"/events/{full['id']}", headers=owner["h"], json={"capacity": 1})
check("places cannot go below the people registered", r.status_code == 400, r.text)

print("== visibility: community page, creator's profile, private events ==")
listed = [e["id"] for e in c.get(f"/communities/{pub['id']}/events", headers=member["h"]).json()["items"]]
check("listed in the community's events", ev["id"] in listed)
hosted = [e["id"] for e in c.get(f"/events/by-host/{owner['id']}", headers=outsider["h"]).json()["items"]]
check("on the host's profile, for anybody (public community)", ev["id"] in hosted, hosted)
secret_ev = c.post(f"/communities/{priv['id']}/events", headers=owner["h"], json={
    "title": "Demo private meetup", "starts_at": later(3), "timezone": "UTC"}).json()
r = c.get(f"/events/{secret_ev['id']}", headers=outsider["h"])
check("a private community's event: its link shows nothing to a non-member", r.status_code == 404, r.status_code)
check("and it is not on the host's profile for them",
      secret_ev["id"] not in [e["id"] for e in c.get(f"/events/by-host/{owner['id']}", headers=outsider["h"]).json()["items"]])
check("the host still sees it on their profile",
      secret_ev["id"] in [e["id"] for e in c.get(f"/events/by-host/{owner['id']}", headers=owner["h"]).json()["items"]])
c.post(f"/events/{full['id']}/cancel", headers=owner["h"])
check("a cancelled event leaves the profile",
      full["id"] not in [e["id"] for e in c.get(f"/events/by-host/{owner['id']}", headers=outsider["h"]).json()["items"]])
r = c.patch(f"/events/{full['id']}", headers=owner["h"], json={"title": "Back again"})
check("a cancelled event cannot be edited", r.status_code == 409, r.text)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
