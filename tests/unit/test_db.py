"""Unit tests for app.db — connection wiring."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from psycopg.rows import dict_row

from app import db


def test_connect_uses_database_url():
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql://u:p@host/dbname"}):
        with patch("app.db.psycopg") as mock_psycopg:
            db.connect()

    args, _ = mock_psycopg.connect.call_args
    assert args[0] == "postgresql://u:p@host/dbname"


def test_connect_disables_prepared_statements_for_pooled_mode():
    """Transaction-pooler URLs cannot hold named prepared statements — the
    module must pass prepare_threshold=None on every connection."""
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql://u:p@host/dbname"}):
        with patch("app.db.psycopg") as mock_psycopg:
            db.connect()

    _, kwargs = mock_psycopg.connect.call_args
    assert kwargs["prepare_threshold"] is None


def test_connect_uses_dict_rows():
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql://u:p@host/dbname"}):
        with patch("app.db.psycopg") as mock_psycopg:
            db.connect()

    _, kwargs = mock_psycopg.connect.call_args
    assert kwargs["row_factory"] is dict_row


def test_connect_raises_keyerror_when_database_url_unset():
    """A missing DATABASE_URL must fail loudly, not open a default connection."""
    with patch.dict("os.environ", {}, clear=True):
        with patch("app.db.psycopg"):
            with pytest.raises(KeyError):
                db.connect()


def test_connect_returns_the_psycopg_connection():
    with patch.dict("os.environ", {"DATABASE_URL": "postgresql://u:p@host/dbname"}):
        with patch("app.db.psycopg") as mock_psycopg:
            conn = db.connect()

    assert conn is mock_psycopg.connect.return_value
