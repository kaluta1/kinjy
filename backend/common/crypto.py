"""Encryption at rest for members' private data — messages and chat attachments.

**What it protects against:** a copy of the database, a backup, or the media
volume ending up in the wrong hands. Everything sealed here reads as noise
without the key.

**What it does not:** someone in control of the running server, which holds the
key in its environment. Only end-to-end encryption, with keys on the members'
devices, closes that; this is not that.

The key
-------
``MESSAGES_ENCRYPTION_KEY`` holds one or more keys, newest first::

    MESSAGES_ENCRYPTION_KEY=k2:<base64 32 bytes>,k1:<base64 32 bytes>

The first key seals everything new; every listed key can still open what it
sealed, which is what makes rotation possible without re-encrypting in one go.
Each sealed value records the id of the key that sealed it.

Generate one with::

    python3 -c "import base64,secrets;print('k1:'+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

**Losing the key loses every message sealed with it, permanently.** Keep a copy
somewhere that is not the server and not the database backups — a key stored
next to the backup it protects protects nothing.

Absent in development, present in production: a production service without a
key refuses to start (:func:`require_in_production`), because a stack that
quietly writes plaintext when the variable is forgotten is worse than one that
does not boot. A key that is present but malformed is always fatal, for the
same reason.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import BinaryIO, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common import settings

KEY_ENV = "MESSAGES_ENCRYPTION_KEY"
_KEY_ID = re.compile(r"^[a-z0-9]{1,8}$")


class CryptoConfigError(RuntimeError):
    """The key is missing where it is required, or is not a valid key."""


class DecryptionError(Exception):
    """A sealed value could not be opened: wrong key, missing key, or tampering."""


@dataclass(frozen=True)
class Keyring:
    active_id: str
    keys: dict[str, AESGCM]

    def get(self, key_id: str) -> AESGCM:
        try:
            return self.keys[key_id]
        except KeyError:
            raise DecryptionError(f"key {key_id!r} is not in {KEY_ENV}") from None


def parse_keyring(raw: str) -> Keyring | None:
    """``"k2:…,k1:…"`` → a keyring whose first key is the active one."""
    raw = raw.strip()
    if not raw:
        return None
    keys: dict[str, AESGCM] = {}
    order: list[str] = []
    for part in raw.split(","):
        key_id, sep, encoded = part.strip().partition(":")
        if not sep or not _KEY_ID.match(key_id):
            raise CryptoConfigError(
                f"{KEY_ENV}: expected 'id:base64key' with an id of 1-8 lowercase letters or digits"
            )
        try:
            material = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except Exception:
            raise CryptoConfigError(f"{KEY_ENV}: key {key_id!r} is not valid base64") from None
        if len(material) != 32:
            raise CryptoConfigError(f"{KEY_ENV}: key {key_id!r} must be 32 bytes, got {len(material)}")
        if key_id in keys:
            raise CryptoConfigError(f"{KEY_ENV}: key id {key_id!r} appears twice")
        keys[key_id] = AESGCM(material)
        order.append(key_id)
    return Keyring(active_id=order[0], keys=keys)


@lru_cache(maxsize=1)
def keyring() -> Keyring | None:
    """The configured keyring, or None when encryption is off (development)."""
    return parse_keyring(os.getenv(KEY_ENV, ""))


def enabled() -> bool:
    return keyring() is not None


def require_in_production(service: str) -> None:
    """Refuse to run a production service that would store plaintext.

    Called at startup. Parsing happens here too, so a malformed key stops the
    service in every environment rather than failing on the first message.
    """
    ring = keyring()
    if ring is None and settings.ENV == "production":
        raise CryptoConfigError(
            f"{service}: {KEY_ENV} is not set. Refusing to start in production and "
            f"write members' messages in plaintext. Generate a key (see common/crypto.py), "
            f"add it to the server's .env, and keep a copy somewhere safe."
        )


# --- short values: message text, file names ----------------------------------

def seal_text(plaintext: str, context: str) -> tuple[str, str]:
    """Encrypt ``plaintext`` → ``(key_id, token)``.

    ``context`` is bound into the ciphertext as associated data — a message's
    id and field, for example — so a sealed value copied onto another row, or
    into another column, fails to open instead of reading as that row's text.
    """
    ring = keyring()
    if ring is None:
        raise CryptoConfigError(f"{KEY_ENV} is not set")
    nonce = os.urandom(12)
    sealed = ring.get(ring.active_id).encrypt(nonce, plaintext.encode(), context.encode())
    return ring.active_id, base64.b64encode(nonce + sealed).decode()


def open_text(key_id: str, token: str, context: str) -> str:
    ring = keyring()
    if ring is None:
        raise DecryptionError(f"{KEY_ENV} is not set, and this value is encrypted")
    try:
        raw = base64.b64decode(token)
        return ring.get(key_id).decrypt(raw[:12], raw[12:], context.encode()).decode()
    except DecryptionError:
        raise
    except Exception as exc:
        raise DecryptionError(f"could not open value sealed with {key_id!r}") from exc


# --- files: chunked, so large files stream and seek ---------------------------
#
# Layout:  MAGIC | key-id length (1 byte) | key id | nonce prefix (8 bytes)
#          then chunks of CHUNK plaintext bytes, each sealed with its own nonce
#          (prefix + chunk index) and associated data binding the asset id, the
#          index and whether it is the final chunk. Reordering, dropping or
#          truncating chunks therefore fails to decrypt rather than yielding a
#          shorter or shuffled file.

MAGIC = b"KJE1"
CHUNK = 1024 * 1024
TAG = 16


def _chunk_aad(asset_id: str, index: int, final: bool) -> bytes:
    return asset_id.encode() + struct.pack(">I?", index, final)


class FileSealer:
    """Write-through encryption: feed plaintext in, sealed chunks go to ``out``."""

    def __init__(self, out: BinaryIO, asset_id: str):
        ring = keyring()
        if ring is None:
            raise CryptoConfigError(f"{KEY_ENV} is not set")
        self._aead = ring.get(ring.active_id)
        self._out = out
        self._asset_id = asset_id
        self._prefix = os.urandom(8)
        self._buffer = bytearray()
        self._index = 0
        self.key_id = ring.active_id
        key_id = ring.active_id.encode()
        out.write(MAGIC + bytes([len(key_id)]) + key_id + self._prefix)

    def _seal(self, data: bytes, final: bool) -> None:
        nonce = self._prefix + struct.pack(">I", self._index)
        self._out.write(self._aead.encrypt(nonce, data, _chunk_aad(self._asset_id, self._index, final)))
        self._index += 1

    def write(self, data: bytes) -> None:
        self._buffer += data
        # Keep at least one byte back: only close() knows which chunk is last.
        while len(self._buffer) > CHUNK:
            self._seal(bytes(self._buffer[:CHUNK]), final=False)
            del self._buffer[:CHUNK]

    def close(self) -> None:
        self._seal(bytes(self._buffer), final=True)
        self._buffer.clear()


def _read_header(handle: BinaryIO) -> tuple[AESGCM, bytes, int]:
    if handle.read(4) != MAGIC:
        raise DecryptionError("not a sealed file")
    key_id = handle.read(handle.read(1)[0]).decode()
    prefix = handle.read(8)
    ring = keyring()
    if ring is None:
        raise DecryptionError(f"{KEY_ENV} is not set, and this file is encrypted")
    return ring.get(key_id), prefix, handle.tell()


def is_sealed_file(path: str) -> bool:
    with open(path, "rb") as handle:
        return handle.read(4) == MAGIC


def file_key_available(path: str) -> bool:
    """Whether a sealed file's key is in the keyring — asked before a response starts.

    The key id is read from the file's own header rather than the database
    row: during a re-seal the two can briefly disagree, and the header is the
    one that decides whether the bytes open.
    """
    try:
        with open(path, "rb") as handle:
            _read_header(handle)
        return True
    except (DecryptionError, OSError, IndexError):
        return False


def reseal_file(source: str, destination: str, asset_id: str, size: int) -> str:
    """Write ``source`` to ``destination`` sealed with the active key; returns its id.

    ``source`` may be sealed with any key still in the keyring, or stored as
    uploaded (a private file from before encryption was on). Every chunk is
    authenticated on the way through, so a damaged source raises
    DecryptionError instead of being re-sealed into a valid-looking file.
    """
    with open(destination, "wb") as out:
        sealer = FileSealer(out, asset_id)
        if is_sealed_file(source):
            if size:
                for plain in open_file_range(source, asset_id, size, 0, size - 1):
                    sealer.write(plain)
        else:
            with open(source, "rb") as handle:
                while chunk := handle.read(CHUNK):
                    sealer.write(chunk)
        sealer.close()
    return sealer.key_id


def open_file_range(path: str, asset_id: str, size: int, start: int, end: int) -> Iterator[bytes]:
    """Plaintext bytes ``start..end`` (inclusive) of a sealed file, chunk by chunk."""
    chunks = max(1, -(-size // CHUNK))
    with open(path, "rb") as handle:
        aead, prefix, header = _read_header(handle)
        for index in range(start // CHUNK, min(end // CHUNK, chunks - 1) + 1):
            final = index == chunks - 1
            plain_len = size - index * CHUNK if final else CHUNK
            handle.seek(header + index * (CHUNK + TAG))
            sealed = handle.read(plain_len + TAG)
            nonce = prefix + struct.pack(">I", index)
            try:
                plain = aead.decrypt(nonce, sealed, _chunk_aad(asset_id, index, final))
            except Exception as exc:
                raise DecryptionError(f"chunk {index} of {asset_id} failed to open") from exc
            lo = start - index * CHUNK if index == start // CHUNK else 0
            hi = end - index * CHUNK + 1 if index == end // CHUNK else len(plain)
            yield plain[lo:hi]


# --- signed media links ---------------------------------------------------------
#
# A private attachment is fetched by an <img>, <video> or <a download>, none of
# which can send an Authorization header. So the service that knows who may see
# it (messaging) hands out a link signed for a limited time, and media-service
# checks the signature without having to know anything about conversations.

MEDIA_LINK_TTL_S = 12 * 3600


def _media_link_key() -> bytes:
    # Derived rather than configured: one secret fewer to manage, and never the
    # JWT secret itself.
    return hmac.new(settings.JWT_SECRET.encode(), b"kinjy media link v1", hashlib.sha256).digest()


def _media_signature(asset_id: str, expires: int) -> str:
    mac = hmac.new(_media_link_key(), f"{asset_id}:{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def sign_media_url(url: str, asset_id: str, ttl_s: int | None = None) -> str:
    """``url`` with a signature valid for about MEDIA_LINK_TTL_S.

    The expiry is rounded up to the hour, so the same attachment gets the same
    link for an hour at a time and the browser can cache it. With ``ttl_s``
    (a paid ebook's download link) the link lives that long, to the minute.
    """
    if ttl_s is not None:
        expires = -(-(int(time.time()) + max(60, ttl_s)) // 60) * 60
    else:
        expires = -(-(int(time.time()) + MEDIA_LINK_TTL_S) // 3600) * 3600
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}exp={expires}&sig={_media_signature(asset_id, expires)}"


def download_receipt(asset_id: str, download_id: int, expires: int) -> str:
    """A signature binding one ebook download link to its download record.

    media-service reports a completed download back to commerce-service with
    it, so a completion can only be claimed for a link commerce really issued
    for that file - not for any record id somebody types in.
    """
    mac = hmac.new(_media_link_key(), f"download:{asset_id}:{download_id}:{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def download_receipt_valid(asset_id: str, download_id: str | None, expires: str | None, signature: str | None) -> bool:
    if not download_id or not download_id.isdigit() or not expires or not expires.isdigit() or not signature:
        return False
    return hmac.compare_digest(download_receipt(asset_id, int(download_id), int(expires)), signature)


def private_fingerprint(sha256_hex: str) -> str:
    """A keyed stand-in for a private file's hash.

    Deduplication needs a stable fingerprint, but a plain SHA-256 of a private
    attachment would let anyone holding the database confirm that a known file
    was sent. Keyed, it still matches itself and tells an outsider nothing.
    """
    return hmac.new(_media_link_key(), b"fingerprint:" + sha256_hex.encode(), hashlib.sha256).hexdigest()


def media_signature_valid(asset_id: str, expires: str | None, signature: str | None) -> bool:
    if not expires or not signature or not expires.isdigit():
        return False
    if int(expires) < time.time():
        return False
    return hmac.compare_digest(_media_signature(asset_id, int(expires)), signature)
