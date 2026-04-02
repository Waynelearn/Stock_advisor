"""Weekend War Room - Saturday morning full-week roundtable recap."""

import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import DEEPSEEK_API_KEY, POSITION, TZ_ET, TZ_SGT, CATALYSTS, PEERS, FUTURES
from .bot import send_alert
from .daily_briefing import get_daily_data, estimate_spread_value
from .news_scanner import get_recent_headlines


def build_war_room() -> str:
    """Build the Saturday morning weekly recap + next week preview."""
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    now_et = datetime.now(ZoneInfo(TZ_ET))

    # Gather weekly data
    mu_data = get_daily_data(POSITION["ticker"])
    vix_data = get_daily_data("^VIX")

    if not mu_data or not mu_data["price"]:
        return "\U0001f3db <b>WEEKEND WAR ROOM</b>\nFailed to fetch market data."

    mu_price = mu_data["price"]
    prev = mu_data["prev_close"] or mu_price

    # Spread + P&L
    has_position = bool(POSITION.get("contracts"))
    if has_position:
        spread_val = estimate_spread_value(mu_price)
        entry = POSITION["entry_price"]
        contracts = POSITION["contracts"]
        pnl = (spread_val - entry) * contracts * 100
        pnl_pct = ((spread_val - entry) / entry) * 100

        # DTE
        expiry = datetime.strptime(POSITION["expiry"], "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
        dte = (expiry.date() - now_et.date()).days
        trading_days = sum(1 for i in range(dte) if (now_et.date() + timedelta(days=i + 1)).weekday() < 5)
    else:
        spread_val = 0
        pnl = 0
        trading_days = 0

    # Peers
    peers_str = ""
    for peer in PEERS:
        if peer == "SOXX":
            continue
        pdata = get_daily_data(peer)
        if pdata and pdata["price"] and pdata["prev_close"]:
            ppct = ((pdata["price"] - pdata["prev_close"]) / pdata["prev_close"]) * 100
            peers_str += f"  {peer}: ${pdata['price']:.2f} ({ppct:+.1f}%)\n"

    # Next week catalysts
    next_week = []
    for month, day, hour, minute, desc in CATALYSTS:
        try:
            evt = datetime(now_et.year, month, day, hour, minute, tzinfo=ZoneInfo(TZ_ET))
            delta = evt - now_et
            if timedelta(0) < delta <= timedelta(days=7):
                sgt = evt.astimezone(ZoneInfo(TZ_SGT))
                next_week.append(f"  {sgt.strftime('%a %b %d %I:%M%p SGT')} - {desc}")
        except Exception:
            continue

    # Headlines from the week
    headlines = get_recent_headlines(hours=120)  # ~5 days
    headlines_str = "\n".join(f"- {h['title']}" for h in headlines[-20:])

    catalysts_str = "\n".join(next_week) if next_week else "None"

    # DeepSeek war room analysis
    analysis = _deepseek_war_room(
        mu_price, spread_val, pnl, trading_days,
        vix_data["price"] if vix_data and vix_data["price"] else 0,
        peers_str, catalysts_str, headlines_str
    )

    # Build message
    pnl_icon = "\U0001f4b9" if pnl > 0 else "\U0001f4c9"
    lines = [
        "\U0001f3db <b>WEEKEND WAR ROOM</b>",
        f"<i>{now_sgt.strftime('%a, %b %d %Y')}</i>",
        "\u2500" * 20,
        "",
    ]
    if has_position:
        lines += [
            f"\U0001f4bc <b>POSITION STATUS</b>",
            f"  MU: ${mu_price:.2f}",
            f"  Spread: ${spread_val:.2f} (entry ${entry:.2f})",
            f"  {pnl_icon} P&L: <b>${pnl:+,.0f}</b> ({pnl_pct:+.1f}%)",
            f"  {trading_days} trading days to expiry",
        ]
    else:
        lines += [
            f"\U0001f4bc <b>POSITION STATUS</b>",
            f"  MU: ${mu_price:.2f}",
            f"  No active spread position",
        ]

    if vix_data and vix_data["price"]:
        lines.append(f"  VIX: {vix_data['price']:.1f}")

    if next_week:
        lines += [
            "",
            "\u2500" * 20,
            "\U0001f4c5 <b>NEXT WEEK</b>",
        ] + next_week

    lines += [
        "",
        "\u2500" * 20,
        "\U0001f9e0 <b>WAR ROOM ANALYSIS</b>",
        "",
        analysis,
        "",
        "<code>#WARROOM</code>",
    ]

    return "\n".join(lines)


def _deepseek_war_room(mu_price, spread_val, pnl, trading_days, vix,
                        peers_str, catalysts_str, headlines_str) -> str:
    prompt = (
        f"You are running a WEEKEND WAR ROOM with 9 expert personas for a short-term options trader.\n\n"
        f"PERSONAS: Rex(Bull), Vera(Bear), Sigma(Quant), Atlas(Macro), Chart(Tech), "
        f"Flux(Market Regime), Edge(Flow/Sentiment), Catalyst(Events), Arbiter(Judge)\n\n"
        f"POSITION: 500x MU 380/400 bull call spread, entry $11.897, expiry March 20, 2026.\n"
        f"Current: MU ${mu_price:.2f}, Spread ${spread_val:.2f}, P&L ${pnl:+,.0f}, "
        f"{trading_days} trading days left, VIX {vix:.1f}\n\n"
        f"PEERS:\n{peers_str}\n"
        f"NEXT WEEK CATALYSTS:\n{catalysts_str}\n\n"
        f"WEEK'S HEADLINES:\n{headlines_str}\n\n"
        f"FORMAT (under 250 words):\n"
        f"1. WEEK IN REVIEW: 2 sentences on what happened\n"
        f"2. Each persona: one line on their key concern/opportunity for next week\n"
        f"3. Arbiter: VERDICT for the week ahead (HOLD/SELL/WATCH + specific trigger levels)\n"
        f"4. GAME PLAN: 2-3 specific if/then scenarios for next week\n"
        f"Be concrete with numbers and levels."
    )
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 400},
            timeout=25,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"War room analysis unavailable: {e}"


def send_war_room():
    """Build and send the weekend war room."""
    msg = build_war_room()
    send_alert(msg)
