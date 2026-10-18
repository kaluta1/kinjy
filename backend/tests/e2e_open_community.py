"""An open community that publishes publicly.

Its content is readable by anybody — on its page, through a link to one of its
posts, and as a preview when that link is shared off Kinjy. Joining is what
lets somebody take part: like, comment, react to a comment.

The two halves are the whole point and are tested against each other. Reading
without joining must work; writing without joining must not. A door that opens
for reading and quietly opens for writing too is the bug this is here to catch.
"""
import random
import re
import string
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
SOCIAL = "http://localhost:8203"
c = httpx.Client(base_url=BASE, timeout=60)
anon = httpx.Client(timeout=30)
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


def H(token=None):
    return {"Authorization": f"Bearer {token}"} if token else {}


def make_community(token, kind="public"):
    r = c.post("/communities", headers=H(token), json={
        "name": "Open Demo", "slug": "open-" + tag(), "kind": kind, "description": "A demo.",
    })
    r.raise_for_status()
    return r.json()["id"]


owner_t, owner = register("Demo Owner")
outsider_t, outsider = register("Demo Outsider")

community = make_community(owner_t)
SENTENCE = "A distinctive sentence nobody else writes."
post = c.post("/posts", headers=H(owner_t), json={
    "body": SENTENCE, "visibility": "community", "community_id": community,
}).json()["id"]
comment = c.post(f"/posts/{post}/comments", headers=H(owner_t), json={"body": "A comment."}).json()["id"]

print("Reading without joining")

check("a visitor can read the community's page",
      c.get(f"/feed/community/{community}").status_code == 200)
check("and open a link to one of its posts",
      c.get(f"/posts/{post}").status_code == 200)
check("a signed-in non-member can too",
      c.get(f"/posts/{post}", headers=H(outsider_t)).status_code == 200)

# The page says whether this reader may take part, so it can draw "Join to
# take part" instead of controls that would be refused.
page = c.get(f"/feed/community/{community}", headers=H(outsider_t)).json()
check("the page says a non-member cannot take part", page.get("can_engage") is False, str(page.get("can_engage")))
check("and that the community publishes publicly",
      page["community"].get("public_content") is True, str(page["community"]))
check("while it says a member can",
      c.get(f"/feed/community/{community}", headers=H(owner_t)).json().get("can_engage") is True)

print()
print("Taking part needs joining")

for label, path, payload in (
    ("like", f"/posts/{post}/like", None),
    ("react", f"/posts/{post}/react", {"kind": "love"}),
    ("comment", f"/posts/{post}/comments", {"body": "me too"}),
    ("react to a comment", f"/comments/{comment}/reaction", {"sticker_id": None}),
):
    r = c.post(path, headers=H(outsider_t), json=payload)
    check(f"a non-member cannot {label}", r.status_code == 403, f"{r.status_code} {r.text[:90]}")
    # 403 and not 404: they are looking at the post, so "not found" would be a
    # bug report. "Join to take part" is something they can act on.
    check(f"and is told to join, not that it is missing ({label})",
          "join" in r.text.lower(), r.text[:90])

check("a member can like", c.post(f"/posts/{post}/like", headers=H(owner_t)).status_code == 200)
check("a member can comment",
      c.post(f"/posts/{post}/comments", headers=H(owner_t), json={"body": "mine"}).status_code == 201)

print()
print("Sharing off Kinjy")

r = anon.get(f"{SOCIAL}/share/p/{post}", headers={"User-Agent": "facebookexternalhit/1.1"})
described = re.search(r'og:description" content="([^"]*)"', r.text)
check("a crawler gets a preview carrying the post's words",
      bool(described) and SENTENCE[:30] in described.group(1), str(described and described.group(1)[:70]))

print()
print("Closing the content closes every door")

r = c.patch(f"/communities/{community}/public-content", headers=H(owner_t), json={"public_content": False})
check("the owner can turn it off", r.status_code == 200 and r.json()["public_content"] is False,
      f"{r.status_code} {r.text[:90]}")

check("the page closes to a visitor", c.get(f"/feed/community/{community}").status_code == 404)
check("the link closes to a visitor", c.get(f"/posts/{post}").status_code == 404)
check("and to a signed-in non-member",
      c.get(f"/posts/{post}", headers=H(outsider_t)).status_code == 404)
r = anon.get(f"{SOCIAL}/share/p/{post}", headers={"User-Agent": "facebookexternalhit/1.1"})
check("the share preview stops carrying the words", SENTENCE[:30] not in r.text, "the post leaked into the preview")
check("members are unaffected", c.get(f"/feed/community/{community}", headers=H(owner_t)).status_code == 200)

print()
print("Only an open community can publish publicly")

r = c.patch(f"/communities/{community}/public-content", headers=H(owner_t), json={"public_content": True})
check("it can be turned back on", r.status_code == 200 and r.json()["public_content"] is True)
check("a visitor reads it again", c.get(f"/posts/{post}").status_code == 200)

# Closing the community itself has to close its content with it, whatever the
# content switch still says - otherwise "private" would mean nothing.
r = c.patch(f"/communities/{community}/privacy", headers=H(owner_t), json={"kind": "private"})
check("the community can be made private", r.status_code == 200, f"{r.status_code} {r.text[:90]}")
check("its content closes even though the switch is still on",
      c.get(f"/posts/{post}").status_code == 404, "a private community's post stayed public")
check("and its page closes too", c.get(f"/feed/community/{community}").status_code == 404)

r = c.patch(f"/communities/{community}/public-content", headers=H(owner_t), json={"public_content": True})
check("the switch is refused on a private community", r.status_code == 409, f"{r.status_code} {r.text[:90]}")

print()
print("Only stewards may change it")

back = c.patch(f"/communities/{community}/privacy", headers=H(owner_t), json={"kind": "public"})
r = c.patch(f"/communities/{community}/public-content", headers=H(outsider_t), json={"public_content": False})
check("an outsider cannot change it", r.status_code in (403, 404), f"{r.status_code} {r.text[:90]}")

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
