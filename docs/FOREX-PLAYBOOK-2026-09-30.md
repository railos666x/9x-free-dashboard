# FX Playbook — mastering the forex sleeve — 2026-09-30

How the bot trades FX today, what the new Forex dashboard view tells you, and
the rules that make FX profitable as part of the automation goal. Companion to
[TRADING-ROADMAP-2026-09-30.md](TRADING-ROADMAP-2026-09-30.md).

---

## 1. What the bot already has (and always applies to FX)

| Layer | Module | What it does |
|---|---|---|
| DXY master filter | `backend/dxy_filter.py` | Never trade a USD pair against the dollar index's trend/phase (dollar smile: risk-off surge, trough weakness, US-outperformance strength). SMT divergence warnings near DXY extremes. |
| Session awareness | `backend/ai_confirm.py` | Asian ×0.5, London ×1.0, NY-overlap ×1.0, NY ×0.75, dead zone ×0.0. Asian can be fully blocked by config. |
| News corroboration | `backend/news_noise.py` | FX signals need ≥2 outlets (or one ≥0.65-score story) — kills single-outlet noise. |
| Counter-trend veto | `ai_confirm` / `news_analyst` | No fighting the 1h regime. |
| Risk sizing | `backend/risk.py` | ~1% of equity per trade to the SL distance, capped 2%. R/R ≥ 1.2 enforced. |
| Guards | `circuit_breaker.py`, `loss_guard_status` | Daily-loss + drawdown guards, tiered risk step-down. |
| MT5 discipline | `execution.MT5Broker` | Ticket-scoped closes (no phantom hedges on the Exness hedging account), venue-side SL/TP attached at entry. |

## 2. What the new Forex view adds (`/api/forex/overview`, sidebar → Forex)

- **Session strip** — live session, liquidity %, countdown to the next session,
  weekend flag, and a **synthetic USD index** (real ICE DXY weights over the 6
  majors, 24h). Read it before anything else: USD direction is the tide; pairs
  are boats.
- **Currency strength (24h)** — every pair's 24h change credits its base and
  debits its quote. Trade the *strong vs weak* axis, not absolute direction.
- **Open FX exposure** — net short/long per currency across ALL open positions.
  This is the AUD-basket detector: 4 short-AUD positions is really ONE big bet
  on AUD down. Warning appears at concentration ≥ 3.
- **Pair analytics table** — trend (EMA50/200 stack), momentum (RSI), ATR%,
  best sessions per pair, carry bias.

## 3. The rules (how the sleeve compounds)

1. **Trade with the tide**: strong-vs-weak axis + DXY filter + regime in
   agreement → highest-grade setup. Two out of three → half size. One → skip.
2. **Sessions are edge, not decoration**: London open and the NY overlap produce
   most trend; Asian ranges suit mean-reversion only; dead zone is blocked.
3. **Respect carry**: overnight swap is a small persistent tailwind/headwind —
   MT5 swap points are shown live (dashboard + digest). Never pay big negative
   carry for a "meh" setup; across weeks it silently eats expectancy.
4. **Correlated risk is one trade**: check the exposure card before letting the
   bot add a same-currency position; MAX_PER_CURRENCY caps count, the exposure
   card shows net direction (roadmap P5 adds a true group cap).
5. **Kill noise at the gate**: FX is the most rumor-prone market; the
   corroboration gate is why the AI-only FX win rate holds up.

## 4. FX economics (why this sleeve matters)

- Deepest, most liquid market in the world → lowest slippage of anything we trade.
- ~$1k risk-per-trade at 1% behaves *linearly* (no coin-style gap chaos in
  majors), so the equity curve is smoother — that is the "passive" feel.
- 24/5 with the 300s cycle → the bot catches London/NY moves while you sleep.
- Expected profile from the journal so far: FX exits skew to time-decay
  (small-positive) with fewer but real TP hits — steady drip, not lottery.

## 5. Next upgrades for the sleeve (roadmap hooks)

- True portfolio-level risk cap for correlated currency groups (P5).
- Inverse-vol + fractional-Kelly sizing (P1/P2) — will naturally favor
  high-liquidity majors where our fills are cheapest.
- Carry + momentum hybrid screen (academic "carry trades in FX" literature —
  be aware carry unwinds violently in risk-off; the DXY smile filter is the guard).
- Central-bank RSS watcher (roadmap Phase 4) — rate-decision drift is the
  highest-grade FX event there is.

— Buffy, 2026-09-30. Sources for FX microstructure/session claims: BIS FX
turnover surveys; ICE US Dollar Index construction; standard carry-trade
literature (e.g. Brunnermeier–Nagel–Pedersen 2008).
