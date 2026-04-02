"""Oil price tracker - monitors crude oil prices and their impact on MU/semis.

Tracks WTI and Brent crude, alerts on significant moves, and correlates
with semiconductor sector impact. During active geopolitical crises
(e.g., Iran war), oil is THE key driver of tech/semi sentiment.

Schedule: Every 15 minutes during futures hours (nearly 24/7)
Tag: #OIL
"""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yfinance as yf

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".oil_state.json")

# Oil tickers
OIL_TICKERS = {
    "CL=F": "WTI Crude",
    "BZ=F": "Brent Crude",
}

# Related tickers for correlation
RELATED_TICKERS = {
    "USO": "US Oil Fund ETF",
    "XLE": "Energy Select Sector",
    "DXY=F": "US Dollar Index",
}

# Alert thresholds
MOVE_THRESHOLD_PCT = 3.0       # Alert on 3%+ move from last alert price
SPIKE_THRESHOLD_PCT = 5.0      # Urgent alert on 5%+ move
LEVEL_ALERTS = [80, 85, 90, 95, 100, 105, 110, 115, 120, 130]  # Key round levels

# MU correlation — rough beta to oil (inverse)
# When oil spikes, MU tends to drop due to stagflation/inflation fear
MU_OIL_BETA = -0.3  # Rough estimate: 10% oil spike -> ~3% MU drag


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_alert_prices": {},
        "last_alert_time": None,
        "crossed_levels": [],
        "session_high": None,
        "session_low": None,
        "session_date": None,
        "alert_count_today": 0,
        "alert_date": None,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_oil_prices() -> dict:
    """Fetch current oil prices."""
    prices = {}
    for ticker, name in OIL_TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            info = t.info
            price = info.get("regularMarketPrice") or info.get("previousClose")
            prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
            day_high = info.get("dayHigh")
            day_low = info.get("dayLow")
            if price:
                prices[ticker] = {
                    "name": name,
                    "price": round(float(price), 2),
                    "prev_close": round(float(prev_close), 2) if prev_close else None,
                    "day_high": round(float(day_high), 2) if day_high else None,
                    "day_low": round(float(day_low), 2) if day_low else None,
                    "change_pct": round((float(price) / float(prev_close) - 1) * 100, 2) if prev_close else 0,
                }
        except Exception:
            continue
    return prices


def _get_oil_history(ticker: str = "CL=F", days: int = 30) -> dict:
    """Get oil price history for context."""
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=f"{days}d")
        if df.empty:
            return {}
        return {
            "current": round(float(df["Close"].iloc[-1]), 2),
            "week_ago": round(float(df["Close"].iloc[-5]), 2) if len(df) >= 5 else None,
            "month_ago": round(float(df["Close"].iloc[0]), 2) if len(df) >= 20 else None,
            "week_change_pct": round((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-5]) - 1) * 100, 1) if len(df) >= 5 else None,
            "month_change_pct": round((float(df["Close"].iloc[-1]) / float(df["Close"].iloc[0]) - 1) * 100, 1) if len(df) >= 20 else None,
            "period_high": round(float(df["High"].max()), 2),
            "period_low": round(float(df["Low"].min()), 2),
        }
    except Exception:
        return {}


def check_oil_prices():
    """Main function — check oil prices and alert on significant moves."""
    state = load_state()
    now = datetime.now(ZoneInfo(TZ_ET))
    today = now.strftime("%Y-%m-%d")

    # Reset daily counter
    if state.get("alert_date") != today:
        state["alert_count_today"] = 0
        state["alert_date"] = today
        state["crossed_levels"] = []
        state["session_high"] = None
        state["session_low"] = None
        state["session_date"] = today

    # Max 10 alerts per day
    if state["alert_count_today"] >= 10:
        save_state(state)
        return

    prices = _get_oil_prices()
    if not prices:
        return

    # Use WTI as primary
    wti = prices.get("CL=F", {})
    brent = prices.get("BZ=F", {})
    primary = wti if wti else brent

    if not primary:
        return

    price = primary["price"]
    name = primary["name"]

    # Update session high/low
    if state.get("session_high") is None or price > state["session_high"]:
        state["session_high"] = price
    if state.get("session_low") is None or price < state["session_low"]:
        state["session_low"] = price

    alerts_to_send = []

    # Check move from last alert price
    last_price = state.get("last_alert_prices", {}).get("CL=F")
    if last_price:
        move_pct = (price / last_price - 1) * 100

        if abs(move_pct) >= SPIKE_THRESHOLD_PCT:
            direction = "SURGING" if move_pct > 0 else "CRASHING"
            mu_est = round(move_pct * MU_OIL_BETA, 1)
            alerts_to_send.append({
                "urgency": "URGENT",
                "msg": f"OIL {direction} {abs(move_pct):.1f}%\n\n"
                       f"{name}: ${price:.2f} (was ${last_price:.2f})\n"
                       f"Brent: ${brent.get('price', 'N/A')}\n\n"
                       f"Estimated MU impact: {mu_est:+.1f}% "
                       f"({'drag' if mu_est < 0 else 'tailwind'})\n\n"
                       f"Session range: ${state.get('session_low', price):.2f} - ${state.get('session_high', price):.2f}",
            })
        elif abs(move_pct) >= MOVE_THRESHOLD_PCT:
            direction = "UP" if move_pct > 0 else "DOWN"
            mu_est = round(move_pct * MU_OIL_BETA, 1)
            alerts_to_send.append({
                "urgency": "ALERT",
                "msg": f"Oil {direction} {abs(move_pct):.1f}% since last alert\n\n"
                       f"{name}: ${price:.2f} (was ${last_price:.2f})\n"
                       f"Brent: ${brent.get('price', 'N/A')}\n\n"
                       f"Est. MU impact: {mu_est:+.1f}%",
            })

    # Check level crossings
    crossed = state.get("crossed_levels", [])
    for level in LEVEL_ALERTS:
        if level in crossed:
            continue
        if last_price and ((last_price < level <= price) or (last_price > level >= price)):
            direction = "ABOVE" if price >= level else "BELOW"
            crossed.append(level)
            # Only alert on key psychological levels
            alerts_to_send.append({
                "urgency": "LEVEL",
                "msg": f"Oil crossed ${level} ({direction})\n\n"
                       f"{name}: ${price:.2f}\n"
                       f"Brent: ${brent.get('price', 'N/A')}",
            })
    state["crossed_levels"] = crossed

    # First run — just set baseline, no alert
    if last_price is None:
        state["last_alert_prices"]["CL=F"] = price
        if brent:
            state["last_alert_prices"]["BZ=F"] = brent["price"]
        save_state(state)
        return

    # Send alerts
    for alert in alerts_to_send:
        if state["alert_count_today"] >= 10:
            break

        # Get context for significant moves
        context = ""
        if alert["urgency"] in ("URGENT", "ALERT"):
            history = _get_oil_history()
            if history:
                parts = []
                if history.get("week_change_pct") is not None:
                    parts.append(f"Week: {history['week_change_pct']:+.1f}%")
                if history.get("month_change_pct") is not None:
                    parts.append(f"Month: {history['month_change_pct']:+.1f}%")
                if history.get("period_high") is not None:
                    parts.append(f"30d range: ${history['period_low']:.0f}-${history['period_high']:.0f}")
                if parts:
                    context = "\n" + " | ".join(parts)

        day_change = primary.get("change_pct", 0)
        header = f"{'🚨' if alert['urgency'] == 'URGENT' else '⛽'} <b>#OIL — {alert['urgency']}</b>"

        msg = f"""{header}

{alert['msg']}

Today: {day_change:+.1f}% from prev close{context}

<i>Oil is currently the #1 driver of MU/tech sentiment.
Higher oil = stagflation fear = tech multiples compress.</i>

#OIL"""

        send_alert(msg)
        state["alert_count_today"] += 1
        state["last_alert_prices"]["CL=F"] = price
        if brent:
            state["last_alert_prices"]["BZ=F"] = brent["price"]
        state["last_alert_time"] = now.isoformat()

    save_state(state)


def get_oil_summary() -> str:
    """Return current oil price summary for other modules."""
    prices = _get_oil_prices()
    if not prices:
        return "Oil: Unable to fetch prices"

    parts = []
    for ticker, data in prices.items():
        parts.append(f"{data['name']}: ${data['price']:.2f} ({data.get('change_pct', 0):+.1f}%)")

    history = _get_oil_history()
    if history and history.get("week_change_pct") is not None:
        parts.append(f"Week: {history['week_change_pct']:+.1f}%")

    return "Oil: " + " | ".join(parts)
