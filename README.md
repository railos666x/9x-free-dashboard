# 📡 cryptoautomation

A Bloomberg-style terminal for crypto: live prices, technical indicators,
and a multi-source news feed with sentiment — all on one screen.

This is **Milestone 1** of a larger platform. The goal here is the
*review surface*: see price, indicators, and news together so you can
judge the market. Later milestones add the AI news reviewer, paper
trading, and automation (news → AI view → trade).

> **What this is vs. Bloomberg:** a real Bloomberg Terminal is a
> multi-year, paid-data product. This is a *terminal-inspired* dark,
> dense dashboard built on **free** professional-grade sources
> (Binance public API + CryptoPanic + trusted RSS feeds). The design
> leaves a clean slot to plug in paid feeds later if you ever want them.

---

## Quick start

### 1. Install dependencies (one time)

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Start the live data loops

In **one** terminal:

```powershell
python collector.py backfill   # one-time: download ~30 days of history
python run_live.py             # keeps prices + news fresh (leave running)
```

### 3. Open the dashboard

In a **second** terminal:

```powershell
streamlit run dashboard.py
```

Your browser opens http://localhost:8501.

---

## Optional: CryptoPanic sentiment

RSS alone works fine. To get **real sentiment votes** on every story,
create a free account at https://cryptopanic.com/, copy your API key into
a `.env` file:

```
CRYPTOPANIC_API_KEY=your_key_here
```

Without a key the system quietly runs RSS-only (with simple keyword
sentiment) — nothing breaks.

---

## What's in each file

| File | Job |
|---|---|
| `config.py` | All settings in one place: symbols, timeframes, news sources, refresh rates |
| `database.py` | SQLite storage for candles + news (de-duplicated, WAL mode) |
| `collector.py` | Binance prices → DB (`backfill` / `live`) |
| `news.py` | CryptoPanic + RSS → DB with sentiment (`fetch` / `live`) |
| `indicators.py` | SMA, EMA, RSI, MACD, ATR, Bollinger — readable, no lookahead |
| `dashboard.py` | The terminal UI (watchlist, chart, news, Fear & Greed) |
| `run_live.py` | Starts price + news loops together |

## Troubleshooting

- **`Binance` connection errors:** the public Binance API may be blocked
  in some regions. Try `https://api.binance.us` if needed (edit
  `KLINES_URL` in `collector.py`).
- **No candles yet:** run `python collector.py backfill` and wait for
  "Done".
- **No news yet:** run `python news.py fetch` and check the printed count.
- **Port already in use:** `streamlit run dashboard.py --server.port 8502`.

## Roadmap (after this milestone)

1. **AI News Reviewer** — LLM (Qwen/DeepSeek, cheap API) reads the news
   panel and scores relevance per coin; model-agnostic advisor interface.
2. **Paper trading engine** — the risk rules that matter: position
   sizing, ATR stops, exposure caps.
3. **News → AI view → paper trade** automation loop.
4. **Live trading** with tiny size + monitoring.
