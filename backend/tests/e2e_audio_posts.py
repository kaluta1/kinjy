"""Audio posts (29/10), end to end against the local stack.

    python backend/tests/e2e_audio_posts.py

An audio file uploads for a post (MP3, M4A, OGG, WAV - checked by its first
bytes), a post carries it as audio, another member hears it through the
signed address, visibility and deletion work as for any post, and photo,
video, article and text posts are unchanged.
"""
import io
import math
import random
import string
import struct
import sys
import wave
import zlib
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
ROOT = "http://localhost:8200"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


def wav() -> bytes:
    """One second of a 440 Hz tone: real, playable audio."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(8000)))
    return buf.getvalue()


def png() -> bytes:
    raw = b"".join(b"\x00" + b"\xc8\x96\x32" * 8 for _ in range(8))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x64" + b"\x00" * 400
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 400


def upload(user, name, data, mime):
    return c.post("/media/upload", headers=user["h"], files={"file": (name, data, mime)}, data={"purpose": "post"})


def post(user, media, fmt="audio", body="Demo audio post", visibility="public"):
    return c.post("/posts", headers=user["h"], json={
        "body": body, "format": fmt, "visibility": visibility,
        "media": [{"media_id": m["id"], "url": m["url"], "kind": m["kind"]} for m in media]})


author, listener, stranger = register("Demo Author"), register("Demo Listener"), register("Demo Stranger")

print("== uploading ==")
r = upload(author, "tone.wav", wav(), "audio/wav")
check("a WAV uploads as audio", r.status_code == 201 and r.json()["kind"] == "audio", r.text[:200])
audio = r.json()
for name, data, mime in (("a.mp3", MP3, "audio/mpeg"), ("a.m4a", M4A, "audio/mp4"), ("b.m4a", M4A, "audio/x-m4a")):
    r = upload(author, name, data, mime)
    check(f"{name} ({mime}) uploads as audio", r.status_code == 201 and r.json()["kind"] == "audio", r.text[:200])
r = upload(author, "fake.mp3", b"this is not audio at all" * 20, "audio/mpeg")
check("a file that is not what it says is refused", r.status_code in (400, 415), r.text[:200])
r = upload(author, "a.flac", b"fLaC" + b"\x00" * 100, "audio/flac")
check("an unsupported format is refused, with the reason", r.status_code in (400, 415) and r.json().get("detail"), r.text[:200])

print("== posting and listening ==")
r = post(author, [audio])
check("an audio post is published", r.status_code == 201 and r.json()["format"] == "audio", r.text[:300])
p = r.json()
seen = c.get(f"/posts/{p['id']}", headers=listener["h"]).json()
m = (seen.get("media") or [{}])[0]
check("another member sees it as audio, with an address", m.get("kind") == "audio" and m.get("url"), seen.get("media"))
got = httpx.get(m["url"] if m["url"].startswith("http") else ROOT + m["url"], follow_redirects=True)
check("and the address serves the sound", got.status_code == 200 and got.headers.get("content-type", "").startswith("audio/")
      and got.content == wav(), (got.status_code, got.headers.get("content-type")))
again = c.get(f"/posts/{p['id']}", headers=listener["h"]).json()["media"][0]
check("read again (a refresh): still playable", again.get("kind") == "audio" and httpx.get(
    again["url"] if again["url"].startswith("http") else ROOT + again["url"]).status_code == 200)
raw = m["url"].split("?", 1)[0]
check("the bare address, without the viewer's ticket, is refused",
      httpx.get(raw if raw.startswith("http") else ROOT + raw).status_code in (401, 403, 404))
r = post(listener, [audio])
check("nobody can post somebody else's audio", r.status_code == 400, r.text[:200])

print("== visibility and deletion ==")
a2 = upload(author, "tone2.wav", wav(), "audio/wav").json()
priv = post(author, [a2], visibility="private").json()
check("a private audio post is not shown to another member",
      c.get(f"/posts/{priv['id']}", headers=stranger["h"]).status_code in (403, 404))
check("the author deletes an audio post", c.delete(f"/posts/{p['id']}", headers=author["h"]).status_code in (200, 204))
check("then nobody gets it", c.get(f"/posts/{p['id']}", headers=listener["h"]).status_code in (403, 404, 410))
check("another member cannot delete it", c.delete(f"/posts/{priv['id']}", headers=stranger["h"]).status_code in (403, 404))
same = upload(author, "again.wav", wav(), "audio/wav").json()
check("the same sound again comes back as the existing file, marked reused (the composer then never deletes it)",
      same.get("deduplicated") is True and same["id"] == a2["id"], same)
d = upload(author, "drop.wav", wav()[:-2] + b"\x01\x02", "audio/wav").json()
check("(a different file is stored fresh)", not d.get("deduplicated"), d)
check("an audio removed before posting is deleted (composer's discard)",
      c.delete(f"/media/{d['id']}", headers=author["h"]).status_code == 204)

print("== other posts unchanged ==")
img = upload(author, "p.png", png(), "image/png").json()
check("a photo post", post(author, [img], fmt="image", body="Demo photo").status_code == 201)
check("a text post", post(author, [], fmt="text", body="Demo text").status_code == 201)
check("an article", post(author, [], fmt="article", body="<h2>Demo article</h2><p>Body</p>").status_code == 201)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
