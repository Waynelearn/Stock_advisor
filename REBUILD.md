# AI-Agent Rebuild — Progress & Handoff

**Goal:** modernize the MU advisor's AI layer to current agent best practices —
unified LLM client, grounded tool-calling, structured outputs + guardrails, a
multi-agent verifier loop, prompt caching, and evals.

**Model strategy (decided):** Claude (`claude-opus-4-8`) for reasoning-heavy work;
DeepSeek v4 for high-frequency monitors. **If `ANTHROPIC_API_KEY` is absent, the
whole system falls back to DeepSeek v4** (reasoning→`deepseek-v4-pro`,
monitors→`deepseek-v4-flash`) — nothing breaks without a Claude key.

Status legend: ✅ done & verified · 🔜 next · ⬜ not started

---

## ✅ Phase 1 — Unified LLM layer  (COMPLETE)

New package **`alerts/llm/`**:

| File | Purpose |
|---|---|
| `base.py` | Provider-agnostic types: `LLMResponse`, `Usage`, `ToolCall`. One return type everywhere. |
| `deepseek.py` | DeepSeek provider — retries/backoff, reuses `deepseek_client._calc_cost` for pricing. |
| `anthropic_provider.py` | Claude provider on the official SDK. Current API: `thinking={"type":"adaptive"}`, `output_config.effort`, prompt-cache `cache_control`, tools, structured outputs, refusal handling. **No** `temperature`/`budget_tokens` (they 400 on Opus 4.8). Cost math incl. cache tiers. |
| `client.py` | `complete(prompt, tier=…, label=…)` router + `ask(...)` convenience wrapper. Routes by tier, falls back to DeepSeek if Claude unavailable, traces every call. |
| `tracing.py` | Appends one JSON line per call to `alerts/.llm_trace.jsonl` (tokens, cost, latency, tier, ok/error, tools). Foundation for evals/cost review. Rotates at 5 MB. |
| `smoke_test.py` | Offline tests (no network): routing, cost math, fallback, tracing. Run: `.venv/bin/python -m alerts.llm.smoke_test` |

**Config (`alerts/config.py`):** added `ANTHROPIC_API_KEY`, `LLM_REASONING_MODEL`,
`LLM_TIER_MODELS` (`reasoning`/`fast`/`extract`), `LLM_FALLBACK_MODELS`.

**Tiers:** `reasoning`→Claude (fallback `deepseek-v4-pro`), `fast`/`extract`→`deepseek-v4-flash`.

**Migrated all 49 raw `requests.post` DeepSeek call sites** to `complete()`/`ask()`:
- **fast tier** (`ask(tier="fast")`): summarizer, market_intel, watchlist, smart_alerts,
  correlation_monitor, volume_analyzer, social_sentiment, analyst_tracker, asia_tracker,
  options_flow, geopolitical_monitor, catalyst_fetcher, event_tracker, weekend_social,
  youtube_monitor, economic_data, pricing_updater (extract tier).
- **reasoning tier** (Claude): insider_tracker, spread_recommender, trade_journal,
  weekend_warroom, daily_briefing, macro_dashboard, weekend_digest, bot_interactive
  (`/ask` roundtable, `/sim`).

**Deps:** `anthropic>=0.69` added to `requirements.txt`, installed in `.venv`.
`.env.example` documents `ANTHROPIC_API_KEY`. `.llm_trace.jsonl*` gitignored.

**Not yet removed (intentional):** the `requests.post` monkeypatch in
`deepseek_client.py`. The DeepSeek provider still uses `requests.post`, so the
patch keeps capturing usage for the Telegram cost footer. Remove it in Phase 6
once the footer is fed from the trace layer (and add Claude cost to footers).

---

## ✅ Phase 2 — Grounded tool-calling agent  (COMPLETE)

The fix for hallucinated numbers: the model calls **deterministic tools** and
narrates real results instead of inventing prices/P&L.

| File | Purpose |
|---|---|
| `alerts/llm/agent_tools.py` | **11 grounded tools** wrapping deterministic project functions. Each returns JSON-serializable dict, never raises. `REGISTRY`, `anthropic_tool_specs()`, `dispatch(name, args)`. |
| `alerts/llm/agent.py` | `run_agent(question, …) -> AgentResult`. Real **Claude tool-use loop** (model fetches → tools run → results fed back → answer) + **DeepSeek grounded fallback** (pre-fetches a tool bundle, single shot). Returns text **+ a claims ledger** (`[{tool,args,result}]`) + accumulated usage. |

**Tools:** `get_price`, `position_pnl`, `simulate_position`, `expected_move`,
`probability_above`, `volatility_report`, `support_resistance`, `options_summary`,
`recommend_spreads`, `upcoming_catalysts`, `recent_alerts`. They wrap
`tools/` (PriceData, ProbabilityEngine, VolatilityAnalyzer, OptionsAnalyzer),
`alerts/price_monitor`, `alerts/spread_recommender`, `config.CATALYSTS`.

**Wired into the bot:** `bot_interactive.handle_freetext` and `handle_reply` now
call `run_agent(...)`. (`handle_ask`/`/sim`/`/pnl` unchanged for now.)

**Verified offline:** tool loop (tool_use→dispatch→tool_result→final answer),
ledger capture, cross-turn usage/cost accumulation; full codebase imports clean.

**Claims ledger** is the grounding record the Phase-4 verifier will check against.

---

## How to activate the full Claude path
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```
Until then everything runs on DeepSeek; the agent uses its grounded fallback
(still grounded, just single-shot instead of the multi-step tool loop).

After adding the key, sanity-check live:
```bash
.venv/bin/python -c "from alerts.llm import run_agent; print(run_agent('how is my position doing?').text)"
```

---

## Design decision: the 4-agent verification loop (user's idea)

Ensuring LLM data is correct is **grounding first, verification second**:
1. **Grounding (Phase 2, done):** numbers come from code (tools), not the model.
2. **Verification (Phase 3–4):** a verifier re-checks each claim in the ledger
   **against the tools**, not against prose. Key rule: *no unsourced number ships.*
   Deterministic math (P&L, breakeven) is asserted in code, not by an LLM.

The agreed loop = **planner → critic → executor → verifier**, where the
**executor and verifier both have tool access**, and the **claims ledger** is the
contract between them. Gate the full loop behind high-stakes calls (`/committee`,
spread recs, post-mortem); simple Q&A uses the single grounded agent.

---

## ✅ Phase 3 — structured outputs + ledger guardrail  (COMPLETE)

The Phase-2 ledger is now enforced: *no unsourced number ships.*

| File | Purpose |
|---|---|
| `alerts/llm/guardrail.py` | Scans an answer's prose for numeric claims and flags any **not backed by a ledger tool result** (or a number the user gave in the question). `check()` → `GuardrailReport(ok, checked, grounded, unsourced)`; `apply()` annotates unsourced figures with a warning footer and stamps a **risk/disclaimer envelope** on actionable advice (buy/sell/roll/close/…). Conservative: bare small integers (DTE/counts) are trivia, sign-insensitive match within a relative tolerance so prose rounding is fine. Non-blocking (surfaces, doesn't redact). |
| `alerts/llm/schemas.py` | JSON schemas for LLM outputs **consumed by code**: `POSITION_ASSESSMENT`, `TRADE_DECISION`. Pass to `complete(..., schema=…)` (wires into `output_config.format`); `parse(text, schema)` validates required keys and raises loudly on bad shape. |

**Wired into `run_agent`:** after the answer is produced (both the Claude tool
loop and the DeepSeek grounded fallback), the guardrail runs automatically.
`AgentResult` gained `verified: bool` and `unsourced: list[dict]`; the trace line
records `verified` + unsourced count. `bot_interactive` surfaces the annotated
text with no change needed.

**Exports:** `from alerts.llm import guardrail, schemas`.

**Verified offline** (extended `smoke_test`): grounded numbers pass, invented
numbers flagged, trivia ignored, question numbers allowed, disclaimer stamped on
advice, unsourced figures annotated; schema parse + required-key/non-JSON errors.

---

## 🔜 Next (resume here)

- **⬜ Phase 4 — multi-agent loop.** Implement planner→critic→executor→verifier
  on top of `run_agent`. Executor = current agent; verifier independently re-runs
  tools and diffs ledger claims. Orchestrator-worker pattern; cache shared prefix.
- **⬜ Phase 5 — prompt caching.** Add `cache_control` breakpoints to the
  high-frequency monitor prompts (stable prefix first, volatile data last);
  verify with `usage.cache_read_input_tokens`.
- **⬜ Phase 6 — evals + observability + cleanup.** Golden-set eval harness over
  recorded (context→expected-properties) cases; `/cost` and `/trace` commands over
  `.llm_trace.jsonl`; remove the `deepseek_client` monkeypatch and feed footers
  (incl. Claude cost) from the trace layer.

---

## Quick reference
- Run offline LLM tests: `.venv/bin/python -m alerts.llm.smoke_test`
- Unified call: `from alerts.llm import complete, ask, run_agent`
- Trace log: `alerts/.llm_trace.jsonl` (gitignored)
- Venv python: `.venv/bin/python` (PEP 668 — don't use system pip)
