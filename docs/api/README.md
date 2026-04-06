# API Reference

Auto-generated interactive docs are available at `/docs` (Swagger UI) and `/redoc` when the service is running.

Base URL: `http://localhost:8000` (local) — override with your host in production.

---

## Authentication

Currently unauthenticated. Add a reverse proxy (e.g. nginx with `auth_basic`, or an API gateway) in front of the service before exposing it publicly.

---

## Endpoints

### Health

#### `GET /healthz`

Liveness probe. Returns immediately without touching Redis or any external service.

**Response `200 OK`**
```json
{ "status": "ok" }
```

---

### Jobs

#### `POST /api/v1/generate`

Enqueue a new video generation job. Returns a `task_id` immediately; rendering happens asynchronously.

**Request body**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `text` | string | Yes | — | Topic or raw idea passed to the AI scriptwriter |
| `background_video_url` | string (URL) | Yes | — | Publicly accessible URL of the gameplay/background video |
| `caption` | string | No | `""` | Post caption including hashtags |
| `platforms` | string[] | No | `["tiktok"]` | Target platforms. Supported: `"tiktok"`, `"instagram"` |

**Example request**
```json
{
  "text": "3 things every developer should know about Docker",
  "background_video_url": "https://cdn.example.com/gameplay.mp4",
  "caption": "Drop a 🔥 if this helped. #docker #devtips",
  "platforms": ["tiktok", "instagram"]
}
```

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string (UUID) | Use this to poll job status |
| `status` | string | Always `"queued"` on successful enqueue |

```json
{
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued"
}
```

**Response `422 Unprocessable Entity`** — missing required fields or invalid URL.

**Response `503 Service Unavailable`** — Celery broker unreachable.

---

#### `GET /api/v1/jobs/{task_id}`

Poll the status of a video generation job.

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `task_id` | string | The `task_id` returned by `POST /api/v1/generate` |

**Response `200 OK`**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Echo of the requested task ID |
| `status` | string | See status values below |
| `step` | string \| null | Current pipeline step when `status` is `"progress"` |
| `output_path` | string \| null | Absolute path to the rendered MP4 when `status` is `"complete"` |

**Status values**

| Value | Meaning |
|-------|---------|
| `pending` | Job enqueued, not yet picked up by a worker |
| `started` | Worker picked up the job |
| `progress` | Worker is actively processing; see `step` for current stage |
| `complete` | Pipeline finished successfully |
| `failed` | Pipeline encountered an unrecoverable error |

**Step values** (when `status` is `"progress"`)

| Value | Pipeline stage |
|-------|---------------|
| `scripting` | Generating script with GPT-4o |
| `audio_processing` | TTS synthesis + silence stripping |
| `timestamp_extraction` | Whisper word-timestamp extraction |
| `video_compositing` | MoviePy render |
| `publishing` | Playwright upload to platforms |

**Example response — in progress**
```json
{
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "progress",
  "step": "video_compositing",
  "output_path": null
}
```

**Example response — complete**
```json
{
  "task_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "complete",
  "step": null,
  "output_path": "/data/outputs/3fa85f64-5717-4562-b3fc-2c963f66afa6.mp4"
}
```

---

## Error Responses

All error responses follow FastAPI's default format:

```json
{
  "detail": "human-readable error message"
}
```

| HTTP Code | When |
|-----------|------|
| 422 | Request body fails validation |
| 503 | Celery broker is unreachable when enqueuing |

---

## Example: Full Job Lifecycle

```bash
# 1. Enqueue
curl -s -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{
    "text": "why you should quit your job and go indie",
    "background_video_url": "https://cdn.example.com/bg.mp4",
    "platforms": ["tiktok"]
  }' | jq .
# → { "task_id": "abc-123", "status": "queued" }

# 2. Poll until complete
watch -n 5 'curl -s http://localhost:8000/api/v1/jobs/abc-123 | jq .'
```
