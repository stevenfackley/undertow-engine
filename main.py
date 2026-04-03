"""
Undertow Engine – FastAPI Application
--------------------------------------
Exposes POST /api/v1/generate which enqueues a video-generation job via Celery.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl

from worker import process_video_payload

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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz", tags=["health"])
def health_check() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/api/v1/generate", response_model=GenerateResponse, tags=["generate"])
def generate(payload: GenerateRequest) -> GenerateResponse:
    """
    Enqueue a video generation job.

    Accepts a JSON body with:
    - **text**: topic or raw idea for the AI scriptwriter.
    - **background_video_url**: publicly accessible URL of the gameplay video.
    - **caption**: optional post caption (hashtags can be included).
    - **platforms**: list of target platforms, e.g. ``["tiktok", "instagram"]``.
    """
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
