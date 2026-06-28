"""Anthropic (Claude) provider — reasoning tier for grounded, multi-step work.

Used for the roundtable/committee, daily/weekend briefings, post-mortems, and the
interactive Q&A agent (Phase 2 tool loop). Built on the official ``anthropic`` SDK
per the current API surface:

  * adaptive thinking (``thinking={"type": "adaptive"}``) — no ``budget_tokens``
  * ``output_config.effort`` for depth; no ``temperature``/``top_p`` (they 400)
  * prompt caching via ``cache_control`` on the stable system prefix
  * native tool use + structured outputs (``output_config.format``)
"""
from __future__ import annotations

from typing import Any

from ..config import ANTHROPIC_API_KEY, LLM_REASONING_MODEL
from .base import LLMResponse, ToolCall, Usage

DEFAULT_MAX_TOKENS = 4096
# Above this, the SDK requires streaming to avoid HTTP timeouts.
_STREAM_THRESHOLD = 16000

# USD per 1M tokens. Cache reads ~0.1x input; cache writes ~1.25x input (5m TTL).
_PRICING = {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
}

_client = None


def _get_client():
    """Lazily construct the SDK client so import never fails without a key/SDK."""
    global _client
    if _client is None:
        import anthropic  # imported lazily — optional dependency
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
    return _client


def _cost(model: str, usage) -> float:
    rates = _PRICING.get(model) or _PRICING["claude-opus-4-8"]
    inp = rates["input"] / 1_000_000
    out = rates["output"] / 1_000_000
    fresh = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    return fresh * inp + cache_read * inp * 0.1 + cache_write * inp * 1.25 + output * out


def _build_system(system: str | None, cache_system: bool) -> Any:
    if not system:
        return None
    block: dict = {"type": "text", "text": system}
    if cache_system:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def complete(
    messages: list[dict],
    *,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    effort: str = "high",
    tools: list[dict] | None = None,
    schema: dict | None = None,
    cache_system: bool = True,
    timeout: int | None = None,
) -> LLMResponse:
    """One Claude completion. The SDK handles retry/backoff (429/5xx) internally."""
    model = model or LLM_REASONING_MODEL
    max_tokens = max_tokens or DEFAULT_MAX_TOKENS

    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
    }
    sys_blocks = _build_system(system, cache_system)
    if sys_blocks is not None:
        kwargs["system"] = sys_blocks
    if tools:
        kwargs["tools"] = tools

    try:
        client = _get_client()
        if timeout is not None:
            client = client.with_options(timeout=timeout)

        if max_tokens > _STREAM_THRESHOLD:
            with client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        else:
            msg = client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        return LLMResponse.failure(provider="anthropic", model=model, error=str(e))

    # Refusal: content may be empty — surface it rather than crashing on content[0].
    if getattr(msg, "stop_reason", None) == "refusal":
        return LLMResponse(
            text="", model=model, provider="anthropic", stop_reason="refusal",
            ok=False, error="model refused", raw=msg,
            usage=Usage(cost_usd=_cost(model, msg.usage)),
        )

    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in msg.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))

    usage = Usage(
        input_tokens=getattr(msg.usage, "input_tokens", 0) or 0,
        output_tokens=getattr(msg.usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
        cost_usd=_cost(model, msg.usage),
    )
    return LLMResponse(
        text="".join(text_parts).strip(), model=model, provider="anthropic",
        usage=usage, tool_calls=tool_calls, stop_reason=msg.stop_reason, raw=msg,
    )
