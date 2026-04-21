# {{PROJECT_NAME}}

## Stack
{{FLAVOR}}. Python 3.11 + FastAPI + uv. See `docs/architecture/SDD.md`.

## Standard flow
analyze -> read docs/ -> read pyproject.toml -> plan -> work.

## Conventions
- Image tags: `sha-{SHORT_SHA}` (test), `prod-{SHA}` (prod).
- Release tags: `YYYYMMDD_{{PROJECT_NAME}}_Release`.
- Deps: `uv pip` over pip. Pinned in `uv.lock`.
- Lint: `ruff check`, `ruff format --check`.
- Types: strict mypy.
- Tests: pytest + pytest-asyncio. E2E smoke on PR, integration on main.
- Commits: Conventional Commits.

## Deploy target
{{DEPLOY_TARGET}}

## Commands
- `/ship` — full pre-push (lint + types + tests + secret-scan)
- `/deploy-test` — trigger test deploy
- `/add-adr` — new DECISIONS.md entry
- `/smoke` — run E2E smoke locally

## Do not
- Add static AWS keys. Use OIDC.
- Skip the secret-scan pre-commit hook.
- Bump major deps without an ADR.
- Add telemetry SDKs. Banned by CI.
- `print()` in production code. Use `logging`.
