# Undertow Engine — Product Roadmap

This document outlines the strategic direction for Undertow Engine beyond the initial CI/CD and ffmpeg migration recovery.

## Phase 1: Resilience & Observability (Current Focus)

- **[ ] Webhook Callbacks:** Fully operationalise the `callback_url` support in the worker. Ensure the payload includes the public download URL or S3 key.
- **[ ] Enhanced Health Monitoring:** Extend `/readyz` to check not just Redis but also verify that the `ffmpeg` binary and fonts are accessible in the container.
- **[ ] Structured Logging Expansion:** Standardise log events across `api` and `worker` to enable better Traceability in CloudWatch/Grafana.
- **[ ] Automatic Job Re-queueing:** Implement logic to handle worker crashes by re-queueing "stale" jobs (jobs that have been in `started` state for > 30 mins).

## Phase 2: Product Enrichment (Next Up)

- **[ ] Animated kinetic captions:** Implement per-word animations (scale-up, fade-in, or "karaoke" style highlighting) using advanced ASS/SSA features.
- **[ ] Background B-Roll Sourcing:** Integration with Pexels/Pixabay API to automatically fetch relevant background footage based on script keywords, removing the requirement for a user-provided URL.
- **[ ] Voice Selection:** Expose the OpenAI TTS voice selection (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`) as an API parameter.
- **[ ] Background Music Bed:** Support mixing a background music track at a lower volume (-20dB) underneath the voiceover.

## Phase 3: Platform & Scale (Long Term)

- **[ ] YouTube Shorts Support:** Implement publishing to YouTube via Playwright (or official Data API if preferred).
- **[ ] S3 Storage Migration:** Replace local Docker volumes with AWS S3 for storing rendered MP4s, enabling multi-worker scaling across multiple EC2 instances.
- **[ ] AI-Driven Clip Selection:** Use a lightweight vision model to identify the "high action" segments of a long background video to use as the render source.
- **[ ] Multi-track Parallel Rendering:** Optimise the worker to handle multiple render jobs in parallel if CPU/GPU headroom allows.

## Phase 4: Developer Experience (Ongoing)

- **[ ] OpenAPI/Swagger Documentation:** Fully annotate the FastAPI models for a polished `/docs` experience.
- **[ ] Local Development CLI:** A small `undertow` CLI for testing the pipeline locally without hitting the full API (wrapper around `worker.py` logic).
- **[ ] CI/CD Hardening:** Implement a "Canary" deploy step that runs a smoke test on a temp container before swapping the prod instance.
