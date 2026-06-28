"""Unified LLM entrypoint — one ``complete()`` for the whole codebase.

Replaces ~27 hand-rolled ``requests.post(<deepseek>)`` call sites. Routes by
logical *tier* (or an explicit model) to the right provider, normalizes the
result to ``LLMResponse``, and traces every call for cost + eval.

Tiers
-----
``reasoning`` — Claude (grounded multi-step: roundtable, briefings, post-mortem, Q&A)
``fast``      — DeepSeek flash (high-frequency monitors, news/web summarization)
``extract``   — DeepSeek flash, low temperature (structured-data extraction)

Routing is config-driven (``LLM_TIER_MODELS`` in config.py) and falls back to
DeepSeek if Claude is unavailable (no key / SDK), so the system degrades rather
than breaking.
"""
from __future__ import annotations

from ..config import (
    ANTHROPIC_API_KEY, LLM_TIER_MODELS, LLM_FALLBACK_TIER, LLM_FALLBACK_MODELS,
)
from . import anthropic_provider, deepseek
from . import tracing
from .base import LLMResponse


def _provider_for_model(model: str) -> str:
    if model.startswith("claude") or model.startswith("anthropic."):
        return "anthropic"
    return "deepseek"


def _claude_available() -> bool:
    if not ANTHROPIC_API_KEY:
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def complete(
    prompt,
    *,
    tier: str = "reasoning",
    model: str | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    effort: str = "high",
    temperature: float = 0.3,
    tools: list[dict] | None = None,
    schema: dict | None = None,
    cache_system: bool = True,
    timeout: int | None = None,
    label: str = "unlabeled",
) -> LLMResponse:
    """Run one completion through the routed provider and trace it.

    ``prompt`` may be a string (becomes a single user turn) or a list of message
    dicts. ``tier`` selects the model unless ``model`` is given explicitly.
    """
    if model is None:
        model = LLM_TIER_MODELS.get(tier, LLM_TIER_MODELS[LLM_FALLBACK_TIER])

    provider = _provider_for_model(model)

    # Graceful degradation: no Claude key/SDK → DeepSeek v4 (reasoning→pro,
    # monitors→flash), so the system runs fully on DeepSeek by default.
    if provider == "anthropic" and not _claude_available():
        model = LLM_FALLBACK_MODELS.get(tier, LLM_TIER_MODELS[LLM_FALLBACK_TIER])
        provider = "deepseek"

    messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
    prompt_chars = sum(len(str(m.get("content", ""))) for m in messages) + len(system or "")

    with tracing.timer() as t:
        if provider == "anthropic":
            resp = anthropic_provider.complete(
                messages, model=model, system=system, max_tokens=max_tokens,
                effort=effort, tools=tools, schema=schema,
                cache_system=cache_system, timeout=timeout,
            )
        else:
            # DeepSeek has no separate system role concept here; fold it in.
            if system:
                messages = [{"role": "system", "content": system}, *messages]
            resp = deepseek.complete(
                messages, model=model, max_tokens=max_tokens,
                temperature=temperature, tools=tools,
                timeout=timeout or 30,
            )
    resp.latency_ms = t.ms

    tracing.trace(resp, label=label, tier=tier, prompt_chars=prompt_chars)
    return resp


def ask(
    prompt,
    *,
    tier: str = "fast",
    system: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.3,
    effort: str = "high",
    label: str = "unlabeled",
    fallback: str = "",
) -> str:
    """Convenience wrapper returning just the text (or ``fallback`` on failure).

    Collapses the old ``requests.post`` + ``raise_for_status`` + JSON-parse +
    try/except boilerplate at every monitor call site into a single line.
    """
    r = complete(
        prompt, tier=tier, system=system, max_tokens=max_tokens,
        temperature=temperature, effort=effort, label=label,
    )
    return r.text if (r.ok and r.text) else fallback
