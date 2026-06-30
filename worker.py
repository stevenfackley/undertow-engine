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

import json
import logging
import os
import random
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from celery import Celery
from celery.exceptions import Retry
from celery.schedules import crontab

from app import webhooks
from app.cost_tracking import JobCostAccumulator
from app.logging_config import configure_logging
from app.reliability import compute_backoff, is_transient_error

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
# Reliability configuration (env-overridable, conservative defaults)
# ---------------------------------------------------------------------------

# Read once for the task decorator. Retry budget for transient failures.
_MAX_RETRIES = int(os.environ.get("JOB_MAX_RETRIES", "3"))


def _backoff_base() -> float:
    return float(os.environ.get("JOB_RETRY_BACKOFF_BASE", "30"))


def _backoff_max() -> float:
    return float(os.environ.get("JOB_RETRY_BACKOFF_MAX", "300"))


def _dlq_dir() -> Path:
    """Directory where dead-lettered job records are captured as JSON."""
    return Path(os.environ.get("DLQ_DIR", "/data/dlq"))


def _webhook_secret() -> str | None:
    return os.environ.get("JOB_WEBHOOK_SECRET") or None


def _resolve_callback_url(per_request_url: str | None) -> str | None:
    """Per-request URL wins; otherwise fall back to the env default; else None."""
    return per_request_url or os.environ.get("JOB_WEBHOOK_URL") or None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fire_webhook(callback_url: str, payload: dict, secret: str | None = None) -> None:
    """POST *payload* to *callback_url* (HMAC-signed when *secret* is set).

    The body is serialised deterministically so the signature matches the bytes
    on the wire. Any failure is swallowed — a dead webhook endpoint must never
    change job state.
    """
    body = webhooks.serialize_payload(payload)
    headers = webhooks.build_headers(body, secret)
    try:
        httpx.post(callback_url, content=body, headers=headers, timeout=10.0)
        logger.info(
            "webhook_fired",
            extra={"callback_url": callback_url, "status": payload.get("status")},
        )
    except Exception as exc:
        logger.warning("webhook_failed", extra={"callback_url": callback_url, "error": str(exc)})


def _send_completion_webhook(per_request_url: str | None, payload: dict) -> None:
    """Resolve the callback URL and fire it; no-op cleanly when unset."""
    url = _resolve_callback_url(per_request_url)
    if not url:
        return
    _fire_webhook(url, payload, secret=_webhook_secret())


def build_dlq_record(
    task_id: str,
    roast_id: str | None,
    error: str,
    error_type: str,
    retries: int,
    classification: str,
    cost: dict | None,
    payload: dict | None = None,
) -> dict:
    """Build the dead-letter record (pure). ``classification`` is one of
    ``permanent`` / ``retries_exhausted``.

    ``payload`` carries the original task inputs so an API-direct job (which has
    no ``roast_id``) can be re-driven from the record.
    """
    return {
        "task_id": task_id,
        "roast_id": roast_id,
        "error": error,
        "error_type": error_type,
        "retries": retries,
        "classification": classification,
        "cost": cost,
        "payload": payload,
        "dead_lettered_at": datetime.now(timezone.utc).isoformat(),
    }


def _persist_dlq_record(record: dict, dlq_dir: Path | None = None) -> Path:
    """Write *record* to ``{DLQ_DIR}/{task_id}.json`` and return the path."""
    target_dir = dlq_dir if dlq_dir is not None else _dlq_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{record['task_id']}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


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


def _run_pipeline(
    self,
    cost: JobCostAccumulator,
    *,
    text: str,
    bg_source: str,
    caption: str,
    platforms: list[str],
    roast_id: str | None,
    task_id: str,
    output_video_path: Path,
    log: logging.LoggerAdapter,
) -> dict:
    """Execute the Synthesis → Compositing → Deployment → Cleanup pipeline.

    Threads *cost* through the OpenAI calls so the per-job spend can be reported.
    Raises on any step failure — the caller classifies and retries/dead-letters.
    """
    # ------------------------------------------------------------------
    # Synthesis (skip if already rendered on a prior retry attempt)
    # ------------------------------------------------------------------
    if output_video_path.exists():
        log.info("render_skipped_output_exists")
        self.update_state(state="PROGRESS", meta={"step": "publishing"})
    else:
        log.info("synthesis_started", extra={"topic": text, "platforms": platforms})
        self.update_state(state="PROGRESS", meta={"step": "scripting"})

        from app.ai_scripting import generate_script

        script = generate_script(topic=text, cost=cost)
        log.info("script_generated", extra={"chars": len(script)})

        self.update_state(state="PROGRESS", meta={"step": "audio_processing"})

        from app.audio_processing import process_script_to_audio

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            audio_path = tmpdir_path / "processed_audio.mp3"
            process_script_to_audio(script=script, output_path=audio_path, cost=cost)
            log.info("audio_ready")

            self.update_state(state="PROGRESS", meta={"step": "timestamp_extraction"})

            from app.timestamp_extraction import extract_word_timestamps

            word_timestamps = extract_word_timestamps(audio_path=audio_path, cost=cost)
            log.info("timestamps_extracted", extra={"word_count": len(word_timestamps)})

            self.update_state(state="PROGRESS", meta={"step": "video_compositing"})

            # --------------------------------------------------------------
            # Compositing — resolve background (local path or URL)
            # --------------------------------------------------------------
            from app.video_compositing import compose_video

            compose_video(
                background_video_url=bg_source,
                audio_path=audio_path,
                word_timestamps=word_timestamps,
                output_path=output_video_path,
            )
            log.info("compositing_complete", extra={"output": str(output_video_path)})

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
        log.info("platform_published", extra={"platform": platform})

    # ------------------------------------------------------------------
    # Cleanup — delete local MP4, sync state to Supabase
    # ------------------------------------------------------------------
    self.update_state(state="PROGRESS", meta={"step": "cleanup"})

    try:
        output_video_path.unlink()
        log.info("local_mp4_deleted")
    except FileNotFoundError:
        pass

    if roast_id:
        from app.sourcing import mark_published

        mark_published(roast_id=roast_id, output_path=str(output_video_path))
        log.info("supabase_state_synced", extra={"status": "published"})

    cost_summary = cost.as_dict()
    log.info("job_cost", extra={"cost": cost_summary})

    result = {
        "task_id": task_id,
        "roast_id": roast_id,
        "status": "complete",
        "output_path": str(output_video_path),
        "cost": cost_summary,
    }

    log.info("pipeline_complete")
    return result


def _handle_task_failure(
    self,
    exc: Exception,
    *,
    task_id: str,
    roast_id: str | None,
    cost: JobCostAccumulator,
    callback_url: str | None,
    log: logging.LoggerAdapter,
    payload: dict | None = None,
) -> None:
    """Classify *exc* and either retry (transient, budget remaining) or
    dead-letter (permanent, or retries exhausted). Always raises.

    Transient → ``self.retry`` with exponential backoff. Permanent/exhausted →
    persist a DLQ record, sync Supabase to ``failed``, fire the completion
    webhook, then re-raise so Celery records the task as ``FAILURE``.
    """
    error_type = type(exc).__name__
    retries = self.request.retries or 0
    max_retries = self.max_retries or 0
    transient = is_transient_error(exc)

    if transient and retries < max_retries:
        countdown = compute_backoff(retries, base=_backoff_base(), cap=_backoff_max())
        # Jitter to avoid a thundering herd when many jobs fail together.
        countdown += random.uniform(0, min(5.0, countdown * 0.1))
        log.warning(
            "job_retry_scheduled",
            extra={
                "error": str(exc),
                "error_type": error_type,
                "retry": retries + 1,
                "max_retries": max_retries,
                "countdown": round(countdown, 2),
            },
        )
        raise self.retry(exc=exc, countdown=countdown, max_retries=max_retries)

    classification = "permanent" if not transient else "retries_exhausted"
    error_str = f"{error_type}: {exc}"

    record = build_dlq_record(
        task_id=task_id,
        roast_id=roast_id,
        error=error_str,
        error_type=error_type,
        retries=retries,
        classification=classification,
        cost=cost.as_dict(),
        payload=payload,
    )
    try:
        dlq_path = _persist_dlq_record(record)
    except Exception as persist_exc:  # never let DLQ-write failure mask the error
        dlq_path = None
        log.error("dlq_persist_failed", extra={"error": str(persist_exc)})

    log.error(
        "job_dead_lettered",
        extra={
            "error": error_str,
            "error_type": error_type,
            "classification": classification,
            "retries": retries,
            "dlq_path": str(dlq_path) if dlq_path else None,
        },
    )

    if roast_id:
        try:
            from app.sourcing import mark_failed

            mark_failed(roast_id=roast_id, error=error_str)
            log.info("supabase_state_synced", extra={"status": "failed"})
        except Exception as sync_exc:
            log.error("dlq_supabase_sync_failed", extra={"error": str(sync_exc)})

    payload = webhooks.build_webhook_payload(
        task_id=task_id,
        status=webhooks.STATUS_DEAD_LETTER,
        roast_id=roast_id,
        error=error_str,
        cost=cost.as_dict(),
    )
    _send_completion_webhook(callback_url, payload)

    # Re-raise so Celery records FAILURE (no autoretry is configured).
    raise exc


@celery_app.task(
    bind=True,
    name="undertow_engine.process_video_payload",
    max_retries=_MAX_RETRIES,
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

    Transient failures (network/API 429/5xx) are retried with bounded
    exponential backoff; permanent failures (malformed input) and exhausted
    retries are dead-lettered to ``DLQ_DIR`` and reported via the completion
    webhook. The terminal completion webhook fires on both success and
    dead-letter.
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
    cost = JobCostAccumulator()

    # Captured into the DLQ record so a dead-lettered job can be re-driven even
    # when it has no roast_id (a direct API call).
    input_payload = {
        "text": text,
        "background_video": bg_source,
        "caption": caption,
        "platforms": platforms,
        "callback_url": callback_url,
    }

    try:
        result = _run_pipeline(
            self,
            cost,
            text=text,
            bg_source=bg_source,
            caption=caption,
            platforms=platforms,
            roast_id=roast_id,
            task_id=task_id,
            output_video_path=output_video_path,
            log=_log,
        )
    except Retry:
        raise  # a self.retry() from inner code — let Celery handle it
    except Exception as exc:
        _handle_task_failure(
            self,
            exc,
            task_id=task_id,
            roast_id=roast_id,
            cost=cost,
            callback_url=callback_url,
            log=_log,
            payload=input_payload,
        )

    # Success — fire the terminal completion webhook (best-effort).
    payload = webhooks.build_webhook_payload(
        task_id=task_id,
        status=webhooks.STATUS_SUCCESS,
        roast_id=roast_id,
        output_path=result["output_path"],
        cost=result.get("cost"),
    )
    _send_completion_webhook(callback_url, payload)

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
