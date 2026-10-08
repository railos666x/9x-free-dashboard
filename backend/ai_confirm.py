"""
cryptoautomation — AI Confirmation Layer (multi-factor trade gate).

The technical confirmation layer that validates/rejects AI news signals.
NOT a trade generator — only referees. Each layer can be individually
disabled via config. The master gate `should_trade()` combines:

    1. Regime detection (ADX + EMA50/200 + ATR percentile)
    2. Technical confirmation (6-factor score: trend, momentum, RSI,
       S/R, multi-timeframe, volatility)
    3. Session awareness (London/NY overlap preferred, Asian reduced)
    4. Sentiment strength + confidence floor (raised thresholds)

Based on research:
    - Hybrid AI Trading (ComSIA 2026): multi-factor score >= 2 → 61.5% WR
    - BlindTrade (Korea Univ 2025): Risk-Regime agent strongest predictor
    - Multi-Timeframe Confluence: 5-TF alignment improves entry precision

The module NEVER raises — on any failure it returns a permissive result
(pass=True, score=0) so the bot keeps working. All functions are pure
(read from candles DB) and safe to call from any thread.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
import database
import indicators
import strategy as strat_mod

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Regime detection
# ----------------------------------------------------------------------
def detect_regime(conn, symbol: str) -> dict:
    """Classify the current market regime for a symbol.

    Returns:
        regime: "trending_up" | "trending_down" | "ranging" | "volatile"
        adx: current ADX value (trend strength, 0-100)
        trend_ema50: price above EMA50?
        trend_ema200: price above EMA200? (None when not enough data)
        vol_percentile: ATR percentile over last 200 bars (0-1)
        atr: current ATR value

    Regime rules (based on Hybrid AI paper + BlindTrade):
        - ATR > 90th percentile → "volatile" (extreme risk)
        - ADX > 25 AND price > EMA50 → "trending_up"
        - ADX > 25 AND price < EMA50 → "trending_down"
        - ADX <= 25 → "ranging" (no clear trend)
    """
    try:
        df = database.load_candles(conn, symbol, "1h", limit=250)
        if df is None or len(df) < 60:
            return {"regime": "ranging", "adx": 0.0, "trend_ema50": True,
                    "trend_ema200": None, "vol_percentile": 0.5, "atr": 0.0}

        df = indicators.add_all(df)
        adx = strat_mod.adx_series(df, period=14)
        adx_val = float(adx.iloc[-1]) if len(adx) > 0 else 0.0

        ema50 = indicators.ema(df["close"], 50)
        ema200 = indicators.ema(df["close"], 200)
        price = float(df["close"].iloc[-1])

        ema50_val = float(ema50.iloc[-1]) if len(ema50) > 0 else price
        ema200_val = float(ema200.iloc[-1]) if len(ema200) > 0 else None

        # ATR percentile over the full sample
        atr_series = df["atr"]
        atr_val = float(atr_series.iloc[-1]) if len(atr_series) > 0 else 0.0
        if len(atr_series) > 20:
            vol_pct = float((atr_series < atr_val).mean())
        else:
            vol_pct = 0.5

        # Regime classification
        if vol_pct > 0.90:
            regime = "volatile"
        elif adx_val > 25:
            regime = "trending_up" if price > ema50_val else "trending_down"
        else:
            regime = "ranging"

        return {
            "regime": regime,
            "adx": round(adx_val, 2),
            "trend_ema50": price > ema50_val,
            "trend_ema200": (price > ema200_val) if ema200_val is not None else None,
            "vol_percentile": round(vol_pct, 3),
            "atr": round(atr_val, 6),
        }
    except Exception as e:
        log.warning("detect_regime(%s) failed: %s", symbol, e)
        return {"regime": "ranging", "adx": 0.0, "trend_ema50": True,
                "trend_ema200": None, "vol_percentile": 0.5, "atr": 0.0}


# ----------------------------------------------------------------------
# Technical confirmation (6-factor score)
# ----------------------------------------------------------------------
def technical_confirmation(conn, symbol: str, direction: str) -> dict:
    """Check 6 technical factors that must confirm the AI signal.

    Each factor worth 1 point. Entry requires score >= AI_CONFIRM_MIN_SCORE.

    Factors:
        1. Trend alignment: LONG → price > EMA50; SHORT → price < EMA50
        2. Momentum: LONG → MACD > signal (or rising); SHORT mirror
        3. RSI position: LONG → RSI(14) < 45 (buying dips); SHORT → RSI > 55
        4. No S/R conflict: entry not within 0.5 ATR of opposing S/R
        5. Multi-timeframe: 4H and 1H trend agree (when AI_MTF_ENABLED)
        6. Volatility check: ATR not in extreme spike (>90th pct)

    Returns:
        confirm: bool (score >= min)
        score: int (0-6)
        details: dict with per-factor results
    """
    details = {}
    score = 0

    try:
        df = database.load_candles(conn, symbol, "1h", limit=250)
        if df is None or len(df) < 60:
            return {"confirm": True, "score": 0,
                    "details": {"reason": "insufficient_data"}}

        df = indicators.add_all(df)
        price = float(df["close"].iloc[-1])
        atr_val = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0

        # Factor 1: Trend alignment (price vs EMA50)
        ema50 = float(indicators.ema(df["close"], 50).iloc[-1])
        if direction == "LONG":
            trend_ok = price > ema50
        else:
            trend_ok = price < ema50
        details["trend_alignment"] = trend_ok
        if trend_ok:
            score += 1

        # Factor 2: Momentum (MACD vs signal line)
        macd_line = float(df["macd"].iloc[-1])
        signal_line = float(df["macd_signal"].iloc[-1])
        macd_hist = float(df["macd_hist"].iloc[-1])
        if direction == "LONG":
            momentum_ok = macd_line > signal_line or macd_hist > 0
        else:
            momentum_ok = macd_line < signal_line or macd_hist < 0
        details["momentum"] = momentum_ok
        if momentum_ok:
            score += 1

        # Factor 3: RSI position (not chasing)
        rsi_val = float(df["rsi"].iloc[-1])
        if direction == "LONG":
            rsi_ok = rsi_val < 55  # buying dips, not chasing overbought
        else:
            rsi_ok = rsi_val > 45  # selling rallies, not chasing oversold
        details["rsi_position"] = rsi_ok
        details["rsi_value"] = round(rsi_val, 1)
        if rsi_ok:
            score += 1

        # Factor 4: No S/R conflict (simple swing-based S/R)
        sr_conflict = _check_sr_conflict(df, price, atr_val, direction)
        details["no_sr_conflict"] = not sr_conflict
        if not sr_conflict:
            score += 1

        # Factor 5: Multi-timeframe alignment (4H trend agrees with 1H)
        if config.AI_MTF_ENABLED:
            mtf_ok = _check_mtf_alignment(conn, symbol, direction)
            details["multi_timeframe"] = mtf_ok
            if mtf_ok:
                score += 1
        else:
            details["multi_timeframe"] = None  # disabled
            score += 1  # give the point when disabled

        # Factor 6: Volatility not extreme
        atr_series = df["atr"]
        if len(atr_series) > 20:
            vol_pct = float((atr_series < atr_val).mean())
            vol_ok = vol_pct < 0.90  # not in extreme volatility
        else:
            vol_ok = True
        details["volatility_ok"] = vol_ok
        if vol_ok:
            score += 1

    except Exception as e:
        log.warning("technical_confirmation(%s, %s) failed: %s", symbol, direction, e)
        return {"confirm": True, "score": 0,
                "details": {"reason": f"error: {e}"}}

    min_score = config.AI_CONFIRM_MIN_SCORE
    return {"confirm": score >= min_score, "score": score, "details": details}


def _check_sr_conflict(df, price: float, atr_val: float, direction: str) -> bool:
    """Check if price is within 0.5 ATR of an opposing support/resistance level.

    Simple swing-based S/R: look at the last 50 bars for swing highs/lows.
    A LONG near resistance or a SHORT near support is a conflict."""
    if atr_val <= 0 or len(df) < 20:
        return False

    lookback = min(50, len(df) - 1)
    recent = df.iloc[-lookback:]
    threshold = 0.5 * atr_val

    if direction == "LONG":
        # Check if price is near a resistance (swing high)
        highs = recent["high"].nlargest(5)
        for h in highs:
            if 0 < (h - price) <= threshold:
                return True  # too close to resistance
    else:
        # Check if price is near a support (swing low)
        lows = recent["low"].nsmallest(5)
        for lo in lows:
            if 0 < (price - lo) <= threshold:
                return True  # too close to support

    return False


def _check_mtf_alignment(conn, symbol: str, direction: str) -> bool:
    """Check if 4H trend agrees with 1H trend direction.

    Load 4H candles, compute EMA20. If direction is LONG, 4H close must
    be above EMA20. If SHORT, below. This is a simplified multi-timeframe
    check — the full 5-TF alignment from research is overkill for 1h bars."""
    try:
        df4h = database.load_candles(conn, symbol, "4h", limit=60)
        if df4h is None or len(df4h) < 25:
            return True  # insufficient data → permissive
        ema20_4h = indicators.ema(df4h["close"], 20)
        price_4h = float(df4h["close"].iloc[-1])
        ema_val = float(ema20_4h.iloc[-1])
        if direction == "LONG":
            return price_4h > ema_val
        else:
            return price_4h < ema_val
    except Exception:
        return True  # permissive on error


# ----------------------------------------------------------------------
# Session awareness
# ----------------------------------------------------------------------
def current_session() -> dict:
    """Identify the current forex trading session (UTC-based).

    Sessions (approximate, in UTC):
        Asian:   23:00 - 07:00 UTC (Tokyo + Sydney)
        London:  07:00 - 16:00 UTC
        New York: 13:00 - 22:00 UTC
        Overlap (London+NY): 13:00 - 16:00 UTC (prime window)
        Dead:    22:00 - 23:00 UTC (late NY close to Asian open)

    Returns:
        session: "asian" | "london" | "ny" | "overlap" | "dead"
        utc_hour: current UTC hour (0-23)
    """
    now_utc = datetime.now(timezone.utc)
    h = now_utc.hour

    if 13 <= h < 16:
        session = "overlap"
    elif 7 <= h < 13:
        session = "london"
    elif 16 <= h < 22:
        session = "ny"
    elif 23 <= h or h < 7:
        session = "asian"
    else:
        session = "dead"

    return {"session": session, "utc_hour": h}


def session_multiplier() -> float:
    """Position size multiplier based on current session.

    overlap: 1.0 (prime window — full size)
    london:  1.0 (high liquidity)
    ny:      0.75 (decent but declining)
    asian:   SESSION_ASIAN_SCALE (default 0.5 — smaller ranges)
    dead:    0.0 (block trading)
    """
    if not config.SESSION_AWARE_ENABLED:
        return 1.0

    s = current_session()["session"]
    if s == "overlap":
        return config.SESSION_OVERLAP_BOOST
    elif s == "london":
        return 1.0
    elif s == "ny":
        return 0.75
    elif s == "asian":
        if config.SESSION_BLOCK_ASIAN:
            return 0.0
        return config.SESSION_ASIAN_SCALE
    else:  # dead
        return 0.0


# ----------------------------------------------------------------------
# Master gate
# ----------------------------------------------------------------------
def should_trade(conn, symbol: str, sentiment_score: float,
                 confidence: float, direction: str) -> dict:
    """The master confirmation gate. Combines all layers:

        1. Regime check (block volatile or counter-trend)
        2. Technical confirmation score (need >= min)
        3. Sentiment strength (raised thresholds)
        4. Confidence floor (raised)
        5. Session multiplier (position sizing)

    Returns:
        pass: bool — should the trade proceed?
        score: int — total confirmation score
        regime: str — current regime
        session: str — current session
        session_mult: float — position size multiplier
        reasons: list[str] — human-readable explanation of each layer
    """
    reasons = []

    # Gate 0: Is the confirmation layer enabled at all?
    if not config.AI_CONFIRM_ENABLED:
        return {"pass": True, "score": 0, "regime": "unknown",
                "session": "unknown", "session_mult": 1.0,
                "reasons": ["confirmation_disabled"]}

    # Gate 1: Regime check
    regime_info = detect_regime(conn, symbol)
    regime = regime_info["regime"]

    if config.AI_REGIME_BLOCK_VOLATILE and regime == "volatile":
        return {"pass": False, "score": 0, "regime": regime,
                "session": current_session()["session"],
                "session_mult": session_multiplier(),
                "reasons": [f"BLOCKED: volatile regime (ATR > 90th pct)"]}

    # Counter-trend handling. Post-mortem of the real book (2026-09-28):
    # with-trend entries won 71.4% (+639.59) while counter-trend entries won
    # 16.7% (-378.90) — the old code only WARNED here and traded anyway.
    # AI_BLOCK_COUNTER_TREND turns that warning into a hard veto once the
    # trend is real (ADX >= AI_COUNTER_TREND_MIN_ADX).
    counter_trend = (regime == "trending_up" and direction == "SHORT") or (
        regime == "trending_down" and direction == "LONG")
    if counter_trend:
        if config.AI_BLOCK_COUNTER_TREND and regime_info["adx"] >= config.AI_COUNTER_TREND_MIN_ADX:
            reasons.append(
                f"BLOCKED: counter-trend {direction} in {regime} "
                f"(ADX={regime_info['adx']} >= {config.AI_COUNTER_TREND_MIN_ADX}) — "
                "these entries ran a 16.7% win rate in the live book")
            return {"pass": False, "score": 0, "regime": regime,
                    "session": current_session()["session"],
                    "session_mult": session_multiplier(),
                    "reasons": reasons}
        if regime_info["adx"] > 30:  # veto off or weak trend: still flag it
            reasons.append(f"WARNING: trading against {regime} (ADX={regime_info['adx']})")

    reasons.append(f"regime={regime} ADX={regime_info['adx']}")

    # Gate 2: Technical confirmation
    tc = technical_confirmation(conn, symbol, direction)
    tc_score = tc["score"]
    reasons.append(f"technical_score={tc_score}/6")

    # Gate 3: Sentiment strength (raised thresholds)
    sent_long = config.AI_SENTIMENT_LONG
    sent_short = config.AI_SENTIMENT_SHORT
    if direction == "LONG" and sentiment_score < sent_long:
        reasons.append(f"BLOCKED: sentiment {sentiment_score:.2f} < {sent_long}")
        return {"pass": False, "score": tc_score, "regime": regime,
                "session": current_session()["session"],
                "session_mult": session_multiplier(),
                "reasons": reasons}
    elif direction == "SHORT" and sentiment_score > sent_short:
        reasons.append(f"BLOCKED: sentiment {sentiment_score:.2f} > {sent_short}")
        return {"pass": False, "score": tc_score, "regime": regime,
                "session": current_session()["session"],
                "session_mult": session_multiplier(),
                "reasons": reasons}

    # Gate 4: Confidence floor (raised)
    conf_floor = config.AI_CONFIDENCE_FLOOR
    if confidence < conf_floor:
        reasons.append(f"BLOCKED: confidence {confidence:.2f} < {conf_floor}")
        return {"pass": False, "score": tc_score, "regime": regime,
                "session": current_session()["session"],
                "session_mult": session_multiplier(),
                "reasons": reasons}

    # Gate 5: Session check
    sess = current_session()
    s_mult = session_multiplier()
    if s_mult <= 0:
        reasons.append(f"BLOCKED: session={sess['session']} (dead hours)")
        return {"pass": False, "score": tc_score, "regime": regime,
                "session": sess["session"], "session_mult": 0.0,
                "reasons": reasons}

    reasons.append(f"session={sess['session']} mult={s_mult}")

    # Final: technical score must meet minimum
    if tc_score < config.AI_CONFIRM_MIN_SCORE:
        reasons.append(f"BLOCKED: technical score {tc_score} < {config.AI_CONFIRM_MIN_SCORE}")
        return {"pass": False, "score": tc_score, "regime": regime,
                "session": sess["session"], "session_mult": s_mult,
                "reasons": reasons}

    reasons.append("PASS: all gates cleared")
    return {"pass": True, "score": tc_score, "regime": regime,
            "session": sess["session"], "session_mult": s_mult,
            "reasons": reasons}
