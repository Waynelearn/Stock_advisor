import numpy as np
from scipy import stats
from .price_data import PriceData
from .volatility import VolatilityAnalyzer


class ProbabilityEngine:
    """Estimate probabilities of price targets being hit."""

    @staticmethod
    def finish_above(ticker: str, target: float, days: int = 1, vol_window: int = 20) -> dict:
        """Probability of finishing above target in N trading days."""
        df = PriceData.get(ticker)
        current = df['Close'].iloc[-1]
        returns = df['Return'].dropna()

        vol_daily = returns.tail(vol_window).std()
        vol_period = vol_daily * np.sqrt(days)
        move_needed = np.log(target / current)

        # GBM model (zero drift for short-term)
        z = move_needed / vol_period
        prob_gbm = 1 - stats.norm.cdf(z)

        # With drift (using recent mean return)
        mu = returns.tail(vol_window).mean() * days
        z_drift = (move_needed - mu) / vol_period
        prob_drift = 1 - stats.norm.cdf(z_drift)

        # Historical frequency
        if days == 1:
            req_ret = target / current - 1
            if req_ret > 0:
                hist_count = (returns >= req_ret).sum()
            else:
                hist_count = (returns >= req_ret).sum()
            hist_prob = hist_count / len(returns)
        else:
            # Rolling N-day returns
            rolling = df['Close'].pct_change(days).dropna()
            req_ret = target / current - 1
            hist_count = (rolling >= req_ret).sum()
            hist_prob = hist_count / len(rolling)

        # Fat-tail adjusted (Student-t)
        nu = 5  # degrees of freedom for fat tails
        z_t = move_needed / vol_period
        prob_t = 1 - stats.t.cdf(z_t, df=nu)

        return {
            'ticker': ticker,
            'current': current,
            'target': target,
            'days': days,
            'gap_pct': (target / current - 1) * 100,
            'vol_daily': vol_daily * 100,
            'vol_period': vol_period * 100,
            'prob_gbm': prob_gbm * 100,
            'prob_with_drift': prob_drift * 100,
            'prob_historical': hist_prob * 100,
            'prob_fat_tail': prob_t * 100,
        }

    @staticmethod
    def finish_below(ticker: str, target: float, days: int = 1, vol_window: int = 20) -> dict:
        """Probability of finishing below target in N trading days."""
        result = ProbabilityEngine.finish_above(ticker, target, days, vol_window)
        return {
            **result,
            'prob_gbm': 100 - result['prob_gbm'],
            'prob_with_drift': 100 - result['prob_with_drift'],
            'prob_historical': 100 - result['prob_historical'],
            'prob_fat_tail': 100 - result['prob_fat_tail'],
        }

    @staticmethod
    def touch_target(ticker: str, target: float, days: int = 1, vol_window: int = 20) -> dict:
        """Probability of touching target at ANY point during the period (barrier probability).
        Uses reflection principle - always higher than finish_above."""
        df = PriceData.get(ticker)
        current = df['Close'].iloc[-1]
        returns = df['Return'].dropna()
        vol_daily = returns.tail(vol_window).std()
        vol_period = vol_daily * np.sqrt(days)
        move_needed = np.log(target / current)

        # Reflection principle: P(touch) = 2 * P(finish above) for zero drift
        z = move_needed / vol_period
        prob_finish = 1 - stats.norm.cdf(z)
        prob_touch = min(2 * prob_finish, 1.0)

        return {
            'ticker': ticker,
            'current': current,
            'target': target,
            'days': days,
            'prob_touch': prob_touch * 100,
            'prob_finish_above': prob_finish * 100,
        }

    @staticmethod
    def range_probability(ticker: str, lower: float, upper: float, days: int = 1, vol_window: int = 20) -> dict:
        """Probability of finishing between lower and upper bounds."""
        df = PriceData.get(ticker)
        current = df['Close'].iloc[-1]
        returns = df['Return'].dropna()
        vol_daily = returns.tail(vol_window).std()
        vol_period = vol_daily * np.sqrt(days)

        z_lower = np.log(lower / current) / vol_period
        z_upper = np.log(upper / current) / vol_period

        prob = stats.norm.cdf(z_upper) - stats.norm.cdf(z_lower)

        return {
            'ticker': ticker,
            'current': current,
            'lower': lower,
            'upper': upper,
            'days': days,
            'prob_in_range': prob * 100,
            'prob_below': stats.norm.cdf(z_lower) * 100,
            'prob_above': (1 - stats.norm.cdf(z_upper)) * 100,
        }

    @staticmethod
    def expected_move(ticker: str, days: int = 1, vol_window: int = 20, confidence: float = 0.68) -> dict:
        """Calculate expected move for a given confidence level."""
        df = PriceData.get(ticker)
        current = df['Close'].iloc[-1]
        returns = df['Return'].dropna()
        vol_daily = returns.tail(vol_window).std()
        vol_period = vol_daily * np.sqrt(days)

        z = stats.norm.ppf((1 + confidence) / 2)
        move = current * (np.exp(z * vol_period) - 1)

        return {
            'ticker': ticker,
            'current': current,
            'days': days,
            'confidence': confidence * 100,
            'expected_move_dollars': move,
            'expected_move_pct': move / current * 100,
            'upper_bound': current + move,
            'lower_bound': current - move,
            '1sd_range': (current * np.exp(-vol_period), current * np.exp(vol_period)),
            '2sd_range': (current * np.exp(-2 * vol_period), current * np.exp(2 * vol_period)),
        }

    @staticmethod
    def multi_target(ticker: str, targets: list, days: int = 1) -> list:
        """Probability of finishing above multiple targets."""
        return [ProbabilityEngine.finish_above(ticker, t, days) for t in targets]
