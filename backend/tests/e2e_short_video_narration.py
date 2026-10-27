"""One-to-Many short videos are read aloud (27/10), end to end on the local stack.

An article goes through "Other formats" -> Short video; the output must be a
narrated video: an AAC track, audible, as long as the picture. The file
itself is inspected inside the media container with ffprobe / volumedetect.
Also: what is offered says whether narration is available, and a language
without a voice is refused up front rather than rendered silent.

    python backend/tests/e2e_short_video_narration.py
"""
import json
import random
import re
import string
import subprocess
import sys
import time
from datetime import date

import httpx

c = httpx.Client(base_url="http://localhost:8200/api", timeout=120)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:400]}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def media(cmd):
    return subprocess.run(["docker", "exec", "kaluta-media", "sh", "-c", cmd], capture_output=True, text=True, timeout=180)


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


t = tag()
r = c.post("/auth/register", json={"email": f"{t}@example.com", "handle": f"u{t}", "display_name": "Demo Writer",
                                   "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US"})
h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}

caps = c.get("/creators/publish/capabilities?lang=en", headers=h).json()
check("short video offered in English", caps["targets"]["short_video"]["available"] if "targets" in caps else caps.get("short_video", {}).get("available"), caps)

body = ("<h2>Chien Shiung Wu and the experiment that changed physics</h2>"
        "<p>In 1956 she designed an experiment with cobalt sixty at very low temperature.</p>"
        "<p>It showed that nature can tell left from right, and it overturned the law of parity.</p>")
post = c.post("/posts", headers=h, json={"body": body, "format": "article", "visibility": "public", "lang": "en"})
check("article posted", post.status_code == 201, post.text)
post_id = post.json()["id"]
job = c.post("/creators/publish", headers=h, json={"source_ref": post_id, "targets": ["short_video"]})
check("short video queued", job.status_code == 202, job.text)
job_id = job.json()["job_id"]

video = None
for _ in range(90):
    view = c.get(f"/creators/publish/{job_id}", headers=h).json()
    video = next((o for o in view["outputs"] if o["target"] == "short_video"), None)
    if video and video["status"] in ("completed", "failed"):
        break
    time.sleep(2)
check("video completed", video and video["status"] == "completed", video)
check("reported as narrated", video and video.get("narrated") is True, video)
check("read by a natural English voice", video and video.get("voice_quality") == "natural", video)

asset_id = psql(f"select media_id from creator.publishing_outputs where job_id = '{job_id}' and target = 'short_video'")
path = psql(f"select storage_path from media.assets where id = '{asset_id}'")
check("file stored", bool(path), asset_id)
info = json.loads(media(f"ffprobe -v error -show_entries stream=codec_type,codec_name,duration -of json {path}").stdout or "{}")
streams = {s["codec_type"]: s for s in info.get("streams", [])}
check("has an H.264 picture", streams.get("video", {}).get("codec_name") == "h264", info)
check("has an AAC voice track", streams.get("audio", {}).get("codec_name") == "aac", info)
vol = media(f"ffmpeg -hide_banner -nostats -i {path} -map 0:a:0 -af volumedetect -f null - 2>&1 | grep -E 'mean_volume|max_volume'").stdout
mean = re.search(r"mean_volume:\s*(-?[0-9.]+)", vol)
check("narration is audible (mean volume above -45 dB)", mean and float(mean.group(1)) > -45, vol)
a_len = float(streams.get("audio", {}).get("duration") or 0)
v_len = float(streams.get("video", {}).get("duration") or 0)
check("voice runs the length of the video", a_len > 0 and abs(a_len - v_len) < 1.0, (a_len, v_len))
# Keep a copy to listen to.
media(f"cp {path} /tmp/narrated-sample.mp4")

caps_ar = c.get("/creators/publish/capabilities?lang=ar", headers=h).json()
sv = caps_ar["targets"]["short_video"] if "targets" in caps_ar else caps_ar.get("short_video", {})
check("a language with no voice is not offered (with a reason)", not sv.get("available") and sv.get("reason"), sv)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
