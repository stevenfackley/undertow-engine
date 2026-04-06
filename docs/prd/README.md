# PRD — Undertow Engine

## Overview

Undertow Engine automates the full lifecycle of short-form video content: from a raw topic idea to a published post on TikTok and Instagram Reels. It eliminates the manual work of scripting, voiceover recording, subtitle editing, and platform uploading by running the entire pipeline as a single API call.

**Problem:** Creating high-performing short-form video at scale requires hours of manual effort per clip — writing hooks, recording VO, burning subtitles, and uploading. This bottleneck caps output volume and responsiveness to trends.

**Solution:** A headless microservice that ingests a topic and a background video URL, generates a viral script via GPT-4o, synthesises speech, extracts word-level timestamps via Whisper, composites kinetic subtitles with MoviePy, and publishes via Playwright — all asynchronously via Celery.

---

## Goals & Non-Goals

### Goals
- Accept a topic + background video URL over HTTP and return a job ID immediately.
- Complete the full script → audio → subtitle → video → publish pipeline without human intervention.
- Support TikTok and Instagram Reels as publishing targets.
- Expose a status endpoint so callers can poll job progress.
- Run reliably inside Docker with a single `docker compose up`.

### Non-Goals
- Video editing UI or dashboard (headless API only).
- Support for YouTube Shorts, X, or other platforms in v1.
- Scheduling posts for a future time.
- Analytics or performance tracking of published posts.
- Multi-tenant isolation or per-user billing.

---

## User Stories

| # | As a… | I want to… | So that… |
|---|-------|-----------|----------|
| 1 | content operator | POST a topic and background video URL | a finished video is rendered and posted automatically |
| 2 | content operator | poll a job ID for status | I know when the video is live without watching logs |
| 3 | developer | run the full stack locally with one command | I can test end-to-end without cloud dependencies |
| 4 | developer | run the test suite in CI | regressions are caught before deploy |
| 5 | content operator | target TikTok, Instagram, or both in one request | I don't have to submit separate jobs per platform |

---

## Requirements

### Functional

| ID | Requirement |
|----|-------------|
| F-01 | `POST /api/v1/generate` accepts `text`, `background_video_url`, `caption`, and `platforms`. Returns `task_id` and `status: queued`. |
| F-02 | `GET /api/v1/jobs/{task_id}` returns `status` (queued / started / progress / complete / failed), current `step`, and `output_path` when complete. |
| F-03 | Script generation uses GPT-4o with the viral hook → body → loop-back structure. |
| F-04 | TTS uses OpenAI `tts-1` (voice: `onyx`). Silence gaps > 200 ms are stripped via pydub. |
| F-05 | Word timestamps are extracted via Whisper `whisper-1` with `timestamp_granularities: ["word"]`. |
| F-06 | Each word is rendered as a `TextClip` (Montserrat Black, 70 pt). Negative words render red; action words render yellow; others white. |
| F-07 | The background video is cropped to the audio duration; the final output is H.264/AAC MP4 at 30 fps. |
| F-08 | `GET /healthz` returns `{"status": "ok"}` for liveness probes. |
| F-09 | Rendered MP4 files persist to `/data/outputs` (Docker volume); the Chromium profile persists to `/data/chromium-profile`. |

### Non-Functional

| ID | Requirement |
|----|-------------|
| NF-01 | API response to `POST /api/v1/generate` < 500 ms (enqueue only; rendering is async). |
| NF-02 | Worker runs as a non-root user inside the container. |
| NF-03 | No secrets are baked into the image; all credentials are injected via environment variables. |
| NF-04 | The test suite runs without a live Redis, OpenAI, or social platform connection (all external calls are mocked). |

---

## Acceptance Criteria

- [ ] `POST /api/v1/generate` with a valid body returns HTTP 200 and a non-empty `task_id`.
- [ ] `GET /api/v1/jobs/{task_id}` returns `status: complete` and a valid `output_path` after the pipeline finishes.
- [ ] A rendered MP4 is present at `output_path` on the shared Docker volume.
- [ ] The post appears on the configured TikTok / Instagram account after the task completes.
- [ ] `docker compose -f docker-compose.yml -f docker-compose.test.yml up --abort-on-container-exit` exits 0.
- [ ] `GET /healthz` returns HTTP 200.

---

## Open Questions

| # | Question | Owner | Status |
|---|----------|-------|--------|
| 1 | What is the maximum acceptable end-to-end latency for a 60-second video? | — | Open |
| 2 | Should failed jobs be retried automatically? How many times? | — | Open |
| 3 | How should the system handle a background video shorter than the generated audio? | — | Open |
| 4 | Is a random clip start offset needed to avoid duplicate-looking content across runs? | — | Open |
| 5 | Do we need a webhook callback when a job completes instead of (or in addition to) polling? | — | Open |
