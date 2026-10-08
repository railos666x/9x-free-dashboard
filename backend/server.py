"""
cryptoautomation — web terminal server.

Serves the Bloomberg-style terminal (terminal/index.html + assets) and a
small JSON API that reads the SQLite database.  Live data loops (prices
from Binance + news from RSS) run automatically as daemon threads.

Run:
    python server.py              # starts everything — no extra terminals
    python server.py --no-live    # serve only, skip live data fetching
    python server.py --reload     # dev: auto-restart on backend/dashboard edits
"""

import math
import os
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
import arbitrator  # manual-trading endpoints build exec-ready Intents

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DASHBOARD = WEB / "dashboard"

app = FastAPI(title="cryptoautomation", version="0.3.0")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Return an empty 204 to suppress browser favicon 404s."""
    from fastapi.responses import Response
    return Response(status_code=204)

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


def _broker_sources() -> list[str]:
    """Active journal source keys — straight from execution's broker registry
    (the single place accounts are declared). In `hybrid` mode orders are
    journaled under their concrete broker (mt5/binance/...), never 'hybrid'."""
    import execution
    return execution.broker_keys()


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


_binance_ticker_cache: dict = {"at": 0.0, "data": {}}


def _ticker_chunk(syms: list[str]) -> dict:
    """One batched 24hr-ticker call (public, no key, max 100 symbols).
    Binance wants compact JSON in `symbols` — whitespace makes it 400."""
    raw = json.dumps(syms, separators=(",", ":"))
    r = requests.get("https://api.binance.com/api/v3/ticker/24hr",
                     params={"symbols": raw}, timeout=8)
    r.raise_for_status()
    return {d["symbol"]: d for d in r.json()}


def _binance_tickers(fiat_pairs: list[str]) -> dict:
    """24hr tickers for the given Binance pairs (BTCUSDT style), batched,
    with a 30s shared cache — /api/watchlist is polled repeatedly."""
    import time as _time
    now = _time.time()
    want = set(fiat_pairs)
    if _binance_ticker_cache["data"] and now - _binance_ticker_cache["at"] < 30.0:
        return {k: v for k, v in _binance_ticker_cache["data"].items() if k in want}
    out: dict = {}
    chunk: list[str] = []
    try:
        for s in fiat_pairs:
            chunk.append(s)
            if len(chunk) == 100:
                out.update(_ticker_chunk(chunk))
                chunk = []
        if chunk:
            out.update(_ticker_chunk(chunk))
    except Exception:
        pass  # best-effort: cached/partial data is fine, candles still work
    if out:
        _binance_ticker_cache.update({"at": now, "data": out})
    return out


@app.get("/api/watchlist")
def watchlist(interval: str = config.DEFAULT_INTERVAL):
    """One stat row per symbol: last price, % change, RSI, sparkline.

    Covers EVERY known asset (TRADING_UNIVERSE + SYMBOLS), even ones the
    bot can't trade: collected symbols get full indicators from candles;
    the rest are priced live from Binance's public 24hr ticker (RSI and
    sparkline unavailable without candles). Each row carries `tradable`
    so the UI can distinguish bot-tradeable from watch-only."""
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

    # ── Every remaining known asset, even if we never trade it ──
    # Collected candles are the primary source; anything the system knows
    # about but doesn't collect gets a live public Binance ticker row.
    known = list(dict.fromkeys(config.TRADING_UNIVERSE + config.SYMBOLS))
    emitted = {r["symbol"] for r in out}
    missing = [s for s in known if s not in emitted and s.endswith("/USDT")]
    for pair, t in _binance_tickers([s.replace("/", "") for s in missing]).items():
        ours = f"{pair[:-4]}/USDT"
        try:
            out.append({
                "symbol": ours,
                "price": float(t["lastPrice"]),
                "change": round(float(t["priceChangePercent"]), 2),
                "rsi": None,
                "volume": float(t["quoteVolume"]),
                "spark": [],
            })
        except (KeyError, TypeError, ValueError):
            continue

    # ── Macro universe: FX / metals / energy / indices (2026-10-07) ──
    # config.SYMBOLS is crypto-only but the bot's MT5 book is the wider
    # TRADABLE_UNIVERSE (gold, FX majors/crosses, oil, indices). Until now
    # NO picker in the dashboard could even list these — the user could not
    # backtest gold though 1h candles sit in the DB. Priced from the
    # collected candles (Yahoo 1h backfill): change = last close vs prev
    # close; RSI skipped (no add_all pass — this endpoint polls often).
    # Macro feeds are 1h-only: if the requested interval is empty the row
    # falls back to 1h so "15m poll" doesn't black out the whole sleeve.
    for s in config.TRADABLE_UNIVERSE:
        if s in emitted or s.endswith("/USDT"):
            continue  # crypto priced live above; no duplicates
        df = database.load_candles(conn, s, interval, limit=40)
        if df.empty and interval != "1h":
            df = database.load_candles(conn, s, "1h", limit=40)
        if df.empty:
            continue  # never collected — hidden in pickers (same as before)
        last = df["close"].iloc[-1]
        prev = df["close"].iloc[-2] if len(df) > 1 else last
        chg = ((last - prev) / prev * 100) if prev else 0.0
        out.append({
            "symbol": s,
            "price": _clean(last),
            "change": round(chg, 2),
            "rsi": _clean(df["rsi"].iloc[-1]) if "rsi" in df else None,
            "volume": 0.0,
            "spark": _series(df, "close")[-40:],
        })
        emitted.add(s)
    for r in out:
        r["tradable"] = r["symbol"] in config.TRADING_UNIVERSE
    return {"interval": interval, "symbols": out}


# ── FX mastery (backend/forex.py) ──────────────────────────────────────────
_forex_cache: dict = {"at": 0.0, "data": {"ok": False, "error": "not loaded yet"}}


@app.get("/api/forex/overview")
def forex_overview():
    """One payload for the Forex view: session, USD index, currency strength,
    per-pair trend snapshots, open exposure by currency and carry bias.
    Cached 60s — 32 pairs x 220 candles is a real query but stable within a minute."""
    import time as _time
    import forex as _forex
    now = _time.time()
    if _forex_cache["data"].get("ok") and now - _forex_cache["at"] < 60.0:
        return _forex_cache["data"]
    conn = database.connect()
    try:
        data = _forex.overview(conn)
    except Exception as e:
        data = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _forex_cache.update({"at": now, "data": data})
    return data


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


@app.get("/api/news/analytics")
def news_analytics(limit: int = 25):
    """Structured per-asset news analytics from the Groq analyst: sentiment
    score (-100..100), confidence, impact horizon, driving headlines — the
    algorithmic-consumption view of the news (see news_analyst.py)."""
    from news_analyst import analyze_market, fetch_headlines
    conn = database.connect()
    try:
        review = analyze_market(conn, limit=min(limit, 60))
    except Exception as e:
        return {"ok": False, "error": str(e), "coins": []}
    if not review:
        return {"ok": False,
                "error": "Groq analyst unavailable (no GROQ_API_KEY, rate-limited, or no news)",
                "coins": []}
    return {"ok": True, "mode": review.get("mode"), "model": review.get("model"),
            "market": review.get("market"), "stories": review.get("stories"),
            "coins": review.get("coins", []),
            "headlines": [h["title"] for h in fetch_headlines(conn, limit=8)]}


@app.get("/api/news/analyze")
def news_analyze(headline: str = "", content: str = "", symbol: str = "",
                 url: str = ""):
    """Agent 1 — News & Catalyst Sentiment: score one headline/article for
    structural price impact (sentiment, impact score, black-swan flag,
    duration, core catalyst). The answer is journaled for the trade
    explainer. Returns {ok, agent, result} or {ok: False} when off/failed.

    When `url` is given and `content` is empty, the article is scraped
    with Firecrawl (web_scraper.get_article — cached) so the agent reasons
    over the full article, not the bare headline.
    """
    import ai_agents
    import web_scraper
    conn = database.connect()
    if url and not content:
        article = web_scraper.get_article(conn, url)
        if article:
            content = article.get("content") or ""
            headline = headline or article.get("title") or ""
        else:
            return {"ok": False,
                    "error": "article scrape failed (no FIRECRAWL_API_KEY, out of credits, or unreachable)"}
    try:
        result = ai_agents.agent_news_sentiment(headline, content,
                                                conn=conn, symbol=symbol)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not result:
        return {"ok": False, "error": "agent unavailable (no GROQ_API_KEY / AI_AGENTS_ENABLED=false / unparseable)"}
    return {"ok": True, "agent": "news_sentiment", "result": result,
            "scraped": bool(url and content)}


@app.get("/api/news/article")
def news_article(url: str = ""):
    """Scrape one article URL to clean markdown (Firecrawl, cached 24h).
    Returns {ok, title, content} or {ok: False} when scraping is off /
    fails. The raw material for the news agents."""
    import web_scraper
    if not url:
        return {"ok": False, "error": "url parameter is required"}
    conn = database.connect()
    try:
        article = web_scraper.get_article(conn, url)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not article:
        return {"ok": False,
                "error": "scrape failed (no FIRECRAWL_API_KEY, out of credits, or unreachable)"}
    return {"ok": True, "title": article["title"],
            "content": article["content"], "chars": len(article["content"])}


@app.get("/api/portfolio/analyze")
def portfolio_analyze(symbol: str = "BTC/USDT", catalyst: str = ""):
    """Agent 2 — Deep Research & HODL Portfolio: given a detected catalyst
    for `symbol`, decide ACCUMULATE_AND_HOLD / TRADE_ONLY / AVOID with a
    confidence percentage. Journaled under ai_analyses(hold_research)."""
    import ai_agents
    conn = database.connect()
    try:
        result = ai_agents.agent_hold_research(symbol, catalyst, conn=conn)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not result:
        return {"ok": False, "error": "agent unavailable (no GROQ_API_KEY / AI_AGENTS_ENABLED=false)"}
    return {"ok": True, "agent": "hold_research", "result": result}


@app.get("/api/trade/verify")
def trade_verify(symbol: str = "BTC/USDT", direction: str = "LONG"):
    """Agent 3 — Price Action & Chart Confluence: verify a news-driven
    trade against the live chart (price, 1h change, S/R, news sentiment).
    Returns {ok, result} where result.is_valid_setup tells the caller
    whether to take the trade, plus entry_strategy and a stop-loss."""
    import ai_agents
    conn = database.connect()
    try:
        result = ai_agents.agent_chart_confluence(
            conn, symbol, direction, force=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not result:
        return {"ok": False, "error": "agent unavailable (no GROQ_API_KEY / AI_AGENTS_ENABLED=false / no candles)"}
    return {"ok": True, "agent": "chart_confluence", "result": result}


@app.get("/api/portfolio/holds")
def portfolio_holds(limit: int = 20, agent: str = None):
    """Recent AI agent journal: every news-sentiment / hold-research /
    chart-confluence answer, newest first — the "why" behind agent-driven
    decisions (see ai_agents.py + docs/math_system/07-ai-agents.md)."""
    conn = database.connect()
    rows = database.load_ai_analyses(conn, agent=agent or None,
                                     limit=min(limit, 200))
    return {"count": len(rows), "analyses": rows}


@app.get("/api/tutor")
def tutor(topic: str = None):
    """Educational layer: list every topic, or the full explanation of one
    (market_order, leverage, slippage, atr_volatility, … see tutor.py)."""
    import tutor
    if topic:
        t = tutor.explain(topic)
        if not t:
            return {"ok": False, "error": f"unknown topic: {topic}",
                    "topics": [x["topic"] for x in tutor.list_topics()]}
        return {"ok": True, **t}
    return {"ok": True, "topics": tutor.list_topics()}


@app.get("/api/tutor/trade")
def tutor_trade(order_id: int = None):
    """Why was this trade taken (or skipped)? Explain a journal order from
    its recorded reasons, or the held-back reasoning from the last cycle."""
    import tutor
    conn = database.connect()
    if order_id:
        order = next((o for o in database.load_orders(conn, limit=2000)
                      if o.get("id") == order_id), None)
        return tutor.explain_trade(order)
    # No id: explain the last bot cycle's executed/held mix.
    summary = _bot_last_cycle.get("summary")
    return tutor.explain_last_cycle(summary)


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


@app.get("/api/broker-health")
def broker_health():
    """Live diagnostics per active broker: connection ok, key fingerprint,
    probe latency and last journal reconciliation. Answers "which account
    is this thing actually trading?" at a glance."""
    import execution
    return {"mode": config.EXECUTION_BROKER, "brokers": execution.broker_health()}


@app.get("/api/broker-accounts")
def broker_accounts():
    """Live account details from every real broker we trade on, plus the
    registry itself so the UI can render tags/donuts/filters without any
    hardcoded broker list. One card per broker, same layout."""
    import execution
    return {"accounts": execution.all_account_cards(),
            "brokers": execution.active_brokers()}


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
           to: str = Query(None, alias="to"),
           exit_from: str = Query(None, alias="exitFrom"),
           exit_to: str = Query(None, alias="exitTo"),
           source: str = Query(None)):
    """The trade journal: every order the bot has placed (or dry-run) for
    the CURRENT broker's account. Old accounts' trades (e.g. the retired
    Alpaca rows after switching to Exness/MT5) are hidden by default to
    avoid confusion — pass source=all to see every account's rows.

    detail=1 enriches each row with current price / market value /
    unrealized PnL and a sparkline of the last 24 1h closes, plus a
    journal summary block. When the broker is Alpaca, statuses are also
    auto-reconciled (throttled to once per 30s) so the journal mirrors
    the broker without a manual refresh.

    `from` / `to` (dates or ISO datetimes) filter by created-at window.
    `exitFrom` / `exitTo` filter by EXIT time — the correct dimension for
    a "closed trades period": combine with status=closed to see exactly
    the trades that closed in the window (open rows have no exit_time and
    drop out automatically).
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

    # Journal views default to the CURRENT broker so a retired account's
    # trades never resurface on the dashboard; source=all overrides.
    # In hybrid mode, orders are journaled under their concrete broker
    # (mt5/binance) — collect both so the dashboard shows them.
    src = None if (source or "") == "all" else (source or _broker_sources())
    if isinstance(src, list):
        # load_orders takes a single source filter — OR-union the rows.
        orders_list = []
        seen_ids = set()
        for s in src:
            for o in database.load_orders(conn, status=status or None, source=s,
                                          created_from=from_, created_to=to,
                                          exit_from=exit_from, exit_to=exit_to,
                                          limit=min(limit, 1000)):
                if o["id"] not in seen_ids:
                    seen_ids.add(o["id"])
                    orders_list.append(o)
        # Re-sort newest-first by id (load_orders is DESC per source already).
        orders_list.sort(key=lambda o: o["id"], reverse=True)
        orders_list = orders_list[:min(limit, 1000)]
    else:
        orders_list = database.load_orders(conn, status=status or None, source=src,
                                           created_from=from_, created_to=to,
                                           exit_from=exit_from, exit_to=exit_to,
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

    # ── Alpaca/MT5: blend the broker's real-time marks so the journal donut
    # matches the live account. Journal marks use 1h-candle closes (only
    # change hourly — the donut looks "stuck"); broker's per-position
    # unrealized PnL is marked continuously and also covers positions that
    # were opened outside the bot. Falls back to journal marks when the
    # account is unreachable.
    def _mt5_key(sym: str) -> str:
        """Journal stores 'EUR/USD', MT5 returns 'EURUSD' — normalize to compare."""
        return str(sym or "").replace("/", "").upper()

    acct_positions: dict[str, dict] = {}
    broker_name = config.EXECUTION_BROKER
    if broker_name == "alpaca":
        try:
            import portfolio as pf
            acct = pf._alpaca_account()
            if acct:
                acct_positions = acct.get("positions") or {}
        except Exception:
            acct_positions = {}
    elif broker_name in ("mt5", "hybrid"):
        # Fetch MT5 account positions (includes manually opened trades)
        try:
            import execution
            broker = execution.get_broker()
            if broker_name == "hybrid":
                # For hybrid, get MT5 broker specifically
                mt5_broker = broker._mt5
            else:
                mt5_broker = broker
            positions = mt5_broker.fetch_positions()
            # fetch_positions exposes profit/qty/current — map to the shape
            # the blending logic expects; notional market_value is derived.
            for pos in positions:
                sym = pos.get("symbol")
                if sym:
                    profit = pos.get("profit")
                    if profit is None:
                        profit = pos.get("unrealized_pl") or pos.get("unrealized_pnl") or 0
                    qty = float(pos.get("qty") or 0)
                    cur = float(pos.get("current") or pos.get("entry") or 0)
                    acct_positions[_mt5_key(sym)] = {
                        "unrealized_pl": float(profit or 0),
                        "market_value": round(qty * cur, 2),
                    }
        except Exception:
            acct_positions = {}
    if acct_positions:
        for r in enriched:
            if r.get("status") not in ("filled", "open"):
                continue
            src = (r.get("source") or "").lower()
            # Only rows this MT5 account could own get broker marks; other
            # brokers' rows (e.g. Binance) keep their candle-based marks.
            mt5_routed = src in ("mt5", "hybrid", "")
            pos = acct_positions.get(_mt5_key(r["symbol"])) if mt5_routed else None
            if pos:
                r["unrealized_pnl"] = round(pos["unrealized_pl"], 2)
                r["market_value"] = pos["market_value"]
            elif mt5_routed:
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
                              {"realized": 0.0, "unrealized": 0.0, "market_value": 0.0, "source": r.get("source", "")})
        if r.get("status") == "closed":
            e["realized"] += float(r.get("pnl") or 0)
        if r.get("status") in ("filled", "open"):
            e["unrealized"] += float(r.get("unrealized_pnl") or 0)
            e["market_value"] += float(r.get("market_value") or 0)
    # Account-only positions (opened outside the bot) still belong in the
    # donut, otherwise the centre total wouldn't match the slices.
    covered = {_mt5_key(r["symbol"]) for r in enriched
               if r.get("status") in ("filled", "open")
               and (r.get("source") or "").lower() in ("mt5", "hybrid", "")}
    for sym, pos in acct_positions.items():
        if sym in covered:
            continue  # already counted via its journal row — no double count
        e = by_sym.setdefault(sym,
                              {"realized": 0.0, "unrealized": 0.0, "market_value": 0.0, "source": "mt5"})
        e["unrealized"] += float(pos.get("unrealized_pl") or 0)
        e["market_value"] += float(pos.get("market_value") or 0)
    
    # Account-only closed deals (closed outside the bot) also belong in the
    # donut to show complete account history.
    acct_deals = []
    if broker_name in ("mt5", "hybrid"):
        try:
            import execution
            broker = execution.get_broker()
            if broker_name == "hybrid":
                mt5_broker = broker._mt5
            else:
                mt5_broker = broker
            # Fetch closed deals from last 30 days
            acct_deals = mt5_broker.fetch_deals(days=30)
        except Exception:
            acct_deals = []
    
    # Add closed deals to the aggregation — but skip deals the journal
    # already recorded (bot-opened trades get journal closed rows AND MT5
    # deal history; counting both doubles realized PnL).
    journal_closed = [( _mt5_key(r["symbol"]), round(float(r.get("pnl") or 0), 2))
                      for r in enriched if r.get("status") == "closed"]
    for deal in acct_deals:
        sym = deal.get("symbol")
        if not sym:
            continue
        d_pnl = round(float(deal.get("pnl") or 0), 2)
        if (_mt5_key(sym), d_pnl) in journal_closed:
            continue
        e = by_sym.setdefault(sym,
                              {"realized": 0.0, "unrealized": 0.0, "market_value": 0.0, "source": "mt5"})
        e["realized"] += d_pnl
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
            "source": e.get("source", ""),
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


# Alert state shared across requests (2026-10-06): the daily-digest
# thread records its last write result here so /api/alerts can surface a
# broken digest BETWEEN daily writes (a silent monitoring gap is itself
# an incident).
_DIGEST_STATE: dict = {"last_attempt": None, "last_ok": None,
                       "last_error": None}
_ALERTS_CACHE: dict = {"at": 0.0, "data": None}


def _order_protection(o: dict) -> str:
    """Venue-side protection class per open leg (2026-10-06) so the
    dashboard can show NAKED legs at a glance:
      venue-oco     exchange-side OCO list resting (oco_ref stamped)
      venue-sltp    SL/TP attached at the broker (MT5 basket rows)
      software-only journal levels only — NAKED whenever the bot is
                    offline (the whole reason OCO brackets exist)
      none          no levels on the row at all
      unknown       manual/live position whose protection we can't see
    """
    if o.get("oco_ref"):
        return "venue-oco"
    src = o.get("source") or ""
    if src == "mt5":
        return ("venue-sltp"
                if (o.get("stop_loss") or o.get("take_profit"))
                else "none")
    if o.get("stop_loss") or o.get("take_profit"):
        return "software-only"
    return "none"


def _compute_alerts(conn) -> list:
    """Real-time alert feed between daily digests (2026-10-06).

    Sources:
      - NAKED legs: open journal rows with software-only/no protection
        (critical when there are NO levels at all — nothing would close
        the trade if this process died).
      - loss guard BLOCKED (new entries are being refused).
      - digest thread failure (monitoring log may be silently stale).

    Cached 5s — the dashboard polls this fast, and each check touches
    the journal + MT5-facing guard math.
    """
    import execution as _ex

    alerts = []
    try:
        rows = database.load_orders(conn, limit=200)
        for o in rows:
            if o.get("status") not in ("filled", "open"):
                continue
            prot = _order_protection(o)
            if prot in ("software-only", "none"):
                alerts.append({
                    "id": f"naked:{o.get('id')}", "kind": "naked_leg",
                    "severity": "critical" if prot == "none" else "warning",
                    "symbol": o.get("symbol"),
                    "msg": (f"{o.get('symbol')} {o.get('side')} is NAKED — "
                            + ("no stop at all"
                               if prot == "none"
                               else "software monitoring only; no venue "
                                    "bracket survives a bot restart")),
                })
    except Exception:
        pass  # journal unavailable → skip naked checks, try the rest
    try:
        g = _ex.loss_guard_status(conn)
        if g.get("blocked"):
            alerts.append({
                "id": "loss-guard", "kind": "loss_guard",
                "severity": "critical", "symbol": None,
                "msg": f"Loss guard BLOCKED new entries — {g.get('reason') or 'daily loss limit hit'}",
            })
    except Exception:
        pass
    if _DIGEST_STATE.get("last_error"):
        alerts.append({
            "id": "digest-failed", "kind": "digest_failed",
            "severity": "warning", "symbol": None,
            "msg": ("Daily digest failed to write ("
                    + str(_DIGEST_STATE["last_error"])[:120]
                    + ") — MONITORING-LOG may be stale"),
        })
    return alerts


@app.get("/api/alerts")
def api_alerts():
    """Dashboard alert feed (naked legs / loss guard / digest failures)
    — polled every 15s; 5s server-side cache keeps it cheap."""
    now = time.time()
    if _ALERTS_CACHE["data"] is None or now - _ALERTS_CACHE["at"] > 5.0:
        conn = database.connect()
        try:
            _ALERTS_CACHE["data"] = _compute_alerts(conn)
        finally:
            conn.close()
        _ALERTS_CACHE["at"] = now
    data = _ALERTS_CACHE["data"] or []
    return {"alerts": data, "count": len(data),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.get("/api/positions")
def positions():
    """LIVE trades snapshot — open/filled journal rows for the CURRENT
    broker's account with a fresh price and unrealized PnL. Deliberately
    lean (no sparklines, no heavy enrichment) so the dashboard can poll it
    fast; broker-side marks (Alpaca) are blended when available. Old
    accounts' rows are excluded (source = current broker).
    """
    conn = database.connect()
    sources = _broker_sources()
    rows = []
    seen_ids = set()
    for s in sources:
        for o in database.load_orders(conn, source=s, limit=500):
            if o["id"] not in seen_ids:
                seen_ids.add(o["id"])
                if o.get("status") in ("filled", "open"):
                    rows.append(o)

    acct_positions = {}
    acct_positions_list = []  # list of all positions (supports multiple per symbol)
    broker_name = config.EXECUTION_BROKER
    if broker_name == "alpaca":
        try:
            import portfolio as pf
            acct = pf._alpaca_account()
            if acct:
                acct_positions = acct.get("positions") or {}
                # Alpaca doesn't have positions_list yet, convert from dict
                for sym, pos in acct_positions.items():
                    acct_positions_list.append({**pos, "symbol": sym})
        except Exception:
            acct_positions = {}
            acct_positions_list = []
    elif broker_name in ("mt5", "hybrid"):
        try:
            import execution
            broker = execution.get_broker()
            if broker_name == "hybrid":
                # For hybrid, get MT5 broker specifically
                mt5_broker = broker._mt5
            else:
                mt5_broker = broker
            # Fetch positions from MT5
            positions = mt5_broker.fetch_positions()
            # Convert to the format expected by the blending logic
            for pos in positions:
                sym = pos.get("symbol")
                if sym:
                    # Add to positions_list (supports multiple positions per symbol)
                    acct_positions_list.append({
                        "symbol": sym,
                        "side": pos.get("side", "long").lower() if pos.get("side") else "long",
                        "qty": pos.get("qty") or pos.get("volume"),
                        "entry": pos.get("entry") or pos.get("price") or pos.get("open_price"),
                        "current": pos.get("current") or pos.get("current_price"),
                        "unrealized_pl": float(pos.get("unrealized_pl") or pos.get("unrealized_pnl") or pos.get("profit") or 0),
                    })
                    # Also add to dict for single-position-per-symbol lookup
                    if sym not in acct_positions:
                        acct_positions[sym] = acct_positions_list[-1]
        except Exception:
            acct_positions = {}
            acct_positions_list = []

    out = []
    seen_symbols = set()
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
            "oco_ref": o.get("oco_ref"),
            "protection": _order_protection(o),
        })
        seen_symbols.add(o["symbol"])
    
    # Add live broker positions that aren't in the journal (manual trades)
    # Use positions_list to support multiple positions per symbol
    seen_entries = set()
    for o in rows:
        seen_entries.add((o["symbol"], o.get("entry")))
    
    for pos in acct_positions_list:
        sym = pos.get("symbol")
        entry = pos.get("entry")
        # Skip if this exact position (symbol + entry) is already in journal
        if (sym, entry) in seen_entries:
            continue
        df = database.load_candles(conn, sym, "1h", limit=3)
        cur = float(df["close"].iloc[-1]) if not df.empty else pos.get("current")
        out.append({
            "symbol": sym,
            "side": pos.get("side", "long"),
            "qty": pos.get("qty"),
            "entry": entry,
            "fill_price": entry,
            "stop_loss": None,
            "take_profit": None,
            "current_price": cur,
            "unrealized_pnl": pos.get("unrealized_pl"),
            "created_at": None,
            "source": "live",
            "oco_ref": None,
            # Manual venue position — MT5 may carry attached SL/TP but the
            # fetch doesn't expose them, so report honestly.
            "protection": "unknown",
        })
    
    return {"positions": out, "count": len(out),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.post("/api/positions/levels")
def update_position_levels(payload: dict):
    """Drag-and-drop levels from the chart: persist new SL/TP to the journal
    row AND push them to the real broker where the position lives (MT5 via
    TRADE_ACTION_SLTP; Binance/Alpaca spot market positions carry no server-
    side SL/TP — journal-only there). Payload: {order_id, symbol, stop_loss,
    take_profit} (either level may be null to leave it unchanged)."""
    import execution
    order_id = payload.get("order_id")
    symbol = str(payload.get("symbol") or "")
    new_sl = payload.get("stop_loss")
    new_tp = payload.get("take_profit")
    if not symbol or (order_id is None and not payload.get("broker_only")):
        return {"ok": False, "error": "symbol and order_id required"}
    conn = database.connect()
    pushed = None
    errors = []
    # 1) Real broker first — if the broker rejects, don't poison the journal.
    broker = execution.get_broker()
    target = broker
    if getattr(broker, "name", "") == "hybrid":
        from token_registry import Venue, resolve_symbol
        info = resolve_symbol(symbol)
        is_crypto = bool(info and (getattr(info, "venue", None) == Venue.BINANCE
                                   or getattr(info, "asset_class", None) and "CRYPTO" in str(getattr(info, "asset_class"))))
        if not is_crypto and not symbol.upper().endswith(("USDT",)):
            target = broker._mt5
        else:
            target = None  # Binance spot: no server-side SL/TP amend
    if target is not None and hasattr(target, "modify_position_sltp"):
        try:
            pushed = target.modify_position_sltp(symbol, new_sl, new_tp)
        except Exception as e:
            errors.append(f"broker: {e}")
    # 2) Journal row.
    if order_id is not None:
        sets, vals = [], []
        if new_sl is not None:
            sets.append("stop_loss = ?"); vals.append(float(new_sl))
        if new_tp is not None:
            sets.append("take_profit = ?"); vals.append(float(new_tp))
        if sets:
            vals.append(int(order_id))
            conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id = ?", vals)
            conn.commit()
    if errors and pushed is None:
        return {"ok": False, "error": "; ".join(errors), "journal": order_id is not None}
    return {"ok": True, "broker_push": pushed, "journal": order_id is not None}


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
    """Set the signal source from the dashboard.

    Since 2026-08-17 the bot trades ONLY on the AI/news analyst
    ("ai_news"); the candle-factor engines (math_engine / strategy /
    hybrid) are retired from the trade path and are refused here. Only
    allowed while the bot loop is STOPPED, so a running bot is never
    yanked mid-cycle. Takes effect immediately (next cycle) and is
    persisted to .env so a restart keeps the choice.
    """
    if _bot_thread is not None and _bot_thread.is_alive():
        return {"ok": False,
                "error": "bot is running — stop it first (Auto loop → Stop)"}
    payload = payload or {}
    source = str(payload.get("source") or "").strip().lower()
    if source != "ai_news":
        return {"ok": False,
                "error": "legacy engines (math_engine/strategy/hybrid) are retired from "
                          "trading — the bot trades on the AI/news analyst only (ai_news)"}
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
        "live_single_source_blocked": (config.SIGNAL_SOURCE_A
                                         in ("math_engine", "strategy", "hybrid")
                                         and config.ARBITRATION_MIN_SOURCES < 2
                                         and not config.ALLOW_LIVE_SINGLE_SOURCE),
        "auto_loop": config.BOT_AUTO_LOOP,
        "auto_loop_running": bool(_bot_thread is not None and _bot_thread.is_alive()),
        "auto_loop_live": _auto_loop_live(),
        "last_cycle": _bot_last_cycle["summary"],
        "last_run_at": _bot_last_cycle["at"],
        "open_orders": len([o for s in _broker_sources() for o in database.load_orders(database.connect(),
                                                            source=s,
                                                            limit=1000)
                            if o.get("status") in ("filled", "open")]),
        # 2026-08-17 upgrade: AI mode + confirmation layer status
        "ai_mode": config.AI_MODE,
        "ai_confirm_enabled": config.AI_CONFIRM_ENABLED,
        "dxy_filter_enabled": config.DXY_FILTER_ENABLED,
        "smc_enabled": config.SMC_ENABLED,
        "calendar_guard_enabled": config.CALENDAR_GUARD_ENABLED,
        "circuit_breaker_enabled": config.CIRCUIT_BREAKER_ENABLED,
        "trailing_stop_enabled": config.TRAILING_STOP_ENABLED,
    }


# ----------------------------------------------------------------------
# Dashboard stats (TradinJournal-style overview cards + radar + heatmap)
# ----------------------------------------------------------------------
@app.get("/api/trade-stats")
def trade_stats():
    """Aggregated trading stats for the TradinJournal-style dashboard:
    today's P&L, total P&L, current streak, win rate, profit factor,
    daily activity for the calendar heatmap, and radar scores."""
    from datetime import datetime, timedelta, timezone
    conn = database.connect()
    sources = _broker_sources()
    orders = []
    seen_ids = set()
    for s in sources:
        for o in database.load_orders(conn, source=s, limit=2000):
            if o["id"] not in seen_ids:
                seen_ids.add(o["id"])
                orders.append(o)
    closed = [o for o in orders if o.get("status") == "closed" and o.get("pnl") is not None]

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    today_pnl = 0.0
    total_pnl = 0.0
    wins = 0
    losses = 0
    total_win_amount = 0.0
    total_loss_amount = 0.0
    daily_pnl = {}  # date -> pnl sum
    biggest_win = 0.0
    best_day_pnl = 0.0

    # Bookkeeping-only rows (a retired account's closes — see A31 analysis)
    # are NOT strategy performance: one such event on 2026-08-17 booked −677
    # from four legacy RSI(2) positions and made every headline stat look
    # negative while the live book was +411. They are counted separately.
    retired = [o for o in closed if o.get("exit_reason") == "account_retired"]
    strategy = [o for o in closed if o.get("exit_reason") != "account_retired"]

    for o in strategy:
        pnl = float(o["pnl"])
        total_pnl += pnl
        if pnl > 0:
            wins += 1
            total_win_amount += pnl
            biggest_win = max(biggest_win, pnl)
        elif pnl < 0:
            losses += 1
            total_loss_amount += abs(pnl)
        # Date key (exit_time or created_at)
        exit_time = o.get("exit_time") or o.get("created_at") or ""
        day = exit_time[:10] if exit_time else ""
        if day:
            daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        # Today's PnL
        if day == today_str:
            today_pnl += pnl

    # Best day
    if daily_pnl:
        best_day_pnl = max(daily_pnl.values())

    total_trades = wins + losses
    win_rate = round(wins / total_trades * 100, 1) if total_trades else 0.0
    avg_win = round(total_win_amount / wins, 2) if wins else 0.0
    avg_loss = round(total_loss_amount / losses, 2) if losses else 0.0
    profit_factor = round(total_win_amount / total_loss_amount, 2) if total_loss_amount > 0 else 99.99
    retired_pnl = round(sum(float(o["pnl"]) for o in retired), 2)
    retired_count = len(retired)

    # Streak calculation (most recent trades first)
    sorted_closed = sorted(closed, key=lambda o: o.get("exit_time") or o.get("created_at") or "", reverse=True)
    current_streak = 0
    streak_type = None
    longest_win_streak = 0
    longest_loss_streak = 0
    temp_streak = 0
    temp_type = None
    for o in sorted_closed:
        pnl = float(o["pnl"])
        is_win = pnl > 0
        st = "win" if is_win else ("loss" if pnl < 0 else None)
        if streak_type is None and st:
            streak_type = st
            current_streak = 1
        elif st == streak_type:
            current_streak += 1
        elif st is not None:
            break
        # Longest streak tracking
        if st == temp_type:
            temp_streak += 1
        else:
            if temp_type == "win":
                longest_win_streak = max(longest_win_streak, temp_streak)
            elif temp_type == "loss":
                longest_loss_streak = max(longest_loss_streak, temp_streak)
            temp_streak = 1
            temp_type = st
    if temp_type == "win":
        longest_win_streak = max(longest_win_streak, temp_streak)
    elif temp_type == "loss":
        longest_loss_streak = max(longest_loss_streak, temp_streak)
    longest_streak = max(longest_win_streak, longest_loss_streak)

    trading_days = len(daily_pnl)

    # Radar scores (0-100)
    radar = {
        "Win Rate": min(win_rate, 100),
        "Risk/Reward": min((avg_win / avg_loss * 50) if avg_loss > 0 else 0, 100),
        "Consistency": min(100 - (abs(total_pnl) / max(total_win_amount, 1) * 100) if total_pnl < 0 else max(0, 100 - (total_loss_amount / max(total_win_amount, 1) * 100)), 100),
        "Profitability": min(max(0, profit_factor * 30), 100),
        "Discipline": min(total_trades * 2, 100) if total_trades < 50 else 100,
        "Recovery": min(max(0, 100 - abs(total_pnl) / max(total_win_amount, 1) * 50), 100),
    }
    trade_score = round(sum(radar.values()) / len(radar))

    # Calendar heatmap data (last 180 days)
    cal_start = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
    heatmap = []
    for day in sorted(daily_pnl.keys()):
        if day >= cal_start:
            heatmap.append({"date": day, "pnl": round(daily_pnl[day], 2)})

    return {
        "today_pnl": round(today_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "balance": round(config.INITIAL_CAPITAL + total_pnl, 2),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "biggest_win": round(biggest_win, 2),
        # Bookkeeping separation: `total_pnl` above EXCLUDES retired-account
        # rows. These fields let the UI show the full picture honestly.
        "retired_pnl": retired_pnl,
        "retired_count": retired_count,
        "all_time_pnl": round(total_pnl + retired_pnl, 2),
        "best_day": round(best_day_pnl, 2),
        "current_streak": current_streak,
        "streak_type": streak_type or "none",
        "longest_streak": longest_streak,
        "trading_days": trading_days,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "trade_score": trade_score,
        "radar": radar,
        "heatmap": heatmap,
        "period": "all",
    }


# ----------------------------------------------------------------------
# Test AI trades + feedback loop (2026-08-17 upgrade)
# ----------------------------------------------------------------------
@app.get("/api/test-trades")
def test_trades(limit: int = 100, status: str = None):
    """AI paper-trading journal: test trades with full confirmation metadata.
    Separate from the live orders table — used when AI_MODE=test."""
    conn = database.connect()
    trades = database.load_test_trades(conn, limit=min(limit, 500),
                                       status=status or None)
    stats = database.test_trade_stats(conn)
    return {"trades": trades, "stats": stats}


@app.get("/api/test-portfolio")
def test_portfolio():
    """Test AI portfolio summary: win rate, PnL, breakdown by regime/session."""
    import test_ai
    conn = database.connect()
    return test_ai.test_portfolio_summary(conn)


@app.get("/api/gov-shadow")
def gov_shadow_status():
    """Government-officials shadow prototype (2026-10-05): recent official
    releases (separate gov_news pool) and the paper-only shadow book
    (test_trades rows tagged ai_model='groq-gov') with running stats.
    """
    import gov_analyst
    conn = database.connect()
    try:
        heads = database.load_gov_news(conn, limit=15)
        shadow = [t for t in database.load_test_trades(conn, limit=100)
                  if str(t.get("ai_model") or "") == "groq-gov"]
        closed = [t for t in shadow if t.get("outcome") in ("win", "loss",
                                                            "breakeven")]
        wins = sum(1 for t in closed if t.get("outcome") == "win")
        net = sum(float(t.get("pnl") or 0) for t in closed)
        return {
            "enabled": bool(config.GOV_FEEDS_ENABLED),
            "feeds": [n for n, _ in config.GOV_FEEDS],
            "recent_official": [
                {"source": h.get("source"), "title": (h.get("title") or "")[:110],
                 "published_at": h.get("published_at")} for h in heads],
            "shadow_trades": shadow,
            "stats": {"total": len(shadow), "closed": len(closed),
                      "open": len(shadow) - len(closed),
                      "wins": wins,
                      "win_rate": round(100.0 * wins / len(closed), 1)
                                  if closed else None,
                      "net_pnl": round(net, 4)},
        }
    finally:
        conn.close()


@app.get("/api/circuit-breaker")
def circuit_breaker_status():
    """Tiered circuit breaker state: current level, risk multiplier,
    drawdown, consecutive losses, strategy health."""
    import circuit_breaker
    conn = database.connect()
    state = circuit_breaker.current_risk_state(conn)
    health = circuit_breaker.strategy_health_check(conn)
    exposure = circuit_breaker.correlation_exposure(conn)
    return {"state": state, "health": health, "exposure": exposure}


@app.get("/api/feedback/calibration")
def feedback_calibration(days: int = 30):
    """Feedback loop calibration report: win rate by factor (regime, session,
    confirm_score, dxy_score, smc_score) with recommendations."""
    import ai_feedback
    conn = database.connect()
    return ai_feedback.calibration_report(conn, days=min(days, 90))


@app.get("/api/feedback/thresholds")
def feedback_thresholds():
    """Adaptive threshold suggestions based on actual trade outcomes.
    Suggests adjustments to sentiment, confidence, confirm score, and
    regime/session blocking based on what's actually winning."""
    import ai_feedback
    conn = database.connect()
    return ai_feedback.adaptive_thresholds(conn)


@app.get("/api/ai-layers")
def ai_layers_status():
    """Status of all 6 confirmation layers: enabled/disabled, current
    settings, and live state for the dashboard's pipeline visualization."""
    import ai_confirm
    conn = database.connect()
    # Current session
    session_info = ai_confirm.current_session()
    session_mult = ai_confirm.session_multiplier()
    # DXY state (best effort)
    dxy_info = {"available": False}
    try:
        import dxy_filter
        dxy_state = dxy_filter.get_dxy_state(conn)
        dxy_info = {"available": True, **dxy_state}
    except Exception:
        pass
    return {
        "ai_mode": config.AI_MODE,
        "layers": {
            "news_sentiment": {
                "enabled": config.NEWS_ANALYST_ENABLED,
                "sentiment_long": config.AI_SENTIMENT_LONG,
                "sentiment_short": config.AI_SENTIMENT_SHORT,
                "confidence_floor": config.AI_CONFIDENCE_FLOOR,
            },
            "freshness": {
                "enabled": True,
                "max_age_hours": config.NEWS_MAX_AGE_HOURS,
                "min_fresh": config.NEWS_MIN_FRESH_HEADLINES,
            },
            "calendar_guard": {
                "enabled": config.CALENDAR_GUARD_ENABLED,
                "high_impact_block_min": config.CALENDAR_HIGH_IMPACT_BLOCK_MIN,
            },
            "ai_confirm": {
                "enabled": config.AI_CONFIRM_ENABLED,
                "min_score": config.AI_CONFIRM_MIN_SCORE,
                "block_volatile": config.AI_REGIME_BLOCK_VOLATILE,
                "mtf_enabled": config.AI_MTF_ENABLED,
            },
            "dxy_filter": {
                "enabled": config.DXY_FILTER_ENABLED,
                "block_on_contradiction": config.DXY_BLOCK_ON_CONTRADICTION,
                "state": dxy_info,
            },
            "smart_money": {
                "enabled": config.SMC_ENABLED,
                "min_score": config.SMC_MIN_SCORE,
            },
        },
        "session": {
            "enabled": config.SESSION_AWARE_ENABLED,
            "current": session_info,
            "multiplier": session_mult,
        },
        "risk": {
            "circuit_breaker": config.CIRCUIT_BREAKER_ENABLED,
            "trailing_stop": config.TRAILING_STOP_ENABLED,
            "trail_activate_atr": config.TRAIL_ACTIVATE_ATR,
            "trail_distance_atr": config.TRAIL_DISTANCE_ATR,
            "partial_tp": config.PARTIAL_TP_ENABLED,
            "max_hold_bars": config.TP_MAX_HOLD_BARS,
        },
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
    # Macro data reality (2026-10-07): FX/metals/energy/indices candles are
    # collected at 1h ONLY — crypto gets 1m/15m/1h. A sub-hour request for
    # gold/FX used to return a silent "0 bars / 0 trades" wall, which is
    # exactly why the user reported "gold is not there in backtest". Fall
    # back to the 1h data and SAY we did (data_note drives a UI banner).
    data_note = None
    if result.total_bars < 60 and interval != "1h":
        fb = backtester.run_backtest(conn, symbol=symbol, interval="1h", limit=limit)
        if fb.total_bars >= 60:
            result = fb
            data_note = (f"{symbol} is collected at 1h only — showing the 1h "
                         f"backtest instead of {interval} (no {interval} "
                         f"candles exist for macro symbols yet).")
        else:
            data_note = (f"No candles collected for {symbol} at {interval} "
                         f"or 1h — run the collector for this symbol first.")
    return {
        "data_note": data_note,
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
    """Landing page: the dashboard (the old terminal was retired 2026-08-15).
    no-cache: the HTML shell must always revalidate — it carries the
    dashboard.js?v= cache-bust, so a stale shell means stale code. Static
    assets under /dash-assets keep heuristic caching (busted via ?v=)."""
    return FileResponse(DASHBOARD / "index.html", headers={
        "Cache-Control": "no-cache",
    })


# ----------------------------------------------------------------------
# Manual trading from the dashboard (2026-10-07)
# ----------------------------------------------------------------------
# A trader must be able to ACT from the dashboard, not just watch an AI
# trade. These endpoints ride the SAME execution path the bot uses
# (execute_intent / close_position) so every manual order gets: broker
# routing, risk sizing with the trader's own SL/TP, the R/R floor, the
# balance precheck, venue-side protection (OCO for Binance / SL-TP for
# MT5), and the trade journal. Closing goes through close_position so an
# OCO bracket is cancelled/fired-checked, PnL is realized and the exit is
# journaled — never a raw broker call.

def _manual_intent(symbol: str, direction: str, entry: float | None,
                   stop_loss: float | None, take_profit: float | None,
                   qty: float | None):
    """Build a bot-shaped Intent for a dashboard-placed order.

    The hash is manual-namespaced and minute-bucketed: re-placing the same
    manual trade within the day is a NEW order (a trader retrying after a
    rejection must not be told 'duplicate'), while double-clicking the
    Place button still collapses to one order via the front-end disable.
    Side-specific sanity: a LONG needs SL below entry, a SHORT above.
    """
    import execution as _ex
    direction = str(direction).upper().strip()
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
    entry = float(entry) if entry not in (None, "") else None
    stop_loss = float(stop_loss) if stop_loss not in (None, "") else None
    take_profit = float(take_profit) if take_profit not in (None, "") else None
    qty = float(qty) if qty not in (None, "") else None
    if qty is not None and qty <= 0:
        raise ValueError("qty must be positive")
    if entry and stop_loss:
        if direction == "LONG" and stop_loss >= entry:
            raise ValueError("LONG stop_loss must be BELOW the entry price")
        if direction == "SHORT" and stop_loss <= entry:
            raise ValueError("SHORT stop_loss must be ABOVE the entry price")
    window = "manual"
    minute = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    raw = f"manual|{symbol}|{direction}|{minute}"
    import hashlib as _hashlib
    h = _hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return arbitrator.Intent(
        symbol=symbol.upper().strip(), direction=direction,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        sources=["manual"], window=window, confidence=100.0,
        intent_hash=h, reasons=["manual order from dashboard"],
    )


@app.post("/api/manual/order")
def manual_order(body: dict):
    """Place a manual order from the dashboard.

    body: {symbol, side|direction, qty?, entry?, stopLoss|stop_loss?,
    takeProfit|take_profit?}. Optional qty → the bot's risk sizing runs
    with the given SL (or its ATR fallback). Optional entry/SL/TP → ATR
    defaults fill any missing level. Loss guard BLOCKED refuses new
    entries exactly like the bot cycle. dry:true previews sizing + fees
    without touching a broker.
    """
    import execution as _ex
    symbol = str(body.get("symbol") or "").strip()
    if not symbol:
        return JSONResponse(status_code=400, content={"error": "symbol is required"})
    direction = body.get("side") or body.get("direction") or ""
    try:
        intent = _manual_intent(
            symbol, direction,
            body.get("entry"), body.get("stopLoss") or body.get("stop_loss"),
            body.get("takeProfit") or body.get("take_profit"),
            body.get("qty"))
        guard = _ex.loss_guard_status(conn=database.connect())
        if guard.get("blocked"):
            return JSONResponse(status_code=423, content={
                "error": "loss guard BLOCKED — new entries refused "
                         f"({guard.get('reason')})",
                "guard": guard})
        conn = database.connect()
        res = _ex.execute_intent(conn, intent,
                                 qty=intent_qty(body),
                                 interval="1h",
                                 dry_run=bool(body.get("dry")))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:  # broker outages etc — surfaced, not swallowed
        return JSONResponse(status_code=502, content={"error": f"execution failed: {e}"})
    if not res.get("order"):
        return JSONResponse(status_code=422, content={
            "error": res.get("error") or "sizer refused the trade"})
    o = res["order"]
    return {
        "ok": True, "order": o, "duplicate": res.get("duplicate", False),
        "dry_run": res.get("dry_run", False),
    }


def intent_qty(body: dict):
    """Manual qty override: explicit qty means the trader sizes it (the
    executor still clamps to broker rules); None → risk sizing."""
    q = body.get("qty")
    try:
        return float(q) if q not in (None, "") else None
    except (TypeError, ValueError):
        return None


@app.post("/api/manual/close/{order_id}")
def manual_close(order_id: int):
    """Close one open position from the dashboard via the real close path
    (OCO lifecycle handled: cancel-or-fired-check, true exit reason)."""
    import execution as _ex
    conn = database.connect()
    rows = database.load_orders(conn, limit=2000)
    order = next((o for o in rows if o.get("id") == order_id), None)
    if not order:
        return JSONResponse(status_code=404, content={"error": f"order #{order_id} not found"})
    if order.get("status") not in ("filled", "open"):
        return JSONResponse(status_code=409, content={
            "error": f"order #{order_id} is {order.get('status')}, not open"})
    try:
        res = _ex.close_position(conn, order, reason="manual")
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": f"close failed: {e}"})
    return {"ok": True, "order": res["order"], "pnl": res.get("pnl"),
            "reason": res.get("reason")}


# ----------------------------------------------------------------------
# Bot auto-loop control (start/stop from the dashboard, see /api/bot/loop)
# ----------------------------------------------------------------------
_bot_thread = None
_bot_stop = threading.Event()
_bot_loop_state = {"running": False, "live": False, "since": None}

# ── Freeze watchdog (2026-10-01) ─────────────────────────────────────
# The bot loop froze for ~2h mid-cycle: a MetaTrader5 IPC call never
# returned (same no-timeout C-call class as the RSS hang) and Python
# cannot kill a wedged C call from another thread. The ONLY recovery is
# replacing the loop. The watchdog tracks cycle completions; when none
# arrive within the window it abandons the stuck thread and starts a
# fresh loop. The zombie thread (if it ever un-wedges) exits via the
# stop event and — thanks to the generation guard — cannot clobber the
# replacement's state.
_BOT_CYCLE_TIMEOUT = 1800.0   # 30 min: normal cycles finish in ~45s (news
                              # re-score) to a few minutes; 30 min = wedged.
_bot_cycle_ts = {"at": time.time()}
_bot_gen = {"n": 0}


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
    _bot_gen["n"] += 1
    live = _auto_loop_live()
    _bot_stop.clear()
    _bot_cycle_ts["at"] = time.time()   # fresh grace window for cycle #1
    _bot_loop_state.update({
        "running": True, "live": live,
        "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _bot_thread = threading.Thread(target=_bot_loop_worker,
                                   args=(live, _bot_gen["n"]),
                                   daemon=True, name="bot-loop")
    _bot_thread.start()
    return True


def _watch_bot_loop() -> None:
    """Daemon: restart the bot loop when it stops completing cycles.

    A completed cycle touches _bot_cycle_ts (via on_cycle). If nothing
    touches it for _BOT_CYCLE_TIMEOUT while the loop is supposed to run,
    the worker is wedged inside a no-timeout MT5/C call — abandon that
    thread (it stays stuck; Python can't kill it) and start a fresh loop
    that shares the DB and the stop event."""
    global _bot_thread
    while True:
        time.sleep(60)
        if not _bot_loop_state.get("running"):
            continue
        if _bot_stop.is_set():
            continue   # deliberate stop in progress — not a freeze
        stale_for = time.time() - _bot_cycle_ts["at"]
        if stale_for <= _BOT_CYCLE_TIMEOUT:
            continue
        t = _bot_thread
        print(f"  [watchdog] bot loop stale {int(stale_for)}s (thread "
              f"{'alive' if t is not None and t.is_alive() else 'dead'}) "
              "— wedged? restarting bot loop")
        _bot_stop.set()          # old worker exits cleanly if it un-wedges
        _bot_thread = None       # abandon the zombie thread
        _bot_cycle_ts["at"] = time.time()
        if _start_bot_loop():
            print("  [watchdog] bot loop restarted")


def _stop_bot_loop() -> bool:
    """Ask the loop to finish after the current cycle. True if it was running."""
    if _bot_thread is None or not _bot_thread.is_alive():
        return False
    _bot_stop.set()
    return True


def _bot_loop_worker(live: bool, gen: int = 0) -> None:
    import bot as _bot
    conn = database.connect()
    database.init_db(conn)

    def _record_cycle(summary: dict) -> None:
        """Surface auto-loop cycles to the dashboard (same slot as manual
        /api/bot/run cycles) and feed the freeze watchdog."""
        _bot_last_cycle.update({"summary": summary, "at": time.time()})
        _bot_cycle_ts["at"] = time.time()

    try:
        _bot.bot_loop(conn, dry_run=not live,
                      interval_seconds=config.BOT_LOOP_SECONDS,
                      stop_event=_bot_stop, on_cycle=_record_cycle)
    finally:
        # Generation guard: a watchdog-replaced (possibly un-wedged later)
        # old thread must not flip the flag off while its replacement runs.
        if gen == _bot_gen["n"]:
            _bot_loop_state.update({"running": False, "live": False,
                                    "since": None})


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

    def _gov_shadow_loop():
        # Government-officials feed + shadow analyst (PROTOTYPE 2026-10-05):
        # official releases land in the SEPARATE gov_news pool and proposed
        # trades are journaled PAPER-ONLY (test_trades, ai_model='groq-gov')
        # for a two-week evaluation. Live sentiment and live orders are
        # untouched. Soft-disabled by env (GOV_FEEDS_ENABLED=0) with zero
        # code risk: the whole loop is wrapped below.
        import news as _newsmod
        _newsmod.gov_live_loop()

    def _daily_digest_loop():
        # Daily digest → docs/MONITORING-LOG.md once per UTC day (2026-10-06):
        # the run-window report (win-rate trajectory, per-symbol table) lands
        # every day without a manual scripts/run_report.py / daily_digest.py
        # run. Catches up at startup when today's section is missing (host
        # restarts are frequent), then sleeps to just past the next 00:00
        # UTC boundary. An exception must never kill the thread — the next
        # iteration retries.
        import datetime as _dt
        sys.path.insert(0, str(ROOT / "scripts"))
        import daily_digest as dd

        def _today_present() -> bool:
            try:
                today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
                text = (ROOT / "docs" / "MONITORING-LOG.md").read_text(
                    encoding="utf-8", errors="replace")
                return f"## Digest {today}" in text
            except Exception:
                return False

        while True:
            try:
                if not _today_present():
                    _DIGEST_STATE["last_attempt"] = time.time()
                    problems = dd.main()
                    _DIGEST_STATE["last_ok"] = time.time()
                    _DIGEST_STATE["last_error"] = None
                    print("  [DIGEST] daily digest appended to "
                          "docs/MONITORING-LOG.md"
                          + (f" ({len(problems)} anomalies)" if problems
                             else ""))
            except Exception as e:
                _DIGEST_STATE["last_error"] = f"{type(e).__name__}: {e}"
                print(f"  [DIGEST] daily digest failed: "
                      f"{type(e).__name__}: {e}")
            now = _dt.datetime.now(_dt.timezone.utc)
            nxt = (now + _dt.timedelta(days=1)).replace(
                hour=0, minute=5, second=0, microsecond=0)
            time.sleep(max(60.0, (nxt - now).total_seconds()))

    def _exness_candle_loop():
        # Keep the bot's Exness 1h candle feed fresh (the macro loop only
        # refreshes the `macro` table — the bot's SMC/data gates read the
        # `candles` table, which went 1-4 days stale on 2026-08-18 and
        # blocked every signal until fetch_all.py was run manually).
        import macro_data
        conn = database.connect()
        database.init_db(conn)
        macro_data.exness_candle_loop(conn)

    t1 = threading.Thread(target=_prices, daemon=True, name="price-collector")
    t2 = threading.Thread(target=_news_loop, daemon=True, name="news-fetcher")
    t3 = threading.Thread(target=_macro_loop, daemon=True, name="macro-fetcher")
    t4 = threading.Thread(target=_exness_candle_loop, daemon=True, name="exness-candles")
    t1.start()
    t2.start()
    t3.start()
    t4.start()
    if config.GOV_FEEDS_ENABLED:
        threading.Thread(target=_gov_shadow_loop, daemon=True,
                         name="gov-shadow").start()
        print("  GOV shadow thread started (official feeds, paper-only)")
    threading.Thread(target=_daily_digest_loop, daemon=True,
                     name="daily-digest").start()
    print("  Daily digest thread started (MONITORING-LOG, once per UTC day)")
    threading.Thread(target=_watch_bot_loop, daemon=True,
                     name="bot-watchdog").start()
    print("  Live data threads started (prices + news + macro + exness candles)"
          " + bot-loop watchdog")
    if config.BOT_AUTO_LOOP:
        if _start_bot_loop():
            print("  Bot loop thread started"
                  + (" (live/paper)" if _auto_loop_live() else " (dry-run)"))
        else:
            print("  Bot loop already running")


def _reload_supervisor() -> None:
    """Dev-only: `python server.py --reload` runs the server as a child
    process and restarts it whenever a backend/ or dashboard file changes.

    uvicorn's own reloader is NOT used here: it serves the app from a
    child process while the live-data/bot threads stay in the parent, so
    a dashboard "Start loop" would spawn a SECOND bot loop on top of the
    parent's (double orders on a live paper broker). A full process
    restart keeps the server and its threads in one process per
    generation, exactly what a code edit wants.
    """
    import subprocess

    here = Path(__file__).resolve()
    watched = sorted(
        set([here])
        | set(here.parent.glob("*.py"))
        | set((ROOT / "web" / "dashboard").glob("*"))
        | {ROOT / ".env"}  # key/config edits must restart too — a running
        #                     server won't re-read .env until it respawns
    )
    watched = [p for p in watched if p.exists()]
    snap = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in watched if p.exists()}

    def spawn() -> subprocess.Popen:
        env = {**os.environ, "CRYPTOAUTO_RELOAD_CHILD": "1"}
        args = [a for a in sys.argv[1:] if a != "--reload"]
        return subprocess.Popen([sys.executable, str(here)] + args, env=env)

    child = spawn()
    last_spawn = time.time()
    print("  --reload: watching backend/ + web/dashboard — Ctrl+C to stop")
    try:
        while True:
            time.sleep(1.0)
            changed = [p for p in watched
                       if p.exists()
                       and (p.stat().st_mtime_ns, p.stat().st_size) != snap.get(p)]
            if changed:
                print(f"  --reload: {changed[0].name} changed — restarting…")
                child.terminate()
                child.wait()
                snap = {p: (p.stat().st_mtime_ns, p.stat().st_size)
                        for p in watched if p.exists()}
                child = spawn()
                last_spawn = time.time()
                continue
            if child.poll() is not None:
                # Crash-loop guard: if the server died almost immediately
                # (e.g. port already in use), don't respawn in a hot loop.
                if time.time() - last_spawn < 3:
                    print(f"  --reload: server exited fast (code {child.returncode}) "
                          "— waiting before restart…")
                    time.sleep(3)
                else:
                    print(f"  --reload: server exited (code {child.returncode}) — "
                          "restarting…")
                snap = {p: (p.stat().st_mtime_ns, p.stat().st_size)
                        for p in watched if p.exists()}
                child = spawn()
                last_spawn = time.time()
    except KeyboardInterrupt:
        child.terminate()
        child.wait()
        print("\n  --reload: stopped.")


if __name__ == "__main__":
    import uvicorn

    reload = "--reload" in sys.argv
    if reload and os.environ.get("CRYPTOAUTO_RELOAD_CHILD") != "1":
        _reload_supervisor()
        sys.exit(0)

    no_live = "--no-live" in sys.argv
    print("cryptoautomation terminal -> http://localhost:8501")
    if no_live:
        print("  (--no-live: serving only, no live data fetching)")
    else:
        _start_live_threads()
    uvicorn.run(app, host="0.0.0.0", port=8501)
