"""
cryptoautomation — Fetch ALL data with one command.

    python fetch_all.py

Downloads everything the platform needs into crypto_data.db:

    1. Binance candles — every symbol x every interval (per-interval depth
       from config.INTERVAL_BACKFILL_DAYS: 1m/5m 30d, 15m 90d, 1h/4h 365d,
       1d 730d). Idempotent: re-running only fills gaps.
    2. Funding rates — perpetual funding history for every symbol.
    3. Macro assets — gold (XAU/USD) + S&P 500 (SPX/USD) daily history.

Useful for interactive shell use (no need to remember the individual
commands):

    python collector.py backfill          # candles only
    python collector.py funding           # funding rates only
    python macro_data.py backfill         # gold + S&P 500 only

Flags:
    --candles-only    skip funding + macro
    --fast            skip 1m and 5m (they're ~80% of the request volume)
"""

from __future__ import annotations

import sys
import time

import config
import collector
import database
import macro_data


def main() -> None:
    candles_only = "--candles-only" in sys.argv
    fast = "--fast" in sys.argv

    conn = database.connect()
    database.init_db(conn)

    t0 = time.time()
    print("=" * 60)
    print("fetch_all — populating crypto_data.db")
    print("=" * 60)

    # ── 1. Candles ────────────────────────────────────────────────
    print("\n[1/3] Binance candles")
    intervals = config.INTERVALS
    if fast:
        intervals = [iv for iv in intervals if iv not in ("1m", "5m")]
        print(f"  (--fast: skipping 1m/5m, using {intervals})")
    failed = 0
    for symbol in config.SYMBOLS:
        for interval in intervals:
            days = config.INTERVAL_BACKFILL_DAYS.get(interval, config.BACKFILL_DAYS)
            try:
                collector.backfill(conn, symbol, interval, days)
            except Exception as e:
                failed += 1
                print(f"  [{symbol} {interval}] SKIP: {e}")
        print(f"  {symbol}: done ({database.count_candles(conn, symbol)} candles)")
    print(f"  total candles: {database.count_candles(conn)} | skipped pairs: {failed}")

    if candles_only:
        _done(t0)
        return

    # ── 2. Funding rates ──────────────────────────────────────────
    print("\n[2/3] Funding rates (perpetual futures)")
    collector.backfill_funding(conn)

    # ── 3. Macro assets ───────────────────────────────────────────
    print("\n[3/3] Macro assets (gold + S&P 500)")
    macro_data.backfill_macro(conn, days=config.INTERVAL_BACKFILL_DAYS.get("1d", 730))

    _done(t0)


def _done(t0: float) -> None:
    print("\n" + "=" * 60)
    print(f"fetch_all complete in {time.time() - t0:.0f}s")
    print("Start the terminal with:  python server.py   ->  http://localhost:8501")
    print("=" * 60)


if __name__ == "__main__":
    main()
