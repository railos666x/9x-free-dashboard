# 📡 SATS — CryptoAutomation

A Python-based crypto trading automation platform:

- **Real-time data** from Binance (REST + WebSocket-ready): candles, funding rates, macro — 87 symbols, 7 timeframes
- **Mathematical signal engine** — Bayesian fusion, Kelly Criterion, regime detection (ADX), confluence scoring
- **5 academic factors** — Trend, Momentum, Carry, Reversal, Value
- **Bloomberg-style dashboard** — FastAPI + ECharts, dark terminal aesthetic
- **Backtesting v2** — walk-forward validation, Monte Carlo, Wilson confidence intervals
- **News sentiment overlay** — 23 RSS/CryptoPanic sources, rule-based + optional LLM review

---

## Quick start (fresh clone)

```bash
# 1. Install dependencies (Python 3.11+)
python -m venv .venv
.venv/Scripts/activate            # Windows  |  source .venv/bin/activate on macOS/Linux
pip install -r backend/requirements.txt

# 2. Fetch market data (candles + funding + macro) — ~1.2M candles
python backend/fetch_all.py --fast

# 3. Start the server
cd backend && python server.py

# 4. Open the dashboard
#    http://localhost:8000   → dashboard (/)
```

Windows users can also use the one-click launchers in `scripts/` (`go.bat`, `start.bat`, `test.bat`, `backtest.bat`).

### Data fetch options

| Command | What it does |
|---|---|
| `python backend/fetch_all.py --fast` | Candles + funding + macro, skips 1m/5m (recommended first run) |
| `python backend/fetch_all.py` | Everything, including 1m/5m backfill |
| `python backend/collector.py live` | Keep prices fresh continuously (polling loop) |

The SQLite database (`crypto_data.db`, gitignored) lives at the repo root.

### Tests

```bash
python tests/test_system.py      # math engine, factors, indicators, signals
python tests/test_strategy.py    # mean-reversion strategy, risk, backtester (41 checks)
```

Both are self-contained (synthetic data) and run in CI on every push.
`tests/test_backtest.py` is a local smoke test that needs a populated `crypto_data.db`.

### Database backups

The database holds ~1.2M+ candles and is not in git. Snapshot it anytime with:

```bash
python scripts/backup_db.py          # keeps newest 10 snapshots in backups/
python scripts/backup_db.py 30       # ...or keep 30
```

Snapshots use SQLite's online backup API, so they're consistent even while
the collector is writing. Add `python scripts/backup_db.py` to a scheduled
task / cron if you want regular backups.

---

## Project layout

```
backend/            Python engine + FastAPI server (the whole platform)
  collector.py      Binance candles → SQLite (backfill + live loop)
  fetch_all.py      One-shot fetcher: candles + funding + macro
  math_engine.py    Bayesian fusion, regime detection, Kelly sizing
  factors.py        5 academic factors with weights + hit rates
  signals.py        Live per-coin signal generation
  strategy.py       Mean-reversion strategy v2 (z-score + trend filter)
  risk.py           Position sizing, slippage tiers, micro-price rounding
  backtester.py     Walk-forward / Monte Carlo / Wilson CI backtesting
  macro_data.py     42 macro instruments (metals, energy, forex, indices, rates)
  news.py           RSS + CryptoPanic collection (23 sources)
  reviewer.py       News sentiment scoring (rule-based + optional LLM)
  server.py         FastAPI app — JSON API + dashboard + background threads
  config.py         Symbols, intervals, refresh cadence, API keys
web/dashboard/      The dashboard (HTML/CSS/JS + ECharts)
tests/              Self-contained test suites
documentation/      ADRs, directives, data-source research
docs/updates/       Changelog + development notes
legacy/             Retired terminal/dashboard pages (recoverable)
scripts/            Windows launchers + db backup
```

## Configuration

- **Symbols / intervals / refresh cadence** — `backend/config.py` (87 coins across 8 categories)
- **API keys (all optional)** — copy `.env.example` to `.env`:

```bash
CRYPTOPANIC_API_KEY=your_key_here     # news sentiment votes
LLM_ENABLED=false                     # optional AI news reviewer
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

The platform runs fully without any keys (RSS-only news, no LLM overlay).

## API (FastAPI)

<<<<<<< HEAD
1. **WebSocket Server** - Custom backend for signal generation
2. **Signal Markers** - Arrow markers with tooltip reasons
3. **News Integration** - Fundamental catalysts on chart
4. **Multi-timeframe** - Sync charts across timeframes
5. **WebGL Renderer** - GPU-accelerated for 1000+ candles
6. **Web Workers** - Offload HA/S/R calculations
7. **Persistence** - LocalStorage for settings/layout
8. **Tests** - Unit tests for calculations, integration for WS
=======
| Endpoint | Purpose |
|---|---|
| `/api/candles?symbol=&interval=&limit=` | OHLCV candles |
| `/api/signals` | Factor scores + trade signals |
| `/api/watchlist` | Live watchlist across all symbols |
| `/api/news` | Recent news with sentiment |
| `/api/macro` | Macro instruments |
| `/api/portfolio` | Demo/exchange portfolio |
| `/api/backtest?symbol=BTC/USDT` | Run the v2 backtester |

## Status (2026-08)

- ✅ 87 coins configured, 1.2M+ candles in DB, 42 macro instruments, 23 news sources
- ✅ Backtester v2: walk-forward + Monte Carlo + Wilson CI; strategy v2 with trend filter
- 🔨 Strategy win rate still below the 60–70% target → parameter optimization workstream
  (see `documentation/directives/DIRECTIVE-001-parameter-optimization.md`)
>>>>>>> 030d678 (Add CI, DB backup script, and accurate README)
