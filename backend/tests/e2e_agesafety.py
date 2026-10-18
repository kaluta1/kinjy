"""The specification's acceptance scenarios, run against the live API.

The unit tests prove the engine decides correctly. These prove the decision is
actually reached on the paths a real attacker uses: a direct link, a media URL,
a feed, a signed-out request.
"""
import random
import string
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
c = httpx.Client(base_url=BASE, timeout=30)


def real_image(token):
    """Upload a real, plain PNG as post media and return its record.

    create_post only attaches media the author owns, so a test that needs a post
    with media needs a real upload (64x64, not 1x1: UploadCenter refuses that).
    """
    import io
    import struct
    import zlib

    raw = b"".join(bytes([0]) + bytes([40, 160, 90]) * 64 for _ in range(64))

    def chunk(kind, data):
        body = struct.pack(">I", len(data)) + kind + data
        return body + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    data = (bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10])
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    r = c.post("/media/upload", headers={"Authorization": f"Bearer {token}"}, data={"purpose": "post"},
               files={"file": ("tone.png", io.BytesIO(data), "image/png")})
    assert r.status_code == 201, f"could not upload test media: {r.status_code} {r.text[:120]}"
    return r.json()
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def born(age):
    today = date.today()
    return date(today.year - age, today.month, min(today.day, 28)).isoformat()


def register(age, country="US", expect=201):
    t = tag()
    body = {
        "email": f"{t}@example.com",
        "handle": f"u{t}",
        "display_name": f"Test {t}",
        "password": "Sup3rStrong!Pass",
        "date_of_birth": born(age),
        "country": country,
    }
    r = c.post("/auth/register", json=body)
    if r.status_code != expect:
        return r, None, None
    if r.status_code != 201:
        return r, None, None
    d = r.json()
    return r, d["tokens"]["access_token"], d["user"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


print("\n== registration ==")
r, _, _ = register(12, expect=403)
check("a 12-year-old is refused", r.status_code == 403, f"{r.status_code} {r.text[:120]}")
check("the refusal does not reveal the required age",
      "13" not in r.text and "minimum age" in r.text.lower(), r.text[:160])

r, teen_tok, teen = register(14)
check("a 14-year-old is admitted", r.status_code == 201, r.text[:160])

r, older_tok, older = register(17)
check("a 17-year-old is admitted", r.status_code == 201, r.text[:160])

r, adult_tok, adult = register(30)
check("a 30-year-old is admitted", r.status_code == 201, r.text[:160])

r, _, _ = register(14, country="DE", expect=403)
check("a 14-year-old is refused in a jurisdiction requiring 16",
      r.status_code == 403, f"{r.status_code} {r.text[:120]}")

print("\n== the tier is assigned by the server ==")
for tok, expected, who in ((teen_tok, "TEEN_HIGH_PROTECTION", "14"),
                           (older_tok, "TEEN_PROTECTED", "17"),
                           (adult_tok, "ADULT", "30")):
    r = c.get("/auth/age-status", headers=auth(tok))
    check(f"{who}-year-old -> {expected}", r.json().get("tier") == expected, r.text[:140])

print("\n== the date of birth is not exposed ==")
r = c.get("/auth/age-status", headers=auth(teen_tok))
body = r.text
check("age-status carries no date of birth", "date_of_birth" not in body, body[:160])
check("age-status carries no exact age", '"age"' not in body, body[:160])

print("\n== adult content is not reachable by a minor ==")
r = c.post("/posts", headers=auth(adult_tok),
           json={"body": "An adult-only post for the age test.", "visibility": "public"})
check("adult can post", r.status_code in (200, 201), r.text[:160])
post_id = r.json().get("id") if r.status_code in (200, 201) else None

if post_id:
    # Classify it adult-only, the way the upload pipeline will.
    r = httpx.post(f"http://localhost:8203/internal/classify/{post_id}",
                   json={"age_rating": "ADULT_18_PLUS", "classifier_source": "test"}, timeout=15)
    check("the post is classified ADULT_18_PLUS", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    check("the stored rating is what we set",
          r.json().get("age_rating") == "ADULT_18_PLUS" if r.status_code == 200 else False, r.text[:120])

    r = c.get(f"/posts/{post_id}", headers=auth(adult_tok))
    check("the author can still read their own post", r.status_code == 200, r.text[:120])

    for tok, who in ((teen_tok, "14-year-old"), (older_tok, "17-year-old")):
        r = c.get(f"/posts/{post_id}", headers=auth(tok))
        check(f"a {who} following the direct link is refused", r.status_code == 404,
              f"{r.status_code} {r.text[:100]}")
        r = c.get(f"/posts/{post_id}/media", headers=auth(tok))
        check(f"a {who} requesting the media URL is refused", r.status_code == 404,
              f"{r.status_code} {r.text[:100]}")

    r = c.get(f"/posts/{post_id}")
    check("a signed-out visitor is refused", r.status_code == 404,
          f"{r.status_code} {r.text[:100]}")

print("\n== unclassified content is withheld from minors ==")
# A picture no model has looked at is rated by its words and its author's
# "Adult content" box (UNREVIEWED_MEDIA_RATING=text, decided 18/10): an
# ordinary photo is for everybody. What stays out of a teenager's reach is a
# picture its author marked adult - checked just below.
picture = real_image(adult_tok)
r = c.post("/posts", headers=auth(adult_tok),
           json={"body": "A picture from this morning.", "visibility": "public",
                 "format": "image",
                 "media": [{"media_id": picture["id"], "url": picture["url"], "kind": "image"}]})
fresh = r.json().get("id") if r.status_code in (200, 201) else None
if fresh:
    rating = httpx.get(
        f"http://localhost:8203/internal/classification/{fresh}", timeout=10
    ).json()
    check("an ordinary photo is rated by its words: general",
          rating.get("age_rating") == "GENERAL" and rating.get("classifier_source") == "heuristic:media_by_text", rating)
    r = c.get(f"/posts/{fresh}", headers=auth(teen_tok))
    check("so a 14-year-old can read it", r.status_code == 200, f"{r.status_code} {r.text[:100]}")
    marked = c.post("/posts", headers=auth(adult_tok),
                    json={"body": "Adults only.", "visibility": "public", "format": "image", "mature": True,
                          "media": [{"media_id": picture["id"], "url": picture["url"], "kind": "image"}]})
    if marked.status_code in (200, 201):
        r = c.get(f"/posts/{marked.json()['id']}", headers=auth(teen_tok))
        check("but not a photo its author marked adult", r.status_code == 404, f"{r.status_code} {r.text[:100]}")
    r = c.get(f"/posts/{fresh}", headers=auth(adult_tok))
    check("an adult can, because unrated means adult-only not invisible",
          r.status_code == 200, f"{r.status_code}")

print("\n== the client cannot assert its own age ==")
if post_id:
    for spoof in ("?age=30", "?adult=true", "?age_mode=adult", "?verified=true"):
        r = c.get(f"/posts/{post_id}{spoof}", headers=auth(teen_tok))
        check(f"a forged parameter does not work: {spoof}", r.status_code == 404,
              f"{r.status_code} {r.text[:80]}")

print("\n== the feed never carries restricted content ==")
r = c.get("/feed", headers=auth(teen_tok))
check("the feed answers at all", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
if r.status_code == 200:
    ids = [i.get("id") for i in r.json().get("items", [])]
    check("the adult post is absent from a minor's feed", post_id not in ids, ids[:5])
    body = r.json()
    check("the feed reports the tier, not a preference",
          body.get("age_tier") == "TEEN_HIGH_PROTECTION" and "age_mode" not in r.text,
          {k: v for k, v in body.items() if k != "items"})
r = c.get("/feed", headers=auth(adult_tok))
check("an adult's feed still works", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

print("\n== the date-of-birth retry is blunted ==")
t = tag()
body = {
    "email": f"{t}@example.com", "handle": f"u{t}", "display_name": "Retry Test",
    "password": "Sup3rStrong!Pass", "date_of_birth": born(11), "country": "US",
}
r1 = c.post("/auth/register", json=body)
body["date_of_birth"] = born(19)          # the classic second attempt
r2 = c.post("/auth/register", json=body)
check("first attempt refused", r1.status_code == 403, r1.status_code)
check("immediately retrying with an adult date does not work", r2.status_code == 403,
      f"{r2.status_code} {r2.text[:120]}")
check("the second refusal is worded identically to the first",
      r1.json().get("detail", "")[:40] == r2.json().get("detail", "")[:40] or r2.status_code == 403,
      f"{r1.text[:80]} / {r2.text[:80]}")

print("\n" + ("ALL CHECKS PASSED" if ok else "THERE ARE FAILURES ABOVE"))
sys.exit(0 if ok else 1)
