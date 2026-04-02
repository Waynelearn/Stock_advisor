"""Prediction market monitor — Polymarket/Kalshi odds for Fed, geopolitical events.

Weekend shifts in prediction markets can front-run Monday moves.
Tracks probabilities for rate decisions, geopolitical outcomes, elections.
"""

import json
import os
import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".prediction_market_state.json")

# Polymarket API (public, no auth needed for read)
POLYMARKET_API = "https://gamma-api.polymarket.com"

# Keywords to search for relevant markets
RELEVANT_KEYWORDS = [
    "fed rate",
    "federal reserve",
    "interest rate",
    "fomc",
    "iran",
    "israel",
    "taiwan",
    "china",
    "tariff",
    "semiconductor",
    "recession",
    "inflation",
    "ceasefire",
]

# Thresholds
PROB_SHIFT_THRESHOLD = 5.0   # Alert if probability shifts >5% from last check


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_alert_date": None, "tracked_markets": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _search_polymarket(keyword: str) -> list:
    """Search Polymarket for active markets matching keyword."""
    try:
        url = f"{POLYMARKET_API}/markets"
        params = {
            "tag": keyword,
            "active": "true",
            "limit": 5,
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            return []

        data = resp.json()
        markets = []

        items = data if isinstance(data, list) else data.get("data", data.get("markets", []))
        if not isinstance(items, list):
            return []

        for m in items:
            if not isinstance(m, dict):
                continue
            question = m.get("question", "") or m.get("title", "")
            if not question:
                continue

            # Get outcome probabilities
            outcomes = m.get("outcomes", []) or m.get("tokens", [])
            prob_yes = None

            if isinstance(outcomes, list):
                for o in outcomes:
                    if isinstance(o, dict):
                        name = (o.get("outcome", "") or o.get("name", "")).lower()
                        price = o.get("price") or o.get("lastTradePrice")
                        if name in ("yes", "true") and price:
                            prob_yes = float(price) * 100
                            break
            elif m.get("outcomePrices"):
                try:
                    prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
                    if isinstance(prices, list) and len(prices) >= 1:
                        prob_yes = float(prices[0]) * 100
                except Exception:
                    pass

            if prob_yes is None:
                # Try clobTokenIds approach
                prob_yes_raw = m.get("bestBid") or m.get("lastTradePrice")
                if prob_yes_raw:
                    prob_yes = float(prob_yes_raw) * 100

            markets.append({
                "question": question[:200],
                "prob_yes": prob_yes,
                "volume": m.get("volume") or m.get("volumeNum", 0),
                "market_id": m.get("conditionId") or m.get("id", ""),
            })

        return markets
    except Exception:
        return []


def check_prediction_markets():
    """Main function — scan prediction markets for shifts relevant to MU."""
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")
    state = load_state()

    if state.get("last_alert_date") == today:
        return

    tracked = state.get("tracked_markets", {})
    all_markets = []
    significant_shifts = []

    for keyword in RELEVANT_KEYWORDS:
        markets = _search_polymarket(keyword)
        for m in markets:
            q = m["question"]
            if q in [am["question"] for am in all_markets]:
                continue  # dedup
            all_markets.append(m)

            # Check for shift from previous
            prev = tracked.get(q, {})
            prev_prob = prev.get("prob_yes")

            if m["prob_yes"] is not None and prev_prob is not None:
                shift = m["prob_yes"] - prev_prob
                if abs(shift) >= PROB_SHIFT_THRESHOLD:
                    significant_shifts.append({
                        "question": q,
                        "prev_prob": prev_prob,
                        "current_prob": m["prob_yes"],
                        "shift": shift,
                    })

            # Update tracked
            if m["prob_yes"] is not None:
                tracked[q] = {
                    "prob_yes": m["prob_yes"],
                    "last_updated": today,
                }

    # Filter to most relevant markets (those with data)
    active_markets = [m for m in all_markets if m["prob_yes"] is not None]

    # Determine if we should alert
    should_alert = bool(significant_shifts)

    # Also alert on first run with interesting markets
    if not tracked and active_markets:
        should_alert = True

    if not should_alert and not active_markets:
        state["tracked_markets"] = tracked
        save_state(state)
        return

    # Even without shifts, send periodic update on weekends
    is_weekend = now_sgt.weekday() >= 5
    if not should_alert and is_weekend and active_markets:
        should_alert = True

    if not should_alert:
        state["tracked_markets"] = tracked
        save_state(state)
        return

    msg = "PREDICTION MARKET UPDATE\n\n"

    if significant_shifts:
        msg += "SIGNIFICANT SHIFTS:\n"
        for s in sorted(significant_shifts, key=lambda x: abs(x["shift"]), reverse=True):
            direction = "UP" if s["shift"] > 0 else "DOWN"
            msg += f"  {s['question'][:100]}\n"
            msg += f"    {s['prev_prob']:.0f}% -> {s['current_prob']:.0f}% ({s['shift']:+.0f}% {direction})\n\n"

    if active_markets:
        # Group by relevance
        fed_markets = [m for m in active_markets if any(k in m["question"].lower() for k in ["fed", "rate", "fomc", "interest"])]
        geo_markets = [m for m in active_markets if any(k in m["question"].lower() for k in ["iran", "israel", "taiwan", "china", "ceasefire", "war"])]
        other_markets = [m for m in active_markets if m not in fed_markets and m not in geo_markets]

        if fed_markets:
            msg += "FED/RATES:\n"
            for m in fed_markets[:3]:
                prob_str = f"{m['prob_yes']:.0f}%" if m["prob_yes"] is not None else "N/A"
                msg += f"  {m['question'][:100]}: {prob_str}\n"
            msg += "\n"

        if geo_markets:
            msg += "GEOPOLITICAL:\n"
            for m in geo_markets[:3]:
                prob_str = f"{m['prob_yes']:.0f}%" if m["prob_yes"] is not None else "N/A"
                msg += f"  {m['question'][:100]}: {prob_str}\n"
            msg += "\n"

        if other_markets:
            msg += "OTHER:\n"
            for m in other_markets[:2]:
                prob_str = f"{m['prob_yes']:.0f}%" if m["prob_yes"] is not None else "N/A"
                msg += f"  {m['question'][:100]}: {prob_str}\n"
            msg += "\n"

    msg += "#PREDICTION_MARKET"

    send_alert(msg)

    state["last_alert_date"] = today
    state["tracked_markets"] = tracked
    save_state(state)


def get_prediction_summary() -> str:
    """Return key prediction market odds for other modules."""
    state = load_state()
    tracked = state.get("tracked_markets", {})

    if not tracked:
        return "Prediction Markets: No data"

    # Find most relevant
    fed_items = [(q, d) for q, d in tracked.items() if any(k in q.lower() for k in ["fed", "rate", "fomc"])]
    geo_items = [(q, d) for q, d in tracked.items() if any(k in q.lower() for k in ["iran", "ceasefire", "war"])]

    parts = []
    for q, d in (fed_items + geo_items)[:3]:
        short_q = q[:60]
        parts.append(f"{short_q}: {d.get('prob_yes', 0):.0f}%")

    return "Predictions: " + " | ".join(parts) if parts else "Prediction Markets: No data"
