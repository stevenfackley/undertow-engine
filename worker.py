"""
Undertow Engine – Celery Worker
---------------------------------
Configures the Celery application and defines the ``process_video_payload``
task that orchestrates the full AI → Audio → Whisper → Video → Upload pipeline.

Broker URL selection:
  - If the environment variable ``CELERY_ENV`` is set to ``"test"``,
    ``REDIS_TEST_URL`` is used (defaults to ``redis://localhost:6379/1``).
  - Otherwise ``REDIS_URL`` is used (defaults to ``redis://redis:6379/0``).
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

import httpx
from celery import Celery
from celery.schedules import crontab

from app.logging_config import configure_logging

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("undertow.worker")

# ---------------------------------------------------------------------------
# Broker / backend configuration
# ---------------------------------------------------------------------------

_env = os.environ.get("CELERY_ENV", "prod")

if _env == "test":
    _broker_url = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/1")
    _backend_url = os.environ.get("REDIS_TEST_URL", "redis://localhost:6379/1")
else:
    _broker_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    _backend_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "undertow_engine",
    broker=_broker_url,
    backend=_backend_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    beat_schedule={
        "cleanup-old-outputs": {
            "task": "undertow_engine.cleanup_old_outputs",
            "schedule": crontab(hour=3, minute=0),  # daily at 03:00 UTC
        },
    },
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fire_webhook(callback_url: str, payload: dict) -> None:
    """POST *payload* to *callback_url*, silently ignoring any failure."""
    try:
        httpx.post(callback_url, json=payload, timeout=10.0)
        logger.info("webhook_fired", extra={"callback_url": callback_url})
    except Exception as exc:
        logger.warning("webhook_failed", extra={"callback_url": callback_url, "error": str(exc)})


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="undertow_engine.process_video_payload",
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    soft_time_limit=600,   # 10 min — raises SoftTimeLimitExceeded (graceful)
    time_limit=660,        # 11 min — hard SIGKILL
)
def process_video_payload(
    self,
    text: str,
    background_video_url: str,
    caption: str = "",
    platforms: list[str] | None = None,
    callback_url: str | None = None,
) -> dict:
    """
    Orchestrate the full Undertow Engine pipeline:

    1. Generate a script with GPT-4o.
    2. Synthesise speech via OpenAI TTS and strip silence with pydub.
    3. Extract word-by-word timestamps with Whisper.
    4. Compose the final MP4 with MoviePy kinetic text overlays.
    5. Upload to each requested platform via Playwright.
    6. POST to *callback_url* if provided.

    Render steps (1–4) are skipped on retry if the output MP4 already exists,
    preventing redundant API calls and re-renders.
    """
    if platforms is None:
        platforms = ["tiktok"]

    task_id = self.request.id or "local"
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/data/outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video_path = output_dir / f"{task_id}.mp4"

    log = logger.bind if hasattr(logger, "bind") else lambda **kw: logger  # structlog compat
    _log = logging.LoggerAdapter(logger, {"task_id": task_id})

    if output_video_path.exists():
        _log.info("render_skipped_output_exists")
        self.update_state(state="PROGRESS", meta={"step": "publishing"})
    else:
        _log.info("pipeline_started", extra={"topic": text, "platforms": platforms})
        self.update_state(state="PROGRESS", meta={"step": "scripting"})

        # Step 1 – AI Script Generation
        from app.ai_scripting import generate_script
        script = generate_script(topic=text)
        _log.info("script_generated", extra={"chars": len(script)})

        self.update_state(state="PROGRESS", meta={"step": "audio_processing"})

        # Step 2 – TTS + Silence Stripping
        from app.audio_processing import process_script_to_audio

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "processed_audio.mp3"
            process_script_to_audio(script=script, output_path=audio_path)
            _log.info("audio_ready")

            self.update_state(state="PROGRESS", meta={"step": "timestamp_extraction"})

            # Step 3 – Whisper Word Timestamps
            from app.timestamp_extraction import extract_word_timestamps
            word_timestamps = extract_word_timestamps(audio_path=audio_path)
            _log.info("timestamps_extracted", extra={"word_count": len(word_timestamps)})

            self.update_state(state="PROGRESS", meta={"step": "video_compositing"})

            # Step 4 – Video Compositing
            from app.video_compositing import compose_video
            compose_video(
                background_video_url=background_video_url,
                audio_path=audio_path,
                word_timestamps=word_timestamps,
                output_path=output_video_path,
            )
            _log.info("video_composed", extra={"output": str(output_video_path)})

        self.update_state(state="PROGRESS", meta={"step": "publishing"})

    # Step 5 – Automated Publishing
    from app.automated_publishing import upload_to_instagram, upload_to_tiktok

    for platform in platforms:
        if platform == "tiktok":
            upload_to_tiktok(video_path=output_video_path, caption=caption)
        elif platform == "instagram":
            upload_to_instagram(video_path=output_video_path, caption=caption)
        _log.info("platform_published", extra={"platform": platform})

    result = {
        "task_id": task_id,
        "status": "complete",
        "output_path": str(output_video_path),
    }

    _log.info("pipeline_complete", extra={"output": str(output_video_path)})

    # Step 6 – Webhook notification
    if callback_url:
        _fire_webhook(callback_url, result)

    return result


@celery_app.task(name="undertow_engine.cleanup_old_outputs")
def cleanup_old_outputs(max_age_days: int = 7) -> dict:
    """Delete rendered MP4s older than *max_age_days* from the output directory."""
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/data/outputs"))
    if not output_dir.exists():
        return {"deleted": 0}
    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    for f in output_dir.glob("*.mp4"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            deleted += 1
    logger.info("cleanup_complete", extra={"deleted": deleted, "max_age_days": max_age_days})
    return {"deleted": deleted, "max_age_days": max_age_days}
