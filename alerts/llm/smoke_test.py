"""Offline smoke test for the unified LLM layer. No network / API keys required.

Run: .venv/bin/python -m alerts.llm.smoke_test
"""
from __future__ import annotations

import os
import sys
import types

# Ensure project root on path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from alerts.llm import complete, LLMResponse, Usage  # noqa: E402
from alerts.llm import client as llm_client  # noqa: E402
from alerts.llm import anthropic_provider, deepseek  # noqa: E402

failures = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        failures.append(name)


print("1. Usage arithmetic + cost aggregation")
u = Usage(input_tokens=100, output_tokens=50, cost_usd=0.01) + Usage(input_tokens=10, cost_usd=0.002)
check("Usage adds tokens", u.input_tokens == 110 and u.output_tokens == 50)
check("Usage adds cost", abs(u.cost_usd - 0.012) < 1e-9)

print("2. provider routing by model id")
check("claude -> anthropic", llm_client._provider_for_model("claude-opus-4-8") == "anthropic")
check("deepseek -> deepseek", llm_client._provider_for_model("deepseek-v4-flash") == "deepseek")

print("3. Claude cost math (opus 4.8 rates, cache-aware)")
fake_usage = types.SimpleNamespace(
    input_tokens=1_000_000, output_tokens=1_000_000,
    cache_read_input_tokens=0, cache_creation_input_tokens=0,
)
cost = anthropic_provider._cost("claude-opus-4-8", fake_usage)
check("1M in + 1M out = $30", abs(cost - 30.0) < 1e-6)
fake_cached = types.SimpleNamespace(
    input_tokens=0, output_tokens=0,
    cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0,
)
check("1M cache-read = $0.50 (0.1x input)", abs(anthropic_provider._cost("claude-opus-4-8", fake_cached) - 0.5) < 1e-6)

print("4. DeepSeek usage parsing")
du = deepseek._usage_from_payload("deepseek-v4-flash", {
    "prompt_tokens": 200, "completion_tokens": 80, "prompt_cache_hit_tokens": 50,
})
check("input tokens parsed", du.input_tokens == 200 and du.output_tokens == 80)
check("cache read parsed", du.cache_read_tokens == 50)
check("cost computed > 0", du.cost_usd > 0)

print("5. routing + tracing (providers stubbed, no network)")
calls = {}

def fake_anthropic(messages, **kw):
    calls["anthropic"] = kw
    return LLMResponse(text="claude-says-hi", model=kw.get("model"), provider="anthropic",
                       usage=Usage(input_tokens=5, output_tokens=3, cost_usd=0.001))

def fake_deepseek(messages, **kw):
    calls["deepseek"] = kw
    return LLMResponse(text="deepseek-says-hi", model=kw.get("model"), provider="deepseek",
                       usage=Usage(input_tokens=4, output_tokens=2, cost_usd=0.0001))

anthropic_provider.complete = fake_anthropic
deepseek.complete = fake_deepseek
llm_client.anthropic_provider.complete = fake_anthropic
llm_client.deepseek.complete = fake_deepseek

# Force-enable the Claude path regardless of local env
llm_client._claude_available = lambda: True

r1 = complete("hello", tier="reasoning", label="smoke_reasoning", system="You are a test.")
check("reasoning tier -> anthropic", r1.provider == "anthropic" and r1.text == "claude-says-hi")
check("system passed to anthropic", calls["anthropic"].get("system") == "You are a test.")
check("latency recorded", r1.latency_ms >= 0)

r2 = complete("hi", tier="fast", label="smoke_fast")
check("fast tier -> deepseek", r2.provider == "deepseek" and r2.text == "deepseek-says-hi")

# Fallback: Claude unavailable -> reasoning routed to deepseek
llm_client._claude_available = lambda: False
r3 = complete("hi", tier="reasoning", label="smoke_fallback")
check("reasoning falls back to deepseek when Claude down", r3.provider == "deepseek")

print("6. trace file written")
from alerts.llm import tracing  # noqa: E402
check("trace file exists", os.path.exists(tracing._TRACE_FILE))

print("7. Phase-3 grounding guardrail")
from alerts.llm import guardrail, schemas  # noqa: E402

# Ledger as run_agent produces it: result is a JSON string.
ledger = [
    {"tool": "position_pnl", "args": {},
     "result": '{"underlying_price": 120.5, "current_pnl_usd": -1700, "breakeven": 118.25}'},
]
rep = guardrail.check(
    "MU is at $120.50, P&L is a loss of $1,700, breakeven $118.25.", ledger)
check("grounded numbers pass", rep.ok and rep.checked == 3 and rep.grounded == 3)

rep2 = guardrail.check("MU is at $120.50 but will rocket to $250.00 for +$9,999.", ledger)
check("invented numbers flagged", not rep2.ok and len(rep2.unsourced) == 2)

rep3 = guardrail.check("Expiry is in 14 days across 4 contracts.", ledger)
check("trivia (bare small ints) ignored", rep3.checked == 0 and rep3.ok)

q_allowed = guardrail.check("If MU hits $135.00 you profit.", ledger, question="what if MU goes to 135?")
check("question numbers count as grounded", q_allowed.ok)

out, rep4 = guardrail.apply("You should close the spread now.", ledger)
check("disclaimer stamped on advice", rep4.disclaimer_added and guardrail.RISK_DISCLAIMER in out)

out2, rep5 = guardrail.apply("MU sits near $999.99 today.", ledger)
check("unsourced number annotated in output", "Unverified figure" in out2 and not rep5.ok)

print("8. structured-output schema parsing")
ok_obj = schemas.parse('{"stance": "hold", "conviction": 4, "rationale": "stable"}',
                       schemas.POSITION_ASSESSMENT)
check("valid structured output parses", ok_obj["stance"] == "hold")
try:
    schemas.parse('{"conviction": 4}', schemas.POSITION_ASSESSMENT)
    check("missing required key raises", False)
except ValueError:
    check("missing required key raises", True)
try:
    schemas.parse("not json", schemas.TRADE_DECISION)
    check("non-JSON raises", False)
except ValueError:
    check("non-JSON raises", True)

print()
if failures:
    print(f"FAILED ({len(failures)}): {failures}")
    sys.exit(1)
print("ALL SMOKE TESTS PASSED")
