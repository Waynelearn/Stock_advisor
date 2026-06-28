"""Periodic DeepSeek pricing updater.

Scrapes https://api-docs.deepseek.com/quick_start/pricing once per day,
parses the page with DeepSeek itself (resilient to HTML format changes), and
writes to ``.deepseek_pricing.json`` so ``deepseek_client.PRICING`` picks up
new rates automatically — e.g. after the v4-pro 75% discount expires on
2026-05-31, this module will detect the higher rates and flip the cache.

Design:
  - One scrape per 24 hours (enforced via a state file).
  - Material rate changes (>5% swing or new/removed model) fire a Telegram
    alert so you know the cost regime shifted.
  - All failures are silent in the live alert system (falls back to whatever
    is currently cached, or to hardcoded values in deepseek_client.py).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST
from .llm import ask

PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing"
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".deepseek_pricing.json")
STATE_FILE = os.path.join(os.path.dirname(__file__), ".pricing_updater_state.json")
REFRESH_INTERVAL_HOURS = 24


def _fetch_page() -> str | None:
    try:
        r = requests.get(
            PRICING_URL,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; mu-advisor)"},
        )
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[PRICING] fetch failed: {e}")
        return None


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)[:8000]


def _parse_with_deepseek(text: str) -> dict | None:
    """Use DeepSeek to extract structured pricing JSON from the page text."""
    prompt = (
        "Extract the current DeepSeek API pricing from this docs page text. "
        "Output ONLY valid JSON, no markdown, no commentary, in this exact schema:\n"
        '{"models": [{"id": "<model-id>", '
        '"input_cache_miss": <float>, "input_cache_hit": <float>, '
        '"output": <float>, "discount_pct": <int>, '
        '"discount_expires": "<ISO date or null>"}]}\n\n'
        "Rules:\n"
        "- All prices are USD per 1 MILLION tokens.\n"
        "- If a discount is currently active, return the DISCOUNTED price "
        "(the price the user pays NOW). Set discount_pct to the percentage off "
        "and discount_expires to the expiry date in ISO format. If no discount, "
        "discount_pct=0 and discount_expires=null.\n"
        "- Include every model the page mentions (deepseek-v4-pro, deepseek-v4-flash, "
        "and any deprecated aliases like deepseek-chat or deepseek-reasoner).\n"
        "- For deprecated aliases, use the same prices as the model they alias.\n\n"
        f"PAGE TEXT:\n{text}"
    )
    try:
        content = ask(prompt, tier="extract", temperature=0.0, max_tokens=3000,
                      label="pricing_updater")
        if not content:
            return None
        # Strip markdown code fences if the model added them
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if m:
            content = m.group(1)
        return json.loads(content)
    except Exception as e:
        print(f"[PRICING] parse failed: {e}")
        return None


def load_cached() -> dict | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


def _diff_pricing(old: dict | None, new: dict) -> list[str]:
    """Return human-readable diff lines if there's a material change (>5%)."""
    lines: list[str] = []
    old_map = {m["id"]: m for m in (old or {}).get("models", [])}
    new_map = {m["id"]: m for m in new.get("models", [])}

    for mid, nm in new_map.items():
        om = old_map.get(mid)
        if om is None:
            lines.append(f"  • {mid}: NEW model — output ${nm.get('output', 0):.4f}/M")
            continue
        for field in ("input_cache_miss", "output"):
            ov = om.get(field) or 0
            nv = nm.get(field) or 0
            if ov and abs(nv - ov) / ov > 0.05:
                lines.append(
                    f"  • {mid} {field}: ${ov:.4f} → ${nv:.4f} "
                    f"({(nv-ov)/ov*100:+.0f}%)"
                )
        old_pct = om.get("discount_pct") or 0
        new_pct = nm.get("discount_pct") or 0
        if old_pct != new_pct:
            lines.append(f"  • {mid} discount: {old_pct}% → {new_pct}%")

    for mid in old_map:
        if mid not in new_map:
            lines.append(f"  • {mid}: REMOVED")

    return lines


def _save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(
                {"last_check": datetime.now(timezone.utc).isoformat()}, f
            )
    except Exception:
        pass


def _should_refresh(force: bool = False) -> bool:
    if force:
        return True
    if not os.path.exists(STATE_FILE):
        return True
    try:
        with open(STATE_FILE) as f:
            last = json.load(f).get("last_check")
        if not last:
            return True
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        elapsed_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed_h >= REFRESH_INTERVAL_HOURS
    except Exception:
        return True


def fetch_and_update(force: bool = False, notify: bool = True) -> dict | None:
    """Fetch the live pricing page, parse, save, and notify on material changes.

    Returns the new pricing dict, or None on failure.
    """
    if not _should_refresh(force):
        return load_cached()

    html = _fetch_page()
    if not html:
        return load_cached()

    text = _extract_text(html)
    parsed = _parse_with_deepseek(text)
    if not parsed or not parsed.get("models"):
        return load_cached()

    parsed["fetched_at"] = datetime.now(timezone.utc).isoformat()

    old = load_cached()
    diff_lines = _diff_pricing(old, parsed)

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(parsed, f, indent=2)
    except Exception as e:
        print(f"[PRICING] cache write failed: {e}")

    _save_state()

    # Reload deepseek_client.PRICING to use the new rates immediately
    try:
        from . import deepseek_client
        deepseek_client.reload_pricing()
    except Exception:
        pass

    # Notify on material changes (skip on first-ever fetch)
    if notify and old and diff_lines:
        try:
            from .bot import send_alert
            send_alert(
                "\U0001f4b0 <b>DeepSeek pricing updated</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(diff_lines)
                + f"\n\n<i>Live page checked: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>"
                + "\n<code>#PRICING</code>"
            )
        except Exception as e:
            print(f"[PRICING] alert failed: {e}")

    return parsed


def daily_pricing_check():
    """Idempotent entry point for the cron tick."""
    fetch_and_update(force=False, notify=True)


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    result = fetch_and_update(force=force, notify=False)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print("(no result)")
