import numpy as np
import pandas as pd
from scipy import stats
from .price_data import PriceData
from .options import OptionsAnalyzer


class SpreadAnalyzer:
    """Analyze vertical bull/bear spreads with P&L simulation."""

    @staticmethod
    def bull_call_spread(ticker: str, long_strike: float, short_strike: float,
                         expiry: str = None, contracts: int = 1) -> dict:
        """Analyze a bull call spread."""
        chain = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain:
            return chain

        calls = chain['calls']
        long_opt = calls[calls['strike'] == long_strike]
        short_opt = calls[calls['strike'] == short_strike]

        if long_opt.empty or short_opt.empty:
            # Find nearest strikes
            available = sorted(calls['strike'].unique())
            return {'error': f'Strikes not found. Available: {available[:20]}'}

        long_ask = long_opt['ask'].values[0]
        short_bid = short_opt['bid'].values[0]
        net_debit = long_ask - short_bid
        max_profit = (short_strike - long_strike) - net_debit
        breakeven = long_strike + net_debit
        width = short_strike - long_strike
        risk_reward = max_profit / net_debit if net_debit > 0 else None

        return {
            'type': 'Bull Call Spread',
            'expiry': chain['expiry'],
            'long_strike': long_strike,
            'short_strike': short_strike,
            'long_premium': long_ask,
            'short_premium': short_bid,
            'net_debit': net_debit,
            'max_loss': net_debit * contracts * 100,
            'max_profit': max_profit * contracts * 100,
            'breakeven': breakeven,
            'width': width,
            'risk_reward': risk_reward,
            'contracts': contracts,
            'long_iv': long_opt['impliedVolatility'].values[0],
            'short_iv': short_opt['impliedVolatility'].values[0],
        }

    @staticmethod
    def bull_put_spread(ticker: str, short_strike: float, long_strike: float,
                        expiry: str = None, contracts: int = 1) -> dict:
        """Analyze a bull put spread (credit spread)."""
        chain = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain:
            return chain

        puts = chain['puts']
        short_opt = puts[puts['strike'] == short_strike]
        long_opt = puts[puts['strike'] == long_strike]

        if long_opt.empty or short_opt.empty:
            available = sorted(puts['strike'].unique())
            return {'error': f'Strikes not found. Available: {available[:20]}'}

        short_bid = short_opt['bid'].values[0]
        long_ask = long_opt['ask'].values[0]
        net_credit = short_bid - long_ask
        width = short_strike - long_strike
        max_loss = (width - net_credit)
        breakeven = short_strike - net_credit

        return {
            'type': 'Bull Put Spread',
            'expiry': chain['expiry'],
            'short_strike': short_strike,
            'long_strike': long_strike,
            'short_premium': short_bid,
            'long_premium': long_ask,
            'net_credit': net_credit,
            'max_profit': net_credit * contracts * 100,
            'max_loss': max_loss * contracts * 100,
            'breakeven': breakeven,
            'width': width,
            'risk_reward': net_credit / max_loss if max_loss > 0 else None,
            'contracts': contracts,
        }

    @staticmethod
    def simulate_pnl(spread: dict, price_range: tuple = None, steps: int = 50) -> pd.DataFrame:
        """Simulate P&L at expiration across a range of prices."""
        if 'error' in spread:
            return pd.DataFrame()

        if price_range is None:
            center = (spread.get('long_strike', 0) + spread.get('short_strike', 0)) / 2
            width = spread.get('width', 10)
            price_range = (center - width * 3, center + width * 3)

        prices = np.linspace(price_range[0], price_range[1], steps)
        contracts = spread.get('contracts', 1)
        rows = []

        if spread['type'] == 'Bull Call Spread':
            for p in prices:
                long_value = max(0, p - spread['long_strike'])
                short_value = max(0, p - spread['short_strike'])
                spread_value = long_value - short_value
                pnl = (spread_value - spread['net_debit']) * contracts * 100
                rows.append({'price': p, 'pnl': pnl, 'spread_value': spread_value})

        elif spread['type'] == 'Bull Put Spread':
            for p in prices:
                short_value = max(0, spread['short_strike'] - p)
                long_value = max(0, spread['long_strike'] - p)
                spread_liability = short_value - long_value
                pnl = (spread['net_credit'] - spread_liability) * contracts * 100
                rows.append({'price': p, 'pnl': pnl, 'spread_liability': spread_liability})

        return pd.DataFrame(rows)

    @staticmethod
    def probability_of_profit(ticker: str, spread: dict, days: int = None, vol_window: int = 20) -> dict:
        """Estimate probability of profit for a spread at expiration."""
        if 'error' in spread:
            return spread

        df = PriceData.get(ticker)
        current = df['Close'].iloc[-1]
        returns = df['Return'].dropna()
        vol_daily = returns.tail(vol_window).std()

        if days is None:
            from datetime import datetime
            exp_date = datetime.strptime(spread['expiry'], '%Y-%m-%d')
            days = max(1, (exp_date - datetime.now()).days)

        vol_period = vol_daily * np.sqrt(days)
        breakeven = spread['breakeven']

        if spread['type'] == 'Bull Call Spread':
            # Profit if price > breakeven
            z = np.log(breakeven / current) / vol_period
            pop = (1 - stats.norm.cdf(z)) * 100
        elif spread['type'] == 'Bull Put Spread':
            # Profit if price > breakeven
            z = np.log(breakeven / current) / vol_period
            pop = (1 - stats.norm.cdf(z)) * 100

        # Max profit probability
        max_profit_price = spread['short_strike']
        z_max = np.log(max_profit_price / current) / vol_period
        prob_max = (1 - stats.norm.cdf(z_max)) * 100

        # Max loss probability
        if spread['type'] == 'Bull Call Spread':
            max_loss_price = spread['long_strike']
        else:
            max_loss_price = spread['long_strike']
        z_loss = np.log(max_loss_price / current) / vol_period
        prob_max_loss = stats.norm.cdf(z_loss) * 100

        return {
            'current': current,
            'breakeven': breakeven,
            'days_to_expiry': days,
            'vol_daily': vol_daily * 100,
            'vol_to_expiry': vol_period * 100,
            'prob_of_profit': pop,
            'prob_max_profit': prob_max,
            'prob_max_loss': prob_max_loss,
            'expected_value': (prob_max / 100 * spread.get('max_profit', 0)
                              - prob_max_loss / 100 * spread.get('max_loss', 0)),
        }

    @staticmethod
    def find_optimal_spread(ticker: str, expiry: str = None, spread_type: str = 'bull_call',
                            min_rr: float = 1.0, max_width: float = 20) -> list:
        """Screen for optimal spreads based on risk/reward and probability."""
        chain = OptionsAnalyzer.get_chain(ticker, expiry)
        if 'error' in chain:
            return []

        import yfinance as yf
        current = yf.Ticker(ticker).fast_info.last_price

        if spread_type == 'bull_call':
            calls = chain['calls']
            strikes = sorted(calls['strike'].unique())
            # Focus on ATM to slightly OTM
            relevant = [s for s in strikes if current * 0.95 <= s <= current * 1.15]
        else:
            puts = chain['puts']
            strikes = sorted(puts['strike'].unique())
            relevant = [s for s in strikes if current * 0.85 <= s <= current * 1.05]

        results = []
        for i, long_s in enumerate(relevant):
            for short_s in relevant[i+1:]:
                if short_s - long_s > max_width:
                    continue
                if spread_type == 'bull_call':
                    spread = SpreadAnalyzer.bull_call_spread(ticker, long_s, short_s, expiry)
                else:
                    spread = SpreadAnalyzer.bull_put_spread(ticker, short_s, long_s, expiry)

                if 'error' in spread:
                    continue
                if spread.get('risk_reward', 0) and spread['risk_reward'] >= min_rr:
                    pop = SpreadAnalyzer.probability_of_profit(ticker, spread)
                    results.append({
                        **spread,
                        'prob_of_profit': pop.get('prob_of_profit', 0),
                        'expected_value': pop.get('expected_value', 0),
                    })

        results.sort(key=lambda x: x.get('expected_value', 0), reverse=True)
        return results[:10]
