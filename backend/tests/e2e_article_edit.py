"""Editing an article, pictures included.

An article is edited as a whole document: its text, and the pictures and clips
placed in it. A picture added by an edit is attached and checked like one
posted at creation - so somebody else's file cannot be placed - and makes the
post wait for a human again. A picture taken out of the text is detached. A
plain correction (no `media` sent) leaves the attachments alone.

The port is probed first: a 404 from the wrong service passes a "refused".
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
    raw = zlib.compress(bytes([0, color, 120, 30, 255]))
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


def fig(media_id):
    return f'<figure data-media-id="{media_id}" data-kind="image"></figure>'


def ids_of(post):
    return [m.get("media_id") for m in post.get("media", [])]


health = httpx.get("http://localhost:8203/health", timeout=5).json()
check("port 8203 is social-service", "social" in str(health), health)

me_t, me = register("Test Writer")
other_t, other = register("Test Other")
first = upload(me_t, 10)
r = c.post("/posts", headers=H(me_t), json={
    "body": f"<h2>Title</h2><p>Intro</p>{fig(first['id'])}<p>End</p>", "format": "article", "visibility": "public",
    "media": [{"media_id": first["id"], "url": first["url"], "kind": "image"}],
})
check("an article with a picture is posted", r.status_code == 201, r.text[:150])
post = r.json()
pid = post["id"]
check("its media says which file it is", ids_of(post) == [first["id"]], post.get("media"))

# A plain correction: text only, attachments untouched.
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": f"<h2>Title</h2><p>Intro, fixed</p>{fig(first['id'])}<p>End</p>"})
check("a text correction saves", r.status_code == 200 and "fixed" in r.json()["body"], r.text[:150])
check("and leaves the picture attached", ids_of(r.json()) == [first["id"]], r.json().get("media"))

# Add a second picture by editing.
second = upload(me_t, 200)
body2 = f"<h2>Title</h2><p>Intro</p>{fig(first['id'])}<p>Middle</p>{fig(second['id'])}<p>End</p>"
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={
    "body": body2, "media": [{"media_id": second["id"], "url": second["url"], "kind": "image"}]})
check("a picture can be added by an edit", r.status_code == 200, r.text[:150])
check("and it is attached, after the first", ids_of(r.json()) == [first["id"], second["id"]], r.json().get("media"))
check("with a signed link for the author", all(m.get("url") for m in r.json()["media"]), r.json().get("media"))

# Somebody else's file cannot be placed.
theirs = upload(other_t, 90)
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={
    "body": body2 + fig(theirs["id"]), "media": [{"media_id": theirs["id"], "url": theirs["url"], "kind": "image"}]})
check("somebody else's picture is refused", r.status_code == 400, r.text[:150])
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={"body": body2 + fig(theirs["id"]), "media": []})
check("and so is one placed without being offered", r.status_code == 400, r.text[:150])
check("the refused edit changed nothing",
      ids_of(c.get(f"/posts/{pid}", headers=H(me_t)).json()) == [first["id"], second["id"]])

# Take the first picture out of the text.
r = c.patch(f"/posts/{pid}", headers=H(me_t), json={
    "body": f"<h2>Title</h2><p>Intro</p><p>Middle</p>{fig(second['id'])}<p>End</p>", "media": []})
check("a picture taken out of the text is detached", r.status_code == 200 and ids_of(r.json()) == [second["id"]],
      r.json().get("media") if r.status_code == 200 else r.text[:150])

# Somebody else cannot edit it at all.
r = c.patch(f"/posts/{pid}", headers=H(other_t), json={"body": "<h2>Mine now</h2>", "media": []})
check("another member cannot edit it", r.status_code == 404, r.text[:150])

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
