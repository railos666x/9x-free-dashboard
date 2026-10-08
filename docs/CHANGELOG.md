# CHANGELOG.md

Comprehensive chronological record of all changes. Every modification by any
agent must be appended here.

---

---

## 2026-10-08 (06:50 UTC) — Manual trading from the dashboard (place AND close), gold/FX reachable in the backtest, suite green at 151

Standing user complaint: "you cant trade on dashboard with surface level ui … a
normal trader cant even navigate through it if orders are going or not", plus
"my forex i meant gold and everything they are not there in backtest". This
change makes the dashboard an actual trading surface, not a read-only report.

### Manual trading — backend ([backend/server.py](backend/server.py))
- `POST /api/manual/order` `{symbol, side|direction, qty?, entry?, stopLoss|stop_loss?,
  takeProfit|take_profit?, dry?}` and `POST /api/manual/close/{order_id}`.
- Both ride the SAME execution path the bot uses (`execution.execute_intent` /
  `execution.close_position`), so a manual order gets broker routing, risk sizing
  with the trader's own SL/TP, the R/R floor, the balance precheck, venue-side
  protection (OCO on Binance / SL-TP on MT5) and the trade journal — and a manual
  exit stands the bracket down and realizes PnL. No raw broker calls anywhere.
- `_manual_intent()` builds a bot-shaped `arbitrator.Intent` (sources=["manual"],
  confidence 100, minute-bucketed hash so a retried rejected order is not
  reported as a duplicate). Side sanity is enforced before execution: a LONG
  needs its stop BELOW entry, a SHORT above; a BLOCKED loss guard refuses new
  entries exactly like the bot (HTTP 423).
- Order of checks: 400 bad input → 423 guard → 422 sizer refusal → 502 broker
  exception → 200 `{ok, order, duplicate, dry_run}`. `dry:true` previews the
  sizing/fees and journals a `dry_run` row WITHOUT touching a broker.

### Manual trading — dashboard ([web/dashboard/dashboard.js](web/dashboard/dashboard.js), [index.html](web/dashboard/index.html), [dashboard.css](web/dashboard/dashboard.css))
- New "Manual trading" card in the Orders view: searchable asset picker (same
  universe as everything else, gold/FX/oil/indices included), LONG/SHORT toggle,
  Qty / Entry / Stop loss / Take profit (blank = risk size / market / ATR), a
  **Dry run** gate, and a Place order button that disables while the request is in
  flight (a double-click cannot double-place).
- A per-row **Close** button on every filled/open journal row (the only rows that
  can be exited) → confirm dialog → `POST /api/manual/close/{id}`, then both the
  journal and the live-trades panel refresh.
- Rejections show the SERVER's own words (e.g. "LONG stop_loss must be BELOW the
  entry price"), not just an HTTP code: manual posts use a dedicated helper that
  parses the error body and never auto-retries (an order must not be placed twice
  by a silent retry).

### Gold/FX in the backtest (the "not there in backtest" report)
- Root cause: macro symbols are collected at **1h only** while crypto has
  1m/5m/15m/1h, so a 15m gold backtest legitimately had 0 bars and the panel
  showed an empty result with no explanation. `/api/backtest` now falls back to
  the 1h frame for such symbols and returns `data_note`, which the panel renders
  as an amber banner first ("XAU/USD is collected at 1h only — showing the 1h
  backtest instead of 15m").
- Verified live after restart: `XAU/USD 15m` → `bars 2803, trades 11` plus the
  note; `XAU/USD 1h` → 2000 bars / 10 trades.
- `/api/watchlist` also appends every collected `config.TRADABLE_UNIVERSE` symbol
  (61 macro rows, 1h fallback), so gold/FX are searchable in the pickers instead
  of only the crypto `config.SYMBOLS` list.

### Verification
- Full suite: **151 passed, 0 failed** — twice in a row, and again with the
  modules reordered (`test_forex` → `test_manual_trading` → `test_forex`).
- Two test-ordering flakes were FIXED at the cause, not skipped:
  `test_manual_trading` repointed `config.DB_PATH` at module import, and since
  pytest imports every test module during collection the leak made
  `test_forex.py::test_overview_runs_against_real_db` read an empty scratch DB
  (`pairs_with_data >= 15` → 0). It now saves/restores `config.DB_PATH` per test.
  The manual-close test also used a fixed `intent_hash`, and `record_order` is
  `INSERT OR IGNORE` (returns 0) — a leftover row made it close "#0" and fail;
  it now uses a unique hash per run and asserts the row really inserted.
- Live smoke (no real orders placed): bad direction → 400, LONG stop above entry
  → 400, missing symbol → 400, unknown close id → 404, `XAU/USD SHORT dry` → 200
  (qty 1.0, mt5, status `dry_run`), `BTC/USDT LONG dry` → 200 (qty 0.0388,
  binance, ATR SL/TP) and the dry-run button driven through the real UI.
- `.gitignore` now ignores `*.log` (server redirect targets reappear on every
  restart) and the three throwaway probe scripts were deleted.
- Cache tags bumped to `?v=20261008c`.

## 2026-10-07 (07:45 UTC) — "no trades of today even after trying buttons" reproduced: journal empty-state lied; now truthful and period-aware

User re-report of the earlier complaint. REPRODUCED in the live tab by
clicking the Today pill exactly as the user would:
- The CLOSED-trades panel was never broken (this session's earlier fix):
  with `exitFrom=2026-10-07` the API returns exactly 2 rows (#408 EUR/USD
  exit 06:37 UTC pnl −7.48, #410 EUR/USD exit 07:15 UTC pnl −3.81) and the
  UI rendered both cards on Today.
- The ORDERS JOURNAL TABLE windowed by OPEN time is legitimately empty —
  no trade was OPENED today (last fill #409 was 2026-10-06) — but its
  empty state said "No orders yet.", which reads as "the bot did nothing
  today" and is what the user actually saw. Misleading copy, not missing
  data; the bot acted today via 2 MT5 SL/TP exits.
- Fix in [web/dashboard/dashboard.js](web/dashboard/dashboard.js)
  (`renderOrders`): the empty state is now period-aware and truthful —
  "No new trades OPENED today — check 'Closed trades' below for exits in
  this window (exits are listed by CLOSE time)".
- Verified live: Today → honest message + 2 closed cards below; 7d →
  10 journal rows + 19 closed cards; All → 236 rows; preset round-trip
  back to Today re-renders both panels. `node --check` clean; suite
  145/145 unchanged (no backend change this time). Cache tag bumped to
  `?v=20261007c`.

## 2026-10-07 (07:05 UTC) — Real-time alert push (banner + toasts) LIVE-VERIFIED end-to-end; dismiss-stick bug fixed; "no trades of today" closed-trades refresh fix; single-slice donut polish; OCO tests de-flaked (testnet 502)

User request: "Add real-time dashboard alerts between daily digests: toast/banner
when a leg goes NAKED, the loss guard trips, or the digest thread fails to
write." Standing instruction: verify in the live browser, document everything.

**Real-time alerts backend** ([backend/server.py](backend/server.py))
- New `GET /api/alerts` — computed alert feed, cached 5s server-side:
  - NAKED legs: every open journal row (status filled|open) classified by
    `_order_protection` (hoisted to module level so /api/positions and
    /api/alerts share one truth): `software-only` → WARNING toast ("no venue
    bracket survives a bot restart"), `none` → CRITICAL banner ("no stop at
    all"), venue-oco / venue-sltp / unknown → silent.
  - Loss guard BLOCKED → CRITICAL (id `loss-guard`, banner turns amber via
    the `.alert-banner.guard` class).
  - Digest-thread failure → WARNING: the digest loop stamps
    `_DIGEST_STATE = {last_attempt, last_ok, last_error}`; `last_error`
    while `last_ok` < `last_attempt` surfaces as id `digest-failed` and
    CLEARS itself on the next successful write.
- Response `{alerts:[{id, kind, severity, symbol, msg}], count, at}`.

**Alerts frontend** ([web/dashboard/dashboard.js](web/dashboard/dashboard.js),
index.html, dashboard.css)
- Persistent banner (`#alertBanner`) for criticals + toast host
  (`#toastHost`, 8s life) for warnings; `startAlertPolling()` rides the
  existing live-poll boot and polls every 15s. Dismiss is per-alert-id and
  re-arms when the condition clears, so a NEW incident always re-alerts.

**LIVE END-TO-END VERIFICATION (synthetic naked rows, deleted afterwards)**
- Host restart had killed the server → restarted (PID 17760). Inserted
  `status=filled, sl/tp NULL` journal row #411: /api/alerts returned
  `naked:411 critical` within one cache cycle AND the red banner rendered
  in the browser with the right message + Dismiss button.
- BUG FOUND by this verification: the banner re-rendered on EVERY poll,
  so Dispress only hid it until the next 15s tick — "Dismiss" was a lie.
  Fix in `pollAlerts()`: the banner branch filters out dismissed ids
  (`visibleCriticals`) and only hides on empty/all-dismissed. Re-verified
  live: dismiss now STICKS across polls; the next synthetic incident
  (row #413) re-alerts correctly; clearing the row clears the banner and
  re-arms. Suite untouched by this (no JS tests) — the browser IS the test.
- SECOND issue found live: the page was running STALE dashboard.js after
  the fix — the static-asset cache-bust tag in index.html had drifted
  (`?v=20260915a`, weeks old), so Chromium kept serving the old heuristic-
  cached bundle indefinitely. Bumped to `?v=20261007a`. NOTE to self:
  EVERY dashboard.js/css change now needs a `?v=` bump or users get silent
  stale code.
- Warning-toast path was verified in the same synthetic cycle earlier
  (software-only row → `[!]` toast, 8s fade). All synthetic rows
  deleted + VACUUM; the real book (408/409/410) stays protected: /api/alerts
  count=0 with the live book OPEN — correctly silent.

**"No trades of today" fixed** (leftover from earlier session, now verified)
- Root cause: `applyTradePreset` / `applyCustomRange` refreshed ONLY the
  journal table (`loadOrders`), never `loadClosedTrades()` — so the closed
  list + PnL donut showed stale data while the journal moved; traders
  honestly believed "there are no trades today". Both preset and custom
  range now refresh BOTH lists; the `api()` fetch helper sends
  `cache:"no-store"` so filtered API responses can't be served from cache.
- Backend period filter re-verified: exitFrom=2026-10-06 returns #407
  (closed 2026-10-06T07:46) and the frontend now SHOWS it.

**Single-slice donut polish** (last of the donut complaints)
- A single-position ring no longer renders the seam notch at 12 o'clock
  (a 100% pie border paints a notch — borderWidth is 0 for single slices)
  and radius is 48%/68% so the ring looks intentional.
- The fake "EUR/USD 100%" label is suppressed (formatter returns "" at
  >= 99.5%) and the symbol + window is shown in the center title instead
  (`title.subtext = EUR · Last 7 days`).

**OCO tests de-flaked** — [tests/test_oco.py](tests/test_oco.py)
- Suite went 143/145: two place_oco tests expected "110.00" (live tick
  grid) but got "110.00000000". Root cause: BINANCE_BASE is the SPOT
  TESTNET and its /api/v3/exchangeInfo is currently 502ing (verified
  directly; testnet only — live spot answers tickSize=0.01 fine), so
  `_tick_size`→0.0→8dp fallback. The tests were silently dependent on
  testnet uptime. Fix: `_tick_size` is pinned (lambda) inside those two
  tests — hermetic, still exercises the real rounding/leg-flip logic
  (`test_round_price_snaps_to_tick_grid` covers the formatter itself).
- Suite: **145 passed** (was 138; alerts added +7 on 2026-10-06, the two
  de-flakes keep the count, all green 2026-10-07).

**Gold / FX / metals finally visible + backtestable** (user complaint:
"my forex i meant gold and everything they are not there in backtest")
- ROOT CAUSE: `/api/watchlist` built its list from `config.SYMBOLS`
  (crypto-only) + `/USDT` foreign tickers — but the bot's actual MT5 book
  is the wider `TRADABLE_UNIVERSE` (59 macro symbols: XAU/XAG/platinum/
palladium, FX majors/crosses, WTI/Brent/nat-gas, indices + BTC/ETH CFDs).
  EVERY asset picker got its options from that list, so gold & friends were
  unreachable in ALL panels while 166 symbols of 1h candles sat in the DB.
- Fix ([backend/server.py](backend/server.py)): the watchlist now also
  prices every collected `TRADABLE_UNIVERSE` symbol from its candles
  (last vs prev close change + 40-point sparkline, RSI skipped — no extra
  indicator pass on a polled endpoint). Macro feeds are 1h-only: an empty
  non-1h request falls back to the 1h candles so the side bars/donuts
  don't black out the sleeve. Live: 77 → 138 rows, 61 macro; XAU/USD
  row carries price 4156.5 + spark; `tradable: true` flows through.
- Searchable picker lists now include gold/FX in ALL three pickers
  (backtest Bot panel, Signals, Orders position chart) — verified: search
  "xau" → row renders.
- Backtest quick chips grew the macro heads: BTC ETH XAU/USD EUR/USD
  WTI/USD (one click instead of hunting).
- UI-VERIFIED END-TO-END: clicked the gold chip in the Bots view, ran the
  backtest — full metrics painted (XAU/USD 1h: 2773 bars, 11 trades, WR
  45.5%, PF 0.80, sharpe 1.61, maxDD −4.65%: mean-reversion on gold is a
  LOSER right now, honest numbers, not a crash or an empty screen).
- Type-safety sweep for the new row shapes: `symOf` already renders macro
  symbols verbatim (BTC chip → "BTC", XAU chip → "XAU/USD"), money
  formatting handles non-$ notional prices; no NaN paths found in the
  touched hunks (donut, trade list, picker).

**Trader flow strip** (user complaint: "a normal trader can't even navigate
through it, if orders are going or not")
- Pinned strip under the topbar, visible on EVERY view
  ([index.html](web/dashboard/index.html) + [dashboard.css](web/dashboard/dashboard.css)):
  loop state (green dot live / RED stopped / AMBER stale when no cycle has
  landed within 2× the configured cadence), last-cycle age ("4s ago",
  "4m ago"), per-cycle action census ("+2 / 1 closed / 0 err" — tooltip
  counts held signals), open-order count, and a one-click hop to the
  Orders view. Renders on the existing 15s /api/bot/status poll — no
  extra endpoint or request weight.
- Verified live: dot `live` + "LIVE (paper)", "4m ago", "+0 / 1 closed /
  0 err", open=1, jump button navigates to #view-orders.

**Ops note** — Freebuff host restarts keep killing the dashboard server;
restarted 3× this session from the standard recipe. Consider a watchdog
(NSSM service or a supervisor thread) so :8501 survives host restarts.

## 2026-10-06 (08:45 UTC) — Run-window report folded into a DAILY auto-digest; OCO #457561 verified (it FIRED — SL leg) + fired-leg detection bug fixed; trailing stop now re-places the venue bracket; dashboard naked-leg panel

User requests: "Fold the run-window report (win-rate trajectory, per-symbol
table) into the daily digest so it lands every day without a manual script
run"; "Verify the first post-cutover Binance OCO bracket"; "Keep
trailing-stop logic and the venue OCO in sync"; "Add a dashboard panel
showing which open positions have venue-side protection vs
software-monitoring-only"; "Re-run run_report and update the CHANGELOG
with whether the 70% rolling win rate is holding or regressing".

**Run-window report → daily digest (no manual runs)**
- Computations extracted from [scripts/run_report.py](scripts/run_report.py)
  into the importable pure module [backend/run_stats.py](backend/run_stats.py)
  (wr_stats / per_symbol_table / trajectory / window_summary — ZERO print/io,
  connection passed in). The CLI keeps its exact printed output as a thin
  wrapper; `need_wins` is now an int (was rendering "5.0").
- [scripts/daily_digest.py](scripts/daily_digest.py) gained
  `run_window_section`: markdown Run-window census + Win-rate trajectory
  table (all-time / last-10/20/50, rolling-10 buckets, trend, path to 70%)
  + per-symbol weekly table, and flags an ANOMALY when the rolling last-20
  win rate drops below 50%. `main()` returns the problems list instead of
  sys.exit (callers decide the exit code).
- The server now runs the digest ONCE PER UTC DAY: a `daily-digest` daemon
  thread in [backend/server.py](backend/server.py) (`_start_live_threads`)
  with startup CATCH-UP — if today's `## Digest YYYY-MM-DD` section is
  missing from [MONITORING-LOG.md](docs/MONITORING-LOG.md) it appends
  immediately (host restarts are frequent), then sleeps to 00:05 UTC.
  Failures print and retry next loop; they never kill the thread.
  LIVE-PROOF: first restart appended "## Digest 2026-10-06 08:02 UTC"
  automatically with the full trajectory + per-symbol tables.

**OCO #457561 verification — the bracket FIRED (and caught a bug)**
- Asked for EXECUTING/2×NEW; reality was better: listStatusType=ALL_DONE,
  STOP_LOSS leg FILLED at the real venue fill 2591.42 (trigger 2649.33,
  journal #407 ETH LONG from 2715.46 → pnl −156.99, fees 3.17, slip 0.32),
  TAKE_PROFIT leg EXPIRED. The venue-side protection did exactly its job.
- BUG FOUND live: `oco_status` mapped fired_leg only when
  `listStatusType == "EXECUTED"`, but the live terminal state is
  `ALL_DONE` — fired_leg came back None and reconcile would have MISSED
  the close (the position pass could then ghost-close it). Fix in
  [backend/execution.py](backend/execution.py): a FILLED leg IS the
  execution proof — the mapping no longer gates on the list-status
  spelling (ALL_DONE with NO filled leg still reports not-fired).
- #407 closed truthfully through the fixed path (`close_position` →
  fired-bracket journaling): exit_reason='sl' at 2591.42, no market close
  issued (coins already gone). Crypto sleeve is flat; the next Binance
  fill (#409 BTC/USDT LONG) immediately rested a NEW bracket — OCO
  #472573 confirmed via /api/positions (protection=venue-oco).

**Trailing stop ↔ venue OCO sync**
- New `_sync_oco_with_stop` in [backend/execution.py](backend/execution.py):
  when `_update_trailing_stop` moves a journal stop on a Binance row with
  `oco_ref`, the OLD bracket is cancelled and re-placed at the trailed
  stop (same TP, exit side flipped per direction, qty re-floored to
  LOT_SIZE) and the NEW orderListId is stamped on the row. Best-effort:
  cancel reports already-gone → leave it to reconcile; re-place FAILS →
  journal oco_ref=NULL (truthful: no bracket any more) + loud print;
  the trail sync can never error the manage cycle.
- Test caught a real bug pre-merge: my exit side was flipped (BUY for a
  long) — a LONG exits by SELLING, matching `_attach_oco`.

**Dashboard UI fixes (user: "FIX UI")**
- PnL donuts: 26-currency MT5 ring rendered micro-slices as a fan of
  black spokes (screenshot). Slices < 3% of gross now group into ONE
  quiet grey "Other ×N" slice in the RING (breakdown list below still
  lists every currency); live: 26 slices → 6 named + Other ×17 (+$114).
- Inside-ring labels got a dark text border (red-on-red "ETH 57%" was
  unreadable) and +1px size; labels < 7% stay hidden as before.
- Verified in the live browser via echarts option inspection (slice
  names/colors/Other aggregation/label border all applied; no console
  errors). Screenshot capture unavailable in this environment (preview
  compositor produced no frames) — DOM-level verification used instead.

**Dashboard naked-leg panel** ([web/dashboard/dashboard.js](web/dashboard/dashboard.js))
- `/api/positions` now carries `oco_ref` + `protection` per open leg:
  `venue-oco` (exchange OCO resting) / `venue-sltp` (MT5 basket rows) /
  `software-only` (journal levels that die with the bot) / `none` /
  `unknown` (manual venue positions). Each Live-trades card shows the
  badge (green OCO / green MT5 SL-TP / amber SW ONLY / red NAKED) and the
  header counts naked legs: "5 open · all venue-protected · 2 unknown" —
  unknowns are counted separately so "all venue-protected" never
  overclaims. Badge CSS in [dashboard.css](web/dashboard/dashboard.css).
- LIVE-VERIFIED in the browser: badges render (2× MT5 SL/TP, 1× OCO on
  #409, 2× ?), meta honest, no console errors.

**70% rolling win-rate verdict (user-requested CHANGELOG update)** —
HOLDING: last-20 = 75% (needs 0 more wins inside a rolling 20 to touch
70%), last-10 = 80%, rolling-10 buckets 10 50 60 50 50 60 70 70 80 →
first-half 42.5% → last-half 66.0% (improving). The #407 SL loss (−156.99)
pulled ALL-TIME down to 55.6% (n=90, PF 1.55) — the rolling window is the
honest trend and it is above target.

**Tests — 138 passing** (was 127). +8 run-stats/digest tests
([tests/test_run_report.py](tests/test_run_report.py): wr_stats math,
per-symbol min-2 filter + ordering, trajectory buckets/trend/need-wins,
digest fold-in rendering + anomaly flag), +3 trail-sync tests
([tests/test_oco.py](tests/test_oco.py)), oco_status ALL_DONE case added.
Test-rot fixes: hard-coded dates in tests/test_costs.py cost-table
fixtures now relative (same time-bomb class the gov tests hit).

## 2026-10-06 (06:55 UTC) — Balance precheck (fail-fast unfundable intents), OCO live-schema fixes, old trades closed, gov-officials shadow feed prototype live

User request: "Reduce crypto-sleeve notional or add a balance precheck so
intents like #403 (insufficient free XRP on binance) fail fast with a clear
reason instead of erroring at submit." Plus the standing follow: close old
trades "so system dont get confused", and the previously approved gov-feed
shadow prototype brought live.

**Balance precheck — the #403 fix** ([backend/execution.py](backend/execution.py))
- `BinanceBroker.fundability(symbol, side, qty, price) -> str | None`:
  None = fundable, reason-string = PROVABLY unfundable (the same message
  `_submit` would raise: "binance: insufficient free X (have H, need N) —
  fund the account or lower risk size"), `"hold"` = balance endpoint
  unavailable (fail-open; never block all trading on a flaky metadata
  call). BUY needs free quote ≥ notional; SELL needs free base ≥ qty.
  Uses the shared 30s `/account` cache (`_account_snapshot`) so UI polling
  + the precheck don't double-hit signed-call rate limits.
- Wired into `execute_intent` AFTER `discard_dry_run` + `get_broker`,
  BEFORE `place_market`: hybrid resolves the sub-broker via
  `_get_broker_for`; on a reason-string it returns
  `{order: None, error: fund}` with NO journal row — the intent dies with
  a clear reason instead of an 'error' row at submit (#403's failure
  mode). Runs after the dry-run return so sizing previews never depend on
  venue reachability, and checks the honest final `size` (post-sizing).
- Non-Binance brokers (no `fundability`) are untouched.

**OCO live-schema fixes** (correction to the 10:00 entry below — the flat
params it describes are REJECTED by the current testnet API)
- Live placement 400'd with −1102 "Mandatory parameter 'aboveType' was not
  sent": Binance moved OCOs to the NEW leg-based schema. `place_oco` now
  sends `aboveType`/`belowType` = TAKE_PROFIT / STOP_LOSS MARKET legs
  (market-on-trigger, no limit-price buffer needed) with
  `aboveStopPrice`/`belowStopPrice`; SELL exit (long): above=TP@tp,
  below=STOP_LOSS@stop; BUY exit (short): mirrored. The
  `BINANCE_OCO_STOP_LIMIT_PCT` knob is REMOVED from config (obsolete).
- `oco_status`: GET `/api/v3/orderList` accepts orderListId ONLY — sending
  `symbol` too 400s with −1104 "Not all sent parameters were read; read 4
  but sent 5". The list response's orders[] is SLIM ({symbol, orderId}),
  so each leg is enriched via a per-order `GET /api/v3/order` (2 extra
  signed calls) to read its type (STOP_LOSS→'sl', TAKE_PROFIT→'tp') and
  status. DELETE keeps symbol+orderListId (works).
- **Live proof:** OCO #457561 resting on ETHUSDT for journal #407 (LONG
  qty=1.2223, sl=2649.3285, tp=2853.123) — legs confirmed NEW/NEW via the
  enriched oco_status (orderIds 8712795 STOP_LOSS, 8712796 TAKE_PROFIT);
  `oco_ref` stamped on the row.
- Tests: [tests/test_oco.py](tests/test_oco.py) now 18 (leg-flip test for
  BUY exit + oco_status rewritten for slim-legs + per-order enrichment).

**Old trades closed (journal hygiene, user request)**
- #405 EUR/USD SHORT → closed +7.31 (exit 1.12038, slip 2.69 stamped);
  #406 BTC/USDT LONG → closed +3.62 (exit 86039.74, slip 0.33). Both via
  `execution.close_position(..., reason='manual_cleanup')` with REAL venue
  fills (MT5 exact-ticket / Binance market close with step-floor +
  fundability prints) — fees and slippage truthful in the journal. The
  only live crypto-sleeve row is now #407 (ETH, bracketed by #457561).

**Gov-officials shadow feed prototype — LIVE (paper-only)**
- New module [backend/gov_analyst.py](backend/gov_analyst.py) + gov pool in
  [backend/news.py](backend/news.py) + [backend/database.py](backend/database.py):
  7 live HTTP-probed official feeds (Fed, FOMC speeches, BoC, BoE, ECB,
  SEC, CFTC; BoJ/RBA/SNB/US-press/OFAC/WhiteHouse/EC excluded after
  probing 404/403/empty) fetch into a SEPARATE `gov_news` table (never
  `news`), refreshed by a daemon loop every `GOV_FETCH_SECONDS` (180).
- The shadow cycle runs ONLY official headlines through the SAME pipeline
  as the live ai_news path (dedup → Groq scorer → direction thresholds →
  distinct-outlet corroboration gate) and journals PROPOSALS ONLY into
  `test_trades` with `ai_model='groq-gov'` (paper-only, guaranteed: zero
  contact with `orders`/brokers — test-enforced). Stale shadow rows close
  at last price after `GOV_SHADOW_MAX_HOLD_BARS` (72) as
  'shadow_expired'. Two-week evaluation before any live decision.
- `GET /api/gov-shadow` returns {enabled, feeds, recent_official[],
  shadow_trades[], stats}. Future-date guard in `gov_headlines`
  (pre-published notices like BoC's 2027 calendar entries skip) — this was
  the last edit needing the restart below.

**Tests — 127 passing** (96 base + 20 OCO + 11 gov). Test-only fix:
[tests/test_gov_feed.py](tests/test_gov_feed.py) fixtures used hard-coded
2026-10-05 timestamps which went stale overnight (freshness gate correctly
filtered them); now relative (`_iso(hours_ago)`). No production behavior
changed by that fix. New precheck coverage in
[tests/test_oco.py](tests/test_oco.py): fundability BUY/SELL/'hold'
semantics + execute_intent fail-fast (unfundable intent → no venue call,
no journal row, clear reason; fundable intent unaffected).

**Ops**
- Server restarted with precheck + gov guard (Start-Process quirk recurs,
  verified separately): PID 9676 on :8501, auto-loop live, GOV shadow
  thread running (first pass: "fetched 95, 51 new, shadow proposals: 0"),
  `/api/gov-shadow` serving 7 feeds + recent official rows, #407 open with
  oco_ref=457561, #405/#406 closed. Gov proposals at 0 so far — they need
  directional + corroborated official releases; watch the
  "[GOV] ... shadow proposals:" log lines.

## 2026-10-05 (10:00 UTC) — Binance venue-side OCO brackets: spot legs get MT5-grade protection

User request: "Add Binance testnet OCO stop-loss/take-profit orders for open
spot crypto legs so they have venue-side protection like the MT5 basket."

Until now a Binance spot position was protected ONLY while this process ran
(`check_position` in the 300s cycle): bot offline, host rebooted, or the loop
thread wedged on an MT5 IPC call = a NAKED position at the venue. The MT5
basket already has deal-attached sl/tp; this gives crypto spot the same
guarantee with a resting exchange OCO list.

**BinanceBroker ([backend/execution.py](backend/execution.py))**
- `place_oco`: signed POST `/api/v3/orderList/oco` — STOP_LOSS_LIMIT
  (trigger = journal SL, limit = SL ∓ `BINANCE_OCO_STOP_LIMIT_PCT` 0.5%
  so a fast tape can't leave the stop resting unfilled) +
  TAKE_PROFIT_LIMIT (journal TP), GTC, exit side auto-flipped for
  SHORTs. 3-attempt transient retry + −1021 clock-drift re-sign (same
  tolerance as market orders). Verified endpoints against Binance's
  swagger: POST on `/orderList/oco`, GET/DELETE on `/orderList`.
- `_tick_size`/`_round_price`: prices snapped to the symbol's PRICE_FILTER
  tick grid (HALF_UP via Decimal) — an off-grid stop would get −1111 and
  reject the WHOLE list. `_signed` centralizes the HMAC signing used by
  all three new calls.
- `cancel_oco`: DELETE `/api/v3/orderList`; −2011 'unknown order list'
  counts as gone (already fired/cancelled); a REAL cancel failure RAISES —
  the caller must know the bracket survived.
- `oco_status`: GET `/api/v3/orderList` → {status, fired_leg, orders[]};
  fired_leg = 'sl' | 'tp' when the list EXECUTED and which leg FILLED.
  `order_fill_price`: real executed price of a fired leg from
  `/api/v3/myTrades` (fallback: last candle close).
- `_attach_oco` (from `place_market` after a fill): bracket rests
  BEST-EFFORT by design — a failed/skipped placement prints and degrades
  to today's software-only behavior; it NEVER turns a filled entry into
  an error (the position exists at the venue regardless). Levels must
  strictly bracket the fill (sl < fill < tp for LONG) or the venue would
  reject the list; qty re-floored to LOT_SIZE. `oco_ref` (orderListId) is
  returned on the fill only when the list actually rested — `NULL` in the
  journal always means 'no venue bracket'.

**Lifecycle wiring**
- `execute_intent` journals `oco_ref` on the row (new TEXT column in
  [backend/database.py](backend/database.py) CREATE + TEXT-column
  migration; `record_order` writes it).
- `close_position` is the trade's brain: it CANCELS the resting bracket
  before a market close (a live bracket would otherwise sell the next
  refill), and if the venue ALREADY fired a leg it journals the exit with
  the TRUE leg ('sl'/'tp') at the OCO's real fill price — never a market
  close into coins that are already gone. Fired-leg recursion passes
  `oco_ref=None` so the status query can't loop.
- `_reconcile_orders_impl` gained a fired-OCO pass: an open row with a
  bracket whose list EXECUTED while the bot was offline closes as
  'sl'/'tp' at the OCO fill price (fees estimated at `FEE_BPS`, exit
  slippage estimate stamped) instead of the position pass ghost-closing
  it broker_closed at a candle price.
- Latent bug found by the new tests: `broker_down` was only initialized
  inside the hybrid/mt5/alpaca position-pass guard, so a standalone
  binance broker crashed reconcile at the final skip-notice check
  (UnboundLocalError). Hoisted the initialization.

**Config** ([backend/config.py](backend/config.py))
- `BINANCE_OCO_ENABLED` (default on) and `BINANCE_OCO_STOP_LIMIT_PCT`
  (0.5), both env-overridable.

> **Live-schema correction (2026-10-06):** the flat price/stopLimitPrice
> params and the `BINANCE_OCO_STOP_LIMIT_PCT` buffer described above were
> replaced by the leg-based `aboveType`/`belowType` schema after live
> testing (−1102); the GET endpoint is orderListId-only. See the
> 2026-10-06 entry. Knob removed from config.

**Tests (113 passing, was 96)** — new [tests/test_oco.py](tests/test_oco.py),
17 tests, zero network (signed calls faked on the instance):
- tick-grid price formatting (HALF_UP), OCO request params (side/quantities
  /rounded prices/GTC), rejection → BrokerError, transient retry then
  success,
- cancel semantics (true; −2011 = gone; real failure raises; malformed ref
  = false), fired-leg detection sl/tp, myTrades fill-price lookup,
- entry attach: bracket rests after a fill with exit-side flip + 0.5%
  stop-limit buffer; skipped when levels don't bracket the fill or qty is
  below minQty after step-floor; disabled knob; failed attach never errors
  the fill,
- journal: `oco_ref` persisted on the row; close cancels the bracket before
  the market close; a fired bracket is journaled truthfully (sl at 94.98,
  no market close issued); reconcile closes a fired row as 'tp' with
  correct pnl/fees/slip and ignores rows without `oco_ref`.

**Ops**
- Server restarted with the new code (Start-Process quirk recurs, verified
  separately): PID 12020, first cycle 0 executed / 160 held / 0 errors,
  `oco_ref` migration confirmed on the live DB. No open Binance legs at
  restart → the next crypto fill places the first live bracket. After the
  first OCO-bearing fill, verify on the testnet UI / `oco_status` that the
  list is EXECUTING with both legs NEW.

## 2026-10-05 (09:25 UTC) — Run-window performance report (owed analysis delivered)

New one-shot script [scripts/run_report.py](scripts/run_report.py)
(re-runnable, BASE_ID=400) answering the standing analysis debt: win rate,
per-symbol stats, cost drag, gate/loop notes, and the 70% win-rate
trajectory. Output archived in
[MONITORING-LOG.md](docs/MONITORING-LOG.md) (2026-10-05 section).

**Headlines (all 87 journal closes are deal-history-reconciled, i.e. truthful)**
- All-time: wr=55.2%, net=+1071.54, PF=1.70.
- **Trajectory IMPROVING**: rolling-10 win-rate buckets
  10→50→60→50→50→60→70→70; first-half mean 42.5% vs last-half 62.5%.
  **last-20 wr=70.0% — the 70% target is touched** (last-10=90%,
  last-50=66.0% PF=4.92). Driver is asymmetry: avg win +21.49 vs avg loss
  −9.26 (~2.3×) in the last-20, not raw hit rate.
- Run window (id>400): 3 closed / 1 error, wr=66.7% net=+150.82. #404
  BTC/USDT time_decay_exit +105.58 saved the BTC bucket (2/6 wins but
  +83.74 net). Best 7d symbols: AUD/JPY +328.16 (2/2), EUR/USD +43.28
  (3/3); laggards USD/JPY −199.06 (7d incl. pre-window), GBP/USD −43.68.
- 7d both venues: n=52 wr=63.5% net=+1330.04; costs negligible so far
  (fees 0.00 on recent rows, slip 0.17 = single cost-layer estimate).
- Gate profile: conservative-hold dominates (~160 held per 300s cycle
  under SENTIMENT ±0.40 / CONF_FLOOR 0.65); #403 was a funding error, not
  a gate failure. Cost-veto idle so far — upstream gates bind first.
- Caveats: tiny window sample (3 closes); 70% is a rolling state over the
  improving Sep–Oct stretch, not a stabilized rate — keep re-running
  run_report.py as closes accrue.

Also this session (ops): server was DOWN after the host restart; correct
relaunch form discovered (see 09:05 entry below). Full suite re-verified:
96 passed post-restart.

## 2026-10-05 (09:05 UTC) — Post-restart ops: server relaunch + first cost-stamped close

**Ops**
- Server found DOWN after the host restart (`netstat :8501` empty, API no
  response). First relaunch attempt with `python -m uvicorn
  backend.server:app` failed fast: `ModuleNotFoundError: No module named
  'config'` — backend modules are imported FLAT (`import config`), so the
  process must start as `python backend/server.py` from the repo root (puts
  `backend/` on sys.path). Relaunched with the documented Start-Process
  recipe (shell-timeout quirk recurs; verified separately). Save this launch
  form — it is the correct one.
- Post-restart: PID 736 listening on :8501, first cycle 0 executed /
  160 held / 0 errors in 11.34s, watchdog quiet, live threads up.
- Run window development: **#404 BTC/USDT LONG closed via time_decay_exit at
  +105.58** — the first close stamped by the cost layer
  (`slippage_cost = 0.169` round-trip estimate; entry predates the column,
  so this is exit-side estimate only). No new fills since; no cost-veto
  rejections observed yet (gates are the binding constraint: 160/160 held
  per cycle; note vetoed intents create NO journal row by design, so veto
  activity is only visible in loop logs).

## 2026-10-01 (09:35 UTC) — Execution-cost layer: slippage + fees journaled
### on every close, per-venue weekly cost table, cost-clearance entry veto

User request: "Journal fees and slippage estimates on every order close and add
a per-venue weekly cost table to the daily digest, then veto entries whose
expected edge doesn't clear 2x round-trip cost."

**Schema ([backend/database.py](backend/database.py))**
- New `orders.slippage_cost` REAL column (CREATE + migration; REAL not TEXT).
  Holds the running ROUND-TRIP slippage total: entry estimate written at
  fill, exit estimate ADDED at close (`update_order_exit` now accumulates:
  `slippage_cost = COALESCE(slippage_cost,0) + COALESCE(?,0)`).
- Journal `fees` already carried broker commissions — unchanged.

**Estimation model ([backend/execution.py](backend/execution.py))**
- `_estimate_slippage_cost(symbol, qty, fill_price)`: venue-tier bps
  (`risk.slippage_bps`) × notional. Honest scope: none of the fill paths
  carries a PRE-ORDER reference price today, so every number is an estimate.
  The exception proves the direction: `PaperBroker._fill` now returns
  `ref_price` (the mid it simulated from) and `execute_intent` journals the
  MEASURED |fill − ref| × qty when a fill carries one — same column, no
  schema change needed as other paths adopt it.
- Entry: stamped in `execute_intent` during sizing (fill-time). Exit:
  `close_position` stamps the exit estimate at close; the reconcile position
  pass stamps it on broker-side closes from the deal history.

**Cost-clearance veto (entry gate)**
- `_cost_clearance(intent, entry)`: refuse entries whose planned WINNER —
  the TP distance — is below `COST_VETO_MIN_EDGE_X` (default **2.0**) × the
  estimated round-trip cost (`risk.total_cost_bps`: slippage + taker fee,
  both sides) × entry. 2× covers cost variance across the round trip
  (news-tape slippage spikes, requotes).
- Wired into `_sized_qty` BEFORE any sizing math; veto reason surfaces as the
  execute_intent `error` (and on dry-run previews). Degradation is open:
  missing entry/TP, `COST_VETO_ENABLED=false`, or any veto-math hiccup → no
  veto, never a false block. Knobs: `COST_VETO_ENABLED` (default on),
  `COST_VETO_MIN_EDGE_X` (2.0).

**Daily digest — per-venue weekly cost table
([scripts/daily_digest.py](scripts/daily_digest.py))**
- `cost_table(cur, lines, problems)`: `### Costs by venue (7d, closed round
  trips)` — per venue: n, fees, slippage, total cost, gross PnL (net +
  costs added back), net PnL, and **cost drag** = cost/gross. Weekly window
  on `COALESCE(exit_time, fill_time)`. Anomaly raised when a venue's cost
  drag > 25% of gross (fee bleed alarm). Verified live on the real DB
  (mt5 40 round trips, binance 10; legacy rows show 0 slip — pre-column).
- The digest now runs `database.init_db` (idempotent) before querying so
  schema additions exist even when the digest runs before the server.

**Tests — tests/test_costs.py (14 new; full suite 96 passing)**
- Estimator math (tier bps, no price → None); veto: blocks 2% edge under
  180bps RT, passes 4% edge, degrades open without levels, kill switch,
  `_sized_qty` zero + telemetry, dry-run error carries the reason (and
  creates NO row — a vetoed trade never becomes a journal order).
- Journaling: entry+exit accumulate to a round-trip total; paper fill's
  measured slip == bps estimate off its ref; close_position stamps exit
  slip.
- Digest: per-venue aggregation with correct fees/slip/gross/drag, >7d
  rows excluded, >25% drag flagged, empty week renders a placeholder row.

**Ops**: server restarted (PID 22580); loop healthy (0 errors, watchdog
quiet, news wire 312 rows/30m). #402 USD/JPY closed at SL +58.00 (fee-aware
journaled); #404 BTC/USDT LONG still open (pre-cost-layer fill, no slip
stamp — new fills are stamped). No cost-veto rejections observed yet (MT5
FX TP distances are hundreds of bps vs ~0.6–1.4bps RT cost). The veto will
bite on tight-TP small-cap crypto where the tier cost is highest.

---

## 2026-10-01 (08:50 UTC) — Roadmap Phase 2 sizing: inverse-vol scaling +
### rolling per-bucket fractional-Kelly in execute_intent, with caps and tests

User request: "Implement Phase 2 of the roadmap: inverse-volatility position
scaling plus rolling per-bucket fractional-Kelly sizing in execute_intent, with
caps and tests." — roadmap items P1+P2 of
[TRADING-ROADMAP-2026-09-30.md](TRADING-ROADMAP-2026-09-30.md) §Phase 2.

**P1 — inverse-volatility scaling ([backend/risk.py](backend/risk.py))**
- `inverse_vol_multiplier(atr_pct, target, lo, hi)`: risk × (target_vol /
  realized_vol) with realized_vol = ATR(14)/price on the 1h entry timeframe;
  clamped [0.5×, 2×] (`SIZE_VOL_MIN_MULT`/`SIZE_VOL_MAX_MULT`). Neutral 1.0
  when vol is unknown — missing data must never invent an opinion.
- ATR source: the existing `_get_atr` (1h, indicators.add_all).

**P2 — rolling per-bucket fractional-Kelly**
- `bucket_expectancy_ci(records)`: per-trade risk-multiple R = pnl /
  (|entry−SL|×qty); mean expectancy + 1.96σ/√n CI (n<2 → CI None).
- `bucket_kelly_multiplier(records)`: statistically negative expectancy
  (mean+CI < 0) → **0.0** (bucket starved — the roadmap's data-driven
  ranging gate); Kelly f* = E − (1−E)/R with R = avg win / avg loss in R
  units; fraction-of-Kelly risk → multiplier vs base 1% risk, clamped
  [0.5×, 1.5×], f* also capped at MAX_RISK_PER_TRADE; n<2 → neutral 1.0.
- Bucket identity ([backend/execution.py](backend/execution.py)):
  `_row_bucket_keys` = venue|symbol|regime|news-tag → venue|regime|news-tag →
  venue|regime (fallback only for real news tags; generic "other" never gets
  the venue-wide bucket). `_news_tag` classifies journal `reasons` text into
  stable tags: central_bank / inflation / etf / regulation / macro / other /
  none — reproducible at sizing and post-mortem time.
- `_bucket_records` pulls the last `SIZE_KELLY_LOOKBACK` (30) closed trades
  per bucket, venue-filtered via `orders.source`; the most specific bucket
  with ≥2 usable trades decides.

**Wiring in `_sized_qty` / `execute_intent`**
- Combined multiplier = vol_mult × kelly_mult, hard product cap 3.0; final
  risk_pct = base × confidence_mult × combined, still capped by
  MAX_RISK_PER_TRADE (2%) — the hard cap survives every boost.
- kelly_mult = 0 (starved bucket) → zero risk → the trade is refused with
  `zero size (kelly bucket starved: negative expectancy CI ...)` — a
  deliberate data-driven veto, distinguishable from data problems.
- Size forensics appended to the row's `reasons`:
  `size=vol1.7,kelly1.0,conf1.0` — every trade records which multipliers
  sized it. `dry_run` previews carry it too.
- Kill switches: `SIZE_VOL_ENABLED` / `SIZE_KELLY_ENABLED` (default on) fall
  back to exact legacy behavior (verified by test).

**Config knobs ([backend/config.py](backend/config.py))**: SIZE_VOL_ENABLED,
SIZE_VOL_TARGET_ATR_PCT (1%), SIZE_VOL_MIN/MAX_MULT (0.5/2.0),
SIZE_KELLY_ENABLED, SIZE_KELLY_LOOKBACK (30), SIZE_KELLY_MIN/MAX_MULT
(0.5/1.5), SIZE_KELLY_ANNUALIZE (reserved for risk-unit → per-trade
conversion tuning), SIZE_KELLY_FRACTION (0.25), SIZE_KELLY_BASE_RISK_PCT (1%).

**Tests — tests/test_sizing.py (19 new; full suite 82 passing)**
- Pure math: quiet tape 2×, wild tape 0.5×, unknown vol neutral, custom
  clamps; Kelly: n<2 neutral (1 win 1 heavy loss stays 1.0 — not the kill
  switch), all-losses → 0.0, positive edges capped 1.5×, better edges ≥
  worse, rows without levels skipped not poisoned.
- Buckets: news-tag classification table, key ordering (symbol > regime >
  venue-wide), `_bucket_records` filters by key AND venue and caps lookback,
  most-specific informed bucket wins (starved symbol bucket vetoes while a
  coarser bucket would be neutral).
- Integration: `_sized_qty` applies vol×kelly with the hard cap intact,
  starved bucket → size 0 + kelly_mult 0 in telemetry, disabled knobs ≡
  legacy, `execute_intent` dry-run rows carry `size=...` telemetry.

**Ops**: server restarted (PID 25396) — loop healthy post-restart (0 errors,
160 held, watchdog quiet, news wire 298 rows/30m). #404 BTC/USDT LONG filled
at 07:32 by the pre-Phase-2 build; the first fill after cutover will carry
the `size=` telemetry. #401 closed at SL −12.76 via the SL monitor.

---
## 2026-10-01 (07:10 UTC) — broker_position column: journal rows now carry
### the exact MT5 POSITION ticket; bot-loop freeze watchdog; server restart

User request: "Store the MT5 position ticket (not just deal id) on journal rows
so reconciliation and cleanup match positions exactly."

**Why:** the journal's `broker_ref` holds the entry DEAL ticket, but MT5 groups
deals by POSITION id — on a hedging account those are different numbers
(AUD/JPY #394: entry deal 1859451756 vs position 1975186562). Reconcile and
cleanup had to guess-symbol and sweep "newest first," which on this account has
already closed the wrong position once.

**Schema ([backend/database.py](backend/database.py))**
- New `orders.broker_position` TEXT column (fresh CREATE TABLE + migration
  ALTER in `init_db`, same pattern as the other column migrations).
- `record_order` writes it; `update_order_exit` gained an optional
  `broker_position` arg stored with `COALESCE` — stamping at close time can
  never erase a ticket already on the row.

**Execution paths ([backend/execution.py](backend/execution.py))**
- `MT5Broker._submit`: on a filled DEAL, resolves the POSITION ticket —
  `result.position` first, falling back to the executed deal's `position_id`
  via `history_deals_get(ticket=deal_id)` (some builds don't return
  `position` on MqlTradeResult). Returns `broker_position` alongside
  `broker_ref`.
- `execute_intent` plumbs `fill["broker_position"]` into the order dict, so
  every new MT5 row is stamped at insert time.
- `close_market`: when the row carries a position ticket, the close hits THAT
  exact position (`positions_get` filtered to it) and returns its ticket — no
  newest-first sweep into a different same-symbol position. A stamped ticket
  that is no longer open raises loudly instead of falling back (fallback is
  what doubled exposure in the 10013 era). Legacy rows keep the sweep.
- `closing_deal_for_position` now also returns `position_id` (from the OUT
  deal); new `MT5Broker.position_id_for_entry_deal` resolves an entry-deal
  ticket → position id for self-healing.

**Reconcile self-healing (`reconcile_orders_impl`)**
- OPEN MT5 rows missing a stamp get their position id resolved from the
  entry deal and stamped immediately — so the ticket exists BEFORE a close is
  ever needed. Verified live: #402 USD/JPY (placed by the pre-column build)
  self-stamped to position 1984304783 on the first reconcile pass.
- Position/correction passes query the deal history by stamped ticket first
  with the legacy entry-deal ref as fallback; any correction/stamp also
  writes `broker_position` (COALESCE).

**Watchdog for the 06:15-discovered freeze ([backend/server.py](backend/server.py))**
- The bot loop had been frozen since 04:27 — an MT5 IPC call never returned
  (same no-timeout C-call class as the RSS hang); Python cannot kill a wedged
  C call, so the only recovery is replacing the loop.
- `_watch_bot_loop` daemon tracks completed cycles (heartbeat via `on_cycle`);
  no completion for 30 min (`_BOT_CYCLE_TIMEOUT`; normal cycles run 20s–45s)
  while the loop should be running → abandons the zombie thread, restarts a
  fresh loop. Generation guard: the replaced thread can no longer flip
  `_bot_loop_state` off when it un-wedges. Deliberate stops via the dashboard
  don't trip it.

**Tests (63 passing, was 56)** — new in tests/test_close_hardening.py:
- `broker_position` returned by `_submit` (result.position and deal-history
  fallback paths), record_order/update_order_exit persistence + COALESCE,
- close scoped to the exact ticket (never touches the newer same-symbol
  position), loud failure when the exact ticket is missing,
- reconcile stamps closed legacy rows and OPEN legacy rows (before close),
- `#L929`-style entries per test: [tests/test_close_hardening.py](tests/test_close_hardening.py).

**Ops**
- Killed frozen PID 4056, restarted the server with the new code (now PID
  24784, one extra restart to pick up the final reconcile revision —
  the documented Start-Process shell-timeout quirk recurs, verify after).
- Post-restart state: bot loop healthy (20–24s cycles, 0 errors), news wire
  flowing (261→265 rows/30m), positions #401 BTC/USDT LONG + #402 USD/JPY
  LONG open and intact. #403 XRP/USDT SHORT errored honestly: binance
  insufficient free XRP (need 2177.60, have 349.00) — sizing, not a bug.
- New MT5 orders from here on carry both ids: broker_ref = entry deal,
  broker_position = exact position ticket. Legacy rows converge to stamped
  as reconcile touches them.

---

## 2026-09-30 (05:30 UTC) — FX mastery layer: Forex dashboard view, currency
### strength, synthetic USD index, carry bias, exposure grouping

User directive: "add forex to dashboard too, master it too." The bot already
traded 30+ FX pairs through MT5 with session sizing, DXY filtering and
venue-side SL/TP — what was missing was a unified analytics/visibility layer:

- **`backend/forex.py`** (new): session model (live session, liquidity
  weight, countdown, weekend flag, per-pair home sessions); currency
  strength scores (each pair's 24h change credits its base / debits its
  quote); **synthetic USD index** from the 6 majors at real ICE DXY weights
  (EUR .576, JPY .136, GBP .119, CAD .091, SEK .042, CHF .036 — returns
  None rather than a half-baked index when a component is missing); carry
  bias (live MT5 swap points when available, labeled static priors as
  fallback); per-pair trend/momentum/ATR snapshots from 1h candles;
  open-exposure grouping by currency (AUD-basket detector, warns at net
  concentration >= 3). All functions degrade gracefully, never raise.
- **`/api/forex/overview`** endpoint (60s cache) in server.py.
- **Dashboard "Forex" view** (sidebar): session strip with USD index,
  currency-strength bars, open-exposure card with correlated-risk warning,
  32-pair analytics table (trend/momentum/RSI/ATR%/sessions/carry).
  Polled every 60s; graceful error state per panel.
- **Daily digest FX section**: live session, open FX exposure per currency
  (+ concentration anomaly flag), FX-only 7d closed-trade economics with
  win-rate floor flag.
- **`docs/FOREX-PLAYBOOK-2026-09-30.md`** (new): what the bot already
  applies to FX (DXY filter, sessions, corroboration, guards), how to read
  the new view, the five rules (trade with the tide, sessions are edge,
  respect carry, correlated risk is one trade, kill noise), FX economics,
  and next upgrades (group risk cap, vol/Kelly sizing hooks, central-bank
  RSS watcher).
- **`tests/test_forex.py`** (new, 21 tests): session boundaries/weekend,
  strength math, USD-index weights + None-on-missing, carry priority
  (live > prior > none), exposure grouping/purity, pair_snapshot with
  injected fakes, overview against the real DB.

Server restarted on the new code (PID 23804, clean start, bot loop live).
Verified: /api/forex/overview serves real data (Asian session, USD index
-0.065%, JPY strongest +2.99); full suite 46/46 green (25 old + 21 new);
open book intact (11 rows; 386/387/388/400 still filled).

---

## 2026-09-30 (03:00 UTC) — Trading roadmap (research-backed) + daily digest tool

User goal restated: automation so reliable it feels like passive income;
social/government-official monitoring planned next. Wrote
`docs/TRADING-ROADMAP-2026-09-30.md` covering:

- Honest math: 10k->15-20k/day is +50-100%/day (unsustainable); the research
  (Barber/Odean/Jordan day-trader studies) says the 3% who persist profitably
  win via risk control + asymmetry + documented edges, not prediction.
  Roadmap targets +0.3-1.0%/day vol-targeted compounding instead.
- 9 pinpointed problems (P1 fixed-risk sizing ignores vol/edge, P2 no
  realized-edge feedback loop, P4 crypto no venue stops, P5 no portfolio vol
  targeting, P7 costs unjournaled — digest already proves fees=0.00 in
  journal, P9 no champion/challenger, etc.).
- Research-backed additions with sources: Moskowitz-Ooi-Pedersen TSMOM
  (inverse-vol sizing, Sharpe 1.3) as a second alpha sleeve; fractional-Kelly
  per news-bucket sizing (¼ cap); event/tweet drift trading (Filippou 2025:
  Trump tweets -> USD appreciation intraday; Fed FEDS 2023-034: overnight
  social sentiment -> next-day returns); cost-aware entry veto (edge must
  clear 2x round-trip cost).
- X API economics for the social module: no free tier since Feb 2026,
  $0.005/post read — plan starts with official .gov RSS + allowlisted
  officials, X pay-per-use only for the shortlist.

Shipped from Phase 1: `scripts/daily_digest.py` — one command appends a dated
health section to `docs/MONITORING-LOG.md`: AI-only win rate 7d/30d, exit
economics, open basket floating P/L (MT5 legs + Binance marks, non-Binance
rows skipped), guard status, fees, anomaly flags; exit code 1 when guard
tripped or anomaly found (cron/alert friendly). First run: 7d 64.7% win
(+1065.43), 30d 61.9% (+1088.80), MT5 +40.67, guards clear, anomalies none.

---

## 2026-09-30 (08:45 UTC) — CRITICAL FIX: Binance ghost-close bug (journal
### said closed, coins still live) + win-rate self-report

Found while auditing tonight's win rate (looked like 33% but the basket
floated positive): three compounding fail-opens in `backend/execution.py`
let reconcile's position pass decide every Binance holding was "gone"
and ghost-close live rows at last candle price with fabricated pnl:

1. `BinanceBroker` never overrode `symbol_of` — journal `"BTC/USDT"`
   never matched fetch_positions' normalized `"BTCUSDT"`, so every
   holding looked unmatched.
2. `fetch_positions` had `except: return []` — a snapshot failure
   looked like an empty account.
3. One junk airdrop holding (no USDT pair) 400'd the batched price
   call, dropping **all** prices — holdings looked empty every cycle.

Real coins (1.04 BTC / 1.06 ETH / 44.4 SOL) sat unmanaged while the
journal claimed profit. Fixes, all verified:

- `BinanceBroker.symbol_of()` -> `symbol.replace("/", "").upper()`.
- `fetch_positions(strict=False)`; strict raises `BrokerError` on
  snapshot/price failure instead of returning [] — callers now know
  broker state is unknown rather than "empty".
- Holdings pre-filtered against exchangeInfo valid symbols (junk
  skipped upfront; batch can no longer 400).
- Reconcile position pass calls Binance strict=True; on failure it
  aborts the pass (`broker_down`), prints why, and appends
  `{"skipped": "position pass aborted: broker snapshot failed — open
  rows left untouched"}` instead of guessing.
- Data repair: rows 386/387/388/400 (BTC/ETH/SOL LONGs) restored to
  `filled` after the buggy code ghost-closed them with fabricated pnl
  4.75/2.72/4.13/1.31. Since the fixed server started (02:27 UTC,
  12+ cycles): all four remain `filled`, 11 open rows (7 MT5 + 4
  crypto), zero ghost closes.
- Self-report written: `docs/WINRATE-REPORT-2026-09-30.md` — AI-only
  closes since Sep 28: n=34, 64.7% win, +1065.43 net, expectancy
  +31.34/trade (all closes are AI/news-sourced; 100% of the book is
  AI-driven). Weakest bucket: broker_closed 50% / −68.50; ranging
  regime 44.4% — both flagged for continued watch.

---

## 2026-09-30 (late) — Trade-quality round: noise gate, R/R floor, re-entry
### cooldown, exposure caps fix, thresholds recalibrated

Directive: take live trades now, watch them resolve, kill "fake news
noise / low-profitable risk" entries. Changes, all verified live:

- **News noise gate** (`news_noise.py`, wired into `fetch_headlines` +
  `signal_for`): wire-copy dedup (SequenceMatcher >= 0.85) before the LLM
  window, plus a corroboration floor — directional trades need >= 2
  distinct outlets, or one story with |score| >= 0.65. Reason strings now
  carry `outlets=N stories=M mean=+X.XX` for auditability.
- **R/R floor** (`execute_intent`): refuse entries whose TP distance is
  closer than MIN_RR (1.2) x SL distance; cap TP at MAX_TP_PCT (6%).
  `_default_levels` low-vol branch raised from TP=1.0x to 1.5x SL
  distance — the old "quick win" default was the small-profit/big-loss
  profile that bleeds accounts.
- **Re-entry cooldown**: day-bucketed intent hashes used to block any
  re-entry for the rest of the UTC day once a symbol+direction traded.
  A finished (closed/error) row older than REENTRY_COOLDOWN_MIN (60)
  now releases its hash (`database.archive_order_hash` rewrites it to
  `#<id>#<hash>` — UNIQUE survives, history intact). Proven live: BTC
  LONG re-entered the same day after its first position closed.
- **Exposure caps fixed** (bot.py): rows are journaled as mt5/binance,
  so filtering open rows by EXECUTION_BROKER=hybrid counted 0 and both
  MAX_OPEN_POSITIONS and MAX_PER_CURRENCY were no-ops — six AUD trades
  from one "regulatory news" narrative slipped through in one cycle.
  Snapshot is now fetched once per cycle and grows in-place as trades
  execute, so intra-cycle bursts are capped too.
- **Thresholds relaxed** (.env): AI_SENTIMENT_LONG/SHORT 0.50 -> 0.40,
  AI_CONFIDENCE_FLOOR 0.70 -> 0.65 — safe now that counter-trend veto,
  noise gate, R/R floor and real caps guard entry quality.
- **Forensics tool** (`scripts/trade_forensics.py`): closed-book
  breakdown by exit reason / regime / R/R geometry / news tag. Current
  economics: time-decay exits win 68.6% (+1163), TP 100% (+782), hard
  SL 12.5% (-356) — the gates push every entry toward the winning
  shapes (with-trend, corroborated news, R/R >= 1.2, protected by
  server-side SL/TP).

Live verification: BTC/USDT LONG filled @ 83552 with R/R 2.01, 4-outlet
corroboration, closed +1.31; 7 MT5 basket trades carry venue-side
SL/TP; 25/25 tests pass.

---

## 2026-09-29 — CRITICAL FIX: ticket-scoped MT5 closes (phantom-hedge bug),
### reconcile grace window, live auto-loop enabled

Found while enabling the live auto-loop: every SL-hit close of an MT5
position sent a naked opposite DEAL **without a `position` ticket**. On
Exness's hedging account that does not close the original position — it
OPENS a mirror position. One "dollar strength" burst (18 SL hits in a
single cycle) left 18 phantom positions: every loss was effectively
doubled while the journal showed the trade closed at the SL price.

- `execution.MT5Broker.close_market` rewritten: closes open positions
  ticket-scoped (`TRADE_ACTION_DEAL` + `position` ticket, newest first,
  partial fills across multiple tickets). If no open position exists it
  journals the close at the live price and places NOTHING — a naked
  opposite deal on this account can only ever open a hedge.
- `_reconcile_orders_impl` position pass: 15-minute grace window for
  freshly filled rows + a fresh position re-query before declaring a
  row `broker_closed`. (BTC/ETH/SOL buys were being closed at the same
  candle price seconds after filling, because the holdings snapshot
  raced the fill.)
- Cleanup: 56 orphaned positions closed via `position` ticket
  (`scripts/cleanup_phantom_positions.py`, kept for reuse); venue and
  journal aligned at zero open.
- Live auto-loop enabled (`BOT_AUTO_LOOP=true`; live mode allowed for
  hybrid broker, guarded against Binance / live Alpaca by design).
- Verification: 25/25 backend tests pass; clean live cycle (0 errors);
  journal open rows == venue positions at all times.

Note: MT5 journal `broker_ref` stores the deal id, not the position
ticket — reconciliation keys on symbol, cleanup keys on symbol sets.

---

## 2026-09-28 — Win-rate overhaul: counter-trend veto, news-pipeline repair,
### server-side SL/TP, concentration cap

Goal: lift the strategy's real win rate toward 70%+ while keeping expectancy
positive. Post-mortem of the live book (39 closed, excluding the retired-account
event) showed **with-trend entries win 71.4% (+639.59) while counter-trend
entries win 16.7% (−378.90)** — so every change below aims trade flow at the
first bucket and closes the holes that let the second bucket through.

- **HARD counter-trend veto** (`ai_confirm.should_trade`, new config
  `AI_BLOCK_COUNTER_TREND=true`, `AI_COUNTER_TREND_MIN_ADX=20`). The old code
  only *warned* on counter-trend and traded anyway. Verified: a LONG against a
  synthetic downtrend (ADX 100) now returns `pass: False` with the reason
  quoting the measured 16.7% bucket.
- **News-pipeline repair (FX sentiment was running on August headlines).**
  - Feed audit (live HTTP probes): FXStreet/CryptoSlate 403, Reuters/
    MarketWatch connection-refused, both Yahoo feeds 404, Kitco empty,
    DailyFX 403. Replaced with verified-live `ForexLive`, `FX Empire`,
    `Investing Forex`, `Investing Econ` (same-day headlines confirmed in-DB).
  - `news.fetch_rss` sends a browser UA (several outlets 403 the default
    feedparser UA) and drops entries older than 7 days at ingest.
  - `news._normalize_time` now delegates to `database.normalize_news_ts`, so
    every stored timestamp is sortable ISO-8601. Root cause of the 6-week
    staleness: raw RFC-822 strings lexicographically outrank ISO in
    `ORDER BY published_at`, burying fresh stories. New `news.published_ts`
    column (auto-migrated in `init_db` + one-time prune of 828 fossils);
    `load_news` orders by `COALESCE(published_ts, published_at)`.
- **Server-side SL/TP on MT5 DEAL orders** (`MT5Broker._submit` with
  `attach_levels=True` for entries; wrong-side levels dropped, not rejected).
  Found live: **40/40 open positions had `sl=0` on the venue** — software-only
  monitoring meant an offline bot left naked positions. Repair pass attached
  journal or fresh-ATR levels: **38/40 now protected server-side** (2 skipped:
  no candle history). New entries verified carrying `sl`/`tp` on the venue.
- **Currency-concentration cap** (`MAX_PER_CURRENCY=3`, enforced in `bot.run_once`):
  one "dollar strength" cycle opened 9 USD-base longs — a single USD reversal
  hit them all. No currency/index may now exceed 3 open positions (both legs of
  a pair count). Reason surfaces as `currency_concentration` in the hold summary.
- **Regime journaled at entry** (`orders.trend_regime` via Proposal→Intent→
  order row): the book is now auditable by-trend without candle replay.
- **Feedback loop reads the REAL book** (`ai_feedback._live_book_report`):
  `adaptive_thresholds` falls back to the orders journal (by `trend_regime`)
  when the paper `test_trades` book is thin, so calibration uses real outcomes.
- Tests: `test_ai_upgrade` 15/15, `test_execution` PASS, `test_mt5_broker`
  PASS. Live demo cycles verified: with-trend fills (regime + tc/dxy/smc in
  reasons), trailing-stop booked +32.55 on XAU/USD, duplicates/no-signal held,
  concentration guard silent when under cap.

Known follow-ups: USD/TRY is broker-restricted on this Exness demo
(retcode 10044) — worth excluding from the MT5 universe; XRP/USDT sizing
exceeds free testnet balance on shorts (needs a balance check pre-submit).

---

## 2026-09-28 — Binance clock-skew retry, honest MT5 reject text

Found by driving live orders through all three dashboards (workspace-root
`REAL-TRADE-SESSION-2026-09-28.md`).

- **Binance `-1021` ('Timestamp for this request is outside of the recvWindow').**
  Every signed request now uses a new `_binance_ts()` that applies a clock offset
  measured from the public `/api/v3/time` endpoint (refreshed every 10 minutes and
  force-refreshed on a `-1021` retry). Applied to `_submit`, `_free_balance`, and
  `fetch_orders`; the `-1021` retry path now re-measures instead of re-signing with
  the drifted local clock.
- **Misleading MT5 rejection text.** A rejected MT5 order logged
  `mt5 order rejected for XAUUSD: 10027 (1, 'Success')` because it printed
  `mt5.last_error()` — which reads `(1, 'Success')` after the fact. It now reports
  the broker's own `result.comment`: `mt5 order rejected for XAUUSD: retcode 10027
  — AutoTrading disabled by client`.
- **Note (no code change needed):** Binance `LOT_SIZE`/`NOTIONAL` filters were
  already pre-validated in `BinanceBroker._submit`; the historical `-1013` rows
  predate that guard. The crypto universe was blocked by **stale 15m–1d candles**
  (last write 2026-09-15) — the freshness gate held every `*/USDT` symbol with
  `reason: "data"`. Fixed operationally with `python backend/fetch_all.py
  --candles-only --fast`.

Tests: 25/25 pass.

## 2026-09-17 — pairs walk-forward: DO NOT WIRE (honest negative)

New `scripts/pairs_walkforward.py` — no-look-ahead walk-forward protocol:
rolling TRAIN(1000 bars)/TEST(240) with frozen params, next-bar execution,
Z-exit 0.5 / Z-stop 3.5 / time & 2×half-life stops, costs 2 legs × 2 sides ×
(10bps taker + 2bps slip) = 48bps per round trip.

Out-of-sample results (all three cointegrated candidates):

| pair | trades | win rate | gross | **net** | max DD |
|---|---|---|---|---|---|
| ADA/DOGE | 12 | 33% | +1.74% | **−4.02%** | 9.5% |
| LINK/AVAX | 12 | 33% | +2.02% | **−3.74%** | 6.1% |
| BTC/ETH | 36 | 6% | −3.26% | **−20.54%** | 19.8% |

**Verdict: NOT wired into the bot.** The in-sample "100% of \|Z\|>2 entries
mean-reverted" was look-ahead; with frozen parameters the edge is smaller
than friction on every candidate (BTC/ETH additionally suffers hedge-ratio
drift on 10-day windows). The pairs scanner stays a research tool.

Retest triggers for the future: entry \|Z\|≥2.5–3 (wider capture), maker-only
fills (limit legs, needs fee tier < 5bps/side), longer train windows, or a
real fee tier from the exchange.

## 2026-09-17 — quant research layer (read-only)

- Installed the statistical stack **into the project venv only** (scipy 1.18.1,
  statsmodels 0.15.0, scikit-learn 1.9.1, arch 8.0.0) — nothing global.
- New `scripts/pairs_scanner.py` (RESEARCH ONLY, reads the DB, never trades):
  Engle-Granger cointegration screen over 45 crypto pairs on 15m bars from the
  local candle store; reports hedge ratio, spread Z-score, half-life, signal.
  Current scan: 11 pairs cointegrated at 5% (ADA/DOGE p=0.0002, LINK/AVAX,
  BTC/DOGE, BTC/ETH …). No pair is at an entry threshold right now (all
  |Z|<2.1 except DOGE/XRP edge).
- **Honest verdict recorded:** paper/gross PnL ≠ profitability. 9x free's
  36-trade sample is −60 bps gross / −348 bps net of fees. The scanner is
  wired into the *bot loop only after* a walk-forward backtest proves
  out-of-sample profit.

## 2026-09-15 (later) — Connection diagnostics card (/api/broker-health)

New **"Connection diagnostics"** section at the bottom of Portfolio (registry-driven — a future broker joins automatically). One row per active broker:

- **Status dot** (green = probe passed, red = down) with the error as tooltip
- **Which account it actually talks to** — the thing that was invisible until now: MT5 shows login `472561011 · Exness-MT5Trial16`; Binance shows key fingerprint `..1TCXqF · testnet.binance.vision`; Alpaca shows `..RDJ4ZF · PA3SN93M95YB`. This directly answers the "my Binance demo is empty" class of confusion (wrong-account mix-ups) at a glance, without leaking the key.
- **Probe latency** (ms, turns red past 2s)
- **Last journal sync** — `_reconcile_state` now records every `reconcile_orders()` run (time, ok/failed, change count); rows show `sync: 27s ago`, `sync: 3m ago ⚠ failed`, or `sync: never` (amber).

Backend: `execution.broker_health()` probes each active broker live (MT5 `detailed_account_info`, Binance signed `_account_snapshot`, Alpaca account via `portfolio._alpaca_account()`), timing each probe; a broker that is down degrades to `ok:false` + error — never silently absent. `GET /api/broker-health` exposes it; UI polls every 60s. Verified live: 3/3 connected, fingerprints correct, sync timestamps update after each reconcile, 25/25 tests, zero console errors.

---

## 2026-09-15 (late) — Journal↔broker sync for hybrid + Binance signed endpoints + professional journal redesign

**Why the dashboard "wasn't updating" when you closed trades on Exness** (and why the Binance demo looked inconsistent):

1. **`reconcile_orders()` skipped hybrid entirely.** The broker gate was `name in ("alpaca", "binance", "mt5")` — with `EXECUTION_BROKER=hybrid`, reconciliation was a permanent no-op, and the broker-side-close position pass only ran for `alpaca|mt5`. Manual closes in the Exness terminal (or a hit stop/TP) never reached the journal unless someone pressed Reconcile or ran a bot cycle. Now: hybrid resolves **each journal row to its real sub-broker** (`_get_broker_for`), caches positions per sub-broker, and checks MT5 rows against MT5 positions (`EUR/USD` ↔ `EURUSD` normalized), crypto rows against **Binance spot holdings as pseudo-positions**.
2. **Binance `fetch_orders` called the signed-only `/api/v3/openOrders` unsigned** → HTTP 400 on every reconcile (same disease as the old `_free_balance` bug). Now signed exactly like `_submit`.
3. **`BinanceBroker` had no `fetch_positions`** — spot holdings couldn't be verified. Added: signed `/account` snapshot → non-quote balances → batched public `/ticker/price` → `[{symbol: "BTC/USDT", qty, current}]` pseudo-positions (spot: a base-asset balance IS the exposure).
4. **Quiet auto-reconcile in the UI**: every 60s the dashboard posts `/api/bot/reconcile` and refreshes the journal only when something actually changed — broker-side closes now appear within a minute, no button press needed.
5. **Verified with a real reconciliation**: the open SOL/USDT row (whose position had been closed at the broker) was detected and journaled — `exit 100.85, pnl −$12.32, exit_reason broker_closed`. Journal now reads Open 4 · $3,837.13 · unrealized −$128.69 · realized +$31.22, consistent with the live accounts. 25/25 tests pass.

**About "my Binance demo is empty"**: it isn't — the API-key account in `.env` holds BTC 0.9686 / ETH 1.0 / SOL 18.32 / USDT 11,014, and all four journal order IDs verify FILLED on the exchange (checked per-order via signed `/api/v3/order`, quantities match). If testnet.binance.vision shows nothing, you're viewing a **different testnet account** — each testnet registration has independent balances; the bot trades with the key ending `…1TCXqF`.

**Professional Trade journal redesign** (the other request this session):
- Summary strip → **stat cards** (Open positions / Open mkt value / Unrealized PnL / Realized PnL, conditional Dry-run), monospaced tabular numerals, fixed 2-decimal money, proper +/− signs; Dry-run card only when nonzero.
- Journal table → right-aligned monospaced numeric columns (Qty/Entry/SL/TP/Exit/PnL), table in its own rounded **scroll lane** (sticky headers, never drags the whole card), Reconcile moved to the card header as a proper labeled pill (it was overflowing as a 34px icon square).
- Backend truthfulness fixes feeding the strip: MT5 broker marks now actually blend (field name `profit` ≠ `unrealized_pl`, journal `EUR/USD` vs broker `EURUSD` key mismatch, Binance rows no longer zeroed by the MT5 stale-row logic, MT5 deal history deduped against journal closed rows so realized PnL can't double-count). "$0 across the board" → real values (mkt $8.2k, unrealized +$17).

Also this session: **watch-only assets** — `/api/watchlist` now returns **every known symbol** (TRADING_UNIVERSE + SYMBOLS = 77 rows), not just collected ones: 10 bot-tradeable with full indicators, 67 watch-only priced live via Binance public 24hr tickers (batched, 30s cache), rendered dimmed with a `WATCH` badge and sorted tradable-first; rows with collected candles still show real RSI.

---

## 2026-09-12 (latest-4) — Portfolio Balance/Holdings aggregate ALL accounts (no more fake demo)

You asked: "balance and holding show total of all — as my portfolio is for every acc?" Correct, and it wasn't: `get_portfolio()` picked **one** mode by `EXECUTION_BROKER`, and since you run `hybrid`, **nothing matched → it silently showed static DEMO_HOLDINGS** ($33,803 fake BTC/ETH/BNB).

- New `_account_slices()` probes **every active registry account**: MT5/Exness, Alpaca, plus a modal-connected Binance exchange. **Margin accounts contribute their EQUITY** (the account IS the asset — open positions are leveraged exposure already inside that equity; adding coin amounts would double count); spot accounts contribute real coin balances.
- `get_portfolio()` now **sums the slices**: Balance/Holdings = total across accounts. One account alone → that account's legacy mode (UI extras keep working); several → **mode `multi`**; none respond → demo fallback (last resort, still labeled).
- `multi` response adds `accounts[]` (per-account label/equity/unrealized) and the allocation bar shows **account allocation in broker registry colors** (Exness 31.8% / Alpaca 68.2%) instead of a degenerate 100% USDT wedge.
- Balance subtitle: "Exness · MT5 $45,609.06 · Alpaca (paper) $97,989.96 · 2 accounts"; Connect button shows "2 accounts ✓".
- Frontend hardening found en route: `symOf()` crashed on non-string input (the broker-labeled distribution rows) — now null-safe.
- Verified live: $143,599.02 = 45,609.06 + 97,989.96 ✓, bar/legend per-account ✓, zero console errors, 25/25 tests. CHANGELOG'd under `2026-09-12 (latest-4)`.

## 2026-09-12 (latest-3) — Donuts now follow the selected period (dynamic window)

**Profit by currency (per-broker donuts + breakdown) now honors the Closed-trades period selector** (Today / 7 days / 30 days / All / custom range) instead of always showing all-time PnL.

- The donut fetch previously had **no date window** (`/api/orders?limit=500&detail=1`), so the donuts ignored the period while the closed-trades list right below them respected it.
- `loadClosedTrades()` now fetches both queries in parallel (windowed closed rows + all rows) and synthesizes the summary client-side via new `_synthDonutSummary()`: **realized** PnL from trades windowed by **exit time**, **unrealized** PnL from currently-open positions (open rows have no exit time, so they can't sit inside a historical window — they always show "now"). With no filter this reproduces the backend summary exactly.
- **First-paint bug fixed (pre-existing)**: `tradesRange` initialized as `{preset:"7d", from:null}` and `applyTradePreset()` was never called at startup — the label said "last 7 days" but the first fetch was **unwindowed all-time**. Startup now runs `applyTradePreset(tradesRange.preset)` before the first journal fetch, so label, pill state, date inputs, and data all agree from the first paint.
- Verified live (v=20260912m): fresh load → 11 currencies +$742.76 "last 7 days" (pill active); Today → 1 currency +$416.22; 30d → 25 currencies −$294.07; All → 25 currencies −$294.07 (today ⊂ 7d ⊂ 30d ≈ all — coherent). Zero console errors.

## 2026-09-12 (registry) — Brokers are now data, not code — add an account in ONE place

You asked "what if I add more accounts in future — we can't add each individually in the codebase?" Agreed. The broker list is now a **single registry** in `backend/execution.py`:

```python
BROKER_REGISTRY = [
  {"key": "mt5",     "name": "Exness · MT5",   "letter": "M", "color": "#0ecb81", "modes": ("hybrid", "mt5")},
  {"key": "binance", "name": "Binance",        "letter": "B", "color": "#f5b50a", "modes": ("hybrid", "binance")},
  {"key": "alpaca",  "name": "Alpaca (paper)", "letter": "A", "color": "#ffd03e", "modes": ("hybrid", "alpaca")},
]
```

Adding an account = one registry entry here + its `.env` keys + one account-card builder. Everything else is generated:
- **Frontend**: donut panes, broker tags (letter, color, name), live-trade badges, breakdown rows, closed-trades names — all render from `/api/broker-accounts`' `brokers[]` (registry mirrored to the client at startup, with a static fallback). Zero frontend edits for a new broker.
- **Backend**: `_broker_sources()` (journal filtering across every endpoint), the account cards, and the SL/TP push routing all read the registry.
- The journal `key` is the stable identity (renaming a display name is free; renaming a key orphans history — documented in the registry).
- Hardcoded leftovers deleted: the 3 donut `<div>`s in HTML (now generated), per-broker chart variables (now a `Map` keyed by broker), the 4 hand-written tag branches, and the old `_broker_sources` if/else.
- Verified live: all 3 donuts render from the registry (+$295 / +$372 / −$961 Alpaca now has trades), tags/names correct, zero console errors, 25/25 tests.

## 2026-09-12 (latest-2) — Per-broker donuts (3-way split) + broker names on trades

**Profit by currency now has THREE separate donuts** — Exness · MT5, Binance, and Alpaca (paper) — one per real broker, never mixed. Alpaca shows its "$0 — no trades in period" empty state (same layout for all).

- **Broker names everywhere**: the closed-trades list, the PnL breakdown list, and the journal table now show the broker's **name** ("Exness · MT5" / "Binance" / "Alpaca (paper)") next to its letter tag — not just a bare B/M letter. Single shared `_brokerTagHtml()` helper (new `tag-alpaca` style added earlier).
- Donut grid is now `repeat(auto-fit, minmax(200px, 1fr))` so 3 panes sit side-by-side and stack on narrow screens.
- **Stale-HTML trap fixed for good (found during verification)**: the browser had pinned an old cached HTML shell (`document.lastModified` = Aug 18) even though the server sent `Cache-Control: no-cache` — the shell was loaded from the heuristic cache before the no-cache header existed. A `fetch(..., {cache:"reload"})` + `location.reload()` refreshes it; the served page now has all current code.
- Verified live: 3 donuts side-by-side (+$295 MT5 / +$417 Binance / $0 Alpaca), breakdown + closed-trade rows carry named broker tags, heatmap re-swept 26 cells with zero exceptions (the tooltip crash in the console log was from the stale shell running old JS — current code is defensive and throws on nothing).

## 2026-09-12 (newest) — TradingView-style draggable SL/TP levels

Levels on the position/signal charts are now **draggable**: hover an Entry/SL/TP line → it becomes grabbable (ns-resize); drag vertically → a live preview line + label shows the candidate price; release → it saves.

- Backend: `POST /api/positions/levels` persists to the journal row AND pushes to the **real broker** — MT5 positions get a `TRADE_ACTION_SLTP` amend via new `MT5Broker.modify_position_sltp()` (tested against the live Exness demo terminal: the endpoint correctly surfaces broker rejections, e.g. retcode 10016 when SL sits on the wrong side of price for the position direction).
- Binance spot market positions carry no server-side SL/TP → journal-only save (explicitly routed, not a silent skip).
- Frontend: `bindLevelDragging()` on both candle charts; drop saves via the endpoint, toast confirms ("saved + pushed to MT5" vs "saved to journal"), chart refreshes from the server so what you see is what's stored.
- Verified live in preview: dragged SOL/USDT SL 104.33 → 106.89; journal row updated, toast shown, zero console errors. Tests 25/25.
- **Follow-up fix (same day)**: the first cut used a full-chart overlay div for drag capture — it blocked ECharts' own tooltips and scroll-zoom. Rewritten to proximity-detect on the chart element itself (grab cursor within 6px of a level line, `stopPropagation` only on an actual grab). Re-verified: drag works (SL → 109.82 persisted), wheel-zoom works (70→68), tooltips render, zero console errors.

## 2026-09-12 (latest) — Chart "cut front half" fixed: data holes + ghost bars + false SMA

The candlestick chart looked cut off / squeezed to the top with a mystery diagonal to 71k. Three stacked root causes, all real data defects (not layout):

1. **18.5-day interior hole** (Aug 19 → Sep 7, machine/collector off). `repair_gaps()` only heals the newest edge, so interior holes never self-healed. → Backfilled **92,218 real Binance bars** across every affected symbol/interval; new `collector.fill_interior_gaps()` heals interior holes from real klines, and the live collector loop now checks each symbol for holes (one indexed query) and fills them.
2. **SMA-50 computed across the hole** dragged pre-gap prices (64k) into post-gap means (78k), drawing a false diagonal that stretched the y-axis and squeezed candles into the top half. Now the hole is filled the SMA is true (sma50[Aug-bar] = 79,681, was the lie 71,038). `indicators.sma()` documents its warmup semantics explicitly (partial-window values must be opted into, never silent).
3. **Ghost snapshot bars on BTC/ETH**: the Exness-candle daemon mapped `BTC/USDT → Yahoo BTC-USD`, writing Yahoo snapshots (O=H=L=C, volume 0, off-hour timestamps) into the same candle rows as real Binance klines — flat ghost candles corrupting the recent window. Crypto is Binance-collector territory; removed the mapping, purged the 44+44 stored junk rows. Chart now has 0 flat bars, 0 zero-volume bars in the last 400.

Verified live: full-height candles across the pane, continuous time axis, zero console errors. Tests 25/25.

## 2026-09-12 (late night) — Broker account cards (Exness/MT5 + Binance + Alpaca) & signed account reads

**New: `Broker accounts` section in Portfolio** — one card per real broker with full live account details, all in the same layout:
- **Exness · MT5**: login, holder, server, company, demo/real, leverage, balance, equity, floating P&L, margin / free margin / margin level (read from `mt5.account_info()`)
- **Binance Spot (testnet)**: masked API key, server, free/locked USDT, assets held (signed `GET /api/v3/account`, 30s cache)
- **Alpaca (paper)**: account #, status, market, cash, buying power, portfolio value

- Backend: `execution.detailed_account_info()` (MT5), `all_account_cards()` (one card per configured real broker; a broker that's offline degrades to an error card — never a fake number), `GET /api/broker-accounts`.
- **Real defect found en route and fixed**: Binance `_free_balance` (the insufficient-balance pre-check in `_submit`) called `/api/v3/account` **unsigned** — every call 400'd and the pre-check silently failed open forever. Account reads now go through a properly **signed** shared snapshot (`BinanceBroker._account_snapshot`), so the pre-check actually works; verified live (free USDT 13,883.53 / BTC 0.96864).
- UI: broker badges reuse the live-trades tag styles; per-broker header count; floating P&L hidden when |pnl| < 0.005 (killed the "-0"); `dashboard.css` now cache-busted alongside `dashboard.js` (browser was pinning the old stylesheet).
- Verified: all 3 cards live with real account data (screenshot), zero console errors, 25/25 tests pass.

## 2026-09-12 (night) — Donut labels redesigned for many assets + HTML no-cache fix

- **Donut readability with 10+ assets (dashboard.js _renderOneDonut)** — external per-slice
  labels (symbol + %) crossed leader lines into an unreadable web with 16 currencies.
  Redesign: **no external labels**; slices ≥7% carry a compact **percent inside the ring**
  (name+percent only when ≤4 slices), label text color chosen by slice-shade luminance
  (`_txtColorFor`: dark text on light shades, white on dark), leader lines off, tooltip and
  the breakdown list (whose dots match slice shades) carry the names. Painted labels
  verified on the live chart: `46%`, `35%` (previously `46.06%` raw floats in red).
- **HTML shell now served with `Cache-Control: no-cache` (server.py index())** — the browser
  repeatedly re-served the stale cached `index.html` (heuristic caching), which pins an old
  `dashboard.js?v=` even after edits; verified the header is live (`curl -D -`: 
  `cache-control: no-cache`). Cache-bust bumped to `?v=20260912e`.

---

## 2026-09-12 (evening) — Trade-error root causes fixed (3 recurring classes) + real-broker guarantee

User reported "an error in this trade". Forensics on the journal + server log found three
recurring order-error classes (plus one triggered during live interaction — no fills landed):

- **Binance `Filter failure: LOT_SIZE` (BTC/ETH/SOL, incl. 08:03 today) — FIXED**
  (`execution.py::_get_market_lot_info`). The spot-testnet sends `MARKET_LOT_SIZE` with
  `stepSize: 0` / `minQty: 0`; the merge let those zeros overwrite the real LOT_SIZE values
  (0.00001), so quantities went out with 8 decimals → -1013. Order-independent fill-gap
  merge now keeps the LOT_SIZE grid; verified against the LIVE testnet.
- **Binance `-1021 Timestamp outside recvWindow` — FIXED** (`execution.py::BinanceBroker._submit`).
  Clock drift burned orders with no retry. Now: re-sign with a fresh timestamp and retry once
  on -1021; persistent -1021 still raises BrokerError (tested both paths).
- **MT5 `zero volume for <SYM>` (USD/JPY, USD/SEK, USD/PLN, USD/NOK, USD/CAD, GBP/USD, XAG — FIXED**
  (`execution.py::_sized_qty`). The lot-aware sizing branch required `broker.name == "mt5"`,
  but the configured broker is `hybrid` — FX/metals sized by generic `notional/price` units
  that floor to 0 lots. Branch now also runs for hybrid-routed MT5 symbols (verified:
  XAG/USD-style CFD sizes to `vmin lots × contract`, USD/JPY no longer floor-to-zero).
- **Latent: exchange-info failure wiped the filter cache to zeros — FIXED.** Both
  `_get_step_size`/`_get_market_lot_info` reset the cache on a failed refresh, making every
  order skip step-rounding during network hiccups. Failures now serve the stale cache.
- **Real-broker guarantee (user rule: every trade on a real broker we have access to)** —
  `get_broker()` used to silently return the simulated PaperBroker for ANY unknown
  EXECUTION_BROKER value, and the config default was `"paper"`. Now: unknown values raise
  BrokerError loudly; `paper` must be explicit; config default is `hybrid` (real brokers).
  All three real endpoints verified reachable: MT5/Exness terminal ✓, Binance spot-testnet
  exec account ✓ (USDT 10,000 / BTC 1.0 free, signed), Alpaca paper ✓ ($97,989.96 ACTIVE).
- **Tests: `scripts/execution_error_fixes_test.py`** — 8 checks driving the real patched
  code (live-testnet filter merge, stubbed HTTP -1021 retry + persistent-raise, hybrid MT5
  lot sizing) — ALL PASS; full existing suite still 25/25 after the changes. Server
  restarted on :8501 with the patched code (HTTP 200).
- **Live end-to-end proof**: real market BUY 0.0001 BTC/USDT on the Binance spot-testnet
  exec account filled (orderId 1140203, qty step-rounded to 0.00010 by the fixed filter
  merge — this exact path failed at 07:54/08:03 UTC today) and a real SELL closed it
  (orderId 1140208). Round trip verified; account flat afterwards. Journal error census
  after fixes: zero new error classes; today's 2 errors were the now-fixed LOT_SIZE path.


- **Levels are now usable on any asset (dashboard.js renderCandleChart)**. Previously the
  chart only drew Entry/SL/TP when a signal or open position existed; everything else just
  said "No levels for this asset" forever. Now, when no signal/position exists, the chart
  computes **auto S/R from recent swing pivots** (a pivot = a bar higher/lower than 3 bars on
  each side), keeps the two nearest supports below price and two nearest resistances above,
  merges pivots within 0.15% of each other (R1 77,347.68 vs R2 77,370.28 originally drew
  overlapping lines — 0.03% apart), and draws them as blue dashed lines with a legend
  `S1/S2/R1/R2 <price>`. Title gains an "— auto S/R" suffix; signals/positions still override
  with Entry/SL/TP (verified on the WTI position chart). Verified live: BTC 1h shows
  S1 77,152.54 · S2 76,962.54 · R1 77,347.68 · R2 77,474.85, correct sides of last close,
  zero console errors.
- **Profit-by-currency donut readability fixed (dashboard.js _renderOneDonut/renderPnlDonut)**.
  With 16 currencies every slice had an external label (a stacked unreadable jumble —
  several 0.09% slices) and all wins were one flat green / losses one flat red, so adjacent
  slices were indistinguishable. Fixes: labels only on slices ≥7% (small slices stay
  tooltip-only; the breakdown list is the legend), `minAngle: 2` so hairline slivers stay
  visible, shade-varied palette per slice (7-step green family for wins / red for losses,
  counter-based assignment so colors stay stable per broker donut), and the breakdown list
  dots now use the exact same shade as their slice. The empty Binance card now shows
  "$0 — Binance PnL — no trades in period" instead of a blank canvas. Verified live with
  screenshots: labels legible, tooltip `USD/CAD: +$3 (0.77% of gross)` works, 16/16 slices
  drawn, list dots match slice shades.
- Cache-bust bumped to `?v=20260912c` (index.html); `node --check` clean throughout.


- **Heatmap rendered only 49 of 181 days — FIXED (dashboard.js renderHeatmap)**. Found while
  behaviorally verifying the tooltip fix: `xAxis.data` was `dayNames` (Mon..Sun, 7 categories)
  while the data fed **weekNum (0–25)** into x, so ECharts silently dropped every week past
  index 6 — the chart showed a 7×7 grid (Mar 16–May 3) and both real P&L days (Aug 19 loss,
  Sep 11 win) were **not rendered at all**. y-axis was also mislabeled (weekday names on both
  axes). Fix: x = 26 week columns labeled by each week's Monday (`3/16`, `4/6`, …, `interval: 2`),
  y = the 7 weekday names, `grid.bottom: 16` for the new labels. Verified live: scripted hover
  sweep across all 26×7 cells → 181 distinct correct tooltip dates (full range 2026-03-16 →
  2026-09-12), `2026-08-19 → $-31.55` and `2026-09-11 → +$326.53` matching `/api/trade-stats`
  exactly; screenshot shows the red Wed-8/19 cell and green Fri-9/11 cell on the calendar.
- **Series data stripped the tooltip's dayIndex — FIXED (dashboard.js renderHeatmap)**. The
  option passed `data.map((d) => [d.value[0], d.value[1], d.value[2]])` to ECharts, discarding
  the rich datum objects, so the tooltip fell back to the `week*7+dow` reconstruction — which
  is only correct when the 180-day window starts on a Monday (true today, wrong 6 days out of 7
  as the window slides; on a Wednesday-start window 50/179 cells misdate and other alignments
  throw the original `reading '0'` crash). Fix: pass the rich data objects straight through.
- **Behavioral test added: `scripts/heatmap_tooltip_test.mjs`** (run `node
  scripts/heatmap_tooltip_test.mjs`). Extracts the REAL shipped formatter verbatim from
  `dashboard.js` and drives it the way ECharts does (`p.data = datum`): (1) dayIndex integrity
  on all 181 cells; (2) exact date on every cell; (3) old stripped-data formula compared
  against it; (4) 7 malformed ECharts param shapes — 0 throws; (5) trade-day tooltip content
  (date + signed P&L + color); (6) a Wednesday-start window — new path 0 wrong, old path
  misdates 50/179. All 6 PASS. Cache-bust bumped to `?v=20260911d`.
- Note: live sweeps found zero JS exceptions; the only console entries were environmental
  `ERR_NETWORK_IO_SUSPENDED` / `ERR_NETWORK_CHANGED` from the machine's network flapping
  during polling (handled by the fetch wrapper as warnings).


- **Heatmap tooltip crash FIXED (dashboard.js ~2388)** — hovering the Trading Activity
  heatmap threw `TypeError: Cannot read properties of undefined (reading '0')` on every
  hover. Two defects: (1) the formatter reconstructed the date as
  `allDays[weekNum*7 + dow]`, but `allDays` is a flat 180-day list — after any partial
  first week `weekNum*7+dow` reads past its end (the `weekNum` is a day-count offset,
  not a true week index); (2) ECharts can invoke tooltip formatters with params that
  carry no data datum, so even indexing `p.data.value` can be undefined. Fix: each
  heatmap datum now carries its true `dayIndex` into `allDays`, and the formatter is
  defensive (falls back to `p.value`, returns a safe string instead of throwing).
  Verified in the live browser: scripted mouse sweep across the heatmap → 0 exceptions
  and tooltips render; sweep across all 8 views → 0 exceptions.
- **Dashboard JS cache-busting** — `index.html` now loads
  `dashboard.js?v=20260911b`; the server serves static assets with heuristic caching,
  so previously edited dashboard JS kept reaching browsers from cache (verified: a
  fixed formatter was not picked up until a cache-busted reload). Bump the query
  string whenever `dashboard.js` changes.

- **All 32 open positions closed** — 27 broker-side MT5/Exness trial positions
  (FX pairs, DXY, UK100, USOIL, UKOIL, XPDUSD) closed market via the project's own
  broker wrapper; every fill got a `broker_ref` (see `scripts/close_all_positions.py`,
  added as a repeatable ops helper). Plus 5 journal rows confirmed flat. Zero open
  exposure on the account after this.
- **Trade-error forensics (11 `error` rows in `orders`)** — every error is the same
  event: `alpaca 403 40310000 insufficient buying power` (avail $13,924.69) on
  crypto orders (DOT/BONK/BCH/ARB/XRP/SOL/…), clustered in two bursts
  2026-08-17 08:05–08:16 UTC. Root cause: the pre-pivot crypto path fired multiple
  near-full-balance entries concurrently with no buying-power pre-check; once the
  first fills landed the rest bounced. Risk today: none — crypto path retired same
  day, `ai_news` is the only source, arbitrator dedups intents, circuit breaker caps
  daily loss. 43 `cancelled` rows are gate vetoes working as designed (RSI/DXY/etc.),
  not failures. 10 `closed` rows net −$1,005.28 realized (legacy bot record).
- **Env/broker connections verified live** — Alpaca paper keys ✓, MT5/Exness trial ✓
  (27 fills through it this session), Binance exec keys present (read-only),
  Groq + LLM fallback chain ✓. `auto_loop` off; nothing traded on its own.
- **Dashboard verified end-to-end in live browser preview** — all 8 views render
  (Dashboard / Assets / Portfolio / Bots / Orders / Signals / AI Test / Learn);
  3 ECharts canvases paint (portfolio value, total assets, signal chart — 100k+ lit
  px each); 50-coin table populated with live prices/RSI; bot status card shows
  broker=hybrid, source=ai_news; **0 console errors** on every view; server log
  0 tracebacks.
- **Known cosmetic issue (not fixed, needs owner input)** — Assets table shows `$0`
  price for sub-cent assets (SHIB/PEPE/BONK/FLOKI/GALA/CELR/ONE) and `$0` volume
  for BTC/ETH: formatting floors below 2 decimals / below $1. Display-only; data in
  the DB is fine.

## 2026-08-19 — Error fixes + full docs refresh (README / CLAUDE / ARCHITECTURE / CHANGELOG)

- **MT5 filling-type bug (live-rejection risk)** — `execution.py` read the
  symbol's `filling_mode` bitmask but always sent `ORDER_FILLING_FOK`;
  symbols that only advertise IOC would be rejected. Now: FOK if bit 0 of
  `filling_mode` is set, else IOC.
- **test_mt5_broker — 2 errors fixed** — `_FakeMT5` was missing
  `ORDER_FILLING_FOK` (and had wrong constant values); now matches the real
  MetaTrader5 constants (FOK=0, IOC=1, RETURN=2).
- **test_news_analyst — 2 failures fixed** — the threshold checks depended
  on live `.env` values; the test now pins
  `AI_SENTIMENT_LONG/SHORT` + `AI_CONFIDENCE_FLOOR` to a known baseline so it
  asserts the threshold LOGIC regardless of environment.
  ⚠️ Note: `.env` currently sets very permissive live thresholds
  (`AI_SENTIMENT_LONG=0.05`, `AI_SENTIMENT_SHORT=-0.05`,
  `AI_CONFIDENCE_FLOOR=0.10`) vs the calibrated code defaults
  (`0.50 / -0.50 / 0.70`). Pending owner decision.
- **Docs refreshed** — README (new AI-only architecture, fixed a leftover
  git merge conflict, correct port 8501), CLAUDE.md (overview, pipeline,
  key files, ownership list, status), docs/ARCHITECTURE.md (rewritten for
  the 2026-08-17 pivot).
- **Verified**: all 15 test suites pass (130+ checks); all 47 backend
  modules import clean; server smoke test — `/api/health`, `/api/bot/status`,
  `/api/circuit-breaker`, `/api/ai-layers`, `/api/fear-greed`,
  `/api/test-trades`, `/api/portfolio` all 200 on port 8501.

### Binance leg fixes (hybrid routing, `execution.py` BinanceBroker)
Why the Binance side of the dashboard showed no trades: crypto was
re-enabled 2026-08-18 (`config.CRYPTO_SYMBOLS` → Binance testnet via the
`hybrid` broker), the bot WAS firing BTC/ETH/SOL orders — but they were all
rejected:
- **`-1013 Filter failure: LOT_SIZE`** (BTC, ETH today) — precision bug:
  `int(-log10(stepSize))` truncates (0.00001→4 places, not 5) and float
  `floor(q/step)*step` drifts off-grid. Now: exact decimal floor via
  `Decimal` to the `MARKET_LOT_SIZE` step grid, precision from the step's
  string form (`_step_decimals` / `_floor_to_step`), minQty re-checked
  AFTER the floor.
- **`-2010 insufficient balance`** (SOL 08-18) — the Binance account lacks
  USDT for the sized notional. Now pre-checked locally (`_free_balance`:
  free USDT for BUYs, free base asset for SELLs) → readable journal reason
  ("fund the account or lower risk size") instead of a wasted order.
  Fail-open when the balance call itself fails. **Account funding still
  needed for the Binance leg to trade.**
- **`ConnectionError / RemoteDisconnected`** (SOL today) — order send now
  retries 3× with backoff on connection-level failures; deterministic 4xx
  (e.g. -1013) are NOT retried.
- Verified offline (fake exchangeInfo/balance/post): on-grid quantities
  (0.04181 for BTCUSDT), BUY/SELL balance guards, fail-open, retry path.
 Regression suites (execution, mt5, system, strategy, arbitrator,
  verification) all pass; server restarted so the fix is live.

---

## 2026-08-17 → 2026-08-19 — AI-only trading pivot: news-driven bot, Exness MT5, 6-layer confirmation, external data stack

### The decision (user)
The system now trades **100% on AI/news signals**. The local quant engines
(mean reversion, z-score, RSI2, Bayesian fusion) caused losses and are
**retired from the trade path** — they remain in the codebase for
`/api/signals` (dashboard display) and the backtester only. All trade
decisions come from `news_analyst → ai_agents confluence gate → execution`;
technical code may confirm AI signals but never generate them.

### Trade pipeline (new core: bot.py + confirmations)
- **`bot.py`** — single orchestrator. One cycle: verification → news analyst
  → 6-layer confirmation → circuit breaker → arbitrator (intent +
  intent-hash dedup) → verification re-gate → dual-mode execution → SL/TP
  monitor. `run_once` / `loop [BOT_LOOP_SECONDS]` / `--dry-run`.
- **`news_analyst.py`** — Groq LLM news sentiment & analytics (per-asset
  sentiment -1..1, confidence, impact horizon, rationale). Provider chain:
  Groq (`GROQ_MODEL`, default `openai/gpt-oss-120b` after the 2026-08-17
  model audit found `qwen/qwen3.6-27b` quota-exhausted and
  `llama-3.3-70b-versatile` not a real Groq model) → local OmniRoute
  gateway (keyless) → rule-based backfill. 10-min cache; never raises.
- **6-layer confirmation** — (1) raw sentiment, (2) freshness,
  (3) `calendar_guard.py` NFP/FOMC/CPI/GDP block + spread check,
  (4) `ai_confirm.py` regime (ADX/EMA/ATR) + 6-factor technicals + session
  filter, (5) `dxy_filter.py` USD-pair master filter (Dollar Smile, SMT
  divergence), (6) `smart_money.py` ICT confluence (order blocks, FVG,
  liquidity sweeps, BOS/CHoCH).
- **`ai_agents.py`** — 3 production agents on the same Groq transport:
  catalyst sentiment (black-swan flag), HODL/deep research, chart confluence
  (`AI_CONFLUENCE_GATE`, off by default). Answers journaled in `ai_analyses`.
- **`circuit_breaker.py`** — tiered step-down (5%/10%/15% DD bands,
  micro-lots, full halt) + step-up recovery ladder + 3-consecutive-loss
  breaker + strategy-decay flag.
- **`ai_feedback.py`** — records outcomes, per-layer win analysis, suggests
  threshold calibration (suggests, does not silently auto-apply).
- **Trade universe** — the bot trades ONLY `config.TRADING_UNIVERSE`: the
  Exness forex / metals / energy / indices instruments
  (`EXNESS_SYMBOLS`, `EXNESS_MT5_SYMBOLS` mapping, e.g. XAU/USD → XAUUSD).
  Crypto pairs + Bitcoin ETFs retired from trading (2026-08-17).
- **`SIGNAL_SOURCE_A=ai_news`** is the default and single-source by design
  (exempt from the legacy two-source gate; legacy engines still require
  `ARBITRATION_MIN_SOURCES=2` for live).

### Execution & safety
- **MT5/Exness broker** in `execution.py` (the only module that touches a
  broker): `AI_MODE=live` → real MT5 orders via `MetaTrader5`;
  `AI_MODE=test` → paper trades in the `test_trades` table (`test_ai.py`).
  `EXECUTION_BROKER=hybrid` routes MT5 + Binance; min-lot flooring, lot-size
  precision, `MT5_POSITION_PCT` sizing.
- Safety rails: `BOT_AUTO_LOOP_LIVE` refuses real-money brokers and falls
  back to dry-run; legacy live mode needs `ALLOW_LIVE_SINGLE_SOURCE`;
  verification gates are hard rejects (staleness, OHLC sanity, spike w/o
  volume, cross-source price agreement).

### Data stack (new free sources, `external_data.py` aggregator)
Coingecko, CoinPaprika, DefiLlama, Etherscan (V2), FRED (keyless), eCON,
Fed/Treasury, Frankfurter (ECB), ExchangeRate-API, Fear & Greed (alternative.me
+ CoinGecko), MarketAux, Mempool.space, NewsData.io, Finnhub (optional),
Tavily/Firecrawl article scraping. Each module is fail-safe (never raises,
permissive defaults) and individually toggleable via `config`.

### Dashboard & ops
- New endpoints: `/api/bot/{run,loop,status,source,reconcile}`,
  `/api/circuit-breaker`, `/api/ai-layers`,
  `/api/feedback/{calibration,thresholds}`, `/api/tutor` (+ `/trade`),
  `/api/trade-stats`, `/api/test-trades`, `/api/test-portfolio`,
  `/api/fear-greed`, `/api/news/{stats,analytics,analyze,article}`,
  `/api/portfolio/{analyze,holds}`, `/api/trade/verify`.
- `tutor.py` educational layer ("why this trade" explainers from the live
  journal). Ops scripts: `check_bot_status/check_orders/check_positions/
  check_sl_tp`, `run_bot_fresh`, debug scripts for LLM/MT5/news/pipeline.

### Docs
- `documentation/` → `docs/` (single docs tree: ADRs, directives,
  math_system specs incl. `07-ai-agents.md`, updates, data-sources);
  `documentation/` now holds deep research (competitive intelligence,
  API integration plan).
- README rewritten for the AI-only architecture; `.env.example` documents
  every new key (Groq model audit note, OmniRoute, MT5, thresholds).

### Status
- ✅ Paper loop (`AI_MODE=test`) verified end-to-end; MT5 broker wired
  against Exness
- 📊 Win-rate calibration in progress via `ai_feedback.py`
- ⚠️ None of the 2026-08-17→19 work is committed yet (untracked/modified in
  git as of 2026-08-19)

---

## 2026-08-15 — Trades analytics: date-range filter, every-closed-trade list, live-trades panel

- **Date-range filter** (Orders view): Today / 7 days / 30 days / All presets
  plus custom from→to date inputs. `/api/orders` accepts `from`/`to`
  (created-at window, widened to full days) — the Profit-by-currency donut,
  the closed-trades list and the journal table all scope to the period.
- **Every closed trade list** — per-trade rows (symbol, side, qty,
  entry→exit, exit reason, PnL, time) under the donut, capped at 200 rows
  with a "widen the range" hint.
- **Live trades panel** — new lean `/api/positions` endpoint (open/filled
  rows + fresh price + unrealized PnL, broker marks blended; no heavy
  enrichment) powering a side panel polled every 10s with SL/TP distance
  and opened time.
- **Donut tooltip sign bug fixed** — slices are sized by |PnL| but the
  tooltip showed "+$" for every slice (absolute value); it now renders the
  real sign (e.g. "DOGE: −$438 (65.9% of gross)").

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
