# 02 — Risk & Performance Math

Implemented in `backend/risk.py` (live + backtest) and `backend/backtester.py`
(reporting). Everything is a pure function — no DB, no network.

## Position sizing (risk-based, Kelly-capped)

```
risk_amount = risk_pct × equity
unit_risk   = |entry − stop_loss|          (per unit, ATR-scaled by the strategy)
size        = risk_amount / unit_risk
```

`risk_pct` defaults to `RISK_PER_TRADE` (1% of equity), is confidence-scaled
up to `MAX_RISK_PER_TRADE` (2%), and is capped at the fractional Kelly when a
measured edge exists:

```
f* = W − (1 − W) / R          (full Kelly; W = win rate, R = risk/reward)
risk_pct = min(RISK_PER_TRADE, f* × KELLY_FRACTION)   when f* > 0
```

`KELLY_FRACTION = 0.25` (25% fractional Kelly). With no measured edge the
system still risks the default 1% — Kelly only ever *shrinks* size below the
cap, it never grows it beyond `MAX_RISK_PER_TRADE`.

A notional cap (`MAX_POSITION_PCT`, default 15% of equity, × confidence
multiplier) prevents a burst of simultaneous signals from spending the book.

## Execution costs

```
cost_round_trip = (slippage_bps(symbol) + fee_bps) × 2      [bps]
cost_factor     = cost_round_trip / 10_000                  [decimal]
```

Slippage is per-symbol tiered (`SLIPPAGE_BPS_TIERS`: BTC 0.5 bps, meme coins
5 bps, default 3 bps); the fee is 10 bps per side (Binance spot taker).
Paper fills pay slippage in the direction of the trade; fees are charged on
entry and exit and subtracted from realized PnL.

## Backtest performance metrics

- **Win rate** — fraction of closed trades with PnL > 0.
- **Win-rate confidence interval** — Wilson score interval:
  ```
  z = 1.96  (95%)
  p̂ = wins / n
  denom = 1 + z²/n
  center = (p̂ + z²/2n) / denom
  margin = z·√(p̂(1−p̂)/n + z²/4n²) / denom
  CI = center ± margin
  ```
  A wide CI on few trades signals an unproven edge (the dashboard flags it).
- **Profit factor** — Σ wins / |Σ losses|.
- **Total return** — percentage change of equity over the sample.
- **Annualised return** — geometric annualisation from trade count and
  average holding time.
- **Sharpe ratio** — `mean(per-trade return) / std(per-trade return)`,
  annualised by √(trades per year).
- **Max drawdown** — largest peak-to-trough equity decline (%), over
  simulated equity from sequential PnL.
- **Calmar ratio** — annualised return / max drawdown.

## Walk-forward + Monte Carlo

- **Walk-forward** splits history into rolling train/test windows; the
  strategy is evaluated on out-of-sample windows only, so the reported
  win rate is not curve-fit.
- **Monte Carlo** bootstraps the trade sequence (resampling with
  replacement) to produce a distribution of outcomes — the CI on total
  return and the likelihood of a losing period.

## Live sizing source

The executor sizes from the **broker's account equity** when the broker is
Alpaca (fetched once per minute, cached), not the ~$10k journal equity — a
$100k paper account therefore trades ~10× larger positions, which is
intentional.
