"""Options Flow Detector - monitors unusual volume, PCR trends, IV changes, and liquidity on MU options.

Features:
  6. Enhanced Unusual Options Activity (OI tracking, dollar-weighted, strike-specific, sweep detection)
  7. Put/Call Ratio Trend (PCR shift alerts)
  23. Liquidity Alert (bid-ask spread on position strikes)
  24. IV Crush/Spike Alert (IV day-over-day tracking on position strikes)
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from .config import (
    POSITION, DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, TZ_ET,
    VOL_OI_RATIO_THRESHOLD, VOL_OI_RATIO_SWEEP, MIN_UNUSUAL_VOLUME,
    PCR_SHIFT_THRESHOLD, IV_CHANGE_THRESHOLD, LIQUIDITY_SPREAD_THRESHOLD,
)
from .bot import send_alert
from .llm import ask

STATE_FILE = os.path.join(os.path.dirname(__file__), ".options_flow_state.json")

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

# Local aliases (single source of truth lives in config.py)
UNUSUAL_VOL_OI_RATIO = VOL_OI_RATIO_THRESHOLD
UNUSUAL_MIN_VOLUME = MIN_UNUSUAL_VOLUME
OTHER_EXPIRY_MIN_VOLUME = MIN_UNUSUAL_VOLUME * 5  # higher bar for non-position expiries
OTHER_EXPIRY_VOL_OI = VOL_OI_RATIO_SWEEP
LIQUIDITY_SPREAD_PCT = LIQUIDITY_SPREAD_THRESHOLD


# ─── State Management ───────────────────────────────────────────────────────

def _load_state() -> dict:
    """Load persisted state from disk."""
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {
        "date": None,
        "prev_oi": {},
        "prev_pcr": None,
        "prev_iv": {},
        "last_pcr_alert_value": None,
        "last_liquidity_alert_date": None,
    })


def _save_state(state: dict):
    """Persist state to disk."""
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


# ─── Option Chain Fetching ───────────────────────────────────────────────────

def _get_chains(ticker_obj, expiries: list[str]) -> dict:
    """Fetch option chains for multiple expiries.

    Returns: {expiry: {"calls": DataFrame, "puts": DataFrame}, ...}
    """
    chains = {}
    for exp in expiries:
        try:
            chain = ticker_obj.option_chain(exp)
            chains[exp] = {"calls": chain.calls, "puts": chain.puts}
        except Exception as e:
            print(f"[OPTIONS FLOW] Chain fetch error for {exp}: {e}")
    return chains


def _get_scan_expiries(ticker_obj) -> list[str]:
    """Get our expiry + next 2 available expiries for scanning."""
    our_expiry = POSITION.get("expiry")
    expiries = [our_expiry] if our_expiry else []
    try:
        all_exp = list(ticker_obj.options)
        # Add up to 3 expiries (or 2 beyond ours)
        max_extras = 2 if our_expiry else 3
        extras_added = 0
        for exp in all_exp:
            if exp != our_expiry and extras_added < max_extras:
                expiries.append(exp)
                extras_added += 1
            if extras_added >= max_extras:
                break
    except Exception:
        pass
    return expiries


# ─── Feature 6: Enhanced Unusual Options Activity ────────────────────────────

def _scan_unusual_activity(chains: dict, state: dict) -> tuple[list[dict], dict]:
    """Scan chains for unusual activity with dollar-weighting, OI changes, and sweep detection.

    Returns: (unusual_list, new_oi_snapshot)
    """
    unusual = []
    new_oi = {}
    prev_oi = state.get("prev_oi", {})
    our_expiry = POSITION.get("expiry")
    long_strike = POSITION.get("long_strike")
    short_strike = POSITION.get("short_strike")

    # Track strikes across expiries for sweep detection
    strike_expiry_map = {}  # {(side, strike): [expiry1, expiry2, ...]}

    for expiry, chain_data in chains.items():
        is_our_expiry = (expiry == our_expiry)

        for side, df in [("CALL", chain_data["calls"]), ("PUT", chain_data["puts"])]:
            for _, row in df.iterrows():
                strike = row["strike"]
                vol = int(row.get("volume") or 0)
                oi = int(row.get("openInterest") or 0)
                last_price = float(row.get("lastPrice") or 0)
                iv = float(row.get("impliedVolatility") or 0) * 100

                # Save OI snapshot
                oi_key = f"{side}_{strike}_{expiry}"
                new_oi[oi_key] = oi

                # Calculate OI change
                prev = prev_oi.get(oi_key, 0)
                oi_change = oi - prev if prev else 0

                # Dollar-weighted notional
                notional = vol * last_price * 100

                # Determine thresholds based on expiry
                if is_our_expiry:
                    min_vol = UNUSUAL_MIN_VOLUME
                    min_ratio = UNUSUAL_VOL_OI_RATIO
                else:
                    min_vol = OTHER_EXPIRY_MIN_VOLUME
                    min_ratio = OTHER_EXPIRY_VOL_OI

                # Strike-specific: ANY volume at user's strikes gets priority
                is_our_strike = (
                    long_strike is not None
                    and side == "CALL"
                    and is_our_expiry
                    and strike in (long_strike, short_strike)
                    and vol > 0
                )

                # Standard unusual detection
                vol_oi_ratio = vol / oi if oi > 0 else 0
                is_unusual = (vol_oi_ratio >= min_ratio and vol >= min_vol) or (oi == 0 and vol >= min_vol)

                if is_unusual or is_our_strike:
                    # Determine strike label
                    strike_label = ""
                    if strike == long_strike and side == "CALL":
                        strike_label = "YOUR LONG STRIKE"
                    elif strike == short_strike and side == "CALL":
                        strike_label = "YOUR SHORT STRIKE"

                    unusual.append({
                        "strike": strike,
                        "side": side,
                        "volume": vol,
                        "oi": oi,
                        "oi_change": oi_change,
                        "ratio": round(vol_oi_ratio, 1),
                        "iv": round(iv, 1),
                        "last": round(last_price, 2),
                        "notional": round(notional),
                        "expiry": expiry,
                        "strike_label": strike_label,
                        "is_our_strike": is_our_strike,
                    })

                    # Track for sweep detection
                    sweep_key = (side, strike)
                    strike_expiry_map.setdefault(sweep_key, []).append(expiry)

    # Tag multi-expiry sweeps
    sweep_strikes = {k for k, v in strike_expiry_map.items() if len(v) >= 2}
    for item in unusual:
        item["is_sweep"] = (item["side"], item["strike"]) in sweep_strikes

    # Sort by notional value (dollar-weighted), our strikes first
    unusual.sort(key=lambda x: (not x["is_our_strike"], -x["notional"]))

    return unusual, new_oi


def _send_unusual_alert(unusual: list[dict]):
    """Format and send unusual activity alert with DeepSeek analysis."""
    if not unusual:
        return

    # Build top entries for alert (max 8)
    display = unusual[:8]
    flow_lines = []
    sweep_detected = False

    for u in display:
        emoji = "\U0001f7e2" if u["side"] == "CALL" else "\U0001f534"
        label = f"  \u26a1 <b>{u['strike_label']}</b>" if u["strike_label"] else ""
        sweep_tag = " \U0001f300 SWEEP" if u["is_sweep"] else ""
        oi_str = ""
        if u["oi_change"] != 0:
            sign = "+" if u["oi_change"] > 0 else ""
            oi_str = f" (OI {sign}{u['oi_change']:,})"

        notional_str = _format_notional(u["notional"])

        flow_lines.append(
            f"  {emoji} {u['side']} ${u['strike']:.0f} {u['expiry']}"
            f"{label}{sweep_tag}\n"
            f"     Vol {u['volume']:,} | OI {u['oi']:,}{oi_str} | ${notional_str}"
        )
        if u["is_sweep"]:
            sweep_detected = True

    # DeepSeek analysis on top 5
    analysis = _deepseek_flow_analysis(unusual[:5])

    header = "\U0001f4a1 <b>UNUSUAL OPTIONS FLOW</b>"
    if sweep_detected:
        header = "\U0001f300 <b>INSTITUTIONAL SWEEP DETECTED</b>"

    msg = (
        f"{header}\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        + "\n\n".join(flow_lines)
        + f"\n\n\U0001f9e0 <b>ANALYSIS</b>\n{analysis}\n\n"
        f"<code>#OPTIONS</code>"
    )
    send_alert(msg)


def _format_notional(notional: float) -> str:
    """Format notional as human-readable string."""
    if notional >= 1_000_000:
        return f"{notional / 1_000_000:.1f}M"
    elif notional >= 1_000:
        return f"{notional / 1_000:.0f}K"
    else:
        return f"{notional:,.0f}"


def _deepseek_flow_analysis(unusual: list[dict]) -> str:
    """Get DeepSeek analysis of unusual flow."""
    lines = []
    for u in unusual:
        sweep = " [MULTI-EXPIRY SWEEP]" if u["is_sweep"] else ""
        label = f" [{u['strike_label']}]" if u["strike_label"] else ""
        oi_info = f", OI change: {u['oi_change']:+,}" if u["oi_change"] != 0 else ""
        lines.append(
            f"{u['side']} ${u['strike']:.0f} exp {u['expiry']}: "
            f"Vol {u['volume']:,} vs OI {u['oi']:,} ({u['ratio']}x), "
            f"IV {u['iv']}%, Notional ${_format_notional(u['notional'])}"
            f"{oi_info}{sweep}{label}"
        )
    flow_text = "\n".join(lines)

    prompt = (
        f"You are an options flow analyst for Micron (MU).\n\n"
        f"UNUSUAL ACTIVITY DETECTED:\n{flow_text}\n\n"
        f"My position: 500x MU {POSITION['long_strike']}/{POSITION['short_strike']} "
        f"bull call spread expiring {POSITION['expiry']}.\n\n"
        f"In 2-3 sentences: Is this flow bullish or bearish? "
        f"Is someone hedging or making a directional bet? "
        f"What does it signal for my spread? Note any sweeps or OI changes."
    )

    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="options_flow", fallback="DeepSeek analysis unavailable.")


# ─── Feature 7: Put/Call Ratio Trend ─────────────────────────────────────────

def _calculate_pcr(chains: dict) -> float | None:
    """Calculate put/call ratio from total put volume / total call volume across all expiries."""
    total_call_vol = 0
    total_put_vol = 0

    for expiry, chain_data in chains.items():
        for _, row in chain_data["calls"].iterrows():
            total_call_vol += int(row.get("volume") or 0)
        for _, row in chain_data["puts"].iterrows():
            total_put_vol += int(row.get("volume") or 0)

    if total_call_vol == 0:
        return None

    return round(total_put_vol / total_call_vol, 3)


def _check_pcr_trend(chains: dict, state: dict):
    """Check PCR shift and alert if significant."""
    pcr = _calculate_pcr(chains)
    if pcr is None:
        return

    prev_pcr = state.get("prev_pcr")
    last_alert_pcr = state.get("last_pcr_alert_value")

    # Update state
    state["prev_pcr"] = pcr

    if prev_pcr is None:
        return

    shift = pcr - prev_pcr

    # Only alert if shift exceeds threshold
    if abs(shift) < PCR_SHIFT_THRESHOLD:
        return

    # Avoid re-alerting at same level
    if last_alert_pcr is not None and abs(pcr - last_alert_pcr) < PCR_SHIFT_THRESHOLD:
        return

    state["last_pcr_alert_value"] = pcr

    if shift > 0:
        emoji = "\U0001f534"  # red circle
        direction = "rising (more bearish)"
        arrow = "\u25b2"
    else:
        emoji = "\U0001f7e2"  # green circle
        direction = "falling (more bullish)"
        arrow = "\u25bc"

    msg = (
        f"{emoji} <b>PUT/CALL RATIO SHIFT</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        f"  PCR {arrow} {direction}\n"
        f"  <b>{prev_pcr:.2f} \u2192 {pcr:.2f}</b> ({shift:+.2f})\n\n"
        f"  Total across {len(chains)} expiries scanned\n\n"
        f"<code>#PCR</code>"
    )
    send_alert(msg)


# ─── Feature 23: Liquidity Alert ─────────────────────────────────────────────

def _check_liquidity(chains: dict, state: dict):
    """Check bid-ask spread on position strikes and alert if wide."""
    our_expiry = POSITION["expiry"]
    if our_expiry not in chains:
        return

    today_str = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    # Only alert once per day to avoid spam
    if state.get("last_liquidity_alert_date") == today_str:
        return

    calls = chains[our_expiry]["calls"]
    wide_spreads = []

    for strike_val, strike_name in [
        (POSITION["long_strike"], f"{POSITION['long_strike']}C (LONG)"),
        (POSITION["short_strike"], f"{POSITION['short_strike']}C (SHORT)"),
    ]:
        row = calls[calls["strike"] == strike_val]
        if row.empty:
            continue
        row = row.iloc[0]

        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)

        if bid <= 0 or ask <= 0:
            continue

        mid = (bid + ask) / 2
        if mid <= 0:
            continue

        spread_pct = (ask - bid) / mid * 100

        if spread_pct > LIQUIDITY_SPREAD_PCT:
            wide_spreads.append({
                "name": strike_name,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread_pct": round(spread_pct, 1),
                "spread_abs": round(ask - bid, 2),
            })

    if not wide_spreads:
        return

    state["last_liquidity_alert_date"] = today_str

    lines = []
    for ws in wide_spreads:
        lines.append(
            f"  \u26a0\ufe0f <b>{ws['name']}</b>\n"
            f"     Bid ${ws['bid']:.2f} / Ask ${ws['ask']:.2f} "
            f"(spread ${ws['spread_abs']:.2f} = {ws['spread_pct']:.1f}%)"
        )

    msg = (
        f"\u26a0\ufe0f <b>WIDE BID-ASK SPREAD</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        + "\n\n".join(lines)
        + f"\n\n  Illiquid \u2014 exit may require price concession\n"
        f"  Consider limit orders only\n\n"
        f"<code>#LIQUIDITY</code>"
    )
    send_alert(msg)


# ─── Feature 24: IV Crush/Spike Alert ────────────────────────────────────────

def _check_iv_changes(chains: dict, state: dict):
    """Track IV on position strikes and alert on large changes."""
    our_expiry = POSITION["expiry"]
    if our_expiry not in chains:
        return

    calls = chains[our_expiry]["calls"]
    prev_iv = state.get("prev_iv", {})
    new_iv = {}
    alerts = []

    for strike_val, strike_name in [
        (POSITION["long_strike"], f"{POSITION['long_strike']}C"),
        (POSITION["short_strike"], f"{POSITION['short_strike']}C"),
    ]:
        row = calls[calls["strike"] == strike_val]
        if row.empty:
            continue
        row = row.iloc[0]

        iv_raw = float(row.get("impliedVolatility") or 0)
        iv_pct = round(iv_raw * 100, 1)

        iv_key = f"CALL_{int(strike_val)}"
        new_iv[iv_key] = iv_pct

        prev_val = prev_iv.get(iv_key)
        if prev_val is None or iv_pct == 0:
            continue

        change = iv_pct - prev_val

        if abs(change) >= IV_CHANGE_THRESHOLD:
            if change > 0:
                label = "IV SPIKE"
                emoji = "\U0001f525"  # fire
            else:
                label = "IV CRUSH"
                emoji = "\u2744\ufe0f"  # snowflake

            alerts.append({
                "name": strike_name,
                "label": label,
                "emoji": emoji,
                "prev": prev_val,
                "curr": iv_pct,
                "change": change,
            })

    # Update state with new IV values
    state["prev_iv"] = new_iv

    if not alerts:
        return

    lines = []
    for a in alerts:
        sign = "+" if a["change"] > 0 else ""
        lines.append(
            f"  {a['emoji']} <b>{a['label']}: {a['name']}</b>\n"
            f"     IV {a['prev']:.1f}% \u2192 {a['curr']:.1f}% ({sign}{a['change']:.1f}pts)"
        )

    # Add earnings context if close to March 18
    now_et = datetime.now(ZoneInfo(TZ_ET))
    days_to_earnings = (datetime(2026, 3, 18, tzinfo=ZoneInfo(TZ_ET)) - now_et).days
    earnings_note = ""
    if 0 <= days_to_earnings <= 5:
        earnings_note = (
            f"\n\n  \U0001f4c5 MU earnings in {days_to_earnings} day{'s' if days_to_earnings != 1 else ''} "
            f"\u2014 IV {'building pre-earnings' if any(a['change'] > 0 for a in alerts) else 'may crush post-event'}"
        )

    is_spike = any(a["change"] > 0 for a in alerts)
    header_emoji = "\U0001f525" if is_spike else "\u2744\ufe0f"
    header_label = "IV SPIKE" if is_spike else "IV CRUSH"

    msg = (
        f"{header_emoji} <b>{header_label} DETECTED</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        + "\n\n".join(lines)
        + earnings_note
        + f"\n\n<code>#IV</code>"
    )
    send_alert(msg)


# ─── Main Entry Point ────────────────────────────────────────────────────────

def analyze_and_alert():
    """Main entry - called every 5 min during market hours by run.py.

    1. Scan unusual activity + OI changes
    2. Check PCR trend
    3. Check IV changes
    4. Check liquidity on our strikes
    Each sub-check sends its own alert if triggered.
    """
    try:
        ticker_obj = yf.Ticker(POSITION["ticker"])
        state = _load_state()

        # Check if new trading day - reset daily flags
        now_et = datetime.now(ZoneInfo(TZ_ET))
        today_str = now_et.strftime("%Y-%m-%d")
        if state.get("date") != today_str:
            # Preserve prev_oi and prev_iv for day-over-day comparison
            # but reset daily alert flags
            state["date"] = today_str
            state["last_pcr_alert_value"] = None
            state["last_liquidity_alert_date"] = None

        # Get expiries to scan
        expiries = _get_scan_expiries(ticker_obj)

        # Fetch all chains once
        chains = _get_chains(ticker_obj, expiries)
        if not chains:
            print("[OPTIONS FLOW] No chains fetched, skipping.")
            _save_state(state)
            return

        has_position = bool(POSITION.get("expiry") and POSITION.get("contracts"))

        # 1. Unusual activity + OI tracking
        try:
            unusual, new_oi = _scan_unusual_activity(chains, state)
            # Filter: only send alert for genuinely notable flow
            # Our-strike alerts pass through even with low volume
            notable = [
                u for u in unusual
                if (has_position and u["is_our_strike"]) or u["notional"] >= 50_000
            ]
            if notable:
                _send_unusual_alert(notable)
            # Always update OI snapshot
            state["prev_oi"] = new_oi
        except Exception as e:
            print(f"[OPTIONS FLOW] Unusual activity error: {e}")

        # 2. PCR trend (works without position)
        try:
            _check_pcr_trend(chains, state)
        except Exception as e:
            print(f"[OPTIONS FLOW] PCR check error: {e}")

        # 3-4: Position-dependent checks — skip if no active position
        if has_position:
            # 3. IV changes
            try:
                _check_iv_changes(chains, state)
            except Exception as e:
                print(f"[OPTIONS FLOW] IV check error: {e}")

            # 4. Liquidity check
            try:
                _check_liquidity(chains, state)
            except Exception as e:
                print(f"[OPTIONS FLOW] Liquidity check error: {e}")
        else:
            print("[OPTIONS FLOW] No active position — skipping IV/liquidity checks.")

        # 5. GEX / Dealer positioning estimate (works without position)
        try:
            _check_dealer_gamma(chains, state)
        except Exception as e:
            print(f"[OPTIONS FLOW] GEX check error: {e}")

        _save_state(state)

    except Exception as e:
        print(f"[OPTIONS FLOW] Top-level error: {e}")


def _check_dealer_gamma(chains: dict, state: dict):
    """Estimate dealer gamma exposure (GEX) from options chain.

    Positive GEX = dealers dampen moves (sell rallies, buy dips).
    Negative GEX = dealers amplify moves (sell dips, buy rallies) = more volatile.
    """
    today_str = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")
    if state.get("last_gex_alert_date") == today_str:
        return

    try:
        info = yf.Ticker(POSITION["ticker"]).info or {}
        spot = float(info.get("regularMarketPrice") or info.get("currentPrice") or 0)
    except Exception:
        return

    if spot <= 0:
        return

    total_call_oi = 0
    total_put_oi = 0
    call_gex_proxy = 0
    put_gex_proxy = 0

    # Use nearest expiry for GEX estimation
    for expiry, chain in chains.items():
        for opt in chain.get("calls", []):
            strike = opt.get("strike", 0)
            oi = opt.get("openInterest", 0)
            if oi and strike:
                total_call_oi += oi
                # GEX proxy: OI * proximity to spot (ATM gamma highest)
                proximity = max(0, 1 - abs(strike - spot) / spot)
                call_gex_proxy += oi * proximity

        for opt in chain.get("puts", []):
            strike = opt.get("strike", 0)
            oi = opt.get("openInterest", 0)
            if oi and strike:
                total_put_oi += oi
                proximity = max(0, 1 - abs(strike - spot) / spot)
                put_gex_proxy += oi * proximity

    # Dealers are short calls (positive gamma) and long puts (negative gamma when sold to dealers)
    # Net GEX = call_gex - put_gex (simplified)
    net_gex = call_gex_proxy - put_gex_proxy

    prev_gex = state.get("prev_gex")
    regime = "POSITIVE (dealers dampen moves)" if net_gex > 0 else "NEGATIVE (dealers amplify moves — more volatile!)"

    # Alert on regime change or first run
    if prev_gex is not None:
        prev_regime_pos = prev_gex > 0
        curr_regime_pos = net_gex > 0
        if prev_regime_pos == curr_regime_pos:
            state["prev_gex"] = net_gex
            return  # No regime change

    # Find key strikes with highest OI (gravity points)
    all_strikes = {}
    for expiry, chain in chains.items():
        for opt in chain.get("calls", []) + chain.get("puts", []):
            s = opt.get("strike", 0)
            oi = opt.get("openInterest", 0)
            if s and oi:
                all_strikes[s] = all_strikes.get(s, 0) + oi

    top_strikes = sorted(all_strikes.items(), key=lambda x: x[1], reverse=True)[:5]
    gravity_str = ", ".join(f"${s:.0f} ({oi:,} OI)" for s, oi in top_strikes)

    msg = f"""DEALER POSITIONING UPDATE

GEX Regime: {regime}

Total Call OI: {total_call_oi:,}
Total Put OI: {total_put_oi:,}
Net GEX Proxy: {net_gex:,.0f}

Gravity Strikes (highest OI):
{gravity_str}

MU @ ${spot:.2f}

{'Expect amplified moves — dealers will sell into dips and chase rallies.' if net_gex < 0 else 'Expect dampened moves — dealers provide liquidity at extremes.'}

#OPTIONS_GEX"""

    send_alert(msg)
    state["prev_gex"] = net_gex
    state["last_gex_alert_date"] = today_str


if __name__ == "__main__":
    analyze_and_alert()
