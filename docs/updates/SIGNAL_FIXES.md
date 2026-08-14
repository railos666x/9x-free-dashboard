# Signal Engine Fixes - Root Cause & Solutions

*Date: 2026-08-12*

---

## Root Cause: Over-Optimistic Hit Rate Priors

The Bayesian fusion in `math_engine.py` was using **inflated default hit rates** (52-60%) that don't reflect real crypto market indicator performance. This caused:

1. **Inflated posteriors** → P(bull) > 0.55 threshold easily met
2. **Too many trades** → 21 trades on ETH in 1000 bars
3. **Low win rate** → 33% (breakeven for 1.67 R:R is 37.5%)
4. **Most trades hit SL** → 62-78% stop loss exits

---

## Fixes Applied

### 1. Conservative Default Hit Rates (`math_engine.py:77-83`)

**Before:**
```python
DEFAULT_HIT_RATES = {
    "RSI":    {"bull": 0.58, "bear": 0.58},
    "MACD":   {"bull": 0.55, "bear": 0.55},
    "SMA":    {"bull": 0.52, "bear": 0.52},
    "BB":     {"bull": 0.56, "bear": 0.56},
    "Volume": {"bull": 0.53, "bear": 0.53},
}
```

**After:**
```python
DEFAULT_HIT_RATES = {
    "RSI":    {"bull": 0.53, "bear": 0.53},  # mean reversion weak in trends
    "MACD":   {"bull": 0.52, "bear": 0.52},  # lagging, many false crosses
    "SMA":    {"bull": 0.51, "bear": 0.51},  # essentially coin flip
    "BB":     {"bull": 0.53, "bear": 0.53},  # touches ≠ reversals
    "Volume": {"bull": 0.52, "bear": 0.52},  # volume spikes often noise
}
```

### 2. Conservative Factor Hit Rates (`math_engine.py:474-480`)

**Before:**
```python
FACTOR_HIT_RATES = {
    "Trend":    {"bull": 0.60, "bear": 0.60},
    "Momentum": {"bull": 0.58, "bear": 0.58},
    "Carry":    {"bull": 0.56, "bear": 0.56},
    "Reversal": {"bull": 0.54, "bear": 0.54},
    "Value":    {"bull": 0.57, "bear": 0.57},
}
```

**After:**
```python
FACTOR_HIT_RATES = {
    "Trend":    {"bull": 0.54, "bear": 0.54},
    "Momentum": {"bull": 0.53, "bear": 0.53},
    "Carry":    {"bull": 0.52, "bear": 0.52},
    "Reversal": {"bull": 0.51, "bear": 0.51},
    "Value":    {"bull": 0.53, "bear": 0.53},
}
```

### 3. Stricter Entry Threshold (`math_engine.py:630-640`)

**Before:** `MIN_POSTERIOR = 0.55`
**After:** `MIN_POSTERIOR = 0.60`

**Rationale:** 0.55 was too permissive → many low-edge trades. 0.60 requires genuine edge.

### 4. Regime-Adaptive SL/TP (`math_engine.py:651-662`)

**Before:** Fixed 1.5x ATR SL, 2.5x ATR TP

**After:**
```python
if regime == Regime.TRENDING_UP or regime == Regime.TRENDING_DOWN:
    sl_mult = 2.0   # wider stop in trends (avoid noise)
    tp_mult = 3.0   # let winners run
else:  # RANGING
    sl_mult = 1.2   # tight stop in ranges
    tp_mult = 2.0   # quick profit target
```

### 5. Fractional Kelly (`math_engine.py:678`)

**Before:** `kelly_criterion(est_wr, risk_reward, 1.0)`
**After:** `kelly_criterion(est_wr, risk_reward, 0.25)`

**Rationale:** 25% fractional Kelly reduces variance & accounts for estimation error in win rate.

---

## Expected Impact

| Metric | Before | Expected After |
|--------|--------|----------------|
| Trades/1000 bars | 21 (ETH) | ~8-12 (fewer, higher quality) |
| Win Rate | 33% | 45-55% |
| SL Exit % | 62-78% | 35-50% |
| Profit Factor | 0.62 | 1.3-1.8 |
| Kelly Fraction | ~0.15 | ~0.05-0.10 (smaller, safer) |

---

## Validation Required

Run backtests after module reload:

```python
import database, backtester
conn = database.connect()
for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']:
    result = backtester.run_backtest(conn, symbol=sym, interval='1h', limit=1000)
    backtester.print_report(result)
```

---

## Additional Recommendations

### 1. Historical Hit Rate Calibration
Track actual indicator performance per symbol/regime:
```python
# In database, add table:
# indicator_performance(symbol, indicator, regime, hit_rate_bull, hit_rate_bear, n_trades)
# Update priors weekly from live data
```

### 2. Minimum Confluence Requirement
Add check: `confluence_score > 0.3` before allowing trade (require ≥2 independent groups agreeing)

### 3. Volatility Filter
Skip trades when ATR percentile > 90 (extreme volatility = wider stops = lower R:R)

### 4. Funding Rate Filter
For shorts: require negative funding (carry supports trade)
For longs: require positive funding

### 5. Multi-Timeframe Confirmation
Require 1h + 4h direction alignment (already in breakdown, add to entry logic)

---

*All changes in `math_engine.py` - lines 77-83, 474-480, 630-640, 651-662, 678*