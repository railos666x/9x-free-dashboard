# ARCHITECTURE.md

High-level system design of the cryptoautomation / OmniRoute2 platform.

*Last updated: 2026-08-14*

---

## Overview

A crypto trading platform with three concentric layers:

1. **Data layer** — Binance public REST (klines) + USD-M futures (funding rates),
   stored in SQLite (WAL mode).
2. **Intelligence layer** — two signal engines plus a production backtesting /
   risk framework:
   - `strategy.py` — mean-reversion core (new, primary)
   - `math_engine.py` — Bayesian-fusion signal engine (legacy, live dashboard)
   - `factors.py` — 5 academic factors (trend, momentum, carry, reversal, value)
   - `risk.py` — execution cost model, Kelly sizing, price sanitizers
   - `backtester.py` — v2 backtest engine (walk-forward, Monte Carlo, CI)
3. **Presentation layer** — FastAPI terminal (`server.py`, `terminal/`),
   TradingView mirror (`tradingview/`), legacy Streamlit (`dashboard.py`).

## Data Flow

```
Binance REST (spot klines) ──► collector.py ──► SQLite (candles, news)
Binance REST (futures funding) ─► collector.py funding ─► SQLite (funding)

crypto_data.db
   ├─► server.py /api/* ──► terminal/ (ECharts dashboard)
   ├─► signals.py → math_engine.py (live signals, news overlay)
   └─► backtester.py → strategy.py + risk.py (research / validation)
```

## Module Map (as of 2026-08-15 — post-restructure)

**Layout:** `backend/` = all Python · `web/` = both UIs · `tests/` = suites ·
`scripts/` = bat launchers · `legacy/` = retired pages · `data/` = runtime
artifacts (DB lives in project root).

| Module | Responsibility | Notes |
|---|---|---|
| `backend/collector.py` | kline backfill/live + gap repair + funding history | per-interval depth, per-symbol fault isolation |
| `backend/database.py` | SQLite access: candles, funding, news, macro | WAL; idempotent upserts; lock-retry writes |
| `backend/config.py` | 87 valid symbols, per-interval backfill days, risk/execution config | deduplicated; dead listings removed |
| `backend/strategy.py` | Mean-reversion core (z-score, OU half-life, ADX/vol/liquidity filters, trend filter) | vectorized, parameter-light |
| `backend/risk.py` | slippage tiers, fees, fractional Kelly, position sizing | pure functions |
| `backend/backtester.py` | v2 engine: equity curve, dynamic sizing, walk-forward, Monte Carlo, Wilson CI | strategy-parameterized |
| `backend/math_engine.py` | Bayesian fusion pipeline (legacy live engine) | SL/TP rounding fixed |
| `backend/signals.py` | signal generation for the terminal | optimized (no O(n²)), funding TTL cache |
| `backend/server.py` | FastAPI app: REST API + static mounts (`/assets`, `/dash-assets`) | pages: `/` dashboard, `/terminal` |
| `backend/portfolio.py` | demo/exchange portfolio, priced live | signed Binance balances when keys set |
| `backend/macro_data.py` | 42 macro instruments (metals/energy/ags/forex/indices/rates) | Yahoo Finance, no key |
| `backend/fetch_all.py` | single-file fetcher: candles + funding + macro | `--fast` skips 1m/5m |
| `backend/factors.py` | 5-factor academic library | shared read-mostly |
| `web/dashboard/` | **New Sellimor-style dashboard** (index.html + dashboard.css + dashboard.js) | served at `/` |
| `web/terminal/` | Terminal dashboard (index.html, assets/js, assets/css) | served at `/terminal` |
| `tests/` | test_system.py, test_strategy.py, test_backtest.py | 41 strategy checks + system suite |
| `scripts/` | go/start/backtest/test .bat launchers | paths updated for new layout |

## Key Design Decisions

- **SQLite + WAL** for zero-ops local storage; upgrade path to TimescaleDB.
- **Public REST polling** (per-interval cadence) over WebSocket for reliability;
  WS upgrade is a roadmap item.
- **Parameter-light strategy core** (4 knobs) as the anti-overfitting stance.
- **Two-agent ownership split** — see CLAUDE.md "Multi-Agent Coordination".

## Known Limitations (tracked)

- Spot data only; no order-book/spread history (slippage is modeled, not measured).
- Funding history only recently added; carry factor still unbacktestable historically.
- 1h depth now ~375 days for the 5 majors; other symbols pending full backfill.
