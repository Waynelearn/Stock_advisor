"""Telegram bot - send alerts via Bot API."""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

MESSAGE_LOG_FILE = os.path.join(os.path.dirname(__file__), ".message_log.json")


def _log_message(message: str, msg_type: str = "text", success: bool = True):
    """Log every sent message with timestamp."""
    log = []
    if os.path.exists(MESSAGE_LOG_FILE):
        try:
            with open(MESSAGE_LOG_FILE) as f:
                log = json.load(f)
        except Exception:
            log = []

    log.append({
        "timestamp": datetime.now(ZoneInfo("Asia/Singapore")).isoformat(),
        "type": msg_type,
        "success": success,
        "message": message[:500],  # Truncate for log storage
    })

    # Keep last 200 messages
    log = log[-200:]
    try:
        with open(MESSAGE_LOG_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except Exception:
        pass


def get_message_log(limit: int = 20) -> list[dict]:
    """Read the last N logged messages."""
    if not os.path.exists(MESSAGE_LOG_FILE):
        return []
    try:
        with open(MESSAGE_LOG_FILE) as f:
            log = json.load(f)
        return log[-limit:]
    except Exception:
        return []


def send_alert(message: str, parse_mode: str = "HTML", image_url: str = None) -> bool:
    """Send a message to Telegram. If image_url provided, sends as photo with caption."""
    if image_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        # Telegram captions have 1024 char limit
        caption = message[:1024] if len(message) > 1024 else message
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": image_url,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            _log_message(message, msg_type="photo", success=True)
            return True
        except Exception:
            # Fall through to text-only if photo fails
            pass

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Split long messages to respect Telegram's 4096-char limit
    MAX_LEN = 4096
    chunks = []
    if len(message) <= MAX_LEN:
        chunks = [message]
    else:
        remaining = message
        while remaining:
            if len(remaining) <= MAX_LEN:
                chunks.append(remaining)
                break
            # Try to split at last newline before limit
            split_at = remaining[:MAX_LEN].rfind("\n")
            if split_at < MAX_LEN // 2:
                split_at = MAX_LEN  # No good newline, hard split
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")

    success = True
    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"[ALERT FAILED] {e}")
            success = False

    _log_message(message, msg_type="text", success=success)
    return success


def send_price_alert(ticker: str, price: float, level: float, direction: str):
    """Send a price level crossing alert."""
    if direction == "up":
        emoji = "\U0001f7e2"  # green circle
        arrow = "\u25b2"
        word = "above"
    else:
        emoji = "\U0001f534"  # red circle
        arrow = "\u25bc"
        word = "below"
    msg = (
        f"{emoji} <b>${ticker} {arrow} ${level:.0f}</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4b0 Now:  <b>${price:.2f}</b>\n\n"
        f"<code>#PRICE</code>"
    )
    send_alert(msg)


def send_big_move_alert(ticker: str, price: float, change_pct: float, prev_close: float):
    """Send alert for large intraday move."""
    if change_pct > 0:
        emoji = "\U0001f680"  # rocket
        arrow = "\u25b2"
    else:
        emoji = "\U0001f4a5"  # boom
        arrow = "\u25bc"
    msg = (
        f"{emoji} <b>{ticker}  {arrow} {change_pct:+.2f}%</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4b0 Now:   <b>${price:.2f}</b>\n"
        f"\U0001f4c5 Prev:  ${prev_close:.2f}\n\n"
        f"<code>#BIGMOVE</code>"
    )
    send_alert(msg)


def send_vix_alert(vix: float, level: float, direction: str):
    """Send VIX level alert."""
    if direction == "up":
        severity = {25: "\u26a0\ufe0f", 30: "\U0001f6a8", 35: "\U0001f525"}  # ⚠️ 🚨 🔥
        icon = severity.get(level, "\u26a0\ufe0f")
        word = "above"
        label = {25: "CAUTION", 30: "DANGER", 35: "EXTREME FEAR"}
    else:
        icon = "\u2705"  # ✅
        word = "below"
        label = {25: "CALMING", 30: "EASING", 35: "DE-ESCALATING"}
    msg = (
        f"{icon} <b>VIX {label.get(level, '')} \u2014 {word} {level}</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4ca VIX:  <b>{vix:.2f}</b>\n\n"
        f"<code>#VIX</code>"
    )
    send_alert(msg)


def send_catalyst_reminder(event: str, minutes_until: int):
    """Send a catalyst reminder."""
    if minutes_until <= 15:
        icon = "\U0001f534\U0001f514"  # 🔴🔔
        urgency = "15 MIN"
    else:
        icon = "\U0001f7e1\U0001f514"  # 🟡🔔
        urgency = "1 HOUR"
    msg = (
        f"{icon} <b>CATALYST IN {urgency}</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4cc {event}\n\n"
        f"<code>#CATALYST</code>"
    )
    send_alert(msg)


def send_spread_update(price: float, spread_value: float, pnl: float, pnl_pct: float):
    """Send spread P&L update."""
    if pnl > 0:
        icon = "\U0001f4b9"  # 💹
    else:
        icon = "\U0001f4c9"  # 📉
    msg = (
        f"{icon} <b>SPREAD  ${pnl:+,.0f}  ({pnl_pct:+.1f}%)</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4b0 MU:      <b>${price:.2f}</b>\n"
        f"\U0001f4ca Spread:  ${spread_value:.2f}\n\n"
        f"<code>#SPREAD_PNL</code>"
    )
    send_alert(msg)


def send_daily_briefing(briefing: str):
    """Send the daily evening briefing."""
    send_alert(briefing)


if __name__ == "__main__":
    send_alert("\U0001f4e1 <b>MU Advisor Bot</b>\nBot is online and ready.")
