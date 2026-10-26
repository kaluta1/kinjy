"""A stand-in for the Anthropic Messages API, for e2e_one_to_many.py only.

Answers POST /v1/messages with a deterministic "translation" (the target
language's name prepended to the post), records each request, and can be told
to fail: GET /_control?mode=ok|error500|refusal|echo.
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"mode": "ok", "calls": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/_control"):
            match = re.search(r"mode=(\w+)", self.path)
            if match:
                STATE["mode"] = match.group(1)
            return self._send(200, STATE)
        self._send(404, {})

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        STATE["calls"] += 1
        STATE["last_headers"] = {k.lower(): v for k, v in self.headers.items() if k.lower().startswith("anthropic")}
        STATE["last_model"] = body.get("model")
        STATE["last_fallbacks"] = body.get("fallbacks")
        if STATE["mode"] == "error500":
            return self._send(500, {"type": "error", "error": {"type": "api_error", "message": "boom"}})
        system = body.get("system") or ""
        target = re.search(r"into ([A-Z][\w ]+?)\. ", system)
        text = body["messages"][0]["content"]
        post = re.sub(r"^<post>\n|\n</post>$", "", text)
        if STATE["mode"] == "echo":
            out = post
        else:
            out = f"({target.group(1) if target else '?'}) {post}"
        stop = "refusal" if STATE["mode"] == "refusal" else "end_turn"
        self._send(200, {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": body.get("model"),
            "content": [] if stop == "refusal" else [{"type": "text", "text": out}],
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8799), Handler).serve_forever()
