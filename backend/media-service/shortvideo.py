"""A short vertical video made from a post: its words as title cards, its pictures in between,
read aloud.

One-to-Many's "Short video". Nothing is invented: every word on screen is the
author's, every picture is one they attached to the post, and the narration
reads exactly the words on each card, in the post's language. What this adds
is framing - a 720x1280 frame, H.264 + AAC that every phone plays.

Rendering is Pillow for the frames, a local voice for the narration (Piper's
neural voices, chosen per language from their published metadata; espeak-ng
only as a declared fallback - no vendor, no key, the text never leaves the
server) and ffmpeg for the encoding. The
result is probed before it is handed back: a file that is not a vertical H.264
video of a sensible length, or whose narration track is missing or silent, is
a failure, not an output. A video is never delivered without its voice.
"""
from __future__ import annotations

import json
import logging
import os
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
# Narrated cards last as long as their reading; ten of them need more room.
NARRATED_MAX_SECONDS = 150
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


# Narration on by default; "0" turns it off (videos are then silent title cards
# and say so). A server setting, not code.
NARRATION_ENABLED = os.getenv("SHORT_VIDEO_NARRATION", "1") not in ("0", "false", "off")
# Natural voices (Piper), one per language, by model name in PIPER_VOICES_DIR.
# Server settings: SHORT_VIDEO_VOICES='{"en": "en_US-ljspeech-high", ...}'.
# Defaults picked from the published voice list by language code, quality and
# licence: LJ Speech (US English, one female reader, public domain); SIWIS
# (French, CC-BY 4.0); Lanfrica (Swahili).
PIPER_VOICES_DIR = Path(os.getenv("PIPER_VOICES_DIR", "/opt/piper-voices"))
NATURAL_VOICES: dict[str, str] = {
    "en": "en_US-ljspeech-high",
    "fr": "fr_FR-siwis-medium",
    "sw": "sw_CD-lanfrica-medium",
    **json.loads(os.getenv("SHORT_VIDEO_VOICES", "{}") or "{}"),
}
# The fallback when no natural voice is installed for a language: espeak-ng,
# intelligible but synthetic. Used only for that language, and reported.
BASIC_VOICES = {"en": "en-us", "fr": "fr-fr", "sw": "sw"}
SPEECH_RATE = int(os.getenv("SHORT_VIDEO_SPEECH_RATE", "150"))  # words a minute, espeak-ng only
# Below this mean loudness a track is treated as silence, not as narration.
AUDIBLE_DB = -45.0


class RenderError(RuntimeError):
    pass


def _piper_language(model: Path) -> str | None:
    """The language a Piper voice declares in its own metadata (e.g. "en_US")."""
    try:
        meta = json.loads(Path(f"{model}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return ((meta.get("language") or {}).get("code") or meta.get("espeak", {}).get("voice") or None)


def voice_for(lang: str | None) -> dict | None:
    """The voice that would read ``lang``: {engine, voice, quality, locale}, or None.

    A natural voice is used only when its model is installed and its own
    metadata says it speaks this language - never one of another language.
    """
    lang = lang or "en"
    name = NATURAL_VOICES.get(lang)
    if name and shutil.which("piper"):
        model = PIPER_VOICES_DIR / f"{name}.onnx"
        locale = _piper_language(model) if model.exists() else None
        if locale and locale.split("_")[0].lower() == lang:
            return {"engine": "piper", "voice": name, "model": str(model), "quality": "natural", "locale": locale}
        if model.exists():
            log.warning("voice %s declares %s, not %s: not used", name, locale, lang)
    if lang in BASIC_VOICES and shutil.which("espeak-ng"):
        return {"engine": "espeak-ng", "voice": BASIC_VOICES[lang], "quality": "basic", "locale": BASIC_VOICES[lang]}
    return None


def narration_available(lang: str | None) -> tuple[bool, str | None]:
    """Whether a video in this language can be narrated here, and why not."""
    if not NARRATION_ENABLED:
        return False, "narration is turned off on this server"
    if voice_for(lang) is None:
        return False, "narration is not available in this language yet"
    return True, None


def _speak(text: str, voice: dict, path: Path) -> float:
    """Read one card aloud into a WAV file, once; returns its length in seconds."""
    if voice["engine"] == "piper":
        source = path.with_suffix(".txt")
        # Through a file, never a shell or an argument: the text is the author's.
        source.write_text(" ".join(text.split()), encoding="utf-8")
        command = ["piper", "-m", voice["model"], "-i", str(source), "-f", str(path)]
    else:
        command = ["espeak-ng", "-v", voice["voice"], "-s", str(SPEECH_RATE), "-w", str(path), "--", text]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise RenderError("the narration took too long") from exc
    if done.returncode != 0 or not path.exists() or path.stat().st_size < 1000:
        log.error("%s failed: %s", voice["engine"], (done.stderr or "")[-300:])
        raise RenderError("the narration could not be generated")
    return _duration(path)


def _duration(path: Path) -> float:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(json.loads(done.stdout or "{}").get("format", {}).get("duration") or 0)
    except (json.JSONDecodeError, ValueError):
        return 0.0


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


def render(
    texts: list[str],
    pictures: list[Path],
    destination: Path,
    workdir: Path,
    byline: str | None = None,
    lang: str | None = None,
    narrate: bool = True,
) -> dict:
    """Render the cards, narrate them, encode, probe.
    Returns ``{duration_s, width, height, size_bytes, narrated}``."""
    ok, reason = available()
    if not ok:
        raise RenderError(reason or "rendering is unavailable")
    if not texts and not pictures:
        raise RenderError("the post has neither text nor pictures")
    if any(unsupported_text(t) for t in texts):
        raise RenderError("title cards cannot be drawn in this script yet")
    narrate = narrate and bool(texts)
    voice = None
    if narrate:
        can, why = narration_available(lang)
        if not can:
            raise RenderError(why or "narration is unavailable")
        voice = voice_for(lang)
    limit = NARRATED_MAX_SECONDS if narrate else MAX_SECONDS

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

    voices: dict[Path, Path] = {}
    for index, (kind, item) in enumerate(order):
        frame = workdir / f"frame{index:02d}.png"
        if kind == "text":
            seconds = _seconds_for(str(item))
            if narrate:
                voice_file = workdir / f"voice{index:02d}.wav"
                spoken = _speak(str(item), voice, voice_file)
                # The card stays up for as long as it is being read, plus a breath.
                seconds = round(max(seconds, spoken + 0.6), 2)
                voices[frame] = voice_file
            last = index == len(order) - 1
            _text_card(str(item), frame, byline if last or index == 0 else None)
        else:
            seconds = IMAGE_SECONDS
            try:
                _picture_card(Path(str(item)), frame)
            except Exception as exc:  # an unreadable picture is skipped, not fatal
                log.warning("picture %s skipped: %s", item, exc)
                continue
        if total + seconds > limit:
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
    if narrate:
        # One audio piece per card, exactly as long as the card: its reading
        # padded with silence, or silence alone under a picture.
        n = len(frames)
        for frame, seconds in frames:
            if frame in voices:
                command += ["-i", str(voices[frame])]
            else:
                command += ["-f", "lavfi", "-t", str(seconds), "-i", "anullsrc=r=44100:cl=mono"]
        for i, (frame, seconds) in enumerate(frames):
            chains += (
                f"[{n + i}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=mono,"
                f"apad,atrim=0:{seconds},asetpts=N/SR/TB[a{i}];"
            )
        joined = "".join(f"[v{i}][a{i}]" for i in range(n))
        command += [
            "-filter_complex", f"{chains}{joined}concat=n={n}:v=1:a=1[out][aout]",
            "-map", "[out]", "-map", "[aout]", "-c:a", "aac", "-b:a", "96k",
        ]
    else:
        joined = "".join(f"[v{i}]" for i in range(len(frames)))
        command += ["-filter_complex", f"{chains}{joined}concat=n={len(frames)}:v=1:a=0[out]", "-map", "[out]"]
    command += [
        "-r", str(FPS),
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
    info = probe(destination, expected_seconds=total, narrated=narrate)
    if voice:
        info["voice"] = {k: voice[k] for k in ("engine", "voice", "quality", "locale")}
    return info


def _mean_volume(path: Path) -> float | None:
    """The audio track's mean loudness in dB, or None when there is none."""
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    found = re.search(r"mean_volume:\s*(-?[0-9.]+|-inf) dB", done.stderr or "")
    if not found:
        return None
    return float("-inf") if found.group(1) == "-inf" else float(found.group(1))


def probe(path: Path, expected_seconds: float | None = None, narrated: bool | None = None) -> dict:
    """What ffprobe says the file is; raises unless it is a playable vertical H.264
    video - and, when it is meant to be narrated, one with an audible voice track.
    ``narrated=None`` (a file made earlier) reports what is there without requiring it."""
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
    if duration < 1 or duration > NARRATED_MAX_SECONDS + 5:
        raise RenderError("the encoded file has an unexpected length")
    if expected_seconds and abs(duration - expected_seconds) > 2:
        raise RenderError("the encoded file is shorter than its cards")
    loudness = _mean_volume(path)
    has_voice = loudness is not None and loudness > AUDIBLE_DB
    if narrated and loudness is None:
        raise RenderError("the video has no narration track")
    if narrated and not has_voice:
        raise RenderError("the narration track is silent")
    return {"duration_s": round(duration, 2), "width": WIDTH, "height": HEIGHT, "size_bytes": path.stat().st_size,
            "narrated": has_voice}
