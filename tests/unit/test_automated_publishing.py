"""Unit tests for app.automated_publishing."""

from __future__ import annotations

import pytest

from app.automated_publishing import (
    INSTAGRAM_CAPTION_LIMIT,
    TIKTOK_CAPTION_LIMIT,
    _truncate_caption,
)


def test_truncate_short_caption_unchanged():
    assert _truncate_caption("Hello #world", TIKTOK_CAPTION_LIMIT) == "Hello #world"


def test_truncate_empty_caption_unchanged():
    assert _truncate_caption("", TIKTOK_CAPTION_LIMIT) == ""


def test_truncate_exact_limit_unchanged():
    caption = "x" * TIKTOK_CAPTION_LIMIT
    assert _truncate_caption(caption, TIKTOK_CAPTION_LIMIT) == caption


def test_truncate_over_limit_shortened():
    caption = "x" * (TIKTOK_CAPTION_LIMIT + 50)
    result = _truncate_caption(caption, TIKTOK_CAPTION_LIMIT)
    assert len(result) == TIKTOK_CAPTION_LIMIT


def test_truncate_over_limit_ends_with_ellipsis():
    caption = "x" * (TIKTOK_CAPTION_LIMIT + 1)
    result = _truncate_caption(caption, TIKTOK_CAPTION_LIMIT)
    assert result.endswith("…")


def test_truncate_instagram_limit():
    caption = "y" * (INSTAGRAM_CAPTION_LIMIT + 10)
    result = _truncate_caption(caption, INSTAGRAM_CAPTION_LIMIT)
    assert len(result) == INSTAGRAM_CAPTION_LIMIT
    assert result.endswith("…")


def test_truncate_custom_limit():
    result = _truncate_caption("Hello world!", 5)
    assert len(result) == 5
    assert result == "Hell…"
