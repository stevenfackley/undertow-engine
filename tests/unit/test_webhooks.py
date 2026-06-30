"""Unit tests for app.webhooks payload builder + HMAC signing."""

from __future__ import annotations

import hashlib
import hmac

from app.webhooks import (
    SIGNATURE_HEADER,
    STATUS_DEAD_LETTER,
    STATUS_SUCCESS,
    build_headers,
    build_webhook_payload,
    serialize_payload,
    sign_payload,
)


def test_build_payload_success_minimal():
    payload = build_webhook_payload("task-1", STATUS_SUCCESS, output_path="/data/outputs/x.mp4")
    assert payload == {
        "event": "job.completed",
        "task_id": "task-1",
        "status": "success",
        "output_path": "/data/outputs/x.mp4",
    }


def test_build_payload_omits_none_fields():
    payload = build_webhook_payload("task-2", STATUS_DEAD_LETTER)
    # roast_id / output_path / error / cost omitted, not set to null
    assert "roast_id" not in payload
    assert "output_path" not in payload
    assert "error" not in payload
    assert "cost" not in payload


def test_build_payload_dead_letter_with_error_and_cost():
    payload = build_webhook_payload(
        "task-3",
        STATUS_DEAD_LETTER,
        roast_id="r-9",
        error="ValueError: bad",
        cost={"total_usd": 0.02},
    )
    assert payload["status"] == "dead_letter"
    assert payload["error"] == "ValueError: bad"
    assert payload["roast_id"] == "r-9"
    assert payload["cost"] == {"total_usd": 0.02}


def test_serialize_is_deterministic_sorted():
    payload = {"b": 2, "a": 1}
    assert serialize_payload(payload) == b'{"a":1,"b":2}'


def test_sign_payload_matches_hmac():
    body = b'{"a":1}'
    secret = "shh"
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert sign_payload(body, secret) == expected


def test_build_headers_unsigned_without_secret():
    headers = build_headers(b"{}", None)
    assert headers == {"Content-Type": "application/json"}
    assert SIGNATURE_HEADER not in headers


def test_build_headers_signed_with_secret():
    body = b'{"x":1}'
    headers = build_headers(body, "key")
    assert headers["Content-Type"] == "application/json"
    assert headers[SIGNATURE_HEADER] == sign_payload(body, "key")
