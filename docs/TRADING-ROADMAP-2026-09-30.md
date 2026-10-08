# Trading Improvement Roadmap — 2026-09-30

Mission: automation with fundamentals so strong it *feels* like passive income —
the bot trades, the user watches. Social/government-official monitoring comes later.
This document is the plan; the changelog records what actually ships.

---

## 1. Reality check first (the honest math)

The stated goal "10k → 15–20k in a day" is **+50–100% per day**. No levered
day-trading system on record sustains that; the research is unambiguous:

- Barber & Odean / Jordan & Dickerson day-trader studies: the large majority of
  day traders lose money; of Brazilian futures day traders who persisted 300+
  days, **97% lost money** (only ~1.1% earned more than a minimum wage).
- The profitable minority are not better predictors — they **control risk per
  trade, cut losses fast, and size positions to survive** (Barber et al.,
  "Cross-Section of Speculator Skill").
- Compounding at +50%/day turns $10k into ~$580k in 11 trading days and absurd
  numbers shortly after — if such an edge existed, it would self-destruct
  (liquidity, slippage, counterparty limits) long before that.

**What IS achievable and compounds beautifully:** a vol-targeted, edge-filtered
system making **+0.3–1.0% per day on average** with hard drawdown control.
At +0.5%/day compounded, $10k → $18k in ~30 days, $10k → $100k in ~4.7 months.
That is the honest version of "passive income from automation" — and it is the
goal this plan optimizes for. Today's live numbers already point that way:
64.7% AI-only win rate, expectancy +31.34/trade on ~1% risk — that run-rate is
roughly +0.5–1%/day when trade frequency holds.

The research consensus on what separates the 3% winners from the 97%:
1. Position sizing discipline (never let one trade matter much).
2. Asymmetric payoffs (win bigger than you lose — our R/R floor already enforces ≥1.2).
3. Trading with persistent, well-documented edges (trend/momentum, event drift) instead of prediction.
4. Ruthless cost control (spread/commission/slippage kill marginal strategies).
5. Survival machinery: circuit breakers, kill switches, no revenge trading.

Our codebase already has #1, #2 and #5 (risk.position_size 1%/2% cap, R/R floor,
4-level circuit breaker, daily-loss + drawdown guards). The plan below closes the
remaining gaps (#3, #4) and industrializes monitoring.

---

## 2. Problem pinpoints (audit of today's system)

Ranked by damage to the compounding goal:

| # | Problem | Evidence | Damage |
|---|---------|----------|--------|
| P1 | **Sizing ignores volatility & edge quality.** Fixed ~1% risk regardless of instrument vol, confidence (tc), corroboration, or strategy health. | `execution.execute_intent` L1364–1375 — risk_pct scaled only by a static multiplier, capped 2%. | Big winners undersized, chop trades oversized; returns noisy instead of steady. |
| P2 | **No realized-edge feedback loop.** The bot doesn't know which news tags/venues/sessions/regimes historically made money when deciding today. Forensics exist but is manual, not wired into sizing/gating. | `scripts/trade_forensics.py` is offline-only. | Compounds slow; repeating low-expectancy buckets (ranging 44.4%, broker_closed 50%). |
| P3 | **Backtest vs live divergence risk.** Backtester and live execution are separate code paths; a strategy that backtests well may not fill the same live (spread, slippage, min-notional). | `backtester.py` vs `execution.py` maintain parallel logic. | False confidence in deployment. |
| P4 | **Crypto legs have no venue-side stops** — software-managed only; a process outage = unbounded risk per leg. | Documented limitation, Binance spot testnet. | Tail risk on the fastest legs. |
| P5 | **No portfolio-level vol targeting.** Five open trades can be 5× one-directional AUD risk in correlated fashion (today's AUD basket proved the pattern — it worked, but it was luck-concentration, not design). | MAX_PER_CURRENCY=3 counts positions, not risk units. | Correlated drawdowns; equity curve lumpy. |
| P6 | **No evaluation window metrics.** Win rate and PnL are tracked, but no rolling Sharpe/Sortino/Calmar/expectancy-per-regime surfaced on the dashboard, so "is the machine healthy today?" is unanswerable at a glance. | `server.py /api/health` exists; metrics are basic. | User can't verify "passive income" without me. |
| P7 | **Cost model unmeasured.** Spread/slippage/fees are journaled inconsistently; no per-venue cost attribution. | orders.fees often NULL. | Marginal trades that are net-losers after costs keep being taken. |
| P8 | **News edge is single-shot.** Every headline is evaluated once at arrival; no follow-through modeling (does a 4-outlet bullish story drift +2h? is the edge at 1min, 15min or 4h?). | news_analyst pipeline. | We capture small slices of moves the data says are bigger. |
| P9 | **No champion/challenger testing.** New signals (social feed later) can only be tested by turning them on live. | — | Blocks safe expansion. |

---

## 3. Research-backed directions worth adding (with sources)

**A. Volatility-scaled time-series momentum as a core sleeve** — Moskowitz, Ooi &
Pedersen (2012, *Journal of Financial Economics*): past 1–12m return predicts next
1m return across 58 liquid instruments; position sized ∝ 1/vol; Sharpe ~1.3;
works best in extreme markets (crisis alpha). Baltas & Kosowski: fitting a linear
trend on the price path beats sign-of-return signals and cuts turnover. → For us:
add a daily 15m/1h TSMOM overlay on majors (BTC/ETH/gold/majors FX) with
inverse-vol sizing. It is the most robust documented edge in existence and it is
*uncorrelated* with our news-driven trades.

**B. Kelly-fraction sizing with a hard ¼ cap** — Kelly maximizes growth; full
Kelly volatility is intolerable, ¼–½ Kelly captures most growth at a fraction of
the drawdown. Use rolling live stats (win rate, avg win/loss per bucket) to set
fractional Kelly *per news-bucket*, floored/capped to 0.25–1.5% risk. The
backtester already supports kelly_now (`backtester.py` L315) — wire the same into
live sizing (P1+P2 solved together).

**C. Event/tweet drift trading** — Filippou et al. 2025 (*J. Int. Money &
Finance*): informative Trump tweets → USD appreciation, tradable intraday;
Gholampour & van Wincoop (NBER w23293): tweets about EUR/USD carried real
information content; Federal Reserve FEDS 2023-034: overnight social sentiment
predicts next-day equity returns. → The planned "government officials' posts"
module has genuine academic support as a *drift* signal (minutes-to-hours), best
consumed with our existing corroboration gate to filter parody/noise.

**D. Overnight/session effects** — persistent, documented (equity overnight vs
intraday return split; FX London/NY overlap volatility patterns). Cheap to add
as a session-aware gate: size up in the session where our fills historically
won (forensics already buckets by regime; add session).

**E. Cost-aware entry veto** — expected edge must clear 2× round-trip cost
(van der Merwe's "minimum edge" heuristic; standard market-microstructure
practice). Solves P7: journal per-venue cost rates, veto entries whose TP
expectation doesn't clear costs at the measured slippage percentile.

---

## 4. Phased plan (each phase independently profitable)

### Phase 1 — Industrialize what we have (1–2 days, ship first)
1. **Daily digest script** `scripts/daily_digest.py` — appends to
   `docs/MONITORING-LOG.md`: AI-only win rate (7d/30d), exit-reason economics,
   open basket floating P/L, guard status, cost totals. Answers "is the machine
   healthy?" every morning without me. *(shipped this round)*
2. **Journal `fees` + slippage estimate on every close** (P7): fill vs mid at
   signal time; per-venue weekly cost table in the digest. Gate: expected TP ≥
   2× measured cost (config EDGE_COST_MULT=2).
3. **Session tag on orders** (asia/london/ny) — one column, feeds forensics;
   later session-aware sizing.

### Phase 2 — Vol-aware & edge-aware sizing (the growth engine)
4. **Inverse-vol position scaling** (P1): risk_pct × (target_vol / realized_vol),
   clamped [0.5×, 2×] of base 1%. ATR(14) on the entry timeframe, already computed
   by `_default_levels`.
5. **Rolling-bucket Kelly** (P2): 30-trade rolling expectancy per
   (venue, regime, news-tag) bucket → fractional-Kelly multiplier, capped [0.5×, 1.5×],
   floored to 0 for buckets whose 95% CI on expectancy is negative (ranging-regime
   gate emerges naturally from data instead of a hard rule).
6. **Portfolio vol targeting** (P5): cap total open risk-units (sum of per-trade
   risk amounts) at e.g. 3% equity; correlated-currency groups (AUD basket!)
   count as one group with a group cap.

### Phase 3 — Second alpha sleeve: TSMOM overlay
7. Daily/4h inverse-vol momentum on 8–12 majors, separate risk budget (0.5%/trade),
   trades only when no conflicting news position exists. This is the sleeve that
   makes the equity curve *look* passive: steady trend capture independent of the
   news cycle. Backtest via existing backtester first (P3: walk-forward, costs on).

### Phase 4 — Social/government feed (the user's planned addition)
8. **Sources**: X API pay-per-use ($0.005/post read — no free tier since Feb 2026);
   cheaper: Truth Social public pages, official .gov press-release RSS
   (Treasury/USTR/White House, central-bank feeds), official Telegram channels —
   all free/cheap and higher signal-per-dollar than scraping X broadly.
   Start with an allowlist of ~20 officials whose statements move USD/gold/oil
   (President, Treasury Sec, Fed Chair, trade reps).
9. **Pipeline**: post → same news_noise dedupe/corroboration gate → LLM scoring →
   **drift model**: act on published-drift windows (first 5–60 min USD
   appreciation per Filippou; next-day equity per Fed paper) rather than instant
   market orders; always with venue-side stops (P4: Binance OCO first).
10. **Champion/challenger** (P9): shadow-trade the feed for 2 weeks paper-only,
    journal everything, require 95% CI on expectancy > 0 before live risk.

### Phase 5 — Trust & telemetry
11. Dashboard "Passive Income Score": rolling 7d return vs target, max DD, guard
    trips, digest link — one number the user can check daily.
12. Alerting (Telegram/email) only on: guard trip, broker_down, fill failure,
    digest anomaly — silence otherwise. Passive means we hear about problems,
    not about every trade.

---

## 5. Metrics of success (30 days)

- Rolling 30-day net return ≥ +8% at ≤ 8% max drawdown (Calmar ≥ 1).
- AI-only win rate sustained ≥ 60%; zero entries failing the cost gate.
- 100% of trades journaling fees/slippage; per-venue cost table populated.
- Zero unattended failures: digest produced every day, guards verified weekly.

## 6. What we deliberately do NOT do

- No grid/martingale/averaging-down (ruin machines).
- No leverage escalation to chase the 10k→20k/day number; the honest compounding
  path beats it by month 2 with survivable risk.
- No prediction models without walk-forward evidence and cost modeling.

— Buffy, 2026-09-30. Sources: Moskowitz–Ooi–Pedersen 2012; Baltas–Kosowski 2013;
Barber et al. 2011/2014 day-trader studies; Jordan–Dickerson 2003; Filippou et
al. 2025; Gholampour–van Wincoop NBER 2017; Federal Reserve FEDS 2023-034.
