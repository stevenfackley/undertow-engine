"""
Timestamp Extraction Module
---------------------------
Passes a processed audio file through OpenAI Whisper to extract
word-by-word timestamps for use in kinetic text rendering.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from openai import OpenAI

if TYPE_CHECKING:
    from app.cost_tracking import JobCostAccumulator

_client: OpenAI | None = None

# OpenAI transcription model used by :func:`extract_word_timestamps`.
WHISPER_MODEL = "whisper-1"


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


class WordTimestamp(TypedDict):
    word: str
    start: float  # seconds
    end: float    # seconds


def extract_word_timestamps(
    audio_path: str | Path,
    cost: JobCostAccumulator | None = None,
) -> list[WordTimestamp]:
    """
    Transcribe *audio_path* with Whisper and return per-word timestamps.

    Parameters
    ----------
    audio_path:
        Path to the processed audio file (MP3 / WAV).
    cost:
        Optional :class:`~app.cost_tracking.JobCostAccumulator`. When provided,
        the transcription cost (priced per audio minute, derived from the
        Whisper ``duration`` field) is recorded.

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
            model=WHISPER_MODEL,
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    if cost is not None:
        duration = getattr(response, "duration", None)
        if duration is not None:
            cost.add_whisper(WHISPER_MODEL, float(duration))

    words: list[WordTimestamp] = []
    for word_info in response.words or []:
        words.append(
            WordTimestamp(
                word=word_info.word.strip(),
                start=float(word_info.start),
                end=float(word_info.end),
            )
        )
    return words
