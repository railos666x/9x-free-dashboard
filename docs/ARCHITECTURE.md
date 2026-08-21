# ARCHITECTURE.md

System design of SATS / CryptoAutomation.

*Last updated: 2026-08-19*

---

## Overview

An AI/news-driven automated trading system. Since **2026-08-17** the bot
trades 100% on AI news signals — the decision came after the local quant
engines (mean reversion, z-score, RSI2, Bayesian fusion) produced losses.
They were **retired from the trade path** and remain only for dashboard
display (`/api/signals`) and the backtester.

Three layers:

1. **Data layer** — Binance REST (87 symbols, candles + funding), Yahoo
   macro (42+ instruments), and ~15 free external source modules, all into
   SQLite (WAL mode) or live on demand.
2. **Intelligence / trade layer** — the AI news pipeline (below); the legacy
   quant stack is a passive consumer of the same data.
3. **Presentation / ops layer** — FastAPI server (`server.py`, port 8501)
   with a 36-endpoint JSON API + ECharts dashboard at `/`, plus ops/debug
   scripts under `scripts/`.

## The AI trade pipeline (the only trade path)

```
        news feed (RSS / CryptoPanic / Alpha Vantage / Tavily)
        Binance + Yahoo + free APIs  ──►  SQLite (candles, news, macro)
                                          │
        ┌────────────────────────────────┴─────────────────────────┐
        ▼                                                          ▼
  bot.py — one cycle                                  server.py — /api/*
  ┌────────────────────────────────────────┐
  │ 1. verification.py    hard data gate    │  staleness, OHLC sanity,
  │                         (reject+HOLD)   │  spikes, cross-source price
  │ 2. news_analyst.py    Groq LLM:         │  per-asset sentiment -1..1,
  │                         conf 0..1,       │  horizon, rationale; 10-min
  │                         Groq→OmniRoute   │  cache; rule-based
  │                         →rule-based fill │  backfill is display-only
  │ 3. Six confirmation layers              │
  │      a. raw sentiment threshold         │  AI_SENTIMENT_LONG/SHORT
  │      b. news freshness                  │  NEWS_MIN_FRESH_HEADLINES
  │      c. calendar_guard.py               │  NFP/FOMC/CPI/GDP windows
  │      d. ai_confirm.py                   │  regime + 6-factor + session
  │      e. dxy_filter.py                   │  USD-pair master filter
  │      f. smart_money.py                  │  ICT OB/FVG/sweeps/BOS 0-4
  │   (ai_agents.gate_news_trade when       │  AI_CONFLUENCE_GATE=true:
  │    AI_CONFLUENCE_GATE — chart veto)     │  agent 3 verifies structure
  │ 4. circuit_breaker.py  tiered step-down │  5/10/15% DD → micro → halt;
  │                         recovery ladder │  3-loss breaker, decay flag
  │ 5. arbitrator.py         Proposal→Intent│  intent_hash = sha256 dedup
  │ 6. verification.py        re-gate       │  fresh data before order
  │ 7. execution.py          idempotent     │  AI_MODE=test → test_trades
  │    (ONLY broker module)               │  AI_MODE=live → MT5/Exness
  │ 8. SL/TP monitor         manage_open    │
  │ 9. ai_feedback.py        record outcome │  calibration suggestions
  └────────────────────────────────────────┘
```

Invariants:

- **Never raises**: every AI layer, external source, and gate module
  degrades to a safe default (HOLD / permissive / neutral score) on any
  failure, with structured logging.
- **Hard gates, not softeners**: verification rejects and logs; it never
  warns-and-trades.
- **Idempotent orders**: `orders.intent_hash` UNIQUE — the same agreed
  signal can never place a second order.
- **Live-mode safety rails**: `BOT_AUTO_LOOP_LIVE` refuses real-money
  brokers and falls back to dry-run; legacy live engines require
  two-source agreement (`ARBITRATION_MIN_SOURCES=2`); the `ai_news` path
  is single-source *by design* and exempt.

## Trade universe & execution

- `config.TRADING_UNIVERSE` = the Exness forex / metals / energy / indices
  instruments (`EXNESS_SYMBOLS` filtered for tradability). Crypto pairs and
  the Bitcoin ETFs were **retired from trading** on 2026-08-17.
- `EXECUTION_BROKER`: `paper` (offline fills from our candles, zero keys) ·
  `mt5` (MetaTrader5 → Exness, `EXNESS_MT5_SYMBOLS` mapping e.g.
  XAU/USD→XAUUSD) · `binance` (signed spot) · `alpaca` (REST) ·
  `hybrid` (routes per asset; MT5 for the Exness universe, Binance for the
  rest).
- MT5 specifics: connection re-open before `order_send` (IPC bug, 2026-08-18),
  min-lot flooring, lot-step precision, filling type chosen from the symbol's
  `filling_mode` bitmask (FOK if advertised, else IOC), `MT5_POSITION_PCT`
  sizing.

## Module map

| Module | Responsibility |
|---|---|
| `bot.py` | Orchestrator: `run_once` / `loop` / `--dry-run`; wires the pipeline |
| `news_analyst.py` | Groq news analytics (main trade source); provider chain Groq → OmniRoute → rule-based |
| `ai_agents.py` | 3 agents: catalyst sentiment, HODL research, chart confluence; journal to `ai_analyses` |
| `ai_confirm.py` | Regime + 6-factor technical + session confirmation (referee only) |
| `ai_feedback.py` | Outcome recording, per-layer win analysis, threshold suggestions |
| `calendar_guard.py` | High-impact event windows + spread safety check |
| `dxy_filter.py` | Dollar Smile / SMT divergence for USD pairs (cross pairs exempt) |
| `smart_money.py` | ICT confluence score 0–4 (OB, FVG, sweeps, BOS/CHoCH) via `smartmoneyconcepts` |
| `circuit_breaker.py` | Tiered DD step-down, recovery ladder, loss/decay breakers |
| `verification.py` | Staleness / OHLC sanity / spike / cross-source / intent gates |
| `arbitrator.py` | Proposal → Intent; intent-hash dedup |
| `execution.py` | **Only** broker module: paper/alpaca/binance/mt5/hybrid; ATR SL/TP defaults; loss guard |
| `tutor.py` | Educational explainers + "why this trade" (journal-driven) |
| `test_ai.py` | `test_trades` paper table + AI-mode helpers |
| `macro_data.py` | 42 Yahoo macro instruments + Exness candle backfill/refresh |
| `external_data.py` | Aggregator: status + payload for every enabled free source |
| Free source modules | `coingecko` `coinpaprika` `defillama` `etherscan` `fear_greed` `fred_data` `econdb` `fed_treasury` `frankfurter` `currency_api` `marketaux` `mempool` `newsdata` `market_data` `web_scraper` — each fail-safe, env-toggleable |
| `news.py` | RSS + CryptoPanic + Alpha Vantage + Tavily (23+ sources) |
| `server.py` | FastAPI: 36 endpoints, dashboard, bot control, background threads (port 8501) |
| `portfolio.py` | Demo/exchange/MT5 portfolio priced live |
| `collector.py` / `fetch_all.py` | Binance backfill/live loop/gap repair; one-shot fetcher |
| `database.py` | SQLite WAL + lock-retry writes; tables incl. orders, test_trades, ai_analyses, news_verdicts, article_content |
| Legacy (display/backtest only) | `math_engine` `factors` `indicators` `signals` `strategy` `risk` `backtester` `reviewer` `proprietary_signals` (retired) `optimizer` |

## Legacy quant stack (retired from trading)

Kept intact for `/api/signals` display and research:

- `strategy.py` — mean-reversion v2 (z-score, OU half-life, ADX/vol/liquidity
  filters, trend filter); 4 knobs (anti-overfitting stance)
- `math_engine.py` — Bayesian fusion, ADX regime, Kelly, EV filter
- `factors.py` — Trend 30% / Momentum 25% / Carry 20% / Reversal 10% / Value 15%
- `backtester.py` — walk-forward, Monte Carlo, Wilson CI
- `proprietary_signals.py` — former second gate source; kept for tests/
  reference only

Re-enabling a legacy engine for **live** trading is deliberately hard:
requires two-source agreement and the `ALLOW_LIVE_SINGLE_SOURCE` kill-switch
stays off by default.

## Key design decisions

- **SQLite + WAL** — zero-ops local storage; single writer at a time
  (backfills and funding fetches run sequentially, never concurrently).
- **Public REST polling** over WebSocket — reliability first; WS is roadmap.
- **AI as the only generator, math as referee** — technicals confirm, they
  never generate (the 2026-08-17 decision).
- **Journal everything** — orders, test_trades, ai_analyses, news_verdicts;
  the dashboard, tutor, and feedback loop all read the journals.
- **Permissive defaults outside the trade path** — a failed external source
  or LLM call degrades the dashboard, never blocks the server.
- **Two-agent repo ownership** — see CLAUDE.md.

## Known limitations (tracked)

- Spot data only; slippage modeled, not measured (no order-book history).
- External free sources are rate-limited (throttles + caches baked in).
- `ai_feedback` suggests calibration but does not silently auto-apply.
- Win-rate calibration against the 6-layer stack is in progress.
