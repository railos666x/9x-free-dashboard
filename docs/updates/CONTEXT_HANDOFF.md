# CryptoAutomation - Session Handoff Context

*Created: 2026-08-13 | For: freebuff AI / next session*

---

## �� WHAT WAS COMPLETED THIS SESSION

### 1. **Expanded Coin Universe** (config.py + terminal.js)
- Added 20+ coins from watchlist screenshots
- Total: ~98 coins across 8 categories (Major, L1, L2, DeFi, Meme, Gaming, Infra, Other)
- New coins: OG, BEL, OGN, SLP, FIO, CREAM, MDT, IRIS, QLC, TRB, LUNA, OMN, BLOB, BTCDOM, etc.
- **Need**: Run `python collector.py backfill` to populate DB, then remove unavailable symbols

### 2. **Dashboard UI/UX Overhaul** (terminal/index.html + terminal.js + terminal.css)
- **Search box** with `/` keyboard shortcut
- **Category tabs** (All, Major, L1, L2, DeFi, Meme, Gaming, Infra, Other) with coin counts
- **Sparklines** - 40-candle mini charts in watchlist (show on hover/active)
- **Compact mode** - toggle button, hides sparklines, tighter spacing
- **Keyboard shortcuts**: 1-6 for intervals, ↑��� for navigation, `/` for search
- **Responsive breakpoints** updated for 80+ coins

### 3. **Fixed ETH Trade Loss / Backtester Alignment**
- **Root cause**: Backtester used simplified factor-only logic vs live system's full math engine
- **Fix**: Modified `backtester.py:_compute_signal_at()` to call `math_engine.compute_trade_signal()` with:
  - Bayesian fusion across 5 indicator groups + 5 factors
  - Regime detection (ADX)
  - Kelly Criterion position sizing
  - Signal freshness decay
  - Confluence scoring
  - News overlay (15%)
- **Trade simulation**: Uses math engine's regime-adaptive SL/TP (1.5x/2.5x ATR in ranges, 2.0x/3.0x in trends)
- **Added filters**: MIN_HOLD_CANDLES=12, MIN_ENTRY_CONFIDENCE=30, MIN_EV=0.01

### 4. **Calibrated Math Engine Priors** (math_engine.py)
- **DEFAULT_HIT_RATES**: Set BELOW 0.50 (0.47-0.49) based on backtest evidence
- **FACTOR_HIT_RATES**: Set BELOW 0.50 (0.47-0.50) - factors have no edge on 1h crypto
- **EV filter**: Rejects trades with negative expected value
- **Stricter thresholds**: MIN_POSTERIOR=0.65, MIN_CONFIDENCE=25%

### 5. **Fixed Live Dashboard Errors**
- **`/api/signals` 500 error**: Added try/except fallback in `signals.py:generate_signal()`
- **`renderStats` TypeError**: Added null checks for `d.rsi`, `d.atr`, `d.volume`, `d.high`, `d.low` arrays
- **Server verified running**: All APIs responding (candles, watchlist, signals, news)

### 6. **Documentation Created** (docs/updates/)
- `README.md` - Index
- `CHANGELOG.md` - Chronological changes
- `coins_added.md` - New coins detail
- `ui_ux_improvements.md` - Dashboard features
- `eth_trade_investigation.md` - Root cause & fix
- `architecture.md` - System architecture
- `development_notes.md` - Dev reference
- `institutional_news_sources.md` - User's research notes
- `openbb_gloomberb_research.md` - OpenBB/Gloomberb feature analysis
- `TASKS_TODO.md` - Full roadmap
- `CONTEXT_HANDOFF.md` - This file

---

## ��� CURRENT STATE

### Server Status
```bash
# To start:
python server.py
# Dashboard: http://localhost:8501
```

### Working APIs
| Endpoint | Status |
|----------|--------|
| `GET /api/candles?symbol=BTC/USDT&interval=1h&limit=400` | �� 200 OK |
| `GET /api/signals` | �� 200 OK (97 signals) |
| `GET /api/watchlist?interval=1h` | �� 200 OK |
| `GET /api/news` | �� 200 OK |
| `GET /api/health` | �� 200 OK |

### Live Signals (Current)
| Symbol | Direction | Confidence | Regime | P(bull) |
|--------|-----------|------------|--------|---------|
| BTC/USDT | HOLD | 10% | trending_down | 0.428 |
| ETH/USDT | HOLD | 4% | ranging | 0.505 |
| SOL/USDT | HOLD | 8% | ranging | 0.559 |

**All HOLD** - Conservative priors working as intended.

### Backtest Status (Last Run)
```bash
python -c "
import database, backtester
conn = database.connect()
result = backtester.run_backtest(conn, 'BTC/USDT', '1h', 1000)
backtester.print_report(result)
"
```
| Symbol | Trades | Win Rate | Return | Profit Factor | Sharpe |
|--------|--------|----------|--------|---------------|--------|
| BTC/USDT | 74 | 33.8% | -20.02% | 0.42 | -13.18 |
| ETH/USDT | 60 | 36.7% | -11.79% | 0.69 | -5.01 |
| SOL/USDT | 61 | 39.3% | -16.50% | 0.59 | -7.32 |

**Still negative** - Need further calibration or strategy change.

---

## ��� IMMEDIATE NEXT TASKS

### Priority 1: Data Backfill for New Coins
```bash
python collector.py backfill
# Then check which symbols failed (not on Binance Spot)
# Remove unavailable from config.py + terminal.js
```

### Priority 2: Fix Backtest Profitability
Options:
1. **Switch strategy**: Mean-reversion (Bollinger + RSI) instead of trend-following
2. **Longer timeframes**: 4h/1d signals more reliable than 1h
3. **Add macro filters**: BTC trend filter (only LONG alts when BTC trending up)
4. **Kelly sizing**: Apply fractional Kelly in backtester position sizing
5. **Transaction costs**: Add realistic slippage + funding costs

### Priority 3: Implement Phase 1 from OpenBB Research
- **Data Provider Abstraction**: Multi-exchange (Binance, Bybit, CoinGecko)
- **Plugin System**: Modular features (news, backtest, portfolio, macro)
- **Economic Calendar**: Free FRED API integration

---

## ��� KEY FILES TO REMEMBER

| File | Purpose |
|------|---------|
| `config.py` | Source of truth for symbols, intervals, API keys |
| `math_engine.py` | Core signal logic (Bayesian, Kelly, regime, EV) |
| `backtester.py` | Historical replay (now uses math_engine) |
| `signals.py` | Live signal generation per coin |
| `server.py` | FastAPI + terminal + background collectors |
| `terminal/assets/js/terminal.js` | Dashboard state, rendering, polling |
| `terminal/assets/css/terminal.css` | Dark theme, responsive, compact mode |

---

## ������ KNOWN ISSUES

1. **Backtester completely rewritten** by external agent (see v2 in file) - legacy `_compute_signal_at` may be stale
2. **Duplicate symbols** in config.py (OG, CTSI, BEL appear multiple times)
3. **Unicode chars** in backtester print_report cause Windows console errors
4. **No futures data** in collector (only spot)
5. **5s polling** instead of WebSocket for dashboard
6. **No authentication** on terminal
7. **SQLite WAL growth** - needs periodic checkpoint

---

## ��� RESEARCH NOTES FOR NEXT SESSION

### OpenBB + Gloomberb Integration (see `openbb_gloomberb_research.md`)
- **Phase 1**: Data Provider Abstraction (multi-exchange, failover)
- **Phase 2**: Plugin System (news, backtest, portfolio, macro)
- **Phase 3**: TUI Mode (textual for SSH access)
- **Phase 4**: Economic Calendar (FRED free API), Portfolio Mgmt

### Institutional News Stack (user-provided)
- **Paid**: Bloomberg Terminal, Refinitiv Eikon, FactSet, Dow Jones Newswires
- **Machine-readable**: RavenPack, Refinitiv MRN, Bloomberg EDT
- **Free**: CNBC, Reuters, WSJ/FT, Yahoo Finance, Seeking Alpha
- **Crypto-specific**: Kaiko, Amberdata, The Block Pro, Messari Pro, Glassnode

---

## ��� QUICK START FOR NEXT SESSION

```bash
# 1. Backfill new coins
python collector.py backfill

# 2. Start server
python server.py
# Visit http://localhost:8501

# 3. Test backtest with mean_reversion strategy
python -c "
import database, backtester
conn = database.connect()
result = backtester.run_backtest(conn, 'BTC/USDT', '1h', 1000, strategy='mean_reversion')
backtester.print_report(result)
"

# 4. Check live signals
curl http://localhost:8501/api/signals | jq '.signals[] | select(.direction != "HOLD")'
```

## ��� SESSION HANDOFF (2026-08-15 — execution stack + news-verified AI trading)

### What's running now
- **Broker: Alpaca paper** (`EXECUTION_BROKER=alpaca`, `ALPACA_PAPER=true`)
  — bot auto-loop LIVE on the ~$100k paper account, sized from account
  equity (`execution._broker_equity`). SHORTs held (spot); unsupported
  assets held (`not_on_broker`) — the old 422/403 spam is fixed.
- **News review: Gemini LLM** — `LLM_ENABLED=true`, model
  `gemini-3.1-flash-lite` (2.5-flash is deprecated for new users),
  cached 10 min (`LLM_MIN_INTERVAL_SECONDS`). Optional OpenRouter/Groq
  fallbacks: `python scripts/setup_free_llm.py --openrouter … --groq …`.
- **News verification gate** — `reviewer.verify_trade` vetoes/confirms
  every agreed trade; decisions journaled to `news_verdicts`;
  `/api/news/stats`; tuning via `python scripts/analyze_news_gate.py`.

### Useful commands
```bash
cd backend && python server.py        # dashboard :8501, auto-loop included
python scripts/setup_free_llm.py      # add free LLM keys to .env
python scripts/analyze_news_gate.py   # replay veto/confirm thresholds
python tests/test_execution.py        # execution + broker regression tests
```

### Known follow-ups
- Add OpenRouter/Groq keys (optional; Gemini alone works).
- Tune `NEWS_VETO_SCORE` / `NEWS_BIG_MOVE_SCORE` after veto history builds.
- Binance live keys for real-money execution (untested).

---

*Session complete. All changes committed to docs/updates/.*