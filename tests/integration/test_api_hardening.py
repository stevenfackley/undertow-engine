"""Integration tests for API cross-cutting concerns: rate limiting, request-ID
middleware, auth coverage on the jobs endpoints, and the static console.

Complements test_api.py, which covers the endpoint bodies themselves.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app, limiter


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """The limiter is module-level state — reset between tests so counts from
    one test (or from test_api.py earlier in the session) never bleed in."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _generate_payload():
    return {"text": "topic", "background_video_url": "http://example.com/bg.mp4"}


# ---------------------------------------------------------------------------
# Request-ID middleware
# ---------------------------------------------------------------------------


def test_request_id_echoed_when_provided(client):
    resp = client.get("/healthz", headers={"X-Request-ID": "req-42"})
    assert resp.headers["X-Request-ID"] == "req-42"


def test_request_id_generated_when_absent(client):
    resp = client.get("/healthz")
    generated = resp.headers["X-Request-ID"]
    uuid.UUID(generated)  # raises if not a valid UUID


# ---------------------------------------------------------------------------
# API-key auth on the jobs endpoints (generate is covered in test_api.py)
# ---------------------------------------------------------------------------


def test_job_status_requires_api_key_when_configured(client):
    with patch.dict("os.environ", {"API_KEY": "secret"}):
        resp = client.get("/api/v1/jobs/abc-123")
    assert resp.status_code == 401


def test_job_status_accepts_valid_api_key(client):
    mock_result = MagicMock()
    mock_result.state = "PENDING"
    with patch.dict("os.environ", {"API_KEY": "secret"}):
        with patch("main.celery_app") as mock_ca:
            mock_ca.AsyncResult.return_value = mock_result
            resp = client.get("/api/v1/jobs/abc-123", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_revoke_requires_api_key_when_configured(client):
    with patch.dict("os.environ", {"API_KEY": "secret"}):
        resp = client.delete("/api/v1/jobs/abc-123")
    assert resp.status_code == 401


def test_wrong_api_key_rejected(client):
    with patch.dict("os.environ", {"API_KEY": "secret"}):
        resp = client.get("/api/v1/jobs/abc-123", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_generate_rate_limited_after_ten_per_minute(client):
    mock_task = MagicMock()
    mock_task.id = "rl-1"
    with patch("main.process_video_payload") as mock_fn:
        mock_fn.delay.return_value = mock_task
        codes = [
            client.post("/api/v1/generate", json=_generate_payload()).status_code for _ in range(11)
        ]

    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_job_status_rate_limit_higher_than_generate(client):
    """Polling is limited at 60/minute — the 11th poll must NOT be throttled."""
    mock_result = MagicMock()
    mock_result.state = "PENDING"
    with patch("main.celery_app") as mock_ca:
        mock_ca.AsyncResult.return_value = mock_result
        codes = [client.get("/api/v1/jobs/abc").status_code for _ in range(11)]

    assert codes == [200] * 11


# ---------------------------------------------------------------------------
# Static console
# ---------------------------------------------------------------------------


def test_console_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
