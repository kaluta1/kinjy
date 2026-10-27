"""Short-video narration: voice choice per language, and each card heard once.

Runs inside the media container (it needs piper, espeak-ng and ffmpeg):

    docker cp backend/tests/unit_shortvideo_voices.py kaluta-media:/tmp/ && docker exec -w /app kaluta-media python /tmp/unit_shortvideo_voices.py

- the voice for en / fr / sw is a natural (Piper) voice whose own metadata
  declares that language; a language without a voice gets none;
- a configured voice of another language is never used for this one (the
  basic voice of the right language is, and is reported as basic);
- a missing natural voice falls back to the basic one, reported;
- a rendered video has one audio track whose speech time equals the sum of
  the cards' readings (a doubled or overlapping narration would not), with
  each card's speech inside that card's time on screen;
- the same for a French video.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
import shortvideo  # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label + ("" if cond else f"  <- {str(detail)[:300]}"))
    if not cond:
        ok = False


print("== voice per language ==")
for lang, locale in (("en", "en_US"), ("fr", "fr_FR"), ("sw", "sw_CD")):
    v = shortvideo.voice_for(lang)
    check(f"{lang}: natural voice declared {locale} by its metadata",
          v and v["engine"] == "piper" and v["quality"] == "natural" and v["locale"] == locale, v)
check("ar: no voice, so not offered", shortvideo.voice_for("ar") is None)
check("no language given: English", (shortvideo.voice_for(None) or {}).get("locale") == "en_US")

saved = dict(shortvideo.NATURAL_VOICES)
shortvideo.NATURAL_VOICES["en"] = "fr_FR-siwis-medium"  # a misconfiguration
v = shortvideo.voice_for("en")
check("a French voice configured for English is refused (basic English instead, reported)",
      v and v["engine"] == "espeak-ng" and v["quality"] == "basic" and v["locale"].startswith("en"), v)
shortvideo.NATURAL_VOICES["en"] = "en_US-not-installed"
v = shortvideo.voice_for("en")
check("a missing natural voice falls back to the basic one, reported as basic", v and v["quality"] == "basic", v)
shortvideo.NATURAL_VOICES.clear()
shortvideo.NATURAL_VOICES.update(saved)


def speech_seconds(path: Path) -> list[tuple[float, float]]:
    """(start, end) of the stretches where something is heard."""
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
         "-af", "silencedetect=noise=-40dB:d=0.25", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", done.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", done.stderr)]
    total = shortvideo._duration(path)
    # Speech = the gaps between silences.
    edges, cursor = [], 0.0
    for s, e in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if s > cursor + 0.05:
            edges.append((cursor, s))
        cursor = e
    if cursor < total - 0.05:
        edges.append((cursor, total))
    return edges


def render_and_check(lang: str, texts: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        out = work / "out.mp4"
        info = shortvideo.render(texts, [], out, work / "w", lang=lang)
        streams = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "json", str(out)],
            capture_output=True, text=True).stdout)["streams"]
        audio = [s for s in streams if s["codec_type"] == "audio"]
        check(f"{lang}: exactly one audio track in the exported video", len(audio) == 1, streams)
        check(f"{lang}: read by a natural voice of the language", info.get("voice", {}).get("quality") == "natural"
              and info["voice"]["locale"].startswith(lang), info.get("voice"))
        # Each card read once, on its own: the readings, measured separately...
        voice = shortvideo.voice_for(lang)
        singles = []
        for i, t in enumerate(texts):
            wav = work / f"single{i}.wav"
            shortvideo._speak(t, voice, wav)
            singles.append(wav)
        alone = sum(sum(e - s for s, e in speech_seconds(w)) for w in singles)
        heard = sum(e - s for s, e in speech_seconds(out))
        check(f"{lang}: speech time in the video = the cards' readings ({heard:.1f}s vs {alone:.1f}s), nothing doubled",
              abs(heard - alone) < 0.6 + 0.1 * len(texts), (heard, alone))
        # ...and every stretch of speech sits inside one card's time on screen.
        bounds, t0 = [], 0.0
        for t, wav in zip(texts, singles):
            length = round(max(shortvideo._seconds_for(t), shortvideo._duration(wav) + 0.6), 2)
            bounds.append((t0, t0 + length))
            t0 += length
        stray = [seg for seg in speech_seconds(out)
                 if not any(a - 0.15 <= seg[0] and seg[1] <= b + 0.15 for a, b in bounds)]
        check(f"{lang}: no speech overlaps from one card into the next", not stray, (stray, bounds))
        loud = shortvideo._mean_volume(out)
        check(f"{lang}: narration audible ({loud:.1f} dB mean)", loud is not None and loud > shortvideo.AUDIBLE_DB, loud)


print("== English video ==")
render_and_check("en", [
    "Chien Shiung Wu and the experiment that changed physics.",
    "In 1956 she designed an experiment with cobalt sixty at very low temperature.",
    "It showed that nature can tell left from right.",
])
print("== French video ==")
render_and_check("fr", [
    "Chien Shiung Wu et l'expérience qui a changé la physique.",
    "En 1956, elle a conçu une expérience avec du cobalt soixante à très basse température.",
])

print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
