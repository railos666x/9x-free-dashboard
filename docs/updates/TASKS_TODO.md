# CryptoAutomation - Task List & Roadmap

*Last Updated: 2026-08-15*

> **📌 Session tracker (current):** this file is the legacy roadmap. The
> authoritative list of completed + pending work for the current session lives in
> `documentation/FOLLOWUPS_TRACKER.md` (created 2026-08-15, branch
> `feature/ai-execution-news-gate`). Resume from there.

---

## ✅ COMPLETED TODAY

### 1. Added New Coins from Watchlist Screenshots
- **Files**: `config.py`, `terminal/assets/js/terminal.js`
- **Added**: 20+ coins (OG, BEL, OGN, SLP, FIO, CREAM, MDT, IRIS, QLC, TRB, LUNA, OMN, BLOB, BTCDOM, etc.)
- **Total**: ~98 coins (was ~78)
- **Status**: Done - needs backfill verification

### 2. Dashboard UI/UX Major Overhaul
- **Files**: `terminal/index.html`, `terminal/assets/js/terminal.js`, `terminal/assets/css/terminal.css`
- **Features Added**:
  - Search box with `/` keyboard shortcut
  - Category tabs (All, Major, L1, L2, DeFi, Meme, Gaming, Infra, Other) with counts
  - Sparklines (40-candle mini charts) on hover/active
  - Compact mode toggle (tighter spacing, hides sparklines)
  - Keyboard shortcuts: 1-6 intervals, ↑↓ navigate, `/` search
  - Responsive breakpoints updated
- **Status**: Done - needs testing with 80+ coins

### 3. Fixed ETH Trade Loss / Backtester Alignment
- **Root Cause**: Backtester used simplified factor-only logic vs live system's full math engine
- **Files**: `backtester.py`
- **Fix**: `_compute_signal_at()` now calls `math_engine.compute_trade_signal()` with full Bayesian fusion, regime detection, Kelly sizing, freshness, confluence, news overlay
- **Trade Simulation**: Uses math engine's SL/TP when available
- **Status**: Code fixed - needs validation with more data

### 4. Documentation Folder Created
- **Location**: `docs/updates/`
- **Files Created**:
  - `README.md` - Index
  - `CHANGELOG.md` - Chronological changes
  - `coins_added.md` - New coins detail
  - `ui_ux_improvements.md` - Dashboard features
  - `eth_trade_investigation.md` - Root cause & fix
  - `architecture.md` - System architecture
  - `development_notes.md` - Dev reference
  - `institutional_news_sources.md` - User's research notes
  - `TASKS_TODO.md` - This file

---

## 🔄 IN PROGRESS / NEEDS VERIFICATION

### 5. Backtester Validation
- [ ] Run full backtest on ETH/USDT with 1000+ bars
- [ ] Compare live signals vs backtest signals for same historical bars
- [ ] Test BTC, SOL, other major coins
- [ ] Verify Kelly sizing affects position sizes correctly
- [ ] Check regime filtering reduces ranging-market trades

### 6. Data Backfill for New Coins
- [ ] Run `python collector.py backfill` for all new symbols
- [ ] Identify which symbols fail (not on Binance Spot)
- [ ] Remove unavailable symbols from config
- [ ] Update terminal.js to match

---

## 📋 PENDING TASKS

### Data & Collection
- [ ] **Futures Data**: Collector only uses Spot API; need fapi.binance.com for perpetual funding rates
- [ ] **WebSocket Upgrade**: Replace 5s polling with real-time WebSocket (Binance streams)
- [ ] **Rate Limit Handling**: Add exponential backoff + request queueing
- [ ] **Multi-Exchange**: Add Bybit, OKX, Coinbase data sources

### Signal Engine Improvements
- [ ] **Historical Hit Rates**: Track actual indicator performance per symbol/regime; update priors
- [ ] **Factor Orthogonalization**: Ensure factors are truly independent (correlation analysis)
- [ ] **Regime Hysteresis**: Prevent rapid regime flipping
- [ ] **News NLP**: Replace keyword sentiment with proper NLP (RavenPack, FinBERT)
- [ ] **On-Chain Factors**: Add Glassnode/Amberdata metrics (MVRV, NUPL, exchange flows)

### Backtester Enhancements
- [ ] **Parallel Backtests**: Run multiple symbols simultaneously
- [ ] **Walk-Forward Optimization**: Rolling train/test windows
- [ ] **Transaction Cost Model**: Realistic fees, slippage, funding costs
- [ ] **Monte Carlo**: Bootstrap trade distribution for confidence intervals
- [ ] **Parameter Sensitivity**: Heatmaps for threshold/sl_mult/tp_mult

### Dashboard Features
- [ ] **Portfolio View**: Positions, P&L, allocation, risk metrics
- [ ] **Alert System**: Telegram/Discord/Email on signal changes
- [ ] **Signal History**: Timeline of past signals with outcomes
- [ ] **Factor Attribution**: Per-trade factor contribution analysis
- [ ] **Custom Watchlists**: User-defined groups
- [ ] **Dark/Light Theme**: Already has - verify all components
- [ ] **Export**: CSV/JSON for signals, trades, backtest results

### Infrastructure
- [ ] **PostgreSQL/TimescaleDB**: Replace SQLite for production
- [ ] **Redis Caching**: Cache /api/watchlist, /api/signals
- [ ] **Authentication**: JWT + role-based access
- [ ] **Docker Compose**: One-command deploy
- [ ] **CI/CD**: GitHub Actions for tests + deploy
- [ ] **Monitoring**: Prometheus + Grafana for system health
- [ ] **Logging**: Structured JSON logs with correlation IDs

### Paper/Live Trading
- [ ] **Binance Testnet**: Paper trading integration
- [ ] **Order Management**: OCO orders, position tracking
- [ ] **Risk Management**: Max position size, daily loss limit, correlation limits
- [ ] **Execution Algos**: TWAP, VWAP, iceberg for larger sizes

### Institutional News Integration (from user notes)
- [ ] **Phase 1**: RavenPack Crypto or Kaiko News API
- [ ] **Phase 2**: NLP pipeline (entity extraction + sentiment)
- [ ] **Phase 3**: Event-driven architecture (webhooks)
- [ ] **Phase 4**: Social sentiment (Twitter/Reddit firehose)
- [ ] **Budget**: ~$35-70k/year for full stack

---

## 🐛 KNOWN ISSUES / TECH DEBT

1. **Duplicate symbols** in config.py (OG, CTSI, BEL appear in multiple categories)
2. **Unicode chars** in backtester print_report cause Windows console errors
3. **No futures data** in collector (only spot)
4. **Single-threaded backtester** - slow for multi-symbol
5. **No authentication** on terminal
6. **Hardcoded $1000** position in backtester
6. **SQLite WAL growth** - needs periodic checkpoint
7. **5s polling** instead of WebSocket

---

## 🎯 NEXT SESSION PRIORITIES

1. **Run full backfill** for new coins → identify unavailable symbols
2. **Validate backtester** with 1000+ bars on ETH, BTC, SOL
3. **Fix Unicode** in print_report (already done, verify)
4. **Clean duplicate symbols** from config.py
5. **Test dashboard** with full 98-coin list

---

## 📝 NOTES FOR NEXT SESSION

### To Run Backfill:
```bash
python collector.py backfill
```

### To Test Backtester:
```bash
python -c "
import database, backtester
conn = database.connect()
result = backtester.run_backtest(conn, 'ETH/USDT', '1h', 1000)
backtester.print_report(result)
"
```

### To Start Server:
```bash
python server.py
# Visit http://localhost:8501
```

### Key Files to Remember:
- `config.py` - Source of truth for symbols
- `backtester.py:_compute_signal_at()` - Now uses math_engine
- `math_engine.py` - Core signal logic (Bayesian, Kelly, regime)
- `terminal/assets/js/terminal.js` - UI state & rendering

---

*Session paused. Resume from here.*