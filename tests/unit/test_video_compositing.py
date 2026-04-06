"""Unit tests for app.video_compositing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app.video_compositing as module
from app.video_compositing import (
    TARGET_H,
    TARGET_W,
    _word_color,
    ACTION_COLOR,
    DEFAULT_TEXT_COLOR,
    NEGATIVE_COLOR,
)


# ---------------------------------------------------------------------------
# _word_color
# ---------------------------------------------------------------------------

def test_word_color_negative_word():
    assert _word_color("never") == NEGATIVE_COLOR


def test_word_color_action_word():
    assert _word_color("build") == ACTION_COLOR


def test_word_color_default():
    assert _word_color("the") == DEFAULT_TEXT_COLOR


def test_word_color_case_insensitive():
    assert _word_color("NEVER") == NEGATIVE_COLOR
    assert _word_color("BUILD") == ACTION_COLOR


def test_word_color_strips_punctuation():
    assert _word_color("never!") == NEGATIVE_COLOR
    assert _word_color("build,") == ACTION_COLOR


# ---------------------------------------------------------------------------
# _to_portrait
# ---------------------------------------------------------------------------

def _mock_clip(w: int, h: int) -> MagicMock:
    clip = MagicMock()
    clip.size = (w, h)
    scaled = MagicMock()
    cropped = MagicMock()
    clip.resize.return_value = scaled
    scaled.crop.return_value = cropped
    scaled.size = (w, h)  # default; overridden per test
    return clip, scaled, cropped


def test_to_portrait_landscape_resizes_to_height():
    """1920×1080 landscape → fit height to 1920, crop width."""
    clip, scaled, cropped = _mock_clip(1920, 1080)
    scaled.size = (3413, TARGET_H)

    result = module._to_portrait(clip)

    clip.resize.assert_called_once_with(height=TARGET_H)
    scaled.crop.assert_called_once()
    assert result is cropped


def test_to_portrait_landscape_crops_to_target_width():
    clip, scaled, cropped = _mock_clip(1920, 1080)
    scaled.size = (3413, TARGET_H)

    module._to_portrait(clip)

    crop_kwargs = scaled.crop.call_args.kwargs
    assert crop_kwargs["x2"] - crop_kwargs["x1"] == TARGET_W


def test_to_portrait_tall_resizes_to_width():
    """600×1200 portrait (narrower than 9:16) → fit width to 1080, crop height."""
    clip, scaled, cropped = _mock_clip(600, 1200)
    scaled.size = (TARGET_W, 2160)

    result = module._to_portrait(clip)

    clip.resize.assert_called_once_with(width=TARGET_W)
    scaled.crop.assert_called_once()
    assert result is cropped


def test_to_portrait_tall_crops_to_target_height():
    clip, scaled, cropped = _mock_clip(600, 1200)
    scaled.size = (TARGET_W, 2160)

    module._to_portrait(clip)

    crop_kwargs = scaled.crop.call_args.kwargs
    assert crop_kwargs["y2"] - crop_kwargs["y1"] == TARGET_H


def test_to_portrait_exact_9_16_still_calls_resize():
    """Exact 9:16 input is treated as portrait (not landscape), resizes to width."""
    clip, scaled, cropped = _mock_clip(TARGET_W, TARGET_H)
    scaled.size = (TARGET_W, TARGET_H)

    module._to_portrait(clip)

    clip.resize.assert_called_once_with(width=TARGET_W)


# ---------------------------------------------------------------------------
# _random_start_offset
# ---------------------------------------------------------------------------

def test_random_start_offset_within_range():
    for _ in range(30):
        offset = module._random_start_offset(clip_duration=10.0, audio_duration=5.0)
        assert 0.0 <= offset <= 5.0


def test_random_start_offset_zero_when_clip_shorter():
    assert module._random_start_offset(clip_duration=3.0, audio_duration=5.0) == 0.0


def test_random_start_offset_zero_when_exact_fit():
    assert module._random_start_offset(clip_duration=5.0, audio_duration=5.0) == 0.0


# ---------------------------------------------------------------------------
# _build_text_clips
# ---------------------------------------------------------------------------

def test_build_text_clips_skips_zero_duration():
    timestamps = [
        {"word": "fast", "start": 0.0, "end": 0.0},
        {"word": "go", "start": 0.5, "end": 0.8},
    ]
    with patch("app.video_compositing.TextClip") as mock_tc:
        mock_clip = MagicMock()
        mock_clip.set_start.return_value = mock_clip
        mock_clip.set_duration.return_value = mock_clip
        mock_clip.set_position.return_value = mock_clip
        mock_tc.return_value = mock_clip

        clips = module._build_text_clips(timestamps, (1080, 1920))

    assert len(clips) == 1
    mock_tc.assert_called_once()
    assert mock_tc.call_args.args[0] == "go"


def test_build_text_clips_negative_duration_skipped():
    timestamps = [{"word": "bad", "start": 1.0, "end": 0.5}]
    with patch("app.video_compositing.TextClip") as mock_tc:
        clips = module._build_text_clips(timestamps, (1080, 1920))
    assert clips == []
    mock_tc.assert_not_called()


def test_build_text_clips_correct_color_for_negative():
    timestamps = [{"word": "fail", "start": 0.0, "end": 0.5}]
    with patch("app.video_compositing.TextClip") as mock_tc:
        mock_clip = MagicMock()
        mock_clip.set_start.return_value = mock_clip
        mock_clip.set_duration.return_value = mock_clip
        mock_clip.set_position.return_value = mock_clip
        mock_tc.return_value = mock_clip

        module._build_text_clips(timestamps, (1080, 1920))

    assert mock_tc.call_args.kwargs["color"] == NEGATIVE_COLOR


# ---------------------------------------------------------------------------
# compose_video
# ---------------------------------------------------------------------------

def test_compose_video_calls_write_videofile(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")
    output = tmp_path / "out.mp4"
    timestamps = [{"word": "go", "start": 0.0, "end": 0.5}]

    mock_audio_clip = MagicMock()
    mock_audio_clip.duration = 5.0

    mock_bg_clip = MagicMock()
    mock_bg_clip.duration = 10.0  # longer than audio — no looping
    mock_bg_clip.size = (TARGET_W, TARGET_H)
    mock_bg_clip.subclip.return_value = mock_bg_clip
    mock_bg_clip.set_audio.return_value = mock_bg_clip

    mock_final = MagicMock()

    with patch("app.video_compositing.AudioFileClip", return_value=mock_audio_clip):
        with patch("app.video_compositing._download_video"):
            with patch("app.video_compositing.VideoFileClip", return_value=mock_bg_clip):
                with patch("app.video_compositing._to_portrait", return_value=mock_bg_clip):
                    with patch("app.video_compositing._build_text_clips", return_value=[]):
                        with patch("app.video_compositing.CompositeVideoClip", return_value=mock_final):
                            result = module.compose_video(
                                background_video_url="http://example.com/bg.mp4",
                                audio_path=audio,
                                word_timestamps=timestamps,
                                output_path=output,
                            )

    mock_final.write_videofile.assert_called_once()
    assert result == output


def test_compose_video_loops_short_clip(tmp_path):
    """Background shorter than audio → concatenate_videoclips should be called."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    mock_audio_clip = MagicMock()
    mock_audio_clip.duration = 10.0

    mock_bg_clip = MagicMock()
    mock_bg_clip.duration = 3.0  # shorter than audio
    mock_bg_clip.size = (TARGET_W, TARGET_H)
    mock_bg_clip.subclip.return_value = mock_bg_clip
    mock_bg_clip.set_audio.return_value = mock_bg_clip

    mock_looped = MagicMock()
    mock_looped.duration = 12.0
    mock_looped.size = (TARGET_W, TARGET_H)
    mock_looped.subclip.return_value = mock_looped
    mock_looped.set_audio.return_value = mock_looped

    mock_final = MagicMock()

    with patch("app.video_compositing.AudioFileClip", return_value=mock_audio_clip):
        with patch("app.video_compositing._download_video"):
            with patch("app.video_compositing.VideoFileClip", return_value=mock_bg_clip):
                with patch("app.video_compositing.concatenate_videoclips", return_value=mock_looped) as mock_concat:
                    with patch("app.video_compositing._to_portrait", return_value=mock_looped):
                        with patch("app.video_compositing._build_text_clips", return_value=[]):
                            with patch("app.video_compositing.CompositeVideoClip", return_value=mock_final):
                                module.compose_video(
                                    background_video_url="http://example.com/bg.mp4",
                                    audio_path=audio,
                                    word_timestamps=[],
                                    output_path=tmp_path / "out.mp4",
                                )

    mock_concat.assert_called_once()
