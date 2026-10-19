"""Kinjy · media-service — uploads, provenance labelling, static serving."""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urllib.parse import quote

from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, true, update
from sqlalchemy.orm import Session as OrmSession
from starlette.concurrency import run_in_threadpool

from common import crypto, mediasign, settings
from common.auth import CurrentUser, optional_principal
from common.database import SessionLocal, get_db
from common.ids import new_id
from common.service import create_app

import models
import profileimages
import imagesize
import poster as videocover
import preview
import signatures
import uploadcenter

log = logging.getLogger("media-service")

MAX_BYTES = 200 * 1024 * 1024  # 200 MB

# What a *post* may carry: formats every browser renders, because a post is
# something people view rather than download.
ALLOWED = {
    "image/jpeg": "image", "image/png": "image", "image/webp": "image", "image/gif": "image",
    "video/mp4": "video", "video/webm": "video",
    "audio/mpeg": "audio", "audio/ogg": "audio", "audio/wav": "audio",
    "application/pdf": "document",
}

# A chat attachment may be any file at all — a spreadsheet, a zip, a voice
# note. The types a browser can play or show get a player; everything else is
# a download.
CHAT_PLAYABLE = {
    **ALLOWED,
    "image/avif": "image",
    "video/quicktime": "video", "video/ogg": "video",
    "audio/webm": "audio", "audio/mp4": "audio", "audio/aac": "audio", "audio/x-m4a": "audio",
    "audio/wave": "audio", "audio/x-wav": "audio", "audio/flac": "audio",
}
PURPOSES = ("post", "chat", "memorial")

# A memorial's gallery (blueprint §8: "photos/videos/voice"). Narrower than a post
# on purpose: it is a public page that families fill from their phones, so it
# takes only what every browser plays, caps a video where the gateway (which holds
# a whole upload in memory) can carry it, and checks the file is what it says it
# is — a gallery is displayed to strangers, a post to followers.
MEMORIAL_TYPES = {
    "image/jpeg": "image", "image/png": "image", "image/webp": "image",
    "video/mp4": "video", "video/webm": "video",
}
MEMORIAL_MAX_BYTES = {"image": 25 * 1024 * 1024, "video": 50 * 1024 * 1024}
MEMORIAL_FORMATS_HINT = (
    "A memorial takes photos as JPEG, PNG or WebP and videos as MP4 or WebM. "
    "A video from an iPhone (.mov, or HEVC) has to be saved as MP4 (H.264) first."
)



# The only kinds ever served inline. Anything else — HTML, SVG, XML, scripts,
# unknown bytes — goes out as an opaque download: this service answers on the
# app's own origin, so a file a browser would *render* there could run script
# with the member's session. SVG is deliberately absent from every list.
INLINE_KINDS = {"image", "video", "audio", "document"}
# access is new. Existing rows default to public, which is correct for what
# is already there (avatars and marketing art); post media is marked restricted
# as it is attached.
MIGRATIONS = [
    # A video's cover picture.
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS poster_path VARCHAR(500)",
    f"ALTER TABLE {models.SCHEMA}.assets "
    "ADD COLUMN IF NOT EXISTS access VARCHAR(20) DEFAULT 'public'",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS provider VARCHAR(20) DEFAULT 'local'",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS external_id VARCHAR(80)",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS purpose VARCHAR(20)",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'ready'",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS status_checks INTEGER DEFAULT 0",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ",
    # Chat attachments: served only on a signed link, sealed on disk when a key
    # is set (common/crypto.py). A sealed name is longer than the name it hides.
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS private BOOLEAN NOT NULL DEFAULT FALSE",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS sealed_with VARCHAR(16)",
    f"ALTER TABLE {models.SCHEMA}.assets ALTER COLUMN filename TYPE TEXT",
    # The picture's shape, read at upload. Null for everything already stored,
    # which keeps being measured in the browser as it was.
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS width INTEGER",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS height INTEGER",
    # The feed-sized copy. Null for everything stored before it existed, until
    # /internal/media/backfill-previews has been over them.
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS preview_path VARCHAR(500)",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS preview_bytes BIGINT",
    f"ALTER TABLE {models.SCHEMA}.assets ADD COLUMN IF NOT EXISTS preview_type VARCHAR(100)",
]

app = create_app(
    name="media-service",
    schema=models.SCHEMA,
    description="Uploads with content provenance labels and hash-based deduplication.",
    migrations=MIGRATIONS,
    on_startup=[lambda: _start_resealing()],
)

ROOT = Path(settings.MEDIA_ROOT)
ROOT.mkdir(parents=True, exist_ok=True)


@app.post("/media/upload", status_code=201, tags=["media"])
async def upload(
    principal: CurrentUser,
    file: UploadFile = File(...),
    provenance: str = Form(default="original"),
    alt_text: str | None = Form(default=None),
    derived_from: str | None = Form(default=None),
    purpose: str = Form(default="post"),
    db: OrmSession = Depends(get_db),
):
    if provenance not in models.Asset.PROVENANCE:
        raise HTTPException(
            status_code=400,
            detail=f"provenance must be one of: {', '.join(models.Asset.PROVENANCE)}",
        )
    if purpose not in PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose must be one of: {', '.join(PURPOSES)}")
    content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if purpose == "memorial":
        kind = MEMORIAL_TYPES.get(content_type)
        if kind is None:
            raise HTTPException(status_code=415, detail=f"Unsupported content type: {file.content_type}. {MEMORIAL_FORMATS_HINT}")
    elif purpose == "chat":
        kind = CHAT_PLAYABLE.get(content_type, "file")
    else:
        kind = ALLOWED.get(content_type)
        if kind is None:
            # Named, with the way out. HEIC is the common case by a distance -
            # it is what an iPhone produces by default, no browser displays it,
            # and "Unsupported content type: image/heic" tells the member
            # nothing they can act on.
            if content_type in ("image/heic", "image/heif"):
                raise HTTPException(
                    status_code=415,
                    detail=(
                        "This photo is in Apple's HEIC format, which browsers cannot show. "
                        "On iPhone: Settings > Camera > Formats > Most Compatible, or share "
                        "the photo as JPEG."
                    ),
                )
            raise HTTPException(
                status_code=415,
                detail=f"{file.content_type} files are not supported here. Use JPEG, PNG, WebP or GIF.",
            )

    asset_id = new_id("mda")
    folder = ROOT / principal.user_id[:12]
    folder.mkdir(parents=True, exist_ok=True)
    # A chat attachment is private, and sealed on disk whenever a key is set.
    # Posts stay as they are: they are public by nature.
    private = purpose == "chat"
    seal = private and crypto.enabled()

    # A sealed file keeps no extension on disk: ".pdf" beside a random id
    # already says what the encryption is hiding.
    suffix = "" if seal else Path(file.filename or "").suffix[:10]
    destination = folder / f"{asset_id}{suffix}"

    digest = hashlib.sha256()
    written = 0
    limit = MEMORIAL_MAX_BYTES[kind] if purpose == "memorial" else MAX_BYTES
    # The start of the file, kept to read the picture's shape from. Taken from
    # the first chunk, which is a megabyte - far more than imagesize needs - so
    # this costs a slice and no extra read.
    head = b""
    with destination.open("wb") as out:
        sink = crypto.FileSealer(out, asset_id) if seal else out
        while chunk := await file.read(1024 * 1024):
            if written == 0:
                head = chunk[: imagesize.HEAD_BYTES]
            if written == 0 and purpose != "chat" and not signatures.matches(content_type, chunk[: signatures.HEAD_BYTES]):
                # Refused on the first chunk, before the rest is stored.
                out.close()
                destination.unlink(missing_ok=True)
                hint = f" {MEMORIAL_FORMATS_HINT}" if purpose == "memorial" else ""
                raise HTTPException(
                    status_code=415,
                    detail=f"The file does not look like {content_type}: its first bytes say otherwise.{hint}",
                )
            written += len(chunk)
            if written > limit:
                out.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"File exceeds the {limit // 1024 // 1024} MB limit")
            digest.update(chunk)
            sink.write(chunk)
        if seal:
            sink.close()
    if written == 0 and purpose != "chat":
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The file is empty")

    sha = crypto.private_fingerprint(digest.hexdigest()) if private else digest.hexdigest()
    # Same bytes already uploaded by this member: reuse instead of storing twice.
    # Only within the same visibility — a private attachment must never come
    # back as a post's public image, nor a public one quietly become private.
    existing = db.scalar(
        select(models.Asset).where(
            models.Asset.sha256 == sha,
            models.Asset.owner_id == principal.user_id,
            models.Asset.private.is_(private),
            models.Asset.purpose == "memorial"
            if purpose == "memorial"
            else or_(models.Asset.purpose.is_(None), models.Asset.purpose != "memorial"),
        )
    )
    if existing is not None:
        destination.unlink(missing_ok=True)
        return {**_describe(existing), "deduplicated": True}

    # The feed-sized copy, built from the staging file while it is still here.
    # It has to happen before the original goes to UploadCenter, because the
    # local copy is deleted the moment it does.
    preview_path: str | None = None
    preview_bytes: int | None = None
    preview_type: str | None = None
    goes_remote = _stores_remotely(purpose)
    if not seal and not private and preview.wanted(content_type, written, remote=goes_remote):
        previews = ROOT / "previews" / principal.user_id[:12]
        previews.mkdir(parents=True, exist_ok=True)
        made = await run_in_threadpool(preview.localise, destination, previews / asset_id, content_type)
        if made:
            path, size_on_disk, kind_on_disk = made
            preview_path, preview_bytes, preview_type = str(path), size_on_disk, kind_on_disk

    # A video's cover, from the same staging file and for the same reason: it
    # may be on its way to UploadCenter. Never for a sealed or private file -
    # a still from an encrypted attachment would be a plaintext copy of it.
    poster_path: str | None = None
    if kind == "video" and not seal and not private:
        covers = ROOT / "previews" / principal.user_id[:12]
        covers.mkdir(parents=True, exist_ok=True)
        made_poster = await run_in_threadpool(videocover.make, destination, covers / asset_id)
        poster_path = str(made_poster) if made_poster else None

    # Post media can live at UploadCenter instead of on the volume. It goes there
    # as a *private* file and is read back only through media-service, after the
    # viewer's ticket has been checked (see _serve_remote): no address that would
    # work without that check is ever handed to anyone.
    remote_id: str | None = None
    if goes_remote:
        if not _claim_remote_slot(principal.user_id):
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail="Too many uploads are in progress; try again in a moment")
        try:
            remote_id = await run_in_threadpool(_store_remotely, destination, written, content_type)
        except uploadcenter.UploadCenterError as exc:
            raise HTTPException(status_code=_remote_failure_status(exc), detail=_remote_failure_detail(exc)) from exc
        finally:
            _release_remote_slot(principal.user_id)
            # Whatever happened, the local copy was only a staging file.
            destination.unlink(missing_ok=True)

    # Read from the header, never decoded. None for video, audio, a format
    # with no reader, or a header that does not parse - the browser measures
    # those as it did before.
    size = imagesize.read(content_type, head)

    asset = models.Asset(
        id=asset_id,
        owner_id=principal.user_id,
        filename=(
            crypto.seal_text(file.filename or asset_id, f"asset:{asset_id}:filename")[1]
            if seal
            else file.filename or asset_id
        ),
        content_type=content_type,
        size_bytes=written,
        sha256=sha,
        storage_path="" if remote_id else str(destination),
        url=f"{settings.MEDIA_PUBLIC_BASE}/{asset_id}",
        kind=kind,
        provenance=provenance,
        provenance_signed=False,
        derived_from=derived_from,
        alt_text=alt_text,
        width=size[0] if size else None,
        height=size[1] if size else None,
        preview_path=preview_path,
        preview_bytes=preview_bytes,
        preview_type=preview_type,
        poster_path=poster_path,
        private=private,
        sealed_with=crypto.keyring().active_id if seal else None,
        # Restricted from the first byte, not only once attached: the file has
        # no public address to fall back on, and an upload nobody has attached
        # yet has no business being served without a ticket.
        **({"provider": "uploadcenter", "external_id": remote_id, "access": "restricted"} if remote_id else {}),
        # Only a memorial's files carry a purpose here; it is what lets the
        # discard route below delete them, and nothing else.
        purpose="memorial" if purpose == "memorial" else None,
    )
    db.add(asset)
    try:
        db.commit()
    except Exception:
        # Do not leave a file at UploadCenter that no row points to.
        db.rollback()
        if remote_id:
            _discard_stored("uploadcenter", remote_id, None)
        raise

    return {
        **_describe(asset),
        "note": "The provenance label is declared by the uploader. C2PA signing is not enabled yet.",
    }


# --- pictures imported from an article pasted from elsewhere -------------------

IMPORT_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")
IMPORT_MAX_BYTES = 15 * 1024 * 1024
IMPORT_PER_MINUTE = 60
_import_log: dict[str, list[float]] = {}


class ImportIn(BaseModel):
    url: str = Field(min_length=8, max_length=4000)
    alt_text: str | None = Field(default=None, max_length=500)


@app.post("/media/import-url", status_code=201, tags=["media"])
async def import_url(payload: ImportIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Copy a picture from a web address into the member's own post media.

    For an article pasted from another site or a document: its pictures are
    addresses there, and an address there can expire, move or be taken down -
    so the picture is copied once, here, and the article points at Kinjy's copy.

    Fetched like a link preview (common/safefetch): public addresses only,
    every redirect checked, images of the four web formats only, 15 MB at
    most. Then stored by the ordinary upload path, so the file's first bytes,
    the deduplication and the storage are exactly an upload's.
    """
    from io import BytesIO

    from starlette.datastructures import Headers

    from common import safefetch

    now = time.monotonic()
    recent = [t for t in _import_log.get(principal.user_id, []) if now - t < 60]
    if len(recent) >= IMPORT_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many pictures at once. Try again in a minute.")
    _import_log[principal.user_id] = [*recent, now]

    try:
        body, content_type, final_url = await run_in_threadpool(
            safefetch.fetch_bytes, payload.url.strip(),
            max_bytes=IMPORT_MAX_BYTES, accept=IMPORT_TYPES,
            agent="KinjyArticleImport/1.0 (+https://kinjy.com)",
        )
    except safefetch.UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=f"The picture could not be imported: {exc}")
    name = (final_url.rsplit("/", 1)[-1].split("?")[0] or "picture")[:80]
    upload_file = UploadFile(file=BytesIO(body), filename=name,
                             headers=Headers({"content-type": content_type}))
    described = await upload(
        principal, file=upload_file, provenance="original", alt_text=payload.alt_text,
        derived_from=None, purpose="post", db=db,
    )
    # A preview address for the author, signed like the one a reader gets:
    # the file is restricted, and the editor has no blob of it to show.
    described["preview_url"] = mediasign.sign_url(described["url"], described["id"], principal.user_id)
    return described


def _stores_remotely(purpose: str) -> bool:
    """Whether a new upload goes to UploadCenter rather than the media volume."""
    return purpose == "post" and settings.POST_MEDIA_PROVIDER == "uploadcenter" and uploadcenter.enabled()


def _store_remotely(path: Path, size: int, content_type: str) -> str:
    """Send a staged file to UploadCenter as private and wait for it to be ready.

    Runs in a worker thread: all of it is blocking I/O, and a 200 MB file must
    not hold the event loop. Returns UploadCenter's file id.
    """
    # The staging file's name is opaque; the member's own file name stays in our
    # database and is not sent to a third party.
    presigned = uploadcenter.presign(path.name, size, content_type, visibility="private")
    file_id, upload_url = presigned.get("file_id"), presigned.get("upload_url")
    if not file_id or not profileimages.upload_url_allowed(upload_url):
        if file_id:
            _discard_stored("uploadcenter", file_id, None)
        raise uploadcenter.UploadCenterError("The file storage returned an unusable upload address")
    try:
        uploadcenter.put_file(upload_url, str(path), size, content_type)
        uploadcenter.complete(file_id)
        record = uploadcenter.wait_until_ready(file_id, size)
        # Trust, then verify: if the service ignored "private" the file would have
        # a public address while we go on believing it has none.
        if record.get("visibility") != "private" or record.get("url") or record.get("trashed_at"):
            log.error("uploadcenter file %s is not private as asked: visibility=%r", file_id, record.get("visibility"))
            raise uploadcenter.UploadCenterError("The file storage did not keep the file private")
        if record.get("size_bytes") not in (None, size):
            raise uploadcenter.UploadCenterError("The file storage kept a different number of bytes")
    except Exception:
        # Nothing half-stored: a file that failed its scan, or never finished,
        # is removed there rather than left to count against the quota.
        _discard_stored("uploadcenter", file_id, None)
        raise
    return file_id


# Each remote upload holds a worker thread for the whole exchange, up to minutes.
# The pool is shared with every other synchronous route of this service, `serve()`
# included, so a few members uploading at once must not be able to fill it.
_REMOTE_SLOTS = 6
_REMOTE_PER_MEMBER = 2
_remote_slots = threading.BoundedSemaphore(_REMOTE_SLOTS)
_remote_by_member: dict[str, int] = {}
_remote_lock = threading.Lock()


def _claim_remote_slot(member_id: str) -> bool:
    with _remote_lock:
        if _remote_by_member.get(member_id, 0) >= _REMOTE_PER_MEMBER:
            return False
        if not _remote_slots.acquire(blocking=False):
            return False
        _remote_by_member[member_id] = _remote_by_member.get(member_id, 0) + 1
        return True


def _release_remote_slot(member_id: str) -> None:
    with _remote_lock:
        left = _remote_by_member.get(member_id, 0) - 1
        if left > 0:
            _remote_by_member[member_id] = left
        else:
            _remote_by_member.pop(member_id, None)
        _remote_slots.release()


def _remote_failure_status(exc: uploadcenter.UploadCenterError) -> int:
    return exc.status if exc.status in (422, 504) else 503


def _remote_failure_detail(exc: uploadcenter.UploadCenterError) -> str:
    # What the member may be told. UploadCenter's own wording and status codes
    # stay in the log: they describe our vendor, not the member's problem.
    if exc.status == 422:
        return "The storage service's checks refused this file"
    if isinstance(exc, uploadcenter.UploadTooSlow):
        return "The file took too long to upload; check your connection and try again"
    if exc.status == 504:
        return "The storage service took too long to check this file; try again"
    return "File storage is unavailable right now; try again"


# --- reading post media back from UploadCenter ---------------------------------
#
# A signed link lasts 900 seconds, is usable by anyone who holds it, and cannot
# be shortened (see uploadcenter.py). So it is used here and nowhere else: this
# service fetches the bytes with it and relays them to a viewer whose ticket has
# already been checked. The link is kept for a while, because asking UploadCenter
# for a new one on every request of a video player would be one API call per
# byte range.
_LINK_MARGIN_SECONDS = 120
_LINKS_MAX = 2000
_links: dict[str, tuple[str, float]] = {}
_links_lock = threading.Lock()


def _remote_link(asset: models.Asset, fresh: bool = False) -> str:
    key = asset.external_id or ""
    now = time.monotonic()
    if not fresh:
        with _links_lock:
            hit = _links.get(key)
        if hit and hit[1] > now:
            return hit[0]
    url, lifetime = uploadcenter.signed_url(key)
    if not profileimages.upload_url_allowed(url):
        raise uploadcenter.UploadCenterError("The file storage returned an unusable link")
    keep = lifetime - _LINK_MARGIN_SECONDS  # a link about to run out is not worth keeping
    if keep > 0:
        with _links_lock:
            if len(_links) >= _LINKS_MAX:
                for stale in sorted(_links, key=lambda k: _links[k][1])[: _LINKS_MAX // 4]:
                    del _links[stale]
            _links[key] = (url, now + keep)
    return url


def _forget_link(asset: models.Asset) -> None:
    with _links_lock:
        _links.pop(asset.external_id or "", None)


def _relay_remote(client, response):
    """The stored bytes, closing the connection to UploadCenter however this ends."""
    try:
        yield from response.iter_raw(1024 * 1024)
    finally:
        response.close()
        client.close()


def _serve_remote(asset: models.Asset, request: Request, media_type: str, inline: bool, headers: dict) -> Response:
    """Relay a post asset from UploadCenter. Only reached after serve()'s ticket check.

    Honours Range, which a video needs to play in Safari and to seek anywhere.
    UploadCenter's own headers are not passed on: its link forces
    ``Content-Disposition: attachment`` and names its storage host, neither of
    which belongs in what a member sees.
    """
    # Chat attachments are sealed on this volume and never stored remotely.
    if asset.private or not asset.external_id:
        raise HTTPException(status_code=404, detail="Asset not found")
    wanted = request.headers.get("range", "").strip()
    # One plain byte range, or none: a multi-range request would be answered with a
    # multipart body this relay does not describe.
    range_header = wanted if re.fullmatch(r"bytes=(\d+-\d*|-\d+)", wanted) else None
    client = response = None
    for attempt in (0, 1):
        try:
            client, response = uploadcenter.open_stream(_remote_link(asset, fresh=attempt == 1), range_header)
        except uploadcenter.UploadCenterError as exc:
            log.warning("media %s could not be read from UploadCenter: %s", asset.id, exc)
            raise HTTPException(status_code=503, detail="The file is temporarily unavailable") from exc
        if response.status_code in (200, 206, 416):
            break
        # An expired or withdrawn link: forget it and try once with a new one.
        log.info("media %s: UploadCenter answered %s", asset.id, response.status_code)
        response.close()
        client.close()
        _forget_link(asset)
        client = response = None
    if response is None or client is None:
        raise HTTPException(status_code=502, detail="The file is temporarily unavailable")

    if response.status_code == 416:
        content_range = response.headers.get("content-range") or f"bytes */{asset.size_bytes}"
        response.close()
        client.close()
        return Response(status_code=416, headers={"Content-Range": content_range})

    out = {
        **headers,
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(_filename(asset))}",
    }
    for name in ("content-length", "content-range"):
        if name in response.headers:
            out[name] = response.headers[name]
    return StreamingResponse(
        _relay_remote(client, response), status_code=response.status_code, media_type=media_type, headers=out,
    )


def _filename(asset: models.Asset) -> str:
    if not asset.sealed_with:
        return asset.filename
    try:
        return crypto.open_text(asset.sealed_with, asset.filename, f"asset:{asset.id}:filename")
    except crypto.DecryptionError:
        return "attachment"


def _describe(asset: models.Asset) -> dict:
    return {
        "id": asset.id,
        "url": asset.url,
        "kind": asset.kind,
        "content_type": asset.content_type,
        "filename": _filename(asset),
        "private": asset.private,
        "size_bytes": asset.size_bytes,
        "provenance": asset.provenance,
        "provenance_signed": asset.provenance_signed,
        # Null for video, audio and everything uploaded before the size was
        # read; the client measures those itself as it always did.
        "width": asset.width,
        "height": asset.height,
    }


@app.get("/internal/assets/{asset_id}", tags=["internal"])
def internal_asset(asset_id: str, db: OrmSession = Depends(get_db)):
    """What an asset is and who owns it — for services attaching one to something.

    messaging-service stores what this says rather than what the client
    claims, so a message cannot label a zip as a photo or attach another
    member's upload.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {**_describe(asset), "owner_id": asset.owner_id}


@app.get("/media/{asset_id}", tags=["media"])
def serve(
    asset_id: str,
    request: Request,
    v: str | None = None,
    e: str | None = None,
    s: str | None = None,
    exp: str | None = None,
    sig: str | None = None,
    p: str | None = None,
    poster: str | None = None,
    db: OrmSession = Depends(get_db),
):
    """Serve the bytes.

    Two kinds of protected asset, each with its own proof:

    - **restricted** (post media) needs a viewer-bound ticket, ``v``/``e``/``s``
      (common/mediasign.py). This endpoint deliberately does **not** decide
      whether the viewer is old enough - that decision was made by whichever
      service minted the ticket, which had the post, its classification and the
      viewer's tier in hand. Here it is arithmetic: is this signature ours, is
      it for this asset, has it expired, and does it match whoever is signed in.
    - **private** (chat attachments) needs a link messaging-service signed for
      a member of the conversation, ``exp``/``sig`` (common/crypto.py).

    An asset that is somehow both needs both. 404 rather than 403 throughout.
    Distinguishing "no such asset" from "you may not have this one" tells
    somebody which ids are worth passing on.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None or asset.status != "ready":
        raise HTTPException(status_code=404, detail="Asset not found")
    # Profile images on UploadCenter are public by construction; their CDN is
    # the place to fetch them from.
    if asset.provider == "uploadcenter" and asset.access == "public":
        if not profileimages.cdn_url_allowed(asset.url, uploadcenter.cdn_hosts()):
            raise HTTPException(status_code=404, detail="Asset not found")
        return RedirectResponse(asset.url, status_code=302)
    remote = asset.provider == "uploadcenter"
    if remote and asset.access != "restricted":
        # Past the profile-image redirect above, a remote file is post media, and
        # post media is never public. Whatever flipped this one, it is not served.
        log.error("media %s is remote and %s: refused", asset_id, asset.access)
        raise HTTPException(status_code=404, detail="Asset not found")
    if not remote and not os.path.exists(asset.storage_path):
        raise HTTPException(status_code=404, detail="Asset not found")

    if asset.access == "restricted":
        principal = optional_principal(request.headers.get("authorization"))
        ok, reason = mediasign.verify(
            asset_id, v, e, s,
            authenticated_as=principal.user_id if principal else None,
        )
        if not ok:
            log.info("media %s refused: %s", asset_id, reason)
            raise HTTPException(status_code=404, detail="Asset not found")
    if asset.private and not crypto.media_signature_valid(asset.id, exp, sig):
        log.info("media %s refused: missing, expired or forged attachment link", asset_id)
        raise HTTPException(status_code=404, detail="Asset not found")

    # Only what a browser can show safely is served inline; anything else
    # (HTML, SVG, archives...) is a download, so an upload can never run as a
    # page on this origin.
    inline = asset.kind in INLINE_KINDS and not asset.content_type.startswith("image/svg")
    media_type = asset.content_type if inline else "application/octet-stream"
    headers = _serve_headers(asset.content_type if inline else None)
    if asset.access == "restricted":
        # `private` is what keeps a shared cache out: a CDN that holds one
        # viewer's authorised response would serve it to the next. It was
        # `no-store`, which also forbade the *viewer's own* browser from
        # keeping it - so every scroll back up re-fetched every picture in the
        # feed, from object storage, through here. Nothing is shared by
        # letting a member's own browser keep a picture it is already looking
        # at, and only for as long as their ticket would have let them ask
        # again anyway.
        headers["Cache-Control"] = f"private, max-age={_ticket_seconds_left(e)}"
    elif asset.private:
        # Signed links are personal; no shared cache may keep one's response.
        headers["Cache-Control"] = "private, max-age=3600"

    # The feed and the grid ask for the display copy; the lightbox, a download
    # and anything that wants the real thing ask for the original. The ticket
    # is the same either way - it is signed over the asset, and this is the
    # same picture, smaller.
    # A video's cover, behind the same ticket as the video: it is a frame of
    # it, so whoever may not see the video may not see this either. No cover
    # is a 404, and the player draws its own.
    if poster:
        if asset.poster_path and os.path.exists(asset.poster_path):
            return FileResponse(
                asset.poster_path,
                media_type=videocover.CONTENT_TYPE,
                headers={**_serve_headers(videocover.CONTENT_TYPE), **headers},
                content_disposition_type="inline",
            )
        raise HTTPException(status_code=404, detail="No cover for this asset")

    if p and asset.preview_path and os.path.exists(asset.preview_path):
        local_type = asset.preview_type or preview.PREVIEW_CONTENT_TYPE
        return FileResponse(
            asset.preview_path,
            media_type=local_type,
            headers={**_serve_headers(local_type), **headers},
            content_disposition_type="inline",
        )

    if remote:
        return _serve_remote(asset, request, media_type, inline, headers)
    if not asset.sealed_with:
        return FileResponse(
            asset.storage_path,
            media_type=media_type,
            filename=_filename(asset),
            content_disposition_type="inline" if inline else "attachment",
            headers=headers,
        )
    return _serve_sealed(asset, request, media_type, inline, headers)


def _ticket_seconds_left(expires: str | None) -> int:
    """How long the viewer's ticket still has, as a cache lifetime.

    Caching past the ticket would leave a browser showing a picture it could
    no longer ask for, so the two run out together. Clamped at both ends: a
    malformed or absent value caches nothing, and nothing caches for a day.
    """
    try:
        left = int(expires or 0) - int(time.time())
    except ValueError:
        return 0
    return max(0, min(left, 86400))


def _serve_sealed(asset: models.Asset, request: Request, media_type: str, inline: bool, headers: dict) -> Response:
    """Decrypt on the way out, honouring Range.

    Range is not optional: Safari will not play a video whose server cannot
    answer a byte range, and seeking anywhere in a clip needs it everywhere.
    """
    # Checked before the first byte goes out: once a streaming response has
    # started, a missing key can only cut the download short.
    if not crypto.file_key_available(asset.storage_path):
        log.error("attachment %s cannot be opened: its key is not in the keyring", asset.id)
        raise HTTPException(status_code=404, detail="Asset not found")
    size = asset.size_bytes
    disposition = "inline" if inline else "attachment"
    headers = {
        **headers,
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(_filename(asset))}",
    }
    start, end, status = 0, size - 1, 200
    wanted = request.headers.get("range", "")
    if wanted.startswith("bytes=") and size:
        first, _, last = wanted[6:].split(",")[0].strip().partition("-")
        try:
            if first:
                start, end = int(first), (int(last) if last else size - 1)
            else:  # "bytes=-500": the last 500 bytes
                start, end = max(0, size - int(last)), size - 1
        except ValueError:
            start, end = 0, size - 1
        else:
            end = min(end, size - 1)
            if start > end:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(end - start + 1 if size else 0)
    return StreamingResponse(
        _stream_sealed(asset, size, start, end) if size else iter(()),
        status_code=status,
        media_type=media_type,
        headers=headers,
    )


def _stream_sealed(asset: models.Asset, size: int, start: int, end: int):
    """The decrypted range, stopping cleanly if a chunk fails to open.

    By then the status and headers are already sent, so the client sees a
    short body either way; this keeps it a logged event rather than an
    unhandled exception in the server.
    """
    try:
        yield from crypto.open_file_range(asset.storage_path, asset.id, size, start, end)
    except crypto.DecryptionError as exc:
        log.error("attachment %s stopped mid-stream: %s", asset.id, exc)


# --- encryption at rest: the attachments' backlog ---------------------------------
#
# messaging-service re-seals message text at startup; this is the same job for
# the attachment files, so that rotating the key ("k2:…,k1:…") really moves
# everything off k1 before k1 is removed. It also seals private files stored
# in the clear before the key was set.

_RESEAL_BATCH = 50


def _start_resealing() -> None:
    crypto.require_in_production("media-service")
    if crypto.enabled():
        # In the background: re-sealing a large backlog must not hold the boot.
        threading.Thread(target=_reseal_backlog, name="reseal-attachments", daemon=True).start()


def _reseal_backlog() -> None:
    """Re-seal every private attachment not yet under the active key. Idempotent."""
    active = crypto.keyring().active_id
    resealed = failed = 0
    skipped: set[str] = set()
    while True:
        leftovers: list[Path] = []
        with SessionLocal() as db:
            rows = db.scalars(
                select(models.Asset)
                .where(
                    models.Asset.private.is_(True),
                    or_(models.Asset.sealed_with.is_(None), models.Asset.sealed_with != active),
                    models.Asset.id.not_in(skipped) if skipped else true(),
                )
                .limit(_RESEAL_BATCH)
                .with_for_update(skip_locked=True)
            ).all()
            if not rows:
                break
            for asset in rows:
                try:
                    leftovers.append(_reseal_asset(asset))
                except (crypto.DecryptionError, OSError) as exc:
                    # Its key is gone, or the file is missing or damaged. Leave
                    # it exactly as it is: rewriting it would destroy the only copy.
                    log.error("attachment %s could not be re-sealed: %s", asset.id, exc)
                    skipped.add(asset.id)
                    failed += 1
                    continue
                resealed += 1
            db.commit()
        # Only once the rows point at the new files: until the commit, the old
        # path is still the one a request would read.
        for leftover in leftovers:
            leftover.unlink(missing_ok=True)
    if resealed or failed:
        log.info("re-sealed %d attachments with key %s; %d could not be opened", resealed, active, failed)


def _reseal_asset(asset: models.Asset) -> Path:
    """Re-seal one attachment's bytes and name; returns the old file, to delete after commit.

    The re-sealed copy goes to a new path named after the asset and the key
    (sealed files keep no extension), and the row is pointed at it in the same
    transaction that updates its key and name. Until that commit every request
    still reads the old file, untouched; there is no moment when the row and
    the bytes disagree.
    """
    source = Path(asset.storage_path)
    if not source.exists():
        raise OSError(f"{source} is missing")
    name = (
        crypto.open_text(asset.sealed_with, asset.filename, f"asset:{asset.id}:filename")
        if asset.sealed_with
        else asset.filename
    )
    destination = source.with_name(f"{asset.id}-{crypto.keyring().active_id}")
    staging = source.with_name(f"{asset.id}.resealing")
    try:
        key_id = crypto.reseal_file(str(source), str(staging), asset.id, asset.size_bytes)
        os.replace(staging, destination)
    finally:
        staging.unlink(missing_ok=True)
    asset.storage_path = str(destination)
    _, asset.filename = crypto.seal_text(name, f"asset:{asset.id}:filename")
    asset.sealed_with = key_id
    return source


def _serve_headers(inline_type: str | None) -> dict[str, str]:
    # Never let a browser second-guess the type into something runnable, and
    # even if it did, give the document no script and no origin. The one
    # exception is PDF: Chrome refuses to open a PDF under a sandbox policy,
    # and its viewer does not run the file's scripts in this origin anyway.
    headers = {"X-Content-Type-Options": "nosniff"}
    if inline_type != "application/pdf":
        headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
        )
    return headers


@app.post("/internal/media/{asset_id}/restrict", tags=["internal"])
def restrict_asset(asset_id: str, db: OrmSession = Depends(get_db)):
    """Mark an asset as needing a ticket.

    Called when the asset is attached to a post. Idempotent, and one-way on
    purpose: there is no internal route back to public, because the only reason
    to want one would be to clear a restriction somebody else applied.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset.access = "restricted"
    db.commit()
    return {"id": asset.id, "access": asset.access}


@app.post("/internal/media/sign", tags=["internal"])
def sign_assets(payload: dict):
    """Mint tickets for assets the caller has already authorised.

    The caller is responsible for the age decision; this only signs. It is on
    the private network for that reason - a public minting endpoint would be a
    public bypass.
    """
    asset_ids = [a for a in (payload.get("asset_ids") or []) if isinstance(a, str)][:100]
    viewer = payload.get("viewer_id")
    ttl = int(payload.get("ttl") or mediasign.DEFAULT_TTL_SECONDS)
    return {
        "tickets": {
            asset_id: mediasign.query_string(asset_id, viewer, ttl=ttl)
            for asset_id in asset_ids
        }
    }


@app.post("/internal/media/backfill-posters", tags=["internal"])
def backfill_posters(limit: int = 50, dry_run: bool = True, db: OrmSession = Depends(get_db)):
    """A cover for every video uploaded before there was one.

    Read from the local copy when there is one (every remote video has one, see
    preview.wanted), else from the file on the volume. dry_run by default;
    /internal is not routed through the gateway.
    """
    if not videocover.available():
        return {"error": "ffmpeg is not installed in this container"}
    videos = db.scalars(
        select(models.Asset)
        .where(
            models.Asset.kind == "video",
            models.Asset.poster_path.is_(None),
            models.Asset.private.is_(False),
            models.Asset.sealed_with.is_(None),
            models.Asset.status == "ready",
        )
        .limit(max(1, min(limit, 200)))
    ).all()
    built, failed = [], []
    for asset in videos:
        source = next(
            (Path(p) for p in (asset.preview_path, asset.storage_path) if p and os.path.exists(p)),
            None,
        )
        if source is None:
            failed.append(asset.id)
            continue
        if dry_run:
            built.append(asset.id)
            continue
        covers = ROOT / "previews" / asset.owner_id[:12]
        covers.mkdir(parents=True, exist_ok=True)
        made = videocover.make(source, covers / asset.id)
        if made:
            asset.poster_path = str(made)
            built.append(asset.id)
        else:
            failed.append(asset.id)
    if not dry_run:
        db.commit()
    return {"dry_run": dry_run, "candidates": len(videos), "built": len(built), "failed": failed}


@app.post("/internal/media/backfill-previews", tags=["internal"])
def backfill_previews(limit: int = 50, dry_run: bool = True, db: OrmSession = Depends(get_db)):
    """Build the display copy for pictures uploaded before there was one.

    Everything stored until now is served whole - a median of 375 kB and a
    long tail into the megabytes, into a column 560 px wide. This reads each
    original once, writes the derivative next to the others on the volume, and
    moves on; from then on the feed is served the small one.

    Unlike the dimensions backfill this does download whole files, because a
    picture cannot be resized from its header. It is therefore deliberately
    batched and slow, and safe to stop: a second run picks up where it left
    off, since a row with a preview is no longer selected.

    dry_run is the default. Reaching this needs access to the container,
    since /internal is not routed through the gateway.
    """
    assets = db.scalars(
        select(models.Asset)
        .where(
            models.Asset.kind.in_(("image", "video")),
            models.Asset.preview_path.is_(None),
            models.Asset.private.is_(False),
            models.Asset.status == "ready",
            models.Asset.content_type.in_(sorted(preview.SOURCES | preview.PLAYABLE)),
            # Big ones wherever they are stored, and *every* remote one
            # however small: a 30 kB thumbnail fetched through UploadCenter's
            # API and then from object storage is slow for reasons that have
            # nothing to do with its size.
            or_(models.Asset.size_bytes > 120 * 1024, models.Asset.external_id.isnot(None)),
        )
        .order_by(models.Asset.size_bytes.desc())  # the worst offenders first
        .limit(max(1, min(limit, 200)))
    ).all()

    built = skipped = failed = 0
    saved = 0
    reasons: dict[str, int] = {}

    def note(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for asset in assets:
        previews = ROOT / "previews" / asset.owner_id[:12]
        previews.mkdir(parents=True, exist_ok=True)
        # Where the original is read from, and whether this leaves a temporary
        # file behind to delete afterwards.
        staged: Path | None = None
        try:
            if asset.provider == "uploadcenter" and asset.external_id:
                staged = previews / f"{asset.id}.staging"
                client = response = None
                try:
                    client, response = uploadcenter.open_stream(_remote_link(asset), None)
                    if response.status_code != 200:
                        note(f"remote http {response.status_code}")
                        failed += 1
                        continue
                    with staged.open("wb") as out:
                        for chunk in response.iter_raw(1024 * 1024):
                            out.write(chunk)
                finally:
                    if response is not None:
                        response.close()
                    if client is not None:
                        client.close()
                source = staged
            elif asset.storage_path and os.path.exists(asset.storage_path):
                source = Path(asset.storage_path)
            else:
                note("nowhere to read from")
                failed += 1
                continue

            made = preview.localise(source, previews / asset.id, asset.content_type)
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            log.warning("preview backfill could not read %s: %s", asset.id, exc)
            note("read failed")
            failed += 1
            continue
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)

        if made is None:
            # Animated, unreadable, or the copy could not be written.
            note("no local copy could be made")
            skipped += 1
            continue

        path, size, kind_on_disk = made
        saved += max(0, (asset.size_bytes or 0) - size)
        if not dry_run:
            asset.preview_path, asset.preview_bytes, asset.preview_type = str(path), size, kind_on_disk
        else:
            path.unlink(missing_ok=True)
        built += 1

    if not dry_run:
        db.commit()

    remaining = db.scalar(
        select(func.count())
        .select_from(models.Asset)
        .where(
            models.Asset.kind.in_(("image", "video")),
            models.Asset.preview_path.is_(None),
            models.Asset.private.is_(False),
            models.Asset.content_type.in_(sorted(preview.SOURCES | preview.PLAYABLE)),
            or_(models.Asset.size_bytes > 120 * 1024, models.Asset.external_id.isnot(None)),
        )
    )
    return {
        "dry_run": dry_run,
        "examined": len(assets),
        "built": built,
        "skipped": skipped,
        "failed": failed,
        "reasons": reasons,
        "bytes_saved_per_view": saved,
        "still_without_preview": remaining,
    }


class AttachableIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=50)


@app.post("/media/attachable", tags=["media"])
def attachable(payload: AttachableIn, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Which of these files the caller may put in a post of their own.

    Asked by the article editor when something is pasted into it. An article
    copied from Kinjy carries its pictures as references to files, not as the
    files themselves, and a post may only carry its author's own post media -
    social-service refuses the whole post otherwise. So the editor asks before
    it pastes: references to the writer's own pictures are kept as they are,
    with nothing uploaded twice, and anybody else's are left out instead of
    turning the Save button into an error.

    The same four conditions social-service checks when the post is saved,
    asked in the same order, so this can never say yes to something the save
    will refuse. Only the caller's own files are named in the answer: an id
    that belongs to somebody else and an id that does not exist are both
    simply absent, so the route cannot be used to learn which ids are real.
    """
    wanted = [i for i in dict.fromkeys(payload.ids) if isinstance(i, str) and i]
    if not wanted:
        return {"attachable": []}
    rows = db.scalars(select(models.Asset).where(models.Asset.id.in_(wanted))).all()
    return {
        "attachable": [
            {"id": a.id, "kind": a.kind}
            for a in rows
            if a.owner_id == principal.user_id
            and a.purpose is None
            and not a.private
            and a.status == "ready"
            and a.url
        ]
    }


@app.get("/media/{asset_id}/provenance", tags=["media"])
def provenance(asset_id: str, db: OrmSession = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    # Provenance is a public record for public media; a private attachment has
    # none to show, and its fingerprint is not for outsiders.
    if asset is None or asset.private:
        raise HTTPException(status_code=404, detail="Asset not found")

    chain = []
    cursor: models.Asset | None = asset
    seen = set()
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        chain.append({"id": cursor.id, "provenance": cursor.provenance, "created_at": cursor.created_at})
        cursor = db.get(models.Asset, cursor.derived_from) if cursor.derived_from else None

    return {
        "id": asset.id,
        "provenance": asset.provenance,
        "signed": asset.provenance_signed,
        "sha256": asset.sha256,
        "chain": chain,
        "labels": list(models.Asset.PROVENANCE),
    }


@app.delete("/media/{asset_id}", status_code=204, tags=["media"])
def delete(asset_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    asset = db.get(models.Asset, asset_id)
    if asset is None or (asset.owner_id != principal.user_id and not principal.is_admin):
        raise HTTPException(status_code=404, detail="Asset not found")
    _discard_bytes(asset)
    db.delete(asset)
    db.commit()


def _discard_bytes(asset: models.Asset) -> None:
    """Delete the stored bytes. A remote failure is logged, never raised: the
    member asked for the row to go, and an orphaned remote file is recoverable
    from UploadCenter's console where a stuck delete button is not."""
    # The derivative goes with the original. It is our copy of a picture
    # somebody asked us to forget.
    if asset.preview_path:
        Path(asset.preview_path).unlink(missing_ok=True)
    _discard_stored(asset.provider, asset.external_id, asset.storage_path)


def _discard_stored(provider: str, external_id: str | None, storage_path: str | None) -> None:
    """_discard_bytes on plain values, for a background task that runs after
    the request's session is closed."""
    if provider == "uploadcenter" and external_id:
        try:
            uploadcenter.delete_file(external_id)
        except uploadcenter.UploadCenterError as exc:
            log.warning("could not delete uploadcenter file %s: %s", external_id, exc)
    elif storage_path:
        Path(storage_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Profile images (avatar, cover)
#
# Three steps, the same for both storages so the client has one flow:
#   1. presign  - the member declares purpose, type and size; refused here
#                 before anything is stored.
#   2. PUT      - the bytes come to this service, which checks them, then
#                 stores them: on the media volume (local mode, no key) or on
#                 UploadCenter, server to server. The browser cannot upload to
#                 UploadCenter itself: its storage refuses CORS preflights.
#   3. complete - 200 once the stored file is ready, 202 while UploadCenter
#                 is still scanning it.
# Only a ready asset can be put on a profile; user-service asks
# /internal/media/{id} before accepting one.
# ---------------------------------------------------------------------------

MAX_UNFINISHED_PROFILE_UPLOADS = 10
PENDING_WINDOW = timedelta(hours=1)


class ProfileImageRequest(BaseModel):
    purpose: str
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    size_bytes: int


def _own_profile_image(db: OrmSession, asset_id: str, owner_id: str, lock: bool = False) -> models.Asset:
    asset = db.get(models.Asset, asset_id, with_for_update=lock)
    if asset is None or asset.owner_id != owner_id or asset.purpose is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return asset


def _profile_image_out(asset: models.Asset) -> dict:
    return {
        "asset_id": asset.id,
        "purpose": asset.purpose,
        "status": asset.status,
        "url": asset.url or None,
        "storage": asset.provider,
    }


def _still_processing(asset: models.Asset, retry_after: int = 2) -> JSONResponse:
    """202: not ready yet, ask again in ``retry_after`` seconds."""
    return JSONResponse(status_code=202, content={**_profile_image_out(asset), "retry_after_seconds": retry_after})


_UNFINISHED = ("pending", "uploading", "processing")
MAX_EXPIRED_PER_REQUEST = 20


def _expire_abandoned_uploads(db: OrmSession, owner_id: str) -> list[tuple[str, str | None, str | None]]:
    """Fail this member's uploads left unfinished for longer than PENDING_WINDOW.

    A tab closed mid-upload, a scan the browser stopped polling, a service
    restarted between claiming an upload and storing it: each leaves a row that
    never becomes ready, and possibly a stored file nothing will ever show.
    Swept here, on the member's next upload, rather than by a timer - the same
    choice as messaging-service's expiry: no scheduler to run or forget.

    Returns what to delete from storage, for after the response.
    """
    cutoff = datetime.now(timezone.utc) - PENDING_WINDOW
    stale = db.scalars(
        select(models.Asset)
        .where(
            models.Asset.owner_id == owner_id,
            models.Asset.purpose.is_not(None),
            models.Asset.status.in_(_UNFINISHED),
            models.Asset.created_at < cutoff,
        )
        .limit(MAX_EXPIRED_PER_REQUEST)
        .with_for_update(skip_locked=True)
    ).all()
    stored = []
    for asset in stale:
        asset.status = "failed"
        stored.append((asset.provider, asset.external_id, asset.storage_path))
    if stale:
        db.commit()
        log.info("expired %d abandoned profile uploads for %s", len(stale), owner_id)
    return stored


@app.post("/media/profile-images/presign", status_code=201, tags=["profile images"])
def presign_profile_image(
    payload: ProfileImageRequest,
    principal: CurrentUser,
    background: BackgroundTasks,
    db: OrmSession = Depends(get_db),
):
    problem = profileimages.check_request(payload.purpose, payload.mime_type, payload.size_bytes)
    if problem:
        raise HTTPException(status_code=422, detail=problem)

    for stored in _expire_abandoned_uploads(db, principal.user_id):
        background.add_task(_discard_stored, *stored)

    # Each presign creates a row. A member changing their picture needs a
    # handful; a script needs thousands.
    since = datetime.now(timezone.utc) - PENDING_WINDOW
    unfinished = db.scalar(
        select(func.count()).select_from(models.Asset).where(
            models.Asset.owner_id == principal.user_id,
            models.Asset.purpose.is_not(None),
            models.Asset.status.in_(_UNFINISHED),
            models.Asset.created_at >= since,
        )
    )
    if unfinished >= MAX_UNFINISHED_PROFILE_UPLOADS:
        raise HTTPException(status_code=429, detail="Too many unfinished uploads. Please try again later.")

    filename = Path(payload.filename).name[:255] or "image"
    asset = models.Asset(
        id=new_id("mda"),
        owner_id=principal.user_id,
        filename=filename,
        content_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        sha256="",
        storage_path="",
        url="",
        kind="image",
        access="public",
        purpose=payload.purpose,
        status="pending",
        provider="uploadcenter" if uploadcenter.enabled() else "local",
    )
    db.add(asset)
    db.commit()
    return {
        "asset_id": asset.id,
        "storage": asset.provider,
        "method": "PUT",
        "headers": {"Content-Type": payload.mime_type},
        "upload": {"path": f"/media/profile-images/{asset.id}/content"},
    }


async def _read_body(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it passes the limit."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"The image must be at most {limit // profileimages.MB} MB")
    return bytes(data)


def _send_to_uploadcenter(filename: str, data: bytes, mime_type: str) -> str:
    """Presign, upload and complete on UploadCenter; returns its file id."""
    signed = uploadcenter.presign(filename, len(data), mime_type, visibility="public")
    if not profileimages.upload_url_allowed(signed.get("upload_url")):
        log.warning("uploadcenter returned an upload URL that is not https; refusing to send")
        raise uploadcenter.UploadCenterError("The file storage returned an unusable upload address")
    uploadcenter.put_bytes(signed["upload_url"], data, mime_type)
    uploadcenter.complete(signed["file_id"])
    return signed["file_id"]


@app.put("/media/profile-images/{asset_id}/content", status_code=204, tags=["profile images"])
async def receive_profile_image(
    asset_id: str, request: Request, principal: CurrentUser, db: OrmSession = Depends(get_db),
):
    """Receive the bytes, check them, then store them.

    At most 10 MB (a cover), so the body is held in memory: that is what lets
    the bytes be checked before anything reaches storage.
    """
    asset = _own_profile_image(db, asset_id, principal.user_id)
    if asset.status != "pending":
        raise HTTPException(status_code=409, detail="This upload does not accept content")

    data = await _read_body(request, profileimages.LIMITS[asset.purpose])

    # The declared type was checked at presign; the bytes are checked here. A
    # file that is not the image it claims to be is refused, not stored.
    if not data or profileimages.sniff(data[:16]) != asset.content_type:
        asset.status = "failed"
        db.commit()
        raise HTTPException(status_code=422, detail="This file is not the image it claims to be")

    # Claimed atomically before storing, so two PUTs racing on the same upload
    # cannot both store a file (one of them would be orphaned). A conditional
    # UPDATE rather than SELECT ... FOR UPDATE: this handler is async and the
    # session is not, so waiting on a row lock would stall every request.
    claimed = db.execute(
        update(models.Asset)
        .where(models.Asset.id == asset.id, models.Asset.status == "pending")
        .values(status="uploading")
    ).rowcount
    db.commit()
    if not claimed:
        raise HTTPException(status_code=409, detail="This upload does not accept content")

    if asset.provider == "uploadcenter":
        try:
            asset.external_id = await run_in_threadpool(_send_to_uploadcenter, asset.filename, data, asset.content_type)
        except uploadcenter.UploadCenterError as exc:
            # Back to pending: the member can send the same upload again.
            asset.status = "pending"
            db.commit()
            raise HTTPException(status_code=502, detail=str(exc))
    else:
        folder = ROOT / principal.user_id[:12]
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / asset.id
        destination.write_bytes(data)
        asset.storage_path = str(destination)

    asset.size_bytes = len(data)
    asset.sha256 = hashlib.sha256(data).hexdigest()
    asset.status = "processing"
    db.commit()


@app.post("/media/profile-images/{asset_id}/complete", tags=["profile images"])
def complete_profile_image(asset_id: str, principal: CurrentUser, db: OrmSession = Depends(get_db)):
    """Idempotent. 200 when ready, 202 while UploadCenter is still scanning.

    The row is locked for the duration so two concurrent calls cannot both
    drive the same upload through complete().
    """
    asset = _own_profile_image(db, asset_id, principal.user_id, lock=True)
    if asset.status == "ready":
        return _profile_image_out(asset)
    if asset.status == "failed":
        raise HTTPException(status_code=422, detail="This image was rejected. Please choose another one.")

    if asset.status != "processing":
        raise HTTPException(status_code=409, detail="The image has not been uploaded yet")

    if asset.provider == "local":
        asset.url = f"{settings.MEDIA_PUBLIC_BASE}/{asset.id}"
        asset.status = "ready"
        db.commit()
        return _profile_image_out(asset)

    # Every check below is a call to UploadCenter. Answered from here when the
    # last one was too recent, refused for good after too many.
    now = datetime.now(timezone.utc)
    delay = profileimages.status_check_delay(asset.last_checked_at, asset.status_checks or 0, now)
    if delay is None:
        asset.status = "failed"
        db.commit()
        _discard_bytes(asset)
        log.info("profile image %s never became ready; given up", asset.id)
        raise HTTPException(status_code=422, detail="This image took too long to process. Please try another one.")
    if delay > 0:
        db.commit()
        return _still_processing(asset, retry_after=math.ceil(delay))
    asset.status_checks = (asset.status_checks or 0) + 1
    asset.last_checked_at = now

    try:
        record = uploadcenter.get_file(asset.external_id)
    except uploadcenter.UploadCenterError as exc:
        db.commit()  # the check counts even when UploadCenter did not answer
        raise HTTPException(status_code=502, detail=str(exc))

    if profileimages.is_failed(record):
        asset.status = "failed"
        db.commit()
        log.info("uploadcenter rejected %s (%s): status=%s", asset.id, asset.external_id, record.get("status"))
        raise HTTPException(status_code=422, detail="This image was rejected. Please choose another one.")
    problem = profileimages.stored_file_problem(record, asset.purpose)
    if problem:
        asset.status = "failed"
        db.commit()
        _discard_bytes(asset)
        raise HTTPException(status_code=422, detail=problem)

    if not profileimages.is_ready(record):
        db.commit()
        return _still_processing(asset)

    url = record["url"]
    if not profileimages.cdn_url_allowed(url, uploadcenter.cdn_hosts()):
        log.error("uploadcenter returned an unexpected url host for %s; check UPLOADCENTER_CDN_HOSTS", asset.id)
        asset.status = "failed"
        db.commit()
        raise HTTPException(status_code=502, detail="The file storage returned an unexpected address")

    # UploadCenter reports the type the browser declared. The bytes are checked
    # here, the first time this service sees any of them, with the same rule the
    # local path applies.
    try:
        head = uploadcenter.read_head(url)
    except uploadcenter.UploadCenterError:
        # CDN propagation can lag the "ready" status by a moment.
        db.commit()
        return _still_processing(asset)
    if profileimages.sniff(head) != asset.content_type:
        asset.status = "failed"
        db.commit()
        _discard_bytes(asset)
        raise HTTPException(status_code=422, detail="This file is not the image it claims to be")

    asset.url = url
    asset.size_bytes = int(record.get("size_bytes") or asset.size_bytes)
    asset.status = "ready"
    db.commit()
    return _profile_image_out(asset)


class DiscardIn(BaseModel):
    owner_id: str


@app.post("/internal/media/{asset_id}/discard", status_code=204, tags=["internal"])
def discard_profile_image(asset_id: str, payload: DiscardIn, db: OrmSession = Depends(get_db)):
    """Delete a file its owner has replaced or removed: a profile image, or a
    memorial's gallery file.

    Only those — they are the assets that carry a purpose — and only for the
    owner named by the caller: an internal caller with a wrong id cannot delete
    somebody's post media.
    Unknown or already deleted is not an error - the outcome is the same.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        return
    if asset.owner_id != payload.owner_id or asset.purpose is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    _discard_bytes(asset)
    db.delete(asset)
    db.commit()


@app.post("/internal/media/{asset_id}/discard-post-media", status_code=204, tags=["internal"])
def discard_post_media(asset_id: str, payload: DiscardIn, db: OrmSession = Depends(get_db)):
    """Delete a post's media once its author has removed the post.

    The counterpart of /discard for profile images, with the opposite scope: only
    post media (no profile purpose, not a chat attachment, restricted), and only
    for the owner the caller names. social-service decides *whether* a file may go
    (it keeps the evidence of reported or reviewed posts); this only refuses
    anything that is not post media, so a wrong id cannot delete an avatar or a
    private attachment. Unknown or already deleted is not an error.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        return
    if (
        asset.owner_id != payload.owner_id
        or asset.purpose is not None
        or asset.private
        or asset.access != "restricted"
    ):
        raise HTTPException(status_code=404, detail="Asset not found")
    _discard_bytes(asset)
    db.delete(asset)
    db.commit()


@app.get("/internal/media/{asset_id}", tags=["internal"])
def internal_asset(asset_id: str, db: OrmSession = Depends(get_db)):
    """Who uploaded an asset, and what it is — one answer for every service.

    user-service reads ``purpose`` and ``status`` before putting an asset on a
    profile. memorial-service and the others read ``owner_id`` and ``kind``
    before letting a member attach a file they uploaded: otherwise anyone
    holding somebody else's asset id could attach it to a public page and have
    the service mint viewing tickets for it.

    There is exactly one route on this path. FastAPI answers from whichever was
    declared first, so a second one — with a different set of fields — would
    silently starve the other service of the fields it reads.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {
        "id": asset.id,
        "owner_id": asset.owner_id,
        "kind": asset.kind,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "provenance": asset.provenance,
        "purpose": asset.purpose,
        "status": asset.status,
        "access": asset.access,
        "url": asset.url or None,
        "storage": asset.provider,
        "kind": asset.kind,
        "private": asset.private,
        # Read off the file's header at upload. social-service stores these on
        # the post rather than the numbers the client sent, so the feed reserves
        # a box from a measurement instead of a claim. Null for video and audio,
        # and for everything uploaded before this was read.
        "width": asset.width,
        "height": asset.height,
    }


@app.post("/internal/media/backfill-dimensions", tags=["internal"])
def backfill_dimensions(limit: int = 100, dry_run: bool = True, db: OrmSession = Depends(get_db)):
    """Read the size of images uploaded before the size was being read.

    Everything stored before imagesize.py existed has no width or height, so
    the feed cannot reserve a box for it and measures it in the browser
    instead - which works, but costs a layout jump on first view. The files are
    still here and their headers still say how big they are, so this reads them.

    Nothing is downloaded whole: local files are read to the header length, and
    an UploadCenter file is fetched with a Range request for the same.

    Only images, and only rows that have no size yet - so it is safe to run
    again, and a second run finds nothing left to do. Sealed attachments are
    skipped: they are chat files, encrypted on disk, and no feed lays them out.

    dry_run is the default on purpose. Reaching this needs access to the
    container, since /internal is not routed through the gateway.
    """
    assets = db.scalars(
        select(models.Asset)
        .where(
            models.Asset.kind == "image",
            models.Asset.width.is_(None),
            models.Asset.private.is_(False),
        )
        .limit(max(1, min(limit, 500)))
    ).all()

    read = failed = 0
    reasons: dict[str, int] = {}
    samples: list[dict] = []

    def note(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for asset in assets:
        head = b""
        try:
            if asset.provider == "uploadcenter" and asset.external_id:
                client = response = None
                try:
                    client, response = uploadcenter.open_stream(
                        _remote_link(asset), f"bytes=0-{imagesize.HEAD_BYTES - 1}"
                    )
                    if response.status_code not in (200, 206):
                        note(f"remote http {response.status_code}")
                        failed += 1
                        continue
                    for chunk in response.iter_bytes():
                        head += chunk
                        if len(head) >= imagesize.HEAD_BYTES:
                            break
                finally:
                    if response is not None:
                        response.close()
                    if client is not None:
                        client.close()
            elif asset.storage_path:
                path = Path(asset.storage_path)
                if not path.exists():
                    note("file missing")
                    failed += 1
                    continue
                with path.open("rb") as handle:
                    head = handle.read(imagesize.HEAD_BYTES)
            else:
                note("nowhere to read from")
                failed += 1
                continue
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            log.warning("backfill could not read %s: %s", asset.id, exc)
            note("read failed")
            failed += 1
            continue

        size = imagesize.read(asset.content_type, head[: imagesize.HEAD_BYTES])
        if size is None:
            note(f"unreadable header ({asset.content_type})")
            failed += 1
            continue

        if not dry_run:
            asset.width, asset.height = size
        read += 1
        if len(samples) < 5:
            samples.append({"id": asset.id, "content_type": asset.content_type, "size": list(size)})

    if not dry_run:
        db.commit()

    remaining = db.scalar(
        select(func.count())
        .select_from(models.Asset)
        .where(
            models.Asset.kind == "image",
            models.Asset.width.is_(None),
            models.Asset.private.is_(False),
        )
    )
    return {
        "dry_run": dry_run,
        "examined": len(assets),
        "read": read,
        "failed": failed,
        "reasons": reasons,
        "samples": samples,
        "still_without_size": remaining,
    }
