"""
Video Compositing Module
------------------------
Uses MoviePy to:
1. Download and crop a background gameplay video to the length of the audio.
2. Crop/resize the background to 9:16 portrait (1080x1920) for TikTok/Reels.
3. Pick a random start offset within the background for content variety.
4. Overlay dynamic, word-by-word kinetic text (Montserrat Black font).
5. Highlight negative words in red and action words in yellow based on
   Whisper word timestamps.
"""

from __future__ import annotations

import os
import random
import tempfile
from pathlib import Path

import httpx
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)

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

FONT = "Montserrat-Black"
FONT_SIZE = 70
DEFAULT_TEXT_COLOR = "white"
NEGATIVE_COLOR = "red"
ACTION_COLOR = "yellow"
TEXT_POSITION = ("center", 0.75)  # centred horizontally, 75 % down vertically

# Target output dimensions for TikTok / Instagram Reels (9:16 portrait)
TARGET_W = 1080
TARGET_H = 1920


def _word_color(word: str) -> str:
    normalised = word.lower().strip(".,!?;:'\"")
    if normalised in NEGATIVE_WORDS:
        return NEGATIVE_COLOR
    if normalised in ACTION_WORDS:
        return ACTION_COLOR
    return DEFAULT_TEXT_COLOR


# ---------------------------------------------------------------------------
# Video helpers
# ---------------------------------------------------------------------------

MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_MB", "500")) * 1024 * 1024


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


def _to_portrait(clip: VideoFileClip) -> VideoFileClip:
    """
    Centre-crop *clip* to 9:16 portrait (1080×1920).

    - Landscape source: scale to TARGET_H height, crop excess width.
    - Portrait/square source: scale to TARGET_W width, crop excess height.
    """
    w, h = clip.size
    if (w / h) > (TARGET_W / TARGET_H):
        # Wider than 9:16 — fit height, crop sides
        scaled = clip.resize(height=TARGET_H)
        excess = scaled.size[0] - TARGET_W
        return scaled.crop(x1=excess // 2, x2=excess // 2 + TARGET_W)
    else:
        # Taller than 9:16 — fit width, crop top/bottom
        scaled = clip.resize(width=TARGET_W)
        excess = scaled.size[1] - TARGET_H
        return scaled.crop(y1=excess // 2, y2=excess // 2 + TARGET_H)


def _random_start_offset(clip_duration: float, audio_duration: float) -> float:
    """Return a random start time within the background that leaves room for *audio_duration*."""
    headroom = clip_duration - audio_duration
    if headroom <= 0:
        return 0.0
    return random.uniform(0, headroom)


# ---------------------------------------------------------------------------
# Text clips
# ---------------------------------------------------------------------------


def _build_text_clips(
    word_timestamps: list[WordTimestamp],
    video_size: tuple[int, int],
) -> list[TextClip]:
    """Create a TextClip for every word timed according to Whisper timestamps."""
    clips: list[TextClip] = []
    for wt in word_timestamps:
        duration = wt["end"] - wt["start"]
        if duration <= 0:
            continue
        clip = (
            TextClip(
                wt["word"],
                fontsize=FONT_SIZE,
                font=FONT,
                color=_word_color(wt["word"]),
                stroke_color="black",
                stroke_width=3,
                method="label",
            )
            .set_start(wt["start"])
            .set_duration(duration)
            .set_position(TEXT_POSITION, relative=True)
        )
        clips.append(clip)
    return clips


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
    2. Loops if shorter than audio, then crops to a random start offset.
    3. Centre-crops to 9:16 portrait (1080×1920).
    4. Overlays kinetic word-by-word subtitles.
    5. Writes the result to *output_path*.
    """
    audio_path = Path(audio_path)
    output_path = Path(output_path)

    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_video_path = Path(tmpdir) / "background_raw.mp4"
        _download_video(background_video_url, raw_video_path)

        bg_clip = VideoFileClip(str(raw_video_path))

        # Loop if the clip is shorter than the audio
        if bg_clip.duration < audio_duration:
            n = int(audio_duration / bg_clip.duration) + 1
            bg_clip = concatenate_videoclips([bg_clip] * n)

        # Random start for content variety
        offset = _random_start_offset(bg_clip.duration, audio_duration)
        bg_clip = bg_clip.subclip(offset, offset + audio_duration)

        # Crop to 9:16 portrait
        bg_clip = _to_portrait(bg_clip)

        bg_clip = bg_clip.set_audio(audio_clip)

        text_clips = _build_text_clips(word_timestamps, bg_clip.size)

        final = CompositeVideoClip([bg_clip, *text_clips])
        final.write_videofile(
            str(output_path),
            codec="libx264",
            audio_codec="aac",
            fps=30,
            preset="fast",
            logger=None,
        )

    return output_path
