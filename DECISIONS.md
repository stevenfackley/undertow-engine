# Decisions

ADR log. Append-only.

## {{DATE}} — Initial stack: Python 3.11 + FastAPI + uv

**Status:** accepted
**Context:** Greenfield service under `repo-template-python-fastapi`. Async IO, quick iteration.
**Decision:** Python 3.11, FastAPI, Pydantic v2, ruff + mypy + pytest, uv for dep management.
**Consequences:** uv lockfile authoritative; pip forbidden in CI. No telemetry SDKs (CI-enforced).

---

## 2026-04-17 — Dependabot sweep: bump fastapi / starlette / httpx / python-multipart / pytest

**Status:** accepted
**Context:** 9 open Dependabot alerts (3 high, 6 med) covering starlette multipart DoS (GHSA-f96h-pmfr-66vw, GHSA-2c2j-9gv5-cj73), python-multipart DoS (GHSA-mj87-hwqh-73pj), and pytest tmpdir symlink (GHSA-6w46-j5rx-g56g). Starlette fix requires ≥0.47.2; fastapi 0.111.0 pins starlette<0.38.0, forcing a fastapi bump.
**Decision:** Bump pyproject.toml: `fastapi>=0.118.0`, `python-multipart>=0.0.26`, add `starlette>=0.47.2`, `httpx>=0.28.0` (required for pytest-httpx 0.36 + pytest 9), add `[project.optional-dependencies].test` with `pytest>=9.0.3`, `pytest-httpx`, `ruff`. Regenerate `requirements.txt` / `requirements-test.txt` via `uv export`.
**Consequences:**
- Resolved pins: fastapi 0.111→0.136, starlette 0.37→1.0 (major!), httpx 0.27→0.28, python-multipart 0.0.22→0.0.26, pytest 8→9.
- **Breaking changes to watch for:**
  - **starlette 1.0:** `websocket.receive_text()` etc. may have stricter typing. `BackgroundTask` signatures unchanged. We only use `BaseHTTPMiddleware` and `Response` — both stable.
  - **fastapi 0.112+:** `.dict()` → `.model_dump()` on Pydantic models (we're on pydantic 2 already).
  - **httpx 0.28:** `app=` kwarg on `AsyncClient` removed → use `transport=httpx.ASGITransport(app=...)`. Check test fixtures.
  - **pytest 9:** `pytest.warns()` stricter; `--strict-markers` is default. Unknown markers now error.
  - **python-multipart 0.0.26:** module renamed `multipart` → `python_multipart` (fastapi handles the import; direct imports break).
- Surface in our code: `main.py` uses `FastAPI`, `Depends`, `HTTPException`, `APIKeyHeader`, `BaseHTTPMiddleware`, `Response` — all stable. `tests/integration/test_api.py` uses `TestClient` — stable across starlette 1.0.

---

## 2026-04-28 — Dependabot sweep: redis 5→7, cryptography 46→47, plus minors

**Status:** accepted (awareness-only stub per saved sweep policy)
**Context:** 8 open Dependabot PRs swept. Minors/patches: click 8.3.2→8.3.3, idna 3.11→3.13, pyroaring 1.0.4→1.1.0, tzdata 2026.1→2026.2, typer 0.24.1→0.25.0, uvicorn 0.44.0→0.46.0. Two majors warranted ADR notes (this entry).
**Decision:** Auto-merge per policy. Trust semver + GHSA `first_patched_version`; watch deploy workflow post-merge; revert if it breaks.
**Consequences — majors to watch:**
- **redis 5.0.4 → 7.4.0** (skipped 6.x):
  - **redis-py 6.0:** `Redis.connection_pool` is now private; use `Redis.from_url()` / explicit pool wiring. `client.execute_command()` return-type strictness tightened.
  - **redis-py 7.0:** drops Python 3.8/3.9; we're on 3.11 → fine. Async cluster API `RedisCluster.execute_command()` signature change. Connection retry kwargs renamed (`retry_on_timeout` → `Retry` instance).
  - Surface in our code: grep `redis` in repo before next deploy. If we use `from redis import Redis` synchronously, behavior is mostly compatible. If async (`redis.asyncio`), retry config may need a touch.
- **cryptography 46.0.7 → 47.0.0:**
  - Removes deprecated `Hash.copy()` returning identical state-machine semantics — minor edge case.
  - Drops legacy OpenSSL 1.1 binary wheels; modern Linux runtimes fine.
  - X.509 builder API: small return-type tightening on extension constructors.
  - Surface: only used transitively by FastAPI/starlette/httpx TLS chain — no direct usage in `main.py`. Risk: low.
**Why no review:** private/solo repo, deploy workflow is the real build, revert is cheap.

