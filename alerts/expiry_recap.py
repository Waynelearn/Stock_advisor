"""Options expiry recap — Saturday morning summary of Friday's options action.

After Friday close, summarizes: max pain vs actual, spread performance,
OI buildup at key strikes for next week, gamma exposure shifts.
"""

import json
import os
import yfinance as yf
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".expiry_recap_state.json")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_recap_week": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_weekly_performance() -> dict:
    """Get MU's weekly performance metrics."""
    try:
        t = yf.Ticker(POSITION["ticker"])
        hist = t.history(period="1mo")

        if len(hist) < 5:
            return {}

        friday_close = hist["Close"].iloc[-1]
        monday_open = hist["Open"].iloc[-5] if len(hist) >= 5 else hist["Open"].iloc[0]
        week_high = hist["High"].iloc[-5:].max()
        week_low = hist["Low"].iloc[-5:].min()
        week_return = (friday_close / hist["Close"].iloc[-6] - 1) * 100 if len(hist) >= 6 else 0
        avg_volume = hist["Volume"].iloc[-5:].mean()

        return {
            "friday_close": float(friday_close),
            "monday_open": float(monday_open),
            "week_high": float(week_high),
            "week_low": float(week_low),
            "week_return": week_return,
            "avg_volume": float(avg_volume),
            "week_range": float(week_high - week_low),
        }
    except Exception:
        return {}


def _get_options_snapshot() -> dict:
    """Get current options data for key strikes."""
    if not POSITION.get("contracts"):
        return {}
    try:
        t = yf.Ticker(POSITION["ticker"])
        expiry_str = POSITION["expiry"]

        # Get chain for position expiry
        chain = t.option_chain(expiry_str)
        calls = chain.calls
        puts = chain.puts

        if calls.empty:
            return {}

        # Find max pain (strike where most options expire worthless)
        all_strikes = sorted(set(calls["strike"].tolist()))
        max_pain_strike = None
        min_pain_value = float("inf")

        for strike in all_strikes:
            # Total pain = sum of ITM call value + ITM put value
            call_pain = sum(
                max(0, strike - s) * oi
                for s, oi in zip(calls["strike"], calls["openInterest"])
            )
            put_pain = sum(
                max(0, s - strike) * oi
                for s, oi in zip(puts["strike"], puts["openInterest"])
            )
            total_pain = call_pain + put_pain
            if total_pain < min_pain_value:
                min_pain_value = total_pain
                max_pain_strike = strike

        # OI at key strikes
        key_strikes = [POSITION["long_strike"], POSITION["short_strike"]]
        strike_data = {}
        for ks in key_strikes:
            call_row = calls[calls["strike"] == ks]
            put_row = puts[puts["strike"] == ks]
            call_oi = int(call_row["openInterest"].iloc[0]) if not call_row.empty else 0
            put_oi = int(put_row["openInterest"].iloc[0]) if not put_row.empty else 0
            call_vol = int(call_row["volume"].iloc[0]) if not call_row.empty and call_row["volume"].iloc[0] == call_row["volume"].iloc[0] else 0
            strike_data[ks] = {"call_oi": call_oi, "put_oi": put_oi, "call_vol": call_vol}

        # Total PCR
        total_call_oi = calls["openInterest"].sum()
        total_put_oi = puts["openInterest"].sum()
        pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 0

        return {
            "max_pain": max_pain_strike,
            "strike_data": strike_data,
            "pcr": pcr,
            "total_call_oi": int(total_call_oi),
            "total_put_oi": int(total_put_oi),
        }
    except Exception:
        return {}


def _get_spread_status(friday_close: float) -> dict:
    """Calculate current spread status."""
    if not POSITION.get("contracts"):
        return {}
    long_strike = POSITION["long_strike"]
    short_strike = POSITION["short_strike"]
    width = short_strike - long_strike
    entry = POSITION["entry_price"]
    contracts = POSITION["contracts"]

    if friday_close >= short_strike:
        intrinsic = width
    elif friday_close <= long_strike:
        intrinsic = 0
    else:
        intrinsic = friday_close - long_strike

    pnl_per = intrinsic - entry
    total_pnl = pnl_per * contracts * 100

    expiry = date.fromisoformat(POSITION["expiry"])
    dte = max((expiry - date.today()).days, 0)

    return {
        "intrinsic": intrinsic,
        "pnl_per": pnl_per,
        "total_pnl": total_pnl,
        "dte": dte,
        "pct_of_max": (intrinsic / width * 100) if width > 0 else 0,
    }


def send_expiry_recap():
    """Main function — Saturday morning options/week recap.

    Runs Saturday 9:30 AM SGT (after war room at 9 AM).
    """
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    state = load_state()
    week_key = now_sgt.strftime("%Y-W%W")

    if state.get("last_recap_week") == week_key:
        return

    perf = _get_weekly_performance()
    options = _get_options_snapshot()
    spread = _get_spread_status(perf.get("friday_close", 0)) if perf else {}

    if not perf:
        return

    msg = "WEEKLY OPTIONS RECAP\n"
    msg += f"Week ending {date.today().strftime('%B %d, %Y')}\n"
    msg += "=" * 30 + "\n\n"

    # Weekly performance
    msg += "MU WEEKLY PERFORMANCE\n"
    msg += f"  Friday Close: ${perf['friday_close']:,.2f}\n"
    msg += f"  Week Return: {perf['week_return']:+.1f}%\n"
    msg += f"  Week Range: ${perf['week_low']:,.2f} - ${perf['week_high']:,.2f} (${perf['week_range']:,.1f})\n"
    msg += f"  Avg Daily Volume: {perf['avg_volume']/1e6:.1f}M\n\n"

    # Spread status
    if spread:
        msg += "SPREAD STATUS\n"
        msg += f"  {POSITION['long_strike']}/{POSITION['short_strike']} BCS x {POSITION['contracts']}\n"
        msg += f"  Intrinsic Value: ${spread['intrinsic']:,.2f} ({spread['pct_of_max']:.0f}% of max)\n"
        msg += f"  P&L: ${spread['total_pnl']:,.0f} ({spread['pnl_per']:+.2f}/contract)\n"
        msg += f"  DTE: {spread['dte']} days\n"

        if spread["dte"] <= 5:
            msg += "  STATUS: EXPIRY WEEK NEXT WEEK\n"
        elif spread["pnl_per"] > 0:
            msg += "  STATUS: IN PROFIT\n"
        else:
            msg += "  STATUS: UNDERWATER\n"
        msg += "\n"

    # Options data
    if options:
        msg += "OPTIONS DATA\n"
        if options.get("max_pain"):
            mp = options["max_pain"]
            diff = perf["friday_close"] - mp
            msg += f"  Max Pain: ${mp:.0f} (closed {'+' if diff > 0 else ''}{diff:.1f} away)\n"

        msg += f"  PCR: {options['pcr']:.2f}\n"
        msg += f"  Total Call OI: {options['total_call_oi']:,}\n"
        msg += f"  Total Put OI: {options['total_put_oi']:,}\n"

        sd = options.get("strike_data", {})
        for strike, data in sd.items():
            msg += f"  ${strike} strike: Call OI {data['call_oi']:,} | Put OI {data['put_oi']:,}\n"
        msg += "\n"

    # Next week outlook
    msg += "NEXT WEEK OUTLOOK\n"
    if spread and spread["dte"] <= 5:
        msg += "  CRITICAL: Expiry week — gamma and theta effects amplified\n"
        if perf["friday_close"] < POSITION["short_strike"]:
            needed = POSITION["short_strike"] - perf["friday_close"]
            msg += f"  Need +${needed:.0f} for max profit\n"
    if options and options.get("max_pain"):
        msg += f"  Max pain magnet at ${options['max_pain']:.0f}\n"

    msg += "\n#EXPIRY_RECAP"

    send_alert(msg)

    state["last_recap_week"] = week_key
    save_state(state)
