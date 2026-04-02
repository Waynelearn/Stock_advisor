"""Live conference/event tracker - monitors real-time social media during events.

Tracks events like NVIDIA GTC, earnings calls, and industry conferences.
During active events, scans Reddit, StockTwits, and YouTube for real-time
mentions that cross-reference event keywords with memory/MU-specific terms.
Uses DeepSeek to filter noise from substantive mentions.

Sources:
1. Reddit search API (multiple subreddits)
2. StockTwits ticker stream
3. YouTube RSS feeds for live/recent event content
4. Nitter/RSS proxy for Twitter/X search (best-effort, often blocked)

Pre-event: countdown alerts 24h and 1h before start.
During event: high-frequency scanning (every 2-3 min via cron) for
cross-referenced mentions (event keyword + memory keyword = HIGH priority).
"""

import json
import os
import re
import hashlib
import requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT, DEEPSEEK_API_KEY
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".event_tracker_state.json")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

REDDIT_HEADERS = {"User-Agent": "MU-Advisor-EventTracker/1.0"}

DEFAULT_SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "semiconductors",
    "nvidia",
    "investing",
]

# Nitter instances to try (public, rotate on failure)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.woodland.cafe",
]

# YouTube channels most likely to cover events live
EVENT_YOUTUBE_CHANNELS = [
    ("UCHuiy8bXnmK5nisYHUd1J5g", "NVIDIA"),
    ("UCBHcMCGaiJhv-ESTcWGJPcw", "NVIDIA Developer"),
    ("UCvJJ_dzjViJCoLf5uKUTwoA", "CNBC"),
    ("UCrp_UI8XtuYfpiqluWLD7Lw", "CNBC Television"),
    ("UCEAZeUIeJs0IjQiqTCdVSIg", "Yahoo Finance"),
    ("UCqoSrYgusd8ZddtMoWhjHYA", "Schwab Network"),
    ("UCK7tptUDHh-RYDsdxO1-5QQ", "Wall Street Journal"),
    ("UCNJ1Ymd5yFuUPtn21xtRbbw", "AI Explained"),
    ("UCawZsQWqfGSbCI5yjkdVkTA", "Matthew Berman"),
]

# =====================================================================
# Tracked Events Configuration
# =====================================================================

TRACKED_EVENTS = [
    {
        "name": "NVIDIA GTC 2026",
        "start": "2026-03-16",
        "end": "2026-03-19",
        "keywords": ["gtc", "nvidia gtc", "jensen", "blackwell", "rubin", "socamm", "lpdram", "inference chip"],
        "memory_keywords": ["micron", "hbm", "lpddr", "lpdram", "socamm", "memory", "dram"],
    },
    {
        "name": "MU Earnings Q2 FY2026",
        "start": "2026-03-18",
        "end": "2026-03-18",
        "keywords": ["micron earnings", "micron results", "mu earnings", "micron guidance"],
        "memory_keywords": ["hbm", "revenue", "margin", "guidance", "eps", "dram price"],
    },
    {
        "name": "FOMC March 2026",
        "start": "2026-03-18",
        "end": "2026-03-18",
        "keywords": ["fomc", "fed decision", "rate decision", "powell", "dot plot"],
        "memory_keywords": ["rate cut", "rate hike", "hold", "hawkish", "dovish"],
    },
]


# =====================================================================
# State Management
# =====================================================================

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "seen_hashes": [],
        "countdown_sent": {},
        "custom_events": [],
    }


def save_state(state: dict):
    # Keep seen_hashes bounded
    state["seen_hashes"] = state["seen_hashes"][-500:]
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def _content_hash(text: str) -> str:
    """Hash content for dedup. Normalize whitespace and case."""
    normalized = re.sub(r'\s+', ' ', text.strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# =====================================================================
# Event Helpers
# =====================================================================

def _get_all_events() -> list[dict]:
    """Return TRACKED_EVENTS + any dynamically added events from state."""
    state = load_state()
    custom = state.get("custom_events", [])
    return TRACKED_EVENTS + custom


def _get_active_events() -> list[dict]:
    """Return events whose date range includes today."""
    today = date.today()
    active = []
    for event in _get_all_events():
        try:
            start = date.fromisoformat(event["start"])
            end = date.fromisoformat(event["end"])
            if start <= today <= end:
                active.append(event)
        except (ValueError, KeyError):
            continue
    return active


def _get_upcoming_events(hours: int = 24) -> list[dict]:
    """Return events starting within the next N hours (but not yet started)."""
    now = datetime.now(ZoneInfo(TZ_ET))
    today = now.date()
    upcoming = []
    for event in _get_all_events():
        try:
            start = date.fromisoformat(event["start"])
            # Event starts in the future but within the horizon
            if start > today:
                hours_until = (datetime.combine(start, datetime.min.time()).replace(
                    tzinfo=ZoneInfo(TZ_ET)) - now).total_seconds() / 3600
                if 0 < hours_until <= hours:
                    event["_hours_until"] = hours_until
                    upcoming.append(event)
            elif start == today:
                # Started today - might still be upcoming if we consider early morning
                pass
        except (ValueError, KeyError):
            continue
    return upcoming


def _matches_keywords(text: str, keywords: list[str]) -> list[str]:
    """Check which keywords match in the text. Returns list of matched keywords."""
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        if kw.lower() in text_lower:
            matched.append(kw)
    return matched


# =====================================================================
# Source Scanners
# =====================================================================

def _scan_reddit(keywords: list, subreddits: list = None) -> list[dict]:
    """Search Reddit for recent posts matching keywords.

    Args:
        keywords: List of search terms
        subreddits: Subreddits to search (defaults to DEFAULT_SUBREDDITS)

    Returns:
        List of {"title": str, "body": str, "url": str, "score": int, "created": datetime}
    """
    if subreddits is None:
        subreddits = DEFAULT_SUBREDDITS

    results = []
    # Build OR query from keywords
    query = " OR ".join(f'"{kw}"' if " " in kw else kw for kw in keywords[:5])

    for subreddit in subreddits:
        try:
            search_url = f"https://www.reddit.com/r/{subreddit}/search.json"
            params = {
                "q": query,
                "sort": "new",
                "t": "hour",
                "limit": 10,
                "restrict_sr": "on",
            }
            resp = requests.get(
                search_url, params=params, headers=REDDIT_HEADERS, timeout=10
            )

            if resp.status_code == 429:
                print(f"[EVENT TRACKER] Reddit rate limited: r/{subreddit}")
                continue

            resp.raise_for_status()
            data = resp.json()

            children = data.get("data", {}).get("children", [])
            for child in children:
                post = child.get("data", {})
                created_utc = post.get("created_utc", 0)
                created_dt = datetime.fromtimestamp(created_utc, tz=ZoneInfo("UTC")) if created_utc else None

                results.append({
                    "title": post.get("title", "")[:300],
                    "body": post.get("selftext", "")[:500],
                    "url": f"https://reddit.com{post.get('permalink', '')}",
                    "score": post.get("score", 0),
                    "created": created_dt,
                    "subreddit": subreddit,
                    "num_comments": post.get("num_comments", 0),
                    "source": "reddit",
                })

        except Exception as e:
            print(f"[EVENT TRACKER] Reddit error r/{subreddit}: {e}")

    return results


def _scan_stocktwits(ticker: str) -> list[dict]:
    """Fetch recent StockTwits messages for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g., "MU")

    Returns:
        List of {"body": str, "created": datetime, "sentiment": str, "url": str, "source": str}
    """
    results = []
    try:
        url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        messages = data.get("messages", [])
        for msg in messages:
            body = msg.get("body", "")
            if not body:
                continue

            created_at = msg.get("created_at", "")
            created_dt = None
            if created_at:
                try:
                    created_dt = datetime.strptime(
                        created_at, "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=ZoneInfo("UTC"))
                except Exception:
                    pass

            sentiment_data = msg.get("entities", {}).get("sentiment", {})
            sentiment = sentiment_data.get("basic", "Unknown") if sentiment_data else "Unknown"

            msg_id = msg.get("id", "")
            results.append({
                "body": body[:500],
                "created": created_dt,
                "sentiment": sentiment,
                "url": f"https://stocktwits.com/message/{msg_id}" if msg_id else "",
                "source": "stocktwits",
            })

    except Exception as e:
        print(f"[EVENT TRACKER] StockTwits error {ticker}: {e}")

    return results


def _scan_twitter_nitter(keywords: list) -> list[dict]:
    """Search Twitter/X via Nitter RSS proxy instances.

    Best-effort: Nitter instances are frequently down. Try multiple,
    return whatever works.

    Returns:
        List of {"body": str, "url": str, "source": str, "created": datetime|None}
    """
    results = []
    query = " OR ".join(keywords[:3])  # Keep query short for Nitter
    query_encoded = requests.utils.quote(query)

    for base_url in NITTER_INSTANCES:
        try:
            rss_url = f"{base_url}/search/rss?f=tweets&q={query_encoded}"
            resp = requests.get(rss_url, timeout=8, headers={
                "User-Agent": "MU-Advisor-EventTracker/1.0"
            })
            if resp.status_code != 200:
                continue

            # Parse RSS
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title_el = item.find("title")
                link_el = item.find("link")
                pubdate_el = item.find("pubDate")
                desc_el = item.find("description")

                body = ""
                if title_el is not None and title_el.text:
                    body = title_el.text.strip()
                if desc_el is not None and desc_el.text:
                    # Strip HTML from description
                    desc_clean = re.sub(r'<[^>]+>', '', desc_el.text).strip()
                    if len(desc_clean) > len(body):
                        body = desc_clean

                if not body:
                    continue

                created_dt = None
                if pubdate_el is not None and pubdate_el.text:
                    try:
                        from email.utils import parsedate_to_datetime
                        created_dt = parsedate_to_datetime(pubdate_el.text)
                    except Exception:
                        pass

                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                # Convert nitter link to twitter link
                link = re.sub(r'https?://[^/]+/', 'https://x.com/', link, count=1)

                results.append({
                    "body": body[:500],
                    "url": link,
                    "source": "twitter",
                    "created": created_dt,
                })

            if results:
                break  # Got results from this instance, no need to try more

        except Exception as e:
            print(f"[EVENT TRACKER] Nitter {base_url} failed: {e}")
            continue

    return results


def _scan_youtube_event(keywords: list) -> list[dict]:
    """Check YouTube RSS feeds for recent videos matching event keywords.

    Returns:
        List of {"title": str, "url": str, "channel": str, "published": datetime|None, "source": str}
    """
    from xml.etree import ElementTree

    results = []
    now_utc = datetime.now(ZoneInfo("UTC"))
    cutoff = now_utc - timedelta(hours=6)  # Only videos from last 6 hours

    for channel_id, channel_name in EVENT_YOUTUBE_CHANNELS:
        try:
            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            resp = requests.get(feed_url, timeout=10)
            if resp.status_code != 200:
                continue

            root = ElementTree.fromstring(resp.text)
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "yt": "http://www.youtube.com/xml/schemas/2015",
            }

            for entry in root.findall("atom:entry", ns):
                video_id = entry.find("yt:videoId", ns)
                title = entry.find("atom:title", ns)
                published = entry.find("atom:published", ns)

                if video_id is None or title is None:
                    continue

                title_text = title.text or ""

                # Check if title matches any event keywords
                title_lower = title_text.lower()
                matched = [kw for kw in keywords if kw.lower() in title_lower]
                if not matched:
                    continue

                pub_dt = None
                if published is not None and published.text:
                    try:
                        pub_dt = datetime.fromisoformat(
                            published.text.replace("Z", "+00:00")
                        )
                    except Exception:
                        pass

                # Only include recent videos
                if pub_dt and pub_dt < cutoff:
                    continue

                url = f"https://www.youtube.com/watch?v={video_id.text}"
                results.append({
                    "title": title_text[:300],
                    "url": url,
                    "channel": channel_name,
                    "published": pub_dt,
                    "source": "youtube",
                })

        except Exception as e:
            print(f"[EVENT TRACKER] YouTube feed {channel_name}: {e}")

    return results


# =====================================================================
# DeepSeek Assessment
# =====================================================================

def _assess_mention(event_name: str, text: str, source: str) -> dict:
    """Use DeepSeek to quickly assess if a mention is substantive or noise.

    Returns:
        {"is_substantive": bool, "relevance": str, "summary": str}
    """
    prompt = (
        f"A social media post from {source} was found during the live event "
        f'"{event_name}". Assess if it contains SUBSTANTIVE, market-moving '
        f"information about memory/semiconductors or is just noise/hype.\n\n"
        f"Post: \"{text[:600]}\"\n\n"
        f"My position: 500x MU 380/400 bull call spread expiring March 20, 2026.\n\n"
        f"Respond in EXACTLY this format:\n"
        f"SUBSTANTIVE: YES or NO\n"
        f"RELEVANCE: HIGH, MEDIUM, or LOW\n"
        f"SUMMARY: One sentence on what this means for MU (under 50 words)"
    )

    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 100,
            },
            timeout=10,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse response
        is_sub = "SUBSTANTIVE: YES" in content.upper()
        relevance = "LOW"
        if "RELEVANCE: HIGH" in content.upper():
            relevance = "HIGH"
        elif "RELEVANCE: MEDIUM" in content.upper():
            relevance = "MEDIUM"

        summary_match = re.search(r'SUMMARY:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
        summary = summary_match.group(1).strip() if summary_match else "Assessment unavailable."

        return {
            "is_substantive": is_sub,
            "relevance": relevance,
            "summary": summary,
        }

    except Exception as e:
        print(f"[EVENT TRACKER] DeepSeek assessment failed: {e}")
        return {
            "is_substantive": True,  # Default to showing it if AI fails
            "relevance": "MEDIUM",
            "summary": "AI assessment unavailable - showing raw mention.",
        }


# =====================================================================
# Alert Formatting
# =====================================================================

def _format_time_ago(dt: datetime | None) -> str:
    """Format a datetime as 'X min ago' or 'X hours ago'."""
    if dt is None:
        return "recently"
    now_utc = datetime.now(ZoneInfo("UTC"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    delta = now_utc - dt
    minutes = int(delta.total_seconds() / 60)
    if minutes < 1:
        return "just now"
    elif minutes < 60:
        return f"{minutes} min ago"
    else:
        hours = minutes // 60
        return f"{hours}h ago"


def _send_live_event_alert(event: dict, mention: dict, assessment: dict, is_cross_ref: bool):
    """Send a live event alert to Telegram.

    Args:
        event: The tracked event dict
        mention: Source mention data (varies by source)
        assessment: DeepSeek assessment result
        is_cross_ref: True if mention matches BOTH event + memory keywords
    """
    event_name = event["name"]
    source = mention.get("source", "unknown")
    relevance = assessment["relevance"]

    # Priority indicator
    if is_cross_ref:
        priority_icon = "\U0001f534"  # red circle
        priority_label = "HIGH"
    elif relevance == "HIGH":
        priority_icon = "\U0001f7e0"  # orange circle
        priority_label = "HIGH"
    elif relevance == "MEDIUM":
        priority_icon = "\U0001f7e1"  # yellow circle
        priority_label = "MEDIUM"
    else:
        priority_icon = "\U0001f7e2"  # green circle
        priority_label = "LOW"

    # Source label
    source_labels = {
        "reddit": "Reddit",
        "stocktwits": "StockTwits",
        "twitter": "Twitter/X",
        "youtube": "YouTube",
    }
    source_label = source_labels.get(source, source.title())

    # Build source detail line
    time_ago = _format_time_ago(mention.get("created"))
    source_detail = f"Source: {source_label}"

    if source == "reddit":
        sub = mention.get("subreddit", "")
        score = mention.get("score", 0)
        if sub:
            source_detail = f"Source: Reddit r/{sub}"
        source_detail += f" ({time_ago}, {score} upvotes)"
    elif source == "stocktwits":
        st_sentiment = mention.get("sentiment", "")
        source_detail += f" ({time_ago}"
        if st_sentiment and st_sentiment != "Unknown":
            source_detail += f", {st_sentiment}"
        source_detail += ")"
    elif source == "twitter":
        source_detail += f" ({time_ago})"
    elif source == "youtube":
        channel = mention.get("channel", "")
        if channel:
            source_detail = f"Source: YouTube ({_escape_html(channel)}, {time_ago})"

    # Content text
    content = ""
    if source == "reddit":
        content = mention.get("title", "")
        body = mention.get("body", "")
        if body and len(body) > 20:
            content += f"\n{body[:200]}"
    elif source == "stocktwits":
        content = mention.get("body", "")
    elif source == "twitter":
        content = mention.get("body", "")
    elif source == "youtube":
        content = mention.get("title", "")

    content = _escape_html(content[:400])

    # Short event name for header
    short_name = event_name.split(" 20")[0] if " 20" in event_name else event_name

    # Link
    url = mention.get("url", "")
    link_line = f"\n\n\U0001f517 <a href=\"{url}\">View source</a>" if url else ""

    # Cross-reference highlight
    cross_ref_note = ""
    if is_cross_ref:
        cross_ref_note = "\n\U0001f4a1 <b>Cross-reference: event + memory keyword match</b>"

    msg = (
        f"{priority_icon} <b>{short_name} LIVE - MEMORY MENTION DETECTED</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        f"\U0001f4e1 {source_detail}\n"
        f"\"{content}\"\n\n"
        f"Relevance: <b>{priority_label}</b>\n"
        f"- {assessment['summary']}"
        f"{cross_ref_note}"
        f"{link_line}\n\n"
        f"<code>#EVENT_LIVE</code>"
    )

    send_alert(msg)


def _send_countdown_alert(event: dict, hours_until: float):
    """Send a pre-event countdown alert."""
    event_name = event["name"]
    start = event["start"]
    end = event["end"]

    # Format date range
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if start == end:
        date_range = start_date.strftime("%B %d, %Y")
    else:
        date_range = f"{start_date.strftime('%B %d')}-{end_date.strftime('%d, %Y')}"

    # Hours display
    if hours_until < 1.5:
        time_label = "1 hour"
        icon = "\U0001f534\u23f0"  # red circle + alarm
    elif hours_until < 4:
        time_label = f"{int(hours_until)} hours"
        icon = "\U0001f7e0\u23f0"  # orange circle + alarm
    else:
        time_label = f"{int(hours_until)} hours"
        icon = "\U0001f7e1\u23f0"  # yellow circle + alarm

    # Build watch items based on memory_keywords
    watch_items = []
    mem_kws = event.get("memory_keywords", [])
    event_kws = event.get("keywords", [])

    # Generate meaningful watch items from keyword combinations
    kw_map = {
        "hbm": "HBM4 roadmap updates and allocation announcements",
        "socamm": "SOCAMM 2 / LPDRAM module announcements",
        "lpdram": "LPDRAM design wins and specifications",
        "lpddr": "LPDDR5X/6 product reveals",
        "micron": "Micron name-drops or partnership mentions",
        "memory": "Memory architecture and capacity details",
        "dram": "DRAM pricing and demand signals",
        "revenue": "Revenue guidance and beat/miss",
        "margin": "Gross margin trajectory",
        "guidance": "Forward guidance and outlook",
        "eps": "EPS vs consensus expectations",
        "dram price": "DRAM pricing outlook",
        "rate cut": "Rate cut probability shift",
        "rate hike": "Rate hike risk signals",
        "hold": "Extended pause implications",
        "hawkish": "Hawkish surprise risk",
        "dovish": "Dovish tilt potential",
        "dot plot": "Dot plot rate path changes",
    }

    for kw in mem_kws:
        if kw.lower() in kw_map:
            watch_items.append(kw_map[kw.lower()])

    # Deduplicate and limit
    seen_items = set()
    unique_items = []
    for item in watch_items:
        if item not in seen_items:
            seen_items.add(item)
            unique_items.append(item)
    watch_items = unique_items[:5]

    if not watch_items:
        watch_items = [f"Mentions of: {', '.join(mem_kws[:4])}"]

    # Why it matters
    ticker = POSITION.get("ticker", "MU")
    if POSITION.get("contracts"):
        expiry = POSITION.get("expiry", "")
        strikes = f"{POSITION['long_strike']}/{POSITION['short_strike']}"
        why_matters = (
            f"Announcements could catalyze {ticker} move - "
            f"holding {POSITION['contracts']}x {strikes} spread "
            f"expiring {expiry}"
        )
    else:
        why_matters = f"Announcements could catalyze {ticker} move - no active position, watching for entry"

    watch_list = "\n".join(f"- {item}" for item in watch_items)

    msg = (
        f"{icon} <b>EVENT COUNTDOWN</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        f"<b>{_escape_html(event_name)}</b> starts in <b>{time_label}</b>\n"
        f"\U0001f4c5 {date_range}\n\n"
        f"\U0001f440 <b>What to watch for {ticker}:</b>\n"
        f"{watch_list}\n\n"
        f"\U0001f4a1 <b>Why it matters:</b>\n"
        f"{why_matters}\n\n"
        f"<code>#EVENT_LIVE</code>"
    )

    send_alert(msg)


# =====================================================================
# Main Entry Points
# =====================================================================

def check_live_events():
    """Main function: scan sources for mentions during active events.

    Called by run.py every 2-3 minutes during active events, or less
    frequently when no events are active.
    """
    state = load_state()
    seen_hashes = set(state.get("seen_hashes", []))

    # Check for upcoming event countdowns first
    check_event_countdown()

    # Reload state after countdown may have updated it
    state = load_state()
    seen_hashes = set(state.get("seen_hashes", []))

    # Find active events
    active_events = _get_active_events()
    if not active_events:
        return

    alerts_sent = 0
    max_alerts_per_cycle = 3  # Prevent flooding

    for event in active_events:
        if alerts_sent >= max_alerts_per_cycle:
            break

        event_kws = event.get("keywords", [])
        mem_kws = event.get("memory_keywords", [])
        all_kws = event_kws + mem_kws

        # Collect mentions from all sources
        mentions = []

        # 1-3: Reddit, StockTwits, Nitter — disabled (APIs blocked/403)
        # Re-enable when working API access is available

        # 4. YouTube live/recent
        try:
            yt_results = _scan_youtube_event(all_kws)
            mentions.extend(yt_results)
        except Exception as e:
            print(f"[EVENT TRACKER] YouTube scan failed: {e}")

        # Process mentions
        for mention in mentions:
            if alerts_sent >= max_alerts_per_cycle:
                break

            # Build content for dedup
            content_text = ""
            if mention.get("source") == "reddit":
                content_text = mention.get("title", "") + " " + mention.get("body", "")
            elif mention.get("source") == "youtube":
                content_text = mention.get("title", "")
            else:
                content_text = mention.get("body", "")

            if not content_text.strip():
                continue

            # Dedup check
            c_hash = _content_hash(content_text)
            if c_hash in seen_hashes:
                continue

            seen_hashes.add(c_hash)
            state["seen_hashes"].append(c_hash)

            # Check for cross-reference: event keyword + memory keyword
            event_matched = _matches_keywords(content_text, event_kws)
            memory_matched = _matches_keywords(content_text, mem_kws)
            is_cross_ref = bool(event_matched) and bool(memory_matched)

            # For non-cross-referenced mentions, require DeepSeek to confirm substantive
            # For cross-referenced mentions, always alert (but still get assessment)
            assessment = _assess_mention(
                event["name"], content_text, mention.get("source", "unknown")
            )

            if not is_cross_ref and not assessment["is_substantive"]:
                continue

            if not is_cross_ref and assessment["relevance"] == "LOW":
                continue

            _send_live_event_alert(event, mention, assessment, is_cross_ref)
            alerts_sent += 1

    save_state(state)


def check_event_countdown():
    """Check for upcoming events and send countdown alerts.

    Sends alerts at two thresholds:
    - 24 hours before event start
    - 1 hour before event start

    Only sends each threshold once per event.
    """
    state = load_state()
    countdown_sent = state.get("countdown_sent", {})

    upcoming = _get_upcoming_events(hours=25)  # Slightly wider window

    for event in upcoming:
        hours_until = event.get("_hours_until", 999)
        event_key = event["name"]

        sent_keys = countdown_sent.get(event_key, [])

        # 24-hour countdown
        if hours_until <= 24 and "24h" not in sent_keys:
            _send_countdown_alert(event, hours_until)
            sent_keys.append("24h")
            countdown_sent[event_key] = sent_keys

        # 1-hour countdown
        if hours_until <= 1 and "1h" not in sent_keys:
            _send_countdown_alert(event, hours_until)
            sent_keys.append("1h")
            countdown_sent[event_key] = sent_keys

    state["countdown_sent"] = countdown_sent
    save_state(state)


def add_event(name: str, start: str, end: str, keywords: list, memory_keywords: list):
    """Register a new event for tracking.

    Can be called by other modules (e.g., catalyst_fetcher) to dynamically
    add events as they are discovered.

    Args:
        name: Event name (e.g., "Samsung AI Forum 2026")
        start: Start date ISO format "YYYY-MM-DD"
        end: End date ISO format "YYYY-MM-DD"
        keywords: Event-specific search terms
        memory_keywords: Memory/MU-specific terms to cross-reference
    """
    # Validate dates
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date < start_date:
            print(f"[EVENT TRACKER] Invalid date range: {start} to {end}")
            return
    except ValueError as e:
        print(f"[EVENT TRACKER] Invalid date format: {e}")
        return

    state = load_state()
    custom_events = state.get("custom_events", [])

    # Check for duplicates
    for existing in custom_events:
        if existing.get("name") == name and existing.get("start") == start:
            print(f"[EVENT TRACKER] Event already registered: {name}")
            return

    # Also check against TRACKED_EVENTS
    for existing in TRACKED_EVENTS:
        if existing.get("name") == name and existing.get("start") == start:
            print(f"[EVENT TRACKER] Event already in TRACKED_EVENTS: {name}")
            return

    new_event = {
        "name": name,
        "start": start,
        "end": end,
        "keywords": keywords,
        "memory_keywords": memory_keywords,
    }

    custom_events.append(new_event)
    state["custom_events"] = custom_events
    save_state(state)

    print(f"[EVENT TRACKER] Registered new event: {name} ({start} to {end})")


if __name__ == "__main__":
    check_live_events()
