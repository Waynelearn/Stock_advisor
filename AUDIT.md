# Codebase Audit — Hardcoded Prices & Magic Numbers

**Audited:** 9 May 2026
**Scope:** every module under `alerts/` + `tools/` + `config.py`
**Focus:** prices, thresholds, lookback windows, lists of "levels", and any literal that should be derived dynamically.

Findings are sorted by **severity**. Each row gives a citation, what's wrong, and the proposed fix.

Legend: 🔴 critical (wrong-answer-now) · 🟠 high (silently degrades signal quality) · 🟡 medium (pure code-smell, harmless today)

---

## 🔴 CRITICAL — wrong / stale data running in production

### C1. Stale position metadata baked into 8 modules' DeepSeek prompts

The system right now tells DeepSeek you hold **"500x MU 380/400 bull call spread expiring March 20"** in every AI prompt, while your real position (per `config.py`) is **410x 720/740 expiring May 29**. Every AI-generated briefing, post-mortem, weekend digest, and news interpretation is being given the wrong context.

| File | Line | Hardcoded text |
|---|---:|---|
| `alerts/insider_tracker.py` | 139 | `f"My position: 500x MU 380/400 bull call spread expiring March 20.\n"` |
| `alerts/insider_tracker.py` | 300 | same |
| `alerts/bot_interactive.py` | 504 | `f"POSITION: 500x MU 380/400 bull call spread, entry $11.897, expiry March 20, 2026.\n"` |
| `alerts/trade_journal.py` | 121 | `f"TRADE: 500x MU 380/400 bull call spread, entry $11.897, expiry March 20, 2026\n"` |
| `alerts/economic_data.py` | 249, 306, 381, 387, 524 | five separate copies of the stale 500x 380/400 text |
| `alerts/weekend_digest.py` | 98 | same |
| `alerts/macro_dashboard.py` | 420 | same |

**Fix:** introduce one helper `position_summary()` in `config.py` that returns a formatted f-string from the live `POSITION` dict. Every prompt above gets rewritten as `f"...{position_summary()}..."`. Single source of truth.

---

### C2. Hardcoded MU price thresholds in daily briefing

`daily_briefing.py` makes narrative decisions ("strong", "weak", "near breakeven") against literal price values that were calibrated when MU traded **$370–$400**. MU is now **$746**, so every cascade evaluates to the same branch. The briefing's framework recommendations are effectively constant.

| Line | Current | Problem |
|---:|---|---|
| 322 | `if price > 400:` | Always True at $746 → every day says "extension" |
| 324 | `elif price > 392:` | Dead branch |
| 326 | `elif price > 385:` | Dead branch |
| 333 | `if price > 395:` | Always True |
| 335 | `elif price > 385:` | Dead branch |
| 344 | `if price > 400:` | Always True |
| 346 | `elif price > 390:` | Dead |
| 348 | `elif price > 385:` | Dead |
| 350 | `elif price > 370:` | Dead |
| 357 | `if price > 400:` | Always True |
| 359 | `elif price > 392:` | Dead |
| 246 | `if move_pct > 3:` (BEAT/MISS) | Should reuse `BIG_MOVE_PCT` from config |
| 304 | `if move_pct > 3:` | same |

**Fix:** replace all of these with **distance from breakeven** as a multiple of the spread's width:
```python
spread_width = POSITION["short_strike"] - POSITION["long_strike"]      # 20
moneyness = (price - POSITION["breakeven"]) / spread_width             # +0.85 = 85% deep ITM
```
Then the cascade becomes regime-based and self-adjusting: `> 0.5` deep ITM, `0.0–0.5` ITM, `-0.5–0` near-breakeven, `< -0.5` OTM. Same logic, but it survives any future strike change.

---

### C3. Hardcoded scenario range in `/sim`

```python
# bot_interactive.py:583
prices = list(range(370, 415, 5))     # MU now $746 — entire range OTM
```

**Fix:** `range(int(spot * 0.93), int(spot * 1.07), max(1, int(spot * 0.005)))` — ±7% around spot in 0.5%-of-spot steps.

---

### C4. `bot_interactive.py:12` and `:480` — docstring + simulator example reference $385

These are user-facing help strings. Cosmetic but inconsistent with the position. Rewrite as `"what if {ticker} drops to {breakeven - 5}"` style.

---

## 🟠 HIGH — distorted signal quality

### H1. `PRICE_LEVELS` is a hand-curated list

```python
# config.py:33
PRICE_LEVELS = [700, 710, 720, 725, 729, 730, 735, 740, 745, 750, 760, 770, 780]
```

I just edited this when you updated POSITION. That's exactly the wrong workflow — every position change becomes a config edit.

**Fix:** auto-generate at module import (or call) time:
```python
def get_price_levels(spot=None):
    if spot is None: spot = get_live_price(POSITION["ticker"])
    strikes = [POSITION["long_strike"], POSITION["short_strike"], POSITION["breakeven"]]
    pct_grid = [spot * (1 + p/100) for p in [-7, -5, -3, -1.5, 0, 1.5, 3, 5, 7]]
    levels = sorted({round(x, 0) for x in strikes + pct_grid})
    return levels
```
Cached for the trading day, refreshed on the morning recap.

### H2. `breakeven` is duplicated, not computed

```python
# config.py:29
"breakeven": 729.213,    # = long_strike + entry_price; redundant
```

**Fix:** drop the field, compute on access via a helper or `__post_init__`-style proxy.

### H3. `PEER_CORRELATIONS` is a static dict

```python
# config.py:130
PEER_CORRELATIONS = {"NVDA": 0.72, "AMD": 0.68, "AVGO": 0.55, "MRVL": 0.60}
```

These were a snapshot. Rolling 60-day correlation between MU and each peer drifts substantially during regime shifts.

**Fix:** compute on demand via `compute_peer_correlations(window_days=60)` in a new `alerts/correlations.py`, cached for a trading day. Falls back to the static values if yfinance is unavailable.

### H4. Parallel threshold constants — two sources of truth

`alerts/options_flow.py` redefines several thresholds that already exist in `config.py`:

| `config.py` | `options_flow.py` | Same? |
|---|---|---|
| `VOL_OI_RATIO_THRESHOLD = 3.0` (L113) | `UNUSUAL_VOL_OI_RATIO = 3.0` (L26) | ✅ duplicate |
| `VOL_OI_RATIO_SWEEP = 5.0` (L114) | `OTHER_EXPIRY_VOL_OI = 5.0` (L29) | ✅ duplicate |
| `PCR_SHIFT_THRESHOLD = 0.3` (L116) | `PCR_SHIFT_THRESHOLD = 0.3` (L30) | ✅ duplicate |
| `LIQUIDITY_SPREAD_THRESHOLD = 10` (L118) | `LIQUIDITY_SPREAD_PCT = 10.0` (L31) | ✅ duplicate |
| `IV_CHANGE_THRESHOLD = 5.0` (L117) | `IV_CHANGE_THRESHOLD = 5.0` (L32) | ✅ duplicate |

Same story in `alerts/gap_monitor.py:20-22` (FUTURES_GAP_PCT, SEMI_GAP_PCT, CONSENSUS_GAP_PCT) and `alerts/fx_monitor.py:27` (FX_MOVE_THRESHOLD).

**Fix:** delete every module-local copy, import from `config.py`.

### H5. Hand-coded gold price ladder in macro_dashboard

```python
# alerts/macro_dashboard.py:72
"GC=F": [2500, 3000, 4000, 5000],
```

Same pattern as PRICE_LEVELS — a literal ladder. Should derive from current gold spot ± fixed % grid.

### H6. Hardcoded earnings-result threshold (`> 3`)

`daily_briefing.py:246` and `:304` use literal `3` for BEAT/MISS classification of MU earnings reactions. `config.py:36` already defines `BIG_MOVE_PCT = 3.0`. Should reuse it (or, better, use historical earnings-day vol distribution to set the threshold dynamically — typical MU earnings move is 5–8%).

### H7. Magic thresholds inside individual modules

Each cluster below should be promoted to a clearly-named config constant (or computed from data):

| File | Lines | Literals | Suggested fix |
|---|---|---|---|
| `daily_briefing.py` | 407, 536, 543, 545 | `0.5`, `1.5`, `0.3` % cutoffs for "stable / momentum" labels | derive from realized 5-day stdev of returns |
| `sector_rotation.py` | 106, 110, 118 | `2.0`, `1.0`, `+1.5`, `2.0` % cutoffs | derive from cross-sector vol baseline |
| `memory_pricing.py` | 94, 96, 158, 160 | `> 3.0`, `< -1.0` (peer divergence) | derive from rolling cross-stock spread vol |
| `gap_monitor.py` | 183, 185, 192 | `1.0`, `0.5`, `0.2` (semi gap) | reuse `BIG_MOVE_PCT` family |
| `hyperscaler_tracker.py` | 180 | `> 1.0` (capex divergence) | configurable |
| `fx_monitor.py` | 108, 124 | `> 0.3` (DXY/KRW flag) | configurable |

### H8. Lookback windows scattered, never named

| File | Window | Where |
|---|---|---|
| `etf_flows.py:76` | 20 (rolling avg vol) | hardcoded in `.rolling(20)` |
| `volume_analyzer.py:33,49,53,71` | 5, 1.0% | hardcoded |
| `sector_rotation.py:60-62` | 1d, 5d, 20d returns | hardcoded indices `iloc[-2]`, `iloc[-6]`, `iloc[0]` |
| `hyperscaler_tracker.py:48-50` | same 1/5/20 pattern | duplicated |
| `memory_pricing.py:53-56` | 6, 22, 5, 20 | mixed lookbacks |
| `correlation_monitor.py:42, 51` | 5, 10 | min sample for divergence |

**Fix:** define one `LOOKBACK = {"intraday": 5, "short": 6, "month": 22, "vol_avg": 20, "corr_min": 10}` dict in `config.py`, and replace every literal index with a named lookback.

### H9. State-cap magic numbers

| File | Line | Cap |
|---|---:|---|
| `analyst_tracker.py` | 64 | `> 200` rated entries kept |
| `news_scanner.py` | 183 | `> 500` seen headline IDs |
| `bot.py` | 32 (logged) | last 200 messages |
| `bot_interactive.py` | (interaction log) | last 500 |

**Fix:** one `STATE_RETENTION = {"news_ids": 500, "analyst_seen": 200, "msg_log": 200, "interaction_log": 500}` block.

---

## 🟡 MEDIUM — pure code-smell

### M1. Truncation literals

| File | Line | Literal | Note |
|---|---:|---|---|
| `bot_interactive.py` | 223 | `> 1500` | original-message context cap |
| `bot_interactive.py` | 304 | `[:100]` | quoted-question preview |
| `bot.py` | 57 | `[:1024]` | Telegram caption limit (real spec) |
| `bot.py` | 76 | `MAX_LEN = 4096` | Telegram message limit (real spec) |
| `economic_data.py` | 42, 47, 65, 69 | `[:4000]` | scraped-text trim |

The Telegram limits at `bot.py:57,76` are protocol facts and fine as constants; the others should move to a `TRUNCATION = {...}` block.

### M2. Catalyst cache TTL

```python
# alerts/catalyst_fetcher.py:31
CACHE_MAX_AGE_DAYS = 7
```

Module-local. Fine for now; promote if you ever want to tweak it.

### M3. VIX threshold ladder

```python
# config.py:39
VIX_LEVELS = [25, 30, 35]
```

Three regime cutoffs (CAUTION / DANGER / EXTREME). These map to widely-recognized VIX regimes, so unlike PRICE_LEVELS this list is **defensible as static**. Recommend leaving as-is and labeling clearly.

### M4. Catalyst reminder schedule

```python
# config.py:101
CATALYST_REMINDER_MINUTES = [60, 15]
```

Two-tier reminder. Static is fine — the choice "1h + 15m" is a UX decision, not a market parameter.

### M5. Expiry escalation window

```python
# config.py:141
EXPIRY_ESCALATION_DAYS = 5
```

Fine as a knob. Could be derived (e.g. `max(3, dte * 0.15)` for longer-dated spreads), but for monthly options 5 is reasonable.

---

## Summary of proposed changes

If you greenlight all of this, the implementation falls into 5 commits:

1. **Add `position_summary()` helper** in `config.py`; replace all 8 stale-position prompts to use it. Drop the `breakeven` field (compute on access). **Fixes C1, C4, H2.**
2. **Replace hardcoded MU price cascades** in `daily_briefing.py` with breakeven-relative `moneyness` logic. **Fixes C2.**
3. **Auto-generate `PRICE_LEVELS`** from spot + strikes + percentage grid; same for `GC=F`. Rewrite `/sim` price range. **Fixes H1, H5, C3.**
4. **Centralize duplicated thresholds**: delete module-local copies in `options_flow.py`, `gap_monitor.py`, `fx_monitor.py`. Add `LOOKBACK`, `STATE_RETENTION`, `TRUNCATION` blocks to `config.py`; replace scattered literals. **Fixes H4, H7, H8, H9, M1.**
5. **Compute `PEER_CORRELATIONS`** dynamically from rolling window. **Fixes H3.**

**Out of scope (intentionally left static):** VIX_LEVELS regimes, catalyst reminder schedule, expiry-escalation window, market-hours constants, Telegram protocol limits, RSS feed lists, peer ticker lists.

**Not touched (deliberate):** the `POSITION` dict itself stays as the user-edited single source of truth for the active trade. Strikes/contracts/entry are inputs to the system, not magic numbers.

---

## Open question

Some "magic" numbers encode **personal trading philosophy** (when do you treat a 0.3% DXY move as material? when does a peer divergence become a tradable signal?). Promoting them to config makes them *tunable*, but the values themselves are still your call. I'll keep current values as defaults during the refactor — happy to revisit after you've seen the centralized list.
