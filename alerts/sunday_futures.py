"""Sunday futures open + global markets Monday preview.

Sunday 6 PM ET = Monday 7 AM SGT — futures open for the week.
The first 30 min sets the tone. Also tracks Asian markets (Nikkei, KOSPI, TAIEX)
that open before US, giving early read on Monday semi sentiment.
"""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT, FUTURES
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".sunday_futures_state.json")

# Asian indices relevant for semis
ASIAN_INDICES = {
    "^N225": "Nikkei 225",
    "^KS11": "KOSPI",
    "^TWII": "TAIEX",
    "000660.KS": "SK Hynix",
    "005930.KS": "Samsung",
}

# Alert thresholds
FUTURES_OPEN_THRESHOLD = 0.3   # Alert if futures gap >0.3% at Sunday open
ASIAN_MOVE_THRESHOLD = 1.0     # Alert if Asian index moves >1%


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_futures_alert": None, "last_asia_monday_alert": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def check_sunday_futures_open():
    """Alert on Sunday futures open — first look at the week ahead.

    Runs Sunday evening ET / Monday morning SGT.
    """
    now_et = datetime.now(ZoneInfo(TZ_ET))
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))

    # Only run Sunday 6-8 PM ET (futures open at 6 PM ET Sunday)
    if now_et.weekday() != 6 or now_et.hour < 18 or now_et.hour > 20:
        return

    state = load_state()
    week_key = now_sgt.strftime("%Y-W%W")

    if state.get("last_futures_alert") == week_key:
        return

    # Fetch futures prices
    gaps = {}
    for ticker, name in FUTURES.items():
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            current = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("regularMarketPreviousClose")

            if current and prev_close and prev_close > 0:
                gap_pct = (float(current) / float(prev_close) - 1) * 100
                gaps[ticker] = {
                    "name": name,
                    "current": float(current),
                    "prev_close": float(prev_close),
                    "gap_pct": gap_pct,
                }
        except Exception:
            continue

    if not gaps:
        return

    # Check threshold
    max_gap = max(abs(g["gap_pct"]) for g in gaps.values())
    if max_gap < FUTURES_OPEN_THRESHOLD:
        # Still send a "flat open" summary if it's the first check of the week
        pass

    # Determine sentiment
    avg_gap = sum(g["gap_pct"] for g in gaps.values()) / len(gaps)
    if avg_gap > 0.5:
        sentiment = "Bullish week-open — positive momentum"
    elif avg_gap > 0.2:
        sentiment = "Mildly bullish — slight positive bias"
    elif avg_gap < -0.5:
        sentiment = "Bearish week-open — risk-off start"
    elif avg_gap < -0.2:
        sentiment = "Mildly bearish — slight negative bias"
    else:
        sentiment = "Flat open — no strong directional signal"

    # Estimate MU Monday open
    nq_gap = gaps.get("NQ=F", {}).get("gap_pct", 0)
    mu_est = nq_gap * 1.4  # MU beta to NQ

    try:
        mu_info = yf.Ticker(POSITION["ticker"]).info or {}
        mu_prev = float(mu_info.get("regularMarketPreviousClose", 0))
    except Exception:
        mu_prev = 0

    msg = "SUNDAY FUTURES OPEN\n\n"
    msg += "Week-Opening Gaps:\n"

    for ticker, g in sorted(gaps.items(), key=lambda x: abs(x[1]["gap_pct"]), reverse=True):
        short_names = {"ES=F": "ES (S&P)", "NQ=F": "NQ (Nasdaq)", "YM=F": "YM (Dow)"}
        label = short_names.get(ticker, g["name"])
        msg += f"  {label}: {g['gap_pct']:+.2f}% -> {g['current']:,.0f}\n"

    if mu_prev:
        mu_est_price = mu_prev * (1 + mu_est / 100)
        msg += f"\nEstimated MU Monday Open: ${mu_est_price:,.0f} ({mu_est:+.1f}%)\n"
        msg += f"Friday Close: ${mu_prev:,.2f}\n"

    msg += f"\nSentiment: {sentiment}\n"
    msg += "\n#SUNDAY_OPEN"

    send_alert(msg)

    state["last_futures_alert"] = week_key
    save_state(state)


def check_monday_asia_preview():
    """Monday morning Asia session preview — runs before US market opens.

    Asian markets open 8-9 AM local = well before US 9:30 AM ET.
    User in SGT can see Asian reaction first.
    """
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))

    # Monday 10 AM SGT (after Asian markets have been trading ~1 hour)
    if now_sgt.weekday() != 0 or now_sgt.hour != 10:
        return

    state = load_state()
    today = now_sgt.strftime("%Y-%m-%d")

    if state.get("last_asia_monday_alert") == today:
        return

    moves = {}
    for ticker, name in ASIAN_INDICES.items():
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            current = info.get("regularMarketPrice") or info.get("currentPrice")
            prev_close = info.get("regularMarketPreviousClose")

            if current and prev_close and prev_close > 0:
                change_pct = (float(current) / float(prev_close) - 1) * 100
                moves[ticker] = {
                    "name": name,
                    "current": float(current),
                    "change_pct": change_pct,
                }
        except Exception:
            continue

    if not moves:
        return

    # Semi-specific signals
    hynix = moves.get("000660.KS", {})
    samsung = moves.get("005930.KS", {})
    semi_signal = ""
    if hynix and samsung:
        avg_semi = (hynix.get("change_pct", 0) + samsung.get("change_pct", 0)) / 2
        if avg_semi > 1:
            semi_signal = "Memory stocks strong in Asia — bullish signal for MU"
        elif avg_semi < -1:
            semi_signal = "Memory stocks weak in Asia — watch for MU sympathy selling"
        else:
            semi_signal = "Memory stocks flat in Asia — neutral for MU"

    # Overall Asian sentiment
    index_moves = {k: v for k, v in moves.items() if k.startswith("^")}
    if index_moves:
        avg_index = sum(m["change_pct"] for m in index_moves.values()) / len(index_moves)
        if avg_index > 0.5:
            asia_sentiment = "Asia green — positive backdrop for US open"
        elif avg_index < -0.5:
            asia_sentiment = "Asia red — cautious backdrop for US open"
        else:
            asia_sentiment = "Asia mixed — no strong signal for US"
    else:
        asia_sentiment = "Index data unavailable"

    msg = "MONDAY ASIA PREVIEW\n\n"
    msg += "Asian Markets (early Monday):\n"

    # Indices first
    for ticker, data in moves.items():
        if ticker.startswith("^"):
            msg += f"  {data['name']}: {data['change_pct']:+.1f}%\n"

    # Semis
    msg += "\nMemory/Semi Stocks:\n"
    for ticker, data in moves.items():
        if not ticker.startswith("^"):
            msg += f"  {data['name']}: {data['change_pct']:+.1f}%\n"

    msg += f"\n{asia_sentiment}\n"
    if semi_signal:
        msg += f"{semi_signal}\n"

    msg += "\n#MONDAY_PREVIEW"

    send_alert(msg)

    state["last_asia_monday_alert"] = today
    save_state(state)
