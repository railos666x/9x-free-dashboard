# CHANGELOG.md

Comprehensive chronological record of all changes. Every modification by any
agent must be appended here.

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
