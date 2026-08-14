# ETH Trade Loss Investigation

## Problem Statement
User reported losing money on an ETH trade. Need to understand why the signal was generated and why it resulted in a loss.

## Root Cause Found

### Different Signal Logic: Live vs Backtest
The **live trading system** (`signals.py` → `math_engine.py`) and the **backtester** (`backtester.py`) use **completely different signal generation logic**:

| Component | Live System | Backtester |
|-----------|-------------|------------|
| **Signal Engine** | `math_engine.compute_trade_signal()` | `_compute_signal_at()` (local) |
| **Indicators** | Bayesian fusion of 5 indicator groups | Factor composite only |
| **Regime Detection** | ADX-based (Trending/Ranging) | None |
| **Position Sizing** | Kelly Criterion | Fixed $1000 |
| **News Overlay** | 15% blend | None |
| **Signal Freshness** | Exponential decay | None |
| **Confluence** | Independence-weighted | None |
| **Threshold** | P(bull) > 0.55 + confidence | Factor composite > 0.15 |

### The Bug
`backtester.py:_compute_signal_at()` (lines 266-303) uses a **simplified factor-only approach**:
```python
bundle = factors.compute_all_factors(window, all_win, fund_rate)
if bundle.composite > 0.15:
    return "LONG"
elif bundle.composite < -0.15:
    return "SHORT"
```

While the live system uses the full `math_engine.compute_trade_signal()` with:
- Bayesian fusion across indicators + factors
- Regime-adjusted hit rates
- Kelly optimal sizing
- News sentiment overlay
- Signal freshness decay

### Why ETH Lost Money in Backtest
1. **No regime filter**: Backtester trades in ranging markets where trend factors fail
2. **No Kelly sizing**: Fixed position size ignores edge magnitude
3. **No stop-loss optimization**: Uses fixed 1.5x ATR without regime adjustment
4. **No signal freshness**: Trades on stale factor signals
5. **No confluence check**: Takes trades on single-factor confirmation

## The Fix

### Option 1: Align Backtester to Live System (RECOMMENDED)
Modify `_compute_signal_at()` to use the actual `math_engine.compute_trade_signal()`:

```python
def _compute_signal_at(df, all_coins, idx):
    # ... existing window slicing ...
    
    # Use the REAL math engine
    result = math_engine.compute_trade_signal(
        df_1h=window,
        df_4h=df_4h_window,  # need to slice 4h data
        df_1d=df_1d_window,  # need to slice 1d data
        news_score=0.0,  # no historical news in backtest
        factor_bundle=factors.compute_all_factors(window, all_win, fund_rate),
        all_coins=all_win,
    )
    
    return result.direction if result.direction != "HOLD" else None
```

### Option 2: Add Missing Components to Backtester
At minimum, add:
- Regime detection (ADX) to filter ranging markets
- Kelly sizing for position management
- Signal freshness check
- Confluence requirement (min 2 independent groups)

## Immediate Actions

1. **Run live backtest with math_engine** to verify ETH performance
2. **Add regime filter** to prevent ranging-market trades
3. **Add Kelly sizing** to the backtester trade simulation
4. **Log signal breakdown** for each backtest trade for debugging

## Testing

```python
# Test current (broken) backtester
result = backtester.run_backtest(conn, 'ETH/USDT', '1h', 500)

# After fix, should match live signal behavior
# Live signal for ETH/USDT can be checked via:
# GET /api/signals → find ETH/USDT → check direction, confidence, regime
```

## Prevention
- Unit test: Backtester signals == Live signals for same historical bar
- Integration test: Run both on same data, compare trade lists
- CI check: Fail if backtester logic diverges from math_engine