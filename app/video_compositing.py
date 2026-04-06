"""
Video Compositing Module
------------------------
Uses MoviePy to:
1. Download and crop a background gameplay video to the length of the audio.
2. Overlay dynamic, word-by-word kinetic text (Montserrat Black font).
3. Highlight negative words in red and action words in yellow based on
   Whisper word timestamps.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Sequence

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


def _word_color(word: str) -> str:
    normalised = word.lower().strip(".,!?;:'\"")
    if normalised in NEGATIVE_WORDS:
        return NEGATIVE_COLOR
    if normalised in ACTION_WORDS:
        return ACTION_COLOR
    return DEFAULT_TEXT_COLOR


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


def _build_text_clips(
    word_timestamps: list[WordTimestamp],
    video_size: tuple[int, int],
) -> list[TextClip]:
    """Create a TextClip for every word timed according to Whisper timestamps."""
    clips: list[TextClip] = []
    width, height = video_size
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


def compose_video(
    background_video_url: str,
    audio_path: str | Path,
    word_timestamps: list[WordTimestamp],
    output_path: str | Path,
) -> Path:
    """
    Compose the final MP4.

    1. Downloads *background_video_url*.
    2. Crops the background to the audio duration.
    3. Overlays kinetic word-by-word subtitles.
    4. Writes the result to *output_path*.

    Parameters
    ----------
    background_video_url:
        Public URL of the background gameplay video.
    audio_path:
        Path to the processed audio file.
    word_timestamps:
        Per-word timestamps from Whisper.
    output_path:
        Destination path for the rendered MP4.

    Returns
    -------
    Path
        Path to the rendered MP4 file.
    """
    audio_path = Path(audio_path)
    output_path = Path(output_path)

    audio_clip = AudioFileClip(str(audio_path))
    audio_duration = audio_clip.duration

    with tempfile.TemporaryDirectory() as tmpdir:
        raw_video_path = Path(tmpdir) / "background_raw.mp4"
        _download_video(background_video_url, raw_video_path)

        bg_clip = VideoFileClip(str(raw_video_path))
        if bg_clip.duration < audio_duration:
            n = int(audio_duration / bg_clip.duration) + 1
            bg_clip = concatenate_videoclips([bg_clip] * n)
        bg_clip = bg_clip.subclip(0, audio_duration)
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
