"""
cryptoautomation — Trading signal engine.

Now powered by the mathematical signal engine (math_engine.py):

  1. Bayesian fusion — posterior probability from indicator hit rates
  2. Regime detection — ADX-based trending/ranging → adjusts indicator trust
  3. Signal freshness — exponential decay on indicator age
  4. Confluence scoring — independence-weighted multi-indicator agreement
  5. Kelly Criterion — optimal position sizing based on edge
  6. News overlay — blended at 20% (unchanged)

Everything is deterministic and runs locally — no API keys needed.
"""

from __future__ import annotations

import time

import pandas as pd

import config
import database
import indicators
import math_engine
import reviewer

# ──────────────────────────────────────────────────────────────────────
# Minimum candles needed for reliable signals.
# ──────────────────────────────────────────────────────────────────────
MIN_CANDLES = 60

# Funding rates are paid every 8 hours and barely move between settlements,
# so a short TTL cache is safe and kills the per-symbol network call that
# used to make a full /api/signals refresh take minutes.
_FUNDING_CACHE: dict[str, tuple] = {}
_FUNDING_CACHE_TTL = 300.0  # 5 minutes


def _funding_rate_cached(conn, symbol: str) -> float | None:
    """
    Funding rate for a symbol, cheapest source first:
      1. in-process TTL cache (5 min)
      2. the `funding` DB table (rates settle every 8h — anything from the
         last ~30h is the current settled cycle)
      3. Binance network call (parallel-prefetched; failures cached too, so
         a dead net can't cost 10 s per symbol)
    Successful network fetches are upserted to the DB, so cold starts get
    progressively cheaper until they are pure DB reads.
    """
    now = time.time()
    cached = _FUNDING_CACHE.get(symbol)
    if cached and now - cached[1] < _FUNDING_CACHE_TTL:
        return cached[0]

    # DB-first: the settled rate barely moves between 8h cycles.
    try:
        df = database.load_funding(conn, symbol, limit=1)
        if not df.empty:
            ts = float(df["funding_time"].iloc[0])
            if now * 1000 - ts < 30 * 3600 * 1000:  # within 30h
                rate = float(df["rate"].iloc[0])
                _FUNDING_CACHE[symbol] = (rate, now)
                return rate
    except Exception:
        pass

    try:
        import factors
        rate = factors.fetch_funding_rate(symbol.replace("/", ""))
    except Exception:
        rate = None  # cache failures too: don't eat 10s per symbol on a dead net
    _FUNDING_CACHE[symbol] = (rate, now)
    if rate is not None:
        try:
            database.upsert_funding(conn, symbol,
                                    [{"funding_time": int(now * 1000),
                                      "rate": rate}])
        except Exception:
            pass
    return rate


# ──────────────────────────────────────────────────────────────────────
# Load indicator DataFrames for all timeframes
# ──────────────────────────────────────────────────────────────────────
def _load_tf_data(conn, symbol: str) -> dict[str, pd.DataFrame]:
    """Load candles + indicators for each timeframe. Returns {interval: df}."""
    out = {}
    for interval in config.INTERVALS:
        df = indicators.add_all(
            database.load_candles(conn, symbol, interval, limit=200)
        )
        if len(df) > 0:
            out[interval] = df
    return out


# ──────────────────────────────────────────────────────────────────────
# Full signal per coin (math engine powered)
# ──────────────────────────────────────────────────────────────────────
def generate_signal(
    conn,
    symbol: str,
    news_score: float = 0.0,
    all_coins_data: dict | None = None,
) -> dict:
    """
    Generate a trading signal for one symbol using the math engine.

    Args:
        conn:        SQLite connection
        symbol:      e.g. "BTC/USDT"
        news_score:  normalised -1..1 (from reviewer)
        all_coins_data: {symbol: df} loaded ONCE by generate_all_signals and
            shared across symbols (avoids the O(n²) DB reload per symbol).

    Returns a dict with direction, entry, SL, TP, confidence, reasons,
    plus the new math engine fields (regime, kelly, EV, posterior, etc).
    """
    # Load indicator DataFrames for each timeframe
    tf_data = _load_tf_data(conn, symbol)

    df_1h = tf_data.get("1h", pd.DataFrame())
    df_4h = tf_data.get("4h", None)
    df_1d = tf_data.get("1d", None)

    if df_1h.empty or len(df_1h) < MIN_CANDLES:
        return {
            "symbol": symbol,
            "direction": "HOLD",
            "confidence": 0,
            "entry": None, "stop_loss": None, "take_profit": None,
            "reasons": [],
            "error": "insufficient data",
        }

    # ── Compute factor scores ────────────────────────────────────
    factor_bundle = None
    try:
        import factors as _factors

        # Get funding rate for carry factor (TTL-cached — no network per call)
        funding_rate = _funding_rate_cached(conn, symbol)

        # Pass all coin DataFrames for cross-sectional momentum. When called
        # from generate_all_signals these are loaded ONCE and shared; the
        # lazy fallback keeps the single-symbol call working standalone.
        if all_coins_data is None:
            all_coins_data = {}
            for s in config.SYMBOLS:
                s_df = database.load_candles(conn, s, "1h", limit=200)
                if not s_df.empty:
                    all_coins_data[s] = indicators.add_all(s_df)

        factor_bundle = _factors.compute_all_factors(
            df_1h, all_coins_data, funding_rate
        )
    except Exception:
        pass  # factors are optional — fall back to indicators only

    # Run the full math pipeline (with fallback like backtester)
    try:
        result = math_engine.compute_trade_signal(
            df_1h=df_1h,
            df_4h=df_4h,
            df_1d=df_1d,
            news_score=news_score,
            factor_bundle=factor_bundle,
            all_coins=all_coins_data if factor_bundle else None,
        )
    except Exception as e:
        # Fallback: return HOLD with error info
        return {
            "symbol": symbol,
            "direction": "HOLD",
            "confidence": 0,
            "entry": None, "stop_loss": None, "take_profit": None,
            "reasons": [],
            "error": f"math_engine error: {str(e)}",
        }

    # Build response
    response = {
        "symbol": symbol,
        "direction": result.direction,
        "confidence": result.confidence,
        "entry": result.entry,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "risk_reward": result.risk_reward,
        "atr_1h": round(float(df_1h["atr"].iloc[-1]), 6) if "atr" in df_1h.columns else None,
        # ── Math engine fields ──
        "regime": result.regime.value,
        "adx": result.adx_value,
        "kelly_fraction": result.kelly_fraction,
        "ev_per_unit": result.ev_per_unit,
        "posterior_bull": result.posterior_bull,
        "posterior_bear": result.posterior_bear,
        "confluence": result.confluence_score,
        "reasons": result.reasons,
        "breakdown": result.signal_breakdown,
    }

    # ── Factor scores ──
    if factor_bundle is not None:
        from factors import factor_bundle_to_dict
        response["factors"] = factor_bundle_to_dict(factor_bundle)

    return response


# ──────────────────────────────────────────────────────────────────────
# All coins
# ──────────────────────────────────────────────────────────────────────
def generate_all_signals(conn, news_limit: int = 60) -> dict:
    """
    Trading signals for every watched coin, plus metadata.

    The news_score per coin is pulled from the reviewer so the sentiment
    overlay stays consistent with the AI market review panel.
    """
    review = reviewer.review_market(conn, limit=news_limit)
    coin_scores = {c["symbol"]: c["score"] for c in review.get("coins", [])}

    # Load the cross-sectional universe ONCE instead of once per symbol
    # (was 87 × 87 DB loads ≈ 4.6 min per refresh).
    all_coins_data = {}
    for s in config.SYMBOLS:
        s_df = database.load_candles(conn, s, "1h", limit=200)
        if not s_df.empty:
            all_coins_data[s] = indicators.add_all(s_df)

    # Prefetch funding for every symbol IN PARALLEL so the per-symbol loop
    # below is pure cache hits. Sequential network calls measured ~2.6 s
    # each → 87 × 2.6 s ≈ 3.8 min cold; 8 threads bring that to ~30 s, and
    # the DB-first path makes later cold starts instant.
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda s: _funding_rate_cached(conn, s), config.SYMBOLS))
    except Exception:
        pass  # prefetch is best-effort; the per-symbol call still falls back

    signals = []
    for symbol in config.SYMBOLS:
        raw = coin_scores.get(symbol, 0)
        ns = max(-1.0, min(1.0, raw / 100.0))
        signals.append(generate_signal(conn, symbol, news_score=ns,
                                       all_coins_data=all_coins_data))

    return {
        "signals": signals,
        "news_mode": review.get("mode", "rule"),
        "generated_at": review.get("generated_at"),
    }
