"""
cryptoautomation — web terminal server.

Serves the Bloomberg-style terminal (terminal/index.html + assets) and a
small JSON API that reads the SQLite database.  Live data loops (prices
from Binance + news from RSS) run automatically as daemon threads.

Run:
    python server.py              # starts everything — no extra terminals
    python server.py --no-live    # serve only, skip live data fetching
"""

import math
import sys
import time
import threading
from pathlib import Path

import pandas as pd
import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import database
import indicators
import reviewer
import signals

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DASHBOARD = WEB / "dashboard"

app = FastAPI(title="cryptoautomation", version="0.3.0")

# Serve the dashboard's static assets (css / js) at /dash-assets
if DASHBOARD.exists():
    app.mount("/dash-assets", StaticFiles(directory=DASHBOARD), name="dash-assets")

# Small in-memory cache for the Fear & Greed API so we don't hammer it.
_fg_cache = {"data": None, "at": 0.0}

# /api/signals is the heaviest endpoint (full signal pipeline over 87 coins).
# The terminal polls every 5s; without a cache each poll spawns a long-running
# compute and the threads pile up. 20s TTL: one compute per 20s, instant
# responses in between.
_signals_cache = {"data": None, "at": 0.0}
SIGNALS_CACHE_TTL = 20.0


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _clean(v):
    """JSON-safe number: NaN/None -> None, otherwise a rounded float."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 6)


def _series(df, col):
    return [_clean(v) for v in df[col]]


# ----------------------------------------------------------------------
# API endpoints
# ----------------------------------------------------------------------
@app.get("/api/health")
def health():
    conn = database.connect()
    n_news = conn.execute("SELECT COUNT(*) AS n FROM news").fetchone()["n"]
    return {
        "ok": True,
        "candles": database.count_candles(conn),
        "news": n_news,
    }


@app.get("/api/candles")
def candles(symbol: str = "BTC/USDT", interval: str = config.DEFAULT_INTERVAL,
            limit: int = 400):
    """Candles + every indicator for one symbol/interval, as JSON arrays."""
    conn = database.connect()
    df = indicators.add_all(
        database.load_candles(conn, symbol, interval, limit=min(limit, 2000))
    )
    if df.empty:
        return {"symbol": symbol, "interval": interval, "count": 0,
                "times": [], "close": []}

    times = pd.to_datetime(df["open_time"], unit="ms").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "symbol": symbol,
        "interval": interval,
        "count": len(df),
        "times": list(times),
        "open": _series(df, "open"),
        "high": _series(df, "high"),
        "low": _series(df, "low"),
        "close": _series(df, "close"),
        "volume": _series(df, "volume"),
        "sma20": _series(df, "sma20"),
        "sma50": _series(df, "sma50"),
        "rsi": _series(df, "rsi"),
        "atr": _series(df, "atr"),
        "macd": _series(df, "macd"),
        "macd_signal": _series(df, "macd_signal"),
        "macd_hist": _series(df, "macd_hist"),
        "bb_up": _series(df, "bb_up"),
        "bb_mid": _series(df, "bb_mid"),
        "bb_low": _series(df, "bb_low"),
    }


@app.get("/api/watchlist")
def watchlist(interval: str = config.DEFAULT_INTERVAL):
    """One stat row per symbol: last price, % change, RSI, sparkline."""
    conn = database.connect()
    out = []
    for s in config.SYMBOLS:
        df = indicators.add_all(database.load_candles(conn, s, interval, limit=120))
        if df.empty:
            continue
        last = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) > 1 else last
        chg = ((last - prev) / prev * 100) if prev else 0.0
        out.append({
            "symbol": s,
            "price": _clean(last),
            "change": round(chg, 2),
            "rsi": _clean(df["rsi"].iloc[-1]),
            "volume": _clean(df["volume"].iloc[-1]) if "volume" in df else 0.0,
            "spark": _series(df, "close")[-40:],
        })
    return {"interval": interval, "symbols": out}


@app.get("/api/news")
def news(limit: int = 30, sentiment: str = None, symbol: str = None):
    """Latest news stories, optionally filtered by sentiment and/or coin."""
    conn = database.connect()
    df = database.load_news(
        conn,
        limit=min(limit, 200),
        sentiment=sentiment or None,
        symbol=symbol or None,
    )
    return {"count": len(df), "items": df.to_dict(orient="records")}


@app.get("/api/fear-greed")
def fear_greed():
    """Fear & Greed index value, cached for 5 minutes."""
    now = time.time()
    if _fg_cache["data"] and now - _fg_cache["at"] < 300:
        return _fg_cache["data"]
    try:
        data = requests.get(config.FEAR_GREED_API, timeout=10).json()["data"]
        result = {"value": int(data[0]["value"]), "label": data[0]["value_classification"]}
    except Exception:
        result = {"value": None, "label": "unavailable"}
    _fg_cache.update({"data": result, "at": now})
    return result


@app.get("/api/review")
def review(limit: int = 40):
    """AI market review: per-coin news score, stance, and the stories behind it."""
    conn = database.connect()
    return reviewer.review_market(conn, limit=min(limit, 200))


@app.get("/api/macro")
def macro(limit: int = 250):
    """Macro asset charts (gold, S&P 500) from the `macro` table."""
    conn = database.connect()
    import macro_data
    symbols = database.macro_symbols(conn)
    if not symbols:
        # First run: fetch history on demand so the panel works immediately.
        macro_data.backfill_macro(conn, days=730)
        symbols = database.macro_symbols(conn)
    import macro_data
    out = []
    for s in symbols:
        df = database.load_macro(conn, symbol=s, limit=min(limit, 1000))
        if df.empty:
            continue
        times = pd.to_datetime(df["open_time"], unit="ms").dt.strftime("%Y-%m-%d")
        closes = _series(df, "close")
        last = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) > 1 else last
        out.append({
            "symbol": s,
            "name": macro_data.label_of(s),
            "category": macro_data.category_of(s),
            "price": _clean(last),
            "change": round(((last - prev) / prev * 100), 2) if prev else 0.0,
            "times": list(times),
            "close": closes,
        })
    # Category order for the UI (matches macro_data.MACRO_CATEGORIES).
    order = {cat: i for i, cat in enumerate(macro_data.MACRO_CATEGORIES)}
    out.sort(key=lambda x: order.get(x["category"], 99))
    return {"count": len(out), "symbols": out, "categories": list(macro_data.MACRO_CATEGORIES)}


# Single-flight lock: the terminal polls /api/signals every 5 s but a full
# compute takes tens of seconds. Without a lock every poll spawns its own
# compute and they stack up (measured: >180 s stalls). The first poll
# computes; everyone else waits on the lock and gets the fresh cache.
_signals_lock = threading.Lock()


@app.get("/api/signals")
def get_signals():
    """Trading signals: direction, entry, SL, TP, confidence for every coin.
    Cached for SIGNALS_CACHE_TTL seconds; single-flight so concurrent polls
    never stack computes."""
    now = time.time()
    if _signals_cache["data"] and now - _signals_cache["at"] < SIGNALS_CACHE_TTL:
        return _signals_cache["data"]
    with _signals_lock:
        # Re-check under the lock: another poll may have just computed it.
        now = time.time()
        if _signals_cache["data"] and now - _signals_cache["at"] < SIGNALS_CACHE_TTL:
            return _signals_cache["data"]
        conn = database.connect()
        data = signals.generate_all_signals(conn)
        _signals_cache.update({"data": data, "at": time.time()})
    return data


@app.get("/api/portfolio")
def portfolio():
    """Portfolio snapshot: real prices x holdings (demo or connected exchange)."""
    import portfolio as pf
    conn = database.connect()
    return pf.get_portfolio(conn)


@app.post("/api/connect-exchange")
def connect_exchange(payload: dict):
    """Save Binance API keys locally (data/exchange_keys.json). Keys are
    only ever sent to Binance itself; stored in plain text locally — treat
    like a password. Use IP whitelisting on Binance."""
    import portfolio as pf
    name = str(payload.get("name") or "My Binance")
    api_key = str(payload.get("api_key") or "").strip()
    api_secret = str(payload.get("api_secret") or "").strip()
    if not api_key or not api_secret:
        return {"ok": False, "error": "API key and secret are required"}
    pf.save_keys(name, api_key, api_secret)
    return {"ok": True, "mode": "exchange", "name": name}


@app.post("/api/disconnect-exchange")
def disconnect_exchange():
    import portfolio as pf
    pf.clear_keys()
    return {"ok": True}


@app.get("/api/backtest")
def run_backtest(
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    limit: int = 2000,
):
    """Run a backtest on historical data and return full performance metrics."""
    import backtester
    conn = database.connect()
    result = backtester.run_backtest(conn, symbol=symbol, interval=interval, limit=limit)
    return {
        "symbol": result.symbol,
        "interval": result.interval,
        "total_bars": result.total_bars,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "win_rate_ci": list(result.win_rate_ci),
        "profit_factor": result.profit_factor,
        "total_return_pct": result.total_return_pct,
        "annualised_return_pct": result.annualised_return_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "calmar_ratio": result.calmar_ratio,
        "avg_win": result.avg_win,
        "avg_loss": result.avg_loss,
        "in_sample": result.in_sample,
        "out_of_sample": result.out_of_sample,
        "walk_forward": result.walk_forward,
        "monte_carlo": result.monte_carlo,
        "trades": len(result.trades),
        "trade_list": _serialize_trades(result.trades),
        "exit_analysis": _exit_analysis(result.trades),
    }


def _serialize_trades(trades, limit: int = 250):
    """The most recent trades, newest last, for the dashboard's trade list."""
    out = []
    for t in trades[-limit:]:
        out.append({
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "direction": t.direction,
            "entry": round(t.entry, 8),
            "exit": round(t.exit_price, 8),
            "pnl": round(t.pnl, 4),
            "pnl_pct": round(t.pnl_pct, 4),
            "hold_bars": t.hold_bars,
            "exit_reason": t.exit_reason,
            "confidence": round(t.confidence, 1),
        })
    return out


def _exit_analysis(trades):
    """Summarise exit reasons."""
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    n = len(trades)
    return {k: {"count": v, "pct": round(v / n * 100, 1)} if n else {"count": 0, "pct": 0}
            for k, v in reasons.items()}


# ----------------------------------------------------------------------
# Pages
# ----------------------------------------------------------------------
@app.get("/")
def index():
    """Landing page: the dashboard (the old terminal was retired 2026-08-15)."""
    return FileResponse(DASHBOARD / "index.html")


def _start_live_threads() -> None:
    """Start the price collector and news loops as daemon threads."""
    import collector as _col
    import news as _news

    def _prices():
        conn = database.connect()
        database.init_db(conn)
        _col.live_loop(conn)

    def _news_loop():
        _news.live_loop()

    def _macro_loop():
        import macro_data
        conn = database.connect()
        database.init_db(conn)
        # First refresh synchronously so the panel has data immediately.
        try:
            macro_data.backfill_macro(conn, days=730)
        except Exception:
            pass
        macro_data.macro_loop(conn)

    t1 = threading.Thread(target=_prices, daemon=True, name="price-collector")
    t2 = threading.Thread(target=_news_loop, daemon=True, name="news-fetcher")
    t3 = threading.Thread(target=_macro_loop, daemon=True, name="macro-fetcher")
    t1.start()
    t2.start()
    t3.start()
    print("  Live data threads started (prices + news + macro)")


if __name__ == "__main__":
    import uvicorn

    no_live = "--no-live" in sys.argv
    print("cryptoautomation terminal -> http://localhost:8501")
    if no_live:
        print("  (--no-live: serving only, no live data fetching)")
    else:
        _start_live_threads()
    uvicorn.run(app, host="0.0.0.0", port=8501)
