# CLAUDE.md - CryptoAutomation

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
**CryptoAutomation** - A Python-based crypto trading automation platform with:
- Real-time data collection from Binance (REST API + WebSocket ready)
- Mathematical signal engine (Bayesian fusion + Kelly Criterion + Regime detection)
- 5 academic factors (Trend, Momentum, Carry, Reversal, Value)
- Bloomberg-style terminal dashboard (FastAPI + ECharts + vanilla JS)
- Backtesting with out-of-sample validation (walk-forward, Monte Carlo)
- News sentiment overlay (RSS + CryptoPanic + optional LLM)

## Architecture

```
 Binance REST  ─▶ backend/collector.py ─▶ SQLite (crypto_data.db)
 Yahoo Finance ─▶ backend/macro_data.py ─▶ macro table
                                        │
                                        ▼
                     backend/server.py (FastAPI)
                                        │
      ┌──────────────┬──────────────────┼───────────────┐
      ▼              ▼                  ▼               ▼
 /api/candles  /api/watchlist      /api/signals    /api/news
 /api/portfolio /api/macro      /api/connect-exchange  /api/backtest
      │              │                  │               │
      ▼              ▼                  ▼               ▼
 web/dashboard (/)  ◀── ECharts ──▶  web/terminal (/terminal)
```

**Layout:** `backend/` all Python · `web/dashboard/` new Sellimor-style dashboard
(served at `/`) · `web/terminal/` terminal UI (served at `/terminal`) · `tests/`
· `scripts/` bat launchers · `legacy/` retired pages · `documentation/` ADRs +
directives · `docs/updates/` Claude's changelog.

## Development Commands
```bash
# Install dependencies
pip install -r backend/requirements.txt

# Fetch everything (candles + funding + macro) — single file
python backend/fetch_all.py --fast

# Start the server (dashboard at /, terminal at /terminal)
cd backend && python server.py

# Run backtest
python -c "
import sys; sys.path.insert(0, 'backend')
import database, backtester
conn = database.connect()
result = backtester.run_backtest(conn, 'BTC/USDT', '1h', 1000)
backtester.print_report(result)
"

# Run tests
python tests/test_system.py
python tests/test_strategy.py
```

## Key Files (all under `backend/` unless noted)

### Core Engine
- `math_engine.py` - **Core signal pipeline**: Bayesian fusion, regime detection (ADX), signal freshness decay, confluence scoring, Kelly Criterion, EV filtering
- `factors.py` - 5 academic factors: Trend (30%), Momentum (25%), Carry (20%), Reversal (10%), Value (15%)
- `indicators.py` - Technical indicators: SMA, EMA, RSI, MACD, ATR, Bollinger Bands
- `signals.py` - Live signal generation per coin (orchestrates math_engine + news); optimized, funding TTL cache
- `strategy.py` / `risk.py` / `backtester.py` - Mean-reversion strategy v2, risk model, and v2 backtester (walk-forward, Monte Carlo, Wilson CI)
- `reviewer.py` - News sentiment scoring (rule-based + optional LLM)

### Data & Infrastructure
- `collector.py` - Binance data collection (backfill + live_loop with per-interval cadence + gap repair)
- `database.py` - SQLite with WAL mode, lock-retry writes, pandas integration
- `macro_data.py` - 42 macro instruments (metals/energy/ags/forex/indices/rates) from Yahoo
- `fetch_all.py` - single-file fetcher: candles + funding + macro (`--fast` skips 1m/5m)
- `portfolio.py` - demo/exchange portfolio priced live (signed Binance balances when keys set)
- `server.py` - FastAPI server + JSON API + both dashboards + background threads
- `news.py` - RSS + CryptoPanic news collection (23 sources)
- `config.py` - All configuration: symbols (87), intervals, API keys, refresh cadence

### Dashboards
- `web/dashboard/` - **The only dashboard** (served at `/`): index.html +
  dashboard.css + dashboard.js. Working sidebar views: Dashboard, Assets,
  My portfolio, Bots (backtest), Signals. The old terminal was retired
  2026-08-15 — it lives in `legacy/terminal/` (recoverable) and `/terminal`
  is gone.

## Data Flow
```
Binance REST → collector.py → SQLite
                    ��
server.py (FastAPI) ← database.py
                    ��
         ��──────────��──────────��
         ��                     ��
   /api/signals           /api/candles
   /api/watchlist         /api/news
         ��                     ��
   terminal.js (polling 5s) → ECharts + UI
```

## Adding Features
1. **New indicator**: Add to `indicators.py` → call in `add_all()` → extract in `math_engine.extract_indicator_signals()` → add hit rates
2. **New factor**: Add in `factors.py` → add to `compute_all_factors()` → add weight + hit rates → convert in `factors_to_signals()`
3. **New data source**: Implement provider pattern in collector.py (see OpenBB research in docs/updates/openbb_gloomberb_research.md)
4. **New signal type**: Extend `TradeSignal` in `math_engine.py` + update `signals.py` + dashboard rendering

## Performance Notes
- Zero allocations in render loop (reuse objects)
- Reactive render on data change (no requestAnimationFrame needed)
- Web Workers: move `calculateHeikinAshi`/`detectSupportResistance` off main thread
- WebGL: swap `CanvasRenderingContext2D` for `WebGL2RenderingContext` in ChartRenderer
- Current: 5s polling → Target: WebSocket for real-time updates

## Configuration (backend/config.py)
- **SYMBOLS**: 87 coins across 8 categories (Major, L1, L2, DeFi, Meme, Gaming, Infra, Other) — dead listings removed
- **INTERVALS**: 1m, 5m, 15m, 1h, 4h, 1d (DEFAULT: 15m)
- **Refresh cadence**: 1m:5s, 5m:15s, 15m:30s, 1h:60s, 4h:300s, 1d:900s
- **API Keys**: CRYPTOPANIC_API_KEY, LLM_* (optional)

## Environment Variables (.env)
```bash
CRYPTOPANIC_API_KEY=your_key_here
LLM_ENABLED=true
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

## Documentation
All updates documented in `docs/updates/`:
- `CHANGELOG.md` - Chronological changes
- `TASKS_TODO.md` - Full roadmap with priorities
- `CONTEXT_HANDOFF.md` - Session handoff context
- `eth_trade_investigation.md` - Root cause of ETH losses
- `openbb_gloomberb_research.md` - Integration roadmap
- `architecture.md` - System architecture detail
- `development_notes.md` - Dev reference

Data plumbing (what the dashboard and backtester run on):
- `documentation/needed-apis-data-sources/00-dashboard-data-sources.md` - every source/API feeding the dashboard
- `documentation/needed-apis-data-sources/01-future-paid-sources-and-apis.md` - researched paid/fast providers (WebSocket, tick data, news/sentiment) to make the dashboard faster + more accurate
- `documentation/needed-apis-data-sources/02-backtesting-data-sources.md` - exactly how backtests get their data + limitations

## Current Status (2026-08-15)
- ✅ 87 coins configured; **1.2M+ candles** in DB (87/87 symbols with 1h data; backfill complete)
- ✅ 42 macro instruments live (gold, silver, oil, forex, indices, rates) + `/api/macro`
- ✅ 23 news sources; reviewer scores 35 symbols
- ✅ New Sellimor-style dashboard at `/` (portfolio, charts, all-crypto table, connect-exchange modal)
- ✅ Backtester v2: walk-forward, Monte Carlo, Wilson CI; strategy v2 with trend filter (BTC WR 37.7% → 52.4%)
- ⚠️ Strategy still below the 60–70% target → parameter optimization workstream (see documentation/directives/)

## Multi-Agent Coordination (IMPORTANT — read before editing)

Two AI agents work this repo. Rules to prevent loops and clobbering:
1. **File ownership**: The strategy/risk/backtest stack (`strategy.py`, `risk.py`, `backtester.py`, `portfolio.py`, `macro_data.py`, `fetch_all.py`, `server.py`, `signals.py`, `database.py`, `config.py`, `collector.py`, `math_engine.py`, `tests/`) is owned by the strategy agent. The dashboard UI (`web/dashboard/`), `docs/updates/`, and legacy pages are the UI agent's territory. Don't edit the other's files without user confirmation.
2. **mtime check before editing**: if a file changed since your last write, re-read and merge rather than overwrite.
3. **One network job at a time**: backfills and funding fetches write `crypto_data.db` — run sequentially, never concurrently.
4. **No edit ping-pong**: if the other agent changed something you own, flag it to the user instead of auto-reverting.
5. **No commits/pushes** without the user.
6. **Docs**: strategy agent's records live in `documentation/` (CHANGELOG, ADRs, directives); UI agent's in `docs/updates/`.

---

*Last updated: 2026-08-15*