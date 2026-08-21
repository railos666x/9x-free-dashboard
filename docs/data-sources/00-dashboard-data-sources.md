# Dashboard — Every Data Source & API Used

Complete inventory of everything the dashboard (`web/dashboard/` + `backend/server.py`) is fed by.
All sources are **free, no API key required** unless noted. Everything is stored locally in
SQLite (`crypto_data.db`) and the dashboard reads only from that DB via the FastAPI endpoints.

```
 Binance REST ──▶ backend/collector.py ──▶ SQLite (crypto_data.db)
 Yahoo Finance ─▶ backend/macro_data.py ──▶ macro table
 RSS + CryptoPanic ─▶ backend/news.py ──▶ news table
        │
        ▼
 backend/server.py (FastAPI) ──▶ dashboard at http://localhost:8501
```

---

## 1. Live market data — Binance (free, public, no key)

| Source | Endpoint | Used by | Purpose |
|---|---|---|---|
| Spot klines (candles) | `https://api.binance.com/api/v3/klines` | `collector.py` | OHLCV for **87 symbols × 6 intervals** (1m/5m/15m/1h/4h/1d) → `candles` table |
| Spot ticker price | `https://api.binance.com/api/v3/ticker/price` | `portfolio.py` | Live portfolio valuation (demo + exchange mode) |
| Spot account | `https://api.binance.com/api/v3/account` | `portfolio.py` | Signed balances — **only when the user connects API keys** |
| USD-M futures funding | `https://fapi.binance.com/fapi/v1/fundingRate` | `collector.py` | Perp funding history → `funding` table (carry factor, signals) |
| Futures premium index | `https://fapi.binance.com/fapi/v1/premiumIndex` | `factors.py` | Live funding rate for the carry factor (TTL-cached 5 min) |

**Refresh cadence** (`config.py` → `INTERVAL_REFRESH`): 1m:5s · 5m:15s · 15m:30s · 1h:60s ·
4h:300s · 1d:900s. The live loop polls each interval on its own cadence — cheap, far inside
Binance's public rate limits.

**History depth** (`config.py` → `INTERVAL_BACKFILL_DAYS`): 1m/5m 30 days, 15m 90 days,
1h/4h 365 days, 1d 730 days.

> **Latency note:** this is REST **polling** (min 5s), not a push feed. See
> `01-future-paid-sources-and-apis.md` for the WebSocket upgrade path.

## 2. Macro assets — Yahoo Finance (free, public, no key)

| Source | Endpoint | Used by | Purpose |
|---|---|---|---|
| Daily OHLCV | `https://query1.finance.yahoo.com/v8/finance/chart` | `macro_data.py` | **42 instruments** (gold, silver, oil, forex, indices, rates) → `macro` table → `/api/macro` |

Refreshed by a daemon thread in `server.py` (daily bars barely move — refreshed periodically).

## 3. News & sentiment

| Source | Endpoint / feed | Key needed? | Purpose |
|---|---|---|---|
| CryptoPanic API | `https://cryptopanic.com/api/v1/posts/` | **Optional** — `CRYPTOPANIC_API_KEY` | Tagged crypto news with votes. ⚠️ Free tier is being discontinued 2026-04-01 — see `01-future-paid-sources-and-apis.md` |
| RSS feeds (23 outlets) | `config.py` → `NEWS_FEEDS` | No | CoinDesk, Cointelegraph, The Block, Decrypt, Bitcoin Magazine, Reuters, CNBC, Kitco, FXStreet, Zero Hedge, etc. — defensive, a dead feed is skipped |
| Fear & Greed index | `https://api.alternative.me/fng/` | No | Market sentiment overlay (cached 5 min in `server.py`) |

News is fetched every `NEWS_REFRESH_SECONDS` (300s) and scored per coin by `reviewer.py`
(rule-based keywords, no key needed).

## 4. Optional AI news reviewer (LLM overlay)

| Source | Config | Purpose |
|---|---|---|
| Any **OpenAI-compatible** endpoint | `LLM_ENABLED` / `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` (`.env`) | LLM stance scoring on top of the rule-based reviewer. Works with OpenAI, DeepSeek, Qwen/DashScope, local llama.cpp… |

Fully optional — the reviewer runs rule-based without it.

## 5. Local storage — SQLite

`crypto_data.db` (WAL mode, lock-retry writes, `backend/database.py`). Tables:

| Table | Contents |
|---|---|
| `candles` | OHLCV per symbol × interval (1.6M+ rows) |
| `funding` | Perp funding-rate history per symbol |
| `macro` | 42 macro instruments, daily bars |
| `news` | Stories + sentiment score + linked coins |

## 6. What the dashboard itself consumes (FastAPI endpoints)

| Endpoint | Data behind it | Used in UI |
|---|---|---|
| `GET /` + `/dash-assets/*` | static dashboard files | whole UI |
| `/api/watchlist?interval=` | 87 symbols × price/change/RSI/volume/spark | tickers, Assets table, both asset pickers, signal-card distances |
| `/api/candles?symbol&interval&limit` | candles + indicators (SMA/EMA/RSI/MACD/ATR/BB) | Signals chart |
| `/api/signals` | math-engine signals (direction, entry/SL/TP, factors, regime, Kelly, EV) | Signals view cards + chart overlay |
| `/api/news` | news feed (filtered by sentiment/coin) | news (not in new dashboard UI yet) |
| `/api/portfolio` | demo or signed-Binance portfolio | Dashboard, My portfolio |
| `/api/macro` | 42 macro instruments | macro (not in new dashboard UI yet) |
| `/api/backtest` | backtester v2 metrics + trade list | Bots view |
| `/api/fear-greed` | cached Fear & Greed value | (available) |
| `/api/health` | DB row counts, liveness | health checks |
| `/api/connect-exchange` (POST) | stores Binance keys locally | Connect-exchange modal |

Frontend also loads **ECharts 5** from `cdn.jsdelivr.net` (the only third-party JS dependency).
