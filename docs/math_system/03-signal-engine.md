# 03 — Signal Engine (Bayesian Fusion)

Implemented in `backend/math_engine.py` (fusion) and `backend/factors.py`
(the five academic factors). This is **Source A** of the bot.

## Factors (the prior structure)

| Factor | Weight | Meaning |
|---|---|---|
| Trend | 30% | SMA/EMA alignment + ADX direction |
| Momentum | 25% | MACD / RSI drift over the window |
| Carry | 20% | Funding-rate direction (when futures data is loaded) |
| Reversal | 10% | Distance from Bollinger extremes, RSI extremes |
| Value | 15% | Deviation from fair-value proxy (z-score vs SMA) |

Each factor votes LONG/SHORT/HOLD with a per-factor hit rate. The composite
prior for direction `d` is a weighted average of the per-factor probabilities.

## Bayesian update

Each independent indicator signal is a piece of evidence `E` with a measured
historical hit rate (posterior calibration, `DEFAULT_HIT_RATES`). The engine
combines them multiplicatively:

```
P(d | E₁…Eₙ) ∝ P(d) · Π P(Eᵢ | d)
```

with a prior `P(d)` built from the factor composite and a small
regularisation floor so no single indicator dominates. The result is the
**posterior confidence** for LONG / SHORT.

Tightening knobs (config):
- `MIN_POSTERIOR` (0.65) — minimum posterior to emit a direction,
- `MIN_CONFIDENCE` (25–30%) — confidence floor,
- `EV` filter — trades are rejected when expected value is negative:
  ```
  EV = P(win) × reward − P(loss) × risk
  ```

## Regime detection (ADX)

```
+DI  = 100 × smoothed(+DM) / ATR
−DI  = 100 × smoothed(−DM) / ATR
DX   = 100 × |+DI − −DI| / (+DI + −DI)
ADX  = smoothed(DX)
```

ADX < threshold → **ranging** (mean-reversion favoured, tighter SL/TP);
ADX ≥ threshold → **trending** (momentum favoured, wider SL/TP). The regime
switch is dampened (hysteresis) to prevent rapid flipping.

## Signal freshness + confluence

- **Freshness decay** — a signal computed on stale candles is weighted down
  exponentially with candle age; very stale signals are dropped (this is
  why the data gate matters — see 05).
- **Confluence scoring** — the final confidence is boosted when several
  independent indicators agree and no factor strongly opposes.

## Why source A is often quiet

The engine is calibrated to be strict (`MIN_POSTERIOR=0.65`), so in a normal
market it emits mostly HOLD. That is by design: a quiet, high-precision
source A makes the two-source agreement gate meaningful. When the bot holds
everything with `no_signal_a` / `no_agreement`, this engine is saying "no
high-conviction setup right now".
