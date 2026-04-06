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

import os
import tempfile
import time
from pathlib import Path

from celery import Celery
from celery.schedules import crontab

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
)
def process_video_payload(
    self,
    text: str,
    background_video_url: str,
    caption: str = "",
    platforms: list[str] | None = None,
) -> dict:
    """
    Orchestrate the full Undertow Engine pipeline:

    1. Generate a script with GPT-4o.
    2. Synthesise speech via OpenAI TTS and strip silence with pydub.
    3. Extract word-by-word timestamps with Whisper.
    4. Compose the final MP4 with MoviePy kinetic text overlays.
    5. Upload to each requested platform via Playwright.

    Parameters
    ----------
    text:
        Topic or raw idea passed to the AI scriptwriter.
    background_video_url:
        Public URL of the background gameplay video.
    caption:
        Post caption (hashtags can be included).
    platforms:
        List of target platforms.  Supported values: ``"tiktok"``,
        ``"instagram"``.  Defaults to ``["tiktok"]``.

    Returns
    -------
    dict
        A result dict with keys ``task_id``, ``status``, and ``output_path``.
    """
    if platforms is None:
        platforms = ["tiktok"]

    self.update_state(state="PROGRESS", meta={"step": "scripting"})

    # Step 1 – AI Script Generation
    from app.ai_scripting import generate_script

    script = generate_script(topic=text)

    self.update_state(state="PROGRESS", meta={"step": "audio_processing"})

    # Step 2 – TTS + Silence Stripping
    from app.audio_processing import process_script_to_audio

    # Persistent output directory so rendered files survive after the task.
    # Mount /data/outputs as a Docker volume in production.
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/data/outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = self.request.id or "local"
    output_video_path = output_dir / f"{task_id}.mp4"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        audio_path = tmpdir_path / "processed_audio.mp3"
        process_script_to_audio(script=script, output_path=audio_path)

        self.update_state(state="PROGRESS", meta={"step": "timestamp_extraction"})

        # Step 3 – Whisper Word Timestamps
        from app.timestamp_extraction import extract_word_timestamps

        word_timestamps = extract_word_timestamps(audio_path=audio_path)

        self.update_state(state="PROGRESS", meta={"step": "video_compositing"})

        # Step 4 – Video Compositing (write directly to persistent output path)
        from app.video_compositing import compose_video

        compose_video(
            background_video_url=background_video_url,
            audio_path=audio_path,
            word_timestamps=word_timestamps,
            output_path=output_video_path,
        )

    # Temporary directory is now cleaned up; output_video_path persists.

    self.update_state(state="PROGRESS", meta={"step": "publishing"})

    # Step 5 – Automated Publishing
    from app.automated_publishing import upload_to_instagram, upload_to_tiktok

    for platform in platforms:
        if platform == "tiktok":
            upload_to_tiktok(video_path=output_video_path, caption=caption)
        elif platform == "instagram":
            upload_to_instagram(video_path=output_video_path, caption=caption)

    return {
        "task_id": task_id,
        "status": "complete",
        "output_path": str(output_video_path),
    }


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
    return {"deleted": deleted, "max_age_days": max_age_days}
