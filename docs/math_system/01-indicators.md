# 01 — Technical Indicators

Implemented in `backend/indicators.py`, consumed by the math engine, the
strategy, the bot's verification gate and the dashboard charts.

## Simple Moving Average (SMA)

```
SMA(n)_t = (1/n) · Σ_{i=0}^{n-1} close_{t-i}
```

Used for trend baselines (SMA20 / SMA50 on the charts) and by the
mean-reversion strategy as the entry anchor (z-score around SMA(n)).

## Exponential Moving Average (EMA)

```
EMA(n)_t = α · close_t + (1 − α) · EMA(n)_{t−1},    α = 2 / (n + 1)
```

Seeded with SMA(n) on the first bar. Used inside MACD and ATR.

## Relative Strength Index (RSI)

```
RS  = avg_gain(n) / avg_loss(n)      (Wilder smoothing: α = 1/n)
RSI = 100 − 100 / (1 + RS)
```

RSI(n) with n = 14 is the classic momentum oscillator. The proprietary
source B uses **RSI(2)** extremes (LONG when RSI(2) ≤ 25, SHORT when
RSI(2) ≥ 75, config-tunable) as a Connors-style mean-reversion trigger.

## MACD

```
MACD      = EMA(12) − EMA(26)
signal    = EMA(9) of MACD
histogram = MACD − signal
```

The 12/26/9 defaults. Crossovers and histogram slope feed the momentum
factor.

## Average True Range (ATR)

```
TR_t  = max( high_t − low_t,
             |high_t − close_{t−1}|,
             |low_t  − close_{t−1}| )
ATR(n) = EMA(n) of TR        (Wilder: α = 1/n)
```

ATR is the volatility yardstick of the system:

- regime-adaptive stop-loss / take-profit distances (see 05),
- the executor's default levels when a signal carries none
  (stop = 1.5 × ATR, target = 2R),
- the spike check in the verification gate (a close-to-close move > 8 × ATR
  without volume confirmation is treated as a bad tick).

## Bollinger Bands

```
mid  = SMA(20)
σ    = population stddev of the last 20 closes
up   = mid + 2σ
low  = mid − 2σ
```

Band touch / reversion into the band feeds the Reversal factor.

## Indicator → signal mapping

Every indicator is computed once per candle in `indicators.add_all()` and
turned into directional signals (`extract_indicator_signals()` in
`math_engine.py`), each with an indicator-specific hit rate used as the
Bayesian likelihood (see 03).
