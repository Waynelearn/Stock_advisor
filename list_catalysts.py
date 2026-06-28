#!/usr/bin/env python3
"""List catalysts between today and the position expiry, optionally with the
9-persona committee's take. Delivered to Telegram + printed to stdout.

Run any of:
    .venv/bin/python list_catalysts.py                    # list + committee + Telegram
    .venv/bin/python list_catalysts.py --no-committee     # just the list (no DeepSeek call)
    .venv/bin/python list_catalysts.py --no-telegram      # print only, do not send
    .venv/bin/python list_catalysts.py --refresh          # force-refresh the cache before listing
    .venv/bin/python list_catalysts.py --until 2026-06-30 # custom end date instead of expiry
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import requests

# Make sure the alerts package imports cleanly when run from the repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from alerts.catalyst_fetcher import get_catalysts, refresh_cache  # noqa: E402
from alerts.config import (  # noqa: E402
    POSITION, position_summary, position_moneyness,
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL_PRO, DEEPSEEK_MODEL_FAST,
    PEER_CORRELATIONS,
)
from alerts.price_monitor import get_live_price  # noqa: E402
from alerts.bot import send_alert  # noqa: E402


COMMITTEE_MODELS = (DEEPSEEK_MODEL_PRO, DEEPSEEK_MODEL_FAST, "deepseek-chat")


def filter_catalysts(start: date, end: date) -> list[tuple[date, int, int, str]]:
    year = start.year
    out = []
    for month, day, hour, minute, desc in get_catalysts():
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if start <= d <= end:
            out.append((d, hour, minute, desc))
    out.sort()
    return out


def call_committee(
    catalysts: list[tuple[date, int, int, str]],
    end: date,
    question: str | None = None,
) -> tuple[str, str]:
    """Returns (analysis_text, model_used). Falls back if a model returns empty.

    If `question` is given, the committee answers that specific question with
    the catalyst list as context. Otherwise it produces the standard
    catalyst-impact-and-verdict format.
    """
    today = date.today()
    mu_price = get_live_price(POSITION["ticker"]) or 0
    moneyness = position_moneyness(mu_price) if mu_price else 0
    dte = (end - today).days

    cat_block = "\n".join(
        f"  {d} {d.strftime('%a')}  {h:02d}:{m:02d} ET  —  {desc}"
        for d, h, m, desc in catalysts
    ) or "(no events in window)"
    peer_corr_str = ", ".join(f"{p}:{c:+.2f}" for p, c in PEER_CORRELATIONS.items())

    base = (
        f"9-persona roundtable for an MU options trader.\n"
        f"Personas: Rex (Bull), Vera (Bear), Sigma (Quant), Atlas (Macro), "
        f"Chart (Technician), Flux (Regime), Edge (Flow), Catalyst (Events), "
        f"Arbiter (Judge).\n\n"
        f"POSITION: {position_summary()} | DTE {dte} | spot ${mu_price:.2f} | "
        f"moneyness {moneyness:+.2f}\n"
        f"Live peer correlations to {POSITION['ticker']}: {peer_corr_str}.\n\n"
        f"CATALYSTS BETWEEN {today} AND {end}:\n{cat_block}\n\n"
    )

    if question:
        prompt = base + (
            f"USER QUESTION: {question}\n\n"
            f"OUTPUT (under 400 words, Telegram-friendly, no markdown headers):\n"
            f"1) One sharp line per persona answering the user's question (skip "
            f"   personas with nothing material to add).\n"
            f"2) Arbiter VERDICT — direct answer to the question + concrete numbers + "
            f"   the one piece of evidence that would change the view.\n"
            f"Be specific. Use the position data, catalysts, and peer correlations above."
        )
    else:
        prompt = base + (
            f"OUTPUT (under 350 words, Telegram-friendly, no markdown headers):\n"
            f"1) One sharp line per persona (skip personas with nothing to add).\n"
            f"2) For EACH catalyst: bullish/bearish/2-way + probable MU move size + "
            f"   1-line reason.\n"
            f"3) Arbiter VERDICT (HOLD/REDUCE/SELL) + the one trigger that flips it.\n"
        )

    for model in COMMITTEE_MODELS:
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.3, "max_tokens": 3000},
                timeout=90,
            )
            if resp.status_code != 200:
                continue
            content = resp.json()["choices"][0]["message"]["content"].strip()
            if content:
                return content, model
        except Exception:
            continue
    return "(committee unavailable)", "none"


def deliver_committee_to_telegram(
    end: date | None = None,
    *,
    refresh: bool = False,
    skip_committee: bool = False,
    question: str | None = None,
) -> str:
    """Pull catalysts, call committee, send to Telegram. Returns message text.

    If `question` is provided, the committee answers that question
    (catalysts + position become context). Otherwise runs the standard
    catalyst-impact analysis.
    """
    if refresh:
        refresh_cache()

    today = date.today()
    if end is None:
        end = date.fromisoformat(POSITION["expiry"])

    catalysts = filter_catalysts(today, end)

    analysis, model = ("", "")
    if not skip_committee:
        analysis, model = call_committee(catalysts, end, question=question)

    mu_price = get_live_price(POSITION["ticker"]) or 0
    moneyness = position_moneyness(mu_price) if mu_price else 0
    dte = (end - today).days
    cat_block = "\n".join(
        f"  {d} {d.strftime('%a')}  {h:02d}:{m:02d} ET  —  {desc}"
        for d, h, m, desc in catalysts
    ) or "(none)"

    title = (
        f"COMMITTEE — Q&A" if question
        else f"COMMITTEE — Catalysts to {end}"
    )
    msg_parts = [
        f"\U0001f3db️ <b>{title}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
    ]
    if question:
        msg_parts.append(f"<b>Question:</b> <i>{question}</i>")
        msg_parts.append("")
    msg_parts += [
        f"<b>Window:</b> {today} → {end}  ({dte}d)",
        f"<b>Position:</b> {position_summary()}",
        f"<b>Spot:</b> {POSITION['ticker']} ${mu_price:.2f} (moneyness {moneyness:+.2f})",
        "",
        f"<b>{len(catalysts)} CATALYSTS:</b>",
        f"<pre>{cat_block}</pre>",
    ]
    if analysis:
        msg_parts += ["", "<b>COMMITTEE:</b>", analysis, "", f"<i>Model: {model}</i>"]
    msg_parts += ["<code>#COMMITTEE #CATALYSTS</code>"]
    msg = "\n".join(msg_parts)

    send_alert(msg)
    return msg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-committee", action="store_true",
                    help="Skip the DeepSeek committee call")
    ap.add_argument("--no-telegram", action="store_true",
                    help="Print only; do not send to Telegram")
    ap.add_argument("--refresh", action="store_true",
                    help="Force-refresh the catalyst cache before listing")
    ap.add_argument("--until", type=str, default=None,
                    help="End date YYYY-MM-DD (default: position expiry)")
    args = ap.parse_args()

    today = date.today()
    end = date.fromisoformat(args.until) if args.until else date.fromisoformat(POSITION["expiry"])

    if args.refresh:
        print("Refreshing catalyst cache...")
    catalysts = filter_catalysts(today, end) if not args.refresh else (
        refresh_cache() or filter_catalysts(today, end)
    )

    print(f"=== {len(catalysts)} catalysts between {today} and {end} ===\n")
    for d, h, m, desc in catalysts:
        print(f"  {d} {d.strftime('%a')}  {h:02d}:{m:02d} ET  —  {desc}")

    if args.no_telegram:
        analysis, model = ("", "")
        if not args.no_committee:
            print("\nCalling committee (this hits the DeepSeek API)...")
            analysis, model = call_committee(catalysts, end)
            print(f"\n--- Committee ({model}) ---\n{analysis}\n")
    else:
        deliver_committee_to_telegram(
            end=end,
            refresh=False,  # already refreshed above if needed
            skip_committee=args.no_committee,
        )
        print(f"\nDelivered to Telegram.")


if __name__ == "__main__":
    main()
