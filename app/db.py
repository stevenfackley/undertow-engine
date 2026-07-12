"""
Database Client
---------------
Short-lived psycopg connections over DATABASE_URL. Replaces the Supabase
PostgREST client: the worker speaks plain SQL as its own database role now —
no service-role key, no RLS bypass.

DATABASE_URL should be the transaction-pooler URL in production;
prepare_threshold is disabled because pooled transaction mode cannot hold
named prepared statements across connections.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    """Open a connection; use as a context manager (commits on clean exit)."""
    return psycopg.connect(
        os.environ["DATABASE_URL"],
        row_factory=dict_row,
        prepare_threshold=None,
    )
