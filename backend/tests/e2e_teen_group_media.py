"""A teenager put into a group sees its photos only after accepting it.

What the age suite does not cover:

* the live frame - the signed link travels over the socket too, and a frame
  that carried it would undo the withholding on the read path;
* an adult put into a group is accepted at once, as before: only minors wait;
* a restart no longer accepts every pending request. A migration ran on every
  boot and marked them all accepted, so each deploy opened every request.

The port is probed first, as everywhere.
"""
import asyncio
import json
import random
import string
import subprocess
import sys
import time
from datetime import date

import httpx
import websockets

BASE = "http://localhost:8200/api"
WS = "ws://localhost:8200/api/ws"
c = httpx.Client(base_url=BASE, timeout=30)
ok = True

def _png() -> bytes:
    """A real 1x1 PNG, built rather than pasted, so it decodes."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = zlib.compress(bytes([0, 255, 200, 0, 255]))
    return (bytes([137, 80, 78, 71, 13, 10, 26, 10]) + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


PNG = _png()


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {detail}"))
    if not cond:
        ok = False


def register(name, age=30):
    t = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    d = date.today()
    r = c.post("/auth/register", json={
        "email": f"{t}@example.com", "handle": f"u{t}", "display_name": name,
        "password": "Sup3rStrong!Pass", "country": "US",
        "date_of_birth": date(d.year - age, d.month, min(d.day, 28)).isoformat(),
    })
    r.raise_for_status()
    body = r.json()
    return body["tokens"]["access_token"], body["user"]


def H(token):
    return {"Authorization": f"Bearer {token}"}


def connect(a_t, a, b_t, b):
    c.post(f"/connections/{b['id']}", headers=H(a_t), json={"message": None})
    c.post(f"/connections/{a['id']}/respond?accept=true", headers=H(b_t))


health = httpx.get("http://localhost:8207/health", timeout=5).json()
check("port 8207 is messaging-service", "messaging" in str(health), health)

teen_a_t, teen_a = register("Test Teen A", 16)
teen_b_t, teen_b = register("Test Teen B", 17)
adult_t, adult = register("Test Adult")
adult2_t, adult2 = register("Test Adult Two")
connect(teen_a_t, teen_a, teen_b_t, teen_b)
connect(adult_t, adult, adult2_t, adult2)

print("Who starts unaccepted")
teen_room = c.post("/conversations", headers=H(teen_a_t), json={
    "kind": "group", "title": "Study", "participant_ids": [teen_b["id"]], "encrypted": False}).json()["id"]
adult_room = c.post("/conversations", headers=H(adult_t), json={
    "kind": "group", "title": "Grown-ups", "participant_ids": [adult2["id"]], "encrypted": False}).json()["id"]


def accepted(token, cid):
    return {x["id"]: x for x in c.get("/conversations", headers=H(token)).json()["items"]}[cid].get("accepted")


check("a teen added to a group starts unaccepted", accepted(teen_b_t, teen_room) is False)
check("the teen who made it is accepted", accepted(teen_a_t, teen_room) is True)
check("an adult added to a group is accepted at once", accepted(adult2_t, adult_room) is True)

print("The live frame")


async def frame_for(token, send):
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"action": "auth", "token": token}))
        await asyncio.wait_for(ws.recv(), 5)
        await asyncio.get_running_loop().run_in_executor(None, send)
        try:
            while True:
                frame = json.loads(await asyncio.wait_for(ws.recv(), 4))
                if frame.get("type") == "message" and frame.get("conversation_id") == teen_room:
                    return frame
        except asyncio.TimeoutError:
            return None


def send_photo():
    up = c.post("/media/upload", headers=H(teen_a_t),
                files={"file": ("p.png", PNG, "image/png")}, data={"purpose": "chat"})
    up.raise_for_status()
    c.post(f"/conversations/{teen_room}/messages", headers=H(teen_a_t),
           json={"body": "look", "media_id": up.json()["id"]}).raise_for_status()


frame = asyncio.run(frame_for(teen_b_t, send_photo))
check("the unaccepted teen gets the live frame", frame is not None)
check("without the signed link", frame and frame.get("media_withheld") is True and not frame.get("media_url"), frame)
frame = asyncio.run(frame_for(teen_a_t, send_photo))
check("the sender's own frame keeps it", frame and frame.get("media_url") and not frame.get("media_withheld"), frame)

c.post(f"/conversations/{teen_room}/accept", headers=H(teen_b_t))
frame = asyncio.run(frame_for(teen_b_t, send_photo))
check("once accepted, the live frame carries the photo", frame and frame.get("media_url"), frame)

print("A restart keeps pending requests pending")
s_t, s = register("Test Sender")
r_t, r = register("Test Receiver")
c.patch("/preferences", headers=H(r_t), json={"who_can_message": "everyone"})
direct = c.post("/conversations", headers=H(s_t), json={
    "kind": "direct", "participant_ids": [r["id"]], "encrypted": False})
check("a request is opened", direct.status_code == 201, direct.text)
direct_id = direct.json()["id"]
check("it is pending for the receiver", accepted(r_t, direct_id) is False)
subprocess.run(["docker", "restart", "kaluta-messaging"], check=True, capture_output=True)
for _ in range(40):
    try:
        if httpx.get("http://localhost:8207/health", timeout=2).status_code == 200:
            break
    except Exception:
        pass
    time.sleep(1)
check("after a restart it is still pending", accepted(r_t, direct_id) is False)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
