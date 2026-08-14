"""
Technical indicator library.

Each function takes pandas Series/DataFrames and returns a new Series.
They're written to be readable rather than clever — this is the part of
the codebase you'll learn the most from.

Everything is non-lookahead: each value uses only data up to that moment,
so these are safe to use in a live or backtest system.
"""

import pandas as pd


def sma(series: pd.Series, n: int = 20) -> pd.Series:
    """Simple Moving Average: the mean of the last `n` closes."""
    return series.rolling(window=n).mean()


def ema(series: pd.Series, n: int = 20) -> pd.Series:
    """Exponential Moving Average: weights recent prices more."""
    return series.ewm(span=n, adjust=False).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """
    Relative Strength Index (0..100) using Wilder's smoothing.
    >70 is often considered overbought, <30 oversold.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing is like an EMA with alpha = 1/n
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-10)  # avoid division by zero
    result = 100 - (100 / (1 + rs))
    result = result.where(avg_loss != 0, 100.0)  # all-gains period -> 100
    return result


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    Moving Average Convergence Divergence.
    Returns (macd_line, signal_line, histogram).
      macd      = EMA(fast) - EMA(slow)
      signal    = EMA(macd, signal)
      histogram = macd - signal
    """
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """
    Average True Range: a volatility measure using high/low/close.
    Higher ATR = choppier market = wider stop-losses make sense.
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def bollinger(series: pd.Series, n: int = 20, k: float = 2.0):
    """
    Bollinger Bands: a moving average with upper/lower bands at
    +/- k standard deviations. Returns (middle, upper, lower).
    """
    middle = sma(series, n)
    std = series.rolling(window=n).std()
    upper = middle + k * std
    lower = middle - k * std
    return middle, upper, lower


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add every indicator to a candles DataFrame (must have
    open/high/low/close/volume columns). Returns a new DataFrame.
    """
    out = df.copy()
    out["sma20"] = sma(out["close"], 20)
    out["sma50"] = sma(out["close"], 50)
    out["ema20"] = ema(out["close"], 20)
    out["rsi"] = rsi(out["close"], 14)
    out["atr"] = atr(out, 14)
    out["macd"], out["macd_signal"], out["macd_hist"] = macd(out["close"])
    out["bb_mid"], out["bb_up"], out["bb_low"] = bollinger(out["close"], 20, 2.0)
    return out
