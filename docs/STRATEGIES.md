# STRATEGIES.md

Detailed breakdowns of trading strategies, hypotheses, and expected outcomes.

*Last updated: 2026-08-14*

---

## Strategy 1: Volatility-Band Mean Reversion (ACTIVE — v2 validated)

**Status:** Implemented (`strategy.py`) **v2 (trend-filtered)** — validated
2026-08-14 as a clear improvement over v1 (see Research Notes §5). Still below
the 60–70% target; optimization workstream open (Directive 001).

### Hypothesis
Short-horizon negative autocorrelation in crypto (Jegadeesh 1990; Avellaneda &
Lee 2010) means that in a *ranging* regime, a price extreme in z-score space is
expected to revert toward the rolling mean. Because the take-profit (the mean)
is closer than the stop-loss (m×ATR beyond entry), wins should be frequent and
small while losses are rarer and larger → 60–70% win rate band with positive
expectancy.

### Formula
```
z_t = (P_t − SMA_n) / σ_n
LONG  ⟺ z_t < −z_entry ∧ ADX < adx_max ∧ ATR% < 90th pct ∧ volume OK
SHORT ⟺ z_t > +z_entry ∧ (mirror)
TP    = SMA_n            (the mean)
SL    = entry ∓ sl_atr × ATR_14
time  = min(1.5 × OU half-life, max_hold)
```
Half-life from OLS on log prices, accepted only if the slope is significantly
negative (|t| > 1.65) and shorter than ¼ of the sample.

### Parameters (v2 defaults — all open to optimization)
| Param | Default | Rationale |
|---|---|---|
| `entry_mode` | `z` | z-score extremes (RSI(2) mode rejected at 1h) |
| `trend_filter` | **True** | LONG only above 200-MA — the v2 win (Connors/200-MA) |
| `trend_ma` | 200 | long-term trend reference |
| `z_entry` | 2.0 | entry at 2σ extreme |
| `n` | 48 | ~2 days of 1h bars |
| `sl_atr` | 1.5 | 1.5×ATR stop |
| TP | at mean | small wins |
| time stop | 1.5×HL | OU decay bound |
| `adx_max` | 25 | ranging only |
| vol pct max | 0.90 | skip extreme vol |
| volume floor | 0.3×median | liquidity |
| `hour_filter` | None | UTC hour window — mechanism only, off until validated |

### Expected outcome (if edge holds)
WR 60–70% with R:R ≈ 0.8–1.1 → E per unit risk ≈ +0.08 to +0.20.

### Measured result (v1, 375 days 1h, majors, net of fees+slippage)
- BTC: WR 37.7% (CI 29.8–46.3%), PF 0.64, −31%
- SOL: WR 35.9%, PF 0.71, −21%
- Diagnosis: 61–64% of exits are stop-losses → SL too tight / entry too eager /
  TP too far relative to cost (21 bps round trip ≈ 15–20% of risk budget).

---

## Strategy 2: Pairs / Statistical Arbitrage (RESEARCH — parked)

Cointegration z-score on perpetual pairs: `s_t = ln(P_A) − β·ln(P_B)`, entry at
±2σ, exit at 0. Literature win rates 70–85%. **Blockers:** needs both legs
sharable (perps) + funding-rate history (now being collected) + cointegration
refit cadence. Parked until Strategy 1 resolves.

## Strategy 3: ML Ensemble Overlay (RESEARCH — parked)

GBM with calibrated probabilities as a *filter* on Strategy 1, never standalone
at current data depth (overfitting risk). Needs walk-forward + purged CV.

---

## Active Workstreams

1. **Parameter optimization with held-out fold** → `docs/directives/DIRECTIVE-001-parameter-optimization.md`
2. **Full data backfill** (all 87 symbols, new depths) — one agent at a time.
3. **Cost sensitivity** — quantify fee/slippage burden (5 bps futures vs 10 bps spot).
4. **Timeframe scan** — 15m / 4h to find where the reversion edge is strongest.
