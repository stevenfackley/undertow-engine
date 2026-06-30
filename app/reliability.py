"""
Reliability primitives
-----------------------
Pure, side-effect-free helpers that decide how a failed Celery task should be
handled:

* :func:`is_transient_error` — classify an exception as *transient* (worth a
  bounded retry: network blips, HTTP 429/5xx, OpenAI rate limits/timeouts) or
  *permanent* (malformed input / programming errors that will never succeed on
  a retry and must be dead-lettered immediately).
* :func:`compute_backoff` — deterministic exponential backoff for the retry
  countdown.
* :class:`PermanentJobError` — raise this from pipeline code to force an
  immediate dead-letter regardless of the exception class.

Kept free of Celery/IO imports so it can be unit-tested in isolation, mirroring
the pure-function style already used for the ffmpeg/ASS compositing path.
"""

from __future__ import annotations

import httpx

try:  # openai is a hard dependency, but guard so this module imports anywhere
    import openai

    _OPENAI_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
        openai.APIConnectionError,  # APITimeoutError subclasses this
    )
except Exception:  # pragma: no cover - openai always present in prod
    _OPENAI_CONNECTION_ERRORS = ()

try:
    from celery.exceptions import SoftTimeLimitExceeded

    _TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (SoftTimeLimitExceeded,)
except Exception:  # pragma: no cover
    _TIMEOUT_ERRORS = ()


class PermanentJobError(Exception):
    """Marker for failures that must be dead-lettered without any retry.

    Raise this for un-processable input (e.g. a missing background file, a
    payload that fails validation) so the job is captured in the DLQ instead of
    burning the full retry budget first.
    """


# Built-in exceptions that signal bad input or a programming error. Retrying
# these is pointless — they are permanent by definition.
_PERMANENT_BUILTINS: tuple[type[BaseException], ...] = (
    ValueError,
    TypeError,
    KeyError,
    IndexError,
    AttributeError,
    FileNotFoundError,
    NotADirectoryError,
    IsADirectoryError,
    NotImplementedError,
    AssertionError,
)


def _status_code(exc: BaseException) -> int | None:
    """Best-effort extraction of an HTTP status code from *exc*.

    Handles both the ``status_code`` attribute used by the OpenAI SDK
    (``APIStatusError`` and subclasses) and the nested ``response.status_code``
    used by ``httpx.HTTPStatusError``.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def is_transient_error(exc: BaseException) -> bool:
    """Return ``True`` if *exc* is worth a bounded retry, ``False`` if permanent.

    Decision order:

    1. :class:`PermanentJobError` and bad-input builtins → permanent.
    2. Anything carrying an HTTP status → 429 or 5xx are transient, other 4xx
       are permanent.
    3. Connection / timeout style errors (httpx transport, OpenAI connection,
       Celery soft-timeout, builtin ``ConnectionError``/``TimeoutError``) →
       transient.
    4. Unknown exceptions default to transient — but the caller bounds total
       attempts via ``max_retries``, so an unrecognised error still ends up in
       the DLQ rather than looping forever.
    """
    if isinstance(exc, PermanentJobError):
        return False
    if isinstance(exc, _PERMANENT_BUILTINS):
        return False

    code = _status_code(exc)
    if code is not None:
        return code == 429 or code >= 500

    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if _OPENAI_CONNECTION_ERRORS and isinstance(exc, _OPENAI_CONNECTION_ERRORS):
        return True
    if _TIMEOUT_ERRORS and isinstance(exc, _TIMEOUT_ERRORS):
        return True

    # Unrecognised — treat as transient but bounded by the retry budget.
    return True


def compute_backoff(retries: int, base: float = 30.0, cap: float = 300.0) -> float:
    """Exponential backoff countdown (seconds) for retry number *retries*.

    ``retries`` is the zero-based count of retries already performed
    (``self.request.retries``). Deterministic (no jitter) so it is trivially
    unit-testable; the worker adds jitter separately when scheduling.
    """
    if retries < 0:
        retries = 0
    delay = base * (2**retries)
    return float(min(cap, delay))
