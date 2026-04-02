"""Weekend news/media digest — batches all weekend content into a single summary.

Instead of individual alerts, collects all weekend news, YouTube videos, and
media coverage, then sends one consolidated digest Sunday evening SGT.
"""

import json
import os
import re
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT, DEEPSEEK_API_KEY
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".weekend_digest_state.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Weekend media sources (Barron's, WSJ, FT publish weekend pieces)
WEEKEND_RSS_FEEDS = [
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=MU&region=US&lang=en-US", "Yahoo MU"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US", "Yahoo NVDA"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=SOXX&region=US&lang=en-US", "Yahoo SOXX"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSM&region=US&lang=en-US", "Yahoo TSM"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_digest_week": None, "collected_headlines": []}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _collect_weekend_headlines() -> list:
    """Collect headlines from RSS feeds published during the weekend."""
    headlines = []
    now = datetime.now(ZoneInfo(TZ_ET))
    # Look back 48 hours to cover Saturday + Sunday
    cutoff = now - timedelta(hours=48)

    for feed_url, source in WEEKEND_RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            for item in root.iter("item"):
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                pub_date = item.findtext("pubDate", "").strip()

                if title:
                    headlines.append({
                        "title": title,
                        "source": source,
                        "link": link,
                        "pub_date": pub_date,
                    })
        except Exception:
            continue

    return headlines


def _collect_youtube_weekend() -> list:
    """Check recent YouTube state for videos posted over the weekend."""
    yt_state_file = os.path.join(os.path.dirname(__file__), ".youtube_state.json")
    videos = []
    if os.path.exists(yt_state_file):
        try:
            with open(yt_state_file) as f:
                state = json.load(f)
            # Get recently alerted videos
            for vid_id, data in state.get("seen_videos", {}).items():
                if isinstance(data, dict) and data.get("alerted"):
                    videos.append({
                        "title": data.get("title", vid_id),
                        "channel": data.get("channel", "Unknown"),
                    })
        except Exception:
            pass
    return videos[-10:]  # Last 10


def _ai_digest(headlines: list, videos: list) -> str:
    """Use DeepSeek to create a consolidated weekend digest."""
    headline_text = "\n".join(f"- [{h['source']}] {h['title']}" for h in headlines[:30])
    video_text = "\n".join(f"- [{v['channel']}] {v['title']}" for v in videos[:10])

    prompt = f"""Create a concise weekend news digest for a Micron (MU) investor holding 500x MU 380/400 bull call spread expiring March 20, 2026.

Weekend Headlines:
{headline_text}

Weekend YouTube Coverage:
{video_text}

Summarize in this format:
1. TOP STORIES (2-3 most important developments, 1 sentence each)
2. SEMI SECTOR (any sector-wide news)
3. MACRO/GEOPOLITICAL (if relevant)
4. MONDAY OUTLOOK (what to expect based on weekend news)
5. MU IMPACT (specific implications for Micron and the spread position)

Keep total response under 300 words. Be direct and actionable."""

    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.3,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return "AI digest unavailable — review headlines below"


def send_weekend_digest():
    """Main function - send consolidated weekend news/media digest.

    Runs Sunday 6 PM SGT (before Sunday futures open alert).
    """
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    state = load_state()
    week_key = now_sgt.strftime("%Y-W%W")

    if state.get("last_digest_week") == week_key:
        return

    headlines = _collect_weekend_headlines()
    videos = _collect_youtube_weekend()

    if not headlines and not videos:
        return

    # AI digest
    digest = _ai_digest(headlines, videos)

    msg = f"WEEKEND NEWS DIGEST\n"
    msg += f"Coverage: Saturday-Sunday\n"
    msg += f"Sources: {len(headlines)} articles, {len(videos)} videos\n"
    msg += f"\n{'-' * 30}\n\n"
    msg += digest
    msg += f"\n\n{'-' * 30}\n"
    msg += f"Headlines scanned: {len(headlines)}\n"
    msg += f"YouTube videos: {len(videos)}\n"
    msg += "\n#WEEKEND_DIGEST"

    send_alert(msg)

    state["last_digest_week"] = week_key
    save_state(state)


def collect_weekend_headline(title: str, source: str):
    """Called by news_scanner during weekend to accumulate headlines for digest."""
    state = load_state()
    collected = state.get("collected_headlines", [])
    collected.append({
        "title": title,
        "source": source,
        "timestamp": datetime.now(ZoneInfo(TZ_SGT)).isoformat(),
    })
    # Keep last 50
    state["collected_headlines"] = collected[-50:]
    save_state(state)
