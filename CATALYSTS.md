# Catalyst Discovery — Method & Reuse

This document describes how the system finds catalysts (economic releases, FOMC, earnings, options expiries, industry events) and how to reproduce the committee analysis you just received.

## TL;DR — to run it again

```bash
cd /Users/wayne_linn/Desktop/ai_projects/Stock_advisor
.venv/bin/python list_catalysts.py
```

That single command pulls the live catalyst list from now → your position's expiry, calls the 9-persona committee, prints the result, and sends it to Telegram.

Useful flags:

| Flag | Effect |
|---|---|
| `--no-committee` | Skip the DeepSeek call; just print the list. Cheap, no API cost. |
| `--no-telegram` | Print only; don't send the alert. |
| `--refresh` | Force-refresh the catalyst cache before listing. Use after a fresh trade or if the system has been idle for >7 days. |
| `--until 2026-06-30` | Use a custom end date instead of `POSITION["expiry"]`. |

---

## The 5 catalyst sources

All logic lives in `alerts/catalyst_fetcher.py`. Each source is independent — if one fails, the others still populate.

| # | Source | What it produces | How |
|---|---|---|---|
| 1 | **Guggenheim economic calendar** | CPI, PPI, PCE, NFP, ISM, retail sales, jobless claims, GDP, etc. | HTML scrape of `guggenheiminvestments.com/perspectives/portfolio-strategy/economic-calendar`, parsed by **DeepSeek** so HTML changes don't break it. |
| 2 | **Federal Reserve FOMC dates** | All FOMC meeting dates (2-day events) | Regex scrape of `federalreserve.gov/monetarypolicy/fomccalendars.htm`, with a DeepSeek fallback if the regex misses. |
| 3 | **yfinance `.calendar`** | Earnings dates for MU + every peer in `PEERS` (NVDA, AMD, AVGO, MRVL) + supply chain (TSM, INTC) | Per-ticker call to `yf.Ticker(t).calendar`, which yfinance scrapes from Yahoo Finance's earnings page. |
| 4 | **Computed monthly options expiry** | Every 3rd-Friday OpEx of every month for the rest of the year | Pure Python in `_compute_expiry_dates()`. No API call. |
| 5 | **Manual overrides** (`MANUAL_EVENTS`) | Industry events that no API provides — NVIDIA GTC, SEMICON conferences | Hand-edited list at `alerts/catalyst_fetcher.py:39-51`. Update yearly. |

The list of trackable earnings tickers is `EARNINGS_TICKERS` (`alerts/catalyst_fetcher.py:34`):

```python
EARNINGS_TICKERS = ["MU"] + [p for p in PEERS if p != "SOXX"] + ["TSM", "INTC"]
```

If you want catalysts for a different stock universe, edit that list (and `PEERS` in `config.py`).

## Caching

- The first call combines all five sources and writes to `alerts/.catalyst_cache.json`.
- Subsequent calls within **7 days** read from cache (`CACHE_MAX_AGE_DAYS = 7`).
- After 7 days the cache auto-refreshes on next access.
- To force-refresh sooner, run with `--refresh` or delete `.catalyst_cache.json`.

The cache is a simple JSON list of `[month, day, hour, minute, description]` tuples. Hours and minutes are US Eastern (where the data is published).

## Filtering to your position window

`list_catalysts.py` filters the cache to events between today and `POSITION["expiry"]`. The expiry is read from `alerts/config.py`. After every trade roll, update `POSITION` in `config.py` and the script automatically tracks the new window — no other edits needed.

## The 9-persona Committee

The Roundtable is a DeepSeek prompt (in `list_catalysts.py:call_committee`) that asks for one line each from these personas:

| Persona | Role |
|---|---|
| Rex | Bull case |
| Vera | Bear case |
| Sigma | Quant — probability + greeks |
| Atlas | Macro — Fed, geopolitics |
| Chart | Technician — levels |
| Flux | Market regime |
| Edge | Options flow / sentiment |
| Catalyst | Events ranking |
| Arbiter | Final verdict |

It outputs three sections: persona one-liners, per-catalyst impact table (bullish/bearish/2-way + size + reason), and an Arbiter verdict with a flip-trigger.

### Model fallback chain

The script tries models in this order:

1. `deepseek-v4-pro`
2. `deepseek-v4-flash`
3. `deepseek-chat` (legacy, non-thinking mode)

If a model returns empty content (which can happen with v4-pro/flash on smaller `max_tokens` budgets — they consume the budget on internal reasoning), the script automatically falls back to the next.

> **Known issue (May 2026):** `deepseek-v4-pro` and `v4-flash` are reasoning-tier models that allocate `max_tokens` to internal "thinking" before producing output. With our 700-token budget, both burned all of it on reasoning and returned 0 chars of visible output. We've raised the budget to 2000 tokens in `list_catalysts.py`, and the fallback to `deepseek-chat` ensures we always get a response. If you want guaranteed v4-pro output, raise `max_tokens` further (cost goes up linearly).

---

## What today's run produced (snapshot)

**Window:** 2026-05-09 → 2026-05-29 (20 days to expiry)
**Position:** 410× MU 720/740 BCS, entry $9.213
**Spot at run time:** MU $746.81, moneyness +0.88 (~max-profit zone)

| Date | Day | Time (ET) | Event |
|---|---|---|---|
| 2026-05-12 | Tue | 08:30 | CPI May — KEY |
| 2026-05-13 | Wed | 08:30 | PPI May — KEY |
| 2026-05-15 | Fri | 16:00 | Monthly Options Expiry |
| 2026-05-21 | Thu | 16:15 | NVDA EARNINGS |
| 2026-05-28 | Thu | 08:30 | PCE May — KEY |
| 2026-05-28 | Thu | 16:15 | MRVL EARNINGS |

The committee's verdict (delivered to Telegram): **HOLD**, with the flip-trigger being "MU closes below 730 on 5/13 after CPI/PPI."

---

## When to re-run

Reasonable cadences:

- **After every trade roll** — verifies the new expiry window is tracked.
- **Sunday evening** — preview the week's catalysts before Monday open.
- **Day before any high-impact print** (CPI/PPI/PCE/FOMC) — confirms the committee's stance.
- **Anytime you want a second opinion** before adjusting the position.

The system's daily-briefing module (`alerts/daily_briefing.py`) already runs a similar Roundtable at 8 PM SGT each weekday, so you don't need to schedule this script — it's an on-demand version with a tighter, catalyst-focused prompt.

## Files involved

| File | Role |
|---|---|
| `alerts/catalyst_fetcher.py` | The 5-source aggregator |
| `alerts/.catalyst_cache.json` | 7-day cache (gitignored) |
| `alerts/config.py` | `POSITION` (defines expiry window), `PEERS` (defines tickers), `DEEPSEEK_MODEL_PRO/FAST` |
| `list_catalysts.py` | The on-demand entry point — script you run |
| `CATALYSTS.md` | This document |
