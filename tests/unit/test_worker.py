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

def test_fire_webhook_posts_serialized_body_unsigned():
    with patch("worker.httpx") as mock_httpx:
        module._fire_webhook("http://example.com/cb", {"status": "complete"})
    _, kwargs = mock_httpx.post.call_args
    assert mock_httpx.post.call_args.args[0] == "http://example.com/cb"
    assert kwargs["content"] == b'{"status":"complete"}'
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert "X-Undertow-Signature" not in kwargs["headers"]
    assert kwargs["timeout"] == 10.0


def test_fire_webhook_signs_body_when_secret_set():
    import hashlib
    import hmac

    secret = "topsecret"
    body = b'{"status":"complete"}'
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch("worker.httpx") as mock_httpx:
        module._fire_webhook("http://example.com/cb", {"status": "complete"}, secret=secret)

    _, kwargs = mock_httpx.post.call_args
    assert kwargs["content"] == body
    assert kwargs["headers"]["X-Undertow-Signature"] == expected


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


# ---------------------------------------------------------------------------
# Callback URL resolution + completion webhook
# ---------------------------------------------------------------------------

def test_resolve_callback_url_prefers_per_request():
    with patch.dict("os.environ", {"JOB_WEBHOOK_URL": "http://env/cb"}):
        assert module._resolve_callback_url("http://req/cb") == "http://req/cb"


def test_resolve_callback_url_falls_back_to_env():
    with patch.dict("os.environ", {"JOB_WEBHOOK_URL": "http://env/cb"}):
        assert module._resolve_callback_url(None) == "http://env/cb"


def test_resolve_callback_url_none_when_unset():
    with patch.dict("os.environ", {}, clear=True):
        assert module._resolve_callback_url(None) is None


def test_send_completion_webhook_noop_when_no_url():
    with patch.dict("os.environ", {}, clear=True):
        with patch("worker._fire_webhook") as mock_fire:
            module._send_completion_webhook(None, {"status": "success"})
    mock_fire.assert_not_called()


def test_send_completion_webhook_fires_with_secret():
    env = {"JOB_WEBHOOK_URL": "http://env/cb", "JOB_WEBHOOK_SECRET": "s3cr3t"}
    with patch.dict("os.environ", env, clear=True):
        with patch("worker._fire_webhook") as mock_fire:
            module._send_completion_webhook(None, {"status": "dead_letter"})
    mock_fire.assert_called_once_with("http://env/cb", {"status": "dead_letter"}, secret="s3cr3t")


# ---------------------------------------------------------------------------
# Dead-letter record
# ---------------------------------------------------------------------------

def test_build_dlq_record_fields():
    record = module.build_dlq_record(
        task_id="t-1",
        roast_id="r-1",
        error="ValueError: bad",
        error_type="ValueError",
        retries=0,
        classification="permanent",
        cost={"total_usd": 0.01},
    )
    assert record["task_id"] == "t-1"
    assert record["classification"] == "permanent"
    assert record["error_type"] == "ValueError"
    assert record["cost"] == {"total_usd": 0.01}
    assert "dead_lettered_at" in record


def test_persist_dlq_record_writes_json(tmp_path):
    import json

    record = module.build_dlq_record(
        task_id="t-2",
        roast_id=None,
        error="boom",
        error_type="RuntimeError",
        retries=3,
        classification="retries_exhausted",
        cost=None,
    )
    path = module._persist_dlq_record(record, dlq_dir=tmp_path / "dlq")
    assert path.exists()
    assert path.name == "t-2.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["classification"] == "retries_exhausted"
    assert loaded["retries"] == 3
