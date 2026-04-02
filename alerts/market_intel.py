"""Market Intelligence - Short interest, sector rotation, peer sympathy."""

import json
import os
import yfinance as yf
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import DEEPSEEK_API_KEY, POSITION, TZ_ET, PEERS, PEER_CORRELATIONS
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".market_intel_state.json")

# Sector ETFs for rotation analysis
SECTOR_ETFS = {
    "XLK": "Tech",
    "SOXX": "Semis",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLP": "Staples",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "QQQ": "Nasdaq 100",
    "SPY": "S&P 500",
    "IWM": "Small Cap",
}


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_rotation_date": None, "last_si_date": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_short_interest():
    """Check MU short interest metrics via yfinance."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_si_date") == today:
        return

    try:
        t = yf.Ticker(POSITION["ticker"])
        info = t.info or {}

        short_pct = (info.get("shortPercentOfFloat", 0) or 0) * 100  # Convert from decimal to %
        short_ratio = info.get("shortRatio", 0)
        shares_short = info.get("sharesShort", 0)
        prev_shares_short = info.get("sharesShortPriorMonth", 0)

        if not short_pct:
            return

        # Calculate change
        si_change = ""
        if prev_shares_short and shares_short:
            chg_pct = ((shares_short - prev_shares_short) / prev_shares_short) * 100
            if abs(chg_pct) > 5:
                direction = "UP" if chg_pct > 0 else "DOWN"
                si_change = f"\nChange vs prior month: {direction} {abs(chg_pct):.1f}%"

        # Only alert if notable
        if short_pct > 5 or si_change:
            analysis = _analyze_short_interest(short_pct, short_ratio, shares_short, si_change)

            msg = (
                f"\U0001f4ca <b>MU SHORT INTEREST</b>\n"
                f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
                f"Short % of float: <b>{short_pct:.1f}%</b>\n"
                f"Short ratio: {short_ratio:.1f} days\n"
                f"Shares short: {shares_short:,.0f}"
                f"{si_change}\n\n"
                f"\U0001f9e0 {analysis}\n\n"
                f"<code>#SI</code>"
            )
            send_alert(msg)

        state["last_si_date"] = today
        save_state(state)
    except Exception as e:
        print(f"[SHORT INTEREST ERROR] {e}")


def check_sector_rotation():
    """Compare sector ETF performance with 1-day and 5-day trends."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_rotation_date") == today:
        return

    performances_1d = {}
    performances_5d = {}
    for etf, name in SECTOR_ETFS.items():
        try:
            t = yf.Ticker(etf)
            hist = t.history(period="10d")
            if len(hist) < 2:
                continue
            # 1-day return
            pct_1d = ((hist["Close"].iloc[-1] - hist["Close"].iloc[-2]) / hist["Close"].iloc[-2]) * 100
            performances_1d[name] = round(pct_1d, 2)
            # 5-day return (or as many days as available)
            if len(hist) >= 6:
                pct_5d = ((hist["Close"].iloc[-1] - hist["Close"].iloc[-6]) / hist["Close"].iloc[-6]) * 100
            else:
                pct_5d = ((hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0]) * 100
            performances_5d[name] = round(pct_5d, 2)
        except Exception:
            continue

    if len(performances_1d) < 5:
        return

    sorted_1d = sorted(performances_1d.items(), key=lambda x: x[1], reverse=True)
    sorted_5d = sorted(performances_5d.items(), key=lambda x: x[1], reverse=True)
    semis_rank_1d = next((i for i, (k, _) in enumerate(sorted_1d) if k == "Semis"), -1)
    semis_rank_5d = next((i for i, (k, _) in enumerate(sorted_5d) if k == "Semis"), -1)
    semis_1d = performances_1d.get("Semis", 0)
    semis_5d = performances_5d.get("Semis", 0)

    # DeepSeek analysis with both timeframes
    perf_str_1d = "\n".join(f"  {name}: {pct:+.2f}%" for name, pct in sorted_1d)
    perf_str_5d = "\n".join(f"  {name}: {pct:+.2f}%" for name, pct in sorted_5d)
    analysis = _analyze_rotation(
        f"1-DAY:\n{perf_str_1d}\n\n5-DAY:\n{perf_str_5d}",
        semis_rank_1d, semis_1d, len(sorted_1d)
    )

    # Build message with both timeframes
    lines = [
        f"\U0001f504 <b>SECTOR ROTATION</b>",
        "\u2500" * 20,
        "",
        "<b>Today:</b>",
    ]
    for name, pct in sorted_1d:
        if pct > 0.3:
            emoji = "\U0001f7e2"
        elif pct < -0.3:
            emoji = "\U0001f534"
        else:
            emoji = "\U0001f7e1"
        marker = " \u2190 YOU" if name == "Semis" else ""
        lines.append(f"  {emoji} {name}: {pct:+.2f}%{marker}")

    lines += ["", "<b>5-Day Trend:</b>"]
    for name, pct in sorted_5d:
        if pct > 0.3:
            emoji = "\U0001f7e2"
        elif pct < -0.3:
            emoji = "\U0001f534"
        else:
            emoji = "\U0001f7e1"
        marker = " \u2190 YOU" if name == "Semis" else ""
        lines.append(f"  {emoji} {name}: {pct:+.2f}%{marker}")

    lines += [
        "",
        f"Semis rank: #{semis_rank_1d + 1} today, #{semis_rank_5d + 1} over 5d",
        "",
        f"\U0001f9e0 {analysis}",
        "",
        "<code>#SECTOR</code>",
    ]

    send_alert("\n".join(lines))

    state["last_rotation_date"] = today
    save_state(state)


def check_peer_sympathy():
    """Check if a peer had a big earnings move and estimate MU sympathy."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_sympathy_date") == today:
        return

    triggered = []
    for peer in ["NVDA", "AMD", "AVGO", "MRVL"]:
        try:
            t = yf.Ticker(peer)
            info = t.fast_info
            price = info.get("lastPrice") or info.get("last_price") or 0
            prev = info.get("previousClose") or info.get("previous_close") or 0
            if not prev or prev <= 0:
                continue
            pct_chg = ((price - prev) / prev) * 100
            # Big move (>5%) suggests earnings/major event
            if abs(pct_chg) >= 5.0:
                corr = PEER_CORRELATIONS.get(peer, 0.5)
                est_mu_move = pct_chg * corr
                triggered.append({
                    "peer": peer,
                    "pct": pct_chg,
                    "corr": corr,
                    "est_mu": est_mu_move,
                })
        except Exception:
            continue

    if not triggered:
        return

    # Get current MU price for context
    mu_price = 0
    try:
        t = yf.Ticker(POSITION["ticker"])
        mu_price = t.fast_info.get("lastPrice") or t.fast_info.get("last_price") or 0
    except Exception:
        pass

    lines = [
        f"\U0001f4e1 <b>PEER SYMPATHY ALERT</b>",
        "\u2500" * 20,
        "",
    ]

    for t in triggered:
        arrow = "\u25b2" if t["pct"] > 0 else "\u25bc"
        emoji = "\U0001f7e2" if t["pct"] > 0 else "\U0001f534"
        lines.append(
            f"{emoji} <b>{t['peer']}</b> {arrow} {t['pct']:+.1f}%\n"
            f"   Correlation: {t['corr']:.0%} | Est MU sympathy: {t['est_mu']:+.1f}%"
        )

    if mu_price:
        best_est = max(triggered, key=lambda x: abs(x["est_mu"]))
        implied = mu_price * (1 + best_est["est_mu"] / 100)
        lines += [
            "",
            f"\U0001f4b0 MU now: <b>${mu_price:.2f}</b>",
            f"\U0001f3af Implied: <b>${implied:.2f}</b> ({best_est['est_mu']:+.1f}%)",
        ]

    # DeepSeek analysis
    context = ", ".join(f"{t['peer']} {t['pct']:+.1f}%" for t in triggered)
    analysis = _analyze_sympathy(context, mu_price)
    lines += ["", f"\U0001f9e0 {analysis}", "", "<code>#SYMPATHY</code>"]

    send_alert("\n".join(lines))
    state["last_sympathy_date"] = today
    save_state(state)


def _analyze_sympathy(peer_moves: str, mu_price: float) -> str:
    """DeepSeek analysis of peer sympathy implications."""
    prompt = (
        f"Semi peer stocks made big moves: {peer_moves}\n"
        f"MU is at ${mu_price:.2f}. I hold 500x MU 380/400 bull call spreads expiring March 20.\n"
        f"In 2 sentences: how will MU likely react? Is the peer move relevant to MU's fundamentals "
        f"(memory/HBM) or is it a different sub-sector?"
    )
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 100},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "Analysis unavailable."


def _analyze_short_interest(short_pct, short_ratio, shares_short, si_change) -> str:
    prompt = (
        f"MU short interest: {short_pct:.1f}% of float, ratio {short_ratio:.1f} days, "
        f"{shares_short:,.0f} shares short. {si_change}\n"
        f"My position: MU 380/400 bull call spread expiring March 20.\n"
        f"In 1-2 sentences: is this SI level bullish (squeeze potential) or bearish (smart money short)?"
    )
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 100},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "Analysis unavailable."


def _analyze_rotation(perf_str, semis_rank, semis_pct, total) -> str:
    prompt = (
        f"Today's sector performance:\n{perf_str}\n\n"
        f"Semis ranked #{semis_rank + 1} of {total} ({semis_pct:+.2f}%).\n"
        f"My position: MU 380/400 bull call spread expiring March 20.\n"
        f"In 2 sentences: is money flowing into or out of semis? Is the sector leading or lagging?"
    )
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 100},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "Analysis unavailable."
