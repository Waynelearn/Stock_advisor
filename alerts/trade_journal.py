"""Trade Journal Bot - logs trades, tracks P&L, post-mortem analysis."""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .config import DEEPSEEK_API_KEY, POSITION, TZ_SGT
from .bot import send_alert
from .price_monitor import get_live_price, estimate_spread_value

JOURNAL_FILE = os.path.join(os.path.dirname(__file__), ".trade_journal.json")


def load_journal() -> list:
    if os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE) as f:
            return json.load(f)
    return []


def save_journal(journal: list):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(journal, f, indent=2)


def log_trade(action: str, details: str = "") -> dict:
    """Log a trade action (BUY/SELL/ADJUST) with current market snapshot."""
    if not POSITION.get("contracts"):
        msg = "\U0001f4dd <b>TRADE JOURNAL</b>\nNo active position to log.\n\n<code>#JOURNAL</code>"
        send_alert(msg)
        return {}
    journal = load_journal()
    now = datetime.now(ZoneInfo(TZ_SGT))

    mu_price = get_live_price(POSITION["ticker"]) or 0
    spread_val = estimate_spread_value(mu_price) if mu_price else 0
    entry = POSITION["entry_price"]
    contracts = POSITION["contracts"]
    pnl = (spread_val - entry) * contracts * 100

    entry_record = {
        "id": len(journal) + 1,
        "timestamp": now.isoformat(),
        "action": action.upper(),
        "details": details,
        "mu_price": round(mu_price, 2),
        "spread_value": round(spread_val, 2),
        "pnl": round(pnl, 0),
        "notes": "",
    }

    journal.append(entry_record)
    save_journal(journal)

    # Send confirmation
    msg = (
        f"\U0001f4dd <b>TRADE LOGGED</b>\n"
        + "\u2500" * 20 + "\n\n"
        f"Action: <b>{action.upper()}</b>\n"
        f"MU: ${mu_price:.2f}\n"
        f"Spread: ${spread_val:.2f}\n"
        f"P&L: ${pnl:+,.0f}\n"
    )
    if details:
        msg += f"Notes: {details}\n"
    msg += "\n<code>#JOURNAL</code>"
    send_alert(msg)

    return entry_record


def get_journal_summary() -> str:
    """Generate a summary of all journal entries."""
    journal = load_journal()
    if not journal:
        return "No trades logged yet."

    lines = [
        f"\U0001f4d3 <b>TRADE JOURNAL</b> ({len(journal)} entries)",
        "\u2500" * 20,
        "",
    ]

    for e in journal[-10:]:  # Last 10 entries
        ts = e.get("timestamp", "")[:16]
        lines.append(
            f"  #{e['id']} {ts} | {e['action']} | "
            f"MU ${e['mu_price']:.2f} | P&L ${e['pnl']:+,.0f}"
        )
        if e.get("details"):
            lines.append(f"     {e['details']}")

    return "\n".join(lines)


def post_mortem() -> str:
    """Generate a post-mortem analysis of the trade using DeepSeek."""
    if not POSITION.get("contracts"):
        return "\U0001f50d <b>POST-MORTEM</b>\nNo active position for post-mortem."
    journal = load_journal()
    mu_price = get_live_price(POSITION["ticker"]) or 0
    spread_val = estimate_spread_value(mu_price) if mu_price else 0
    entry = POSITION["entry_price"]
    pnl = (spread_val - entry) * POSITION["contracts"] * 100

    journal_str = ""
    for e in journal[-10:]:
        journal_str += f"  {e['timestamp'][:10]} {e['action']}: MU ${e['mu_price']}, P&L ${e['pnl']:+,.0f}\n"

    prompt = (
        f"You are a trading coach reviewing a completed options trade.\n\n"
        f"TRADE: 500x MU 380/400 bull call spread, entry $11.897, expiry March 20, 2026\n"
        f"Current: MU ${mu_price:.2f}, Spread ${spread_val:.2f}, P&L ${pnl:+,.0f}\n\n"
        f"JOURNAL ENTRIES:\n{journal_str}\n"
        f"In 3-4 sentences: What went right? What went wrong? Key lessons for next trade? "
        f"Grade the trade A-F."
    )

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 200},
            timeout=15,
        )
        resp.raise_for_status()
        analysis = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        analysis = "Post-mortem analysis unavailable."

    msg = (
        f"\U0001f50d <b>POST-MORTEM</b>\n"
        + "\u2500" * 20 + "\n\n"
        f"Final P&L: <b>${pnl:+,.0f}</b>\n\n"
        f"\U0001f9e0 {analysis}"
    )
    return msg
