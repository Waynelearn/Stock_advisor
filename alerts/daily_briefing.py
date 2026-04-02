"""Daily evening briefing - sends summary before US market open."""

import json
import os
import yfinance as yf
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, CATALYSTS, TZ_ET, TZ_SGT, FUTURES, PEERS, DEEPSEEK_API_KEY
from .bot import send_daily_briefing
from .price_monitor import estimate_spread_value
from .news_scanner import get_recent_headlines

EARNINGS_STATE_FILE = os.path.join(os.path.dirname(__file__), ".earnings_state.json")


def _gather_market_snapshot() -> dict:
    """Gather all market data into a dict for DeepSeek analysis."""
    snapshot = {}

    mu_data = get_daily_data(POSITION["ticker"])
    if mu_data and mu_data["price"]:
        prev = mu_data["prev_close"] or mu_data["price"]
        snapshot["mu"] = {
            "price": mu_data["price"],
            "change_pct": ((mu_data["price"] - prev) / prev) * 100 if prev else 0,
            "high": mu_data.get("day_high"),
            "low": mu_data.get("day_low"),
        }

    vix_data = get_daily_data("^VIX")
    if vix_data and vix_data["price"]:
        snapshot["vix"] = vix_data["price"]

    snapshot["peers"] = {}
    for peer in PEERS:
        if peer == "SOXX":
            continue
        pdata = get_daily_data(peer)
        if pdata and pdata["price"] and pdata["prev_close"]:
            snapshot["peers"][peer] = {
                "price": pdata["price"],
                "change_pct": ((pdata["price"] - pdata["prev_close"]) / pdata["prev_close"]) * 100,
            }

    snapshot["futures"] = {}
    for fticker, fname in FUTURES.items():
        fdata = get_daily_data(fticker)
        if fdata and fdata["price"] and fdata["prev_close"]:
            short = {"S&P 500 Futures": "S&P", "Nasdaq Futures": "NQ", "Dow Futures": "DOW"}
            snapshot["futures"][short.get(fname, fname)] = {
                "price": fdata["price"],
                "change_pct": ((fdata["price"] - fdata["prev_close"]) / fdata["prev_close"]) * 100,
            }

    # Spread
    if "mu" in snapshot and POSITION.get("contracts"):
        spread_val = estimate_spread_value(snapshot["mu"]["price"])
        entry = POSITION["entry_price"]
        contracts = POSITION["contracts"]
        pnl = (spread_val - entry) * contracts * 100
        now_et = datetime.now(ZoneInfo(TZ_ET))
        expiry = datetime.strptime(POSITION["expiry"], "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
        dte = (expiry.date() - now_et.date()).days
        trading_days = sum(1 for i in range(dte) if (now_et.date() + timedelta(days=i + 1)).weekday() < 5)
        snapshot["position"] = {
            "spread_value": spread_val,
            "entry": entry,
            "pnl": pnl,
            "trading_days_left": trading_days,
        }

    # Recent headlines
    headlines = get_recent_headlines(hours=24)
    snapshot["headlines"] = [h["title"] for h in headlines[-15:]]

    # Upcoming catalysts
    snapshot["catalysts"] = get_upcoming_catalysts(days_ahead=2)

    return snapshot


def deepseek_daily_analysis(mode: str = "recap") -> str:
    """Ask DeepSeek for a contextual analysis of today's session.

    mode: 'recap' (morning 7AM) or 'preview' (evening 8PM)
    Returns: 3-4 sentence AI analysis.
    """
    snapshot = _gather_market_snapshot()

    mu = snapshot.get("mu", {})
    vix = snapshot.get("vix", 0)
    peers = snapshot.get("peers", {})
    futures = snapshot.get("futures", {})
    pos = snapshot.get("position", {})
    headlines = snapshot.get("headlines", [])
    catalysts = snapshot.get("catalysts", [])

    peers_str = ", ".join(f"{k} {v['change_pct']:+.1f}%" for k, v in peers.items())
    futures_str = ", ".join(f"{k} {v['change_pct']:+.1f}%" for k, v in futures.items())
    headlines_str = "\n".join(f"- {h}" for h in headlines) if headlines else "None"
    catalysts_str = "\n".join(catalysts) if catalysts else "None"

    if mode == "recap":
        context = "The US trading session just closed."
        task = "RECAP last night's session."
    else:
        context = "The US market is about to open tonight."
        task = "PREVIEW tonight's session."

    position_block = ""
    if POSITION.get("contracts"):
        position_block = (
            f"POSITION: {POSITION['contracts']}x MU {POSITION['long_strike']}/{POSITION['short_strike']} bull call spread, "
            f"entry ${POSITION['entry_price']}, expiry {POSITION['expiry']}. "
            f"Breakeven ${POSITION['breakeven']}. Max profit if MU > ${POSITION['short_strike']} at expiry.\n\n"
        )
        spread_line = (
            f"Spread value: ${pos.get('spread_value', 0):.2f}, P&L: ${pos.get('pnl', 0):+,.0f}, "
            f"{pos.get('trading_days_left', 0)} trading days left\n"
        )
    else:
        spread_line = "No active position.\n"

    prompt = (
        f"You are running a roundtable of 9 expert personas analyzing Micron (MU) for a short-term options trader.\n\n"
        f"PERSONAS:\n"
        f"- Rex (Bull): finds upside catalysts\n"
        f"- Vera (Bear): identifies risks and downside\n"
        f"- Sigma (Quant): probability and numbers\n"
        f"- Atlas (Macro): Fed, geopolitics, macro flows\n"
        f"- Chart (Technician): price levels, support/resistance\n"
        f"- Flux (Market Regime): risk-on/off, sector rotation\n"
        f"- Edge (Flow/Sentiment): options flow, positioning, narrative\n"
        f"- Catalyst (Events): upcoming catalysts, timing\n"
        f"- Arbiter (Judge): weighs all views, prevents groupthink, gives final verdict\n\n"
        f"{position_block}"
        f"CURRENT DATA:\n"
        f"MU: ${mu.get('price', 0):.2f} ({mu.get('change_pct', 0):+.2f}%), "
        f"Range: ${mu.get('low', 0):.2f}-${mu.get('high', 0):.2f}\n"
        f"VIX: {vix:.1f}{' (data unavailable)' if not vix else ''}\n"
        f"Peers: {peers_str}\n"
        f"Futures: {futures_str}\n"
        f"{spread_line}\n"
        f"RECENT NEWS:\n{headlines_str}\n\n"
        f"UPCOMING CATALYSTS:\n{catalysts_str}\n\n"
        f"{context} {task}\n\n"
        f"FORMAT (keep it tight for Telegram):\n"
        f"Line 1: One-line from each persona (name: key point). Skip personas with nothing to add.\n"
        f"Line 2: Arbiter's VERDICT — the actionable bottom line (HOLD/SELL/WATCH + reason).\n"
        f"Line 3: Key level or event to watch.\n"
        f"Keep total under 200 words. No fluff."
    )

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 250,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Analysis unavailable: {e}"


def get_daily_data(ticker: str) -> dict | None:
    """Get latest daily data for a ticker."""
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        return {
            "price": info.get("lastPrice") or info.get("last_price"),
            "prev_close": info.get("previousClose") or info.get("previous_close"),
            "day_high": info.get("dayHigh") or info.get("day_high"),
            "day_low": info.get("dayLow") or info.get("day_low"),
        }
    except Exception as e:
        print(f"[BRIEFING ERROR] {ticker}: {e}")
        return None


def get_upcoming_catalysts(days_ahead: int = 2) -> list[str]:
    """Get catalysts happening in the next N days."""
    now_et = datetime.now(ZoneInfo(TZ_ET))
    year = now_et.year
    upcoming = []

    for month, day, hour, minute, description in CATALYSTS:
        event_time = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(TZ_ET))
        delta = event_time - now_et
        if timedelta(0) < delta <= timedelta(days=days_ahead):
            sgt_time = event_time.astimezone(ZoneInfo(TZ_SGT))
            time_str = sgt_time.strftime("%b %d %I:%M %p SGT")
            upcoming.append(f"  {time_str} - {description}")

    return upcoming


def detect_earnings_result() -> dict | None:
    """Detect if MU earnings have fired by checking post-earnings price action.

    Called after March 18 AMC. Checks overnight move and uses DeepSeek to
    analyze the result from recent news headlines.

    Returns: {"result": "BEAT"|"MISS"|"INLINE", "move_pct": float, "analysis": str} or None
    """
    # Only relevant after earnings date
    now_et = datetime.now(ZoneInfo(TZ_ET))
    earnings_date = datetime(2026, 3, 18, 16, 15, tzinfo=ZoneInfo(TZ_ET))
    if now_et < earnings_date:
        return None

    # Check if we already cached the result
    if os.path.exists(EARNINGS_STATE_FILE):
        with open(EARNINGS_STATE_FILE) as f:
            state = json.load(f)
            if state.get("result"):
                return state

    # Get price data to detect the move
    try:
        t = yf.Ticker(POSITION["ticker"])
        hist = t.history(period="5d")
        if len(hist) < 2:
            return None

        # Compare last close to previous close (post-earnings vs pre-earnings)
        prev_close = hist["Close"].iloc[-2]
        last_close = hist["Close"].iloc[-1]
        move_pct = ((last_close - prev_close) / prev_close) * 100
    except Exception:
        return None

    # Quick heuristic from price move
    if move_pct > 3:
        heuristic = "BEAT"
    elif move_pct < -3:
        heuristic = "MISS"
    else:
        heuristic = "INLINE"

    # Ask DeepSeek for context from the move
    analysis = _deepseek_earnings_analysis(move_pct, last_close)

    result = {
        "result": heuristic,
        "move_pct": round(move_pct, 2),
        "last_close": round(last_close, 2),
        "analysis": analysis,
    }

    # If DeepSeek gave a clear override, use it
    if "BEAT" in analysis.upper() and "MISS" not in analysis.upper():
        result["result"] = "BEAT"
    elif "MISS" in analysis.upper() and "BEAT" not in analysis.upper():
        result["result"] = "MISS"

    # Cache it
    with open(EARNINGS_STATE_FILE, "w") as f:
        json.dump(result, f, indent=2)

    return result


def _deepseek_earnings_analysis(move_pct: float, price: float) -> str:
    """Ask DeepSeek to analyze the MU earnings result."""
    prompt = (
        f"Micron (MU) just reported FQ2 2026 earnings after market close on March 18, 2026. "
        f"The stock moved {move_pct:+.2f}% to ${price:.2f} in after-hours/next session. "
        f"Consensus was: $18.7B revenue, 68% gross margin, $8.42 EPS. "
        f"Based on the price reaction, did Micron BEAT, MISS, or come INLINE with expectations? "
        f"Give a 2-sentence assessment of the result and what it means for the stock near-term. "
        f"Start your response with BEAT, MISS, or INLINE."
    )
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 150,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        if move_pct > 3:
            return "BEAT. Strong positive reaction suggests earnings exceeded expectations."
        elif move_pct < -3:
            return "MISS. Negative reaction suggests earnings or guidance disappointed."
        return "INLINE. Muted reaction suggests results met expectations."


def get_framework_action(price: float, days_to_expiry: int) -> str:
    """Get the decision framework recommendation. Earnings-aware after March 18."""

    # Check if earnings have fired
    earnings = detect_earnings_result()

    if earnings and earnings.get("result"):
        result = earnings["result"]
        move = earnings.get("move_pct", 0)

        if result == "BEAT":
            if price > 400:
                return f"\U0001f389 Earnings BEAT ({move:+.1f}%) \u2014 HOLD to expiry, you won"
            elif price > 392:
                return f"\U0001f389 Earnings BEAT ({move:+.1f}%) \u2014 HOLD, drift to $400 likely"
            elif price > 385:
                return f"\u26a0\ufe0f Earnings BEAT ({move:+.1f}%) but not enough \u2014 consider selling"
            else:
                return f"\u26a0\ufe0f Earnings BEAT ({move:+.1f}%) but MU still below $385 \u2014 SELL, won't reach $400 by expiry"
        elif result == "MISS":
            return f"\U0001f534 Earnings MISS ({move:+.1f}%) \u2014 SELL EVERYTHING at open. No second catalyst."
        else:  # INLINE
            if price > 395:
                return f"\U0001f7e1 Earnings INLINE ({move:+.1f}%) \u2014 HOLD, close to $400"
            elif price > 385:
                return f"\U0001f7e1 Earnings INLINE ({move:+.1f}%) \u2014 risky hold, may not reach $400"
            else:
                return f"\U0001f7e1 Earnings INLINE ({move:+.1f}%) \u2014 SELL, not enough momentum"

    # Pre-earnings logic (unchanged)
    if days_to_expiry > 3:
        return "HOLD - earnings catalyst hasn't fired yet"
    elif days_to_expiry == 3:
        if price > 400:
            return "HOLD - at max profit"
        elif price > 390:
            return "HOLD - earnings beat gets you there"
        elif price > 385:
            return "DECISION POINT - do you believe in the beat?"
        elif price > 370:
            return "SELL HALF - even a beat barely reaches $400"
        else:
            return "SELL ALL - insufficient recovery potential"
    elif days_to_expiry == 2:
        return "DO NOT SELL - FOMC + earnings day, wait for results"
    elif days_to_expiry == 1:
        if price > 400:
            return "HOLD to expiry - you won"
        elif price > 392:
            return "HOLD - 1 day drift possible"
        else:
            return "SELL - catalyst fired, insufficient recovery"
    else:
        return "EXPIRY DAY - settles at intrinsic value"


def build_briefing() -> str:
    """Build the daily evening briefing message."""
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    now_et = datetime.now(ZoneInfo(TZ_ET))

    # Get data
    mu_data = get_daily_data(POSITION["ticker"])
    vix_data = get_daily_data("^VIX")
    soxx_data = get_daily_data("SOXX")

    if not mu_data or mu_data["price"] is None:
        return "<b>DAILY BRIEFING</b>\nFailed to fetch MU data."

    mu_price = mu_data["price"]
    prev_close = mu_data["prev_close"] or mu_price
    change_pct = ((mu_price - prev_close) / prev_close) * 100 if prev_close else 0

    # Spread calculations (only if position active)
    has_position = POSITION.get("contracts")
    if has_position:
        spread_val = estimate_spread_value(mu_price)
        entry = POSITION["entry_price"]
        contracts = POSITION["contracts"]
        pnl = (spread_val - entry) * contracts * 100
        pnl_pct = ((spread_val - entry) / entry) * 100

        # Days to expiry
        expiry = datetime.strptime(POSITION["expiry"], "%Y-%m-%d")
        expiry = expiry.replace(tzinfo=ZoneInfo(TZ_ET))
        dte = (expiry.date() - now_et.date()).days
        trading_days = sum(1 for i in range(dte) if (now_et.date() + timedelta(days=i+1)).weekday() < 5)

        # Framework action
        action = get_framework_action(mu_price, trading_days)

    # Upcoming catalysts
    upcoming = get_upcoming_catalysts(days_ahead=2)

    # Helpers
    def chg_arrow(pct):
        if pct > 0.5:
            return "\U0001f7e2"  # 🟢
        elif pct < -0.5:
            return "\U0001f534"  # 🔴
        return "\U0001f7e1"  # 🟡

    # Build message
    mu_arrow = chg_arrow(change_pct)
    lines = [
        f"\U0001f4cb <b>DAILY BRIEFING</b>",
        f"<i>{now_sgt.strftime('%a, %b %d  %I:%M %p SGT')}</i>",
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        "",
        f"{mu_arrow} <b>MU  ${mu_price:.2f}  ({change_pct:+.2f}%)</b>",
    ]

    if mu_data.get("day_high") and mu_data.get("day_low"):
        lines.append(f"    L ${mu_data['day_low']:.2f}  \u2022  H ${mu_data['day_high']:.2f}")

    if vix_data and vix_data["price"]:
        vix_icon = "\U0001f6a8" if vix_data["price"] >= 25 else "\U0001f4ca"
        lines.append(f"\n{vix_icon} <b>VIX  {vix_data['price']:.2f}</b>")

    if soxx_data and soxx_data["price"]:
        soxx_pct = 0
        if soxx_data["prev_close"]:
            soxx_pct = ((soxx_data["price"] - soxx_data["prev_close"]) / soxx_data["prev_close"]) * 100
        soxx_arrow = chg_arrow(soxx_pct)
        lines.append(f"{soxx_arrow} SOXX  ${soxx_data['price']:.2f}  ({soxx_pct:+.2f}%)")

    # Futures
    lines += ["", "\U0001f310 <b>FUTURES</b>"]
    for fticker, fname in FUTURES.items():
        fdata = get_daily_data(fticker)
        if fdata and fdata["price"]:
            fpct = 0
            if fdata["prev_close"]:
                fpct = ((fdata["price"] - fdata["prev_close"]) / fdata["prev_close"]) * 100
            f_arrow = chg_arrow(fpct)
            # Shorten names
            short = {"S&P 500 Futures": "S&P", "Nasdaq Futures": "NQ", "Dow Futures": "DOW"}
            lines.append(f"  {f_arrow} {short.get(fname, fname)}  {fdata['price']:,.0f}  ({fpct:+.2f}%)")

    # Peers
    lines += ["", "\U0001f4c8 <b>PEERS</b>"]
    for peer in PEERS:
        if peer == "SOXX":
            continue
        pdata = get_daily_data(peer)
        if pdata and pdata["price"]:
            ppct = 0
            if pdata["prev_close"]:
                ppct = ((pdata["price"] - pdata["prev_close"]) / pdata["prev_close"]) * 100
            p_arrow = chg_arrow(ppct)
            lines.append(f"  {p_arrow} {peer}  ${pdata['price']:.2f}  ({ppct:+.2f}%)")

    # Position (only if active)
    if has_position:
        pnl_icon = "\U0001f4b9" if pnl > 0 else "\U0001f4c9"
        lines += [
            "",
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            f"\U0001f4bc <b>YOUR POSITION</b>",
            f"  {POSITION['long_strike']}/{POSITION['short_strike']} spread \u00d7{contracts}",
            f"  {trading_days} trading days left",
            f"  Spread: ${spread_val:.2f}  (entry ${entry:.2f})",
            f"  {pnl_icon} P&L: <b>${pnl:+,.0f}</b>  ({pnl_pct:+.1f}%)",
            "",
            f"\U0001f3af <b>ACTION:</b> {action}",
        ]

    if upcoming:
        lines += [
            "",
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "\U0001f4c5 <b>UPCOMING</b>",
        ]
        for u in upcoming:
            lines.append(f"  \U0001f4cc{u.strip()}")

    # AI Roundtable analysis
    analysis = deepseek_daily_analysis(mode="preview")
    if analysis:
        lines += [
            "",
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "\U0001f9e0 <b>AI ROUNDTABLE</b>",
            "",
            analysis,
            "",
            "<code>#BRIEFING</code>",
        ]

    return "\n".join(lines)


def build_morning_recap() -> str:
    """Build the 7 AM SGT morning recap of last night's US session."""
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    now_et = datetime.now(ZoneInfo(TZ_ET))

    mu_data = get_daily_data(POSITION["ticker"])
    vix_data = get_daily_data("^VIX")
    soxx_data = get_daily_data("SOXX")

    if not mu_data or mu_data["price"] is None:
        return "\U0001f319 <b>MORNING RECAP</b>\nFailed to fetch MU data."

    mu_price = mu_data["price"]
    prev_close = mu_data["prev_close"] or mu_price
    change_pct = ((mu_price - prev_close) / prev_close) * 100 if prev_close else 0

    # Spread (only if position active)
    has_position = POSITION.get("contracts")
    if has_position:
        spread_val = estimate_spread_value(mu_price)
        entry = POSITION["entry_price"]
        contracts = POSITION["contracts"]
        pnl = (spread_val - entry) * contracts * 100
        pnl_pct = ((spread_val - entry) / entry) * 100

        # DTE
        expiry = datetime.strptime(POSITION["expiry"], "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
        dte = (expiry.date() - now_et.date()).days
        trading_days = sum(1 for i in range(dte) if (now_et.date() + timedelta(days=i+1)).weekday() < 5)

        action = get_framework_action(mu_price, trading_days)

    def chg_arrow(pct):
        if pct > 0.5:
            return "\U0001f7e2"
        elif pct < -0.5:
            return "\U0001f534"
        return "\U0001f7e1"

    # Determine session verdict
    if change_pct > 1.5:
        verdict = "\U0001f389 Strong session"
    elif change_pct > 0.3:
        verdict = "\u2705 Positive session"
    elif change_pct > -0.3:
        verdict = "\u2796 Flat session"
    elif change_pct > -1.5:
        verdict = "\u26a0\ufe0f Weak session"
    else:
        verdict = "\U0001f534 Rough session"

    mu_arrow = chg_arrow(change_pct)
    lines = [
        f"\U0001f319 <b>MORNING RECAP</b>",
        f"<i>{now_sgt.strftime('%a, %b %d')} \u2014 Last night's session</i>",
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
        "",
        f"{verdict}",
        "",
        f"{mu_arrow} <b>MU  ${mu_price:.2f}  ({change_pct:+.2f}%)</b>",
    ]

    if mu_data.get("day_high") and mu_data.get("day_low"):
        lines.append(f"    L ${mu_data['day_low']:.2f}  \u2022  H ${mu_data['day_high']:.2f}")

    # VIX
    if vix_data and vix_data["price"]:
        vix_icon = "\U0001f6a8" if vix_data["price"] >= 25 else "\U0001f4ca"
        lines.append(f"\n{vix_icon} <b>VIX  {vix_data['price']:.2f}</b>")

    # SOXX
    if soxx_data and soxx_data["price"]:
        soxx_pct = 0
        if soxx_data["prev_close"]:
            soxx_pct = ((soxx_data["price"] - soxx_data["prev_close"]) / soxx_data["prev_close"]) * 100
        lines.append(f"{chg_arrow(soxx_pct)} SOXX  ${soxx_data['price']:.2f}  ({soxx_pct:+.2f}%)")

    # Peers
    lines += ["", "\U0001f4c8 <b>PEERS</b>"]
    for peer in PEERS:
        if peer == "SOXX":
            continue
        pdata = get_daily_data(peer)
        if pdata and pdata["price"]:
            ppct = 0
            if pdata["prev_close"]:
                ppct = ((pdata["price"] - pdata["prev_close"]) / pdata["prev_close"]) * 100
            lines.append(f"  {chg_arrow(ppct)} {peer}  ${pdata['price']:.2f}  ({ppct:+.2f}%)")

    # Futures (overnight/next session preview)
    lines += ["", "\U0001f310 <b>FUTURES</b>"]
    for fticker, fname in FUTURES.items():
        fdata = get_daily_data(fticker)
        if fdata and fdata["price"]:
            fpct = 0
            if fdata["prev_close"]:
                fpct = ((fdata["price"] - fdata["prev_close"]) / fdata["prev_close"]) * 100
            short = {"S&P 500 Futures": "S&P", "Nasdaq Futures": "NQ", "Dow Futures": "DOW"}
            lines.append(f"  {chg_arrow(fpct)} {short.get(fname, fname)}  {fdata['price']:,.0f}  ({fpct:+.2f}%)")

    # Position summary (only if active)
    if has_position:
        pnl_icon = "\U0001f4b9" if pnl > 0 else "\U0001f4c9"
        lines += [
            "",
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            f"\U0001f4bc <b>POSITION</b>",
            f"  {POSITION['long_strike']}/{POSITION['short_strike']} \u00d7{contracts}  |  {trading_days} days left",
            f"  Spread: ${spread_val:.2f}  (entry ${entry:.2f})",
            f"  {pnl_icon} P&L: <b>${pnl:+,.0f}</b>  ({pnl_pct:+.1f}%)",
            "",
            f"\U0001f3af <b>TODAY:</b> {action}",
        ]

        # Earnings analysis (post March 18) - only relevant with position
        earnings = detect_earnings_result()
        if earnings and earnings.get("result"):
            result_emoji = {
                "BEAT": "\U0001f389",
                "MISS": "\U0001f534",
                "INLINE": "\U0001f7e1",
            }
            e = earnings
            lines += [
                "",
                f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
                f"{result_emoji.get(e['result'], '')} <b>EARNINGS: {e['result']}</b>  ({e['move_pct']:+.1f}%)",
                f"<i>{e.get('analysis', '')}</i>",
            ]

    # What's coming today/tomorrow
    upcoming = get_upcoming_catalysts(days_ahead=1)
    if upcoming:
        lines += [
            "",
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "\U0001f4c5 <b>TODAY'S CATALYSTS</b>",
        ]
        for u in upcoming:
            lines.append(f"  \U0001f4cc{u.strip()}")

    # AI Roundtable analysis
    analysis = deepseek_daily_analysis(mode="recap")
    if analysis:
        lines += [
            "",
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
            "\U0001f9e0 <b>AI ROUNDTABLE</b>",
            "",
            analysis,
            "",
            "<code>#RECAP</code>",
        ]

    return "\n".join(lines)


def send_briefing():
    """Build and send the daily pre-market briefing."""
    msg = build_briefing()
    send_daily_briefing(msg)


def send_morning_recap():
    """Build and send the 7 AM SGT morning recap."""
    msg = build_morning_recap()
    send_daily_briefing(msg)


if __name__ == "__main__":
    send_briefing()
