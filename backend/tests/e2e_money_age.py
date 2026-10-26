"""Age gates on money and livestreaming, against the live API.

Money is where an age rule stops being a judgement about suitability and
becomes a legal one. A 15-year-old seeing an unsuitable post is a moderation
failure; a 15-year-old entering a contract, selling to strangers or receiving a
payout is a different problem with different consequences.
"""
import random
import string
import sys
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


def tag():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def born(age):
    t = date.today()
    return date(t.year - age, t.month, min(t.day, 28)).isoformat()


def register(age):
    t = tag()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": f"Demo {t}",
        "password": "Sup3rStrong!Pass", "date_of_birth": born(age), "country": "US",
    })
    r.raise_for_status()
    d = r.json()
    return d["tokens"]["access_token"], d["user"]


def auth(t):
    return {"Authorization": f"Bearer {t}"}


adult_tok, adult = register(30)
teen14_tok, _ = register(14)
teen17_tok, _ = register(17)

print("\n== selling ==")
listing = {"title": "Woven basket", "vendor_price": "20.00"}
for tok, who, expect in ((teen14_tok, "14", 403), (teen17_tok, "17", 403), (adult_tok, "adult", 201)):
    r = c.post("/commerce/products", headers=auth(tok), json=listing)
    check(f"a {who}-year-old listing a product -> {expect}", r.status_code == expect,
          f"{r.status_code} {r.text[:110]}")
    if who == "adult":
        product_id = r.json().get("id")

print("\n== buying ==")
for tok, who, expect in ((teen14_tok, "14", 403), (teen17_tok, "17", 403), (adult_tok, "adult", 400)):
    r = c.post("/commerce/orders", headers=auth(tok), json={"acknowledge_policy": True, "policy_version": "2026-10-27", "product_id": product_id})
    # The adult is refused for a different reason - you cannot buy your own
    # listing - which is exactly the point: they got past the age gate.
    check(f"a {who}-year-old ordering -> {expect}", r.status_code == expect,
          f"{r.status_code} {r.text[:110]}")

print("\n== paying ==")
intent = {"purpose": "subscription", "amount": "9.99"}
for tok, who, expect in ((teen14_tok, "14", 403), (teen17_tok, "17", 403), (adult_tok, "adult", 201)):
    r = c.post("/payments/checkout", headers=auth(tok), json=intent)
    check(f"a {who}-year-old at checkout -> {expect}", r.status_code == expect,
          f"{r.status_code} {r.text[:110]}")

print("\n== being paid ==")
# The payout wallet (Sonco Pay) since the NowPayments destinations were removed
# on 27/10. Only the refusal is exercised: an accepted wallet sends a
# confirmation e-mail.
r = c.put("/payments/payout-wallet", headers=auth(teen17_tok), json={"address": "0x" + "a" * 40})
check("a 17 adding a payout wallet -> 403", r.status_code == 403, f"{r.status_code} {r.text[:110]}")

print("\n== becoming a creator ==")
for tok, who, expect in ((teen17_tok, "17", 403), (adult_tok, "adult", 200)):
    r = c.post("/creators/enable", headers=auth(tok))
    check(f"a {who} enabling monetization -> {expect}", r.status_code == expect,
          f"{r.status_code} {r.text[:110]}")

print("\n== the referral pool seat ==")
r = c.post("/payments/referral-pool/join", headers=auth(teen17_tok))
check("a 17-year-old buying a pool seat is refused", r.status_code == 403,
      f"{r.status_code} {r.text[:110]}")

print("\n== every refusal reads the same ==")
messages = set()
for path, body in (("/commerce/products", listing), ("/payments/checkout", intent)):
    r = c.post(path, headers=auth(teen14_tok), json=body)
    if r.status_code == 403:
        messages.add(r.json().get("detail", ""))
check("one wording for every gated feature", len(messages) == 1, messages)
check("it names no age and no rule",
      all("18" not in m and "adult" not in m.lower() for m in messages), messages)

print("\n== the client can ask in advance ==")
r = c.get("/creators/eligibility", headers=auth(teen14_tok))
check("eligibility answers", r.status_code == 200, r.text[:120])
if r.status_code == 200:
    body = r.json()
    check("it reports the tier", body.get("age_tier") == "TEEN_HIGH_PROTECTION", body)
    check("every money feature is false for a 14-year-old",
          not any(body["features"].values()), body["features"])

r = c.get("/creators/eligibility", headers=auth(adult_tok))
if r.status_code == 200:
    check("and true for an adult", all(r.json()["features"].values()), r.json()["features"])

print("\n== livestreaming ==")
for tok, who, expected in ((teen14_tok, "14", False), (teen17_tok, "17", False),
                           (adult_tok, "adult", True)):
    r = c.get("/live/eligibility", headers=auth(tok))
    check(f"a {who} may go live: {expected}",
          r.status_code == 200 and r.json().get("allowed") is expected,
          f"{r.status_code} {r.text[:120]}")

r = c.get("/live/eligibility", headers=auth(adult_tok))
if r.status_code == 200:
    policy = r.json().get("policy", {})
    check("the policy states its minimum age", policy.get("minimum_age") == 18, policy)
    check("and names the version it came from", bool(policy.get("policy_version")), policy)
    check("the answer admits there is no streaming stack yet",
          "not built" in r.json().get("note", ""), r.json().get("note"))

print("\n" + ("ALL CHECKS PASSED" if ok else "THERE ARE FAILURES ABOVE"))
sys.exit(0 if ok else 1)
