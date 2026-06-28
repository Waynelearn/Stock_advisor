"""Grounded Q&A agent — the tool-calling executor.

This is the Phase-2 core: instead of stuffing a static context blob into one
prompt and letting the model free-write numbers, the model is given real tools
(``agent_tools``) and must call them. Every number in the answer therefore comes
from deterministic code, not the model's imagination.

``run_agent`` returns the answer **and a claims ledger** — the ordered list of
tool calls and their results. The ledger is the grounding record that the
Phase-4 verifier agent will check the answer against.

Two execution paths:
  * Claude available  → a real Anthropic tool-use loop (model decides what to
    fetch, sees results, continues until it answers).
  * Claude unavailable → a grounded single-shot on DeepSeek: a default bundle of
    tools is pre-fetched and injected as context, so answers stay grounded even
    without tool-calling.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import LLM_REASONING_MODEL
from . import agent_tools, guardrail, tracing
from .base import Usage
from .client import _claude_available, ask

DEFAULT_SYSTEM = (
    "You are an expert options-trading assistant for a short-term trader who "
    "holds an MU bull-call-spread. You have tools that return REAL, live market "
    "data and EXACT position math.\n\n"
    "RULES:\n"
    "- NEVER state a price, P&L, probability, volatility, or Greek from memory. "
    "Call the relevant tool and use its exact returned value.\n"
    "- For position/P&L questions call position_pnl. For 'what if X' call "
    "simulate_position. For odds call probability_above / expected_move.\n"
    "- If a number you need is not available from any tool, say so explicitly "
    "rather than guessing.\n"
    "- Be concise and actionable: under 200 words, plain text (no markdown)."
)

_MAX_STEPS = 6
_STEP_MAX_TOKENS = 1500
# Tools pre-fetched for the no-Claude grounded fallback.
_FALLBACK_BUNDLE = [
    ("position_pnl", {}),
    ("upcoming_catalysts", {}),
    ("options_summary", {"ticker": "MU"}),
    ("recent_alerts", {"n": 5}),
]


@dataclass
class AgentResult:
    text: str
    ledger: list[dict] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    steps: int = 0
    model: str = ""
    ok: bool = True
    error: str | None = None
    # Phase-3 grounding guardrail: True once every numeric claim is tool-backed.
    verified: bool = True
    unsourced: list[dict] = field(default_factory=list)


def run_agent(
    question: str,
    *,
    system: str | None = None,
    max_steps: int = _MAX_STEPS,
    effort: str = "high",
    label: str = "agent",
) -> AgentResult:
    """Answer ``question`` by letting the model call grounded tools."""
    system = system or DEFAULT_SYSTEM
    with tracing.timer() as t:
        if _claude_available():
            res = _run_claude_loop(question, system, max_steps, effort)
        else:
            res = _run_grounded_fallback(question, system)
        # Phase-3 guardrail: flag any numeric claim not backed by the ledger and
        # stamp a risk envelope on actionable advice before the answer ships.
        if res.text:
            res.text, report = guardrail.apply(res.text, res.ledger, question=question)
            res.verified = report.ok
            res.unsourced = report.unsourced
    _trace(res, label=label, latency_ms=t.ms, question=question)
    return res


# ── Claude tool-use loop ─────────────────────────────────────────────────────

def _run_claude_loop(question: str, system: str, max_steps: int, effort: str) -> AgentResult:
    from . import anthropic_provider
    model = LLM_REASONING_MODEL
    tools = agent_tools.anthropic_tool_specs()
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": question}]
    ledger: list[dict] = []
    total = Usage()

    try:
        client = anthropic_provider._get_client()
    except Exception as e:  # noqa: BLE001 — SDK/key missing → degrade
        return _run_grounded_fallback(question, system, note=f"(claude unavailable: {e})")

    final_text = ""
    steps = 0
    for steps in range(1, max_steps + 1):
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=_STEP_MAX_TOKENS,
                system=system_blocks,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
            )
        except Exception as e:  # noqa: BLE001
            return AgentResult(text="", ledger=ledger, usage=total, steps=steps,
                               model=model, ok=False, error=str(e))

        total = total + _usage_of(model, msg)

        if msg.stop_reason == "refusal":
            return AgentResult(text="", ledger=ledger, usage=total, steps=steps,
                               model=model, ok=False, error="model refused")

        tool_uses = [b for b in msg.content if b.type == "tool_use"]
        text_now = "".join(b.text for b in msg.content if b.type == "text").strip()

        if not tool_uses:  # model is done
            final_text = text_now
            break

        # Execute requested tools, append results, continue the loop.
        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for tu in tool_uses:
            out = agent_tools.dispatch(tu.name, dict(tu.input))
            ledger.append({"tool": tu.name, "args": dict(tu.input), "result": out})
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
        messages.append({"role": "user", "content": results})
    else:
        # Ran out of steps without a final text answer — ask for a wrap-up.
        final_text = text_now or "(reached step limit before answering)"

    return AgentResult(text=final_text, ledger=ledger, usage=total, steps=steps,
                       model=model, ok=bool(final_text))


def _usage_of(model: str, msg) -> Usage:
    from .anthropic_provider import _cost
    u = msg.usage
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cost_usd=_cost(model, u),
    )


# ── DeepSeek grounded fallback ───────────────────────────────────────────────

def _run_grounded_fallback(question: str, system: str, note: str = "") -> AgentResult:
    """No tool-calling: pre-fetch a default bundle, inject as context, one shot."""
    ledger = []
    lines = []
    for name, args in _FALLBACK_BUNDLE:
        out = agent_tools.dispatch(name, args)
        ledger.append({"tool": name, "args": args, "result": out})
        lines.append(f"{name}({args}) = {out}")
    grounded = "GROUNDED DATA (use these exact numbers, do not invent others):\n" + "\n".join(lines)
    prompt = f"{grounded}\n\nUSER QUESTION: {question}"
    text = ask(prompt, tier="reasoning", system=system, max_tokens=_STEP_MAX_TOKENS,
               label="agent.fallback", fallback="Analysis unavailable.")
    if note:
        text = f"{text}\n{note}"
    return AgentResult(text=text, ledger=ledger, steps=1,
                       model="deepseek (grounded fallback)", ok=bool(text))


def _trace(res: AgentResult, *, label: str, latency_ms: int, question: str):
    from .base import LLMResponse
    synthetic = LLMResponse(
        text=res.text, model=res.model, provider="agent",
        usage=res.usage, stop_reason=None, ok=res.ok, error=res.error,
        latency_ms=latency_ms,
    )
    tracing.trace(
        synthetic, label=label, tier="reasoning", prompt_chars=len(question),
        extra={
            "steps": res.steps,
            "tools_called": [c["tool"] for c in res.ledger],
            "verified": res.verified,
            "unsourced": len(res.unsourced),
        },
    )
