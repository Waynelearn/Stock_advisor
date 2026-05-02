"""Short interest tracker - monitors MU short interest, days-to-cover, and squeeze potential."""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".short_tracker_state.json")

# Alert thresholds
SI_CHANGE_THRESHOLD = 5.0      # Alert if SI changes >5% from last report
DTC_SQUEEZE_THRESHOLD = 3.0    # Days-to-cover above this = squeeze potential
SI_PCT_HIGH = 8.0              # Short % of float above this is notable


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {
        "last_alert_date": None,
        "last_report_date": None,
        "prev_shares_short": None,
        "prev_short_pct": None,
        "history": [],
    })


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _fmt_number(val: float) -> str:
    if abs(val) >= 1e9:
        return f"{val / 1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"{val / 1e6:.1f}M"
    if abs(val) >= 1e3:
        return f"{val / 1e3:.1f}K"
    return f"{val:.0f}"


def _get_short_data() -> dict:
    """Fetch short interest data from yfinance."""
    try:
        t = yf.Ticker(POSITION["ticker"])
        info = t.info or {}

        shares_short = info.get("sharesShort", 0)
        shares_short_prior = info.get("sharesShortPriorMonth", 0)
        short_ratio = info.get("shortRatio", 0)  # days to cover
        short_pct = info.get("shortPercentOfFloat", 0)
        short_date = info.get("dateShortInterest")
        float_shares = info.get("floatShares", 0)
        avg_volume = info.get("averageDailyVolume10Day", 0) or info.get("averageVolume", 0)

        if not shares_short:
            return None

        # Convert short_pct from decimal to percentage if needed
        if short_pct and short_pct < 1:
            short_pct *= 100

        # Calculate days to cover ourselves for verification
        dtc_calc = shares_short / avg_volume if avg_volume > 0 else 0

        # Change from prior month
        change_shares = shares_short - shares_short_prior if shares_short_prior else 0
        change_pct = (change_shares / shares_short_prior * 100) if shares_short_prior else 0

        return {
            "shares_short": shares_short,
            "shares_short_prior": shares_short_prior,
            "short_ratio": short_ratio,  # yfinance days-to-cover
            "short_pct_float": short_pct,
            "float_shares": float_shares,
            "avg_volume": avg_volume,
            "dtc_calc": dtc_calc,
            "change_shares": change_shares,
            "change_pct": change_pct,
            "report_date": short_date,
        }
    except Exception:
        return None


def _estimate_squeeze_potential(data: dict) -> dict:
    """Assess short squeeze potential."""
    dtc = data.get("short_ratio") or data.get("dtc_calc", 0)
    short_pct = data.get("short_pct_float", 0)

    # Check if stock is in uptrend (rising price + high SI = squeeze setup)
    try:
        hist = yf.Ticker(POSITION["ticker"]).history(period="1mo")
        if len(hist) >= 10:
            recent_return = (hist["Close"].iloc[-1] / hist["Close"].iloc[-5] - 1) * 100
        else:
            recent_return = 0
    except Exception:
        recent_return = 0

    if dtc > 4 and short_pct > SI_PCT_HIGH and recent_return > 0:
        potential = "HIGH"
        est_impact = dtc * 0.5  # rough estimate
    elif dtc > DTC_SQUEEZE_THRESHOLD and recent_return > 0:
        potential = "MEDIUM"
        est_impact = dtc * 0.3
    elif dtc > DTC_SQUEEZE_THRESHOLD:
        potential = "LOW-MEDIUM"
        est_impact = dtc * 0.2
    else:
        potential = "LOW"
        est_impact = 0

    return {
        "potential": potential,
        "days_to_cover": dtc,
        "short_pct": short_pct,
        "est_impact_pct": est_impact,
        "stock_rising": recent_return > 0,
        "recent_return_5d": recent_return,
    }


def check_short_updates():
    """Main function - check for short interest changes and alert."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_alert_date") == today:
        return

    data = _get_short_data()
    if not data:
        return

    # Check if this is a new report
    report_date_str = str(data["report_date"]) if data["report_date"] else None
    if report_date_str and report_date_str == state.get("last_report_date"):
        return  # Already processed this report

    should_alert = False
    alert_reasons = []

    # Check SI change vs prior month
    if abs(data["change_pct"]) > SI_CHANGE_THRESHOLD:
        should_alert = True
        direction = "decreased" if data["change_pct"] < 0 else "increased"
        alert_reasons.append(f"SI {direction} {abs(data['change_pct']):.1f}% from prior report")

    # Check days-to-cover threshold
    dtc = data.get("short_ratio") or data.get("dtc_calc", 0)
    if dtc > DTC_SQUEEZE_THRESHOLD:
        should_alert = True
        alert_reasons.append(f"Days-to-cover at {dtc:.1f} (above {DTC_SQUEEZE_THRESHOLD} threshold)")

    # Check vs our saved previous data
    prev_shares = state.get("prev_shares_short")
    if prev_shares and data["shares_short"]:
        our_change = (data["shares_short"] - prev_shares) / prev_shares * 100
        if abs(our_change) > SI_CHANGE_THRESHOLD:
            should_alert = True

    # Always alert on first run to establish baseline
    if state.get("prev_shares_short") is None:
        should_alert = True
        alert_reasons.append("Initial short interest baseline")

    if not should_alert:
        # Still save data for tracking
        state["last_report_date"] = report_date_str
        state["prev_shares_short"] = data["shares_short"]
        state["prev_short_pct"] = data["short_pct_float"]
        save_state(state)
        return

    squeeze = _estimate_squeeze_potential(data)

    # Trend analysis
    if data["change_pct"] < -5:
        trend = "Bears covering — short interest declining"
    elif data["change_pct"] > 5:
        trend = "Shorts increasing — bearish positioning growing"
    elif data["change_pct"] < 0:
        trend = "Modest short covering"
    elif data["change_pct"] > 0:
        trend = "Slight increase in short positioning"
    else:
        trend = "Stable short interest"

    report_label = f" (as of {report_date_str})" if report_date_str else ""

    msg = f"""SHORT INTEREST UPDATE

{POSITION['ticker']} Short Interest Report{report_label}:
  Shares Short: {_fmt_number(data['shares_short'])}
  Short % of Float: {data['short_pct_float']:.1f}%
  Change: {data['change_pct']:+.1f}% (from {_fmt_number(data['shares_short_prior'])})
  Days to Cover: {dtc:.1f}
  Short Ratio: {data['short_ratio']:.1f}

Trend: {trend}
Prior Month: {_fmt_number(data['shares_short_prior'])} -> {_fmt_number(data['shares_short'])} ({_fmt_number(data['change_shares'])} shares {'covered' if data['change_shares'] < 0 else 'added'})

Squeeze Potential: {squeeze['potential']}
  Days-to-cover: {squeeze['days_to_cover']:.1f}
  Stock trend (5d): {squeeze['recent_return_5d']:+.1f}%"""

    if squeeze["est_impact_pct"] > 0:
        msg += f"\n  Est. squeeze impact: +{squeeze['est_impact_pct']:.1f}% if covering accelerates"

    msg += "\n\n#SHORT_INTEREST"

    send_alert(msg)

    # Update state
    state["last_alert_date"] = today
    state["last_report_date"] = report_date_str
    state["prev_shares_short"] = data["shares_short"]
    state["prev_short_pct"] = data["short_pct_float"]
    state["history"].append({
        "date": today,
        "shares_short": data["shares_short"],
        "short_pct": data["short_pct_float"],
        "dtc": dtc,
    })
    # Keep last 12 reports
    state["history"] = state["history"][-12:]
    save_state(state)


def get_short_summary() -> str:
    """Return formatted current short interest for other modules."""
    data = _get_short_data()
    if not data:
        return "Short interest data unavailable"

    dtc = data.get("short_ratio") or data.get("dtc_calc", 0)
    squeeze = _estimate_squeeze_potential(data)

    return (
        f"Short Interest: {_fmt_number(data['shares_short'])} shares "
        f"({data['short_pct_float']:.1f}% of float) | "
        f"DTC: {dtc:.1f} | "
        f"Change: {data['change_pct']:+.1f}% | "
        f"Squeeze: {squeeze['potential']}"
    )
