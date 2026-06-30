"""Unit tests for app.reliability error classification + backoff."""

from __future__ import annotations

import httpx
import pytest

from app.reliability import (
    PermanentJobError,
    compute_backoff,
    is_transient_error,
)


class _FakeApiError(Exception):
    """Stand-in for an SDK error that carries an HTTP status code (e.g. the
    OpenAI ``APIStatusError`` family)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://example.com")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError("err", request=req, response=resp)


# ---------------------------------------------------------------------------
# Permanent classifications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        PermanentJobError("bad payload"),
        ValueError("malformed"),
        TypeError("nope"),
        KeyError("missing"),
        FileNotFoundError("no such file"),
        AttributeError("none has no attr"),
    ],
)
def test_permanent_errors_are_not_transient(exc):
    assert is_transient_error(exc) is False


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_http_4xx_except_429_is_permanent(code):
    assert is_transient_error(_http_status_error(code)) is False
    assert is_transient_error(_FakeApiError(code)) is False


# ---------------------------------------------------------------------------
# Transient classifications
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_http_429_and_5xx_are_transient(code):
    assert is_transient_error(_http_status_error(code)) is True
    assert is_transient_error(_FakeApiError(code)) is True


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("conn refused"),
        httpx.ReadTimeout("timed out"),
        ConnectionError("reset"),
        TimeoutError("slow"),
    ],
)
def test_connection_errors_are_transient(exc):
    assert is_transient_error(exc) is True


def test_unknown_error_defaults_to_transient():
    # Bounded by max_retries at the call site, so it still ends up in the DLQ.
    class WeirdError(Exception):
        pass

    assert is_transient_error(WeirdError("???")) is True


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_compute_backoff_exponential():
    assert compute_backoff(0, base=30, cap=300) == 30
    assert compute_backoff(1, base=30, cap=300) == 60
    assert compute_backoff(2, base=30, cap=300) == 120


def test_compute_backoff_caps():
    assert compute_backoff(10, base=30, cap=300) == 300


def test_compute_backoff_negative_retries_floored():
    assert compute_backoff(-5, base=30, cap=300) == 30
