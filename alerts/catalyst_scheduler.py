"""Catalyst event reminders - fires alerts before scheduled events.

Also auto-scrapes and analyzes economic data (CPI, NFP, FOMC) when those events fire.
"""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import CATALYSTS, CATALYST_REMINDER_MINUTES, TZ_ET
from .bot import send_catalyst_reminder

STATE_FILE = os.path.join(os.path.dirname(__file__), ".catalyst_state.json")

# Event types that trigger auto-scrape + analysis.
# Order matters — first match wins, so peer-earnings keys must precede the
# generic "EARNINGS" trigger so e.g. "NVDA EARNINGS" doesn't fall through to MU.
AUTO_ANALYZE_KEYWORDS = {
    "CPI": "CPI",
    "PPI": "PPI",
    "PCE": "PCE",
    "NFP": "NFP",
    "FOMC RATE DECISION": "FOMC",
    "POWELL PRESS CONFERENCE": "FOMC",
    "NVDA EARNINGS": "PEER_NVDA",
    "AMD EARNINGS": "PEER_AMD",
    "AVGO EARNINGS": "PEER_AVGO",
    "MRVL EARNINGS": "PEER_MRVL",
    "TSM EARNINGS": "PEER_TSM",
    "INTC EARNINGS": "PEER_INTC",
    "MU EARNINGS": "EARNINGS",
}


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"alerted": []})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def check_catalysts():
    """Check if any catalyst reminders need to fire.

    Also triggers auto-scrape + analysis for CPI/NFP/FOMC events
    5 minutes after the event time (to allow data to be published).
    """
    state = load_state()
    now_et = datetime.now(ZoneInfo(TZ_ET))
    year = now_et.year

    for month, day, hour, minute, description in CATALYSTS:
        event_time = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(TZ_ET))

        # Standard reminders (60min and 15min before)
        for reminder_min in CATALYST_REMINDER_MINUTES:
            alert_key = f"{month}-{day}-{hour}:{minute}-{reminder_min}min"
            if alert_key in state["alerted"]:
                continue

            reminder_time = event_time - timedelta(minutes=reminder_min)
            # Fire if we're within 2 minutes of the reminder time
            if 0 <= (now_et - reminder_time).total_seconds() <= 120:
                send_catalyst_reminder(description, reminder_min)
                state["alerted"].append(alert_key)

        # Auto-scrape + analyze: 5 minutes after event time
        analyze_key = f"{month}-{day}-{hour}:{minute}-auto_analyze"
        if analyze_key not in state["alerted"]:
            analyze_time = event_time + timedelta(minutes=5)
            if 0 <= (now_et - analyze_time).total_seconds() <= 180:
                _auto_analyze_event(description)
                state["alerted"].append(analyze_key)

    save_state(state)


def _auto_analyze_event(description: str):
    """Auto-scrape reports and run DeepSeek analysis.

    - CPI/NFP: scrapes BLS, runs roundtable analysis
    - FOMC: scrapes latest + previous statement, does diff analysis
    - MU EARNINGS: scrapes Micron IR press release, full roundtable
    """
    event_type = None
    desc_upper = description.upper()
    for keyword, etype in AUTO_ANALYZE_KEYWORDS.items():
        if keyword in desc_upper:
            event_type = etype
            break

    if not event_type:
        return

    try:
        if event_type == "FOMC":
            from .economic_data import analyze_fomc_with_diff
            analyze_fomc_with_diff()
        elif event_type == "EARNINGS":
            from .economic_data import analyze_mu_earnings
            analyze_mu_earnings()
        elif event_type.startswith("PEER_"):
            from .economic_data import analyze_peer_earnings
            ticker = event_type.split("_", 1)[1]
            analyze_peer_earnings(ticker)
        else:
            from .economic_data import fetch_latest_economic_data, analyze_economic_release
            scraped = fetch_latest_economic_data(event_type)
            if scraped:
                analyze_economic_release(event_type, scraped)
            else:
                from .bot import send_alert
                send_alert(
                    f"\u26a0\ufe0f <b>{event_type} SCRAPE FAILED</b>\n"
                    f"Could not retrieve the {event_type} report from the source. "
                    f"The page format may have changed or the site is down. "
                    f"Check manually."
                )
    except Exception as e:
        print(f"[AUTO-ANALYZE ERROR] {event_type}: {e}")
        try:
            from .bot import send_alert
            send_alert(
                f"\u26a0\ufe0f <b>{event_type} ANALYSIS ERROR</b>\n"
                f"Failed to auto-analyze {event_type}: {e}\n"
                f"Check logs and source pages manually."
            )
        except Exception:
            pass


if __name__ == "__main__":
    check_catalysts()
