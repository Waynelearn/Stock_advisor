"""Earnings estimate revision tracker - monitors consensus EPS/revenue changes for MU and peers."""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT, PEERS
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".estimate_tracker_state.json")

ESTIMATE_TICKERS = ["MU", "NVDA", "AMD", "AVGO", "MRVL"]

# AI demand signal stocks — their earnings signal MU demand trajectory
AI_DEMAND_TICKERS = {
    "ORCL": "Oracle (cloud/AI infra)",
    "ADBE": "Adobe (AI monetization)",
    "HPE": "HPE (enterprise AI servers)",
    "MSFT": "Microsoft (Azure AI)",
    "GOOGL": "Google (Cloud AI/TPU)",
    "META": "Meta (AI capex)",
    "AMZN": "Amazon (AWS AI)",
    "CRM": "Salesforce (enterprise AI)",
    "SNOW": "Snowflake (data/AI)",
}

# Alert thresholds for MU
EPS_CHANGE_THRESHOLD = 0.10   # Alert if EPS consensus changes >$0.10
REV_CHANGE_THRESHOLD = 200e6  # Alert if rev consensus changes >$200M


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_alert_date": None, "estimates": {}, "pt_data": {}})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _fmt_dollar(val: float) -> str:
    if abs(val) >= 1e9:
        return f"${val / 1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.1f}M"
    return f"${val:,.2f}"


def _get_estimates_for_ticker(ticker: str) -> dict:
    """Pull all available estimate data from yfinance."""
    result = {
        "ticker": ticker,
        "eps_current_avg": None,
        "eps_current_low": None,
        "eps_current_high": None,
        "eps_current_analysts": None,
        "eps_next_avg": None,
        "rev_current_avg": None,
        "rev_current_low": None,
        "rev_current_high": None,
        "rev_current_analysts": None,
        "rev_next_avg": None,
        "pt_mean": None,
        "pt_high": None,
        "pt_low": None,
        "pt_current": None,
        "recommendation": None,
    }

    try:
        t = yf.Ticker(ticker)

        # Earnings estimates
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty:
                # Current quarter
                if len(ee.columns) >= 1:
                    col = ee.columns[0]
                    result["eps_current_avg"] = float(ee.loc["avg", col]) if "avg" in ee.index else None
                    result["eps_current_low"] = float(ee.loc["low", col]) if "low" in ee.index else None
                    result["eps_current_high"] = float(ee.loc["high", col]) if "high" in ee.index else None
                    result["eps_current_analysts"] = int(ee.loc["numberOfAnalysts", col]) if "numberOfAnalysts" in ee.index else None
                # Next quarter
                if len(ee.columns) >= 2:
                    col = ee.columns[1]
                    result["eps_next_avg"] = float(ee.loc["avg", col]) if "avg" in ee.index else None
        except Exception:
            pass

        # Revenue estimates
        try:
            re_ = t.revenue_estimate
            if re_ is not None and not re_.empty:
                if len(re_.columns) >= 1:
                    col = re_.columns[0]
                    result["rev_current_avg"] = float(re_.loc["avg", col]) if "avg" in re_.index else None
                    result["rev_current_low"] = float(re_.loc["low", col]) if "low" in re_.index else None
                    result["rev_current_high"] = float(re_.loc["high", col]) if "high" in re_.index else None
                    result["rev_current_analysts"] = int(re_.loc["numberOfAnalysts", col]) if "numberOfAnalysts" in re_.index else None
                if len(re_.columns) >= 2:
                    col = re_.columns[1]
                    result["rev_next_avg"] = float(re_.loc["avg", col]) if "avg" in re_.index else None
        except Exception:
            pass

        # Analyst price targets
        try:
            pt = t.analyst_price_targets
            if pt is not None and isinstance(pt, dict):
                result["pt_mean"] = pt.get("mean")
                result["pt_high"] = pt.get("high")
                result["pt_low"] = pt.get("low")
                result["pt_current"] = pt.get("current")
            elif pt is not None and hasattr(pt, "to_dict"):
                pt_dict = pt.to_dict()
                result["pt_mean"] = pt_dict.get("mean")
                result["pt_high"] = pt_dict.get("high")
                result["pt_low"] = pt_dict.get("low")
        except Exception:
            pass

        # Current price for upside calculation
        try:
            info = t.info or {}
            result["pt_current"] = result["pt_current"] or info.get("currentPrice") or info.get("regularMarketPrice")
        except Exception:
            pass

        # Recommendation
        try:
            result["recommendation"] = (t.info or {}).get("recommendationKey", "").replace("_", " ").title()
        except Exception:
            pass

    except Exception:
        pass

    return result


def check_estimate_changes():
    """Main function - check for estimate revisions and alert."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_alert_date") == today:
        return

    prev_estimates = state.get("estimates", {})
    current_estimates = {}
    alerts = []

    for ticker in ESTIMATE_TICKERS:
        est = _get_estimates_for_ticker(ticker)
        current_estimates[ticker] = {
            "eps_current_avg": est["eps_current_avg"],
            "eps_next_avg": est["eps_next_avg"],
            "rev_current_avg": est["rev_current_avg"],
            "rev_next_avg": est["rev_next_avg"],
            "pt_mean": est["pt_mean"],
        }

        prev = prev_estimates.get(ticker, {})
        if not prev:
            continue

        # Check MU estimate changes (stricter thresholds for peers)
        is_primary = ticker == POSITION["ticker"]
        eps_threshold = EPS_CHANGE_THRESHOLD if is_primary else EPS_CHANGE_THRESHOLD * 2
        rev_threshold = REV_CHANGE_THRESHOLD if is_primary else REV_CHANGE_THRESHOLD * 2

        changes = []

        # EPS change
        if est["eps_current_avg"] and prev.get("eps_current_avg"):
            eps_diff = est["eps_current_avg"] - prev["eps_current_avg"]
            if abs(eps_diff) >= eps_threshold:
                changes.append(
                    f"EPS: ${prev['eps_current_avg']:.2f} -> ${est['eps_current_avg']:.2f} ({eps_diff:+.2f})"
                )

        # Revenue change
        if est["rev_current_avg"] and prev.get("rev_current_avg"):
            rev_diff = est["rev_current_avg"] - prev["rev_current_avg"]
            if abs(rev_diff) >= rev_threshold:
                changes.append(
                    f"Revenue: {_fmt_dollar(prev['rev_current_avg'])} -> {_fmt_dollar(est['rev_current_avg'])} ({_fmt_dollar(rev_diff)})"
                )

        # PT change (MU only)
        if is_primary and est["pt_mean"] and prev.get("pt_mean"):
            pt_diff = est["pt_mean"] - prev["pt_mean"]
            if abs(pt_diff) >= 3:  # $3 PT change
                changes.append(
                    f"Mean PT: ${prev['pt_mean']:.0f} -> ${est['pt_mean']:.0f} ({pt_diff:+.0f})"
                )

        if changes:
            alerts.append({"ticker": ticker, "changes": changes, "est": est})

    if not alerts:
        # Save state even with no alerts
        state["estimates"] = current_estimates
        save_state(state)
        return

    # Build alert message
    msg = "ESTIMATE REVISION ALERT\n\n"

    for a in alerts:
        ticker = a["ticker"]
        est = a["est"]
        is_primary = ticker == POSITION["ticker"]

        if is_primary:
            msg += f"{ticker} Estimates Updated:\n\n"
            for ch in a["changes"]:
                msg += f"  {ch}\n"

            # Add context
            if est["eps_current_analysts"]:
                msg += f"\n  Analysts covering: {est['eps_current_analysts']}\n"
            if est["eps_current_low"] and est["eps_current_high"]:
                msg += f"  EPS range: ${est['eps_current_low']:.2f} - ${est['eps_current_high']:.2f}\n"
            if est["rev_current_low"] and est["rev_current_high"]:
                msg += f"  Rev range: {_fmt_dollar(est['rev_current_low'])} - {_fmt_dollar(est['rev_current_high'])}\n"

            # PT info
            if est["pt_mean"] and est["pt_current"]:
                upside = (est["pt_mean"] / est["pt_current"] - 1) * 100
                msg += f"\n  Analyst PTs: Mean ${est['pt_mean']:.0f} | High ${est['pt_high']:.0f} | Low ${est['pt_low']:.0f}\n"
                msg += f"  Current: ${est['pt_current']:.0f} ({upside:+.0f}% to mean)\n"

            if est["recommendation"]:
                msg += f"  Consensus: {est['recommendation']}\n"

            # Estimate momentum
            prev_eps = prev_estimates.get(ticker, {}).get("eps_current_avg")
            if prev_eps and est["eps_current_avg"]:
                if est["eps_current_avg"] > prev_eps:
                    msg += "\n  Estimate Momentum: RISING (bar getting higher)\n"
                else:
                    msg += "\n  Estimate Momentum: FALLING (bar getting lower)\n"

            msg += "\n"
        else:
            msg += f"Peer Signal - {ticker}:\n"
            for ch in a["changes"]:
                msg += f"  {ch}\n"
            msg += "\n"

    msg += "#ESTIMATE"
    send_alert(msg)

    state["last_alert_date"] = today
    state["estimates"] = current_estimates
    save_state(state)


def get_estimate_summary() -> str:
    """Return formatted estimate summary for MU."""
    est = _get_estimates_for_ticker(POSITION["ticker"])

    lines = [f"{POSITION['ticker']} Consensus Estimates:"]

    if est["eps_current_avg"]:
        lines.append(f"  EPS (current Q): ${est['eps_current_avg']:.2f} "
                      f"(range: ${est['eps_current_low']:.2f}-${est['eps_current_high']:.2f})"
                      if est["eps_current_low"] and est["eps_current_high"]
                      else f"  EPS (current Q): ${est['eps_current_avg']:.2f}")
    if est["rev_current_avg"]:
        lines.append(f"  Revenue (current Q): {_fmt_dollar(est['rev_current_avg'])}")
    if est["eps_next_avg"]:
        lines.append(f"  EPS (next Q): ${est['eps_next_avg']:.2f}")
    if est["pt_mean"]:
        lines.append(f"  PT: Mean ${est['pt_mean']:.0f} | High ${est['pt_high']:.0f} | Low ${est['pt_low']:.0f}")
    if est["recommendation"]:
        lines.append(f"  Consensus: {est['recommendation']}")

    return "\n".join(lines) if len(lines) > 1 else "Estimate data unavailable"
