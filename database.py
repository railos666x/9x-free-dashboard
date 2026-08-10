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
    """Open (and create if needed) the SQLite database file."""
    conn = sqlite3.connect(config.DB_PATH)
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
    conn.executemany(
        """
        INSERT OR REPLACE INTO candles
            (symbol, interval, open_time, open, high, low, close, volume, close_time)
        VALUES
            (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume, :close_time)
        """,
        [dict(r, symbol=symbol, interval=interval) for r in rows],
    )
    conn.commit()
    return len(rows)


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
