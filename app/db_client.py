"""
Neon / PostgreSQL client
------------------------
Direct psycopg2 implementation for use with Neon (or any standard Postgres
endpoint) instead of supabase-py.

Activate by setting DATABASE_URL in your environment:
    postgresql://user:password@ep-xxx.us-east-1.aws.neon.tech/dbname?sslmode=require

Provides the same interface as app.sourcing so the worker doesn't care which
backend is active.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

_pool: ThreadedConnectionPool | None = None
_TABLE = "roast_queue"


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=os.environ["DATABASE_URL"],
        )
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def fetch_pending(limit: int = 5) -> list[dict]:
    """Return up to *limit* rows with status='pending', oldest first."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT id, content, caption, platforms, background_video
                FROM {_TABLE}
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]


def mark_processing(roast_id: str) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_TABLE} SET status = 'processing' WHERE id = %s",
                (roast_id,),
            )


def mark_published(roast_id: str, output_path: str) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET status = 'published', output_path = %s, published_at = %s
                WHERE id = %s
                """,
                (output_path, datetime.now(timezone.utc), roast_id),
            )


def mark_failed(roast_id: str, error: str) -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE {_TABLE} SET status = 'failed', error_message = %s WHERE id = %s",
                (error[:2000], roast_id),
            )
