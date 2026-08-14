"""
cryptoautomation — Mathematical Signal Engine.

Quantitative methods for more accurate trade signals:

  1. Bayesian Signal Fusion — posterior probability given indicator evidence
  2. Regime Detection — ADX-based trending/ranging market classification
  3. Signal Freshness — exponential decay on indicator age
  4. Confluence Scoring — independence-weighted multi-indicator agreement
  5. Kelly Criterion — mathematically optimal position sizing

All functions are pure and testable. No API keys or external deps needed.

Usage:
    from math_engine import compute_trade_signal
    result = compute_trade_signal(df_1h, df_4h, df_1d, news_score=0.3)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


def _safe(v):
    """Return True if value is a usable number (not None, NaN, or Inf)."""
    if v is None:
        return False
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return False
    return True


# ──────────────────────────────────────────────────────────────────────
# Data types
# ──────────────────────────────────────────────────────────────────────

class Regime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"


@dataclass
class IndicatorSignal:
    """One indicator's signal with metadata for the math pipeline."""
    name: str
    direction: int       # +1 bullish, -1 bearish, 0 neutral
    strength: float      # 0-1, how strong the signal is
    age_candles: int = 0 # candles since the signal fired
    group: str = "price" # independence group: "price" | "reversal" | "momentum" | "volume"
    # Bayesian hit rates — P(this signal was right | market went in that direction)
    hit_rate_bull: float = 0.58
    hit_rate_bear: float = 0.58


@dataclass
class TradeSignal:
    """Final computed trade signal with full math breakdown."""
    direction: str           # LONG, SHORT, HOLD
    confidence: float        # 0-100
    ev_per_unit: float       # expected value per unit risk
    kelly_fraction: float    # optimal position size fraction (0-1)
    regime: Regime
    adx_value: float         # raw ADX reading
    posterior_bull: float    # Bayesian P(bull | all evidence)
    posterior_bear: float    # Bayesian P(bear | all evidence)
    confluence_score: float  # 0-1, independence-weighted agreement
    entry: float
    stop_loss: float | None
    take_profit: float | None
    risk_reward: float
    reasons: list[dict] = field(default_factory=list)
    signal_breakdown: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# Default indicator hit rates (priors)
# Calibrated from backtest results: actual win rate ~33-40% on 1h
# We set priors slightly BELOW 0.50 to require strong confluence before
# trading. This prevents over-trading on weak signals.
# ──────────────────────────────────────────────────────────────────────

DEFAULT_HIT_RATES: dict[str, dict[str, float]] = {
    "RSI":    {"bull": 0.49, "bear": 0.49},
    "MACD":   {"bull": 0.48, "bear": 0.48},
    "SMA":    {"bull": 0.47, "bear": 0.47},
    "BB":     {"bull": 0.49, "bear": 0.49},
    "Volume": {"bull": 0.48, "bear": 0.48},
}

# Regime-dependent weight multipliers.
# Trending markets: momentum signals are more reliable.
# Ranging markets: mean-reversion signals are more reliable.
REGIME_MULTIPLIERS = {
    "momentum": {
        Regime.TRENDING_UP: 1.15, Regime.TRENDING_DOWN: 1.15, Regime.RANGING: 0.80,
    },
    "reversal": {
        Regime.TRENDING_UP: 0.85, Regime.TRENDING_DOWN: 0.85, Regime.RANGING: 1.15,
    },
    "volume": {
        Regime.TRENDING_UP: 1.05, Regime.TRENDING_DOWN: 1.05, Regime.RANGING: 0.95,
    },
    "price": {
        Regime.TRENDING_UP: 1.0, Regime.TRENDING_DOWN: 1.0, Regime.RANGING: 1.0,
    },
}


# ──────────────────────────────────────────────────────────────────────
# 1. Regime Detection (ADX)
# ──────────────────────────────────────────────────────────────────────

def detect_regime(df, adx_period: int = 14) -> tuple[Regime, float]:
    """
    Detect market regime using ADX (Average Directional Index).

    ADX > 25 → trending (direction from +DI vs -DI)
    ADX < 20 → ranging
    20-25    → borderline (use DI imbalance to decide)

    Args:
        df: DataFrame with 'high', 'low', 'close' columns.
        adx_period: lookback for ADX smoothing.

    Returns:
        (regime, adx_value)
    """
    if len(df) < adx_period * 3:
        return Regime.RANGING, 0.0

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    # True Range
    tr = _max_series([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ])

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    import pandas as pd
    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    # Wilder smoothing (alpha = 1/period)
    alpha = 1.0 / adx_period
    atr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_smooth.replace(0, 1e-10)
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_smooth.replace(0, 1e-10)

    # DX → ADX
    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()
    dx = 100 * di_diff / di_sum.replace(0, 1e-10)
    adx = dx.ewm(alpha=alpha, adjust=False).mean()

    adx_val = float(adx.iloc[-1])
    plus_val = float(plus_di.iloc[-1])
    minus_val = float(minus_di.iloc[-1])

    if adx_val > 25:
        regime = Regime.TRENDING_UP if plus_val > minus_val else Regime.TRENDING_DOWN
    elif adx_val < 20:
        regime = Regime.RANGING
    else:
        # Borderline — use DI imbalance
        if plus_val > minus_val * 1.1:
            regime = Regime.TRENDING_UP
        elif minus_val > plus_val * 1.1:
            regime = Regime.TRENDING_DOWN
        else:
            regime = Regime.RANGING

    return regime, adx_val


def _max_series(series_list):
    """Element-wise max across a list of pandas Series."""
    result = series_list[0].copy()
    for s in series_list[1:]:
        result = result.where(result >= s, s)
    return result


# ──────────────────────────────────────────────────────────────────────
# 2. Signal Freshness (exponential decay)
# ──────────────────────────────────────────────────────────────────────

def freshness_weight(age_candles: int, decay_lambda: float = 0.15) -> float:
    """
    Exponential decay for signal freshness.

    0 candles ago → 1.00
    5 candles ago → 0.47
    10 candles ago → 0.22
    20 candles ago → 0.05
    """
    return math.exp(-decay_lambda * age_candles)


# ──────────────────────────────────────────────────────────────────────
# 3. Bayesian Signal Fusion
# ──────────────────────────────────────────────────────────────────────

def bayesian_fusion(
    signals: list[IndicatorSignal],
    regime: Regime,
    prior_bull: float = 0.50,
) -> tuple[float, float]:
    """
    Combine signals via Bayesian updating in log-space.

    For each signal we compute:
        P(signal | bull) and P(signal | bear)
    using regime-adjusted hit rates, then multiply (add in log-space).

    Returns:
        (P(bull | all evidence), P(bear | all evidence))
    """
    if not signals:
        return prior_bull, 1.0 - prior_bull

    log_bull = math.log(max(prior_bull, 1e-10))
    log_bear = math.log(max(1.0 - prior_bull, 1e-10))

    for sig in signals:
        if sig.direction == 0:
            continue

        hr_bull, hr_bear = _regime_adjusted_hit_rates(sig, regime)
        fresh = freshness_weight(sig.age_candles)
        strength = sig.strength * fresh

        if sig.direction > 0:
            # Bullish signal: high P(signal|bull), low P(signal|bear)
            ps_bull = hr_bull * strength + (1.0 - strength) * 0.50
            ps_bear = (1.0 - hr_bear) * strength + (1.0 - strength) * 0.50
        else:
            ps_bull = (1.0 - hr_bull) * strength + (1.0 - strength) * 0.50
            ps_bear = hr_bear * strength + (1.0 - strength) * 0.50

        log_bull += math.log(max(ps_bull, 1e-10))
        log_bear += math.log(max(ps_bear, 1e-10))

    # Log-sum-exp normalisation
    mx = max(log_bull, log_bear)
    p_bull = math.exp(log_bull - mx)
    p_bear = math.exp(log_bear - mx)
    total = p_bull + p_bear
    return p_bull / total, p_bear / total


def _regime_adjusted_hit_rates(
    sig: IndicatorSignal, regime: Regime,
) -> tuple[float, float]:
    """Scale hit rates up/down based on how well this indicator's group
    performs in the current regime."""
    mult = REGIME_MULTIPLIERS.get(sig.group, {}).get(regime, 1.0)
    # Cap hit rate at 0.85 to stay realistic
    return min(sig.hit_rate_bull * mult, 0.85), min(sig.hit_rate_bear * mult, 0.85)


# ──────────────────────────────────────────────────────────────────────
# 4. Confluence Scoring
# ──────────────────────────────────────────────────────────────────────

def confluence_score(signals: list[IndicatorSignal]) -> float:
    """
    How much *independent* confirmation exists across indicators.

    Agreement across different groups → high score (truly independent).
    Agreement within the same group → diminishing returns (correlated).

    Returns 0-1 where 1 = perfect independent confluence.
    """
    active = [s for s in signals if s.direction != 0]
    if len(active) < 2:
        return 0.0

    # All must agree on direction
    dirs = {s.direction for s in active}
    if len(dirs) != 1:
        return 0.0

    # Group signals and score with diminishing returns per group
    groups: dict[str, list] = {}
    for s in active:
        groups.setdefault(s.group, []).append(s)

    total = 0.0
    for group_sigs in groups.values():
        for i in range(len(group_sigs)):
            total += 1.0 / (i + 1)  # full credit for first, 1/2 for second, etc.

    return min(total / len(active), 1.0)


# ──────────────────────────────────────────────────────────────────────
# 5. Kelly Criterion + Expected Value
# ──────────────────────────────────────────────────────────────────────

def kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.25,
) -> float:
    """
    Kelly Criterion: optimal fraction of bankroll to risk.

        kelly = W - (1-W)/R

    We use fractional Kelly (default 25%) to cut variance.

    Returns 0 if edge is negative.
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0

    R = avg_win / avg_loss
    full_kelly = win_rate - (1.0 - win_rate) / R

    return max(full_kelly * fraction, 0.0)


def expected_value_per_unit(
    win_rate: float,
    risk_reward: float,
) -> float:
    """Expected profit per unit risked."""
    return win_rate * risk_reward - (1.0 - win_rate)


# ──────────────────────────────────────────────────────────────────────
# 6. Extract indicator signals from a DataFrame
# ──────────────────────────────────────────────────────────────────────

def _find_signal_age(df, cond_true, lookback: int = 30) -> int:
    """How many candles ago the condition was last True. Returns 0 if now."""
    tail = df[cond_true].tail(lookback)
    if tail.empty:
        return lookback  # signal is old / stale
    return len(df) - 1 - tail.index[-1]


def extract_indicator_signals(
    df,
    historical_accuracy: dict | None = None,
) -> list[IndicatorSignal]:
    """
    Pull individual indicator signals from a DataFrame that already has
    indicators added (via indicators.add_all).

    Returns a list of IndicatorSignal objects with direction, strength,
    age, group, and hit rates.
    """
    import pandas as pd

    if len(df) < 60:
        return []

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    hr = historical_accuracy or DEFAULT_HIT_RATES
    signals: list[IndicatorSignal] = []

    # ── RSI ──────────────────────────────────────────────────────
    rsi = last.get("rsi")
    if _safe(rsi):
        h = hr.get("RSI", DEFAULT_HIT_RATES["RSI"])
        if rsi < 30:
            strength = min((30 - rsi) / 15.0, 1.0)
            age = _find_signal_age(df, df["rsi"] < 30)
            signals.append(IndicatorSignal("RSI", 1, strength, age, "reversal", h["bull"], h["bear"]))
        elif rsi > 70:
            strength = min((rsi - 70) / 15.0, 1.0)
            age = _find_signal_age(df, df["rsi"] > 70)
            signals.append(IndicatorSignal("RSI", -1, strength, age, "reversal", h["bull"], h["bear"]))
        elif rsi < 40:
            signals.append(IndicatorSignal("RSI", 1, 0.3, 0, "reversal", h["bull"], h["bear"]))
        elif rsi > 60:
            signals.append(IndicatorSignal("RSI", -1, 0.3, 0, "reversal", h["bull"], h["bear"]))

    # ── MACD crossover ───────────────────────────────────────────
    macd_now = last.get("macd")
    sig_now = last.get("macd_signal")
    macd_prev = prev.get("macd")
    sig_prev = prev.get("macd_signal")
    if all(_safe(v) for v in [macd_now, sig_now, macd_prev, sig_prev]):
        h = hr.get("MACD", DEFAULT_HIT_RATES["MACD"])
        if macd_now > sig_now and macd_prev <= sig_prev:
            age = _find_signal_age(df, (df["macd"] > df["macd_signal"]))
            signals.append(IndicatorSignal("MACD", 1, 0.85, age, "momentum", h["bull"], h["bear"]))
        elif macd_now < sig_now and macd_prev >= sig_prev:
            age = _find_signal_age(df, (df["macd"] < df["macd_signal"]))
            signals.append(IndicatorSignal("MACD", -1, 0.85, age, "momentum", h["bull"], h["bear"]))

        # Histogram momentum (weaker, supplementary)
        hist_now = last.get("macd_hist")
        hist_prev = prev.get("macd_hist")
        if _safe(hist_now) and _safe(hist_prev):
            if hist_now > hist_prev and hist_now > 0:
                signals.append(IndicatorSignal("MACD", 1, 0.35, 0, "momentum", h["bull"], h["bear"]))
            elif hist_now < hist_prev and hist_now < 0:
                signals.append(IndicatorSignal("MACD", -1, 0.35, 0, "momentum", h["bull"], h["bear"]))

    # ── SMA crossover (20/50) ────────────────────────────────────
    sma20_n = last.get("sma20")
    sma50_n = last.get("sma50")
    sma20_p = prev.get("sma20")
    sma50_p = prev.get("sma50")
    if all(_safe(v) for v in [sma20_n, sma50_n, sma20_p, sma50_p]):
        h = hr.get("SMA", DEFAULT_HIT_RATES["SMA"])
        if sma20_n > sma50_n and sma20_p <= sma50_p:
            age = _find_signal_age(df, df["sma20"] > df["sma50"])
            signals.append(IndicatorSignal("SMA", 1, 0.85, age, "momentum", h["bull"], h["bear"]))
        elif sma20_n < sma50_n and sma20_p >= sma50_p:
            age = _find_signal_age(df, df["sma20"] < df["sma50"])
            signals.append(IndicatorSignal("SMA", -1, 0.85, age, "momentum", h["bull"], h["bear"]))
        # Trend alignment (weaker)
        elif sma20_n > sma50_n:
            signals.append(IndicatorSignal("SMA", 1, 0.30, 0, "momentum", h["bull"], h["bear"]))
        elif sma20_n < sma50_n:
            signals.append(IndicatorSignal("SMA", -1, 0.30, 0, "momentum", h["bull"], h["bear"]))

    # ── Bollinger Band touch ─────────────────────────────────────
    close = last.get("close")
    bb_up = last.get("bb_up")
    bb_low = last.get("bb_low")
    if all(_safe(v) for v in [close, bb_up, bb_low]):
        h = hr.get("BB", DEFAULT_HIT_RATES["BB"])
        bb_range = bb_up - bb_low
        if bb_range > 0:
            if close < bb_low:
                strength = min((bb_low - close) / bb_range * 2, 1.0)
                age = _find_signal_age(df, df["close"] < df["bb_low"])
                signals.append(IndicatorSignal("BB", 1, strength, age, "reversal", h["bull"], h["bear"]))
            elif close > bb_up:
                strength = min((close - bb_up) / bb_range * 2, 1.0)
                age = _find_signal_age(df, df["close"] > df["bb_up"])
                signals.append(IndicatorSignal("BB", -1, strength, age, "reversal", h["bull"], h["bear"]))

    # ── Volume spike ─────────────────────────────────────────────
    vol = last.get("volume")
    if _safe(vol):
        h = hr.get("Volume", DEFAULT_HIT_RATES["Volume"])
        vol_avg = df["volume"].tail(20).mean()
        if _safe(vol_avg) and vol_avg > 0:
            ratio = vol / vol_avg
            if ratio > 1.5:
                # High volume confirms current candle direction
                price_dir = 1 if close >= last.get("open", close) else -1
                strength = min(ratio / 3.0, 1.0)
                signals.append(IndicatorSignal("Volume", price_dir, strength, 0, "volume",
                                                h["bull"], h["bear"]))

    return signals


# ──────────────────────────────────────────────────────────────────────
# 7. Factor-to-Signal conversion
# ──────────────────────────────────────────────────────────────────────

# Hit rates for factor-based signals (independent from indicator hit rates)
# Calibrated from backtest: factors alone have no edge on 1h crypto.
# Set BELOW 0.50 to require strong confluence before trading.
# ──────────────────────────────────────────────────────────────────────

FACTOR_HIT_RATES = {
    "Trend":    {"bull": 0.50, "bear": 0.50},
    "Momentum": {"bull": 0.49, "bear": 0.49},
    "Carry":    {"bull": 0.48, "bear": 0.48},
    "Reversal": {"bull": 0.47, "bear": 0.47},
    "Value":    {"bull": 0.49, "bear": 0.49},
}


def factors_to_signals(factor_bundle) -> list[IndicatorSignal]:
    """
    Convert a FactorBundle (from factors.py) into IndicatorSignals
    that can be fed into the Bayesian fusion alongside technical indicators.

    Each factor becomes a separate signal with its own group — this is
    critical because factors measure INDEPENDENT economic mechanisms.
    High confluence across factor groups = much stronger signal.
    """
    if factor_bundle is None:
        return []

    signals = []
    factor_map = {
        "trend":     ("Trend",    factor_bundle.trend),
        "momentum":  ("Momentum", factor_bundle.momentum),
        "carry":     ("Carry",    factor_bundle.carry),
        "reversal":  ("Reversal", factor_bundle.reversal),
        "value":     ("Value",    factor_bundle.value),
    }

    for group_name, (display_name, fr) in factor_map.items():
        if fr.score == 0.0 and fr.confidence == 0.0:
            continue  # skip factors with no data

        direction = 1 if fr.score > 0.05 else (-1 if fr.score < -0.05 else 0)
        strength = abs(fr.score) * fr.confidence
        hr = FACTOR_HIT_RATES.get(display_name, {"bull": 0.55, "bear": 0.55})

        signals.append(IndicatorSignal(
            name=display_name,
            direction=direction,
            strength=min(strength, 1.0),
            age_candles=0,
            group=group_name,
            hit_rate_bull=hr["bull"],
            hit_rate_bear=hr["bear"],
        ))

    return signals


# ──────────────────────────────────────────────────────────────────────
# 8. Full pipeline
# ──────────────────────────────────────────────────────────────────────

def compute_trade_signal(
    df_1h,
    df_4h=None,
    df_1d=None,
    news_score: float = 0.0,
    historical_accuracy: dict | None = None,
    factor_bundle=None,
    all_coins: dict | None = None,
) -> TradeSignal:
    """
    Full mathematical signal computation pipeline.

    Combines multi-timeframe indicator data AND multi-factor scores
    through Bayesian fusion, regime detection, confluence scoring,
    and Kelly sizing.

    Args:
        df_1h: 1-hour candles with indicators added (indicators.add_all).
        df_4h: 4-hour candles (optional, boosts accuracy).
        df_1d: 1-day candles (optional, boosts accuracy).
        news_score: -1..1 from the reviewer module.
        historical_accuracy: optional dict overriding default hit rates.
        factor_bundle: FactorBundle from factors.py (trend/momentum/carry/reversal/value).
        all_coins: {symbol: df} for cross-sectional momentum.

    Returns:
        TradeSignal with full math breakdown.
    """
    import pandas as pd

    # ── 1. Regime detection (use 1h for most responsive reading) ──
    regime, adx = detect_regime(df_1h)

    # ── 2. Extract technical indicator signals from each timeframe ─
    all_signals: list[IndicatorSignal] = []

    tf_dfs = []
    if len(df_1h) > 10:
        tf_dfs.append(("1h", df_1h))
    if df_4h is not None and len(df_4h) > 10:
        tf_dfs.append(("4h", df_4h))
    if df_1d is not None and len(df_1d) > 10:
        tf_dfs.append(("1d", df_1d))

    for tf_name, tf_df in tf_dfs:
        sigs = extract_indicator_signals(tf_df, historical_accuracy)
        for s in sigs:
            s.name = f"{s.name}@{tf_name}"
        all_signals.extend(sigs)

    # ── 3. Add factor signals (INDEPENDENT from indicators) ──────
    if factor_bundle is None:
        # Compute factors from the 1h data
        try:
            from factors import compute_all_factors
            fund_rate = None
            # Try to get funding rate
            try:
                from factors import fetch_funding_rate
                fund_rate = fetch_funding_rate(
                    df_1h["symbol"].iloc[-1] if "symbol" in df_1h.columns else "BTCUSDT"
                )
            except Exception:
                pass
            factor_bundle = compute_all_factors(df_1h, all_coins, fund_rate)
        except Exception:
            pass

    factor_signals = factors_to_signals(factor_bundle)
    all_signals.extend(factor_signals)

    # ── 4. Bayesian fusion ───────────────────────────────────────
    p_bull, p_bear = bayesian_fusion(all_signals, regime)

    # ── 5. Confluence score ──────────────────────────────────────
    conf = confluence_score(all_signals)

    # ── 6. News overlay (15% blend — factors reduce news weight) ─
    # With 5 independent factors, we can reduce news weight from 20% to 15%
    # because the factors provide their own independent information
    news_bull = max(0.0, (1.0 + news_score) / 2.0)
    news_bear = max(0.0, (1.0 - news_score) / 2.0)
    p_bull_final = 0.85 * p_bull + 0.15 * news_bull
    p_bear_final = 0.85 * p_bear + 0.15 * news_bear
    total_p = p_bull_final + p_bear_final
    p_bull_final /= total_p
    p_bear_final /= total_p

    # ── 7. Direction + confidence ────────────────────────────────
    raw_edge = abs(p_bull_final - p_bear_final)
    # Confluence multiplier: 0.7 (no confirmation) to 1.0 (strong independent confirmation)
    conf_mult = 0.70 + 0.30 * conf
    confidence = min(raw_edge * conf_mult * 100.0, 95.0)

    # Minimum posterior probability for a trade (stricter = fewer false signals)
    # 0.60 still too permissive → many signal flips
    MIN_POSTERIOR = 0.65
    MIN_CONFIDENCE = 25.0  # minimum confidence to act

    if p_bull_final > p_bear_final and p_bull_final > MIN_POSTERIOR and confidence >= MIN_CONFIDENCE:
        direction = "LONG"
    elif p_bear_final > p_bull_final and p_bear_final > MIN_POSTERIOR and confidence >= MIN_CONFIDENCE:
        direction = "SHORT"
    else:
        direction = "HOLD"
        confidence = min(confidence, 45.0)

    # ── 7. Entry + risk levels from ATR (1h) ────────────────────
    atr_val = 0.0
    entry = 0.0
    if len(df_1h) > 0:
        atr_raw = df_1h["atr"].iloc[-1]
        if _safe(atr_raw):
            atr_val = float(atr_raw)
        entry = float(df_1h["close"].iloc[-1])

    # Regime-adaptive SL/TP: wider in trends (avoid noise), tighter in ranges
    # Only compute for actual trades (not HOLD)
    stop_loss = None
    take_profit = None

    if direction in ("LONG", "SHORT"):
        if regime == Regime.TRENDING_UP or regime == Regime.TRENDING_DOWN:
            sl_mult = 2.0   # wider stop in trends
            tp_mult = 3.0   # let winners run
        else:  # RANGING
            sl_mult = 1.2   # tight stop in ranges
            tp_mult = 2.0   # quick profit target

        if direction == "LONG":
            stop_loss = entry - sl_mult * atr_val if atr_val else None
            take_profit = entry + tp_mult * atr_val if atr_val else None
        elif direction == "SHORT":
            stop_loss = entry + sl_mult * atr_val if atr_val else None
            take_profit = entry - tp_mult * atr_val if atr_val else None

    risk_reward = 0.0
    if stop_loss and take_profit and entry:
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        risk_reward = reward / risk if risk > 0 else 0.0

    # ── 8. Kelly Criterion (fractional: 25% of full Kelly) ────────
    if direction == "LONG":
        est_wr = p_bull_final
    elif direction == "SHORT":
        est_wr = p_bear_final
    else:
        est_wr = 0.50

    # Use fractional Kelly (0.25) to reduce variance & account for estimation error
    kelly = kelly_criterion(est_wr, risk_reward, 0.25) if risk_reward > 0 else 0.0
    ev = expected_value_per_unit(est_wr, risk_reward)

    # ── 8b. Minimum EV filter - reject trades with negative expected value
    # This is the key: our priors are optimistic, so filter by actual EV
    MIN_EV = 0.01  # require positive EV per unit risk
    if direction != "HOLD" and ev <= MIN_EV:
        direction = "HOLD"
        confidence = min(confidence, 45.0)
        kelly = 0.0
        ev = 0.0

    # ── 9. Build breakdown + reasons ────────────────────────────
    breakdown = {}
    for sig in all_signals:
        fresh = freshness_weight(sig.age_candles)
        hr_b, hr_be = _regime_adjusted_hit_rates(sig, regime)
        base_name = sig.name.split("@")[0]
        tf_name = sig.name.split("@")[1] if "@" in sig.name else "1h"
        breakdown[sig.name] = {
            "direction": ["BEARISH", "NEUTRAL", "BULLISH"][sig.direction + 1],
            "strength": round(sig.strength, 3),
            "freshness": round(fresh, 3),
            "effective": round(sig.strength * fresh, 3),
            "hit_rate_bull": round(hr_b, 3),
            "hit_rate_bear": round(hr_be, 3),
            "group": sig.group,
            "timeframe": tf_name,
        }

    reasons = []
    for sig in all_signals:
        if sig.direction == 0:
            continue
        fresh = freshness_weight(sig.age_candles)
        eff = sig.strength * fresh
        if eff > 0.25:
            dir_label = "BULLISH" if sig.direction > 0 else "BEARISH"
            reasons.append({
                "indicator": sig.name,
                "signal": dir_label,
                "detail": (f"str={sig.strength:.2f} fresh={fresh:.2f} "
                           f"eff={eff:.2f} group={sig.group}"),
            })

    # Sort reasons by effective strength
    reasons.sort(key=lambda r: abs(float(r["detail"].split("eff=")[1].split()[0])), reverse=True)

    # Round to 6 significant figures (not fixed decimals) so micro-priced
    # coins like SHIB (~0.00002) keep valid SL/TP levels — fixed decimals
    # would round them to 0.0 and guarantee a stop-out.
    from risk import round_price
    return TradeSignal(
        direction=direction,
        confidence=round(confidence),
        ev_per_unit=round(ev, 4),
        kelly_fraction=round(kelly, 4),
        regime=regime,
        adx_value=round(adx, 1),
        posterior_bull=round(p_bull_final, 4),
        posterior_bear=round(p_bear_final, 4),
        confluence_score=round(conf, 3),
        entry=round_price(entry) if entry else 0,
        stop_loss=round_price(stop_loss) if stop_loss else None,
        take_profit=round_price(take_profit) if take_profit else None,
        risk_reward=round(risk_reward, 2),
        reasons=reasons,
        signal_breakdown=breakdown,
    )
