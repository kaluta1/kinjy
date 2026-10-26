"""A short vertical video made from a post: its words as title cards, its pictures in between.

One-to-Many's "Short video". Nothing is invented: every word on screen is the
author's, every picture is one they attached to the post. What this adds is
framing - a 720x1280 frame, a few seconds per card, H.264 that every phone plays.

Rendering is Pillow for the frames and ffmpeg for the encoding. The result is
probed before it is handed back: a file ffprobe cannot read as a vertical H.264
video of a sensible length is a failure, not an output.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

log = logging.getLogger("media-service.shortvideo")

WIDTH, HEIGHT = 720, 1280
FPS = 25
MAX_SCENES = 10
MAX_SECONDS = 60
IMAGE_SECONDS = 3.0
CONTENT_TYPE = "video/mp4"

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Kinjy's ink, cream and gold.
INK = (20, 17, 15)
CREAM = (248, 245, 239)
GOLD = (214, 168, 79)

# DejaVu has no CJK glyphs, and Pillow here has no shaping engine for Arabic:
# such text would come out as boxes or as disconnected letters. Refused up front.
_UNSUPPORTED_SCRIPT = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿぀-ヿ㐀-鿿가-힯豈-﫿]")
UNSUPPORTED_LANGS = ("ar", "zh")


class RenderError(RuntimeError):
    pass


def available() -> tuple[bool, str | None]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False, "ffmpeg is not installed on the media server"
    if not Path(FONT_BOLD).exists() or not Path(FONT_REGULAR).exists():
        return False, "the fonts for title cards are not installed on the media server"
    return True, None


def unsupported_text(text: str) -> bool:
    return bool(_UNSUPPORTED_SCRIPT.search(text or ""))


def scenes_from_text(text: str, limit: int = MAX_SCENES) -> list[str]:
    """Split the post into title cards of a readable size, in order.

    Sentences are kept whole where they fit; a long one is cut at a word. The
    words are the author's and nothing is reordered or dropped except what
    goes past the last card.
    """
    clean = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not clean:
        return []
    pieces: list[str] = []
    for paragraph in [p.strip() for p in clean.split("\n") if p.strip()]:
        pieces.extend(s.strip() for s in re.split(r"(?<=[.!?…])\s+", paragraph) if s.strip())
    cards: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > 160:
            cut = piece.rfind(" ", 0, 160)
            cut = cut if cut > 40 else 160
            if current:
                cards.append(current)
                current = ""
            cards.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        if current and len(current) + 1 + len(piece) > 140:
            cards.append(current)
            current = piece
        else:
            current = f"{current} {piece}".strip()
    if current:
        cards.append(current)
    return cards[:limit]


def _seconds_for(text: str) -> float:
    # About 15 characters a second, never shorter than a glance.
    return round(min(6.0, max(2.5, 1.2 + len(text) / 15)), 2)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and draw.textlength(f"{lines[-1]} {word}", font=font) <= width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def _text_card(text: str, path: Path, byline: str | None) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), INK)
    draw = ImageDraw.Draw(image)
    margin = 64
    size = 58 if len(text) < 60 else 48 if len(text) < 110 else 40
    font = ImageFont.truetype(FONT_BOLD, size)
    while True:
        lines = _wrap(draw, text, font, WIDTH - 2 * margin)
        line_h = int(size * 1.3)
        if len(lines) * line_h < HEIGHT - 420 or size <= 28:
            break
        size -= 4
        font = ImageFont.truetype(FONT_BOLD, size)
    block = len(lines) * line_h
    y = (HEIGHT - block) // 2
    draw.rectangle([margin, y - 48, margin + 72, y - 40], fill=GOLD)
    for line in lines:
        draw.text((margin, y), line, font=font, fill=CREAM)
        y += line_h
    if byline:
        small = ImageFont.truetype(FONT_REGULAR, 28)
        draw.text((margin, HEIGHT - 120), byline, font=small, fill=GOLD)
    image.save(path, "PNG")


def _picture_card(source: Path, path: Path) -> None:
    with Image.open(source) as opened:
        picture = ImageOps.exif_transpose(opened).convert("RGB")
    # The picture whole, on a blurred, darkened copy of itself.
    backdrop = ImageOps.fit(picture, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(28))
    backdrop = Image.blend(backdrop, Image.new("RGB", (WIDTH, HEIGHT), INK), 0.45)
    framed = ImageOps.contain(picture, (WIDTH, HEIGHT))
    backdrop.paste(framed, ((WIDTH - framed.width) // 2, (HEIGHT - framed.height) // 2))
    backdrop.save(path, "PNG")


def render(texts: list[str], pictures: list[Path], destination: Path, workdir: Path, byline: str | None = None) -> dict:
    """Render the cards, encode, probe. Returns ``{duration_s, width, height, size_bytes}``."""
    ok, reason = available()
    if not ok:
        raise RenderError(reason or "rendering is unavailable")
    if not texts and not pictures:
        raise RenderError("the post has neither text nor pictures")
    if any(unsupported_text(t) for t in texts):
        raise RenderError("title cards cannot be drawn in this script yet")

    workdir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[Path, float]] = []
    total = 0.0
    # Words first, a picture after each card while there are pictures, the rest at the end.
    order: list[tuple[str, object]] = []
    queue = list(pictures)
    for text in texts:
        order.append(("text", text))
        if queue:
            order.append(("picture", queue.pop(0)))
    order.extend(("picture", p) for p in queue)

    for index, (kind, item) in enumerate(order):
        frame = workdir / f"frame{index:02d}.png"
        if kind == "text":
            seconds = _seconds_for(str(item))
            last = index == len(order) - 1
            _text_card(str(item), frame, byline if last or index == 0 else None)
        else:
            seconds = IMAGE_SECONDS
            try:
                _picture_card(Path(str(item)), frame)
            except Exception as exc:  # an unreadable picture is skipped, not fatal
                log.warning("picture %s skipped: %s", item, exc)
                continue
        if total + seconds > MAX_SECONDS:
            break
        frames.append((frame, seconds))
        total += seconds
    if not frames:
        raise RenderError("nothing could be drawn from this post")

    # Each card is a looped still of its own length, joined by the concat filter:
    # the concat demuxer's per-file durations are not reliably honoured.
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for frame, seconds in frames:
        command += ["-loop", "1", "-framerate", str(FPS), "-t", str(seconds), "-i", str(frame)]
    chains = "".join(f"[{i}:v]scale={WIDTH}:{HEIGHT},setsar=1,format=yuv420p[v{i}];" for i in range(len(frames)))
    joined = "".join(f"[v{i}]" for i in range(len(frames)))
    command += [
        "-filter_complex", f"{chains}{joined}concat=n={len(frames)}:v=1:a=0[out]",
        "-map", "[out]", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-movflags", "+faststart", "-threads", "2",
        str(destination),
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("encoding took too long") from exc
    if done.returncode != 0 or not destination.exists():
        log.error("ffmpeg failed: %s", (done.stderr or "")[-500:])
        raise RenderError("the video could not be encoded")
    return probe(destination, expected_seconds=total)


def probe(path: Path, expected_seconds: float | None = None) -> dict:
    """What ffprobe says the file is; raises unless it is a playable vertical H.264 video."""
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,width,height", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(done.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise RenderError("the encoded file could not be read back") from exc
    streams = info.get("streams") or []
    stream = streams[0] if streams else {}
    duration = float((info.get("format") or {}).get("duration") or 0)
    if stream.get("codec_name") != "h264" or stream.get("width") != WIDTH or stream.get("height") != HEIGHT:
        raise RenderError("the encoded file is not the expected video")
    if duration < 1 or duration > MAX_SECONDS + 5:
        raise RenderError("the encoded file has an unexpected length")
    if expected_seconds and abs(duration - expected_seconds) > 2:
        raise RenderError("the encoded file is shorter than its cards")
    return {"duration_s": round(duration, 2), "width": WIDTH, "height": HEIGHT, "size_bytes": path.stat().st_size}
