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

# Token registry for unified symbol management
try:
    from . import token_registry
except ImportError:
    import token_registry

# Public Binance REST endpoint for candlesticks (klines).
KLINES_URL = "https://api.binance.com/api/v3/klines"
# Public Binance USD-M futures endpoint for funding-rate history (no key).
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"


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


def backfill(conn, symbol: str, interval: str, days: int) -> int:
    """
    Walk backwards in pages of 1000 candles until we have `days` of history.
    Idempotent: re-running only adds whatever is missing.

    Returns the number of candles stored. Raises on network/API errors so
    the caller can skip a dead symbol without aborting the whole run.
    """
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    end_ms = None  # None means "start from the latest candle"
    total = 0

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
        time.sleep(0.2)  # be polite to the API

    return total


def fill_interior_gaps(conn, symbol: str, interval: str) -> int:
    """
    Backfill INTERIOR holes (missing bars between two stored bars) from real
    Binance klines. repair_gaps() only heals the newest edge — an interior
    hole (e.g. the machine off Aug 19 -> Sep 7) stayed forever, and SMA-50
    computed across it dragged pre-gap prices into post-gap means, drawing a
    false diagonal across the chart. Skips symbols Binance doesn't list.

    Returns the number of candles added.
    """
    iv_ms = _interval_to_ms(interval)
    ots = [r[0] for r in conn.execute(
        "SELECT open_time FROM candles WHERE symbol=? AND interval=? ORDER BY open_time",
        (symbol, interval)).fetchall()]
    if len(ots) < 2:
        return 0
    total = 0
    for i in range(len(ots) - 1):
        d = ots[i + 1] - ots[i]
        if d <= iv_ms * 3:
            continue
        cursor = ots[i] + iv_ms
        end = ots[i + 1]
        while cursor < end:
            try:
                resp = requests.get(KLINES_URL, params={
                    "symbol": _api_symbol(symbol), "interval": interval,
                    "startTime": cursor, "endTime": min(end - 1, cursor + 999 * iv_ms),
                    "limit": 1000}, timeout=15)
                resp.raise_for_status()
                rows = _to_rows(resp.json())
            except Exception:
                break  # dead symbol / network — leave the hole, don't spin
            if not rows:
                break
            total += database.upsert_candles(conn, symbol, interval, rows)
            cursor = rows[-1]["open_time"] + iv_ms
            time.sleep(0.15)
    return total


def repair_gaps(conn, symbol: str, interval: str, days: int = 7) -> int:
    """
    Check the newest stored candle against wall-clock time and backfill any
    gap left by downtime (e.g. the collector wasn't running).

    Returns the number of candles added.
    """
    df = database.load_candles(conn, symbol, interval, limit=3)
    if df.empty:
        return backfill(conn, symbol, interval, days)

    interval_ms = _interval_to_ms(interval)
    newest = int(df["open_time"].iloc[-1])
    now_ms = int(time.time() * 1000)

    # If the newest candle is older than ~2.5 intervals, backfill the gap.
    if now_ms - newest > int(2.5 * interval_ms):
        return backfill(conn, symbol, interval, days)
    return 0


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    n = int(interval[:-1])
    table = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return n * table[unit]


def fetch_funding_history(symbol: str, limit: int = 1000) -> list:
    """
    Fetch funding-rate history for a perpetual symbol (e.g. "BTCUSDT")
    from Binance's public USD-M futures endpoint. No API key needed.

    Returns a list of dicts: {funding_time, rate}.
    """
    resp = requests.get(
        FUNDING_URL,
        params={"symbol": symbol.replace("/", ""), "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {"funding_time": int(f["fundingTime"]), "rate": float(f["fundingRate"])}
        for f in resp.json()
    ]


def backfill_funding(conn, symbols: list = None) -> None:
    """
    Download funding-rate history for every watched symbol into the
    `funding` table. Symbols without a perpetual listing are skipped
    (they're spot-only or delisted) without aborting the run.
    """
    if symbols is None:
        binance_symbols = [s.symbol for s in token_registry.get_symbols_by_venue(token_registry.Venue.BINANCE)]
        symbols = [s.replace("/", "") for s in binance_symbols]
    failed = []
    for sym in symbols:
        try:
            rows = fetch_funding_history(sym, limit=1000)
            n = database.upsert_funding(conn, sym, rows)
            print(f"  {sym}: {n} funding rows")
        except Exception as e:
            failed.append(sym)
            print(f"  [{sym}] skip: {e}")
        time.sleep(0.15)
    print(f"\nFunding backfill done. Rows: {database.count_funding(conn)}")
    if failed:
        print(f"Skipped {len(failed)} symbols (spot-only / delisted): {', '.join(failed)}")


def live_loop(conn, refresh_seconds: int = None) -> None:
    """
    Forever: keep every configured timeframe fresh, each at its own cadence.

    Short intervals poll often (1m every 5s), long ones rarely (1d every 15m),
    so all six timeframes in the terminal have live data while we stay well
    inside Binance's public rate limits. The dashboard reads from the DB, so it
    stays fresh as long as this runs.

    Each symbol/interval is wrapped in try/except so a dead listing can never
    kill the loop, and stale timeframes are gap-repaired on the next poll.
    """
    refresh_seconds = refresh_seconds or config.LIVE_REFRESH_SECONDS
    last_fetch = {}  # interval -> wall-clock time of the last poll

    def due(interval: str) -> bool:
        cadence = config.INTERVAL_REFRESH.get(interval, config.LIVE_REFRESH_SECONDS)
        return time.time() - last_fetch.get(interval, 0.0) >= cadence

    print("Live loop started — every timeframe on its own cadence. Ctrl+C to stop.")
    # Get Binance symbols from token registry
    binance_symbols = [s.symbol for s in token_registry.get_symbols_by_venue(token_registry.Venue.BINANCE)]
    while True:
        for interval in config.INTERVALS:
            if not due(interval):
                continue
            last_fetch[interval] = time.time()
            for symbol in binance_symbols:
                try:
                    # Repair any gap left by collector downtime (cheap: only
                    # fires when the newest candle is stale).
                    repair_gaps(conn, symbol, interval, days=3)
                    # Heal interior holes too, but rarely: only when a hole
                    # actually exists (single indexed query per symbol),
                    # since a real fill costs API round-trips.
                    _hole_check = conn.execute(
                        "SELECT COUNT(*) FROM ("
                        "  SELECT open_time - LAG(open_time) OVER (ORDER BY open_time) AS d"
                        "  FROM candles WHERE symbol=? AND interval=?)"
                        "WHERE d > ?",
                        (symbol, interval, _interval_to_ms(interval) * 3)).fetchone()
                    if _hole_check and _hole_check[0] > 0:
                        fill_interior_gaps(conn, symbol, interval)
                    rows = fetch_klines(symbol, interval, limit=5)
                    n = database.upsert_candles(conn, symbol, interval, rows)
                    if n and interval in ("1m", "5m", "15m"):
                        latest = rows[-1]["close"]
                        print(f"  [{datetime.now().strftime('%H:%M:%S')}] "
                              f"{symbol} {interval} last={latest:,.2f} ({n} rows)")
                except Exception as e:
                    # A dead/invalid symbol must not abort the loop.
                    pass
        time.sleep(refresh_seconds)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backfill"

    conn = database.connect()
    database.init_db(conn)

    if cmd == "backfill":
        failed = []
        # Get Binance symbols from token registry
        binance_symbols = [s.symbol for s in token_registry.get_symbols_by_venue(token_registry.Venue.BINANCE)]
        for symbol in binance_symbols:
            for interval in config.INTERVALS:
                days = config.INTERVAL_BACKFILL_DAYS.get(interval, config.BACKFILL_DAYS)
                try:
                    n = backfill(conn, symbol, interval, days)
                    print(f"  {symbol} {interval}: {n} candles")
                except Exception as e:
                    failed.append((symbol, interval))
                    print(f"  [{symbol} {interval}] SKIP: {e}")
        print(f"\nTotal candles in DB: {database.count_candles(conn)}")
        if failed:
            print(f"Skipped {len(failed)} symbol/interval pairs (likely delisted "
                  f"or not on Binance Spot): {failed[:10]}")
    elif cmd == "funding":
        backfill_funding(conn)
    elif cmd == "live":
        live_loop(conn)
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python collector.py [backfill|funding|live]")


if __name__ == "__main__":
    main()
