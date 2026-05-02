"""Week ahead preview — earnings calendar, catalyst schedule, position health check.

Combines:
- Earnings week preview (peer earnings, consensus estimates)
- Week ahead calendar (all catalysts, economic events, FOMC)
- Position health check (theta decay, breakeven distance, probability update)

Sends Sunday 7 PM SGT (after weekend digest, before Sunday futures).
"""

import json
import os
import math
import yfinance as yf
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from scipy.stats import norm

from .config import POSITION, TZ_ET, TZ_SGT, CATALYSTS
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".week_ahead_state.json")


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"last_preview_week": None})


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _get_week_catalysts() -> list:
    """Get catalysts for the coming week (Mon-Fri)."""
    today = date.today()
    # Find next Monday
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    monday = today + timedelta(days=days_until_monday)
    friday = monday + timedelta(days=4)

    week_events = []
    for cat in CATALYSTS:
        if len(cat) >= 5:
            month, day_start, _, _, event_name = cat[0], cat[1], cat[2], cat[3], cat[4]
            try:
                event_date = date(today.year, month, day_start)
                if monday <= event_date <= friday:
                    week_events.append((event_date, event_name))
            except ValueError:
                pass

    return sorted(week_events, key=lambda x: x[0])


def _get_position_health() -> dict:
    """Calculate current position health metrics."""
    if not POSITION.get("contracts"):
        return {}
    try:
        t = yf.Ticker(POSITION["ticker"])
        info = t.info or {}
        current_price = info.get("regularMarketPreviousClose") or info.get("currentPrice", 0)
        current_price = float(current_price) if current_price else 0

        expiry = date.fromisoformat(POSITION["expiry"])
        today = date.today()
        dte = max((expiry - today).days, 0)

        long_strike = POSITION["long_strike"]
        short_strike = POSITION["short_strike"]
        width = short_strike - long_strike
        entry_price = POSITION["entry_price"]
        contracts = POSITION["contracts"]
        breakeven = POSITION["breakeven"]

        # Intrinsic value estimate
        if current_price >= short_strike:
            intrinsic = width
        elif current_price <= long_strike:
            intrinsic = 0
        else:
            intrinsic = current_price - long_strike

        # Simple Black-Scholes probability estimate
        # Use 30% IV as rough estimate
        iv = 0.45
        if current_price > 0 and dte > 0:
            t_years = dte / 365
            d2 = (math.log(current_price / short_strike) + (-0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
            prob_above_short = norm.cdf(d2)

            d2_long = (math.log(current_price / long_strike) + (-0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
            prob_above_long = norm.cdf(d2_long)
        else:
            prob_above_short = 0
            prob_above_long = 0

        # P&L
        current_value = max(min(intrinsic, width), 0)
        pnl_per_contract = current_value - entry_price
        total_pnl = pnl_per_contract * contracts * 100

        # Max profit/loss
        max_profit = (width - entry_price) * contracts * 100
        max_loss = entry_price * contracts * 100

        # Distance to breakeven
        distance_to_be = current_price - breakeven if current_price else 0
        distance_pct = (distance_to_be / breakeven * 100) if breakeven else 0

        # Theta decay estimate (rough: time value decays ~1/sqrt(dte))
        if dte > 0:
            time_value = current_value - intrinsic if current_value > intrinsic else 0
            daily_theta = time_value / dte * 1.5  # accelerating decay
        else:
            daily_theta = 0

        return {
            "current_price": current_price,
            "dte": dte,
            "intrinsic": intrinsic,
            "current_value": current_value,
            "pnl_per_contract": pnl_per_contract,
            "total_pnl": total_pnl,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "distance_to_be": distance_to_be,
            "distance_pct": distance_pct,
            "prob_above_short": prob_above_short,
            "prob_above_long": prob_above_long,
            "daily_theta": daily_theta,
            "breakeven": breakeven,
            "long_strike": long_strike,
            "short_strike": short_strike,
        }
    except Exception:
        return {}


def _get_peer_earnings_this_week() -> list:
    """Check if any peers report earnings this week."""
    peers = ["NVDA", "AMD", "AVGO", "MRVL", "TSM", "INTC", "MU"]
    earnings = []
    for ticker in peers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal and isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                for ed in dates:
                    if hasattr(ed, 'date'):
                        ed = ed.date()
                    elif isinstance(ed, datetime):
                        ed = ed.date()
                    today = date.today()
                    days_until_monday = (7 - today.weekday()) % 7 or 7
                    monday = today + timedelta(days=days_until_monday)
                    friday = monday + timedelta(days=4)
                    if monday <= ed <= friday:
                        earnings.append((ed, ticker))
        except Exception:
            pass
    return sorted(earnings, key=lambda x: x[0])


def send_week_ahead():
    """Main function — comprehensive week-ahead preview.

    Runs Sunday 7 PM SGT.
    """
    now_sgt = datetime.now(ZoneInfo(TZ_SGT))
    state = load_state()
    week_key = now_sgt.strftime("%Y-W%W")

    if state.get("last_preview_week") == week_key:
        return

    catalysts = _get_week_catalysts()
    health = _get_position_health()
    peer_earnings = _get_peer_earnings_this_week()

    msg = "WEEK AHEAD PREVIEW\n"
    msg += f"Week of {(date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)).strftime('%B %d')}\n"
    msg += "=" * 30 + "\n\n"

    # Position Health
    if health:
        msg += "POSITION HEALTH\n"
        msg += f"  MU: ${health['current_price']:,.2f}\n"
        msg += f"  DTE: {health['dte']} days\n"
        msg += f"  Spread: {health['long_strike']}/{health['short_strike']} BCS\n"

        if health["distance_to_be"] > 0:
            msg += f"  Above breakeven (${health['breakeven']:.0f}) by ${health['distance_to_be']:,.1f} ({health['distance_pct']:+.1f}%)\n"
        else:
            msg += f"  Below breakeven (${health['breakeven']:.0f}) by ${abs(health['distance_to_be']):,.1f} ({health['distance_pct']:+.1f}%)\n"

        msg += f"  Est. Value: ${health['current_value']:,.2f}/spread\n"
        msg += f"  P&L: ${health['total_pnl']:,.0f} ({'+' if health['total_pnl'] > 0 else ''}{health['pnl_per_contract']:.2f}/contract)\n"
        msg += f"  Prob > ${health['short_strike']} (max profit): {health['prob_above_short']*100:.0f}%\n"
        msg += f"  Prob > ${health['long_strike']} (in the money): {health['prob_above_long']*100:.0f}%\n"
        msg += f"  Daily theta burn: ~${health['daily_theta']:.2f}/spread\n"

        # What does the spread need this week?
        if health["dte"] <= 5:
            msg += "\n  EXPIRY WEEK — theta decay accelerating rapidly\n"
            if health["current_price"] < health["short_strike"]:
                needed = health["short_strike"] - health["current_price"]
                msg += f"  Need +${needed:.0f} ({needed/health['current_price']*100:.1f}%) to reach max profit\n"
        msg += "\n"

    # Week Catalysts
    if catalysts:
        msg += "CATALYSTS THIS WEEK\n"
        for event_date, event_name in catalysts:
            day_name = event_date.strftime("%a %b %d")
            msg += f"  {day_name}: {event_name}\n"
        msg += "\n"
    else:
        msg += "CATALYSTS: No major events scheduled\n\n"

    # Peer Earnings
    if peer_earnings:
        msg += "EARNINGS THIS WEEK\n"
        for ed, ticker in peer_earnings:
            msg += f"  {ed.strftime('%a %b %d')}: {ticker}\n"
        msg += "\n"

    # What to watch
    msg += "KEY QUESTIONS FOR THE WEEK\n"
    if health and health["dte"] <= 10:
        msg += f"  - Can MU close above ${health['short_strike']} by expiry?\n"
    if catalysts:
        msg += f"  - How will catalysts move the stock?\n"
    if peer_earnings:
        tickers = [t for _, t in peer_earnings]
        msg += f"  - Will {'/'.join(tickers)} earnings provide sympathy move?\n"
    msg += f"  - Geopolitical risk evolution over the week\n"

    msg += "\n#WEEK_AHEAD"

    send_alert(msg)

    state["last_preview_week"] = week_key
    save_state(state)
