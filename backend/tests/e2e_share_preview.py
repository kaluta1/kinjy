"""Link previews for shared posts, and video covers.

A shared post's page carries that post's title, description and picture in its
<head>, because crawlers do not run the app. Only what a signed-out visitor may
see goes there: a followers-only post, or one still waiting for review, gets
the generic card - no author name, no text. The picture is served from a stable
address as a JPEG, re-checked on every request.

A video gets a cover at upload (ffmpeg in media-service), served behind the
same ticket as the video.
"""
import os
import random
import re
import string
import struct
import subprocess
import sys
import tempfile
import zlib
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=60)
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


def png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = zlib.compress(bytes([0, 30, 160, 90, 255]))
    return (bytes([137, 80, 78, 71, 13, 10, 26, 10]) + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def og(post_id):
    r = httpx.get(f"{BASE}/og/p/{post_id}", timeout=30)
    meta = dict(re.findall(r'<meta property="og:(title|description|image)" content="([^"]*)"', r.text))
    return r, meta


def approve(post_id):
    """Stand in for a human review, as a test may and production may not."""
    user = subprocess.run(["docker", "exec", "kaluta-postgres", "printenv", "POSTGRES_USER"], capture_output=True, text=True).stdout.strip()
    db = subprocess.run(["docker", "exec", "kaluta-postgres", "printenv", "POSTGRES_DB"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", user, "-d", db, "-tAc",
                    f"update social.content_safety set age_rating='GENERAL', human_review_status='approved' "
                    f"where content_id='{post_id}'"], check=True, capture_output=True)


health = httpx.get("http://localhost:8203/health", timeout=5).json()
check("port 8203 is social-service", "social" in str(health), health)

me_t, me = register("Test Sharer")

print("Text post")
p = c.post("/posts", headers=H(me_t), json={"body": "Sunset over the lake <b>tonight</b> & a view", "format": "text", "visibility": "public"}).json()
r, meta = og(p["id"])
check("the page answers", r.status_code == 200 and r.headers["content-type"].startswith("text/html"), r.status_code)
check("titled after the author", meta.get("title") == "Test Sharer on Kinjy", meta)
check("described by its text, tags dropped and escaped", meta.get("description") == "Sunset over the lake tonight &amp; a view", meta)
check("one og:title, not the generic one as well", r.text.count('property="og:title"') == 1, r.text.count('property="og:title"'))
check("still the app's page", "<script" in r.text, "no script")

print("Not for everyone")
f = c.post("/posts", headers=H(me_t), json={"body": "Only my followers", "format": "text", "visibility": "followers"}).json()
r, meta = og(f["id"])
check("a followers-only post gets the generic card", meta.get("title") == "A post on Kinjy", meta)
check("without its text or its author", "Only my followers" not in r.text and "Test Sharer" not in r.text)
r, meta = og("pst_doesnotexist")
check("a missing post gets the generic card", meta.get("title") == "A post on Kinjy", meta)

print("Picture")
up = c.post("/media/upload", headers=H(me_t), files={"file": ("p.png", png(), "image/png")}).json()
q = c.post("/posts", headers=H(me_t), json={"body": "", "format": "image", "visibility": "public",
                                             "media": [{"media_id": up["id"], "url": up["url"], "kind": "image"}]}).json()
r, meta = og(q["id"])
check("a photo post is previewed by its author's name", meta.get("title") == "Test Sharer on Kinjy", meta)
check("and says what it is", meta.get("description") == "A photo by Test Sharer", meta)
# UNREVIEWED_MEDIA_RATING=text (18/10): an ordinary photo is for everybody,
# so its link is previewed with the photo straight away - not the author's
# avatar, which is what members saw when it waited for a review.
check("with the photo itself, at once", meta.get("image", "").endswith(f"/api/og/image/{q['id']}"), meta)
im = httpx.get(f"{BASE}/og/image/{q['id']}", follow_redirects=False)
check("and that picture is served", im.status_code == 200 and im.headers.get("content-type") == "image/jpeg", im.status_code)
adult = c.post("/posts", headers=H(me_t), json={"body": "", "format": "image", "visibility": "public", "mature": True,
                                                 "media": [{"media_id": up["id"], "url": up["url"], "kind": "image"}]}).json()
r, meta = og(adult["id"])
check("a photo marked adult is not shown in a preview", "/api/og/image/" not in meta.get("image", ""), meta)
approve(q["id"])
r, meta = og(q["id"])
check("once reviewed it is previewed", meta.get("image", "").endswith(f"/api/og/image/{q['id']}"), meta)
check("an empty caption says what it is", meta.get("description") == "A photo by Test Sharer", meta)
im = httpx.get(f"{BASE}/og/image/{q['id']}")
check("the preview picture is a JPEG", im.status_code == 200 and im.headers["content-type"] == "image/jpeg" and im.content[:2] == b"\xff\xd8", im.status_code)

print("Video cover")
has_ffmpeg = subprocess.run(["docker", "exec", "kaluta-media", "which", "ffmpeg"], capture_output=True).returncode == 0
check("media-service has ffmpeg", has_ffmpeg)
if has_ffmpeg:
    subprocess.run(["docker", "exec", "kaluta-media", "ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=duration=2:size=320x180:rate=25", "-pix_fmt", "yuv420p", "/tmp/e2e.mp4"], check=True)
    path = os.path.join(tempfile.gettempdir(), "e2e_share.mp4")
    subprocess.run(["docker", "cp", "kaluta-media:/tmp/e2e.mp4", path], check=True)
    vid = c.post("/media/upload", headers=H(me_t), files={"file": ("clip.mp4", open(path, "rb").read(), "video/mp4")}).json()
    v = c.post("/posts", headers=H(me_t), json={"body": "My clip", "format": "video", "visibility": "public",
                                                 "media": [{"media_id": vid["id"], "url": vid["url"], "kind": "video"}]}).json()
    m = v["media"][0]
    check("a video carries a cover address", bool(m.get("poster_url")), m)
    cover = httpx.get(m["poster_url"].replace("https://kinjy.com", "http://localhost:8200"), headers=H(me_t))
    check("the cover is served with the video's ticket", cover.status_code == 200 and cover.headers["content-type"] == "image/jpeg", cover.status_code)
    unsigned = httpx.get(f"http://localhost:8200/media/{vid['id']}?poster=1")
    check("and not without it", unsigned.status_code == 404, unsigned.status_code)
    approve(v["id"])
    im = httpx.get(f"{BASE}/og/image/{v['id']}")
    check("a video's preview picture is its cover", im.status_code == 200 and im.headers["content-type"] == "image/jpeg", im.status_code)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
