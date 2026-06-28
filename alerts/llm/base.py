"""Provider-agnostic types for the unified LLM client.

Every provider (DeepSeek, Anthropic) returns an ``LLMResponse``. Callers never
see raw HTTP or SDK objects unless they ask for ``.raw``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Usage:
    """Token + cost accounting for a single completion."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


@dataclass
class ToolCall:
    """A tool the model wants the harness to run. Provider-agnostic shape."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """The single return type for every completion across every provider."""
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    ok: bool = True
    error: str | None = None
    latency_ms: int = 0
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @classmethod
    def failure(cls, *, provider: str, model: str, error: str) -> "LLMResponse":
        return cls(text="", model=model, provider=provider, ok=False, error=error)
