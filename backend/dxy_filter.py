"""
cryptoautomation — DXY Master Filter (dollar index confirmation).

The DXY is the "invisible hand" of forex. With 57.6% Euro weighting, it's
essentially a mirror of EUR/USD. Gold is priced in dollars. The rule:
**never trade a USD pair without checking the DXY's location on the map.**

Based on FXNX institutional research and the Dollar Smile Theory:
    - Phase 1 (Risk-Off): DXY surges as safe haven — ignore sell signals
    - Phase 2 (Economic Trough): DXY weakens — gold/long pairs favored
    - Phase 3 (US Outperformance): DXY rises — USD longs favored

SMT Divergence: when DXY makes a new HH but the pair fails to make a new LL,
it signals a reversal (massive short-covering rally incoming).

The filter only applies to USD-denominated pairs. Cross pairs (EUR/GBP,
AUD/JPY) are NOT filtered by DXY. For indices/crypto CFDs the filter is
informational only (no veto).

The module NEVER raises — on any failure it returns permissive results.
"""

from __future__ import annotations

import logging

import config
import database
import indicators

log = logging.getLogger(__name__)

# Symbols where DXY acts as a master filter (USD is one leg)
_USD_PAIRS = {
    "EUR/USD", "GBP/USD", "AUD/USD", "USD/JPY", "USD/CHF",
    "USD/CAD", "USD/CNY", "USD/SGD", "USD/MXN", "USD/ZAR", "USD/TRY",
    "USD/SEK", "USD/NOK", "USD/PLN",
}
# Gold is priced in dollars — strong inverse correlation
_GOLD_PAIRS = {"XAU/USD", "XAG/USD", "XPT/USD", "XPD/USD"}
# These are NOT filtered by DXY (neither leg is USD)
_CROSS_PAIRS = {"DAX", "NIKKEI"}  # indices have their own dynamics


def get_dxy_state(conn) -> dict:
    """Analyze the current DXY state from candles.

    Returns:
        trend: "bullish" | "bearish" | "neutral" (DXY direction)
        dxy_price: latest DXY close
        ema50: DXY above/below EMA50
        momentum: "rising" | "falling" | "flat" (MACD direction)
        at_resistance: bool — DXY near a recent high (reversal warning)
        at_support: bool — DXY near a recent low (reversal warning)
    """
    try:
        dxy_sym = config.DXY_SYMBOL
        df = database.load_candles(conn, dxy_sym, "1h", limit=200)
        if df is None or len(df) < 30:
            return {"trend": "neutral", "dxy_price": None, "ema50": None,
                    "momentum": "flat", "at_resistance": False,
                    "at_support": False}

        df = indicators.add_all(df)
        price = float(df["close"].iloc[-1])
        ema50 = float(indicators.ema(df["close"], 50).iloc[-1])

        # MACD for momentum
        macd_line = float(df["macd"].iloc[-1])
        signal_line = float(df["macd_signal"].iloc[-1])
        macd_hist = float(df["macd_hist"].iloc[-1])

        if macd_hist > 0.001:
            momentum = "rising"
        elif macd_hist < -0.001:
            momentum = "falling"
        else:
            momentum = "flat"

        # Trend
        if price > ema50 and momentum != "falling":
            trend = "bullish"
        elif price < ema50 and momentum != "rising":
            trend = "bearish"
        else:
            trend = "neutral"

        # S/R proximity (last 50 bars)
        lookback = min(50, len(df))
        recent = df.iloc[-lookback:]
        atr_val = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.5
        threshold = 0.3 * atr_val

        recent_high = float(recent["high"].max())
        recent_low = float(recent["low"].min())
        at_resistance = (recent_high - price) <= threshold
        at_support = (price - recent_low) <= threshold

        return {
            "trend": trend,
            "dxy_price": round(price, 3),
            "ema50": round(ema50, 3),
            "momentum": momentum,
            "at_resistance": at_resistance,
            "at_support": at_support,
        }
    except Exception as e:
        log.warning("get_dxy_state failed: %s", e)
        return {"trend": "neutral", "dxy_price": None, "ema50": None,
                "momentum": "flat", "at_resistance": False, "at_support": False}


def dxy_correlation_score(symbol: str, direction: str,
                          dxy_state: dict = None) -> int:
    """Score how well DXY confirms the proposed trade direction.

    Returns:
        2 = DXY strongly confirms (trending in right direction)
        1 = DXY neutral / ranging (no strong signal either way)
        0 = DXY contradicts (trending against trade) → BLOCK

    Logic:
        - USD pairs (EUR/USD, GBP/USD): LONG requires DXY bearish
        - USD pairs (USD/JPY, USD/CHF): LONG requires DXY bullish
        - Gold (XAU/USD): LONG requires DXY bearish (inverse)
        - Indices/crypto: always 1 (informational only)
    """
    if not config.DXY_FILTER_ENABLED:
        return 1  # disabled → neutral

    if symbol in _CROSS_PAIRS:
        return 1  # DXY doesn't apply to non-USD instruments

    if dxy_state is None:
        return 1  # no data → permissive

    trend = dxy_state.get("trend", "neutral")

    if trend == "neutral":
        return 1  # DXY ranging → neutral score

    # Determine if DXY should be bullish or bearish for this trade
    if symbol in _GOLD_PAIRS:
        # Gold inversely correlated with DXY
        expected_dxy = "bearish" if direction == "LONG" else "bullish"
    elif symbol in _USD_PAIRS:
        # USD is the quote currency for EUR/USD, GBP/USD, AUD/USD
        # USD is the base currency for USD/JPY, USD/CHF, USD/CAD, USD/CNY
        usd_is_base = symbol.startswith("USD/")
        if direction == "LONG":
            expected_dxy = "bullish" if usd_is_base else "bearish"
        else:
            expected_dxy = "bearish" if usd_is_base else "bullish"
    else:
        # Indices (SPX/USD, NDX/USD, DOW/USD) — DXY has some influence
        # but it's informational. Return neutral.
        return 1

    if trend == expected_dxy:
        return 2  # strong confirmation
    else:
        return 0  # contradiction


def detect_smt_divergence(conn, symbol: str) -> dict:
    """Smart Money Technique divergence detection.

    When DXY makes a new Higher High but the pair fails to make a new
    Lower Low (or vice versa), it signals a reversal.

    Returns:
        divergence: bool
        type: "bullish" | "bearish" | None
        strength: 0.0 - 1.0
    """
    try:
        dxy_sym = config.DXY_SYMBOL
        df_dxy = database.load_candles(conn, dxy_sym, "1h", limit=100)
        df_pair = database.load_candles(conn, symbol, "1h", limit=100)

        if (df_dxy is None or df_pair is None or
                len(df_dxy) < 30 or len(df_pair) < 30):
            return {"divergence": False, "type": None, "strength": 0.0}

        # Find recent swing highs/lows (last 20 bars)
        lookback = min(20, len(df_dxy) - 1, len(df_pair) - 1)
        dxy_recent = df_dxy.iloc[-lookback:]
        pair_recent = df_pair.iloc[-lookback:]

        dxy_curr_high = float(dxy_recent["high"].iloc[-1])
        dxy_prev_high = float(dxy_recent["high"].iloc[:-5].max())
        pair_curr_low = float(pair_recent["low"].iloc[-1])
        pair_prev_low = float(pair_recent["low"].iloc[:-5].min())

        # Bullish SMT: DXY makes HH but pair fails to make LL
        dxy_hh = dxy_curr_high > dxy_prev_high
        pair_hl = pair_curr_low > pair_prev_low  # higher low = not making new low

        if dxy_hh and pair_hl:
            strength = min(1.0, abs(dxy_curr_high - dxy_prev_high) /
                           (float(dxy_recent["high"].std()) + 1e-10))
            return {"divergence": True, "type": "bullish",
                    "strength": round(min(strength, 1.0), 2)}

        # Bearish SMT: DXY makes LL but pair fails to make HH
        dxy_ll = dxy_curr_high < dxy_prev_high
        pair_lh = pair_curr_low < pair_prev_low

        if dxy_ll and pair_lh:
            strength = min(1.0, abs(dxy_curr_high - dxy_prev_high) /
                           (float(dxy_recent["high"].std()) + 1e-10))
            return {"divergence": True, "type": "bearish",
                    "strength": round(min(strength, 1.0), 2)}

        return {"divergence": False, "type": None, "strength": 0.0}

    except Exception as e:
        log.warning("detect_smt_divergence(%s) failed: %s", symbol, e)
        return {"divergence": False, "type": None, "strength": 0.0}


def dxy_allows_trade(symbol: str, direction: str,
                     dxy_state: dict = None) -> dict:
    """Master DXY filter function.

    Returns:
        allowed: bool
        score: int (0, 1, 2)
        dxy_trend: str
        reasons: list[str]
    """
    if not config.DXY_FILTER_ENABLED:
        return {"allowed": True, "score": 1, "dxy_trend": "disabled",
                "reasons": ["dxy_filter_disabled"]}

    if dxy_state is None:
        dxy_state = get_dxy_state(None)  # will need conn — handled by caller

    score = dxy_correlation_score(symbol, direction, dxy_state)
    trend = dxy_state.get("trend", "neutral")
    reasons = [f"dxy_trend={trend} dxy_score={score}"]

    if score == 0 and config.DXY_BLOCK_ON_CONTRADICTION:
        # Only block USD pairs and gold — indices/crypto are informational
        if symbol in _USD_PAIRS or symbol in _GOLD_PAIRS:
            reasons.append(f"BLOCKED: DXY contradicts {direction} on {symbol}")
            return {"allowed": False, "score": 0, "dxy_trend": trend,
                    "reasons": reasons}

    # SMT divergence bonus
    # (caller should pass conn for this — handled in should_trade integration)
    if score == 2:
        reasons.append("DXY strongly confirms")

    return {"allowed": True, "score": score, "dxy_trend": trend,
            "reasons": reasons}
