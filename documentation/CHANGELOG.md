# CHANGELOG.md

Comprehensive chronological record of all changes. Every modification by any
agent must be appended here.

---

## 2026-08-15 — Execution stack + news-verification gate + free-LLM news review (feature/ai-execution-news-gate)

### Trading pipeline (new: bot.py · arbitrator.py · execution.py · verification.py · proprietary_signals.py)
- **Two-source agreement gate** — source A (math_engine | strategy | hybrid,
  `SIGNAL_SOURCE_A`) + source B (RSI(2) proprietary, `PROPRIETARY_SIGNALS_ENABLED`)
  must agree before any trade. Intent-hash dedup makes duplicate/conflicting
  orders impossible.
- **Single execution path** — `execution.py`: paper (offline fills) / Alpaca
  (REST, paper or live) / Binance (signed spot) brokers; idempotent order
  journal (`orders` table); ATR default SL/TP; risk-based sizing capped by
  Kelly + `MAX_POSITION_PCT`; SL/TP monitor; broker reconciliation incl.
  **foreign-fill flagging** (broker fills our journal never placed).
- **Verification gate** — data-quality checks (staleness/spikes) before both
  trusting signals and executing; second-price cross-check via Alpaca.
- **Alpaca account-equity sizing** — `_broker_equity()` sizes from the real
  Alpaca account (e.g. $99,888 paper) instead of the ~$10k local journal.

### News verification layer (the confirmation AI)
- `reviewer.verify_trade()` runs AFTER the sources agree, BEFORE execution:
  opposing headlines **veto**, aligned headlines **confirm** (journaled as a
  reason), an extreme score in either direction (`NEWS_BIG_MOVE_SCORE`)
  means a big move is expected → stand aside. Neutral news never blocks.
- Every gate decision journaled (`news_verdicts` table) → `/api/news/stats`
  → dashboard "News gate (7d)" + `scripts/analyze_news_gate.py` threshold
  replay for tuning.
- Signal cards show `NEWS ✓ / ✗ / −` verdict badges + journal trade chips
  linking to Orders; held-by-reason panel surfaces veto messages.

### Free-LLM news review chain (verified Aug 2026)
- Primary **Gemini** free tier (OpenAI-compat endpoint,
  `gemini-3.1-flash-lite` — 2.5-flash is deprecated for new users) + free
  **OpenRouter** / **Groq** fallbacks via `LLM_FALLBACKS`.
- **Free-tier throttle**: successful LLM verdict cached and served for
  `LLM_MIN_INTERVAL_SECONDS` (600s) instead of per-20s signals refresh;
  keyless cloud providers skipped (no wasted 401s).
- `scripts/setup_free_llm.py` one-command key wiring; `LLM_ENABLED=true`;
  dashboard shows overlay status (rule-based vs active providers).

### Fixes
- **Alpaca 422/403 spam fixed**: `broker_supported_symbols()` fell back to
  ALL 87 symbols when the asset-metadata call failed → orders for assets
  Alpaca doesn't list always 422'd and spot SHORTs 403'd. Now falls back to
  an EMPTY set (hold everything) — a held signal is harmless, a rejected
  order is journal noise. 37 stale never-filled error rows cleaned.
- SHIB fills mystery: confirmed the two "$355k/$462k" rows were our own
  SHIB order's two partial fills, displayed with a ~545× wrong price
  ($0.0025 vs $0.00000459) — notional was $1,498.69.

### Ops
- `EXECUTION_BROKER=alpaca` (paper) — bot now trades the $100k Alpaca paper
  account with correct sizing; portfolio mode flips to the Alpaca account.
- Tests: execution/arbitrator/verification/reviewer/proprietary all pass;
  node --check clean; server restarted; `/api/orders` 0 ERROR rows.

---

## 2026-08-15 — Dashboard v2: working sidebar, signals + bots panels, old terminal retired

### Old terminal removed (per user request)
- `web/terminal/` moved to `legacy/terminal/` (recoverable). `/terminal` route
  and `/assets` static mount dropped from `backend/server.py`; `/` is now the
  only page. Verified: `/terminal` and `/assets/js/*` return 404.

### Dashboard rebuilt (`web/dashboard/`)
- **Working sidebar** — every nav item switches a real view: Dashboard
  (overview), Assets (all-crypto table), My portfolio (holdings + balance),
  Bots (backtest panel), Signals (live signals). Dead items removed
  (Terminal, Marketplace, DCA/GRID bots, Orders, Subscription).
- **Signals view** — live `/api/signals` table: direction badges (BUY/SELL/
  HOLD), confidence, entry, SL, TP, R:R, top reason; sorted by confidence;
  polls every 60 s. 87 coins, 15 active.
- **Bots view** — backtest panel wired to `/api/backtest`: symbol + timeframe
  pickers → return, win rate + Wilson CI, PF, Sharpe, max DD, annualised,
  Monte Carlo P(profit), walk-forward verdict, exit analysis.
- **Dead buttons removed**: Forecast/Sort/⋯ chips, This Week/⋯ chips, table
  preset chips, Show More link, notification bell. Search + Connect exchange
  kept (both functional).
- **Bug fixes**: `totalValue` never set (showed $0.00) → now latest history
  value; ticker pills showed "BTCT"/"ETHT" → proper symbol split; signals
  reason rendering handled dict-shaped reasons.

### Verified
- node --check clean; `/`, assets, portfolio, watchlist, signals all 200;
  test_strategy 41/41; test_system ALL PASSED.

---

## 2026-08-15 — Server ops: single-flight signals lock + funding DB-first (cold-start fix)

- **Bug:** `/api/signals` had a TTL cache but no single-flight lock. The
  terminal polls every 5 s while a full 87-symbol compute takes tens of
  seconds → every poll spawned its own compute and they stacked
  (measured: >180 s stalls, CPU thrash).
- **Fix (server.py):** `_signals_lock` around the compute; concurrent polls
  wait on the lock and share the fresh cache. Verified: 5 concurrent
  requests all returned in ~3.8 s.
- **Fix (signals.py):** funding rates now read DB-first
  (`database.load_funding`, 30h recency window — rates settle every 8h),
  network only on DB miss, successful fetches upserted to the `funding`
  table. Cold-start funding cost: 87 × ~2.6 s sequential (~3.8 min) →
  parallel 8-thread prefetch (~30 s) → pure DB reads (~0 s) once populated.
  Funding rows in DB: 72+ of 87 after first cycles.
- Verified: `/`, `/terminal`, portfolio, macro, watchlist, news,
  fear-greed, signals — all 200; warm signals 0.1 s.

---

## 2026-08-15 — DIRECTIVE-001 executed: parameter optimization (negative result)

### New file
- `backend/optimizer.py` — the directive's optimizer: grid search on the
  train window (first 80% of bars), cross-symbol robustness ranking,
  hold-out validation (last 20%, untouched), cost sensitivity. Owned files
  stay byte-identical — train/hold-out slicing and the half-distance TP mode
  are injected via in-process monkeypatching (same technique the directive
  endorses for cost scenarios). Caches results in `data/optimizer_{train,
  holdout}.json` so steps rerun incrementally.

### Execution (4 workers, ~1 min)
- Step 1: 324 configs × 5 majors = 1,625 train backtests (z ∈ {2,2.5,3},
  n ∈ {24,48,72}, sl ∈ {1.5,2,2.5}, trend_ma ∈ {100,200,400}, TP ∈
  {mean,half}, time stop ∈ {OU-HL,fixed24}).
- Step 2: **no config passed the gate** (WR≥50, PF≥1.1, ≥30 trades on ≥3/5
  symbols); best passes 2/5 (SOL, DOGE). BTC/BNB lose across the grid.
- Step 3: top-3 configs on hold-out → **0/15 symbol-runs meet the bar**
  (PF>1.1, P(profit)>0.7, WR≥60%, CI-lo≥50%). Hold-out samples 3–13 trades.
- Step 4: BTC cost sensitivity: 10 bps spot −9.62% vs 5 bps futures −7.17%.
- Step 5: **NOT ACCEPTED** — honest negative result; sample-size diagnosis
  (trend filter makes 1h trades sparse: 3–56 per 300-day train window).

### Docs
- RESEARCH_NOTES §7 (full protocol + findings), DIRECTIVE-001 status
  checked off with the verdict, this changelog.

---

## 2026-08-15 — Filesystem restructure + Sellimor-style dashboard + watchlist volume

### Filesystem restructure
- New layout: `backend/` (all Python), `web/` (terminal + new dashboard),
  `tests/`, `scripts/` (bat launchers), `legacy/` (streamlit dashboard,
  run_live, root index/src Vite app), `data/` (DB lives in project root still).
- All imports/paths updated: config DB path, server static mounts
  (`/assets`, `/dash-assets`), tests bootstrap, bat files. Both test suites
  pass from new locations.

### Sellimor-style dashboard (new page at `/`)
- `web/dashboard/` — index.html + dashboard.css + dashboard.js, wired to the
  real API: ticker pills (BTC/ETH), 4 asset cards, monthly portfolio-value bar
  chart, range-selectable total-assets line chart, portfolio list, balance +
  distribution bar, all-crypto table (87 symbols, search, sparklines), and the
  Connect-exchange modal (stores Binance keys via `/api/connect-exchange`).
- Terminal remains at `/terminal`.

### Backend
- `portfolio.py` (new): demo portfolio priced live + real Binance balances via
  signed API when keys are connected; 90-day daily value history from real
  candles.
- `server.py`: `/api/portfolio`, `/api/connect-exchange`,
  `/api/disconnect-exchange`; `/api/watchlist` now returns `volume`.
- Static mounts: `/dash-assets` serves dashboard css/js.

### Validation
- watchlist: 87 rows (83 with volume); portfolio demo: $26.8k, 5 holdings,
  90 history bars; node --check clean; test_system ALL PASSED; test_strategy
  41/41.

---

## 2026-08-14

### Research & Audit (Phase 1–2)
- Researched win-rate/expectancy math: `E = W·R − (1−W)`, breakeven `W* = 1/(1+R)`.
- Full codebase audit (all Python modules, docs, live DB).
- **Data audit findings:** only ~44 days of 1h data for majors; most of 109
  symbols had no 1h data; 5 dead symbols; 5 duplicate symbols; no funding-rate
  history; no spread/order-book data; 2 gaps in BTC 1h.
- **Bug findings:** SL/TP rounded to 2 decimals (fatal for SHIB-class coins);
  hardcoded $1,000 position sizing; Kelly computed but unused; live/backtest
  divergence (4h/1d/news/funding absent in backtest); backfill aborts on first
  dead symbol; hand-set (unmeasured) Bayesian priors.

### Implementation (Phase 3A — data layer)
- `config.py`: deduplicated SYMBOLS (removed DYDX/OCEAN/CKB/TRB/CTSI dupes),
  removed dead listings (ICON, QLC, OMN, BLOB, BTCDOM) → 87 valid symbols;
  added `INTERVAL_BACKFILL_DAYS` (1h/4h → 365d, 1d → 730d); added risk/execution
  config block (initial capital, risk per trade, Kelly fraction, fee bps,
  slippage tiers, volume floor).
- `database.py`: new `funding` table + `upsert_funding` / `load_funding` /
  `count_funding`.
- `collector.py`: per-symbol try/except in backfill (dead symbols skipped, run
  completes); per-interval backfill depth; live-loop gap repair
  (`repair_gaps`); new `python collector.py funding` command +
  `fetch_funding_history` (fapi.binance.com, no key).

### Implementation (Phase 3B — strategy & risk)
- `strategy.py` (NEW): `MeanReversionParams`, `zscore_series`, `adx_series`,
  `estimate_half_life` (significance filter + sample-length cap),
  `signal_frame` (vectorized), `compute_signal` (live wrapper).
- `risk.py` (NEW): `slippage_bps`/`fee_bps`/`total_cost_bps` (config tiers),
  `kelly_fraction` (fractional), `risk_per_trade_pct` (capped),
  `position_size`, `safe_stop` (NaN-proof), `round_price` (6 sig figs).

### Implementation (Phase 3C — backtester & fixes)
- `backtester.py` (REWRITE → v2): vectorized mean-reversion engine + legacy
  math_engine path; real equity curve; dynamic Kelly-capped sizing (replaces
  $1,000 hardcode); slippage + fees on every fill; conservative intrabar
  SL-first ordering; 1-bar re-entry cooldown; chronological 5-fold walk-forward
  with McLean-Pontiff verdict; Monte Carlo bootstrap (1,000 iters);
  Wilson 95% CI on win rate; exit-reason attribution; cost model reporting.
  Public API preserved (`run_backtest` / `BacktestResult` / `print_report`).
- `math_engine.py`: SL/TP rounding → `risk.round_price` (6 sig figs). Fixes
  SHIB-class coins where SL/TP rounded to 0.0.
- `test_system.py`: stdout reconfigure to UTF-8 (fixes cp1252 emoji crash).

### Validation (Phase 3D)
- `test_strategy.py` (NEW): 40 tests — z-score, half-life, signal geometry,
  risk math, micro-coin regression, no-look-ahead guards, equity consistency,
  trend-filter suppression, legacy path. **All pass.** `test_system.py` green.
- Backfilled 1h + 4h for BTC/ETH/SOL/BNB/DOGE to 365 days (+9,000 / +3,000
  candles per symbol).
- Validation backtests (net of fees+slippage): BTC WR 37.7% (CI 29.8–46.3%),
  PF 0.64, −31% total; SOL WR 35.9%, PF 0.71. **Target (60–70% WR) NOT met** —
  recorded honestly; diagnosis: SL-dominant exits (61–64% SL), cost burden.
- Walk-forward stability: folds consistent (30–42% WR), decay −2.6% (OK) →
  result is a weak edge, not overfitting.

### Coordination
- `CLAUDE.md`: added "Multi-Agent Coordination" section (file ownership split,
  mtime-check protocol, no concurrent network jobs, no edit ping-pong, no
  commits without approval).
- `documentation/` created (this folder): ARCHITECTURE, STRATEGIES, CHANGELOG,
  RESEARCH_NOTES, decisions/, directives/.

### Claude's session (observed, not ours)
- `server.py` (13:28), `signals.py` (15:34), `terminal/assets/js/terminal.js`
  (16:46) modified — UI/server territory, no overlap with owned files.

## 2026-08-14 (evening) — Web research round 1 + strategy v2

### Research
- Reviewed Connors RSI(2) literature (75–79% WR claims, 200-MA trend rule),
  Presto Research funding-rate predictability study, and intraday-seasonality
  sources (22:00–23:00 UTC best; 03:00–04:00 UTC worst).

### Implementation (`strategy.py` — additive, backward compatible)
- `MeanReversionParams`: added `entry_mode` (z | rsi2), `rsi2_entry`/`rsi2_exit`,
  `trend_filter`/`trend_ma`, `hour_filter` (UTC window), + validation.
- `signal_frame()`: added `rsi2`, `trend_ok_long/short`, `hour_ok`,
  `rsi2_exit_ok` columns; entries now respect trend + hour filters; RSI(2)
  entry mode supported.
- New `_rsi_series()` helper (Wilder RSI(2), dependency-free).
- **Default change:** `trend_filter=True` (v2 baseline, ADR-002).

### Backtester (`backtester.py`)
- `_run_mean_reversion`: added `rsi_exit` exit path (Connors RSI(2) crossing,
  used only in `entry_mode="rsi2"`).

### Validation (A/B, 5 majors, 1h, 375d, net costs)
- v1 baseline: WR 35.5–41.7%, returns −33.7 to −12.6% (all negative).
- **v2 trend-filter: WR 31.4–52.4% (BTC 52.4%), returns −15.8 to −4.2% —
  adopted as default (ADR-002).**
- v2 rsi2+trend: WR 19.7–29.9%, returns −68.5 to −51.7% — rejected at 1h
  (ADR-002); mechanism retained for other timeframes.
- Funding overlay: not implemented (research shows no single-asset edge).

### Docs & tests
- `test_strategy.py`: 41 checks (added trend-filter geometry/reduction
  assertions; pipeline checks now run unfiltered). All pass.
- `documentation/`: RESEARCH_NOTES §5, STRATEGIES v2 table, ADR-002, CHANGELOG.

## 2026-08-14 (late) — UI fix: "Trading signals" panel stuck on "loading…"

### Root cause
`/api/signals` was pathologically slow, so the terminal's 5s polling loop
piled up long-running requests and the panel never resolved:
1. **O(n²) in `signals.py`:** `generate_all_signals` looped 87 symbols, and
   each `generate_signal` re-loaded all 87 symbols' 1h data from the DB
   (87×87 = 7,569 queries) + a synchronous Binance funding fetch per symbol
   (10s timeout each). Measured: 3.15s per symbol → **~4.6 min per refresh**.
2. **No cache:** every 5s poll spawned a new 4.6-min compute thread.
3. **Frontend:** no fetch timeout / no error state → permanent "loading…".

### Fixes
- `signals.py`: cross-sectional universe loaded ONCE per refresh and shared
  across symbols (kills O(n²)); funding rate fetched through a TTL cache
  (5 min — funding only moves every 8h) that caches failures too. Warm
  refresh: 4.6 min → ~8s; cold first call ~17–27s (network-bound).
- `server.py`: `/api/signals` now served from a 20s in-memory cache — polls
  between computes return instantly, no thread pile-up.
- `terminal/assets/js/terminal.js`: `fetchJSON` aborts after 15s (AbortController)
  so nothing hangs forever; `loadSignals` shows "⚠ Signals unavailable —
  retrying…" on failure and self-heals on the next poll.

### Validation
- Warm refresh measured 7.7–8s (was 276s). Server import OK; terminal.js
  syntax OK; all 41 strategy tests + system tests pass.
- **Action required:** restart `python server.py` for the fixes to take effect.

## 2026-08-14 (night) — Watchlist data gap, macro graphs (gold/S&P 500), fetch_all

### Diagnosis: why watchlist/signals didn't load for all coins
1. `terminal.js` SYMBOLS was stale: 5 dead listings (ICON, QLC, OMN, BLOB,
   BTCDOM) + 5 duplicates — sidebar rows that could never load.
2. **Real gap:** only 5/87 symbols had 1h data and 22/87 lacked 15m data —
   signal engine needs ≥60 1h bars, so 82/87 coins returned "insufficient
   data" and the watchlist (15m) showed blanks. Fixed by full backfill.

### Changes
- `terminal/assets/js/terminal.js`: SYMBOLS synced to config.py (87 clean,
  deduped, dead removed); added `loadMacro()` (gold + S&P 500 ECharts);
  `loadAll` calls it.
- `terminal/index.html` + `terminal/assets/css/terminal.css`: new Macro card
  (Gold + S&P 500 mini charts).
- `macro_data.py` (NEW): Yahoo Finance public chart API fetcher —
  XAU/USD -> GC=F (gold futures), SPX/USD -> ^GSPC; daily bars, no API key;
  `backfill_macro` / `refresh_macro` / `macro_loop`.
- `database.py`: new `macro` table + upsert/load; **all writers now retry on
  "database is locked"** and use a 30s busy timeout (the live server writes
  continuously — this killed concurrent backfills).
- `server.py`: new `/api/macro` endpoint (auto-backfills on first call) +
  macro refresh daemon thread.
- `fetch_all.py` (NEW): one command fetches everything —
  `python fetch_all.py` (or `--fast` to skip 1m/5m).

### Data populated (running in background)
- Full backfill for all 87 symbols: 15m ~104k, 1h ~67k (87/87 symbols),
  4h ~16k, 1d ~7k candles. 1m/5m still at 30d (optional followup).
- Macro: XAU/USD 330 daily bars, SPX/USD 400 daily bars.

### Coordination
- **Found 2 server.py instances running** (`.venv` + system Python) — both
  write the DB every 5s; the source of the lock storm. User should keep one.
- 6 duplicate `fetch_all` processes killed (taskkill) after nohup launches
  duplicated across venv/system pythons.

## 2026-08-14 (late 2) — Universe expansion: 42 macro instruments + 16 news feeds

### Macro universe (macro_data.py rewrite)
- Categorized Yahoo Finance universe, 42 instruments, all tickers verified:
  - **metals**: gold, silver, platinum, palladium, copper, aluminum, zinc,
    iron ore
  - **energy**: WTI crude, Brent, natural gas, heating oil, gasoline (RBOB)
  - **agriculture**: wheat, corn, soybeans, coffee, sugar, cocoa, cotton
  - **forex**: EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD,
    USD/CNY, dollar index (DXY)
  - **indices**: S&P 500, Nasdaq 100, Dow, Russell 2000, VIX, DAX, FTSE,
    Nikkei 225, Hang Seng, Euro Stoxx 50
  - **rates**: US 2/5/10/30Y treasury yields
- All 42 stored in `macro` table (~400 daily bars each) — data ready for the
  incoming UI.
- Nickel (NI=F) dropped — Yahoo returns no bars.

### News expansion (config.py)
- NEWS_FEEDS: 7 → 23 sources. +9 crypto (Bitcoin Magazine, Bitcoin.com News,
  NewsBTC, CryptoSlate, Blockworks, The Defiant, BeInCrypto, U.Today,
  AMBCrypto, CoinJournal) +7 macro (CNBC Markets, MarketWatch, Yahoo Markets,
  Kitco Gold, OilPrice.com, FXStreet, Zero Hedge).
- Verified: 5 of the 8 new feeds tested return content (85 stories); Kitco /
  FXStreet / MarketWatch feeds are dead or blocked — skipped silently, never
  fatal.

### Reviewer (reviewer.py)
- COIN_HINTS: 5 → 35 symbols — all major alts + macro assets (gold, silver,
  oil, gas, S&P 500, Nasdaq, DXY, EUR/USD, USD/JPY, treasury yields) so the
  expanded news feed scores them. Review now scores 35 coins (was 5).

### API
- `/api/macro` now returns `category` + `name` per symbol and a `categories`
  list (metals/energy/agriculture/forex/indices/rates) for the new UI.

### Data status (backfill still running in background)
- Candles: 15m ~135k, 1h ~126k (87/87 symbols), 4h ~17k, 1d pending; then
  funding + macro refresh. 1m/5m at 30d (optional followup).

## 2026-08-15 — Tool evaluation: free AI / trading tools (doc only)

- Added `documentation/tool-evaluation-free-ai-trading-tools.md`: maps the current
  codebase against the target architecture (dashboard / data pipeline /
  verification / 2 signal sources / execution) and flags gaps.
- Verdicts (as of Aug 2026): **Use** Alpaca (execution + paper trading — biggest
  gap), Gemini Flash (low-frequency AI analysis, drops into existing
  OpenAI-compatible reviewer overlay), Ollama (local AI + verification);
  **Skip** Freqtrade/FreqAI (crypto-only, wants to own the whole bot); **Depends
  on stocks expansion** QuantConnect (free tier as independent backtest/validation
  harness only).
- Proposed unified signal path: both sources emit a canonical Proposal →
  arbitrator (agreement gate + intent-hash dedup) → verification (data-quality
  gate) → single execution module (broker gateway + `orders` journal).
- No code changed.

## 2026-08-15 — Tool evaluation revision: Ollama dropped, cloud LLM backends added

- Per user: no local downloads / storage bloat. Ollama verdict changed from
  **Use** to **Skip** (multi-GB models, hardware-dependent) in
  `documentation/tool-evaluation-free-ai-trading-tools.md`.
- Added **OpenRouter / Groq / DeepSeek** as interchangeable cloud AI-analysis
  backends: all OpenAI-compatible, so they plug into the existing reviewer
  overlay via `.env` (LLM_BASE_URL/LLM_MODEL) with no code change and no disk
  usage. Recommended chain: Gemini Flash (primary) → OpenRouter free models
  (fallback) → Groq (fast verification path).
- Calls now: **Use** Alpaca + Gemini/OpenRouter/Groq · **Skip** Ollama +
  Freqtrade · **Depends on stocks expansion** QuantConnect.
- No code changed.

## 2026-08-15 — LLM overlay failover chain (Gemini/OpenRouter/Groq/DeepSeek)

Per the tool-evaluation doc, the AI review overlay is now provider-agnostic with
a failover chain — cloud-only, no local models.

- **config.py**: new `LLM_FALLBACKS` env var — `;`-separated list of
  `base_url|model[|api_key]` OpenAI-compatible endpoints tried in order after
  the primary `LLM_*` provider (OpenRouter, Groq, DeepSeek, Gemini's
  OpenAI-compat endpoint…). Keyless providers supported. Documented in
  `.env.example`.
- **reviewer.py**: `_llm_review` now walks the provider chain — first provider
  returning usable JSON wins (result tags `model` with the winner). All-fail →
  rule-based scorer + 10-min cooldown (`LLM_COOLDOWN_SECONDS`) so a dead
  provider isn't hammered every 5-min refresh. LLM gate now works with
  fallback-only setups (no primary key needed). LLM results now carry
  `generated_at` (parity with rule mode).
- **tests/test_reviewer.py** (new): config parsing, primary-down→fallback
  failover, all-fail cooldown + retry suppression, rule-mode fallback — all
  stubbed, no network/DB. 4/4 pass.
- Verified: test_system.py ALL PASSED (1.64M candles; reviewer import path
  intact via signals).

## 2026-08-15 — Bot stack v1: execution + verification + arbitration + source B

Implemented the full target architecture (per tool-evaluation doc, order of
work items 1-4). Everything is offline-safe by default; no keys needed to run.

### New modules (backend/)
- **execution.py** — the ONLY module that touches a broker. `execute_intent`
  is idempotent: `orders.intent_hash` is UNIQUE, so the same agreed signal can
  never place a second order (this is the duplicate/conflict prevention
  between the two signal sources). Brokers: `PaperBroker` (offline fills from
  our candles + risk slippage/fees), `AlpacaBroker` (plain REST, no SDK,
  paper endpoint by default), `BinanceBroker` (signed spot, reuses
  portfolio.py pattern). Sizing via risk.position_size (1% default, Kelly-capped).
- **verification.py** — data-quality gate: staleness (2.5× interval), OHLC
  candle sanity, bad-tick spike detection (move > 8× ATR without volume),
  cross-source price tolerance, plus `verify_candle_df` and `verify_intent`
  used by the bot.
- **arbitrator.py** — canonical Proposal/Intent schema + the agreement gate:
  both sources must want the same direction or no trade; confidence = weaker
  source; `intent_hash` = sha256(symbol|direction|window).
- **proprietary_signals.py** — source #2, independent of math_engine:
  Connors-style RSI(2) extremes on 15m (LONG <10, SHORT >90), no price
  levels invented. Disabled by default (`PROPRIETARY_SIGNALS_ENABLED=false`)
  → bot never trades until both sources are live.
- **bot.py** — `run_once` orchestrator + CLI (`python bot.py [--dry-run]
  [--symbol X]`): data gate → source A (signals) + source B (proprietary) →
  arbitrate → intent gate → execute.

### Config (.env, .env.example)
- `EXECUTION_BROKER` (paper|alpaca|binance), `ALPACA_API_KEY/SECRET_KEY`,
  `ALPACA_PAPER`, `BINANCE_EXEC_API_KEY/SECRET`, `PROPRIETARY_SIGNALS_ENABLED`.

### Database
- New `intents` + `orders` tables (order journal) + helpers:
  record_intent, record_order (INSERT OR IGNORE by intent_hash), load_orders,
  order_for_intent, paper_equity (INITIAL_CAPITAL + realized PnL).

### Tests (all pass, script-style)
- tests/test_verification.py (staleness, sanity, spikes, cross-source)
- tests/test_arbitrator.py (agreement, conflict, hash stability, conversion)
- tests/test_execution.py (paper fill math, idempotency, zero-size, dry-run)
- tests/test_proprietary.py (default-off safety, RSI(2) LONG/SHORT)
- Regression: test_system, test_strategy, test_reviewer all pass.
- Bot smoke: dry-run cycle over temp DB executes 0 trades (source B off) and
  holds with no_agreement — safe default verified.

### Not done (next)
- SL/TP monitoring + close logic (positions stay open after fill; journal
  has status open/closed + pnl fields ready).
- Live broker verification (needs Alpaca/Binance keys).
- Dashboard "bot status / orders" view (UI agent territory).

## 2026-08-15 — Bot stack v2: position management + bot loop + API + Orders dashboard view

Completed the remaining bot-stack items ("DO ALL AS THE BEST").

### Position management (execution.py)
- Brokers now implement `close_market` (opposite-side exit; paper fills at
  latest close paying slippage on the way out).
- `check_position` (SL/TP hit vs live price), `close_position` (realizes PnL
  = gross − entry fees − exit fees into the journal), and
  `manage_open_positions` — the SL/TP monitor that closes hits each cycle.
- database.py: `orders` table gained `exit_price`/`exit_time`/`exit_reason`
  columns (ALTER TABLE migration for existing DBs) + `update_order_exit`.

### Bot loop (bot.py)
- `python bot.py loop [--dry-run]` — scheduled cycles every
  `BOT_LOOP_SECONDS` (default 300, env-tunable). CLI now defaults to
  DRY-RUN unless `--live` is passed (safety).
- `run_once` now also runs the SL/TP monitor and reports `closed`; adds a
  cross-source price check (Alpaca feed via new market_data.py) when keys
  are configured — Binance vs Alpaca must agree within 1% or the trade is held.

### API (server.py)
- GET /api/orders (trade journal), POST /api/bot/run (dry_run default true),
  GET /api/bot/status (broker mode, source B, open positions, last cycle).

### Dashboard (web/dashboard — Orders view)
- New sidebar "Orders" view: bot status card (broker, source B, cadence,
  open positions, last-cycle summary) + trade-journal table (side, qty,
  entry/fill/exit, PnL, status, source) with a "Run bot cycle (dry-run)"
  button. Polls orders 30s / status 15s. `node --check` clean.

### Verification (real-data dry-run, temp DB copy, source B ON)
- Full 87-symbol cycle in ~10-17s, 0 errors. With the collector down the
  freshness gate correctly rejects all (stale feed) — proves the gate.
- With staleness relaxed: source B emitted 13 non-HOLD (7 LONG/6 SHORT);
  math engine stayed HOLD-heavy (conservative priors) → agreement rare.
  Agreement frequency is a tuning knob (source B thresholds / LLM overlay
  directionality), the two-source plumbing itself works end-to-end.

### Tests
- test_execution.py extended: SL/TP detection (both sides), close + PnL
  math, manage_open_positions monitor (TP close on a fresh candle). All
  suites pass (system, strategy, reviewer, verification, arbitrator,
  execution, proprietary). Server smoke test: /api/orders, /api/bot/status,
  /api/bot/run all 200.

## 2026-08-15 — Alpaca paper broker live: keys verified, market-data endpoint fixed

Per user request: configure the free Alpaca **paper** trading account and
verify the full execution path. Alpaca = the "Use" verdict from
`documentation/tool-evaluation-free-ai-trading-tools.md` (execution + paper).

### Configuration (.env — gitignored)
- `EXECUTION_BROKER=alpaca` with verified paper keys:
  `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`, `ALPACA_PAPER=true`.
- Verified read-only against `https://paper-api.alpaca.markets/v2`:
  account `PA3SN93M95YB`, status **ACTIVE**, $100,000 paper equity,
  $400,000 buying power. (Two earlier key attempts were 401 — they were
  OAuth2 Broker-API client credentials, not Trading API keys.)
- `.vscode/settings.json` (new): `python.terminal.useEnvFile: true` so
  terminals get `.env` vars injected (code already self-loads via dotenv).

### Bug fix (backend/market_data.py)
- `alpaca_last_price` called Alpaca's **retired** crypto data endpoint
  (`v1beta2/crypto/latest/trade` → 404). Switched to the current
  `v1beta3/crypto/us/latest/trades` (plural path, symbol keeps the slash:
  `BTC/USD` in both request and response).
- Verified prices for BTC/ETH/SOL/XRP — the bot's independent second feed
  (cross-source check in bot.py / verification.cross_source_ok) now works
  against the live Alpaca account.

### Tests
- test_execution.py + test_system.py pass; execution module imports clean.

## 2026-08-15 — "Why no trades?" root cause: stale data + dead listings

User reported 0 executed / 87 held on the Orders view. Debugged the full
pipeline:

### Root cause #1 — stale 1h data (fixed by repair)
- The bot's data gate (verification.verify_candle_df) requires 1h candles
  ≤ 2.5h old. The collector had been down ~21h, so every symbol failed the
  freshness check → 87/87 held on "data".
- The live collector loop heals intervals sequentially starting with 1m
  (87 symbols × 6 intervals), so 1h stayed stale for minutes after startup
  even with the server running.
- Fix: `collector.repair_gaps(conn, sym, '1h', days=3)` for all symbols
  (+86k candles, ~109s). Bot immediately produced agreed trades (dry-run):
  ADA, HBAR, OP, DASH (LONG, math_engine + proprietary agreement).

### Root cause #2 — 11 delisted symbols in config (permanent holds)
- HNT, XMR, MATIC, MKR, OCEAN, EOS, FIO, CREAM, MDT, IRIS, BLZ are
  BREAK/delisted on Binance (verified via exchangeInfo) — the freshness
  gate correctly rejects them forever (their newest candle is months/years
  old). Removed from `config.SYMBOLS` (87 → 77); MATIC renamed to its live
  successor POL/USDT (backfilled 1h, +3000 candles).

### Result
- Bot cycle: 0 errors, 77/77 symbols processed, no "data" holds.
- Remaining holds are by design: 72 no_agreement (both sources must want
  the same trade — source A is HOLD-heavy), 4 duplicate (the dry-run
  orders from earlier cycles — intent-hash dedup working), 1 cross_source
  (SUSHI: Binance vs Alpaca price 1.16% apart, just over the 1% tolerance).
- Server restarted to pick up the pruned symbol list.

## 2026-08-15 — Dashboard polish: custom interval dropdown + repo cleanup

### Custom Timeframe dropdown (Bots view)
- Replaced the native `<select id="btInterval">` (inconsistent OS-styled
  popup on the dark theme) with a custom `createSelectPicker` component in
  dashboard.js — same dark rounded style as the asset picker, keyboard
  support (arrows/enter/escape), click-outside close, aria attributes.
- HTML: `intervalPicker` + `intervalList` replace the select; CSS: `.ap-select`
  variants reuse the existing ap-list styling. `node --check` clean.

### Repo cleanup (per user: remove unnecessary files/folders)
- `legacy/` removed (retired terminal + old dashboard; fully replaced by
  `web/dashboard/`, zero code references, recoverable from git history).
- `backups/` removed (527M of stale 2026-08-14 DB snapshots; regenerable
  via `scripts/backup_db.py`).
- Kept `tradingview/` (working standalone Pine indicator) and `scripts/`
  (Windows launchers).
- Server restarted; all views + backtest API verified 200. Tests pass.

## 2026-08-15 — Dashboard: Dry test / Live test split + per-trade "why"

Per user request: the single "Run bot cycle (dry-run)" button is now two —
🧪 **Dry test** (journals intents, never touches a broker) and ⚡ **Live test**
(submits real orders to the configured broker, with a confirm dialog showing
the broker name). The bot-status meta line and cycle summary now tag the last
run as `dry-run` vs `live`.

Every executed trade now carries its **reasons** end-to-end:
- `arbitrator.py`: `Proposal`/`Intent` gain a `reasons` list; source A's
  indicator hits + math summary (regime/confluence/EV/kelly) are built in
  `proposal_from_signal_dict`; `arbitrate()` merges both sources' reasons.
- `proprietary_signals.py`: rsi2 mode explains the RSI(2) value vs thresholds
  (oversold/overbought); llm mode carries the headline reasoning.
- `bot.py`: executed summary entries include `reasons` (shown as "Why the
  system traded" in the dashboard); held trades show A vs B direction.
- `execution.py`/`database.py`: `orders` table gains a `reasons` column
  (auto-migrated) so the trade journal explains every dry-run/live order.
- Dashboard: journal table has a **Why** column (first reason + tooltip with
  all), and the cycle summary lists each executed trade with its reasons.
- Verified: test_arbitrator + test_execution pass, node --check OK, and a
  synthetic agreed trade persists merged reasons to the journal.


## 2026-08-15 — Configurable agreement gate + verified live Alpaca paper order

Per user: make dry/live testing actually productive end-to-end.

### Configurable agreement gate (arbitrator.py / config.py / bot.py)
- New env knobs: ARBITRATION_MIN_SOURCES (2 = both sources, production default;
  1 = a single high-confidence source may trade alone, for testing) and
  ARBITRATION_MIN_CONFIDENCE (default 45 = source B rsi2 flat conviction).
- Gate reworked around ACTIVE sources: a source is active only when it has a
  concrete direction AND confidence >= floor. HOLD / missing / too-weak are
  ABSTENTIONS - they neither trade nor veto. In single-source mode an
  abstaining source A (all-HOLD regime) no longer blocks source B's RSI(2)
  trade; only a conflicting active source blocks.
- Measured on real data: source A is currently HOLD on all 77 symbols (EV
  floor -0.5, confluence 0 - the math-engine parameter workstream), so RSI
  threshold tuning alone cannot create agreements. RSI(2) thresholds stay at
  25/75 (42/77 symbols produce a B signal - already good coverage); the gate
  is the lever.
- test_arbitrator: new single-source section (floor, abstention, conflict veto).

### Dry-run no longer consumes the trade (execution.py / database.py)
- Previously a dry_run journal row made the same intent a duplicate forever -
  so dry-test then live-test of the same signal was impossible. Now
  discard_dry_run() deletes the preview row before a real order, and only
  placed/error orders are idempotent. test_execution updated.

### broker_ref persisted (database.py)
- orders gains a broker_ref column (migrated) so the journal carries the
  broker's order id for reconciliation.

### Verified live order on Alpaca paper (BTC/USDT)
- Ran: ARBITRATION_MIN_SOURCES=1 ARBITRATION_MIN_CONFIDENCE=45 python bot.py
  --live --symbol=BTC/USDT
- Alpaca filled: BTC/USD buy 0.158644515 @ 62,981.83, order id
  7ccb3886-c5f5-4667-8a8c-ae9a311dbe7f. Journal row updated to match (filled,
  same fill price, broker_ref = same id). Dry test in single-source mode
  yields ~38 inspectable trades with reasons.


## 2026-08-15 — Source-A switch, live kill-switch, broker reconcile, richer journal

Per user: all three follow-ups + a more detailed Orders view (like the example
portfolio dashboard with sparklines).

### Source A: honest diagnosis + switchable generator (bot.py / config.py)
- Diagnosis: source A (math engine) is HOLD on all 77 symbols - confluence 0.0
  (confluence_score requires ALL signals to agree, any dissent zeroes it) and
  posterior pinned ~0.55 vs MIN_POSTERIOR 0.65. DIRECTIVE-001 forbids touching
  math_engine.py (owned, byte-identical) and already concluded the optimization
  workstream with an honest negative result - loosening thresholds would be
  curve-fitting. So the engines stay calibrated.
- New SIGNAL_SOURCE_A env: "math_engine" (default) or "strategy" - the
  validated mean-reversion v2 (imported, never edited) with full entry/SL/TP
  intents + filter diagnostics in reasons (ADX, z-score). Strategy currently
  fires on OG/USDT (ranging, z=2.30) - both engines are legitimately quiet in
  this trending/low-liquidity market, which the per-symbol held reasons now
  make visible.

### Live kill-switch (bot.py / server.py / dashboard)
- ALLOW_LIVE_SINGLE_SOURCE (default false): LIVE runs are blocked while
  ARBITRATION_MIN_SOURCES=1. Dry runs always allowed. Blocked state shown in
  bot status ("Live safety") and the Live-test button alerts with the fix.

### Broker reconciliation (execution.py / server.py)
- reconcile_orders(): pulls Alpaca/Binance order status and syncs journal rows
  by broker_ref (pending_new -> filled, canceled -> cancelled, fill price/time/
  fees). Runs at the start of every live bot cycle, on GET /api/orders (30s
  throttle), and via POST /api/bot/reconcile (dashboard has a Reconcile button).
- orders table gains broker_ref (migrated) so reconciliation is possible.

### Richer trade journal (server.py / dashboard)
- GET /api/orders?detail=1 enriches each row: current price, market value,
  unrealized PnL (LONG/SHORT aware) and a 24-close sparkline, plus a summary
  strip (open positions, open mkt value, dry-run rows, realized PnL).
- New table columns: Unreal, Current, Mkt value, 24h chart (inline SVG spark).
- Fixed latent bug: _sized_qty crashed on symbols with no candles (entry None);
  and B-only intents now backfill entry from the last close so dry-run and live
  rows price correctly.

### Verified
- All tests pass (arbitrator, execution, reviewer, proprietary, verification,
  strategy). Endpoints smoke-tested in-process: status fields, live-run block,
  dry-run cycle, detail enrichment (BTC row: current 63034.01, mkt value 10000,
  spark 24 pts), reconcile no-op on matched fills. Server on :8501 is the OLD
  process - needs restart to pick this up.


## 2026-08-15 - Profit-by-currency donut in Orders view
- New "Profit by currency" card (ECharts donut, dark theme) in the Orders
  view: one slice per currency, sized by |PnL| (realized + unrealized, green
  positive / red negative), center shows net PnL, side list shows symbol,
  signed PnL, share % and market value with mini bars - matching the example
  portfolio dashboard.
- /api/orders?detail=1 summary gains total_pnl + by_symbol (realized,
  unrealized, market_value, share %). Cost basis falls back to fill price
  when the intent carried no entry, so filled orders price unrealized PnL.
- Server restarted on :8501 with the new backend.


## 2026-08-15 - Portfolio now mirrors the connected Alpaca paper account
- portfolio.py gains an "alpaca" mode (highest priority): when
  EXECUTION_BROKER=alpaca and Alpaca keys are set, the dashboard reads the
  actual trading account - equity, cash, buying power, unrealized PnL and
  open positions (BTCUSD -> BTC/USDT mapping, crypto only). Falls back to
  Binance connect-exchange mode, then demo.
- Dashboard: balance card shows equity with cash / buying power / account
  number in the subtitle, connect button reads "Alpaca paper/live", asset
  cards + distribution reflect real holdings (USDT cash + BTC position).
- Verified live: equity 99,959.40, cash 90,008.28, BTC 0.158248 @ 63,034,
  distribution USDT 90% / BTC 10%, 90-day history. Server restarted on :8501.


## 2026-08-15 - Big chart with Entry/SL/TP in the Signals view

Per user: the big trading chart belongs in the Signals tab (where the signal
cards are), not the Dashboard. The Signals view is now stacked:

- Signal cards render as a horizontal scroll strip on top.
- The candlestick chart below is FULL-WIDTH and 620px tall (candles, SMA20/50,
  Bollinger bands, volume, RSI, MACD panes, zoom/pan) with the signal's Entry
  (amber dashed), SL (red) and TP (green) lines overlaid on price and a legend
  under the chart - no TradingView needed to see where the levels sit.
- Removed the Market chart card previously added to the Dashboard view
  (shared renderCandleChart kept; only Signals uses it now).


## 2026-08-15 - Orders chart now always has SL/TP (auto-derived levels)
- Root cause: source-B-only intents carry no SL/TP, and execution never
  persisted defaults - so the BTC paper position had NO stop management and
  the Orders chart drew only the Entry line.
- Fix (execution.py): every order now resolves entry (backfilled from the
  last close / fill price) and fills missing SL/TP from ATR(1h) - stop =
  1.5x ATR (0.5% floor, 5% cap), TP = 2R - before sizing, and persists them
  to the journal. The SL/TP monitor can now manage every position.
- Backfilled the open BTC position: entry 62,981.83, SL 62,666.92, TP
  63,611.65. Orders chart (and journal) now show all three levels.
- Server restarted on :8501.
