"""Test suite for mu_advisor tools."""
import sys
sys.path.insert(0, '/home/wayne/website/mu_advisor')

import numpy as np
import pandas as pd
from tools.price_data import PriceData
from tools.volatility import VolatilityAnalyzer
from tools.probability import ProbabilityEngine
from tools.options import OptionsAnalyzer
from tools.spreads import SpreadAnalyzer

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} -- {detail}")


def test_price_data():
    print("\n=== PriceData ===")

    # get() returns DataFrame with expected columns
    df = PriceData.get('MU')
    check("get returns DataFrame", isinstance(df, pd.DataFrame))
    check("has rows", len(df) > 0, f"got {len(df)} rows")
    for col in ['Open', 'High', 'Low', 'Close', 'Volume', 'Return', 'LogReturn', 'Range', 'RangePct']:
        check(f"has column {col}", col in df.columns)

    # current_price returns float
    price = PriceData.current_price('MU')
    check("current_price is float", isinstance(price, (float, np.floating)), f"type={type(price)}")
    check("current_price > 0", price > 0, f"price={price}")

    # last_n_closes
    closes = PriceData.last_n_closes('MU', 5)
    check("last_n_closes len", len(closes) == 5, f"got {len(closes)}")
    check("closes are positive", all(c > 0 for c in closes))

    # summary
    s = PriceData.summary('MU')
    check("summary is dict", isinstance(s, dict))
    for key in ['current', 'prev_close', 'change_pct', 'high_90d', 'low_90d', 'avg_volume']:
        check(f"summary has {key}", key in s)
    check("high >= current", s['high_90d'] >= s['current'])
    check("low <= current", s['low_90d'] <= s['current'])

    # multi_summary
    ms = PriceData.multi_summary(['MU', 'NVDA'])
    check("multi_summary has 2 rows", len(ms) == 2, f"got {len(ms)}")

    # support_resistance
    sr = PriceData.support_resistance('MU')
    check("support_resistance is dict", isinstance(sr, dict))
    check("has current", 'current' in sr)
    check("has resistance list", isinstance(sr.get('resistance'), list))
    check("has support list", isinstance(sr.get('support'), list))

    # caching
    df2 = PriceData.get('MU')
    check("cache hit (same object)", df is df2)


def test_volatility():
    print("\n=== VolatilityAnalyzer ===")

    # realized vol
    rv = VolatilityAnalyzer.realized('MU')
    check("realized returns dict", isinstance(rv, dict))
    check("has 20d_daily", '20d_daily' in rv)
    check("has 60d_annual", '60d_annual' in rv)
    check("daily < annual", rv['20d_daily'] < rv['20d_annual'])
    check("vol is positive", rv['20d_daily'] > 0)

    # parkinson
    park = VolatilityAnalyzer.parkinson('MU')
    check("parkinson returns float", isinstance(park, (float, np.floating)))
    check("parkinson > 0", park > 0)
    check("parkinson reasonable (5%-300%)", 0.05 < park < 3.0, f"got {park}")

    # yang_zhang
    yz = VolatilityAnalyzer.yang_zhang('MU')
    check("yang_zhang returns float", isinstance(yz, (float, np.floating)))
    check("yang_zhang > 0", yz > 0)
    check("yang_zhang reasonable (5%-300%)", 0.05 < yz < 3.0, f"got {yz}")

    # full_report
    fr = VolatilityAnalyzer.full_report('MU')
    check("full_report has realized", 'realized' in fr)
    check("full_report has parkinson", 'parkinson_annual' in fr)
    check("full_report has yang_zhang", 'yang_zhang_annual' in fr)

    # vol term structure
    vts = VolatilityAnalyzer.vol_term_structure('MU')
    check("vol_term_structure is DataFrame", isinstance(vts, pd.DataFrame))
    check("has multiple windows", len(vts) >= 4, f"got {len(vts)}")


def test_probability():
    print("\n=== ProbabilityEngine ===")

    current = PriceData.current_price('MU')

    # finish_above with target above current
    pa = ProbabilityEngine.finish_above('MU', current * 1.05, days=5)
    check("finish_above returns dict", isinstance(pa, dict))
    check("prob_gbm in [0, 100]", 0 <= pa['prob_gbm'] <= 100, f"got {pa['prob_gbm']}")
    check("prob_historical in [0, 100]", 0 <= pa['prob_historical'] <= 100)
    check("prob < 50 for +5% target", pa['prob_gbm'] < 50, f"got {pa['prob_gbm']}")

    # finish_above at current price should be ~50%
    pa_atm = ProbabilityEngine.finish_above('MU', current, days=1)
    check("ATM prob ~50%", 40 < pa_atm['prob_gbm'] < 60, f"got {pa_atm['prob_gbm']}")

    # finish_below is complement
    pb = ProbabilityEngine.finish_below('MU', current * 1.05, days=5)
    check("finish_below + finish_above ~ 100",
          abs(pa['prob_gbm'] + pb['prob_gbm'] - 100) < 0.01,
          f"sum = {pa['prob_gbm'] + pb['prob_gbm']}")

    # touch_target >= finish_above
    touch = ProbabilityEngine.touch_target('MU', current * 1.02, days=5)
    finish = ProbabilityEngine.finish_above('MU', current * 1.02, days=5)
    check("touch_prob >= finish_prob",
          touch['prob_touch'] >= finish['prob_gbm'] - 0.1,
          f"touch={touch['prob_touch']}, finish={finish['prob_gbm']}")

    # range_probability
    rp = ProbabilityEngine.range_probability('MU', current * 0.95, current * 1.05, days=5)
    check("range_prob is dict", isinstance(rp, dict))
    check("range parts sum ~100",
          abs(rp['prob_below'] + rp['prob_in_range'] + rp['prob_above'] - 100) < 0.1,
          f"sum = {rp['prob_below'] + rp['prob_in_range'] + rp['prob_above']}")

    # expected_move
    em = ProbabilityEngine.expected_move('MU', days=1)
    check("expected_move has bounds", 'upper_bound' in em and 'lower_bound' in em)
    check("upper > current > lower",
          em['upper_bound'] > em['current'] > em['lower_bound'])
    check("1sd_range tuple", isinstance(em['1sd_range'], tuple))

    # multi_target
    mt = ProbabilityEngine.multi_target('MU', [current * 0.95, current, current * 1.05])
    check("multi_target returns 3 results", len(mt) == 3)
    check("probabilities descending", mt[0]['prob_gbm'] > mt[1]['prob_gbm'] > mt[2]['prob_gbm'])


def test_options():
    print("\n=== OptionsAnalyzer ===")

    # get_chain
    chain = OptionsAnalyzer.get_chain('MU')
    if 'error' in chain:
        check("get_chain available", False, chain['error'])
        return
    check("chain has expiry", 'expiry' in chain)
    check("chain has calls", isinstance(chain['calls'], pd.DataFrame))
    check("chain has puts", isinstance(chain['puts'], pd.DataFrame))
    check("calls not empty", len(chain['calls']) > 0)
    check("puts not empty", len(chain['puts']) > 0)

    # pcr
    pcr = OptionsAnalyzer.pcr('MU')
    check("pcr has pcr_volume", 'pcr_volume' in pcr)
    check("pcr_volume > 0", pcr['pcr_volume'] is not None and pcr['pcr_volume'] > 0)

    # max_pain
    mp = OptionsAnalyzer.max_pain('MU')
    check("max_pain has strike", 'max_pain' in mp)
    check("max_pain > 0", mp['max_pain'] is not None and mp['max_pain'] > 0)

    # unusual_activity
    ua = OptionsAnalyzer.unusual_activity('MU')
    check("unusual_activity is DataFrame", isinstance(ua, pd.DataFrame))

    # skew
    sk = OptionsAnalyzer.skew('MU')
    if 'error' not in sk:
        check("skew has atm_iv", 'atm_iv' in sk)
        check("atm_iv > 0", sk['atm_iv'] > 0)


def test_spreads():
    print("\n=== SpreadAnalyzer ===")

    # Get a valid expiry first
    chain = OptionsAnalyzer.get_chain('MU')
    if 'error' in chain:
        check("chain available for spreads", False, chain['error'])
        return

    # Find two valid OTM strikes for a bull call spread
    calls = chain['calls']
    current = PriceData.current_price('MU')
    strikes = sorted(calls['strike'].unique())
    # Pick ATM and slightly OTM strikes (both >= current for valid debit spread)
    otm_strikes = [s for s in strikes if s >= current and s <= current * 1.10]
    if len(otm_strikes) < 2:
        check("enough OTM strikes for spread", False, f"only {len(otm_strikes)} OTM")
        return

    long_s = otm_strikes[0]
    short_s = otm_strikes[min(3, len(otm_strikes) - 1)]  # ~2-3 strikes wide

    # bull_call_spread
    bcs = SpreadAnalyzer.bull_call_spread('MU', long_s, short_s)
    if 'error' in bcs:
        check("bull_call_spread", False, bcs['error'])
    else:
        check("bcs has net_debit", 'net_debit' in bcs)
        check("bcs net_debit > 0", bcs['net_debit'] > 0, f"debit={bcs['net_debit']}")
        check("bcs max_profit > 0", bcs['max_profit'] > 0)
        check("bcs breakeven between strikes",
              long_s <= bcs['breakeven'] <= short_s,
              f"be={bcs['breakeven']}")
        check("bcs has risk_reward", bcs['risk_reward'] is not None)

        # simulate_pnl
        pnl_df = SpreadAnalyzer.simulate_pnl(bcs)
        check("simulate_pnl returns DataFrame", isinstance(pnl_df, pd.DataFrame))
        check("simulate_pnl has rows", len(pnl_df) > 0)
        check("pnl has price and pnl columns", 'price' in pnl_df.columns and 'pnl' in pnl_df.columns)

        # max P&L check at extremes
        max_pnl = pnl_df['pnl'].max()
        min_pnl = pnl_df['pnl'].min()
        check("max pnl ~ max_profit", abs(max_pnl - bcs['max_profit']) < 1,
              f"sim_max={max_pnl}, stated={bcs['max_profit']}")
        check("min pnl ~ -max_loss", abs(min_pnl + bcs['max_loss']) < 1,
              f"sim_min={min_pnl}, stated=-{bcs['max_loss']}")

        # probability_of_profit
        pop = SpreadAnalyzer.probability_of_profit('MU', bcs)
        check("pop has prob_of_profit", 'prob_of_profit' in pop)
        check("pop in [0, 100]", 0 <= pop['prob_of_profit'] <= 100)


def test_roundtable():
    print("\n=== Roundtable ===")
    from tools.roundtable import build_roundtable_prompt, gather_context, PERSONAS

    # PERSONAS structure
    check("has 9 personas", len(PERSONAS) == 9)
    for key in ['bull', 'bear', 'quant', 'macro', 'technician', 'flux', 'edge', 'catalyst', 'judge']:
        check(f"has {key} persona", key in PERSONAS)
        p = PERSONAS[key]
        check(f"{key} has name", 'name' in p and len(p['name']) > 0)
        check(f"{key} has mandate", 'mandate' in p and len(p['mandate']) > 20)
        check(f"{key} has icon", 'icon' in p and len(p['icon']) > 0)
        check(f"{key} has bias", 'bias' in p)

    # build_roundtable_prompt
    prompt = build_roundtable_prompt("Should I buy MU calls?")
    check("prompt is string", isinstance(prompt, str))
    check("prompt has topic", "Should I buy MU calls?" in prompt)
    check("prompt has BULL", "[BULL]" in prompt)
    check("prompt has BEAR", "[BEAR]" in prompt)
    check("prompt has JUDGE", "[JUDGE]" in prompt)
    check("prompt has FLUX", "[FLUX]" in prompt)
    check("prompt has FLOW", "[FLOW]" in prompt)
    check("prompt has EVENT", "[EVENT]" in prompt)
    check("prompt mentions groupthink", "groupthink" in prompt.lower())
    check("prompt has discussion format", "CROSS-EXAMINATION" in prompt)
    check("prompt has flow check", "FLOW CHECK" in prompt)
    check("prompt has regime check", "REGIME CHECK" in prompt)
    check("prompt has event sequencing", "EVENT SEQUENCING" in prompt)

    # with context
    prompt2 = build_roundtable_prompt("MU earnings", context={'price': 397, 'vol': '70%'})
    check("prompt with context has data", "397" in prompt2)

    # gather_context
    ctx = gather_context('MU')
    check("gather_context returns dict", isinstance(ctx, dict))
    check("context has price", 'price' in ctx)
    check("context has volatility", 'volatility' in ctx)
    check("context has expected_move_1d", 'expected_move_1d' in ctx)
    check("context has options_flow", 'options_flow' in ctx)


def run_all():
    global PASS, FAIL
    test_price_data()
    test_volatility()
    test_probability()
    test_options()
    test_spreads()
    test_roundtable()

    print(f"\n{'='*40}")
    print(f"RESULTS: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
    if FAIL == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"*** {FAIL} TESTS FAILED ***")
    return FAIL == 0


if __name__ == '__main__':
    success = run_all()
    sys.exit(0 if success else 1)
