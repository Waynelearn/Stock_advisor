"""Semiconductor supply chain risk tracker - shipping, industrial gases, helium.

Tracks proxy tickers for supply chain stress impacting MU fab operations.
Helium risk: Qatar = 40% of world helium, shipped through Hormuz; APD distributes.

Schedule: Daily at 11 AM ET | Tag: #SUPPLY_CHAIN
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".supply_chain_state.json")

SHIPPING_TICKERS = {"BDRY": "Dry Bulk Shipping ETF", "BOAT": "Global Shipping ETF"}
GAS_TICKERS = {
    "APD": "Air Products (helium/gases)",
    "LIN": "Linde (process gases)",
    "ECL": "Ecolab (fab water treatment)",
    "QAT": "iShares MSCI Qatar ETF",
}
ALL_TICKERS = {**SHIPPING_TICKERS, **GAS_TICKERS}

BIG_MOVE_PCT = 3.0
STRESS_ELEVATED, STRESS_HIGH, STRESS_CRITICAL = 1.5, 2.5, 4.0
BASELINE_DAYS = 60


def _load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_check_date": None, "last_stress": None, "baselines": {}})


def _save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _fetch_ticker_data(ticker: str) -> dict | None:
    """Fetch current price, daily change, and 60-day baseline stats."""
    try:
        hist = yf.Ticker(ticker).history(period=f"{BASELINE_DAYS}d")
        if hist.empty or len(hist) < 5:
            return None
        cur = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        avg = float(hist["Close"].mean())
        std = float(hist["Close"].std())
        return {
            "price": round(cur, 2),
            "day_chg_pct": round((cur / prev - 1) * 100, 2) if prev else 0,
            "avg_60d": round(avg, 2),
            "z_score": round((cur - avg) / std, 2) if std > 0 else 0,
            "pct_vs_baseline": round((cur / avg - 1) * 100, 1),
        }
    except Exception:
        return None


def _calculate_stress_index(data: dict[str, dict | None]) -> tuple[float, dict]:
    """Composite stress index from shipping (40%) + gas (60%) z-scores.

    Higher = more stress on MU supply chain. Uses absolute z-scores since
    both spikes (cost pressure) and crashes (demand concern) are risks.
    """
    ship_z, gas_z = [], []
    for ticker, info in data.items():
        if info is None:
            continue
        z = abs(info["z_score"])
        if ticker in SHIPPING_TICKERS:
            ship_z.append(z)
        elif ticker in GAS_TICKERS:
            gas_z.append(z)

    s = sum(ship_z) / len(ship_z) if ship_z else 0
    g = sum(gas_z) / len(gas_z) if gas_z else 0
    stress = round(s * 0.4 + g * 0.6, 2)
    return stress, {"shipping": round(s, 2), "gas": round(g, 2), "composite": stress}


def _stress_label(stress: float | None) -> str:
    if stress is None:
        return "NORMAL"
    if stress >= STRESS_CRITICAL:
        return "CRITICAL"
    if stress >= STRESS_HIGH:
        return "HIGH"
    if stress >= STRESS_ELEVATED:
        return "ELEVATED"
    return "NORMAL"


def check_supply_chain():
    """Main entry point - check supply chain indicators and alert on stress."""
    state = _load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_check_date") == today:
        return

    data = {t: _fetch_ticker_data(t) for t in ALL_TICKERS}
    fetched = {k: v for k, v in data.items() if v is not None}
    if len(fetched) < 3:
        return

    stress, components = _calculate_stress_index(data)
    label = _stress_label(stress)
    prev_label = _stress_label(state.get("last_stress"))

    # Stress index alert: on level change or persistent HIGH/CRITICAL
    if label != "NORMAL" and (prev_label != label or label in ("HIGH", "CRITICAL")):
        icon = {"CRITICAL": "\u26a0\ufe0f", "HIGH": "\U0001f7e0", "ELEVATED": "\U0001f7e1"}[label]
        lines = [f"{icon} <b>#SUPPLY_CHAIN \u2014 {label} STRESS</b>", "",
                 f"Stress Index: <b>{stress:.1f}</b> (shipping {components['shipping']:.1f} | gas {components['gas']:.1f})", "",
                 "<b>Shipping/Freight:</b>"]
        for t in SHIPPING_TICKERS:
            if t in fetched:
                d = fetched[t]
                lines.append(f"  {t}: ${d['price']} ({d['day_chg_pct']:+.1f}% day, {d['pct_vs_baseline']:+.1f}% vs 60d)")
        lines += ["", "<b>Fab Inputs (Gas/Chem):</b>"]
        for t in GAS_TICKERS:
            if t in fetched:
                d = fetched[t]
                lines.append(f"  {t}: ${d['price']} ({d['day_chg_pct']:+.1f}% day, {d['pct_vs_baseline']:+.1f}% vs 60d)")
        apd = fetched.get("APD")
        if apd and abs(apd["z_score"]) >= 1.5:
            verb = "spike" if apd["z_score"] > 0 else "drop"
            lines += ["", f"<i>APD {verb} (z={apd['z_score']:+.1f}) \u2014 Qatar=40% world helium via Hormuz. "
                      f"APD is primary distributor; moves signal helium supply stress.</i>"]
        impact = "raises" if stress >= STRESS_HIGH else "may raise"
        lines += ["", f"<i>Supply chain stress {impact} MU fab input costs.</i>", "", "#SUPPLY_CHAIN"]
        send_alert("\n".join(lines))

    # Helium-specific alert (lower threshold for APD — critical fab input)
    apd_data = fetched.get("APD")
    qat_data = fetched.get("QAT")
    if apd_data and abs(apd_data["day_chg_pct"]) >= 2.0:
        direction = "SPIKING" if apd_data["day_chg_pct"] > 0 else "DROPPING"
        he_lines = [
            f"\U0001f9ea <b>#HELIUM ALERT - APD {direction}</b>",
            "",
            f"Air Products: ${apd_data['price']} ({apd_data['day_chg_pct']:+.1f}% today)",
            f"vs 60d avg: {apd_data['pct_vs_baseline']:+.1f}% | z-score: {apd_data['z_score']:+.1f}",
            "",
            "<b>Why it matters:</b>",
            "Qatar (30%+ global helium) offline since Iran war.",
            "APD is primary helium distributor to chip fabs.",
            "SK Hynix, TSMC, Samsung all rely on helium for wafer fab.",
            "Spike = supply tightening. Drop = restocking or demand destruction.",
        ]
        if qat_data:
            he_lines.append(f"\nQatar ETF (QAT): ${qat_data['price']} ({qat_data['day_chg_pct']:+.1f}%)")
        he_lines += ["", "#HELIUM #SUPPLY_CHAIN"]
        send_alert("\n".join(he_lines))

    # Individual big movers alert
    movers = [(t, d) for t, d in fetched.items() if abs(d["day_chg_pct"]) >= BIG_MOVE_PCT]
    if movers:
        lines = ["\U0001f4e6 <b>#SUPPLY_CHAIN \u2014 BIG MOVERS</b>", ""]
        for ticker, info in movers:
            arrow = "\U0001f4c8" if info["day_chg_pct"] > 0 else "\U0001f4c9"
            lines.append(f"{arrow} <b>{ticker}</b> ({ALL_TICKERS[ticker]}): ${info['price']} ({info['day_chg_pct']:+.1f}%)")
            lines.append(f"   vs 60d avg: {info['pct_vs_baseline']:+.1f}% | z={info['z_score']:+.1f}")
        lines += ["", "<i>Large moves in MU supply chain proxies \u2014 check input cost / demand impact.</i>", "", "#SUPPLY_CHAIN"]
        send_alert("\n".join(lines))

    state["last_check_date"] = today
    state["last_stress"] = stress
    state["baselines"] = {k: v["avg_60d"] for k, v in fetched.items()}
    _save_state(state)


def get_supply_chain_summary() -> str:
    """Return current supply chain status string for other modules."""
    data = {t: _fetch_ticker_data(t) for t in ALL_TICKERS}
    fetched = {k: v for k, v in data.items() if v is not None}
    if len(fetched) < 2:
        return "Supply Chain: Unable to fetch data"
    stress, _ = _calculate_stress_index(data)
    label = _stress_label(stress)
    parts = [f"Supply Chain: {label} (stress={stress:.1f})"]
    parts.extend(f"{t} {d['day_chg_pct']:+.1f}%" for t, d in fetched.items())
    return " | ".join(parts)
