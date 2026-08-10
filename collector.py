"""
Binance price collector.

Two jobs:
  1. `backfill` — download history (e.g. 30 days of 1m candles) into SQLite.
  2. `live`     — keep polling the newest candle and upserting it forever.

Run:
    python collector.py backfill
    python collector.py live

No API key needed — this is Binance's public market data.
"""

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import config
import database

# Public Binance REST endpoint for candlesticks (klines).
KLINES_URL = "https://api.binance.com/api/v3/klines"


def _api_symbol(symbol: str) -> str:
    """Binance's raw REST API wants 'BTCUSDT', not 'BTC/USDT'."""
    return symbol.replace("/", "")


def _to_rows(raw: list) -> list:
    """Convert Binance's raw kline arrays into our dict format."""
    rows = []
    for k in raw:
        rows.append(
            {
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
            }
        )
    return rows


def fetch_klines(symbol: str, interval: str, limit: int = 1000) -> list:
    """
    Fetch up to `limit` candles for a symbol+interval from Binance.
    Binance returns each candle as a long list like
    [open_time_ms, open, high, low, close, volume, close_time_ms, ...];
    `_to_rows` keeps only the fields we care about.
    """
    resp = requests.get(
        KLINES_URL,
        params={"symbol": _api_symbol(symbol), "interval": interval, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()  # raise an error if the request failed
    return _to_rows(resp.json())


def backfill(conn, symbol: str, interval: str, days: int) -> None:
    """
    Walk backwards in pages of 1000 candles until we have `days` of history.
    Idempotent: re-running only adds whatever is missing.
    """
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_ms = None  # None means "start from the latest candle"
    total = 0

    print(f"Backfilling {symbol} {interval} (last {days} days)...")
    while True:
        params = {"symbol": _api_symbol(symbol), "interval": interval, "limit": 1000}
        if end_ms:
            params["endTime"] = end_ms
        resp = requests.get(KLINES_URL, params=params, timeout=15)
        resp.raise_for_status()
        rows = _to_rows(resp.json())
        if not rows:
            break

        total += database.upsert_candles(conn, symbol, interval, rows)
        oldest = rows[0]["open_time"]
        if oldest <= start_ms:
            break  # we've reached far enough back
        end_ms = oldest - 1  # next page ends just before this page's oldest
        print(f"  ... {total} candles so far")
        time.sleep(0.2)  # be polite to the API

    print(f"  Done: {total} candles stored.")


def live_loop(conn, refresh_seconds: int = None) -> None:
    """
    Forever: fetch the newest few candles for every symbol and upsert them.
    The dashboard reads from the DB, so it stays fresh as long as this runs.
    """
    refresh_seconds = refresh_seconds or config.LIVE_REFRESH_SECONDS
    print(f"Live loop started — refreshing every {refresh_seconds}s. Ctrl+C to stop.")
    while True:
        for symbol in config.SYMBOLS:
            try:
                rows = fetch_klines(symbol, config.DEFAULT_INTERVAL, limit=5)
                n = database.upsert_candles(conn, symbol, config.DEFAULT_INTERVAL, rows)
                if n:
                    latest = rows[-1]["close"]
                    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {symbol} "
                          f"last={latest:,.2f} ({n} rows)")
            except Exception as e:
                print(f"  [{symbol}] error: {e}")
        time.sleep(refresh_seconds)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backfill"

    conn = database.connect()
    database.init_db(conn)

    if cmd == "backfill":
        for symbol in config.SYMBOLS:
            backfill(conn, symbol, config.DEFAULT_INTERVAL, config.BACKFILL_DAYS)
        print(f"\nTotal candles in DB: {database.count_candles(conn)}")
    elif cmd == "live":
        live_loop(conn)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python collector.py [backfill|live]")


if __name__ == "__main__":
    main()
