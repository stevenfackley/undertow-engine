# Contributing

## Flow
1. Branch off `main`.
2. Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`, `ci:`.
3. PR → CI green → squash-merge.

## Pre-push (run `mise run ship` plus the secret scan, or manually)

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
pwsh scripts/scan-secrets.ps1
```

## Major changes

Add a `DECISIONS.md` entry (ADR) when bumping a major dep, changing auth, or changing deploy topology.
