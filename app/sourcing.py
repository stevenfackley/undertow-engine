"""
Sourcing Module
---------------
Reads pending roast payloads and writes state transitions back after each
pipeline stage.

This module uses Postgres (shared qavren-db project) as the source of truth
for queue state. Table names are unqualified — they resolve through the
connection role's search_path.

Expected table schema (roast_queue):
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

from datetime import datetime, timezone

from app.db import connect

_TABLE = "roast_queue"


def fetch_pending(limit: int = 5) -> list[dict]:
    """Return up to *limit* rows with status='pending', oldest first."""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM {_TABLE} WHERE status = 'pending' ORDER BY created_at LIMIT %s",
            (limit,),
        ).fetchall()
    return rows


def mark_processing(roast_id: str) -> None:
    with connect() as conn:
        conn.execute(
            f"UPDATE {_TABLE} SET status = 'processing' WHERE id = %s",
            (roast_id,),
        )


def mark_published(roast_id: str, output_path: str) -> None:
    with connect() as conn:
        conn.execute(
            f"UPDATE {_TABLE} SET status = 'published', output_path = %s, published_at = %s"
            " WHERE id = %s",
            (output_path, datetime.now(timezone.utc), roast_id),
        )


def mark_failed(roast_id: str, error: str) -> None:
    with connect() as conn:
        conn.execute(
            f"UPDATE {_TABLE} SET status = 'failed', error_message = %s WHERE id = %s",
            (error[:2000], roast_id),
        )
