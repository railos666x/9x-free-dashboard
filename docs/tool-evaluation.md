# Tool Evaluation: Free AI / Trading Tools

*Date: 2026-08-15 · Evaluation only — no code changed. Research current as of Aug 2026.*

Target architecture: **dashboard (TS) · data pipeline · verification · two signal
sources (AI analysis + proprietary) → one execution path**. Market: stocks and/or
crypto (not locked in).

---

## 1. Current code vs. target — gap map

| Architecture piece | Status | Where it lives today | Gap / note |
|---|---|---|---|
| Dashboard | ✅ exists | `web/dashboard/` (vanilla JS + ECharts, served at `/`) | **Not TypeScript** — it's plain JS. Views: Dashboard, Assets, Portfolio, Bots (backtest), Signals |
| Data pipeline | ✅ exists | `collector.py` (Binance REST klines + funding, backfill + live loop w/ gap repair), `macro_data.py` (Yahoo), `news.py` (RSS + CryptoPanic) → SQLite (WAL) | Polling REST (5s), single venue (Binance only), no fallback source. `websockets` is in requirements but **unused** — WS is a roadmap item |
| Verification | ⚠️ implicit only | `risk.py` sanitizers, `MIN_CANDLES`, liquidity floor + regime/vol filters in `strategy.py`, freshness decay in `math_engine.py` | **No first-class data-quality gate**: no staleness check on live feed, no spike/outlier detection, no cross-source sanity, no "reject bad candle before signal" step |
| Trade decision — AI analysis of live data | ✅ exists | `math_engine.py` (Bayesian fusion, regime, Kelly, confluence) + `strategy.py` (mean reversion) + `factors.py` + LLM news overlay in `reviewer.py` | This is signal source #1 |
| Trade decision — proprietary signals | ❌ missing | — | **No second, independent signal source.** `signals.py` is one pipeline, not two |
| Unification / arbitration of both sources | ❌ missing | — | No agreement gate, no dedup / conflict resolution between sources |
| Execution | ❌ missing | `risk.py` only *models* costs/sizing; `portfolio.py` only *reads* balances | **The biggest gap**: no order placement, no position/order state, no trade journal, no broker gateway, no paper-trading loop |

**Bottom line:** the intelligence layer is the strong part. Execution, the
verification gate, and the second signal source are all missing; the dashboard
exists but in JS, not TS.

---

## 2. Tool verdicts

| Tool | Fit | Why | Recommended role in my project |
|---|---|---|---|
| **Alpaca** | ★★★★★ | Free paper trading (no brokerage account needed), commission-free stocks/options/**crypto** — matches "stocks and/or crypto". Free real-time **IEX** market-data tier (SIP is paid $99/mo). SDKs: `alpaca-py` (Python) + `@alpacahq/alpaca-trade-api` (TS) — matches your Python backend / TS frontend. WebSocket streaming. | **Execution gateway + paper trading.** Fills the biggest gap. Also a second market-data source (for verification cross-checks) and the on-ramp to stocks. |
| **Freqtrade + FreqAI** | ★★☆ | Free, open-source (v2026.3), Python, built-in FreqAI adaptive ML (feature engineering + retraining), REST API + WebSocket + Telegram control. **Crypto-only** (via ccxt) — fails the "possibly stocks" requirement. Wants to **be the whole bot**: its own strategy + order loop, so feeding it as one of two signal sources into *your* execution path fights its design. | **Skip as a component.** Steal the patterns: FreqAI feature/retraining cadence and its REST API shape are great references for your source #2. Revisit only if you commit to crypto-only *and* accept freqtrade as the engine itself. |
| **QuantConnect** | ★★★☆ | Free tier: cloud backtesting on free minute-to-daily datasets across equities/forex/crypto/futures/options. Live trading + LEAN CLI need paid plans (~$60–120/mo). Strategies are written in QC's own Python/C# framework — re-implementation, not reuse. You already have a v2 backtester (walk-forward, Monte Carlo, Wilson CI) on your own data. | **Depends on the stocks expansion.** If you add equities, use the free tier as an independent historical dataset + second validation harness for strategies. Not part of the runtime path. |
| **Gemini API** | ★★★★ | Free tier on Flash models (2.5/3 Flash) — but Google cut free limits hard in late 2025 (order of ~tens of requests/day on Flash; Pro is pay-per-token). Fine for low-frequency analysis, not per-tick. Exposes an **OpenAI-compatible endpoint** → drops into the existing `reviewer.py` LLM overlay (`LLM_BASE_URL`/`LLM_MODEL`) with zero code change. | **AI signal source (the "AI analysis of live market data") + news sentiment**, run batched on a slow cadence (e.g. every 15–60 min) to stay inside free limits. || **Ollama** | ★★☆ | Local, open-source, free forever, no rate limits — but every model is a multi-GB local download and speed/quality depend on your hardware. For a dashboard + cloud bot ("not much local"), the storage and ops cost isn't worth it. | **Skip.** The same OpenAI-compatible slot is filled by cloud backends below — no local downloads, no storage hit. |
| **Free cloud LLM backends (OpenRouter / Groq / DeepSeek)** | ★★★★ | All OpenAI-compatible → drop into the existing `reviewer.py` overlay (`LLM_BASE_URL`/`LLM_MODEL`) with zero code change. **OpenRouter**: 20+ free models (DeepSeek R1, Llama 3.3 70B, Qwen3) behind one key, ~50 req/day free (1,000 after a small top-up). **Groq**: very fast inference, ~1,000 req/day free. **DeepSeek**: no free tier, but the budget pay-as-you-go leader — good last-resort fallback. | **Interchangeable AI-analysis backends.** Primary = Gemini Flash (free tier); fallback = OpenRouter free models; fast-path = Groq for verification calls. A configured failover chain, not a local install. |

**Calls:** **Use** Alpaca (execution) + Gemini and OpenRouter/Groq (AI analysis, all cloud, no local storage). **Skip** Ollama (storage/hardware) and Freqtrade. **Depends on stocks expansion** QuantConnect.

---

## 3. Unifying the two signal sources → one execution path

```
Source A (exists): signals.py → math_engine/strategy/factors
        └─ emits canonical Proposal dict {symbol, direction, entry, SL, TP,
           confidence, source, ts, window}
Source B (new): proprietary_signals.py (your external/proprietary generator)
        └─ emits the SAME Proposal shape (one schema, both sources)

                    ▼
   arbitrator.py — the ONLY place two sources meet
     • agreement gate: both sources must agree on direction before a
       trade is allowed (MIN_SOURCES=2, per-symbol override)
     • conflict → HOLD + log (never pick a winner by coin-flip)
     • intent_hash = sha256(symbol|direction|entry_ref|window) → dedup:
       an intent already seen within its TTL is skipped — this is what
       stops two sources firing duplicate orders for the same move

                    ▼
   verification.py — data-quality gate (runs before signals AND before execution)
     • staleness: last candle age ≤ 2× interval cadence, else HOLD
     • spike check: |move| > k×ATR without volume confirmation → reject
     • cross-source price sanity (Binance vs Alpaca) when both connected
     • NaN / zero-volume / zero-price guards

                    ▼
   execution.py — the ONLY module allowed to touch a broker
     • broker gateway interface: PaperBroker (Alpaca paper) + BinanceBroker
       (signed calls — pattern already exists in portfolio.py)
     • sizing via existing risk.py (Kelly cap, slippage tiers)
     • records orders + fills in a new SQLite `orders` table (trade journal)
     • idempotent by intent_hash: same intent can never place two orders
```

Key properties:
- **AI provider is swappable** — the news/AI overlay already speaks any
  OpenAI-compatible endpoint. Adding Gemini, OpenRouter, or Groq is a `.env`
  change (`LLM_BASE_URL`/`LLM_MODEL`), not a code change. A failover chain
  (primary → fallback → rule-based) costs nothing and needs no local models
  or disk space.
- **One canonical schema** (`Proposal`) — `signals.py` already returns almost this
  shape, so source A is ~free to convert; source B is a thin adapter.
- **Agreement is a hard gate**, not a weighted vote — matches "both need to trigger
  trades" exactly; disagreement degrades to HOLD, never to a lone-source order.
- **Dedup lives at the intent level** — duplicate/conflicting orders are impossible
  by construction, regardless of source cadence.
- **Execution is a single module** — one place for risk, one place for the journal,
  one place to swap paper ↔ live (Alpaca paper now, Alpaca live or Binance later).

---

## 4. Suggested order of work

**Progress (2026-08-15): items 1–4 implemented and LIVE.** `execution.py`
(paper/Alpaca/Binance brokers + idempotent order journal + SL/TP position
management + reconciliation incl. foreign-fill flagging), `verification.py`
(data-quality gate), `arbitrator.py` (agreement gate + intent-hash dedup),
`proprietary_signals.py` (source #2, default off), `bot.py` (orchestrator +
`loop` CLI), `/api/orders` + `/api/bot/run` + `/api/bot/status`, and the
dashboards. **Also done since:** the news verification layer (the
"confirmation AI" — `reviewer.verify_trade` vetoes/confirms/big-move-stands-
aside between agreement and execution, journaled for stats); the free-LLM
chain per this doc's calls (Gemini `gemini-3.1-flash-lite` primary + optional
OpenRouter/Groq fallbacks, 10-min throttle for free tiers); Alpaca paper
broker switched on (`EXECUTION_BROKER=alpaca`, $100k paper account, sizing
from account equity); the Alpaca 422/403 bug fixed (unsupported assets held,
not fired). Remaining: OpenRouter/Groq keys (optional — Gemini alone works),
threshold tuning once veto history accumulates, Binance live keys.

1. **Execution layer (Alpaca paper)** — `execution.py` + broker gateway + `orders`
   table. Highest value, unblocks everything downstream. ✅ done
2. **Verification gate** — `verification.py` between data→signals and
   intent→execution.
3. **Proprietary signal source #2** — `proprietary_signals.py` emitting the
   Proposal schema (prototype with FreqAI-style features or a scheduled
   cloud-LLM analysis through the OpenAI-compatible overlay).
4. **Arbitrator** — agreement gate + intent-hash dedup.
5. **TS migration / dashboard additions** (UI agent territory) — bot status +
   live order view once `orders` exists.
