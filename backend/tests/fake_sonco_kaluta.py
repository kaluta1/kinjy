"""A stand-in for Sonco Pay, Kaluta KYC and a mail server, for e2e_soncopay.py.

Run on the host; the stack reaches it through host.docker.internal (see
tests/compose.sonco-test.yml). Nothing here moves money or opens a real
identity session, which is the point: those cost real money and are tested
against the real services only by a human, with a real key.

HTTP on 8790 speaks the parts of both APIs Kinjy uses, plus /_ctl routes the
test drives. SMTP on 8791 keeps every message so the test can follow the
confirmation link.
"""
from __future__ import annotations

import json
import re
import socketserver
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {
    "payments": {},        # id -> payment
    "payouts": {},         # id -> payout
    "sessions": {},        # id -> kaluta session
    "mails": [],
    "payout_mode": "ok",   # ok | refuse | unreachable
    "kp_invoices": {},     # Kaluta Pay invoices
    "kp_escrow": {},       # invoice id -> lines
    "payout_calls": 0,
}
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _kp_key(self):
        return (self.headers.get("X-Api-Key") or "").startswith("kpk_")

    def do_PUT(self):
        p = self.path.split("?")[0]
        body = self._body()
        with LOCK:
            m = re.fullmatch(r"/kp/v1/invoices/([^/]+)/escrow", p)
            if m and m.group(1) in STATE["kp_invoices"]:
                inv = STATE["kp_invoices"][m.group(1)]
                if inv["status"] != "paid":
                    return self._send(409, {"detail": "no paid payment on this invoice yet"})
                STATE["kp_escrow"][inv["id"]] = body.get("lines", [])
                return self._send(200, {"lines": body.get("lines", [])})
        self._send(404, {"detail": "not found"})

    def do_GET(self):
        p = self.path.split("?")[0]
        with LOCK:
            if p == "/kp/v1/payment-methods":
                return self._send(200, [
                    {"id": "crypto:USDT:BSC", "kind": "crypto", "method": "crypto", "asset": "USDT",
                     "network": "BSC", "label": "USDT BSC", "enabled": True},
                    {"id": "mtn_bj", "kind": "mobile_money", "method": "mobile_money", "code": "mtn_bj",
                     "label": "MTN Benin", "countries": ["BJ"], "fields": ["phone"], "enabled": True},
                ])
            m = re.fullmatch(r"/kp/v1/invoices/([^/]+)", p)
            if m and m.group(1) in STATE["kp_invoices"]:
                return self._send(200, STATE["kp_invoices"][m.group(1)])
            if p == "/v1/payout-fees":
                return self._send(200, {"fees": {"USDT:BSC": "0.50", "USDT:TRON": "1"}, "min_payout_usd": "5"})
            m = re.fullmatch(r"/v1/payments/([^/]+)/methods", p)
            if m and m.group(1) in STATE["payments"]:
                pay = STATE["payments"][m.group(1)]
                return self._send(200, {"status": pay["status"], "amount": pay["amount"], "currency": pay["currency"],
                                        "methods": [
                    {"method": "mobile_money", "code": "mtn_bj", "operator": "MTN", "label": "MTN Benin",
                     "countries": ["BJ"], "pay_currency": "XOF", "estimated_pay_amount": "6100", "fields": ["phone"]},
                    {"method": "crypto", "asset": "USDT", "network": "BSC", "network_label": "BNB Smart Chain",
                     "estimated_pay_amount": pay["amount"]},
                    {"method": "card", "code": "card", "label": "Bank card"},
                ]})
            m = re.fullmatch(r"/v1/payments/([^/]+)", p)
            if m and m.group(1) in STATE["payments"]:
                return self._send(200, STATE["payments"][m.group(1)])
            m = re.fullmatch(r"/v1/payouts/([^/]+)", p)
            if m and m.group(1) in STATE["payouts"]:
                return self._send(200, STATE["payouts"][m.group(1)])
            m = re.fullmatch(r"/v1/sessions/([^/]+)/public", p)
            if m and m.group(1) in STATE["sessions"]:
                s = STATE["sessions"][m.group(1)]
                return self._send(200, {"session_id": s["session_id"], "status": s["status"]})
            if p == "/_ctl/state":
                return self._send(200, STATE)
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        body = self._body()
        with LOCK:
            if p == "/v1/payments":
                if not (self.headers.get("X-Api-Key") or "").startswith("sp_"):
                    return self._send(401, {"detail": "bad key", "code": "authentication_error"})
                for pay in STATE["payments"].values():
                    if pay["order_id"] == body.get("order_id"):
                        return self._send(200, pay)
                pid = "pay_" + uuid.uuid4().hex[:12]
                pay = {"id": pid, "order_id": body.get("order_id"), "amount": body.get("amount"),
                       "currency": body.get("currency"), "status": "pending",
                       "checkout_url": f"https://fake.soncopay.test/pay/{pid}",
                       "return_url": body.get("return_url"), "callback_url": body.get("callback_url")}
                STATE["payments"][pid] = pay
                return self._send(201, pay)
            if p == "/v1/payouts":
                STATE["payout_calls"] += 1
                mode = STATE["payout_mode"]
                if mode == "refuse":
                    return self._send(400, {"detail": "Insufficient balance", "code": "insufficient_balance"})
                if mode == "unreachable":
                    return self._send(503, {"detail": "down"})
                for po in STATE["payouts"].values():
                    if po["reference"] == body.get("reference"):
                        return self._send(200, po)
                oid = "po_" + uuid.uuid4().hex[:12]
                po = {"id": oid, "reference": body.get("reference"), "amount": body.get("amount"),
                      "asset": body.get("asset"), "network": body.get("network"),
                      "address": body.get("address"), "status": "pending", "tx_hash": None}
                STATE["payouts"][oid] = po
                return self._send(201, po)
            if p == "/v1/sessions":
                if not self.headers.get("X-API-Key"):
                    return self._send(401, {"detail": "no key"})
                sid = "ses_" + uuid.uuid4().hex[:12]
                s = {"session_id": sid, "status": "created", "request": body,
                     "verification_url": f"https://fake.kalutakyc.test/v/{sid}",
                     "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))}
                STATE["sessions"][sid] = s
                return self._send(201, {k: s[k] for k in ("session_id", "verification_url", "expires_at")})
            m = re.fullmatch(r"/v1/payments/([^/]+)/method", p)
            if m and m.group(1) in STATE["payments"]:
                pay = STATE["payments"][m.group(1)]
                pay["chosen"] = body
                kind = body.get("method")
                if kind == "mobile_money":
                    if (body.get("phone") or "").endswith("0000000"):
                        return self._send(400, {"detail": "This number is not a mobile money account", "code": "bad_phone"})
                    pay["next_action"] = {"type": "approve_on_phone", "phone": body.get("phone"),
                                          "operator": "MTN", "amount": "6100", "currency": "XOF"}
                elif kind == "crypto":
                    pay["next_action"] = {"type": "send_crypto", "asset": body.get("asset"),
                                          "network": body.get("network"), "address": "0xfeedfeedfeedfeedfeedfeedfeedfeedfeedfeed",
                                          "amount": pay["amount"], "amount_rule": "exact"}
                else:
                    pay["next_action"] = {"type": "redirect", "url": f"https://fake.soncopay.test/card/{pay['id']}"}
                return self._send(200, pay)
            if p == "/kp/v1/invoices":
                if not self._kp_key():
                    return self._send(401, {"detail": "invalid API key"})
                for inv in STATE["kp_invoices"].values():
                    if inv["order_ref"] == body.get("order_ref"):
                        return self._send(201, inv)
                iid = "bil_" + uuid.uuid4().hex[:12]
                inv = {"id": iid, "status": "open", "amount": body.get("amount"), "currency": body.get("currency"),
                       "amount_paid": 0, "order_ref": body.get("order_ref"), "customer": body.get("customer"),
                       "metadata": body.get("metadata"), "url": f"https://fake.kalutapay.test/i/{iid}",
                       "checkouts": []}
                STATE["kp_invoices"][iid] = inv
                return self._send(201, inv)
            m = re.fullmatch(r"/kp/v1/invoices/([^/]+)/checkout", p)
            if m and m.group(1) in STATE["kp_invoices"]:
                inv = STATE["kp_invoices"][m.group(1)]
                method = body.get("method", "")
                if method.startswith("crypto:"):
                    na = {"type": "send_crypto", "asset": method.split(":")[1], "network": method.split(":")[2],
                          "address": "0xkpkpkpkpkpkpkpkpkpkpkpkpkpkpkpkpkpkpkpkp", "amount": inv["amount"],
                          "amount_rule": "exact"}
                else:
                    na = {"type": "approve_on_phone", "phone": body.get("phone"), "amount": "6100", "currency": "XOF"}
                chk = {"id": "chk_" + uuid.uuid4().hex[:10], "provider": method, "status": "pending",
                       "next_action": na, "request": body}
                inv["checkouts"].insert(0, chk)
                return self._send(201, {"checkout": chk, "invoice": inv})
            if p == "/_ctl/kp_paid":
                inv = STATE["kp_invoices"][body["id"]]
                inv.update({"status": "paid", "amount_paid": body.get("amount_paid", inv["amount"])})
                for chk in inv["checkouts"]:
                    chk["status"] = "completed"
                return self._send(200, inv)
            # --- controls ---
            if p == "/_ctl/payment":
                STATE["payments"][body["id"]].update(body.get("set", {}))
                return self._send(200, STATE["payments"][body["id"]])
            if p == "/_ctl/payout":
                STATE["payouts"][body["id"]].update(body.get("set", {}))
                return self._send(200, STATE["payouts"][body["id"]])
            if p == "/_ctl/session":
                STATE["sessions"][body["id"]]["status"] = body["status"]
                return self._send(200, {"ok": True})
            if p == "/_ctl/mode":
                STATE["payout_mode"] = body["payout_mode"]
                return self._send(200, {"ok": True})
        self._send(404, {"detail": "not found"})


class SMTPHandler(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib.send_message, without TLS or auth."""

    def handle(self):
        def say(line):
            self.wfile.write((line + "\r\n").encode())

        say("220 fake ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd = line.decode(errors="replace").strip().upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                say("250 fake")
            elif cmd.startswith("DATA"):
                say("354 go ahead")
                data = []
                while True:
                    row = self.rfile.readline()
                    if row in (b".\r\n", b".\n", b""):
                        break
                    data.append(row.decode(errors="replace"))
                with LOCK:
                    STATE["mails"].append("".join(data))
                say("250 queued")
            elif cmd.startswith("QUIT"):
                say("221 bye")
                return
            else:
                say("250 ok")


if __name__ == "__main__":
    smtp = socketserver.ThreadingTCPServer(("0.0.0.0", 8791), SMTPHandler)
    smtp.daemon_threads = True
    threading.Thread(target=smtp.serve_forever, daemon=True).start()
    print("fake sonco/kaluta on :8790, smtp on :8791", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8790), Handler).serve_forever()
