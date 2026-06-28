"""Analyst Rating Tracker - monitors upgrades/downgrades/PT changes."""

import json
import os
import yfinance as yf
import requests
from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, POSITION, position_summary, PEERS, STATE_RETENTION
from .bot import send_alert
from .llm import ask

STATE_FILE = os.path.join(os.path.dirname(__file__), ".analyst_state.json")
TICKERS = [POSITION["ticker"]] + [p for p in PEERS if p != "SOXX"]


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"seen": {}})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def check_analyst_changes():
    """Check for new analyst recommendations on MU and peers."""
    state = load_state()
    new_ratings = []

    for ticker in TICKERS:
        try:
            t = yf.Ticker(ticker)
            recs = t.upgrades_downgrades
            if recs is None or recs.empty:
                continue

            # Get the most recent recommendations
            recent = recs.head(5)
            for idx, row in recent.iterrows():
                firm = row.get("Firm", row.get("firm", "Unknown"))
                key = f"{ticker}_{idx}_{firm}"
                if key in state["seen"]:
                    continue

                state["seen"][key] = True
                grade = row.get("ToGrade", row.get("To Grade", ""))
                prev_grade = row.get("FromGrade", row.get("From Grade", ""))
                action = row.get("Action", row.get("action", ""))

                if not grade:
                    continue

                new_ratings.append({
                    "ticker": ticker,
                    "firm": firm,
                    "grade": grade,
                    "prev_grade": prev_grade,
                    "action": action,
                    "date": str(idx),
                })
        except Exception:
            continue

    # Keep state manageable
    if len(state["seen"]) > STATE_RETENTION["analyst_seen"]:
        keys = list(state["seen"].keys())
        state["seen"] = {k: True for k in keys[-100:]}

    save_state(state)

    if not new_ratings:
        return

    # Alert on new ratings
    for r in new_ratings[:3]:
        # Determine emoji
        action = (r["action"] or "").lower()
        if "upgrade" in action or "initiat" in action:
            emoji = "\u2b06\ufe0f"  # ⬆️
        elif "downgrade" in action:
            emoji = "\u2b07\ufe0f"  # ⬇️
        else:
            emoji = "\u27a1\ufe0f"  # ➡️

        grade_change = f"{r['prev_grade']} \u2192 {r['grade']}" if r['prev_grade'] else (r['grade'] or "N/A")

        # DeepSeek analysis for MU-related ratings
        analysis = ""
        if r["ticker"] == "MU" or len(new_ratings) <= 2:
            analysis = _analyze_rating(r)

        msg = (
            f"{emoji} <b>ANALYST: {r['ticker']}</b>\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"\U0001f3e2 {r['firm']}\n"
            f"\U0001f4cb {(action or 'update').title()}: {grade_change}\n"
        )
        if analysis:
            msg += f"\n\U0001f9e0 {analysis}"
        msg += "\n\n<code>#ANALYST</code>"

        send_alert(msg)


def _analyze_rating(rating: dict) -> str:
    """Ask DeepSeek what this rating change means for MU."""
    prompt = (
        f"{rating['firm']} just {rating['action']} {rating['ticker']} "
        f"from {rating['prev_grade']} to {rating['grade']}.\n"
        f"My position: {position_summary()}.\n"
        f"In 1-2 sentences: what does this mean for MU? Is this firm influential?"
    )
    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="analyst_tracker", fallback="")
