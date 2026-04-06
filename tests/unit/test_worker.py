"""Unit tests for worker helpers and tasks."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import worker as module


# ---------------------------------------------------------------------------
# _fire_webhook
# ---------------------------------------------------------------------------

def test_fire_webhook_posts_json_payload():
    with patch("worker.httpx") as mock_httpx:
        module._fire_webhook("http://example.com/cb", {"status": "complete"})
    mock_httpx.post.assert_called_once_with(
        "http://example.com/cb",
        json={"status": "complete"},
        timeout=10.0,
    )


def test_fire_webhook_silent_on_http_error():
    with patch("worker.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("connection refused")
        # Should not raise
        module._fire_webhook("http://example.com/cb", {})


def test_fire_webhook_silent_on_bad_url():
    with patch("worker.httpx") as mock_httpx:
        mock_httpx.post.side_effect = ValueError("invalid url")
        module._fire_webhook("not-a-url", {})


# ---------------------------------------------------------------------------
# cleanup_old_outputs
# ---------------------------------------------------------------------------

def test_cleanup_deletes_old_files(tmp_path):
    old = tmp_path / "old.mp4"
    old.write_bytes(b"x")
    # Backdate mtime to 8 days ago
    old_mtime = time.time() - 8 * 86400
    import os
    os.utime(old, (old_mtime, old_mtime))

    recent = tmp_path / "recent.mp4"
    recent.write_bytes(b"y")

    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        result = module.cleanup_old_outputs(max_age_days=7)

    assert result["deleted"] == 1
    assert not old.exists()
    assert recent.exists()


def test_cleanup_keeps_recent_files(tmp_path):
    recent = tmp_path / "fresh.mp4"
    recent.write_bytes(b"z")

    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        result = module.cleanup_old_outputs(max_age_days=7)

    assert result["deleted"] == 0
    assert recent.exists()


def test_cleanup_missing_dir_returns_zero():
    with patch.dict("os.environ", {"OUTPUT_DIR": "/nonexistent/path/12345"}):
        result = module.cleanup_old_outputs(max_age_days=7)
    assert result == {"deleted": 0}


def test_cleanup_only_targets_mp4_files(tmp_path):
    other = tmp_path / "old.txt"
    other.write_bytes(b"x")
    import os
    old_mtime = time.time() - 10 * 86400
    os.utime(other, (old_mtime, old_mtime))

    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        result = module.cleanup_old_outputs(max_age_days=7)

    assert result["deleted"] == 0
    assert other.exists()
