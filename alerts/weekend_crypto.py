"""Weekend crypto sentiment proxy - BTC/ETH moves as Monday preview signal."""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".weekend_crypto_state.json")

CRYPTO_TICKERS = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
}

# Thresholds
SINGLE_MOVE_PCT = 3.0       # Alert if any single crypto moves >3%
CONSENSUS_MOVE_PCT = 2.0    # Alert if all move same direction >2%
CHECK_INTERVAL_HOURS = 4    # Don't re-alert within 4 hours


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_alert": None, "friday_closes": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _capture_friday_close():
    """Capture Friday close prices as weekend baseline."""
    state = load_state()
    closes = {}
    for ticker in CRYPTO_TICKERS:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                closes[ticker] = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    if closes:
        state["friday_closes"] = closes
        state["friday_date"] = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")
        save_state(state)


def check_weekend_crypto():
    """Main function - check crypto moves during weekend as Monday sentiment proxy."""
    now_et = datetime.now(ZoneInfo(TZ_ET))
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))

    # Only run on weekends (Saturday/Sunday ET)
    if now_et.weekday() < 5:
        # Friday: capture close prices for baseline
        if now_et.weekday() == 4 and now_et.hour == 16 and now_et.minute == 0:
            _capture_friday_close()
        return

    state = load_state()

    # Rate limit
    if state.get("last_alert"):
        try:
            last = datetime.fromisoformat(state["last_alert"])
            if (now_sgt - last.astimezone(ZoneInfo(TZ_SGT))).total_seconds() < CHECK_INTERVAL_HOURS * 3600:
                return
        except Exception:
            pass

    friday_closes = state.get("friday_closes", {})

    # Get current crypto prices
    moves = {}
    for ticker, name in CRYPTO_TICKERS.items():
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            current = info.get("regularMarketPrice") or info.get("currentPrice")

            if not current:
                hist = t.history(period="2d")
                if not hist.empty:
                    current = float(hist["Close"].iloc[-1])

            if not current:
                continue

            current = float(current)
            baseline = friday_closes.get(ticker, current)

            if baseline and baseline > 0:
                change_pct = (current / baseline - 1) * 100
            else:
                change_pct = 0

            moves[ticker] = {
                "name": name,
                "current": current,
                "baseline": baseline,
                "change_pct": change_pct,
            }
        except Exception:
            continue

    if not moves:
        return

    # Check alert conditions
    should_alert = False
    alert_reasons = []

    # Any single crypto >3%
    for ticker, data in moves.items():
        if abs(data["change_pct"]) >= SINGLE_MOVE_PCT:
            should_alert = True
            direction = "up" if data["change_pct"] > 0 else "down"
            alert_reasons.append(f"{data['name']} {direction} {abs(data['change_pct']):.1f}%")

    # Consensus move
    if len(moves) >= 2:
        directions = [1 if m["change_pct"] > 0 else -1 for m in moves.values()]
        pcts = [abs(m["change_pct"]) for m in moves.values()]
        if abs(sum(directions)) == len(directions) and min(pcts) >= CONSENSUS_MOVE_PCT:
            should_alert = True
            direction = "bullish" if directions[0] > 0 else "bearish"
            alert_reasons.append(f"Consensus {direction} across all crypto")

    if not should_alert:
        return

    # Determine Monday outlook
    avg_move = sum(m["change_pct"] for m in moves.values()) / len(moves)
    if avg_move > 3:
        outlook = "Risk-on sentiment strong — expect positive Monday open"
        impact = "Bullish tailwind for growth stocks including MU"
    elif avg_move > 1:
        outlook = "Mildly risk-on — slight positive bias for Monday"
        impact = "Neutral to slightly bullish for MU"
    elif avg_move < -3:
        outlook = "Risk-off sentiment building — expect weak Monday open"
        impact = "Bearish headwind for growth stocks, watch for gap down"
    elif avg_move < -1:
        outlook = "Mildly risk-off — slight negative bias for Monday"
        impact = "Neutral to slightly bearish for MU"
    else:
        outlook = "Mixed crypto signals — no strong directional bias"
        impact = "Neutral for MU"

    day = "Saturday" if now_et.weekday() == 5 else "Sunday"

    msg = f"WEEKEND CRYPTO SENTIMENT ({day})\n\n"
    msg += "Crypto vs Friday Close:\n"

    for ticker, data in sorted(moves.items(), key=lambda x: abs(x[1]["change_pct"]), reverse=True):
        arrow = "+" if data["change_pct"] > 0 else ""
        msg += f"  {data['name']}: ${data['current']:,.0f} ({arrow}{data['change_pct']:.1f}%)\n"

    msg += f"\nMonday Outlook: {outlook}\n"
    msg += f"MU Impact: {impact}\n"
    msg += "\n#CRYPTO_SENTIMENT"

    send_alert(msg)

    state["last_alert"] = now_sgt.isoformat()
    save_state(state)


def get_crypto_sentiment() -> str:
    """Return current crypto sentiment summary for other modules."""
    moves = {}
    for ticker, name in CRYPTO_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="3d")
            if len(hist) >= 2:
                change = (hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100
                moves[name] = change
        except Exception:
            pass

    if not moves:
        return "Crypto: data unavailable"

    parts = [f"{name}: {pct:+.1f}%" for name, pct in moves.items()]
    return "Crypto: " + " | ".join(parts)
