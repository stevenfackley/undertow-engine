"""Unit tests for app.sourcing — queue-state SQL against a fake connection."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app import sourcing


class FakeConn:
    """Context-manager stand-in for a psycopg connection.

    Records every (sql, params) pair passed to execute() and returns a cursor
    whose fetchall() yields the configured rows.
    """

    def __init__(self, rows: list[dict] | None = None):
        self.rows = rows or []
        self.calls: list[tuple[str, tuple | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        cursor = MagicMock()
        cursor.fetchall.return_value = self.rows
        return cursor


def _patched(fake: FakeConn):
    return patch("app.sourcing.connect", return_value=fake)


# ---------------------------------------------------------------------------
# fetch_pending
# ---------------------------------------------------------------------------


def test_fetch_pending_returns_rows():
    rows = [{"id": "a", "content": "one"}, {"id": "b", "content": "two"}]
    fake = FakeConn(rows=rows)
    with _patched(fake):
        result = sourcing.fetch_pending()
    assert result == rows


def test_fetch_pending_filters_pending_oldest_first():
    fake = FakeConn()
    with _patched(fake):
        sourcing.fetch_pending()
    sql, _ = fake.calls[0]
    assert "status = 'pending'" in sql
    assert "ORDER BY created_at" in sql


def test_fetch_pending_passes_limit_as_parameter():
    fake = FakeConn()
    with _patched(fake):
        sourcing.fetch_pending(limit=17)
    sql, params = fake.calls[0]
    assert "%s" in sql
    assert params == (17,)


# ---------------------------------------------------------------------------
# mark_processing
# ---------------------------------------------------------------------------


def test_mark_processing_updates_status_by_id():
    fake = FakeConn()
    with _patched(fake):
        sourcing.mark_processing("roast-1")
    sql, params = fake.calls[0]
    assert "status = 'processing'" in sql
    assert params == ("roast-1",)


# ---------------------------------------------------------------------------
# mark_published
# ---------------------------------------------------------------------------


def test_mark_published_sets_output_and_utc_timestamp():
    fake = FakeConn()
    with _patched(fake):
        sourcing.mark_published("roast-2", "/data/outputs/x.mp4")
    sql, params = fake.calls[0]
    assert "status = 'published'" in sql
    output_path, published_at, roast_id = params
    assert output_path == "/data/outputs/x.mp4"
    assert roast_id == "roast-2"
    assert isinstance(published_at, datetime)
    assert published_at.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


def test_mark_failed_stores_error_by_id():
    fake = FakeConn()
    with _patched(fake):
        sourcing.mark_failed("roast-3", "RuntimeError: boom")
    sql, params = fake.calls[0]
    assert "status = 'failed'" in sql
    assert params == ("RuntimeError: boom", "roast-3")


def test_mark_failed_truncates_error_to_2000_chars():
    """error_message is text but the module bounds it — a runaway traceback
    must not ship megabytes into the queue table."""
    fake = FakeConn()
    with _patched(fake):
        sourcing.mark_failed("roast-4", "x" * 5000)
    _, params = fake.calls[0]
    assert len(params[0]) == 2000
