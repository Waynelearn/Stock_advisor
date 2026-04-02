import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


class PriceData:
    """Fetch and cache price data for any ticker."""

    _cache = {}

    @classmethod
    def get(cls, ticker: str, period: str = "90d", interval: str = "1d", force: bool = False) -> pd.DataFrame:
        key = f"{ticker}_{period}_{interval}"
        if not force and key in cls._cache:
            return cls._cache[key]
        t = yf.Ticker(ticker)
        df = t.history(period=period, interval=interval)
        if not df.empty:
            df['Return'] = df['Close'].pct_change()
            df['LogReturn'] = np.log(df['Close'] / df['Close'].shift(1))
            df['Range'] = df['High'] - df['Low']
            df['RangePct'] = df['Range'] / df['Close']
            cls._cache[key] = df
        return df

    @classmethod
    def current_price(cls, ticker: str) -> float:
        df = cls.get(ticker, period="5d", interval="1m")
        if not df.empty:
            return df['Close'].iloc[-1]
        df = cls.get(ticker, period="5d")
        return df['Close'].iloc[-1] if not df.empty else None

    @classmethod
    def last_n_closes(cls, ticker: str, n: int = 10) -> pd.Series:
        df = cls.get(ticker)
        return df['Close'].tail(n)

    @classmethod
    def intraday(cls, ticker: str, interval: str = "1m") -> pd.DataFrame:
        return cls.get(ticker, period="1d", interval=interval, force=True)

    @classmethod
    def summary(cls, ticker: str) -> dict:
        df = cls.get(ticker)
        if df.empty:
            return {}
        current = df['Close'].iloc[-1]
        prev = df['Close'].iloc[-2] if len(df) > 1 else current
        returns = df['Return'].dropna()
        return {
            'ticker': ticker,
            'current': current,
            'prev_close': prev,
            'change_pct': (current / prev - 1) * 100,
            'high_90d': df['High'].max(),
            'low_90d': df['Low'].min(),
            'avg_volume': df['Volume'].mean(),
            'last_volume': df['Volume'].iloc[-1],
            'vol_ratio': df['Volume'].iloc[-1] / df['Volume'].mean(),
            'avg_daily_range_pct': df['RangePct'].mean() * 100,
            'max_daily_move': returns.abs().max() * 100,
        }

    @classmethod
    def multi_summary(cls, tickers: list) -> pd.DataFrame:
        rows = []
        for t in tickers:
            s = cls.summary(t)
            if s:
                rows.append(s)
        return pd.DataFrame(rows).set_index('ticker') if rows else pd.DataFrame()

    @classmethod
    def support_resistance(cls, ticker: str, lookback: int = 30) -> dict:
        df = cls.get(ticker).tail(lookback)
        if df.empty:
            return {}
        closes = df['Close'].values
        highs = df['High'].values
        lows = df['Low'].values
        current = closes[-1]

        pivots_high = []
        pivots_low = []
        for i in range(2, len(df) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                pivots_high.append(highs[i])
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                pivots_low.append(lows[i])

        resistance = sorted([p for p in pivots_high if p > current])[:3]
        support = sorted([p for p in pivots_low if p < current], reverse=True)[:3]

        return {
            'current': current,
            'resistance': resistance,
            'support': support,
            'range_high': df['High'].max(),
            'range_low': df['Low'].min(),
        }
