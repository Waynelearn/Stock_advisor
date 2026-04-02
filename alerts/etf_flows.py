"""ETF flow monitor - tracks creations/redemptions for semi ETFs and estimates passive MU pressure."""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".etf_flows_state.json")

MU_ETF_WEIGHTS = {
    "SOXX": {"name": "iShares Semi ETF", "mu_weight": 0.045},
    "SMH": {"name": "VanEck Semi ETF", "mu_weight": 0.055},
    "XSD": {"name": "SPDR S&P Semi ETF", "mu_weight": 0.04},
    "QQQ": {"name": "Invesco QQQ", "mu_weight": 0.005},
    "XLK": {"name": "Tech Select SPDR", "mu_weight": 0.003},
}

VOLUME_ALERT_RATIO = 2.0     # Alert if volume >2x 20-day avg
MU_FLOW_THRESHOLD = 20e6     # Alert if estimated MU passive flow >$20M

# Quarterly rebalance dates (index reconstitution = forced buying/selling)
REBALANCE_DATES = {
    "2026-03-20": "March quarterly OPEX + index rebalance",
    "2026-06-19": "June quarterly OPEX + Russell reconstitution",
    "2026-09-18": "September quarterly OPEX + index rebalance",
    "2026-12-18": "December quarterly OPEX + index rebalance",
}


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_date": None, "etf_data": {}, "alerts_sent": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _fmt_dollar(val: float) -> str:
    """Format dollar amount with appropriate suffix."""
    if abs(val) >= 1e9:
        return f"${val / 1e9:,.1f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:,.1f}M"
    return f"${val:,.0f}"


def _fmt_number(val: float) -> str:
    """Format large numbers with suffix."""
    if abs(val) >= 1e9:
        return f"{val / 1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"{val / 1e6:.1f}M"
    if abs(val) >= 1e3:
        return f"{val / 1e3:.1f}K"
    return f"{val:.0f}"


def _get_etf_data(ticker: str) -> dict:
    """Fetch ETF data from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        hist = t.history(period="1mo")

        if hist.empty:
            return None

        price = hist["Close"].iloc[-1]
        last_vol = hist["Volume"].iloc[-1]
        avg_vol = hist["Volume"].rolling(20).mean().iloc[-1] if len(hist) >= 20 else hist["Volume"].mean()
        vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1.0

        total_assets = info.get("totalAssets") or info.get("netAssets", 0)
        shares_out = info.get("sharesOutstanding", 0)

        return {
            "price": price,
            "volume": last_vol,
            "avg_volume": avg_vol,
            "vol_ratio": vol_ratio,
            "total_assets": total_assets,
            "shares_outstanding": shares_out,
        }
    except Exception:
        return None


def check_etf_flows():
    """Main function - check ETF flows and estimate MU passive pressure."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_date") == today:
        return

    prev_data = state.get("etf_data", {})
    alerts = []
    total_mu_flow = 0
    current_data = {}
    flow_direction_count = {"inflow": 0, "outflow": 0}

    for ticker, meta in MU_ETF_WEIGHTS.items():
        data = _get_etf_data(ticker)
        if not data:
            continue

        current_data[ticker] = {
            "total_assets": data["total_assets"],
            "shares_outstanding": data["shares_outstanding"],
            "volume": data["volume"],
            "price": data["price"],
        }

        etf_alert = {"ticker": ticker, "name": meta["name"], "issues": []}

        # Volume check
        if data["vol_ratio"] >= VOLUME_ALERT_RATIO:
            etf_alert["issues"].append(
                f"Volume: {_fmt_number(data['volume'])} shares ({data['vol_ratio']:.1f}x avg)"
            )

        # Flow estimation via AUM change
        prev = prev_data.get(ticker, {})
        if prev.get("total_assets") and data["total_assets"]:
            aum_change = data["total_assets"] - prev["total_assets"]
            # Price-adjust: subtract market return to isolate flows
            if prev.get("price") and data["price"]:
                market_return = (data["price"] / prev["price"] - 1)
                price_effect = prev["total_assets"] * market_return
                est_flow = aum_change - price_effect
            else:
                est_flow = aum_change

            mu_flow = est_flow * meta["mu_weight"]
            total_mu_flow += mu_flow

            if est_flow > 0:
                flow_direction_count["inflow"] += 1
            elif est_flow < 0:
                flow_direction_count["outflow"] += 1

            if abs(mu_flow) > MU_FLOW_THRESHOLD:
                direction = "inflow" if est_flow > 0 else "outflow"
                etf_alert["issues"].append(
                    f"Est. Flow: {_fmt_dollar(est_flow)} ({direction})\n"
                    f"  MU Impact: ~{_fmt_dollar(abs(mu_flow))} passive {'buying' if mu_flow > 0 else 'selling'} "
                    f"({meta['mu_weight']*100:.1f}% weight)"
                )

        if etf_alert["issues"]:
            alerts.append(etf_alert)

    # Check for consensus signal
    consensus = None
    if flow_direction_count["inflow"] >= 3:
        consensus = "Multiple semi ETFs showing inflows — passive bid supporting MU"
    elif flow_direction_count["outflow"] >= 3:
        consensus = "Multiple semi ETFs showing outflows — passive selling pressure on MU"

    if alerts or (consensus and abs(total_mu_flow) > MU_FLOW_THRESHOLD):
        msg = "ETF FLOW ALERT\n\n"
        for a in alerts:
            msg += f"{a['ticker']} ({a['name']})\n"
            for issue in a["issues"]:
                msg += f"  {issue}\n"
            msg += "\n"

        if total_mu_flow != 0:
            direction = "buying" if total_mu_flow > 0 else "selling"
            msg += f"Total Est. MU Passive Flow: ~{_fmt_dollar(abs(total_mu_flow))} {direction}\n"

        if consensus:
            msg += f"\nSignal: {consensus}\n"

        msg += "\n#ETF_FLOW"
        send_alert(msg)

    # Save state
    state["last_date"] = today
    state["etf_data"] = current_data
    save_state(state)


def get_etf_flow_summary() -> str:
    """Return formatted ETF flow summary for daily briefing."""
    lines = ["ETF Flow Summary:"]
    lines.append(f"{'ETF':<6} {'Name':<22} {'Vol Ratio':>10} {'MU Wt':>6}")
    lines.append("-" * 48)

    for ticker, meta in MU_ETF_WEIGHTS.items():
        data = _get_etf_data(ticker)
        if not data:
            continue
        lines.append(
            f"{ticker:<6} {meta['name']:<22} {data['vol_ratio']:>9.1f}x {meta['mu_weight']*100:>5.1f}%"
        )

    return "\n".join(lines)
