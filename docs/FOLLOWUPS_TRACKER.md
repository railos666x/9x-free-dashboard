# Followups & Session Tracker

*Created: 2026-08-15 · Branch: `feature/ai-execution-news-gate` · All work committed
and pushed to GitHub (`origin/feature/ai-execution-news-gate`).*

This document is the single source of truth for what each session completed and
what is still open, so any future session (human or AI) can pick up exactly
where we left off.

---

## ✅ DONE — Trading switched to AI + news on the Exness forex universe (2026-08-17)

Per user request: "don't use old sources… totally trade with AI and APIs and news, do the forex trades, crypto was loss-making anyway." The old candle-factor engines are REMOVED from the trade path (documented, not deleted — they still power the dashboard /api/signals display + backtester).

| Item | What was delivered |
|---|---|
| **One AI/news source** | `bot._ai_news_proposals` — every proposal now comes from `news_analyst.signal_for` (Groq analyst over Tavily / Alpha Vantage / RSS headlines; LONG/SHORT above the `NEWS_SIGNAL_*` thresholds). The two-source agreement gate is gone from the trade path; the analyst's own confidence floor applies instead. |
| **AI verification** | `AI_CONFLUENCE_GATE=true` in `.env` — agent 3 (`ai_agents.gate_news_trade`) verifies each news LONG/SHORT against live support/resistance before it becomes a proposal; its `calculated_stop_loss` is used when provided. Falls open if the LLM is down. |
| **Old sources retired** | `math_engine`, `strategy v2`, `hybrid` (`config.SIGNAL_SOURCE_A` default now `ai_news`), the proprietary RSI(2) source (`PROPRIETARY_SIGNALS_ENABLED=false`), and the `reviewer.verify_trade` news gate (`NEWS_VERIFY_ENABLED=false` — redundant now the signal IS news). Kill-switch re-scoped: only blocks LIVE runs for a LEGACY engine in single-source test mode. |
| **Forex-only universe** | New `config.TRADING_UNIVERSE` = Exness forex/metals/energy/indices (BTC/ETH CFDs excluded — still crypto exposure). `bot.run_once` iterates it; crypto pairs + Bitcoin ETFs no longer generate orders (data collection continues for dashboard/backtest). |
| **Macro news coverage** | Tavily query broadened to macro headlines (forex/dollar/gold/oil/indices + crypto); Groq analysis prompt + `_resolve_symbol` asset map now cover the Exness instruments (EURUSD, XAU, WTI, US500, DXY, DAX, NIKKEI…). |
| **Dashboard + tutor** | Source selector is `ai_news` only (legacy engines refused with a clear message); stats row labels updated; tutor explains the new `no_signal_ai` held reason. |
| **Tests** | All 15 test files pass after the switch (old modules kept + config-gated, so their tests still run). |
| **Verified live** | Fresh macro Tavily fetch → analyst scored EUR/USD +70, GBP/USD +60, USD/JPY −70, AUD/USD −50 (all above NEWS_SIGNAL thresholds) and a real dry cycle fired **DXY LONG qty 50** with agent-3 chart confirmation. Data-layer fixes that made it work: news timestamp normalization (1272 rows backfilled), working Groq models (gpt-oss-120b/20b — the old qwen/llama chain 429/404'd and stalled 2.5 min), traded-instrument feed prioritization, rule-based backfill now display-only, and an inverse-pair direction-convention guard in the analyst prompt. |

---

## ✅ DONE — Exness demo via MT5: old Alpaca account retired (2026-08-17)

User created an Exness demo account; the system now trades it through MetaTrader 5.

| Item | What was delivered |
|---|---|
| **Old account retired** | All Alpaca paper positions closed at market (`account_retired` reason): ADA −234/−277, DOGE −89/−137, AAVE −261, YFI −21. Alpaca account flat (≈$6 BAT dust left). Alpaca keys still in `.env` (unused). |
| **MT5Broker** | `execution.MT5Broker` — the Exness path (Exness has no public REST API; the `MetaTrader5` Python package talks to the LOCAL terminal). Market IOC DEALs, lot sizing via `trade_contract_size`/`volume_step` (floor at `volume_min`), `broker_ref` = deal id, graceful BrokerError when terminal down / package missing. `symbol_of()` per-broker + reconcile position pass extended to mt5 (exits journaled at last close when the position vanishes). |
| **Symbol map** | `config.EXNESS_MT5_SYMBOLS` (18): forex 8 + XAUUSD/XAGUSD/USOIL + US500/USTEC/US30/DE30/JP225 + BTCUSD/ETHUSD. **Verified live on `Exness-MT5Trial16`**: USTEC (not US100) and DE30 (not GER40) are this server's names. |
| **Universe + data** | `config.EXNESS_SYMBOLS` added to `TRADABLE_UNIVERSE` (103 total). 1h candles via `macro_data.backfill_exness_candles` (Yahoo, ~2,300-2,800 bars/symbol; indices ~840). Wired into `fetch_all.py` [5/5] and `python macro_data.py exness`. |
| **Sizing for CFDs** | `_sized_qty` mt5 branch: MT5_POSITION_PCT (50%) cap replaces the 15% notional cap (which would zero-size a min-lot gold position), min-lot floor, margin rules as backstop. `_broker_equity` = min(equity, free margin) for mt5. EUR/USD dry-run: 0.04 lots ≈ $4.4k. |
| **Loss guard fix** | `account_retired` exits are now EXCLUDED from `loss_guard_status` (broker-swap bookkeeping ≠ trading results) — without it the retirement losses froze the fresh account (daily 9.65%, drawdown 10%). Regression test added (test_execution §8b). Today's broker_closed rows relabeled `account_retired`. Guard now: daily 2.85% / drawdown 3.28% / equity 9672, unblocked. |
| **⚠ Server was killed** | The running `server.py --reload` had a STALE shell env (`EXECUTION_BROKER=alpaca` exported, overriding `.env`) and its auto-loop kept live-trading the flattened Alpaca account + reconcile fighting the cleanup. Whole process tree stopped. **The user must restart the server with the right env** (see below). |
| **Tests** | New `test_mt5_broker.py` (9 offline blocks: mapping, lots, orders, rejection, positions, sizing, universe, graceful-no-package, dry-run). 14/14 test files pass. |
| **Old trades hidden from dashboard** | All journal views (`/api/orders`, `/api/positions`, bot-status open count, signal-card "last trade") default to **source = current broker** — the retired Alpaca/paper rows stay in the DB (loss guard still uses them as journal history) but never resurface on the dashboard. `source=all` reveals everything. The bot's `MAX_OPEN_POSITIONS` counter also counts only the current broker's open rows. Fresh mt5 account now shows a clean slate. |

## ✅ DONE — Dashboard up on Exness, waiting for the Start button (2026-08-17)

- **MT5 portfolio view**: `portfolio._mt5_account()` added — `/api/portfolio` now reports `mode=mt5`, real balance/equity/free-margin from the terminal (verified: **$10,000 / $10,000 / $10,000**, account flat). Previously it fell back to demo holdings, so the dashboard would have shown fake data next to the real account.
- **Loss guard now broker-scoped**: `loss_guard_status` counts ONLY `source = config.EXECUTION_BROKER` closed rows (same scope as the dashboard). The old Alpaca closes no longer drag the fresh account (was: 2.85% daily / 3.28% DD; now: **0% / 0% / $10k**). A retired account's bookkeeping can never freeze the new one.
- **Auto-loop off at boot**: `.env` now `BOT_AUTO_LOOP=false` (was true) — the server starts WITHOUT trading; the dashboard **Start button** begins the (live) loop on click. `BOT_AUTO_LOOP_LIVE=true` so the button runs live when pressed.
- **Server restarted** on :8501 with `EXECUTION_BROKER=mt5 BOT_AUTO_LOOP=false` exported (shell env still has stale `alpaca` exports — a plain `python server.py` would revert to Alpaca!). Verified: broker=mt5, auto_loop=false, auto_loop_running=false, open_orders=0, portfolio mode=mt5, orders empty.
- Tests: test_execution (guard scoping) + test_mt5_broker + test_system + test_strategy all pass.

---

### ⚠️ User action needed: restart with the right environment
- The shell/terminal exports `EXECUTION_BROKER=alpaca MAX_DRAWDOWN_PCT=10.0 MAX_DAILY_LOSS_PCT=3.0` — non-empty env vars BEAT `.env` (by design). Start the server with the new broker:
  ```bash
  cd cryptoautomation/backend
  unset EXECUTION_BROKER MAX_DRAWDOWN_PCT MAX_DAILY_LOSS_PCT   # or: export EXECUTION_BROKER=mt5
  python server.py --reload
  ```
- MT5 terminal must be RUNNING + logged into the Exness demo (account 472561011, Exness-MT5Trial16, $10k) whenever the bot trades — the package connects to the local terminal.
- Start the bot loop from the dashboard (Auto loop) when ready; first cycles will signal on the 18 Exness symbols.

---

## ✅ DONE — trade check + sizing fix + ETF universe (2026-08-17)

Followed up the trade-check findings: fixed the over-commit sizing bug and added
a new tradeable universe (US Bitcoin ETFs) so the AI has setups that aren't
crypto — **the ETF backtest is dramatically better than crypto** (see below).

### Trade check (live journal)
- Closed trades: 6, WR **16.7%**, realized **−$327.90** (tiny sample; 5/6 closes
  were the Aug 15 broker-side cleanup, only 1 organic exit: ADA SL −$283.73).
- Open: ADA, DOGE + AAVE (filled 08:05) — unrealized ≈ −$18.
- **The live trades are NOT the AI source** — every recent fill's reason is
  `RSI(2)=… → oversold, mean-reversion LONG` (math-engine source A). The AI news
  layer (source B) has produced ZERO live fills: NEWS_SIGNAL thresholds (±0.35
  sentiment, conf 0.6) are strict and crypto news is mostly neutral.

### Bug found & fixed: burst over-commit (7× 403 insufficient balance @ 08:05)
- Root cause: `_sized_qty` sized on **account equity** ($98.8k) — a burst of 10
  concurrent RSI(2) longs at up to 15% each over-committed the $13.9k available
  cash. AAVE alone was sized to a $14.9k notional.
- Fix: `_broker_equity` now returns **min(equity, cash × 0.8)** — the binding
  constraint. AAVE-sized positions are now ~$2.2k. `MAX_OPEN_POSITIONS` still
  caps concurrent slots; residual high-confidence-burst overshoot is bounded
  and simply broker-rejected (harmless journal error) rather than the whole
  cycle failing.

### NEW: US Bitcoin ETF universe (the AI's tradeable alternative to crypto)
- `config.ETF_SYMBOLS` = **IBIT, BITB, FBTC, BTCW, BTCO, HODL, EZBC, ARKB**
  (ARKB, NOT the watchlist's "ARKX" — ARKX is ARK's space ETF; ARKB is the
  ARK 21Shares Bitcoin ETF). `config.TRADABLE_UNIVERSE = SYMBOLS + ETF_SYMBOLS`
  (85 symbols).
- Data: `macro_data.backfill_etf_candles()` — Yahoo **1h** candles (60m
  interval, ~2,780 bars ≈ 400 trading days) stored in the `candles` table so the
  signal engine / bot / backtester treat them exactly like Binance pairs.
  Wired into `fetch_all.py` step [4/4] and `python macro_data.py etf`.
- Execution: Alpaca trades these like stocks (no symbol conversion);
  `broker_supported_symbols` adds them on the success path (crypto
  `asset_class` query can't see equity assets). Paper broker trades everything.
- Signals: `signals.generate_all_signals`, `proprietary_signals` (rsi2 mode) and
  `bot.run_once` now iterate `TRADABLE_UNIVERSE`; funding prefetch caches None
  for ETFs (carry neutral). News/coin-map layers stay crypto-only (no ETF news
  sentiment exists).
- **Data gate fix**: non-pair symbols (no "/") get a weekend-tolerant
  staleness window (`verification.ETF_STALE_MULT = 84h` vs crypto 2.5h) — the
  newest ETF 1h bar is Friday's close on Monday pre-market, which the crypto
  window rejected. `verify_candle_df(..., symbol=)`.
- New macro symbols for AI context: **NIFTY/USD (^NSEI), BANKNIFTY/USD
  (^NSEBANK), SENSEX/USD (^BSESN)** (indices) + **BTC1/USD (BTC=F)** (new
  `btc_futures` category) — data-only, not broker-executable.
- **Backtest validation (1h, current strategy v2):**
  | Symbol | Trades | WR | PF | MaxDD |
  |---|---|---|---|---|
  | IBIT | 6 | 66.7% | 3.08 | 1.2% |
  | BITB | 7 | 57.1% | 2.12 | 2.4% |
  | FBTC | 7 | 57.1% | 2.07 | 2.4% |
  | ARKB | 7 | 57.1% | 2.13 | 2.4% |
  | BTCW | 10 | 40.0% | 1.10 | 2.9% |
  vs crypto: BTC WR 44% PF 0.45, SOL 40% PF 0.66, DOGE 50% PF 0.19 — the ETF
  universe gives the AI 2-3× the profit factor. This is the winrate lever.
- Tests: `test_execution` §7 extended (Alpaca success path includes ETFs; paper
  universe = TRADABLE_UNIVERSE). 12/12 test files pass.

### ⏳ Still pending (followups not done this session)
- **Parameter optimizer** (lift WR toward 60-70%): run `python backend/optimizer.py`
  — now more interesting with the ETF universe (PF 2-3 already).
- Consider `MAX_DRAWDOWN_PCT` tightening (equity 9,672 vs 10k peak) once the
  journal grows.

---

## ✅ DONE — stale-position cleanup + broker_ref fix (2026-08-17)

Resumed the tracker's open cleanup item (4 stale paper positions eating the
`MAX_OPEN_POSITIONS=5` cap). Journal verified against the LIVE Alpaca order/
position list, then repaired.

| Item | What was delivered |
|---|---|
| **Phantom rows removed** | **1INCH (129), ALICE (130), CELO (131)** — pre-Alpaca paper fills with NO broker order anywhere → marked `cancelled` with `exit_reason="cleanup: stale paper position (pre-Alpaca switch, no broker order)"`. `cancelled` (not `closed`) so the loss-guard equity curve stays untouched. Open count 5 → **2**. |
| **DOGE/ADA are REAL — kept** | Contrary to the earlier note, **DOGE (160) and ADA (165) were actually placed through Alpaca** (journal fill_times match broker orders `fc73d6e6…` and `dfd3644e…` exactly; live positions ADAUSD 167,334 / DOGEUSD 426,962 = journal qty + 2 foreign buys). `broker_ref` backfilled on both so reconciliation matches them. |
| **Root bug fixed** | `execution.execute_intent` dropped `fill.get("broker_ref")` when updating the order → **every** real order journaled with `broker_ref=NULL`, so reconcile could never match our own fills (and the foreign-fill detector flagged our own orders). Now persisted. Regression test added (`test_execution` §1 asserts the paper fill's `paper-…` ref reaches the DB row). |
| **Reconcile brought journal in line** | Position pass journaled 5 legitimately broker-side-closed positions (LINK, BTC, SHIB, GRT, BCH — all sold at Alpaca Aug 15, journal rows were stale-open). Equity math verifies: 10000 − 283.7 − 5.2 − 11.1 − 8.9 + 25.5 − 44.4 = **9672.10**. |
| **New setup checked (live)** | Loss guard: **blocked=False, daily 2.85% (< 3%), drawdown 3.28% (< 10%), equity 9672.10** — armed, just under the caps. Open positions now exactly match Alpaca reality (2 journal rows ↔ DOGEUSD + ADAUSD). 13/13 test files pass. |

Left as-is (out of scope): Alpaca still holds **SHIBUSD 0.157 dust** and 2 foreign
buys (ADA 83,148 from Aug 15 09:52, DOGE 212,946) with no journal rows — the
foreign-fill detector flags them for the dashboard; nothing to clean journal-side.

---

## ✅ DONE — Tavily + OmniRoute wired, loss guard added (2026-08-17)

Everything below verified live against the running system.

| Item | What was delivered |
|---|---|
| **Tavily news search** | `news.fetch_tavily_news(conn)` — one `topic=news` search → **FRESH** stories with real published dates (Alpha Vantage's free feed lags by months; verified: 20 stories dated Aug 16–17, 2026). Sentiment from the keyword scorer, symbols cross-referenced via `reviewer.COIN_HINTS` (whole-word tickers — `software` never matches SOL). Throttled to 1 call per `TAVILY_NEWS_FETCH_HOURS` (6h → ≤4 calls/day ≈ 120/mo on the ~1000-credit free tier). Wired into `fetch_all()`. |
| **Tavily extract fallback** | `web_scraper.scrape()` now tries Firecrawl first, then Tavily's `extract` endpoint when Firecrawl is off / unkeyed / 402 / failed (one credit per URL, on demand only). Same `article_content` cache. |
| **OmniRoute LLM gateway** | `OMNIROUTE_BASE_URL` (default `http://localhost:20128/v1`), `OMNIROUTE_MODEL` (`auto` combo), optional key. `news_analyst._providers()` = Groq (when keyed) then OmniRoute; `reviewer._llm_providers()` gains it as a keyless local provider; AI agents ride the same transport. **Verified live**: gateway reachable, `auto/best-*` combos listed, `chat_json` → OmniRoute answers `{"pong": 1}`, agent 1 via OmniRoute scored BlackRock ETH-ETF **BULLISH 0.85**, `analyze_market` via OmniRoute returned BTC +65 conf 0.85 / 35 coins. |
| **SSE fix discovered** | OmniRoute streams SSE by default → plain-JSON parsing broke. `_chat_once` now sends `"stream": false` (harmless for Groq). |
| **`llm_available()` gate** | The analyst/agents previously hard-gated on `GROQ_API_KEY`, silently dead with Groq unkeyed even though OmniRoute was configured. New `config.llm_available()` (Groq key OR OmniRoute enabled) is the single gate in `analytics_for`, `_agent_call`, `agent_chart_confluence`, `gate_news_trade`, `_news_proposals`, `review_market`. |
| **Loss guard (circuit breaker)** | `execution.loss_guard_status(conn)` — two hard caps checked at the top of every bot cycle BEFORE new entries: `MAX_DAILY_LOSS_PCT` (3% default, realized PnL today vs start-of-day equity) and `MAX_DRAWDOWN_PCT` (10% default, equity vs journal peak). Tripped → new entries held (`loss_guard` reason, visible in cycle summary + dashboard held panel); the SL/TP monitor still closes open positions (closing ≠ new exposure). 0 disables. **Live check**: current journal daily loss 2.85% / drawdown 3.28% — just under the caps, guard armed. |
| **Backtest loss profile** | 1h×1000 bars, mean-reversion v2: BTC 9 trades WR 44% maxDD 6.29%, ETH 5 trades WR 60% maxDD 2.36%, SOL 5 trades WR 40% maxDD 3.81%, DOGE 2 trades WR 50% maxDD 1.25% — losses bounded by the 1% risk + ATR stops, worst avg loss ~$140 on a $10k book. |
| **Config + env** | `.env` gained `TAVILY_API_KEY` (user's `tvly-dev-…` key), `TAVILY_NEWS_*`, `OMNIROUTE_*`, `MAX_DAILY_LOSS_PCT`/`MAX_DRAWDOWN_PCT`. `.env.example` documents all of it. |
| **Tests** | New `test_tavily_news.py` (13 checks, offline); `test_web_scraper.py` +6 (Tavily fallback, 402→fallback, disabled); `test_news_analyst.py` +10 (provider chain, keyless routing, SSE payload); `test_execution.py` +1 block (loss guard: daily/drawdown/disabled); `test_reviewer.py` refactored (OmniRoute isolated from cloud-count sections, new §6b). **12/12 test files pass** (test_backtest.py is a report script — run with `PYTHONPATH=backend`). |

---

## ✅ DONE — session of 2026-08-17 (this tracker was updated)

Everything below was verified live (server running, `python server.py --reload`):

| Item | What was delivered |
|---|---|
| **A. Reconnecting badge** | `api()` in `web/dashboard/dashboard.js` counts consecutive poll failures and shows an amber pulsing `#reconnectBadge` in the topbar after ≥2 misses; any success hides it. Markup in `index.html` (uses the existing `i-refresh` SVG), style in `dashboard.css` (`reconnect-badge[hidden]` override so the `hidden` attribute wins over `inline-flex`). |
| **B. Server auto-reload** | `python server.py --reload` runs the server as a child process and restarts it on any `backend/*.py` or `web/dashboard/*` change (`_reload_supervisor()` in `server.py`, mtime+size poll every 1s, crash-loop guard). uvicorn's own reloader was deliberately NOT used: it splits the app (child) from the live-data/bot threads (parent), which would let a dashboard "Start loop" double the bot loop. No new dependency. |
| **C. Alpaca-paper bot cycle verified** | Dry-run + live cycle both healthy. Held-by-reason (live cycle): 52 `not_on_broker`, 15 `no_agreement`, 6 `no_short_on_spot`, 3 `max_positions`; **ADA/USDT LONG filled via Alpaca** (qty 84,186). 0 errors. |
| **Bugfix: 422 crash in SL/TP monitor** | `manage_open_positions` crashed the WHOLE cycle with `alpaca 422: asset "CELOUSD" not found` — 4 stale **paper-broker** journal rows (no broker_ref, from before the Alpaca switch) were being priced and closed at the broker. Fix: skip symbols the broker can't trade + wrap each close in try/except (retry next cycle), and wrap the `manage_open_positions` call in `bot.run_once` so a broker hiccup can't kill the cycle. |
| **Data repair** | 1h candles were 44.5h stale (collector down since Aug 15) — the data gate correctly held everything. Refreshed 1h for 76/77 symbols via one-shot `repair_gaps`; collector's 1m backfill finished naturally. |

### Key system state (verified live)
- **Broker**: `EXECUTION_BROKER=alpaca`, `ALPACA_PAPER=true` — bot auto-loop LIVE on
  the ~$100k Alpaca paper account, sized from account equity.
- **⚠ Agreement gate is in TEST MODE**: `ARBITRATION_MIN_SOURCES=1` +
  `ALLOW_LIVE_SINGLE_SOURCE=true` (set in `.env`) — the live loop trades on a
  single high-confidence source (e.g. RSI(2)-only ADA LONG). This is intentional
  test config, but be aware it bypasses the two-source production gate.
- **Stale-position cleanup: DONE (see session note above)** — the 3 phantom
  paper rows (1INCH/ALICE/CELO) are `cancelled`; DOGE/ADA were real Alpaca fills
  and keep their (now backfilled) `broker_ref`s. Open count is 2, matching
  Alpaca exactly. Also note: `execution.execute_intent` now persists the fill's
  `broker_ref` — before that fix every real order journaled with `broker_ref`
  empty and reconcile couldn't match it.
- **News review**: `GROQ_API_KEY` wired — the Groq analyst is the main news
  engine (BTC +60 bullish, conf 0.8, verified live); Gemini `gemini-3.1-flash-lite`
  is the LLM primary with **OpenRouter as its fallback** (task E done — chain:
  Gemini → OpenRouter `meta-llama/llama-3.3-70b-instruct` → rule-based).
- **News**: 23 RSS feeds fetch fine; **no CryptoPanic key set** (RSS-only mode).
- **Tests**: all 10 files pass — execution, reviewer, arbitrator, verification,
  proprietary, system (41), strategy, news_analyst (18), tutor (27),
  ai_agents (39), alpha_news (19), web_scraper (17).

---

## ✅ DONE — Alpha Vantage News & Sentiment source (user key, 2026-08-17)

User's `ak_live_…` key wired as a structured news source.

| Item | What was delivered |
|---|---|
| **Fetcher** | `news.fetch_alpha_news(conn)` — one `NEWS_SENTIMENT` call → news-table rows with REAL per-ticker scores (not keyword guesses). Row score = most-relevant matched crypto ticker's `ticker_sentiment_score` (relevance-weighted, so a Solana story doesn't drag BTC along); `symbols` = matched tickers with relevance ≥ 0.1. Labels mapped from the API's Bullish/Somewhat-* scale. |
| **Free-tier safe** | Throttled to 1 call per `ALPHA_NEWS_FETCH_HOURS` (6h → ≤4 calls/day on the 25/day free budget); process-local throttle + never raises. Key in `.env`, gitignored. |
| **5-ticker cap discovered** | Free keys accept at most 5 symbols per request (verified: 50 and 77 tickers → `Invalid inputs`). So `ALPHA_NEWS_TICKERS` (default BTC,ETH,SOL,BNB,DOGE) drives the call; the long tail stays on RSS + Groq. |
| **Data caveat** | The free crypto feed lags (newest items ~6 months old at integration time) — the reviewer's 12h half-life decay zeroes stale scores, so old items fill the feed but never move the gate. Not a replacement for fresh RSS/Groq; a complementary structured-sentiment layer. |
| **Tests** | `test_alpha_news.py` (19 checks, offline): mapping, relevance floor, label scale, throttle, disabled, API-down. **11/11 test files pass.** |

---

## ✅ DONE — Firecrawl web scraping (user key, 2026-08-17)

User's `fc-…` key wired as the article-content layer for the AI agents.

| Item | What was delivered |
|---|---|
| **Scraper** | `backend/web_scraper.py` — one `POST /v1/scrape` per URL → clean
  markdown (main content only, boilerplate stripped, capped 12k chars). Retry
  with exponential backoff on 429/5xx; 402 (out of credits) is fatal and NOT
  retried; never raises. Key in `.env`, gitignored. |
| **Cache** | New `article_content` table + `database.get/set_article_cache` —
  content cached 24 h (`FIRECRAWL_CACHE_TTL_HOURS`), so repeat asks don't
  re-burn credits. Cache-first `get_article(conn, url)`; `enrich_recent(conn,
  limit)` pre-warms with the N newest articles (idempotent). CLI:
  `python web_scraper.py enrich [limit]`. |
| **Agents get context** | `/api/news/analyze?url=…&headline=…` — when `content`
  is empty the article is scraped (cached) and agent 1 scores the FULL
  article, not a bare headline. New `/api/news/article?url=` returns the raw
  markdown. Verified live: CoinDesk policy piece → 12k chars → agent scored
  NEUTRAL 0.2 (correctly non-sensational). |
| **Reload fix** | `.env` added to the `--reload` supervisor's watched set — a
  key written to `.env` while the server runs now auto-restarts it (previously
  the running server kept the old empty key until a code file changed). |
| **Tests** | `test_web_scraper.py` (17 checks, offline): mapping, boilerplate
  strip, retry/backoff, 402 fatal, cache round-trip + stale-rescrape, disabled,
  enrich idempotency. **12/12 test files pass.** |

---

## ✅ DONE — Groq AI integration (user spec, 2026-08-17)

Delivered the 4-component spec: Groq news engine, news-driven signals,
interactive tutor, and the math-system docs.

| Component | What was built |
|---|---|
| **1. Groq news analyst** | `backend/news_analyst.py` — batched Groq call
  (`qwen/qwen3.6-27b`, fallback `llama-3.3-70b-versatile`) returning structured
  per-asset analytics: sentiment −1..+1, confidence 0..1, impact horizon,
  headline reasons. Retry/backoff (honors Retry-After), second-model fallback,
  10-min cache + cooldown, NEVER raises (rule-based back-fill per coin).
  `GROQ_API_KEY` read from `.env` only (key provided by user, stored in `.env`,
  gitignored — recommend rotating since it was shared in chat). Now the MAIN
  engine: `reviewer.review_market` / the news gate use it first. Verified live:
  BTC +60 bullish (conf 0.8), LONG confirmed / SHORT vetoed by the gate.
  Note: Groq's Cloudflare rejects `urllib` (403 code 1010) — `requests` only. |
| **2. News-driven signals** | `news_analyst.signal_for` + `PROPRIETARY_MODE=news`
  source-B: LONG when sentiment ≥ +0.35 AND confidence ≥ 0.6, SHORT ≤ −0.35,
  HOLD inside the noise band. Risk applied downstream (ATR SL/TP, risk-% sizing,
  MAX_OPEN_POSITIONS, duplicate/cooldown). Thresholds configurable
  (NEWS_SIGNAL_*). |
| **3. Trading tutor** | `backend/tutor.py` + `/api/tutor` + `/api/tutor/trade`
  + dashboard **Learn** view. 12 topics (basics: market/limit/stop-limit orders,
  bid-ask, leverage, margin, R:R; advanced: slippage, ATR volatility weighting,
  liquidity traps, order-book imbalance, multi-timeframe) each with example +
  "why it matters" tied to THIS system; plus a why-this-trade explainer from
  journal reasons / held-by-reason. |
| **4. Math docs** | `docs/math_system/` — README (index), 01-indicators,
  02-risk-and-performance (Sharpe/drawdown/Wilson CI/Kelly), 03-signal-engine
  (Bayesian fusion/regime), 04-news-sentiment (normalization + thresholds),
  05-execution (ATR SL/TP, caps, cooldowns), 06-architecture (data flow). |

Tests: `test_news_analyst.py` (18 checks, offline via monkeypatch) +
`test_tutor.py` (27 checks) — all pass; full 9-file suite passes.

---

## ✅ DONE — three AI agents on Groq (user's system prompts, 2026-08-17)

Wired the user's three production-ready agent prompts onto the Groq transport.

| Item | What was delivered |
|---|---|
| **Agent 1 — News & Catalyst Sentiment** | `ai_agents.agent_news_sentiment(headline, content)` → strict-JSON `{sentiment BULLISH/BEARISH/NEUTRAL, impact_score 0..1, is_black_swan, expected_duration IMMEDIATE/MULTI_DAY/LONG_TERM_MACRO, core_catalyst}`. Schema violations → None. `/api/news/analyze`. |
| **Agent 2 — HODL / Portfolio research** | `agent_hold_research(symbol, catalyst)` → `{macro_tailwinds, risk_falsification, hold_recommendation ACCUMULATE_AND_HOLD/TRADE_ONLY/AVOID, confidence_percentage}` (unknown rec coerced, confidence clamped). `/api/portfolio/analyze`. |
| **Agent 3 — Chart confluence** | `agent_chart_confluence(conn, symbol, direction)` verifies a news trade against live chart structure: price, 1h change, S/R from `detect_support_resistance` (swing pivots, 1h×200, swing=5), news stance → `{is_valid_setup, rejection_reason, entry_strategy MARKET_BUY/WAIT_FOR_RETEST/ABORT, calculated_stop_loss}`. Cached 10 min/symbol. `/api/trade/verify`. |
| **Opt-in confluence gate** | `AI_CONFLUENCE_GATE=false` default: when enabled, source-B news proposals are verified by agent 3 before the arbitrator — TRAP setups dropped, valid ones carry S/R reasons. Gate never blocks a trade it can't run. |
| **Journal** | New `ai_analyses` table + `database.record_ai_analysis`/`load_ai_analyses`; every agent answer recorded (agent, symbol, request, verdict, payload JSON, model). `/api/portfolio/holds` lists them. |
| **Docs** | `docs/math_system/07-ai-agents.md` (prompts, schemas, S/R math, gate flow, failure semantics). |

Verified live: BlackRock ETH-ETF headline → BULLISH impact 0.85; hold research →
ACCUMULATE_AND_HOLD 80%; BTC LONG verify → WAIT_FOR_RETEST, stop 63210 just
outside support 63310. All endpoints journal into `ai_analyses`.
Tests: `test_ai_agents.py` (39 offline checks) — **10/10 test files pass**.

---

## ⏳ PENDING — followups not yet done

### D. Tune `NEWS_VETO_SCORE` / `NEWS_BIG_MOVE_SCORE`
- **What**: pick veto thresholds from real gate history.
- **Status**: still blocked on data. 116 recorded decisions (all neutral, score 0 —
  news was stale). After the Aug 17 news refresh + LLM overlay, 8 new LLM-mode
  verdicts were recorded but still neutral (score 0 for the traded symbols; BTC
  scored +20 on the review but wasn't in a veto position). Replay tool:
  `python scripts/analyze_news_gate.py`. Revisit after ~a week of live vetoes.
- **Effort**: tiny now, revisit later.

### E. OpenRouter free fallback key ✅ DONE (2026-08-17)
- **What**: add fallbacks so the news review survives Gemini rate-limits/downtime.
- **Status**: DONE. User's `sk-or-v1-…` key wired into `.env` as
  `LLM_FALLBACKS="https://openrouter.ai/api/v1|meta-llama/llama-3.3-70b-instruct|<key>"`.
  Reviewer chain is now **Gemini → OpenRouter → rule-based** (verified live:
  `_llm_providers()` length 2; `/api/bot/status` reports `llm_providers: 2`;
  simulated Gemini downtime → OpenRouter answered, BTC +50 bullish).
- **Bug found & fixed along the way**: (1) an **empty `LLM_FALLBACKS=` in the
  shell environment** silently disabled every configured fallback (python-dotenv
  won't override existing env vars) — `config.py` now fills any empty/missing
  key from `.env` after `load_dotenv()` (real non-empty env overrides still win,
  so the test fixtures keep working). (2) **Prose-wrapped JSON**: both
  `reviewer._parse_llm_json` and `news_analyst._parse_json` only stripped
  code fences at the very start; OpenRouter's llama-3.3-70b answers with
  "Based on the headlines…\n\n```\n{…}\n```" → parse returned None → the
  fallback silently never scored. Both parsers now extract the first `{…}`
  block as a fallback. Regression tests added (test_reviewer §2b, test_news_analyst).
- **Security**: key was shared in chat — stored in `.env` (gitignored) only;
  consider rotating at openrouter.ai/keys.
- **Effort**: done.

---

## 🔁 Useful commands (resume kit)

```bash
# Start the server (dashboard :8501, auto-loop included) — dev mode with auto-restart
cd backend && python server.py --reload
# or plain: cd backend && python server.py

# Add free LLM keys to .env (Gemini/OpenRouter/Groq)
python scripts/setup_free_llm.py

# Replay news-gate thresholds against recorded decisions
python scripts/analyze_news_gate.py

# One-shot bot cycle (preview only)
cd backend && python bot.py --dry-run

# Tests
python tests/test_execution.py && python tests/test_reviewer.py \
  && python tests/test_arbitrator.py && python tests/test_verification.py \
  && python tests/test_proprietary.py && python tests/test_system.py \
  && python tests/test_strategy.py
```

## 📌 Known quirks / gotchas (save for later)
- `gemini-2.5-flash` (and `-lite`) are **deprecated for new users** — use
  `gemini-3.1-flash-lite`. If the /models list changes, re-check with a tiny
  chat-completions call before updating `.env`.
- Alpaca only trades ~25 of our 77 symbols — the rest are held `not_on_broker`
  (by design, no more 422 spam). Alpaca crypto is **long-only** (SHORTs held).
- The bot sizes from the **Alpaca account equity** (not the ~$10k journal) — that
  is intentional; switching `EXECUTION_BROKER` back to `paper` reverts sizing.
- **Collector is slow after downtime**: it gap-repairs every symbol×interval
  sequentially (1m first). With ~1.3s per Binance request on this network and GIL
  contention with the bot loop, a full catch-up after a long outage takes a while.
  `collector.repair_gaps(conn, sym, "1h", days=3)` one-shot per symbol is the fast
  fix (idempotent upserts, safe alongside the running collector).
- `server.log` and `.vscode/` are gitignored.
- Uvicorn is plain (no `[standard]` extras) — the `--reload` supervisor in
  `server.py` doesn't need watchfiles (mtime poll).
