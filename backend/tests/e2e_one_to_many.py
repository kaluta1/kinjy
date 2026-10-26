"""One-to-Many: short video, newsletter draft, four translations - produced, checked, private.

Needs the local stack with the test override and the fake vendor:
    python backend/tests/fake_anthropic.py &
    docker compose -f docker-compose.yml -f backend/tests/compose.onetomany-test.yml up -d --no-deps ai-service creator-service
"""
import io
import random
import string
import subprocess
import sys
import threading
import time
from datetime import date

import httpx
from PIL import Image

BASE = "http://localhost:8200/api"
CREATOR = "http://localhost:8208"
FAKE = "http://localhost:8799"
c = httpx.Client(base_url=BASE, timeout=120)
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:400]}"))
    if not cond:
        ok = False


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    r = c.post("/auth/register", json={"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
                                       "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(),
                                       "country": "US"})
    r.raise_for_status()
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"]}


def fake(mode):
    httpx.get(f"{FAKE}/_control", params={"mode": mode})


def picture():
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (40, 120, 200)).save(buf, "PNG")
    r = c.post("/media/upload", headers=author["h"], files={"file": ("demo.png", buf.getvalue(), "image/png")})
    r.raise_for_status()
    return r.json()


RUN = "".join(random.choices(string.ascii_lowercase, k=6))


def post(body, *, lang="en", visibility="public", media=None):
    # A word unique to this run: ai-service caches real translations, and a
    # failure test must not be answered from an earlier run's cache.
    payload = {"body": f"{body} {RUN}", "lang": lang, "visibility": visibility}
    if media:
        payload.update(format="image", media=[{"media_id": m["id"], "url": m["url"], "kind": "image"} for m in media])
    r = c.post("/posts", headers=author["h"], json=payload)
    r.raise_for_status()
    return r.json()


def publish(post_id, targets, who=None):
    return c.post("/creators/publish", headers=(who or author)["h"], json={"source_ref": post_id, "targets": targets})


def wait(job_id, timeout=120, until=None):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        r = c.get(f"/creators/publish/{job_id}", headers=author["h"])
        if r.status_code != 200:
            print(f"    (poll answered {r.status_code}: {r.text[:120]})")
            time.sleep(2)
            continue
        job = r.json()
        done = all(o["status"] in ("completed", "failed", "cancelled") for o in job["outputs"])
        if (until(job) if until else done):
            return job
        time.sleep(1.5)
    return job


def by(job, target, lang=None):
    return [o for o in job["outputs"] if o["target"] == target and (lang is None or o["lang"] == lang)]


fake("ok")
author = register("Demo Author")
reader = register("Demo Reader")
TEXT = ("Our harvest festival is on Saturday.\n"
        "Bring a dish to share and a friend who has never been. There will be music, games for the children, "
        "and the long table under the mango trees. We start at noon and finish when the last song is sung.")

print("Capabilities")
caps = c.get("/creators/publish/capabilities", headers=author["h"], params={"lang": "en"}).json()["targets"]
check("all three available with a model and a renderer", all(caps[t]["available"] for t in caps), caps)
check("translations exclude the post's language", caps["translation"]["langs"] == ["sw", "fr", "ar", "zh"], caps["translation"])
caps_ar = c.get("/creators/publish/capabilities", headers=author["h"], params={"lang": "ar"}).json()["targets"]
check("no title cards for an Arabic post", not caps_ar["short_video"]["available"] and caps_ar["short_video"]["reason"])
check("signed out: refused", c.get("/creators/publish/capabilities").status_code == 401)

print()
print("All three together")
img = picture()
p1 = post(TEXT, media=[img])
r = publish(p1["id"], ["short_video", "newsletter", "translation"])
check("accepted", r.status_code == 202, r.text)
job = r.json()
check("six outputs: one video, one newsletter, four translations", len(job["outputs"]) == 6, [(o["target"], o["lang"]) for o in job["outputs"]])
check("queued, not done", all(o["status"] in ("queued", "processing") for o in job["outputs"]))
job = wait(job["job_id"])
check("job completed", job["status"] == "completed", [(o["target"], o["lang"], o["status"], o["error"]) for o in job["outputs"]])
tr = {o["lang"]: o for o in by(job, "translation")}
check("translations in fr, sw, ar, zh", sorted(tr) == ["ar", "fr", "sw", "zh"], list(tr))
check("French is French", (tr.get("fr") or {}).get("content", "").startswith("(French) Our harvest"), tr.get("fr"))
check("Arabic flagged right-to-left", (tr.get("ar") or {}).get("rtl") is True and not tr["fr"]["rtl"])
check("from the real provider path", all(o["provider"] == "anthropic" for o in tr.values()))
state = httpx.get(f"{FAKE}/_control").json()
check("model and fallbacks sent", state.get("last_model") == "claude-opus-5-5" and state.get("last_fallbacks") == "default", state)
nl = by(job, "newsletter")[0]
check("newsletter subject is the first line", nl["title"] == "Our harvest festival is on Saturday.", nl["title"])
check("newsletter has a preview line", (nl["preview_text"] or "").startswith("Bring a dish"), nl["preview_text"])
check("newsletter links back to the post", f"/p/{p1['id']}" in (nl["html"] or "") and f"/p/{p1['id']}" in (nl["content"] or ""))
check("newsletter html is sanitized markup", "<p>" in nl["html"] and "<script" not in nl["html"])
vid = by(job, "short_video")[0]
check("video has a length", (vid["duration_s"] or 0) > 3, vid)
v = httpx.get(vid["video_url"], headers=author["h"], timeout=60)
check("the author can play it", v.status_code == 200 and v.headers["content-type"] == "video/mp4" and b"ftyp" in v.content[:16], v.status_code)
probe = subprocess.run(["docker", "exec", "-i", "kaluta-media", "ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_name,width,height", "-of", "csv=p=0", "-"], input=v.content, capture_output=True)
check("a vertical H.264 file", probe.stdout.strip() == b"h264,720,1280", probe.stdout)
d = httpx.get(vid["download_url"], headers=author["h"], timeout=60)
check("and download it", d.status_code == 200 and "attachment" in d.headers.get("content-disposition", ""))
check("another member cannot use the author's link", httpx.get(vid["video_url"], headers=reader["h"]).status_code == 404)
check("nor anyone without a ticket", httpx.get(vid["video_url"].split("?")[0]).status_code == 404)

print()
print("Only the author")
check("another member cannot ask for a post they did not write", publish(p1["id"], ["newsletter"], reader).status_code == 404)
check("nor read the job", c.get(f"/creators/publish/{job['job_id']}", headers=reader["h"]).status_code == 404)
check("nor see it by post", c.get(f"/creators/publish/by-post/{p1['id']}", headers=reader["h"]).json()["job"] is None)
check("nor retry an output", c.post(f"/creators/publish/outputs/{nl['id']}/retry", headers=reader["h"]).status_code == 404)
check("nor edit the newsletter", c.patch(f"/creators/publish/outputs/{nl['id']}", headers=reader["h"],
                                         json={"subject": "x", "html": "<p>x</p>"}).status_code == 404)
check("signed out: refused", c.post("/creators/publish", json={"source_ref": p1["id"], "targets": ["newsletter"]}).status_code == 401)
check("unknown post: 404", publish("pst_doesnotexist", ["newsletter"]).status_code == 404)
check("a format nothing produces is refused", publish(p1["id"], ["audio"]).status_code == 400)

print()
print("Duplicates")
again = publish(p1["id"], ["short_video", "newsletter", "translation"])
check("same request, same job", again.status_code == 202 and again.json()["job_id"] == job["job_id"] and again.json()["added"] == 0, again.text)
check("still six outputs", len(again.json()["outputs"]) == 6)
check("and nothing redone", by(again.json(), "newsletter")[0]["completed_at"] == nl["completed_at"])
p2 = post("Two tabs at once. The same post, asked for twice in the same second.")
results = []
threads = [threading.Thread(target=lambda: results.append(publish(p2["id"], ["newsletter"]))) for _ in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
ids = {r.json().get("job_id") for r in results if r.status_code == 202}
check("concurrent requests make one job", len(ids) == 1 and all(r.status_code == 202 for r in results), [r.status_code for r in results])
check("with one newsletter", psql(f"select count(*) from creator.publishing_outputs where job_id='{ids.pop()}'") == "1")
later = publish(p2["id"], ["translation"])
check("asking for more later adds to the same job", later.json()["added"] == 4 and len(later.json()["outputs"]) == 5, later.text)
wait(later.json()["job_id"])

print()
print("Each on its own")
p3 = post("Only a newsletter, please. Nothing else.\nThe second line is the introduction.")
j3 = wait(publish(p3["id"], ["newsletter"]).json()["job_id"])
check("newsletter only", [o["target"] for o in j3["outputs"]] == ["newsletter"] and j3["status"] == "completed")
p4 = post("Just the words this time.\nNo pictures at all, so the video is title cards only.")
j4 = wait(publish(p4["id"], ["short_video"]).json()["job_id"])
check("text-only video", j4["status"] == "completed" and by(j4, "short_video")[0]["video_url"], j4["outputs"])
p5 = post("Translate me into the four other languages.")
j5 = wait(publish(p5["id"], ["translation"]).json()["job_id"])
check("translations only", len(j5["outputs"]) == 4 and j5["status"] == "completed")
p6 = post("Bonjour à tous, rendez-vous samedi pour la fête.", lang="fr")
j6 = wait(publish(p6["id"], ["translation"]).json()["job_id"])
check("a French post goes to en, sw, ar, zh", sorted(o["lang"] for o in j6["outputs"]) == ["ar", "en", "sw", "zh"])
p7 = post("مرحبا بالجميع، نلتقي يوم السبت", lang="ar")
r7 = publish(p7["id"], ["short_video"])
check("an unavailable target is refused, nothing queued", r7.status_code == 409 and
      c.get(f"/creators/publish/by-post/{p7['id']}", headers=author["h"]).json()["job"] is None, r7.text)

print()
print("Failures")
fake("error500")
p8 = post("The vendor is down today. Translations will fail, the newsletter will not.")
j8 = wait(publish(p8["id"], ["newsletter", "translation"]).json()["job_id"], timeout=150)
failed = [o for o in j8["outputs"] if o["status"] == "failed"]
check("translations failed after bounded retries", len(failed) == 4 and all(o["attempts"] == 3 for o in failed),
      [(o["lang"], o["status"], o["attempts"], o["error"]) for o in j8["outputs"]])
check("the newsletter still completed: partial success", by(j8, "newsletter")[0]["status"] == "completed" and j8["status"] == "partial", j8["status"])
nl8 = by(j8, "newsletter")[0]
fake("ok")
rf = c.post(f"/creators/publish/{j8['job_id']}/retry-failed", headers=author["h"]).json()
check("retry-failed requeues only the failed four", rf["requeued"] == 4)
j8 = wait(j8["job_id"])
check("which now complete", j8["status"] == "completed", [(o["lang"], o["status"]) for o in j8["outputs"]])
check("the newsletter was not redone", by(j8, "newsletter")[0]["completed_at"] == nl8["completed_at"])
fake("refusal")
p9 = post("A text the model declines.")
j9 = wait(publish(p9["id"], ["translation"]).json()["job_id"])
check("a refusal fails at once, no retries", all(o["status"] == "failed" and o["attempts"] == 1 for o in j9["outputs"]),
      [(o["status"], o["attempts"], o["error"]) for o in j9["outputs"]])
fake("echo")
p10 = post("A long enough sentence that an unchanged answer cannot be a translation of it.")
j10 = wait(publish(p10["id"], ["translation"]).json()["job_id"], timeout=150)
check("the original sent back is not a translation", all(o["status"] == "failed" for o in j10["outputs"]),
      [(o["status"], o["error"]) for o in j10["outputs"]])
fake("ok")

print()
print("Abandoned work")
p11 = post("A worker died holding this one.")
j11 = publish(p11["id"], ["newsletter"]).json()
oid = j11["outputs"][0]["id"]
time.sleep(0.2)
psql(f"update creator.publishing_outputs set status='processing', attempts=3, lease_until=now()-interval '1 minute' where id={oid}")
j11 = wait(j11["job_id"], until=lambda j: j["outputs"][0]["status"] == "failed")
check("an expired lease with no tries left fails", j11["outputs"][0]["status"] == "failed", j11["outputs"])
psql(f"update creator.publishing_outputs set status='processing', attempts=1, lease_until=now()-interval '1 minute' where id={oid}")
j11 = wait(j11["job_id"], until=lambda j: j["outputs"][0]["status"] == "completed")
check("an expired lease with tries left is taken again", j11["outputs"][0]["status"] == "completed", j11["outputs"])

print()
print("Edits, visibility, deletion")
nid = by(job, "newsletter")[0]["id"]
e = c.patch(f"/creators/publish/outputs/{nid}", headers=author["h"],
            json={"subject": "Harvest festival", "preview_text": "Saturday at noon",
                  "html": "<p>Come early.</p><script>alert(1)</script><p onclick='x()'>Bring a dish.</p>"})
check("the author edits the draft", e.status_code == 200, e.text)
edited = by(e.json(), "newsletter")[0]
check("saved sanitized", "<script" not in edited["html"] and "onclick" not in edited["html"] and "Come early." in edited["html"])
check("marked as edited", edited["user_edited"] is True and edited["title"] == "Harvest festival")
check("nothing to send it with", c.post(f"/creators/publish/outputs/{nid}/send", headers=author["h"]).status_code in (404, 405))
routes = httpx.get(f"{CREATOR}/openapi.json").json()["paths"]
check("no sending route exists at all", not [p for p in routes if "send" in p or "deliver" in p], [p for p in routes if "send" in p])
ed = c.patch(f"/posts/{p1['id']}", headers=author["h"], json={"body": TEXT + "\nUpdated: bring a chair too."})
check("the post is edited", ed.status_code == 200, ed.text)
j1 = c.get(f"/creators/publish/{job['job_id']}", headers=author["h"]).json()
check("outputs made before the edit are stale", all(o["stale"] for o in j1["outputs"]), [(o["target"], o["stale"]) for o in j1["outputs"]])
r = c.post(f"/creators/publish/outputs/{nid}/retry", headers=author["h"])
check("regenerating an edited draft needs consent", r.status_code == 409, r.text)
r = c.post(f"/creators/publish/outputs/{nid}/retry", headers=author["h"], params={"force": "true"})
check("with consent it is regenerated", r.status_code == 200)
j1 = wait(job["job_id"], until=lambda j: by(j, "newsletter")[0]["status"] == "completed" and not by(j, "newsletter")[0]["stale"])
check("from the edited post", "bring a chair too" in (by(j1, "newsletter")[0]["content"] or ""))
p12 = post("For followers only.\nThe newsletter must not link to a page they cannot open.", visibility="followers")
j12 = wait(publish(p12["id"], ["newsletter"]).json()["job_id"])
n12 = by(j12, "newsletter")[0]
check("no link back to a followers-only post", "/p/" not in (n12["html"] or "") and n12["warning"], n12)
old_video = by(j1, "short_video")[0]["video_url"]
dl = c.delete(f"/posts/{p1['id']}", headers=author["h"])
check("the post is deleted", dl.status_code == 204, dl.text)
j1 = c.get(f"/creators/publish/{job['job_id']}", headers=author["h"]).json()
check("the job knows", j1["source"]["deleted"] is True)
check("nothing made from it is shown", all(o["content"] is None and o["video_url"] is None for o in j1["outputs"]))
check("the video is withdrawn", by(j1, "short_video")[0]["status"] == "cancelled")
check("and its file is gone", httpx.get(old_video, headers=author["h"]).status_code == 404)
check("it cannot be retried", c.post(f"/creators/publish/{job['job_id']}/retry-failed", headers=author["h"]).status_code == 404)

print()
print("History, by hand")
p13 = post("Asked for before the pipeline existed.")
legacy = "pub_legacy" + "".join(random.choices(string.ascii_lowercase, k=8))
psql(f"insert into creator.publishing_jobs (id, creator_id, source_kind, source_ref, source_text, source_lang, targets, target_langs, status, created_at) "
     f"values ('{legacy}', '{author['id']}', 'text', '{p13['id']}', 'x', 'en', 'translation,short_video', 'en,fr', 'partial', now())")
psql(f"insert into creator.publishing_outputs (job_id, target, lang, status, content, provenance, error, created_at) values "
     f"('{legacy}', 'translation', 'en', 'ready', 'x', 'original', null, now()), "
     f"('{legacy}', 'translation', 'fr', 'ready', '[fr] x', 'ai_generated', null, now()), "
     f"('{legacy}', 'short_video', 'en', 'unsupported', null, 'ai_generated', 'needs video rendering', now())")
time.sleep(3)
check("the worker leaves history alone", psql(f"select string_agg(status, ',' order by id) from creator.publishing_outputs where job_id='{legacy}'") == "ready,ready,unsupported")
dry = httpx.post(f"{CREATOR}/internal/publish/backfill", params={"dry_run": "true", "limit": 200}).json()
mine = [j for j in dry["jobs"] if j["job_id"] == legacy]
check("a dry run reports it", mine and mine[0]["action"] == "requeued" and mine[0]["outputs"] == 3, dry)
check("and changes nothing", psql(f"select count(*) from creator.publishing_outputs where job_id='{legacy}' and pipeline is null") == "3")
httpx.post(f"{CREATOR}/internal/publish/backfill", params={"dry_run": "false", "limit": 200})
jl = wait(legacy)
check("run for real, it is produced", [(o["target"], o["lang"], o["status"]) for o in jl["outputs"]] ==
      [("translation", "en", "cancelled"), ("translation", "fr", "completed"), ("short_video", "en", "completed")], jl["outputs"])
check("now one job per post", publish(p13["id"], ["translation"]).json()["job_id"] == legacy)

print()
print("No model configured")
subprocess.run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "ai-service"], capture_output=True)
for _ in range(30):
    try:
        if httpx.get("http://localhost:8212/health", timeout=2).status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(1)
caps = c.get("/creators/publish/capabilities", headers=author["h"], params={"lang": "en"}).json()["targets"]
check("translation is unavailable, and says why", not caps["translation"]["available"] and caps["translation"]["reason"], caps["translation"])
p14 = post("Nobody can translate this today.")
r14 = publish(p14["id"], ["translation", "newsletter"])
check("the request is refused, nothing queued", r14.status_code == 409 and
      c.get(f"/creators/publish/by-post/{p14['id']}", headers=author["h"]).json()["job"] is None, r14.text)
direct = httpx.post("http://localhost:8212/ai/translate", json={"text": "hello", "target_lang": "fr", "require_live": True})
check("ai-service refuses a placeholder when asked for a real one", direct.status_code == 503, direct.text)
subprocess.run(["docker", "compose", "-f", "docker-compose.yml", "-f", "backend/tests/compose.onetomany-test.yml",
                "up", "-d", "--no-deps", "--force-recreate", "ai-service"], capture_output=True)

print()
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
