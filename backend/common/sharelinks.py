"""Short links: kinjy.com/s/<token>.

A member pastes the address of a Kinjy page and gets an opaque token back. The
server keeps what the token stands for - where it goes and, only when the
member asked for it, that it carries their invitation. Neither the member,
their code nor the long address ever appears in the short one.

Only Kinjy pages on this list can be stored, and the stored destination is
checked again every time a link is opened, so a short link can never become an
open redirect, and a page taken off the list stops resolving:

    /                              home (an invitation)
    /join?mode=signup              the sign-up form (an invitation)
    /p/<post id>                   a post or an article
    /u/<handle>                    a profile
    /communities[/<slug>]          communities (a secret one carries ?access=)
    /messages/join/<invite code>   a group conversation's invitation
    /memorial/<code>               a public memorial
    /market, /market/products/<id>, /market/vendors/<id>
    the public pages: /platform, /feeds, /pricing, /safety, ...

Nothing here fetches the destination: it is parsed, never requested.
"""
from __future__ import annotations

import os
import re
import secrets
from urllib.parse import SplitResult, parse_qsl, quote, urlencode, urlsplit

from common import settings

# 62 symbols, 9 of them: ~53 bits. Unguessable in practice, and short enough to
# type from a poster.
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
TOKEN_LENGTH = 9
TOKEN_RE = re.compile(rf"^[0-9A-Za-z]{{{TOKEN_LENGTH}}}$")
MAX_DESTINATION = 1000

# The unfurlers whose previews matter, as the Caddyfile lists them for /p/*.
CRAWLER_RE = re.compile(
    r"(?i)(whatsapp|facebookexternalhit|facebookcatalog|twitterbot|telegrambot|linkedinbot|slackbot|"
    r"discordbot|skypeuripreview|redditbot|pinterest|applebot|googlebot|bingbot|embedly|quora|vkshare|"
    r"bitlybot|signal|mastodon|iframely|outbrain|tumblr|viber|line-poker|kakaotalk|snapchat)"
)

_ID = r"[A-Za-z0-9_-]{3,80}"
# (pattern, kind): the pages a member would hand to somebody else. Anything
# absent is refused - account, admin, moderation, payment, order and token
# pages, the sign-in callbacks, and every API path.
_PAGES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), kind)
    for pattern, kind in (
        (r"^/$", "invite"),
        (r"^/join$", "invite"),
        (r"^/p/pst_[0-9A-Za-z]{6,60}$", "post"),
        (r"^/u/[A-Za-z0-9_.]{2,40}$", "profile"),
        (r"^/communities$", "page"),
        (r"^/communities/[a-z0-9][a-z0-9-]{0,99}$", "community"),
        (r"^/messages/join/[A-Za-z0-9_-]{6,64}$", "chat"),
        (rf"^/memorial/{_ID}$", "page"),
        (r"^/market$", "page"),
        (rf"^/market/(products|vendors)/{_ID}$", "page"),
        (r"^/(platform|feeds|family|memorials|creators|commerce|pricing|assistant|safety|developers|app"
         r"|forums|explore|shorts|live|earn|graveyard|hub)$", "page"),
    )
)

# The invitation is the link's own option, never inherited from the address
# that was pasted: these are dropped, whatever their value.
REFERRAL_PARAMS = frozenset({"ref", "via", "invite", "invited_by", "referral", "referral_code", "referrer", "sponsor"})
# A link carrying one of these is somebody's secret, not a page.
SECRET_PARAMS = frozenset({
    "access_token", "refresh_token", "id_token", "auth", "authorization", "password", "pass", "pwd",
    "client_secret", "key", "api_key", "apikey", "sig", "signature", "otp", "session", "sid", "jwt",
    "code", "state", "ticket", "nonce", "reset", "confirm",
})
_PARAM_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
_ACCESS = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")
# A decoded parameter value may hold a space (a search for "cafe creme").
_CONTROL_VALUE = re.compile(r"[\x00-\x1f\x7f]")
_SHORT_PATH = re.compile(r"^/s/([0-9A-Za-z]{1,32})/?$")


class BadDestination(ValueError):
    """Not a place a Kinjy short link may point to."""


def site_url() -> str:
    """The public origin links are written with: the site members use."""
    base = settings.FRONTEND_URL.rstrip("/")
    return base if base.startswith(("http://", "https://")) else "https://kinjy.com"


def allowed_hosts() -> frozenset[str]:
    """Hosts (with a port where one is used) a pasted link may name.

    ``SHORT_LINK_HOSTS`` (comma separated) when set; otherwise the site's own
    address plus kinjy.com and www.kinjy.com. Exact matches only - a lookalike
    such as kinjy.com.evil.example or kinjy-com.net is simply not on it.
    """
    configured = {h.strip().lower() for h in os.getenv("SHORT_LINK_HOSTS", "").split(",") if h.strip()}
    return frozenset(configured or {"kinjy.com", "www.kinjy.com", urlsplit(site_url()).netloc.lower()})


def _split(destination: str) -> SplitResult:
    raw = (destination or "").strip()
    if not raw:
        raise BadDestination("Paste a Kinjy page link")
    if len(raw) > MAX_DESTINATION:
        raise BadDestination("That link is too long")
    if _CONTROL.search(raw) or "\\" in raw:
        raise BadDestination("That is not a valid address")
    # "kinjy.com/p/..." without a scheme is how people paste from a phone.
    if not raw.startswith("/") and "://" not in raw:
        raw = f"https://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise BadDestination("That is not a valid address") from exc
    if parts.scheme or parts.netloc:
        if parts.scheme.lower() not in ("http", "https"):
            raise BadDestination("Only https:// Kinjy links can be shortened")
        if "@" in parts.netloc:
            raise BadDestination("Links with a user name or password cannot be shortened")
        host = (parts.hostname or "").lower()
        if (f"{host}:{port}" if port else host) not in allowed_hosts():
            raise BadDestination("Only links to Kinjy pages can be shortened")
    return parts


def short_token_in(destination: str) -> str | None:
    """The token, when the pasted address is itself a Kinjy short link."""
    try:
        parts = _split(destination)
    except BadDestination:
        return None
    match = _SHORT_PATH.match(parts.path or "")
    return match.group(1) if match else None


def normalize(destination: str) -> tuple[str, str]:
    """(canonical destination, kind) for a Kinjy address, or BadDestination.

    The canonical form is a path on this site with its legitimate query
    parameters (in their order) and fragment kept, and every referral
    parameter removed. It is what is stored, and where a visitor is sent.
    """
    parts = _split(destination)
    path = parts.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        raise BadDestination("That is not a valid address")
    lowered = path.lower()
    if any(bad in lowered for bad in ("%2f", "%5c", "%00", "/./", "/../")) or lowered.endswith("/.."):
        raise BadDestination("That is not a valid address")
    if path != "/":
        path = path.rstrip("/")
    if _SHORT_PATH.match(path):
        # Resolved by the caller before it gets here; one that reaches here is
        # a short link that does not exist (or no longer works).
        raise BadDestination("That short link does not work")

    kind = next((k for pattern, k in _PAGES if pattern.match(path)), None)
    if kind is None:
        raise BadDestination("That page cannot be shortened")

    kept: list[tuple[str, str]] = []
    for name, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered_name = name.lower()
        if lowered_name in REFERRAL_PARAMS:
            continue
        if lowered_name in SECRET_PARAMS or "token" in lowered_name or "secret" in lowered_name:
            raise BadDestination("Links containing a secret or a sign-in token cannot be shortened")
        if not _PARAM_NAME.match(name) or len(value) > 300 or _CONTROL_VALUE.search(value):
            raise BadDestination("That link has a parameter that cannot be kept")
        kept.append((name, value))

    if path == "/join":
        # The sign-up form, and nothing else from it: ?next= and friends are
        # how a sign-in page is turned into a redirect.
        kept = [("mode", "signup")]
    if kind == "community":
        access = [v for k, v in kept if k == "access"]
        if access and not _ACCESS.match(access[0]):
            raise BadDestination("That community link is not valid")

    fragment = parts.fragment
    if len(fragment) > 200:
        raise BadDestination("That link is too long")

    canonical = quote(path, safe="/-_.~@%")
    if kept:
        canonical += "?" + urlencode(kept)
    if fragment:
        canonical += "#" + quote(fragment, safe="-_.~:/=&!$'()*+,;@%?")
    if len(canonical) > MAX_DESTINATION:
        raise BadDestination("That link is too long")
    return canonical, kind


def new_token() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LENGTH))


def short_url(token: str) -> str:
    return f"{site_url()}/s/{token}"


def with_via(destination: str, token: str) -> str:
    """The destination carrying the link's token - before any fragment."""
    path, hash_mark, fragment = destination.partition("#")
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}via={token}{hash_mark}{fragment}"
