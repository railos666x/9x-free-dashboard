# RESEARCH_NOTES.md

Findings, benchmarks, reference material, and trade-offs.

*Last updated: 2026-08-14*

---

## 1. Expectancy math (the win-rate constraint)

```
E[profit per unit risk] = W·R − (1−W)        R = avg win / avg loss
Breakeven win rate      = 1 / (1+R)
```

| Target WR | Max R:R for positive E | Example E |
|---|---|---|
| 60% | < 1.5 | W=60%, R=1.0 → +0.20 |
| 65% | < 1.86 | W=65%, R=1.0 → +0.30 |
| 70% | < 2.33 | W=70%, R=0.8 → +0.26 |

**Implication:** 60–70% WR is only coherent with R:R ≈ 0.8–1.1. A system with
R:R 1.5 (legacy SL 2×ATR / TP 3×ATR) is correctly optimized for ~40–45% WR —
the two targets cannot coexist. The legacy engine's own docs confirmed 33–40%
measured WR.

## 2. Strategy rationale (mean reversion)

- OU process: `dP = θ(μ − P)dt + σdW`; z-score entry at extremes; TP at mean.
- Half-life: OLS `ΔlogP = α + β·logP_{t−1} + ε`, `HL = −ln2/β`; require
  significant negative β (|t|>1.65) and HL < sample/4 (anti-spurious-reversion).
- **References:** Jegadeesh (1990) short-horizon reversal; Avellaneda & Lee
  (2010) OU z-score stat-arb; Chan, *Algorithmic Trading* (z-score/HL
  framework); Moskowitz-Ooi-Pedersen (2012) TS momentum (for trend filter
  contrast); McLean & Pontiff (2016) — OOS returns ~26% lower, ~58% lower
  after publication (our validation bar).

## 3. Measured diagnostics (v1 backtest, 375 days 1h, majors, net costs)

| Symbol | Trades | WR (95% CI) | PF | Total return | SL exits | TP exits |
|---|---|---|---|---|---|---|
| BTC/USDT | 130 | 37.7% (29.8–46.3) | 0.64 | −30.95% | 60.8% | 36.2% |
| ETH/USDT | ~138 | ~38% (see report) | ~0.7 | negative | ~62% | ~36% |
| SOL/USDT | 131 | 35.9% (28.2–44.4) | 0.71 | −21.06% | 64.1% | 35.1% |

- Walk-forward: folds stable 30–42% WR; BTC decay −2.6% (OK). **Conclusion:
  weak edge, not overfit.**
- Monte Carlo (BTC): P(profit) 0.8% → negative expectancy as configured.
- Cost burden: 21 bps round trip (10 bps fee × 2 + 0.5 bps slip × 2) ≈ 15–20%
  of a 1.5-ATR risk budget at 1h.

**Hypotheses to test next (Directive 001):**
1. Entry too eager: z_entry 2.0 → 2.5–3.0 filters low-quality extremes.
2. SL too tight: sl_atr 1.5 → 2.0–2.5 reduces SL-dominant exits (61–64%).
3. TP at mean too far: alternative = half-distance TP (raise WR, cut win size).
4. Regime filter too weak: adx_max 25 → 20.
5. Costs dominate: re-test at 5 bps (futures) to size the cost handicap.

## 4. Data status

| Item | Before | Now |
|---|---|---|
| 1h depth (majors) | ~44 days | ~375 days (+9,000 bars) |
| Funding history | none | table + collector command (not yet backfilled) |
| Symbols | 109 in DB / 92 unique cfg | 87 valid, deduped |
| Known gaps | 2 in BTC 1h | gap repair active in live loop |

## 5. Web research round 1 (2026-08-14) — findings & A/B validation

### Sources reviewed
- Connors RSI(2) family (quantifiedstrategies.com, toptradingstrategy.com,
  r/algotrading): entry RSI(2) < 10, exit on crossing 50 or time stop,
  **200-MA trend filter (longs only above)** — 75–79% win-rate claims on
  daily equities.
- Presto Research (2024) "Can Funding Rate Predict Price Change?": funding
  changes explain 12.5% of 7-day price variation, but **lagged R² ≈ 0 — no
  single-asset predictive power**. Cross-sectional stat-arb alpha
  (Kakushadze 101-formula style) is where funding value lives.
- Intraday seasonality: 22:00–23:00 UTC best hours for BTC; 03:00–04:00 UTC
  worst (Quantpedia, Quantified Strategies, Jasiak 2024).

### A/B validation (5 majors, 1h, 375 days, net costs)
| Config | BTC WR | BTC ret | BTC P(prof) | All-symbol WR range | All-symbol ret range |
|---|---|---|---|---|---|
| v1 baseline | 37.7% | −31.0% | 0.8% | 35.5–41.7% | −33.7 to −12.6% |
| **v2 trend-filter (ADOPTED)** | **52.4%** | **−4.2%** | **27.4%** | 31.4–52.4% | −15.8 to −4.2% |
| v2 rsi2+trend (REJECTED) | 19.7% | −68.5% | 0.0% | 19.7–29.9% | −68.5 to −51.7% |

**Conclusions:**
1. **Trend filter is a real, cross-symbol improvement** (fewer, better trades:
   BTC 130 → 42 trades). Confirms the falling-knife diagnosis. Now the default.
2. **RSI(2) at 1h crypto is a false lead** — the ATR stop is hit before the
   RSI(2) crossing (19–30% WR). Connors' rules do not transfer from daily
   equities. Mechanism retained (params `entry_mode="rsi2"`) for 15m/4h
   exploration; not the default.
3. **Funding overlay rejected for single-asset use** (research-backed).
   Funding value is cross-sectional → belongs to the parked stat-arb workstream.
4. Time-of-day filter implemented as a mechanism (`hour_filter`), default off —
   a symbol-specific value must be validated out-of-sample before enabling.

## 6. Open trade-offs

- **Spot vs perps:** perps enable shorts + carry but add funding cost and
  liquidation risk; spot removes funding but restricts to long-only (halves
  the strategy's opportunity set).
- **Polling vs WebSocket:** polling is simpler and crash-proof; WS is lower
  latency. Not yet a bottleneck at 1h.
- **SQLite vs TimescaleDB:** fine until multi-process writers / big scans.

## 7. Parameter optimization — DIRECTIVE-001 executed (2026-08-15)

**Protocol:** new file `backend/optimizer.py` (owned files byte-identical);
grid scanned on TRAIN = first 80% of bars (7200 of 9000 1h bars, ~300 days),
hold-out = last 20% (1800 bars) never touched by selection. 324 configs × 5
majors = 1,625 train backtests (4 workers, 51 s). Costs: 10 bps/side fees,
0 slippage. Train/hold-out slicing and the `half` TP mode (target halfway to
the rolling mean) were injected via in-process monkeypatching only.

**Grid:** z_entry ∈ {2.0, 2.5, 3.0} × n ∈ {24, 48, 72} × sl_atr ∈ {1.5, 2.0,
2.5} × trend_ma ∈ {100, 200, 400} × TP {at-mean, half} × time stop {OU-HL,
fixed 24}.

### Finding 1 — sample size collapses under the v2 trend filter

Trade counts per symbol in the 300-day train window:

| config family | trades/symbol |
|---|---|
| n=24, z=2.0 | 43–56 |
| n=24, z=2.5 | 20–29 |
| n=48, z=2.0 | 21–31 |
| n=72 (any z) | 0–14 |

Every config that achieves a meaningful sample (>30 trades) **loses money
after costs** (PF 0.42–0.99). The configs showing 60–82% WR (n=72 family)
trade only 3–13 times — those WR numbers are noise, and the Wilson CIs
confirm it (CI lower bounds 12–44%). The directive's 60-trade floor is
structurally unreachable at 1h: the trend filter cuts v1's ~130 BTC trades
to ~42 over 9000 bars.

### Finding 2 — no cross-symbol robustness

Best train config (z=2.5, n=24, sl=2.5, ma=400, at-mean, fixed-24): passes
the WR≥50/PF≥1.1 gate on only **2/5** symbols (SOL 64%/PF 1.27, DOGE
69%/PF 1.23; BTC 33%/PF 0.24, BNB 39%/PF 0.45, ETH 51%/PF 0.67). DOGE and
SOL show reversion edge; BTC and BNB lose consistently. No config passes on
≥3 symbols — the robustness gate fails for all 324.

### Finding 3 — hold-out (last 20%, never touched): NOT ACCEPTED

Top-3 configs, 0/15 symbol-runs meet the acceptance bar (PF>1.1 AND
P(profit)>0.7 AND WR≥60% with CI-lo≥50%). Best single runs: BTC 75% WR
(CI-lo 40.9%) with walk-forward flagging high OOS decay; SOL 83% WR / PF
3.64 (CI-lo 43.6%, 6 trades). All statistically indistinguishable from a
coin flip at these sample sizes. Hold-out trade counts: 3–13.

### Finding 4 — costs are not the killer

Top config on BTC full window: 10 bps spot −9.62% vs 5 bps futures −7.17%.
Fees+slippage explain ~2.5 pp of a 9.6 pp loss. The edge itself is absent,
not merely fee-eaten.

### Verdict

**NEGATIVE RESULT — no parameterization of the v2 1h mean-reversion
geometry clears the acceptance gate.** This is the honest answer to the
60–70% WR target on current data: it cannot be reached by tuning this
strategy on 300 train days. What the data says would change that:

1. **More history per symbol** — 375 days of 1h ≈ 300 train days is too
   short for a trade-sparse strategy. Binance serves ~1,000 days of 1h
   (paginated): tripling the train window is the single highest-value data
   fix, and it stays inside the no-curve-fitting protocol (optimize on
   earlier 80%, validate on untouched later 20%).
2. **15m depth** — would multiply trades ~4× (43–56 → 170+ in a similar
   window) but current 15m depth is only ~15 days; needs a real backfill.
3. **Per-symbol parameterization is rejected** (DOGE/SOL edge vs BTC/BNB
   losses) — selecting the good coins IS the curve-fit this exercise exists
   to catch.
4. **Strategy pivot candidates** (research already parked in STRATEGIES.md):
   pairs/stat-arb (cross-sectional, fits the funding finding) and an ML
   ensemble overlay — both are genuinely new workstreams, not re-tunes.
