# ADR-002: Adopt Trend Filter; Reject RSI(2) at 1h

- **Status:** Accepted
- **Date:** 2026-08-14
- **Deciders:** Lead Architect (Codebuff)

## Context

v1 mean reversion measured 36–42% WR with 61–64% of exits hitting the
stop-loss — the classic falling-knife failure (fading extremes against the
long-term trend). Web research (Connors RSI-2 family; 200-SMA trend reference)
proposed two fixes: (a) trade extremes only in the direction of the long-term
trend, (b) faster RSI(2) entries.

## Decision

1. **Adopt the long-term trend filter (ADOPT):** `trend_filter=True`,
   `trend_ma=200` bars — LONG entries require `close > SMA(200)`, SHORT entries
   require `close < SMA(200)`. Now the default in `MeanReversionParams`.
2. **Reject RSI(2) entry mode at 1h (REJECT):** `entry_mode="rsi2"` stays
   available as a parameter but is not the default. Rationale: A/B validation
   showed 19–30% WR / PF 0.14–0.25 / −50%+ returns on all 5 majors — the ATR
   stop is hit before the RSI(2) crossing at hourly frequency, and the 200-day
   (not 200-bar) regime Connors validated does not exist in our data.
3. **Funding-rate single-asset overlay (REJECT, research-backed):** Presto
   Research shows zero lagged single-asset predictive power. Not implemented.
4. **Time-of-day filter (PENDING):** mechanism added, default off; requires
   symbol-specific OOS validation (research: 22:00–23:00 UTC best, 03:00–04:00
   worst).

## Validation evidence (1h, 375 days, net costs, 5 majors)

| Config | WR range | Return range | Verdict |
|---|---|---|---|
| v1 baseline | 35.5–41.7% | −33.7 to −12.6% | negative |
| v2 trend-filter | 31.4–52.4% | −15.8 to −4.2% | improved, adopted |
| v2 rsi2+trend | 19.7–29.9% | −68.5 to −51.7% | rejected |

## Consequences

- **Positive:** fewer, higher-quality trades (BTC 130 → 42); every major's
  net return improved; cross-symbol consistency (5/5 better).
- **Negative:** trade count drops 3–4× (fewer opportunities); WR still below
  target on 4/5 symbols → Directive 001 (parameter optimization) now starts
  from the v2 baseline.
- **Risk:** the trend MA length (200 bars ≈ 8 days on 1h) is a new knob —
  must be scanned in the optimization workstream, not trusted blindly.
