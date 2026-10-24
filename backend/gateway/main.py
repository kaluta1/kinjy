"""Kinjy · gateway

Single public entry point. The browser only ever talks to this origin, so the
frontend needs no per-service URLs and no cross-origin dance.

Routing is prefix-based and declared in ROUTES below. Two rules matter:

* ``/internal/*`` is never proxied. Those endpoints trust their caller and must
  stay on the private network — exposing one would let anyone post to the ledger.
* The Authorization header is forwarded untouched; the gateway does not mint,
  inspect or rewrite tokens. Each service verifies for itself.
"""
from __future__ import annotations

import logging
import time

import httpx
import asyncio

import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from common import settings

log = logging.getLogger("gateway")

# prefix -> upstream. Longest prefix wins, so "/api/ads" beats "/api/a".
ROUTES: dict[str, str] = {
    "/api/auth": "http://auth-service:8000",
    "/api/admin/users": "http://auth-service:8000",
    "/api/admin/team": "http://auth-service:8000",
    "/api/users": "http://user-service:8000",
    "/api/circles": "http://user-service:8000",
    "/api/connections": "http://user-service:8000",
    "/api/wellbeing": "http://user-service:8000",
    "/api/preferences": "http://user-service:8000",
    "/api/supervision": "http://user-service:8000",
    "/api/posts": "http://social-service:8000",
    "/api/link-preview": "http://social-service:8000",
    "/api/share": "http://social-service:8000",
    "/api/comments": "http://social-service:8000",
    "/api/moderation": "http://social-service:8000",
    "/api/feed": "http://social-service:8000",
    # Link previews for shared posts (the page crawlers read, and its picture).
    "/api/og": "http://social-service:8000",
    # A member's own numbers (social-service /stats/me).
    "/api/stats": "http://social-service:8000",
    "/api/shorts": "http://social-service:8000",
    "/api/algorithms": "http://social-service:8000",
    "/api/communities": "http://community-service:8000",
    "/api/forums": "http://community-service:8000",
    "/api/discover": "http://community-service:8000",
    "/api/threads": "http://community-service:8000",
    "/api/replies": "http://community-service:8000",
    "/api/family": "http://family-service:8000",
    "/api/memorials": "http://memorial-service:8000",
    "/api/conversations": "http://messaging-service:8000",
    "/api/notifications": "http://messaging-service:8000",
    "/api/presence": "http://messaging-service:8000",
    "/api/stickers": "http://messaging-service:8000",
    "/api/push": "http://messaging-service:8000",
    "/api/creators": "http://creator-service:8000",
    "/api/live": "http://creator-service:8000",
    "/api/subscriptions": "http://creator-service:8000",
    "/api/badges": "http://creator-service:8000",
    "/api/commerce": "http://commerce-service:8000",
    "/api/ads": "http://commerce-service:8000",
    "/api/ledger": "http://ledger-service:8000",
    "/api/wallet": "http://ledger-service:8000",
    "/api/commissions": "http://ledger-service:8000",
    "/api/leaders-pool": "http://ledger-service:8000",
    "/api/leaders": "http://ledger-service:8000",
    "/api/payments": "http://payment-service:8000",
    "/api/kyc": "http://payment-service:8000",
    "/api/ai": "http://ai-service:8000",
    "/api/assistant": "http://ai-service:8000",
    "/api/media": "http://media-service:8000",
    "/media": "http://media-service:8000",
}

# Admin routes fan out to whichever service owns them.
ADMIN_ROUTES: dict[str, str] = {
    "/api/admin/journals": "http://ledger-service:8000",
    "/api/admin/trial-balance": "http://ledger-service:8000",
    "/api/admin/reconcile": "http://ledger-service:8000",
    "/api/admin/leaders-pool": "http://ledger-service:8000",
    "/api/admin/referral-pool": "http://auth-service:8000",
    "/api/admin/moderation": "http://social-service:8000",
    "/api/admin/classification": "http://social-service:8000",
    "/api/admin/classification-queue": "http://social-service:8000",
    "/api/admin/disputes": "http://commerce-service:8000",
    "/api/admin/contact-risk": "http://messaging-service:8000",
    "/api/admin/age": "http://auth-service:8000",
    "/api/admin/refunds": "http://payment-service:8000",
    "/api/admin/pool-refunds": "http://payment-service:8000",
    "/api/admin/kyc": "http://payment-service:8000",
    "/api/admin/fraud": "http://auth-service:8000",
    "/api/admin/payouts": "http://payment-service:8000",
    "/api/admin/memorials": "http://memorial-service:8000",
    "/api/admin/ads": "http://commerce-service:8000",
    "/api/admin/assistant": "http://ai-service:8000",
    "/api/admin/ai": "http://ai-service:8000",
}

ALL_ROUTES = {**ROUTES, **ADMIN_ROUTES}
SORTED_PREFIXES = sorted(ALL_ROUTES, key=len, reverse=True)

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

app = FastAPI(title="Kinjy · gateway", version="0.1.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3030", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=False)


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _client is not None:
        await _client.aclose()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway", "routes": len(ALL_ROUTES)}


@app.get("/api/status")
async def status():
    """Health of every upstream — the one call an ops dashboard needs."""
    upstreams = sorted(set(ALL_ROUTES.values()))
    results = {}
    for url in upstreams:
        name = url.split("//")[1].split(":")[0]
        try:
            response = await _client.get(f"{url}/health", timeout=3)
            results[name] = {"status": "ok" if response.status_code == 200 else "degraded"}
        except Exception as exc:
            results[name] = {"status": "down", "error": type(exc).__name__}
    healthy = sum(1 for r in results.values() if r["status"] == "ok")
    return {"healthy": healthy, "total": len(results), "services": results}


# --- WebSocket relay ---------------------------------------------------------
# Real-time chat has to reach messaging-service, and the browser only ever talks
# to this origin. Starlette will not proxy a socket for us, so the gateway holds
# both ends and pumps frames between them until either side closes.
WS_ROUTES: dict[str, str] = {
    "/api/ws": "ws://messaging-service:8000/ws",
}


@app.websocket("/api/ws")
async def websocket_relay(client: WebSocket):
    # Authentication is the first frame, which this relay passes through like
    # any other; the token never appears in a URL, so no access log holds it.
    upstream_url = WS_ROUTES["/api/ws"]
    await client.accept()

    try:
        async with websockets.connect(upstream_url, open_timeout=8) as upstream:

            async def to_upstream() -> None:
                while True:
                    await upstream.send(await client.receive_text())

            async def to_client() -> None:
                async for message in upstream:
                    await client.send_text(
                        message if isinstance(message, str) else message.decode()
                    )

            pump_up = asyncio.create_task(to_upstream())
            pump_down = asyncio.create_task(to_client())
            done, pending = await asyncio.wait(
                {pump_up, pump_down}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            # Surface a genuine upstream failure instead of a silent close.
            for task in done:
                if task.exception() and not isinstance(
                    task.exception(), (WebSocketDisconnect, asyncio.CancelledError)
                ):
                    log.warning("ws relay ended: %s", task.exception())

            if pump_down in done:
                # Upstream closed first. Hand the browser the same close code:
                # 4401 tells the client to refresh its token, and a relay that
                # replaced it with a generic close left the client retrying
                # with a dead token forever.
                code = upstream.close_code or 1000
                try:
                    await client.close(code=code if code != 1006 else 1011)
                except RuntimeError:
                    pass  # the browser had already gone
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("ws relay could not reach messaging-service: %s", exc)
        # 1011 = the gateway itself failed, which is what happened.
        await client.close(code=1011)


def _resolve(path: str) -> tuple[str, str] | None:
    for prefix in SORTED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            upstream = ALL_ROUTES[prefix]
            # Strip the /api namespace; services expose their routes unprefixed.
            downstream = path[4:] if path.startswith("/api/") else path
            return upstream, downstream
    return None


# --- request bodies ----------------------------------------------------------
#
# Relaying a body as it arrives means an upstream connection is taken before the
# body is complete. Buffering used to hide that: a client trickling bytes only
# cost a coroutine, because nothing was sent upstream until it had finished. So
# the gateway now bounds what it relays — a size, an idle time between chunks
# and a total time — and answers 413 / 408 itself instead of holding a slot for
# a client that is not really sending.
#
# Only media-service takes files (200 MB, see its MAX_BYTES, plus multipart
# framing); every other route takes JSON.
BODY_LIMIT = 8 * 1024 * 1024
BODY_LIMIT_MEDIA = 201 * 1024 * 1024
BODY_IDLE_SECONDS = 20
BODY_DEADLINE = 60
BODY_DEADLINE_MEDIA = 900
# How long the gateway waits for media-service to answer an upload once it has sent
# the whole body. With post media at UploadCenter, media-service stages the file,
# sends it on (up to PUT_MAX_SECONDS in uploadcenter.py) and waits for their scan
# (up to READY_MAX_SECONDS), and measured on the real service that is anywhere from
# 12 to 134 seconds for the same size of file. A shorter wait here showed the member
# a 504 for an upload that went on to succeed, and left the file behind. It must not
# be shorter than media-service's own worst case; test_gateway_timeouts.py checks that.
UPLOAD_READ_SECONDS = 1200


class BodyRejected(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


async def guarded_body(request: Request, limit: int, deadline: float):
    """The client's body, chunk by chunk, refused if it is too big or too slow."""
    stream = request.stream().__aiter__()
    started, received = time.monotonic(), 0
    while True:
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            raise BodyRejected(408, "The request body took too long to arrive")
        try:
            chunk = await asyncio.wait_for(stream.__anext__(), timeout=min(BODY_IDLE_SECONDS, remaining))
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise BodyRejected(408, "The request body stopped arriving") from None
        received += len(chunk)
        if received > limit:
            raise BodyRejected(413, f"The request body exceeds {limit // (1024 * 1024)} MB")
        yield chunk


async def relay(response: httpx.Response):
    """The upstream body, closing the upstream connection however this ends.

    A client that disconnects mid-download makes the server raise inside this
    iteration, and Starlette then skips its background task — so the close has
    to live here, or the connection stays checked out of the pool.
    """
    try:
        async for chunk in response.aiter_raw():
            yield chunk
    finally:
        await response.aclose()


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy(full_path: str, request: Request):
    path = "/" + full_path

    if "/internal/" in path or path.endswith("/internal"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    resolved = _resolve(path)
    if resolved is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"No route for {path}", "available": SORTED_PREFIXES[:20]},
        )

    upstream, downstream = resolved
    # Values are stripped because a client can legally send a header with
    # trailing whitespace ("Bearer ") that httpx then refuses to forward — an
    # unsanitised pass-through turns a malformed request into a gateway 500.
    headers = {
        k: v.strip()
        for k, v in request.headers.items()
        if k.lower() not in HOP_BY_HOP and v.strip()
    }
    headers["X-Forwarded-For"] = request.client.host if request.client else ""
    headers["X-Forwarded-Host"] = request.headers.get("host", "")

    # The body goes upstream as it arrives, not after it has all arrived: a
    # 200 MB upload used to sit in this process in full (more than once, since
    # it was copied on the way through), per concurrent request.
    #
    # Content-Length is forwarded so the service sees the size up front and can
    # refuse early — except when the client also sent Transfer-Encoding, in which
    # case the two could disagree and the upstream framing is left to httpx.
    declared = request.headers.get("content-length", "").strip()
    declared_ok = declared.isascii() and declared.isdigit()
    chunked = "transfer-encoding" in request.headers
    has_body = chunked or (declared_ok and int(declared) > 0)
    if has_body and declared_ok and not chunked:
        headers["content-length"] = declared

    large = path == "/api/media" or path.startswith("/api/media/")
    limit = BODY_LIMIT_MEDIA if large else BODY_LIMIT
    if declared_ok and int(declared) > limit:
        # Said up front: refuse before a single byte goes upstream.
        return JSONResponse(status_code=413, content={"detail": f"The request body exceeds {limit // (1024 * 1024)} MB"})

    try:
        upstream_request = _client.build_request(
            request.method,
            f"{upstream}{downstream}",
            content=guarded_body(request, limit, BODY_DEADLINE_MEDIA if large else BODY_DEADLINE) if has_body else None,
            headers=headers,
            params=request.query_params,
            # Only an upload waits this long; every other call keeps the client's 30 s.
            timeout=(
                httpx.Timeout(30.0, connect=5.0, read=UPLOAD_READ_SECONDS)
                if large and request.method in ("POST", "PUT")
                else httpx.USE_CLIENT_DEFAULT
            ),
        )
        response = await _client.send(upstream_request, stream=True)
    except httpx.LocalProtocolError as exc:
        return JSONResponse(status_code=400, content={"detail": f"Malformed request: {exc}"})
    except httpx.ConnectError:
        return JSONResponse(
            status_code=503,
            content={"detail": f"{upstream.split('//')[1].split(':')[0]} is unavailable"},
        )
    except httpx.TimeoutException:
        return JSONResponse(status_code=504, content={"detail": "Upstream timed out"})
    except BodyRejected as exc:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
    except ClientDisconnect:
        # The client left mid-upload: nobody is there to answer.
        return Response(status_code=499)
    except httpx.TransportError:
        # The service closed the connection while the body was still going in,
        # typically having refused it early (too large, not allowed).
        return JSONResponse(status_code=502, content={"detail": "The service closed the connection"})

    out_headers = {k: v for k, v in response.headers.items() if k.lower() not in HOP_BY_HOP}
    content_type = response.headers.get("content-type")

    # No body to relay: answer directly rather than open a stream that would be
    # framed as chunked, which a HEAD, 204 or 304 must not be.
    if request.method == "HEAD" or response.status_code in (204, 304) or response.status_code < 200:
        await response.aclose()
        return Response(status_code=response.status_code, headers=out_headers, media_type=content_type)

    # The response goes back the same way: raw bytes, so a downloaded file is
    # never held whole here and a service's own Content-Encoding stays truthful.
    # Its Content-Length is kept so clients can show progress and resume.
    if "content-length" in response.headers and "transfer-encoding" not in response.headers:
        out_headers["content-length"] = response.headers["content-length"]
    return StreamingResponse(
        relay(response),
        status_code=response.status_code,
        headers=out_headers,
        media_type=content_type,
        background=BackgroundTask(response.aclose),
    )
