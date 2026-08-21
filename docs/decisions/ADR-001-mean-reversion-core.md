# ADR-001: Volatility-Band Mean Reversion as the Primary Strategy Core

- **Status:** Accepted (v1 implemented; validation pending optimization)
- **Date:** 2026-08-14
- **Deciders:** Lead Architect (Codebuff), project owner

## Context

The project targets a 60–70% win rate with positive mathematical expectancy.
The legacy engine (Bayesian fusion + factors, R:R ≈ 1.5) measured 33–40% WR —
structurally incapable of the target (expectancy math: breakeven at R:R 1.5 is
40%). A new strategy core was required, with the data + backtesting layer
repaired first (see CHANGELOG 2026-08-14).

## Decision

Adopt **volatility-band mean reversion with a regime filter** as the primary
strategy:

- Entry on rolling z-score extremes (`|z| ≥ 2.0`, n=48) **only** in ranging
  regimes (ADX < 25), normal volatility (ATR% < 90th percentile), adequate
  liquidity.
- TP at the rolling mean; SL at 1.5×ATR; time stop 1.5×OU half-life.
- Dynamic sizing: risk % of current equity, capped by fractional Kelly.
- Costs: per-symbol slippage tiers + 10 bps/side fees on every fill.

Rationale: economically grounded short-horizon reversal (Jegadeesh 1990;
Avellaneda & Lee 2010); parameter-light (4 knobs) for overfitting resistance;
win-rate target is coherent only with small R:R, which this geometry provides.

## Alternatives considered

| Option | Verdict | Why rejected (for now) |
|---|---|---|
| Pairs / statistical arbitrage | Parked | Needs perps + funding history + cointegration refit cadence; spot can't short |
| ML ensemble (GBM) standalone | Parked | Data depth insufficient (375d × few symbols); overfitting risk |
| Legacy Bayesian engine | Rejected for target | R:R 1.5 geometry mathematically incompatible with 60–70% WR |

## Consequences

- **Positive:** vectorized (fast), measurable (CI/walk-forward/MC), auditable,
  cost-aware; extends naturally to pairs/ML as overlays.
- **Negative:** high trade frequency → cost-sensitive; long-only on spot halves
  the opportunity set; v1 measured WR (36–42%) is below target → optimization
  workstream required (Directive 001).
- **Risk:** parameter optimization could overfit → mitigated by held-out last
  fold, per-fold reporting, and Monte Carlo.

## Confirmation

Directive 001 (parameter optimization, held-out fold) is the acceptance gate
for this ADR's strategy to be considered viable.
