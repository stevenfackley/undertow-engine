# undertow-engine

## Stack
Short-form video pipeline: FastAPI API + Celery worker/beat on Redis, ffmpeg + libass compositing, Playwright uploads. Python 3.12 + uv. See `docs/sdd/README.md` and `docs/architecture/README.md`.

## Standard flow
analyze -> read docs/ -> read pyproject.toml -> plan -> work.

## Conventions
- Image tags: `test-{SHA}` (test), `prod-{SHA}` (prod).
- Deps: `uv pip` over pip. Pinned in `uv.lock`.
- Lint: `ruff check`, `ruff format --check`.
- Tests: pytest + pytest-asyncio. E2E smoke on PR, integration on main.
- Commits: Conventional Commits.

## Deploy target
AWS EC2 host tagged `App=undertow` (`infra/ec2.tf`). `deploy-prod.yml` runs on push to `main`: builds and pushes `prod-{SHA}` to ECR via OIDC, then an SSM Run Command resets `/opt/undertow-engine` to `origin/main` and runs `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`. Dependabot pushes skip the deploy.

## Commands
mise tasks (`mise.toml`):
- `mise run ship` — pre-push: ruff check + ruff format --check + pytest. Run `pwsh scripts/scan-secrets.ps1` separately.
- `mise run lint` / `format-check` / `format` / `test`
- `mise run dev` — uvicorn API with reload on :8000
- `mise run worker` — Celery worker
- `mise run install` — install the package with test extras

## Do not
- Add static AWS keys. Use OIDC.
- Skip the secret scan: run `pwsh scripts/scan-secrets.ps1` before pushing; the secret-scan workflow gates PRs.
- Bump major deps without an ADR.
- Add telemetry SDKs. Banned by CI.
- `print()` in production code. Use `logging`.
