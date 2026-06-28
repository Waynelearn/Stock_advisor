"""DeepSeek provider — fast/cheap tier for high-frequency monitors and extraction.

Wraps the DeepSeek chat-completions HTTP API behind the provider-agnostic
``LLMResponse``. Reuses the pricing table and cost math already maintained in
``alerts.deepseek_client`` so there is a single source of truth for rates, and
keeps the legacy ``send_alert`` cost footer working (the request still flows
through the patched ``requests.post`` that accumulates pending usage).
"""
from __future__ import annotations

import time

import requests

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST
from .base import LLMResponse, ToolCall, Usage

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MAX_TOKENS = 3000


def _usage_from_payload(model: str, usage: dict) -> Usage:
    """Build a Usage (incl. USD cost) from a DeepSeek usage block."""
    from ..deepseek_client import _calc_cost  # reuse single pricing source

    cache_hit = usage.get("prompt_cache_hit_tokens", 0)
    cache_miss = usage.get("prompt_cache_miss_tokens")
    if cache_miss is None:
        cache_miss = max(usage.get("prompt_tokens", 0) - cache_hit, 0)
    return Usage(
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cache_read_tokens=cache_hit,
        cache_write_tokens=0,  # DeepSeek auto-caches; no explicit write line
        cost_usd=_calc_cost(model, usage),
    )


def complete(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.3,
    tools: list[dict] | None = None,
    timeout: int = 30,
    max_retries: int = 2,
) -> LLMResponse:
    """One DeepSeek completion. Retries transient failures with backoff."""
    model = model or DEEPSEEK_MODEL_FAST
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]["message"]
            text = (choice.get("content") or "").strip()
            usage = _usage_from_payload(model, data.get("usage", {}))

            tool_calls = []
            for tc in choice.get("tool_calls") or []:
                fn = tc.get("function", {})
                import json as _json
                try:
                    args = _json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))

            return LLMResponse(
                text=text, model=model, provider="deepseek", usage=usage,
                tool_calls=tool_calls,
                stop_reason=data["choices"][0].get("finish_reason"),
                raw=data,
            )
        except Exception as e:  # noqa: BLE001 — convert any failure into a typed result
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            # Don't retry client errors (4xx other than 429)
            if status is not None and 400 <= status < 500 and status != 429:
                break
            if attempt < max_retries:
                time.sleep(min(2 ** attempt, 8))

    return LLMResponse.failure(provider="deepseek", model=model, error=str(last_err))
