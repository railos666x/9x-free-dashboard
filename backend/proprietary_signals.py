"""
cryptoautomation — Proprietary signal source #2 (independent of math engine).

This is the SECOND signal source in the two-source design (see
arbitrator.py). It must stay independent of `signals.py` / `math_engine.py`
so the agreement gate in the arbitrator is meaningful.

Two modes (config.PROPRIETARY_MODE):

  "rsi2" (default) — Connors-style RSI(2) extremes on 15m candles:
        LONG  when RSI(2) <  PROPRIETARY_RSI_LOW  (oversold)
        SHORT when RSI(2) >  PROPRIETARY_RSI_HIGH (overbought)
        HOLD  otherwise
      Thresholds are config-tunable: 10/90 is ultra-strict (rare but high
      conviction), 25/75 (default) produces enough agreement with the math
      engine to actually trade.

  "llm" — one batched call to the LLM failover chain (Gemini → OpenRouter
      → Groq, see reviewer.py) scoring the latest headlines per coin; the
      score's sign becomes the direction. Requires LLM_ENABLED=true; without
      keys it degrades to HOLD for every symbol (never a hard dependency).
      Cached for PROPRIETARY_LLM_TTL seconds — news moves slowly.

No entry/SL/TP levels are emitted — the arbitrator fills them from source A
or the execution layer uses live prices. This is a *prototype* source: swap
`generate_all_proposals` for your real proprietary generator (same shape:
list of Proposal). Disabled entirely unless PROPRIETARY_SIGNALS_ENABLED=true.
"""

from __future__ import annotations

import time

import pandas as pd

import config
import database
import indicators
import reviewer
from arbitrator import Proposal

WINDOW = "15m"
LLM_TTL = 300.0  # llm-mode cache: 5 minutes

_llm_cache = {"at": 0.0, "coins": []}


def _proposal_from_rsi(symbol: str, rsi2: float) -> Proposal:
    if rsi2 <= config.PROPRIETARY_RSI_LOW:
        direction = "LONG"
    elif rsi2 >= config.PROPRIETARY_RSI_HIGH:
        direction = "SHORT"
    else:
        direction = "HOLD"
    confidence = 45.0  # flat, moderate conviction
    if direction == "LONG":
        why = (f"RSI(2)={rsi2:.1f} ≤ {config.PROPRIETARY_RSI_LOW:.0f} "
               f"→ oversold, mean-reversion LONG")
    elif direction == "SHORT":
        why = (f"RSI(2)={rsi2:.1f} ≥ {config.PROPRIETARY_RSI_HIGH:.0f} "
               f"→ overbought, mean-reversion SHORT")
    else:
        why = f"RSI(2)={rsi2:.1f} inside [{config.PROPRIETARY_RSI_LOW:.0f}, " \
              f"{config.PROPRIETARY_RSI_HIGH:.0f}] → no extreme, HOLD"
    return Proposal(symbol=symbol, direction=direction, confidence=confidence,
                    entry=None, stop_loss=None, take_profit=None,
                    source="proprietary", window=WINDOW, reasons=[why])


def _llm_proposals(conn) -> list[Proposal]:
    """One batched LLM review → per-coin proposals. [] when unavailable."""
    if not config.LLM_ENABLED:
        return []
    now = time.time()
    if now - _llm_cache["at"] < LLM_TTL and _llm_cache["coins"]:
        return _llm_cache["coins"]

    review = reviewer._llm_review(conn, limit=20)
    coins = (review or {}).get("coins") or []
    if not coins:
        _llm_cache["at"] = now  # cache the empty result too (cooldown inside)
        return []

    props = []
    for c in coins:
        score = float(c.get("score") or 0)
        if score >= 20:
            direction = "LONG"
        elif score <= -20:
            direction = "SHORT"
        else:
            direction = "HOLD"
        # LLM headline reasoning (a short phrase per coin, when provided).
        why = []
        for r in c.get("reasons") or []:
            title = (r.get("title") if isinstance(r, dict) else str(r)) or ""
            if title:
                why.append(f"news: {title}")
        if not why:
            why.append(f"headline score={score:.0f} → {'bullish' if direction == 'LONG' else 'bearish' if direction == 'SHORT' else 'neutral'}")
        props.append(Proposal(symbol=c["symbol"], direction=direction,
                              confidence=min(70.0, max(30.0, abs(score))),
                              entry=None, stop_loss=None, take_profit=None,
                              source="proprietary-llm", window="news",
                              reasons=why))
    _llm_cache.update({"at": now, "coins": props})
    return props


def generate_all_proposals(conn, symbols: list[str] | None = None) -> list[Proposal]:
    """
    One Proposal per symbol from the proprietary source. Returns [] when
    disabled (config.PROPRIETARY_SIGNALS_ENABLED=false).
    """
    if not config.PROPRIETARY_SIGNALS_ENABLED:
        return []
    mode = config.PROPRIETARY_MODE
    if mode == "llm":
        return _llm_proposals(conn)

    # rsi2 mode (default)
    out = []
    for symbol in symbols or config.SYMBOLS:
        try:
            df = indicators.add_all(
                database.load_candles(conn, symbol, WINDOW, limit=200)
            )
            if df.empty or len(df) < 30:
                continue
            rsi2 = float(indicators.rsi(df["close"], 2).iloc[-1])
            if pd.isna(rsi2):
                continue
            out.append(_proposal_from_rsi(symbol, rsi2))
        except Exception:
            continue  # one bad symbol must not kill the source
    return out
