"""Volume anomaly detector - dark pool / block trade proxy."""

import json
import os
import yfinance as yf
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import (
    DEEPSEEK_API_KEY, POSITION, TZ_ET,
    VOLUME_ANOMALY_RATIO, VOLUME_EXTREME_RATIO,
)
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".volume_state.json")


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_alert_date": None}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def check_volume_anomaly():
    """Detect unusual volume as a proxy for dark pool / block activity.

    - Volume > 2x 20-day avg with <1% price change = likely institutional/dark pool
    - Volume > 3x 20-day avg = extreme volume regardless of price change
    - Also scans NVDA, AMD for sector-wide volume signals
    """
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_alert_date") == today:
        return

    anomalies = []
    tickers_to_check = [POSITION["ticker"], "NVDA", "AMD"]

    for ticker in tickers_to_check:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="1mo")
            if len(hist) < 5:
                continue

            # 20-day average volume (or however many days available)
            avg_vol = hist["Volume"].iloc[:-1].mean() if len(hist) > 1 else 0
            if avg_vol <= 0:
                continue

            # Today's data
            today_vol = hist["Volume"].iloc[-1]
            today_close = hist["Close"].iloc[-1]
            prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else today_close
            price_chg_pct = ((today_close - prev_close) / prev_close * 100) if prev_close else 0

            vol_ratio = today_vol / avg_vol

            if vol_ratio < VOLUME_ANOMALY_RATIO:
                continue

            # Classify the anomaly
            if vol_ratio >= VOLUME_EXTREME_RATIO:
                anomaly_type = "EXTREME"
            elif abs(price_chg_pct) < 1.0:
                anomaly_type = "DARK POOL"  # High volume, small price move
            else:
                anomaly_type = "BLOCK"

            anomalies.append({
                "ticker": ticker,
                "type": anomaly_type,
                "vol_ratio": round(vol_ratio, 1),
                "today_vol": int(today_vol),
                "avg_vol": int(avg_vol),
                "price_chg": round(price_chg_pct, 2),
                "price": round(today_close, 2),
            })

        except Exception as e:
            print(f"[VOLUME CHECK ERROR] {ticker}: {e}")
            continue

    if not anomalies:
        return

    # Build message
    lines = [
        "\U0001f4ca <b>VOLUME ANOMALY DETECTED</b>",
        "\u2500" * 20,
        "",
    ]

    for a in anomalies:
        if a["type"] == "EXTREME":
            emoji = "\U0001f525"  # fire
            label = "EXTREME VOLUME"
        elif a["type"] == "DARK POOL":
            emoji = "\U0001f576\ufe0f"  # sunglasses
            label = "DARK POOL PROXY"
        else:
            emoji = "\U0001f4e6"  # package
            label = "BLOCK ACTIVITY"

        is_mu = " \u2190 YOUR STOCK" if a["ticker"] == POSITION["ticker"] else ""
        lines.append(
            f"{emoji} <b>{a['ticker']}</b> - {label}{is_mu}\n"
            f"   Vol: {a['today_vol']:,} ({a['vol_ratio']}x avg)\n"
            f"   Avg: {a['avg_vol']:,}/day\n"
            f"   Price: ${a['price']:.2f} ({a['price_chg']:+.2f}%)"
        )
        lines.append("")

    # DeepSeek analysis
    context = "; ".join(
        f"{a['ticker']}: {a['vol_ratio']}x vol, {a['price_chg']:+.2f}% price ({a['type']})"
        for a in anomalies
    )
    analysis = _analyze_volume(context)
    lines.append(f"\U0001f9e0 {analysis}")
    lines.append("")
    lines.append("<code>#VOLUME</code>")

    send_alert("\n".join(lines))

    state["last_alert_date"] = today
    save_state(state)


def _analyze_volume(context: str) -> str:
    prompt = (
        f"Volume anomalies detected in semi stocks: {context}\n"
        f"'Dark pool proxy' = high volume with small price impact (institutional accumulation/distribution).\n"
        f"My position: 500x MU 380/400 bull call spread expiring March 20.\n"
        f"In 2 sentences: is this likely accumulation (bullish) or distribution (bearish)? "
        f"What does the volume pattern suggest for MU?"
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
