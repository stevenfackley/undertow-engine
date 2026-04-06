"""Unit tests for app.ai_scripting."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.ai_scripting as module


@pytest.fixture(autouse=True)
def reset_client():
    """Ensure the cached OpenAI client is cleared between tests."""
    module._client = None
    yield
    module._client = None


def _mock_completion(text: str) -> MagicMock:
    choice = SimpleNamespace(message=SimpleNamespace(content=f"  {text}  "))
    response = MagicMock()
    response.choices = [choice]
    return response


def test_generate_script_returns_stripped_text():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion("Hook. Body. Loop.")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.ai_scripting.OpenAI", return_value=mock_client):
            result = module.generate_script("gaming tips")

    assert result == "Hook. Body. Loop."


def test_generate_script_passes_topic_in_user_message():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion("script")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.ai_scripting.OpenAI", return_value=mock_client):
            module.generate_script("python tricks")

    call_kwargs = mock_client.chat.completions.create.call_args
    messages = call_kwargs.kwargs["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    assert "python tricks" in user_msg["content"]


def test_generate_script_uses_gpt4o():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion("x")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.ai_scripting.OpenAI", return_value=mock_client):
            module.generate_script("topic")

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == "gpt-4o"


def test_generate_script_respects_max_tokens():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_completion("x")

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.ai_scripting.OpenAI", return_value=mock_client):
            module.generate_script("topic", max_tokens=150)

    call_kwargs = mock_client.chat.completions.create.call_args
    assert call_kwargs.kwargs["max_tokens"] == 150
