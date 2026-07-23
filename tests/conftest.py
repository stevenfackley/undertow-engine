"""Shared fixtures for the undertow-engine test suite."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def task_self():
    """A fake bound-task ``self`` for calling Celery task bodies directly.

    Mirrors the attributes worker code reads: ``request.id``,
    ``request.retries``, ``max_retries``, plus ``update_state`` and ``retry``
    as recordable mocks. ``retry`` raises ``celery.exceptions.Retry`` like the
    real method so control flow matches production.
    """
    from celery.exceptions import Retry

    self_mock = MagicMock()
    self_mock.request.id = "task-under-test"
    self_mock.request.retries = 0
    self_mock.max_retries = 3
    self_mock.retry = MagicMock(side_effect=Retry("retry scheduled"))
    return self_mock


@pytest.fixture()
def preserve_root_logger():
    """Snapshot and restore root-logger handlers/level around a test.

    ``configure_logging`` mutates global logging state; without this, tests
    that exercise it would leak JSON handlers into the rest of the run.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
