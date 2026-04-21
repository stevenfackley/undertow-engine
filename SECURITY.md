# Security

Report privately via GitHub Security Advisories. Acknowledge within 72h.

Rules: no static AWS keys (OIDC only); no telemetry SDKs (CI-enforced); `.env` chmod 600 on deploy; secret-shape grep runs on every PR.
