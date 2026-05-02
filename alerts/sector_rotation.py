"""Sector rotation tracker - monitors fund flows into/out of semis vs other sectors."""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".sector_rotation_state.json")

SECTOR_ETFS = {
    "SOXX": "Semiconductors",
    "SMH": "VanEck Semis",
    "XLK": "Tech",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLP": "Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "XLC": "Communication",
    "XLB": "Materials",
    "XLY": "Consumer Disc",
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Small Cap",
}

# Defensive sectors (outperform in risk-off)
DEFENSIVE = {"XLU", "XLP", "XLV"}
# Growth/risk-on sectors
GROWTH = {"XLK", "SOXX", "SMH", "XLY", "XLC"}

# Thresholds
RELATIVE_PERF_THRESHOLD = 2.0  # SOXX vs SPY relative perf to alert (%)
VOLUME_RATIO_THRESHOLD = 1.5   # SOXX volume vs 20-day avg


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_alert_date": None, "last_pattern": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _get_returns(ticker: str, period: str = "1mo") -> dict:
    """Get 1-day and 5-day returns for a ticker."""
    try:
        hist = yf.Ticker(ticker).history(period=period)
        if hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"]
        ret_1d = (closes.iloc[-1] / closes.iloc[-2] - 1) * 100 if len(closes) >= 2 else 0
        ret_5d = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100 if len(closes) >= 6 else 0
        ret_20d = (closes.iloc[-1] / closes.iloc[0] - 1) * 100
        avg_vol = hist["Volume"].mean() if "Volume" in hist.columns else 0
        last_vol = hist["Volume"].iloc[-1] if "Volume" in hist.columns else 0
        return {
            "ret_1d": ret_1d,
            "ret_5d": ret_5d,
            "ret_20d": ret_20d,
            "price": closes.iloc[-1],
            "vol_ratio": last_vol / avg_vol if avg_vol > 0 else 1.0,
        }
    except Exception:
        return None


def _detect_pattern(returns: dict) -> tuple:
    """Detect rotation pattern from sector returns. Returns (pattern_name, description)."""
    soxx = returns.get("SOXX")
    spy = returns.get("SPY")
    xlk = returns.get("XLK")

    if not soxx or not spy or not xlk:
        return None, None

    soxx_vs_spy_5d = soxx["ret_5d"] - spy["ret_5d"]
    soxx_vs_xlk_5d = soxx["ret_5d"] - xlk["ret_5d"]

    # Check defensive outperformance
    defensive_avg = 0
    d_count = 0
    for s in DEFENSIVE:
        if s in returns and returns[s]:
            defensive_avg += returns[s]["ret_5d"]
            d_count += 1
    defensive_avg = defensive_avg / d_count if d_count > 0 else 0

    growth_avg = 0
    g_count = 0
    for s in GROWTH:
        if s in returns and returns[s]:
            growth_avg += returns[s]["ret_5d"]
            g_count += 1
    growth_avg = growth_avg / g_count if g_count > 0 else 0

    # Risk-off: defensives up, growth down
    if defensive_avg > 0 and growth_avg < 0 and (defensive_avg - growth_avg) > 2.0:
        return "RISK_OFF", "Defensive sectors outperforming while growth/semis underperform"

    # Risk-on: growth up, defensives flat/down
    if growth_avg > 1.0 and growth_avg > defensive_avg + 1.5:
        return "RISK_ON", "Growth and semis leading the market — risk-on environment"

    # Rotation out of semis: tech up but semis down
    if soxx_vs_xlk_5d < -2.0:
        return "ROTATION_OUT_SEMIS", "Tech rising but semis lagging — money rotating within tech away from semis"

    # Rotation into semis: semis outperforming tech
    if soxx_vs_xlk_5d > 2.0:
        return "ROTATION_INTO_SEMIS", "Semis outperforming broader tech — sector-specific catalyst driving inflows"

    # SOXX significant underperformance vs SPY
    if soxx_vs_spy_5d < -RELATIVE_PERF_THRESHOLD:
        return "SEMI_UNDERPERFORM", f"SOXX underperforming SPY by {abs(soxx_vs_spy_5d):.1f}% over 5 days"

    # SOXX significant outperformance vs SPY
    if soxx_vs_spy_5d > RELATIVE_PERF_THRESHOLD:
        return "SEMI_OUTPERFORM", f"SOXX outperforming SPY by {soxx_vs_spy_5d:.1f}% over 5 days"

    return None, None


def check_sector_rotation():
    """Main function - check for sector rotation patterns."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_alert_date") == today:
        return

    # Fetch returns for all sectors
    returns = {}
    for ticker in SECTOR_ETFS:
        data = _get_returns(ticker)
        if data:
            returns[ticker] = data

    if len(returns) < 5:
        return

    pattern, description = _detect_pattern(returns)
    if not pattern:
        return

    # Don't re-alert same pattern
    if pattern == state.get("last_pattern") and state.get("last_alert_date") == today:
        return

    soxx = returns.get("SOXX", {})
    spy = returns.get("SPY", {})
    soxx_vs_spy = soxx.get("ret_5d", 0) - spy.get("ret_5d", 0) if soxx and spy else 0

    # Build outperformers / underperformers lists
    sorted_5d = sorted(
        [(t, returns[t]) for t in returns if t not in ("SPY", "QQQ", "IWM")],
        key=lambda x: x[1]["ret_5d"],
        reverse=True,
    )
    top_3 = sorted_5d[:3]
    bottom_3 = sorted_5d[-3:]

    # Volume note
    vol_note = ""
    if soxx and soxx.get("vol_ratio", 1) > VOLUME_RATIO_THRESHOLD:
        vol_note = f"\nSOXX Volume: {soxx['vol_ratio']:.1f}x avg (elevated)"

    # Impact assessment
    impact_map = {
        "RISK_OFF": "Institutional money rotating into defensive sectors. Headwind for MU momentum.",
        "RISK_ON": "Growth-friendly environment. Tailwind for MU and semi sector.",
        "ROTATION_OUT_SEMIS": "Semi-specific weakness within tech. Watch for sector headwinds on MU.",
        "ROTATION_INTO_SEMIS": "Semi sector seeing inflows. Positive momentum for MU.",
        "SEMI_UNDERPERFORM": "Semis lagging the market. MU may face passive selling pressure.",
        "SEMI_OUTPERFORM": "Semis leading the market. MU benefits from sector momentum.",
    }

    msg = f"""SECTOR ROTATION ALERT

Pattern: {pattern.replace("_", " ").title()}
{description}
Period: 5-day

Outperforming:
"""
    for t, d in top_3:
        msg += f"  {t} ({SECTOR_ETFS[t]}): {d['ret_5d']:+.1f}%\n"

    msg += "\nUnderperforming:\n"
    for t, d in bottom_3:
        msg += f"  {t} ({SECTOR_ETFS[t]}): {d['ret_5d']:+.1f}%\n"

    msg += f"\nSOXX vs SPY (5d): {soxx_vs_spy:+.1f}% relative"
    msg += vol_note
    msg += f"\n\nImpact: {impact_map.get(pattern, 'Monitor for continued rotation.')}"
    msg += "\n\n#SECTOR_ROTATION"

    send_alert(msg)

    state["last_alert_date"] = today
    state["last_pattern"] = pattern
    save_state(state)


def get_sector_heatmap() -> str:
    """Return formatted sector heatmap for use by daily briefing."""
    returns = {}
    for ticker in SECTOR_ETFS:
        data = _get_returns(ticker, period="1mo")
        if data:
            returns[ticker] = data

    if not returns:
        return "Sector data unavailable"

    sorted_1d = sorted(
        [(t, returns[t]) for t in returns],
        key=lambda x: x[1]["ret_1d"],
        reverse=True,
    )

    lines = ["Sector Heatmap:"]
    lines.append(f"{'Sector':<20} {'1D':>7} {'5D':>7} {'20D':>7}")
    lines.append("-" * 45)
    for t, d in sorted_1d:
        name = SECTOR_ETFS[t]
        lines.append(f"{name:<20} {d['ret_1d']:>+6.1f}% {d['ret_5d']:>+6.1f}% {d['ret_20d']:>+6.1f}%")

    return "\n".join(lines)
