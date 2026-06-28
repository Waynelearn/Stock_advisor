"""Structured trace log for every LLM call — the foundation for evals + cost review.

Each completion appends one JSON line to ``alerts/.llm_trace.jsonl`` with enough
detail to debug behavior, attribute cost, and later replay/score outputs. Writing
is best-effort: a tracing failure must never break an alert.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

from .base import LLMResponse

_TRACE_FILE = os.path.join(os.path.dirname(__file__), os.pardir, ".llm_trace.jsonl")
_lock = threading.Lock()

# Rotate when the trace exceeds this size (bytes) to keep it bounded on disk.
_MAX_BYTES = 5_000_000


def _rotate_if_needed():
    try:
        if os.path.exists(_TRACE_FILE) and os.path.getsize(_TRACE_FILE) > _MAX_BYTES:
            os.replace(_TRACE_FILE, _TRACE_FILE + ".1")
    except OSError:
        pass


def trace(
    resp: LLMResponse,
    *,
    label: str,
    tier: str,
    prompt_chars: int,
    extra: dict | None = None,
):
    """Append one trace record for a completed (or failed) LLM call."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "tier": tier,
        "provider": resp.provider,
        "model": resp.model,
        "ok": resp.ok,
        "error": resp.error,
        "latency_ms": resp.latency_ms,
        "prompt_chars": prompt_chars,
        "tokens_in": resp.usage.input_tokens,
        "tokens_out": resp.usage.output_tokens,
        "cache_read": resp.usage.cache_read_tokens,
        "cache_write": resp.usage.cache_write_tokens,
        "cost_usd": round(resp.usage.cost_usd, 6),
        "stop_reason": resp.stop_reason,
        "tool_calls": [tc.name for tc in resp.tool_calls],
    }
    if extra:
        record["extra"] = extra
    try:
        with _lock:
            _rotate_if_needed()
            with open(_TRACE_FILE, "a") as f:
                f.write(json.dumps(record) + "\n")
    except Exception:
        pass


class _Timer:
    """Context manager that measures wall-clock latency in ms."""
    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.monotonic() - self._t0) * 1000)
        return False


def timer() -> _Timer:
    return _Timer()
