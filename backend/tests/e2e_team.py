"""The staff and its roles.

moderator: review and moderation only. admin: the whole console. superadmin:
also decides who is on the team. Only a superadmin changes the team, nobody
changes their own role, and Kinjy always keeps one superadmin.
"""
import random
import string
import subprocess
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


def register(name):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    email = f"{t}@example.com"
    r = c.post("/auth/register", json={
        "email": email, "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "date_of_birth": date(1990, 2, 2).isoformat(), "country": "UG",
    })
    r.raise_for_status()
    return email, r.json()["user"]


def login(email):
    r = c.post("/auth/login", json={"email": email, "password": "Sup3rStrong!Pass"})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}


def set_role(user_id, role):
    """Seeding the first superadmin, as production does by hand."""
    user = subprocess.run(["docker", "exec", "kaluta-postgres", "printenv", "POSTGRES_USER"], capture_output=True, text=True).stdout.strip()
    db = subprocess.run(["docker", "exec", "kaluta-postgres", "printenv", "POSTGRES_DB"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["docker", "exec", "kaluta-postgres", "psql", "-U", user, "-d", db, "-tAc",
                    f"update auth.users set role='{role}' where id='{user_id}'"], check=True, capture_output=True)


health = httpx.get("http://localhost:8201/health", timeout=5).json()
check("port 8201 is auth-service", "auth" in str(health), health)

boss_email, boss = register("Test Boss")
mod_email, mod = register("Test Moderator")
set_role(boss["id"], "superadmin")
B = login(boss_email)

M = login(mod_email)
check("a member cannot see the team", c.get("/admin/team", headers=M).status_code == 403)
check("nor the review queue", c.get("/admin/classification-queue", headers=M).status_code == 403)

r = c.post("/admin/team", headers=B, json={"who": mod_email, "role": "moderator"})
check("a superadmin adds a moderator by email", r.status_code == 201 and r.json()["role"] == "moderator", r.text)
M = login(mod_email)
check("the moderator reaches the review queue", c.get("/admin/classification-queue", headers=M).status_code == 200)
check("and the appeals", c.get("/admin/moderation/appeals", headers=M).status_code == 200)
check("but not the accounts list", c.get("/admin/users", headers=M).status_code == 403)
check("nor the ledger", c.get("/admin/trial-balance", headers=M).status_code == 403)
check("a moderator sees the team", c.get("/admin/team", headers=M).status_code == 200)
check("but cannot change it", c.post("/admin/team", headers=M, json={"who": boss_email, "role": "moderator"}).status_code == 403)

r = c.patch(f"/admin/team/{mod['id']}", headers=B, json={"role": "admin"})
check("a superadmin makes them admin", r.status_code == 200 and r.json()["role"] == "admin", r.text)
M = login(mod_email)
check("an admin reaches the accounts list", c.get("/admin/users", headers=M).status_code == 200)
check("an admin cannot change the team", c.patch(f"/admin/team/{boss['id']}", headers=M, json={"role": "admin"}).status_code == 403)

r = c.patch(f"/admin/team/{boss['id']}", headers=B, json={"role": "admin"})
check("nobody changes their own role", r.status_code == 400, r.text)
r = c.post("/admin/team", headers=B, json={"who": "nobody-here@example.com", "role": "moderator"})
check("an unknown account is refused (by auth-service, not a missing route)",
      r.status_code == 404 and "No active account" in r.text, r.text)
r = c.post("/admin/team", headers=B, json={"who": "@" + mod["handle"], "role": "moderator"})
check("a @handle works too", r.status_code == 201 and r.json()["role"] == "moderator", r.text)

listed = {m["user_id"]: m for m in c.get("/admin/team", headers=B).json()["items"]}
check("the team lists both", boss["id"] in listed and mod["id"] in listed, list(listed))
check("and says the superadmin may manage it", c.get("/admin/team", headers=B).json()["can_manage"] is True)

r = c.delete(f"/admin/team/{mod['id']}", headers=B)
check("taken off the team", r.status_code == 200 and r.json()["role"] == "member", r.text)
M = login(mod_email)
check("and back to an ordinary member", c.get("/admin/classification-queue", headers=M).status_code == 403)

# leave the local database as it was
set_role(boss["id"], "member")

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
