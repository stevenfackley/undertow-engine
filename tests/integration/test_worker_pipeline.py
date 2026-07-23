"""Integration tests for the full worker pipeline wiring.

Runs process_video_payload's real orchestration (_run_pipeline,
_handle_task_failure, webhook serialization/signing) with only the external
boundaries faked: the AI/audio/whisper/ffmpeg stages write real files into
tmp_path, the webhook POST is captured, and Supabase sync is recorded.

This is the test the repo was missing: it proves the seams between stages —
state transitions, DLQ capture, signed completion callbacks — actually fit
together, rather than each helper working in isolation.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import httpx
import pytest
from celery.exceptions import Retry

import worker as module
from app import webhooks

SECRET = "integration-secret"

# The raw task function: __wrapped__ is bound to the real task instance, so go
# through __func__ to inject a controllable fake ``self``.
task_body = module.process_video_payload.__wrapped__.__func__


class PipelineHarness:
    """Patch the pipeline's external boundaries and record what crossed them."""

    def __init__(self, tmp_path, script_error=None):
        self.tmp_path = tmp_path
        self.script_error = script_error
        self.webhook_posts: list[tuple[str, bytes, dict]] = []
        self.published: list[dict] = []
        self.failed: list[dict] = []
        self.uploads: list[str] = []

    def __enter__(self):
        self._stack = ExitStack()
        env = {
            "OUTPUT_DIR": str(self.tmp_path / "outputs"),
            "DLQ_DIR": str(self.tmp_path / "dlq"),
            "JOB_WEBHOOK_SECRET": SECRET,
            "JOB_RETRY_BACKOFF_BASE": "1",
        }
        self._stack.enter_context(patch.dict("os.environ", env))

        def fake_script(topic, cost=None):
            if self.script_error is not None:
                raise self.script_error
            return f"script about {topic}"

        def fake_audio(script, output_path, cost=None):
            output_path.write_bytes(b"mp3-bytes")

        def fake_compose(background_video_url, audio_path, word_timestamps, output_path):
            output_path.write_bytes(b"mp4-bytes")
            return output_path

        def fake_post(url, content=b"", headers=None, timeout=None):
            self.webhook_posts.append((url, content, headers or {}))
            return MagicMock(status_code=200)

        p = self._stack.enter_context
        p(patch("app.ai_scripting.generate_script", side_effect=fake_script))
        p(patch("app.audio_processing.process_script_to_audio", side_effect=fake_audio))
        p(patch("app.timestamp_extraction.extract_word_timestamps", return_value=[]))
        p(patch("app.video_compositing.compose_video", side_effect=fake_compose))
        p(
            patch(
                "app.automated_publishing.upload_to_tiktok",
                side_effect=lambda video_path, caption: self.uploads.append("tiktok"),
            )
        )
        p(
            patch(
                "app.automated_publishing.upload_to_instagram",
                side_effect=lambda video_path, caption: self.uploads.append("instagram"),
            )
        )
        p(
            patch(
                "app.sourcing.mark_published",
                side_effect=lambda **kw: self.published.append(kw),
            )
        )
        p(
            patch(
                "app.sourcing.mark_failed",
                side_effect=lambda **kw: self.failed.append(kw),
            )
        )
        p(patch("worker.httpx.post", side_effect=fake_post))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


def _run(task_self, **kwargs):
    defaults = {
        "background_video": "http://example.com/bg.mp4",
        "callback_url": "http://consumer.example.com/hook",
    }
    defaults.update(kwargs)
    return task_body(task_self, "a topic", **defaults)


def _verify_signature(body: bytes, headers: dict) -> None:
    assert headers[webhooks.SIGNATURE_HEADER] == webhooks.sign_payload(body, SECRET)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_success_end_to_end(task_self, tmp_path):
    with PipelineHarness(tmp_path) as harness:
        result = _run(task_self, platforms=["tiktok", "instagram"], roast_id="r-1")

    assert result["status"] == "complete"
    assert result["task_id"] == "task-under-test"
    assert "cost" in result

    # Both platforms published, Supabase synced, local MP4 cleaned up.
    assert harness.uploads == ["tiktok", "instagram"]
    assert harness.published[0]["roast_id"] == "r-1"
    assert not (tmp_path / "outputs" / "task-under-test.mp4").exists()

    # Exactly one signed completion webhook with a success payload.
    assert len(harness.webhook_posts) == 1
    url, body, headers = harness.webhook_posts[0]
    assert url == "http://consumer.example.com/hook"
    payload = json.loads(body)
    assert payload["event"] == "job.completed"
    assert payload["status"] == webhooks.STATUS_SUCCESS
    assert payload["output_path"].endswith("task-under-test.mp4")
    _verify_signature(body, headers)


def test_success_reports_progress_steps_in_order(task_self, tmp_path):
    with PipelineHarness(tmp_path):
        _run(task_self)

    steps = [c.kwargs["meta"]["step"] for c in task_self.update_state.call_args_list]
    assert steps == [
        "scripting",
        "audio_processing",
        "timestamp_extraction",
        "video_compositing",
        "publishing",
        "cleanup",
    ]


# ---------------------------------------------------------------------------
# Permanent failure → dead-letter
# ---------------------------------------------------------------------------


def test_permanent_failure_dead_letters_and_signs_webhook(task_self, tmp_path):
    error = ValueError("unusable input")
    with PipelineHarness(tmp_path, script_error=error) as harness:
        with pytest.raises(ValueError):
            _run(task_self, roast_id="r-2", caption="c")

    # DLQ record captured with the original inputs for re-driving.
    record = json.loads((tmp_path / "dlq" / "task-under-test.json").read_text(encoding="utf-8"))
    assert record["classification"] == "permanent"
    assert record["payload"]["text"] == "a topic"
    assert record["payload"]["background_video"] == "http://example.com/bg.mp4"

    # Supabase marked failed; nothing published or uploaded.
    assert harness.failed[0]["roast_id"] == "r-2"
    assert harness.published == []
    assert harness.uploads == []

    # Dead-letter webhook fired and signed.
    _, body, headers = harness.webhook_posts[0]
    payload = json.loads(body)
    assert payload["status"] == webhooks.STATUS_DEAD_LETTER
    assert "ValueError" in payload["error"]
    _verify_signature(body, headers)


# ---------------------------------------------------------------------------
# Transient failure → retry, no dead-letter, no webhook
# ---------------------------------------------------------------------------


def test_transient_failure_schedules_retry_without_side_effects(task_self, tmp_path):
    error = httpx.ConnectError("upstream flake")
    with PipelineHarness(tmp_path, script_error=error) as harness:
        with pytest.raises(Retry):
            _run(task_self, roast_id="r-3")

    task_self.retry.assert_called_once()
    assert not (tmp_path / "dlq").exists()
    assert harness.webhook_posts == []
    assert harness.failed == []
