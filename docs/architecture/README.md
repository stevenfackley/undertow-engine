# Architecture

This directory contains high-level architecture documentation and Architecture Decision Records (ADRs) for undertow-engine.

## Contents

_Add architecture diagrams and ADRs here._

## Architecture Decision Records (ADRs)

ADRs capture significant technical decisions and their rationale. Name files using the pattern:

```
adr-001-<short-title>.md
adr-002-<short-title>.md
```

Each ADR should include:

- **Status** — Proposed | Accepted | Deprecated | Superseded
- **Context** — the situation that prompted the decision
- **Decision** — what was decided
- **Consequences** — trade-offs and implications

## System Overview

undertow-engine is a Python microservice composed of:

- **FastAPI** — REST API layer
- **Celery** — async task queue for video processing jobs
- **MoviePy** — video compositing engine
- **Playwright** — headless browser for social media deployment
- **Docker / Docker Compose** — containerised deployment
