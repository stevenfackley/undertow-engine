"""
Undertow Engine – FastAPI Application
--------------------------------------
Exposes POST /api/v1/generate which enqueues a video-generation job via Celery.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, HttpUrl

from worker import celery_app, process_video_payload

# ---------------------------------------------------------------------------
# API key auth
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    expected = os.environ.get("API_KEY")
    if not expected:
        return  # API_KEY not set → auth disabled (local dev)
    if api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

_PRIVATE_NETS = [
    ipaddress.ip_network(cidr)
    for cidr in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "127.0.0.0/8", "169.254.0.0/16", "::1/128", "fc00::/7",
    )
]


def _validate_video_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=422, detail="background_video_url must use http or https")
    host = parsed.hostname or ""
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
    except (socket.gaierror, ValueError):
        raise HTTPException(status_code=422, detail=f"Could not resolve host: {host!r}")
    if any(addr in net for net in _PRIVATE_NETS):
        raise HTTPException(status_code=422, detail="background_video_url must not resolve to a private address")

app = FastAPI(
    title="Undertow Engine",
    description=(
        "Automated Hook Engine – handles video rendering and headless "
        "social media uploading."
    ),
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    text: str
    background_video_url: HttpUrl
    caption: str = ""
    platforms: list[str] = ["tiktok"]


class GenerateResponse(BaseModel):
    task_id: str
    status: str = "queued"


class JobStatusResponse(BaseModel):
    task_id: str
    status: str
    step: str | None = None
    output_path: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["health"])
def health_check() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.post(
    "/api/v1/generate",
    response_model=GenerateResponse,
    tags=["generate"],
    dependencies=[Depends(_require_api_key)],
)
def generate(payload: GenerateRequest) -> GenerateResponse:
    """
    Enqueue a video generation job.

    Accepts a JSON body with:
    - **text**: topic or raw idea for the AI scriptwriter.
    - **background_video_url**: publicly accessible URL of the gameplay video.
    - **caption**: optional post caption (hashtags can be included).
    - **platforms**: list of target platforms, e.g. ``["tiktok", "instagram"]``.
    """
    _validate_video_url(str(payload.background_video_url))

    try:
        task = process_video_payload.delay(
            text=payload.text,
            background_video_url=str(payload.background_video_url),
            caption=payload.caption,
            platforms=payload.platforms,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not enqueue task") from exc

    return GenerateResponse(task_id=task.id)


@app.get(
    "/api/v1/jobs/{task_id}",
    response_model=JobStatusResponse,
    tags=["jobs"],
    dependencies=[Depends(_require_api_key)],
)
def job_status(task_id: str) -> JobStatusResponse:
    """
    Poll the status of a video generation job.

    Returns one of: ``queued``, ``started``, ``progress``, ``complete``, ``failed``.
    """
    result = celery_app.AsyncResult(task_id)
    state = result.state.lower()

    step: str | None = None
    output_path: str | None = None

    if state == "progress":
        meta = result.info or {}
        step = meta.get("step")
    elif state == "success":
        state = "complete"
        info = result.result or {}
        output_path = info.get("output_path")
    elif state == "failure":
        state = "failed"

    return JobStatusResponse(
        task_id=task_id,
        status=state,
        step=step,
        output_path=output_path,
    )
