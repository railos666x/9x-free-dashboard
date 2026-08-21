# CLAUDE.md - CryptoAutomation

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
**CryptoAutomation (SATS)** - An AI/news-driven automated trading system:
- **AI-only trade path (since 2026-08-17)**: Groq news analyst → 6-layer
  confirmation (sentiment, freshness, calendar guard, AI confirm, DXY filter,
  smart-money confluence) → circuit breaker → intent dedup → execution →
  SL/TP monitor. Trades the **Exness forex/metals/energy/indices** universe
  on MT5 (`config.TRADING_UNIVERSE`). `AI_MODE=test` = paper, `live` = real.
- **Data platform**: Binance REST (87 symbols, candles + funding), Yahoo macro
  (42+ instruments), free external sources (CoinGecko, CoinPaprika, DefiLlama,
  Etherscan, FRED, eCON, Fed/Treasury, Frankfurter, Fear&Greed, MarketAux,
  Mempool, NewsData.io, Tavily/Firecrawl) aggregated in `external_data.py`
- **Legacy quant stack (retired from trading, dashboard/backtester only)**:
  Bayesian math engine, 5 academic factors, mean-reversion strategy v2,
  backtester v2 (walk-forward, Monte Carlo, Wilson CI)
- **Dashboard**: FastAPI + ECharts dark terminal at `/`
- **Safety**: tiered circuit breaker, verification gates, intent-hash idempotent
  execution, live-mode kill-switches

## Architecture

```
 news feed (RSS/CryptoPanic/Tavily/Alpha Vantage) ─▶ SQLite (news)
 Binance REST ─▶ collector.py ────────────────────▶ SQLite (candles, funding)
 Yahoo/Free APIs ─▶ macro_data.py + external sources ─▶ SQLite / live calls
                                         │
        ┌────────────────────────────────┴───────────────────────────┐
        ▼                                                            ▼
  bot.py (one cycle)                                   server.py (FastAPI)
   verification → news_analyst (Groq)                         │
   → 6-layer confirm (calendar/ai_confirm/dxy/smart_money)    ▼
   → circuit_breaker → arbitrator (intent_hash)          /api/candles /api/signals
   → verification → execution (paper | MT5 | Binance)     /api/news   /api/bot/*
   → SL/TP monitor → ai_feedback                         /api/orders /api/circuit-breaker
        │                                                 /api/feedback/* /api/tutor
        ▼                                                 ▼
   orders / test_trades / ai_analyses          web/dashboard (/) ◀── ECharts
```

**Layout:** `backend/` all Python · `web/dashboard/` the one dashboard
(served at `/`) · `tests/` · `scripts/` bat launchers + ops/debug scripts ·
`data/` DB + logs + runtime artifacts (gitignored) · `docs/` all
documentation (ADRs, changelogs, math specs) · `documentation/` deep research

## Development Commands
```bash
# Install dependencies
pip install -r backend/requirements.txt

# Fetch everything (candles + funding + macro) — single file
python backend/fetch_all.py --fast

# Start the server (dashboard at /)
cd backend && python server.py

# Run the bot (paper mode is the default)
python backend/bot.py --dry-run     # journal intents, no broker
python backend/bot.py loop          # scheduled cycles every BOT_LOOP_SECONDS

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

### AI Trading Core (the live trade path)
- `bot.py` - **Bot orchestrator**: verification → news analyst → 6-layer confirm → circuit breaker → arbitrator → execution → SL/TP monitor; `run_once` / `loop` / `--dry-run`
- `news_analyst.py` - Groq LLM news sentiment & analytics (main trade source); Groq → OmniRoute → rule-based chain; never raises
- `ai_agents.py` - 3 production agents: catalyst sentiment, HODL research, chart confluence gate (`AI_CONFLUENCE_GATE`)
- `ai_confirm.py` - Technical confirmation layer (regime + 6-factor score + session); referee, not generator
- `ai_feedback.py` - Outcome recording + threshold calibration suggestions
- `calendar_guard.py` - Blocks trading around high-impact events (NFP/FOMC/CPI/GDP)
- `dxy_filter.py` - USD-pair master filter (Dollar Smile, SMT divergence)
- `smart_money.py` - ICT confluence: order blocks, FVG, liquidity sweeps, BOS/CHoCH (score 0-4)
- `circuit_breaker.py` - Tiered drawdown step-down + recovery ladder + loss breakers
- `verification.py` - Hard data-quality gates (staleness, OHLC sanity, spikes, cross-source)
- `arbitrator.py` - Proposal → Intent; intent-hash dedup (no duplicate orders)
- `execution.py` - **The ONLY module that touches a broker** (paper/alpaca/binance/mt5/hybrid)
- `test_ai.py` - `test_trades` paper table + AI-mode helpers
- `tutor.py` - Educational explainers (`/api/tutor`), "why this trade"
- `proprietary_signals.py` — RETIRED second source (kept for tests/reference only)

### Legacy Quant Stack (dashboard + backtester only — NOT the trade path)
- `math_engine.py` - Bayesian fusion, regime detection (ADX), Kelly Criterion, EV filtering
- `factors.py` - 5 academic factors: Trend (30%), Momentum (25%), Carry (20%), Reversal (10%), Value (15%)
- `indicators.py` - Technical indicators: SMA, EMA, RSI, MACD, ATR, Bollinger Bands
- `signals.py` - Display signal generation per coin (orchestrates math_engine + news)
- `strategy.py` / `risk.py` / `backtester.py` - Mean-reversion strategy v2, risk model, v2 backtester (walk-forward, Monte Carlo, Wilson CI)
- `reviewer.py` - News verification (rule-based + free-LLM failover chain)

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
    dashboard.js (polling) → ECharts + UI
```

## Adding Features
1. **New indicator**: Add to `indicators.py` → call in `add_all()` → extract in `math_engine.extract_indicator_signals()` → add hit rates
2. **New factor**: Add in `factors.py` → add to `compute_all_factors()` → add weight + hit rates → convert in `factors_to_signals()`
3. **New free data source**: Add a fail-safe module next to `coingecko.py` (never raises, permissive defaults, config toggle) → register in `external_data.get_all_data()`
4. **New confirmation layer**: Add to the 6-layer pipeline in `bot.py` with a config toggle; must be fail-open/permissive and journaled
5. **New signal type**: Extend `TradeSignal` in `math_engine.py` + update `signals.py` + dashboard rendering (legacy display only — new TRADE sources must go through the AI pipeline)

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
All documentation lives in `docs/`:
- `docs/CHANGELOG.md` - Chronological changes
- `docs/FOLLOWUPS_TRACKER.md` - Open items and priorities
- `docs/ARCHITECTURE.md` - System architecture overview
- `docs/STRATEGIES.md` - Trading strategy documentation
- `docs/RESEARCH_NOTES.md` - Research findings
- `docs/decisions/` - Architecture Decision Records (ADRs)
- `docs/directives/` - Development directives
- `docs/math_system/` - Technical math/indicator specs
- `docs/updates/` - Development notes, session handoffs
- `docs/data-sources/` - API and data source documentation

## Current Status (2026-08-19)
- ✅ **AI-only trade path live** (since 2026-08-17): Groq news analyst → 6-layer
  confirmation → circuit breaker → intent dedup → execution → SL/TP monitor
- ✅ Exness MT5 broker wired (`AI_MODE=live` = real orders, `test` = paper);
  server runs on **port 8501**
- ✅ Tiered circuit breaker, verification gates, AI feedback loop, tutor layer
- ✅ 15+ free external data sources (external_data.py aggregator)
- ✅ 87 coins configured; **2.3M+ candles** in DB; 42 macro instruments; 23+ news sources
- ✅ 15 test suites, all passing (system, strategy, ai_agents, arbitrator,
  verification, execution, mt5_broker, news_analyst, reviewer, tutor,
  web_scraper, alpha_news, tavily_news, proprietary, ai_upgrade, backtest)
- 📊 Win-rate / threshold calibration in progress via `ai_feedback.py`
- ⚠️ 2026-08-17→19 work is **uncommitted** in git

## Multi-Agent Coordination (IMPORTANT — read before editing)

Two AI agents work this repo. Rules to prevent loops and clobbering:
1. **File ownership**: The strategy/risk/backtest stack (`strategy.py`, `risk.py`, `backtester.py`, `portfolio.py`, `macro_data.py`, `fetch_all.py`, `server.py`, `signals.py`, `database.py`, `config.py`, `collector.py`, `math_engine.py`, plus the AI trade core `bot.py`, `news_analyst.py`, `ai_agents.py`, `ai_confirm.py`, `ai_feedback.py`, `calendar_guard.py`, `dxy_filter.py`, `smart_money.py`, `circuit_breaker.py`, `verification.py`, `arbitrator.py`, `execution.py`, `tutor.py`, `tests/`) is owned by the strategy agent. The dashboard UI (`web/dashboard/`), `docs/updates/`, and legacy pages are the UI agent's territory. Don't edit the other's files without user confirmation.
2. **mtime check before editing**: if a file changed since your last write, re-read and merge rather than overwrite.
3. **One network job at a time**: backfills and funding fetches write `crypto_data.db` — run sequentially, never concurrently.
4. **No edit ping-pong**: if the other agent changed something you own, flag it to the user instead of auto-reverting.
5. **No commits/pushes** without the user.
6. **Docs**: All documentation lives in `docs/` — strategy agent owns `docs/CHANGELOG.md`, `docs/decisions/`, `docs/directives/`; UI agent owns `docs/updates/`, `web/`.

---

*Last updated: 2026-08-19*