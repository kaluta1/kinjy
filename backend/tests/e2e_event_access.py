"""Online events: who gets in, and who pays (29/10). Local stack, test rail.

    python backend/tests/e2e_event_access.py

The host and the presenters the host names take part without a ticket; a paid
attendee gets the joining link; an unpaid member does not; presenters of one
event are nobody at another; nobody names themselves a presenter. The paid
switch is turned on for the test and put back as it was.
"""
import random
import string
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=120)
ok = True
URL = "https://meet.example.com/demo-room"


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"], "handle": f"u{t}",
            "email": f"{t}@example.com"}


def later(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def ev(user, event_id):
    return c.get(f"/events/{event_id}", headers=user["h"]).json()


def quote(user, cid, event_id):
    return c.post(f"/communities/{cid}/quote", headers=user["h"], json={"event_id": event_id})


admin = register("Demo Admin")
psql(f"update auth.users set role='admin' where id='{admin['id']}'")
admin["h"] = {"Authorization": "Bearer " + c.post("/auth/login", json={
    "email": admin["email"], "password": "Sup3rStrong!Pass"}).json()["tokens"]["access_token"]}
was = psql("select value from community.platform_settings where key='community_paid_enabled'")
c.patch("/admin/community-settings", headers=admin["h"], json={"community_paid_enabled": True})

host, mod, speaker, buyer, member, stranger = (register(f"Demo {n}") for n in
                                               ("Host", "Moderator", "Speaker", "Buyer", "Member", "Stranger"))
try:
    cm = c.post("/communities", headers=host["h"], json={"name": f"Demo Classes {host['handle']}", "kind": "public"}).json()
    cid = cm["id"]
    c.patch(f"/communities/{cid}/spaces", headers=host["h"], json={"spaces": {"events": True}}).raise_for_status()
    for u in (mod, speaker, buyer, member):
        c.post(f"/communities/{cid}/join", headers=u["h"])
    psql(f"update community.memberships set role='moderator' where community_id='{cid}' and user_id='{mod['id']}'")
    paid_ev = c.post(f"/communities/{cid}/events", headers=host["h"], json={
        "title": "Demo paid class", "starts_at": later(24), "timezone": "Africa/Kampala", "capacity": 5,
        "ticket_price": 8, "online_url": URL}).json()
    other_ev = c.post(f"/communities/{cid}/events", headers=host["h"], json={
        "title": "Demo other class", "starts_at": later(48), "timezone": "UTC", "capacity": 5,
        "ticket_price": 8, "online_url": URL}).json()
    free_ev = c.post(f"/communities/{cid}/events", headers=host["h"], json={
        "title": "Demo free class", "starts_at": later(24), "timezone": "UTC", "online_url": URL}).json()
    check("(set-up: a paid and a free online event)", paid_ev.get("ticket_price") and free_ev.get("id"), (paid_ev, free_ev))

    print("== the host ==")
    e = ev(host, paid_ev["id"])
    check("the host is told they host it", e["my_role"] == "host", e)
    check("and gets the joining link without a ticket", e["online_url"] == URL and e["my_status"] is None)
    r = quote(host, cid, paid_ev["id"])
    check("no ticket can be sold to the host", r.status_code == 409 and "without a ticket" in r.text, r.text)
    r = c.post(f"/events/{paid_ev['id']}/register", headers=host["h"])
    check("'registering' as host: no charge, no place taken",
          r.status_code == 200 and r.json()["registered_count"] == 0 and psql(
              f"select count(*) from community.event_registrations where event_id='{paid_ev['id']}' and user_id='{host['id']}'") == "0",
          r.text)
    for _ in range(3):
        c.post(f"/events/{paid_ev['id']}/register", headers=host["h"])
    check("repeated: still nothing created", psql(f"select count(*) from community.event_registrations where event_id='{paid_ev['id']}'") == "0")

    print("== presenters ==")
    r = c.post(f"/events/{paid_ev['id']}/presenters", headers=speaker["h"], json={"handle": speaker["handle"]})
    check("a member cannot name themselves a presenter", r.status_code == 403, r.text)
    r = c.post(f"/events/{paid_ev['id']}/presenters", headers=host["h"], json={"handle": stranger["handle"]})
    check("somebody outside the community cannot be named", r.status_code == 404, r.text)
    r = c.post(f"/events/{paid_ev['id']}/presenters", headers=host["h"], json={"handle": "@" + speaker["handle"].upper()})
    check("the host names a member by @handle", r.status_code == 201 and [p["user_id"] for p in r.json()["items"]] == [speaker["id"]], r.text)
    c.post(f"/events/{paid_ev['id']}/presenters", headers=host["h"], json={"handle": speaker["handle"]})
    check("naming them twice: one presenter", psql(f"select count(*) from community.event_presenters where event_id='{paid_ev['id']}'") == "1")
    e = ev(speaker, paid_ev["id"])
    check("the presenter gets the joining link without a ticket", e["my_role"] == "presenter" and e["online_url"] == URL, e)
    check("but cannot edit the event", e["can_edit"] is False)
    check("no ticket is sold to a presenter", quote(speaker, cid, paid_ev["id"]).status_code == 409)
    r = c.post(f"/events/{paid_ev['id']}/presenters", headers=speaker["h"], json={"handle": member["handle"]})
    check("a presenter cannot name others", r.status_code == 403, r.text)
    check("nor see the attendee list", c.get(f"/events/{paid_ev['id']}/attendees", headers=speaker["h"]).status_code == 403)
    e2 = ev(speaker, other_ev["id"])
    check("presenting at one event gives nothing at another", e2["my_role"] is None and e2["online_url"] is None, e2)
    r = c.post(f"/events/{paid_ev['id']}/presenters", headers=mod["h"], json={"handle": member["handle"]})
    check("a moderator may name presenters too", r.status_code == 201, r.text)
    r = c.delete(f"/events/{paid_ev['id']}/presenters/{member['id']}", headers=mod["h"])
    check("and remove them", r.status_code == 200 and member["id"] not in [p["user_id"] for p in r.json()["items"]])
    check("removed: no link any more", ev(member, paid_ev["id"])["online_url"] is None)
    check("a stranger cannot read the presenter list", c.get(f"/events/{paid_ev['id']}/presenters", headers=stranger["h"]).status_code == 403)

    print("== attendees ==")
    e = ev(member, paid_ev["id"])
    check("an unpaid member gets no joining link", e["online_url"] is None and e["my_role"] is None, e)
    r = c.post(f"/events/{paid_ev['id']}/register", headers=member["h"])
    check("and cannot register around the ticket", r.status_code == 402, r.text)
    check("a stranger gets no joining link", ev(stranger, paid_ev["id"])["online_url"] is None)
    q = quote(buyer, cid, paid_ev["id"])
    check("an attendee is quoted the ticket", q.status_code == 200, q.text)
    pay = c.post("/payments/community/checkout", headers=buyer["h"], json={"quote_id": q.json()["quote_id"]}).json()
    c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=buyer["h"])
    e = ev(buyer, paid_ev["id"])
    check("paid: registered, with the joining link", e["my_status"] == "registered" and e["online_url"] == URL, e)

    print("== free event ==")
    r = c.post(f"/events/{free_ev['id']}/register", headers=member["h"])
    check("a member registers for free and gets the link", r.status_code == 200 and r.json()["online_url"] == URL, r.text)
    e = ev(host, free_ev["id"])
    check("the host of a free event: hosting, link, no registration", e["my_role"] == "host" and e["online_url"] == URL
          and e["my_status"] is None)

    print("== the start time ==")
    psql(f"update community.community_events set starts_at = now() - interval '1 second' where id='{paid_ev['id']}'")
    check("at the start, ticket sales close", quote(member, cid, paid_ev["id"]).status_code == 409)
    for who, name in ((host, "host"), (speaker, "presenter"), (buyer, "paid attendee")):
        check(f"the {name} still has the joining link once it has started", ev(who, paid_ev["id"])["online_url"] == URL)
    check("an unpaid member still does not", ev(member, paid_ev["id"])["online_url"] is None)
    e = ev(host, paid_ev["id"])
    check("the start time is returned as an instant (UTC offset), whatever the event's zone",
          e["starts_at"].endswith(("+00:00", "Z")) and e["timezone"] == "Africa/Kampala", e["starts_at"])
finally:
    c.patch("/admin/community-settings", headers=admin["h"], json={"community_paid_enabled": was == "true"})

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
