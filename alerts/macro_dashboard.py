"""Macro dashboard - tracks institutional indicators, cross-asset signals, and market regime.

Sends a daily dashboard to Telegram + alerts on significant threshold changes.
Data sources: yfinance (free), alternative.me (free), CBOE scraping.
"""

import json
import os
import requests
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import (
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL_PRO, POSITION, position_summary,
    TZ_ET, TZ_SGT, get_commodity_levels,
)
from .bot import send_alert
from .llm import ask

STATE_FILE = os.path.join(os.path.dirname(__file__), ".macro_state.json")

# === INDICATOR DEFINITIONS ===

# yfinance tickers for market data
YF_TICKERS = {
    # Volatility & Sentiment
    "^VIX": "VIX",
    "^SKEW": "CBOE SKEW",
    "^MOVE": "MOVE Index",
    # Currencies
    "DX-Y.NYB": "DXY",
    "USDKRW=X": "USD/KRW",
    "USDCNY=X": "USD/CNY",
    "USDTWD=X": "USD/TWD",
    # Commodities
    "CL=F": "Crude Oil",
    "GC=F": "Gold",
    "HG=F": "Copper",
    "NG=F": "Nat Gas",
    "BTC-USD": "Bitcoin",
    # Bonds & Yields
    "^IRX": "3M Yield",
    "^FVX": "5Y Yield",
    "^TNX": "10Y Yield",
    "^TYX": "30Y Yield",
    # Credit
    "HYG": "HY Corp Bond",
    "LQD": "IG Corp Bond",
    # Semis
    "^SOX": "SOX Index",
    "SMH": "SMH ETF",
    # Market
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    # Sector rotation
    "XLK": "Tech",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLU": "Utilities",
    "TLT": "20Y+ Treasury",
    # Credit spreads (HY-IG spread = risk appetite)
    "JNK": "SPDR HY Bond",
    # Power grid / data center demand
    "GRID": "First Trust NASDAQ Clean Edge Smart Grid Infra",
    "NLR": "VanEck Uranium+Nuclear",
}

# Static regime thresholds — these encode well-known cutoffs (VIX 20/30/40
# fear regimes; Fear & Greed quintiles; yield 4%/5% headlines). CL=F and GC=F
# are computed dynamically from spot via get_alert_thresholds() below.
STATIC_ALERT_THRESHOLDS = {
    "^VIX": [20, 25, 30, 35, 40],
    "^SKEW": [130, 140, 150, 160],
    "^MOVE": [100, 120, 140],
    "DX-Y.NYB": [95, 100, 105, 110],
    "^TNX": [3.5, 4.0, 4.5, 5.0],
    "fear_greed": [20, 25, 40, 60, 75, 80],
}


def get_alert_thresholds(indicators: dict) -> dict:
    """Threshold dict combining static regime levels with dynamic commodity
    ladders generated from the latest spot for oil and gold."""
    thresholds = dict(STATIC_ALERT_THRESHOLDS)
    cl = (indicators.get("CL=F") or {}).get("value")
    gc = (indicators.get("GC=F") or {}).get("value")
    if cl:
        thresholds["CL=F"] = get_commodity_levels(cl)
    if gc:
        thresholds["GC=F"] = get_commodity_levels(gc)
    return thresholds

# Labels for Fear & Greed
FG_LABELS = {
    (0, 25): "Extreme Fear",
    (25, 45): "Fear",
    (45, 55): "Neutral",
    (55, 75): "Greed",
    (75, 100): "Extreme Greed",
}


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {
        "last_values": {},
        "alerted_levels": {},
        "last_dashboard": None,
        "date": None,
    })


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _get_price(ticker: str) -> float | None:
    """Get latest price from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        price = info.get("regularMarketPrice") or info.get("currentPrice")
        if price:
            return float(price)
        fi = t.fast_info
        return fi.get("lastPrice") or fi.get("last_price")
    except Exception:
        return None


def _get_price_fast(ticker: str) -> float | None:
    """Get latest price using fast_info only (cheaper API call)."""
    try:
        fi = yf.Ticker(ticker).fast_info
        return fi.get("lastPrice") or fi.get("last_price")
    except Exception:
        return None


def _get_fear_greed() -> dict | None:
    """Fetch Fear & Greed index from alternative.me (free, no key)."""
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        r.raise_for_status()
        data = r.json()
        entry = data["data"][0]
        score = int(entry["value"])
        label = entry["value_classification"]
        return {"score": score, "label": label}
    except Exception:
        return None


def _get_prev_close(ticker: str) -> float | None:
    """Get previous close."""
    try:
        fi = yf.Ticker(ticker).fast_info
        return fi.get("previousClose") or fi.get("previous_close")
    except Exception:
        return None


def _calc_change(current: float, previous: float) -> tuple[float, str]:
    """Calculate % change and format arrow."""
    if not previous or previous == 0:
        return 0.0, ""
    pct = ((current - previous) / previous) * 100
    arrow = "\u25b2" if pct > 0 else "\u25bc" if pct < 0 else "\u25ac"
    return pct, arrow


def fetch_all_indicators() -> dict:
    """Fetch all market indicators. Returns dict of {name: {value, prev, change_pct}}."""
    results = {}

    for ticker, name in YF_TICKERS.items():
        price = _get_price_fast(ticker)
        prev = _get_prev_close(ticker)
        if price is not None:
            pct, arrow = _calc_change(price, prev)
            results[ticker] = {
                "name": name,
                "value": price,
                "prev": prev,
                "change_pct": pct,
                "arrow": arrow,
            }

    # Fear & Greed
    fg = _get_fear_greed()
    if fg:
        results["fear_greed"] = {
            "name": "Fear & Greed",
            "value": fg["score"],
            "label": fg["label"],
            "prev": None,
            "change_pct": 0,
            "arrow": "",
        }

    # Derived indicators
    tnx = results.get("^TNX", {}).get("value")
    irx = results.get("^IRX", {}).get("value")
    fvx = results.get("^FVX", {}).get("value")

    # Yield curve: 10Y - 3M
    if tnx and irx:
        spread = tnx - irx
        results["yield_curve_10y3m"] = {
            "name": "10Y-3M Spread",
            "value": spread,
            "prev": None,
            "change_pct": 0,
            "arrow": "\u25b2" if spread > 0 else "\u25bc",
            "unit": "bps",
        }

    # Yield curve: 10Y - 5Y (proxy for 10Y-2Y)
    if tnx and fvx:
        spread = tnx - fvx
        results["yield_curve_10y5y"] = {
            "name": "10Y-5Y Spread",
            "value": spread,
            "prev": None,
            "change_pct": 0,
            "arrow": "\u25b2" if spread > 0 else "\u25bc",
        }

    # Credit spread proxy: HYG yield - LQD yield (via price ratio)
    hyg = results.get("HYG", {}).get("value")
    lqd = results.get("LQD", {}).get("value")
    if hyg and lqd:
        # Lower HYG price relative to LQD = wider credit spreads = risk-off
        ratio = hyg / lqd
        results["credit_ratio"] = {
            "name": "HYG/LQD Ratio",
            "value": round(ratio, 4),
            "prev": None,
            "change_pct": 0,
            "arrow": "",
        }

    # Copper/Gold ratio (risk appetite)
    cu = results.get("HG=F", {}).get("value")
    gold = results.get("GC=F", {}).get("value")
    if cu and gold:
        ratio = (cu * 1000) / gold  # Normalize copper to per-ton equivalent
        results["copper_gold"] = {
            "name": "Copper/Gold",
            "value": round(ratio, 3),
            "prev": None,
            "change_pct": 0,
            "arrow": "",
        }

    return results


def check_macro_alerts():
    """Check for threshold crossings on key indicators."""
    state = load_state()
    now_et = datetime.now(ZoneInfo(TZ_ET))
    today = now_et.strftime("%Y-%m-%d")

    # Reset daily alerts
    if state.get("date") != today:
        state["alerted_levels"] = {}
        state["date"] = today

    indicators = fetch_all_indicators()

    for ticker, levels in get_alert_thresholds(indicators).items():
        data = indicators.get(ticker)
        if not data:
            continue

        current = data["value"]
        last = state.get("last_values", {}).get(ticker)
        if last is None:
            state.setdefault("last_values", {})[ticker] = current
            continue

        alerted = state.get("alerted_levels", {}).get(ticker, [])

        for level in levels:
            level_key = str(level)
            if level_key in alerted:
                continue

            crossed = False
            direction = ""
            if last < level <= current:
                crossed = True
                direction = "above"
            elif last > level >= current:
                crossed = True
                direction = "below"

            if crossed:
                name = data["name"]
                emoji = "\U0001f534" if direction == "below" else "\U0001f7e2"
                if ticker == "^VIX" or ticker == "CL=F":
                    # VIX up = bad, Oil up = bad
                    emoji = "\U0001f534" if direction == "above" else "\U0001f7e2"

                msg = (
                    f"{emoji} <b>MACRO: {name} crossed {level} ({direction})</b>\n"
                    f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                    f"\U0001f4ca {name}: <b>{current:.2f}</b> (prev: {last:.2f})\n"
                )

                # Add context for key indicators
                if ticker == "fear_greed":
                    label = data.get("label", "")
                    msg += f"\U0001f3ad Sentiment: {label}\n"
                elif ticker == "^VIX":
                    msg += f"\u26a0\ufe0f {'Elevated fear' if current > 25 else 'Calm'}\n"
                elif ticker == "CL=F":
                    msg += f"\U0001f6e2\ufe0f Oil {'spiking - inflation risk' if direction == 'above' else 'falling - easing pressure'}\n"

                msg += f"\n<code>#MACRO</code>"
                send_alert(msg)
                state.setdefault("alerted_levels", {}).setdefault(ticker, []).append(level_key)

        state.setdefault("last_values", {})[ticker] = current

    save_state(state)
    return indicators


def build_dashboard(indicators: dict | None = None) -> str:
    """Build the full macro dashboard message."""
    if indicators is None:
        indicators = fetch_all_indicators()

    now_sgt = datetime.now(ZoneInfo(TZ_SGT))

    def fmt(ticker, decimals=2, prefix="", suffix=""):
        d = indicators.get(ticker, {})
        v = d.get("value")
        if v is None:
            return "N/A"
        pct = d.get("change_pct", 0)
        arrow = d.get("arrow", "")
        sign = "+" if pct > 0 else ""
        return f"{prefix}{v:,.{decimals}f}{suffix} {arrow}{sign}{pct:.1f}%"

    # Fear & Greed
    fg = indicators.get("fear_greed", {})
    fg_score = fg.get("value", "N/A")
    fg_label = fg.get("label", "")
    fg_bar = ""
    if isinstance(fg_score, (int, float)):
        filled = int(fg_score / 5)
        fg_bar = "\u2588" * filled + "\u2591" * (20 - filled)

    # Yield curve
    yc = indicators.get("yield_curve_10y3m", {})
    yc_val = yc.get("value")
    yc_str = f"{yc_val:+.2f}%" if yc_val is not None else "N/A"
    yc_signal = "NORMAL" if yc_val and yc_val > 0 else "INVERTED \u26a0\ufe0f"

    msg = (
        f"\U0001f4ca <b>MACRO DASHBOARD</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f552 {now_sgt.strftime('%b %d %H:%M SGT')}\n\n"

        f"\U0001f3ad <b>SENTIMENT</b>\n"
        f"Fear & Greed: <b>{fg_score}</b> ({fg_label})\n"
        f"<code>{fg_bar}</code>\n"
        f"VIX: {fmt('^VIX')}\n"
        f"SKEW: {fmt('^SKEW', 0)}\n"
        f"MOVE: {fmt('^MOVE')}\n\n"

        f"\U0001f4b5 <b>RATES & CURVE</b>\n"
        f"10Y: {fmt('^TNX')}  |  30Y: {fmt('^TYX')}\n"
        f"5Y: {fmt('^FVX')}  |  3M: {fmt('^IRX')}\n"
        f"10Y-3M: {yc_str} ({yc_signal})\n\n"

        f"\U0001f4b0 <b>CURRENCIES</b>\n"
        f"DXY: {fmt('DX-Y.NYB')}\n"
        f"USD/KRW: {fmt('USDKRW=X', 0)}\n"
        f"USD/CNY: {fmt('USDCNY=X', 2)}\n"
        f"USD/TWD: {fmt('USDTWD=X', 2)}\n\n"

        f"\U0001f6e2\ufe0f <b>COMMODITIES</b>\n"
        f"Crude: {fmt('CL=F', 2, '$')}\n"
        f"Gold: {fmt('GC=F', 0, '$')}\n"
        f"Copper: {fmt('HG=F', 2, '$')}\n"
        f"Nat Gas: {fmt('NG=F', 2, '$')}\n\n"

        f"\U0001f4c8 <b>CREDIT & RISK</b>\n"
        f"HYG: {fmt('HYG')}  |  LQD: {fmt('LQD')}\n"
        f"TLT: {fmt('TLT')}\n"
        f"BTC: {fmt('BTC-USD', 0, '$')}\n\n"

        f"\U0001f9e0 <b>SEMIS & MARKET</b>\n"
        f"SOX: {fmt('^SOX', 0)}\n"
        f"SMH: {fmt('SMH')}\n"
        f"SPY: {fmt('SPY')}  |  QQQ: {fmt('QQQ')}\n\n"

        f"\U0001f504 <b>SECTOR ROTATION</b>\n"
        f"Tech: {fmt('XLK')}  |  Energy: {fmt('XLE')}\n"
        f"Financials: {fmt('XLF')}  |  Utilities: {fmt('XLU')}\n\n"

        f"<code>#MACRO_DASH</code>"
    )
    return msg


def send_dashboard():
    """Fetch all indicators and send the dashboard to Telegram."""
    indicators = fetch_all_indicators()
    msg = build_dashboard(indicators)
    send_alert(msg)

    # Also send AI interpretation
    _send_ai_interpretation(indicators)


def _send_ai_interpretation(indicators: dict):
    """Use DeepSeek to interpret the macro picture for MU."""
    # Build context string
    context_parts = []
    for key, data in indicators.items():
        name = data.get("name", key)
        val = data.get("value")
        pct = data.get("change_pct", 0)
        if val is not None:
            context_parts.append(f"{name}: {val:.2f} ({pct:+.1f}%)")

    context = "\n".join(context_parts)

    prompt = (
        f"You are a macro strategist advising a trader holding {position_summary()} "
        f"expiring March 20, 2026.\n\n"
        f"Today's macro indicators:\n{context}\n\n"
        f"In 100 words max, answer:\n"
        f"1. REGIME: Risk-on, risk-off, or transitioning?\n"
        f"2. KEY SIGNAL: What's the single most important indicator right now?\n"
        f"3. MU IMPACT: How does this macro picture affect the MU spread?\n"
        f"4. WATCH: What should change to flip the thesis?\n"
    )

    try:
        analysis = ask(prompt, tier="reasoning", temperature=0.3, max_tokens=2000,
                       label="macro_dashboard", fallback="(interpretation unavailable)")

        msg = (
            f"\U0001f9e0 <b>MACRO INTERPRETATION</b>\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{analysis}\n\n"
            f"<code>#MACRO_AI</code>"
        )
        send_alert(msg)
    except Exception as e:
        print(f"[MACRO AI ERROR] {e}")


if __name__ == "__main__":
    send_dashboard()
