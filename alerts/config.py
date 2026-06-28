"""Telegram alert configuration."""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# DeepSeek API for news summarization + sentiment
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# DeepSeek model tiers (env-overridable):
#   PRO   - higher-quality reasoning. Used for user-facing Q&A, daily/weekend
#           briefings, post-mortems, spread recommendations, insider analysis.
#   FAST  - cheaper / faster. Used for high-frequency monitors, news/web
#           summarization, and structured-data extraction.
DEEPSEEK_MODEL_PRO = os.environ.get("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")
DEEPSEEK_MODEL_FAST = os.environ.get("DEEPSEEK_MODEL_FAST", "deepseek-v4-flash")

# ─────────────────────────────────────────────────────────────────────────
# Unified LLM routing (alerts/llm). Reasoning-heavy, grounded work goes to
# Claude; high-frequency monitors + extraction stay on cheap DeepSeek. If the
# Anthropic key/SDK is unavailable the router falls back to the DeepSeek tier,
# so the system degrades rather than breaking.
# ─────────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_REASONING_MODEL = os.environ.get("LLM_REASONING_MODEL", "claude-opus-4-8")

LLM_TIER_MODELS = {
    "reasoning": LLM_REASONING_MODEL,   # Claude — roundtable, briefings, post-mortem, Q&A
    "fast": DEEPSEEK_MODEL_FAST,        # cheap high-frequency monitors, summarization
    "extract": DEEPSEEK_MODEL_FAST,     # structured-data extraction
}
LLM_FALLBACK_TIER = "fast"

# If ANTHROPIC_API_KEY is not set (or the SDK is missing), every Claude-routed
# call falls back to DeepSeek v4 — reasoning work to deepseek-v4-pro (preserving
# quality), monitors/extraction to the cheaper deepseek-v4-flash.
LLM_FALLBACK_MODELS = {
    "reasoning": DEEPSEEK_MODEL_PRO,
    "fast": DEEPSEEK_MODEL_FAST,
    "extract": DEEPSEEK_MODEL_FAST,
}

# MU spread position (410x 720/740 BCS, expires 29 May 2026)
# `breakeven` and `spread_width` are computed below — do not duplicate them here.
POSITION = {
    "ticker": "MU",
    "long_strike": 720,
    "short_strike": 740,
    "contracts": 410,
    "entry_price": 9.213,
    "expiry": "2026-05-29",
}
POSITION["breakeven"] = POSITION["long_strike"] + POSITION["entry_price"]
POSITION["spread_width"] = POSITION["short_strike"] - POSITION["long_strike"]
POSITION["max_value"] = POSITION["spread_width"]
POSITION["max_profit_per_contract"] = POSITION["spread_width"] - POSITION["entry_price"]


def position_summary() -> str:
    """One-line position description for AI prompts. Single source of truth."""
    p = POSITION
    return (
        f"{p['contracts']}x {p['ticker']} {p['long_strike']}/{p['short_strike']} "
        f"bull call spread, entry ${p['entry_price']:.3f}, expiry {p['expiry']} "
        f"(breakeven ${p['breakeven']:.2f}, max ${p['max_profit_per_contract']:.2f}/contract)"
    )


def position_moneyness(price: float) -> float:
    """How deep ITM/OTM the spread is, normalized by spread width.
    +1.0 = price at short strike (max profit zone)
     0.0 = price at breakeven
    -1.0 = price below breakeven by one spread-width
    """
    return (price - POSITION["breakeven"]) / POSITION["spread_width"]

# Price-level grid: percent offsets from spot, applied dynamically.
# Tight grid near spot (1.5% steps) widens to ±7% caps.
_PRICE_LEVEL_PCT_GRID = [-7, -5, -3, -1.5, -0.5, 0, 0.5, 1.5, 3, 5, 7]


def get_price_levels(spot: float | None = None) -> list[float]:
    """Generate price-crossing alert levels for the position's ticker.

    Combines: long_strike, short_strike, breakeven, plus a percent grid
    around current spot. Deduplicated, sorted, rounded to nearest dollar.
    Falls back to breakeven if spot lookup fails.
    """
    if spot is None:
        try:
            from .price_monitor import get_live_price
            spot = get_live_price(POSITION["ticker"]) or POSITION["breakeven"]
        except Exception:
            spot = POSITION["breakeven"]

    strikes = [POSITION["long_strike"], POSITION["short_strike"], round(POSITION["breakeven"])]
    grid = [round(spot * (1 + p / 100)) for p in _PRICE_LEVEL_PCT_GRID]
    return sorted({float(x) for x in strikes + grid})


def get_commodity_levels(spot: float, pct_offsets=(-15, -10, -5, -2, 0, 2, 5, 10, 15)) -> list[float]:
    """Generate alert levels for a commodity (oil, gold) around current spot.
    Returns a sorted, deduplicated list rounded to nearest whole number.
    """
    return sorted({round(spot * (1 + p / 100)) for p in pct_offsets})

# Alert if intraday move exceeds this %
BIG_MOVE_PCT = 3.0

# VIX alert levels
VIX_LEVELS = [25, 30, 35]

# Futures tickers to track
FUTURES = {
    "ES=F": "S&P 500 Futures",
    "NQ=F": "Nasdaq Futures",
    "YM=F": "Dow Futures",
}

# Futures alert: notify if any future moves more than this %
FUTURES_BIG_MOVE_PCT = 1.0

# Check interval in seconds during market hours
CHECK_INTERVAL_SECONDS = 60

# Timezone: US Eastern (market hours) and Singapore
TZ_ET = "US/Eastern"
TZ_SGT = "Asia/Singapore"

# Market hours (ET)
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 30
MARKET_CLOSE_HOUR = 16
MARKET_CLOSE_MIN = 0

# Pre-market starts 4 AM ET, after-hours ends 8 PM ET
# Futures trade nearly 24h (Sun 6PM - Fri 5PM ET)
PREMARKET_HOUR = 4
AFTERHOURS_END_HOUR = 20

# Peer tickers to monitor
PEERS = ["NVDA", "AMD", "AVGO", "MRVL", "SOXX"]

# Peer big move threshold (alert if peer moves this much)
PEER_BIG_MOVE_PCT = 4.0

# Catalyst schedule: auto-fetched from web sources + yfinance + manual overrides
# Lazy-loaded on first access to avoid slow imports at module level
_catalysts_cache = None

def _get_catalysts():
    global _catalysts_cache
    if _catalysts_cache is None:
        from .catalyst_fetcher import get_catalysts
        _catalysts_cache = get_catalysts()
    return _catalysts_cache


class _CatalystProxy:
    """Lazy proxy that behaves like a list but fetches on first access."""
    def __iter__(self):
        return iter(_get_catalysts())
    def __len__(self):
        return len(_get_catalysts())
    def __getitem__(self, idx):
        return _get_catalysts()[idx]
    def __bool__(self):
        return bool(_get_catalysts())

CATALYSTS = _CatalystProxy()

# How many minutes before catalyst to send reminder
CATALYST_REMINDER_MINUTES = [60, 15]

# Asia session tickers (leading indicators for MU)
ASIA_TICKERS = {
    "000660.KS": "SK Hynix",
    "005930.KS": "Samsung",
    "ASML": "ASML",
}
ASIA_MOVE_THRESHOLD = 2.0        # Alert if any single ticker moves >2%
ASIA_CONSENSUS_THRESHOLD = 1.0   # Alert if ALL move same direction >1%

# Options flow thresholds
VOL_OI_RATIO_THRESHOLD = 3.0     # Flag if volume > 3x OI
VOL_OI_RATIO_SWEEP = 5.0         # Higher bar for non-primary expiries
MIN_UNUSUAL_VOLUME = 100         # Min contracts for unusual activity
PCR_SHIFT_THRESHOLD = 0.3        # Alert on PCR shift > 0.3
IV_CHANGE_THRESHOLD = 5.0        # Alert on IV change > 5 pts
LIQUIDITY_SPREAD_THRESHOLD = 10  # Alert if bid-ask spread > 10%

# Volume anomaly thresholds
VOLUME_ANOMALY_RATIO = 2.0       # Alert if volume > 2x 20-day avg
VOLUME_EXTREME_RATIO = 3.0       # Extreme volume flag

# Correlation thresholds
DIVERGENCE_ZSCORE = 1.5          # MU-SOX divergence z-score threshold
VIX_ANOMALY_MU_PCT = 1.0        # Min MU move for VIX anomaly
VIX_ANOMALY_VIX_PCT = 5.0       # Min VIX move for anomaly

# Peer earnings sympathy — coefficients computed live from rolling daily returns.
# Cached per trading day; falls back to static defaults if yfinance is unavailable.
class _PeerCorrProxy:
    """Lazy proxy that fetches/caches rolling peer correlations on first access."""
    def _data(self):
        from .correlations import get_peer_correlations, FALLBACK
        try:
            return get_peer_correlations(POSITION["ticker"], list(FALLBACK.keys()))
        except Exception:
            return FALLBACK

    def get(self, key, default=None):
        return self._data().get(key, default)

    def __getitem__(self, key):
        return self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __contains__(self, key):
        return key in self._data()

    def __len__(self):
        return len(self._data())

    def keys(self):
        return self._data().keys()

    def values(self):
        return self._data().values()

    def items(self):
        return self._data().items()

    def __repr__(self):
        return repr(self._data())

PEER_CORRELATIONS = _PeerCorrProxy()

# SEC EDGAR - Micron CIK
MU_CIK = "0000723125"

# Expiry week escalation - days before expiry to ramp up monitoring
EXPIRY_ESCALATION_DAYS = 5

# ─────────────────────────────────────────────────────────────────────────
# Centralized: lookback windows, state retention, truncation
# ─────────────────────────────────────────────────────────────────────────
LOOKBACK = {
    "intraday_min": 5,         # min sample bars for intraday detectors
    "short_window": 6,         # ~5 trading days for w/w (iloc[-6])
    "month_window": 22,        # ~1 trading month (iloc[-22])
    "vol_avg": 20,             # 20-day rolling volume average
    "vol_5d": 5,               # 5-day volume window
    "corr_min": 10,            # min sample for divergence z-score
    "history_period": "1mo",   # default yfinance history window
}

STATE_RETENTION = {
    "news_ids": 500,
    "analyst_seen": 200,
    "msg_log": 200,
    "interaction_log": 500,
}

# Truncation budgets (Telegram limits are protocol facts; the rest are knobs)
TRUNCATION = {
    "telegram_msg": 4096,
    "telegram_caption": 1024,
    "scrape_max": 4000,        # raw HTML/text fed into AI
    "prompt_context": 1500,    # original-message context budget
    "preview": 100,            # short snippet/preview
    "log_message": 500,        # logged-message field cap
}

# ─────────────────────────────────────────────────────────────────────────
# Centralized thresholds — were duplicated across modules; now single source.
# ─────────────────────────────────────────────────────────────────────────
# Gap detection (was in gap_monitor.py)
GAP_FUTURES_PCT = 0.5          # any single future
GAP_SEMI_PCT = 0.75            # SOXX/SOX gap
GAP_CONSENSUS_PCT = 0.3        # all futures same direction
GAP_PREMARKET_PCT = 1.0        # MU pre-market
GAP_SEMI_LARGE_PCT = 1.0       # narrative bucket: large semi gap
GAP_SEMI_MEDIUM_PCT = 0.5      # narrative bucket: medium semi gap
GAP_AVG_SMALL_PCT = 0.2        # narrative bucket: avg gap below = quiet

# MU beta estimates (used to project open from futures; gap_monitor.py)
MU_BETA_NQ = 1.4
MU_BETA_SOX = 0.9

# FX (was in fx_monitor.py)
FX_MOVE_PCT = 0.5
FX_DXY_MOVE_PCT = 0.3

# Daily-briefing pct cutoffs for stable/momentum/trending labels
DAILY_STABLE_PCT = 0.5
DAILY_MOMENTUM_PCT = 1.5
DAILY_TRENDING_PCT = 0.3

# Sector rotation (was in sector_rotation.py)
SECTOR_DEFENSIVE_BIAS_PCT = 2.0
SECTOR_GROWTH_LEAD_PCT = 1.0
SECTOR_GROWTH_VS_DEFENSIVE_PCT = 1.5
SECTOR_SOXX_OUTPERFORM_PCT = 2.0

# Memory pricing peer divergence (was in memory_pricing.py)
MEMORY_DIVERGENCE_HIGH_PCT = 3.0
MEMORY_DIVERGENCE_LOW_PCT = -1.0
MEMORY_VOL_RATIO = 1.5

# Hyperscaler tracker
HYPERSCALER_DIVERGENCE_PCT = 1.0

# Volume analyzer (was in volume_analyzer.py)
VOLUME_DARK_POOL_PRICE_PCT = 1.0  # high vol with |price chg| below this = dark-pool proxy

# ─────────────────────────────────────────────────────────────────────────
# Spread recommender (was scattered as literals across spread_recommender.py)
# ─────────────────────────────────────────────────────────────────────────
SPREAD_REC = {
    # Quality filters: drop spreads that are uneconomic to trade
    "min_max_profit_dollars": 0.50,    # absolute floor on max-profit per contract
    "min_max_profit_ratio": 0.05,      # max_profit must be ≥ this fraction of width
    "min_risk_reward": 0.20,           # minimum acceptable R:R

    # Scoring weights (must sum to 1.0)
    "score_weight_prob_max":     0.20,
    "score_weight_prob_profit":  0.20,
    "score_weight_risk_reward":  0.20,
    "score_weight_ev":           0.20,
    "score_weight_kelly":        0.20,

    # Score-component normalization
    "rr_full_credit":            3.0,  # R:R that gets full 1.0 credit (capped)
    "kelly_full_credit_pct":     20.0, # Kelly % that gets full credit (capped)

    # Position sizing
    "base_risk_pct":             1.5,  # baseline % of portfolio per spread
    "size_floor_pct":            0.3,  # minimum sizing for score ≥ score_threshold_low
    "size_cap_pct":              5.0,  # absolute cap per single spread

    # Score buckets → size multiplier
    "score_threshold_high":      60,
    "score_threshold_mid":       45,
    "score_threshold_low":       30,
    "size_mult_high":            2.0,
    "size_mult_mid":             1.3,
    "size_mult_low":             0.8,
    "size_mult_below_low":       0.4,

    # Kelly bump
    "kelly_bump_full_pct":       10.0, # Kelly % that yields max bump
    "kelly_bump_max_multiplier": 1.5,  # +150% over baseline at full Kelly
    "kelly_bump_normalizer":     2.5,  # normalize so kelly=0 leaves baseline intact
}
