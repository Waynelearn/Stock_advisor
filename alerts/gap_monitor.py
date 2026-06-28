"""Overnight gap monitor - futures and pre-market gaps for Singapore-based user.

Designed to run from midnight SGT onwards (6 PM+ ET when futures are active).
Alerts on significant gaps vs previous close so the user knows what to expect
when the US regular session opens.
"""

import json
import os
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import (
    POSITION, TZ_ET, TZ_SGT, FUTURES,
    GAP_FUTURES_PCT as FUTURES_GAP_PCT,
    GAP_SEMI_PCT as SEMI_GAP_PCT,
    GAP_CONSENSUS_PCT as CONSENSUS_GAP_PCT,
    GAP_PREMARKET_PCT as MU_PREMARKET_PCT,
    GAP_SEMI_LARGE_PCT, GAP_SEMI_MEDIUM_PCT, GAP_AVG_SMALL_PCT,
    MU_BETA_NQ, MU_BETA_SOX,
)
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".gap_monitor_state.json")

# Semi proxy ticker (SOXX ETF is more reliably available than ^SOX)
SEMI_TICKER = "SOXX"
SEMI_NAME = "SOXX (Semis)"


def load_state() -> dict:
    """Load persisted state."""
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {
        "last_alert_date": None,
        "last_premarket_alert_date": None,
    })


def save_state(state: dict):
    """Persist state to disk."""
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _get_price_and_prev(ticker: str) -> tuple[float | None, float | None]:
    """Get current price and previous close for a ticker.

    For futures, uses info dict which includes extended hours prices.
    Returns (current_price, previous_close).
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        # Current price: prefer pre/post market, then regular
        current = (
            info.get("preMarketPrice")
            or info.get("postMarketPrice")
            or info.get("currentPrice")
            or info.get("regularMarketPrice")
        )

        # Previous close
        prev_close = info.get("regularMarketPreviousClose")

        if current:
            current = float(current)
        if prev_close:
            prev_close = float(prev_close)

        # Fallback to fast_info for prev close
        if not prev_close:
            fi = t.fast_info
            prev_close = fi.get("previousClose") or fi.get("previous_close")
            if prev_close:
                prev_close = float(prev_close)

        return current, prev_close
    except Exception as e:
        print(f"[GAP MONITOR] Price fetch failed for {ticker}: {e}")
        return None, None


def _get_mu_premarket_info() -> dict:
    """Get MU pre-market price, previous close, and volume.

    Returns dict with keys: price, prev_close, change_pct, volume, ok
    """
    result = {"price": None, "prev_close": None, "change_pct": None, "volume": None, "ok": False}
    try:
        t = yf.Ticker(POSITION["ticker"])
        info = t.info or {}

        prev_close = info.get("regularMarketPreviousClose")
        pre_price = info.get("preMarketPrice")
        post_price = info.get("postMarketPrice")

        # Use whichever extended-hours price is available
        ext_price = pre_price or post_price
        if not ext_price or not prev_close:
            return result

        ext_price = float(ext_price)
        prev_close = float(prev_close)

        if prev_close <= 0:
            return result

        change_pct = (ext_price - prev_close) / prev_close * 100

        result["price"] = ext_price
        result["prev_close"] = prev_close
        result["change_pct"] = round(change_pct, 2)

        # Pre-market volume if available
        pre_vol = info.get("preMarketVolume")
        if pre_vol:
            result["volume"] = int(pre_vol)

        result["ok"] = True
    except Exception as e:
        print(f"[GAP MONITOR] MU pre-market fetch failed: {e}")
    return result


def _format_volume(vol: int) -> str:
    """Format volume with K/M suffix."""
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    elif vol >= 1_000:
        return f"{vol / 1_000:.0f}K"
    return str(vol)


def _estimate_mu_open(mu_prev_close: float, nq_gap_pct: float | None, sox_gap_pct: float | None) -> tuple[float, float]:
    """Estimate MU open range based on NQ and SOX gaps.

    Uses MU's historical beta to NQ (~1.4x) and SOX (~0.9x).
    Returns (low_estimate, high_estimate).
    """
    estimates = []

    if nq_gap_pct is not None:
        nq_implied = mu_prev_close * (1 + nq_gap_pct / 100 * MU_BETA_NQ)
        estimates.append(nq_implied)

    if sox_gap_pct is not None:
        sox_implied = mu_prev_close * (1 + sox_gap_pct / 100 * MU_BETA_SOX)
        estimates.append(sox_implied)

    if not estimates:
        return mu_prev_close, mu_prev_close

    low = min(estimates)
    high = max(estimates)

    # Add a small buffer for uncertainty
    spread = max(high - low, mu_prev_close * 0.003)  # At least 0.3% spread
    mid = (low + high) / 2
    return round(mid - spread / 2, 2), round(mid + spread / 2, 2)


def _determine_sentiment(gaps: dict[str, float], semi_gap: float | None) -> str:
    """Determine overall sentiment from gap data.

    Returns one of: "Bullish gap-up", "Bearish gap-down", "Mixed signals", "Flat open expected"
    """
    valid_gaps = [v for v in gaps.values() if v is not None]
    if not valid_gaps:
        return "No data"

    avg_gap = sum(valid_gaps) / len(valid_gaps)
    all_positive = all(g > 0 for g in valid_gaps)
    all_negative = all(g < 0 for g in valid_gaps)

    # Add semi weight to sentiment
    semi_bias = ""
    if semi_gap is not None:
        if abs(semi_gap) > GAP_SEMI_LARGE_PCT:
            semi_bias = ", semis leading" if semi_gap > 0 else ", semis lagging"
        elif abs(semi_gap) > GAP_SEMI_MEDIUM_PCT:
            semi_bias = ", semis firm" if semi_gap > 0 else ", semis soft"

    if all_positive and avg_gap >= 0.5:
        return f"Bullish gap-up{semi_bias}"
    elif all_negative and avg_gap <= -0.5:
        return f"Bearish gap-down{semi_bias}"
    elif abs(avg_gap) < GAP_AVG_SMALL_PCT:
        return f"Flat open expected{semi_bias}"
    else:
        return f"Mixed signals{semi_bias}"


def check_overnight_gaps():
    """Main overnight gap check - futures vs previous close.

    Tracks ES, NQ, YM futures plus SOXX as semi proxy.
    Estimates MU's likely open based on futures gaps.
    Alerts when gaps exceed thresholds.
    """
    try:
        now_sgt = datetime.now(ZoneInfo(TZ_SGT))
        today = now_sgt.strftime("%Y-%m-%d")

        # Only run on weekdays (futures don't trade Sat night / Sun)
        # Sunday 6 PM ET = Monday ~7 AM SGT, so day-of-week check uses ET
        now_et = datetime.now(ZoneInfo(TZ_ET))
        if now_et.weekday() >= 5:  # Saturday or Sunday
            # Exception: Sunday after 6 PM ET futures are open (next week)
            if not (now_et.weekday() == 6 and now_et.hour >= 18):
                return

        state = load_state()
        if state.get("last_alert_date") == today:
            return

        # Fetch futures data
        gaps = {}        # ticker -> gap_pct
        prices = {}      # ticker -> current_price
        prev_closes = {} # ticker -> prev_close

        for fticker in FUTURES:
            current, prev = _get_price_and_prev(fticker)
            if current and prev and prev > 0:
                gap_pct = (current - prev) / prev * 100
                gaps[fticker] = round(gap_pct, 2)
                prices[fticker] = current
                prev_closes[fticker] = prev

        # Fetch semi proxy (SOXX)
        semi_current, semi_prev = _get_price_and_prev(SEMI_TICKER)
        semi_gap = None
        if semi_current and semi_prev and semi_prev > 0:
            semi_gap = round((semi_current - semi_prev) / semi_prev * 100, 2)

        # Check if any threshold is breached
        should_alert_flag = False

        # Individual futures gap >0.5%
        for fticker, gap in gaps.items():
            if abs(gap) >= FUTURES_GAP_PCT:
                should_alert_flag = True
                break

        # Semi gap >0.75%
        if semi_gap is not None and abs(semi_gap) >= SEMI_GAP_PCT:
            should_alert_flag = True

        # Consensus: all futures gap same direction >0.3%
        if len(gaps) >= 2:
            gap_values = list(gaps.values())
            all_same_dir = (all(g > 0 for g in gap_values) or all(g < 0 for g in gap_values))
            all_above_consensus = all(abs(g) >= CONSENSUS_GAP_PCT for g in gap_values)
            if all_same_dir and all_above_consensus:
                should_alert_flag = True

        if not should_alert_flag:
            # No significant gaps - mark as checked, skip alert
            state["last_alert_date"] = today
            save_state(state)
            return

        # Get MU previous close for open estimate
        mu_prev = None
        try:
            mu_info = yf.Ticker(POSITION["ticker"]).info or {}
            mu_prev = mu_info.get("regularMarketPreviousClose")
            if mu_prev:
                mu_prev = float(mu_prev)
        except Exception:
            pass

        # Build the message
        lines = []
        lines.append("\U0001f30c <b>OVERNIGHT GAP ALERT</b>")
        lines.append("\u2501" * 22)
        lines.append("")
        lines.append("<b>Futures vs Yesterday Close:</b>")

        # Futures lines
        short_names = {"ES=F": "ES (S&P)", "NQ=F": "NQ (Nasdaq)", "YM=F": "YM (Dow)"}
        for fticker, fname in FUTURES.items():
            short = short_names.get(fticker, fname)
            if fticker in gaps:
                gap = gaps[fticker]
                arrow = "\u25b2" if gap > 0 else ("\u25bc" if gap < 0 else "\u25ac")
                price_str = f"{prices[fticker]:,.0f}" if prices[fticker] >= 1000 else f"{prices[fticker]:.2f}"
                lines.append(f"  {short}: <b>{gap:+.1f}%</b> {arrow} {price_str}")
            else:
                lines.append(f"  {short}: n/a")

        # Semi line
        if semi_gap is not None:
            arrow = "\u25b2" if semi_gap > 0 else ("\u25bc" if semi_gap < 0 else "\u25ac")
            lines.append(f"  {SEMI_NAME}: <b>{semi_gap:+.1f}%</b> {arrow} {semi_current:.2f}")
        else:
            lines.append(f"  {SEMI_NAME}: n/a")

        # Estimated MU open
        lines.append("")
        if mu_prev:
            nq_gap = gaps.get("NQ=F")
            low_est, high_est = _estimate_mu_open(mu_prev, nq_gap, semi_gap)
            gap_low = low_est - mu_prev
            gap_high = high_est - mu_prev
            gap_low_pct = gap_low / mu_prev * 100
            gap_high_pct = gap_high / mu_prev * 100

            lines.append(f"<b>Estimated MU Open:</b> ${low_est:.2f}-${high_est:.2f} (prev close ${mu_prev:.2f})")
            lines.append(f"Gap: ${gap_low:+.2f} to ${gap_high:+.2f} ({gap_low_pct:+.1f}% to {gap_high_pct:+.1f}%)")

        # MU pre-market check (inline)
        mu_pre = _get_mu_premarket_info()
        if mu_pre["ok"]:
            vol_str = f" [vol: {_format_volume(mu_pre['volume'])}]" if mu_pre["volume"] else ""
            lines.append("")
            lines.append(f"\U0001f4ca <b>MU Pre-Market:</b> ${mu_pre['price']:.2f} ({mu_pre['change_pct']:+.1f}%){vol_str}")

        # Sentiment
        lines.append("")
        sentiment = _determine_sentiment(gaps, semi_gap)
        lines.append(f"\U0001f9e0 Sentiment: <b>{sentiment}</b>")

        lines.append("")
        lines.append("<code>#GAP</code>")

        msg = "\n".join(lines)
        send_alert(msg)

        state["last_alert_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[GAP MONITOR ERROR] {e}")


def check_premarket_mu():
    """Check MU's own pre-market price and alert if move >1%.

    Standalone function for targeted MU pre-market monitoring.
    Can be called independently from check_overnight_gaps().
    """
    try:
        now_sgt = datetime.now(ZoneInfo(TZ_SGT))
        today = now_sgt.strftime("%Y-%m-%d")

        # Skip weekends
        now_et = datetime.now(ZoneInfo(TZ_ET))
        if now_et.weekday() >= 5:
            return

        state = load_state()
        if state.get("last_premarket_alert_date") == today:
            return

        mu_pre = _get_mu_premarket_info()
        if not mu_pre["ok"]:
            return

        # Only alert if move exceeds threshold
        if abs(mu_pre["change_pct"]) < MU_PREMARKET_PCT:
            return

        # Build message
        if mu_pre["change_pct"] > 0:
            icon = "\U0001f7e2"  # green circle
            arrow = "\u25b2"
        else:
            icon = "\U0001f534"  # red circle
            arrow = "\u25bc"

        vol_str = f"\n\U0001f4ca Volume: {_format_volume(mu_pre['volume'])}" if mu_pre["volume"] else ""

        msg = (
            f"{icon} <b>MU PRE-MARKET {arrow} {mu_pre['change_pct']:+.1f}%</b>\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\n"
            f"\U0001f4b0 Pre-Market: <b>${mu_pre['price']:.2f}</b>\n"
            f"\U0001f4c5 Prev Close: ${mu_pre['prev_close']:.2f}\n"
            f"Move: ${mu_pre['price'] - mu_pre['prev_close']:+.2f} ({mu_pre['change_pct']:+.1f}%)"
            f"{vol_str}\n"
            f"\n"
            f"<code>#GAP</code>"
        )
        send_alert(msg)

        state["last_premarket_alert_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[GAP PREMARKET ERROR] {e}")


if __name__ == "__main__":
    check_overnight_gaps()
