#!/usr/bin/env python3
"""Quick check of actual spread value from option chain."""
import sys
import yfinance as yf

t = yf.Ticker("MU")
try:
    chain = t.option_chain("2026-03-20")
except Exception as e:
    print(f"Error fetching option chain: {e}")
    sys.exit(1)

calls = chain.calls
c380_df = calls[calls["strike"] == 380]
c400_df = calls[calls["strike"] == 400]
if c380_df.empty or c400_df.empty:
    print("Error: 380 or 400 strike not found in option chain")
    sys.exit(1)

c380 = c380_df.iloc[0]
c400 = c400_df.iloc[0]

print("=== MU 380 Call ===")
print(f"  Bid: {c380['bid']:.2f}  Ask: {c380['ask']:.2f}  Mid: {(c380['bid']+c380['ask'])/2:.2f}  Last: {c380['lastPrice']:.2f}  IV: {c380['impliedVolatility']:.2%}")

print("\n=== MU 400 Call ===")
print(f"  Bid: {c400['bid']:.2f}  Ask: {c400['ask']:.2f}  Mid: {(c400['bid']+c400['ask'])/2:.2f}  Last: {c400['lastPrice']:.2f}  IV: {c400['impliedVolatility']:.2%}")

long_mid = (c380["bid"] + c380["ask"]) / 2 if c380["bid"] > 0 else c380["lastPrice"]
short_mid = (c400["bid"] + c400["ask"]) / 2 if c400["bid"] > 0 else c400["lastPrice"]
spread_val = long_mid - short_mid

source = "mid" if c380["bid"] > 0 else "lastPrice (market closed)"
print(f"\n=== Spread Value ({source}) ===")
print(f"  Long 380:  ${long_mid:.3f}")
print(f"  Short 400: ${short_mid:.3f}")
print(f"  Net spread:    ${spread_val:.3f}")
print(f"  Entry:         $11.897")
pnl_per = spread_val - 11.897
print(f"  P&L/contract:  ${pnl_per:.3f}")
print(f"  Total P&L (500x): ${pnl_per * 500 * 100:+,.0f}")

info = t.fast_info
print(f"\nMU last price: ${info['lastPrice']:.2f}")
