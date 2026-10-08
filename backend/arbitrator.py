"""
cryptoautomation — Signal arbitration (the two-source unification).

Both signal sources emit the same canonical shape, a `Proposal`:

    Source A (math engine)      signals.py → math_engine/strategy/factors
    Source B (proprietary)      proprietary_signals.py  (independent generator)

`arbitrate()` is the ONLY place the two sources meet:

  - both must agree on direction → one `Intent` for the single execution path
  - disagreement / HOLD → None (never a lone-source order)
  - `intent_hash` = sha256(symbol|direction|window|utc-day) → stable dedup
    key so the same signal can never place two orders, while a re-entry on a
    later day is a fresh, tradable intent (see execution.py)

The merge is conservative: entry/SL/TP are taken from either source (first
that provides them), confidence is the WEAKER of the two, and a source
pairing that yields no entry, stop, or take-profit is still allowed — the
execution layer fills the gaps from live prices (entry) and ATR (stops)
rather than inventing levels here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field


@dataclass
class Proposal:
    """One signal source's opinion on one symbol. The canonical schema."""
    symbol: str
    direction: str            # LONG | SHORT | HOLD
    confidence: float         # 0-100
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    source: str = "unknown"
    window: str = "1h"
    ts: float = field(default_factory=time.time)
    reasons: list[str] = field(default_factory=list)   # why this source says so
    # Extended metadata from the 6-layer confirmation pipeline (2026-08-17):
    regime: str | None = None
    confirm_score: int = 0
    dxy_score: int = 0
    smc_score: int = 0
    session: str | None = None
    session_mult: float = 1.0
    block_reasons: list[str] = field(default_factory=list)
    # AI-suggested SL/TP percentages (2026-08-18):
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    # Market regime at signal time (from ai_confirm / news analyst) —
    # journaled on the order so the book can be audited by-trend later.
    trend_regime: str | None = None


@dataclass
class Intent:
    """An agreed, dedup-ready trade intent heading to the execution path."""
    symbol: str
    direction: str            # LONG | SHORT (never HOLD — only agreed trades)
    entry: float | None
    stop_loss: float | None
    take_profit: float | None
    sources: list[str]
    window: str
    confidence: float
    intent_hash: str
    reasons: list[str] = field(default_factory=list)   # why the trade is taken
    # AI-suggested SL/TP percentages (2026-08-18):
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    # Regime at signal time — carried to the order journal for post-mortems.
    trend_regime: str | None = None


def proposal_from_signal_dict(d: dict, source: str = "math_engine",
                              window: str = "1h") -> Proposal:
    """Convert a signals.py-style dict (direction, entry, stop_loss,
    take_profit, confidence, …) into a Proposal. HOLD maps to HOLD."""
    direction = str(d.get("direction", "HOLD")).upper()
    if direction not in ("LONG", "SHORT", "HOLD"):
        direction = "HOLD"
    reasons = _reasons_from_signal(d)
    return Proposal(
        symbol=str(d.get("symbol", "")),
        direction=direction,
        confidence=float(d.get("confidence") or 0.0),
        entry=d.get("entry"),
        stop_loss=d.get("stop_loss"),
        take_profit=d.get("take_profit"),
        source=source,
        window=window,
        reasons=reasons,
        stop_loss_pct=float(d.get("stop_loss_pct") or 0.0),
        take_profit_pct=float(d.get("take_profit_pct") or 0.0),
    )


def _reasons_from_signal(d: dict) -> list[str]:
    """Human-readable "why" lines from a signals.py dict: indicator hits
    (with effective strength) plus the math-engine regime/EV/kelly summary.
    Used by the bot to explain each trade in dry-test and the journal."""
    out = []
    for r in d.get("reasons") or []:
        name = str(r.get("indicator") or "?")
        sig = str(r.get("signal") or "")
        detail = str(r.get("detail") or "")
        out.append(f"{name} {sig} ({detail})" if detail else f"{name} {sig}")
    ctx = []
    if d.get("regime"):
        ctx.append(f"regime={d['regime']}")
    if d.get("confluence") is not None:
        ctx.append(f"confluence={d['confluence']:.2f}")
    if d.get("ev_per_unit") is not None:
        ctx.append(f"EV={d['ev_per_unit']:.4f}")
    if d.get("kelly_fraction") is not None:
        ctx.append(f"kelly={d['kelly_fraction']:.3f}")
    if ctx:
        out.append("math: " + " · ".join(ctx))
    return out


def intent_hash(symbol: str, direction: str, window: str,
                when: float | None = None) -> str:
    """
    Stable dedup key for an intent: sha256(symbol|direction|window|utc-day),
    truncated for readability.

    The utc-day component is what lets the bot trade repeatedly: without it
    the same symbol+direction could only ever trade ONCE (after the first
    BTC/USDT LONG fill, every future BTC/USDT LONG in the same window was a
    permanent "duplicate"). With it, a re-entry on a later day is a NEW
    intent, while duplicate firings of the SAME signal (both sources firing
    at once, or a re-run of the same cycle) still collapse to one order
    because they happen on the same day.
    """
    when = when or time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(when))
    raw = f"{symbol}|{direction}|{window}|{day}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def arbitrate(a: Proposal, b: Proposal, min_sources: int = 2,
              min_confidence: float = 0.0) -> Intent | None:
    """
    The agreement gate.

    A source is ACTIVE when it has a concrete direction (LONG/SHORT) and
    confidence >= min_confidence. Everything else (HOLD, missing, too-weak)
    is an ABSTENTION — it has no opinion, so it neither trades nor vetoes.

    Default (min_sources=2): an Intent only when BOTH sources are active
    and want the same direction; otherwise None.

    Testing mode (min_sources=1): a single active source can trade alone
    (e.g. source B's RSI(2) extreme while source A abstains in an all-HOLD
    regime). A conflicting active source still blocks the trade — only
    abstentions are ignored.

    Confidence on the merged intent is always the WEAKER active source's
    (a barely-confident source caps the position regardless of the other's
    conviction).
    """
    active = [p for p in (a, b)
              if p is not None and p.direction in ("LONG", "SHORT")
              and p.confidence >= min_confidence]
    if not active:
        return None
    if len(active) < min_sources:
        return None
    directions = {p.direction for p in active}
    if len(directions) > 1:
        return None  # conflict — never pick a winner

    direction = active[0].direction
    window = active[0].window or (b.window if b else None) or "1h"
    if len(active) == 2:
        # Merge both active sources: levels from whichever provides them,
        # confidence = weaker, reasons from both (so dry-test explains why).
        a_, b_ = active
        intent = Intent(
            symbol=a_.symbol,
            direction=direction,
            entry=a_.entry or b_.entry,
            stop_loss=a_.stop_loss or b_.stop_loss,
            take_profit=a_.take_profit or b_.take_profit,
            sources=[a_.source, b_.source],
            window=window,
            confidence=min(a_.confidence, b_.confidence),
            intent_hash=intent_hash(a_.symbol, direction, window),
            reasons=list(a_.reasons or []),
            stop_loss_pct=a_.stop_loss_pct or b_.stop_loss_pct,
            take_profit_pct=a_.take_profit_pct or b_.take_profit_pct,
            trend_regime=a_.trend_regime or b_.trend_regime,
        )
        for r in b_.reasons or []:
            if r not in intent.reasons:
                intent.reasons.append(r)
        return intent

    p = active[0]
    return Intent(
        symbol=p.symbol,
        direction=direction,
        entry=p.entry,
        stop_loss=p.stop_loss,
        take_profit=p.take_profit,
        sources=[p.source],
        window=window,
        confidence=p.confidence,
        intent_hash=intent_hash(p.symbol, direction, window),
        reasons=list(p.reasons or []),
        trend_regime=p.trend_regime,
        stop_loss_pct=p.stop_loss_pct,
        take_profit_pct=p.take_profit_pct,
    )
