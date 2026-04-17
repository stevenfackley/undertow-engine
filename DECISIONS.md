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
