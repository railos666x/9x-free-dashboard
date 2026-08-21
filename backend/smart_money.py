"""
cryptoautomation — Smart Money Concepts (institutional order flow).

ICT-based detection of where institutions are placing orders:
    - Order Blocks (OB): last opposing candle before an impulsive move
    - Fair Value Gaps (FVG): 3-candle imbalance zones price tends to fill
    - Liquidity Sweeps: wicks beyond equal highs/lows (stop hunts)
    - Break of Structure (BOS) / Change of Character (CHoCH): trend shifts

Uses the `smartmoneyconcepts` Python library for the heavy lifting.
The master function `smart_money_confluence()` returns a 0-4 score
indicating how well the current setup aligns with institutional flow.

Based on:
    - ICT (Inner Circle Trader) concepts
    - FXNX institutional framework
    - JP Morgan Digital Trading Desk research

The module NEVER raises — on any failure it returns a neutral score
(score=0) so the bot keeps working. All functions are pure (read from
candles DB) and safe to call from any thread.
"""

from __future__ import annotations

import contextlib
import io
import logging

import config
import database
import indicators

log = logging.getLogger(__name__)


def _import_smc():
    """Import the `smartmoneyconcepts` package, suppressing its banner.

    The package prints a star-emoji banner on import (__init__.py), which
    raises UnicodeEncodeError on Windows consoles that default to cp1252.
    Without this, every SMC function silently degrades to a neutral score
    (the except-Exception fallback) and the SMC_MIN_SCORE gate then blocks
    all trades on machines where the banner can't be encoded. Redirecting
    stdout/stderr during the import makes it deterministic everywhere.
    """
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        from smartmoneyconcepts import smc
    return smc


def _load_smc_data(conn, symbol: str):
    """Load candle data formatted for smartmoneyconcepts library.

    The library expects a DataFrame with columns: open, high, low, close,
    volume (lowercase). Returns None on failure.

    500 bars (NOT 200): on 1h candles a 200-bar window leaves < 15 swing
    points, so order blocks / BOS / CHoCH almost never form and the SMC
    gate (SMC_MIN_SCORE=2) blocked every trade. 500 1h bars (~3 weeks)
    gives the detection a real population.
    """
    try:
        df = database.load_candles(conn, symbol, "1h", limit=500)
        if df.empty or len(df) < 20:
            return None
        # smartmoneyconcepts expects lowercase column names
        df = df.rename(columns={
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        })
        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        return df
    except Exception as e:
        log.warning("SMC data load failed for %s: %s", symbol, e)
        return None


def _compute_swing_highs_lows(df, swing_length: int = 10):
    """Compute swing highs/lows — prerequisite for OB, BOS/CHoCH, liquidity.

    Uses a shorter swing_length (10) since we only have ~200 bars and
    the library default of 50 would leave very few swing points.
    Returns the swing highs/lows DataFrame, or None on failure.
    """
    try:
        smc = _import_smc()
        shl = smc.swing_highs_lows(df, swing_length=swing_length)
        return shl
    except Exception as e:
        log.warning("swing_highs_lows computation failed: %s", e)
        return None


# ----------------------------------------------------------------------
# Order Blocks
# ----------------------------------------------------------------------
def detect_order_blocks(conn, symbol: str) -> dict:
    """Detect Order Blocks — last opposing candle before an impulsive move.

    Bullish OB: last bearish candle before a strong bullish impulse.
    Bearish OB: last bullish candle before a strong bearish impulse.

    Returns:
        bullish_obs: list of recent bullish order block levels
        bearish_obs: list of recent bearish order block levels
        score: 0-2 (how many recent OBs support the current direction)
    """
    result = {"bullish_obs": [], "bearish_obs": [], "score": 0}
    if not config.SMC_ENABLED:
        return result

    try:
        smc = _import_smc()
        df = _load_smc_data(conn, symbol)
        if df is None:
            return result

        shl = _compute_swing_highs_lows(df)
        if shl is None:
            return result

        # smc.ob() returns: OB (1=bull, -1=bear), Top, Bottom, OBVolume, Percentage
        obs = smc.ob(df, shl)
        if obs is None or obs.empty:
            return result

        # Recent order blocks: last 60 bars (~2.5 days of 1h bars). Order
        # blocks form ~1 per 50-100 bars, so a 30-bar scan found them almost
        # never. Also skip MITIGATED blocks (MitigatedIndex > 0): price has
        # already traded through them, so they are no longer active levels.
        recent = obs.tail(60)
        for idx, row in recent.iterrows():
            if float(row.get("MitigatedIndex") or 0) > 0:
                continue
            ob_val = row.get("OB", 0)
            if ob_val == 1:  # Bullish order block
                result["bullish_obs"].append(float(row.get("Bottom", 0)))
            elif ob_val == -1:  # Bearish order block
                result["bearish_obs"].append(float(row.get("Top", 0)))

        # Score: recent OBs suggest institutional interest
        result["score"] = min(2, max(len(result["bullish_obs"]),
                                      len(result["bearish_obs"])) // 2)

    except ImportError:
        log.warning("smartmoneyconcepts not installed — pip install smartmoneyconcepts")
    except Exception as e:
        log.warning("Order block detection failed for %s: %s", symbol, e)

    return result


# ----------------------------------------------------------------------
# Fair Value Gaps
# ----------------------------------------------------------------------
def detect_fair_value_gaps(conn, symbol: str) -> dict:
    """Detect Fair Value Gaps — 3-candle imbalance zones.

    Bullish FVG: gap between candle 1's high and candle 3's low (up).
    Bearish FVG: gap between candle 3's high and candle 1's low (down).

    Price tends to return to fill FVGs — they act as magnets and support.

    Returns:
        bullish_fvgs: list of bullish FVG levels
        bearish_fvgs: list of bearish FVG levels
        score: 0-2 (FVG alignment with direction)
    """
    result = {"bullish_fvgs": [], "bearish_fvgs": [], "score": 0}
    if not config.SMC_ENABLED:
        return result

    try:
        smc = _import_smc()
        df = _load_smc_data(conn, symbol)
        if df is None:
            return result

        # smc.fvg() returns: FVG (1=bull, -1=bear), Top, Bottom, MitigatedIndex
        fvgs = smc.fvg(df)
        if fvgs is None or fvgs.empty:
            return result

        recent = fvgs.tail(30)
        for idx, row in recent.iterrows():
            fvg_val = row.get("FVG", 0)
            if fvg_val == 1:  # Bullish FVG
                top = float(row.get("Top", 0))
                if top > 0:
                    result["bullish_fvgs"].append(top)
            elif fvg_val == -1:  # Bearish FVG
                bottom = float(row.get("Bottom", 0))
                if bottom > 0:
                    result["bearish_fvgs"].append(bottom)

        # Score based on recent FVG density
        total = len(result["bullish_fvgs"]) + len(result["bearish_fvgs"])
        result["score"] = min(2, total // 3)

    except ImportError:
        log.warning("smartmoneyconcepts not installed")
    except Exception as e:
        log.warning("FVG detection failed for %s: %s", symbol, e)

    return result


# ----------------------------------------------------------------------
# Break of Structure / Change of Character
# ----------------------------------------------------------------------
def detect_structure_changes(conn, symbol: str) -> dict:
    """Detect Break of Structure (BOS) and Change of Character (CHoCH).

    BOS: continuation signal — price breaks a previous swing in trend dir.
    CHoCH: reversal signal — price breaks a swing against the trend.

    These are the primary trend-structure signals in ICT methodology.

    Returns:
        bos: "bullish" | "bearish" | None (recent BOS direction)
        choch: "bullish" | "bearish" | None (recent CHoCH direction)
        score: 0-2 (structure alignment)
    """
    result = {"bos": None, "choch": None, "score": 0}
    if not config.SMC_ENABLED:
        return result

    try:
        smc = _import_smc()
        df = _load_smc_data(conn, symbol)
        if df is None:
            return result

        shl = _compute_swing_highs_lows(df)
        if shl is None:
            return result

        # smc.bos_choch() returns: BOS (1=bull,-1=bear), CHOCH (1=bull,-1=bear),
        # Level, BrokenIndex
        bc = smc.bos_choch(df, shl)
        if bc is None or bc.empty:
            return result

        # Scan recent bars for the latest BOS and CHoCH signals (last 60
        # bars ~ 2.5 days; 20 bars found them almost never on 1h candles).
        recent = bc.tail(60)
        for idx, row in recent.iterrows():
            bos_val = row.get("BOS", 0)
            if bos_val == 1:
                result["bos"] = "bullish"
            elif bos_val == -1:
                result["bos"] = "bearish"

            choch_val = row.get("CHOCH", 0)
            if choch_val == 1:
                result["choch"] = "bullish"
            elif choch_val == -1:
                result["choch"] = "bearish"

        # Score: BOS + CHoCH agreement = strong structure signal
        score = 0
        if result["bos"]:
            score += 1
        if result["choch"]:
            score += 1
        result["score"] = min(2, score)

    except ImportError:
        log.warning("smartmoneyconcepts not installed")
    except Exception as e:
        log.warning("Structure detection failed for %s: %s", symbol, e)

    return result


# ----------------------------------------------------------------------
# Liquidity Detection
# ----------------------------------------------------------------------
def detect_liquidity(conn, symbol: str) -> dict:
    """Detect liquidity pools — equal highs/lows where stops accumulate.

    Institutional algorithms target these levels for stop hunts before
    reversing. A liquidity sweep (wick beyond the level, close back
    inside) is a strong reversal signal.

    Returns:
        bullish_liquidity: price levels where buy-side liquidity sits
        bearish_liquidity: price levels where sell-side liquidity sits
        swept: bool — was liquidity recently swept? (reversal signal)
    """
    result = {"bullish_liquidity": [], "bearish_liquidity": [], "swept": False}
    if not config.SMC_ENABLED:
        return result

    try:
        smc = _import_smc()
        df = _load_smc_data(conn, symbol)
        if df is None:
            return result

        shl = _compute_swing_highs_lows(df)
        if shl is None:
            return result

        # smc.liquidity() returns: Liquidity (1=bull,-1=bear), Level, End, Swept
        liq = smc.liquidity(df, shl)
        if liq is not None and not liq.empty:
            recent = liq.tail(60)
            for idx, row in recent.iterrows():
                liq_val = row.get("Liquidity", 0)
                level = float(row.get("Level", 0))
                if liq_val == 1 and level > 0:  # Bullish liquidity (buy-side)
                    result["bullish_liquidity"].append(level)
                elif liq_val == -1 and level > 0:  # Bearish liquidity (sell-side)
                    result["bearish_liquidity"].append(level)

                # Check if this liquidity was swept (reversal signal)
                swept_val = row.get("Swept", 0)
                if swept_val and float(swept_val) > 0:
                    result["swept"] = True

    except ImportError:
        log.warning("smartmoneyconcepts not installed")
    except Exception as e:
        log.warning("Liquidity detection failed for %s: %s", symbol, e)

    return result


# ----------------------------------------------------------------------
# Master SMC Confluence
# ----------------------------------------------------------------------
def smart_money_confluence(conn, symbol: str, direction: str) -> dict:
    """Master SMC function — combines all institutional flow signals.

    Scores 0-4 based on how many SMC concepts align with the proposed
    trade direction:
        +1 if order blocks support the direction
        +1 if fair value gaps support the direction
        +1 if structure (BOS/CHoCH) supports the direction
        +1 if liquidity setup favors the direction

    The score is used as a gate (SMC_MIN_SCORE) and as metadata for
    the feedback loop (which SMC factors contribute to wins).

    Returns:
        score: int (0-4)
        details: dict with per-factor breakdown
    """
    result = {"score": 0, "details": {}}
    if not config.SMC_ENABLED:
        return result

    try:
        # Gather all SMC signals
        obs = detect_order_blocks(conn, symbol)
        fvgs = detect_fair_value_gaps(conn, symbol)
        structure = detect_structure_changes(conn, symbol)
        liquidity = detect_liquidity(conn, symbol)

        long = direction.upper() == "LONG"
        score = 0
        details = {}

        # 1. Order Block alignment
        if long and obs["bullish_obs"]:
            score += 1
            details["ob"] = "bullish_alignment"
        elif not long and obs["bearish_obs"]:
            score += 1
            details["ob"] = "bearish_alignment"
        else:
            details["ob"] = "neutral"

        # 2. Fair Value Gap alignment
        if long and fvgs["bullish_fvgs"]:
            score += 1
            details["fvg"] = f"bullish ({len(fvgs['bullish_fvgs'])} gaps)"
        elif not long and fvgs["bearish_fvgs"]:
            score += 1
            details["fvg"] = f"bearish ({len(fvgs['bearish_fvgs'])} gaps)"
        else:
            details["fvg"] = "neutral"

        # 3. Structure alignment
        if long and (structure["bos"] == "bullish" or structure["choch"] == "bullish"):
            score += 1
            details["structure"] = (f"BOS={structure['bos']} CHoCH={structure['choch']}"
                                    ).replace("None", "-")
        elif not long and (structure["bos"] == "bearish" or structure["choch"] == "bearish"):
            score += 1
            details["structure"] = (f"BOS={structure['bos']} CHoCH={structure['choch']}"
                                    ).replace("None", "-")
        else:
            details["structure"] = (f"BOS={structure['bos']} CHoCH={structure['choch']}"
                                    ).replace("None", "-")

        # 4. Liquidity setup
        if liquidity["swept"]:
            # A sweep is a reversal signal — it supports trading against
            # the sweep direction (the sweep trapped traders, institutions
            # reverse).
            score += 1
            details["liquidity"] = "sweep_detected"
        elif long and liquidity["bearish_liquidity"]:
            # Below: price may drop to grab sell-side liquidity before
            # reversing up — supports a LONG with patience.
            score += 1
            details["liquidity"] = "sell_side_liquidity_below"
        elif not long and liquidity["bullish_liquidity"]:
            score += 1
            details["liquidity"] = "buy_side_liquidity_above"
        else:
            details["liquidity"] = "neutral"

        result["score"] = min(4, score)
        result["details"] = details

    except Exception as e:
        log.warning("smart_money_confluence failed for %s: %s", symbol, e)
        result["details"] = {"error": str(e)}

    return result
