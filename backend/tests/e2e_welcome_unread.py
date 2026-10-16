"""A new member starts with nothing unread.

The welcome group holds everybody and writes "X joined Kinjy" for every
registration. Before: a new account opened Messages on hundreds of unread
lines it had never been sent - the room's whole history, and every arrival
after its own. Now history before you arrived is not unread for you, and the
room's own lines are never unread. Somebody actually writing in the room
still counts, which is what proves the badge is not simply switched off.
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


def unread(token):
    return c.get("/conversations/unread-count", headers=H(token)).json()


health = httpx.get("http://localhost:8207/health", timeout=5).json()
check("port 8207 is messaging-service", "messaging" in str(health), health)

new_t, new = register("Test Newcomer")
items = c.get("/conversations", headers=H(new_t)).json()["items"]
welcome = next((x for x in items if x.get("is_welcome")), None)
check("a new member is in the welcome group", welcome is not None, [x.get("title") for x in items])
history = len(c.get(f"/conversations/{welcome['id']}/messages?limit=100", headers=H(new_t)).json()["items"]) if welcome else 0
check("the welcome group has history before them", history > 1, history)
check("and yet nothing is unread", unread(new_t) == {"messages": 0, "conversations": 0}, unread(new_t))

register("Test Next Arrival")
check("somebody else arriving is not unread either", unread(new_t)["messages"] == 0, unread(new_t))

other_t, other = register("Test Talker")
r = c.post(f"/conversations/{welcome['id']}/messages", headers=H(other_t), json={"body": "Hello everyone"})
check("a member can write in the welcome group", r.status_code in (200, 201), r.text[:120])
check("and that does count as unread", unread(new_t) == {"messages": 1, "conversations": 1}, unread(new_t))
row = {x["id"]: x for x in c.get("/conversations", headers=H(new_t)).json()["items"]}[welcome["id"]]
check("the list says the same", row.get("unread") == 1, row.get("unread"))

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
