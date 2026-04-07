"""
Roast & Resolve → Undertow Engine integration example.

Drop this into the Roast FastAPI codebase.
Uses the internal Docker DNS name so traffic never leaves the server.

Required env vars in Roast's docker-compose.prod.yml:
    UNDERTOW_URL=http://undertow-api:8001
    UNDERTOW_API_KEY=<same value as Undertow's API_KEY>
"""

from __future__ import annotations

import os

import httpx

UNDERTOW_URL = os.environ.get("UNDERTOW_URL", "http://undertow-api:8001")
UNDERTOW_API_KEY = os.environ["UNDERTOW_API_KEY"]


async def trigger_undertow(
    topic: str,
    caption: str = "",
    platforms: list[str] | None = None,
    callback_url: str | None = None,
) -> str:
    """
    Enqueue a video generation job on Undertow Engine.
    Returns the task_id for polling.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{UNDERTOW_URL}/api/v1/generate",
            json={
                "text": topic,
                "background_video_url": "https://cdn.roastandresolve.com/backgrounds/gameplay.mp4",
                "caption": caption,
                "platforms": platforms or ["tiktok"],
                "callback_url": callback_url,
            },
            headers={"X-API-Key": UNDERTOW_API_KEY},
        )
        response.raise_for_status()
        return response.json()["task_id"]


async def get_undertow_status(task_id: str) -> dict:
    """Poll job status from Undertow."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{UNDERTOW_URL}/api/v1/jobs/{task_id}",
            headers={"X-API-Key": UNDERTOW_API_KEY},
        )
        response.raise_for_status()
        return response.json()
