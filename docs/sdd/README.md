# SDD — Undertow Engine

## Overview

Undertow Engine is a Python microservice that orchestrates a five-stage pipeline — AI scripting → TTS + silence stripping → Whisper word timestamps → MoviePy kinetic video compositing → Playwright social upload — triggered by a single HTTP POST and executed asynchronously via Celery.

---

## Background

Short-form video platforms (TikTok, Instagram Reels) reward high posting frequency and strong opening hooks. Existing tools either require manual editing or produce generic output. Undertow Engine automates the full loop using:

- **OpenAI GPT-4o** for hook-first scriptwriting.
- **OpenAI TTS** (`tts-1`) for natural-sounding narration.
- **OpenAI Whisper** (`whisper-1`) for word-level timestamp extraction.
- **MoviePy** for subtitle compositing over gameplay footage.
- **Playwright** for headless browser-based social media upload.

---

## Design

### Data Flow

```
Client
  │
  ▼
POST /api/v1/generate
  │  (returns task_id immediately)
  ▼
Celery Queue (Redis)
  │
  ▼
Worker: process_video_payload
  ├─ 1. generate_script(topic)          → script: str
  ├─ 2. process_script_to_audio(script) → audio.mp3
  ├─ 3. extract_word_timestamps(audio)  → [WordTimestamp]
  ├─ 4. compose_video(bg_url, audio, timestamps) → {task_id}.mp4
  └─ 5. upload_to_tiktok / upload_to_instagram
```

### Module Breakdown

#### `main.py` — FastAPI Application

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Liveness probe |
| `/api/v1/generate` | POST | Enqueue a video generation job |
| `/api/v1/jobs/{task_id}` | GET | Poll job status |

Request schema (`GenerateRequest`):
```json
{
  "text": "string",
  "background_video_url": "https://...",
  "caption": "optional string",
  "platforms": ["tiktok", "instagram"]
}
```

Response schema (`GenerateResponse`):
```json
{ "task_id": "uuid", "status": "queued" }
```

Job status schema (`JobStatusResponse`):
```json
{
  "task_id": "uuid",
  "status": "queued | started | progress | complete | failed",
  "step": "scripting | audio_processing | timestamp_extraction | video_compositing | publishing | null",
  "output_path": "/data/outputs/{task_id}.mp4 | null"
}
```

---

#### `worker.py` — Celery Application

- Broker and backend: Redis (DB 0 in prod, DB 1 in test).
- Single task: `undertow_engine.process_video_payload`.
- Uses `task_track_started=True` so the `STARTED` state is written to Redis on pickup.
- Updates state to `PROGRESS` with a `step` key at each pipeline stage.
- Output MP4 is written to `$OUTPUT_DIR/{task_id}.mp4` (persists after `TempDirectory` cleanup).

---

#### `app/ai_scripting.py`

- Model: `gpt-4o`, temperature 0.9, max 300 tokens.
- System prompt enforces three-part structure: 3-second hook → rapid-fire body → loop-back ending.
- Lazy-initialised `OpenAI` client cached in a module-level variable.

---

#### `app/audio_processing.py`

- TTS: `tts-1`, voice `onyx`, response format `mp3`.
- Silence stripping: `pydub.split_on_silence` with 200 ms minimum silence, −40 dBFS threshold, 50 ms kept at edges.
- Output: MP3 written to a caller-supplied path.

---

#### `app/timestamp_extraction.py`

- Whisper call: `whisper-1`, `response_format=verbose_json`, `timestamp_granularities=["word"]`.
- Result: `response.words` (top-level list) — **not** `response.segments[].words`.
- Returns `list[WordTimestamp]` where each entry has `word`, `start`, `end` (seconds).

```python
class WordTimestamp(TypedDict):
    word: str
    start: float
    end: float
```

---

#### `app/video_compositing.py`

Word colour classification:

| Category | Colour | Examples |
|----------|--------|---------|
| Negative | Red | never, fail, lose, banned |
| Action | Yellow | build, ship, dominate, grind |
| Default | White | everything else |

Pipeline:
1. `AudioFileClip` loads the processed MP3; duration is extracted.
2. Background video is downloaded via `urllib.request.urlretrieve` to a temp dir.
3. `VideoFileClip.subclip(0, audio_duration)` crops the background.
4. One `TextClip` per word: Montserrat Black 70pt, with black stroke, positioned at `(center, 75%)`.
5. `CompositeVideoClip([bg, *text_clips])` renders to H.264/AAC MP4 at 30 fps.

---

#### `app/automated_publishing.py`

- Uses `playwright.sync_api.sync_playwright` with a **persistent Chromium context** so login cookies survive container restarts.
- Profile directory: `$CHROMIUM_PROFILE_DIR` (Docker volume mount).
- All selectors are defined as module-level constants for easy maintenance when platform UIs change.
- Both `upload_to_tiktok` and `upload_to_instagram` follow the pattern: navigate → file input → wait for upload → fill caption → submit → wait for success indicator.

---

### Key Data Models

```
GenerateRequest
  text: str
  background_video_url: HttpUrl
  caption: str = ""
  platforms: list[str] = ["tiktok"]

WordTimestamp
  word: str
  start: float   # seconds
  end: float     # seconds
```

---

### Infrastructure

```
┌─────────┐    HTTP     ┌────────────────┐
│  Client │ ──────────▶ │  FastAPI (api) │
└─────────┘             └───────┬────────┘
                                │ enqueue
                         ┌──────▼──────┐
                         │    Redis    │
                         └──────┬──────┘
                                │ consume
                    ┌───────────▼────────────┐
                    │   Celery Worker        │
                    │  (worker service)      │
                    └───────────┬────────────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
        OpenAI APIs       MoviePy/ffmpeg    Playwright
        (GPT-4o, TTS,                      (Chromium)
         Whisper)
```

Volumes:
- `chromium_profile` → `/data/chromium-profile` (shared by api + worker)
- `outputs` → `/data/outputs` (rendered MP4 files)

---

## Alternatives Considered

| Decision | Alternative | Why rejected |
|----------|-------------|-------------|
| Celery + Redis for async | FastAPI `BackgroundTasks` | No persistence, no retry, no cross-process result backend |
| OpenAI Whisper for timestamps | Gentle, WhisperX | Requires local GPU or separate service; Whisper API is zero-infra |
| MoviePy for compositing | FFmpeg subprocess | MoviePy provides a Pythonic clip model; raw ffmpeg commands are fragile to maintain |
| Playwright persistent context | Selenium, API-based upload | Platform upload APIs are restricted/private; persistent context re-uses auth cookies |
| pydub for silence stripping | librosa, sox | pydub is the simplest API for this single operation; no need for librosa's analysis capabilities |

---

## Security & Privacy

| Threat | Mitigation |
|--------|-----------|
| API key leakage | `OPENAI_API_KEY` injected at runtime via env var; never baked into image |
| SSRF via `background_video_url` | Currently unmitigated — should add allowlist or signed-URL validation before public deployment |
| Container privilege escalation | Worker runs as non-root `undertow` user |
| Social account credential theft | Chromium profile volume should be encrypted at rest in production |
| Arbitrary URL download | `urllib.request.urlretrieve` should be replaced with `httpx` + size/content-type validation |

---

## Testing Plan

| Layer | Tool | What is tested |
|-------|------|---------------|
| Unit | pytest + `unittest.mock` | Each `app/` module in isolation; all external calls mocked |
| Integration | pytest + `FastAPI TestClient` | API endpoints: enqueue, 503 on broker error, validation, status polling |
| Contract | — | Not yet implemented; would assert OpenAI request/response shapes |
| E2E | Manual | Full pipeline run against a real OpenAI key and test social accounts |

Run tests:
```bash
docker compose -f docker-compose.yml -f docker-compose.test.yml up --abort-on-container-exit
```

---

## Rollout Plan

| Phase | Description |
|-------|-------------|
| 1 | Deploy to a single host; test with internal test accounts |
| 2 | Connect real TikTok / Instagram accounts; validate selector stability |
| 3 | Enable auto-retry on failed Celery tasks (max 3 attempts, exponential backoff) |
| 4 | Add horizontal worker scaling behind a load balancer |
| 5 | Add webhook callback support for job completion events |
