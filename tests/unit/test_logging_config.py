"""Unit tests for app.logging_config — JSON log line shape and idempotency."""

from __future__ import annotations

import json
import logging

from app.logging_config import configure_logging


def _emit_and_capture(capsys, logger_name: str = "undertow.test", message: str = "hello"):
    logging.getLogger(logger_name).info(message)
    return capsys.readouterr().out.strip()


def test_emits_json_with_renamed_fields(capsys, preserve_root_logger):
    configure_logging()
    line = _emit_and_capture(capsys)
    record = json.loads(line)
    assert record["level"] == "INFO"
    assert record["logger"] == "undertow.test"
    assert record["message"] == "hello"
    assert "ts" in record
    # The originals must be renamed away, not duplicated.
    assert "levelname" not in record
    assert "asctime" not in record


def test_extra_fields_appear_in_json(capsys, preserve_root_logger):
    configure_logging()
    logging.getLogger("undertow.test").info("evt", extra={"task_id": "t-1"})
    record = json.loads(capsys.readouterr().out.strip())
    assert record["task_id"] == "t-1"


def test_configure_twice_installs_single_handler(preserve_root_logger):
    """Both main.py and worker.py call configure_logging at import — a double
    call must not double every log line."""
    configure_logging()
    configure_logging()
    assert len(logging.getLogger().handlers) == 1


def test_level_argument_is_applied(preserve_root_logger):
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_unknown_level_falls_back_to_info(preserve_root_logger):
    configure_logging("NOT_A_LEVEL")
    assert logging.getLogger().level == logging.INFO


def test_noisy_third_party_loggers_are_quieted(preserve_root_logger):
    configure_logging()
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("celery").level == logging.INFO
