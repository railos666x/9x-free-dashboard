# CryptoAutomation - Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-08-17

### Changed — Trading is now 100% AI + news on the Exness forex universe

Per user request: the old candle-factor signal sources are REMOVED from the
trade path and the bot trades ONLY on the AI/news engine, on the Exness
forex/metals/indices instruments (crypto was loss-making).

- **Only one trade source: the Groq news analyst.** `bot.run_once` now
generates every proposal from `news_analyst.signal_for` (headlines from
Tavily / Alpha Vantage / RSS → structured per-asset sentiment → LONG/SHORT
above the `NEWS_SIGNAL_*` thresholds). No more two-source agreement gate:
the analyst applies its own confidence floor, then the chart-confluence
agent (agent 3, `ai_agents.gate_news_trade`) verifies the setup against
live price structure when `AI_CONFLUENCE_GATE=true` (on in `.env`).
- **Retired from trading:** `math_engine`, `strategy v2`, `hybrid`, the
proprietary RSI(2) source, and the two-source arbitrator gate. They still
power `/api/signals` (dashboard display) and the backtester, and remain
re-enable-able for dry-testing — but never feed trade decisions. The old
`reviewer.verify_trade` news gate is off too (`NEWS_VERIFY_ENABLED=false`):
the signal already IS news, a second news review would double-gate it.
- **Universe = Exness only.** New `config.TRADING_UNIVERSE` = the Exness
forex/metals/energy/indices instruments (BTC/ETH CFDs excluded — still
crypto exposure). Crypto pairs + Bitcoin ETFs no longer generate orders;
the data pipeline still collects them for the dashboard + backtester.
- `config.SIGNAL_SOURCE_A` default is now `ai_news`; `TRADING_UNIVERSE`
scopes the bot; Tavily news query broadened to macro headlines
(forex/dollar/gold/oil/indices + crypto) so the analyst can score the
instruments the bot trades. The news analyst prompt + asset→symbol map now
cover the Exness macro universe (EURUSD, XAU, WTI, US500, DXY, …).
- Kill-switch re-scoped: LIVE runs are only blocked for a LEGACY engine in
single-source test mode; the ai_news path is single-source by design.

### Fixed — data + LLM layers the switch depended on (verified live)
- **News timestamp ordering bug** — `published_at` mixed RFC-2822 and ISO
  formats, so SQL `ORDER BY published_at DESC` sorted lexicographically
  ("Wed" > "Mon", ISO beats RFC) and buried TODAY's macro stories behind
  3-day-old RSS rows — the news analyst never saw the stories the bot
  trades. `database.normalize_news_ts` now normalizes on write and
  `backfill_news_ts` migrated the 1,272 existing rows.
- **Dead LLM failover** — `llama-3.3-70b-versatile` is NOT a Groq model on
  this account (404) and `qwen/qwen3.6-27b` was free-tier 429, so the
  analysis chain stalled ~2.5 min of backoff then gave up (a bot cycle
  timed out at 240s). Now `openai/gpt-oss-120b` primary +
  `openai/gpt-oss-20b` fallback (both verified, ~2s per analysis) and
  backoff sleeps are capped (8s / 30s Retry-After) so a dead chain can
  never stall the bot loop.
- **Analyst feed prioritization** — `fetch_headlines` surfaces stories
  about traded instruments (gold/dollar/yen/oil/indices via
  `reviewer.COIN_HINTS`, re-derived at read time) ahead of the general
  RSS flood; COIN_HINTS extended to the full trading universe.
- **Rule-based backfill never trades** — backfill rows have no model
  confidence and naive keyword direction (a "yen strengthens" headline
  tagged USD/JPY LONG — the quote-currency inversion). Display-only now.
- **Inverse-pair guard** — the analyst prompt now states direction
  conventions (USDJPY/USDCHF/USDCAD/USDCNY: second-currency strength =
  pair falls) after the model scored "yen strength" as USD/JPY LONG.

### Added — Groq AI integration (4-component spec)
- **Groq news analyst** (`backend/news_analyst.py`) — the MAIN news sentiment
  & analytics engine: one batched call per 10 min to `qwen/qwen3.6-27b`
  (fallback `llama-3.3-70b-versatile`) returning structured per-asset JSON
  (sentiment −1..+1, confidence, impact horizon, headline reasons). Retry with
  exponential backoff, second-model fallback, cache + cooldown, rule-based
  back-fill for every coin, never raises. `GROQ_API_KEY` from `.env` only.
  `reviewer.review_market` and the news gate now use it first (BTC +60
  bullish → LONG confirmed / SHORT vetoed, verified live).
- **News-driven trade signals** — `news_analyst.signal_for` + new source-B
  mode `PROPRIETARY_MODE=news`: LONG at sentiment ≥ +0.35 with confidence
  ≥ 0.6, SHORT ≤ −0.35, HOLD inside the noise band (config `NEWS_SIGNAL_*`).
- **Interactive trading tutor** (`backend/tutor.py` + `/api/tutor` +
  `/api/tutor/trade`) — 12 basic/advanced topics (order types, bid-ask,
  leverage, margin, R:R, slippage, ATR volatility weighting, liquidity traps,
  order-book imbalance, multi-timeframe) each with example + "why it matters";
  plus a why-this-trade explainer from journal reasons / held-by-reason.
  Dashboard **Learn** view added (sidebar → Learn).
- **Math documentation** — `docs/math_system/` (README + 6 files): indicator
  formulas, risk & performance math (Sharpe/drawdown/Wilson CI/Kelly), signal
  engine (Bayesian fusion/regime), news-sentiment pipeline + normalization,
  execution/risk controls, architecture & data-flow diagrams.

### Added — Firecrawl web scraping (`backend/web_scraper.py`)
- **Full article context for the AI agents**: one `POST /v1/scrape` per URL
  returns clean markdown (main content only, boilerplate stripped, capped at
  12k chars). `/api/news/analyze` now accepts `url=` — when `content` is
  empty the article is scraped (cached) and agent 1 scores the FULL article
  instead of a bare headline. New `/api/news/article?url=` returns the raw
  markdown. `python web_scraper.py enrich [limit]` pre-warms the cache.
- **Credit-safe**: content cached 24h in the new `article_content` table
  (`FIRECRAWL_CACHE_TTL_HOURS`), retry with exponential backoff on 429/5xx,
  402 (out of credits) is fatal and NOT retried, never raises. Key in `.env`
  only.
- **`--reload` watches `.env`**: a key written to `.env` while the server
  runs now auto-restarts it (previously the running process kept the old
  empty key until a code file changed).
- Tests: `test_web_scraper.py` (17 offline checks) — 12/12 test files pass.

### Added — Alpha Vantage News & Sentiment source (`backend/news.py`)
- **Structured news scores**: one `NEWS_SENTIMENT` call per
  `ALPHA_NEWS_FETCH_HOURS` (6h default → ≤4 calls/day) ingests the top
  crypto stories with real per-ticker sentiment scores + relevance. Row
  score = most-relevant matched ticker's score; `symbols` = matched
  tickers with relevance ≥ 0.1. Label from the API's Bullish/Somewhat-*
  scale.
- **Free-tier safe**: 25 requests/day budget respected via throttle;
  never raises; key in `.env` only. Discovered the free keys accept at
  most **5 symbols per request** (50+ tickers → `Invalid inputs`), so
  `ALPHA_NEWS_TICKERS` (default BTC,ETH,SOL,BNB,DOGE) drives the call;
  the long tail stays on RSS + Groq.
- **Caveat**: the free crypto feed lags (newest items ~6 months old) —
  the reviewer's 12h half-life decay zeroes stale scores, so old items
  fill the feed but never move the gate. Complementary to RSS/Groq, not
  a replacement.
- Tests: `test_alpha_news.py` (19 offline checks) — 11/11 test files pass.

### Added — OpenRouter failover for the LLM news review (task E)
- **Reviewer chain is now Gemini → OpenRouter → rule-based**: the user's
  OpenRouter key is wired via `LLM_FALLBACKS` in `.env`
  (`meta-llama/llama-3.3-70b-instruct`). Verified live: simulated Gemini
  downtime → OpenRouter answered the review (BTC +50 bullish).
  `/api/bot/status` now reports 2 providers.

### Fixed — two config/parsing bugs the fallback exposed
- **Empty env var shadowed `.env`**: a shell-exported empty `LLM_FALLBACKS=`
  silently disabled every configured fallback (python-dotenv never overrides
  existing env vars). `config.py` now fills any key that is empty/missing in
  the environment from `.env` after `load_dotenv()` — real non-empty env
  overrides still win, so env-based test fixtures keep working.
- **Prose-wrapped JSON was rejected**: `reviewer._parse_llm_json` and
  `news_analyst._parse_json` only stripped code fences at the very start of
  the reply; OpenRouter's llama-3.3-70b-instruct answers with
  "Based on the headlines…\n\n```json\n{…}\n```", so the parse returned
  None and the fallback never scored a coin. Both parsers now fall back to
  extracting the first `{…}` block. Regression tests: test_reviewer §2b,
  test_news_analyst prose-wrapped case.

### Added — three production AI agents (`backend/ai_agents.py`)
- **Agent 1 — News & Catalyst Sentiment** — one headline/article → strict-JSON
  `{sentiment, impact_score 0..1 (0.8+ = major liquidity event), is_black_swan,
  expected_duration, core_catalyst}`. `/api/news/analyze`.
- **Agent 2 — Deep Research & HODL Portfolio** — catalyst →
  `{macro_tailwinds, risk_falsification, hold_recommendation
  (ACCUMULATE_AND_HOLD/TRADE_ONLY/AVOID), confidence_percentage}`.
  `/api/portfolio/analyze` + `/api/portfolio/holds` (journal listing).
- **Agent 3 — Price Action & Chart Confluence** — news trade verified against
  live chart structure: price, 1h change, nearest support/resistance (swing-
  pivot detector) and news stance → `{is_valid_setup, rejection_reason,
  entry_strategy (MARKET_BUY/WAIT_FOR_RETEST/ABORT), calculated_stop_loss}`.
  Flags DIVERGENCE / TRAP (never buy under heavy resistance, never short above
  heavy support). Per-symbol 10-min cache. `/api/trade/verify`.
- **Opt-in confluence gate** (`AI_CONFLUENCE_GATE`, default off) — news-driven
  source-B proposals are verified by agent 3 before the arbitrator; rejected
  traps never reach the gate. Fail-open (a gate it can't run never blocks).
- **Agent journal** — `ai_analyses` table: every agent answer recorded
  (agent/symbol/request/verdict/payload/model), read back via
  `database.load_ai_analyses`, exposed at `/api/portfolio/holds`. All agents
  never raise: schema violations and failures return None, endpoints report
  `ok: false`.
- **Docs** — `docs/math_system/07-ai-agents.md`: prompts, JSON schemas, S/R
  math, the gate's data flow, failure semantics.

### Added
- **"Reconnecting…" badge** — when ≥2 dashboard polls fail in a row (server
  restart / downtime), an amber pulsing pill appears in the topbar; any
  successful response hides it. No more silent console noise.
- **`python server.py --reload`** — dev auto-restart: the server runs as a
  child process and restarts on any `backend/*.py` / `web/dashboard/*` change
  (mtime poll, crash-loop guard, no new dependency). Deliberately not
  uvicorn's reloader, which would split the bot-loop threads from the app
  and allow a double bot loop.

### Fixed
- **SL/TP monitor 422 crash** — `manage_open_positions` crashed the whole
  bot cycle with `alpaca 422: asset "CELOUSD" not found` when pricing stale
  paper-broker journal rows left over from before the Alpaca switch. The
  monitor now skips symbols the broker can't trade and wraps each close in
  try/except (retries next cycle); `bot.run_once` also wraps the monitor call.

### Changed
- **1h candle data repaired** (76/77 symbols refreshed; was 44.5h stale after
  collector downtime) — the data-quality gate had been correctly holding all
  trades on stale candles.

---

## [Unreleased] - 2026-08-15

### Added
- **Date-range filter for closed trades** — Today / 7d / 30d / All presets
  plus custom from→to dates; donut, every-closed-trade list and journal
  table all scope to the period
- **Every closed trade list** — per-trade rows under the Profit-by-currency
  donut (symbol, side, entry→exit, reason, PnL, time)
- **Live trades panel** — side card with open positions (price, unrealized
  PnL, SL/TP distance, opened time), polled every 10s via the new lean
  `/api/positions` endpoint

### Fixed
- **Donut tooltip PnL sign** — slices sized by absolute value showed "+$"
  for losses too; now shows the real sign
- **Side badges wrapping** — "▲ LONG"/"▼ SHORT" stacked onto two lines in
  narrow table/live-panel columns; badges and journal cells now
  `white-space: nowrap`, table card scrolls horizontally instead

### Changed
- **Emoji → professional inline-SVG icons** — sidebar nav, brand mark,
  search/caret pickers, Dry/Live test buttons, TV link, journal chip,
  alert/lock indicators all use a self-contained stroke-icon sprite
  (no CDN, matches the theme)

---

## [Unreleased] - 2026-08-15

### Added
- **News verification badges on signal cards** — `NEWS ✓ / ✗ / −` verdict
  chips per coin (from the reviewer gate), with score + reason on hover;
  compact `N +score` chip on HOLD rows
- **Journal trade chips in Signals view** — coins with journal orders show
  a `⏺ status` chip linking to the Orders view
- **News-gate stats row in Bots panel** — "News gate (7d): X vetoed · Y
  confirmed · Z neutral" from `/api/news/stats`
- **Foreign-fill warning** — Reconcile now flags broker fills our journal
  never placed (manual / other tools) inline in the journal summary
- **LLM overlay status row** — Bots panel shows rule-based vs active
  (provider count) via `/api/bot/status llm_providers`

### Changed
- **News review now LLM-powered** — Gemini free tier (`gemini-3.1-flash-lite`,
  OpenAI-compat endpoint) with optional OpenRouter/Groq fallbacks; cached
  10 min so free tiers aren't burned; keyless cloud providers skipped
- **Broker = Alpaca paper** — bot + portfolio use the real $100k Alpaca
  paper account; sizing scales with account equity

### Fixed
- **Alpaca rejected-order spam** — unsupported assets/shorts are held
  (`not_on_broker` / `no_short_on_spot`) instead of 422/403ing at the
  broker; stale error rows cleaned from the journal

---

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