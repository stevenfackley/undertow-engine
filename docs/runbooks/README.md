# Runbooks

Operational runbooks for deploying, maintaining, and troubleshooting Undertow Engine.

- **[prod-operations.md](prod-operations.md)** — production on-call: diagnose
  stuck/failed jobs, inspect & re-drive the DLQ, read Flower, roll back a
  `prod-{SHA}` image, and find per-job cost. Start here for prod incidents.
- This file — local/dev compose operations and general troubleshooting.

---

## Quick Reference

| Task | Command |
|------|---------|
| Start all services | `docker compose up -d` |
| View live logs | `docker compose logs -f` |
| View worker logs only | `docker compose logs -f worker` |
| Run test suite | `docker compose -f docker-compose.yml -f docker-compose.test.yml up --abort-on-container-exit` |
| Stop all services | `docker compose down` |
| Stop and wipe volumes | `docker compose down -v` ⚠️ destroys rendered files and Chromium profile |

---

## Deploy a New Version

1. Pull latest code: `git pull origin main`
2. Rebuild the image: `docker compose build`
3. Restart services with zero-downtime rolling update:
   ```bash
   docker compose up -d --no-deps api
   docker compose up -d --no-deps worker
   ```
4. Verify: `curl http://localhost:8000/healthz` → `{"status":"ok"}`
5. Check worker connected to Redis: `docker compose logs worker | grep "celery@"`

---

## Rollback to a Previous Version

1. Identify the image tag or git SHA to roll back to.
2. Check out that commit: `git checkout <sha>`
3. Rebuild: `docker compose build`
4. Restart: `docker compose up -d`

If you tagged releases:
```bash
docker tag undertow-engine:latest undertow-engine:backup-$(date +%Y%m%d)
```
before deploying, you can roll back without rebuilding:
```bash
docker compose stop api worker
docker tag undertow-engine:backup-<date> undertow-engine:latest
docker compose up -d api worker
```

---

## Scale Celery Workers

Increase `--concurrency` (threads per worker) or run multiple worker replicas.

**Option A — increase concurrency in compose:**
Edit `docker-compose.yml`:
```yaml
command: celery -A worker.celery_app worker --loglevel=info --concurrency=4
```
Then: `docker compose up -d worker`

**Option B — run additional worker containers:**
```bash
docker compose up -d --scale worker=3
```
Each worker connects to the same Redis queue and processes jobs independently.

**Check active workers:**
```bash
docker compose exec worker celery -A worker.celery_app inspect active
```

---

## Restart a Stuck or Crashed Worker

Symptoms: jobs stay in `started` state indefinitely; no new `PROGRESS` updates.

```bash
# Check worker health
docker compose ps worker
docker compose logs --tail=50 worker

# Graceful restart (drains in-flight tasks)
docker compose restart worker

# Hard restart (kills in-flight tasks — jobs will stay in STARTED state in Redis)
docker compose stop worker && docker compose start worker
```

If a task is stuck in `STARTED` indefinitely, revoke it:
```bash
docker compose exec worker \
  celery -A worker.celery_app control revoke <task_id> --terminate
```

---

## Re-authenticate Chromium (Session Expired)

When TikTok or Instagram sessions expire, uploads will fail silently or redirect to a login page.

1. Stop the worker: `docker compose stop worker`
2. Run an interactive Chromium session against the persistent profile:
   ```bash
   docker compose run --rm -it \
     -e DISPLAY=:0 \
     --entrypoint python worker \
     -c "
   from playwright.sync_api import sync_playwright
   with sync_playwright() as pw:
       ctx = pw.chromium.launch_persistent_context('/data/chromium-profile', headless=False)
       input('Log in, then press Enter to save and exit...')
       ctx.close()
   "
   ```
   > On a headless server, forward X11 or use a VNC session to see the browser.
3. Log in to TikTok / Instagram in the browser that opens.
4. Press Enter to close and save the profile.
5. Restart the worker: `docker compose start worker`

---

## Incident Response

### Job stuck in `queued` — worker not consuming

1. Check Redis is healthy: `docker compose exec redis redis-cli ping` → `PONG`
2. Check worker is running: `docker compose ps worker`
3. Check worker can reach Redis: `docker compose logs worker | grep -i "error\|refused"`
4. If Redis restarted, the worker may need a restart to re-establish the connection:
   `docker compose restart worker`

### Job stuck in `progress` — pipeline hanging

1. Check which step it's stuck on: `curl http://localhost:8000/api/v1/jobs/<task_id>`
2. Tail worker logs: `docker compose logs -f worker`
3. Common causes:
   - `video_compositing`: ffmpeg running on a large file — check CPU and give it time.
   - `publishing`: Playwright waiting for a UI element that changed — check selector constants in `automated_publishing.py`.
4. If unrecoverable, revoke the task (see "Restart a Stuck Worker" above).

### All jobs failing with `failed`

1. Check `OPENAI_API_KEY` is set: `docker compose exec worker env | grep OPENAI`
2. Check OpenAI API status: https://status.openai.com
3. Check worker logs for Python tracebacks: `docker compose logs worker | grep -A 20 "ERROR"`

### Disk full — output volume

Rendered MP4s accumulate in `/data/outputs`. To clean up files older than 7 days:
```bash
docker compose exec worker find /data/outputs -name "*.mp4" -mtime +7 -delete
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key |
| `DATABASE_URL` | Yes | — | Postgres URL for the `roast_queue` backend (qavren-db transaction pooler; the role's search_path targets the app schema) |
| `REDIS_URL` | No | `redis://redis:6379/0` | Redis connection URL (prod) |
| `REDIS_TEST_URL` | No | `redis://localhost:6379/1` | Redis URL for test env |
| `CELERY_ENV` | No | `prod` | Set to `test` to use `REDIS_TEST_URL` |
| `CHROMIUM_PROFILE_DIR` | No | `/data/chromium-profile` | Persistent Chromium profile path |
| `OUTPUT_DIR` | No | `/data/outputs` | Rendered MP4 output directory |

Copy `.env.example` to `.env` and fill in values before starting.
