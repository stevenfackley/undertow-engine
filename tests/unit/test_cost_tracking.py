"""Unit tests for app.cost_tracking pricing + accumulator."""

from __future__ import annotations

import pytest

from app.cost_tracking import (
    JobCostAccumulator,
    chat_cost_usd,
    tts_cost_usd,
    whisper_cost_usd,
)


def test_chat_cost_known_model():
    # gpt-4.1-mini: $0.40/1M input, $1.60/1M output
    cost = chat_cost_usd("gpt-4.1-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == pytest.approx(0.40 + 1.60)


def test_chat_cost_unknown_model_uses_fallback():
    cost = chat_cost_usd("some-future-model", prompt_tokens=1_000_000, completion_tokens=0)
    assert cost == pytest.approx(0.40)


def test_tts_cost_per_million_chars():
    assert tts_cost_usd(1_000_000) == pytest.approx(15.0)
    assert tts_cost_usd(0) == 0.0


def test_whisper_cost_per_minute():
    # whisper-1: $0.006 / minute
    assert whisper_cost_usd(60) == pytest.approx(0.006)
    assert whisper_cost_usd(30) == pytest.approx(0.003)


def test_accumulator_totals():
    acc = JobCostAccumulator()
    acc.add_chat("gpt-4.1-mini", prompt_tokens=1000, completion_tokens=500)
    acc.add_tts("tts-1", 2000)
    acc.add_whisper("whisper-1", 45.0)

    data = acc.as_dict()
    assert len(data["line_items"]) == 3
    assert data["total_tokens"] == 1500
    # Total is the sum of the per-line rounded costs.
    expected = (
        chat_cost_usd("gpt-4.1-mini", 1000, 500) + tts_cost_usd(2000) + whisper_cost_usd(45.0)
    )
    assert data["total_usd"] == pytest.approx(expected, abs=1e-6)


def test_accumulator_empty_is_zero():
    acc = JobCostAccumulator()
    data = acc.as_dict()
    assert data["total_usd"] == 0.0
    assert data["total_tokens"] == 0
    assert data["line_items"] == []


def test_accumulator_line_item_shape():
    acc = JobCostAccumulator()
    acc.add_chat("gpt-4o", prompt_tokens=10, completion_tokens=20)
    item = acc.line_items[0]
    assert item["step"] == "chat"
    assert item["model"] == "gpt-4o"
    assert item["prompt_tokens"] == 10
    assert item["completion_tokens"] == 20
    assert "usd" in item
