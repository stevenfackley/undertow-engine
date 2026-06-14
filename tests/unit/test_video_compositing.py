"""Unit tests for app.video_compositing (ffmpeg + ASS pipeline)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

import app.video_compositing as module
from app.video_compositing import (
    ACTION_COLOR,
    DEFAULT_TEXT_COLOR,
    NEGATIVE_COLOR,
    TARGET_H,
    TARGET_W,
    _ass_escape,
    _build_ass,
    _build_ffmpeg_cmd,
    _format_ass_time,
    _random_start_offset,
    _word_color,
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
# _random_start_offset
# ---------------------------------------------------------------------------


def test_random_start_offset_within_range():
    for _ in range(30):
        offset = _random_start_offset(clip_duration=10.0, audio_duration=5.0)
        assert 0.0 <= offset <= 5.0


def test_random_start_offset_zero_when_clip_shorter():
    assert _random_start_offset(clip_duration=3.0, audio_duration=5.0) == 0.0


def test_random_start_offset_zero_when_exact_fit():
    assert _random_start_offset(clip_duration=5.0, audio_duration=5.0) == 0.0


# ---------------------------------------------------------------------------
# _format_ass_time
# ---------------------------------------------------------------------------


def test_format_ass_time_zero():
    assert _format_ass_time(0.0) == "0:00:00.00"


def test_format_ass_time_fractional():
    assert _format_ass_time(1.5) == "0:00:01.50"


def test_format_ass_time_minutes_and_centiseconds():
    assert _format_ass_time(65.25) == "0:01:05.25"


def test_format_ass_time_hours():
    assert _format_ass_time(3661.0) == "1:01:01.00"


def test_format_ass_time_negative_clamped():
    assert _format_ass_time(-3.0) == "0:00:00.00"


# ---------------------------------------------------------------------------
# _ass_escape
# ---------------------------------------------------------------------------


def test_ass_escape_strips_control_chars():
    assert _ass_escape("{\\bad}word") == "badword"


def test_ass_escape_trims_and_flattens_newlines():
    assert _ass_escape("  hi\nthere  ") == "hi there"


# ---------------------------------------------------------------------------
# _build_ass
# ---------------------------------------------------------------------------


def test_build_ass_has_header_and_montserrat_style():
    ass = _build_ass([])
    assert "[Script Info]" in ass
    assert f"PlayResX: {TARGET_W}" in ass
    assert f"PlayResY: {TARGET_H}" in ass
    assert "Montserrat Black" in ass
    assert "Dialogue:" not in ass  # no words → no events


def test_build_ass_one_dialogue_per_positive_duration_word():
    ts = [
        {"word": "go", "start": 0.0, "end": 0.5},
        {"word": "now", "start": 0.5, "end": 1.0},
    ]
    ass = _build_ass(ts)
    assert ass.count("Dialogue:") == 2


def test_build_ass_skips_zero_and_negative_duration():
    ts = [
        {"word": "skip", "start": 0.0, "end": 0.0},
        {"word": "back", "start": 1.0, "end": 0.5},
        {"word": "keep", "start": 1.0, "end": 1.5},
    ]
    ass = _build_ass(ts)
    assert ass.count("Dialogue:") == 1
    assert "keep" in ass


def test_build_ass_colours_by_category():
    ts = [
        {"word": "fail", "start": 0.0, "end": 0.5},  # negative → red
        {"word": "ship", "start": 0.5, "end": 1.0},  # action → yellow
        {"word": "the", "start": 1.0, "end": 1.5},  # default → white
    ]
    ass = _build_ass(ts)
    assert "\\c&H0000FF&" in ass  # red (BGR)
    assert "\\c&H00FFFF&" in ass  # yellow
    assert "\\c&HFFFFFF&" in ass  # white


def test_build_ass_centres_word_at_75_percent_height():
    ts = [{"word": "hi", "start": 0.0, "end": 0.5}]
    ass = _build_ass(ts)
    expected_x = TARGET_W // 2
    expected_y = int(round(TARGET_H * module.TEXT_Y_FRACTION))
    assert f"\\an5\\pos({expected_x},{expected_y})" in ass


def test_build_ass_escapes_word_braces():
    ts = [{"word": "ev{i}l", "start": 0.0, "end": 0.5}]
    ass = _build_ass(ts)
    # the only braces left are the ASS override block, not from the word
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")][0]
    assert dialogue.endswith("evil")


# ---------------------------------------------------------------------------
# _build_ffmpeg_cmd
# ---------------------------------------------------------------------------


def _cmd(**overrides):
    args = dict(
        bg_path="/tmp/bg.mp4",
        audio_path="/tmp/a.mp3",
        ass_path="/tmp/c.ass",
        offset=0.0,
        duration=5.0,
        loop=False,
        output_path="/out/x.mp4",
    )
    args.update(overrides)
    return _build_ffmpeg_cmd(**args)


def test_ffmpeg_cmd_scales_and_crops_to_portrait():
    fc = _cmd()
    filtergraph = fc[fc.index("-filter_complex") + 1]
    assert f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase" in filtergraph
    assert f"crop={TARGET_W}:{TARGET_H}" in filtergraph
    assert "subtitles=" in filtergraph


def test_ffmpeg_cmd_maps_video_and_audio():
    fc = _cmd()
    assert fc[fc.index("-map") : fc.index("-map") + 2] == ["-map", "[v]"]
    assert "1:a" in fc  # audio mapped from the second input


def test_ffmpeg_cmd_codecs_and_duration():
    fc = _cmd(duration=7.5)
    assert "libx264" in fc
    assert "aac" in fc
    assert fc[fc.index("-t") + 1] == "7.500"
    assert fc[-1] == "/out/x.mp4"


def test_ffmpeg_cmd_loops_when_flagged():
    assert "-stream_loop" in _cmd(loop=True)
    assert "-stream_loop" not in _cmd(loop=False)


def test_ffmpeg_cmd_seeks_only_with_offset():
    assert "-ss" in _cmd(offset=3.2)
    assert "-ss" not in _cmd(offset=0.0)


# ---------------------------------------------------------------------------
# Real render smoke test
#
# Replaces the moviepy migration's whole class of breakage: exercises the
# actual ffmpeg filtergraph (scale/crop + libass subtitle burn + audio mux +
# encode) end-to-end on generated inputs. Skipped when ffmpeg isn't on PATH so
# the unit suite stays hermetic; runs in any env that has ffmpeg (incl. the
# production image).
# ---------------------------------------------------------------------------

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_real_ffmpeg_render_produces_valid_mp4(tmp_path):
    bg = tmp_path / "bg.mp4"
    audio = tmp_path / "a.wav"
    ass = tmp_path / "c.ass"
    out = tmp_path / "out.mp4"

    # 2s 320x240 test pattern + 1s tone, via ffmpeg's built-in sources.
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2", str(bg)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio)],
        check=True, capture_output=True,
    )
    ass.write_text(_build_ass([{"word": "test", "start": 0.0, "end": 1.0}]), encoding="utf-8")

    cmd = _build_ffmpeg_cmd(bg, audio, ass, offset=0.0, duration=1.0, loop=False, output_path=out)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert out.exists() and out.stat().st_size > 0
    assert abs(module._probe_duration(out) - 1.0) < 0.3
