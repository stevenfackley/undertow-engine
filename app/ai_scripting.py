"""
AI Scripting Module
-------------------
Sends a topic/text prompt to an OpenAI chat model to generate a short,
fast-paced video script with a 3-second hook opening and an infinite-loop
ending. Model defaults to gpt-4.1-mini; override with OPENAI_CHAT_MODEL.
"""

from __future__ import annotations

import os

from openai import OpenAI

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


SYSTEM_PROMPT = (
    "You are a viral short-form video scriptwriter. "
    "Write punchy, fast-paced scripts optimised for TikTok and Instagram Reels. "
    "Structure: (1) a single-sentence 3-second hook that grabs attention immediately, "
    "(2) rapid-fire body copy, "
    "(3) an open-ended loop-back ending that forces the viewer to re-watch. "
    "Use short sentences. No filler words. Output ONLY the script text."
)


def generate_script(topic: str, max_tokens: int = 300) -> str:
    """
    Generate a short-form video script for *topic*.

    Uses the model named by ``OPENAI_CHAT_MODEL`` (default ``gpt-4.1-mini``).

    Parameters
    ----------
    topic:
        The subject or raw idea to build the script around.
    max_tokens:
        Upper bound on the script length in tokens.

    Returns
    -------
    str
        The generated script text.
    """
    client = _get_client()
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_CHAT_MODEL", "gpt-4.1-mini"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Write a script about: {topic}"},
        ],
        max_tokens=max_tokens,
        temperature=0.9,
    )
    return response.choices[0].message.content.strip()
