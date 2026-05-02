"""Memory pricing proxy tracker - DRAM/NAND demand signals from memory stock momentum."""

import json
import os
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".memory_pricing_state.json")

# Memory stock proxies
MEMORY_TICKERS = {
    "MU": "Micron (DRAM+NAND)",
    "000660.KS": "SK Hynix (DRAM leader)",
    "005930.KS": "Samsung (DRAM+NAND)",
    "WDC": "Western Digital (NAND)",
}

# Groupings for divergence detection
DRAM_PROXIES = ["MU", "000660.KS", "005930.KS"]
NAND_PROXIES = ["WDC", "005930.KS"]  # Samsung does both

# Thresholds
WOW_MOMENTUM_ALERT = 5.0     # Week-over-week % to flag
MOM_MOMENTUM_ALERT = 10.0    # Month-over-month % to flag
DIVERGENCE_THRESHOLD = 5.0   # WoW gap between groups to flag divergence
MU_WDC_RATIO_ZSCORE = 1.5    # MU/WDC ratio z-score for extreme alert


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_alert_date": None, "last_ratio_alert_date": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _get_memory_proxy_data() -> dict:
    """Fetch price history and compute momentum for all memory stocks."""
    results = {}
    for ticker, name in MEMORY_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="3mo")
            if hist.empty or len(hist) < 10:
                continue
            closes = hist["Close"]
            price = closes.iloc[-1]
            wow = (price / closes.iloc[-6] - 1) * 100 if len(closes) >= 6 else 0
            mom = (price / closes.iloc[-22] - 1) * 100 if len(closes) >= 22 else 0
            vol_5d = hist["Volume"].iloc[-5:].mean() if len(hist) >= 5 else 0
            vol_20d = hist["Volume"].iloc[-20:].mean() if len(hist) >= 20 else 0
            results[ticker] = {
                "name": name, "price": float(price),
                "wow": float(wow), "mom": float(mom),
                "vol_ratio": float(vol_5d / vol_20d if vol_20d > 0 else 1.0),
                "closes": [float(c) for c in closes.values[-60:]],
            }
        except Exception as e:
            print(f"[MEMORY PRICING] {ticker} error: {e}")
    return results


def _check_divergence(data: dict) -> list:
    """Detect DRAM vs NAND momentum divergences."""
    alerts = []

    # Average WoW for DRAM vs NAND proxies
    dram_wows = [data[t]["wow"] for t in DRAM_PROXIES if t in data]
    nand_wows = [data[t]["wow"] for t in NAND_PROXIES if t in data]

    if not dram_wows or not nand_wows:
        return alerts

    dram_avg = sum(dram_wows) / len(dram_wows)
    nand_avg = sum(nand_wows) / len(nand_wows)
    gap = dram_avg - nand_avg

    if abs(gap) >= DIVERGENCE_THRESHOLD:
        leader = "DRAM" if gap > 0 else "NAND"
        signal = "DRAM pricing firming faster" if gap > 0 else "NAND recovery leading, DRAM softer"
        alerts.append(
            f"{leader} proxies outpacing by {abs(gap):.1f}% WoW\n"
            f"  DRAM avg: {dram_avg:+.1f}% | NAND avg: {nand_avg:+.1f}%\n"
            f"  Signal: {signal}"
        )

    mu, wdc = data.get("MU"), data.get("WDC")
    if mu and wdc:
        if wdc["wow"] > 3.0 and mu["wow"] < -1.0:
            alerts.append(f"WDC {wdc['wow']:+.1f}% vs MU {mu['wow']:+.1f}% WoW — NAND strong, DRAM weak")
        elif mu["wow"] > 3.0 and wdc["wow"] < -1.0:
            alerts.append(f"MU {mu['wow']:+.1f}% vs WDC {wdc['wow']:+.1f}% WoW — DRAM/HBM dominant, NAND lagging")

    return alerts


def _check_mu_wdc_ratio(data: dict) -> str:
    """Check if MU/WDC ratio is at extremes (valuation signal)."""
    mu = data.get("MU")
    wdc = data.get("WDC")
    if not mu or not wdc or len(mu["closes"]) < 20 or len(wdc["closes"]) < 20:
        return None
    min_len = min(len(mu["closes"]), len(wdc["closes"]))
    ratios = [mu["closes"][i] / wdc["closes"][i]
              for i in range(min_len) if wdc["closes"][i] > 0]
    if len(ratios) < 20:
        return None
    current = ratios[-1]
    mean = sum(ratios) / len(ratios)
    std = (sum((r - mean) ** 2 for r in ratios) / len(ratios)) ** 0.5
    if std == 0:
        return None
    z = (current - mean) / std
    if abs(z) >= MU_WDC_RATIO_ZSCORE:
        signal = ("MU premium elevated — watch for mean reversion" if z > 0
                  else "MU cheap vs NAND peer — catch-up opportunity")
        return f"MU/WDC Ratio: {current:.2f} (avg {mean:.2f}, z={z:+.1f}) — {signal}"
    return None


def check_memory_pricing():
    """Main function - check memory pricing proxies and alert on signals."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_alert_date") == today:
        return

    data = _get_memory_proxy_data()
    if len(data) < 2:
        return

    divergence_alerts = _check_divergence(data)
    ratio_alert = _check_mu_wdc_ratio(data)

    lines = ["MEMORY PRICING PROXY UPDATE\n"]
    lines.append(f"{'Ticker':<18} {'Price':>9} {'WoW':>8} {'MoM':>8} {'Vol':>6}")
    lines.append("-" * 52)
    for ticker in MEMORY_TICKERS:
        if ticker not in data:
            continue
        d = data[ticker]
        wow_flag = " !" if abs(d["wow"]) >= WOW_MOMENTUM_ALERT else ""
        mom_flag = " !" if abs(d["mom"]) >= MOM_MOMENTUM_ALERT else ""
        vol_flag = " H" if d["vol_ratio"] > 1.5 else ""
        lines.append(
            f"{d['name']:<18} {d['price']:>8.2f} {d['wow']:>+7.1f}%{wow_flag}"
            f" {d['mom']:>+7.1f}%{mom_flag} {d['vol_ratio']:>4.1f}x{vol_flag}"
        )

    mu, hynix = data.get("MU"), data.get("000660.KS")
    if mu and hynix:
        if hynix["wow"] > 3.0 and mu["wow"] > 3.0:
            lines.append("\nSignal: Memory broadly strong — HBM/DRAM pricing power")
        elif hynix["wow"] > 3.0 and mu["wow"] < 0:
            lines.append("\nSignal: SK Hynix leading, MU lagging — watch for catch-up")
        elif hynix["wow"] < -3.0 and mu["wow"] < -3.0:
            lines.append("\nSignal: Memory weak — check for demand/pricing concerns")

    if divergence_alerts:
        lines.append("\nDivergences:")
        for alert in divergence_alerts:
            lines.append(f"  {alert}")
    if ratio_alert:
        lines.append(f"\n{ratio_alert}")

    has_signal = (
        divergence_alerts or ratio_alert or
        any(abs(data[t]["wow"]) >= WOW_MOMENTUM_ALERT for t in data) or
        any(abs(data[t]["mom"]) >= MOM_MOMENTUM_ALERT for t in data)
    )

    if not has_signal:
        # Still update state so we don't recheck today
        state["last_alert_date"] = today
        save_state(state)
        return

    msg = "\n".join(lines) + "\n\n#MEMORY_PRICING"
    send_alert(msg)

    state["last_alert_date"] = today
    if ratio_alert:
        state["last_ratio_alert_date"] = today
    save_state(state)


def get_memory_pricing_summary() -> str:
    """Return formatted memory pricing summary for use by daily briefing."""
    data = _get_memory_proxy_data()
    if not data:
        return "Memory pricing data unavailable"

    lines = ["Memory Sector Momentum:"]
    lines.append(f"{'Name':<18} {'WoW':>8} {'MoM':>8}")
    lines.append("-" * 36)
    for ticker in MEMORY_TICKERS:
        if ticker not in data:
            continue
        d = data[ticker]
        lines.append(f"{d['name']:<18} {d['wow']:>+7.1f}% {d['mom']:>+7.1f}%")

    divergences = _check_divergence(data)
    if divergences:
        lines.append(f"\nDivergence: {divergences[0].split(chr(10))[0]}")

    ratio = _check_mu_wdc_ratio(data)
    if ratio:
        lines.append(f"\n{ratio.split(chr(10))[0]}")

    return "\n".join(lines)
