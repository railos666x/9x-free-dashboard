"""
SQLite storage for market candles and news.

Uses Python's built-in sqlite3 — no extra dependency, and you can open
crypto_data.db with any SQLite viewer (e.g. DB Browser for SQLite) to
look at the raw data.

WAL journal mode is enabled so the live data loops can write while the
dashboard reads, without "database is locked" errors.
"""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pandas as pd

import config


def connect() -> sqlite3.Connection:
    """
    Open (and create if needed) the SQLite database file.

    check_same_thread=False: Streamlit runs the app script in a new thread
    on every rerun, so the module-level connection must be usable from any
    thread. Safe here because WAL mode handles concurrent access.
    """
    # timeout=30: with the live server's collector also writing to this DB,
    # short write bursts can collide — wait up to 30s instead of failing with
    # "database is locked" (WAL allows one writer + many readers).
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row  # access rows by column name
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the tables if they don't exist yet."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS candles (
            symbol     TEXT,
            interval   TEXT,
            open_time  INTEGER,          -- ms epoch, Binance format
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            volume     REAL,
            close_time INTEGER,
            PRIMARY KEY (symbol, interval, open_time)
        );

        CREATE TABLE IF NOT EXISTS funding (
            symbol       TEXT,
            funding_time INTEGER,          -- ms epoch of the funding settlement
            rate         REAL,             -- funding rate as decimal (0.0001 = 0.01%)
            PRIMARY KEY (symbol, funding_time)
        );

        CREATE INDEX IF NOT EXISTS idx_funding_symbol ON funding(symbol);

        CREATE TABLE IF NOT EXISTS macro (
            symbol     TEXT,
            open_time  INTEGER,          -- ms epoch (Yahoo timestamps are seconds)
            open       REAL,
            high       REAL,
            low        REAL,
            close      REAL,
            volume     REAL,
            PRIMARY KEY (symbol, open_time)
        );

        CREATE INDEX IF NOT EXISTS idx_macro_symbol ON macro(symbol);

        CREATE TABLE IF NOT EXISTS news (
            id              TEXT PRIMARY KEY,  -- hash of url+title, for de-duplication
            source          TEXT,
            title           TEXT,
            url             TEXT,
            published_at    TEXT,
            sentiment       TEXT,              -- bullish | bearish | neutral | important
            sentiment_score REAL,              -- how strong the signal is (-1..1)
            symbols         TEXT,              -- coins this story is about, comma-separated
            fetched_at      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);

        CREATE TABLE IF NOT EXISTS intents (
            intent_hash  TEXT PRIMARY KEY,   -- sha256(symbol|direction|window) — dedup key
            symbol       TEXT,
            direction    TEXT,               -- LONG | SHORT
            window       TEXT,               -- signal window, e.g. "1h"
            sources      TEXT,               -- comma-separated source ids that agreed
            entry        REAL,
            stop_loss    REAL,
            take_profit  REAL,
            confidence   REAL,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            intent_hash TEXT UNIQUE,         -- one order per intent → no duplicate orders
            symbol      TEXT,
            side        TEXT,                -- LONG | SHORT
            qty         REAL,
            entry       REAL,
            stop_loss   REAL,
            take_profit REAL,
            source      TEXT,                -- which broker/gateway placed it
            status      TEXT,                -- filled | open | closed | cancelled | error
            fill_price  REAL,
            fill_time   TEXT,
            fees        REAL,
            pnl         REAL,
            error       TEXT,
            reasons     TEXT,                -- why the trade was taken (sources A+B)
            broker_ref  TEXT,                -- broker order id (e.g. Alpaca order UUID)
            created_at  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

        CREATE TABLE IF NOT EXISTS news_verdicts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT,
            direction  TEXT,                -- the proposed trade: LONG | SHORT
            verdict    TEXT,                -- confirm | veto | neutral
            score      REAL,                -- the news score (-100..100)
            big_move   INTEGER,             -- 1 = big-move expected (vetoed)
            mode       TEXT,                -- rule | llm
            message    TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_news_verdicts_created ON news_verdicts(created_at);
        """
    )
    # Migrate older orders tables that predate the exit columns.
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}
        for col in ("exit_price", "exit_time", "exit_reason", "reasons", "broker_ref"):
            if col not in cols:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
        conn.commit()
    except Exception:
        pass  # table may not exist yet — CREATE IF NOT EXISTS covers fresh DBs
    conn.commit()


# ----------------------------------------------------------------------
# Candles
# ----------------------------------------------------------------------
def upsert_candles(conn, symbol: str, interval: str, rows: list) -> int:
    """
    Insert or replace candle rows. `rows` is a list of dicts with keys:
    open_time, open, high, low, close, volume, close_time.

    Returns how many rows were written (including updates).
    """
    if not rows:
        return 0
    return _write_retry(conn, [dict(r, symbol=symbol, interval=interval) for r in rows],
                        _UPSERT_CANDLES_SQL)


_UPSERT_CANDLES_SQL = """
    INSERT OR REPLACE INTO candles
        (symbol, interval, open_time, open, high, low, close, volume, close_time)
    VALUES
        (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume, :close_time)
"""


def _write_retry(conn, params_list: list, sql: str, attempts: int = 5) -> int:
    """Run an executemany+commit with retry on 'database is locked'.

    The live server writes to this DB continuously; under contention a write
    can hit SQLITE_BUSY even with a long busy timeout. Retrying is safe
    because every upsert here is idempotent (INSERT OR REPLACE).
    """
    import time
    for attempt in range(attempts):
        try:
            conn.executemany(sql, params_list)
            conn.commit()
            return len(params_list)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < attempts - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    return 0


def load_candles(conn, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """
    Return the most recent candles for a symbol+interval as a DataFrame,
    oldest first. Columns: open_time, open, high, low, close, volume, close_time.
    """
    query = """
        SELECT open_time, open, high, low, close, volume, close_time
        FROM candles
        WHERE symbol = ? AND interval = ?
        ORDER BY open_time DESC
        LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=[symbol, interval, limit])
    return df.iloc[::-1].reset_index(drop=True)  # reverse so it's oldest -> newest


def count_candles(conn, symbol=None, interval=None) -> int:
    """How many candles are stored (optionally for one symbol/interval)."""
    query = "SELECT COUNT(*) AS n FROM candles"
    params = []
    if symbol:
        query += " WHERE symbol = ?"
        params.append(symbol)
        if interval:
            query += " AND interval = ?"
            params.append(interval)
    row = conn.execute(query, params).fetchone()
    return row["n"]


# ----------------------------------------------------------------------
# Funding rates (Binance perpetuals, fapi.binance.com)
# ----------------------------------------------------------------------
def upsert_funding(conn, symbol: str, rows: list) -> int:
    """
    Insert or replace funding-rate rows. `rows` is a list of dicts with
    keys: funding_time, rate. Returns how many rows were written.
    """
    if not rows:
        return 0
    return _write_retry(conn, [dict(r, symbol=symbol) for r in rows],
                        """
        INSERT OR REPLACE INTO funding (symbol, funding_time, rate)
        VALUES (:symbol, :funding_time, :rate)
        """)


def load_funding(conn, symbol: str, limit: int = 1000) -> pd.DataFrame:
    """Most recent funding rates for a symbol, oldest first."""
    query = """
        SELECT funding_time, rate
        FROM funding
        WHERE symbol = ?
        ORDER BY funding_time DESC
        LIMIT ?
    """
    df = pd.read_sql_query(query, conn, params=[symbol, limit])
    return df.iloc[::-1].reset_index(drop=True)


def count_funding(conn, symbol: str = None) -> int:
    query = "SELECT COUNT(*) AS n FROM funding"
    params = []
    if symbol:
        query += " WHERE symbol = ?"
        params.append(symbol)
    return conn.execute(query, params).fetchone()["n"]


# ----------------------------------------------------------------------
# Macro assets (gold, S&P 500 — Yahoo Finance, separate from Binance candles)
# ----------------------------------------------------------------------
def upsert_macro(conn, symbol: str, rows: list) -> int:
    """Insert or replace macro OHLCV rows. `rows`: list of dicts with keys
    open_time, open, high, low, close, volume (open_time in ms)."""
    if not rows:
        return 0
    return _write_retry(conn, [dict(r, symbol=symbol) for r in rows],
                        """
        INSERT OR REPLACE INTO macro (symbol, open_time, open, high, low, close, volume)
        VALUES (:symbol, :open_time, :open, :high, :low, :close, :volume)
        """)


def load_macro(conn, symbol: str = None, limit: int = 400) -> pd.DataFrame:
    """Macro candles, oldest first. Optionally one symbol."""
    query = "SELECT symbol, open_time, open, high, low, close, volume FROM macro"
    params = []
    if symbol:
        query += " WHERE symbol = ?"
        params.append(symbol)
    query += " ORDER BY open_time DESC LIMIT ?"
    params.append(limit)
    df = pd.read_sql_query(query, conn, params=params)
    return df.iloc[::-1].reset_index(drop=True)


def macro_symbols(conn) -> list[str]:
    rows = conn.execute("SELECT DISTINCT symbol FROM macro").fetchall()
    return [r[0] for r in rows]


# ----------------------------------------------------------------------
# News
# ----------------------------------------------------------------------
def _news_id(url: str, title: str) -> str:
    """Stable unique id for a story: a short hash of url + title."""
    raw = f"{url}|{title}".strip().lower()
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def save_news(conn, items: list) -> int:
    """
    Insert news items that aren't already stored.
    `items` is a list of dicts with keys: source, title, url, published_at,
    sentiment, sentiment_score, symbols.

    Returns the number of NEW rows added.
    """
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for item in items:
        nid = _news_id(item.get("url", ""), item.get("title", ""))
        if conn.execute("SELECT 1 FROM news WHERE id = ?", (nid,)).fetchone():
            continue  # already have it — skip (de-duplication)
        conn.execute(
            """
            INSERT OR IGNORE INTO news
                (id, source, title, url, published_at, sentiment, sentiment_score, symbols, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nid,
                item.get("source", ""),
                item.get("title", ""),
                item.get("url", ""),
                item.get("published_at", ""),
                item.get("sentiment", "neutral"),
                float(item.get("sentiment_score", 0.0)),
                item.get("symbols", ""),
                now,
            ),
        )
        added += 1
    conn.commit()
    return added


def load_news(conn, limit: int = 50, sentiment=None, symbol=None) -> pd.DataFrame:
    """Latest news as a DataFrame, newest first. Optionally filter."""
    query = "SELECT * FROM news WHERE 1=1"
    params = []
    if sentiment:
        query += " AND sentiment = ?"
        params.append(sentiment)
    if symbol:
        # match stories whose `symbols` column contains the coin, e.g. "BTC"
        coin = symbol.split("/")[0]
        query += " AND symbols LIKE ?"
        params.append(f"%{coin}%")
    query += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)
    return pd.read_sql_query(query, conn, params=params)


# ----------------------------------------------------------------------
# Intents & orders (execution journal — see arbitrator.py / execution.py)
# ----------------------------------------------------------------------
def record_intent(conn, intent) -> bool:
    """
    Persist an approved trade intent. Returns True if it was NEW, False if
    the same intent_hash already exists (i.e. a duplicate that must not
    produce another order).
    """
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO intents
            (intent_hash, symbol, direction, window, sources, entry,
             stop_loss, take_profit, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (intent.intent_hash, intent.symbol, intent.direction, intent.window,
         ",".join(intent.sources), intent.entry, intent.stop_loss,
         intent.take_profit, intent.confidence, now),
    )
    conn.commit()
    return cur.rowcount > 0


def intent_exists(conn, intent_hash: str) -> bool:
    return conn.execute("SELECT 1 FROM intents WHERE intent_hash = ?",
                        (intent_hash,)).fetchone() is not None


def record_order(conn, order: dict) -> int:
    """
    Insert an order row keyed by intent_hash (INSERT OR IGNORE — a second
    attempt for the same intent silently becomes a no-op). Returns the new
    rowid, or 0 if it already existed.
    """
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO orders
            (intent_hash, symbol, side, qty, entry, stop_loss, take_profit,
             source, status, fill_price, fill_time, fees, pnl, error,
             reasons, broker_ref, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (order["intent_hash"], order["symbol"], order["side"], order["qty"],
         order["entry"], order["stop_loss"], order["take_profit"],
         order["source"], order["status"], order.get("fill_price"),
         order.get("fill_time"), order.get("fees"), order.get("pnl"),
         order.get("error"), order.get("reasons"), order.get("broker_ref"),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount > 0 else 0


def order_for_intent(conn, intent_hash: str) -> dict | None:
    row = conn.execute("SELECT * FROM orders WHERE intent_hash = ?",
                       (intent_hash,)).fetchone()
    return dict(row) if row else None


def discard_dry_run(conn, intent_hash: str) -> bool:
    """Remove a preview (dry_run) order row so a later REAL order can be
    placed for the same intent. A dry run must never consume the trade —
    only a placed/error order is idempotent."""
    cur = conn.execute(
        "DELETE FROM orders WHERE intent_hash = ? AND status = 'dry_run'",
        (intent_hash,))
    conn.commit()
    return cur.rowcount > 0


def load_orders(conn, symbol: str = None, status: str = None,
                limit: int = 100) -> list[dict]:
    """Recent orders, newest first. Optionally filter by symbol/status."""
    query = "SELECT * FROM orders WHERE 1=1"
    params = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# News gate verdict history (see reviewer.verify_trade / bot.py)
# ----------------------------------------------------------------------
def record_news_verdict(conn, symbol: str, direction: str, verdict: str,
                        score: float, big_move: bool, mode: str,
                        message: str = "") -> None:
    """Journal one news-gate decision so veto/confirm stats can be tracked."""
    conn.execute(
        """INSERT INTO news_verdicts
           (symbol, direction, verdict, score, big_move, mode, message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (symbol, direction, verdict, float(score or 0), 1 if big_move else 0,
         mode or "rule", message or "",
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def news_verdict_stats(conn, days: int = 7) -> dict:
    """Veto/confirm/neutral totals for the last `days`, for the dashboard."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT verdict, COUNT(*) AS n FROM news_verdicts WHERE created_at >= ? "
        "GROUP BY verdict", (cutoff,)).fetchall()
    out = {"veto": 0, "confirm": 0, "neutral": 0, "days": days, "total": 0}
    for r in rows:
        if r["verdict"] in out:
            out[r["verdict"]] = r["n"]
    out["total"] = out["veto"] + out["confirm"] + out["neutral"]
    return out


def paper_equity(conn) -> float:
    """Paper-trading equity: starting capital + realized PnL on closed
    orders. Used by the paper broker for risk-based sizing."""
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) AS pnl FROM orders WHERE status = 'closed'"
    ).fetchone()
    return config.INITIAL_CAPITAL + float(row["pnl"] or 0.0)


def update_order_exit(conn, order_id: int, exit_price: float, exit_time: str,
                      exit_reason: str, pnl: float, fees: float) -> None:
    """Mark an open/filled order as closed with its exit + realized PnL."""
    conn.execute(
        """
        UPDATE orders
        SET status = 'closed', exit_price = ?, exit_time = ?, exit_reason = ?,
            pnl = ?, fees = ?
        WHERE id = ? AND status IN ('filled', 'open')
        """,
        (exit_price, exit_time, exit_reason, pnl, fees, order_id),
    )
    conn.commit()
