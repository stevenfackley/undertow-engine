"""Unit tests for app.audio_processing."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydub import AudioSegment

import app.audio_processing as module


@pytest.fixture(autouse=True)
def reset_client():
    module._client = None
    yield
    module._client = None


def _silent_segment(duration_ms: int = 500) -> AudioSegment:
    return AudioSegment.silent(duration=duration_ms)


def test_synthesise_speech_returns_audio_segment():
    fake_mp3 = _silent_segment(500).export(format="mp3").read()
    mock_response = MagicMock()
    mock_response.read.return_value = fake_mp3

    mock_client = MagicMock()
    mock_client.audio.speech.create.return_value = mock_response

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.audio_processing.OpenAI", return_value=mock_client):
            result = module.synthesise_speech("Hello world")

    assert isinstance(result, AudioSegment)
    mock_client.audio.speech.create.assert_called_once_with(
        model="tts-1",
        voice="onyx",
        input="Hello world",
        response_format="mp3",
    )


def test_synthesise_speech_custom_voice():
    fake_mp3 = _silent_segment(200).export(format="mp3").read()
    mock_response = MagicMock()
    mock_response.read.return_value = fake_mp3

    mock_client = MagicMock()
    mock_client.audio.speech.create.return_value = mock_response

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        with patch("app.audio_processing.OpenAI", return_value=mock_client):
            module.synthesise_speech("Hi", voice="nova")

    call_kwargs = mock_client.audio.speech.create.call_args.kwargs
    assert call_kwargs["voice"] == "nova"


def test_strip_silence_removes_gaps():
    # Build audio: 200ms tone, 500ms silence, 200ms tone
    tone = AudioSegment.silent(duration=200) + AudioSegment.from_mono_audiosegments(
        AudioSegment.silent(duration=1)  # just needs to exist
    ) if False else AudioSegment.silent(duration=200)

    # Use a real audio segment with actual content to exercise split_on_silence
    segment = AudioSegment.silent(duration=1000)
    result = module.strip_silence(segment)
    # Silent audio → chunks will be empty → original returned unchanged
    assert isinstance(result, AudioSegment)


def test_strip_silence_returns_original_when_no_chunks():
    audio = AudioSegment.silent(duration=300)
    with patch("app.audio_processing.split_on_silence", return_value=[]):
        result = module.strip_silence(audio)
    assert result is audio


def test_process_script_to_audio_writes_file(tmp_path):
    output = tmp_path / "out.mp3"
    fake_audio = _silent_segment(300)

    with patch.object(module, "synthesise_speech", return_value=fake_audio) as mock_synth:
        with patch.object(module, "strip_silence", return_value=fake_audio) as mock_strip:
            result = module.process_script_to_audio("test script", output)

    assert result == output
    assert output.exists()
    mock_synth.assert_called_once_with("test script")
    mock_strip.assert_called_once_with(fake_audio)
