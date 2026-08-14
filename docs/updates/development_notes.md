# CryptoAutomation - Development Notes

## Quick Start Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Initial data backfill (run once)
python collector.py backfill

# Start live data + server
python server.py

# Server only (no live data)
python server.py --no-live

# Run backtest
python -c "
import database, backtester
conn = database.connect()
result = backtester.run_backtest(conn, 'BTC/USDT', '1h')
backtester.print_report(result)
"

# Run tests
python test_system.py
```

## Key Files Reference

| File | Purpose |
|------|---------|
| `config.py` | All configuration (symbols, intervals, API keys) |
| `collector.py` | Binance data collection (backfill + live) |
| `database.py` | SQLite operations (candles + news) |
| `indicators.py` | Technical indicators (pure functions) |
| `factors.py` | 5 academic factors (trend, momentum, carry, reversal, value) |
| `math_engine.py` | Core signal pipeline (Bayesian, regime, Kelly, etc.) |
| `signals.py` | Signal generation per coin (orchestrates math_engine) |
| `reviewer.py` | News sentiment review per coin |
| `backtester.py` | Historical replay + performance metrics |
| `server.py` | FastAPI server + JSON API + terminal |
| `news.py` | RSS + CryptoPanic news collection |
| `terminal/index.html` | Dashboard HTML |
| `terminal/assets/js/terminal.js` | Dashboard JS (state, charts, polling) |
| `terminal/assets/css/*.css` | Styling |

## Environment Variables (`.env`)

```bash
# CryptoPanic API (free tier: 600 req/day)
CRYPTOPANIC_API_KEY=your_key_here

# Optional LLM for news review
LLM_ENABLED=true
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

## Common Development Tasks

### Add New Symbol
1. Add to `config.py:SYMBOLS` array (with category comment)
2. Add to `terminal/assets/js/terminal.js:SYMBOLS` constant
3. Run `python collector.py backfill` for new symbol

### Add New Indicator
1. Add function to `indicators.py`
2. Call in `indicators.add_all()`
3. Add extraction in `math_engine.extract_indicator_signals()`
4. Add hit rates in `math_engine.DEFAULT_HIT_RATES`

### Add New Factor
1. Add function in `factors.py` returning `FactorResult`
2. Add to `factors.compute_all_factors()`
3. Add weight in `factors.FACTOR_WEIGHTS`
4. Add hit rates in `math_engine.FACTOR_HIT_RATES`
5. Add conversion in `math_engine.factors_to_signals()`

### Debug Signal for One Coin
```python
import database, signals
conn = database.connect()
signal = signals.generate_signal(conn, "ETH/USDT")
print(signal)
```

### Check Live Signal via API
```
GET http://localhost:8501/api/signals
GET http://localhost:8501/api/candles?symbol=ETH/USDT&interval=1h
GET http://localhost:8501/api/watchlist?interval=1h
```

## Signal Logic Summary

### Live System (`math_engine.compute_trade_signal`)
```
1. Regime Detection (ADX on 1h)
2. Extract signals from 1h, 4h, 1d:
   - RSI (reversal group)
   - MACD crossover (momentum group)
   - SMA 20/50 crossover (momentum group)
   - Bollinger Band touch (reversal group)
   - Volume spike (volume group)
3. Convert 5 factors → IndicatorSignals (independent groups)
4. Bayesian Fusion (log-space, regime-adjusted hit rates)
5. Freshness decay: exp(-0.15 * age_candles)
6. Confluence score (independence-weighted)
7. News overlay: 15% blend
8. Direction: P(bull) > 0.55 + confluence
9. Confidence: edge * (0.7 + 0.3 * confluence)
10. SL/TP: ATR * 1.5 / 2.5
11. Kelly: fractional (25%) based on win_rate & R:R
```

### Backtester (NOW FIXED to match live)
Uses same `math_engine.compute_trade_signal()` for entry signals.

## Troubleshooting

### "Database is locked"
- Ensure only ONE `python server.py` running
- WAL mode handles concurrent read/write but not multiple writers

### "No data for symbol"
- Run `python collector.py backfill` first
- Check symbol exists on Binance Spot (not futures-only)

### Backtest shows 0 trades
- Need 500+ candles (WARMUP_PERIOD=100 + signal needs 60)
- Check interval has enough history (1m needs 30 days = 43k bars)

### Signal differs from TradingView
- Our math engine uses Bayesian fusion (not single indicator)
- Check `signal.breakdown` for individual indicator votes
- Regime filter may suppress signals in ranging markets

### High memory usage
- `collector.py live_loop` holds connection open
- SQLite WAL file grows - checkpoint periodically
- Dashboard polls every 5s - consider WebSocket

## Performance Tuning

### Reduce API Calls
- Increase `INTERVAL_REFRESH` for higher timeframes
- Only backfill symbols you actually trade

### Speed Up Dashboard
- Enable browser caching for static assets
- Consider `/api/watchlist` caching (Redis)

### Backtest Faster
- Reduce `limit` parameter
- Parallelize per-symbol (but respect rate limits)

## Testing Checklist

Before committing:
- [ ] `python test_system.py` passes
- [ ] `python collector.py backfill` completes without errors
- [ ] `python server.py --no-live` starts and serves terminal
- [ ] Dashboard loads at http://localhost:8501
- [ ] Signals update when clicking different symbols
- [ ] Category tabs filter correctly
- [ ] Search works
- [ ] Compact mode toggles

## Known Issues / Tech Debt

1. **Duplicate symbols in config.py** - Some appear in multiple categories (OG, CTSI, etc.)
2. **No futures data** - Collector only uses spot API; perpetual funding from fapi
3. **Single-threaded backtest** - Could parallelize across symbols
4. **No WebSocket** - Dashboard polls every 5s
5. **SQLite limitations** - Not suitable for multi-process production
6. **No authentication** - Terminal open on network
7. **Hardcoded $1000 position** in backtester - should use Kelly

## Future Enhancements (Priority Order)

1. **WebSocket server** for real-time chart updates
2. **PostgreSQL/TimescaleDB** for production
3. **Worker queue** (Celery/RQ) for signal generation
4. **Authentication** (JWT + roles)
5. **Alerting** (Telegram/Discord/Email on signal change)
6. **Portfolio tracking** (positions, P&L, allocation)
7. **Paper trading** (Binance testnet integration)
8. **Options/derivatives** data (Binance options API)

## Code Style

- Type hints everywhere
- Pure functions for calculations (testable)
- Classes for stateful components (renderer, WS, app)
- CSS custom properties for theming
- ESLint/Prettier for JS, Black for Python

---

*Last updated: 2026-08-12*