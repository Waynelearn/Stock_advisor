"""Quick spread valuation tool — always uses market prices + Black-Scholes, never just intrinsic.

Usage: conda run -n mu_advisor python3 tools/spread_value.py
"""
import sys
import math
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from scipy.stats import norm

sys.path.insert(0, "/home/wayne/website/mu_advisor")
from alerts.config import POSITION, TZ_ET


def bs_call(S, K, T, r, sigma):
    if T <= 0:
        return max(0, S - K)
    if sigma <= 0:
        return max(0, S - K)
    d1 = (math.log(S / K) + (r + sigma**2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def get_spread_value():
    if not POSITION.get("contracts"):
        print("No active position.")
        return

    ticker = POSITION["ticker"]
    long_strike = POSITION["long_strike"]
    short_strike = POSITION["short_strike"]
    entry = POSITION["entry_price"]
    contracts = POSITION["contracts"]
    expiry_str = POSITION["expiry"]
    width = short_strike - long_strike

    t = yf.Ticker(ticker)
    info = t.info
    price = info.get("preMarketPrice") or info.get("regularMarketPrice") or info.get("previousClose")
    prev_close = info.get("previousClose")

    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TZ_ET))
    now = datetime.now(ZoneInfo(TZ_ET))
    dte = (expiry_date - now).days
    T = max(dte / 365.0, 0.001)
    r = 0.0375

    print(f"{'='*60}")
    print(f"SPREAD VALUATION — {ticker} {long_strike}/{short_strike} BCS")
    print(f"{'='*60}")
    print(f"MU: ${price:.2f} (prev close ${prev_close:.2f})")
    print(f"DTE: {dte} | Expiry: {expiry_str}")
    print(f"Position: {contracts}x | Entry: ${entry:.3f}")
    print()

    # Method 1: Market prices (chain)
    try:
        chain = t.option_chain(expiry_str)
        calls = chain.calls
        long_row = calls[calls.strike == long_strike].iloc[0]
        short_row = calls[calls.strike == short_strike].iloc[0]

        # Last traded
        spread_last = long_row.lastPrice - short_row.lastPrice

        # Bid/ask mid
        if long_row.bid > 0 and short_row.bid > 0:
            long_mid = (long_row.bid + long_row.ask) / 2
            short_mid = (short_row.bid + short_row.ask) / 2
            spread_mid = long_mid - short_mid
        else:
            spread_mid = None

        # IV from chain
        long_iv = float(long_row.impliedVolatility or 0)
        short_iv = float(short_row.impliedVolatility or 0)

        print("METHOD 1: Market Prices (options chain)")
        print(f"  {long_strike}C: last=${long_row.lastPrice:.2f}  bid=${long_row.bid:.2f}  ask=${long_row.ask:.2f}  IV={long_iv:.1%}")
        print(f"  {short_strike}C: last=${short_row.lastPrice:.2f}  bid=${short_row.bid:.2f}  ask=${short_row.ask:.2f}  IV={short_iv:.1%}")
        print(f"  Spread (last): ${spread_last:.2f}")
        if spread_mid:
            print(f"  Spread (mid):  ${spread_mid:.2f}")
        print()
    except Exception as e:
        spread_last = None
        spread_mid = None
        long_iv = 0.70
        short_iv = 0.70
        print(f"  Chain error: {e}")
        print()

    # Method 2: Black-Scholes
    iv_long = long_iv if long_iv > 0.01 else 0.70
    iv_short = short_iv if short_iv > 0.01 else 0.70
    bs_long = bs_call(price, long_strike, T, r, iv_long)
    bs_short = bs_call(price, short_strike, T, r, iv_short)
    spread_bs = bs_long - bs_short

    print(f"METHOD 2: Black-Scholes (IV: {iv_long:.1%} / {iv_short:.1%})")
    print(f"  {long_strike}C BS: ${bs_long:.2f}")
    print(f"  {short_strike}C BS: ${bs_short:.2f}")
    print(f"  Spread (BS):   ${spread_bs:.2f}")
    print()

    # Method 3: Intrinsic only (expiry value)
    intrinsic = max(0, min(price - long_strike, width))
    print(f"METHOD 3: Intrinsic Only (expiry value)")
    print(f"  Spread (intrinsic): ${intrinsic:.2f}")
    print(f"  *** THIS IS ONLY VALID AT EXPIRY — DO NOT USE FOR CURRENT VALUE ***")
    print()

    # Best estimate
    best = spread_mid if spread_mid and spread_mid > 0 else (spread_last if spread_last and spread_last > 0 else spread_bs)
    print(f"{'='*60}")
    print(f"BEST ESTIMATE: ${best:.2f}")
    print(f"{'='*60}")
    pnl_per = best - entry
    pnl_total = pnl_per * contracts * 100
    print(f"  Entry:     ${entry:.3f}")
    print(f"  Current:   ${best:.2f}")
    print(f"  P&L/spread: ${pnl_per:+.2f} ({pnl_per/entry*100:+.1f}%)")
    print(f"  P&L total:  ${pnl_total:+,.0f}")
    print()
    print(f"  If sold now ({contracts}x at ${best:.2f}):")
    print(f"    Proceeds:  ${best * contracts * 100:,.0f}")
    print(f"    Cost was:  ${entry * contracts * 100:,.0f}")
    print(f"    Net P&L:   ${pnl_total:+,.0f}")
    print()
    print(f"  If held to expiry at current MU ${price:.2f}:")
    print(f"    Intrinsic: ${intrinsic:.2f}")
    expiry_pnl = (intrinsic - entry) * contracts * 100
    print(f"    P&L:       ${expiry_pnl:+,.0f}")


if __name__ == "__main__":
    get_spread_value()
