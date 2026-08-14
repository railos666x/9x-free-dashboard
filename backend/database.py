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
from datetime import datetime, timezone

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
        """
    )
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
