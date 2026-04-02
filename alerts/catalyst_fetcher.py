"""Auto-fetch catalyst calendar from web sources + yfinance.

Sources:
1. Economic calendar: Guggenheim (CPI, NFP, PPI, PCE, ISM, Employment)
2. FOMC dates: Federal Reserve website (regex + DeepSeek AI fallback)
3. Earnings: yfinance .calendar for MU + peers
4. Options expiry: computed (3rd Friday of month)
5. Industry events: manual overrides only (conferences like GTC, SEMICON)

All web scraping uses DeepSeek AI as the parser, making it resilient to
HTML format changes. If a page restructures, DeepSeek still extracts the data.

Caches results in .catalyst_cache.json, refreshes weekly.
Exports get_catalysts() returning list of (month, day, hour, minute, description) tuples
matching the format used by catalyst_scheduler, daily_briefing, etc.
"""

import json
import os
import re
import calendar
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from .config import DEEPSEEK_API_KEY, TZ_ET, POSITION, PEERS

CACHE_FILE = os.path.join(os.path.dirname(__file__), ".catalyst_cache.json")
CACHE_MAX_AGE_DAYS = 7

# Tickers to check earnings for (MU + peers + key supply chain)
EARNINGS_TICKERS = ["MU"] + [p for p in PEERS if p != "SOXX"] + ["TSM", "INTC"]

# Manual overrides: events that can't be auto-fetched (conferences, custom notes)
# Format: (month, day, hour_ET, minute_ET, description)
# Keep this small - only things no API can provide
MANUAL_EVENTS = [
    # NVIDIA GTC 2026 (March 16-19, San Jose) - annual, update yearly
    (3, 16, 9, 0, "NVIDIA GTC 2026 OPENS - Jensen keynote expected"),
    (3, 17, 9, 0, "NVIDIA GTC Day 2 - Sessions & announcements"),
    (3, 18, 9, 0, "NVIDIA GTC Day 3 - Sessions continue"),
    (3, 19, 9, 0, "NVIDIA GTC Day 4 - Final day"),
    # SEMICON events - update from semi.org/en/expositions-events/calendar
    (3, 25, 9, 0, "SEMICON China 2026 (Shanghai, Mar 25-27)"),
    (5, 5, 9, 0, "SEMICON Southeast Asia 2026 (KL, May 5-7)"),
    (10, 13, 9, 0, "SEMICON West 2026 (San Francisco, Oct 13-15)"),
    (11, 18, 9, 0, "SEMICON Europa 2026 (Munich, Nov 18-21)"),
    (12, 9, 9, 0, "SEMICON Japan 2026 (Tokyo, Dec 9-11)"),
]

# Known economic release times (ET) - most releases are 8:30 AM
RELEASE_TIMES = {
    "employment": (8, 30),
    "nfp": (8, 30),
    "cpi": (8, 30),
    "ppi": (8, 30),
    "pce": (8, 30),
    "retail sales": (8, 30),
    "import": (8, 30),
    "export": (8, 30),
    "jobless": (8, 30),
    "gdp": (8, 30),
    "ism": (10, 0),
    "jolts": (10, 0),
    "michigan": (10, 0),
    "consumer sentiment": (10, 0),
    "fomc": (14, 0),
    "fed": (14, 0),
}

# Tags for MU relevance ranking
HIGH_RELEVANCE = ["fomc", "cpi", "ppi", "nfp", "employment situation", "pce", "fed"]


def _load_cache() -> dict | None:
    """Load cache if fresh enough."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        cached_date = datetime.fromisoformat(cache["fetched_at"])
        if (datetime.now(ZoneInfo(TZ_ET)) - cached_date).days < CACHE_MAX_AGE_DAYS:
            return cache
    except Exception:
        pass
    return None


def _save_cache(data: dict):
    data["fetched_at"] = datetime.now(ZoneInfo(TZ_ET)).isoformat()
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _get_release_time(event_name: str) -> tuple[int, int]:
    """Guess the release time based on event name."""
    name_lower = event_name.lower()
    for keyword, (h, m) in RELEASE_TIMES.items():
        if keyword in name_lower:
            return (h, m)
    return (8, 30)  # default


def _get_relevance_tag(event_name: str) -> str:
    """Tag event with relevance level for MU."""
    name_lower = event_name.lower()
    for kw in HIGH_RELEVANCE:
        if kw in name_lower:
            return "KEY"
    return ""


def _deepseek_extract(prompt: str, max_tokens: int = 3000) -> str | None:
    """Call DeepSeek to extract structured data from text. Returns raw content."""
    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Clean markdown fences if present
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        return content
    except Exception as e:
        print(f"[CATALYST FETCH] DeepSeek call failed: {e}")
        return None


def _html_to_text(html: str) -> str:
    """Strip HTML to plain text for AI parsing."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# =====================================================================
# Source 1: Guggenheim Economic Calendar (structured, reliable)
# =====================================================================

def _fetch_economic_calendar_guggenheim() -> list[tuple]:
    """Scrape Guggenheim's economic calendar page, parse with DeepSeek."""
    try:
        resp = requests.get(
            "https://www.guggenheiminvestments.com/services/advisor-resources/us-economic-calendar",
            headers={"User-Agent": "MU-Advisor-Bot/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        text = _html_to_text(resp.text)[:8000]
    except Exception as e:
        print(f"[CATALYST FETCH] Guggenheim scrape failed: {e}")
        return []

    now = datetime.now(ZoneInfo(TZ_ET))
    prompt = (
        f"Extract ALL US economic release dates from this Guggenheim calendar. "
        f"The current year is {now.year}. Today is {now.strftime('%B %d, %Y')}.\n"
        f"Return ONLY a JSON array of objects with these fields:\n"
        f'  "month": int (1-12),\n'
        f'  "day": int,\n'
        f'  "event": string (short name like "CPI January", "NFP February", "FOMC Rate Decision")\n\n'
        f"Include: Employment/NFP, CPI, PPI, PCE, ISM Manufacturing, FOMC, Retail Sales.\n"
        f"Skip: weekly claims, Treasury auctions, housing data, Beige Book.\n"
        f"Only include events from {now.strftime('%B %Y')} onward.\n\n"
        f"Calendar text:\n{text}\n\n"
        f"Return ONLY the JSON array, no markdown, no explanation."
    )

    content = _deepseek_extract(prompt)
    if not content:
        return []

    try:
        events = json.loads(content)
        results = []
        for e in events:
            month = int(e["month"])
            day = int(e["day"])
            name = e["event"]
            hour, minute = _get_release_time(name)
            tag = _get_relevance_tag(name)
            desc = f"{name} - {tag}" if tag else name
            results.append((month, day, hour, minute, desc))
        return results
    except Exception as e:
        print(f"[CATALYST FETCH] Economic calendar parse failed: {e}")
        return []


# =====================================================================
# Source 2: FOMC dates from Federal Reserve (AI-parsed)
# =====================================================================

def _fetch_fomc_dates() -> list[tuple]:
    """Scrape FOMC meeting dates from the Fed website, parse with DeepSeek AI."""
    try:
        resp = requests.get(
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            headers={"User-Agent": "MU-Advisor-Bot/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        text = _html_to_text(resp.text)[:10000]
    except Exception as e:
        print(f"[CATALYST FETCH] Fed FOMC scrape failed: {e}")
        return []

    now = datetime.now(ZoneInfo(TZ_ET))
    prompt = (
        f"Extract ALL FOMC meeting dates from this Federal Reserve calendar page. "
        f"The current year is {now.year}. Today is {now.strftime('%B %d, %Y')}.\n\n"
        f"Return ONLY a JSON array of objects with these fields:\n"
        f'  "month": int (1-12),\n'
        f'  "day1": int (first day of meeting),\n'
        f'  "day2": int (second day - decision day),\n'
        f'  "has_sep": boolean (true if meeting includes Summary of Economic Projections / dot plot, usually marked with *)\n\n'
        f"Include meetings from {now.year} and {now.year + 1}.\n"
        f"Only include meetings that haven't happened yet (after {now.strftime('%B %d, %Y')}).\n\n"
        f"Page text:\n{text}\n\n"
        f"Return ONLY the JSON array, no markdown, no explanation."
    )

    content = _deepseek_extract(prompt, max_tokens=1000)
    if not content:
        return []

    try:
        meetings = json.loads(content)
        results = []
        for m in meetings:
            month = int(m["month"])
            day2 = int(m["day2"])
            has_sep = m.get("has_sep", False)

            sep_tag = " + DOT PLOT + SEP" if has_sep else ""
            results.append((month, day2, 14, 0, f"FOMC RATE DECISION{sep_tag}"))
            results.append((month, day2, 14, 30, "POWELL PRESS CONFERENCE"))
        return results
    except Exception as e:
        print(f"[CATALYST FETCH] FOMC parse failed: {e}")
        return []


# =====================================================================
# Source 3: Earnings dates from yfinance
# =====================================================================

def _fetch_earnings_dates() -> list[tuple]:
    """Get upcoming earnings dates for MU and peers via yfinance."""
    results = []
    now = datetime.now(ZoneInfo(TZ_ET))

    for ticker in EARNINGS_TICKERS:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None:
                continue

            # yfinance returns dict with "Earnings Date" as list of date objects
            earnings_date = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed:
                    if isinstance(ed, list) and len(ed) > 0:
                        earnings_date = ed[0]
                    else:
                        earnings_date = ed
            elif hasattr(cal, 'iloc'):
                try:
                    earnings_date = cal.iloc[0, 0]
                except Exception:
                    pass

            if earnings_date is None:
                continue

            # Convert to date - handle datetime.date, Timestamp, string
            if isinstance(earnings_date, date):
                ed = earnings_date
            elif hasattr(earnings_date, 'date') and callable(earnings_date.date):
                ed = earnings_date.date()
            elif isinstance(earnings_date, str):
                ed = datetime.strptime(earnings_date, "%Y-%m-%d").date()
            else:
                continue

            if ed < now.date():
                continue

            # Default to 16:15 ET (after market close)
            hour, minute = 16, 15
            label = f"{ticker} EARNINGS"
            if ticker == "MU":
                label = "MU EARNINGS - THE CATALYST"

            results.append((ed.month, ed.day, hour, minute, label))
        except Exception as e:
            print(f"[CATALYST FETCH] {ticker} earnings lookup failed: {e}")
            continue

    return results


# =====================================================================
# Source 4: Options expiry dates (computed)
# =====================================================================

def _compute_expiry_dates() -> list[tuple]:
    """Compute monthly options expiry dates (3rd Friday of each month)."""
    results = []
    now = datetime.now(ZoneInfo(TZ_ET))
    pos_expiry = POSITION.get("expiry")

    for month_offset in range(12):
        month = now.month + month_offset
        year = now.year
        if month > 12:
            month -= 12
            year += 1

        cal = calendar.monthcalendar(year, month)
        fridays = [week[4] for week in cal if week[4] != 0]
        if len(fridays) >= 3:
            third_friday = fridays[2]
        else:
            continue

        exp_date = date(year, month, third_friday)
        if exp_date <= now.date():
            continue

        desc = "Monthly Options Expiry"
        if pos_expiry and exp_date.isoformat() == pos_expiry:
            desc = "SPREAD EXPIRY - Settlement"

        results.append((month, third_friday, 16, 0, desc))

    return results


# =====================================================================
# Main: get_catalysts() - drop-in replacement for config.CATALYSTS
# =====================================================================

def refresh_cache() -> list[tuple]:
    """Fetch all sources and rebuild the catalyst cache."""
    all_events = []

    # 1. Economic calendar
    econ = _fetch_economic_calendar_guggenheim()
    all_events.extend(econ)
    print(f"[CATALYST FETCH] Economic calendar: {len(econ)} events")

    # 2. FOMC dates
    fomc = _fetch_fomc_dates()
    all_events.extend(fomc)
    print(f"[CATALYST FETCH] FOMC: {len(fomc)} events")

    # 3. Earnings
    earnings = _fetch_earnings_dates()
    all_events.extend(earnings)
    print(f"[CATALYST FETCH] Earnings: {len(earnings)} events")

    # 4. Options expiry
    expiry = _compute_expiry_dates()
    all_events.extend(expiry)
    print(f"[CATALYST FETCH] Expiry dates: {len(expiry)} events")

    # 5. Manual events (conferences, custom)
    now = datetime.now(ZoneInfo(TZ_ET))
    manual_count = 0
    for evt in MANUAL_EVENTS:
        try:
            evt_date = date(now.year, evt[0], evt[1])
            if evt_date >= now.date():
                all_events.append(evt)
                manual_count += 1
        except ValueError:
            continue
    print(f"[CATALYST FETCH] Manual events: {manual_count}")

    # Deduplicate: same date/time + overlapping description
    seen_keys = set()
    deduped = []
    for evt in all_events:
        # Normalize: collapse "FOMC Rate Decision - KEY" and "FOMC RATE DECISION + DOT PLOT"
        desc_norm = evt[4].lower().replace(" - key", "").replace(" + dot plot + sep", "").strip()
        key = (evt[0], evt[1], evt[2], evt[3], desc_norm[:20])
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(evt)
        else:
            # If duplicate but new one has more info (longer desc), prefer it
            for i, existing in enumerate(deduped):
                ex_norm = existing[4].lower().replace(" - key", "").replace(" + dot plot + sep", "").strip()
                ex_key = (existing[0], existing[1], existing[2], existing[3], ex_norm[:20])
                if ex_key == key and len(evt[4]) > len(existing[4]):
                    deduped[i] = evt
                    break

    # Sort by date
    deduped.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    # Save cache
    cache_data = {
        "catalysts": [list(e) for e in deduped],
        "counts": {
            "economic": len(econ),
            "fomc": len(fomc),
            "earnings": len(earnings),
            "expiry": len(expiry),
            "manual": manual_count,
            "total": len(deduped),
        },
    }
    _save_cache(cache_data)
    print(f"[CATALYST FETCH] Total: {len(deduped)} catalysts cached")

    return [tuple(e) for e in deduped]


def get_catalysts() -> list[tuple]:
    """Get the catalyst list. Uses cache if fresh, otherwise fetches.

    Returns list of (month, day, hour_ET, minute_ET, description) tuples.
    Drop-in replacement for the old config.CATALYSTS hardcoded list.
    """
    cache = _load_cache()
    if cache and "catalysts" in cache:
        return [tuple(e) for e in cache["catalysts"]]

    # Cache is stale or missing - refresh
    try:
        return refresh_cache()
    except Exception as e:
        print(f"[CATALYST FETCH] Refresh failed: {e}")
        # Fallback: return manual events only
        return list(MANUAL_EVENTS)


def add_discovered_catalyst(month: int, day: int, event: str):
    """Add a catalyst discovered from news/YouTube content.

    Called by summarizer.py when DeepSeek identifies a future dated event
    in an article. Persists to cache so it appears in future catalyst lists.
    """
    now = datetime.now(ZoneInfo(TZ_ET))

    # Validate date
    try:
        evt_date = date(now.year, month, day)
        if evt_date < now.date():
            return  # Past event, skip
    except ValueError:
        return

    # Load existing cache
    if not os.path.exists(CACHE_FILE):
        return  # No cache yet, skip (will be picked up on next refresh)

    try:
        with open(CACHE_FILE) as f:
            cache = json.load(f)
    except Exception:
        return

    catalysts = cache.get("catalysts", [])

    # Check if already exists (same date + similar event name)
    event_lower = event.lower()[:25]
    for existing in catalysts:
        if existing[0] == month and existing[1] == day:
            if existing[4].lower()[:25] == event_lower:
                return  # Already have this

    # Guess time and add
    hour, minute = _get_release_time(event)
    new_cat = [month, day, hour, minute, f"{event} (discovered)"]
    catalysts.append(new_cat)
    catalysts.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    cache["catalysts"] = catalysts
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"[CATALYST DISCOVERY] Added: {month}/{day} - {event}")

    # Reset the config cache so it picks up the new catalyst
    from . import config
    config._catalysts_cache = None


if __name__ == "__main__":
    catalysts = refresh_cache()
    print(f"\n{'='*60}")
    print(f"Fetched {len(catalysts)} catalysts:")
    print(f"{'='*60}")
    for c in catalysts:
        print(f"  {c[0]:2d}/{c[1]:2d} {c[2]:2d}:{c[3]:02d} ET  {c[4]}")
