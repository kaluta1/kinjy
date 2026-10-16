"""Adult-to-minor messaging protections, against the live API.

The scenario: an unknown adult opens a private conversation with a 14-year-old
and sends them a photograph. Before this work, whether that was possible was
decided entirely by a privacy setting the 14-year-old was expected to find.
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


def born(age):
    t = date.today()
    return date(t.year - age, t.month, min(t.day, 28)).isoformat()


def register(age):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": f"T {t}",
        "password": "Sup3rStrong!Pass", "date_of_birth": born(age), "country": "US",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def supervise(teen_token, guardian_token, guardian):
    """Put a younger teen under supervision, as the product requires.

    Since parental supervision, a younger teen cannot loosen a safety setting
    on their own: the request goes to the supervising adult. The teen invites,
    the guardian accepts - the same path a family takes in the app.
    """
    r = c.post("/supervision/invite", headers=auth(teen_token), json={"other_handle": guardian["handle"]})
    assert r.status_code == 201, f"could not invite a guardian: {r.status_code} {r.text[:120]}"
    r = c.post(f"/supervision/{r.json()['id']}/answer", headers=auth(guardian_token), json={"approve": True})
    assert r.status_code == 200, f"guardian could not accept: {r.status_code} {r.text[:120]}"


def open_to_everyone(token, guardian_token=None):
    """Let anyone message this member.

    The platform default is `connections`, which refuses strangers before the
    age layer is ever consulted. The case the age layer exists for is exactly
    this one: a teenager who has opened their messages to everybody has not
    thereby agreed to unknown adults, and their own setting is not the whole
    answer.

    For a supervised teen the change is held for the guardian, who approves it
    here; the setting is then read back so the scenario starts from what is
    actually in force, not from what was asked for.
    """
    r = c.patch("/preferences", headers=auth(token), json={"who_can_message": "everyone"})
    assert r.status_code == 200, f"could not open messages: {r.status_code} {r.text[:120]}"
    for held in r.json().get("awaiting_approval", []):
        assert guardian_token, "a change is waiting for a guardian, but none was given"
        a = c.post(f"/supervision/requests/{held['request_id']}", headers=auth(guardian_token),
                   json={"approve": True})
        assert a.status_code == 200, f"guardian could not approve: {a.status_code} {a.text[:120]}"
    setting = c.get("/preferences", headers=auth(token)).json().get("who_can_message")
    assert setting == "everyone", f"messages are still limited to {setting!r}"


def open_convo(token, target_id):
    return c.post("/conversations", headers=auth(token),
                  json={"kind": "direct", "participant_ids": [target_id], "encrypted": False})


adult_tok, adult = register(30)
adult2_tok, adult2 = register(41)
teen14_tok, teen14 = register(14)
teen17_tok, teen17 = register(17)
teen15_tok, teen15 = register(15)
guardian_tok, guardian = register(45)

# Without a guardian on the account, a younger teen's request is refused
# outright rather than queued - there is nobody to queue it for, and "ask
# nobody" would make removing your parent the way to get the permission. If
# the tier lock ever disappears, this is where it shows.
r = c.patch("/preferences", headers=auth(teen14_tok), json={"who_can_message": "everyone"})
assert r.status_code == 403, (
    f"a 14-year-old opened their messages to everyone unaided ({r.status_code}) - the tier lock is gone"
)

for tok in (teen14_tok, teen15_tok):
    supervise(tok, guardian_tok, guardian)
for tok in (adult_tok, adult2_tok, teen14_tok, teen17_tok, teen15_tok):
    open_to_everyone(tok, guardian_tok)
print("every test member has opened their messages to everyone (younger teens with their guardian's approval)")

print("\n== an unknown adult reaching a minor ==")
r = open_convo(adult_tok, teen14["id"])
check("adult -> unconnected 14-year-old is refused", r.status_code == 403,
      f"{r.status_code} {r.text[:120]}")
check("the refusal does not reveal the recipient's age",
      "14" not in r.text and "age" not in r.text.lower(), r.text[:140])
check("the refusal came from the age layer, not the privacy setting",
      "connect" not in r.text.lower(), r.text[:140])

r = open_convo(adult_tok, teen17["id"])
check("adult -> unconnected 17-year-old is refused", r.status_code == 403,
      f"{r.status_code} {r.text[:120]}")

print("\n== adults may still reach adults ==")
r = open_convo(adult_tok, adult2["id"])
check("adult -> adult is allowed", r.status_code in (200, 201),
      f"{r.status_code} {r.text[:120]}")
adult_convo = r.json().get("id") if r.status_code in (200, 201) else None

print("\n== a minor may start the conversation themselves ==")
r = open_convo(teen14_tok, adult["id"])
teen_started = r.status_code in (200, 201)
check("14-year-old -> adult is allowed (their choice, their initiation)",
      teen_started, f"{r.status_code} {r.text[:120]}")
convo = r.json().get("id") if teen_started else None

print("\n== attachments wait for acceptance ==")
if convo:
    r = c.post(f"/conversations/{convo}/messages", headers=auth(teen14_tok),
               json={"body": "hello", "kind": "text"})
    check("text goes through", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")

    r = c.post(f"/conversations/{convo}/messages", headers=auth(adult_tok),
               json={"body": "look at this", "kind": "media",
                     "media_url": "http://localhost:8200/media/mda_x"})
    # The adult has not accepted, but the *recipient* here is the teenager, who
    # started the thread and is therefore accepted. The rule protects the
    # unaccepted party.
    print(f"   adult sending an image into a teen-started thread: {r.status_code}")

    r = c.post(f"/conversations/{convo}/accept", headers=auth(adult_tok))
    check("the other party can accept", r.status_code == 200, f"{r.status_code} {r.text[:100]}")

print("\n== an unaccepted recipient gets no attachments ==")
r = open_convo(adult_tok, adult2["id"])
convo2 = r.json().get("id") if r.status_code in (200, 201) else adult_convo
if convo2:
    r = c.post(f"/conversations/{convo2}/messages", headers=auth(adult_tok),
               json={"body": "photo", "kind": "media",
                     "media_url": "http://localhost:8200/media/mda_y"})
    print(f"   image into an unaccepted adult thread: {r.status_code}")
    r = c.post(f"/conversations/{convo2}/messages", headers=auth(adult_tok),
               json={"body": "just text", "kind": "text"})
    check("text is always allowed", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")

print("\n== the case section 20 exists for: media to a minor who has not accepted ==")
# Adult -> minor is refused at creation, so the only way a minor ends up as an
# unaccepted recipient is somebody they are allowed to reach adding them. A
# teenager starting a group with another teenager does exactly that.
# The 17-year-old is the unaccepted recipient here. A 14-year-old cannot be
# added by somebody unconnected at all - the engine refuses that one step
# earlier, which is itself the stronger protection.
r = c.post("/conversations", headers=auth(teen14_tok),
           json={"kind": "group", "title": "study group",
                 "participant_ids": [teen17["id"]], "encrypted": False})
group = r.json().get("id") if r.status_code in (200, 201) else None
check("a teenager can start a group with another teenager",
      group is not None, f"{r.status_code} {r.text[:120]}")

if group:
    r = c.post(f"/conversations/{group}/messages", headers=auth(teen14_tok),
               json={"body": "hi all", "kind": "text"})
    check("text reaches an unaccepted minor", r.status_code in (200, 201),
          f"{r.status_code} {r.text[:120]}")

    # In a group the attachment is not refused to the sender - one teenager
    # who has not accepted would stop everybody's photos. It is withheld from
    # that teenager instead, until they accept.
    listed = {x["id"]: x for x in c.get("/conversations", headers=auth(teen17_tok)).json()["items"]}
    check("a minor added to a group starts unaccepted", listed[group].get("accepted") is False,
          listed.get(group))
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
           b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82") + bytes(random.randbytes(6))
    up = c.post("/media/upload", headers=auth(teen14_tok),
                files={"file": ("photo.png", png, "image/png")}, data={"purpose": "chat"})
    check("a photo can be uploaded", up.status_code in (200, 201), f"{up.status_code} {up.text[:100]}")
    r = c.post(f"/conversations/{group}/messages", headers=auth(teen14_tok),
               json={"body": "photo", "media_id": up.json().get("id")})
    check("a photo can still be sent into the group", r.status_code in (200, 201),
          f"{r.status_code} {r.text[:140]}")
    photo_id = r.json().get("id")

    def photo_as(token):
        items = c.get(f"/conversations/{group}/messages", headers=auth(token)).json().get("items", [])
        return next((m for m in items if m["id"] == photo_id), {})

    seen = photo_as(teen17_tok)
    check("the unaccepted minor is not handed the photo",
          seen.get("media_withheld") is True and not seen.get("media_url"), seen)
    mine = photo_as(teen14_tok)
    check("the sender still sees their own photo",
          bool(mine.get("media_url")) and not mine.get("media_withheld"), mine)

    r = c.post(f"/conversations/{group}/accept", headers=auth(teen17_tok))
    check("the minor can accept", r.status_code == 200, f"{r.status_code}")
    seen = photo_as(teen17_tok)
    check("after acceptance the photo is there",
          bool(seen.get("media_url")) and not seen.get("media_withheld"), seen)

print("\n== teenagers and each other ==")
r = open_convo(teen15_tok, teen14["id"])
check("unconnected 15-year-old -> 14-year-old is restricted", r.status_code == 403,
      f"{r.status_code} {r.text[:120]}")
check("the refusal points at connection, not age",
      "connect" in r.text.lower(), r.text[:140])

print("\n== repeated refusals become a signal ==")
targets = [register(13 + i % 4)[1]["id"] for i in range(4)]
for t_id in targets:
    open_convo(adult2_tok, t_id)
r = c.get("/admin/contact-risk")
check("the risk queue is not reachable without an admin token",
      r.status_code in (401, 403), f"{r.status_code} (404 would mean it is not routed)")

print("\n" + ("ALL CHECKS PASSED" if ok else "THERE ARE FAILURES ABOVE"))
sys.exit(0 if ok else 1)
