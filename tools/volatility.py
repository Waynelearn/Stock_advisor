import numpy as np
import pandas as pd
from .price_data import PriceData


class VolatilityAnalyzer:
    """Compute realized and estimated volatility metrics."""

    @staticmethod
    def realized(ticker: str, windows: list = None) -> dict:
        if windows is None:
            windows = [5, 10, 20, 60]
        df = PriceData.get(ticker)
        returns = df['Return'].dropna()
        result = {}
        for w in windows:
            if len(returns) >= w:
                vol = returns.tail(w).std()
                result[f'{w}d_daily'] = vol
                result[f'{w}d_annual'] = vol * np.sqrt(252)
        return result

    @staticmethod
    def intraday_vol(ticker: str) -> dict:
        df = PriceData.intraday(ticker)
        if df.empty:
            return {}
        returns = df['Close'].pct_change().dropna()
        minutes = len(returns)
        vol_per_min = returns.std()
        remaining = max(0, 390 - minutes)
        return {
            'minutes_elapsed': minutes,
            'minutes_remaining': remaining,
            'vol_per_minute': vol_per_min,
            'realized_today': vol_per_min * np.sqrt(minutes),
            'projected_eod': vol_per_min * np.sqrt(390),
            'remaining_vol': vol_per_min * np.sqrt(remaining) if remaining > 0 else 0,
        }

    @staticmethod
    def parkinson(ticker: str, window: int = 20) -> float:
        """Parkinson volatility estimator using high-low range."""
        df = PriceData.get(ticker).tail(window)
        if df.empty:
            return None
        hl = np.log(df['High'] / df['Low'])
        return np.sqrt((1 / (4 * len(df) * np.log(2))) * (hl ** 2).sum()) * np.sqrt(252)

    @staticmethod
    def yang_zhang(ticker: str, window: int = 20) -> float:
        """Yang-Zhang volatility estimator (most efficient for OHLC data)."""
        df = PriceData.get(ticker).tail(window + 1)
        if len(df) < window + 1:
            return None

        n = window
        log_oc = np.log(df['Close'] / df['Open']).values[1:]
        log_co = np.log(df['Open'].values[1:] / df['Close'].values[:-1])
        log_ho = np.log(df['High'] / df['Open']).values[1:]
        log_lo = np.log(df['Low'] / df['Open']).values[1:]

        # Overnight volatility
        sigma_o = (1 / (n - 1)) * np.sum((log_co - log_co.mean()) ** 2)
        # Close-to-close
        sigma_c = (1 / (n - 1)) * np.sum((log_oc - log_oc.mean()) ** 2)
        # Rogers-Satchell
        sigma_rs = (1 / n) * np.sum(log_ho * (log_ho - log_oc) + log_lo * (log_lo - log_oc))

        k = 0.34 / (1.34 + (n + 1) / (n - 1))
        sigma_yz = np.sqrt(sigma_o + k * sigma_c + (1 - k) * sigma_rs)
        return sigma_yz * np.sqrt(252)

    @classmethod
    def full_report(cls, ticker: str) -> dict:
        realized = cls.realized(ticker)
        park = cls.parkinson(ticker)
        yz = cls.yang_zhang(ticker)
        intra = cls.intraday_vol(ticker)
        return {
            'realized': realized,
            'parkinson_annual': park,
            'yang_zhang_annual': yz,
            'intraday': intra,
        }

    @staticmethod
    def vol_term_structure(ticker: str) -> pd.DataFrame:
        """Show how vol changes across different lookback windows."""
        df = PriceData.get(ticker, period="1y")
        returns = df['Return'].dropna()
        windows = [5, 10, 20, 30, 60, 90, 120, 252]
        rows = []
        for w in windows:
            if len(returns) >= w:
                vol = returns.tail(w).std() * np.sqrt(252)
                rows.append({'window': f'{w}d', 'annualized_vol': vol})
        return pd.DataFrame(rows)
