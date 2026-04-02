"""Hyperscaler capex tracker - monitors cloud/AI demand signals impacting MU."""

import json, os
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".hyperscaler_state.json")

HYPERSCALERS = {
    "MSFT": "Microsoft (Azure)", "GOOGL": "Alphabet (GCP)",
    "META": "Meta (AI infra)", "AMZN": "Amazon (AWS)",
}
DEMAND_ETFS = {
    "CLOU": "Global X Cloud", "WCLD": "WisdomTree Cloud",
    "BOTZ": "Global X Robotics & AI", "ROBO": "ROBO Global Robotics",
}

COLLECTIVE_SELLOFF_PCT = -2.0
DIVERGENCE_THRESHOLD = 2.0


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_alert_date": None, "last_selloff_alert": None,
            "last_divergence_alert": None, "last_demand_score": None}


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_hyperscaler_data() -> dict:
    """Fetch price and return data for hyperscalers, demand ETFs, and MU."""
    data = {}
    for ticker in list(HYPERSCALERS) + list(DEMAND_ETFS) + [POSITION["ticker"]]:
        try:
            hist = yf.Ticker(ticker).history(period="1mo")
            if hist.empty or len(hist) < 2:
                continue
            c = hist["Close"]
            data[ticker] = {
                "price": float(c.iloc[-1]),
                "ret_1d": float((c.iloc[-1] / c.iloc[-2] - 1) * 100),
                "ret_5d": float((c.iloc[-1] / c.iloc[-6] - 1) * 100) if len(c) >= 6 else 0.0,
                "ret_20d": float((c.iloc[-1] / c.iloc[0] - 1) * 100),
            }
        except Exception:
            continue
    return data


def _calculate_demand_score(data: dict) -> int:
    """AI demand score 0-100 from hyperscaler + ETF momentum.

    Weights: hyperscaler 5d (40%), hyperscaler 20d (30%), ETF 5d (20%), breadth (10%).
    """
    hyper_5d, hyper_20d, etf_5d = [], [], []
    hyper_pos = 0
    for t in HYPERSCALERS:
        if t in data:
            hyper_5d.append(data[t]["ret_5d"])
            hyper_20d.append(data[t]["ret_20d"])
            if data[t]["ret_5d"] > 0:
                hyper_pos += 1
    for t in DEMAND_ETFS:
        if t in data:
            etf_5d.append(data[t]["ret_5d"])
    if not hyper_5d:
        return 50

    def _s(val, lo, hi):
        return max(0, min(100, (val - lo) / (hi - lo) * 100))

    avg_h5 = sum(hyper_5d) / len(hyper_5d)
    avg_h20 = sum(hyper_20d) / len(hyper_20d) if hyper_20d else 0
    avg_e5 = sum(etf_5d) / len(etf_5d) if etf_5d else 0
    breadth = (hyper_pos / len(hyper_5d)) * 100
    return int(round(0.40 * _s(avg_h5, -5, 5) + 0.30 * _s(avg_h20, -10, 10)
                      + 0.20 * _s(avg_e5, -5, 5) + 0.10 * breadth))


def check_hyperscaler_signals():
    """Main check - daily at 10 AM ET + big-move triggered alerts."""
    state = _load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")
    data = _get_hyperscaler_data()
    if len(data) < 3:
        return

    mu_ticker = POSITION["ticker"]
    mu_data = data.get(mu_ticker)
    demand_score = _calculate_demand_score(data)
    hyper_1d = [data[t]["ret_1d"] for t in HYPERSCALERS if t in data]
    avg_hyper_1d = sum(hyper_1d) / len(hyper_1d) if hyper_1d else 0

    # Check 1: Collective hyperscaler selloff
    if avg_hyper_1d < COLLECTIVE_SELLOFF_PCT and state.get("last_selloff_alert") != today:
        detail = "\n".join(f"  {t} ({HYPERSCALERS[t]}): {data[t]['ret_1d']:+.1f}%"
                           for t in HYPERSCALERS if t in data)
        msg = (f"\U0001f534 <b>HYPERSCALER SELLOFF</b>\n" + "\u2500" * 20 + "\n\n"
               f"Avg move: <b>{avg_hyper_1d:+.1f}%</b>\n\n{detail}\n\n"
               f"AI Demand Score: {demand_score}/100\n"
               f"Risk: Cloud capex sentiment deteriorating. Watch for MU sympathy selling.\n\n"
               f"<code>#HYPERSCALER</code>")
        send_alert(msg)
        state["last_selloff_alert"] = today

    # Check 2: Hyperscaler-MU divergence
    if mu_data and hyper_1d:
        div = mu_data["ret_1d"] - avg_hyper_1d
        if abs(div) > DIVERGENCE_THRESHOLD and state.get("last_divergence_alert") != today:
            if div < 0:
                signal, emoji = "MU LAGGING", "\U0001f534"
                note = "Hyperscalers up but MU down \u2014 MU may be oversold relative to demand signals."
            else:
                signal, emoji = "MU LEADING", "\U0001f7e2"
                note = "MU outperforming its customers \u2014 stock-specific catalyst or positioning."
            msg = (f"{emoji} <b>HYPERSCALER DIVERGENCE: {signal}</b>\n" + "\u2500" * 20 + "\n\n"
                   f"MU: <b>{mu_data['ret_1d']:+.1f}%</b> (${mu_data['price']:.2f})\n"
                   f"Hyperscaler avg: <b>{avg_hyper_1d:+.1f}%</b>\n"
                   f"Gap: {div:+.1f}%\n\n{note}\n\n"
                   f"AI Demand Score: {demand_score}/100\n\n<code>#HYPERSCALER</code>")
            send_alert(msg)
            state["last_divergence_alert"] = today

    # Check 3: Demand score extreme shift (>=15 pts)
    prev_score = state.get("last_demand_score")
    if prev_score is not None:
        shift = demand_score - prev_score
        if abs(shift) >= 15:
            direction = "SURGING" if shift > 0 else "COLLAPSING"
            msg = (f"\U0001f4ca <b>AI DEMAND SCORE {direction}</b>\n" + "\u2500" * 20 + "\n\n"
                   f"Score: <b>{demand_score}/100</b> (was {prev_score})\n"
                   f"Shift: {shift:+d} pts\n\n<code>#HYPERSCALER</code>")
            send_alert(msg)

    state["last_alert_date"] = today
    state["last_demand_score"] = demand_score
    _save_state(state)


def get_hyperscaler_summary() -> str:
    """Return formatted summary for daily briefing or interactive bot."""
    data = _get_hyperscaler_data()
    if not data:
        return "Hyperscaler data unavailable"

    demand_score = _calculate_demand_score(data)
    mu_ticker = POSITION["ticker"]
    lines = [
        "Hyperscaler / AI Demand Dashboard",
        "=" * 36,
        f"AI Demand Score: {demand_score}/100", "",
        f"{'Ticker':<8} {'Name':<22} {'1D':>7} {'5D':>7} {'20D':>7}",
        "-" * 55,
    ]
    for group in (HYPERSCALERS, DEMAND_ETFS):
        for t in group:
            if t in data:
                d = data[t]
                lines.append(f"{t:<8} {group[t]:<22} {d['ret_1d']:>+6.1f}% "
                             f"{d['ret_5d']:>+6.1f}% {d['ret_20d']:>+6.1f}%")
        lines.append("")

    if mu_ticker in data:
        d = data[mu_ticker]
        lines.append(f"{mu_ticker:<8} {'Micron':<22} {d['ret_1d']:>+6.1f}% "
                     f"{d['ret_5d']:>+6.1f}% {d['ret_20d']:>+6.1f}%")

    hyper_5d = [data[t]["ret_5d"] for t in HYPERSCALERS if t in data]
    mu_d = data.get(mu_ticker)
    if hyper_5d and mu_d:
        avg_h5 = sum(hyper_5d) / len(hyper_5d)
        gap = mu_d["ret_5d"] - avg_h5
        if abs(gap) > 1.0:
            label = "outperforming" if gap > 0 else "underperforming"
            lines.append(f"\nMU {label} hyperscalers by {abs(gap):.1f}% (5d)")

    return "\n".join(lines)
