#!/usr/bin/env python3
"""
MU Advisor Alert Runner - called by cron every minute.

Checks:
1. Price level crossings (MU and VIX) with smart WHY analysis
2. Big intraday moves (MU, peers, futures)
3. Spread P&L thresholds
4. Catalyst reminders (60min and 15min before events, incl GTC/peer earnings)
5. News scanner (RSS feeds for MU, NVDA, AMD, AVGO, MRVL headlines)
6. Daily briefing (8 PM SGT) with futures, peers, spread value
7. Morning recap (7 AM SGT) with session review
8. Options flow (unusual vol, OI changes, PCR, IV, liquidity)
9. Analyst rating tracker
10. Market intel (short interest, sector rotation, peer sympathy)
11. Weekend war room (Saturday 9 AM SGT)
12. Correlation monitor (MU-SOX divergence, VIX anomaly)
13. Volume anomaly detector (dark pool proxy)
14. Asia session tracker (SK Hynix, Samsung, ASML)
15. Insider & congressional trade tracker
16. Social sentiment (StockTwits, Reddit)
17. Expiry week escalation (ramp up monitoring in final 5 days)
18. Macro dashboard (VIX, yields, DXY, oil, F&G, credit, sector rotation)
19. Sector rotation tracker (SOXX vs sectors, risk-on/off detection)
20. ETF flow monitor (passive MU buying/selling pressure from ETF flows)
21. Gap monitor (overnight futures gaps, pre-market MU, estimated open)
22. Short interest tracker (SI changes, days-to-cover, squeeze potential)
23. Estimate tracker (EPS/revenue consensus revisions, PT changes)
24. Geopolitical monitor (Taiwan, Korea, US-China, Middle East supply chain risks)
25. Event live tracker (real-time GTC/earnings/FOMC social media monitoring)
26. Weekend crypto sentiment (BTC/ETH as Monday preview signal)
27. Sunday futures open (first look at week-ahead, Asia Monday preview)
28. Weekend news digest (batched Saturday-Sunday coverage)
29. Week ahead preview (catalysts, earnings, position health check)
30. Weekend social sentiment (Reddit/StockTwits weekend conviction tracker)
31. Expiry recap (Saturday morning options/spread review)
32. FX monitor (USD/JPY, USD/KRW, DXY impact on semis)
33. Prediction market (Polymarket odds for Fed, geopolitical events)
34. Oil price tracker (WTI/Brent monitoring, level crossings, MU impact est.)
35. Memory pricing (DRAM/NAND proxy tracking, MU vs peers momentum, divergence)
36. Hyperscaler capex signals (MSFT/GOOGL/META/AMZN demand score, divergence)
37. Supply chain monitor (shipping rates, helium/gas supply, fab input costs)

Enhanced existing modules:
- Geopolitical: +oil_energy category, +chips_act category, +China retaliation, +Taiwan/TSMC
- FX monitor: +yen carry trade unwind detection
- Macro dashboard: +credit spreads (JNK), +power grid (GRID, NLR), +oil levels to $130
- ETF flows: +quarterly rebalance dates
- Options flow: +GEX/dealer gamma positioning estimation
- Social sentiment: +retail capitulation/FOMO detection
- Estimate tracker: +AI demand signal tickers (ORCL, ADBE, HPE, etc.)

Only makes API calls during relevant hours to avoid rate limits.
"""

import sys
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts.config import (
    TZ_ET, TZ_SGT, PREMARKET_HOUR, AFTERHOURS_END_HOUR,
    POSITION, EXPIRY_ESCALATION_DAYS,
)
from alerts.price_monitor import check_prices, is_market_hours, is_extended_hours
from alerts.catalyst_scheduler import check_catalysts
from alerts.daily_briefing import send_briefing, send_morning_recap
from alerts.news_scanner import check_news
from alerts.options_flow import analyze_and_alert as check_options_flow
from alerts.analyst_tracker import check_analyst_changes
from alerts.market_intel import check_short_interest, check_sector_rotation, check_peer_sympathy
from alerts.weekend_warroom import send_war_room
from alerts.correlation_monitor import check_mu_sox_divergence, check_vix_correlation_breakdown
from alerts.volume_analyzer import check_volume_anomaly
from alerts.asia_tracker import check_asia_session
from alerts.insider_tracker import check_insider_trades, check_congressional_trades
from alerts.social_sentiment import check_social_sentiment, check_retail_positioning
from alerts.spread_recommender import send_spread_recommendations
from alerts.youtube_monitor import check_youtube_news
from alerts.macro_dashboard import check_macro_alerts, send_dashboard
from alerts.sector_rotation import check_sector_rotation as check_sector_rotation_new
from alerts.etf_flows import check_etf_flows
from alerts.gap_monitor import check_overnight_gaps, check_premarket_mu
from alerts.short_tracker import check_short_updates
from alerts.estimate_tracker import check_estimate_changes
from alerts.geopolitical_monitor import check_geopolitical_risks
from alerts.event_tracker import check_live_events
from alerts.weekend_crypto import check_weekend_crypto
from alerts.sunday_futures import check_sunday_futures_open, check_monday_asia_preview
from alerts.weekend_digest import send_weekend_digest
from alerts.week_ahead import send_week_ahead
from alerts.weekend_social import check_weekend_social
from alerts.expiry_recap import send_expiry_recap
from alerts.fx_monitor import check_fx_moves
from alerts.prediction_market import check_prediction_markets
from alerts.oil_tracker import check_oil_prices
from alerts.memory_pricing import check_memory_pricing
from alerts.hyperscaler_tracker import check_hyperscaler_signals
from alerts.supply_chain import check_supply_chain

BRIEFING_STATE_FILE = os.path.join(os.path.dirname(__file__), ".briefing_state.json")


def days_to_expiry() -> int:
    """Calculate trading days until spread expiry."""
    if not POSITION.get("expiry"):
        return 999  # No active position
    today = date.today()
    expiry = date.fromisoformat(POSITION["expiry"])
    delta = (expiry - today).days
    return max(delta, 0)


def is_expiry_week() -> bool:
    """Check if we're in the final escalation window."""
    return days_to_expiry() <= EXPIRY_ESCALATION_DAYS


def should_check_prices() -> bool:
    """Only check prices during market + extended hours on weekdays."""
    return is_market_hours() or is_extended_hours()


def should_send_briefing() -> bool:
    """Send daily briefing at 8 PM SGT (once per day)."""
    import json
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")

    # Only at 8 PM hour SGT on weekdays (Mon-Fri)
    if now_sgt.hour != 20 or now_sgt.weekday() >= 5:
        return False

    # Check if already sent today
    if os.path.exists(BRIEFING_STATE_FILE):
        with open(BRIEFING_STATE_FILE) as f:
            state = json.load(f)
            if state.get("last_briefing_date") == today:
                return False

    return True


def should_send_war_room() -> bool:
    """Send weekend war room at 9 AM SGT on Saturday (once per week)."""
    import json
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")

    # Saturday at 9 AM SGT
    if now_sgt.weekday() != 5 or now_sgt.hour != 9:
        return False

    if os.path.exists(BRIEFING_STATE_FILE):
        with open(BRIEFING_STATE_FILE) as f:
            state = json.load(f)
            if state.get("last_warroom_date") == today:
                return False

    return True


def should_send_morning_recap() -> bool:
    """Send morning recap at 7 AM SGT (once per day, Tue-Sat = after Mon-Fri sessions)."""
    import json
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")

    # 7 AM SGT, Tue-Sat (recap of Mon-Fri US sessions)
    if now_sgt.hour != 7 or now_sgt.weekday() == 0 or now_sgt.weekday() == 6:
        return False

    if os.path.exists(BRIEFING_STATE_FILE):
        with open(BRIEFING_STATE_FILE) as f:
            state = json.load(f)
            if state.get("last_recap_date") == today:
                return False

    return True


def should_check_asia() -> bool:
    """Check Asia session at 5 PM SGT on weekdays (once per day)."""
    import json
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")

    if now_sgt.hour != 17 or now_sgt.weekday() >= 5:
        return False

    if os.path.exists(BRIEFING_STATE_FILE):
        with open(BRIEFING_STATE_FILE) as f:
            state = json.load(f)
            if state.get("last_asia_date") == today:
                return False

    return True


def mark_briefing_sent(key: str = "last_briefing_date"):
    """Record that a briefing/recap was sent today."""
    import json
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    today = now_sgt.strftime("%Y-%m-%d")

    state = {}
    if os.path.exists(BRIEFING_STATE_FILE):
        with open(BRIEFING_STATE_FILE) as f:
            state = json.load(f)

    state[key] = today
    with open(BRIEFING_STATE_FILE, "w") as f:
        json.dump(state, f)


def main():
    now_et = datetime.now(ZoneInfo(TZ_ET))
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    expiry_mode = is_expiry_week()

    # Always check catalysts (they can happen outside market hours)
    try:
        check_catalysts()
    except Exception as e:
        print(f"[CATALYST ERROR] {e}")

    # Check prices during market/extended hours
    if should_check_prices():
        try:
            check_prices()
        except Exception as e:
            print(f"[PRICE ERROR] {e}")

    # Scan news every cycle (RSS feeds are lightweight)
    try:
        check_news()
    except Exception as e:
        print(f"[NEWS ERROR] {e}")

    # --- OPTIONS FLOW ---
    # Normal: every 5 min during market hours
    # Expiry week: every minute during market hours
    flow_interval = 1 if expiry_mode else 5
    if is_market_hours() and now_et.minute % flow_interval == 0:
        try:
            check_options_flow()
        except Exception as e:
            print(f"[OPTIONS FLOW ERROR] {e}")

    # --- ANALYST TRACKER ---
    # Normal: every 30 min | Expiry week: every 15 min
    analyst_interval = 15 if expiry_mode else 30
    if should_check_prices() and now_et.minute % analyst_interval == 0:
        try:
            check_analyst_changes()
        except Exception as e:
            print(f"[ANALYST ERROR] {e}")

    # --- MARKET INTEL ---
    # Short interest + sector rotation + peer sympathy - once daily at 10 AM ET
    if is_market_hours() and now_et.hour == 10 and now_et.minute == 0:
        try:
            check_short_interest()
        except Exception as e:
            print(f"[SHORT INTEREST ERROR] {e}")
        try:
            check_sector_rotation()
        except Exception as e:
            print(f"[SECTOR ROTATION ERROR] {e}")
        try:
            check_peer_sympathy()
        except Exception as e:
            print(f"[PEER SYMPATHY ERROR] {e}")

    # --- CORRELATION MONITOR ---
    # MU-SOX divergence + VIX anomaly - every 15 min during market hours
    if is_market_hours() and now_et.minute % 15 == 0:
        try:
            check_mu_sox_divergence()
        except Exception as e:
            print(f"[DIVERGENCE ERROR] {e}")
        try:
            check_vix_correlation_breakdown()
        except Exception as e:
            print(f"[VIX ANOMALY ERROR] {e}")

    # --- VOLUME ANOMALY ---
    # Check every 30 min during market hours (needs accumulated volume)
    if is_market_hours() and now_et.minute % 30 == 0:
        try:
            check_volume_anomaly()
        except Exception as e:
            print(f"[VOLUME ERROR] {e}")

    # --- ASIA SESSION ---
    # Check at 5 PM SGT on weekdays
    if should_check_asia():
        try:
            check_asia_session()
            mark_briefing_sent("last_asia_date")
        except Exception as e:
            print(f"[ASIA TRACKER ERROR] {e}")

    # --- INSIDER & CONGRESSIONAL TRADES ---
    # Once daily at 11 AM ET (filings typically posted by then)
    if is_market_hours() and now_et.hour == 11 and now_et.minute == 0:
        try:
            check_insider_trades()
        except Exception as e:
            print(f"[INSIDER ERROR] {e}")
        try:
            check_congressional_trades()
        except Exception as e:
            print(f"[CONGRESS ERROR] {e}")

    # --- SOCIAL SENTIMENT ---
    # Every 2 hours during market hours | Expiry week: every hour
    social_interval = 60 if expiry_mode else 120
    if should_check_prices() and (now_et.hour * 60 + now_et.minute) % social_interval == 0:
        try:
            check_social_sentiment()
        except Exception as e:
            print(f"[SOCIAL ERROR] {e}")
        try:
            check_retail_positioning()
        except Exception as e:
            print(f"[RETAIL POSITIONING ERROR] {e}")

    # --- YOUTUBE NEWS ---
    # Every 10 min normally, every 5 min during market hours
    yt_interval = 5 if is_market_hours() else 10
    if now_et.minute % yt_interval == 0:
        try:
            check_youtube_news()
        except Exception as e:
            print(f"[YOUTUBE ERROR] {e}")

    # --- MACRO DASHBOARD ---
    # Threshold alerts: every 15 min during market hours
    if is_market_hours() and now_et.minute % 15 == 0:
        try:
            check_macro_alerts()
        except Exception as e:
            print(f"[MACRO ALERT ERROR] {e}")

    # Daily dashboard: 7:30 PM SGT (30 min before briefing for macro context)
    if now_sgt.hour == 19 and now_sgt.minute == 30 and now_sgt.weekday() < 5:
        try:
            send_dashboard()
        except Exception as e:
            print(f"[MACRO DASH ERROR] {e}")

    # Daily briefing at 8 PM SGT (pre-market)
    if should_send_briefing():
        try:
            send_briefing()
            mark_briefing_sent("last_briefing_date")
        except Exception as e:
            print(f"[BRIEFING ERROR] {e}")

        # Spread recommendations alongside daily briefing
        try:
            send_spread_recommendations()
        except Exception as e:
            print(f"[SPREAD REC ERROR] {e}")

    # Morning recap at 7 AM SGT (post-session)
    if should_send_morning_recap():
        try:
            send_morning_recap()
            mark_briefing_sent("last_recap_date")
        except Exception as e:
            print(f"[RECAP ERROR] {e}")

    # Weekend war room at 9 AM SGT Saturday
    if should_send_war_room():
        try:
            send_war_room()
            mark_briefing_sent("last_warroom_date")
        except Exception as e:
            print(f"[WAR ROOM ERROR] {e}")

    # --- SECTOR ROTATION ---
    # Once daily at 10:30 AM ET (after market open settles)
    if is_market_hours() and now_et.hour == 10 and now_et.minute == 30:
        try:
            check_sector_rotation_new()
        except Exception as e:
            print(f"[SECTOR ROTATION ERROR] {e}")

    # --- ETF FLOWS ---
    # Once daily at 3 PM ET (end of day to capture full day flows)
    if is_market_hours() and now_et.hour == 15 and now_et.minute == 0:
        try:
            check_etf_flows()
        except Exception as e:
            print(f"[ETF FLOW ERROR] {e}")

    # --- GAP MONITOR ---
    # Overnight gaps: 6 AM SGT (midnight ET, futures active) on weekdays
    # Pre-market MU: 5 PM SGT (4 AM ET pre-market opens) on weekdays
    if now_sgt.hour == 6 and now_sgt.minute == 0 and now_sgt.weekday() < 5:
        try:
            check_overnight_gaps()
        except Exception as e:
            print(f"[GAP MONITOR ERROR] {e}")
    if now_sgt.hour == 17 and now_sgt.minute == 0 and now_sgt.weekday() < 5:
        try:
            check_premarket_mu()
        except Exception as e:
            print(f"[PREMARKET ERROR] {e}")

    # --- SHORT INTEREST ---
    # Once daily at 11:30 AM ET (after FINRA data published)
    if is_market_hours() and now_et.hour == 11 and now_et.minute == 30:
        try:
            check_short_updates()
        except Exception as e:
            print(f"[SHORT TRACKER ERROR] {e}")

    # --- ESTIMATE TRACKER ---
    # Once daily at 9 AM ET (before market open, catch overnight revisions)
    if now_et.hour == 9 and now_et.minute == 0 and now_et.weekday() < 5:
        try:
            check_estimate_changes()
        except Exception as e:
            print(f"[ESTIMATE ERROR] {e}")

    # --- OIL PRICE TRACKER ---
    # Every 15 min (oil is #1 driver during Iran war)
    # Runs during futures hours (nearly 24/7 Sun 6PM - Fri 5PM ET)
    if now_et.minute % 15 == 0:
        try:
            check_oil_prices()
        except Exception as e:
            print(f"[OIL TRACKER ERROR] {e}")

    # --- MEMORY PRICING ---
    # Once daily at 9:30 AM ET (after pre-market data available)
    if now_et.hour == 9 and now_et.minute == 30 and now_et.weekday() < 5:
        try:
            check_memory_pricing()
        except Exception as e:
            print(f"[MEMORY PRICING ERROR] {e}")

    # --- HYPERSCALER CAPEX SIGNALS ---
    # Once daily at 10 AM ET
    if is_market_hours() and now_et.hour == 10 and now_et.minute == 0:
        try:
            check_hyperscaler_signals()
        except Exception as e:
            print(f"[HYPERSCALER ERROR] {e}")

    # --- SUPPLY CHAIN MONITOR ---
    # Once daily at 11 AM ET
    if is_market_hours() and now_et.hour == 11 and now_et.minute == 0:
        try:
            check_supply_chain()
        except Exception as e:
            print(f"[SUPPLY CHAIN ERROR] {e}")

    # --- GEOPOLITICAL MONITOR ---
    # Every 5 min during active Iran war — user wants real-time escalation tracking
    if now_et.minute % 5 == 0:
        try:
            check_geopolitical_risks()
        except Exception as e:
            print(f"[GEOPOLITICAL ERROR] {e}")

    # --- EVENT LIVE TRACKER ---
    # Every 3 min during active events, every 30 min otherwise (for countdowns)
    from alerts.event_tracker import _get_active_events
    has_active_event = bool(_get_active_events())
    event_interval = 3 if has_active_event else 30
    if now_et.minute % event_interval == 0:
        try:
            check_live_events()
        except Exception as e:
            print(f"[EVENT TRACKER ERROR] {e}")

    # =========================================================================
    # WEEKEND-SPECIFIC ALERTS
    # =========================================================================
    is_weekend = now_et.weekday() >= 5  # Saturday or Sunday

    # --- WEEKEND CRYPTO SENTIMENT ---
    # Every 4 hours on weekends (rate-limited internally)
    if is_weekend and now_sgt.minute == 0 and now_sgt.hour % 4 == 0:
        try:
            check_weekend_crypto()
        except Exception as e:
            print(f"[CRYPTO ERROR] {e}")

    # --- SUNDAY FUTURES OPEN ---
    # Sunday 6-8 PM ET (Monday 7-9 AM SGT) — checks internally
    if now_et.weekday() == 6 and now_et.hour >= 18:
        try:
            check_sunday_futures_open()
        except Exception as e:
            print(f"[SUNDAY FUTURES ERROR] {e}")

    # --- MONDAY ASIA PREVIEW ---
    # Monday 10 AM SGT (after Asian markets open)
    if now_sgt.weekday() == 0 and now_sgt.hour == 10 and now_sgt.minute == 0:
        try:
            check_monday_asia_preview()
        except Exception as e:
            print(f"[MONDAY ASIA ERROR] {e}")

    # --- WEEKEND NEWS DIGEST ---
    # Sunday 6 PM SGT (batch all weekend content)
    if now_sgt.weekday() == 6 and now_sgt.hour == 18 and now_sgt.minute == 0:
        try:
            send_weekend_digest()
        except Exception as e:
            print(f"[WEEKEND DIGEST ERROR] {e}")

    # --- WEEK AHEAD PREVIEW ---
    # Sunday 7 PM SGT (catalysts, earnings, position health)
    if now_sgt.weekday() == 6 and now_sgt.hour == 19 and now_sgt.minute == 0:
        try:
            send_week_ahead()
        except Exception as e:
            print(f"[WEEK AHEAD ERROR] {e}")

    # --- WEEKEND SOCIAL SENTIMENT ---
    # Sunday 5 PM SGT
    if now_sgt.weekday() == 6 and now_sgt.hour == 17 and now_sgt.minute == 0:
        try:
            check_weekend_social()
        except Exception as e:
            print(f"[WEEKEND SOCIAL ERROR] {e}")

    # --- EXPIRY RECAP ---
    # Saturday 9:30 AM SGT (after war room)
    if now_sgt.weekday() == 5 and now_sgt.hour == 9 and now_sgt.minute == 30:
        try:
            send_expiry_recap()
        except Exception as e:
            print(f"[EXPIRY RECAP ERROR] {e}")

    # --- FX MONITOR ---
    # Sunday 7 PM ET (FX opens) + weekday 8 AM ET
    if (now_et.weekday() == 6 and now_et.hour == 19 and now_et.minute == 0) or \
       (now_et.weekday() < 5 and now_et.hour == 8 and now_et.minute == 0):
        try:
            check_fx_moves()
        except Exception as e:
            print(f"[FX MONITOR ERROR] {e}")

    # --- PREDICTION MARKETS ---
    # Weekends: every 6 hours | Weekdays: once daily at noon ET
    if is_weekend and now_sgt.hour % 6 == 0 and now_sgt.minute == 0:
        try:
            check_prediction_markets()
        except Exception as e:
            print(f"[PREDICTION MARKET ERROR] {e}")
    elif not is_weekend and now_et.hour == 12 and now_et.minute == 0:
        try:
            check_prediction_markets()
        except Exception as e:
            print(f"[PREDICTION MARKET ERROR] {e}")

    # --- EXPIRY WEEK STATUS ---
    if expiry_mode and now_sgt.hour == 20 and now_sgt.minute == 0:
        dte = days_to_expiry()
        print(f"[EXPIRY MODE] {dte} days to expiry - escalated monitoring active")


if __name__ == "__main__":
    main()
