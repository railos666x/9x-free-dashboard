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
from fastapi import FastAPI, Query
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


@app.get("/api/news/stats")
def news_stats(days: int = 7):
    """News-gate history: how often the confirmation layer vetoed / confirmed
    / stayed neutral over the last `days` (see reviewer.verify_trade)."""
    conn = database.connect()
    return database.news_verdict_stats(conn, days=min(days, 30))


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


# ----------------------------------------------------------------------
# Bot + trade journal (see bot.py / execution.py)
# ----------------------------------------------------------------------
_bot_last_cycle = {"summary": None, "at": None}


_last_reconcile = 0.0
RECONCILE_THROTTLE = 15.0  # seconds between auto-reconciles (matches the 15s journal poll)


@app.get("/api/orders")
def orders(status: str = None, limit: int = 100, detail: int = 0,
           from_: str = Query(None, alias="from"),
           to: str = Query(None, alias="to")):
    """The trade journal: every order the bot has placed (or dry-run).

    detail=1 enriches each row with current price / market value /
    unrealized PnL and a sparkline of the last 24 1h closes, plus a
    journal summary block. When the broker is Alpaca, statuses are also
    auto-reconciled (throttled to once per 30s) so the journal mirrors
    the broker without a manual refresh.

    `from` / `to` (dates or ISO datetimes) filter by created-at window,
    so the donut + summary reflect any period (today / week / custom).
    """
    conn = database.connect()
    global _last_reconcile
    if config.EXECUTION_BROKER == "alpaca" and time.time() - _last_reconcile > RECONCILE_THROTTLE:
        try:
            import execution
            execution.reconcile_orders(conn)
            _last_reconcile = time.time()
        except Exception:
            pass  # reconciliation is best-effort on reads

    orders_list = database.load_orders(conn, status=status or None,
                                       created_from=from_, created_to=to,
                                       limit=min(limit, 1000))
    if not detail:
        return {"orders": orders_list}

    # ── Per-symbol market context (1h closes), cached across rows ──
    closes: dict[str, list[float]] = {}
    for o in orders_list:
        sym = o["symbol"]
        if sym in closes:
            continue
        df = database.load_candles(conn, sym, "1h", limit=24)
        closes[sym] = [float(c) for c in df["close"].tolist()] if not df.empty else []

    enriched = []
    for o in orders_list:
        row = dict(o)
        cc = closes.get(o["symbol"], [])
        row["spark"] = cc
        row["current_price"] = cc[-1] if cc else None
        qty = float(o.get("qty") or 0)
        # Cost basis: intent entry, falling back to the actual fill price.
        entry = o.get("entry") or o.get("fill_price")
        cur = row["current_price"]
        active = o.get("status") in ("filled", "open")
        row["market_value"] = round(qty * cur, 2) if (active and cur) else None
        if active and cur and entry:
            if o["side"].upper() == "LONG":
                unreal = (cur - float(entry)) * qty - float(o.get("fees") or 0)
            else:
                unreal = (float(entry) - cur) * qty - float(o.get("fees") or 0)
            row["unrealized_pnl"] = round(unreal, 2)
        else:
            row["unrealized_pnl"] = None
        enriched.append(row)

    # ── Alpaca: blend the broker's real-time marks so the journal donut
    # matches the live account. Journal marks use 1h-candle closes (only
    # change hourly — the donut looks "stuck"); Alpaca's per-position
    # unrealized PnL is marked continuously and also covers positions that
    # were opened outside the bot. Falls back to journal marks when the
    # account is unreachable.
    acct_positions: dict[str, dict] = {}
    if config.EXECUTION_BROKER == "alpaca":
        try:
            import portfolio as pf
            acct = pf._alpaca_account()
            if acct:
                acct_positions = acct.get("positions") or {}
        except Exception:
            acct_positions = {}
    if acct_positions:
        for r in enriched:
            if r.get("status") not in ("filled", "open"):
                continue
            pos = acct_positions.get(r["symbol"])
            if pos:
                r["unrealized_pnl"] = round(pos["unrealized_pl"], 2)
                r["market_value"] = round(pos["market_value"], 2)
            else:
                # No open position at the broker — the journal row is stale
                # (closed broker-side); don't show phantom PnL.
                r["unrealized_pnl"] = 0.0
                r["market_value"] = None

    open_val = sum(r["market_value"] or 0 for r in enriched
                   if r.get("status") in ("filled", "open"))
    realized = sum(float(r.get("pnl") or 0) for r in enriched
                   if r.get("status") == "closed")

    # ── Per-currency PnL contribution (for the donut chart) ──
    by_sym: dict[str, dict] = {}
    for r in enriched:
        e = by_sym.setdefault(r["symbol"],
                              {"realized": 0.0, "unrealized": 0.0, "market_value": 0.0})
        if r.get("status") == "closed":
            e["realized"] += float(r.get("pnl") or 0)
        if r.get("status") in ("filled", "open"):
            e["unrealized"] += float(r.get("unrealized_pnl") or 0)
            e["market_value"] += float(r.get("market_value") or 0)
    # Account-only positions (opened outside the bot) still belong in the
    # donut, otherwise the centre total wouldn't match the slices.
    for sym, pos in acct_positions.items():
        e = by_sym.setdefault(sym,
                              {"realized": 0.0, "unrealized": 0.0, "market_value": 0.0})
        e["unrealized"] += float(pos.get("unrealized_pl") or 0)
        e["market_value"] += float(pos.get("market_value") or 0)
    gross = sum(abs(e["realized"] + e["unrealized"]) for e in by_sym.values()) or 1.0
    by_symbol = []
    for sym, e in by_sym.items():
        pnl = round(e["realized"] + e["unrealized"], 2)
        by_symbol.append({
            "symbol": sym,
            "pnl": pnl,
            "realized": round(e["realized"], 2),
            "unrealized": round(e["unrealized"], 2),
            "market_value": round(e["market_value"], 2),
            "share": round(abs(pnl) / gross * 100, 1),
        })
    by_symbol.sort(key=lambda r: abs(r["pnl"]), reverse=True)

    summary_block = {
        "orders": len(enriched),
        "open_positions": len([r for r in enriched
                                if r.get("status") in ("filled", "open")]),
        "dry_run": len([r for r in enriched if r.get("status") == "dry_run"]),
        "open_market_value": round(open_val, 2),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(sum(e["unrealized"] for e in by_sym.values()), 2),
        "total_pnl": round(realized + sum(e["unrealized"] for e in by_sym.values()), 2),
        "by_symbol": by_symbol,
    }
    return {"orders": enriched, "summary": summary_block}


@app.get("/api/positions")
def positions():
    """LIVE trades snapshot — open/filled journal rows with a fresh price
    and unrealized PnL. Deliberately lean (no sparklines, no heavy
    enrichment) so the dashboard can poll it fast; broker-side marks
    (Alpaca) are blended when available.
    """
    conn = database.connect()
    rows = [o for o in database.load_orders(conn, limit=500)
            if o.get("status") in ("filled", "open")]

    acct_positions = {}
    if config.EXECUTION_BROKER == "alpaca":
        try:
            import portfolio as pf
            acct = pf._alpaca_account()
            if acct:
                acct_positions = acct.get("positions") or {}
        except Exception:
            acct_positions = {}

    out = []
    for o in rows:
        df = database.load_candles(conn, o["symbol"], "1h", limit=3)
        cur = float(df["close"].iloc[-1]) if not df.empty else None
        entry = o.get("entry") or o.get("fill_price")
        unreal = None
        if cur and entry:
            qty = float(o.get("qty") or 0)
            if o["side"].upper() == "LONG":
                unreal = (cur - float(entry)) * qty - float(o.get("fees") or 0)
            else:
                unreal = (float(entry) - cur) * qty - float(o.get("fees") or 0)
        pos = acct_positions.get(o["symbol"])
        if pos:
            cur = pos.get("current") or cur
            unreal = pos.get("unrealized_pl")
        out.append({
            "symbol": o["symbol"], "side": o["side"], "qty": o.get("qty"),
            "entry": entry, "fill_price": o.get("fill_price"),
            "stop_loss": o.get("stop_loss"), "take_profit": o.get("take_profit"),
            "current_price": cur,
            "unrealized_pnl": round(unreal, 2) if unreal is not None else None,
            "created_at": o.get("created_at"),
        })
    return {"positions": out, "count": len(out),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.post("/api/bot/reconcile")
def bot_reconcile():
    """Force a journal ↔ broker reconciliation now. Returns updated rows
    plus any foreign fills (broker orders our journal didn't place)."""
    import execution
    conn = database.connect()
    try:
        rows = execution.reconcile_orders(conn)
        updated = [r for r in rows if not r.get("foreign")]
        foreign = [r for r in rows if r.get("foreign")]
        return {"updated": updated, "count": len(updated),
                "foreign": foreign, "foreign_count": len(foreign)}
    except Exception as e:
        return {"error": str(e), "updated": [], "count": 0, "foreign": []}


@app.post("/api/bot/run")
def bot_run(payload: dict = None):
    """Run one bot cycle. dry_run=true (default) journals intents without
    touching a broker — safe to press from the dashboard."""
    payload = payload or {}
    dry_run = bool(payload.get("dry_run", True))
    conn = database.connect()
    import bot
    summary = bot.run_once(conn, dry_run=dry_run)
    _bot_last_cycle.update({"summary": summary, "at": time.time()})
    return summary


@app.post("/api/bot/loop")
def bot_loop_control(payload: dict = None):
    """Start/stop the server-side bot loop from the dashboard.

    Start runs dry-run unless the broker is paper (live auto-loop is
    refused for real-money brokers by _auto_loop_live). Stop asks the
    loop to finish after the current cycle.
    """
    payload = payload or {}
    if bool(payload.get("running")):
        started = _start_bot_loop()
        return {"ok": True, "running": True, "live": _auto_loop_live(),
                "message": "already running" if not started else "started"}
    stopped = _stop_bot_loop()
    return {"ok": True, "running": False, "stopped": stopped,
            "message": ("stop requested — finishes after the current cycle"
                         if stopped else "loop was not running")}


@app.post("/api/bot/source")
def bot_source(payload: dict = None):
    """Switch signal source A (math_engine | strategy) from the dashboard.

    Only allowed while the bot loop is STOPPED, so a running bot is never
    yanked mid-cycle. Takes effect immediately (next cycle) and is
    persisted to .env so a restart keeps the choice.
    """
    if _bot_thread is not None and _bot_thread.is_alive():
        return {"ok": False,
                "error": "bot is running — stop it first (Auto loop → Stop)"}
    payload = payload or {}
    source = str(payload.get("source") or "").strip().lower()
    if source not in ("math_engine", "strategy", "hybrid"):
        return {"ok": False,
                "error": "source must be 'math_engine', 'strategy' or 'hybrid'"}
    config.SIGNAL_SOURCE_A = source
    try:
        _write_env_key("SIGNAL_SOURCE_A", source)
    except Exception as e:
        return {"ok": True, "source": source,
                "warning": f"applied for this session but .env not updated: {e}"}
    return {"ok": True, "source": source}


@app.get("/api/bot/status")
def bot_status():
    """Bot configuration + the last cycle summary (for the dashboard)."""
    # Keep open_positions truthful: broker-side closes are synced (throttled)
    # so a position closed in Alpaca stops showing as open here.
    global _last_reconcile
    if config.EXECUTION_BROKER == "alpaca" and time.time() - _last_reconcile > RECONCILE_THROTTLE:
        try:
            import execution
            execution.reconcile_orders(database.connect())
            _last_reconcile = time.time()
        except Exception:
            pass
    return {
        "broker": config.EXECUTION_BROKER,
        "alpaca_paper": config.ALPACA_PAPER,
        "proprietary_enabled": config.PROPRIETARY_SIGNALS_ENABLED,
        "llm_providers": len(reviewer._llm_providers()) if config.LLM_ENABLED else -1,
        "loop_seconds": config.BOT_LOOP_SECONDS,
        "signal_source_a": config.SIGNAL_SOURCE_A,
        "arbitration_min_sources": config.ARBITRATION_MIN_SOURCES,
        "arbitration_min_confidence": config.ARBITRATION_MIN_CONFIDENCE,
        "live_single_source_blocked": (config.ARBITRATION_MIN_SOURCES < 2
                                        and not config.ALLOW_LIVE_SINGLE_SOURCE),
        "auto_loop": config.BOT_AUTO_LOOP,
        "auto_loop_running": bool(_bot_thread is not None and _bot_thread.is_alive()),
        "auto_loop_live": _auto_loop_live(),
        "last_cycle": _bot_last_cycle["summary"],
        "last_run_at": _bot_last_cycle["at"],
        "open_orders": len([o for o in database.load_orders(database.connect(),
                                                            limit=1000)
                            if o.get("status") in ("filled", "open")]),
    }


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


# ----------------------------------------------------------------------
# Bot auto-loop control (start/stop from the dashboard, see /api/bot/loop)
# ----------------------------------------------------------------------
_bot_thread = None
_bot_stop = threading.Event()
_bot_loop_state = {"running": False, "live": False, "since": None}


def _auto_loop_live() -> bool:
    """Effective live mode for the server-side bot loop. Real-money
    brokers (live Alpaca, Binance) are NEVER auto-traded — only paper
    brokers may run the loop live; everything else falls back to dry-run."""
    if not config.BOT_AUTO_LOOP_LIVE:
        return False
    if config.EXECUTION_BROKER == "binance":
        return False
    if config.EXECUTION_BROKER == "alpaca" and not config.ALPACA_PAPER:
        return False
    return True


def _start_bot_loop() -> bool:
    """Start the bot loop thread (idempotent). Returns True if started."""
    global _bot_thread
    if _bot_thread is not None and _bot_thread.is_alive():
        return False
    live = _auto_loop_live()
    _bot_stop.clear()
    _bot_loop_state.update({
        "running": True, "live": live,
        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _bot_thread = threading.Thread(target=_bot_loop_worker, args=(live,),
                                   daemon=True, name="bot-loop")
    _bot_thread.start()
    return True


def _stop_bot_loop() -> bool:
    """Ask the loop to finish after the current cycle. True if it was running."""
    if _bot_thread is None or not _bot_thread.is_alive():
        return False
    _bot_stop.set()
    return True


def _bot_loop_worker(live: bool) -> None:
    import bot as _bot
    conn = database.connect()
    database.init_db(conn)

    def _record_cycle(summary: dict) -> None:
        """Surface auto-loop cycles to the dashboard (same slot as manual
        /api/bot/run cycles)."""
        _bot_last_cycle.update({"summary": summary, "at": time.time()})

    try:
        _bot.bot_loop(conn, dry_run=not live,
                      interval_seconds=config.BOT_LOOP_SECONDS,
                      stop_event=_bot_stop, on_cycle=_record_cycle)
    finally:
        _bot_loop_state.update({"running": False, "live": False, "since": None})


def _write_env_key(key: str, value: str) -> None:
    """Set one KEY=VALUE line in the project .env, preserving every other
    line (and never printing/reading the secrets in it)."""
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.split("=", 1)[0].strip() == key:
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


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
    if config.BOT_AUTO_LOOP:
        if _start_bot_loop():
            print("  Bot loop thread started"
                  + (" (live/paper)" if _auto_loop_live() else " (dry-run)"))
        else:
            print("  Bot loop already running")


if __name__ == "__main__":
    import uvicorn

    no_live = "--no-live" in sys.argv
    print("cryptoautomation terminal -> http://localhost:8501")
    if no_live:
        print("  (--no-live: serving only, no live data fetching)")
    else:
        _start_live_threads()
    uvicorn.run(app, host="0.0.0.0", port=8501)
