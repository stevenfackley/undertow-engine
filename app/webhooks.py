"""
Completion webhook helpers
--------------------------
Pure builders for the terminal-state callback that the worker POSTs when a job
reaches a final state — either ``success`` or ``dead_letter``.

* :func:`build_webhook_payload` — assembles the JSON-serialisable status body.
* :func:`serialize_payload` — deterministic JSON encoding (sorted keys, compact
  separators) so the bytes on the wire match what the signature is computed
  over.
* :func:`sign_payload` — HMAC-SHA256 over the exact request body.

Auth convention: when ``JOB_WEBHOOK_SECRET`` is set, the worker sends
``X-Undertow-Signature: sha256=<hexdigest>`` computed with
:func:`sign_payload`. Receivers recompute the HMAC over the raw body to verify
authenticity (GitHub-webhook style). With no secret configured the callback is
sent unsigned — documented, opt-in.

No IO here; the actual ``httpx.post`` lives in the worker so failures can be
swallowed without affecting job state.
"""

from __future__ import annotations

import hashlib
import hmac
import json

# Terminal statuses carried in the payload.
STATUS_SUCCESS = "success"
STATUS_DEAD_LETTER = "dead_letter"

SIGNATURE_HEADER = "X-Undertow-Signature"


def build_webhook_payload(
    task_id: str,
    status: str,
    roast_id: str | None = None,
    output_path: str | None = None,
    error: str | None = None,
    cost: dict | None = None,
) -> dict:
    """Build the completion-callback body.

    ``status`` is one of :data:`STATUS_SUCCESS` / :data:`STATUS_DEAD_LETTER`.
    Optional fields are omitted (rather than set to ``null``) so a signed body
    is deterministic and minimal.
    """
    payload: dict = {
        "event": "job.completed",
        "task_id": task_id,
        "status": status,
    }
    if roast_id is not None:
        payload["roast_id"] = roast_id
    if output_path is not None:
        payload["output_path"] = output_path
    if error is not None:
        payload["error"] = error
    if cost is not None:
        payload["cost"] = cost
    return payload


def serialize_payload(payload: dict) -> bytes:
    """Deterministic JSON encoding used for both transport and signing."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign_payload(body: bytes, secret: str) -> str:
    """Return ``sha256=<hexdigest>`` HMAC of *body* keyed by *secret*."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def build_headers(body: bytes, secret: str | None) -> dict[str, str]:
    """Headers for the webhook POST, adding the signature when *secret* is set."""
    headers = {"Content-Type": "application/json"}
    if secret:
        headers[SIGNATURE_HEADER] = sign_payload(body, secret)
    return headers
