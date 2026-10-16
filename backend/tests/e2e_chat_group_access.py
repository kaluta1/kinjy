"""Chat groups: public or private, a link that works either way, and pins.

A group's link is a random code, never its id. Public lets its holder in at
once; private turns it into a request that an admin answers. The welcome group
has no link and no privacy to change - everybody is in it already.

The age rule on links, in both directions: a minor never joins by link, and an
adult never joins by link a room with a minor in it. Both are refused in the
same words, so the refusal says nothing about who is inside.

Pins: three free, a fourth asks for this year's KYC fee.

The port is probed first: a 404 from the wrong service passes a "refused".
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


def register(name, born=date(1990, 2, 2)):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": born.isoformat(),
        "country": "UG",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


def connect(a_t, a, b_t, b):
    c.post(f"/connections/{b['id']}", headers=H(a_t), json={"message": None})
    c.post(f"/connections/{a['id']}/respond?accept=true", headers=H(b_t))


def group(owner_t, title, members):
    r = c.post("/conversations", headers=H(owner_t), json={
        "kind": "group", "title": title, "participant_ids": [m["id"] for m in members],
    })
    r.raise_for_status()
    return r.json()["id"]


def system_lines(token, cid):
    r = c.get(f"/conversations/{cid}/messages", headers=H(token))
    return [m.get("body") or "" for m in r.json().get("items", []) if m.get("kind") == "system"]


health = httpx.get("http://localhost:8207/health", timeout=5).json()
check("port 8207 is messaging-service", "messaging" in str(health), health)

print("Privacy and links")
owner_t, owner = register("Test Owner")
mate_t, mate = register("Test Mate")
walker_t, walker = register("Test Walker")
asker_t, asker = register("Test Asker")
connect(owner_t, owner, mate_t, mate)
gid = group(owner_t, f"Chat {tag()}", [mate])

listed = {x["id"]: x for x in c.get("/conversations", headers=H(owner_t)).json()["items"]}
check("a new group is private", listed[gid]["privacy"] == "private", listed[gid])

r = c.post(f"/conversations/{gid}/share-link", headers=H(mate_t))
check("any member can get the link", r.status_code == 200, r.text)
code, path = r.json()["code"], r.json()["path"]
check("the link is a code, not the group's id", gid not in path and path == f"/messages/join/{code}", path)
check("asking again gives the same link",
      c.post(f"/conversations/{gid}/share-link", headers=H(owner_t)).json()["code"] == code)
r = c.post(f"/conversations/{gid}/share-link", headers=H(walker_t))
check("an outsider cannot get the link from the id", r.status_code in (403, 404), r.text)

r = c.get(f"/conversations/join/{code}", headers=H(walker_t))
check("the link previews the group", r.status_code == 200 and r.json()["my_state"] == "none", r.text)
check("the preview carries no id and no members", r.json()["id"] is None and "participants" not in r.json(), r.json())
check("a made-up code leads nowhere", c.get(f"/conversations/join/x{code}", headers=H(walker_t)).status_code == 404)
check("signed out sees nothing", c.get(f"/conversations/join/{code}").status_code == 401)

# Private: the link asks.
r = c.post(f"/conversations/join/{code}", headers=H(asker_t))
check("a private group's link sends a request", r.json().get("status") == "pending", r.text)
r = c.get(f"/conversations/{gid}/messages", headers=H(asker_t))
check("a request does not open the messages", r.status_code in (403, 404), r.status_code)
check("asking twice is harmless", c.post(f"/conversations/join/{code}", headers=H(asker_t)).json()["status"] == "pending")
r = c.get(f"/conversations/{gid}/requests", headers=H(mate_t))
check("a plain member cannot see the requests", r.status_code == 403, r.text)
reqs = c.get(f"/conversations/{gid}/requests", headers=H(owner_t)).json()["items"]
check("the owner sees the request", [x["user_id"] for x in reqs] == [asker["id"]], reqs)
r = c.post(f"/conversations/{gid}/requests/{asker['id']}", headers=H(mate_t), json={"action": "approve"})
check("a plain member cannot answer it", r.status_code == 403, r.text)
r = c.post(f"/conversations/{gid}/requests/{asker['id']}", headers=H(owner_t), json={"action": "approve"})
check("the owner lets them in", r.status_code == 200 and r.json()["status"] == "member", r.text)
check("they can read the room now", c.get(f"/conversations/{gid}/messages", headers=H(asker_t)).status_code == 200)
check("the thread says who let them in", any("let Test Asker in" in l for l in system_lines(owner_t, gid)))

# Who may change it.
r = c.patch(f"/conversations/{gid}/privacy", headers=H(mate_t), json={"privacy": "public"})
check("a plain member cannot make it public", r.status_code == 403, r.text)
c.post(f"/conversations/join/{code}", headers=H(walker_t))  # waits at the door
r = c.patch(f"/conversations/{gid}/privacy", headers=H(owner_t), json={"privacy": "public"})
check("the owner makes it public", r.status_code == 200 and r.json()["privacy"] == "public", r.text)
check("whoever was waiting is let in", r.json().get("admitted") == 1, r.text)
check("the thread says it went public", any("made the group public" in l for l in system_lines(owner_t, gid)))

late_t, late = register("Test Late")
r = c.post(f"/conversations/join/{code}", headers=H(late_t))
check("a public group's link lets you straight in", r.json() == {"status": "member", "conversation_id": gid}, r.text)
check("a member's preview names the room", c.get(f"/conversations/join/{code}", headers=H(late_t)).json()["id"] == gid)

r = c.patch(f"/conversations/{gid}/privacy", headers=H(owner_t), json={"privacy": "private"})
check("and private again", r.json()["privacy"] == "private", r.text)
again_t, again = register("Test Again")
check("then the link asks once more", c.post(f"/conversations/join/{code}", headers=H(again_t)).json()["status"] == "pending")
r = c.post(f"/conversations/{gid}/requests/{again['id']}", headers=H(owner_t), json={"action": "reject"})
check("an admin can turn somebody away", r.json().get("status") == "rejected", r.text)
check("and they are not in", c.get(f"/conversations/{gid}/messages", headers=H(again_t)).status_code in (403, 404))

welcome = [x for x in c.get("/conversations", headers=H(owner_t)).json()["items"] if x.get("is_welcome")]
if welcome:
    w = welcome[0]["id"]
    check("the welcome group has no link", c.post(f"/conversations/{w}/share-link", headers=H(owner_t)).status_code in (400, 403))
else:
    print("  (no welcome group locally - skipped)")

print("Minors and links")
teen_t, teen = register("Test Teen", born=date(date.today().year - 15, 1, 1))
r = c.patch(f"/conversations/{gid}/privacy", headers=H(owner_t), json={"privacy": "public"})
r = c.post(f"/conversations/join/{code}", headers=H(teen_t))
check("a minor cannot join a public group by link", r.status_code == 403, r.text)
refusal_minor = r.json().get("detail")
check("and is not let in", c.get(f"/conversations/{gid}/messages", headers=H(teen_t)).status_code in (403, 404))

fam_t, fam = register("Test Parent")
connect(fam_t, fam, teen_t, teen)
fid = group(fam_t, f"Family {tag()}", [teen])
fcode = c.post(f"/conversations/{fid}/share-link", headers=H(fam_t)).json()["code"]
c.patch(f"/conversations/{fid}/privacy", headers=H(fam_t), json={"privacy": "public"})
stranger_t, stranger = register("Test Stranger")
r = c.post(f"/conversations/join/{fcode}", headers=H(stranger_t))
check("an adult cannot join by link a room with a minor in it", r.status_code == 403, r.text)
check("the two refusals read the same", r.json().get("detail") == refusal_minor, (r.json(), refusal_minor))
check("nothing in the refusal mentions age", "minor" not in r.text.lower() and "age" not in r.text.lower(), r.text)

c.patch(f"/conversations/{fid}/privacy", headers=H(fam_t), json={"privacy": "private"})
r = c.post(f"/conversations/join/{fcode}", headers=H(stranger_t))
check("a private room with a minor does not even take the request", r.status_code == 403, r.text)

print("Pins")
p_t, p = register("Test Pinner")
connect(p_t, p, mate_t, mate)
rooms = [group(p_t, f"Pin {i} {tag()}", [mate]) for i in range(4)]
for room in rooms[:3]:
    r = c.post(f"/conversations/{room}/pin", headers=H(p_t))
    check(f"pin {r.json().get('count')} of 3 is free", r.status_code == 200, r.text)
r = c.post(f"/conversations/{rooms[3]}/pin", headers=H(p_t))
check("a fourth pin asks for the KYC fee", r.status_code == 402 and "KYC" in r.text, r.text)
items = c.get("/conversations", headers=H(p_t)).json()["items"]
check("pinned groups come first, in pin order", [x["id"] for x in items[:3]] == rooms[:3], [x["id"] for x in items[:4]])
check("and say they are pinned", all(x["pinned"] for x in items[:3]) and not any(x["pinned"] for x in items[3:]))
r = c.post(f"/conversations/{gid}/pin", headers=H(p_t))
check("a group you are not in cannot be pinned", r.status_code in (403, 404), r.text)
c.delete(f"/conversations/{rooms[0]}/pin", headers=H(p_t))
check("unpinning frees a slot", c.post(f"/conversations/{rooms[3]}/pin", headers=H(p_t)).status_code == 200)
c.delete(f"/conversations/{rooms[1]}/participants/{p['id']}", headers=H(p_t))  # leave
check("leaving drops the pin",
      c.post(f"/conversations/{rooms[0]}/pin", headers=H(p_t)).status_code == 200)
pay = c.post("/payments/checkout", headers=H(p_t), json={"purpose": "kyc_fee", "amount": "10.00"}).json()
c.post(f"/payments/{pay['payment_id']}/mock-settle", headers=H(p_t), json={})
extra = group(p_t, f"Pin extra {tag()}", [mate])
r = c.post(f"/conversations/{extra}/pin", headers=H(p_t))
check("with the fee paid, a fourth pin goes through", r.status_code == 200 and r.json()["count"] == 4, r.text)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
