"""Integration tests for the FastAPI application."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from main import app
    return TestClient(app), None


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------

def test_healthz(client):
    tc, _ = client
    resp = tc.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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


def test_job_status_failed(client):
    tc, _ = client
    mock_result = MagicMock()
    mock_result.state = "FAILURE"
    mock_result.info = {}

    with patch("main.celery_app") as mock_ca:
        mock_ca.AsyncResult.return_value = mock_result
        resp = tc.get("/api/v1/jobs/abc-123")

    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
