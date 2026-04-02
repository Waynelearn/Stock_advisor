"""Price and volatility monitor - checks MU and VIX levels."""

import json
import os
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import (
    POSITION, PRICE_LEVELS, BIG_MOVE_PCT, VIX_LEVELS, TZ_ET, TZ_SGT,
    MARKET_OPEN_HOUR, MARKET_OPEN_MIN, MARKET_CLOSE_HOUR, MARKET_CLOSE_MIN,
    PREMARKET_HOUR, AFTERHOURS_END_HOUR,
    FUTURES, FUTURES_BIG_MOVE_PCT, PEERS, PEER_BIG_MOVE_PCT,
)
from .bot import send_price_alert, send_big_move_alert, send_vix_alert, send_spread_update, send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".monitor_state.json")


def load_state() -> dict:
    """Load persisted state (last known levels, alerted flags)."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_mu_price": None,
        "last_vix": None,
        "prev_close": None,
        "alerted_price_levels": [],
        "alerted_vix_levels": [],
        "alerted_big_move": False,
        "last_spread_alert_level": None,
        "last_spread_alert_time": None,
        "date": None,
    }


def save_state(state: dict):
    """Persist state to disk."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def reset_daily_state(state: dict, today: str, prev_close: float):
    """Reset alerts for a new trading day."""
    state["alerted_price_levels"] = []
    state["alerted_vix_levels"] = []
    state["alerted_big_move"] = False
    state["alerted_peers"] = []
    state["alerted_futures"] = []
    state["last_spread_alert_level"] = None
    state["prev_close"] = prev_close
    state["date"] = today


def is_market_hours() -> bool:
    """Check if US market is open (regular hours)."""
    now_et = datetime.now(ZoneInfo(TZ_ET))
    if now_et.weekday() >= 5:  # Saturday/Sunday
        return False
    market_open = now_et.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0)
    market_close = now_et.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MIN, second=0)
    return market_open <= now_et <= market_close


def is_extended_hours() -> bool:
    """Check if in pre-market or after-hours."""
    now_et = datetime.now(ZoneInfo(TZ_ET))
    if now_et.weekday() >= 5:
        return False
    now_mins = now_et.hour * 60 + now_et.minute
    market_open_mins = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MIN
    premarket_start = PREMARKET_HOUR * 60
    afterhours_end = AFTERHOURS_END_HOUR * 60
    market_close_mins = MARKET_CLOSE_HOUR * 60 + MARKET_CLOSE_MIN
    return (premarket_start <= now_mins < market_open_mins) or (market_close_mins < now_mins < afterhours_end)


def get_live_price(ticker: str) -> float | None:
    """Get current/latest price via yfinance.

    Priority: preMarketPrice/postMarketPrice (extended hours) > currentPrice > fast_info.lastPrice.
    fast_info.lastPrice only reflects the last regular session close, NOT live pre/post-market.
    """
    try:
        t = yf.Ticker(ticker)
        # info has pre/post market prices; fast_info does not
        info = t.info or {}
        pre = info.get("preMarketPrice")
        post = info.get("postMarketPrice")
        current = info.get("currentPrice") or info.get("regularMarketPrice")

        # Use extended hours price if available and market is in that phase
        if is_extended_hours():
            # Pre-market or after-hours - prefer those prices
            if pre:
                return float(pre)
            if post:
                return float(post)

        # During regular hours or as fallback
        if current:
            return float(current)

        # Last resort: fast_info (regular session close)
        data = t.fast_info
        return data.get("lastPrice") or data.get("last_price")
    except Exception as e:
        print(f"[PRICE ERROR] {ticker}: {e}")
        return None


def get_prev_close(ticker: str) -> float | None:
    """Get previous session close."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        # regularMarketPreviousClose is more accurate than fast_info.previousClose
        prev = info.get("regularMarketPreviousClose")
        if prev:
            return float(prev)
        data = t.fast_info
        return data.get("previousClose") or data.get("previous_close")
    except Exception:
        return None


def _bs_call_price(S, K, T, sigma, r=0.04):
    """Black-Scholes call price."""
    import math
    if T <= 0:
        return max(S - K, 0.0)
    if sigma <= 0:
        return max(S - K * math.exp(-r * T), 0.0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
    nd2 = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
    return S * nd1 - K * math.exp(-r * T) * nd2


# Cache IV from last successful chain fetch so we can price during extended hours
_cached_iv = {"long": None, "short": None, "timestamp": None}


def get_spread_market_value() -> tuple[float | None, float | None, float | None]:
    """Get spread value from option chain + cache IV for extended hours pricing.

    Returns: (spread_value, long_iv, short_iv) or (None, None, None).
    """
    if not POSITION.get("expiry") or not POSITION.get("contracts"):
        return None, None, None
    global _cached_iv
    try:
        t = yf.Ticker(POSITION["ticker"])
        chain = t.option_chain(POSITION["expiry"])
        calls = chain.calls

        long_row = calls[calls["strike"] == POSITION["long_strike"]]
        short_row = calls[calls["strike"] == POSITION["short_strike"]]

        if long_row.empty or short_row.empty:
            return None, None, None

        long_data = long_row.iloc[0]
        short_data = short_row.iloc[0]

        # Cache IV for BS pricing during extended hours
        long_iv = float(long_data.get("impliedVolatility") or 0)
        short_iv = float(short_data.get("impliedVolatility") or 0)
        if long_iv > 0 and short_iv > 0:
            _cached_iv["long"] = long_iv
            _cached_iv["short"] = short_iv
            _cached_iv["timestamp"] = datetime.now(ZoneInfo(TZ_ET)).isoformat()

        # Use mid price if bid/ask available (market hours)
        if long_data["bid"] > 0 and long_data["ask"] > 0:
            long_val = (long_data["bid"] + long_data["ask"]) / 2
        else:
            long_val = long_data["lastPrice"]

        if short_data["bid"] > 0 and short_data["ask"] > 0:
            short_val = (short_data["bid"] + short_data["ask"]) / 2
        else:
            short_val = short_data["lastPrice"]

        if long_val <= 0:
            return None, long_iv, short_iv

        return long_val - short_val, long_iv, short_iv
    except Exception as e:
        print(f"[SPREAD VALUE ERROR] {e}")
        return None, None, None


def estimate_spread_value(price: float) -> float:
    """Estimate spread value using best available method.

    Priority:
    1. Live option chain mid prices (during market hours with active quotes)
    2. Black-Scholes model using live underlying price + cached/chain IV
    3. Intrinsic value only (last resort, accurate only at expiry)
    """
    if not POSITION.get("expiry") or not POSITION.get("contracts"):
        return 0.0
    import math
    long_strike = POSITION["long_strike"]
    short_strike = POSITION["short_strike"]
    width = short_strike - long_strike
    expiry_date = datetime.strptime(POSITION["expiry"], "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
    now = datetime.now(ZoneInfo(TZ_ET))
    T = max((expiry_date - now).total_seconds() / (365.25 * 86400), 0)

    # Try live chain first
    market_val, long_iv, short_iv = get_spread_market_value()

    # If we got a market value AND it's market hours with active quotes, use it
    if market_val is not None and is_market_hours():
        return market_val

    # Use Black-Scholes with live price + IV (chain or cached)
    iv_long = long_iv or _cached_iv.get("long")
    iv_short = short_iv or _cached_iv.get("short")

    if iv_long and iv_short and iv_long > 0 and iv_short > 0 and price > 0:
        long_call = _bs_call_price(price, long_strike, T, iv_long)
        short_call = _bs_call_price(price, short_strike, T, iv_short)
        bs_spread = long_call - short_call
        # Sanity check: spread value must be between 0 and width
        return max(0.0, min(bs_spread, float(width)))

    # If we have a market value but couldn't do BS, still use it
    if market_val is not None:
        return market_val

    # Last resort: intrinsic value
    if price <= long_strike:
        return 0.0
    elif price >= short_strike:
        return float(width)
    else:
        return price - long_strike


def check_prices():
    """Main price check routine. Call this on each cycle."""
    state = load_state()
    now_et = datetime.now(ZoneInfo(TZ_ET))
    today = now_et.strftime("%Y-%m-%d")

    # Reset state on new day
    if state["date"] != today:
        prev = get_prev_close(POSITION["ticker"])
        reset_daily_state(state, today, prev)

    # Get MU price
    mu_price = get_live_price(POSITION["ticker"])
    if mu_price is None:
        save_state(state)
        return

    # Check price level crossings
    last_price = state["last_mu_price"]
    if last_price is not None:
        for level in PRICE_LEVELS:
            level_key = str(level)
            if level_key in state["alerted_price_levels"]:
                continue
            # Crossed above
            if last_price < level <= mu_price:
                send_price_alert(POSITION["ticker"], mu_price, level, "up")
                try:
                    from .smart_alerts import analyze_level_cross
                    analysis = analyze_level_cross(POSITION["ticker"], mu_price, level, "up")
                    send_alert(f"\U0001f9e0 <b>WHY?</b> {analysis}\n\n<code>#SMART</code>")
                except Exception:
                    pass
                state["alerted_price_levels"].append(level_key)
            # Crossed below
            elif last_price > level >= mu_price:
                send_price_alert(POSITION["ticker"], mu_price, level, "down")
                try:
                    from .smart_alerts import analyze_level_cross
                    analysis = analyze_level_cross(POSITION["ticker"], mu_price, level, "down")
                    send_alert(f"\U0001f9e0 <b>WHY?</b> {analysis}\n\n<code>#SMART</code>")
                except Exception:
                    pass
                state["alerted_price_levels"].append(level_key)

    state["last_mu_price"] = mu_price

    # Check big intraday move
    if state["prev_close"] and not state["alerted_big_move"]:
        change_pct = ((mu_price - state["prev_close"]) / state["prev_close"]) * 100
        if abs(change_pct) >= BIG_MOVE_PCT:
            send_big_move_alert(POSITION["ticker"], mu_price, change_pct, state["prev_close"])
            state["alerted_big_move"] = True

    # Check VIX
    vix = get_live_price("^VIX")
    if vix is not None:
        last_vix = state["last_vix"]
        if last_vix is not None:
            for level in VIX_LEVELS:
                level_key = str(level)
                if level_key in state["alerted_vix_levels"]:
                    continue
                if last_vix < level <= vix:
                    send_vix_alert(vix, level, "up")
                    state["alerted_vix_levels"].append(level_key)
                elif last_vix > level >= vix:
                    send_vix_alert(vix, level, "down")
                    state["alerted_vix_levels"].append(level_key)
        state["last_vix"] = vix

    # Spread P&L update at key thresholds (skip if no active position)
    if POSITION.get("contracts"):
        spread_val = estimate_spread_value(mu_price)
        entry = POSITION["entry_price"]
        contracts = POSITION["contracts"]
        pnl = (spread_val - entry) * contracts * 100
        pnl_pct = ((spread_val - entry) / entry) * 100

        # Alert at $100K P&L threshold changes, max once per hour
        pnl_level = int(pnl // 100000) * 100000
        now_ts = datetime.now(ZoneInfo(TZ_ET)).isoformat()
        last_alert_time = state.get("last_spread_alert_time")
        cooldown_ok = True
        if last_alert_time:
            try:
                last_dt = datetime.fromisoformat(last_alert_time)
                elapsed = (datetime.now(ZoneInfo(TZ_ET)) - last_dt).total_seconds()
                cooldown_ok = elapsed >= 3600  # 1 hour cooldown
            except Exception:
                cooldown_ok = True

        if state["last_spread_alert_level"] != pnl_level and cooldown_ok:
            send_spread_update(mu_price, spread_val, pnl, pnl_pct)
            state["last_spread_alert_level"] = pnl_level
            state["last_spread_alert_time"] = now_ts

    # Check peer big moves
    for peer in PEERS:
        peer_price = get_live_price(peer)
        if peer_price is None:
            continue
        peer_prev = get_prev_close(peer)
        if peer_prev and peer_prev > 0:
            peer_chg = ((peer_price - peer_prev) / peer_prev) * 100
            peer_key = f"peer_{peer}"
            if abs(peer_chg) >= PEER_BIG_MOVE_PCT and peer_key not in state.get("alerted_peers", []):
                emoji = "\U0001f680" if peer_chg > 0 else "\U0001f4a5"
                arrow = "\u25b2" if peer_chg > 0 else "\u25bc"
                msg = (
                    f"{emoji} <b>{peer}  {arrow} {peer_chg:+.2f}%</b>\n"
                    f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                    f"\U0001f4b0 ${peer_price:.2f}\n\n"
                    f"<code>#PEER</code>"
                )
                send_alert(msg)
                state.setdefault("alerted_peers", []).append(peer_key)

    # Check futures
    for fticker, fname in FUTURES.items():
        fut_price = get_live_price(fticker)
        if fut_price is None:
            continue
        fut_prev = get_prev_close(fticker)
        if fut_prev and fut_prev > 0:
            fut_chg = ((fut_price - fut_prev) / fut_prev) * 100
            fut_key = f"fut_{fticker}"
            if abs(fut_chg) >= FUTURES_BIG_MOVE_PCT and fut_key not in state.get("alerted_futures", []):
                emoji = "\U0001f7e2" if fut_chg > 0 else "\U0001f534"
                arrow = "\u25b2" if fut_chg > 0 else "\u25bc"
                msg = (
                    f"{emoji} <b>{fname}  {arrow} {fut_chg:+.2f}%</b>\n"
                    f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
                    f"\U0001f310 {fut_price:,.0f}\n\n"
                    f"<code>#FUTURES</code>"
                )
                send_alert(msg)
                state.setdefault("alerted_futures", []).append(fut_key)

    save_state(state)


if __name__ == "__main__":
    check_prices()
