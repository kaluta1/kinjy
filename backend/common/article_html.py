"""The server's own whitelist for article bodies.

Articles are written as HTML in the browser and the browser sanitises them
(app/src/lib/richtext.ts) - but a body can be posted by anything that speaks
HTTP, so the server keeps only the same safe subset before storing. The two
lists are meant to match; this one is the one that decides.

A whitelist rebuild, not a strip: the markup is parsed, and only known tags
and attributes are written out again, with every text and attribute value
escaped. Unknown tags are unwrapped (their words kept); the contents of
script, style and the like are dropped with them.
"""
from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

# Tags kept as they are.
KEPT = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "sub", "sup",
    "h2", "h3", "ul", "ol", "li", "blockquote", "a", "code", "pre", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
}
VOID = {"br", "hr"}
# Mapped onto what an article has: one title level and one subtitle level.
RENAMED = {"h1": "h2", "h4": "h3", "h5": "h3", "h6": "h3", "strike": "s", "del": "s", "ins": "u"}
# Dropped with everything inside them.
DROPPED = {"script", "style", "template", "noscript", "iframe", "object", "svg", "math",
           "head", "title", "textarea", "select", "button", "form", "canvas"}
# Dropped too, but they never have an end tag, so there is nothing to skip.
DROPPED_VOID = {"meta", "link", "input", "embed", "img", "source", "track", "base", "param", "wbr", "col", "area"}

MEDIA_ID_RE = re.compile(r"^mda_[A-Za-z0-9]{6,60}$")
EMBED_ID = {"youtube": re.compile(r"^[A-Za-z0-9_-]{11}$"), "vimeo": re.compile(r"^\d{6,12}$")}
ALIGNS = {"left", "center", "right", "wide"}


def _safe_href(value: str) -> str | None:
    value = (value or "").strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme in ("http", "https") and parts.netloc:
        return value
    if parts.scheme == "mailto":
        return value
    return None


def _figure(attrs: dict[str, str]) -> str | None:
    """A clean figure marker, or None when it is not one we know."""
    out: list[tuple[str, str]] = []
    media_id = attrs.get("data-media-id", "")
    kind = attrs.get("data-kind", "")
    provider = attrs.get("data-embed", "")
    embed_id = attrs.get("data-id", "")
    if MEDIA_ID_RE.match(media_id) and kind in ("image", "video"):
        out += [("data-media-id", media_id), ("data-kind", kind)]
    elif provider in EMBED_ID and EMBED_ID[provider].match(embed_id):
        out += [("data-embed", provider), ("data-id", embed_id)]
    else:
        return None
    for key, limit in (("data-caption", 300), ("data-alt", 300)):
        text = " ".join((attrs.get(key) or "").split())[:limit]
        if text:
            out.append((key, text))
    if attrs.get("data-align") in ALIGNS:
        out.append(("data-align", attrs["data-align"]))
    return "<figure " + " ".join(f'{k}="{escape(v, quote=True)}"' for k, v in out) + "></figure>"


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []      # tags written, to close in order
        self.skip = 0                   # depth inside a dropped element
        self.in_anchor = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]], closed: bool = False) -> None:
        tag = tag.lower()
        if tag in DROPPED_VOID:
            return
        if self.skip:
            if (tag in DROPPED or tag == "figure") and not closed:
                self.skip += 1
            return
        if tag in DROPPED:
            if not closed:
                self.skip = 1
            return
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "figure":
            marker = None if self.in_anchor else _figure(values)
            if marker:
                self.out.append(marker)
            # Whatever the figure held is a preview; the marker is all we keep.
            if not closed:
                self.skip = 1
            return
        tag = RENAMED.get(tag, tag)
        if tag not in KEPT:
            return  # unwrapped: the words inside are kept
        if tag == "a":
            href = _safe_href(values.get("href", ""))
            if not href or self.in_anchor:
                return
            self.out.append(f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer nofollow" target="_blank">')
            self.in_anchor += 1
            self.stack.append("a")
            return
        extra = ""
        if tag in ("th", "td"):
            for key in ("colspan", "rowspan"):
                value = values.get(key, "")
                if value.isdigit() and 1 < int(value) <= 20:
                    extra += f' {key}="{int(value)}"'
        self.out.append(f"<{tag}{extra}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs) -> None:
        depth = len(self.stack)
        self.handle_starttag(tag, attrs, closed=True)
        # <p/> and the like: opened just now, so closed just now.
        if len(self.stack) > depth:
            self.handle_endtag(self.stack[-1])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip:
            if tag in DROPPED or tag == "figure":
                self.skip -= 1
            return
        tag = RENAMED.get(tag, tag)
        if tag not in self.stack:
            return
        # Close up to and including it, so the output is always balanced.
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == "a":
                self.in_anchor -= 1
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


_EMPTY_RUNS = re.compile(r"(?:<p>(?:\s|<br>|&nbsp;| )*</p>\s*){2,}")


def sanitize_article(html: str) -> str:
    """The article body, reduced to the safe subset."""
    if not html:
        return ""
    cleaner = _Cleaner()
    cleaner.feed(html)
    cleaner.close()
    out = cleaner.result()
    # A paste from a word processor arrives with runs of empty paragraphs;
    # one blank line is spacing, five are not.
    return _EMPTY_RUNS.sub("<p><br></p>", out)
