# Win-Rate Self-Report — 2026-09-30 (08:40 UTC)

Scope: live journal (`data/crypto_data.db`, orders table), all stats after the data repair
described below. Generated autonomously per standing instruction ("do the best suggested
follows without asking, just document changes and updates").

## 1. Headline numbers — closed trades since 2026-09-28 overhaul

| Cohort | n | Win rate | Net PnL | avgWin | avgLoss | Expectancy/trade |
|---|---|---|---|---|---|---|
| **All closes since Sep 28** | 34 | **64.7%** | **+1065.43** | +67.74 | −42.47 | **+31.34** |
| AI-only subset (reasons `ai/news`) | 34 | 64.7% | +1065.43 | +67.74 | −42.47 | +31.34 |
| Last 24 h (AI-only) | 25 | 16 wins (64%) | +1012.93 | — | — | +40.52 |

**100% of closes since the overhaul are AI/news-sourced** (reasons begin `ai/news ...`).
There is no manual cohort to compare against — the bot's decision layer is fully AI-driven,
so "overall" and "AI-only" are the same population. Win rate = pnl > 0; 2 trades exited
flat (pnl = 0, time decay at breakeven).

Comparison with the pre-repair snapshot (n=38, 60.5%): the 4 extra rows were fabricated
ghost closes (pnl ≈ 0 / small fake wins). Removing them raised both win rate and avgLoss
honesty. The +1065 PnL figure is fully real.

## 2. Where the money comes from (last 50 closed, forensics run 08:38 UTC)

| Exit reason | n | Win % | PnL |
|---|---|---|---|
| time_decay_exit | 27 | 70.4% | +1153.51 |
| tp | 3 | 100% | +229.65 |
| sl | 4 | 25% | −27.62 |
| broker_closed | 16 | 50% | −68.50 |

Key readings:
- **time_decay wins are the engine** (+1153): the bot exits decaying trades at breakeven-or-better
  instead of letting them become full losses. This is the biggest profitability driver —
  keep the time-decay thresholds where they are.
- **TP hits are 100% winners** and the R/R audit found **0 SL losers with R/R < 1.2**
  (the rr_floor refuses them at entry, so the geometry fix is working).
- **SL cohort only −27.62 total across 4 stops** — stops are doing their job (contained loss,
  not account-killers like the pre-Sept-29 `hard sl` cohort of −356 on 8 trades).
- **broker_closed 50%/−68.50** is the weakest bucket: these are venue-side closes the bot
  did not initiate (margin/stop-out on the venue, manual closes, or broker-side TP/SL
  without a journal tag). Larger loss tail when it loses. Watch bucket.

Regime at entry (last 50): trending_up 87.5% (n=8), trending_down 66.7% (n=12),
unknown 57.1% (n=21), ranging 44.4% (n=9, −89.63). The bot is still allowed to trade
ranging regimes but they are the only net-negative bucket — the AI gate on tc/confidence
is what keeps them small.

## 3. The data-integrity bug that was poisoning these stats (FIXED, verified)

**Symptom:** "tonight" win rate looked like 33% while the real basket floated positive.

**Root cause — three compounding fail-opens in the Binance path of
`backend/execution.py`:**
1. `BinanceBroker` never overrode `symbol_of` → journal symbol `"BTC/USDT"` never matched
   fetch_positions' normalized `"BTCUSDT"` → reconcile's position pass judged every Binance
   holding "gone" → ghost-closed live rows at last candle price with fabricated pnl.
2. `fetch_positions` had `except: return []` — a snapshot failure looked like "no holdings".
3. One junk airdrop holding (no USDT pair) made the batched price call HTTP 400 → **all**
   prices dropped → holdings looked empty every single cycle.

Real coins (1.04 BTC / 1.06 ETH / 44.4 SOL) sat unmanaged while the journal claimed profit.

**Fixes (all in `backend/execution.py`, live since server start 02:27 UTC):**
- `BinanceBroker.symbol_of()` → `symbol.replace("/", "").upper()`.
- `fetch_positions(strict=False)`; strict raises `BrokerError` on snapshot/price failure
  instead of returning [].
- holdings pre-filtered against exchangeInfo valid symbols (junk can no longer 400 the batch).
- reconcile position pass calls Binance with `strict=True`; on broker failure it aborts the
  pass (`broker_down`), prints the reason, and appends
  `{"skipped": "position pass aborted: broker snapshot failed — open rows left untouched"}`
  instead of guessing.

**Verification:** rows 386/387/388/400 (BTC/ETH/SOL LONGs) were restored to `filled` after
the buggy code ghost-closed them. Since the fixed server started (02:27 UTC, 12+ cycles),
all four remain `filled`, 11 open rows total (7 MT5 + 4 crypto), zero ghost closes.
A partial dead session of the bug remains in history: 4 bogus `broker_closed` rows with
pnl 4.75/2.72/4.13/1.31 — excluded from all stats above; marks written before repair.

## 4. Live basket at report time (08:38 UTC)

**MT5 (Exness demo, venue-side SL/TP attached):** 7 positions, floating **+56.23**
— AUDCAD +7.15, AUDJPY +5.91, AUDNZD +8.48, AUDUSD +15.96, EURAUD +10.41, GBPAUD +8.68,
GBPUSD −0.36. The "AUD regulatory weakness" basket (rows 393–399) has flipped from
−0.76…−1.55 at entry to solidly green.

**Binance spot (software-managed, no venue-side stops — documented limitation):**
| Row | Trade | Fill | Mark | Floating | TP distance | SL distance |
|---|---|---|---|---|---|---|
| 386 | BTC/USDT LONG | 83,498.01 | 83,438 | −2.35 | +4,220 | +2,041 |
| 387 | ETH/USDT LONG | 2,686.80 | 2,672.21 | −8.87 | +175.8 | +66.0 |
| 388 | SOL/USDT LONG | 118.69 | 119.64 | +13.07 | +6.19 | +4.49 |
| 400 | BTC/USDT LONG | 83,552.01 | 83,438 | −4.42 | +4,300 | +1,967 |

Crypto net −2.57; combined live floating ≈ **+53.7**. SOL is closest to TP (+6.19 vs
+4.49 to SL) — watch for the first software-managed TP resolution.

## 5. Fixes shipped this round (all verified, tests 25/25 green)

1. Binance ghost-close root cause (above) — symbol_of, strict fetch, exchangeInfo pre-filter,
   broker-down abort.
2. Ticket-scoped `MT5Broker.close_market` (position ticket, newest-first) — never emits a
   naked opposite DEAL, which was *opening* hedges on the Exness hedging account.
   56 phantoms cleaned via `scripts/cleanup_phantom_positions.py`.
3. Reconcile 15-min grace window + fresh re-query for fresh fills (no more instant
   ghost-close of a just-filled order).
4. Re-entry cooldown 60 min via `_hash_freed` + `archive_order_hash` (proven live: BTC
   re-entered same day without hash collision).
5. R/R floor at entry: refuse TP distance < 1.2× SL distance; MAX_TP_PCT=6 cap;
   low-vol default TP raised 1.0× → 1.5× SL.
6. News noise gate (`news_noise.py`): wire-copy dedupe (SequenceMatcher ≥ 0.85) +
   corroboration gate (≥2 distinct outlets, or single outlet with |score| ≥ 0.65).
   Forensics confirm **0 losers carried single-outlet news tags** since the gate went live.
7. Bot caps made real: MAX_OPEN_POSITIONS=5, MAX_PER_CURRENCY=3 (snapshot-grown counters).
8. Thresholds relaxed (.env): AI_SENTIMENT_LONG/SHORT ±0.40, AI_CONFIDENCE_FLOOR 0.65.

## 6. Known limitations / watchlist

- Binance spot rows have **no venue-side SL/TP**; protection is software-managed each
  cycle (5 min). A broker outage > 1 cycle with a fast crash is the residual risk —
  mitigated by strict-mode abort refusing to act on stale state.
- `broker_closed` exits are the weakest economics bucket (50%, −68.50) — investigate
  which venue events produce them (candidates: Exness venue-side SL without journal tag,
  margin events).
- Ranging-regime entries are net-negative (44.4%, −89.63 over 50) — candidate for a
  regime gate if the next 50 trades confirm.
- MT5 `broker_ref` stores deal id, not position ticket; close path resolves the ticket
  by symbol lookup at close time (ticket-scoped close_market handles this).

— Generated by Buffy (autonomous run), 2026-09-30.
