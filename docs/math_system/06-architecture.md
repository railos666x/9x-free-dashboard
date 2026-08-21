# 06 — System Architecture & Data Flow

## End-to-end data flow

```
┌─────────────────────────────── DATA IN ───────────────────────────────┐
│ Binance REST (klines, public) ──▶ collector.py ──▶ SQLite candles      │
│ Yahoo Finance (42 macro)       ──▶ macro_data.py ──▶ SQLite macro      │
│ 23 RSS feeds + CryptoPanic     ──▶ news.py ──▶ SQLite news             │
│ Alpaca REST (paper)            ──▶ execution.py / portfolio.py (live)  │
└────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                     SQLite (WAL) crypto_data.db
        candles · news · news_verdicts · orders · intents · funding · macro
                              │
        ┌─────────────────────┼───────────────────────────┐
        ▼                     ▼                           ▼
   Source A                Source B                    Dashboard / API
   math_engine.py          proprietary_signals.py      server.py (FastAPI)
   (Bayesian fusion +      (rsi2 | llm | news)         /api/signals /api/candles
    factors + regime)      Groq news analyst           /api/orders /api/positions
        │                     │                        /api/news/analytics
        └─────────┬───────────┘                        /api/tutor /api/backtest
                  ▼
        arbitrator.arbitrate (agreement gate)
                  │
                  ▼
        verification (data gate + cross-source + news gate)
                  │
                  ▼
        execution.execute_intent (risk sizing → broker → journal)
                  │
                  ▼
        SL/TP monitor (manage_open_positions)  ──▶ orders table (journal)
                  │
                  ▼
        web/dashboard (ECharts UI)  ·  tests/  ·  scripts/
```

## Process model

```
python server.py [--reload] [--no-live]
├─ FastAPI app on :8501 (dashboard `/`, JSON API)
├─ price-collector thread   (Binance candles, per-interval cadence)
├─ news-fetcher thread      (RSS + CryptoPanic every 5 min)
├─ macro-fetcher thread     (Yahoo macro every N min)
└─ bot-loop thread          (one cycle every BOT_LOOP_SECONDS; LIVE on paper)
```

Everything runs in ONE process so the bot loop and the API share state
(`_bot_last_cycle`, loop control). `--reload` runs the whole thing as a
child process and restarts it on file changes — deliberately not uvicorn's
built-in reloader, which would split the threads from the app and allow a
double bot loop.

## SQLite (WAL) concurrency

- One writer + many readers via WAL.
- `timeout=30` + `_write_retry` (idempotent `INSERT OR REPLACE`) absorb
  write collisions.
- `PRIMARY KEY (symbol, interval, open_time)` on candles keeps upserts
  indexed at 1.8M+ rows.
- Rule: **one network job at a time** — backfills and funding fetches must
  not run concurrently (they both write candles).

## The decision pipeline per symbol (one bot cycle)

1. **Source A** — math engine proposals (or strategy/hybrid per
   `SIGNAL_SOURCE_A`).
2. **Source B** — proprietary: RSI(2) extremes, LLM headlines, or Groq news
   (`PROPRIETARY_MODE`).
3. **Broker filter** — skip assets the broker can't trade
   (`not_on_broker`), shorts on spot (`no_short_on_spot`).
4. **Agreement gate** — both sources (production) or one high-confidence
   source (test mode).
5. **Data gate** — freshness/sanity/spike checks; **news gate** —
   veto/confirm; **cross-source** — price agreement.
6. **Risk + execution** — size from account equity, place market order,
   journal with reasons.
7. **SL/TP monitor** — close positions that hit levels.

Every skip is recorded with a machine-readable reason and shown in the
dashboard's "held by reason" panel — the system is fully auditable.

## AI components

| Component | Where | Role |
|---|---|---|
| Groq news analyst | `news_analyst.py` | Main news sentiment engine (structured JSON, confidence + horizon) |
| Gemini chain | `reviewer.py` | LLM overlay fallback for news scoring |
| News trade gate | `reviewer.verify_trade` | Veto/confirm layer on agreed trades |
| News signal source | `proprietary_signals` mode `news` | News-initiated LONG/SHORT entries |
| Trading tutor | `tutor.py` | Educational layer explaining mechanics + why trades happened |
