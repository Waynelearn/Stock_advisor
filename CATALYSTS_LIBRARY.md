# Catalyst Library

Curated, hand-maintained list of catalysts that affect the MU spread position. **Add to this file as you learn.** Distinct from `alerts/.catalyst_cache.json`, which is the auto-fetched 7-day cache used by the live alert system — this file is the long-memory knowledge base.

When in doubt about whether something belongs here: if the auto-fetcher (Guggenheim / FOMC scrape / yfinance / SEMICON manual list) does NOT find it, but it could plausibly move MU by ≥1%, write it down here.

---

## Schema for each entry

```
### <Event name>
- **When:** <specific date OR recurring pattern>
- **Time (ET):** <if known>
- **Type:** macro | earnings | industry | geopolitical | corporate | sector
- **MU exposure:** high | medium | low
- **Direction bias:** bullish | bearish | 2-way
- **Typical move:** <historical % range, if known>
- **Why it matters:** <one sentence>
- **Source / how to track:** <where you found it; URL or feed if applicable>
- **Last seen:** <YYYY-MM-DD>  ← update when you re-confirm
```

Keep it terse. One line each is fine; full schema is for high-impact events.

---

## A. Active window (2026-05-09 → 2026-05-29)

These are the catalysts we identified for the current 410× 720/740 spread (verified live 2026-05-09 via `list_catalysts.py`).

### CPI May
- **When:** 2026-05-12 (Tue)
- **Time (ET):** 08:30
- **Type:** macro
- **MU exposure:** high
- **Direction bias:** 2-way
- **Typical move:** ±2-3% on surprise of >0.2pp
- **Why it matters:** Hot CPI → Fed delays cuts → semis sell off; cool → relief rally.
- **Source / how to track:** BLS release, auto-scraped by `economic_data.py`
- **Last seen:** 2026-05-09

### PPI May
- **When:** 2026-05-13 (Wed)
- **Time (ET):** 08:30
- **Type:** macro
- **MU exposure:** medium
- **Direction bias:** 2-way
- **Typical move:** ±1-2%
- **Why it matters:** Input-cost read; specifically relevant for chip-input inflation.
- **Source / how to track:** BLS release
- **Last seen:** 2026-05-09

### Monthly Options Expiry — May
- **When:** 2026-05-15 (3rd Friday)
- **Time (ET):** 16:00
- **Type:** sector / structural
- **MU exposure:** medium
- **Direction bias:** pin-risk (gamma)
- **Why it matters:** Heavy OI at round strikes (e.g. 750) creates pin risk; dealer hedging can cap moves.
- **Source / how to track:** computed in `catalyst_fetcher.py:_compute_expiry_dates`

### NVDA Earnings
- **When:** 2026-05-21 (Thu)
- **Time (ET):** 16:15 (post-close)
- **Type:** earnings (peer)
- **MU exposure:** **high** — biggest sympathy driver
- **Direction bias:** bullish (asymmetric — beat helps more than miss hurts, since MU is now ITM)
- **Typical move:** MU sympathy ±3-6%, scaled by current rolling correlation (currently ~0.45 vs the historical 0.72)
- **Why it matters:** NVDA is MU's largest customer; AI capex guide is the demand-side read for HBM3/HBM4.
- **Source / how to track:** `yfinance .calendar` via `catalyst_fetcher.py`
- **Last seen:** 2026-05-09

### PCE May
- **When:** 2026-05-28 (Thu)
- **Time (ET):** 08:30
- **Type:** macro
- **MU exposure:** medium
- **Direction bias:** 2-way
- **Typical move:** ±1.5-2.5%
- **Why it matters:** Fed's preferred inflation gauge. One day before MU expiry — vol exposure peaks here.
- **Source / how to track:** BEA release

### MRVL Earnings
- **When:** 2026-05-28 (Thu)
- **Time (ET):** 16:15 (post-close)
- **Type:** earnings (peer)
- **MU exposure:** medium-low (rolling correlation ~0.24)
- **Direction bias:** 2-way
- **Typical move:** MU sympathy ±1-2%
- **Why it matters:** Data-center networking read-through for memory demand.
- **Source / how to track:** `yfinance .calendar`

---

## B. Recurring catalysts (templates — always relevant)

Use these to extend the active-window list whenever you re-position.

### B1. FOMC meeting cycle (8x/year)
- **Pattern:** ~every 6 weeks, 2-day meeting; statement + dot plot Wednesday 14:00 ET, presser 14:30 ET
- **MU exposure:** high (rate path → semi multiples)
- **Typical move:** ±1-3% on hawkish/dovish surprise
- **2026 dates (auto-fetched):** see `.catalyst_cache.json` — typically Jan, Mar, May, Jun, Jul, Sep, Oct/Nov, Dec
- **Source:** `federalreserve.gov/monetarypolicy/fomccalendars.htm`

### B2. Monthly economic releases (always present in any window ≥ 1 month)
| Release | Day-of-month pattern | Time ET | MU exposure |
|---|---|---|---|
| CPI | ~10th-15th | 08:30 | high |
| PPI | day after CPI | 08:30 | medium |
| Retail Sales | ~15th | 08:30 | medium |
| Industrial Production | ~mid-month | 09:15 | medium |
| Existing Home Sales | ~25th | 10:00 | low |
| Personal Income / PCE | ~last day | 08:30 | medium |
| ISM Manufacturing | 1st business day | 10:00 | medium |
| ISM Services | 3rd business day | 10:00 | medium |
| JOLTS | ~7th | 10:00 | medium |
| ADP Employment | Wed before NFP | 08:15 | low |
| NFP | 1st Friday | 08:30 | high |
| Jobless Claims | every Thursday | 08:30 | low |
| GDP | end-Apr/Jul/Oct/Jan | 08:30 | medium |

### B3. MU earnings cadence
- **FQ1:** late Sept (covers Jun-Aug)
- **FQ2:** mid-Dec (covers Sep-Nov)
- **FQ3:** mid-Mar (covers Dec-Feb)
- **FQ4:** late June (covers Mar-May)
- **Time:** post-close, ~16:00-16:30 ET, conference call 16:30 ET
- **Typical move:** ±5-12% on the print (highest single-day vol of the year for MU)

### B4. Peer earnings (semis sympathy)
| Peer | Cadence | MU correlation (live) | Sympathy size |
|---|---|---|---|
| NVDA | ~mid-Feb, mid-May, late-Aug, late-Nov | 0.45 (was 0.72) | high |
| AMD | late-Jan/Apr/Jul/Oct | 0.45 | medium |
| AVGO | early-Mar/Jun/Sep/Dec | 0.38 | medium |
| MRVL | late-May/Aug/Nov/Feb | 0.24 | low |
| TSM | mid-Jan/Apr/Jul/Oct | n/a (not in `PEERS`) | medium |
| INTC | late-Jan/Apr/Jul/Oct | n/a | low |

> Correlations are computed live by `alerts/correlations.py` over a rolling 60-day window. Re-confirm before pricing in sympathy moves.

### B5. Industry conferences (already in `MANUAL_EVENTS`)
- **NVIDIA GTC:** ~mid-March (San Jose); Jensen keynote = AI capex guide.
- **SEMICON China:** ~late March (Shanghai)
- **SEMICON Southeast Asia:** ~early May (KL)
- **SEMICON West:** ~mid-October (San Francisco)
- **SEMICON Europa:** ~mid-November (Munich)
- **SEMICON Japan:** ~early December (Tokyo)
- **Computex:** ~late May/early June (Taipei) — AI server announcements
- **Hot Chips:** ~late August — chip architecture deep-dive
- **AWS re:Invent:** ~late November/early December (Las Vegas) — hyperscaler signals

### B6. Options-flow structural events
- **Monthly OpEx:** 3rd Friday, 16:00 ET
- **Quarterly OpEx (triple witching):** 3rd Friday of Mar/Jun/Sep/Dec — heavier
- **VIX expiry:** Wednesday before monthly OpEx
- **Quarterly ETF rebalance:** end of Mar/Jun/Sep/Dec — passive flows
- **Russell rebalance:** late June (annual) — only matters if MU's market cap shifts class

---

## C. Stock-specific triggers (the auto-fetcher misses these)

### C1. Korean memory commentary
- **Trigger:** SK Hynix or Samsung CFO/IR commentary on DRAM/NAND pricing
- **Cadence:** earnings (~end-Jan/Apr/Jul/Oct) + ad-hoc
- **MU impact:** high — reads as direct demand signal
- **How to track:** Asia tracker (`alerts/asia_tracker.py`) checks 000660.KS / 005930.KS daily

### C2. China export controls (US Commerce Dept)
- **Trigger:** new BIS export rules, Entity List additions
- **Cadence:** ad-hoc — typically 2-3 announcements/year
- **MU impact:** medium-high — China is ~10-15% of MU revenue; tighter controls = bearish
- **How to track:** RSS feed in `alerts/news_scanner.py`

### C3. CHIPS Act / domestic manufacturing
- **Trigger:** new awards or amendment to existing awards
- **Cadence:** ad-hoc
- **MU impact:** medium — MU has $6.1B award for ID/NY fabs
- **How to track:** Commerce Dept press, news scanner

### C4. Hyperscaler capex guidance updates
- **Trigger:** MSFT/META/GOOGL/AMZN earnings — capex line specifically
- **MU impact:** very high (HBM = AI capex)
- **Tracked by:** `alerts/hyperscaler_tracker.py`

### C5. Taiwan / Korea geopolitical risk
- **Triggers:** Chinese military exercises near Taiwan, North Korea actions, Korea-Japan tensions
- **MU impact:** asymmetric — small de-escalation = no move; major escalation = -10%+ tail
- **Tracked by:** `alerts/geopolitical_monitor.py`

### C6. Memory contract pricing
- **Trigger:** TrendForce/DRAMeXchange monthly contract price reports
- **Cadence:** ~early month for prior-month contract pricing
- **MU impact:** high — direct revenue read
- **How to track:** TrendForce news, currently NOT auto-tracked (consider adding RSS feed)

### C7. AI inference / LLM model releases
- **Trigger:** new GPT/Claude/Gemini/Llama with significantly larger context or weights
- **MU impact:** indirect-bullish — drives HBM demand
- **How to track:** ad-hoc; current `news_scanner.py` keywords include "deepseek"

---

## D. Watch list (themes to monitor — graduate to A/B/C when material)

Add things here as soon as you spot them. Move to a numbered section above once you have a date or pattern.

- _add new themes here as you learn_

---

## E. Past high-impact events (historical reference)

For pattern recognition. Add post-event entries here describing what happened and how MU moved.

- _add post-mortems here_

---

## How to add to this file

1. Edit `CATALYSTS_LIBRARY.md` directly.
2. Use the schema at the top.
3. If the catalyst is a one-off with a known date in your current position window, add it under section A.
4. If it's a recurring pattern, add it under section B.
5. If it's stock-specific and the auto-fetcher won't catch it, add under section C.
6. New emerging themes go under section D.
7. After each high-impact event, write a one-line post-mortem under section E.

If a catalyst becomes structurally trackable (e.g. a new RSS feed, or a yfinance field we weren't using), promote it: file a one-line note here, then add the integration to `alerts/catalyst_fetcher.py`.

## Cross-reference

- `list_catalysts.py` — query the auto-fetched cache + run committee on demand
- `alerts/catalyst_fetcher.py` — the 5 auto-sources
- `alerts/catalyst_scheduler.py` — the per-minute scheduler (reminders + auto-analysis)
- `alerts/economic_data.py` — scrapers + DeepSeek analyzers per event type
- `CATALYSTS.md` — method documentation for the auto-fetcher
- `MANUAL_EVENTS` in `alerts/catalyst_fetcher.py:39` — manual overrides that the live system uses (kept short; long-term knowledge lives here)

---

## F. Live-system coverage matrix

What the scheduler does automatically when each event fires (all driven by per-minute cron):

| Event keyword | Reminders (60/15 min) | Auto-analysis (5 min after) | Source scraped | Function |
|---|:-:|:-:|---|---|
| `CPI` | ✅ | ✅ | BLS `cpi.nr0.htm` | `analyze_economic_release("CPI")` |
| `PPI` | ✅ | ✅ | BLS `ppi.nr0.htm` | `analyze_economic_release("PPI")` |
| `PCE` | ✅ | ✅ | BEA `current-releases` (auto-discover URL) | `analyze_economic_release("PCE")` |
| `NFP` | ✅ | ✅ | BLS `empsit.nr0.htm` | `analyze_economic_release("NFP")` |
| `FOMC RATE DECISION` / `POWELL PRESS CONFERENCE` | ✅ | ✅ | Fed `fomccalendars.htm` (latest + previous, diff'd) | `analyze_fomc_with_diff()` |
| `MU EARNINGS` | ✅ | ✅ | Micron IR press release | `analyze_mu_earnings()` |
| `NVDA EARNINGS` | ✅ | ✅ | yfinance post-close move + live correlation | `analyze_peer_earnings("NVDA")` |
| `AMD EARNINGS` | ✅ | ✅ | yfinance + correlation | `analyze_peer_earnings("AMD")` |
| `AVGO EARNINGS` | ✅ | ✅ | yfinance + correlation | `analyze_peer_earnings("AVGO")` |
| `MRVL EARNINGS` | ✅ | ✅ | yfinance + correlation | `analyze_peer_earnings("MRVL")` |
| `TSM EARNINGS` | ✅ | ✅ | yfinance + correlation | `analyze_peer_earnings("TSM")` |
| `INTC EARNINGS` | ✅ | ✅ | yfinance + correlation | `analyze_peer_earnings("INTC")` |
| `Monthly Options Expiry` | ✅ | n/a | structural — no analysis to do |  |
| anything else (SEMICON, GTC, etc.) | ✅ | n/a (reminder-only) |  |  |

Keywords are matched case-insensitively in the catalyst description (`alerts/catalyst_scheduler.py:17`). Order matters — peer-earnings keys are placed before the generic `MU EARNINGS` so e.g. "NVDA EARNINGS" doesn't fall through to MU's analyzer.

### Active-window coverage (snapshot 2026-05-09)

| Date | Event | Pipeline |
|---|---|---|
| 2026-05-12 | CPI May | reminder + BLS scrape + DeepSeek analysis |
| 2026-05-13 | PPI May | reminder + BLS scrape + DeepSeek analysis |
| 2026-05-15 | Monthly Options Expiry | reminder only |
| 2026-05-21 | NVDA EARNINGS | reminder + yfinance + sympathy analysis |
| 2026-05-28 | PCE May | reminder + BEA scrape + DeepSeek analysis |
| 2026-05-28 | MRVL EARNINGS | reminder + yfinance + sympathy analysis |

### Adding a new event-type to auto-analysis

Two-step process:
1. Add a keyword → event_type entry to `AUTO_ANALYZE_KEYWORDS` in `alerts/catalyst_scheduler.py`.
2. If it's a new kind of analyzer, add the function to `alerts/economic_data.py` and dispatch in `_auto_analyze_event` (which currently handles `FOMC`, `EARNINGS`, `PEER_*`, and a generic scrape fallback).

For new peer earnings (e.g. KLAC, LRCX, ON), only step 1 is needed — `analyze_peer_earnings` accepts any ticker.
