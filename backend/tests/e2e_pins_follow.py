"""Pinned groups behind the KYC fee, and a Follow button that remembers.

Pins: three groups are free; a fourth answers 402 until the year's
verification fee is paid, and the fee now costs what it says - a checkout for
one cent used to mark the year as paid. Pins belong to members: an outsider
cannot pin, leaving drops the pin, and a ban hides it.

Follow: /connections/with now says whether the viewer already follows, which
is what the profile page starts from. Without it every profile reached from a
notification or from Connections showed "Follow" for somebody followed weeks
ago.

Each refusal is checked against the right service first: a 404 from the wrong
one would pass the same assertion.
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


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def register(name):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(),
        "country": "UG",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


for port, name in ((8204, "community"), (8202, "user")):
    try:
        health = httpx.get(f"http://localhost:{port}/health", timeout=5).json()
    except Exception as exc:
        health = str(exc)
    check(f"port {port} is {name}-service", name in str(health), health)

print("Pinned groups")
me_t, me = register("Test Pinner")
other_t, other = register("Test Other")

groups = []
for i in range(5):
    g = c.post("/communities", headers=H(other_t), json={"name": f"Pin {i} {tag()}", "kind": "public"}).json()
    c.post(f"/communities/{g['id']}/join", headers=H(me_t))
    groups.append(g)

outside = c.post("/communities", headers=H(other_t), json={"name": f"Outside {tag()}", "kind": "public"}).json()
r = c.post(f"/communities/{outside['id']}/pin", headers=H(me_t))
check("a group you are not in cannot be pinned", r.status_code == 404, r.text)
secret = c.post("/communities", headers=H(other_t), json={"name": f"Hidden {tag()}", "kind": "secret"}).json()
r = c.post(f"/communities/{secret['id']}/pin", headers=H(me_t))
check("pinning does not reveal a secret group (same 404)", r.status_code == 404 and "Join this group" in r.text, r.text)
check("signed out cannot pin", c.post(f"/communities/{groups[0]['id']}/pin").status_code == 401)

for g in groups[:3]:
    r = c.post(f"/communities/{g['id']}/pin", headers=H(me_t))
    check(f"pin {r.json().get('count')} of 3 is free", r.status_code == 200, r.text)
r = c.post(f"/communities/{groups[0]['id']}/pin", headers=H(me_t))
check("pinning twice is harmless", r.json().get("already") is True, r.text)

r = c.post(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
check("a fourth pin asks for the KYC fee", r.status_code == 402 and "KYC" in r.text, r.text)
check("and names the price", "$10.00" in r.text, r.text)

listed = c.get("/communities/pinned", headers=H(me_t)).json()
check("the pinned list has the three, oldest first",
      [x["id"] for x in listed["items"]] == [g["id"] for g in groups[:3]], listed)
check("and says three are free", listed["free_pins"] == 3, listed)
check("the group page knows it is pinned",
      c.get(f"/communities/{groups[0]['slug']}", headers=H(me_t)).json().get("my_pinned") is True)
check("and not for somebody else",
      c.get(f"/communities/{groups[0]['slug']}", headers=H(other_t)).json().get("my_pinned") is False)

# Unpinning frees a slot.
c.delete(f"/communities/{groups[2]['id']}/pin", headers=H(me_t))
r = c.post(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
check("unpinning frees a slot", r.status_code == 200, r.text)
c.delete(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
c.post(f"/communities/{groups[2]['id']}/pin", headers=H(me_t))

# Leaving drops the pin.
c.post(f"/communities/{groups[1]['id']}/leave", headers=H(me_t))
ids = [x["id"] for x in c.get("/communities/pinned", headers=H(me_t)).json()["items"]]
check("leaving a group drops its pin", groups[1]["id"] not in ids and len(ids) == 2, ids)
c.post(f"/communities/{groups[1]['id']}/join", headers=H(me_t))
c.post(f"/communities/{groups[1]['id']}/pin", headers=H(me_t))

# A ban hides it.
c.post(f"/communities/{groups[0]['id']}/members/{me['id']}", headers=H(other_t), json={"action": "ban"})
ids = [x["id"] for x in c.get("/communities/pinned", headers=H(me_t)).json()["items"]]
check("a ban hides the pin", groups[0]["id"] not in ids, ids)
r = c.post(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
check("and frees its slot", r.status_code == 200, r.text)
c.delete(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
c.post(f"/communities/{groups[0]['id']}/members/{me['id']}", headers=H(other_t), json={"action": "unban"})
r = c.post(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
check("unbanned, the old pin counts again", r.status_code == 402, r.text)

print("KYC fee")
r = c.post("/payments/checkout", headers=H(me_t), json={"purpose": "kyc_fee", "amount": "0.01"})
check("the fee cannot be paid for one cent", r.status_code == 400, r.text)
r = c.post("/payments/checkout", headers=H(me_t), json={"purpose": "kyc_fee", "amount": "100"})
check("nor for more than it costs", r.status_code == 400, r.text)
r = c.post("/payments/checkout", headers=H(me_t), json={"purpose": "kyc_fee", "amount": "10.00"})
check("the right price starts a payment", r.status_code == 201, r.text)
pid = r.json()["payment_id"]
r = c.post(f"/communities/{groups[3]['id']}/pin", headers=H(me_t))
check("a started, unpaid fee unlocks nothing", r.status_code == 402, r.text)
c.post(f"/payments/{pid}/mock-settle", headers=H(me_t), json={})
check("the fee reads as paid", c.get("/kyc/status", headers=H(me_t)).json()["fee_paid"] is True)
r = c.post("/payments/checkout", headers=H(me_t), json={"purpose": "kyc_fee", "amount": "10.00"})
check("it cannot be paid twice in a year", r.status_code == 409, r.text)
for g in groups[3:]:
    r = c.post(f"/communities/{g['id']}/pin", headers=H(me_t))
    check(f"with the fee paid, pin {r.json().get('count')} goes through", r.status_code == 200, r.text)
check("the pinned list has five", len(c.get("/communities/pinned", headers=H(me_t)).json()["items"]) == 5)
# The owner of all five groups has not paid: the fee is per member.
for g in groups[:3]:
    c.post(f"/communities/{g['id']}/pin", headers=H(other_t))
r = c.post(f"/communities/{groups[3]['id']}/pin", headers=H(other_t))
check("somebody else's paid fee does not unlock my fourth pin", r.status_code == 402, r.text)

print("Follow")
a_t, a = register("Test Follower")
b_t, b = register("Test Followed")
w = c.get(f"/connections/with/{b['id']}", headers=H(a_t)).json()
check("before: not following", w.get("following") is False and w.get("follows_you") is False, w)
r = c.post(f"/users/{b['id']}/follow", headers=H(a_t))
check("follow", r.status_code == 201, r.text)
w = c.get(f"/connections/with/{b['id']}", headers=H(a_t)).json()
check("a fresh page load knows it is followed", w.get("following") is True, w)
w = c.get(f"/connections/with/{a['id']}", headers=H(b_t)).json()
check("and the other side sees 'follows you'", w.get("follows_you") is True and w.get("following") is False, w)
r = c.post(f"/users/{b['id']}/follow", headers=H(a_t))
check("following again says already, without a second count", r.json().get("already") is True, r.text)
count = c.get(f"/users/{b['handle']}").json()["followers_count"]
check("the count is one, not two", count == 1, count)
c.delete(f"/users/{b['id']}/follow", headers=H(a_t))
w = c.get(f"/connections/with/{b['id']}", headers=H(a_t)).json()
check("unfollow is remembered too", w.get("following") is False, w)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
