"""Spread Recommender - suggests optimal MU bull call spreads.

Analyzes option chains to find spreads optimized for:
- Risk/reward ratio
- Probability of finishing at max profit
- Catalyst exposure (events between now and expiry)
- Position sizing based on Kelly criterion / risk budget
"""

import json
import os
import math
import yfinance as yf
import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo

from .config import (
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL_PRO, POSITION, TZ_ET, TZ_SGT, CATALYSTS,
    SPREAD_REC,
)
from .bot import send_alert
from .llm import ask

STATE_FILE = os.path.join(os.path.dirname(__file__), ".spread_rec_state.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_rec_date": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _get_catalysts_before_expiry(expiry_str: str) -> list[dict]:
    """Get all catalysts between now and expiry date."""
    now = datetime.now(ZoneInfo(TZ_ET))
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
    upcoming = []
    for month, day, hour, minute, desc in CATALYSTS:
        try:
            cat_dt = datetime(now.year, month, day, hour, minute, tzinfo=ZoneInfo(TZ_ET))
            if now < cat_dt <= expiry:
                upcoming.append({"date": cat_dt.strftime("%m/%d"), "time": f"{hour}:{minute:02d} ET", "desc": desc})
        except ValueError:
            continue
    return upcoming


def _black_scholes_prob(S, K, T, sigma, r=0.04):
    """Probability that stock finishes above K at time T (years)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S >= K else 0.0
    d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d2)


def _norm_cdf(x):
    """Standard normal CDF approximation."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _scan_spreads(ticker: str, expiry: str, current_price: float) -> list[dict]:
    """Scan all viable bull call spreads and score them."""
    t = yf.Ticker(ticker)
    try:
        chain = t.option_chain(expiry)
    except Exception as e:
        print(f"[SPREAD REC] Chain fetch error: {e}")
        return []

    calls = chain.calls
    if calls.empty:
        return []

    # Filter strikes: long_strike from -10% to ATM, short_strike from ATM to +15%
    min_long = current_price * 0.90
    max_short = current_price * 1.15

    # Calculate DTE
    today = date.today()
    expiry_date = date.fromisoformat(expiry)
    dte = (expiry_date - today).days
    T = dte / 365.0

    viable = calls[
        (calls["strike"] >= min_long) & (calls["strike"] <= max_short) &
        (calls["bid"] > 0) & (calls["ask"] > 0)
    ].copy()

    if len(viable) < 2:
        return []

    strikes = sorted(viable["strike"].unique())
    spreads = []

    for i, long_strike in enumerate(strikes):
        long_row = viable[viable["strike"] == long_strike].iloc[0]
        long_ask = long_row["ask"]
        long_iv = long_row["impliedVolatility"]

        for short_strike in strikes[i + 1:]:
            width = short_strike - long_strike
            if width < 5 or width > 30:
                continue

            short_row = viable[viable["strike"] == short_strike].iloc[0]
            short_bid = short_row["bid"]
            short_iv = short_row["impliedVolatility"]

            net_debit = long_ask - short_bid
            if net_debit <= 0 or net_debit >= width:
                continue

            max_profit = width - net_debit
            risk_reward = max_profit / net_debit
            breakeven = long_strike + net_debit

            # Use average IV for probability calc
            avg_iv = (long_iv + short_iv) / 2
            if avg_iv <= 0:
                continue

            # Quality filters — drop spreads that are uneconomic to trade.
            min_profit = max(
                SPREAD_REC["min_max_profit_dollars"],
                SPREAD_REC["min_max_profit_ratio"] * width,
            )
            if max_profit < min_profit:
                continue
            if risk_reward < SPREAD_REC["min_risk_reward"]:
                continue

            # Probability of max profit (finish above short strike)
            prob_max = _black_scholes_prob(current_price, short_strike, T, avg_iv)

            # Probability of profit (finish above breakeven)
            prob_profit = _black_scholes_prob(current_price, breakeven, T, avg_iv)

            # Expected value = prob_max * max_profit - (1 - prob_profit) * net_debit
            ev = prob_max * max_profit - (1 - prob_profit) * net_debit

            # Kelly criterion: f = (bp - q) / b where b = risk_reward, p = prob_profit, q = 1-p
            kelly = 0
            if risk_reward > 0:
                p = prob_profit
                q = 1 - p
                kelly = max(0, (risk_reward * p - q) / risk_reward)

            # Composite score combines: prob, R:R, EV, AND Kelly (so Kelly-positive
            # setups dominate the leaderboard, not high-prob low-edge spreads).
            kelly_normalizer = SPREAD_REC["kelly_full_credit_pct"] / 100.0
            score = (
                SPREAD_REC["score_weight_prob_max"]    * min(prob_max, 1.0)
                + SPREAD_REC["score_weight_prob_profit"] * min(prob_profit, 1.0)
                + SPREAD_REC["score_weight_risk_reward"] * min(risk_reward / SPREAD_REC["rr_full_credit"], 1.0)
                + SPREAD_REC["score_weight_ev"]          * max(0, min(ev / net_debit, 1.0))
                + SPREAD_REC["score_weight_kelly"]       * min(kelly / kelly_normalizer, 1.0)
            )

            spreads.append({
                "long_strike": long_strike,
                "short_strike": short_strike,
                "width": width,
                "net_debit": round(net_debit, 2),
                "max_profit": round(max_profit, 2),
                "risk_reward": round(risk_reward, 2),
                "breakeven": round(breakeven, 2),
                "prob_max": round(prob_max * 100, 1),
                "prob_profit": round(prob_profit * 100, 1),
                "ev": round(ev, 2),
                "kelly_pct": round(kelly * 100, 1),
                "avg_iv": round(avg_iv * 100, 1),
                "score": round(score * 100, 1),
                "expiry": expiry,
                "dte": dte,
            })

    # Sort by score descending
    spreads.sort(key=lambda x: x["score"], reverse=True)
    return spreads


def _get_position_size_pct(spread: dict, base_risk_pct: float | None = None) -> float:
    """Recommended portfolio % to allocate to a spread's net debit.

    All thresholds, multipliers, and caps live in `SPREAD_REC` in config.py
    so this function has no in-line magic numbers.
    """
    if base_risk_pct is None:
        base_risk_pct = SPREAD_REC["base_risk_pct"]

    if not isinstance(spread, dict):
        # Legacy: caller passed a kelly_pct number directly. Apply same
        # config-driven baseline so behaviour stays consistent.
        kelly_pct = float(spread or 0)
        legacy_size = max(
            SPREAD_REC["size_floor_pct"],
            kelly_pct * SPREAD_REC["size_mult_low"] / SPREAD_REC["kelly_bump_normalizer"],
        )
        return round(min(legacy_size, SPREAD_REC["size_cap_pct"]), 1)

    score = float(spread.get("score") or 0)
    kelly_pct = float(spread.get("kelly_pct") or 0)

    if score >= SPREAD_REC["score_threshold_high"]:
        score_mult = SPREAD_REC["size_mult_high"]
    elif score >= SPREAD_REC["score_threshold_mid"]:
        score_mult = SPREAD_REC["size_mult_mid"]
    elif score >= SPREAD_REC["score_threshold_low"]:
        score_mult = SPREAD_REC["size_mult_low"]
    else:
        score_mult = SPREAD_REC["size_mult_below_low"]

    kelly_mult = 1.0 + min(
        kelly_pct / SPREAD_REC["kelly_bump_full_pct"],
        SPREAD_REC["kelly_bump_max_multiplier"],
    )

    size = base_risk_pct * score_mult * (kelly_mult / SPREAD_REC["kelly_bump_normalizer"])
    if score >= SPREAD_REC["score_threshold_low"] and size < SPREAD_REC["size_floor_pct"]:
        size = SPREAD_REC["size_floor_pct"]
    return round(min(size, SPREAD_REC["size_cap_pct"]), 1)


def recommend_spreads(ticker: str = "MU", num_expiries: int = 3) -> list[dict]:
    """Find and rank the best bull call spreads across multiple expiries."""
    t = yf.Ticker(ticker)
    info = t.fast_info
    current_price = info.get("lastPrice") or info.get("last_price") or 0
    if not current_price:
        return []

    try:
        all_expiries = list(t.options)
    except Exception:
        return []

    # Filter: only expiries within 7-60 days
    today = date.today()
    valid_expiries = []
    for exp in all_expiries:
        exp_date = date.fromisoformat(exp)
        dte = (exp_date - today).days
        if 7 <= dte <= 60:
            valid_expiries.append(exp)
        if len(valid_expiries) >= num_expiries:
            break

    all_spreads = []
    for expiry in valid_expiries:
        spreads = _scan_spreads(ticker, expiry, current_price)
        all_spreads.extend(spreads)

    # Re-sort all by score
    all_spreads.sort(key=lambda x: x["score"], reverse=True)
    return all_spreads


def send_spread_recommendations():
    """Generate and send spread recommendations via Telegram."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_rec_date") == today:
        return

    try:
        t = yf.Ticker("MU")
        info = t.fast_info
        current_price = info.get("lastPrice") or info.get("last_price") or 0
        if not current_price:
            return

        all_spreads = recommend_spreads("MU", num_expiries=3)
        if not all_spreads:
            return

        # Top 5 spreads
        top = all_spreads[:5]

        # Get catalysts for context
        catalysts = []
        for s in top:
            cats = _get_catalysts_before_expiry(s["expiry"])
            catalysts.extend(cats)
        # Deduplicate
        seen = set()
        unique_cats = []
        for c in catalysts:
            key = c["desc"]
            if key not in seen:
                seen.add(key)
                unique_cats.append(c)

        # Build message
        lines = [
            "\U0001f3af <b>SPREAD RECOMMENDATIONS</b>",
            "\u2500" * 20,
            f"\nMU: <b>${current_price:.2f}</b>",
            "",
        ]

        for i, s in enumerate(top, 1):
            # Position sizing (uses score + Kelly, not Kelly alone)
            size_pct = _get_position_size_pct(s)
            cats_before = _get_catalysts_before_expiry(s["expiry"])
            cat_count = len(cats_before)
            key_events = [c["desc"] for c in cats_before if any(kw in c["desc"].upper() for kw in ["FOMC", "EARNINGS", "CPI", "NFP", "GTC"])]

            emoji = ["\U0001f947", "\U0001f948", "\U0001f949", "4\ufe0f\u20e3", "5\ufe0f\u20e3"][i - 1]

            lines.append(
                f"{emoji} <b>${s['long_strike']:.0f}/{s['short_strike']:.0f}C</b> "
                f"exp {s['expiry']} ({s['dte']}d)"
            )
            lines.append(
                f"   Debit: ${s['net_debit']:.2f} | Max: ${s['max_profit']:.2f} | "
                f"R:R <b>{s['risk_reward']:.1f}x</b>"
            )
            lines.append(
                f"   BE: ${s['breakeven']:.2f} | "
                f"P(max): <b>{s['prob_max']:.0f}%</b> | P(profit): {s['prob_profit']:.0f}%"
            )
            lines.append(
                f"   IV: {s['avg_iv']:.0f}% | Kelly: {s['kelly_pct']:.1f}% | "
                f"Size: <b>{size_pct:.1f}%</b> of portfolio"
            )
            if key_events:
                lines.append(f"   \u26a1 {', '.join(key_events[:2])}")
            lines.append(f"   Score: {s['score']:.0f}/100")
            lines.append("")

        # Catalyst section
        if unique_cats:
            lines.append("<b>Key Catalysts Before Expiry:</b>")
            for c in unique_cats[:6]:
                lines.append(f"  \U0001f4c5 {c['date']} {c['time']} - {c['desc']}")
            lines.append("")

        # DeepSeek analysis
        analysis = _analyze_recommendations(top, current_price, unique_cats)
        lines.append(f"\U0001f9e0 {analysis}")
        lines.append("")
        lines.append("<code>#SPREAD_REC</code>")

        send_alert("\n".join(lines))

        state["last_rec_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[SPREAD REC ERROR] {e}")


def _analyze_recommendations(spreads: list[dict], price: float, catalysts: list[dict]) -> str:
    """DeepSeek analysis of the recommended spreads."""
    top3 = spreads[:3]
    spread_text = "\n".join(
        f"  {s['long_strike']}/{s['short_strike']}C exp {s['expiry']}: "
        f"debit ${s['net_debit']}, R:R {s['risk_reward']}x, P(max) {s['prob_max']}%, "
        f"Kelly {s['kelly_pct']}%"
        for s in top3
    )
    cat_text = "\n".join(f"  {c['date']} - {c['desc']}" for c in catalysts[:5])

    prompt = (
        f"MU is at ${price:.2f}. Top recommended bull call spreads:\n{spread_text}\n\n"
        f"Upcoming catalysts:\n{cat_text}\n\n"
        f"In 2-3 sentences: which spread offers the best risk-adjusted entry given these catalysts? "
        f"Should the trader wait for a specific event, or enter now? "
        f"Consider IV levels, event risk, and timing."
    )
    return ask(prompt, tier="reasoning", temperature=0.3, max_tokens=1500,
               label="spread_recommender", fallback="Analysis unavailable.")


if __name__ == "__main__":
    send_spread_recommendations()
