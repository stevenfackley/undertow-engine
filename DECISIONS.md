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


## 2026-06-10 — Dependabot sweep: redis 7.4→8.0, rich 14→15, plus minors

**Context:** 10-PR sweep (#94–#103) merged in one pass: redis 8.0.0, rich 15.0.0
(majors), starlette 1.2.1, uvicorn 0.49.0, openai 2.41.0, pyjwt 2.13.0,
wcwidth 0.8.1, supabase trio 2.31.0.
**Consequences — majors to watch:**
- **redis 7.4.0 → 8.0.0:** direct first-party usage is a single health-check
  `redis.from_url(...).ping()` in `main.py:175` — stable API. Real exposure is
  transitive via celery/kombu broker+backend; CI install + pytest green means
  kombu's constraint range accepts 8.x. Watch Celery worker logs on first prod
  deploy for connection/retry-shape changes.
- **rich 14.3.3 → 15.0.0:** zero direct imports; only consumed by fastapi-cli's
  console output. Cosmetic blast radius.
**Why no review:** private/solo repo, deploy workflow is the real build, revert
is cheap.

**2026-06-11 correction:** rich 15 was NOT cosmetic — pyiceberg 0.11.x caps
`rich<15`, so the prod Docker build hit ResolutionImpossible. Reverted to
14.3.3 with a dependabot major-ignore until pyiceberg lifts the cap. Same
deploy also surfaced: pydantic-core must move only with pydantic (standalone
bump → 2.47.0 vs pydantic 2.13.4's exact 2.46.4 pin), and the supabase python
family (supabase / realtime / supabase-functions / storage3 / supabase-auth /
postgrest) is exact-pinned by the parent — Dependabot bumped 3 of 5 children,
leaving the parent unsatisfiable. Lesson: after merging a multi-PR pip sweep,
run one full `uv pip install --dry-run -r requirements.txt` resolve — pip
surfaces conflicts one at a time, a local resolve surfaces them all at once.

---

## 2026-06-13 — Replace MoviePy with direct ffmpeg + libass for video compositing

**Status:** accepted
**Context:** A prod render failed at `video_compositing` with `module 'PIL.Image'
has no attribute 'ANTIALIAS'`. Root cause: `moviepy==1.0.3` (unmaintained since
2020) calls `Image.ANTIALIAS`, removed in Pillow 10, and a Dependabot bump put us
on `pillow==12.2.0`. A compat shim would fix the symptom, but MoviePy is the
underlying liability: it's abandoned, drags ImageMagick (for `TextClip`) and a
pinned-Pillow tail, decodes every frame into NumPy and composites in Python
(slow), and MoviePy 2.x still caps `pillow<12`. MoviePy is used in exactly one
module, and the image already ships `ffmpeg` (with libass) and `fonts-montserrat`.
**Decision:** Drop MoviePy entirely. Rewrite `app/video_compositing.py` to render
with a single `ffmpeg` filtergraph: `scale=…:force_original_aspect_ratio=increase`
+ `crop` for the 9:16 cover-crop, `subtitles=` to burn a generated **ASS** subtitle
for the kinetic word-by-word captions (per-word timing/colour/outline via libass),
`-stream_loop` to loop short backgrounds, and `-map` to mux the voiceover. Caption
and command construction are pure functions (`_build_ass`, `_build_ffmpeg_cmd`,
`_format_ass_time`) with unit tests, plus a `skipif`-guarded real-ffmpeg render
smoke test. Remove `imagemagick` + its policy hack from the Dockerfile.
**Consequences:**
- Dependencies removed (no longer referenced anywhere): `moviepy`, `decorator`,
  `imageio`, `imageio-ffmpeg`, `numpy`, `pillow`, `proglog` (95 → 88 pins).
  The entire MoviePy/Pillow/ImageMagick version-conflict class is gone at the
  root — no shim, no Pillow pin, and the `decorator` Dependabot-ignore is dropped.
- `compose_video(...)` signature is unchanged; the worker is untouched.
- Captions now render via libass instead of ImageMagick `TextClip`; font is
  resolved by fontconfig name (`Montserrat Black`) from `fonts-montserrat`.
- Render is far faster (single native encode vs per-frame Python compositing).
- The render itself is exercised by CI only where ffmpeg is on PATH (smoke test
  skips otherwise); the production image always has it. First prod render after
  deploy is the live confirmation.
