"""Editing an ordinary post's pictures and clips.

`media` on an edit is the list the post should carry now, in order: files it
already has are kept, new ones are checked as at creation (somebody else's is
refused), the ones left out are detached, and the format follows (a post with a
video is a video post). A text-only correction sends no `media` and touches no
attachment. The edit answers with links signed for the author, not for an
anonymous visitor.
"""
import random
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


def png(color: int) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = zlib.compress(bytes([0, color, 40, 200, 255]))
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


def upload(token, color):
    r = c.post("/media/upload", headers=H(token), files={"file": (f"p{color}.png", png(color), "image/png")})
    r.raise_for_status()
    return r.json()


def item(m):
    return {"media_id": m["id"], "url": m["url"], "kind": "image"}


def ids(post):
    return [m.get("media_id") for m in post.get("media", [])]


health = httpx.get("http://localhost:8203/health", timeout=5).json()
check("port 8203 is social-service", "social" in str(health), health)

me_t, me = register("Test Editor")
other_t, other = register("Test Other")

r = c.post("/posts", headers=H(me_t), json={"body": "Just words", "format": "text", "visibility": "public"})
pid = r.json()["id"]

a, b = upload(me_t, 10), upload(me_t, 120)
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "Just words, and two photos", "media": [item(a), item(b)]})
check("photos can be added to a text post by editing", r.status_code == 200 and ids(r.json()) == [a["id"], b["id"]], r.text[:200])
check("and it becomes a photo post", r.json().get("format") == "image", r.json().get("format"))
check("the links are signed for the author", all("v=anon" not in (m["url"] or "") for m in r.json()["media"]), r.json()["media"])

r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "Just words, fixed"})
check("a text correction leaves the photos", r.status_code == 200 and ids(r.json()) == [a["id"], b["id"]], r.text[:200])

r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "Reordered", "media": [item(b), item(a)]})
check("the order can change", ids(r.json()) == [b["id"], a["id"]], ids(r.json()))

r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "One left", "media": [item(b)]})
check("a photo can be taken off", ids(r.json()) == [b["id"]], ids(r.json()))

theirs = upload(other_t, 77)
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "Stolen", "media": [item(b), item(theirs)]})
check("somebody else's photo is refused", r.status_code == 400, r.text[:150])
check("and the refused edit changed nothing", ids(c.get(f"/posts/{pid}", headers=H(me_t)).json()) == [b["id"]])

many = [item(upload(me_t, 30 + i)) for i in range(5)]
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "Too many", "media": many})
check("no more than four", r.status_code == 400, r.text[:150])

r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "Words again", "media": []})
check("all photos can be taken off", r.status_code == 200 and ids(r.json()) == [], r.text[:150])
check("and it is a text post again", r.json().get("format") == "text", r.json().get("format"))

r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": "", "media": []})
check("an edit cannot leave a post with nothing", r.status_code in (400, 422), r.status_code)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
