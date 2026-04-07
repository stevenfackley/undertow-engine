"""
Supabase Client
---------------
Lazy singleton client for the Roast & Resolve Supabase project.
Uses the service-role key so the worker can bypass RLS.
"""

from __future__ import annotations

import os

from supabase import Client, create_client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(
            supabase_url=os.environ["SUPABASE_URL"],
            supabase_key=os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _client
