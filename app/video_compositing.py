"""
Video Compositing Module
------------------------
Renders the final short-form video with **ffmpeg** (no MoviePy):

1. Download the background gameplay video.
2. Pick a random start offset (loop the source if it is shorter than the audio).
3. Scale-to-cover and centre-crop to 9:16 portrait (1080×1920).
4. Burn word-by-word kinetic captions from a generated ASS subtitle, coloured
   by word category (negative = red, action = yellow, else white) and timed to
   the Whisper word timestamps.
5. Mux the voiceover audio and encode to H.264 / AAC.

ffmpeg (with libass) and the Montserrat fonts are provided by the image — see
the Dockerfile. This replaces the former MoviePy 1.0.3 pipeline, which was
unmaintained and incompatible with modern Pillow (it called the removed
Image.ANTIALIAS). See DECISIONS.md.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.timestamp_extraction import WordTimestamp

# ---------------------------------------------------------------------------
# Word colour classification
# ---------------------------------------------------------------------------

NEGATIVE_WORDS: frozenset[str] = frozenset(
    {
        "never", "no", "not", "fail", "failing", "failed", "wrong", "bad",
        "worst", "terrible", "awful", "broken", "dead", "dying", "hate",
        "hated", "lost", "lose", "losing", "stop", "stopped", "quit",
        "quitting", "refuse", "denied", "blocked", "banned",
    }
)

ACTION_WORDS: frozenset[str] = frozenset(
    {
        "start", "do", "build", "create", "launch", "grow", "learn",
        "master", "unlock", "discover", "achieve", "win", "winning",
        "grind", "hustle", "push", "run", "execute", "ship", "deploy",
        "automate", "scale", "dominate", "crush",
    }
)

FONT = "Montserrat Black"  # fontconfig name; libass resolves it (fonts-montserrat)
FONT_SIZE = 80
DEFAULT_TEXT_COLOR = "white"
NEGATIVE_COLOR = "red"
ACTION_COLOR = "yellow"

# ASS inline \c colours are &H<BB><GG><RR>& (blue-green-red, the reverse of web hex).
_ASS_COLOR_BGR = {
    "white": "FFFFFF",
    "red": "0000FF",
    "yellow": "00FFFF",
}

# Target output dimensions for TikTok / Instagram Reels (9:16 portrait)
TARGET_W = 1080
TARGET_H = 1920
# Captions sit at 75 % of the height, horizontally centred.
TEXT_Y_FRACTION = 0.75

MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_MB", "500")) * 1024 * 1024

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def _word_color(word: str) -> str:
    normalised = word.lower().strip(".,!?;:'\"")
    if normalised in NEGATIVE_WORDS:
        return NEGATIVE_COLOR
    if normalised in ACTION_WORDS:
        return ACTION_COLOR
    return DEFAULT_TEXT_COLOR


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------


def _download_video(url: str, dest: Path) -> Path:
    """Stream *url* to *dest*, enforcing a size cap and video content-type check."""
    with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
        r.raise_for_status()
        content_type = r.headers.get("content-type", "")
        if not content_type.startswith("video/"):
            raise ValueError(f"Expected video/* content-type, got {content_type!r}")
        total = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise ValueError(
                        f"Background video exceeds {MAX_VIDEO_BYTES // (1024 * 1024)} MB limit"
                    )
                f.write(chunk)
    return dest


def _probe_duration(path: str | Path) -> float:
    """Return the media duration of *path* in seconds via ffprobe."""
    result = subprocess.run(
        [
            FFPROBE,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def _random_start_offset(clip_duration: float, audio_duration: float) -> float:
    """Return a random start time that leaves room for *audio_duration*."""
    headroom = clip_duration - audio_duration
    if headroom <= 0:
        return 0.0
    return random.uniform(0, headroom)


# ---------------------------------------------------------------------------
# ASS subtitle generation (kinetic word-by-word captions)
# ---------------------------------------------------------------------------


def _format_ass_time(seconds: float) -> str:
    """Format *seconds* as an ASS timestamp ``H:MM:SS.cc`` (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    hours, total_cs = divmod(total_cs, 360_000)
    minutes, total_cs = divmod(total_cs, 6_000)
    secs, cs = divmod(total_cs, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_escape(word: str) -> str:
    """Strip ASS control characters so a caption word can't break the markup."""
    return (
        word.replace("\\", "")
        .replace("{", "")
        .replace("}", "")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def _build_ass(word_timestamps: list[WordTimestamp]) -> str:
    """
    Build an ASS subtitle: one centred, outlined, colour-coded ``Dialogue`` per
    word, anchored at 75 % height and timed to the Whisper word timestamps.
    Words with non-positive duration (or empty after escaping) are skipped.
    """
    x = TARGET_W // 2
    y = int(round(TARGET_H * TEXT_Y_FRACTION))

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {TARGET_W}\n"
        f"PlayResY: {TARGET_H}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{FONT},{FONT_SIZE},&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,3,0,5,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
    )

    lines: list[str] = []
    for wt in word_timestamps:
        if wt["end"] - wt["start"] <= 0:
            continue
        word = _ass_escape(wt["word"])
        if not word:
            continue
        bgr = _ASS_COLOR_BGR[_word_color(wt["word"])]
        start = _format_ass_time(wt["start"])
        end = _format_ass_time(wt["end"])
        text = f"{{\\an5\\pos({x},{y})\\c&H{bgr}&}}{word}"
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    body = "\n".join(lines)
    return header + body + ("\n" if body else "")


# ---------------------------------------------------------------------------
# ffmpeg command construction
# ---------------------------------------------------------------------------


def _escape_filter_path(path: str | Path) -> str:
    """Escape a filesystem path for use inside an ffmpeg filtergraph value."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_ffmpeg_cmd(
    bg_path: str | Path,
    audio_path: str | Path,
    ass_path: str | Path,
    offset: float,
    duration: float,
    loop: bool,
    output_path: str | Path,
) -> list[str]:
    """
    Build the ffmpeg command that crops the background to 9:16, burns the ASS
    captions, muxes the voiceover, and encodes to H.264/AAC trimmed to
    *duration*. Loops the background input when it is shorter than the audio.
    """
    video_filter = (
        f"[0:v]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},setsar=1,"
        f"subtitles={_escape_filter_path(ass_path)}[v]"
    )

    cmd: list[str] = [FFMPEG, "-y"]
    if loop:
        cmd += ["-stream_loop", "-1"]
    if offset > 0:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += [
        "-i", str(bg_path),
        "-i", str(audio_path),
        "-filter_complex", video_filter,
        "-map", "[v]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{duration:.3f}",
        "-r", "30",
        str(output_path),
    ]
    return cmd


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compose_video(
    background_video_url: str,
    audio_path: str | Path,
    word_timestamps: list[WordTimestamp],
    output_path: str | Path,
) -> Path:
    """
    Compose the final MP4.

    1. Downloads *background_video_url*.
    2. Loops it if shorter than the audio, then seeks to a random start offset.
    3. Scale-covers and centre-crops to 9:16 portrait (1080×1920).
    4. Burns kinetic word-by-word subtitles.
    5. Muxes *audio_path* and writes the result to *output_path*.
    """
    audio_path = Path(audio_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_duration = _probe_duration(audio_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        raw_video_path = tmp / "background_raw.mp4"
        _download_video(background_video_url, raw_video_path)

        bg_duration = _probe_duration(raw_video_path)
        offset = _random_start_offset(bg_duration, audio_duration)
        loop = bg_duration < audio_duration

        ass_path = tmp / "captions.ass"
        ass_path.write_text(_build_ass(word_timestamps), encoding="utf-8")

        cmd = _build_ffmpeg_cmd(
            raw_video_path, audio_path, ass_path, offset, audio_duration, loop, output_path
        )
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg compositing failed (exit {proc.returncode}): {proc.stderr[-2000:]}"
            )

    return output_path
