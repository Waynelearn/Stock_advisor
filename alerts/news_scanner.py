"""News scanner - polls RSS feeds, summarizes via DeepSeek, rates MU sentiment."""

import json
import os
import re
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .config import TZ_SGT, STATE_RETENTION
from .bot import send_alert
from .summarizer import summarize_article, fetch_article, batch_sentiment

STATE_FILE = os.path.join(os.path.dirname(__file__), ".news_state.json")
HEADLINES_LOG = os.path.join(os.path.dirname(__file__), ".recent_headlines.json")

# RSS feeds to monitor
RSS_FEEDS = [
    # Yahoo Finance RSS for key tickers
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=MU&region=US&lang=en-US", "Yahoo MU"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US", "Yahoo NVDA"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=AMD&region=US&lang=en-US", "Yahoo AMD"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=AVGO&region=US&lang=en-US", "Yahoo AVGO"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=MRVL&region=US&lang=en-US", "Yahoo MRVL"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=SOXX&region=US&lang=en-US", "Yahoo SOXX"),
    # Key semi peers & supply chain
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSM&region=US&lang=en-US", "Yahoo TSM"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=INTC&region=US&lang=en-US", "Yahoo INTC"),
    # Finviz news for semis
    ("https://finviz.com/news_export.ashx?v=3&t=MU", "Finviz MU"),
    ("https://finviz.com/news_export.ashx?v=3&t=NVDA", "Finviz NVDA"),
]

# Keywords that trigger alerts (case-insensitive)
# High priority = individual alert with full summary, normal = batched with sentiment tags
HIGH_PRIORITY_KEYWORDS = [
    # Direct MU
    r"\bmicron\b", r"\bMU\b", r"\bhbm\b", r"\bhbm3e?\b", r"\bhbm4\b",
    r"\bearnings\b.*\b(micron|MU)\b", r"\b(micron|MU)\b.*\bearnings\b",
    # Memory market
    r"\bdram\s+pric", r"\bnand\s+pric", r"\bmemory\s+(pric|demand|market|shortage|oversupply)",
    r"\bsandisk\b", r"\bwestern\s+digital\b.*\bnand\b",
    # Key macro
    r"\bfomc\b", r"\bfed\s+(rate|cut|hike|hold)\b", r"\brate\s+(cut|hike|decision)\b",
    # Semi tariff / export controls / China chip
    r"\btariff\b.*\b(semiconductor|chip)\b", r"\b(semiconductor|chip)\b.*\btariff\b",
    r"\bexport\s+control\b.*\bchip\b", r"\bchip\b.*\bexport\s+control\b",
    r"\bchina\b.*\bchip\b", r"\bchip\b.*\bchina\b",
    r"\bchina\b.*\bai\b.*\b(restrict|ban|sanction|control)\b",
    # GTC / Jensen
    r"\bgtc\b.*\bnvidia\b", r"\bnvidia\b.*\bgtc\b", r"\bjensen\b",
    # DeepSeek (direct GPU/memory demand signal)
    r"\bdeepseek\b",
    # Iran war / oil (impacts inflation → rates → semis)
    r"\biran\b.*\b(war|strike|ceasefire|nuclear|attack)\b",
    r"\b(war|strike|ceasefire|attack)\b.*\biran\b",
    r"\boil\b.*\b(spike|surge|jump|crash|price)\b",
    r"\bcrude\b.*\b(spike|surge|\$\d)",
    # Helium / critical materials (fab supply chain)
    r"\bhelium\b.*\b(shortage|supply|crisis|price|ration)",
    r"\b(shortage|supply|crisis)\b.*\bhelium\b",
    r"\bqatar\b.*\b(helium|lng|gas|attack|shut)",
    r"\bhelium\b.*\b(semiconductor|chip|fab|wafer)\b",
    r"\bneon\b.*\b(shortage|supply|semiconductor)\b",
]

NORMAL_KEYWORDS = [
    # Semi stocks (only when about stock/earnings/guidance)
    r"\bnvidia\b.*\b(stock|earn|revenue|guidance|beat|miss|upgrade|downgrade)\b",
    r"\b(stock|earn|revenue|guidance|beat|miss|upgrade|downgrade)\b.*\bnvidia\b",
    r"\bamd\b.*\b(stock|earn|revenue|guidance|beat|miss)\b",
    r"\bbroadcom\b.*\b(stock|earn|revenue|guidance|beat|miss)\b",
    r"\bmarvell\b.*\b(stock|earn|revenue|guidance|beat|miss)\b",
    r"\btsmc\b.*\b(revenue|earn|guidance|beat|miss|capex|fab)\b",
    r"\bintel\b.*\b(earn|revenue|guidance|beat|miss|foundry|fab)\b",
    # Semiconductor industry
    r"\bdram\b", r"\bnand\b", r"\bsemiconductor\b",
    r"\bsk\s*hynix\b", r"\bsamsung\b.*\b(memory|hbm|fab|earn|revenue)\b",
    r"\btsmc\b.*\b(monthly|revenue)\b",
    r"\bai\s+(chip|accelerator|server|demand|capex|spending)\b",
    r"\bblackwell\b", r"\brubin\b",
    r"\bsoxx\b",
    # China semi-specific (not generic China news)
    r"\bchina\b.*\bsemiconductor\b",
]

SENTIMENT_EMOJI = {
    "BULLISH": "\U0001f7e2",   # 🟢
    "BEARISH": "\U0001f534",   # 🔴
    "NEUTRAL": "\U0001f7e1",   # 🟡
}

RELEVANCE_EMOJI = {
    "HIGH": "\U0001f525",   # 🔥
    "MED": "\u2b50",        # ⭐
    "LOW": "\u2022",        # •
}


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"seen_hashes": [], "last_cleanup": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def normalize_title(title: str) -> str:
    """Normalize title for dedup: lowercase, strip punctuation/whitespace, remove source tags."""
    t = title.strip().lower()
    # Remove common source attributions like "- Reuters", "| Yahoo Finance", "(Bloomberg)"
    t = re.sub(r'\s*[\-\|]\s*(?:reuters|yahoo|bloomberg|cnbc|barrons|seekingalpha|motley fool|investopedia|benzinga|marketwatch|tipranks|zacks|finviz).*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\s*\((?:reuters|bloomberg|cnbc)\)\s*', '', t, flags=re.IGNORECASE)
    # Strip punctuation and collapse whitespace
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def hash_headline(title: str) -> str:
    return hashlib.md5(normalize_title(title).encode()).hexdigest()[:12]


def hash_url(url: str) -> str:
    """Hash the URL path (ignoring tracking params) for dedup."""
    if not url:
        return ""
    # Strip common tracking params
    clean = re.sub(r'[?&](utm_\w+|ref|source|soc_src|soc_trk)=[^&]*', '', url)
    return hashlib.md5(clean.strip().lower().encode()).hexdigest()[:12]


def fetch_rss(url: str, source_name: str) -> list[dict]:
    """Fetch and parse an RSS feed. Returns list of {title, link, source}."""
    items = []
    try:
        headers = {"User-Agent": "MU-Advisor-Bot/1.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            if title_el is not None and title_el.text:
                items.append({
                    "title": title_el.text.strip(),
                    "link": link_el.text.strip() if link_el is not None and link_el.text else "",
                    "source": source_name,
                })
    except Exception:
        pass
    return items


def classify_headline(title: str) -> str | None:
    """Classify headline priority. Returns 'high', 'normal', or None."""
    for pattern in HIGH_PRIORITY_KEYWORDS:
        if re.search(pattern, title, re.IGNORECASE):
            return "high"
    for pattern in NORMAL_KEYWORDS:
        if re.search(pattern, title, re.IGNORECASE):
            return "normal"
    return None


def check_news():
    """Scan RSS feeds for relevant news. Summarize and rate MU sentiment via DeepSeek.

    First run: silently seeds all existing headlines as 'seen' (no alerts).
    Subsequent runs: alerts on each new article individually as it appears.
    """
    state = load_state()
    seen = set(state["seen_hashes"])
    first_run = state.get("seeded") is not True

    # Cleanup old hashes (keep last 500)
    if len(seen) > STATE_RETENTION["news_ids"]:
        state["seen_hashes"] = state["seen_hashes"][-(STATE_RETENTION["news_ids"] // 2):]
        seen = set(state["seen_hashes"])

    new_articles = []
    seen_urls = set(state.get("seen_urls", []))

    for feed_url, source_name in RSS_FEEDS:
        items = fetch_rss(feed_url, source_name)
        for item in items:
            title_hash = hash_headline(item["title"])
            url_hash = hash_url(item["link"])

            # Skip if we've seen this title OR this URL
            if title_hash in seen or (url_hash and url_hash in seen_urls):
                continue

            state["seen_hashes"].append(title_hash)
            seen.add(title_hash)
            if url_hash:
                state.setdefault("seen_urls", []).append(url_hash)
                seen_urls.add(url_hash)

            if first_run:
                continue

            priority = classify_headline(item["title"])
            if priority is None:
                continue

            new_articles.append({
                "title": item["title"],
                "source": item["source"],
                "link": item["link"],
                "priority": priority,
            })

    # Cleanup seen_urls list (keep last 300)
    if len(state.get("seen_urls", [])) > STATE_RETENTION["news_ids"]:
        state["seen_urls"] = state["seen_urls"][-300:]

    if first_run:
        state["seeded"] = True
        save_state(state)
        return

    # Send each new article as its own alert (max 5 per cycle to avoid spam)
    for article in new_articles[:5]:
        fetched = fetch_article(article["link"])
        analysis = summarize_article(article["title"], fetched["text"])

        sentiment = analysis["sentiment"]
        article["sentiment"] = sentiment  # Store for headline logging
        relevance = analysis["relevance"]

        # Skip LOW relevance articles for normal priority (only alert HIGH/MED)
        if article["priority"] == "normal" and relevance == "LOW":
            continue

        s_emoji = SENTIMENT_EMOJI.get(sentiment, "\U0001f7e1")
        r_emoji = RELEVANCE_EMOJI.get(relevance, "\u2022")

        if article["priority"] == "high":
            header = "\U0001f6a8"  # 🚨
        else:
            header = "\U0001f4f0"  # 📰

        link_line = f"\n\n\U0001f517 <a href=\"{article['link']}\">Read full article</a>" if article["link"] else ""
        msg = (
            f"{header} <b>{article['title']}</b>\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
            f"{analysis['summary']}\n\n"
            f"{s_emoji} <b>{sentiment}</b>  {r_emoji} {relevance}\n"
            f"<i>{article['source']}</i>{link_line}\n\n"
            f"<code>#NEWS</code>"
        )
        send_alert(msg, image_url=fetched.get("image"))

    # Log all new articles (with sentiment) for daily analysis
    if new_articles:
        _log_headlines(new_articles)

    save_state(state)


def _log_headlines(articles: list[dict]):
    """Append headlines to the rolling log for daily analysis."""
    log = []
    if os.path.exists(HEADLINES_LOG):
        try:
            with open(HEADLINES_LOG) as f:
                log = json.load(f)
        except Exception:
            log = []

    now = datetime.now(ZoneInfo("US/Eastern")).isoformat()
    for a in articles:
        log.append({
            "title": a["title"],
            "source": a["source"],
            "sentiment": a.get("sentiment", ""),
            "time": now,
        })

    # Keep last 50 headlines
    log = log[-50:]
    with open(HEADLINES_LOG, "w") as f:
        json.dump(log, f, indent=2)


def get_recent_headlines(hours: int = 24) -> list[dict]:
    """Get headlines from the last N hours for daily analysis."""
    if not os.path.exists(HEADLINES_LOG):
        return []
    try:
        with open(HEADLINES_LOG) as f:
            log = json.load(f)
        cutoff = datetime.now(ZoneInfo("US/Eastern")) - timedelta(hours=hours)
        recent = []
        for entry in log:
            try:
                t = datetime.fromisoformat(entry["time"])
                if t >= cutoff:
                    recent.append(entry)
            except Exception:
                recent.append(entry)  # include if can't parse time
        return recent
    except Exception:
        return []


if __name__ == "__main__":
    check_news()
