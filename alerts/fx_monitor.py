"""Foreign exchange monitor — USD/JPY, USD/KRW weekend moves and impact on semis.

FX trades Sunday night. Weak dollar = bullish for US semis revenue.
Strong yen = BOJ risk. KRW moves affect SK Hynix/Samsung competitiveness.
"""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import (
    POSITION, TZ_ET, TZ_SGT,
    FX_MOVE_PCT as FX_MOVE_THRESHOLD,
    FX_DXY_MOVE_PCT as DXY_MOVE_THRESHOLD,
)
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".fx_monitor_state.json")

FX_PAIRS = {
    "JPY=X": {"name": "USD/JPY", "impact": "Weak USD (lower USD/JPY) boosts US semi revenue in yen terms. Strong yen = BOJ tightening risk."},
    "KRW=X": {"name": "USD/KRW", "impact": "Weak KRW makes SK Hynix/Samsung exports cheaper, more competitive vs Micron."},
    "DX-Y.NYB": {"name": "DXY (Dollar Index)", "impact": "Weak dollar broadly bullish for US multinationals including MU."},
    "CNY=X": {"name": "USD/CNY", "impact": "Yuan moves signal China trade tensions or stimulus expectations."},
    "EUR=X": {"name": "EUR/USD", "impact": "Euro strength = dollar weakness = generally positive for US exporters."},
}


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_alert_date": None, "prev_prices": {}})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def check_fx_moves():
    """Main function — check FX moves and their implications for semis.

    Runs twice: Sunday evening (FX opens) and weekday mornings.
    """
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")
    state = load_state()

    if state.get("last_alert_date") == today:
        return

    prev_prices = state.get("prev_prices", {})
    current_prices = {}
    moves = {}

    for ticker, meta in FX_PAIRS.items():
        try:
            t = yf.Ticker(ticker)
            info = t.info or {}
            current = info.get("regularMarketPrice") or info.get("currentPrice")
            prev = info.get("regularMarketPreviousClose")

            if not current:
                hist = t.history(period="5d")
                if not hist.empty:
                    current = float(hist["Close"].iloc[-1])
                    if len(hist) >= 2:
                        prev = float(hist["Close"].iloc[-2])

            if current:
                current = float(current)
                current_prices[ticker] = current

                baseline = prev or prev_prices.get(ticker)
                if baseline and baseline > 0:
                    change_pct = (current / float(baseline) - 1) * 100
                    moves[ticker] = {
                        "name": meta["name"],
                        "current": current,
                        "prev": float(baseline),
                        "change_pct": change_pct,
                        "impact": meta["impact"],
                    }
        except Exception:
            continue

    if not moves:
        return

    # Check if any move exceeds threshold
    should_alert = False
    for ticker, data in moves.items():
        threshold = DXY_MOVE_THRESHOLD if "DX" in ticker else FX_MOVE_THRESHOLD
        if abs(data["change_pct"]) >= threshold:
            should_alert = True
            break

    if not should_alert:
        # Save prices but don't alert
        state["prev_prices"] = current_prices
        save_state(state)
        return

    # Determine overall dollar direction
    dxy = moves.get("DX-Y.NYB", {})
    if dxy:
        if dxy["change_pct"] > 0.3:
            dollar_trend = "Dollar strengthening — headwind for US exporters"
            mu_bias = "Slightly bearish for MU (foreign revenue worth less in USD)"
        elif dxy["change_pct"] < -0.3:
            dollar_trend = "Dollar weakening — tailwind for US exporters"
            mu_bias = "Slightly bullish for MU (foreign revenue worth more in USD)"
        else:
            dollar_trend = "Dollar stable"
            mu_bias = "Neutral FX impact on MU"
    else:
        dollar_trend = "DXY data unavailable"
        mu_bias = "FX impact unclear"

    # KRW-specific: weak KRW is bad for Micron competitiveness
    krw = moves.get("KRW=X", {})
    krw_note = ""
    if krw and abs(krw["change_pct"]) > 0.3:
        if krw["change_pct"] > 0:  # USD/KRW up = weak KRW
            krw_note = "Weak KRW makes Korean memory (SK Hynix, Samsung) more competitive vs Micron"
        else:
            krw_note = "Strong KRW reduces Korean memory cost advantage vs Micron"

    # JPY carry trade unwind detection
    jpy = moves.get("JPY=X", {})
    carry_trade_note = ""
    if jpy and jpy["change_pct"] < -1.0:  # USD/JPY dropping >1% = yen strengthening fast
        carry_trade_note = (
            "YEN CARRY TRADE RISK: JPY strengthening sharply. "
            "If USD/JPY breaks below key levels, carry trade unwind could trigger "
            "global risk-off cascade (like Aug 2024). MU has fabs in Hiroshima — "
            "strong yen also raises Japan fab operating costs in USD terms."
        )

    msg = "FX MONITOR ALERT\n\n"
    msg += "Currency Moves:\n"

    for ticker, data in sorted(moves.items(), key=lambda x: abs(x[1]["change_pct"]), reverse=True):
        if abs(data["change_pct"]) >= 0.1:  # Only show meaningful moves
            msg += f"  {data['name']}: {data['current']:.2f} ({data['change_pct']:+.2f}%)\n"

    msg += f"\nDollar Trend: {dollar_trend}\n"
    msg += f"MU Bias: {mu_bias}\n"
    if krw_note:
        msg += f"Korea: {krw_note}\n"
    if carry_trade_note:
        msg += f"\n{carry_trade_note}\n"

    msg += "\n#FX_MONITOR"

    send_alert(msg)

    state["last_alert_date"] = today
    state["prev_prices"] = current_prices
    save_state(state)


def get_fx_summary() -> str:
    """Return FX summary for other modules."""
    parts = []
    for ticker, meta in FX_PAIRS.items():
        try:
            info = yf.Ticker(ticker).info or {}
            price = info.get("regularMarketPrice")
            prev = info.get("regularMarketPreviousClose")
            if price and prev and prev > 0:
                change = (float(price) / float(prev) - 1) * 100
                parts.append(f"{meta['name']}: {float(price):.2f} ({change:+.1f}%)")
        except Exception:
            pass
    return "FX: " + " | ".join(parts) if parts else "FX: data unavailable"
