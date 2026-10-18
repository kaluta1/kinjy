"""Pasting an article copied from Kinjy keeps its pictures - the writer's own.

An article's pictures are markers naming files, not the files. A post may only
carry its author's own post media, so the editor asks media-service which of
the pasted files are the writer's before it keeps them. This checks that
answer, and that a post built from the answer saves and shows its picture.

The route must not become a way to learn which file ids exist: somebody
else's file and a file that does not exist have to look the same.
"""
import random
import string
import struct
import sys
import zlib
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=90)
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
    return r.json()["tokens"]["access_token"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


def png():
    raw = b"".join(b"\x00" + bytes([40, 160, 220] * 16) for _ in range(16))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def upload(token):
    r = c.post("/media/upload", headers=H(token), files={"file": ("p.png", png(), "image/png")},
               data={"provenance": "original"})
    r.raise_for_status()
    return r.json()


writer = register("Demo Writer")
stranger = register("Demo Stranger")
mine = upload(writer)
theirs = upload(stranger)

print("Which pasted pictures are the writer's")

r = c.post("/media/attachable", headers=H(writer),
           json={"ids": [mine["id"], theirs["id"], "mda_doesnotexist01"]})
check("the route answers", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
named = {item["id"] for item in r.json().get("attachable", [])}
check("the writer's own picture is named", mine["id"] in named, str(named))
check("somebody else's is not", theirs["id"] not in named, str(named))
check("a file that does not exist is not",
      "mda_doesnotexist01" not in named, str(named))
# The two refusals must be indistinguishable, or the route tells a caller
# which ids are real.
check("and nothing tells those two apart", len(named) == 1, str(named))
check("signed out, it refuses", c.post("/media/attachable", json={"ids": [mine["id"]]}).status_code == 401)
check("an empty list is fine", c.post("/media/attachable", headers=H(writer), json={"ids": []})
      .json() == {"attachable": []})

print()
print("A post built from that answer")

# What the editor does after a paste: the marker stays in the text, and the
# picture is offered to the server with the post. The same file, a second
# post, nothing uploaded again.
first = c.post("/posts", headers=H(writer), json={
    "format": "article", "visibility": "public",
    "body": f'<h2>First</h2><figure data-media-id="{mine["id"]}" data-kind="image"></figure>',
    "media": [{"media_id": mine["id"], "url": mine["url"], "kind": "image"}],
})
check("the original article saves", first.status_code in (200, 201), first.text[:120])
second = c.post("/posts", headers=H(writer), json={
    "format": "article", "visibility": "public",
    "body": f'<h2>Pasted</h2><p>Copied.</p><figure data-media-id="{mine["id"]}" data-kind="image"></figure>',
    "media": [{"media_id": mine["id"], "url": mine["url"], "kind": "image"}],
})
check("an article pasted from it saves with the same picture",
      second.status_code in (200, 201), f"{second.status_code} {second.text[:120]}")
if second.status_code in (200, 201):
    carried = [m.get("media_id") for m in second.json().get("media") or []]
    check("and carries it", mine["id"] in carried, str(carried))

# What the editor prevents: keeping somebody else's picture would have cost
# the writer the whole post.
refused = c.post("/posts", headers=H(writer), json={
    "format": "article", "visibility": "public",
    "body": f'<h2>Not mine</h2><figure data-media-id="{theirs["id"]}" data-kind="image"></figure>',
    "media": [{"media_id": theirs["id"], "url": theirs["url"], "kind": "image"}],
})
check("a post carrying somebody else's picture is refused - which is why the editor leaves it out",
      refused.status_code == 400, f"{refused.status_code} {refused.text[:120]}")

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
