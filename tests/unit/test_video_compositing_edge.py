"""Edge-case tests for app.video_compositing download guards and failure paths.

The happy-path helpers (_word_color, _build_ass, _build_ffmpeg_cmd, real ffmpeg
render) are covered in test_video_compositing.py; this file exercises the
guard rails: content-type/size enforcement on downloads and ffmpeg failure
propagation in compose_video.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

import app.video_compositing as module
from app.video_compositing import _download_video, _resolve_background, compose_video

# ---------------------------------------------------------------------------
# _download_video guard rails
# ---------------------------------------------------------------------------


def _fake_stream(headers: dict, chunks: list[bytes] | None = None):
    """Context-manager mock mimicking httpx.stream()."""
    response = MagicMock()
    response.headers = headers
    response.iter_bytes.return_value = chunks or []
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=response)
    stream_cm.__exit__ = MagicMock(return_value=False)
    return stream_cm, response


def test_download_rejects_non_video_content_type(tmp_path):
    stream_cm, _ = _fake_stream({"content-type": "text/html"})
    with patch("app.video_compositing.httpx.stream", return_value=stream_cm):
        with pytest.raises(ValueError, match="Expected video/"):
            _download_video("http://example.com/page", tmp_path / "bg.mp4")


def test_download_enforces_size_cap(tmp_path):
    stream_cm, _ = _fake_stream({"content-type": "video/mp4"}, chunks=[b"x" * 1024, b"y" * 1024])
    with patch.object(module, "MAX_VIDEO_BYTES", 1500):
        with patch("app.video_compositing.httpx.stream", return_value=stream_cm):
            with pytest.raises(ValueError, match="exceeds"):
                _download_video("http://example.com/huge.mp4", tmp_path / "bg.mp4")


def test_download_propagates_http_errors(tmp_path):
    stream_cm, response = _fake_stream({"content-type": "video/mp4"})
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock(status_code=404)
    )
    with patch("app.video_compositing.httpx.stream", return_value=stream_cm):
        with pytest.raises(httpx.HTTPStatusError):
            _download_video("http://example.com/missing.mp4", tmp_path / "bg.mp4")


def test_download_writes_streamed_bytes(tmp_path):
    dest = tmp_path / "bg.mp4"
    stream_cm, _ = _fake_stream({"content-type": "video/mp4"}, chunks=[b"abc", b"def"])
    with patch("app.video_compositing.httpx.stream", return_value=stream_cm):
        result = _download_video("http://example.com/ok.mp4", dest)

    assert result == dest
    assert dest.read_bytes() == b"abcdef"


# ---------------------------------------------------------------------------
# _resolve_background — local path vs URL dispatch
# ---------------------------------------------------------------------------


def test_resolve_background_copies_local_file(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"local video bytes")
    dest = tmp_path / "dest.mp4"

    result = _resolve_background(str(src), dest)

    assert result == dest
    assert dest.read_bytes() == b"local video bytes"


def test_resolve_background_missing_local_file_raises(tmp_path):
    missing = tmp_path / "nope.mp4"
    with pytest.raises(FileNotFoundError):
        _resolve_background(str(missing), tmp_path / "dest.mp4")


def test_resolve_background_delegates_urls_to_download(tmp_path):
    dest = tmp_path / "dest.mp4"
    with patch.object(module, "_download_video", return_value=dest) as mock_dl:
        result = _resolve_background("https://example.com/bg.mp4", dest)

    mock_dl.assert_called_once_with("https://example.com/bg.mp4", dest)
    assert result == dest


# ---------------------------------------------------------------------------
# compose_video failure propagation
# ---------------------------------------------------------------------------


def _compose(tmp_path, run_result):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake audio")
    output = tmp_path / "out" / "final.mp4"

    def fake_download(url, dest):
        dest.write_bytes(b"fake video")
        return dest

    with patch.object(module, "_probe_duration", side_effect=[10.0, 30.0]):
        with patch.object(module, "_download_video", side_effect=fake_download):
            with patch("app.video_compositing.subprocess.run", return_value=run_result):
                return compose_video(
                    background_video_url="http://example.com/bg.mp4",
                    audio_path=audio,
                    word_timestamps=[],
                    output_path=output,
                )


def test_compose_video_raises_on_ffmpeg_failure(tmp_path):
    proc = MagicMock(returncode=1, stderr="ffmpeg: filtergraph error")
    with pytest.raises(RuntimeError, match="ffmpeg compositing failed"):
        _compose(tmp_path, proc)


def test_compose_video_error_includes_ffmpeg_stderr_tail(tmp_path):
    proc = MagicMock(returncode=187, stderr="x" * 3000 + "TAIL-MARKER")
    with pytest.raises(RuntimeError, match="TAIL-MARKER"):
        _compose(tmp_path, proc)


def test_compose_video_returns_output_and_creates_parent_dir(tmp_path):
    proc = MagicMock(returncode=0, stderr="")
    result = _compose(tmp_path, proc)
    assert result == tmp_path / "out" / "final.mp4"
    assert result.parent.is_dir()


def test_compose_video_accepts_local_background_path(tmp_path):
    """The roast_queue background_video column documents 'local path OR public
    URL' — a local path must compose without any network access."""
    background = tmp_path / "gameplay.mp4"
    background.write_bytes(b"local gameplay bytes")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake audio")
    output = tmp_path / "final.mp4"

    proc = MagicMock(returncode=0, stderr="")
    with patch.object(module, "_probe_duration", side_effect=[10.0, 30.0]):
        with patch("app.video_compositing.subprocess.run", return_value=proc) as mock_run:
            with patch("app.video_compositing.httpx.stream") as mock_stream:
                result = compose_video(
                    background_video_url=str(background),
                    audio_path=audio,
                    word_timestamps=[],
                    output_path=output,
                )

    assert result == output
    mock_stream.assert_not_called()
    # ffmpeg was pointed at the materialised local copy, not the original.
    cmd = mock_run.call_args.args[0]
    assert any("background_raw.mp4" in str(part) for part in cmd)
