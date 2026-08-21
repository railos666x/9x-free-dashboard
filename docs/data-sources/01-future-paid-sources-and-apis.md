# Future Paid Sources & APIs — Faster, More Accurate Dashboard

Research notes (verified mid-2026 — **prices change, re-check before buying**). Everything here
is a *candidate*; nothing is integrated yet.

## Why upgrade — current gaps

| Gap today | Consequence | Fix |
|---|---|---|
| REST polling, min 5s refresh | prices/charts up to ~1 min stale | WebSocket push feeds (free first) |
| Aggregated 1m+ OHLCV only | no tick/order-book/microstructure signals | tick + L2/L3 providers |
| No liquidation / OI / funding streams | no derivatives context | Binance WS + derivatives APIs |
| RSS polling every 5 min + CryptoPanic **free tier dying 2026-04-01** | news lags, sentiment thin | paid news/sentiment APIs |
| Rule-based news sentiment | accuracy limited | LLM overlay (already supported) + paid sentiment feeds |
| No on-chain / social signals | regime calls miss flows | Glassnode / Santiment / LunarCrush |

---

## A. Real-time market data (speed)

### 1. Binance WebSocket — FREE, do this first
`wss://stream.binance.com:9443` — kline streams, `bookTicker`, partial depth, trades.
Sub-second updates, no rate-limit pressure, zero cost. The single biggest speed win.
(`CLAUDE.md` already flags "WebSocket ready" as the target.)

### 2. CoinAPI — the drop-in commercial upgrade
- REST + **WebSocket + FIX**, unified schema across **370+ exchanges**, spot + derivatives,
  full historical archives, bulk flat files (CSV/Parquet/S3) for backtesting.
- Pricing: paid plans ~**$79 → $599/mo** (Pro = 100k requests/day); free tier with ~$25 credits.
- Best fit: multi-exchange prices, derivatives, or when Binance REST rate limits bite.

### 3. Kaiko — institutional analytics
- L1/L2 trade + order-book data, derivatives, reference rates & indices, deep history.
- Pricing: custom/enterprise (quote required). Overkill unless you need audit-grade data.

### 4. Amberdata — market + on-chain + DeFi in one
- CEX market data + deep blockchain/DeFi analytics, bulk S3 delivery. Enterprise pricing.

### 5. CoinStats API — broad and cheap
- Market data across 200+ exchanges, wallet balances, DeFi, **news aggregation**, MCP server
  for AI agents; free tier + credit-based paid plans. Good value for a retail dashboard.

### 6. Tardis.dev — backtest-grade tick history (not live)
- Raw, exchange-native tick + L2/L3 order-book history with market replay; funding/OI/
  liquidations history. Full access ~**$2,200/mo** (smaller plans exist). Ideal one-time
  research buy for realistic backtests, not for live screens.

### 7. Others worth knowing
- **CoinGecko API** — retail aggregate, paid from ~$35/mo (free demo 10k calls/mo).
- **CoinMarketCap API** — from ~$29/mo (free Basic tier).
- **CryptoAPIs** — blockchain-focused, from ~$49/mo.
- Stock/macro supplements: **Polygon.io, Alpaca, Finnhub, Tiingo, EODHD** (all have free tiers).

---

## B. On-chain & analytics (accuracy — "is this real?")

| Provider | What you get | Rough pricing (mid-2026) |
|---|---|---|
| **Glassnode** | on-chain metrics (exchange flows, SOPR, MVRV, realized cap) | Free / Advanced ~$39/mo / Pro ~$799/mo |
| **Santiment** | on-chain + social + development activity, one API | similar tiers |
| **Coin Metrics** | institutional on-chain + market reference data | enterprise |
| **Nansen / Dune** | wallet intelligence / SQL analytics | subscription |

These would power the regime/value factors with real flows instead of price-only math.

---

## C. News & sentiment (accuracy)

| Provider | What you get | Rough pricing (mid-2026) |
|---|---|---|
| **CryptoPanic (paid)** | tagged news with community votes — replaces the discontinued free tier | paid plans from ~**$899/yr** |
| **LunarCrush** | real-time social sentiment for 4,000+ coins, stocks, topics; alerts | ~$249/mo tier (cheaper tiers exist) |
| **The TIE** | institutional crypto news sentiment + social aggregation | enterprise |
| **Santiment** (above) | social + on-chain + news in one API | tiers |
| **CoinDesk Indices / CoinStats News API** | curated news feeds | per API |

Free stop-gaps: add more RSS feeds, Reddit JSON, official project blogs — all already fit the
existing `news.py` pipeline.

---

## D. Recommended priority (cost → benefit)

1. **Binance WebSocket (free)** — sub-second prices/charts, biggest speed win, hours of work.
2. **Keep Binance REST** as the backfill/history backbone (free, already tuned).
3. **Tardis.dev** — buy once for realistic backtesting (tick/L2 data, market replay) when the
   strategy workstream wants it.
4. **CryptoPanic paid or LunarCrush** — only if news sentiment drives decisions (the free
   CryptoPanic tier dies 2026-04-01; plan before then).
5. **CoinAPI** — only when multi-exchange / derivatives / FIX actually matter.
6. **Glassnode ($39 tier)** — on-chain regime confirmation once factors show it helps.

## E. Cost summary (per month, approx)

| Provider | Approx cost | What it buys |
|---|---|---|
| Binance WebSocket | $0 | real-time klines/bookTicker |
| CoinAPI Pro | $599 | 370+ exchanges, WS/FIX, derivatives |
| CoinGecko Basic | $35 | retail aggregate, 100k calls |
| CoinMarketCap Builder | $29 | retail aggregate |
| CryptoPanic paid | ~$75/mo equiv. | tagged news feed (replaces free tier) |
| LunarCrush | ~$249 | social sentiment |
| Glassnode Advanced | $39 | on-chain metrics |
| Tardis.dev | ~$2,200 (or smaller plans) | full tick history for backtests |

*All figures from public pricing pages, mid-2026; verify before purchase.*
