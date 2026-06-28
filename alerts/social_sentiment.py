"""Social Sentiment Pulse - StockTwits and Reddit monitoring for MU."""

import json
import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, POSITION, position_summary, TZ_ET
from .bot import send_alert
from .llm import ask

STATE_FILE = os.path.join(os.path.dirname(__file__), ".social_state.json")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# StockTwits public API (no auth required)
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/MU.json"

# Reddit search endpoints (public JSON, no auth)
REDDIT_SUBREDDITS = [
    ("wallstreetbets", "https://www.reddit.com/r/wallstreetbets/search.json"),
    ("stocks", "https://www.reddit.com/r/stocks/search.json"),
]

REDDIT_HEADERS = {"User-Agent": "MU-Advisor/1.0"}

# Alert thresholds
EXTREME_BULLISH_RATIO = 0.75
EXTREME_BEARISH_RATIO = 0.25
REDDIT_HIGH_VOLUME = 20
RATIO_SHIFT_THRESHOLD = 0.20  # 20% shift from previous check


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {
        "last_check_date": None,
        "prev_bullish_ratio": None,
        "prev_reddit_volume": None,
    })


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _fetch_stocktwits() -> dict:
    """Fetch StockTwits stream for MU and compute sentiment metrics.

    Returns: {"bullish": int, "bearish": int, "total": int, "ratio": float|None, "sample_messages": list}
    """
    result = {"bullish": 0, "bearish": 0, "total": 0, "ratio": None, "sample_messages": []}

    try:
        resp = requests.get(STOCKTWITS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        messages = data.get("messages", [])
        result["total"] = len(messages)

        bullish = 0
        bearish = 0
        samples = []

        for msg in messages:
            sentiment = msg.get("entities", {}).get("sentiment", {})
            if sentiment:
                basic = sentiment.get("basic", "")
                if basic == "Bullish":
                    bullish += 1
                elif basic == "Bearish":
                    bearish += 1

            # Collect a few sample messages for DeepSeek context
            body = msg.get("body", "")
            if body and len(samples) < 5:
                samples.append(body[:150])

        result["bullish"] = bullish
        result["bearish"] = bearish
        result["sample_messages"] = samples

        tagged = bullish + bearish
        if tagged > 0:
            result["ratio"] = bullish / tagged

    except Exception as e:
        print(f"[STOCKTWITS ERROR] {e}")

    return result


def _fetch_reddit_mentions() -> dict:
    """Search Reddit for MU/Micron mentions in key subreddits.

    Returns: {"total": int, "posts": list[dict], "subreddit_counts": dict}
    """
    result = {"total": 0, "posts": [], "subreddit_counts": {}}

    for sub_name, search_url in REDDIT_SUBREDDITS:
        try:
            params = {
                "q": "MU OR micron",
                "sort": "new",
                "t": "day",
                "limit": 25,
                "restrict_sr": "on",
            }
            resp = requests.get(
                search_url, params=params, headers=REDDIT_HEADERS, timeout=10
            )

            if resp.status_code == 429:
                # Rate limited - skip this subreddit
                print(f"[REDDIT RATE LIMITED] r/{sub_name}")
                continue

            resp.raise_for_status()
            data = resp.json()

            children = data.get("data", {}).get("children", [])
            count = len(children)
            result["subreddit_counts"][sub_name] = count
            result["total"] += count

            for child in children[:5]:
                post = child.get("data", {})
                result["posts"].append({
                    "title": post.get("title", "")[:150],
                    "subreddit": sub_name,
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                })

        except Exception as e:
            print(f"[REDDIT ERROR r/{sub_name}] {e}")

    return result


def _analyze_social_sentiment(stocktwits: dict, reddit: dict) -> str:
    """Ask DeepSeek to interpret the social sentiment data."""
    st_info = (
        f"StockTwits MU stream: {stocktwits['total']} messages, "
        f"{stocktwits['bullish']} bullish, {stocktwits['bearish']} bearish"
    )
    if stocktwits["ratio"] is not None:
        st_info += f" (ratio: {stocktwits['ratio']:.0%} bullish)"

    if stocktwits["sample_messages"]:
        st_info += "\nSample messages:\n" + "\n".join(
            f'  - "{m}"' for m in stocktwits["sample_messages"][:3]
        )

    reddit_info = f"Reddit mentions (24h): {reddit['total']} total"
    for sub, count in reddit.get("subreddit_counts", {}).items():
        reddit_info += f", r/{sub}: {count}"

    if reddit["posts"]:
        reddit_info += "\nTop posts:\n" + "\n".join(
            f'  - r/{p["subreddit"]}: "{p["title"]}" ({p["score"]} upvotes, {p["num_comments"]} comments)'
            for p in reddit["posts"][:3]
        )

    prompt = (
        f"Social sentiment snapshot for Micron Technology (MU):\n\n"
        f"{st_info}\n\n{reddit_info}\n\n"
        f"My position: {position_summary()}.\n"
        f"In 2-3 sentences: summarize the social sentiment. Is this a contrarian signal? "
        f"Is retail overly bullish/bearish? What's the dominant narrative?"
    )
    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="social_sentiment", fallback="Analysis unavailable.")


def check_social_sentiment():
    """Check StockTwits and Reddit for MU social sentiment. Once daily."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    # Only check once per day
    if state.get("last_check_date") == today:
        return

    try:
        stocktwits = _fetch_stocktwits()
        reddit = _fetch_reddit_mentions()

        ratio = stocktwits["ratio"]
        reddit_vol = reddit["total"]
        prev_ratio = state.get("prev_bullish_ratio")
        prev_reddit_vol = state.get("prev_reddit_volume")

        # Determine if any alert conditions are met
        alerts = []

        if ratio is not None:
            if ratio >= EXTREME_BULLISH_RATIO:
                alerts.append(f"Extreme bullish sentiment ({ratio:.0%})")
            elif ratio <= EXTREME_BEARISH_RATIO:
                alerts.append(f"Extreme bearish sentiment ({ratio:.0%})")

            if prev_ratio is not None:
                shift = abs(ratio - prev_ratio)
                if shift >= RATIO_SHIFT_THRESHOLD:
                    direction = "bullish" if ratio > prev_ratio else "bearish"
                    alerts.append(f"Sentiment shift {direction} ({prev_ratio:.0%} -> {ratio:.0%})")

        if reddit_vol >= REDDIT_HIGH_VOLUME:
            alerts.append(f"High Reddit buzz ({reddit_vol} mentions in 24h)")

        if prev_reddit_vol is not None and prev_reddit_vol > 0 and reddit_vol > 0:
            vol_change = reddit_vol / prev_reddit_vol
            if vol_change >= 3.0:
                alerts.append(f"Reddit volume spike ({prev_reddit_vol} -> {reddit_vol})")

        # Get DeepSeek analysis
        analysis = _analyze_social_sentiment(stocktwits, reddit)

        # Build alert message
        # Determine overall sentiment emoji
        if ratio is not None:
            if ratio >= 0.65:
                sentiment_emoji = "\U0001f7e2"  # green circle
                sentiment_label = "BULLISH"
            elif ratio <= 0.35:
                sentiment_emoji = "\U0001f534"  # red circle
                sentiment_label = "BEARISH"
            else:
                sentiment_emoji = "\U0001f7e1"  # yellow circle
                sentiment_label = "MIXED"
        else:
            sentiment_emoji = "\U0001f7e1"
            sentiment_label = "INSUFFICIENT DATA"

        # StockTwits section
        st_line = f"\U0001f4ac StockTwits: {stocktwits['total']} messages"
        if ratio is not None:
            bar_filled = int(ratio * 10)
            bar = "\U0001f7e2" * bar_filled + "\U0001f534" * (10 - bar_filled)
            st_line += (
                f"\n  {bar}\n"
                f"  Bulls: {stocktwits['bullish']}  Bears: {stocktwits['bearish']}  "
                f"Ratio: <b>{ratio:.0%}</b>"
            )
        else:
            st_line += "\n  No tagged sentiment in current batch"

        # Reddit section
        reddit_line = f"\U0001f4e2 Reddit (24h): <b>{reddit_vol}</b> mentions"
        for sub, count in reddit.get("subreddit_counts", {}).items():
            reddit_line += f"\n  r/{sub}: {count}"

        # Alert triggers section
        trigger_line = ""
        if alerts:
            trigger_line = "\n\n\u26a0\ufe0f <b>Triggers:</b>\n" + "\n".join(f"  \u2022 {a}" for a in alerts)

        msg = (
            f"{sentiment_emoji} <b>SOCIAL SENTIMENT: MU {sentiment_label}</b>\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{st_line}\n\n"
            f"{reddit_line}"
            f"{trigger_line}\n\n"
            f"\U0001f9e0 {analysis}\n\n"
            f"<code>#SOCIAL</code>"
        )

        send_alert(msg)

        # Update state
        state["last_check_date"] = today
        if ratio is not None:
            state["prev_bullish_ratio"] = ratio
        state["prev_reddit_volume"] = reddit_vol
        save_state(state)

    except Exception as e:
        print(f"[SOCIAL SENTIMENT ERROR] {e}")


def check_retail_positioning():
    """Check retail trader positioning signals — margin calls, capitulation, FOMO.

    Uses StockTwits message volume spikes + sentiment extremes as proxy
    for retail panic (sell) or FOMO (buy) cascades.
    """
    try:
        stocktwits = _fetch_stocktwits()
        total = stocktwits.get("total", 0)
        ratio = stocktwits.get("ratio")

        if total < 5 or ratio is None:
            return

        # Retail capitulation signal: high volume + extreme bearishness
        if total >= 30 and ratio <= 0.20:
            msg = (
                "RETAIL CAPITULATION SIGNAL\n\n"
                f"StockTwits: {total} messages, only {ratio:.0%} bullish\n"
                "Retail traders are panic-selling MU.\n"
                "Historically, extreme retail bearishness = near-term bottom signal.\n"
                "Watch for margin call cascades in 24hr trading.\n\n"
                "#SOCIAL #RETAIL_PANIC"
            )
            send_alert(msg)

        # Retail FOMO signal: high volume + extreme bullishness
        elif total >= 30 and ratio >= 0.85:
            msg = (
                "RETAIL FOMO SIGNAL\n\n"
                f"StockTwits: {total} messages, {ratio:.0%} bullish\n"
                "Retail traders are piling in aggressively.\n"
                "Extreme retail bullishness often precedes short-term pullbacks.\n\n"
                "#SOCIAL #RETAIL_FOMO"
            )
            send_alert(msg)

    except Exception as e:
        print(f"[RETAIL POSITIONING ERROR] {e}")


if __name__ == "__main__":
    check_social_sentiment()
