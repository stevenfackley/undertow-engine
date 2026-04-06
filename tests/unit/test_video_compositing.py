"""Unit tests for app.video_compositing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import app.video_compositing as module
from app.video_compositing import (
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

    call_kwargs = mock_tc.call_args.kwargs
    assert call_kwargs["color"] == NEGATIVE_COLOR


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
    mock_bg_clip.size = (1080, 1920)
    mock_bg_clip.subclip.return_value = mock_bg_clip
    mock_bg_clip.set_audio.return_value = mock_bg_clip

    mock_final = MagicMock()

    with patch("app.video_compositing.AudioFileClip", return_value=mock_audio_clip):
        with patch("app.video_compositing._download_video") as mock_dl:
            mock_dl.return_value = Path("/tmp/fake.mp4")
            with patch("app.video_compositing.VideoFileClip", return_value=mock_bg_clip):
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
