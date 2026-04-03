# Runbooks

This directory contains operational runbooks for deploying, maintaining, and troubleshooting undertow-engine in production.

## Contents

_Add runbook documents here._

## Suggested Runbooks

| Runbook | Description |
|---------|-------------|
| `deploy.md` | Steps to deploy a new version to production |
| `rollback.md` | How to roll back to a previous version |
| `scaling.md` | How to scale Celery workers |
| `incident-response.md` | General incident response playbook |
| `celery-worker-restart.md` | Restarting stuck or crashed workers |

## Quick Reference

- **Start services:** `docker compose up -d`
- **View logs:** `docker compose logs -f`
- **Run tests:** `docker compose -f docker-compose.test.yml up --abort-on-container-exit`
