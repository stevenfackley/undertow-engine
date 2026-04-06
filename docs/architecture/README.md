# Architecture

## System Overview

Undertow Engine is a stateless Python microservice composed of four runtime components:

| Component | Technology | Role |
|-----------|-----------|------|
| **api** | FastAPI + Uvicorn | Accepts HTTP requests, enqueues jobs, exposes status endpoint |
| **worker** | Celery | Executes the video pipeline asynchronously |
| **redis** | Redis 7 | Message broker and Celery result backend |
| *(Chromium)* | Playwright | Embedded in the worker; used for headless social upload |

All three services are built from the same Docker image (`undertow-engine:latest`) and wired together via Docker Compose.

```
┌──────────────────────────────────────────────────────────┐
│  Docker Compose Network                                  │
│                                                          │
│  ┌─────────────┐  POST /generate   ┌─────────────────┐  │
│  │   Client    │ ────────────────▶ │   api           │  │
│  │  (external) │ ◀──────────────── │   :8000         │  │
│  └─────────────┘  {task_id}        └────────┬────────┘  │
│                                             │ LPUSH      │
│                                    ┌────────▼────────┐  │
│                                    │   redis         │  │
│                                    │   :6379         │  │
│                                    └────────┬────────┘  │
│                                             │ BRPOP      │
│                                    ┌────────▼────────┐  │
│                                    │   worker        │  │
│                                    │   (Celery)      │  │
│                                    └────────┬────────┘  │
│                                             │            │
│                          ┌──────────────────┤            │
│                          │                  │            │
│                 ┌────────▼──────┐  ┌────────▼────────┐  │
│                 │  OpenAI APIs  │  │  Playwright     │  │
│                 │  (external)   │  │  (Chromium)     │  │
│                 └───────────────┘  └─────────────────┘  │
│                                                          │
│  Volumes:                                                │
│    chromium_profile ──▶ /data/chromium-profile           │
│    outputs          ──▶ /data/outputs                    │
└──────────────────────────────────────────────────────────┘
```

---

## Pipeline Sequence

```
Client                  api               Redis             Worker
  │                      │                  │                 │
  │ POST /generate        │                  │                 │
  │─────────────────────▶│                  │                 │
  │                      │ process_video_   │                 │
  │                      │ payload.delay()  │                 │
  │                      │─────────────────▶│                 │
  │  {task_id, queued}   │                  │  BRPOP          │
  │◀─────────────────────│                  │────────────────▶│
  │                      │                  │                 │ generate_script()
  │                      │                  │  PROGRESS       │ ──▶ OpenAI GPT-4o
  │                      │                  │◀────────────────│
  │                      │                  │                 │ process_script_to_audio()
  │                      │                  │  PROGRESS       │ ──▶ OpenAI TTS
  │                      │                  │◀────────────────│
  │                      │                  │                 │ extract_word_timestamps()
  │                      │                  │  PROGRESS       │ ──▶ OpenAI Whisper
  │                      │                  │◀────────────────│
  │                      │                  │                 │ compose_video()
  │                      │                  │  PROGRESS       │ ──▶ MoviePy/ffmpeg
  │                      │                  │◀────────────────│
  │                      │                  │                 │ upload_to_tiktok/instagram()
  │                      │                  │  SUCCESS        │ ──▶ Playwright/Chromium
  │                      │                  │◀────────────────│
  │ GET /jobs/{task_id}  │                  │                 │
  │─────────────────────▶│ AsyncResult()    │                 │
  │                      │─────────────────▶│                 │
  │  {status: complete}  │                  │                 │
  │◀─────────────────────│                  │                 │
```

---

## ADRs

### ADR-001 — Use Celery + Redis for async job execution

**Status:** Accepted

**Context:** Video rendering takes 30–120 seconds. Blocking an HTTP request for that duration is unacceptable. The API must return a job ID immediately and let the caller poll for completion.

**Decision:** Use Celery with Redis as both broker and result backend. The FastAPI process enqueues tasks via `.delay()`; a separate worker container consumes them. Redis stores task state so `AsyncResult(task_id)` works across processes.

**Consequences:**
- Adds a Redis dependency.
- Job state is only as durable as Redis (data loss on crash without persistence enabled).
- Enables horizontal worker scaling by adding more worker containers.

---

### ADR-002 — Single Docker image for api and worker

**Status:** Accepted

**Context:** The API and worker share all Python dependencies (FastAPI, Celery, MoviePy, Playwright, OpenAI SDK). Maintaining two Dockerfiles would duplicate the dependency installation and increase image divergence risk.

**Decision:** Build one image (`undertow-engine:latest`) and override the `command` per service in `docker-compose.yml`.

**Consequences:**
- Image is larger than strictly necessary for the API-only service (includes MoviePy, Playwright, ffmpeg).
- Simpler CI: one build step, one push.
- Both services always stay in sync on dependency versions.

---

### ADR-003 — Use OpenAI Whisper API for word timestamps

**Status:** Accepted

**Context:** Kinetic subtitle rendering requires word-level start/end timestamps. Options: local Whisper model, WhisperX, Gentle forced aligner, or OpenAI Whisper API.

**Decision:** Use the OpenAI Whisper API (`whisper-1`) with `timestamp_granularities: ["word"]`. Timestamps are at `response.words` (top-level), not nested under segments.

**Consequences:**
- Zero infrastructure for transcription (no GPU, no extra service).
- Per-call cost; latency depends on OpenAI API response time.
- Word-level accuracy is good but not frame-perfect — acceptable for subtitle overlay use cases.

---

### ADR-004 — Playwright persistent Chromium context for social upload

**Status:** Accepted

**Context:** TikTok and Instagram do not offer public upload APIs. Options: unofficial third-party libraries, mobile app emulation, or headless browser automation.

**Decision:** Use Playwright with a persistent Chromium profile directory (Docker volume). The operator logs in manually once; subsequent uploads reuse the saved session cookies.

**Consequences:**
- Sessions expire periodically; the operator must re-authenticate manually.
- UI selectors are fragile — platform UI changes will break uploads silently.
- All selectors are isolated to module-level constants in `automated_publishing.py` for easy patching.
- Headless Chromium adds ~200 MB to the image.

---

### ADR-005 — Store rendered MP4s on a named Docker volume

**Status:** Accepted

**Context:** The Celery worker renders MP4s inside a container. Files written to the container filesystem are lost on restart. Both the API (to serve download links) and the worker need access.

**Decision:** Mount a named volume `outputs` at `/data/outputs` in both `api` and `worker` services. Output path is configurable via `$OUTPUT_DIR`.

**Consequences:**
- Files accumulate indefinitely — a cleanup job will be needed before production.
- Both services see the same files without additional object storage.
- Not suitable for multi-host deployments without switching to S3 or similar.
