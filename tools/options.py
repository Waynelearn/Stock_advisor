import yfinance as yf
import numpy as np
import pandas as pd
from scipy import stats
from datetime import datetime


class OptionsAnalyzer:
    """Analyze options chains, IV, and Greeks."""

    @staticmethod
    def get_chain(ticker: str, expiry: str = None) -> dict:
        """Get options chain. If expiry is None, uses nearest."""
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return {'error': 'No options data available'}
        if expiry is None:
            expiry = expirations[0]
        elif expiry not in expirations:
            nearest = min(expirations, key=lambda x: abs(
                datetime.strptime(x, '%Y-%m-%d') - datetime.strptime(expiry, '%Y-%m-%d')
            ))
            expiry = nearest
        chain = t.option_chain(expiry)
        return {
            'expiry': expiry,
            'expirations': list(expirations),
            'calls': chain.calls,
            'puts': chain.puts,
        }

    @staticmethod
    def iv_surface(ticker: str, strike_range: tuple = None) -> pd.DataFrame:
        """Build IV surface across expirations and strikes."""
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return pd.DataFrame()

        info = t.fast_info
        current = info.last_price

        if strike_range is None:
            strike_range = (current * 0.85, current * 1.15)

        rows = []
        for exp in expirations[:8]:  # limit to 8 nearest
            try:
                chain = t.option_chain(exp)
                dte = (datetime.strptime(exp, '%Y-%m-%d') - datetime.now()).days

                for _, row in chain.calls.iterrows():
                    if strike_range[0] <= row['strike'] <= strike_range[1]:
                        rows.append({
                            'expiry': exp,
                            'dte': dte,
                            'strike': row['strike'],
                            'type': 'call',
                            'bid': row['bid'],
                            'ask': row['ask'],
                            'mid': (row['bid'] + row['ask']) / 2,
                            'iv': row['impliedVolatility'],
                            'volume': row['volume'],
                            'oi': row['openInterest'],
                            'moneyness': row['strike'] / current,
                        })
                for _, row in chain.puts.iterrows():
                    if strike_range[0] <= row['strike'] <= strike_range[1]:
                        rows.append({
                            'expiry': exp,
                            'dte': dte,
                            'strike': row['strike'],
                            'type': 'put',
                            'bid': row['bid'],
                            'ask': row['ask'],
                            'mid': (row['bid'] + row['ask']) / 2,
                            'iv': row['impliedVolatility'],
                            'volume': row['volume'],
                            'oi': row['openInterest'],
                            'moneyness': row['strike'] / current,
                        })
            except Exception:
                continue

        return pd.DataFrame(rows)

    @staticmethod
    def skew(ticker: str, expiry: str = None) -> dict:
        """Measure put-call IV skew for a given expiry."""
        chain_data = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain_data:
            return chain_data

        t = yf.Ticker(ticker)
        current = t.fast_info.last_price
        calls = chain_data['calls']
        puts = chain_data['puts']

        # ATM IV
        atm_call = calls.iloc[(calls['strike'] - current).abs().argsort()[:1]]
        atm_put = puts.iloc[(puts['strike'] - current).abs().argsort()[:1]]
        atm_iv = (atm_call['impliedVolatility'].values[0] + atm_put['impliedVolatility'].values[0]) / 2

        # 25-delta approximation (roughly 5% OTM)
        otm_put_strike = current * 0.95
        otm_call_strike = current * 1.05
        otm_put = puts.iloc[(puts['strike'] - otm_put_strike).abs().argsort()[:1]]
        otm_call = calls.iloc[(calls['strike'] - otm_call_strike).abs().argsort()[:1]]

        put_iv = otm_put['impliedVolatility'].values[0]
        call_iv = otm_call['impliedVolatility'].values[0]

        return {
            'expiry': chain_data['expiry'],
            'current': current,
            'atm_iv': atm_iv,
            'otm_put_iv': put_iv,
            'otm_call_iv': call_iv,
            'put_call_skew': put_iv - call_iv,
            'skew_ratio': put_iv / call_iv,
            'risk_reversal': call_iv - put_iv,
        }

    @staticmethod
    def pcr(ticker: str, expiry: str = None) -> dict:
        """Put/Call ratio by volume and open interest."""
        chain_data = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain_data:
            return chain_data

        calls = chain_data['calls']
        puts = chain_data['puts']

        call_vol = calls['volume'].sum()
        put_vol = puts['volume'].sum()
        call_oi = calls['openInterest'].sum()
        put_oi = puts['openInterest'].sum()

        return {
            'expiry': chain_data['expiry'],
            'call_volume': call_vol,
            'put_volume': put_vol,
            'pcr_volume': put_vol / call_vol if call_vol > 0 else None,
            'call_oi': call_oi,
            'put_oi': put_oi,
            'pcr_oi': put_oi / call_oi if call_oi > 0 else None,
        }

    @staticmethod
    def max_pain(ticker: str, expiry: str = None) -> dict:
        """Calculate max pain strike."""
        chain_data = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain_data:
            return chain_data

        calls = chain_data['calls']
        puts = chain_data['puts']
        strikes = sorted(set(calls['strike'].tolist() + puts['strike'].tolist()))

        min_pain = float('inf')
        max_pain_strike = None

        for test_strike in strikes:
            call_pain = 0
            put_pain = 0
            for _, c in calls.iterrows():
                if test_strike > c['strike']:
                    call_pain += (test_strike - c['strike']) * c['openInterest']
            for _, p in puts.iterrows():
                if test_strike < p['strike']:
                    put_pain += (p['strike'] - test_strike) * p['openInterest']
            total = call_pain + put_pain
            if total < min_pain:
                min_pain = total
                max_pain_strike = test_strike

        return {
            'expiry': chain_data['expiry'],
            'max_pain': max_pain_strike,
            'total_pain_at_max': min_pain,
        }

    @staticmethod
    def unusual_activity(ticker: str, expiry: str = None, vol_threshold: float = 2.0) -> pd.DataFrame:
        """Find options with unusually high volume relative to open interest."""
        chain_data = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain_data:
            return pd.DataFrame()

        rows = []
        for opt_type, df in [('call', chain_data['calls']), ('put', chain_data['puts'])]:
            for _, row in df.iterrows():
                if row['openInterest'] > 0 and row['volume'] > 0:
                    ratio = row['volume'] / row['openInterest']
                    if ratio >= vol_threshold:
                        rows.append({
                            'type': opt_type,
                            'strike': row['strike'],
                            'volume': row['volume'],
                            'oi': row['openInterest'],
                            'vol_oi_ratio': ratio,
                            'iv': row['impliedVolatility'],
                            'bid': row['bid'],
                            'ask': row['ask'],
                        })

        result = pd.DataFrame(rows)
        if not result.empty:
            result = result.sort_values('vol_oi_ratio', ascending=False)
        return result
