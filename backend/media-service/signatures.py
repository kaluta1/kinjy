"""Does a file start the way its declared type says it does?

The upload endpoint used to trust the Content-Type the client wrote: a file full
of "x" declared video/mp4 was stored, and later handed to a browser as a video.
The browser would refuse it, but the platform had still taken it in, classified
a post around it and kept it. Checking the first bytes is cheap, and it moves the
mismatch to the one moment it can be refused with a clear reason.

This is a check of the *start* of the file only, and it is not a scan: a file
can begin like a PNG and carry anything after. What it rules out is the cheap
lie (HTML, a script or random bytes named image/png), which is also the one an
UploadCenter-hosted file would be refused for on their side. It is applied to
the types a post can carry; a chat attachment may be any file at all.
"""
from __future__ import annotations

# Enough for every signature below; the PDF header may sit up to 1024 bytes in.
HEAD_BYTES = 1024

# MP4 and QuickTime files normally open with an ``ftyp`` box, but the format
# lets a few other top-level boxes come first, and real files do.
_MP4_FIRST_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide")


def _riff(head: bytes, form: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == form


_CHECKS = {
    "image/jpeg": lambda h: h[:3] == b"\xff\xd8\xff",
    "image/png": lambda h: h[:8] == b"\x89PNG\r\n\x1a\n",
    "image/gif": lambda h: h[:6] in (b"GIF87a", b"GIF89a"),
    "image/webp": lambda h: _riff(h, b"WEBP"),
    "video/mp4": lambda h: h[4:8] in _MP4_FIRST_BOXES,
    # M4A is the same container as MP4, holding only sound.
    "audio/mp4": lambda h: h[4:8] in _MP4_FIRST_BOXES,
    "audio/x-m4a": lambda h: h[4:8] in _MP4_FIRST_BOXES,
    "video/webm": lambda h: h[:4] == b"\x1a\x45\xdf\xa3",
    # An MP3 starts with an ID3 tag, or straight with a frame (11 set sync bits).
    "audio/mpeg": lambda h: h[:3] == b"ID3" or (len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0),
    "audio/ogg": lambda h: h[:4] == b"OggS",
    "audio/wav": lambda h: _riff(h, b"WAVE"),
    # The PDF header may be preceded by a little junk; the spec allows 1024 bytes.
    "application/pdf": lambda h: b"%PDF-" in h[:1024],
    # An EPUB is a zip whose first entry is "mimetype", stored, holding
    # exactly this string - which is what makes it an EPUB and not any zip.
    "application/epub+zip": lambda h: h[:4] == b"PK\x03\x04" and b"application/epub+zip" in h[:1024],
}


def ebook_type(head: bytes) -> str | None:
    """The ebook format the first bytes prove, whatever the browser declared."""
    if _CHECKS["application/pdf"](head):
        return "application/pdf"
    if _CHECKS["application/epub+zip"](head):
        return "application/epub+zip"
    return None


def checkable(content_type: str) -> bool:
    return content_type in _CHECKS


def matches(content_type: str, head: bytes) -> bool:
    """True when ``head`` (the file's first bytes) fits ``content_type``.

    A type with no signature here is not refused: only the types we can
    actually recognise are held to it.
    """
    check = _CHECKS.get(content_type)
    return True if check is None else bool(check(head))
