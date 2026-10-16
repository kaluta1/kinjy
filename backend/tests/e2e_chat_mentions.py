"""@mentions in chat.

A member named in a message is told, with a link to the conversation and no
quote of the message. Somebody outside the conversation is never told, even if
their handle is typed: a mention is not a way to reach a person the room's
rules keep out. An email address is not a mention. Your own handle does not
notify you. In the welcome group, where ordinary traffic notifies nobody, a
mention still does.
"""
import random
import string
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=30)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG",
    })
    r.raise_for_status()
    return r.json()["tokens"]["access_token"], r.json()["user"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


def connect(a_t, a, b_t, b):
    c.post(f"/connections/{b['id']}", headers=H(a_t), json={"message": None})
    c.post(f"/connections/{a['id']}/respond?accept=true", headers=H(b_t))


def mentions(token):
    items = c.get("/notifications", headers=H(token)).json().get("items", [])
    return [n for n in items if n.get("kind") == "mention"]


health = httpx.get("http://localhost:8207/health", timeout=5).json()
check("port 8207 is messaging-service", "messaging" in str(health), health)

a_t, a = register("Test Author")
b_t, b = register("Test Named")
x_t, x = register("Test Outsider")
connect(a_t, a, b_t, b)
room = c.post("/conversations", headers=H(a_t), json={
    "kind": "group", "title": "Mention room", "participant_ids": [b["id"]], "encrypted": False}).json()["id"]

r = c.post(f"/conversations/{room}/messages", headers=H(a_t),
           json={"body": f"Hello @{b['handle']}, and @{x['handle']} too. Mail me at a@{b['handle']}.com"})
check("the message is sent", r.status_code == 201, r.text)

got = mentions(b_t)
check("the member named is told", len(got) == 1, got)
check("with the author's name and the room", got and "Test Author mentioned you in Mention room" == got[0]["title"], got)
check("linking to the conversation", got and got[0]["link"] == f"/messages?c={room}", got)
check("without quoting the message", got and not got[0].get("body"), got)
check("somebody outside the room is not told", mentions(x_t) == [], mentions(x_t))

c.post(f"/conversations/{room}/messages", headers=H(a_t), json={"body": f"note to self @{a['handle']}"})
check("your own handle does not notify you", mentions(a_t) == [], mentions(a_t))

c.post(f"/conversations/{room}/messages", headers=H(a_t), json={"body": f"Again @{b['handle']}."})
check("a trailing full stop still finds the handle", len(mentions(b_t)) == 2, mentions(b_t))

welcome = next((w for w in c.get("/conversations", headers=H(a_t)).json()["items"] if w.get("is_welcome")), None)
if welcome:
    c.post(f"/conversations/{welcome['id']}/messages", headers=H(a_t), json={"body": f"Welcome @{x['handle']}!"})
    got = mentions(x_t)
    check("in the welcome group a mention is still delivered", len(got) == 1 and got[0]["link"].endswith(welcome["id"]), got)
else:
    print("  (no welcome group locally - skipped)")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
