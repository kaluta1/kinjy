"""A cover picture for every video.

A video in a feed was a black box until it played: nothing stored a still for
it, and the player asked the browser to paint the frame at 0.1 s, which on a
slow connection is a black box for as long as the first segment takes - and
which a link preview on WhatsApp or Facebook can never do at all. So each
video gets one JPEG, taken a second in (the very first frame is often black
or a fade), at most 1280 px wide, made once at upload by ffmpeg and kept on
the media volume next to the previews.

Never raises. A video ffmpeg cannot read is still a perfectly good upload; it
simply has no cover, and the player falls back to its own.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("media-service")

CONTENT_TYPE = "image/jpeg"
SUFFIX = ".poster.jpg"
# A cover, not a frame grab: wide enough for a full-width card and a share
# preview (1200 px), small enough to be a quick download.
MAX_WIDTH = 1280
TIMEOUT_S = 40


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def make(source: Path, destination: Path) -> Path | None:
    """Write the cover for `source` at `destination` (.poster.jpg). None if not."""
    if not available():
        return None
    target = destination.with_suffix(SUFFIX)
    # One second in first; a clip shorter than that has no frame there, so
    # the start is the fallback.
    for offset in ("1", "0"):
        try:
            subprocess.run(
                [
                    "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                    "-ss", offset, "-i", str(source),
                    "-frames:v", "1",
                    "-vf", f"scale='min({MAX_WIDTH},iw)':-2",
                    "-q:v", "3",
                    str(target),
                ],
                check=True,
                timeout=TIMEOUT_S,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            log.info("no cover at %ss for %s: %s", offset, source.name, type(exc).__name__)
            continue
        if target.exists() and target.stat().st_size > 0:
            return target
    return None
