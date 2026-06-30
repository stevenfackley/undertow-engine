"""
Per-job OpenAI cost tracking
----------------------------
Pure helpers that turn OpenAI usage metrics into an estimated USD cost, plus a
small accumulator the worker fills in over the course of one job so the total
can be emitted in the structured ``job_cost`` log line and the Celery result
record (the per-job charge-back unit).

Pricing is expressed in USD and kept as module constants with sensible defaults.
Override the headline rates without a code change via env vars (read once at
import time):

* ``OPENAI_PRICE_CHAT_INPUT_PER_1M``  — default per-model table below
* ``OPENAI_PRICE_CHAT_OUTPUT_PER_1M``
* ``OPENAI_PRICE_TTS_PER_1M_CHARS``   — default 15.00 (tts-1)
* ``OPENAI_PRICE_WHISPER_PER_MIN``    — default 0.006 (whisper-1)

All money math is pure and unit-tested; nothing here performs IO.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Default pricing (USD). Per-1M-token rates for chat; per-1M-char for TTS;
# per-minute for transcription. Values reflect standard list pricing and can be
# overridden per the env vars documented above.
# ---------------------------------------------------------------------------

# model -> (input_per_1m, output_per_1m)
DEFAULT_CHAT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

# Fallback when a model is not in the table (use gpt-4.1-mini's rate).
_FALLBACK_CHAT_RATE: tuple[float, float] = (0.40, 1.60)

TTS_RATE_PER_1M_CHARS: float = float(os.environ.get("OPENAI_PRICE_TTS_PER_1M_CHARS", "15.0"))
WHISPER_RATE_PER_MIN: float = float(os.environ.get("OPENAI_PRICE_WHISPER_PER_MIN", "0.006"))


def _chat_rate(model: str) -> tuple[float, float]:
    inp = os.environ.get("OPENAI_PRICE_CHAT_INPUT_PER_1M")
    out = os.environ.get("OPENAI_PRICE_CHAT_OUTPUT_PER_1M")
    if inp is not None and out is not None:
        return float(inp), float(out)
    return DEFAULT_CHAT_PRICING.get(model, _FALLBACK_CHAT_RATE)


def chat_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimated USD for one chat completion call."""
    inp_rate, out_rate = _chat_rate(model)
    return (prompt_tokens / 1_000_000) * inp_rate + (completion_tokens / 1_000_000) * out_rate


def tts_cost_usd(n_chars: int, rate_per_1m: float = TTS_RATE_PER_1M_CHARS) -> float:
    """Estimated USD for a TTS synthesis of *n_chars* input characters."""
    return (n_chars / 1_000_000) * rate_per_1m


def whisper_cost_usd(seconds: float, rate_per_min: float = WHISPER_RATE_PER_MIN) -> float:
    """Estimated USD for transcribing *seconds* of audio."""
    return (seconds / 60.0) * rate_per_min


@dataclass
class JobCostAccumulator:
    """Collects per-step OpenAI usage for a single job and totals the cost.

    The worker creates one of these per job, threads it through the AI calls,
    and emits ``as_dict()`` in the ``job_cost`` log and the result record.
    Pure state — no IO.
    """

    line_items: list[dict] = field(default_factory=list)

    def add_chat(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        cost = chat_cost_usd(model, prompt_tokens, completion_tokens)
        self.line_items.append(
            {
                "step": "chat",
                "model": model,
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "usd": round(cost, 6),
            }
        )

    def add_tts(self, model: str, n_chars: int) -> None:
        cost = tts_cost_usd(n_chars)
        self.line_items.append(
            {
                "step": "tts",
                "model": model,
                "chars": int(n_chars),
                "usd": round(cost, 6),
            }
        )

    def add_whisper(self, model: str, seconds: float) -> None:
        cost = whisper_cost_usd(seconds)
        self.line_items.append(
            {
                "step": "whisper",
                "model": model,
                "seconds": round(float(seconds), 3),
                "usd": round(cost, 6),
            }
        )

    @property
    def total_usd(self) -> float:
        return round(sum(item["usd"] for item in self.line_items), 6)

    @property
    def total_tokens(self) -> int:
        return sum(
            item.get("prompt_tokens", 0) + item.get("completion_tokens", 0)
            for item in self.line_items
        )

    def as_dict(self) -> dict:
        return {
            "total_usd": self.total_usd,
            "total_tokens": self.total_tokens,
            "line_items": list(self.line_items),
        }
