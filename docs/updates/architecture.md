# CryptoAutomation - System Architecture

## Overview
High-performance crypto trading automation platform with:
- Real-time data collection from Binance
- Mathematical signal engine (Bayesian fusion + Kelly Criterion)
- 5 academic factors (Trend, Momentum, Carry, Reversal, Value)
- Bloomberg-style terminal dashboard (FastAPI + ECharts)
- Backtesting with out-of-sample validation

## Data Flow

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Binance   │────▶│  collector.py │────▶│  SQLite DB  │
│  WebSocket  │     │  (live_loop)  │     │ (crypto_data│
│  + REST API │     └──────────────┘     │    .db)     │
└─────────────┘                          └──────┬──────┘
                                                 │
                    ┌──────────────┐             │
                    │   server.py  │◀────────────┤
                    │  (FastAPI)   │             │
                    └──────┬───────┘             │
                           │                     │
              ┌────────────┼────────────┐        │
              ▼            ▼            ▼        ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ /candles │ │/watchlist│ │ /signals │ │ /news    │
        └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │            │
             ▼            ▼            ▼            ▼
        ┌──────────────────────────────────────────────┐
        │         terminal/index.html (ECharts)        │
        │   terminal/assets/js/terminal.js (State)     │
        └──────────────────────────────────────────────┘
```

## Core Modules

### 1. Data Collection (`collector.py`)
- **backfill**: Historical download (30 days default) for all symbols × intervals
- **live_loop**: Continuous polling with per-interval cadence
  - 1m: every 5s, 5m: every 15s, 15m: every 30s
  - 1h: every 60s, 4h: every 300s, 1d: every 900s
- Rate limit aware (Binance public: 1200 req/min)

### 2. Database (`database.py`)
- SQLite with WAL mode (concurrent read/write)
- Tables: `candles` (PK: symbol, interval, open_time), `news`
- Pandas integration for fast loads

### 3. Indicators (`indicators.py`)
- Pure functions: SMA, EMA, RSI, MACD, ATR, Bollinger Bands
- `add_all()`: Adds all indicators to DataFrame

### 4. Factors (`factors.py`) - 5 Independent Factors
| Factor | Academic Basis | Weight |
|--------|----------------|--------|
| Trend Following | Moskowitz, Ooi & Pedersen (2012) | 30% |
| Cross-Sectional Momentum | Jegadeesh & Titman (1993) | 25% |
| Carry (Funding Rate) | Koijen et al. (2018) | 20% |
| Short-Term Reversal | Jegadeesh (1990) | 10% |
| Value Proxy (MA Deviation) | Asness, Moskowitz & Pedersen (2013) | 15% |

### 5. Math Engine (`math_engine.py`) - Core Signal Pipeline
1. **Regime Detection** (ADX): Trending Up/Down vs Ranging
2. **Signal Extraction**: RSI, MACD, SMA, BB, Volume from each timeframe
3. **Factor Conversion**: Factors → IndicatorSignals (independent groups)
4. **Bayesian Fusion**: Log-space updating with regime-adjusted hit rates
5. **Freshness Decay**: exp(-λ × age), λ=0.15
6. **Confluence Score**: Independence-weighted agreement
7. **News Overlay**: 15% blend (reduced from 20% due to factors)
8. **Direction/Confidence**: P(bull) > 0.55 + confluence multiplier
9. **Risk Levels**: ATR-based SL (1.5x) / TP (2.5x)
10. **Kelly Criterion**: Fractional Kelly (25%) for position sizing

### 6. Signals (`signals.py`)
- Orchestrates math_engine per coin
- Adds news scores from reviewer
- Returns full TradeSignal with breakdown

### 7. Reviewer (`reviewer.py`)
- Rule-based sentiment scoring per coin
- Optional LLM overlay (OpenAI-compatible)
- Outputs: coin scores, stance, top reasons

### 8. Backtester (`backtester.py`)
- Replays historical data through full pipeline
- Out-of-sample validation (60/40 split)
- Metrics: Win rate, Sharpe, Max DD, Calmar, Profit Factor
- Exit analysis: SL / TP / Timeout / Signal Flip

### 9. Server (`server.py`)
- FastAPI serving terminal + JSON API
- Daemon threads: price collector + news fetcher
- Endpoints: /candles, /watchlist, /signals, /news, /review, /backtest, /health

### 10. Terminal (`terminal/`)
- `index.html`: Semantic HTML, accessibility
- `assets/js/terminal.js`: State management, ECharts rendering, polling
- `assets/css/`: Dark theme, responsive, compact mode

## Configuration (`config.py`)

### Symbols (~98 coins)
Organized by category: Major, L1, L2, DeFi, Meme, Gaming, Infra, Other

### Intervals
1m, 5m, 15m, 1h, 4h, 1d (DEFAULT: 15m)

### Refresh Cadence
| Interval | Live Refresh | Watchlist Update |
|----------|--------------|------------------|
| 1m | 5s | 5s (via API poll) |
| 5m | 15s | |
| 15m | 30s | |
| 1h | 60s | |
| 4h | 300s | |
| 1d | 900s | |

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Symbols tracked | ~98 |
| Intervals per symbol | 6 |
| Candles stored (30d @ 1m) | ~4.2M |
| Live poll frequency | 5s |
| Dashboard API poll | 5s |
| Signal generation | <50ms per coin |
| Backtest (500 bars) | ~2-5s |

## Deployment

### Development
```bash
pip install -r requirements.txt
python collector.py backfill  # initial data
python server.py              # starts on :8501
```

### Production Considerations
- Run collector.py live as systemd service
- Run server.py behind nginx + gunicorn
- SQLite → PostgreSQL for multi-process
- Add Redis for caching /api/watchlist
- WebSocket for real-time chart updates

## Future Architecture Improvements

1. **WebSocket Server**: Push updates instead of 5s polling
2. **Worker Pool**: Offload signal generation to workers
3. **TimescaleDB**: Better time-series compression
4. **Message Queue**: Decouple collector → processor → storage
5. **WebAssembly**: Move math_engine to client for instant signals
6. **WebGL Charts**: 1000+ candle smooth rendering