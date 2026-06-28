"""Interactive Telegram Bot - polling listener for commands and reply-based Q&A.

Features:
  - Reply to any bot alert to ask a follow-up question in context
  - Free-text messages are answered as general MU/position questions
  - /commands for specific actions

Commands:
  /ask <question>  - Ask the Roundtable (9 persona debate)
  /committee [end] - Catalysts to expiry + 9-persona verdict (optional YYYY-MM-DD)
  /price <ticker>  - Quick price check with sentiment
  /pnl             - P&L report with expiry table
  /sim <scenario>  - Scenario simulator
  /spreads         - Get spread recommendations
  /journal         - Show trade journal
  /log <action> <details> - Log a trade
  /postmortem      - Run post-mortem analysis
  /scan            - Run watchlist scanner
  /history         - Recent message log
  /help            - Show commands
"""

import json
import os
import re
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY, DEEPSEEK_MODEL_PRO, STATE_RETENTION, TRUNCATION,
    POSITION, position_summary, TZ_SGT, TZ_ET, PEERS,
)
from .bot import send_alert, get_message_log
from .price_monitor import get_live_price, get_prev_close, estimate_spread_value
from .trade_journal import log_trade, get_journal_summary, post_mortem
from .llm import complete


POLL_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
OFFSET_FILE = os.path.join(os.path.dirname(__file__), ".poll_offset.json")
INTERACTION_LOG = os.path.join(os.path.dirname(__file__), ".interaction_log.json")


def _log_interaction(user_msg: str, response: str, msg_type: str = "freetext"):
    """Log every user interaction with timestamp, message, response, and type."""
    log = []
    if os.path.exists(INTERACTION_LOG):
        try:
            with open(INTERACTION_LOG) as f:
                log = json.load(f)
        except Exception:
            log = []

    log.append({
        "timestamp": datetime.now(ZoneInfo(TZ_SGT)).isoformat(),
        "type": msg_type,  # "reply", "command", "freetext"
        "user": user_msg[:500],
        "response": _strip_html(response)[:500] if response else "",
    })

    # Keep last 500 interactions
    log = log[-STATE_RETENTION["interaction_log"]:]
    try:
        with open(INTERACTION_LOG, "w") as f:
            json.dump(log, f, indent=2)
    except Exception:
        pass


def _load_offset() -> int:
    """Load last processed update_id so we don't reprocess after restart."""
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE) as f:
                return json.load(f).get("offset", 0)
        except Exception:
            pass
    return 0


def _save_offset(offset: int):
    """Persist offset to survive restarts."""
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except Exception:
        pass


def _strip_html(text: str) -> str:
    """Remove HTML tags for context extraction."""
    import re as _re
    text = _re.sub(r'<[^>]+>', '', text)
    # Collapse multiple newlines
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _get_full_context() -> str:
    """Build comprehensive market context for DeepSeek prompts.

    Includes: position, live prices, VIX, peers, upcoming catalysts, recent alerts.
    """
    from datetime import date
    from .config import CATALYSTS, FUTURES

    lines = []

    # Position
    mu_price = get_live_price(POSITION["ticker"]) or 0
    spread_val = estimate_spread_value(mu_price) if mu_price else 0
    entry = POSITION["entry_price"]
    pnl = (spread_val - entry) * POSITION["contracts"] * 100
    dte = (date.fromisoformat(POSITION["expiry"]) - date.today()).days

    lines.append(
        f"POSITION: {position_summary()} ({dte} DTE).\n"
        f"Current: {POSITION['ticker']} ${mu_price:.2f}, Spread ~${spread_val:.2f}, P&L ${pnl:+,.0f}"
    )

    # VIX
    vix = get_live_price("^VIX")
    if vix:
        lines.append(f"VIX: {vix:.2f}")

    # Peers
    peer_lines = []
    for peer in PEERS[:4]:
        p = get_live_price(peer)
        prev = get_prev_close(peer)
        if p and prev and prev > 0:
            chg = ((p - prev) / prev) * 100
            peer_lines.append(f"{peer} ${p:.2f} ({chg:+.1f}%)")
    if peer_lines:
        lines.append("Peers: " + ", ".join(peer_lines))

    # Futures
    fut_lines = []
    for fticker, fname in FUTURES.items():
        fp = get_live_price(fticker)
        fprev = get_prev_close(fticker)
        if fp and fprev and fprev > 0:
            fchg = ((fp - fprev) / fprev) * 100
            fut_lines.append(f"{fname} ({fchg:+.1f}%)")
    if fut_lines:
        lines.append("Futures: " + ", ".join(fut_lines))

    # Upcoming catalysts (next 5 within DTE)
    now_et = datetime.now(ZoneInfo(TZ_ET))
    expiry_dt = datetime.strptime(POSITION["expiry"], "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
    upcoming = []
    for month, day, hour, minute, desc in CATALYSTS:
        try:
            cat_dt = datetime(now_et.year, month, day, hour, minute, tzinfo=ZoneInfo(TZ_ET))
            if now_et < cat_dt <= expiry_dt:
                upcoming.append(f"{month}/{day} - {desc}")
        except ValueError:
            continue
    if upcoming:
        lines.append("Upcoming catalysts:\n  " + "\n  ".join(upcoming[:5]))

    # Recent alerts (last 5 from message log)
    recent = get_message_log(5)
    if recent:
        alert_previews = []
        for entry in recent:
            preview = _strip_html(entry.get("message", ""))[:100]
            if preview:
                alert_previews.append(preview)
        if alert_previews:
            lines.append("Recent alerts:\n  " + "\n  ".join(alert_previews[-3:]))

    return "\n".join(lines)


def _send_reply(text: str, reply_to_message_id: int = None):
    """Send a message, optionally as a reply to a specific message."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Split for 4096 limit
    MAX_LEN = 4096
    chunks = []
    if len(text) <= MAX_LEN:
        chunks = [text]
    else:
        remaining = text
        while remaining:
            if len(remaining) <= MAX_LEN:
                chunks.append(remaining)
                break
            split_at = remaining[:MAX_LEN].rfind("\n")
            if split_at < MAX_LEN // 2:
                split_at = MAX_LEN
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # Only reply_to on the first chunk
        if reply_to_message_id and i == 0:
            payload["reply_to_message_id"] = reply_to_message_id

        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"[BOT REPLY ERROR] {e}")


# ─── Reply Handler ─────────────────────────────────────────────────────────

def handle_reply(user_text: str, original_text: str) -> str:
    """Handle a reply to a bot alert — answered by the grounded tool-using agent."""
    from .llm.agent import run_agent

    original_clean = _strip_html(original_text)
    if len(original_clean) > TRUNCATION["prompt_context"]:
        original_clean = original_clean[:TRUNCATION["prompt_context"]] + "\n[...truncated]"

    question = (
        f"I received this alert from my monitoring system:\n---\n{original_clean}\n---\n\n"
        f"My reply / question: {user_text}"
    )
    res = run_agent(question, label="reply")
    answer = res.text if res.ok else f"Analysis unavailable: {res.error}"

    return (
        f"\U0001f4ac <b>REPLY</b>\n"
        + "\u2500" * 20 + "\n\n"
        f"{answer}\n\n"
        f"<code>#REPLY</code>"
    )


# ─── Free Text Handler ─────────────────────────────────────────────────────

def handle_freetext(text: str) -> str:
    """Handle a free-text question — answered by the grounded tool-using agent."""
    from .llm.agent import run_agent

    res = run_agent(text, label="freetext")
    answer = res.text if res.ok else f"Analysis unavailable: {res.error}"

    return (
        f"\U0001f4ac <b>ANSWER</b>\n"
        + "\u2500" * 20 + "\n\n"
        f"<i>Q: {text[:TRUNCATION['preview']]}{'...' if len(text) > TRUNCATION['preview'] else ''}</i>\n\n"
        f"{answer}\n\n"
        f"<code>#ANSWER</code>"
    )


# ─── Command Handlers (existing) ──────────────────────────────────────────

def _format_message_log() -> str:
    """Format recent message log for display."""
    logs = get_message_log(15)
    if not logs:
        return "No messages logged yet."

    lines = [
        "\U0001f4dc <b>MESSAGE LOG</b>",
        "\u2500" * 20,
        "",
    ]
    for entry in reversed(logs):
        ts = entry.get("timestamp", "")[:16]
        ok = "\u2705" if entry.get("success") else "\u274c"
        preview = entry.get("message", "")[:80].replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        lines.append(f"  {ok} {ts} {preview}")

    return "\n".join(lines)


def get_updates(offset: int = 0, timeout: int = 30) -> list:
    """Long-poll Telegram for new messages."""
    try:
        resp = requests.get(
            POLL_URL,
            params={"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'},
            timeout=timeout + 5,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception:
        return []


def handle_command(text: str) -> str | None:
    """Route a /command message to the right handler. Returns response text."""
    text = text.strip()

    if text.startswith("/ask "):
        return handle_ask(text[5:].strip())
    elif text.startswith("/price"):
        parts = text.split(maxsplit=1)
        ticker = parts[1].strip().upper() if len(parts) > 1 else POSITION["ticker"]
        return handle_price(ticker)
    elif text.startswith("/sim "):
        return handle_sim(text[5:].strip())
    elif text == "/spreads":
        return handle_spreads()
    elif text == "/pnl":
        return handle_pnl()
    elif text == "/journal":
        return get_journal_summary()
    elif text.startswith("/log "):
        parts = text[5:].strip().split(maxsplit=1)
        action = parts[0] if parts else "NOTE"
        details = parts[1] if len(parts) > 1 else ""
        log_trade(action, details)
        return None  # log_trade sends its own alert
    elif text == "/postmortem":
        return post_mortem()
    elif text == "/scan":
        from .watchlist import analyze_and_alert
        analyze_and_alert()
        return None  # sends its own alert
    elif text == "/committee":
        # Bare /committee — wait for the user's question. Don't fire any analysis.
        return (
            "\U0001f3db️ <b>Committee — what's your question?</b>\n"
            "Send it like:\n"
            "  <code>/committee will MU hit 760 by expiry?</code>\n"
            "  <code>/committee should I close half before NVDA earnings?</code>\n\n"
            "<i>Tip:</i> use <code>/catalysts</code> for the standard catalyst rundown "
            "without a question."
        )
    elif text.startswith("/committee "):
        # /committee <date>     → custom end date for the standard analysis
        # /committee <question> → committee answers a free-form question
        from datetime import date as _date
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from list_catalysts import deliver_committee_to_telegram
        arg = text[len("/committee"):].strip()
        end = None
        question = None
        try:
            end = _date.fromisoformat(arg)
        except ValueError:
            question = arg
        try:
            deliver_committee_to_telegram(end=end, question=question)
        except Exception as e:
            return f"⚠️ Committee call failed: {e}"
        return None
    elif text == "/catalysts":
        # The old "bare /committee" behaviour — catalyst list + standard verdict
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from list_catalysts import deliver_committee_to_telegram
        try:
            deliver_committee_to_telegram()
        except Exception as e:
            return f"⚠️ Catalysts call failed: {e}"
        return None
    elif text == "/history":
        return _format_message_log()
    elif text == "/help":
        return (
            "\U0001f4cb <b>COMMANDS</b>\n"
            + "\u2500" * 20 + "\n\n"
            "<b>Reply to any alert</b> to ask a follow-up question\n\n"
            "/ask &lt;question&gt; - Ask the Roundtable\n"
            "/committee &lt;question&gt; - Committee answers a specific question\n"
            "/catalysts - Catalyst list + standard verdict (to expiry)\n"
            "/price [ticker] - Quick price check\n"
            "/pnl - P&amp;L report with expiry table\n"
            "/sim &lt;scenario&gt; - Scenario simulator\n"
            "/spreads - Spread recommendations\n"
            "/journal - Trade journal\n"
            "/log &lt;action&gt; &lt;notes&gt; - Log trade\n"
            "/postmortem - Trade post-mortem\n"
            "/scan - Watchlist scanner\n"
            "/history - Recent message log\n\n"
            "Or just type any question!"
        )

    return None


def handle_ask(question: str) -> str:
    """Ask the 9-persona roundtable a question."""
    context = _get_full_context()

    system = (
        "You are running a roundtable of 9 expert personas for a short-term options trader.\n\n"
        "PERSONAS:\n"
        "- Rex (Bull): finds upside catalysts\n"
        "- Vera (Bear): identifies risks\n"
        "- Sigma (Quant): probability and numbers\n"
        "- Atlas (Macro): Fed, geopolitics\n"
        "- Chart (Tech): price levels\n"
        "- Flux (Market Regime): risk-on/off\n"
        "- Edge (Flow/Sentiment): options flow, narrative\n"
        "- Catalyst (Events): upcoming catalysts\n"
        "- Arbiter (Judge): weighs all views, final verdict\n\n"
        "Each persona gives a 1-line response using the live market data provided. "
        "Arbiter gives VERDICT. Under 200 words."
    )
    user = f"MARKET CONTEXT:\n{context}\n\nUSER QUESTION: {question}"
    resp = complete(user, tier="reasoning", system=system, max_tokens=1500, label="roundtable")
    analysis = resp.text if resp.ok else f"Roundtable unavailable: {resp.error}"

    return (
        f"\U0001f9e0 <b>ROUNDTABLE</b>\n"
        + "\u2500" * 20 + "\n\n"
        f"<i>Q: {question}</i>\n\n"
        f"{analysis}\n\n"
        f"<code>#ROUNDTABLE</code>"
    )


def handle_price(ticker: str) -> str:
    """Quick price check with context."""
    price = get_live_price(ticker)
    if price is None:
        return f"Could not fetch price for {ticker}"

    prev = get_prev_close(ticker)
    chg = ((price - prev) / prev * 100) if prev and prev > 0 else 0

    if chg > 0.5:
        emoji = "\U0001f7e2"
    elif chg < -0.5:
        emoji = "\U0001f534"
    else:
        emoji = "\U0001f7e1"

    lines = [
        f"{emoji} <b>{ticker}  ${price:.2f}  ({chg:+.2f}%)</b>",
    ]

    if ticker == POSITION["ticker"] and POSITION.get("contracts"):
        spread_val = estimate_spread_value(price)
        entry = POSITION["entry_price"]
        pnl = (spread_val - entry) * POSITION["contracts"] * 100
        pnl_pct = ((spread_val - entry) / entry) * 100
        pnl_icon = "\U0001f4b9" if pnl > 0 else "\U0001f4c9"
        lines += [
            f"  Spread: ${spread_val:.2f}",
            f"  {pnl_icon} P&L: ${pnl:+,.0f} ({pnl_pct:+.1f}%)",
        ]

    lines.append("")
    lines.append("<code>#PRICE_CMD</code>")
    return "\n".join(lines)


def handle_sim(scenario: str) -> str:
    """Simulate a scenario (e.g., 'MU drops to 385')."""
    if not POSITION.get("contracts"):
        return "No active position to simulate."

    match = re.search(r'\$?(\d{3,4}(?:\.\d{1,2})?)', scenario)
    target_price = float(match.group(1)) if match else None

    mu_price = get_live_price(POSITION["ticker"]) or 0
    spread_val = estimate_spread_value(mu_price) if mu_price else 0
    entry = POSITION["entry_price"]
    contracts = POSITION["contracts"]
    current_pnl = (spread_val - entry) * contracts * 100

    sim_context = f"Current: MU ${mu_price:.2f}, Spread ${spread_val:.2f}, P&L ${current_pnl:+,.0f}"

    if target_price:
        sim_spread = estimate_spread_value(target_price)
        sim_pnl = (sim_spread - entry) * contracts * 100
        sim_context += (
            f"\nSimulated at ${target_price:.2f}: Spread ${sim_spread:.2f}, P&L ${sim_pnl:+,.0f}"
        )

    system = (
        "You are a scenario analyst for a short-term options trader. "
        "In 3-4 sentences: what is the probability of the scenario, what would the "
        "P&L impact be, and what should the trader do? Be specific with numbers."
    )
    user = f"POSITION: {position_summary()}.\n{sim_context}\n\nSCENARIO: {scenario}"
    resp = complete(user, tier="reasoning", system=system, max_tokens=1024,
                    effort="medium", label="sim")
    analysis = resp.text if resp.ok else f"Simulation unavailable: {resp.error}"

    lines = [
        "\U0001f3b2 <b>SCENARIO SIM</b>",
        "\u2500" * 20,
        "",
        f"<i>{scenario}</i>",
        "",
    ]

    if target_price:
        sim_spread = estimate_spread_value(target_price)
        sim_pnl = (sim_spread - entry) * contracts * 100
        pnl_icon = "\U0001f4b9" if sim_pnl > 0 else "\U0001f4c9"
        lines += [
            f"If MU = ${target_price:.2f}:",
            f"  Spread: ${sim_spread:.2f}",
            f"  {pnl_icon} P&L: ${sim_pnl:+,.0f}",
            f"  vs current: ${sim_pnl - current_pnl:+,.0f}",
            "",
        ]

    lines += ["\U0001f9e0 " + analysis, "", "<code>#SIM</code>"]

    return "\n".join(lines)


def handle_pnl() -> str:
    """Show P&L report: current value + expiry P&L table."""
    mu_price = get_live_price(POSITION["ticker"])
    if not mu_price:
        return "Cannot fetch MU price."

    entry = POSITION["entry_price"]
    contracts = POSITION["contracts"]
    long_strike = POSITION["long_strike"]
    short_strike = POSITION["short_strike"]
    width = short_strike - long_strike
    breakeven = long_strike + entry

    # Current spread value (with time value via BS)
    current_spread = estimate_spread_value(mu_price)
    current_pnl = (current_spread - entry) * contracts * 100
    current_pnl_pct = ((current_spread - entry) / entry) * 100

    # Expiry spread value (intrinsic only)
    if mu_price <= long_strike:
        expiry_spread = 0.0
    elif mu_price >= short_strike:
        expiry_spread = float(width)
    else:
        expiry_spread = mu_price - long_strike
    expiry_pnl = (expiry_spread - entry) * contracts * 100
    expiry_pnl_pct = ((expiry_spread - entry) / entry) * 100

    max_gain = (width - entry) * contracts * 100
    max_loss = -entry * contracts * 100

    status = "\U0001f4b9" if current_pnl > 0 else "\U0001f4c9"

    # P&L table at expiry — span centered on spot, covering both strikes.
    # Pick step ~0.5% of spot (rounded to a clean increment), span ±10% of spot.
    span = max(short_strike - long_strike + 20, int(mu_price * 0.10))
    step = max(1, int(round(mu_price * 0.005)))
    lo = int(round(min(mu_price, long_strike) - span / 2))
    hi = int(round(max(mu_price, short_strike) + span / 2))
    prices = list(range(lo, hi + 1, step))
    for special in [round(mu_price), round(breakeven), long_strike, short_strike]:
        if special not in prices:
            prices.append(special)
    prices.sort()

    table_lines = []
    for p in prices:
        if p <= long_strike:
            sv = 0.0
        elif p >= short_strike:
            sv = float(width)
        else:
            sv = p - long_strike
        pnl = (sv - entry) * contracts * 100

        marker = ""
        if p == round(mu_price):
            marker = " \u25c0 NOW"
        elif p == round(breakeven):
            marker = " \u25c0 BE"

        icon = "\U0001f7e2" if pnl > 0 else ("\u26aa" if pnl == 0 else "\U0001f534")
        table_lines.append(f"{icon} ${p:>3}  ${sv:>5.2f}  ${pnl:>+10,.0f}{marker}")

    from datetime import date
    dte = (date.fromisoformat(POSITION["expiry"]) - date.today()).days

    lines = [
        f"\U0001f4ca <b>P&L REPORT</b>",
        "\u2500" * 25,
        "",
        f"<b>Position:</b> {contracts}x MU {long_strike}/{short_strike} bull call spread",
        f"<b>Entry:</b> ${entry:.3f}  |  <b>Expiry:</b> {POSITION['expiry']} ({dte} DTE)",
        f"<b>Breakeven:</b> ${breakeven:.2f}",
        "",
        f"\U0001f4b0 <b>CURRENT (with time value)</b>",
        f"  MU: <b>${mu_price:.2f}</b>",
        f"  Spread: ${current_spread:.2f}",
        f"  {status} P&L: <b>${current_pnl:+,.0f}</b> ({current_pnl_pct:+.1f}%)",
        "",
        f"\U0001f3af <b>AT EXPIRY (intrinsic only)</b>",
        f"  If MU stays ${mu_price:.2f}:",
        f"  Spread: ${expiry_spread:.2f}",
        f"  P&L: <b>${expiry_pnl:+,.0f}</b> ({expiry_pnl_pct:+.1f}%)",
        "",
        f"\U0001f4b5 Max Gain: <b>${max_gain:+,.0f}</b> (MU \u2265 ${short_strike})",
        f"\U0001f4a8 Max Loss: <b>${max_loss:+,.0f}</b> (MU \u2264 ${long_strike})",
        "",
        "\u2500" * 25,
        "<b>EXPIRY P&L TABLE</b>",
        "<pre>",
        "MU     Spread      P&L",
        "\u2500" * 26,
    ]
    lines.extend(table_lines)
    lines.append("</pre>")
    lines.append("")
    lines.append("<code>#PNL</code>")

    return "\n".join(lines)


def handle_spreads() -> str:
    """Get spread recommendations."""
    try:
        from .spread_recommender import recommend_spreads, _get_catalysts_before_expiry, _get_position_size_pct
        mu_price = get_live_price(POSITION["ticker"]) or 0
        if not mu_price:
            return "Cannot fetch MU price."

        spreads = recommend_spreads("MU", num_expiries=3)
        if not spreads:
            return "No viable spreads found."

        top = spreads[:5]
        lines = [
            "\U0001f3af <b>TOP SPREADS</b>",
            "\u2500" * 20,
            f"\nMU: <b>${mu_price:.2f}</b>\n",
        ]

        medals = ["\U0001f947", "\U0001f948", "\U0001f949", "4\ufe0f\u20e3", "5\ufe0f\u20e3"]
        for i, s in enumerate(top):
            size_pct = _get_position_size_pct(s["kelly_pct"])
            lines.append(
                f"{medals[i]} <b>${s['long_strike']:.0f}/{s['short_strike']:.0f}C</b> "
                f"exp {s['expiry']} ({s['dte']}d)\n"
                f"   ${s['net_debit']:.2f} debit | {s['risk_reward']:.1f}x R:R | "
                f"P(max) {s['prob_max']:.0f}% | Size {size_pct:.0f}%"
            )
            lines.append("")

        lines.append("<code>#SPREADS</code>")
        return "\n".join(lines)
    except Exception as e:
        return f"Spread recommendation error: {e}"


# ─── Main Polling Loop ─────────────────────────────────────────────────────

def _get_bot_id() -> int | None:
    """Get the bot's own user ID to identify its messages."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("id")
    except Exception:
        return None


def _remove_persistent_keyboard():
    """One-shot helper: clear the reply keyboard if a previous /menu installed it.

    Telegram's reply keyboard sticks around until explicitly removed. We send a
    near-invisible message with ReplyKeyboardRemove on bot startup so any prior
    keyboard is cleared without leaving a visible artifact.
    """
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "​",  # zero-width space — no visible content
        "reply_markup": json.dumps({"remove_keyboard": True}),
    }
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload, timeout=10,
        )
    except Exception:
        pass


_BOT_COMMANDS = [
    {"command": "ask",        "description": "Ask the 9-persona Roundtable"},
    {"command": "committee",  "description": "Committee answers a question (add it after the command)"},
    {"command": "catalysts",  "description": "Catalyst list + verdict (no question needed)"},
    {"command": "price",      "description": "Quick price check (default: position ticker)"},
    {"command": "pnl",        "description": "P&L report with expiry table"},
    {"command": "sim",        "description": "Scenario simulator"},
    {"command": "spreads",    "description": "Spread recommendations"},
    {"command": "journal",    "description": "Show trade journal"},
    {"command": "log",        "description": "Log a trade event: /log <action> <notes>"},
    {"command": "postmortem", "description": "Trade post-mortem"},
    {"command": "scan",       "description": "Watchlist scanner"},
    {"command": "history",    "description": "Recent message log"},
    {"command": "help",       "description": "Show all commands"},
]


def _setup_bot_commands():
    """Register the / autocomplete menu with Telegram.

    Idempotent — safe to call on every bot startup. Telegram clients pick up
    the new list within a few seconds of being sent.
    """
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setMyCommands",
            json={"commands": _BOT_COMMANDS}, timeout=10,
        )
        print("[BOT] Registered command menu with Telegram")
    except Exception as e:
        print(f"[BOT COMMANDS ERROR] {e}")


def run_polling():
    """Main polling loop. Run this as a background process.

    Handles three types of input:
    1. Reply to a bot message → contextual follow-up Q&A
    2. /command → specific action handler
    3. Free text → general question answered by DeepSeek
    """
    print("[BOT] Interactive bot polling started...")
    _setup_bot_commands()
    _remove_persistent_keyboard()
    offset = _load_offset()
    bot_id = _get_bot_id()
    print(f"[BOT] Bot ID: {bot_id}")

    while True:
        try:
            updates = get_updates(offset)
        except Exception as e:
            print(f"[BOT POLL ERROR] {e}")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            _save_offset(offset)

            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")
            message_id = msg.get("message_id")

            # Only respond to our chat
            if chat_id != TELEGRAM_CHAT_ID or not text:
                continue

            # Check if this is a reply to a bot message
            reply_to = msg.get("reply_to_message")
            is_reply_to_bot = False
            original_text = ""

            if reply_to:
                reply_from = reply_to.get("from", {})
                # Check if the replied-to message is from our bot
                if bot_id and reply_from.get("id") == bot_id:
                    is_reply_to_bot = True
                    original_text = reply_to.get("text", "")
                elif reply_from.get("is_bot", False):
                    # Fallback: any bot message in our chat is likely ours
                    is_reply_to_bot = True
                    original_text = reply_to.get("text", "")

            try:
                if is_reply_to_bot and original_text:
                    # Case 1: Reply to a bot alert → contextual Q&A
                    print(f"[BOT] Reply to alert: {text[:50]}")
                    response = handle_reply(text, original_text)
                    _send_reply(response, reply_to_message_id=message_id)
                    _log_interaction(text, response, "reply")

                elif text.startswith("/"):
                    # Case 2: Command
                    print(f"[BOT] Command: {text[:50]}")
                    response = handle_command(text)
                    if response:
                        _send_reply(response, reply_to_message_id=message_id)
                    _log_interaction(text, response or "", "command")

                else:
                    # Case 3: Free text question
                    print(f"[BOT] Question: {text[:50]}")
                    response = handle_freetext(text)
                    _send_reply(response, reply_to_message_id=message_id)
                    _log_interaction(text, response, "freetext")

            except Exception as e:
                print(f"[BOT HANDLER ERROR] {e}")
                _send_reply(
                    f"\u274c Error processing your message: {str(e)[:200]}",
                    reply_to_message_id=message_id,
                )

        time.sleep(1)


if __name__ == "__main__":
    run_polling()
