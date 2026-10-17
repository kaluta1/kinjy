"""Links to Kinjy, previewed inside and outside.

Inside (a link in a chat or a post): a link to a Kinjy post or group is
answered for the member looking - their access, their signed links - and not
fetched as a stranger and cached for a week, which is how every Kinjy link
became Kinjy's generic card.

Outside (crawlers): the User-Agent route Caddy uses (/api/share/p/<id>) gives
the same answer as everybody else's (/api/og/p/<id>). A public post held back
only for its picture still shows its words and its author's picture.
"""
import random
import re
import string
import struct
import sys
import zlib
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


def png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = zlib.compress(bytes([0, 200, 90, 30, 255]))
    return (bytes([137, 80, 78, 71, 13, 10, 26, 10]) + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


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


def meta(html):
    return dict(re.findall(r'<meta property="og:(title|description|image)" content="([^"]*)"', html))


health = httpx.get("http://localhost:8203/health", timeout=5).json()
check("port 8203 is social-service", "social" in str(health), health)

author_t, author = register("Test Author")
reader_t, reader = register("Test Reader")

up = c.post("/media/upload", headers=H(author_t), files={"file": ("p.png", png(), "image/png")}).json()
pending = c.post("/posts", headers=H(author_t), json={
    "body": "Our family picnic by the river", "format": "image", "visibility": "public",
    "media": [{"media_id": up["id"], "url": up["url"], "kind": "image"}]}).json()

print("Outside: crawlers and everybody else agree")
crawler = httpx.get(f"{BASE}/share/p/{pending['id']}", timeout=30)
browser = httpx.get(f"{BASE}/og/p/{pending['id']}", timeout=30)
check("the crawler route answers", crawler.status_code == 200, crawler.status_code)
check("with the same tags as the other route", meta(crawler.text) == meta(browser.text), (meta(crawler.text), meta(browser.text)))
m = meta(crawler.text)
check("a post waiting for review still shows its words", m.get("description") == "Our family picnic by the river", m)
check("and is titled after its author", m.get("title") == "Test Author on Kinjy", m)
check("but not its picture yet", "/api/og/image/" not in m.get("image", ""), m)

print("Inside: a link to a post, for the member looking")
link = f"https://kinjy.com/p/{pending['id']}"
r = c.get("/link-preview", params={"url": link}, headers=H(author_t))
check("the author's own link previews with the post's picture", r.status_code == 200 and "/media/" in (r.json().get("image") or ""), r.text[:200])
check("and its words", r.json().get("description") == "Our family picnic by the river", r.json())
r2 = c.get("/link-preview", params={"url": link}, headers=H(reader_t))
check("an adult reader sees it as in their feed (unreviewed is restricted for minors only)",
      "/media/" in (r2.json().get("image") or ""), r2.json())
check("with a link signed for them, not shared from another member's answer",
      r.json().get("image") != r2.json().get("image"), (r.json(), r2.json()))
d = date.today()
t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
teen = c.post("/auth/register", json={
    "email": f"{t}@example.com", "handle": f"u{t}", "display_name": "Test Teen",
    "password": "Sup3rStrong!Pass", "date_of_birth": date(d.year - 15, 1, 1).isoformat(), "country": "UG",
}).json()["tokens"]["access_token"]
r3 = c.get("/link-preview", params={"url": link}, headers=H(teen))
check("a minor gets a card that says they cannot see it", r3.json().get("description") == "You cannot see this post.", r3.json())
check("with no picture", not r3.json().get("image"), r3.json())

text_post = c.post("/posts", headers=H(author_t), json={"body": "Plain words for everyone", "format": "text", "visibility": "public"}).json()
r = c.get("/link-preview", params={"url": f"https://kinjy.com/p/{text_post['id']}?ref=ABCDEFGH"}, headers=H(reader_t))
check("a public text post previews for any member, a ?ref= and all", r.json().get("description") == "Plain words for everyone", r.json())

followers = c.post("/posts", headers=H(author_t), json={"body": "For my followers", "format": "text", "visibility": "followers"}).json()
r = c.get("/link-preview", params={"url": f"https://kinjy.com/p/{followers['id']}"}, headers=H(reader_t))
check("a followers-only post does not leak to a non-follower", "For my followers" not in str(r.json()), r.json())

print("Inside: a group invitation link")
connect = c.post(f"/connections/{reader['id']}", headers=H(author_t), json={"message": None})
c.post(f"/connections/{author['id']}/respond?accept=true", headers=H(reader_t))
gid = c.post("/conversations", headers=H(author_t), json={"kind": "group", "title": "Picnic planning",
                                                         "description": "Who brings what", "participant_ids": [reader["id"]]}).json()["id"]
code = c.post(f"/conversations/{gid}/share-link", headers=H(author_t)).json()["code"]
r = c.get("/link-preview", params={"url": f"https://kinjy.com/messages/join/{code}"}, headers=H(reader_t))
check("a group link previews with the group's name", r.json().get("title") == "Picnic planning", r.json())
check("and what it is for", r.json().get("description") == "Who brings what", r.json())
r = c.get("/link-preview", params={"url": "https://kinjy.com/messages/join/nope-nope"}, headers=H(reader_t))
check("a dead group link says so", "does not lead anywhere" in (r.json().get("description") or ""), r.json())

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
