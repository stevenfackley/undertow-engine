"""
Undertow Engine – Celery Worker
---------------------------------
Configures the Celery application and defines tasks that orchestrate the
full AI → Audio → Whisper → Video → Upload pipeline.

State machine per the STD:
  Idle → Sourcing → Synthesis → Compositing → Deployment → Cleanup → Idle

Broker URL selection:
  CELERY_ENV=test  →  REDIS_TEST_URL  (redis://localhost:6379/1)
  default          →  REDIS_URL       (redis://redis:6379/0)
"""

from __future__ import annotations

import logging
import os
import shutil
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
    result_expires=86400,  # purge task results from Redis after 24 hours
    beat_schedule={
        # Poll Supabase for pending roasts every 5 minutes
        "source-pending-roasts": {
            "task": "undertow_engine.source_and_enqueue",
            "schedule": crontab(minute="*/5"),
        },
        # Daily output cleanup at 03:00 UTC
        "cleanup-old-outputs": {
            "task": "undertow_engine.cleanup_old_outputs",
            "schedule": crontab(hour=3, minute=0),
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


def _resolve_background(source: str, dest: Path) -> Path:
    """
    Handle both local file paths and remote URLs for the background video.
    Local paths (not starting with http) are copied to *dest*.
    Remote URLs are streamed with httpx.
    """
    if source.startswith(("http://", "https://")):
        from app.video_compositing import _download_video
        return _download_video(source, dest)

    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"Background video not found at local path: {source}")
    shutil.copy2(src, dest)
    return dest


# ---------------------------------------------------------------------------
# Sourcing task — runs on schedule, polls Supabase, enqueues work
# ---------------------------------------------------------------------------


@celery_app.task(name="undertow_engine.source_and_enqueue")
def source_and_enqueue() -> dict:
    """
    State: Idle → Sourcing.
    Queries Supabase for pending roast records and enqueues a
    process_video_payload task for each one.
    Immediately marks each record as 'processing' to prevent double-pickup.
    """
    from app.sourcing import fetch_pending, mark_processing

    pending = fetch_pending(limit=5)

    if not pending:
        logger.info("sourcing_idle", extra={"pending": 0})
        return {"enqueued": 0}

    enqueued = 0
    for row in pending:
        try:
            mark_processing(row["id"])
            process_video_payload.delay(
                text=row["content"],
                background_video=row.get("background_video") or "",
                caption=row.get("caption", ""),
                platforms=list(row.get("platforms") or ["tiktok"]),
                roast_id=row["id"],
            )
            enqueued += 1
            logger.info("roast_enqueued", extra={"roast_id": row["id"]})
        except Exception as exc:
            logger.error("enqueue_failed", extra={"roast_id": row["id"], "error": str(exc)})

    return {"enqueued": enqueued}


# ---------------------------------------------------------------------------
# Main pipeline task
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
    soft_time_limit=600,
    time_limit=660,
)
def process_video_payload(
    self,
    text: str,
    background_video: str = "",
    caption: str = "",
    platforms: list[str] | None = None,
    roast_id: str | None = None,
    callback_url: str | None = None,
    # Legacy param — kept for API-triggered calls
    background_video_url: str = "",
) -> dict:
    """
    Orchestrate the full pipeline per the state machine:

    Sourcing → Synthesis → Compositing → Deployment → Cleanup

    Accepts either:
    - background_video: local path or URL (Supabase-sourced flow)
    - background_video_url: URL only (direct API call, legacy)
    """
    if platforms is None:
        platforms = ["tiktok"]

    # Normalise: background_video takes precedence over legacy background_video_url
    bg_source = background_video or background_video_url

    task_id = self.request.id or "local"
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/data/outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    output_video_path = output_dir / f"{task_id}.mp4"

    _log = logging.LoggerAdapter(logger, {"task_id": task_id, "roast_id": roast_id})

    # ------------------------------------------------------------------
    # Synthesis (skip if already rendered on a prior retry attempt)
    # ------------------------------------------------------------------
    if output_video_path.exists():
        _log.info("render_skipped_output_exists")
        self.update_state(state="PROGRESS", meta={"step": "publishing"})
    else:
        _log.info("synthesis_started", extra={"topic": text, "platforms": platforms})
        self.update_state(state="PROGRESS", meta={"step": "scripting"})

        from app.ai_scripting import generate_script
        script = generate_script(topic=text)
        _log.info("script_generated", extra={"chars": len(script)})

        self.update_state(state="PROGRESS", meta={"step": "audio_processing"})

        from app.audio_processing import process_script_to_audio

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            audio_path = tmpdir_path / "processed_audio.mp3"
            process_script_to_audio(script=script, output_path=audio_path)
            _log.info("audio_ready")

            self.update_state(state="PROGRESS", meta={"step": "timestamp_extraction"})

            from app.timestamp_extraction import extract_word_timestamps
            word_timestamps = extract_word_timestamps(audio_path=audio_path)
            _log.info("timestamps_extracted", extra={"word_count": len(word_timestamps)})

            self.update_state(state="PROGRESS", meta={"step": "video_compositing"})

            # ------------------------------------------------------------------
            # Compositing — resolve background (local path or URL)
            # ------------------------------------------------------------------
            from app.video_compositing import compose_video

            compose_video(
                background_video_url=bg_source,
                audio_path=audio_path,
                word_timestamps=word_timestamps,
                output_path=output_video_path,
            )
            _log.info("compositing_complete", extra={"output": str(output_video_path)})

        self.update_state(state="PROGRESS", meta={"step": "publishing"})

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------
    from app.automated_publishing import upload_to_instagram, upload_to_tiktok

    for platform in platforms:
        if platform == "tiktok":
            upload_to_tiktok(video_path=output_video_path, caption=caption)
        elif platform == "instagram":
            upload_to_instagram(video_path=output_video_path, caption=caption)
        _log.info("platform_published", extra={"platform": platform})

    # ------------------------------------------------------------------
    # Cleanup — delete local MP4, sync state to Supabase
    # ------------------------------------------------------------------
    self.update_state(state="PROGRESS", meta={"step": "cleanup"})

    try:
        output_video_path.unlink()
        _log.info("local_mp4_deleted")
    except FileNotFoundError:
        pass

    if roast_id:
        from app.sourcing import mark_published
        mark_published(roast_id=roast_id, output_path=str(output_video_path))
        _log.info("supabase_state_synced", extra={"status": "published"})

    result = {
        "task_id": task_id,
        "roast_id": roast_id,
        "status": "complete",
        "output_path": str(output_video_path),
    }

    _log.info("pipeline_complete")

    if callback_url:
        _fire_webhook(callback_url, result)

    return result


# ---------------------------------------------------------------------------
# Periodic cleanup task
# ---------------------------------------------------------------------------


@celery_app.task(name="undertow_engine.cleanup_old_outputs")
def cleanup_old_outputs(max_age_days: int = 7) -> dict:
    """
    Safety net: delete any MP4s that weren't cleaned up by the pipeline
    (e.g. tasks that failed permanently before reaching the Cleanup step).
    """
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
