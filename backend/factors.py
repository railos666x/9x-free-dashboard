"""
cryptoautomation — Multi-Factor Signal Library.

Implements 5 independent factors backed by academic research:

  1. Trend Following (⭐⭐⭐⭐⭐) — Moskowitz, Ooi & Pedersen (2012)
     Multi-lookback time-series momentum across 1d/7d/30d/90d horizons.

  2. Cross-Sectional Momentum (⭐⭐⭐⭐⭐) — Jegadeesh & Titman (1993)
     Rank coins by relative performance, go long winners, short losers.

  3. Carry (⭐⭐⭐⭐) — Koijen, Moskowitz, Pedersen & Vrugt (2018)
     Binance perpetual funding rate as a direct carry yield.

  4. Short-Term Reversal (⭐⭐⭐⭐) — Jegadeesh (1990)
     Mean reversion at 1-5 day horizons (independent of trend).

  5. Value Proxy (⭐⭐⭐⭐) — Asness, Moskowitz & Pedersen (2013)
     Price deviation from long-term moving average as "cheapness" measure.

Each factor returns a score from -1 (strongly bearish) to +1 (strongly bullish).
Factors are deliberately designed to be INDEPENDENT — they measure different
economic mechanisms, so combining them via Bayesian fusion is more robust
than using any single factor.

Key research finding: Value and Momentum are negatively correlated.
Combining them produces a more robust portfolio than either alone.

All functions are pure and testable. No API keys needed for candle-based factors.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import pandas as pd
import requests


# ──────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────

@dataclass
class FactorResult:
    """Result from a single factor computation."""
    name: str
    score: float       # -1 to +1
    confidence: float  # 0 to 1 — how reliable this reading is
    detail: str        # human-readable explanation


@dataclass
class FactorBundle:
    """All factor results for one coin at one point in time."""
    trend: FactorResult
    momentum: FactorResult
    carry: FactorResult
    reversal: FactorResult
    value: FactorResult
    composite: float   # weighted average of all factors


# ──────────────────────────────────────────────────────────────────────
# Factor Weights — how much each factor contributes to the composite
# These reflect the academic evidence strength.
# ──────────────────────────────────────────────────────────────────────
FACTOR_WEIGHTS = {
    "trend":     0.30,   # ⭐⭐⭐⭐⭐ strongest long-run evidence
    "momentum":  0.25,   # ⭐⭐⭐⭐⭐ strong across markets
    "carry":     0.20,   # ⭐⭐⭐⭐  strong but tail risk
    "reversal":  0.10,   # ⭐⭐⭐⭐  high turnover erodes it
    "value":     0.15,   # ⭐⭐⭐⭐  strong but long drawdowns
}


# ──────────────────────────────────────────────────────────────────────
# 1. Trend Following (Time-Series Momentum)
# ──────────────────────────────────────────────────────────────────────

def trend_factor(df: pd.DataFrame) -> FactorResult:
    """
    Multi-lookback time-series momentum.

    Calculates returns over 4 horizons (1d, 7d, 30d, 90d) and combines
    them with recency weighting. If the asset has been going up across
    multiple timeframes, that's a strong trend signal.

    Academic basis: Moskowitz, Ooi & Pedersen (2012) found significant
    time-series momentum across 58 futures contracts over 25 years.
    The strongest signals occur at 1-12 month horizons.

    Returns score from -1 (strong downtrend) to +1 (strong uptrend).
    """
    if len(df) < 90:
        return FactorResult("trend", 0.0, 0.0, "insufficient data (< 90 bars)")

    close = df["close"]
    current = float(close.iloc[-1])

    # Multi-lookback returns
    lookbacks = {
        "1d":  1,
        "7d":  7,
        "30d": 30,
        "90d": 90,
    }
    # Weights: longer horizons are more reliable but slower
    horizon_weights = {"1d": 0.15, "7d": 0.25, "30d": 0.30, "90d": 0.30}

    weighted_signal = 0.0
    total_weight = 0.0
    details = []

    for name, lookback in lookbacks.items():
        if len(close) <= lookback:
            continue
        past = float(close.iloc[-lookback - 1])
        if past == 0:
            continue
        ret = (current - past) / past
        # Normalise: clamp to [-1, 1] — a 50% move is max signal
        normalised = max(-1.0, min(1.0, ret / 0.50))
        w = horizon_weights[name]
        weighted_signal += normalised * w
        total_weight += w
        details.append(f"{name}: {ret:+.2%}")

    if total_weight == 0:
        return FactorResult("trend", 0.0, 0.0, "no valid lookbacks")

    score = weighted_signal / total_weight

    # Confidence based on agreement across timeframes
    signs = []
    for name, lookback in lookbacks.items():
        if len(close) <= lookback:
            continue
        past = float(close.iloc[-lookback - 1])
        if past > 0:
            ret = (current - past) / past
            signs.append(1 if ret > 0 else -1 if ret < 0 else 0)
    agreement = abs(sum(signs)) / max(len(signs), 1)
    confidence = agreement * min(total_weight / 0.75, 1.0)

    detail_str = " | ".join(details)
    return FactorResult("trend", max(-1.0, min(1.0, score)),
                        confidence, detail_str)


# ──────────────────────────────────────────────────────────────────────
# 2. Cross-Sectional Momentum
# ──────────────────────────────────────────────────────────────────────

def momentum_factor(
    df: pd.DataFrame,
    all_coins: dict[str, pd.DataFrame] | None = None,
) -> FactorResult:
    """
    Cross-sectional momentum: rank this coin against all others.

    If we have data for multiple coins, rank them by 30d return.
    Top performers get positive scores, bottom performers negative.
    This captures the academic finding that relative strength predicts
    future returns.

    Academic basis: Jegadeesh & Titman (1993) found ~0.95%/month
    excess return for buying winners and selling losers over 3-12 months.

    If only one coin is available, falls back to absolute momentum
    (positive return = +1, negative = -1).
    """
    if len(df) < 30:
        return FactorResult("momentum", 0.0, 0.0, "insufficient data (< 30 bars)")

    lookback = min(30, len(df) - 1)
    current = float(df["close"].iloc[-1])
    past = float(df["close"].iloc[-lookback - 1])
    if past == 0:
        return FactorResult("momentum", 0.0, 0.0, "invalid price data")

    my_return = (current - past) / past

    # If we have multiple coins, do cross-sectional ranking
    if all_coins and len(all_coins) >= 2:
        returns = {}
        for sym, cdf in all_coins.items():
            if len(cdf) > lookback:
                p = float(cdf["close"].iloc[-lookback - 1])
                c = float(cdf["close"].iloc[-1])
                if p > 0:
                    returns[sym] = (c - p) / p

        if len(returns) >= 2:
            # Rank: percentile within the group
            sorted_rets = sorted(returns.values())
            n = len(sorted_rets)
            rank = sorted(ren for ren in sorted_rets if ren <= my_return)
            percentile = len(rank) / n

            # Map percentile to -1..+1
            score = 2.0 * (percentile - 0.5)
            # Scale by return magnitude
            mag = min(abs(my_return) / 0.30, 1.0)
            score *= mag

            # Confidence from how extreme the ranking is
            confidence = abs(score)
            detail = (f"rank {len(rank)}/{n} | "
                      f"return {my_return:+.2%} | "
                      f"range [{sorted_rets[0]:+.2%}, {sorted_rets[-1]:+.2%}]")
            return FactorResult("momentum", max(-1.0, min(1.0, score)),
                                confidence, detail)

    # Fallback: absolute momentum (single coin)
    score = max(-1.0, min(1.0, my_return / 0.30))
    confidence = min(abs(my_return) / 0.15, 1.0)
    return FactorResult("momentum", score, confidence,
                        f"absolute return {lookback}d: {my_return:+.2%}")


# ──────────────────────────────────────────────────────────────────────
# 3. Carry (Funding Rate)
# ──────────────────────────────────────────────────────────────────────

def carry_factor(funding_rate: float | None = None) -> FactorResult:
    """
    Binance perpetual funding rate as carry yield.

    When funding is positive, longs pay shorts → being SHORT earns carry.
    When funding is negative, shorts pay longs → being LONG earns carry.

    A funding rate of +0.01% per8h = ~10.95% annualised carry for shorts.
    A funding rate of -0.01% per8h = ~10.95% annualised carry for longs.

    Academic basis: Koijen et al. (2018) found carry is a broad source of
    return predictability across asset classes. Crypto perpetual funding
    is one of the cleanest carry signals in any market — it's paid
    every8 hours and directly observable.

    Score: negative funding → bullish (longs earn carry)
           positive funding → bearish (shorts earn carry)
    """
    if funding_rate is None:
        return FactorResult("carry", 0.0, 0.0, "no funding rate data")

    # Funding rate is typically ±0.01% to ±0.3% per8h
    # Normalise: 0.01% = small signal, 0.1% = strong signal
    annualised = funding_rate * 3 * 365  # 3 funding periods per day × 365

    # Score: negative funding → bullish score (longs earn)
    # We INVERT the funding direction because carry says
    # "be on the side that earns the carry"
    score = -funding_rate / 0.001  # 0.1% = score of -1.0 or +1.0
    score = max(-1.0, min(1.0, score))

    # Confidence: high funding = strong carry signal, but cap it
    # because extreme funding often precedes reversals (tail risk)
    confidence = min(abs(funding_rate) / 0.0005, 0.9)  # cap at 0.9

    # Academic warning: carry has crash risk at extremes
    if abs(funding_rate) > 0.003:
        confidence *= 0.5  # halve confidence at extreme funding

    detail = (f"funding {funding_rate*100:+.4f}%/8h | "
              f"annualised {annualised:+.1f}% | "
              f"{'longs pay' if funding_rate > 0 else 'shorts pay'}")
    return FactorResult("carry", score, confidence, detail)


def fetch_funding_rate(symbol: str = "BTCUSDT") -> float | None:
    """
    Fetch the latest Binance perpetual funding rate.
    No API key needed — this is public data.

    Returns the funding rate as a decimal (e.g., 0.0001 = 0.01%).
    Returns None on failure.
    """
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        resp = requests.get(url, params={"symbol": symbol}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("lastFundingRate", 0))
    except Exception:
        return None


def fetch_all_funding_rates(symbols: list[str] | None = None) -> dict[str, float]:
    """
    Fetch funding rates for all watched symbols.
    Returns {symbol: funding_rate} dict.
    """
    if symbols is None:
        import config
        symbols = [s.replace("/", "") for s in config.SYMBOLS]

    rates = {}
    for sym in symbols:
        rate = fetch_funding_rate(sym)
        if rate is not None:
            rates[sym] = rate
    return rates


# ──────────────────────────────────────────────────────────────────────
# 4. Short-Term Reversal
# ──────────────────────────────────────────────────────────────────────

def reversal_factor(df: pd.DataFrame) -> FactorResult:
    """
    Short-term mean reversion signal.

    At 1-5 day horizons, recent losers tend to rebound and vice versa.
    This is INDEPENDENT of trend (which works at longer horizons).

    Academic basis: Jegadeesh (1990) found significant negative first-order
    serial correlation at short horizons, with the strongest effect at
    1-month. The effect is real but high turnover erodes returns after costs.

    We use 1d and 5d reversals with exponential weighting.
    """
    if len(df) < 10:
        return FactorResult("reversal", 0.0, 0.0, "insufficient data (< 10 bars)")

    close = df["close"]
    current = float(close.iloc[-1])

    # 1-day reversal
    prev_1d = float(close.iloc[-2])
    ret_1d = (current - prev_1d) / prev_1d if prev_1d > 0 else 0

    # 5-day reversal
    prev_5d = float(close.iloc[-6]) if len(close) >= 6 else prev_1d
    ret_5d = (current - prev_5d) / prev_5d if prev_5d > 0 else 0

    # Reversal: if price dropped, signal is BULLISH (expect bounce)
    # Invert the return direction
    rev_1d = -ret_1d
    rev_5d = -ret_5d

    # Combine: 1d is noisier (weight 0.35), 5d is more reliable (0.65)
    raw_score = 0.35 * rev_1d + 0.65 * rev_5d

    # Normalise: 3% move in 5 days = full signal
    score = max(-1.0, min(1.0, raw_score / 0.03))

    # Confidence: only confident when both timeframes agree
    agree = (rev_1d > 0 and rev_5d > 0) or (rev_1d < 0 and rev_5d < 0)
    magnitude = min(abs(raw_score) / 0.02, 1.0)
    confidence = magnitude * (0.8 if agree else 0.4)

    detail = (f"1d ret {ret_1d:+.2%} → rev {rev_1d:+.2%} | "
              f"5d ret {ret_5d:+.2%} → rev {rev_5d:+.2%} | "
              f"{'agree' if agree else 'disagree'}")
    return FactorResult("reversal", score, confidence, detail)


# ──────────────────────────────────────────────────────────────────────
# 5. Value Proxy (Mean Reversion from Fair Value)
# ──────────────────────────────────────────────────────────────────────

def value_factor(df: pd.DataFrame) -> FactorResult:
    """
    Price deviation from long-term moving average as a "cheapness" measure.

    When price is far below its200-period MA, it's "cheap" → bullish.
    When price is far above its200-period MA, it's "expensive" → bearish.

    This is a proxy for the Value factor in equities (Fama-French).
    For crypto, which has no book value or earnings, the MA-deviation
    captures the same mean-reverting tendency.

    Academic basis: Asness, Moskowitz & Pedersen (2013) found value
    premiums across 8 asset classes. For commodities and currencies,
    value is measured similarly to this — price vs long-term average.
    """
    if len(df) < 200:
        # Use available data for shorter MA
        ma_period = min(len(df), 50)
        if ma_period < 20:
            return FactorResult("value", 0.0, 0.0, "insufficient data")
    else:
        ma_period = 200

    close = df["close"]
    current = float(close.iloc[-1])
    ma = float(close.tail(ma_period).mean())

    if ma == 0:
        return FactorResult("value", 0.0, 0.0, "invalid MA")

    # Deviation from fair value
    deviation = (current - ma) / ma

    # Invert: negative deviation = cheap = bullish
    score = -deviation / 0.15  # 15% below MA = score of +1.0
    score = max(-1.0, min(1.0, score))

    # Confidence: stronger at extremes, but use Bollinger-like bandwidth
    std = float(close.tail(ma_period).std())
    if std > 0 and ma > 0:
        # How many standard deviations from the mean?
        z_score = (current - ma) / std
        # Higher z-score = more confident in reversion
        confidence = min(abs(z_score) / 2.5, 0.9)
    else:
        confidence = min(abs(deviation) / 0.10, 0.7)

    ma_label = f"MA{ma_period}"
    detail = (f"price {current:,.2f} vs {ma_label} {ma:,.2f} | "
              f"deviation {deviation:+.2%} | "
              f"{'cheap' if deviation < -0.05 else 'expensive' if deviation > 0.05 else 'fair value'}")
    return FactorResult("value", score, confidence, detail)


# ──────────────────────────────────────────────────────────────────────
# Composite Score
# ──────────────────────────────────────────────────────────────────────

def compute_all_factors(
    df: pd.DataFrame,
    all_coins: dict[str, pd.DataFrame] | None = None,
    funding_rate: float | None = None,
    weights: dict[str, float] | None = None,
) -> FactorBundle:
    """
    Compute all 5 factors and combine into a composite score.

    The composite is a confidence-weighted average: factors with higher
    confidence get more influence. This prevents a noisy low-confidence
    factor from dominating the signal.

    Args:
        df: candles for the target coin (with indicators if available)
        all_coins: {symbol: df} for cross-sectional momentum
        funding_rate: latest funding rate (decimal, e.g. 0.0001)
        weights: optional override for factor weights

    Returns:
        FactorBundle with all individual results + composite score
    """
    w = weights or FACTOR_WEIGHTS

    trend = trend_factor(df)
    mom = momentum_factor(df, all_coins)
    carry = carry_factor(funding_rate)
    rev = reversal_factor(df)
    val = value_factor(df)

    # Confidence-weighted composite
    factors = [
        (trend, w.get("trend", 0.30)),
        (mom, w.get("momentum", 0.25)),
        (carry, w.get("carry", 0.20)),
        (rev, w.get("reversal", 0.10)),
        (val, w.get("value", 0.15)),
    ]

    weighted_sum = 0.0
    total_weight = 0.0
    for factor, base_weight in factors:
        # Modulate weight by confidence: high confidence → full weight
        effective_weight = base_weight * factor.confidence
        weighted_sum += factor.score * effective_weight
        total_weight += effective_weight

    composite = weighted_sum / total_weight if total_weight > 0 else 0.0

    return FactorBundle(
        trend=trend,
        momentum=mom,
        carry=carry,
        reversal=rev,
        value=val,
        composite=max(-1.0, min(1.0, composite)),
    )


def factor_bundle_to_dict(bundle: FactorBundle) -> dict:
    """Convert a FactorBundle to a JSON-serialisable dict."""
    def _f(fr: FactorResult) -> dict:
        return {
            "score": round(fr.score, 4),
            "confidence": round(fr.confidence, 4),
            "detail": fr.detail,
        }
    return {
        "trend": _f(bundle.trend),
        "momentum": _f(bundle.momentum),
        "carry": _f(bundle.carry),
        "reversal": _f(bundle.reversal),
        "value": _f(bundle.value),
        "composite": round(bundle.composite, 4),
    }
