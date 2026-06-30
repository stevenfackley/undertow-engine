# Production Operations Runbook

On-call reference for the **live** Undertow Engine prod host (EC2, tagged
`App=undertow`, deployed via SSM). For local/dev compose operations see
[`README.md`](README.md).

Prod runs the compose overlay:

```bash
cd /opt/undertow-engine
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
```

> All `$COMPOSE` commands below assume that alias and `cd /opt/undertow-engine`.

---

## Diagnose a stuck or failed job

A job's lifecycle: `queued → started → progress(step) → complete | failed`.
Poll it:

```bash
curl -s -H "X-API-Key: $API_KEY" http://localhost:8001/api/v1/jobs/<task_id> | jq
```

| Symptom | Where to look | Likely cause |
|---------|---------------|--------------|
| Stuck `queued` | worker up? Redis reachable? | worker down / broker auth |
| Stuck `progress` on a step | worker logs for that step | ffmpeg on a big file (give it time), Playwright selector drift (`automated_publishing.py`) |
| `failed` | `job_dead_lettered` log + the DLQ record | permanent input error, or retries exhausted on a transient one |

Find the structured terminal event in the worker logs (JSON lines):

```bash
$COMPOSE logs --since 2h worker | grep -E '"(job_dead_lettered|job_retry_scheduled|pipeline_complete)"'
```

`job_retry_scheduled` shows transient failures being retried with backoff;
`job_dead_lettered` is terminal and includes `classification`
(`permanent` vs `retries_exhausted`) and the `dlq_path`.

### Retry / DLQ policy (how failures are handled)

- **Transient** (network, OpenAI 429 / 5xx, timeouts) → retried up to
  `JOB_MAX_RETRIES` (default 3) with exponential backoff
  (`min(JOB_RETRY_BACKOFF_MAX, JOB_RETRY_BACKOFF_BASE * 2**n)` + jitter).
- **Permanent** (malformed input: `ValueError`, `FileNotFoundError`, HTTP 4xx
  other than 429, or an explicit `app.reliability.PermanentJobError`) →
  dead-lettered **immediately**, no retries.
- On dead-letter the worker: writes a DLQ record, sets the Supabase row to
  `failed` (if `roast_id`), and fires the completion webhook with
  `status=dead_letter`.

---

## Inspect the dead-letter queue (DLQ)

Dead-lettered jobs are captured as JSON at `DLQ_DIR` (default `/data/dlq`,
mounted as the `dlq` named volume so it survives deploys).

```bash
# List dead-lettered jobs
$COMPOSE exec worker ls -lt /data/dlq

# Read one record (original payload, error, classification, cost, timestamp)
$COMPOSE exec worker cat /data/dlq/<task_id>.json
```

Each record contains: `task_id`, `roast_id`, `error`, `error_type`, `retries`,
`classification`, `cost`, the original `payload`, and `dead_lettered_at`.

---

## Re-drive a dead-lettered job

Decide first **why** it failed (`classification` + `error` in the record). A
`permanent` failure will fail again unless the input is fixed.

**Option A — Supabase-sourced job (has `roast_id`):** the row was set to
`failed`. Reset it to `pending`; the `source_and_enqueue` beat task re-enqueues
it within 5 minutes.

```sql
update roast_queue set status = 'pending', error_message = null where id = '<roast_id>';
```

**Option B — re-enqueue directly from the DLQ record (works with or without a
roast_id):**

```bash
$COMPOSE exec worker python - <<'PY'
import json, sys
from worker import process_video_payload
rec = json.load(open("/data/dlq/<task_id>.json"))
p = rec["payload"]
process_video_payload.delay(
    text=p["text"],
    background_video=p.get("background_video", ""),
    caption=p.get("caption", ""),
    platforms=p.get("platforms", ["tiktok"]),
    callback_url=p.get("callback_url"),
)
print("re-enqueued")
PY
```

After a successful re-drive, remove the stale DLQ file:
`$COMPOSE exec worker rm /data/dlq/<task_id>.json`.

---

## Read Flower (Celery monitoring)

Flower runs in the `flower` service on port 5555 with basic auth
(`FLOWER_USER` / `FLOWER_PASSWORD`). In prod the port is not published; reach it
over the Cloudflare tunnel or an SSH port-forward:

```bash
# From your laptop, tunnel 5555 to the prod box, then open http://localhost:5555
ssh -N -L 5555:localhost:5555 <prod-host>
```

Use it to see active/reserved/failed tasks, per-task runtime, and worker
concurrency. A growing "reserved" count with no "active" usually means the
worker is wedged — see "Restart a stuck worker" in `README.md`.

---

## Roll back a prod image (`prod-{SHA}`)

Each prod build pushes an immutable `prod-${GITHUB_SHA}` tag to ECR, but the
running containers are **built on the box** from the git checkout at
`/opt/undertow-engine` (the deploy does `git reset --hard origin/main` then
`up -d --build`). So a rollback pins the box to a previous commit and rebuilds:

```bash
cd /opt/undertow-engine
git fetch origin
git reset --hard <previous-good-SHA>        # the SHA from the prod-<SHA> tag you trust
$COMPOSE up -d --build --remove-orphans
curl -s http://localhost:8001/healthz       # expect {"status":"ok",...}
```

> Prefer rolling **forward** (revert the bad commit on `main`, let the deploy
> workflow run) when possible — a manual `reset --hard` on the box drifts from
> `main` and the next deploy will overwrite it.

To confirm which commit is live: `git -C /opt/undertow-engine rev-parse HEAD`.

---

## Cost tracking (per-job OpenAI spend)

Spend is tracked per job (charge-back unit = one job).

- **Structured log:** every successful job emits a `job_cost` event; the result
  record and the success webhook carry the same `cost` object
  (`total_usd`, `total_tokens`, and per-step `line_items` for chat / TTS /
  whisper).

  ```bash
  $COMPOSE logs --since 24h worker | grep '"job_cost"' | jq '.cost.total_usd'
  ```

- Dead-lettered jobs include the partial `cost` accrued before failure in their
  DLQ record.
- Rates default to OpenAI list pricing and can be overridden via
  `OPENAI_PRICE_*` env vars (see `.env.example`).

---

## Reliability environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JOB_MAX_RETRIES` | `3` | Max retries for transient failures |
| `JOB_RETRY_BACKOFF_BASE` | `30` | Backoff base seconds |
| `JOB_RETRY_BACKOFF_MAX` | `300` | Backoff ceiling seconds |
| `DLQ_DIR` | `/data/dlq` | Dead-letter record directory (named volume) |
| `JOB_WEBHOOK_URL` | — | Default completion-callback URL (blank = disabled) |
| `JOB_WEBHOOK_SECRET` | — | HMAC-SHA256 signing key for the callback body |
| `OPENAI_PRICE_*` | list pricing | Cost-tracking rate overrides |
