"""Unit tests for app.timestamp_extraction."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest

import app.timestamp_extraction as module


@pytest.fixture(autouse=True)
def reset_client():
    module._client = None
    yield
    module._client = None


def _make_word(word: str, start: float, end: float):
    return SimpleNamespace(word=f"  {word}  ", start=start, end=end)


def _make_response(words):
    return SimpleNamespace(words=words)


def test_extract_word_timestamps_returns_correct_structure(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = _make_response([
        _make_word("Hello", 0.0, 0.4),
        _make_word("world", 0.5, 0.9),
    ])

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.timestamp_extraction.OpenAI", return_value=mock_client):
            result = module.extract_word_timestamps(audio)

    assert len(result) == 2
    assert result[0] == {"word": "Hello", "start": 0.0, "end": 0.4}
    assert result[1] == {"word": "world", "start": 0.5, "end": 0.9}


def test_extract_word_timestamps_strips_whitespace(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = _make_response([
        _make_word("  Go  ", 0.0, 0.3),
    ])

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.timestamp_extraction.OpenAI", return_value=mock_client):
            result = module.extract_word_timestamps(audio)

    assert result[0]["word"] == "Go"


def test_extract_word_timestamps_empty_response(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = _make_response(None)

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.timestamp_extraction.OpenAI", return_value=mock_client):
            result = module.extract_word_timestamps(audio)

    assert result == []


def test_extract_word_timestamps_uses_whisper_1(tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"fake")

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = _make_response([])

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.timestamp_extraction.OpenAI", return_value=mock_client):
            module.extract_word_timestamps(audio)

    call_kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
    assert call_kwargs["model"] == "whisper-1"
    assert call_kwargs["response_format"] == "verbose_json"
    assert "word" in call_kwargs["timestamp_granularities"]
