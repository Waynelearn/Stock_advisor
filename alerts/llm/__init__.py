"""Unified, provider-abstracted LLM layer.

Public API:
    from alerts.llm import complete, LLMResponse
    resp = complete("...", tier="reasoning", label="daily_briefing")
    print(resp.text, resp.usage.cost_usd)
"""
from .base import LLMResponse, ToolCall, Usage
from .client import complete, ask
from . import guardrail, schemas

__all__ = [
    "complete", "ask", "run_agent", "LLMResponse", "ToolCall", "Usage",
    "guardrail", "schemas",
]


def run_agent(*args, **kwargs):
    """Lazy re-export of the grounded agent (avoids importing tools/ at package load)."""
    from .agent import run_agent as _run_agent
    return _run_agent(*args, **kwargs)
