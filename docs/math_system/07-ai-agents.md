# 07 — The Three Production AI Agents (`ai_agents.py`)

Three distinct agent prompts run on the same Groq transport as the news
analyst (`news_analyst.chat_json` — retry/backoff, model fallback, never
raises). They are **on-demand or gated — never in a hot loop**:

1. **News & Catalyst Sentiment** — score one headline/article.
2. **Deep Research & HODL Portfolio** — day-trade it or hold it long-term.
3. **Price Action & Chart Confluence** — verify a news trade against the
   chart to avoid liquidity traps.

```
headline/article ──▶ ① news sentiment ──▶ {sentiment, impact, black_swan, duration, catalyst}
catalyst (from ①) ──▶ ② hold research ──▶ {tailwinds, risk, hold_recommendation, confidence}
price + S/R + news ──▶ ③ chart confluence ─▶ {is_valid_setup, entry_strategy, stop_loss}
       │                                            │
       │  (opt-in gate)                             ▼
       └── gate_news_trade → source B proposals → arbitrator → gates → execution
```

Every agent:
- loads `GROQ_API_KEY` from `.env` — never hardcoded,
- validates the model's JSON against its schema (a schema violation
  returns `None` instead of trusting garbage),
- **never raises**: on failure it returns `None` and the caller falls back
  to the deterministic path,
- journals its structured answer into the `ai_analyses` table (see
  `database.record_ai_analysis`) so the dashboard and the trade explainer
  can show *why* an agent said what it said.

Config: `AI_AGENTS_ENABLED` (default true), `AI_AGENT_MODEL` (defaults to
`GROQ_MODEL`), `AI_CONFLUENCE_GATE` (default **false** — it changes which
trades fire, enable deliberately).

---

## ① News & Catalyst Sentiment — `agent_news_sentiment(headline, content)`

**Purpose.** Instantly evaluate breaking market news and extract the
structural impact on price action. Ignores sensationalism; focuses on
liquidity shifts, institutional adoption, protocol upgrades and
macroeconomic data.

**Prompt.** `Analyze the following news: Headline: {headline} Context:
{content}` with a strict system prompt demanding JSON-only output.

**Output schema** (invalid → `None`):

```json
{
  "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
  "impact_score": 0.85,            // 0.0–1.0; 0.8+ = major liquidity event
  "is_black_swan": false,
  "expected_duration": "IMMEDIATE" | "MULTI_DAY" | "LONG_TERM_MACRO",
  "core_catalyst": "one-sentence summary of the actual market driver"
}
```

**Consumers.** Dashboard `/api/news/analyze`; the catalyst feeds agent ②.
The endpoint also accepts `url=` — when `content` is empty the article is
scraped to markdown by Firecrawl (`web_scraper.get_article`, cached 24 h in
the `article_content` table) so the agent reasons over the full article
instead of a bare headline: `/api/news/analyze?url=<article>&headline=<title>`.
`/api/news/article?url=` returns the raw scraped markdown.

---

## ② Deep Research & HODL Portfolio — `agent_hold_research(symbol, catalyst)`

**Purpose.** When a major catalyst is detected for an asset, decide whether
it is a strategic long-term hold or just a short-term trading vehicle.

**Prompt.** `A major catalyst has just been detected for {symbol}. Catalyst:
{core_catalyst}` — evaluated on tokenomics / fiat-inflation resistance,
on-chain utility and ecosystem growth, and counter-party risk.

**Output schema** (unknown recommendation coerced to `TRADE_ONLY`;
confidence clamped 0–100):

```json
{
  "macro_tailwinds": "brief description of supporting macro environment",
  "risk_falsification": "biggest risk that invalidates this trade",
  "hold_recommendation": "ACCUMULATE_AND_HOLD" | "TRADE_ONLY" | "AVOID",
  "confidence_percentage": 80
}
```

**Consumers.** Dashboard `/api/portfolio/analyze`, journal listing at
`/api/portfolio/holds`. The recommendation is advisory — it does not
override the risk engine; the news gate and arbitrator still apply.

---

## ③ Price Action & Chart Confluence — `agent_chart_confluence(conn, symbol, direction)`

**Purpose.** A news-driven trade signal must be verified against the
current chart structure to avoid liquidity traps. It replaces the naive
"news said bullish → buy" path with a structural check.

**Rules baked into the prompt:**
- Never buy immediately below heavy resistance.
- Never short immediately above heavy support.
- News BULLISH but price dumping through support → flag
  `"DIVERGENCE / TRAP"`.

**Inputs** (pulled from the DB, not hand-fed):
- `price` — last 1h close,
- `change_1h` — last-close vs previous-close %,
- `support` / `resistance` — `detect_support_resistance()` (below),
- `news_sentiment` — stance from the Groq analyst (`news_analyst.analytics_for`),
- proposed `direction` (LONG / SHORT).

**Output schema:**

```json
{
  "is_valid_setup": true,
  "rejection_reason": "",
  "entry_strategy": "MARKET_BUY" | "WAIT_FOR_RETEST" | "ABORT",
  "calculated_stop_loss": 95.5
}
```

`calculated_stop_loss` is placed just outside support/resistance — a
structurally-derived stop that replaces the default ATR stop for agent-
verified trades.

**Caching.** One chart verification per symbol per
`NEWS_ANALYST_CACHE_TTL` (10 min) — the 5-min bot loop must not re-ask the
model for the same symbol every cycle. `force=True` bypasses (used by the
dashboard verify button).

### Support / resistance detection

```text
pivot high:  high[i] > max(high[i-swing..i-1]) and ≥ max(high[i+1..i+swing])
pivot low:   low[i]  < min(low[i-swing..i-1])  and ≤ min(low[i+1..i+swing])

resistance = min(pivot highs > price)  → fallback max(last-24 highs)
support    = max(pivot lows  < price)  → fallback min(last-24 lows)
```

Default `lookback=200` 1h candles, `swing=5` bars. Fallbacks guarantee a
result whenever data exists (no data → `(None, None)`).

### The confluence gate — `gate_news_trade(conn, symbol, direction)`

When `AI_CONFLUENCE_GATE=true`, the news-driven source B
(`proprietary_signals._news_proposals`) verifies every LONG/SHORT proposal
through agent ③ **before** the arbitrator sees it:

```text
news signal (LONG/SHORT)
   → agent ③ chart check
   → is_valid_setup=false  → proposal dropped (DIVERGENCE / TRAP)
   → is_valid_setup=true   → proposal kept, S/R noted in reasons
   → agent unavailable     → proposal kept (the gate never blocks a trade
                             it cannot run; the news gate + arbitrator
                             still apply their own checks)
```

The gate is off by default and opt-in: enabling it changes which trades
fire, so it is a deliberate configuration decision, not a default.

---

## Journaling (`ai_analyses` table)

Every agent answer is journaled via `database.record_ai_analysis`:

| column | meaning |
|---|---|
| `agent` | `news_sentiment` \| `hold_research` \| `chart_confluence` |
| `symbol` | asset the analysis concerns |
| `request` | headline / catalyst / trade context (truncated 400) |
| `verdict` | normalized outcome (BULLISH, ACCUMULATE_AND_HOLD, VALID, …) |
| `payload` | the full structured answer as JSON (truncated 8000) |
| `model` | which Groq model answered |
| `created_at` | UTC timestamp |

Read back with `database.load_ai_analyses(conn, agent=..., limit=...)`;
exposed at `/api/portfolio/holds`. Corrupt payloads degrade to raw text —
the journal never raises.

## Endpoints

| Endpoint | Agent | Notes |
|---|---|---|
| `GET /api/news/analyze?headline=&content=&symbol=` | ① | journaled |
| `GET /api/portfolio/analyze?symbol=&catalyst=` | ② | journaled |
| `GET /api/trade/verify?symbol=&direction=` | ③ | `force=True` (fresh) |
| `GET /api/portfolio/holds?limit=&agent=` | — | journal listing |

## Failure semantics (everything is fail-open except the opt-in gate)

- No `GROQ_API_KEY` / `AI_AGENTS_ENABLED=false` → all agents return `None`
  (endpoints report `ok: false`).
- Model returns schema-violating JSON → `None` (never trusted).
- Rate limit / 5xx → transport retries (3× backoff) then the fallback
  model once; still failing → `None`.
- The confluence gate is the *only* component that can block a trade, and
  it only blocks when it successfully ran: failure → pass.

## Verification

- `tests/test_ai_agents.py` — 39 offline checks (schema validation,
  S/R detection on synthetic candles, gate off/valid/trap/failure, cache,
  journal round-trip). Run with all other suites via
  `python tests/test_ai_agents.py`.
- Live smoke: `agent_news_sentiment` scored the BlackRock spot-ETH-ETF
  headline BULLISH (impact 0.85, MULTI_DAY); `agent_hold_research` →
  ACCUMULATE_AND_HOLD (80%); `agent_chart_confluence` verified a BTC LONG
  as `WAIT_FOR_RETEST` with a stop just outside support.
