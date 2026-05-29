"""
Undertow Engine – FastAPI Application
--------------------------------------
Exposes POST /api/v1/generate which enqueues a video-generation job via Celery.
"""

import ipaddress
import logging
import os
import socket
import uuid
from pathlib import Path
from urllib.parse import urlparse

import redis as _redis_lib
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, HttpUrl
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.logging_config import configure_logging
from worker import celery_app, process_video_payload

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("undertow.api")

# Startup banner — Qavren mascot signature. See content/brand/qav-mascot.md in the
# qavren-solutions-site repo for the voice rules. Banner is logged (never printed,
# per CLAUDE.md) so it appears in stdout under uvicorn but respects production
# logging discipline.
logger.info(
    "undertow_starting\n"
    "      ___\n"
    "    .'   `.___\n"
    "   / O      __>      qav is watching\n"
    "  |       _/         undertow-engine — built by Qavren\n"
    "   \\     /\n"
    "    `--'\n"
)

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Undertow Engine",
    description=(
        "Automated Hook Engine – handles video rendering and headless "
        "social media uploading."
    ),
    version="1.0.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "request_id": request_id,
            },
        )
        return response


app.add_middleware(RequestIDMiddleware)


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
        raise HTTPException(
            status_code=422,
            detail="background_video_url must not resolve to a private address",
        )


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    text: str
    background_video_url: HttpUrl
    caption: str = ""
    platforms: list[str] = ["tiktok"]
    callback_url: HttpUrl | None = None


class GenerateResponse(BaseModel):
    task_id: str
    status: str = "queued"


class JobStatusResponse(BaseModel):
    task_id: str
    status: str
    step: str | None = None
    output_path: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["health"])
def health_check() -> dict:
    """Liveness probe — returns immediately without touching external services."""
    return {"status": "ok", "qav": "watching"}


@app.get("/readyz", tags=["health"])
def readiness_check() -> dict:
    """Readiness probe — verifies Redis connectivity before accepting traffic."""
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        _redis_lib.from_url(url, socket_connect_timeout=2).ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Redis unreachable: {exc}") from exc
    return {"status": "ok"}


@app.post(
    "/api/v1/generate",
    response_model=GenerateResponse,
    tags=["generate"],
    dependencies=[Depends(_require_api_key)],
)
@limiter.limit("10/minute")
def generate(request: Request, payload: GenerateRequest = Body(...)) -> GenerateResponse:
    """
    Enqueue a video generation job.

    - **text**: topic or raw idea for the AI scriptwriter.
    - **background_video_url**: publicly accessible URL of the gameplay video.
    - **caption**: optional post caption (hashtags can be included).
    - **platforms**: list of target platforms, e.g. ``["tiktok", "instagram"]``.
    - **callback_url**: optional URL to POST a completion payload to when the job finishes.
    """
    _validate_video_url(str(payload.background_video_url))

    try:
        task = process_video_payload.delay(
            text=payload.text,
            background_video_url=str(payload.background_video_url),
            caption=payload.caption,
            platforms=payload.platforms,
            callback_url=str(payload.callback_url) if payload.callback_url else None,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not enqueue task") from exc

    logger.info(
        "task_enqueued",
        extra={
            "task_id": task.id,
            "platforms": payload.platforms,
            "request_id": getattr(request.state, "request_id", "-"),
        },
    )
    return GenerateResponse(task_id=task.id)


@app.get(
    "/api/v1/jobs/{task_id}",
    response_model=JobStatusResponse,
    tags=["jobs"],
    dependencies=[Depends(_require_api_key)],
)
@limiter.limit("60/minute")
def job_status(request: Request, task_id: str) -> JobStatusResponse:
    """
    Poll the status of a video generation job.

    Returns one of: ``pending``, ``started``, ``progress``, ``complete``, ``failed``.
    """
    result = celery_app.AsyncResult(task_id)
    state = result.state.lower()

    step: str | None = None
    output_path: str | None = None
    error: str | None = None

    if state == "progress":
        meta = result.info or {}
        step = meta.get("step")
    elif state == "success":
        state = "complete"
        info = result.result or {}
        output_path = info.get("output_path")
    elif state == "failure":
        state = "failed"
        exc = result.result
        error = str(exc) if exc else "Unknown error"

    return JobStatusResponse(
        task_id=task_id,
        status=state,
        step=step,
        output_path=output_path,
        error=error,
    )


@app.delete(
    "/api/v1/jobs/{task_id}",
    status_code=202,
    tags=["jobs"],
    dependencies=[Depends(_require_api_key)],
)
@limiter.limit("20/minute")
def revoke_job(request: Request, task_id: str) -> dict:
    """
    Revoke a pending or running job.

    Sends SIGTERM to the worker executing the task. Also removes any partial
    output file so a subsequent re-submission doesn't skip the render step.
    """
    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    # Remove partial MP4 so a future re-submit doesn't treat it as a completed render
    output_dir = os.environ.get("OUTPUT_DIR", "/data/outputs")
    partial = Path(output_dir) / f"{task_id}.mp4"
    if partial.exists():
        partial.unlink()
        logger.info("partial_output_removed", extra={"task_id": task_id})

    logger.info(
        "task_revoked",
        extra={
            "task_id": task_id,
            "request_id": getattr(request.state, "request_id", "-"),
        },
    )
    return {"task_id": task_id, "status": "revoked"}
