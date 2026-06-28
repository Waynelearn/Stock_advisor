"""Correlation monitor - MU-SOX divergence and VIX correlation breakdown alerts."""

import json
import os
import yfinance as yf
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, POSITION, position_summary, TZ_ET, DIVERGENCE_ZSCORE, VIX_ANOMALY_MU_PCT, VIX_ANOMALY_VIX_PCT
from .llm import ask
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".correlation_state.json")


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {
        "date": None,
        "last_divergence_alert_date": None,
        "last_vix_anomaly_date": None,
    })


def save_state(state: dict):
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def check_mu_sox_divergence():
    """Alert when MU decouples from SOXX by >1.5 sigma (stock-specific event)."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_divergence_alert_date") == today:
        return

    try:
        mu_hist = yf.Ticker(POSITION["ticker"]).history(period="1mo")
        soxx_hist = yf.Ticker("SOXX").history(period="1mo")

        if len(mu_hist) < 5 or len(soxx_hist) < 5:
            return

        # Daily returns
        mu_returns = mu_hist["Close"].pct_change().dropna()
        soxx_returns = soxx_hist["Close"].pct_change().dropna()

        # Align on common dates
        common_dates = mu_returns.index.intersection(soxx_returns.index)
        if len(common_dates) < 10:
            return

        mu_ret = mu_returns.loc[common_dates].values
        soxx_ret = soxx_returns.loc[common_dates].values

        # Correlation
        correlation = float(_corr(mu_ret, soxx_ret))

        # Divergence z-score
        divergences = mu_ret - soxx_ret
        today_div = divergences[-1]
        div_std = float(divergences.std())

        if div_std == 0:
            return

        z_score = today_div / div_std

        if abs(z_score) < DIVERGENCE_ZSCORE:
            return

        mu_today_pct = mu_ret[-1] * 100
        soxx_today_pct = soxx_ret[-1] * 100
        div_pct = today_div * 100

        if z_score > 0:
            direction = "OUTPERFORMING"
            emoji = "\U0001f7e2"
        else:
            direction = "UNDERPERFORMING"
            emoji = "\U0001f534"

        analysis = _analyze_divergence(mu_today_pct, soxx_today_pct, z_score, correlation)

        msg = (
            f"\U0001f4ca <b>MU-SOX DIVERGENCE</b>\n"
            + "\u2500" * 20 + "\n\n"
            f"{emoji} MU {direction} semis sector\n\n"
            f"MU today: <b>{mu_today_pct:+.2f}%</b>\n"
            f"SOXX today: <b>{soxx_today_pct:+.2f}%</b>\n"
            f"Divergence: {div_pct:+.2f}%\n"
            f"Z-score: <b>{z_score:+.2f}</b>\n"
            f"20-day correlation: {correlation:.2f}\n\n"
            f"\U0001f9e0 {analysis}\n\n"
            f"<code>#DIVERGE</code>"
        )
        send_alert(msg)

        state["last_divergence_alert_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[DIVERGENCE ERROR] {e}")


def check_vix_correlation_breakdown():
    """Alert when VIX and MU move in the same direction (anomaly)."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    if state.get("last_vix_anomaly_date") == today:
        return

    try:
        mu_info = yf.Ticker(POSITION["ticker"]).fast_info
        mu_price = mu_info.get("lastPrice") or mu_info.get("last_price") or 0
        mu_prev = mu_info.get("previousClose") or mu_info.get("previous_close") or 0

        vix_info = yf.Ticker("^VIX").fast_info
        vix_price = vix_info.get("lastPrice") or vix_info.get("last_price") or 0
        vix_prev = vix_info.get("previousClose") or vix_info.get("previous_close") or 0

        if not mu_prev or not vix_prev:
            return

        mu_chg = ((mu_price - mu_prev) / mu_prev) * 100
        vix_chg = ((vix_price - vix_prev) / vix_prev) * 100

        # Both moving same direction by meaningful amounts
        both_up = mu_chg > VIX_ANOMALY_MU_PCT and vix_chg > VIX_ANOMALY_VIX_PCT
        both_down = mu_chg < -VIX_ANOMALY_MU_PCT and vix_chg < -VIX_ANOMALY_VIX_PCT

        if not (both_up or both_down):
            return

        if both_up:
            scenario = "BOTH RISING"
            detail = "MU rising WITH fear - unusual bullish conviction or volatility event"
        else:
            scenario = "BOTH FALLING"
            detail = "MU falling WITH fear easing - potential forced selling or sector derisking"

        analysis = _analyze_vix_anomaly(mu_price, mu_chg, vix_price, vix_chg, scenario)

        msg = (
            f"\u26a0\ufe0f <b>VIX CORRELATION BREAKDOWN</b>\n"
            + "\u2500" * 20 + "\n\n"
            f"<b>{scenario}</b> - normally inverse!\n\n"
            f"MU: ${mu_price:.2f} ({mu_chg:+.2f}%)\n"
            f"VIX: {vix_price:.2f} ({vix_chg:+.2f}%)\n\n"
            f"{detail}\n\n"
            f"\U0001f9e0 {analysis}\n\n"
            f"<code>#VIXCORR</code>"
        )
        send_alert(msg)

        state["last_vix_anomaly_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[VIX ANOMALY ERROR] {e}")


def _corr(a, b) -> float:
    """Simple Pearson correlation."""
    n = len(a)
    if n < 2:
        return 0.0
    a_mean = sum(a) / n
    b_mean = sum(b) / n
    cov = sum((ai - a_mean) * (bi - b_mean) for ai, bi in zip(a, b))
    a_std = (sum((ai - a_mean) ** 2 for ai in a)) ** 0.5
    b_std = (sum((bi - b_mean) ** 2 for bi in b)) ** 0.5
    if a_std == 0 or b_std == 0:
        return 0.0
    return cov / (a_std * b_std)


def _analyze_divergence(mu_ret, soxx_ret, z_score, correlation) -> str:
    prompt = (
        f"MU returned {mu_ret:+.2f}% today while SOXX returned {soxx_ret:+.2f}%. "
        f"Divergence z-score: {z_score:+.2f} (>1.5 = significant). "
        f"20-day correlation: {correlation:.2f}.\n"
        f"My position: {position_summary()}.\n"
        f"In 2 sentences: is MU leading or lagging? Stock-specific driver or sector rotation?"
    )
    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="correlation_monitor", fallback="Analysis unavailable.")


def _analyze_vix_anomaly(mu_price, mu_chg, vix_price, vix_chg, scenario) -> str:
    prompt = (
        f"Unusual: MU ({mu_chg:+.2f}%) and VIX ({vix_chg:+.2f}%) are {scenario}. "
        f"MU at ${mu_price:.2f}, VIX at {vix_price:.1f}.\n"
        f"My position: {position_summary()}.\n"
        f"In 2 sentences: what's causing this? Warning sign or opportunity?"
    )
    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="correlation_monitor", fallback="Analysis unavailable.")
