"""Smart Level Alerts - DeepSeek analyzes WHY a level was crossed."""

import requests
from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, POSITION, position_summary, PEERS, FUTURES
from .llm import ask


def analyze_level_cross(ticker: str, price: float, level: float, direction: str) -> str:
    """When MU crosses a key level, gather context and ask DeepSeek WHY."""
    from .price_monitor import get_live_price, get_prev_close

    # Gather peer/VIX context
    context_parts = [f"MU just crossed {'above' if direction == 'up' else 'below'} ${level:.0f}, now at ${price:.2f}."]

    vix = get_live_price("^VIX")
    if vix:
        context_parts.append(f"VIX: {vix:.1f}")

    for peer in PEERS[:4]:
        pp = get_live_price(peer)
        pc = get_prev_close(peer)
        if pp and pc and pc > 0:
            pct = ((pp - pc) / pc) * 100
            context_parts.append(f"{peer}: {pct:+.1f}%")

    for fticker, fname in FUTURES.items():
        fp = get_live_price(fticker)
        fc = get_prev_close(fticker)
        if fp and fc and fc > 0:
            fpct = ((fp - fc) / fc) * 100
            short = {"S&P 500 Futures": "S&P", "Nasdaq Futures": "NQ", "Dow Futures": "DOW"}
            context_parts.append(f"{short.get(fname, fname)}: {fpct:+.1f}%")

    context = " | ".join(context_parts)

    prompt = (
        f"You are a real-time semiconductor trading analyst. MU just hit a key price level.\n\n"
        f"Context: {context}\n\n"
        f"POSITION: {position_summary()}.\n\n"
        f"In 2-3 sentences: WHY is MU moving (sector-wide? MU-specific? macro?), "
        f"is this move likely to continue or reverse, and should the trader act? Be direct."
    )

    return ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
               label="smart_alerts", fallback="Analysis unavailable.")
