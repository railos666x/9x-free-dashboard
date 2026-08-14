# CryptoAutomation - Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-08-13

### Added
- **New Coins**: Added 20+ coins from watchlist screenshots (OG/USDT, BEL/USDT, OGN/USDT, SLP/USDT, FIO/USDT, CREAM/USDT, MDT/USDT, IRIS/USDT, QLC/USDT, TRB/USDT, LUNA/USDT, OMN/USDT, BLOB/USDT, BTCDOM/USDT)
- **Search & Filter**: Searchable coin list with category tabs (Major, L1, L2, DeFi, Meme, Gaming, Infra, Other)
- **Sparklines**: Mini price charts in watchlist sidebar
- **Compact Mode**: Toggle for tighter spacing with 80+ coins
- **Keyboard Shortcuts**: 1-6 for intervals, arrows for navigation, `/` for search
- **Documentation**: New `/docs/updates` folder structure (10 files)
- **EV Filter**: Minimum expected value threshold in math_engine (rejects negative EV trades)
- **Regime-Adaptive SL/TP**: Wider stops in trends (2.0x/3.0x ATR), tighter in ranges (1.2x/2.0x)
- **Minimum Hold Period**: 12 candles to prevent signal flip whipsaws
- **Confidence Floor**: 30% minimum confidence to enter trades

### Changed
- **Watchlist**: Now shows 40-candle sparklines for each coin
- **Sidebar**: Responsive layout with horizontal category tabs
- **Signal Cards**: Factor score bars, math engine stats (regime, Kelly, EV, posterior), multi-timeframe dots
- **Responsive**: Better mobile/tablet support
- **Hit Rates Calibrated**: DEFAULT_HIT_RATES and FACTOR_HIT_RATES set BELOW 0.50 based on backtest evidence
- **Stricter Thresholds**: MIN_POSTERIOR=0.65, MIN_CONFIDENCE=25%

### Fixed
- **Backtester Alignment**: Fixed backtester to use the same math engine as live signals (was using simplified factor-only logic)
- **Signal Calculation**: ETH trade losses traced to backtester using different logic than live system
- **Config Sync**: terminal.js SYMBOLS array now mirrors config.py exactly
- **Dashboard 500 Error**: Added try/except fallback in signals.py for math_engine failures
- **renderStats TypeError**: Added null checks for indicator arrays in terminal.js

### Technical Debt
- Removed duplicate symbol definitions (still some remain: OG, CTSI, BEL)
- Added `_safe` helper to math_engine for NaN/inf handling
- Fixed `entry_bar.name` AttributeError in backtester
- Backtester v2 rewrite exists but legacy path still used - needs consolidation
- Unicode chars in print_report cause Windows console issues
- No futures data in collector (only spot)
- 5s polling instead of WebSocket for dashboard
- No authentication on terminal
- SQLite WAL growth needs periodic checkpoint