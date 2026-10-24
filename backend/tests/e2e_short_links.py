"""Short links (kinjy.com/s/<token>): the member tool, attribution, previews, safety.

Against the local stack (gateway 8200, auth-service 8201). Covers:
 1. a neutral link, and an invitation link, made from a pasted Kinjy address;
 2. another member's referral parameters stripped; page parameters and fragments kept;
 3. destinations refused: other sites, lookalikes, credentials, schemes, admin,
    account, payment and token pages, secrets in the query;
 4. a short link pasted back in is resolved to its destination (no chains);
 5. opening: one 302, never shared-cached, the token only on invitation links;
 6. registration through an invitation link, a neutral link, a disabled link;
    precedence of a code, existing members, self-referral;
 7. page permissions still apply behind a short link;
 8. previews for crawlers: public data only, no invitation, nothing written;
 9. management: my list only, disable (owner only), immutability;
10. concurrent creation, a forced token collision, rate limit, links after a restart.
"""
import concurrent.futures
import random
import re
import string
import subprocess
import sys
import time
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
AUTH = "http://localhost:8201"
c = httpx.Client(base_url=BASE, timeout=60)
ok = True
CRAWLER = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"
SITE = "http://localhost:3030"  # FRONTEND_URL of the local stack


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name, **extra):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    body = {"email": f"{t}@example.com", "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
            "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG", **extra}
    r = c.post("/auth/register", json=body)
    r.raise_for_status()
    d = r.json()
    return {"token": d["tokens"]["access_token"], "id": d["user"]["id"], "handle": f"u{t}", "email": body["email"],
            "code": d["user"].get("referral_code")}


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def link(u, destination, invite=False):
    return c.post("/auth/share-links", headers=H(u), json={"destination": destination, "include_invitation": invite})


def open_link(token, agent=BROWSER):
    return httpx.get(f"{BASE}/auth/s/{token}", headers={"User-Agent": agent}, follow_redirects=False, timeout=30)


def invited_by(u):
    return httpx.get(f"{AUTH}/internal/users/{u['id']}").json().get("invited_by")


def psql(sql):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "kaluta", "-d", "kaluta",
                           "-tAc", sql], capture_output=True, text=True).stdout.strip()


alice = register("Demo Alice")
bob = register("Demo Bob")
post = c.post("/posts", headers=H(bob), json={"body": "Demo public post for short links", "visibility": "public"}).json()
article = c.post("/posts", headers=H(bob), json={"body": "<h2>Demo article</h2><p>About short links.</p>",
                                                "format": "article", "visibility": "public"}).json()

print("1. Neutral and invitation links")
r = link(alice, f"{SITE}/p/{post['id']}")
check("a neutral link is made from a full address", r.status_code == 200, r.text[:200])
neutral = r.json()
check("short: our domain, /s/ and nine characters", re.fullmatch(rf"{SITE}/s/[0-9A-Za-z]{{9}}", neutral["url"]), neutral)
check("neutral by default", neutral["include_invitation"] is False and neutral["active"] and neutral["created"], neutral)
invite = link(alice, f"{SITE}/p/{post['id']}", invite=True).json()
check("an invitation link for the same page is a different link", invite["token"] != neutral["token"]
      and invite["include_invitation"] is True, invite)
for lk in (neutral, invite):
    check(f"no code, id or long address in {lk['token']}",
          alice["code"] not in lk["url"] and alice["id"] not in lk["url"] and post["id"] not in lk["url"], lk["url"])
again = link(alice, f"/p/{post['id']}").json()
check("asking again with the same choice returns the same link", again["token"] == neutral["token"]
      and again["created"] is False, again)
fake = c.post("/auth/share-links", headers=H(alice),
              json={"destination": "/", "include_invitation": True, "member_id": bob["id"], "referral_code": bob["code"]})
check("a member id or code in the request is ignored: the invitation is Alice's",
      fake.status_code == 200 and psql(f"select member_id from auth.share_links where token='{fake.json()['token']}'")
      == alice["id"], fake.text[:200])
check("signed out: no link", c.post("/auth/share-links", json={"destination": "/"}).status_code == 401)

print()
print("2. Parameters: referral stripped, the page's own kept")
kept = link(alice, f"{SITE}/p/{article['id']}?ref={bob['code']}&utm_source=wa&invite={bob['code']}&via=AAAAAAAAA#comments").json()
check("Bob's ?ref= / ?invite= / ?via= are removed", "ref=" not in kept["destination"] and "invite=" not in
      kept["destination"] and "via=" not in kept["destination"], kept)
check("utm_source and the #fragment are kept", kept["destination"] == f"/p/{article['id']}?utm_source=wa#comments", kept)
r = open_link(kept["token"])
check("a neutral link made from Bob's invitation address carries no invitation at all",
      r.headers.get("location") == f"/p/{article['id']}?utm_source=wa#comments", r.headers)
search = link(alice, f"{SITE}/explore?q=caf%C3%A9+cr%C3%A8me&tab=people#top", invite=True).json()
r = open_link(search["token"])
check("an invitation link adds its token before the fragment and keeps the query",
      r.headers.get("location") == f"/explore?q=caf%C3%A9+cr%C3%A8me&tab=people&via={search['token']}#top", r.headers)
r = link(alice, f"{SITE}/join?mode=signin&next=https://evil.example/")
check("/join keeps only the sign-up form, never ?next=", r.json().get("destination") == "/join?mode=signup", r.text)
r = link(alice, "kinjy.com/u/" + bob["handle"])
check("a profile pasted without https:// is accepted", r.status_code == 200 and r.json()["kind"] == "profile", r.text)

print()
print("3. Refused destinations")
for label, dest in [
    ("another site", "https://evil.example/p/pst_123456"),
    ("a lookalike host", "https://kinjy.com.evil.example/"),
    ("a lookalike name", "https://kinjy-com.net/"),
    ("credentials in the address", f"https://user:pw@kinjy.com/p/{post['id']}"),
    ("javascript:", "javascript:alert(1)"),
    ("data:", "data:text/html,hi"),
    ("ftp", "ftp://kinjy.com/"),
    ("protocol-relative", "//evil.example/"),
    ("malformed", "https://[kinjy.com/"),
    ("a wrong port", "https://kinjy.com:8443/"),
    ("an admin page", "/admin"),
    ("admin wallets", "/admin/wallets"),
    ("moderation", "/moderation"),
    ("the account dashboard", "/dashboard"),
    ("a password reset with its token", "/reset-password?token=abc"),
    ("a payout wallet confirmation", "/payout-wallet/confirm?t=abc"),
    ("an order (payment)", "/market/orders/ord_123"),
    ("the API", "/api/auth/logout"),
    ("a logout action", "/logout"),
    ("an OAuth callback", "/auth/callback?code=x&state=y"),
    ("a secret in the query", "/pricing?access_token=abc"),
    ("a code parameter", "/feeds?code=123456"),
    ("path traversal", "/u/demo/../../admin"),
    ("an encoded slash", "/p/pst_abcdef%2F..%2Fadmin"),
    ("an unknown page", "/no-such-page"),
    ("a bad access code", "/communities/demo?access=<x>"),
    ("an empty field", " "),
]:
    r = link(alice, dest)
    check(f"{label} is refused with a message", r.status_code in (400, 422) and r.json().get("detail"),
          f"{r.status_code} {r.text[:120]}")

print()
print("4. A short link pasted back in")
r = link(bob, invite["url"])
check("resolved to its destination, not chained", r.status_code == 200 and r.json()["destination"] == f"/p/{post['id']}",
      r.text[:200])
check("and it does not inherit Alice's invitation", r.json()["include_invitation"] is False
      and psql(f"select member_id from auth.share_links where token='{r.json()['token']}'") == bob["id"])
check("an unknown short link is refused", link(bob, f"{SITE}/s/ZZZZZZZZZ").status_code == 400)

print()
print("5. Opening a link")
r = open_link(neutral["token"])
check("neutral: one 302 to the page, no token", r.status_code == 302 and r.headers["location"] == f"/p/{post['id']}",
      r.headers)
check("never cached by a shared cache", "no-store" in r.headers.get("cache-control", "")
      and "private" in r.headers.get("cache-control", "") and "user-agent" in r.headers.get("vary", "").lower(), r.headers)
r = open_link(invite["token"])
check("invitation: one 302 carrying the link's token only",
      r.status_code == 302 and r.headers["location"] == f"/p/{post['id']}?via={invite['token']}", r.headers)
check("no code or account id in the redirect", alice["code"] not in r.headers["location"]
      and alice["id"] not in r.headers["location"])
resolved = c.get(f"/auth/share-links/{invite['token']}").json()
check("the browser learns the destination and the sharer's name only",
      resolved["invitation"] is True and resolved["inviter"] == {"display_name": "Demo Alice"}
      and alice["code"] not in str(resolved) and alice["id"] not in str(resolved), resolved)
resolved = c.get(f"/auth/share-links/{neutral['token']}").json()
check("a neutral link names nobody", resolved["invitation"] is False and resolved["inviter"] is None, resolved)

print()
print("6. Registering through links")
carol = register("Demo Carol", referral_link=invite["token"])
check("an invitation link credits the member who made it", invited_by(carol) == alice["id"], invited_by(carol))
dave = register("Demo Dave", referral_link=neutral["token"])
check("a neutral link credits nobody (the pool decides, as without a link)", invited_by(dave) != alice["id"],
      invited_by(dave))
gina = register("Demo Gina", referral_code=bob["code"], referral_link=invite["token"])
check("an explicit code takes precedence over a link, as before", invited_by(gina) == bob["id"], invited_by(gina))
hank = register("Demo Hank", referral_code=alice["code"])
check("?ref=CODE registration credits as before", invited_by(hank) == alice["id"])
frank = register("Demo Frank", referral_link="ZZZZZZZZZ")
check("an unknown token does not refuse the account", frank["id"] and invited_by(frank) != alice["id"])
before = invited_by(bob)
open_link(invite["token"])
check("an existing member opening a link keeps their sponsor", invited_by(bob) == before)
r = c.post("/auth/register", json={"email": alice["email"], "handle": "x" + alice["handle"], "display_name": "Demo",
                                   "password": "Sup3rStrong!Pass", "date_of_birth": "1990-02-02",
                                   "referral_link": invite["token"]})
check("self-referral: the sharer cannot sign up again through their own link", r.status_code == 409, r.status_code)
check("attribution is recorded at registration only (opening links created none)",
      psql(f"select count(*) from auth.users where invited_by='{alice['id']}'") == "2")

print()
print("7. Page permissions still apply")
private = c.post("/posts", headers=H(bob), json={"body": "Demo private words for followers", "visibility": "followers"}).json()
p_link = link(bob, f"/p/{private['id']}").json()
r = open_link(p_link["token"])
check("a followers-only post's link only points at the page", r.headers.get("location") == f"/p/{private['id']}")
check("which still refuses a stranger", c.get(f"/posts/{private['id']}").status_code in (401, 403, 404))
check("and a non-follower", c.get(f"/posts/{private['id']}", headers=H(carol)).status_code in (403, 404))
slug = "demo" + "".join(random.choices(string.ascii_lowercase, k=6))
c.post("/communities", headers=H(bob), json={"name": "Demo Secret Circle", "slug": slug, "kind": "secret"})
s_link = link(bob, f"/communities/{slug}").json()
check("a secret community without its access code stays closed",
      c.get(f"/communities/{slug}", headers=H(carol)).status_code in (403, 404))
com = c.post("/communities", headers=H(bob), json={"name": f"Demo Group {slug}", "slug": slug + "o", "kind": "public",
                                                    "description": "A demo community for links"}).json()
c_link = link(alice, f"/communities/{com['slug']}", invite=True).json()

print()
print("8. Previews for crawlers")
users_before = psql("select count(*) from auth.users")
r = open_link(invite["token"], CRAWLER)
page = r.text
check("a crawler gets an HTML page, not a redirect", r.status_code == 200 and "text/html" in r.headers["content-type"])
check("with the post's own description", "Demo public post for short links" in page, page[:400])
check("image and description present", 'property="og:image"' in page and 'property="og:description"' in page)
check("og:url is the short link", f'property="og:url" content="{invite["url"]}"' in page, page[:600])
check("no invitation in the page (no token in the refresh, no code, no id)",
      "via=" not in page and alice["code"] not in page and alice["id"] not in page, page)
check("no script", "<script" not in page.lower())
check("not shared-cached either", "no-store" in r.headers.get("cache-control", ""))
r = open_link(link(alice, f"/p/{article['id']}").json()["token"], "WhatsApp/2.23")
check("an article's headline", "Demo article" in r.text, r.text[:400])
r = open_link(c_link["token"], "TelegramBot (like TwitterBot)")
check("a public community preview names it", f"Demo Group {slug}" in r.text and "A demo community" in r.text, r.text[:300])
r = open_link(s_link["token"], "Slackbot-LinkExpanding 1.0")
check("a secret community preview does not", "Demo Secret Circle" not in r.text, r.text[:300])
r = open_link(p_link["token"], "LinkedInBot/1.0")
check("a followers-only post previews generically", "Demo private words" not in r.text, r.text[:400])
check("crawler visits created no account and no attribution", psql("select count(*) from auth.users") == users_before)

print()
print("9. Management")
mine = c.get("/auth/share-links", headers=H(alice)).json()
tokens = [i["token"] for i in mine["items"]]
check("my list has my links", neutral["token"] in tokens and invite["token"] in tokens, tokens)
check("and none of Bob's", p_link["token"] not in tokens)
check("newest first", mine["items"][0]["created_at"] >= mine["items"][-1]["created_at"])
row = next(i for i in mine["items"] if i["token"] == invite["token"])
check("each row: url, destination, invitation, date, status",
      all(k in row for k in ("url", "destination", "include_invitation", "created_at", "active")), row)
check("signed out: no list", c.get("/auth/share-links").status_code == 401)
r = c.post(f"/auth/share-links/{invite['token']}/disable", headers=H(bob))
check("Bob cannot disable Alice's link (404)", r.status_code == 404, r.status_code)
check("nor somebody signed out", c.post(f"/auth/share-links/{invite['token']}/disable").status_code == 401)
check("still active after those attempts", open_link(invite["token"]).headers.get("location", "").startswith("/p/"))
check("there is no way to change a link", c.patch(f"/auth/share-links/{invite['token']}", headers=H(alice),
                                                  json={"destination": "/"}).status_code in (404, 405))
r = c.post(f"/auth/share-links/{invite['token']}/disable", headers=H(alice))
check("Alice disables her link", r.status_code == 200 and r.json()["active"] is False, r.text[:200])
check("a disabled link no longer opens (helpful page, not the destination)",
      open_link(invite["token"]).headers.get("location") == "/link-unavailable")
check("nor resolves", c.get(f"/auth/share-links/{invite['token']}").status_code == 404)
ivy = register("Demo Ivy", referral_link=invite["token"])
check("and no longer credits at sign-up", invited_by(ivy) != alice["id"])
fresh = link(alice, f"/p/{post['id']}", invite=True).json()
check("a new link can be made for the same page after disabling", fresh["token"] != invite["token"] and fresh["created"])
psql(f"update auth.share_links set destination='https://evil.example/x' where token='{neutral['token']}'")
check("a destination altered in the database is checked again and refused",
      open_link(neutral["token"]).headers.get("location") == "/link-unavailable")
bob_link = link(bob, "/pricing", invite=True).json()
psql(f"update auth.users set status='deleted' where id='{bob['id']}'")
r = open_link(bob_link["token"])
check("a link whose sharer has left opens without the invitation", r.headers.get("location") == "/pricing", r.headers)
psql(f"update auth.users set status='active' where id='{bob['id']}'")
check("unknown and malformed tokens: the 'link unavailable' page",
      open_link("ZZZZZZZZZ").headers.get("location") == "/link-unavailable"
      and open_link("..%2F..%2Fetc").status_code in (302, 404))

print()
print("10. Concurrency, collisions, rate limit, restart")
judy = register("Demo Judy")
dest = f"/p/{article['id']}"
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    results = list(pool.map(lambda _: httpx.post(f"{BASE}/auth/share-links", headers=H(judy),
                                                 json={"destination": dest}, timeout=60).json(), range(12)))
got = {r.get("token") for r in results}
check("twelve simultaneous requests: one link", len(got) == 1 and None not in got, got)
check("one row in the database", psql(f"select count(*) from auth.share_links where member_id='{judy['id']}'") == "1")
# A forced collision: the first two tokens drawn are already taken.
taken = next(iter(got))
collision = subprocess.run(
    ["docker", "compose", "exec", "-T", "auth-service", "python", "-c", f"""
import itertools, main
from common.database import SessionLocal
real = main.sharelinks.new_token
draws = itertools.chain(['{taken}', '{taken}'], iter(real, None))
main.sharelinks.new_token = lambda: next(draws)
class P: user_id = '{judy['id']}'
with SessionLocal() as db:
    out = main.create_share_link(main.ShareLinkIn(destination='/pricing'), P(), db)
print(out['token'], out['created'])
"""], capture_output=True, text=True)
parts = collision.stdout.strip().split()
check("a token collision is retried with a new token", len(parts) == 2 and parts[0] != taken and parts[1] == "True",
      collision.stdout + collision.stderr[-400:])
check("the colliding token still belongs to its owner",
      psql(f"select destination from auth.share_links where token='{taken}'") == dest)
burst = [link(judy, f"/explore?n={i}").status_code for i in range(25)]
check("creation is rate limited (20 a minute)", 429 in burst and burst[:18].count(200) == 18, burst)
subprocess.run(["docker", "compose", "restart", "auth-service"], capture_output=True)
for _ in range(40):
    try:
        if httpx.get(f"{AUTH}/health", timeout=3).status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(2)
r = open_link(taken)
check("after a restart the link opens the same way", r.status_code == 302 and r.headers["location"] == dest, r.headers)

print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
