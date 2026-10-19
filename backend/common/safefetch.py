"""Fetching a URL a member handed us, without letting it reach inside the network.

The same rules as the link preview (social-service/linkpreview.py), for any
server-side fetch of an address that came from a member - today, a picture in
an article pasted from another site:

* http and https only, no credentials in the URL;
* every address the host resolves to must be public - one private answer is
  enough to refuse (cloud metadata, the services next door);
* redirects are followed by hand, at most three, each hop checked again;
* the body is read in chunks and abandoned past the size limit;
* the response type must be one the caller asked for.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 3


class UnsafeURL(Exception):
    """Not a URL this server may fetch, or one that could not be fetched."""


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    )


def check(url: str) -> str:
    """Validate a URL; return its host. Raises UnsafeURL."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UnsafeURL("That is not a web address") from exc
    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL("Only http and https addresses can be fetched")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeURL("That address has no host")
    if parts.username or parts.password:
        raise UnsafeURL("That address carries credentials")
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError as exc:
        raise UnsafeURL("That address could not be resolved") from exc
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UnsafeURL("That address points inside a private network")
    return host


def fetch_bytes(
    url: str, *, max_bytes: int, accept: tuple[str, ...], timeout: float = 15, agent: str = "Kinjy/1.0 (+https://kinjy.com)"
) -> tuple[bytes, str, str]:
    """(body, content type, final url) for a URL, or UnsafeURL.

    ``accept`` lists the content types (or prefixes such as "image/") taken;
    anything else is refused before the body is read.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        check(current)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False,
                              headers={"User-Agent": agent, "Accept": ", ".join(accept) + ", */*;q=0.1"}) as client:
                with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise UnsafeURL("The address redirects nowhere")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise UnsafeURL(f"The source answered {response.status_code}")
                    kind = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if not any(kind == a or (a.endswith("/") and kind.startswith(a)) for a in accept):
                        raise UnsafeURL(f"That is not a supported file ({kind or 'unknown type'})")
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise UnsafeURL(f"The file is larger than {max_bytes // 1024 // 1024} MB")
                    chunks, total = [], 0
                    for chunk in response.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise UnsafeURL(f"The file is larger than {max_bytes // 1024 // 1024} MB")
                        chunks.append(chunk)
                    return b"".join(chunks), kind, current
        except httpx.HTTPError as exc:
            raise UnsafeURL("The source could not be reached") from exc
    raise UnsafeURL("The address redirects too many times")
