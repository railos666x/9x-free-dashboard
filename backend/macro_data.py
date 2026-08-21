"""
cryptoautomation — Macro asset data (metals, energy, agriculture, forex,
indices, rates) from Yahoo Finance's public chart API.

No API key, no account. Daily OHLCV stored in the `macro` table and served
to the terminal as /api/macro (grouped by category for the UI).

Universe (42 instruments, all verified live against Yahoo 2026-08-14):

    metals      XAU/USD (gold), XAG/USD (silver), XPT/USD (platinum),
                XPD/USD (palladium), COPPER/USD, ALUMINUM/USD, ZINC/USD,
                IRON_ORE/USD
    energy      WTI/USD (crude), BRENT/USD, NAT_GAS/USD, HEATING_OIL/USD,
                GASOLINE/USD (RBOB)
    agriculture WHEAT/USD, CORN/USD, SOYBEAN/USD, COFFEE/USD, SUGAR/USD,
                COCOA/USD, COTTON/USD
    forex       EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD,
                USD/CNY, DXY (dollar index)
    indices     SPX/USD (S&P 500), NDX/USD (Nasdaq), DOW/USD, RUT/USD,
                VIX, DAX, FTSE, NIKKEI, HANG_SENG, EUROSTOXX
    rates       US10Y, US30Y, US5Y, US2Y (treasury yields, %)

Usage:
    python macro_data.py backfill     # fetch full history for everything
    python macro_data.py live         # keep fresh (server daemon)
"""

from __future__ import annotations

import time
from urllib.parse import quote as _quote

import requests

import config
import database

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Display symbol -> Yahoo ticker, grouped by category. Kept as one ordered
# dict so categories preserve their display order in the UI.
MACRO_CATEGORIES: dict[str, dict[str, str]] = {
    "metals": {
        "XAU/USD": "GC=F", "XAG/USD": "SI=F", "XPT/USD": "PL=F",
        "XPD/USD": "PA=F", "COPPER/USD": "HG=F", "ALUMINUM/USD": "ALI=F",
        "ZINC/USD": "ZNC=F", "IRON_ORE/USD": "TIO=F",
    },
    "energy": {
        "WTI/USD": "CL=F", "BRENT/USD": "BZ=F", "NAT_GAS/USD": "NG=F",
        "HEATING_OIL/USD": "HO=F", "GASOLINE/USD": "RB=F",
    },
    "agriculture": {
        "WHEAT/USD": "ZW=F", "CORN/USD": "ZC=F", "SOYBEAN/USD": "ZS=F",
        "COFFEE/USD": "KC=F", "SUGAR/USD": "SB=F", "COCOA/USD": "CC=F",
        "COTTON/USD": "CT=F",
    },
    "forex": {
        "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
        "USD/CHF": "USDCHF=X", "AUD/USD": "AUDUSD=X", "USD/CAD": "USDCAD=X",
        "USD/CNY": "USDCNY=X", "DXY": "DX-Y.NYB",
    },
    "indices": {
        "SPX/USD": "^GSPC", "NDX/USD": "^IXIC", "DOW/USD": "^DJI",
        "RUT/USD": "^RUT", "VIX": "^VIX", "DAX": "^GDAXI", "FTSE": "^FTSE",
        "NIKKEI": "^N225", "HANG_SENG": "^HSI", "EUROSTOXX": "^STOXX50E",
        # India — from the trading watchlist; AI can reference them even if
        # no broker executes them (signal/context only).
        "NIFTY/USD": "^NSEI", "BANKNIFTY/USD": "^NSEBANK", "SENSEX/USD": "^BSESN",
    },
    "rates": {
        "US10Y": "^TNX", "US30Y": "^TYX", "US5Y": "^FVX", "US2Y": "^IRX",
    },
    "btc_futures": {
        "BTC1/USD": "BTC=F",   # Bitcoin CME futures (continuous) — BTC1!
    },
}

# ── US-listed spot Bitcoin ETFs (the AI-tradeable universe) ─────────────
# Display symbol -> Yahoo ticker (identical). 1h candles are fetched from
# Yahoo and stored in the `candles` table (not the `macro` table) so the
# signal engine / bot can trade them exactly like Binance pairs. Alpaca
# executes them; the paper broker executes anything.
ETF_TICKERS: dict[str, str] = {
    "IBIT": "IBIT", "BITB": "BITB", "FBTC": "FBTC", "BTCW": "BTCW",
    "BTCO": "BTCO", "HODL": "HODL", "EZBC": "EZBC", "ARKB": "ARKB",
}

# ── Exness/MT5 tradeable instruments → Yahoo ticker (1h candles) ────────
# Same display symbols as config.EXNESS_SYMBOLS, so the bot can signal and
# trade forex / metals / indices / BTC / ETH CFDs on the Exness demo.
EXNESS_YAHOO: dict[str, str] = {
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "USDJPY=X",
    "USD/CHF": "USDCHF=X", "AUD/USD": "AUDUSD=X", "USD/CAD": "USDCAD=X",
    "USD/CNY": "USDCNY=X", "DXY": "DX-Y.NYB",
    "EUR/GBP": "EURGBP=X", "EUR/JPY": "EURJPY=X", "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X", "NZD/USD": "NZDUSD=X", "USD/SGD": "USDSGD=X",
    "USD/MXN": "USDMXN=X", "EUR/CHF": "EURCHF=X",
    "EUR/AUD": "EURAUD=X", "EUR/CAD": "EURCAD=X", "EUR/NZD": "EURNZD=X",
    "GBP/AUD": "GBPAUD=X", "GBP/CAD": "GBPCAD=X", "GBP/NZD": "GBPNZD=X",
    "GBP/CHF": "GBPCHF=X", "AUD/CAD": "AUDCAD=X", "AUD/NZD": "AUDNZD=X",
    "NZD/CHF": "NZDCHF=X", "CAD/JPY": "CADJPY=X", "CHF/JPY": "CHFJPY=X",
    "NZD/JPY": "NZDJPY=X", "USD/ZAR": "USDZAR=X", "USD/TRY": "USDTRY=X",
    "USD/SEK": "USDSEK=X", "USD/NOK": "USDNOK=X", "USD/PLN": "USDPLN=X",
    "XAU/USD": "GC=F", "XAG/USD": "SI=F", "XPT/USD": "PL=F",
    "XPD/USD": "PA=F", "WTI/USD": "CL=F", "BRENT/USD": "BZ=F",
    "NAT_GAS/USD": "NG=F",
    "SPX/USD": "^GSPC", "NDX/USD": "^NDX", "DOW/USD": "^DJI",
    "DAX": "^GDAXI", "NIKKEI": "^N225", "FTSE": "^FTSE",
    "EUROSTOXX": "^STOXX50E", "HANG_SENG": "^HSI", "FR40": "^FCHI",
    "ES35": "^IBEX", "ITA40": "FTSEMIB.MI", "AU200": "^AXJO",
    "BTC/USDT": "BTC-USD", "ETH/USDT": "ETH-USD",
}

# Flat lookup for backward compatibility.
MACRO_SYMBOLS: dict[str, str] = {
    sym: tk for cat in MACRO_CATEGORIES.values() for sym, tk in cat.items()
}

# Display names for the UI.
MACRO_LABELS = {
    "XAU/USD": "Gold", "XAG/USD": "Silver", "XPT/USD": "Platinum",
    "XPD/USD": "Palladium", "COPPER/USD": "Copper", "ALUMINUM/USD": "Aluminum",
    "ZINC/USD": "Zinc", "IRON_ORE/USD": "Iron ore",
    "WTI/USD": "WTI Crude", "BRENT/USD": "Brent", "NAT_GAS/USD": "Natural gas",
    "HEATING_OIL/USD": "Heating oil", "GASOLINE/USD": "Gasoline (RBOB)",
    "WHEAT/USD": "Wheat", "CORN/USD": "Corn", "SOYBEAN/USD": "Soybeans",
    "COFFEE/USD": "Coffee", "SUGAR/USD": "Sugar", "COCOA/USD": "Cocoa",
    "COTTON/USD": "Cotton",
    "EUR/USD": "EUR/USD", "GBP/USD": "GBP/USD", "USD/JPY": "USD/JPY",
    "USD/CHF": "USD/CHF", "AUD/USD": "AUD/USD", "USD/CAD": "USD/CAD",
    "USD/CNY": "USD/CNY", "DXY": "Dollar index",
    "SPX/USD": "S&P 500", "NDX/USD": "Nasdaq 100", "DOW/USD": "Dow Jones",
    "RUT/USD": "Russell 2000", "VIX": "VIX", "DAX": "DAX",
    "FTSE": "FTSE 100", "NIKKEI": "Nikkei 225", "HANG_SENG": "Hang Seng",
    "EUROSTOXX": "Euro Stoxx 50", "NIFTY/USD": "Nifty 50",
    "BANKNIFTY/USD": "Nifty Bank", "SENSEX/USD": "S&P BSE Sensex",
    "BTC1/USD": "Bitcoin CME futures",
    "US10Y": "US 10Y yield", "US30Y": "US 30Y yield", "US5Y": "US 5Y yield",
    "US2Y": "US 2Y yield",
}

# Server daemon cadence: daily bars only move a few times per day.
MACRO_REFRESH_SECONDS = 6 * 3600  # 6 hours
# The bot's SMC/verification gates read 1h candles for the Exness universe
# (candles table), NOT the `macro` table — the 6h macro loop never touched
# them, so forex/metals/indices candles went stale and every signal was
# held on the data/SMC gates. Refresh the candle feed at 30 min.
EXNESS_CANDLE_REFRESH_SECONDS = 30 * 60


def category_of(symbol: str) -> str:
    """Which category a display symbol belongs to ('metals', 'forex', ...)."""
    for cat, syms in MACRO_CATEGORIES.items():
        if symbol in syms:
            return cat
    return "other"


def label_of(symbol: str) -> str:
    return MACRO_LABELS.get(symbol, symbol)


def fetch_daily(yahoo_ticker: str, days: int = 730) -> list[dict]:
    """
    Fetch daily OHLCV bars from Yahoo. Returns a list of dicts with keys
    open_time (ms), open, high, low, close, volume. Raises on failure.
    """
    url = f"{YAHOO_CHART}/{_quote(yahoo_ticker)}"
    resp = requests.get(
        url,
        params={"range": f"{max(days, 5)}d", "interval": "1d"},
        timeout=20,
        headers=_UA,
    )
    resp.raise_for_status()
    payload = resp.json()
    result = payload["chart"]["result"]
    if not result:
        raise ValueError(f"no chart data for {yahoo_ticker}")
    d = result[0]
    timestamps = d.get("timestamp") or []
    qdata = (d.get("indicators", {}).get("quote") or [{}])[0]

    def _v(key, i):
        arr = qdata.get(key, [None] * len(timestamps))
        return arr[i] if i < len(arr) else None

    rows = []
    for i, ts in enumerate(timestamps):
        close = _v("close", i)
        if close is None:
            continue  # skip non-trading days / null bars
        rows.append({
            "open_time": int(ts) * 1000,  # Yahoo uses seconds -> ms
            "open": _v("open", i),
            "high": _v("high", i),
            "low": _v("low", i),
            "close": close,
            "volume": _v("volume", i) or 0.0,
        })
    if not rows:
        raise ValueError(f"no usable bars for {yahoo_ticker}")
    return rows


def fetch_interval(yahoo_ticker: str, interval: str = "60m", days: int = 60) -> list[dict]:
    """
    Intraday OHLCV bars from Yahoo (e.g. 60m → our "1h"). Returns rows
    with keys open_time (ms), open, high, low, close, volume, close_time.
    Raises on failure. Yahoo caps 60m charts at ~730 days of history.
    """
    url = f"{YAHOO_CHART}/{_quote(yahoo_ticker)}"
    resp = requests.get(
        url,
        params={"range": f"{max(days, 5)}d", "interval": interval},
        timeout=20,
        headers=_UA,
    )
    resp.raise_for_status()
    payload = resp.json()
    result = payload["chart"]["result"]
    if not result:
        raise ValueError(f"no chart data for {yahoo_ticker}")
    d = result[0]
    timestamps = d.get("timestamp") or []
    qdata = (d.get("indicators", {}).get("quote") or [{}])[0]

    def _v(key, i):
        arr = qdata.get(key, [None] * len(timestamps))
        return arr[i] if i < len(arr) else None

    rows = []
    for i, ts in enumerate(timestamps):
        close = _v("close", i)
        if close is None:
            continue
        open_ms = int(ts) * 1000
        rows.append({
            "open_time": open_ms,
            "open": _v("open", i),
            "high": _v("high", i),
            "low": _v("low", i),
            "close": close,
            "volume": _v("volume", i) or 0.0,
            "close_time": open_ms + 3_599_999,  # 1h bar
        })
    if not rows:
        raise ValueError(f"no usable bars for {yahoo_ticker}")
    return rows


def _backfill_yahoo_candles(conn, tickers: dict, days: int, label: str,
                            symbols: list[str] = None) -> dict:
    """
    Yahoo 1h candles for a ticker map, stored in the `candles` table as
    interval "1h" so the signal engine / bot trade them exactly like
    Binance pairs. Returns {symbol: rows_stored}; never raises on
    per-symbol failure (a dead Yahoo or missing ticker just skips).
    """
    symbols = symbols or list(tickers.keys())
    out = {}
    for display in symbols:
        yahoo = tickers[display]
        try:
            rows = fetch_interval(yahoo, interval="60m", days=days)
            n = database.upsert_candles(conn, display, "1h", rows)
            out[display] = n
        except Exception as e:
            out[display] = 0
            print(f"  [{display}] skip: {e}")
        time.sleep(0.25)
    ok = sum(1 for v in out.values() if v > 0)
    print(f"  {label}: {ok}/{len(symbols)} symbols stored")
    return out


def backfill_etf_candles(conn, days: int = 60, symbols: list[str] = None) -> dict:
    """Yahoo 1h candles for the US Bitcoin ETFs (Alpaca-tradeable)."""
    return _backfill_yahoo_candles(conn, ETF_TICKERS, days, "etf-candles", symbols)


def backfill_exness_candles(conn, days: int = 120, symbols: list[str] = None) -> dict:
    """Yahoo 1h candles for the Exness/MT5 tradeable universe (forex,
    metals, indices, BTC/ETH CFDs)."""
    return _backfill_yahoo_candles(conn, EXNESS_YAHOO, days, "exness-candles", symbols)


def backfill_macro(conn, days: int = 730, symbols: list[str] = None) -> dict:
    """
    Download history for every macro asset into the `macro` table.
    Returns {symbol: rows_stored} and never raises on per-symbol failure.
    """
    symbols = symbols or list(MACRO_SYMBOLS.keys())
    out = {}
    for display in symbols:
        yahoo = MACRO_SYMBOLS[display]
        try:
            rows = fetch_daily(yahoo, days)
            n = database.upsert_macro(conn, display, rows)
            out[display] = n
        except Exception as e:
            out[display] = 0
            print(f"  [{display}] skip: {e}")
        time.sleep(0.25)
    ok = sum(1 for v in out.values() if v > 0)
    print(f"  macro: {ok}/{len(symbols)} symbols stored")
    return out


def refresh_macro(conn, days: int = 10) -> dict:
    """Upsert the most recent bars (keeps the charts current)."""
    return backfill_macro(conn, days=days)


def macro_loop(conn, refresh_seconds: int = None) -> None:
    """Daemon loop for the server: keep macro charts fresh."""
    refresh_seconds = refresh_seconds or MACRO_REFRESH_SECONDS
    print("Macro loop started (metals/energy/forex/indices/rates).")
    while True:
        try:
            refresh_macro(conn, days=10)
        except Exception as e:
            print(f"  [macro] refresh error: {e}")
        time.sleep(refresh_seconds)


def exness_candle_loop(conn, refresh_seconds: int = None) -> None:
    """Daemon loop for the server: keep the Exness 1h candles fresh.

    The bot trades ONLY this universe and its SMC + data-verification
    gates read these 1h candles (candles table). Without this loop the
    feed only updates when fetch_all.py runs, and stale candles block
    every signal (the 2026-08-18 outage: data was 1-4 days old).
    """
    refresh_seconds = refresh_seconds or EXNESS_CANDLE_REFRESH_SECONDS
    print("Exness candle loop started (forex/metals/indices 1h feed).")
    while True:
        try:
            backfill_exness_candles(conn, days=5)
        except Exception as e:
            print(f"  [exness-candles] refresh error: {e}")
        time.sleep(refresh_seconds)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "backfill"
    conn = database.connect()
    database.init_db(conn)
    if cmd == "backfill":
        backfill_macro(conn, days=config.INTERVAL_BACKFILL_DAYS.get("1d", 730))
    elif cmd == "etf":
        # Yahoo 1h candles for the tradable Bitcoin ETFs → candles table.
        backfill_etf_candles(conn, days=400)
    elif cmd == "exness":
        # Yahoo 1h candles for the Exness/MT5 tradeable universe.
        backfill_exness_candles(conn, days=120)
    elif cmd == "live":
        macro_loop(conn)
    else:
        print("Usage: python macro_data.py [backfill|etf|exness|live]")
