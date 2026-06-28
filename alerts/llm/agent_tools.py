"""Tool registry for the grounded Q&A agent.

Exposes the project's *deterministic* functions (live prices, P&L math, the
quant library in ``tools/``) as LLM-callable tools. The model never computes a
number itself — it calls a tool and narrates the real result. Every tool returns
a JSON-serializable dict and never raises (errors come back as ``{"error": ...}``)
so a bad tool call degrades the answer rather than crashing the loop.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..config import POSITION, position_summary, CATALYSTS, TZ_ET


# ── individual tool implementations ──────────────────────────────────────────

def _get_price(ticker: str) -> dict:
    from ..price_monitor import get_live_price, get_prev_close
    t = (ticker or POSITION["ticker"]).upper()
    price = get_live_price(t)
    if price is None:
        return {"error": f"could not fetch price for {t}"}
    prev = get_prev_close(t)
    pct = ((price - prev) / prev * 100) if prev else None
    return {"ticker": t, "price": round(price, 2),
            "prev_close": round(prev, 2) if prev else None,
            "pct_change": round(pct, 2) if pct is not None else None}


def _position_pnl() -> dict:
    from ..price_monitor import get_live_price, estimate_spread_value
    p = POSITION
    mu = get_live_price(p["ticker"])
    if not mu:
        return {"error": "could not fetch underlying price"}
    entry, contracts = p["entry_price"], p["contracts"]
    long_k, short_k = p["long_strike"], p["short_strike"]
    spread = estimate_spread_value(mu)
    pnl = (spread - entry) * contracts * 100
    # intrinsic value at expiry if price stays here
    if mu <= long_k:
        expiry_spread = 0.0
    elif mu >= short_k:
        expiry_spread = float(short_k - long_k)
    else:
        expiry_spread = mu - long_k
    dte = (date.fromisoformat(p["expiry"]) - date.today()).days
    return {
        "position": position_summary(),
        "underlying_price": round(mu, 2),
        "current_spread_value": round(spread, 2),
        "current_pnl_usd": round(pnl, 0),
        "current_pnl_pct": round((spread - entry) / entry * 100, 1),
        "breakeven": round(p["breakeven"], 2),
        "expiry_spread_if_unchanged": round(expiry_spread, 2),
        "max_gain_usd": round((short_k - long_k - entry) * contracts * 100, 0),
        "max_loss_usd": round(-entry * contracts * 100, 0),
        "days_to_expiry": dte,
    }


def _simulate_position(target_price: float) -> dict:
    from ..price_monitor import get_live_price, estimate_spread_value
    p = POSITION
    try:
        target = float(target_price)
    except (TypeError, ValueError):
        return {"error": "target_price must be a number"}
    entry, contracts = p["entry_price"], p["contracts"]
    long_k, short_k = p["long_strike"], p["short_strike"]
    sim_spread = estimate_spread_value(target)
    sim_pnl = (sim_spread - entry) * contracts * 100
    if target <= long_k:
        intrinsic = 0.0
    elif target >= short_k:
        intrinsic = float(short_k - long_k)
    else:
        intrinsic = target - long_k
    cur = get_live_price(p["ticker"])
    cur_pnl = (estimate_spread_value(cur) - entry) * contracts * 100 if cur else None
    return {
        "target_price": round(target, 2),
        "spread_value_at_target": round(sim_spread, 2),
        "pnl_at_target_usd": round(sim_pnl, 0),
        "expiry_intrinsic_at_target": round(intrinsic, 2),
        "delta_vs_current_usd": round(sim_pnl - cur_pnl, 0) if cur_pnl is not None else None,
    }


def _expected_move(ticker: str, days: int = 5) -> dict:
    from tools.probability import ProbabilityEngine
    return ProbabilityEngine.expected_move((ticker or POSITION["ticker"]).upper(), days=int(days))


def _probability_above(ticker: str, target: float, days: int = 5) -> dict:
    from tools.probability import ProbabilityEngine
    return ProbabilityEngine.finish_above(
        (ticker or POSITION["ticker"]).upper(), float(target), days=int(days))


def _volatility_report(ticker: str) -> dict:
    from tools.volatility import VolatilityAnalyzer
    return VolatilityAnalyzer.full_report((ticker or POSITION["ticker"]).upper())


def _support_resistance(ticker: str) -> dict:
    from tools.price_data import PriceData
    return PriceData.support_resistance((ticker or POSITION["ticker"]).upper())


def _options_summary(ticker: str) -> dict:
    from tools.options import OptionsAnalyzer
    t = (ticker or POSITION["ticker"]).upper()
    out = {}
    for name, fn in (("pcr", OptionsAnalyzer.pcr),
                     ("skew", OptionsAnalyzer.skew),
                     ("max_pain", OptionsAnalyzer.max_pain)):
        try:
            out[name] = fn(t)
        except Exception as e:  # noqa: BLE001
            out[name] = {"error": str(e)}
    return out


def _recommend_spreads(ticker: str = "MU") -> dict:
    from ..spread_recommender import recommend_spreads
    rows = recommend_spreads((ticker or "MU").upper(), num_expiries=3) or []
    top = []
    for s in rows[:5]:
        top.append({k: s.get(k) for k in (
            "long_strike", "short_strike", "expiry", "dte", "net_debit",
            "risk_reward", "prob_max", "kelly_pct")})
    return {"candidates": top} if top else {"error": "no viable spreads found"}


def _upcoming_catalysts() -> dict:
    now = datetime.now(ZoneInfo(TZ_ET))
    expiry = datetime.strptime(POSITION["expiry"], "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
    out = []
    for month, day, hour, minute, desc in CATALYSTS:
        try:
            dt = datetime(now.year, month, day, hour, minute, tzinfo=ZoneInfo(TZ_ET))
            if now < dt <= expiry:
                out.append({"date": dt.strftime("%Y-%m-%d %H:%M ET"), "event": desc})
        except ValueError:
            continue
    return {"catalysts_to_expiry": out[:8]}


def _recent_alerts(n: int = 5) -> dict:
    import re
    from ..bot import get_message_log
    out = []
    for e in get_message_log(int(n)) or []:
        txt = re.sub(r"<[^>]+>", "", e.get("message", "")).strip()[:160]
        if txt:
            out.append({"ts": e.get("timestamp", "")[:16], "text": txt})
    return {"recent_alerts": out}


# ── registry: schema + dispatch ──────────────────────────────────────────────

_TICKER = {"type": "string", "description": "Ticker symbol; defaults to the position ticker (MU) if omitted."}

REGISTRY: dict[str, dict] = {
    "get_price": {
        "fn": _get_price,
        "description": "Live price, previous close, and % change for a ticker.",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER}},
    },
    "position_pnl": {
        "fn": lambda: _position_pnl(),
        "description": "Current P&L of the active MU spread: live value, breakeven, max gain/loss, DTE. Use for any 'how is my position' question.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "simulate_position": {
        "fn": _simulate_position,
        "description": "Spread value and P&L if the underlying reaches target_price. Use for 'what if MU goes to X' scenarios.",
        "input_schema": {"type": "object", "properties": {"target_price": {"type": "number"}}, "required": ["target_price"]},
    },
    "expected_move": {
        "fn": _expected_move,
        "description": "Expected move (upper/lower bound, % move) over N days from realized vol.",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER, "days": {"type": "integer", "default": 5}}},
    },
    "probability_above": {
        "fn": _probability_above,
        "description": "Probability the ticker finishes above a target price in N days (lognormal from realized vol).",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER, "target": {"type": "number"}, "days": {"type": "integer", "default": 5}}, "required": ["target"]},
    },
    "volatility_report": {
        "fn": _volatility_report,
        "description": "Realized volatility (multiple estimators) for a ticker.",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER}},
    },
    "support_resistance": {
        "fn": _support_resistance,
        "description": "Recent support and resistance levels for a ticker.",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER}},
    },
    "options_summary": {
        "fn": _options_summary,
        "description": "Put/call ratio, IV skew, and max pain for a ticker's options.",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER}},
    },
    "recommend_spreads": {
        "fn": _recommend_spreads,
        "description": "Top scored bull-call-spread candidates (strikes, debit, R:R, P(max), Kelly).",
        "input_schema": {"type": "object", "properties": {"ticker": _TICKER}},
    },
    "upcoming_catalysts": {
        "fn": lambda: _upcoming_catalysts(),
        "description": "Scheduled catalysts (earnings, Fed, data) between now and the position's expiry.",
        "input_schema": {"type": "object", "properties": {}},
    },
    "recent_alerts": {
        "fn": _recent_alerts,
        "description": "The most recent alerts the monitoring system sent, for context.",
        "input_schema": {"type": "object", "properties": {"n": {"type": "integer", "default": 5}}},
    },
}


def anthropic_tool_specs() -> list[dict]:
    """Tool definitions in Anthropic Messages-API shape."""
    return [
        {"name": name, "description": t["description"], "input_schema": t["input_schema"]}
        for name, t in REGISTRY.items()
    ]


def dispatch(name: str, arguments: dict) -> str:
    """Execute a tool by name with kwargs; return a JSON string (never raises)."""
    spec = REGISTRY.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool '{name}'"})
    try:
        result = spec["fn"](**(arguments or {}))
    except TypeError as e:
        return json.dumps({"error": f"bad arguments for {name}: {e}"})
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{name} failed: {e}"})
    try:
        return json.dumps(result, default=str)
    except Exception:
        return json.dumps({"result": str(result)})
