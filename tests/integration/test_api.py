"""Integration tests for the FastAPI application."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=False), None


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

def test_healthz(client):
    tc, _ = client
    resp = tc.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /readyz
# ---------------------------------------------------------------------------

def test_readyz_ok(client):
    tc, _ = client
    mock_conn = MagicMock()
    with patch("main._redis_lib") as mock_redis:
        mock_redis.from_url.return_value = mock_conn
        resp = tc.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_conn.ping.assert_called_once()


def test_readyz_redis_down(client):
    tc, _ = client
    mock_conn = MagicMock()
    mock_conn.ping.side_effect = Exception("Connection refused")
    with patch("main._redis_lib") as mock_redis:
        mock_redis.from_url.return_value = mock_conn
        resp = tc.get("/readyz")
    assert resp.status_code == 503
    assert "Redis unreachable" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/v1/generate
# ---------------------------------------------------------------------------

def test_generate_enqueues_task(client):
    tc, _ = client
    mock_task = MagicMock()
    mock_task.id = "abc-123"

    with patch("main.process_video_payload") as mock_fn:
        mock_fn.delay.return_value = mock_task
        resp = tc.post("/api/v1/generate", json={
            "text": "gaming tips",
            "background_video_url": "http://example.com/bg.mp4",
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "abc-123"
    assert body["status"] == "queued"


def test_generate_forwards_callback_url(client):
    tc, _ = client
    mock_task = MagicMock()
    mock_task.id = "cb-456"

    with patch("main.process_video_payload") as mock_fn:
        mock_fn.delay.return_value = mock_task
        tc.post("/api/v1/generate", json={
            "text": "topic",
            "background_video_url": "http://example.com/bg.mp4",
            "callback_url": "http://example.com/webhook",
        })

    call_kwargs = mock_fn.delay.call_args.kwargs
    assert call_kwargs["callback_url"] == "http://example.com/webhook"


def test_generate_null_callback_url_when_omitted(client):
    tc, _ = client
    mock_task = MagicMock()
    mock_task.id = "no-cb"

    with patch("main.process_video_payload") as mock_fn:
        mock_fn.delay.return_value = mock_task
        tc.post("/api/v1/generate", json={
            "text": "topic",
            "background_video_url": "http://example.com/bg.mp4",
        })

    call_kwargs = mock_fn.delay.call_args.kwargs
    assert call_kwargs["callback_url"] is None


def test_generate_ssrf_blocked(client):
    tc, _ = client
    resp = tc.post("/api/v1/generate", json={
        "text": "topic",
        "background_video_url": "http://127.0.0.1/bg.mp4",
    })
    assert resp.status_code == 422
    assert "private" in resp.json()["detail"]


def test_generate_503_on_celery_error(client):
    tc, _ = client
    with patch("main.process_video_payload") as mock_fn:
        mock_fn.delay.side_effect = Exception("broker down")
        resp = tc.post("/api/v1/generate", json={
            "text": "topic",
            "background_video_url": "http://example.com/bg.mp4",
        })
    assert resp.status_code == 503


def test_generate_validation_error_missing_text(client):
    tc, _ = client
    resp = tc.post("/api/v1/generate", json={
        "background_video_url": "http://example.com/bg.mp4",
    })
    assert resp.status_code == 422


def test_generate_validation_error_bad_url(client):
    tc, _ = client
    resp = tc.post("/api/v1/generate", json={
        "text": "topic",
        "background_video_url": "not-a-url",
    })
    assert resp.status_code == 422


def test_generate_api_key_required_when_configured(client):
    tc, _ = client
    with patch.dict("os.environ", {"API_KEY": "secret"}):
        resp = tc.post("/api/v1/generate", json={
            "text": "topic",
            "background_video_url": "http://example.com/bg.mp4",
        })
    assert resp.status_code == 401


def test_generate_api_key_accepted(client):
    tc, _ = client
    mock_task = MagicMock()
    mock_task.id = "key-ok"

    with patch.dict("os.environ", {"API_KEY": "secret"}):
        with patch("main.process_video_payload") as mock_fn:
            mock_fn.delay.return_value = mock_task
            resp = tc.post(
                "/api/v1/generate",
                json={"text": "t", "background_video_url": "http://example.com/bg.mp4"},
                headers={"X-API-Key": "secret"},
            )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{task_id}
# ---------------------------------------------------------------------------

def test_job_status_progress(client):
    tc, _ = client
    mock_result = MagicMock()
    mock_result.state = "PROGRESS"
    mock_result.info = {"step": "audio_processing"}

    with patch("main.celery_app") as mock_ca:
        mock_ca.AsyncResult.return_value = mock_result
        resp = tc.get("/api/v1/jobs/abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "progress"
    assert body["step"] == "audio_processing"
    assert body["task_id"] == "abc-123"


def test_job_status_complete(client):
    tc, _ = client
    mock_result = MagicMock()
    mock_result.state = "SUCCESS"
    mock_result.result = {"output_path": "/data/outputs/abc-123.mp4"}

    with patch("main.celery_app") as mock_ca:
        mock_ca.AsyncResult.return_value = mock_result
        resp = tc.get("/api/v1/jobs/abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["output_path"] == "/data/outputs/abc-123.mp4"


def test_job_status_failed_includes_error(client):
    tc, _ = client
    mock_result = MagicMock()
    mock_result.state = "FAILURE"
    mock_result.result = ValueError("OpenAI rate limit exceeded")

    with patch("main.celery_app") as mock_ca:
        mock_ca.AsyncResult.return_value = mock_result
        resp = tc.get("/api/v1/jobs/abc-123")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "OpenAI rate limit exceeded" in body["error"]


def test_job_status_failed_no_result(client):
    tc, _ = client
    mock_result = MagicMock()
    mock_result.state = "FAILURE"
    mock_result.result = None

    with patch("main.celery_app") as mock_ca:
        mock_ca.AsyncResult.return_value = mock_result
        resp = tc.get("/api/v1/jobs/abc-123")

    assert resp.json()["error"] == "Unknown error"
