"""
Sourcing Module
---------------
Reads pending roast payloads and writes state transitions back after each
pipeline stage.

Backend selection (checked at import time):
  DATABASE_URL set  →  app.db_client  (Neon / any Postgres via psycopg2)
  DATABASE_URL unset →  Supabase client (supabase-py)

Set DATABASE_URL to your Neon connection string to bypass supabase-py entirely:
    DATABASE_URL=postgresql://user:pass@ep-xxx.us-east-1.aws.neon.tech/dbname?sslmode=require

Expected table schema (roast_queue) — works for both backends:
    create table roast_queue (
        id                uuid primary key default gen_random_uuid(),
        content           text not null,
        caption           text default '',
        platforms         text[] default '{tiktok}',
        background_video  text,          -- local path OR public URL
        status            text default 'pending',  -- pending | processing | published | failed
        error_message     text,
        output_path       text,
        created_at        timestamptz default now(),
        published_at      timestamptz
    );
"""

from __future__ import annotations

import os

if os.environ.get("DATABASE_URL"):
    # Neon / standard Postgres — psycopg2 connection pool, no supabase-py needed
    from app.db_client import (
        fetch_pending,
        mark_failed,
        mark_processing,
        mark_published,
    )
else:
    # Supabase — uses supabase-py + service-role key
    from datetime import datetime, timezone

    from app.supabase_client import get_client

    _TABLE = "roast_queue"

    def fetch_pending(limit: int = 5) -> list[dict]:
        """Return up to *limit* rows with status='pending', oldest first."""
        response = (
            get_client()
            .table(_TABLE)
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(limit)
            .execute()
        )
        return response.data or []

    def mark_processing(roast_id: str) -> None:
        get_client().table(_TABLE).update({"status": "processing"}).eq("id", roast_id).execute()

    def mark_published(roast_id: str, output_path: str) -> None:
        get_client().table(_TABLE).update(
            {
                "status": "published",
                "output_path": output_path,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", roast_id).execute()

    def mark_failed(roast_id: str, error: str) -> None:
        get_client().table(_TABLE).update(
            {"status": "failed", "error_message": error[:2000]}
        ).eq("id", roast_id).execute()
