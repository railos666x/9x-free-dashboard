"""
cryptoautomation — Data verification gate.

The bot must not act on garbage: before a signal is trusted (and again
right before an order is placed) the incoming data is checked for sanity.

Checks (all pure functions, unit-testable):

  1. staleness_ok()      — is the newest candle close enough to now?
  2. candle_sanity()     — OHLC consistency, no NaN/zero/negative prices
  3. spike_check()       — price move >> ATR without volume confirmation
  4. cross_source_ok()   — two independent feeds roughly agree on a price
  5. verify_candle_df()  — combined gate over a candles DataFrame
  6. verify_intent()     — gate an approved intent (data + entry sanity)

The philosophy: verification is a hard gate (reject → HOLD), never a
softener. A rejected signal is dropped and logged, not traded with a
warning.
"""

from __future__ import annotations

import math
import time

import pandas as pd

import database

# Newest candle must be within this multiple of the interval, or the feed
# is stale (collector down / exchange hiccup). 2.5× leaves room for the
# collector's own polling cadence.
MAX_STALE_MULT = 2.5

# Symbols WITHOUT a quote part ("IBIT", not "BTC/USDT") are US ETFs/stocks:
# they trade exchange hours only, so on a Monday pre-market the newest 1h
# bar is Friday's close (~64h old). The crypto window would hold every ETF
# signal until the market prints fresh bars. 3.5 days covers a weekend +
# holiday while still rejecting a genuinely dead feed.
ETF_STALE_MULT = 84.0

# A close-to-close move above this many ATRs is treated as a possible
# bad tick UNLESS volume confirms it (volume > this multiple of its
# 20-bar median).
SPIKE_ATR_MULT = 8.0
SPIKE_VOLUME_MULT = 3.0

# Two independent price feeds must agree within this % (cross-source).
CROSS_SOURCE_TOLERANCE_PCT = 1.0


def interval_ms(interval: str) -> int:
    """Milliseconds in a Binance-style interval ('1h' -> 3_600_000)."""
    unit = interval[-1]
    n = int(interval[:-1])
    table = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    return n * table[unit]


def staleness_ok(last_open_time_ms: float, interval: str,
                 now_ms: float | None = None,
                 max_stale_mult: float = MAX_STALE_MULT) -> bool:
    """True if the newest candle opened recently enough to be trustworthy."""
    if last_open_time_ms is None or last_open_time_ms <= 0:
        return False
    now_ms = now_ms or time.time() * 1000
    return now_ms - float(last_open_time_ms) <= max_stale_mult * interval_ms(interval)


def candle_sanity(df: pd.DataFrame) -> dict:
    """
    Structural sanity of a candles frame. Returns {ok, problems} where
    problems is a list of human-readable issues. Checks:
      - at least 2 rows, required columns present
      - close/high/low/open are finite, positive
      - high >= max(open, close) and low <= min(open, close)
      - volume >= 0
    """
    problems = []
    if df is None or df.empty or len(df) < 2:
        return {"ok": False, "problems": ["insufficient candles"]}
    required = ["open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        return {"ok": False, "problems": [f"missing columns: {missing}"]}

    for col in ("open", "high", "low", "close"):
        vals = df[col]
        if not math.isfinite(float(vals.iloc[-1])):
            problems.append(f"{col} not finite on newest bar")
            continue
        if float(vals.iloc[-1]) <= 0:
            problems.append(f"{col} <= 0 on newest bar")

    last = df.iloc[-1]
    if float(last["high"]) + 1e-12 < max(float(last["open"]), float(last["close"])):
        problems.append("high < max(open, close)")
    if float(last["low"]) - 1e-12 > min(float(last["open"]), float(last["close"])):
        problems.append("low > min(open, close)")
    if "volume" in df.columns and float(last.get("volume") or 0) < 0:
        problems.append("negative volume")
    return {"ok": not problems, "problems": problems}


def spike_check(df: pd.DataFrame,
                atr_mult: float = SPIKE_ATR_MULT,
                volume_mult: float = SPIKE_VOLUME_MULT) -> list[dict]:
    """
    Find bars where the close-to-close move is implausibly large relative
    to recent volatility (ATR) AND not backed by volume. Each entry:
    {index, move_pct, atr_mult, volume_mult}. A fresh spike on the newest
    bar is the classic 'bad tick' — reject the signal.
    """
    if len(df) < 30 or "atr" not in df.columns:
        return []
    atr = df["atr"]
    med_vol = df["volume"].rolling(20, min_periods=5).median()
    spikes = []
    prev_close = df["close"].shift(1)
    for i in range(1, len(df)):
        a = atr.iloc[i]
        if not math.isfinite(a) or a <= 0:
            continue
        move = abs(df["close"].iloc[i] - prev_close.iloc[i])
        move_atr = move / a
        if move_atr <= atr_mult:
            continue
        vol_mult = (df["volume"].iloc[i] / med_vol.iloc[i]) if med_vol.iloc[i] else 0
        if vol_mult >= volume_mult:
            continue  # big move WITH volume = real news, not a bad tick
        spikes.append({
            "index": int(i),
            "move_pct": round(move / prev_close.iloc[i] * 100, 2) if prev_close.iloc[i] else 0,
            "atr_mult": round(move_atr, 1),
            "volume_mult": round(float(vol_mult), 1),
        })
    return spikes


def cross_source_ok(price_a: float, price_b: float,
                    tolerance_pct: float = CROSS_SOURCE_TOLERANCE_PCT) -> bool:
    """Two independent feeds (e.g. Binance + Alpaca) within tolerance %."""
    if not price_a or not price_b or price_a <= 0 or price_b <= 0:
        return False
    diff = abs(price_a - price_b) / min(price_a, price_b) * 100
    return diff <= tolerance_pct


def verify_candle_df(df: pd.DataFrame, interval: str, symbol: str = "") -> dict:
    """Full data-quality gate over a candles DataFrame. Returns {ok, checks}.
    `symbol` tunes the staleness window: non-pair symbols (US ETFs/stocks)
    trade market hours, so they get a weekend-tolerant window."""
    checks = {}
    sanity = candle_sanity(df)
    checks["sanity"] = sanity
    if not sanity["ok"]:
        return {"ok": False, "checks": checks}

    last_open = float(df["open_time"].iloc[-1]) if "open_time" in df.columns else None
    max_stale = ETF_STALE_MULT if (symbol and "/" not in symbol) else MAX_STALE_MULT
    stale = staleness_ok(last_open, interval, max_stale_mult=max_stale) if last_open else False
    checks["fresh"] = {"ok": stale, "last_open_ms": last_open}
    if not stale:
        return {"ok": False, "checks": checks}

    spikes = spike_check(df)
    checks["spikes"] = {"count": len(spikes), "bars": spikes[-3:]}
    if spikes and spikes[-1]["index"] == len(df) - 1:
        return {"ok": False, "checks": checks}  # bad tick on the newest bar

    checks["bars"] = len(df)
    return {"ok": True, "checks": checks}


def verify_intent(intent, conn, interval: str = "1h") -> dict:
    """
    Gate an approved intent right before execution:
      - candles for the symbol pass the data-quality checks
      - the intent's entry price is within tolerance of the last close
        (signals computed on stale prices must not fire at a stale level)
    Returns {ok, checks}.
    """
    df = database.load_candles(conn, intent.symbol, interval, limit=100)
    data = verify_candle_df(df, interval, symbol=intent.symbol)
    checks = {"data": data}
    if not data["ok"]:
        return {"ok": False, "checks": checks}

    last_close = float(df["close"].iloc[-1])
    entry = intent.entry
    if not entry:
        # No entry level from either source — the executor will use the
        # live price, so nothing to sanity-check here.
        checks["entry"] = {"ok": True, "note": "no entry level, will use live price"}
        return {"ok": True, "checks": checks}

    # Entry must sit near the current price, else the level is stale.
    drift = abs(entry - last_close) / last_close * 100
    checks["entry"] = {"ok": drift <= CROSS_SOURCE_TOLERANCE_PCT * 5,
                       "entry": entry, "last_close": last_close,
                       "drift_pct": round(drift, 2)}
    if drift > CROSS_SOURCE_TOLERANCE_PCT * 5:
        return {"ok": False, "checks": checks}
    return {"ok": True, "checks": checks}
