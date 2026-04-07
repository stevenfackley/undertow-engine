"""
Sourcing Module
---------------
Reads pending roast payloads from the Roast & Resolve Supabase instance
and writes state transitions back after each pipeline stage.

Expected table schema (roast_queue):
    id                  uuid primary key default gen_random_uuid()
    content             text not null           -- topic / roast text passed to GPT-4o
    caption             text default ''
    platforms           text[] default '{tiktok}'
    background_video    text                    -- local path OR public URL
    status              text default 'pending'  -- pending | processing | published | failed
    error_message       text
    output_path         text
    created_at          timestamptz default now()
    published_at        timestamptz

SQL to create:
    create table roast_queue (
        id                uuid primary key default gen_random_uuid(),
        content           text not null,
        caption           text default '',
        platforms         text[] default '{tiktok}',
        background_video  text,
        status            text default 'pending',
        error_message     text,
        output_path       text,
        created_at        timestamptz default now(),
        published_at      timestamptz
    );
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.supabase_client import get_client

TABLE = "roast_queue"


def fetch_pending(limit: int = 5) -> list[dict]:
    """Return up to *limit* rows with status='pending', oldest first."""
    response = (
        get_client()
        .table(TABLE)
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .limit(limit)
        .execute()
    )
    return response.data or []


def mark_processing(roast_id: str) -> None:
    """Claim a row so no other worker picks it up."""
    get_client().table(TABLE).update({"status": "processing"}).eq("id", roast_id).execute()


def mark_published(roast_id: str, output_path: str) -> None:
    get_client().table(TABLE).update(
        {
            "status": "published",
            "output_path": output_path,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("id", roast_id).execute()


def mark_failed(roast_id: str, error: str) -> None:
    get_client().table(TABLE).update(
        {"status": "failed", "error_message": error[:2000]}
    ).eq("id", roast_id).execute()
