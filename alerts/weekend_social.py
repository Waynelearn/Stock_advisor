"""Weekend social sentiment tracker — monitors retail conviction building over weekends.

Weekend Reddit/StockTwits can be predictive: retail writes DD posts and builds
positions over the weekend, driving Monday morning momentum.
"""

import json
import os
import re
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT, DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST
from .llm import ask
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".weekend_social_state.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

HEADERS = {"User-Agent": "MU-Advisor-WeekendSocial/1.0 (research)"}

SUBREDDITS = ["wallstreetbets", "stocks", "semiconductors", "investing", "options"]
SEARCH_TERMS = ["micron", "MU", "$MU", "memory chip", "HBM", "DRAM"]


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_alert_week": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _search_reddit_weekend() -> dict:
    """Search Reddit for MU-related weekend activity."""
    posts = []
    total_score = 0
    total_comments = 0
    sentiments = {"bullish": 0, "bearish": 0, "neutral": 0}

    for term in SEARCH_TERMS[:3]:
        try:
            url = f"https://www.reddit.com/search.json"
            params = {
                "q": term,
                "sort": "hot",
                "t": "week",
                "limit": 15,
            }
            resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
            if resp.status_code == 429:
                continue
            resp.raise_for_status()
            data = resp.json()

            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                title = post.get("title", "")
                body = post.get("selftext", "")[:200]
                score = post.get("score", 0)
                comments = post.get("num_comments", 0)
                subreddit = post.get("subreddit", "")

                posts.append({
                    "title": title,
                    "body": body,
                    "score": score,
                    "comments": comments,
                    "subreddit": subreddit,
                })
                total_score += score
                total_comments += comments

                # Simple sentiment from title
                title_lower = title.lower()
                if any(w in title_lower for w in ["buy", "bull", "moon", "calls", "undervalued", "beat", "long"]):
                    sentiments["bullish"] += 1
                elif any(w in title_lower for w in ["sell", "bear", "puts", "overvalued", "short", "miss", "crash"]):
                    sentiments["bearish"] += 1
                else:
                    sentiments["neutral"] += 1

        except Exception:
            continue

    # Deduplicate by title
    seen = set()
    unique_posts = []
    for p in posts:
        if p["title"] not in seen:
            seen.add(p["title"])
            unique_posts.append(p)

    return {
        "posts": unique_posts[:20],
        "total_posts": len(unique_posts),
        "total_score": total_score,
        "total_comments": total_comments,
        "sentiments": sentiments,
    }


def _search_stocktwits_weekend() -> dict:
    """Get StockTwits weekend activity for MU."""
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{POSITION['ticker']}.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {"messages": 0, "bullish": 0, "bearish": 0}

        data = resp.json()
        messages = data.get("messages", [])

        bullish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
        bearish = sum(1 for m in messages if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")

        return {
            "messages": len(messages),
            "bullish": bullish,
            "bearish": bearish,
        }
    except Exception:
        return {"messages": 0, "bullish": 0, "bearish": 0}


def _ai_sentiment_summary(reddit_data: dict, st_data: dict) -> str:
    """Use DeepSeek to analyze weekend social sentiment."""
    top_posts = "\n".join(
        f"- [{p['subreddit']}] (score:{p['score']}, comments:{p['comments']}) {p['title']}"
        for p in sorted(reddit_data["posts"], key=lambda x: x["score"], reverse=True)[:10]
    )

    prompt = f"""Analyze weekend social media sentiment for Micron (MU):

Reddit Activity:
- Total posts: {reddit_data['total_posts']}
- Total engagement: {reddit_data['total_score']} upvotes, {reddit_data['total_comments']} comments
- Sentiment: {reddit_data['sentiments']['bullish']} bullish, {reddit_data['sentiments']['bearish']} bearish, {reddit_data['sentiments']['neutral']} neutral

Top Posts:
{top_posts}

StockTwits:
- Recent messages: {st_data['messages']}
- Bullish: {st_data['bullish']}, Bearish: {st_data['bearish']}

Provide:
1. Overall weekend sentiment (1 sentence)
2. Key narrative themes retail is focused on (2-3 bullets)
3. Monday prediction based on retail positioning (1 sentence)
4. Is retail sentiment a contrarian signal? (1 sentence)

Keep total under 150 words."""

    return ask(prompt, tier="fast", temperature=0.3, max_tokens=3000,
               label="weekend_social", fallback="AI analysis unavailable")


def check_weekend_social():
    """Main function — weekend social sentiment analysis.

    Runs Sunday 5 PM SGT.
    """
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))

    # Sunday only
    if now_sgt.weekday() != 6:
        return

    state = load_state()
    week_key = now_sgt.strftime("%Y-W%W")

    if state.get("last_alert_week") == week_key:
        return

    reddit_data = _search_reddit_weekend()
    st_data = _search_stocktwits_weekend()

    if reddit_data["total_posts"] == 0 and st_data["messages"] == 0:
        return

    # AI summary
    ai_summary = _ai_sentiment_summary(reddit_data, st_data)

    # Calculate overall sentiment score
    total_bull = reddit_data["sentiments"]["bullish"] + st_data["bullish"]
    total_bear = reddit_data["sentiments"]["bearish"] + st_data["bearish"]
    total = total_bull + total_bear
    if total > 0:
        bull_pct = total_bull / total * 100
    else:
        bull_pct = 50

    if bull_pct > 70:
        sentiment_label = "STRONGLY BULLISH"
    elif bull_pct > 55:
        sentiment_label = "MODERATELY BULLISH"
    elif bull_pct < 30:
        sentiment_label = "STRONGLY BEARISH"
    elif bull_pct < 45:
        sentiment_label = "MODERATELY BEARISH"
    else:
        sentiment_label = "MIXED/NEUTRAL"

    msg = f"WEEKEND SOCIAL SENTIMENT\n\n"
    msg += f"Overall: {sentiment_label} ({bull_pct:.0f}% bullish)\n\n"

    msg += "Reddit:\n"
    msg += f"  Posts found: {reddit_data['total_posts']}\n"
    msg += f"  Engagement: {reddit_data['total_score']} upvotes, {reddit_data['total_comments']} comments\n"
    msg += f"  Sentiment: {reddit_data['sentiments']['bullish']} bull / {reddit_data['sentiments']['bearish']} bear / {reddit_data['sentiments']['neutral']} neutral\n\n"

    msg += "StockTwits:\n"
    msg += f"  Messages: {st_data['messages']}\n"
    msg += f"  Sentiment: {st_data['bullish']} bullish / {st_data['bearish']} bearish\n\n"

    # Top posts
    top = sorted(reddit_data["posts"], key=lambda x: x["score"], reverse=True)[:3]
    if top:
        msg += "Top Weekend Posts:\n"
        for p in top:
            msg += f"  [{p['subreddit']}] ({p['score']} pts) {p['title'][:80]}\n"
        msg += "\n"

    msg += f"AI Analysis:\n{ai_summary}\n"
    msg += "\n#WEEKEND_SOCIAL"

    send_alert(msg)

    state["last_alert_week"] = week_key
    save_state(state)
