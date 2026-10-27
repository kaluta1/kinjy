"""Invitation attribution held by the server (27/10), end to end against the local stack.

A followed invitation is captured server-side for REFERRAL_ATTRIBUTION_DAYS
and applied at registration whatever the form says; expiry; several links
(the configured policy); no invitation; an existing member following a link;
invalid, inactive and tampered cases; a cleared cookie; the cookie's flags;
an administrator's correction and its audit record.

    python backend/tests/e2e_referral_attribution.py
"""
import random
import string
import subprocess
import sys
from datetime import date

import httpx

BASE = "http://localhost:8200/api"
ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def psql(sql):
    return subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", "kaluta", "-d", "kaluta", "-tAc", sql],
                          capture_output=True, text=True).stdout.strip()


def browser():
    """One visitor's browser: its own cookie jar."""
    return httpx.Client(base_url=BASE, timeout=60)


def register(client, name="Demo Invitee", **extra):
    t = tag()
    email = f"{t}@example.com"
    r = client.post("/auth/register", json={
        "email": email, "handle": f"u{t}", "display_name": name, "password": "Sup3rStrong!Pass",
        "date_of_birth": date(1990, 2, 2).isoformat(), "country": "US", **extra})
    assert r.status_code == 201, r.text
    d = r.json()
    return {"h": {"Authorization": f"Bearer {d['tokens']['access_token']}"}, "id": d["user"]["id"], "email": email,
            "code": d["user"]["referral_code"], "sponsor": d["user"].get("invited_by"), "response": r}


def method_of(user_id):
    return psql(f"select method from auth.referral_attributions where user_id = '{user_id}'")


inviter = register(browser(), "Demo Inviter")
other = register(browser(), "Demo Other")

print("== a followed link, then the code field cleared or changed ==")
b = browser()
r = b.post("/auth/invitation/capture", json={"code": inviter["code"]})
check("capture answers who invited", r.status_code == 200 and r.json()["inviter"]["display_name"] == "Demo Inviter", r.text)
cookie = r.headers.get("set-cookie", "")
check("cookie is HttpOnly", "httponly" in cookie.lower(), cookie)
check("cookie is SameSite=Lax", "samesite=lax" in cookie.lower(), cookie)
check("cookie lasts 30 days", "max-age=2592000" in cookie.lower(), cookie)
check("cookie holds no code or name", inviter["code"] not in cookie and "Demo" not in cookie, cookie)
check("held invitation shown to sign-up", b.get("/auth/invitation").json()["inviter"]["display_name"] == "Demo Inviter")
u = register(b)  # the field cleared: no code sent at all
check("cleared field: still credited to the inviter", u["sponsor"] == inviter["id"], u)
check("recorded as captured", method_of(u["id"]) == "captured")
check("capture spent: cookie cleared at registration", "kinjy_ref=" in u["response"].headers.get("set-cookie", "") and
      ("max-age=0" in u["response"].headers.get("set-cookie", "").lower() or "expires=" in u["response"].headers.get("set-cookie", "").lower()))
check("capture time and expiry recorded",
      psql(f"select count(*) from auth.referral_attributions where user_id = '{u['id']}' and captured_at is not null and expires_at > registered_at") == "1")

b = browser()
b.post("/auth/invitation/capture", json={"code": inviter["code"]})
u = register(b, referral_code=other["code"])  # the field changed to someone else
check("changed field: the captured inviter still wins", u["sponsor"] == inviter["id"], u)

print("== expired ==")
b = browser()
b.post("/auth/invitation/capture", json={"code": inviter["code"]})
psql(f"update auth.referral_captures set expires_at = now() - interval '1 minute' where referrer_id = '{inviter['id']}' and used_by is null")
check("expired: not shown any more", b.get("/auth/invitation").json()["inviter"] is None)
u = register(b)
check("expired: not credited to the inviter", u["sponsor"] != inviter["id"], u)
check("expired: falls back to the existing no-invitation rule", method_of(u["id"]) in ("pool", "none"), method_of(u["id"]))

print("== several links (policy: %s) ==" % (psql("select 1") and "last, the default"))
b = browser()
b.post("/auth/invitation/capture", json={"code": inviter["code"]})
r = b.post("/auth/invitation/capture", json={"code": other["code"]})
u = register(b)
check("newest invitation link wins (existing Kinjy rule, REFERRAL_ATTRIBUTION_POLICY=last)", u["sponsor"] == other["id"], (r.text, u))
b = browser()
b.post("/auth/invitation/capture", json={"code": inviter["code"]})
r = b.post("/auth/invitation/capture", json={"code": "ZZZZZZZZ"})
check("an unknown code does not displace the held one", r.status_code == 404 and register(b)["sponsor"] == inviter["id"], r.text)

print("== no invitation ==")
u = register(browser())
check("no invitation: existing pool / none rule", method_of(u["id"]) in ("pool", "none") and u["sponsor"] != inviter["id"])
u = register(browser(), referral_code=inviter["code"])
check("typed code without a link still works", u["sponsor"] == inviter["id"] and method_of(u["id"]) == "code", u)

print("== existing member, self-referral, ineligible referrers ==")
member = browser()
r = member.post("/auth/invitation/capture", json={"code": other["code"]}, headers=inviter["h"])
check("a signed-in member is not captured", r.status_code == 200 and r.json()["captured"] is False and "set-cookie" not in r.headers, r.text)
me = member.get("/auth/me", headers=inviter["h"]).json()
check("their sponsor is unchanged", me.get("invited_by") == inviter["sponsor"], me)
r = browser().post("/auth/invitation/capture", json={"code": inviter["code"]}, headers=inviter["h"])
check("self-referral: own link while signed in captures nothing", r.json()["captured"] is False)
gone = register(browser(), "Demo Gone")
psql(f"update auth.users set status = 'deactivated' where id = '{gone['id']}'")
r = browser().post("/auth/invitation/capture", json={"code": gone["code"]})
check("inactive referrer cannot be captured", r.status_code == 404, r.text)
r = browser().post("/auth/register", json={
    "email": f"{tag()}@example.com", "handle": f"u{tag()}", "display_name": "Demo X", "password": "Sup3rStrong!Pass",
    "date_of_birth": "1990-02-02", "country": "US", "referral_code": gone["code"]})
check("inactive referrer's typed code refused", r.status_code == 400, r.text)
later = register(browser(), "Demo Later")
b = browser()
b.post("/auth/invitation/capture", json={"code": later["code"]})
psql(f"update auth.users set status = 'deactivated' where id = '{later['id']}'")
u = register(b)
check("referrer closed after capture: not credited", u["sponsor"] != later["id"], u)

print("== tampering, cleared cookies ==")
b = browser()
b.post("/auth/invitation/capture", json={"code": inviter["code"]})
value = b.cookies.get("kinjy_ref")
b.cookies.clear()
b.cookies.set("kinjy_ref", value[:-1] + ("0" if value[-1] != "0" else "1"), domain="localhost")
check("tampered cookie ignored", b.get("/auth/invitation").json()["inviter"] is None)
b = browser()
b.post("/auth/invitation/capture", json={"code": inviter["code"]})
b.cookies.clear()  # the visitor deleted their cookies
u = register(b)
check("cleared cookies: invitation not recreated", u["sponsor"] != inviter["id"] and method_of(u["id"]) in ("pool", "none"), u)

print("== administrator correction ==")
admin = register(browser(), "Demo Admin")
psql(f"update auth.users set role = 'admin' where id = '{admin['id']}'")
login = httpx.post(f"{BASE}/auth/login", json={"email": admin["email"], "password": "Sup3rStrong!Pass"}, timeout=60).json()
ah = {"Authorization": f"Bearer {login['tokens']['access_token']}"}
target = register(browser())
r = httpx.post(f"{BASE}/admin/referrals/{target['id']}/sponsor", json={"sponsor": inviter["id"], "reason": "x"}, headers=ah, timeout=60)
check("a reason is required", r.status_code == 422, r.text)
r = httpx.post(f"{BASE}/admin/referrals/{target['id']}/sponsor", json={"sponsor": inviter["id"], "reason": "Invitation proven by support ticket"},
               headers=target["h"], timeout=60)
check("a member cannot correct a sponsor", r.status_code in (401, 403), r.text)
r = httpx.post(f"{BASE}/admin/referrals/{target['id']}/sponsor", json={"sponsor": target["id"], "reason": "Invitation proven by support ticket"},
               headers=ah, timeout=60)
check("no self-sponsorship by correction", r.status_code == 400, r.text)
before = target["sponsor"]
r = httpx.post(f"{BASE}/admin/referrals/{target['id']}/sponsor", json={"sponsor": inviter["id"], "reason": "Invitation proven by support ticket"},
               headers=ah, timeout=60)
check("admin corrects the sponsor", r.status_code == 200 and r.json()["sponsor"]["user_id"] == inviter["id"], r.text)
corr = r.json()["corrections"]
check("audit record: from, to, by, reason, time",
      len(corr) == 1 and (corr[0]["from"] or {}).get("user_id") == before and corr[0]["to"]["user_id"] == inviter["id"]
      and corr[0]["by"] == admin["id"] and corr[0]["reason"].startswith("Invitation") and corr[0]["at"], corr)

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
