# SATS — CryptoAutomation

An AI/news-driven automated trading system. Since 2026-08-17 the bot trades
**100% on AI news signals** — the Groq news analyst scores the headline feed,
a 6-layer confirmation stack validates the signal, and an idempotent
execution layer places the trade.

- **Trades** the Exness forex / metals / energy / indices universe
  (`config.TRADING_UNIVERSE`) on **MT5 (Exness)** — crypto pairs and the
  Bitcoin ETFs were retired from the trade path
- **AI pipeline** — Groq news analyst (batched per-asset sentiment +
  confidence) + 3 production AI agents (catalyst sentiment, HODL research,
  chart confluence), with a local OmniRoute gateway as keyless fallback
- **6-layer confirmation** — sentiment → freshness → economic calendar guard
  (NFP/FOMC/CPI) → AI confirm (regime + 6-factor technicals + session) → DXY
  master filter → smart-money (ICT) confluence
- **Capital protection** — tiered circuit breaker (step-down on drawdown,
  step-up recovery), data-verification gates, intent-hash dedup, kill-switch
  and live-mode safety rails
- **Data platform** — Binance candles/funding for 87 crypto symbols, 42+
  macro instruments (Yahoo), plus free external sources: CoinGecko,
  CoinPaprika, DefiLlama, Etherscan, FRED, eCON, Frankfurter, Fear & Greed,
  Fed/Treasury, MarketAux, Mempool.space, NewsData.io, web scraping
  (Tavily/Firecrawl)
- **Dashboard** — FastAPI + ECharts dark terminal at `/` (signals, orders,
  live positions, AI layer status, circuit breaker, tutor)
- **Research stack** (retired from trading, still powers dashboard +
  backtests) — mean-reversion strategy v2, Bayesian math engine, 5 academic
  factors, backtester v2 (walk-forward, Monte Carlo, Wilson CI)

---

## Quick start

```bash
# 1. Install dependencies (Python 3.11+)
python -m venv .venv
.venv/Scripts/activate            # Windows  |  source .venv/bin/activate on macOS/Linux
pip install -r backend/requirements.txt

# 2. Configure keys
copy .env.example .env            # Windows  |  cp .env.example .env on macOS/Linux
#    set GROQ_API_KEY (the main AI engine) — everything else is optional

# 3. Fetch market data (candles + funding + macro)
python backend/fetch_all.py --fast

# 4. Start the dashboard server
cd backend && python server.py    # → http://localhost:8501

# 5. Run the bot (paper mode is the default — AI_MODE=test)
python backend/bot.py --dry-run   # journal intents, touch no broker
python backend/bot.py loop        # scheduled cycles every BOT_LOOP_SECONDS
```

The system runs without any keys (rule-based news scoring, paper broker).
Windows one-click launchers live in `scripts/` (`go.bat`, `start.bat`,
`test.bat`, `backtest.bat`); ops helpers include `check_bot_status.py`,
`check_orders.py`, `check_positions.py`, `check_sl_tp.py`.

## How a trade happens (one bot cycle)

```
data (SQLite candles + news feed)
  → verification.py        data-quality gate (staleness / spikes / sanity)
  → news_analyst.py        Groq LLM: per-asset sentiment, confidence, horizon
  → 6-layer confirmation
        1 raw sentiment    2 freshness           3 calendar_guard (NFP/FOMC)
        4 ai_confirm       regime + 6-factor technicals + session filter
        5 dxy_filter       USD-pair master filter (Dollar Smile / SMT divergence)
        6 smart_money      ICT order blocks / FVG / liquidity sweeps / BOS
  → ai_agents.gate_news_trade   (AI_CONFLUENCE_GATE: chart-structure veto)
  → circuit_breaker.py     tiered step-down on drawdown, recovery ladder
  → arbitrator.py          intent shape + intent_hash dedup (no duplicate orders)
  → verification.py        re-gate the intent against fresh data
  → execution.py           single idempotent order path:
        AI_MODE=test  → paper trades in the test_trades table
        AI_MODE=live  → real MT5/Exness orders (EXECUTION_BROKER=mt5|hybrid)
  → SL/TP monitor          closes open positions on hit
  → ai_feedback.py         records outcomes, suggests threshold calibration
```

Every gate decision is journaled (orders, test_trades, ai_analyses,
news_verdicts) and visible in the dashboard or via `/api/bot/status`.

## Project layout

```
backend/
  # AI trading core
  bot.py                Bot orchestrator: run_once / loop, dual-mode execution
  news_analyst.py       Groq news sentiment & analytics engine (main trade source)
  ai_agents.py          3 production agents: sentiment / HODL research / chart confluence
  ai_confirm.py         Technical confirmation layer (referee, not generator)
  ai_feedback.py        Adaptive calibration from trade outcomes
  dxy_filter.py         Dollar-index master filter for USD pairs
  smart_money.py        ICT confluence score (OB, FVG, sweeps, BOS/CHoCH)
  calendar_guard.py     Blocks trading around NFP/FOMC/CPI/GDP
  circuit_breaker.py    Tiered drawdown step-down + recovery ladder
  verification.py       Data-quality gates (hard reject, never a softener)
  arbitrator.py         Proposal → Intent unification + intent-hash dedup
  execution.py          ONLY module that touches a broker (paper/alpaca/binance/mt5/hybrid)
  test_ai.py            Paper-trade table + AI-mode helpers
  tutor.py              Educational layer: /api/tutor explainers
  # Legacy quant stack (dashboard + backtester only — retired from trading 2026-08-17)
  math_engine.py        Bayesian fusion, regime detection, Kelly sizing
  factors.py            5 academic factors with weights + hit rates
  strategy.py           Mean-reversion strategy v2 (z-score + trend filter)
  signals.py            Per-coin signal generation (display)
  risk.py               Position sizing, slippage tiers, Kelly
  backtester.py         Walk-forward / Monte Carlo / Wilson CI backtesting
  proprietary_signals.py  Retired second signal source (keep for tests/reference)
  reviewer.py           News verification (rule-based + free-LLM failover chain)
  # Data
  collector.py          Binance candles → SQLite (backfill + live loop + gap repair)
  fetch_all.py          One-shot fetcher: candles + funding + macro
  macro_data.py         42 macro instruments + Exness Yahoo candles
  news.py               RSS + CryptoPanic + Alpha Vantage + Tavily (23+ sources)
  web_scraper.py        Article extraction (Firecrawl, Tavily fallback)
  external_data.py      Aggregator over the free API sources below
  coingecko.py coinpaprika.py defillama.py etherscan.py fear_greed.py
  fred_data.py econdb.py fed_treasury.py frankfurter.py currency_api.py
  marketaux.py mempool.py newsdata.py market_data.py
  # Service
  server.py             FastAPI app — JSON API + dashboard + background threads
  portfolio.py          Demo/exchange/MT5 portfolio priced live
  database.py           SQLite (WAL) with lock-retry writes
  optimizer.py          Parameter optimization cache
  config.py             Symbols, universe, intervals, all env-driven settings
web/dashboard/          The dashboard (HTML/CSS/JS + ECharts), served at /
tests/                  Self-contained test suites + AI/MT5/execution tests
data/                   DB, logs, optimizer cache (gitignored)
docs/                   All documentation (ADRs, changelog, math specs, directives)
documentation/          Deep research (competitive intelligence, API integration plan)
scripts/                Bat launchers + ops/debug/check scripts
tradingview/            Pine indicator mirror of the signal logic
```

## Configuration

All configuration is env-driven — copy `.env.example` to `.env`. The file is
fully commented; the essentials:

| Key | Purpose |
|---|---|
| `GROQ_API_KEY`, `GROQ_MODEL` | Main AI engine (news analyst + agents) |
| `OMNIROUTE_*` | Local keyless AI gateway fallback (default localhost:20128) |
| `SIGNAL_SOURCE_A` | `ai_news` (default since 2026-08-17) or legacy engine for dry-tests |
| `AI_MODE` | `test` (paper, default) / `live` (real MT5 orders) |
| `EXECUTION_BROKER` | `paper` / `mt5` / `binance` / `alpaca` / `hybrid` |
| `EXNESS_MT5_ENABLED`, `MT5_TERMINAL_PATH` | MT5 broker wiring (Exness account) |
| `AI_CONFLUENCE_GATE` | Chart-structure veto on news signals (off by default) |
| `NEWS_SIGNAL_LONG/SHORT/MIN_CONFIDENCE` | News → trade thresholds |
| `BOT_LOOP_SECONDS`, `BOT_AUTO_LOOP` | Bot cadence + server-side auto-start |
| `MAX_DAILY_LOSS_PCT`, `MAX_DRAWDOWN_PCT` | Loss guard (defaults 3% / 10%) |
| `ALPACA_*`, `BINANCE_EXEC_*`, `CRYPTOPANIC_*`, `TAVILY_*`, `FIRECRAWL_*`, ... | Optional brokers and data sources |

The dashboard and research stack run with zero keys; live AI trading needs a
Groq key (or a running OmniRoute gateway) plus an Exness MT5 setup for
`AI_MODE=live`.

## API (FastAPI)

| Group | Endpoints |
|---|---|
| Market data | `/api/candles` `/api/watchlist` `/api/macro` `/api/fear-greed` |
| Signals | `/api/signals` `/api/review` `/api/trade/verify` `/api/ai-layers` |
| News & AI | `/api/news` `/api/news/stats` `/api/news/analytics` `/api/news/analyze` `/api/news/article` |
| Bot | `/api/bot/run` `/api/bot/loop` `/api/bot/status` `/api/bot/source` `/api/bot/reconcile` |
| Trades | `/api/orders` `/api/positions` `/api/trade-stats` `/api/test-trades` `/api/test-portfolio` |
| Risk & learning | `/api/circuit-breaker` `/api/feedback/calibration` `/api/feedback/thresholds` |
| Portfolio | `/api/portfolio` `/api/portfolio/holds` `/api/portfolio/analyze` `/api/connect-exchange` `/api/disconnect-exchange` |
| Research & education | `/api/backtest` `/api/tutor` `/api/tutor/trade` `/api/health` |

## Testing

```bash
python tests/test_system.py       # math engine, factors, indicators, signals
python tests/test_strategy.py     # strategy, risk, backtester (41 checks)
python tests/test_ai_agents.py    # production agents (mocked LLM)
python tests/test_arbitrator.py   # two-source gate + intent hash
python tests/test_execution.py    # execution layer (paper broker)
python tests/test_mt5_broker.py   # MT5 symbol mapping / sizing
python tests/test_verification.py # data-quality gates
```

Self-contained suites (synthetic data, monkeypatched LLM) run in CI on every
push; `tests/test_backtest.py` is a local smoke test needing a populated
`crypto_data.db`.

## Database backups

`crypto_data.db` (gitignored, 1.2M+ candles) can be snapshotted anytime:

```bash
python scripts/backup_db.py          # keeps newest 10 snapshots in backups/
```

---

## Status (2026-08-19)

- ✅ AI-only trade path live: news analyst → 6-layer confirm → execution
  (`AI_MODE=test` paper verified; `live` = real Exness MT5 orders)
- ✅ Exness MT5 broker wired (dual-broker hybrid mode supported)
- ✅ Tiered circuit breaker + verification gates + tutor layer
- ✅ 15+ free external data sources aggregated (`external_data.py`)
- ✅ Docs reorganized into `docs/` + `documentation/` (deep research)
- 📊 Win-rate and threshold calibration in progress via `ai_feedback.py`

## Documentation

- `docs/CHANGELOG.md` — chronological change log (all changes land here)
- `docs/ARCHITECTURE.md` — system design
- `docs/decisions/` — ADRs · `docs/directives/` — workstream directives
- `docs/math_system/` — full specs: indicators, risk, signal engine, news
  sentiment, execution, architecture, AI agents
- `docs/updates/` — UI/dev notes, session handoffs
- `documentation/` — competitive intelligence report, API integration plan
- `CLAUDE.md` — agent guidance (multi-agent coordination rules)
