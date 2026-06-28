"""Asia Session Tracker - monitors SK Hynix, Samsung, ASML before US open."""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, POSITION, TZ_SGT, TZ_ET
from .llm import ask
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".asia_state.json")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Asian semi tickers to track
ASIA_TICKERS = {
    "000660.KS": {"name": "SK Hynix", "flag": "\U0001f1f0\U0001f1f7", "country": "Korea"},
    "005930.KS": {"name": "Samsung", "flag": "\U0001f1f0\U0001f1f7", "country": "Korea"},
    "ASML": {"name": "ASML", "flag": "\U0001f1f3\U0001f1f1", "country": "Netherlands"},
}

# Thresholds
SINGLE_MOVE_PCT = 2.0   # Alert if any single ticker moves >2%
ALIGNED_MOVE_PCT = 1.0  # Alert if all three move same direction >1%


def load_state() -> dict:
    """Load persisted state."""
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_alert_date": None})


def save_state(state: dict):
    """Persist state to disk."""
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def get_daily_change(ticker: str) -> dict:
    """Get today's return for a ticker using yfinance history.

    Returns: {"price": float, "change_pct": float, "ok": bool}
    """
    result = {"price": 0, "change_pct": 0, "ok": False}
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="5d")
        if hist is None or len(hist) < 2:
            return result

        latest_close = float(hist["Close"].iloc[-1] or 0)
        prior_close = float(hist["Close"].iloc[-2] or 0)

        if prior_close == 0 or latest_close == 0:
            return result

        change_pct = (latest_close - prior_close) / prior_close * 100
        result["price"] = latest_close
        result["change_pct"] = round(change_pct, 2)
        result["ok"] = True
    except Exception as e:
        print(f"[ASIA TRACKER] Failed to get data for {ticker}: {e}")
    return result


def get_mu_prev_close() -> float | None:
    """Get MU previous close for context."""
    try:
        t = yf.Ticker(POSITION["ticker"])
        data = t.fast_info
        return data.get("previousClose") or data.get("previous_close")
    except Exception:
        return None


def should_alert(changes: dict) -> bool:
    """Determine if the moves warrant an alert.

    Alert if:
    - Any single ticker moved >2%
    - All three moved in the same direction by >1%
    """
    valid = {k: v for k, v in changes.items() if v["ok"]}
    if not valid:
        return False

    # Check single big move
    for data in valid.values():
        if abs(data["change_pct"]) > SINGLE_MOVE_PCT:
            return True

    # Check aligned move (all same direction >1%)
    if len(valid) >= 3:
        directions = [1 if v["change_pct"] > 0 else -1 for v in valid.values()]
        all_same = all(d == directions[0] for d in directions)
        all_above_threshold = all(abs(v["change_pct"]) > ALIGNED_MOVE_PCT for v in valid.values())
        if all_same and all_above_threshold:
            return True

    return False


def get_deepseek_analysis(changes: dict, mu_close: float | None) -> str:
    """Ask DeepSeek what the Asian semi moves signal for MU."""
    moves_text = []
    for ticker, info in ASIA_TICKERS.items():
        data = changes.get(ticker, {})
        if data.get("ok"):
            moves_text.append(f"{info['name']}: {data['change_pct']:+.1f}%")
        else:
            moves_text.append(f"{info['name']}: no data")

    mu_ctx = f"MU previous close: ${mu_close:.2f}" if mu_close else "MU previous close: unavailable"

    prompt = (
        "You are a semiconductor equity analyst focused on Micron Technology (MU). "
        "The Asian trading session just closed with these moves:\n"
        + "\n".join(moves_text) + "\n"
        f"{mu_ctx}\n\n"
        "In 2-3 concise sentences, analyze what these Asian semi moves signal for MU "
        "when the US market opens tonight. Consider HBM demand signals from SK Hynix, "
        "Samsung competitive dynamics, and ASML equipment demand implications. "
        "Be specific and actionable."
    )

    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="asia_tracker", fallback="DeepSeek analysis unavailable.")


def build_message(changes: dict, mu_close: float | None, analysis: str) -> str:
    """Build the Telegram alert message."""
    lines = []
    lines.append("\U0001f30f <b>ASIA SESSION \u2014 SEMI MOVES</b>")
    lines.append("\u2501" * 20)

    for ticker, info in ASIA_TICKERS.items():
        data = changes.get(ticker, {})
        if data.get("ok"):
            pct = data["change_pct"]
            sign = "+" if pct >= 0 else ""
            lines.append(f"{info['flag']} {info['name']:10s} {sign}{pct:.1f}%")
        else:
            lines.append(f"{info['flag']} {info['name']:10s} n/a")

    lines.append("")
    if mu_close:
        lines.append(f"\U0001f4ca MU prev close: <b>${mu_close:.2f}</b>")
    else:
        lines.append("\U0001f4ca MU prev close: unavailable")

    lines.append("")
    lines.append(f"\U0001f9e0 {analysis}")
    lines.append("")
    lines.append("<code>#ASIA</code>")

    return "\n".join(lines)


def check_asia_session():
    """Main entry point - called from run.py.

    Runs at 5 PM SGT on weekdays. Checks Asian semi moves and alerts if significant.
    """
    try:
        now_sgt = datetime.now(ZoneInfo(TZ_SGT))

        # Only run at 5 PM SGT hour on weekdays (Mon-Fri)
        if now_sgt.hour != 17 or now_sgt.weekday() >= 5:
            return

        today = now_sgt.strftime("%Y-%m-%d")

        # Check if already alerted today
        state = load_state()
        if state.get("last_alert_date") == today:
            return

        # Fetch changes for all tickers
        changes = {}
        for ticker in ASIA_TICKERS:
            changes[ticker] = get_daily_change(ticker)

        # Check if any valid data came back
        valid_count = sum(1 for v in changes.values() if v["ok"])
        if valid_count == 0:
            print("[ASIA TRACKER] No valid data for any ticker, skipping")
            return

        # Only alert if moves are significant
        if not should_alert(changes):
            # Mark as checked even if no alert, so we don't re-check
            state["last_alert_date"] = today
            save_state(state)
            return

        # Get MU context
        mu_close = get_mu_prev_close()

        # Get DeepSeek analysis
        analysis = get_deepseek_analysis(changes, mu_close)

        # Build and send message
        msg = build_message(changes, mu_close, analysis)
        send_alert(msg)

        # Mark as sent
        state["last_alert_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[ASIA TRACKER ERROR] {e}")


if __name__ == "__main__":
    check_asia_session()
