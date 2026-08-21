# DIRECTIVE-001: Mean-Reversion Parameter Optimization (for Claude)

- **Issued by:** Lead Architect & Strategy Director (Codebuff)
- **Date:** 2026-08-14
- **Executed by:** Lead Architect & Strategy Director (Codebuff) on 2026-08-15
  (Claude was busy on UI territory; execution was in-process and new-file-only)
- **Priority:** HIGH — this is the acceptance gate for ADR-001

---

## Objective

Find a parameterization of the mean-reversion strategy (`strategy.py`) that
achieves **positive expectancy** and, if possible, the **60–70% win rate**
target — without curve-fitting. **Baseline is now v2** (trend-filtered,
`trend_filter=True` — already validated as the best-known config): BTC WR
52.4%, returns −4.2%; others 31–52% WR, all returns improved vs v1. The goal
is to push the v2 baseline toward the target.

**Update 2026-08-14 (Lead Architect):** `entry_mode="rsi2"` was A/B tested
and rejected at 1h (WR 19–30%, PF ~0.2) — do NOT spend grid budget on it at
1h. Add `trend_ma` (e.g. {100, 200, 400}) to the scan grid, and note that
`hour_filter` (mechanism exists, default off) and `rsi2` mode remain candidates
for 15m/4h sweeps only.

## Hard constraints (non-negotiable)

1. **Create NEW file(s) only:** `optimizer.py` (plus any helper it needs).
   Do **NOT** modify `strategy.py`, `risk.py`, `backtester.py`, `config.py`,
   `collector.py`, `database.py`, `math_engine.py` — those are owned by the
   Lead Architect. Import from them.
2. **Data:** use existing `crypto_data.db` (BTC/ETH/SOL/BNB/DOGE now have
   ~375 days of 1h/4h). **Do NOT run collector backfills or the funding
   command** — one network job at a time; the full backfill is pending user
   approval.
3. **Final validation discipline:** the chronological LAST 20% of bars is the
   hold-out. You may optimize on the first 80% (train). Report hold-out
   metrics separately. Never re-run the optimizer after seeing hold-out
   results.
4. If the optimizer imports `backtester.run_backtest`, call it with
   `strategy="mean_reversion"` and `params=MeanReversionParams(...)`.
5. Append results to `docs/RESEARCH_NOTES.md` and update the
   directive's status below when done. Log your changes in
   `docs/CHANGELOG.md`.

## Work plan (modular steps — do in order, report after each)

### Step 1 — Sensitivity grid (train window = first 80% of bars)
Scan, for each of BTC/ETH/SOL/BNB/DOGE (1h, limit=9000):
- `z_entry ∈ {2.0, 2.5, 3.0}`
- `n ∈ {24, 48, 72}`
- `sl_atr ∈ {1.5, 2.0, 2.5}`
- TP modes: `at-mean` (current) and `half-distance` (TP = halfway from entry
  to mean — add via `params` only if expressible without touching strategy.py;
  otherwise approximate with a fixed-RR TP and note it)
- time stop: `1.5×HL` (current) and `fixed 12` / `fixed 24`

Output: a table per symbol (or a heatmap-style summary) of
`win_rate, profit_factor, sharpe, max_dd, avg_hold, trades`.

### Step 2 — Ranking
Select configs that are robust ACROSS symbols:
- train-window WR ≥ 50% **and** PF ≥ 1.1 **on at least 3 of 5 symbols**
- cap: nothing with trades < 60 in train (statistically meaningless)
Pick the top 3 configs by median (across symbols) expectancy.

### Step 3 — Hold-out validation (last 20% of bars, NEVER touched by Steps 1–2)
For each of the top 3 configs, on each symbol, report:
- WR + **Wilson 95% CI** (from `backtester` result `win_rate_ci`)
- PF, Sharpe, max DD, total return
- Monte Carlo `P(profit)` (already in `BacktestResult.monte_carlo`)
- walk-forward verdict (`mc_lean_pontiff_check`)

### Step 4 — Cost sensitivity
Re-run the top config on BTC with:
- `fee_bps = 5` (futures-tier) and slippage 0 — quantify the cost handicap
  by comparing expectancy vs the 10 bps spot run. (Note: config.py is owned;
  compute by monkeypatching `config.FEE_BPS` and `config.SLIPPAGE_BPS_TIERS`
  in your optimizer process only.)

### Step 5 — Report & recommendation
Write a section into `docs/RESEARCH_NOTES.md` with:
- the sensitivity table summary, top-3 configs, hold-out numbers
- one recommended config, or an explicit statement that none qualify
- acceptance check: **PF > 1.1 AND MC P(profit) > 0.7 AND WR ≥ 60% with CI
  lower bound ≥ 50% on hold-out** — anything less is reported as a negative
  result (that is a valid outcome; honesty beats a fitted number).

## Acceptance criteria
- New files only; owned files byte-identical before/after (verify with
  `git status` / mtimes).
- Hold-out numbers reported, not just train numbers.
- CHANGELOG.md + RESEARCH_NOTES.md updated.

## Status — EXECUTED 2026-08-15 (full protocol, honest negative result)
- [x] Step 1 — sensitivity grid (324 configs × 5 symbols, 1,625 train backtests)
- [x] Step 2 — ranking (**no config passed the gate**; best 2/5 symbols)
- [x] Step 3 — hold-out validation (top-3 configs, **0/15 symbol-runs meet the bar**)
- [x] Step 4 — cost sensitivity (10 bps spot −9.62% vs 5 bps futures −7.17%)
- [x] Step 5 — recommendation (**NOT ACCEPTED** — details in RESEARCH_NOTES §7)

**Result:** the 60–70% WR target is NOT achievable by tuning v2 on ~300 train
days of 1h. Every meaningful-sample config loses after costs; the
high-WR configs are 3–13-trade noise (Wilson CI-lo 12–44%). Data-first next
steps (more 1h history via paginated backfill, 15m depth, or a strategy
pivot to pairs/stat-arb) are documented in RESEARCH_NOTES §7. No
parameterization is recommended; re-running the optimizer on the same data
would be curve-fitting.
