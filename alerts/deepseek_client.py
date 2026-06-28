"""Centralized DeepSeek API client with automatic cost + token tracking.

Two integration paths:
  1. New code: call ``call_deepseek(prompt, ...)`` directly.
  2. Legacy code: existing ``requests.post(<deepseek-url>, ...)`` calls are
     transparently intercepted by the import-time patch installed below — every
     DeepSeek API response has its ``usage`` block captured automatically.

After any number of DeepSeek calls within a logical operation, the next
``send_alert()`` consumes the accumulated usage, computes USD cost, and
appends a footer to the Telegram message.
"""

from __future__ import annotations

import threading
from typing import Any

import requests

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_PRO, DEEPSEEK_MODEL_FAST

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Pricing per 1M tokens, USD. Snapshot 2026-05-09 from api-docs.deepseek.com.
# v4-pro carries a 75% promotional discount until 2026-05-31 15:59 UTC.
# Set DEEPSEEK_PRO_DISCOUNTED=False (or update the dict) after the discount
# expires to use the regular tier.
PRICING = {
    "deepseek-v4-pro": {
        "input_cache_miss": 0.435,    # 75% off (raw $1.74)
        "input_cache_hit":  0.003625, # 75% off (raw $0.0145)
        "output":           0.87,     # 75% off (raw $3.48)
    },
    "deepseek-v4-flash": {
        "input_cache_miss": 0.14,
        "input_cache_hit":  0.0028,
        "output":           0.28,
    },
    "deepseek-chat": {  # legacy alias for v4-flash non-thinking
        "input_cache_miss": 0.14,
        "input_cache_hit":  0.0028,
        "output":           0.28,
    },
    "deepseek-reasoner": {  # legacy alias for v4-flash thinking
        "input_cache_miss": 0.14,
        "input_cache_hit":  0.0028,
        "output":           0.28,
    },
}

# v4-pro / v4-flash are reasoning models that allocate max_tokens between
# internal "thinking" tokens and visible output. Empirically a 700-token budget
# produced 700 reasoning tokens and 0 visible output. 3000 leaves comfortable
# headroom for both.
DEFAULT_MAX_TOKENS = 3000

_lock = threading.Lock()
_pending: list[dict[str, Any]] = []

# Cache file written by alerts/pricing_updater.py
_PRICING_CACHE_FILE = __file__.replace("deepseek_client.py", ".deepseek_pricing.json")


def reload_pricing():
    """Reload PRICING from the cache file written by pricing_updater.

    Called automatically at module import and from pricing_updater after a
    successful refresh. Silently no-ops if the cache file is missing or
    malformed — falls back to the hardcoded values above.
    """
    import json
    import os
    if not os.path.exists(_PRICING_CACHE_FILE):
        return
    try:
        with open(_PRICING_CACHE_FILE) as f:
            data = json.load(f)
        for m in data.get("models", []):
            mid = m.get("id")
            if not mid:
                continue
            PRICING[mid] = {
                "input_cache_miss": float(m.get("input_cache_miss", 0)),
                "input_cache_hit": float(m.get("input_cache_hit", 0)),
                "output": float(m.get("output", 0)),
            }
    except Exception:
        pass


# Apply cached rates at import time if available
reload_pricing()


def _calc_cost(model: str, usage: dict) -> float:
    """USD cost for one API call given its returned usage dict."""
    rates = PRICING.get(model) or PRICING["deepseek-v4-flash"]
    cache_hit = usage.get("prompt_cache_hit_tokens", 0)
    cache_miss = usage.get("prompt_cache_miss_tokens")
    if cache_miss is None:
        # Some responses only have prompt_tokens; treat as full miss
        cache_miss = max(usage.get("prompt_tokens", 0) - cache_hit, 0)
    output = usage.get("completion_tokens", 0)
    return (
        cache_hit  * rates["input_cache_hit"]  / 1_000_000
        + cache_miss * rates["input_cache_miss"] / 1_000_000
        + output     * rates["output"]           / 1_000_000
    )


def _record_usage(model: str | None, usage: dict | None):
    """Append a captured usage dict to the pending accumulator."""
    if not usage:
        return
    entry = dict(usage)
    entry["model"] = model or entry.get("model", "unknown")
    with _lock:
        _pending.append(entry)


def call_deepseek(
    prompt,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.3,
    timeout: int = 30,
    fallback_to_chat: bool = True,
) -> tuple[str, dict]:
    """Make a DeepSeek call and return (content, usage).

    Usage is also recorded into the pending accumulator so the next
    ``send_alert`` can append a cost footer.

    On empty content from a thinking-mode model, automatically retries with
    ``deepseek-chat`` (the deprecated non-thinking alias). Both calls' usage
    is recorded — the user pays for both, and the footer reflects that.
    """
    if model is None:
        model = DEEPSEEK_MODEL_PRO
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = (
        [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
    )
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        # Note: usage is also captured by the patch — no need to record again

        if not content and fallback_to_chat and model != "deepseek-chat":
            payload2 = dict(payload, model="deepseek-chat")
            resp2 = requests.post(DEEPSEEK_URL, headers=headers, json=payload2, timeout=timeout)
            resp2.raise_for_status()
            data2 = resp2.json()
            content = data2["choices"][0]["message"]["content"].strip()
            usage = data2.get("usage", {})

        return content, usage
    except Exception as e:
        return f"(deepseek call failed: {e})", {}


def consume_pending_usage() -> dict | None:
    """Pop and total all pending usage. Returns totals or None if nothing."""
    with _lock:
        if not _pending:
            return None
        items = _pending[:]
        _pending.clear()

    total_input = 0
    total_cache_hit = 0
    total_output = 0
    total_cost = 0.0
    models = set()
    for u in items:
        m = u.get("model") or "deepseek-v4-flash"
        models.add(m)
        total_input += u.get("prompt_tokens", 0)
        total_cache_hit += u.get("prompt_cache_hit_tokens", 0)
        total_output += u.get("completion_tokens", 0)
        total_cost += _calc_cost(m, u)
    return {
        "calls": len(items),
        "input": total_input,
        "cache_hit": total_cache_hit,
        "output": total_output,
        "cost_usd": total_cost,
        "models": sorted(models),
    }


def format_usage_footer(totals: dict) -> str:
    """One-line footer summarizing API spend for a single Telegram message."""
    cost = totals["cost_usd"]
    cost_str = f"${cost:.4f}" if cost >= 0.0001 else f"${cost:.6f}"
    return (
        f"API: {cost_str} | {totals['input']:,} in "
        f"({totals['cache_hit']:,} cached) / {totals['output']:,} out | "
        f"{totals['calls']} call{'s' if totals['calls'] > 1 else ''}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Legacy-call interceptor: monkey-patch requests.post to capture usage from
# any direct calls to api.deepseek.com that haven't migrated to call_deepseek().
# ─────────────────────────────────────────────────────────────────────────────

_PATCH_INSTALLED = False


def _install_patch():
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    original_post = requests.post

    def _patched_post(url, *args, **kwargs):
        resp = original_post(url, *args, **kwargs)
        try:
            if "api.deepseek.com" in str(url):
                # Don't disturb existing call's response handling; just observe
                if resp.status_code == 200:
                    body = kwargs.get("json") or {}
                    model = body.get("model") if isinstance(body, dict) else None
                    try:
                        data = resp.json()
                        usage = data.get("usage")
                        if usage:
                            _record_usage(model, usage)
                    except Exception:
                        pass
        except Exception:
            pass
        return resp

    requests.post = _patched_post  # type: ignore[assignment]
    _PATCH_INSTALLED = True


_install_patch()
