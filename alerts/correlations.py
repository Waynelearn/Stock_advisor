"""Rolling peer correlations.

Computes Pearson correlation of daily returns between the position's ticker
and a list of peer tickers over the past N trading days. Result is cached
per trading day so the call is cheap on hot paths.

Falls back to a static default dict on any failure (yfinance hiccup, network).
"""

from __future__ import annotations

from datetime import date
import yfinance as yf

# Static fallback used if live computation fails. Updated periodically by hand;
# treated as a floor, not a source of truth.
FALLBACK = {
    "NVDA": 0.72,
    "AMD": 0.68,
    "AVGO": 0.55,
    "MRVL": 0.60,
}

DEFAULT_WINDOW_DAYS = 60

_cache: dict = {"date": None, "data": None}


def compute_peer_correlations(
    base_ticker: str,
    peer_tickers,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, float]:
    """Compute rolling correlation between base_ticker and each peer.

    Returns a dict mapping peer → correlation coefficient (rounded to 3 dp).
    Uses yfinance batch download. On any failure, returns FALLBACK.
    """
    peer_tickers = [p for p in peer_tickers if p != base_ticker]
    tickers = [base_ticker] + peer_tickers

    try:
        hist = yf.download(
            tickers,
            period=f"{window_days + 10}d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
        if hist is None or hist.empty:
            raise RuntimeError("empty history")

        # Build a DataFrame of close prices indexed by date
        closes = {}
        for t in tickers:
            try:
                closes[t] = hist[t]["Close"] if (t,) not in [tuple(c) for c in hist.columns] else hist[(t, "Close")]
            except Exception:
                # Fall back to single-ticker history
                try:
                    closes[t] = yf.Ticker(t).history(period=f"{window_days + 10}d")["Close"]
                except Exception:
                    pass

        if base_ticker not in closes:
            raise RuntimeError(f"base ticker {base_ticker} unavailable")

        # Daily returns
        import pandas as pd
        df = pd.DataFrame(closes).dropna(how="all")
        returns = df.pct_change().dropna(how="all").tail(window_days)

        if base_ticker not in returns.columns:
            raise RuntimeError(f"base returns missing for {base_ticker}")

        base = returns[base_ticker]
        result = {}
        for peer in peer_tickers:
            if peer in returns.columns:
                series = returns[peer]
                aligned = base.align(series, join="inner")
                if len(aligned[0]) >= 10:  # min sample
                    c = aligned[0].corr(aligned[1])
                    if c is not None and not (c != c):  # not NaN
                        result[peer] = round(float(c), 3)
        if not result:
            raise RuntimeError("no peer pairs aligned")
        return result
    except Exception as e:
        print(f"[CORRELATIONS] Falling back to static defaults: {e}")
        return {p: FALLBACK.get(p, 0.5) for p in peer_tickers}


def get_peer_correlations(
    base_ticker: str,
    peer_tickers,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, float]:
    """Cached daily peer-correlation lookup. Recomputes once per trading day."""
    today = date.today().isoformat()
    cache_key = (today, base_ticker, tuple(sorted(peer_tickers)), window_days)
    if _cache.get("key") != cache_key:
        _cache["data"] = compute_peer_correlations(base_ticker, peer_tickers, window_days)
        _cache["key"] = cache_key
        _cache["date"] = today
    return _cache["data"] or {}
