"""
Timestamp Extraction Module
---------------------------
Passes a processed audio file through OpenAI Whisper to extract
word-by-word timestamps for use in kinetic text rendering.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypedDict

from openai import OpenAI

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


class WordTimestamp(TypedDict):
    word: str
    start: float  # seconds
    end: float    # seconds


def extract_word_timestamps(audio_path: str | Path) -> list[WordTimestamp]:
    """
    Transcribe *audio_path* with Whisper and return per-word timestamps.

    Parameters
    ----------
    audio_path:
        Path to the processed audio file (MP3 / WAV).

    Returns
    -------
    list[WordTimestamp]
        A list of dicts with keys ``word``, ``start``, and ``end``
        (all times in seconds).
    """
    client = _get_client()
    audio_path = Path(audio_path)

    with audio_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words: list[WordTimestamp] = []
    for segment in response.segments or []:
        for word_info in segment.words or []:
            words.append(
                WordTimestamp(
                    word=word_info.word.strip(),
                    start=float(word_info.start),
                    end=float(word_info.end),
                )
            )
    return words
