"""
Audio Processing Module
-----------------------
1. Passes a script to OpenAI TTS (tts-1) to synthesise speech.
2. Uses pydub ``split_on_silence`` to aggressively strip dead air and
   breathing pauses longer than 200 ms.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import TYPE_CHECKING

from openai import OpenAI
from pydub import AudioSegment
from pydub.silence import split_on_silence

if TYPE_CHECKING:
    from app.cost_tracking import JobCostAccumulator

_client: OpenAI | None = None

# OpenAI TTS model used by :func:`synthesise_speech`.
TTS_MODEL = "tts-1"


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def synthesise_speech(script: str, voice: str = "onyx") -> AudioSegment:
    """
    Convert *script* to speech using OpenAI TTS (tts-1).

    Parameters
    ----------
    script:
        The script text to synthesise.
    voice:
        One of the TTS voice identifiers supported by OpenAI (default: "onyx").

    Returns
    -------
    AudioSegment
        Raw synthesised audio as a pydub AudioSegment.
    """
    client = _get_client()
    response = client.audio.speech.create(
        model=TTS_MODEL,
        voice=voice,
        input=script,
        response_format="mp3",
    )
    audio_bytes = io.BytesIO(response.read())
    return AudioSegment.from_file(audio_bytes, format="mp3")


def strip_silence(
    audio: AudioSegment,
    min_silence_len: int = 200,
    silence_thresh_db: int = -40,
    keep_silence: int = 50,
) -> AudioSegment:
    """
    Aggressively remove silent gaps and breathing pauses from *audio*.

    Parameters
    ----------
    audio:
        The AudioSegment to process.
    min_silence_len:
        Minimum length of silence (ms) that will be stripped (default: 200).
    silence_thresh_db:
        Amplitude threshold below which audio is treated as silence (dBFS).
    keep_silence:
        Milliseconds of silence to retain at the start/end of each chunk.

    Returns
    -------
    AudioSegment
        The processed audio with silence removed.
    """
    chunks = split_on_silence(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh_db,
        keep_silence=keep_silence,
    )
    if not chunks:
        return audio
    return sum(chunks[1:], chunks[0])


def process_script_to_audio(
    script: str,
    output_path: str | Path,
    cost: JobCostAccumulator | None = None,
) -> Path:
    """
    Full pipeline: synthesise speech from *script*, strip silence, and write
    the result as an MP3 file at *output_path*.

    Parameters
    ----------
    script:
        The script text.
    output_path:
        Destination path for the processed MP3 file.
    cost:
        Optional :class:`~app.cost_tracking.JobCostAccumulator`. When provided,
        the TTS cost (priced per input character) is recorded.

    Returns
    -------
    Path
        The path to the written audio file.
    """
    output_path = Path(output_path)
    raw_audio = synthesise_speech(script)
    if cost is not None:
        cost.add_tts(TTS_MODEL, len(script))
    clean_audio = strip_silence(raw_audio)
    clean_audio.export(str(output_path), format="mp3")
    return output_path
