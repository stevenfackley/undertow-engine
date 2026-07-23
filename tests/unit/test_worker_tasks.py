"""Unit tests for worker task orchestration: sourcing, background resolution,
retry/dead-letter classification, and the main task's failure wiring.

These target the paths that decide whether a job retries, dead-letters, or
completes — the reliability core that previously had no direct coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry

import worker as module
from app.cost_tracking import JobCostAccumulator

# The raw task function: __wrapped__ is bound to the real task instance, so go
# through __func__ to inject a controllable fake ``self``.
task_body = module.process_video_payload.__wrapped__.__func__

# ---------------------------------------------------------------------------
# source_and_enqueue
# ---------------------------------------------------------------------------


def _rows(*ids: str) -> list[dict]:
    return [
        {
            "id": i,
            "content": f"content-{i}",
            "caption": "cap",
            "platforms": ["tiktok", "instagram"],
            "background_video": "http://example.com/bg.mp4",
        }
        for i in ids
    ]


def test_source_and_enqueue_marks_and_enqueues_each_row():
    with patch("app.sourcing.fetch_pending", return_value=_rows("r1", "r2")):
        with patch("app.sourcing.mark_processing") as mock_mark:
            with patch.object(module.process_video_payload, "delay") as mock_delay:
                result = module.source_and_enqueue()

    assert result == {"enqueued": 2}
    assert mock_mark.call_count == 2
    kwargs = mock_delay.call_args_list[0].kwargs
    assert kwargs["text"] == "content-r1"
    assert kwargs["background_video"] == "http://example.com/bg.mp4"
    assert kwargs["caption"] == "cap"
    assert kwargs["platforms"] == ["tiktok", "instagram"]
    assert kwargs["roast_id"] == "r1"


def test_source_and_enqueue_defaults_platforms_and_background():
    row = {"id": "r1", "content": "c", "platforms": None, "background_video": None}
    with patch("app.sourcing.fetch_pending", return_value=[row]):
        with patch("app.sourcing.mark_processing"):
            with patch.object(module.process_video_payload, "delay") as mock_delay:
                module.source_and_enqueue()

    kwargs = mock_delay.call_args.kwargs
    assert kwargs["platforms"] == ["tiktok"]
    assert kwargs["background_video"] == ""


def test_source_and_enqueue_idle_when_no_pending():
    with patch("app.sourcing.fetch_pending", return_value=[]):
        with patch.object(module.process_video_payload, "delay") as mock_delay:
            result = module.source_and_enqueue()

    assert result == {"enqueued": 0}
    mock_delay.assert_not_called()


def test_source_and_enqueue_survives_single_row_failure():
    """One poisoned row must not stop the rest of the batch."""
    with patch("app.sourcing.fetch_pending", return_value=_rows("bad", "good")):
        with patch("app.sourcing.mark_processing", side_effect=[RuntimeError("db"), None]):
            with patch.object(module.process_video_payload, "delay") as mock_delay:
                result = module.source_and_enqueue()

    assert result == {"enqueued": 1}
    assert mock_delay.call_count == 1
    assert mock_delay.call_args.kwargs["roast_id"] == "good"


# ---------------------------------------------------------------------------
# _handle_task_failure — retry vs dead-letter classification
# ---------------------------------------------------------------------------


def _failure_env(tmp_path, **extra):
    env = {
        "DLQ_DIR": str(tmp_path / "dlq"),
        "JOB_RETRY_BACKOFF_BASE": "1",
        "JOB_RETRY_BACKOFF_MAX": "300",
    }
    env.update(extra)
    return patch.dict("os.environ", env)


def _call_failure(task_self, exc, tmp_path, **overrides):
    kwargs = {
        "task_id": "task-under-test",
        "roast_id": None,
        "cost": JobCostAccumulator(),
        "callback_url": None,
        "log": MagicMock(),
        "payload": {"text": "t"},
    }
    kwargs.update(overrides)
    return module._handle_task_failure(task_self, exc, **kwargs)


def test_transient_error_with_budget_schedules_retry(task_self, tmp_path):
    exc = ConnectionError("network blip")
    with _failure_env(tmp_path):
        with pytest.raises(Retry):
            _call_failure(task_self, exc, tmp_path)

    retry_kwargs = task_self.retry.call_args.kwargs
    assert retry_kwargs["exc"] is exc
    assert retry_kwargs["max_retries"] == 3
    # base backoff 1s plus bounded jitter (<= 10% of countdown, capped at 5s)
    assert 1.0 <= retry_kwargs["countdown"] <= 1.1
    # No dead-letter record on a scheduled retry.
    assert not (tmp_path / "dlq").exists()


def test_permanent_error_dead_letters_immediately(task_self, tmp_path):
    exc = ValueError("malformed input")
    with _failure_env(tmp_path):
        with patch.object(module, "_send_completion_webhook") as mock_send:
            with pytest.raises(ValueError) as exc_info:
                _call_failure(task_self, exc, tmp_path)

    assert exc_info.value is exc
    task_self.retry.assert_not_called()

    record = json.loads((tmp_path / "dlq" / "task-under-test.json").read_text())
    assert record["classification"] == "permanent"
    assert record["error_type"] == "ValueError"
    assert record["payload"] == {"text": "t"}

    webhook_payload = mock_send.call_args.args[1]
    assert webhook_payload["status"] == "dead_letter"
    assert webhook_payload["task_id"] == "task-under-test"


def test_exhausted_retries_dead_letter_as_retries_exhausted(task_self, tmp_path):
    task_self.request.retries = 3  # equals max_retries → budget spent
    exc = ConnectionError("still down")
    with _failure_env(tmp_path):
        with patch.object(module, "_send_completion_webhook"):
            with pytest.raises(ConnectionError):
                _call_failure(task_self, exc, tmp_path)

    record = json.loads((tmp_path / "dlq" / "task-under-test.json").read_text())
    assert record["classification"] == "retries_exhausted"
    assert record["retries"] == 3


def test_dead_letter_syncs_supabase_when_roast_id_present(task_self, tmp_path):
    with _failure_env(tmp_path):
        with patch("app.sourcing.mark_failed") as mock_failed:
            with patch.object(module, "_send_completion_webhook"):
                with pytest.raises(ValueError):
                    _call_failure(task_self, ValueError("bad"), tmp_path, roast_id="r-9")

    roast_kwargs = mock_failed.call_args.kwargs
    assert roast_kwargs["roast_id"] == "r-9"
    assert "ValueError" in roast_kwargs["error"]


def test_dlq_write_failure_never_masks_original_error(task_self, tmp_path):
    original = ValueError("the real problem")
    with _failure_env(tmp_path):
        with patch.object(module, "_persist_dlq_record", side_effect=OSError("disk full")):
            with patch.object(module, "_send_completion_webhook"):
                with pytest.raises(ValueError) as exc_info:
                    _call_failure(task_self, original, tmp_path)

    assert exc_info.value is original


def test_supabase_sync_failure_never_masks_original_error(task_self, tmp_path):
    original = ValueError("the real problem")
    with _failure_env(tmp_path):
        with patch("app.sourcing.mark_failed", side_effect=RuntimeError("db down")):
            with patch.object(module, "_send_completion_webhook"):
                with pytest.raises(ValueError) as exc_info:
                    _call_failure(task_self, original, tmp_path, roast_id="r-1")

    assert exc_info.value is original


# ---------------------------------------------------------------------------
# process_video_payload — orchestration wiring
# ---------------------------------------------------------------------------


def test_task_skips_render_when_output_exists(task_self, tmp_path):
    """A retry after the render step must not re-synthesise (and re-spend)."""
    output = tmp_path / "task-under-test.mp4"
    output.write_bytes(b"already rendered")

    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        with patch("app.automated_publishing.upload_to_tiktok") as mock_tiktok:
            with patch("app.ai_scripting.generate_script") as mock_script:
                with patch.object(module, "_send_completion_webhook") as mock_send:
                    result = task_body(
                        task_self, "topic", background_video="http://example.com/bg.mp4"
                    )

    assert result["status"] == "complete"
    mock_script.assert_not_called()
    mock_tiktok.assert_called_once()
    assert not output.exists()  # cleanup ran

    webhook_payload = mock_send.call_args.args[1]
    assert webhook_payload["status"] == "success"
    assert webhook_payload["output_path"] == str(output)


def test_task_failure_routes_through_failure_handler_with_input_payload(task_self, tmp_path):
    boom = ValueError("scripting failed")
    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        with patch.object(module, "_run_pipeline", side_effect=boom):
            with patch.object(module, "_handle_task_failure", side_effect=boom) as mock_handle:
                with pytest.raises(ValueError):
                    task_body(
                        task_self,
                        "topic",
                        background_video="http://example.com/bg.mp4",
                        caption="cap",
                        platforms=["instagram"],
                        callback_url="http://cb.example.com/",
                    )

    handle_kwargs = mock_handle.call_args.kwargs
    assert handle_kwargs["payload"] == {
        "text": "topic",
        "background_video": "http://example.com/bg.mp4",
        "caption": "cap",
        "platforms": ["instagram"],
        "callback_url": "http://cb.example.com/",
    }
    assert handle_kwargs["callback_url"] == "http://cb.example.com/"


def test_task_lets_celery_retry_exceptions_propagate(task_self, tmp_path):
    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        with patch.object(module, "_run_pipeline", side_effect=Retry("scheduled")):
            with patch.object(module, "_handle_task_failure") as mock_handle:
                with pytest.raises(Retry):
                    task_body(task_self, "topic", background_video="http://example.com/bg.mp4")

    mock_handle.assert_not_called()


def test_task_legacy_background_video_url_still_accepted(task_self, tmp_path):
    """API-triggered calls use the legacy background_video_url kwarg."""
    output = tmp_path / "task-under-test.mp4"
    output.write_bytes(b"rendered")

    with patch.dict("os.environ", {"OUTPUT_DIR": str(tmp_path)}):
        with patch.object(module, "_send_completion_webhook"):
            with patch.object(module, "_run_pipeline") as mock_pipeline:
                mock_pipeline.return_value = {"status": "complete", "output_path": str(output)}
                task_body(task_self, "topic", background_video_url="http://example.com/legacy.mp4")

    assert mock_pipeline.call_args.kwargs["bg_source"] == "http://example.com/legacy.mp4"
