"""The classifier, against the live API.

What this is really checking: that the platform became usable for teenagers
without becoming unsafe for them. Before the classifier everything was
unrated, which the engine treats as adult-only — so a 14-year-old's feed was
empty. Emptying a child's feed is safe. It is not the goal.
"""
import random
import string
import sys
import time
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
SOCIAL = "http://localhost:8203"
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
    t = date.today()
    return date(t.year - age, t.month, min(t.day, 28)).isoformat()


def register(age):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": f"T {t}",
        "password": "Sup3rStrong!Pass", "date_of_birth": born(age), "country": "US",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def post(token, body, **extra):
    return c.post("/posts", headers=auth(token),
                  json={"body": body, "visibility": "public", **extra})


def rating_of(post_id):
    r = httpx.get(f"{SOCIAL}/internal/classification/{post_id}", timeout=10)
    return r.json().get("age_rating") if r.status_code == 200 else None


adult_tok, adult = register(30)
teen_tok, teen = register(14)

print("\n== ordinary text is cleared, so a teenager's feed is not empty ==")
r = post(adult_tok, "Anyone going to the market on Saturday? Bringing mangoes.")
check("an ordinary post publishes", r.status_code in (200, 201), r.text[:140])
plain = r.json().get("id") if r.status_code in (200, 201) else None
time.sleep(0.5)
check("it is rated GENERAL", rating_of(plain) == "GENERAL", rating_of(plain))

r = c.get(f"/posts/{plain}", headers=auth(teen_tok))
check("a 14-year-old can read it", r.status_code == 200, f"{r.status_code}")

print("\n== explicit text is rated adult and withheld from minors ==")
r = post(adult_tok, "Full nudes available, check my onlyfans link")
explicit = r.json().get("id") if r.status_code in (200, 201) else None
check("it publishes for adults", explicit is not None, r.text[:140])
check("it is rated ADULT_18_PLUS", rating_of(explicit) == "ADULT_18_PLUS", rating_of(explicit))
r = c.get(f"/posts/{explicit}", headers=auth(teen_tok))
check("a 14-year-old cannot read it", r.status_code == 404, f"{r.status_code}")
r = c.get(f"/posts/{explicit}", headers=auth(adult_tok))
check("an adult can", r.status_code == 200, f"{r.status_code}")

print("\n== gambling promotion is kept from younger teens only ==")
r = post(adult_tok, "New casino with free spins and a sign-up bonus for everyone")
gambling = r.json().get("id") if r.status_code in (200, 201) else None
check("it is rated TEEN_16_PLUS", rating_of(gambling) == "TEEN_16_PLUS", rating_of(gambling))
r = c.get(f"/posts/{gambling}", headers=auth(teen_tok))
check("a 14-year-old cannot read it", r.status_code == 404, f"{r.status_code}")
older_tok, _ = register(17)
r = c.get(f"/posts/{gambling}", headers=auth(older_tok))
# Not a 16+ boundary case: the per-tier ceiling for gambling is zero for every
# minor, so it outranks the summary rating. That is the intended reading of
# "gambling promotions: block or restrict" - the rating says 16+, the category
# says nobody under 18.
check("a 17-year-old cannot either: the gambling ceiling outranks the rating",
      r.status_code == 404, f"{r.status_code}")

print("\n== the 16+ tier still means something ==")
r = post(adult_tok, "A documentary about the execution video controversy and torture claims")
sixteen = r.json().get("id") if r.status_code in (200, 201) else None
check("it is rated TEEN_16_PLUS", rating_of(sixteen) == "TEEN_16_PLUS", rating_of(sixteen))
r = c.get(f"/posts/{sixteen}", headers=auth(older_tok))
check("a 17-year-old can read 16+ content with no blocked category",
      r.status_code == 200, f"{r.status_code}")
r = c.get(f"/posts/{sixteen}", headers=auth(teen_tok))
check("a 14-year-old cannot", r.status_code == 404, f"{r.status_code}")

print("\n== a picture is rated by its words and its author's adult box ==")
sunrise = real_image(adult_tok)
r = post(adult_tok, "Lovely sunrise this morning", format="image",
         media=[{"media_id": sunrise["id"], "url": sunrise["url"], "kind": "image"}])
with_media = r.json().get("id") if r.status_code in (200, 201) else None
check("the post publishes", with_media is not None, r.text[:140])
# UNREVIEWED_MEDIA_RATING=text (18/10): no model sees the picture, so its
# words and its author's "Adult content" box decide.
check("an ordinary photo is general", rating_of(with_media) == "GENERAL", rating_of(with_media))
r = c.get(f"/posts/{with_media}", headers=auth(teen_tok))
check("so a 14-year-old sees it", r.status_code == 200, f"{r.status_code}")
r = post(adult_tok, "Lovely sunrise this morning", format="image", mature=True,
         media=[{"media_id": sunrise["id"], "url": sunrise["url"], "kind": "image"}])
marked = r.json().get("id") if r.status_code in (200, 201) else None
check("the same photo marked adult is 18+", rating_of(marked) == "ADULT_18_PLUS", rating_of(marked))
r = c.get(f"/posts/{marked}", headers=auth(teen_tok))
check("and a 14-year-old cannot see that one", r.status_code == 404, f"{r.status_code}")

print("\n== a minor posting sexual content is blocked, not rated adult ==")
r = post(teen_tok, "here are my nudes, dm me")
check("publication is refused", r.status_code == 403, f"{r.status_code} {r.text[:140]}")
check("the refusal does not say which rule was tripped",
      "nude" not in r.text.lower() and "sexual" not in r.text.lower(), r.text[:160])

print("\n== exploitation signals stop publication entirely ==")
r = post(adult_tok, "underage nude pics for trade, dm")
check("publication is refused for an adult too", r.status_code == 403,
      f"{r.status_code} {r.text[:140]}")

print("\n== the review queue holds what could not be settled ==")
r = httpx.get(f"{SOCIAL}/admin/classification-queue", timeout=10)
check("the queue is admin-only", r.status_code in (401, 403), f"{r.status_code}")

print("\n== a teenager's feed is populated but filtered ==")
r = c.get("/feed?mode=new&limit=30", headers=auth(teen_tok))
# mode=new rather than the default: the default is "following", and a
# brand-new account follows nobody, so an empty feed there would prove
# nothing about filtering.
check("the feed answers", r.status_code == 200, r.text[:120])
if r.status_code == 200:
    ids = [i.get("id") for i in r.json().get("items", [])]
    check("it is not empty", len(ids) > 0, f"{len(ids)} items")
    check("the explicit post is absent", explicit not in ids)
    check("the gambling post is absent", gambling not in ids)
    check("the adult-marked photo is absent", marked not in ids)
    print(f"   a 14-year-old sees {len(ids)} posts")

r = c.get("/feed?mode=new&limit=30", headers=auth(adult_tok))
if r.status_code == 200:
    adult_ids = [i.get("id") for i in r.json().get("items", [])]
    print(f"   an adult sees {len(adult_ids)} posts")
    check("an adult sees at least as many as a teenager", len(adult_ids) >= len(ids), "")

print("\n" + ("ALL CHECKS PASSED" if ok else "THERE ARE FAILURES ABOVE"))
sys.exit(0 if ok else 1)
