"""
cryptoautomation — Volatility-Band Mean Reversion Strategy.

Quantitative core for the 60-70% win-rate target. Economic rationale:
short-horizon negative autocorrelation in crypto (Jegadeesh 1990;
Avellaneda & Lee 2010) — when price deviates k standard deviations from
its rolling mean in a *ranging* regime, the OU-consistent expectation is
reversion toward the mean.

Entry logic (vectorized over the whole frame):
    z_t = (P_t − SMA_n) / σ_n
    LONG  when z_t < −z_entry  AND regime is ranging (ADX < adx_max)
           AND volatility is normal (ATR percentile < vol_pct_max)
           AND liquidity is adequate (volume floor)
    SHORT when z_t > +z_entry  (mirror)

Exit logic:
    TP  = rolling mean (SMA_n)      → wins are frequent and small
    SL  = entry ∓ sl_atr × ATR_14   → losses are rarer and larger
    Time stop = 1.5 × OU half-life (bounded by max_hold) — the OU model
    says the edge decays with the half-life, so stale positions close.

This is deliberately parameter-light (~4 knobs) and every parameter is
economically motivated — the anti-overfitting discipline. Nothing here is
fit to any specific symbol's history.

All functions are pure (DataFrame in → DataFrame out) and testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ──────────────────────────────────────────────────────────────────────
# Parameters (the whole strategy surface — keep it small)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class MeanReversionParams:
    # Entry mode: "z" (z-score extremes, v1) or "rsi2" (Connors-style
    # 2-period RSI extremes — research-documented ~75-79% win rate on
    # equities; validated for crypto in backtests).
    entry_mode: str = "z"
    # --- z-mode entry ---
    z_entry: float = 2.0
    # --- rsi2-mode entry/exit (Connors RSI-2) ---
    rsi2_entry: float = 10.0   # long when RSI(2) < 10 (short when > 100-10)
    rsi2_exit: float = 50.0    # exit long when RSI(2) crosses >= 50
    # Rolling window for the mean / standard deviation (bars).
    n: int = 48
    # Stop-loss width in ATR units.
    sl_atr: float = 1.5
    # Minimum hold before an opposite signal can close the trade (bars).
    min_hold: int = 3
    # Absolute cap on holding period (bars).
    max_hold: int = 50
    # Time stop = half_life_mult × OU half-life, capped at max_hold.
    use_hl_time_stop: bool = True
    half_life_mult: float = 1.5
    # Regime filter: only trade when ADX is below this (ranging market).
    adx_period: int = 14
    adx_max: float = 25.0
    # Volatility filter: skip when ATR% is above this rolling percentile.
    vol_pct_max: float = 0.90
    vol_lookback: int = 250
    # Liquidity floor: volume must exceed this multiple of its 20-bar median.
    volume_min_mult: float = 0.3
    # Long-term trend filter (Connors/200-MA rule): LONG only when price is
    # ABOVE the trend MA (buy oversold in an uptrend), SHORT only when below.
    # v2 default ON — validated 2026-08-14: improves WR + returns on all 5
    # majors vs v1 (e.g. BTC WR 37.7% -> 52.4%, return -31% -> -4.2%).
    trend_filter: bool = True
    trend_ma: int = 200
    # Time-of-day filter (UTC, inclusive [start_hour, end_hour] on the bar's
    # open). None = trade all hours. Research: 03:00-04:00 UTC are the worst
    # hours for BTC; 22:00-23:00 the best. Mechanism only — leave None until
    # a symbol-specific value is validated out-of-sample.
    hour_filter: tuple | None = None

    def validate(self) -> None:
        if self.entry_mode not in ("z", "rsi2"):
            raise ValueError(f"entry_mode must be 'z' or 'rsi2', got {self.entry_mode!r}")
        if self.z_entry <= 0:
            raise ValueError("z_entry must be > 0")
        if not 0 < self.rsi2_entry < 50:
            raise ValueError("rsi2_entry must be in (0, 50)")
        if not 50 <= self.rsi2_exit <= 100:
            raise ValueError("rsi2_exit must be in [50, 100]")
        if self.n < 20:
            raise ValueError("n must be >= 20 (statistically meaningful std)")
        if self.sl_atr <= 0:
            raise ValueError("sl_atr must be > 0")
        if not 0 < self.vol_pct_max <= 1:
            raise ValueError("vol_pct_max must be in (0, 1]")
        if self.max_hold < self.min_hold:
            raise ValueError("max_hold must be >= min_hold")
        if self.hour_filter is not None:
            a, b = self.hour_filter
            if not (0 <= a <= 23 and 0 <= b <= 23 and a <= b):
                raise ValueError(f"hour_filter must be [start, end] in 0..23, got {self.hour_filter}")


DEFAULT_PARAMS = MeanReversionParams()


# ──────────────────────────────────────────────────────────────────────
# Building blocks
# ──────────────────────────────────────────────────────────────────────

def zscore_series(close: pd.Series, n: int) -> pd.Series:
    """Rolling z-score of price vs its n-bar mean: z = (P − μ) / σ."""
    mean = close.rolling(window=n, min_periods=n).mean()
    std = close.rolling(window=n, min_periods=n).std()
    z = (close - mean) / std.replace(0, 1e-12)
    return z


def adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Full-series ADX using Wilder smoothing (same logic as the live
    math_engine regime detector, but vectorized for backtests).
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    alpha = 1.0 / period
    atr = tr.ewm(alpha=alpha, adjust=False).mean().replace(0, 1e-10)
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr

    di_sum = (plus_di + minus_di).replace(0, 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=alpha, adjust=False).mean()


def estimate_half_life(close: pd.Series) -> float | None:
    """
    OU half-life of mean reversion via OLS on log prices:

        Δlog P_t = α + β · log P_{t−1} + ε
        half_life = −ln(2) / β          (β < 0 ⇒ mean-reverting)

    Returns None when the series is not mean-reverting (β ≥ 0), so the
    caller falls back to a plain time stop.
    """
    if len(close) < 60:
        return None
    vals = pd.Series(close, dtype="float64").to_numpy()
    vals = np.maximum(vals, 1e-12)
    log_p = np.log(vals)
    x = log_p[:-1]
    y = log_p[1:] - log_p[:-1]

    n = len(x)
    x_bar = x.mean()
    y_bar = y.mean()
    cov = float(((x - x_bar) * (y - y_bar)).sum())
    var = float(((x - x_bar) ** 2).sum())
    if var <= 0:
        return None
    beta = cov / var
    if beta >= 0:
        return None
    # Significance filter: reject spurious reversion. A random walk can
    # produce a slightly negative beta by chance; require |t| on the slope
    # to exceed ~1.65 (90% one-sided) before trusting the half-life.
    resid = y - (beta * x + (y_bar - beta * x_bar))
    sse = float((resid ** 2).sum())
    dof = max(n - 2, 1)
    se_beta = math.sqrt(sse / dof / var) if var > 0 else float("inf")
    t_stat = beta / se_beta if se_beta > 0 else 0.0
    if t_stat > -1.65:
        return None
    hl = -math.log(2.0) / beta
    # Sample-length cap: a half-life longer than ~1/4 of the sample is not
    # estimable (there aren't enough reversion cycles to measure it). This
    # is what rejects the spurious 'slow reversion' a drifting random walk
    # can produce on long samples.
    if hl > len(close) / 4:
        return None
    return hl


def signal_frame(
    df: pd.DataFrame,
    params: MeanReversionParams | None = None,
) -> pd.DataFrame:
    """
    Compute every strategy input for the whole frame, vectorized.

    Returns a DataFrame (same index as `df`) with columns:
      z, sma_n, atr, adx, ranging, vol_ok, liq_ok,
      entry_long, entry_short,
      sl_long, sl_short, tp_long, tp_short,
      signal (1/-1/0), time_stop (bars)

    `signal` is the entry direction at that bar (for flip detection).
    """
    params = params or DEFAULT_PARAMS
    params.validate()

    close = df["close"]
    z = zscore_series(close, params.n)
    sma_n = close.rolling(params.n, min_periods=params.n).mean()

    # Connors-style 2-period RSI (fast reversion trigger).
    rsi2 = _rsi_series(close, 2)

    # Long-term trend filter: LONG only above the trend MA, SHORT only below.
    sma_trend = close.rolling(params.trend_ma, min_periods=params.trend_ma // 2).mean()
    if params.trend_filter:
        trend_ok_long = close > sma_trend
        trend_ok_short = close < sma_trend
    else:
        trend_ok_long = pd.Series(True, index=df.index)
        trend_ok_short = pd.Series(True, index=df.index)

    # Time-of-day filter (UTC hour of the bar's open time).
    if params.hour_filter is not None:
        h0, h1 = params.hour_filter
        hour = (df["open_time"] // 3_600_000) % 24
        hour_ok = (hour >= h0) & (hour <= h1)
    else:
        hour_ok = pd.Series(True, index=df.index)

    atr = pd.Series(df["atr"]) if "atr" in df.columns else _atr_series(df, 14)
    adx = adx_series(df, params.adx_period)

    # Volatility filter: ATR% above the trailing percentile = skip.
    # Quantile is computed on the window ending at t-1 (shift) so the
    # filter never uses the current bar's own value against itself.
    atr_pct = (atr / close.replace(0, 1e-12)) * 100.0
    past_atr_pct = atr_pct.shift(1)
    vol_threshold = past_atr_pct.rolling(
        params.vol_lookback, min_periods=params.vol_lookback // 2
    ).quantile(params.vol_pct_max)
    vol_ok = atr_pct <= vol_threshold

    # Liquidity floor: volume vs its 20-bar median.
    vol_med = df["volume"].rolling(20, min_periods=10).median()
    liq_ok = df["volume"] >= params.volume_min_mult * vol_med.replace(0, 1e-12)

    ranging = adx < params.adx_max
    base_ok = ranging & vol_ok & liq_ok & hour_ok

    if params.entry_mode == "rsi2":
        entry_long = (rsi2 < params.rsi2_entry) & base_ok & trend_ok_long
        entry_short = (rsi2 > (100.0 - params.rsi2_entry)) & base_ok & trend_ok_short
    else:  # "z"
        entry_long = (z < -params.z_entry) & base_ok & trend_ok_long
        entry_short = (z > params.z_entry) & base_ok & trend_ok_short

    # RSI(2) profit-taking exit: close long when RSI(2) crosses >= exit level.
    rsi2_exit_ok = (rsi2 >= params.rsi2_exit) & rsi2.notna()

    sl_long = close - params.sl_atr * atr
    sl_short = close + params.sl_atr * atr
    tp_long = sma_n
    tp_short = sma_n

    signal = pd.Series(0, index=df.index, dtype=int)
    signal[entry_long] = 1
    signal[entry_short] = -1

    hl = estimate_half_life(close)
    if hl is not None and params.use_hl_time_stop:
        time_stop = max(params.min_hold, min(int(params.half_life_mult * hl), params.max_hold))
    else:
        time_stop = params.max_hold

    return pd.DataFrame({
        "z": z,
        "sma_n": sma_n,
        "rsi2": rsi2,
        "atr": atr,
        "adx": adx,
        "ranging": ranging,
        "vol_ok": vol_ok,
        "liq_ok": liq_ok,
        "hour_ok": hour_ok,
        "trend_ok_long": trend_ok_long,
        "trend_ok_short": trend_ok_short,
        "entry_long": entry_long,
        "entry_short": entry_short,
        "rsi2_exit_ok": rsi2_exit_ok,
        "sl_long": sl_long,
        "sl_short": sl_short,
        "tp_long": tp_long,
        "tp_short": tp_short,
        "signal": signal,
        "time_stop": time_stop,
    })


def compute_signal(
    df: pd.DataFrame,
    params: MeanReversionParams | None = None,
) -> dict:
    """
    Live entry-point wrapper: run signal_frame() and return the decision
    at the LAST (current) bar. Returns a dict with direction, entry, SL,
    TP, time_stop, and the filter states that explain the call.
    """
    params = params or DEFAULT_PARAMS
    sf = signal_frame(df, params)
    last = sf.iloc[-1]
    close = float(df["close"].iloc[-1])

    direction = "HOLD"
    if bool(last["entry_long"]):
        direction = "LONG"
    elif bool(last["entry_short"]):
        direction = "SHORT"

    return {
        "direction": direction,
        "entry": close,
        "stop_loss": float(last["sl_long"]) if direction == "LONG" else (
            float(last["sl_short"]) if direction == "SHORT" else None),
        "take_profit": float(last["tp_long"]) if direction == "LONG" else (
            float(last["tp_short"]) if direction == "SHORT" else None),
        "time_stop_bars": int(last["time_stop"]),
        "z": _safe_float(last["z"]),
        "adx": _safe_float(last["adx"]),
        "ranging": bool(last["ranging"]),
        "vol_ok": bool(last["vol_ok"]),
        "liq_ok": bool(last["liq_ok"]),
        "half_life_bars": float(last["time_stop"]) / 1.5 if params.use_hl_time_stop else None,
    }


def _rsi_series(series: pd.Series, n: int = 2) -> pd.Series:
    """Wilder RSI (same math as indicators.rsi) — local copy so strategy.py
    stays dependency-free for this fast reversion trigger."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    result = 100 - (100 / (1 + rs))
    return result.where(avg_loss != 0, 100.0)


def _atr_series(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None
