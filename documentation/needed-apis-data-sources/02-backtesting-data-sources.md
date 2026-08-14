# How Backtesting Gets Its Data

End-to-end data flow for the backtester (v2, `backend/backtester.py`), so anyone knows exactly
what a backtest is — and is not — built on.

## Pipeline

```
 Binance REST klines (/api/v3/klines)
        │  collector.backfill(): page backwards 1000 candles/request, idempotent
        ▼
 SQLite — candles table (crypto_data.db)
        │  database.load_candles(conn, symbol, interval, limit)
        ▼
 indicators.add_all(df)  → SMA/EMA/RSI/MACD/ATR/Bollinger (no lookahead)
        │
        ▼
 strategy v2 (mean-reversion + trend filter)  +  risk.py (sizing, fees, slippage)
        │
        ▼
 Trade list → metrics (win rate, PF, Sharpe, drawdown)
        │
        ├── walk-forward: 5 folds split on the SAME candles (in-sample / out-of-sample)
        └── Monte Carlo:  1,000 resamples of the trade P&L distribution
```

The `/api/backtest` endpoint (`server.py`) calls
`backtester.run_backtest(conn, symbol, interval, limit)` — the UI uses `limit=9000`.

## Where the data comes from

| Input | Source | Notes |
|---|---|---|
| OHLCV candles | Binance public REST `/api/v3/klines` → `collector.py backfill` | free, no key; pages of 1000 |
| Symbol universe | `config.py` → `SYMBOLS` (87 crypto pairs) | Binance spot pairs only |
| History depth | `config.py` → `INTERVAL_BACKFILL_DAYS` | 1m/5m 30d · 15m 90d · 1h/4h 365d · 1d 730d |
| Indicators | computed locally, `indicators.py` | deterministic, no lookahead |
| Fees | `config.py` → `FEE_BPS = 10` (10 bps/side, spot taker) | applied on entry + exit notional |
| Slippage | `config.py` → `SLIPPAGE_BPS_TIERS` + default 3 bps | per-symbol tier (majors 0.5 → memes 5) |
| Risk model | `risk.py` | 1% risk/trade, 25% fractional Kelly cap |

## How to populate / refresh

```bash
python backend/fetch_all.py            # candles + funding + macro, idempotent
python backend/fetch_all.py --fast     # skip 1m/5m (~80% of request volume)
python backend/collector.py backfill   # candles only
```

## What backtests do NOT use (deliberately)

- ❌ **Tick / order-book data** — trades are simulated on candle OHLC closes, not real fills
- ❌ **Funding rates** — the `funding` table feeds the live *signals* carry factor, but the
  backtest strategy v2 is candle-only (`news_score=0.0`, no funding, no macro, no news)
- ❌ **News / sentiment / on-chain / macro** — none of these enter `run_backtest`
- ❌ **Binance WebSocket** — no live data; backtests read only the local DB

This keeps results deterministic and reproducible — but it also means **backtest accuracy is
bounded by candle granularity**: fills assume the bar's close, there is no intra-bar slippage
model, and micro-structure effects (queue position, partial fills) are invisible.

## Known consequences & upgrade paths

| Limitation | Effect on results | Fix |
|---|---|---|
| Close-fill assumption | optimistic fills vs. real markets | tick data + market replay (Tardis.dev, CoinAPI flat files) |
| Candle-only signals | no intra-bar entries/exits | tick/L2 backtesting |
| No funding in backtest | carry edge not measured in P&L | extend backtest to include funding accrual per 8h |
| Binance spot pairs only | universe is 87 spot pairs | multi-exchange coverage (CoinAPI/Kaiko) |

See `01-future-paid-sources-and-apis.md` for the data providers that close these gaps.
