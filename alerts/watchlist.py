"""Watchlist Scanner - finds next high-conviction setup after MU expiry."""

import yfinance as yf
import requests
from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, PEERS, position_summary
from .llm import ask
from .bot import send_alert


# Tickers to scan for setups
WATCHLIST_TICKERS = [
    "MU", "NVDA", "AMD", "AVGO", "MRVL", "QCOM", "LRCX", "AMAT", "KLAC",
    "TSM", "ASML", "SNPS", "CDNS", "ON", "MCHP",
]


def scan_setups() -> list[dict]:
    """Scan watchlist for potential setups based on technicals and momentum."""
    setups = []
    for ticker in WATCHLIST_TICKERS:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1mo")
            if len(hist) < 10:
                continue

            info = t.fast_info
            price = info.get("lastPrice") or info.get("last_price")
            prev = info.get("previousClose") or info.get("previous_close")
            if not price or not prev:
                continue

            # Monthly performance
            month_start = hist["Close"].iloc[0]
            month_pct = ((price - month_start) / month_start) * 100

            # 1-day change
            day_pct = ((price - prev) / prev) * 100

            # Volatility (std of daily returns)
            returns = hist["Close"].pct_change().dropna()
            vol = returns.std() * (252 ** 0.5) * 100  # annualized

            # Near 52-week high?
            high_52 = info.get("yearHigh") or info.get("year_high", 0)
            pct_from_high = ((price - high_52) / high_52 * 100) if high_52 else 0

            setups.append({
                "ticker": ticker,
                "price": round(price, 2),
                "day_pct": round(day_pct, 2),
                "month_pct": round(month_pct, 2),
                "vol": round(vol, 1),
                "pct_from_high": round(pct_from_high, 1),
            })
        except Exception:
            continue

    # Sort by momentum (monthly performance)
    setups.sort(key=lambda x: x["month_pct"], reverse=True)
    return setups


def analyze_and_alert():
    """Scan watchlist, analyze top setups with DeepSeek, send alert."""
    setups = scan_setups()
    if not setups:
        return

    top = setups[:5]
    bottom = setups[-3:]

    setup_text = "TOP MOMENTUM:\n"
    for s in top:
        setup_text += f"  {s['ticker']}: ${s['price']}, 1mo {s['month_pct']:+.1f}%, vol {s['vol']:.0f}%, {s['pct_from_high']:+.1f}% from 52wk high\n"
    setup_text += "\nWEAKEST:\n"
    for s in bottom:
        setup_text += f"  {s['ticker']}: ${s['price']}, 1mo {s['month_pct']:+.1f}%\n"

    # DeepSeek analysis
    prompt = (
        f"You are a semiconductor options setup scanner.\n\n"
        f"SCANNED RESULTS:\n{setup_text}\n"
        f"The trader currently holds {position_summary()}. "
        f"They like high-conviction vertical spreads on semis with upcoming catalysts.\n\n"
        f"In 3-4 sentences: Which ticker has the best setup for the NEXT vertical spread trade? "
        f"What strikes and expiry would you suggest? What's the catalyst?"
    )

    analysis = ask(prompt, tier="fast", temperature=0.3, max_tokens=3000,
                   label="watchlist", fallback="Setup analysis unavailable.")

    # Format message
    lines = [
        "\U0001f50e <b>WATCHLIST SCANNER</b>",
        "\u2500" * 20,
        "",
        "\U0001f4c8 <b>TOP MOMENTUM</b>",
    ]
    for s in top:
        emoji = "\U0001f7e2" if s["month_pct"] > 0 else "\U0001f534"
        lines.append(f"  {emoji} {s['ticker']}: ${s['price']} ({s['month_pct']:+.1f}% 1mo)")

    lines += [
        "",
        "\U0001f4c9 <b>WEAKEST</b>",
    ]
    for s in bottom:
        lines.append(f"  \U0001f534 {s['ticker']}: ${s['price']} ({s['month_pct']:+.1f}% 1mo)")

    lines += [
        "",
        "\u2500" * 20,
        "\U0001f9e0 <b>NEXT SETUP</b>",
        "",
        analysis,
        "",
        "<code>#WATCHLIST</code>",
    ]

    send_alert("\n".join(lines))
