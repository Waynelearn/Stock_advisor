"""Telegram alert configuration."""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# DeepSeek API for news summarization + sentiment
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# MU spread position (500x 400/420 BCS, entered March 21)
POSITION = {
    "ticker": "MU",
    "long_strike": 400,
    "short_strike": 420,
    "contracts": 500,
    "entry_price": 10.886,
    "expiry": "2026-05-01",
    "breakeven": 410.886,
}

# Price levels to alert on (crossed in either direction)
PRICE_LEVELS = [380, 385, 390, 395, 400, 405, 410, 415, 420, 425, 430, 440, 450]

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

# Peer earnings sympathy - historical correlation coefficients (approximate)
PEER_CORRELATIONS = {
    "NVDA": 0.72,
    "AMD": 0.68,
    "AVGO": 0.55,
    "MRVL": 0.60,
}

# SEC EDGAR - Micron CIK
MU_CIK = "0000723125"

# Expiry week escalation - days before expiry to ramp up monitoring
EXPIRY_ESCALATION_DAYS = 5
