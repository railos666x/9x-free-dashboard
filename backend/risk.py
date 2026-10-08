"""
cryptoautomation — Risk Management & Execution Model.

Production-grade risk layer used by the backtester (and reusable for
paper/live trading):

  1. Execution costs — per-symbol slippage (bps) + exchange fees (bps).
     Slippage tiers live in config.SLIPPAGE_BPS_TIERS; anything not in the
     tier table falls back to a default. Conservative by design.
  2. Position sizing — risk-based: risk a fraction of *current* equity per
     trade, capped by fractional Kelly when a measured edge exists.
     size = (risk_pct * equity) / |entry - SL|
  3. ATR-based stops — the SL passed in is already ATR-scaled by the
     strategy; here we only sanitize it and compute the per-unit risk.

Everything is a pure function — no DB, no network — so it can be unit
tested and reused in any execution path.
"""

from __future__ import annotations

import math

import config


# ──────────────────────────────────────────────────────────────────────
# Execution costs
# ──────────────────────────────────────────────────────────────────────

def slippage_bps(symbol: str) -> float:
    """Assumed slippage in basis points for a market-order fill."""
    return float(config.SLIPPAGE_BPS_TIERS.get(symbol, config.SLIPPAGE_BPS_DEFAULT))


def fee_bps() -> float:
    """Exchange taker fee per side, in basis points."""
    return float(config.FEE_BPS)


def total_cost_bps(symbol: str, sides: int = 2) -> float:
    """Round-trip cost (slippage both sides + fee both sides), in bps."""
    return (slippage_bps(symbol) + fee_bps()) * sides


def cost_factor(symbol: str) -> float:
    """Round-trip cost as a decimal fraction of notional (1 bps = 1e-4)."""
    return total_cost_bps(symbol) / 10_000.0


# ──────────────────────────────────────────────────────────────────────
# Sizing
# ──────────────────────────────────────────────────────────────────────

def kelly_fraction(win_rate: float, risk_reward: float, fraction: float = None) -> float:
    """
    Fractional Kelly: f* = W − (1−W)/R, scaled by `fraction` (default
    config.KELLY_FRACTION) and floored at 0 (no edge → no size).
    """
    fraction = config.KELLY_FRACTION if fraction is None else fraction
    if win_rate <= 0 or win_rate >= 1 or risk_reward <= 0:
        return 0.0
    full = win_rate - (1.0 - win_rate) / risk_reward
    return max(full * fraction, 0.0)


def risk_per_trade_pct(kelly: float) -> float:
    """
    Risk % of equity for this trade: bounded between 0 and MAX_RISK_PER_TRADE.
    With no measured edge (kelly == 0) we still trade at the default
    RISK_PER_TRADE — a measured edge only ever *shrinks* risk below the cap.
    """
    base = config.RISK_PER_TRADE
    if kelly > 0:
        base = min(base, kelly)
    return max(0.0, min(base, config.MAX_RISK_PER_TRADE))


def position_size(
    equity: float,
    entry: float,
    stop_loss: float,
    risk_pct: float,
) -> float:
    """
    Units of the asset to trade:

        risk_amount  = risk_pct * equity
        unit_risk    = |entry − SL|      (per unit, already ATR-scaled)
        size         = risk_amount / unit_risk

    Returns 0 for degenerate inputs (no equity, no price distance).
    """
    if equity <= 0 or entry <= 0 or risk_pct <= 0:
        return 0.0
    unit_risk = abs(entry - stop_loss)
    if unit_risk <= 0 or not math.isfinite(unit_risk):
        return 0.0
    return (risk_pct * equity) / unit_risk


def notional(size: float, entry: float) -> float:
    return size * entry


# ──────────────────────────────────────────────────────────────────────
# Phase-2 sizing multipliers (pure math — DB/IO stays in execution.py)
# ──────────────────────────────────────────────────────────────────────

def inverse_vol_multiplier(atr_pct: float | None,
                           target_atr_pct: float | None = None,
                           lo: float | None = None,
                           hi: float | None = None) -> float:
    """Vol-targeting multiplier: risk MORE in quiet tape, LESS in wild tape.

        mult = target_vol / realized_vol      (realized_vol = ATR / price)

    ATR(14) on the entry timeframe already exists in the indicators stack
    (roadmap Phase 2, P1). Returns 1.0 (neutral) when volatility is unknown
    — missing data must never invent an opinion. The result is clamped to
    [lo, hi] (default 0.5×–2.0× of the base risk).
    """
    target = config.SIZE_VOL_TARGET_ATR_PCT if target_atr_pct is None else target_atr_pct
    lo = config.SIZE_VOL_MIN_MULT if lo is None else lo
    hi = config.SIZE_VOL_MAX_MULT if hi is None else hi
    try:
        if (not target or target <= 0 or atr_pct is None
                or not math.isfinite(float(atr_pct)) or float(atr_pct) <= 0):
            return 1.0
        raw = target / float(atr_pct)
        if not math.isfinite(raw) or raw <= 0:
            return 1.0
        return max(lo, min(hi, raw))
    except Exception:
        return 1.0


def bucket_expectancy_ci(records: list[dict]) -> tuple[float, float] | None:
    """Mean per-trade risk-multiple (expectancy in R) and its approximate
    95% CI half-width for a bucket of closed trades.

    Each record = one closed trade: {pnl, entry, stop_loss, qty}. R is
    pnl / risk_amount with risk_amount = |entry − stop| × qty — how much
    the trade made vs what it set out to risk, independent of the account
    size it happened to run on. Trades without usable levels are skipped.
    With n < 2 the CI is meaningless → returns (mean, None); callers treat
    a missing CI as 'not significant' (never the negative-CI kill switch).
    Normal approximation (σ/√n) is deliberately simple: at n=30 the exact
    Student-t quantile differs by <5%, far inside the clamp band.
    """
    rs = []
    for rec in records or []:
        try:
            risk_amt = abs(float(rec["entry"]) - float(rec["stop_loss"])) \
                * abs(float(rec["qty"]))
            if risk_amt <= 0:
                continue
            rs.append(float(rec["pnl"]) / risk_amt)
        except (KeyError, TypeError, ValueError):
            continue
    n = len(rs)
    if n == 0:
        return None
    mean = sum(rs) / n
    if n < 2:
        return mean, None
    var = sum((x - mean) ** 2 for x in rs) / (n - 1)
    half = 1.96 * math.sqrt(max(var, 0.0) / n)
    return mean, half


def bucket_kelly_multiplier(records: list[dict],
                            lo: float | None = None,
                            hi: float | None = None) -> float:
    """Fractional-Kelly sizing multiplier for a bucket of closed trades.

    From the last N closed trades in the (venue, regime, news-tag) bucket
    (roadmap Phase 2, P2):
      * expectancy E[R] and its 95% CI (bucket_expectancy_ci);
      * statistically NEGATIVE expectancy (mean + 1.96σ/√n < 0) → 0.0:
        the bucket stops trading size until the data says otherwise — the
        ranging-regime gate emerges from data instead of a hard rule;
      * Kelly f* = E/R − (1 − E)/R with R = avg |winner| / avg |loser| in R
        units (the payoff ratio), floored at 0 when E ≤ 0;
      * the fraction-of-Kelly risk pct becomes a multiplier vs the base
        risk: mult = f* / base_risk_pct, clamped [0.5, 1.5] by default;
      * f* is additionally capped at MAX_RISK_PER_TRADE so an implausible
        measured edge can't inflate past the hard risk cap via the clamp;
      * < 2 usable trades → 1.0 (neutral): no opinion yet.
    """
    lo = config.SIZE_KELLY_MIN_MULT if lo is None else lo
    hi = config.SIZE_KELLY_MAX_MULT if hi is None else hi
    try:
        ci = bucket_expectancy_ci(records)
        if ci is None:
            return 1.0
        mean, half = ci
        if half is None:
            return 1.0   # n < 2: no usable CI → no evidence, no opinion
        if (mean + half) < 0:
            return 0.0   # negative at 95% confidence: starve the bucket
        if mean <= 0:
            return 1.0   # negative but not significant → no confidence to shrink
        wins = [abs(float(r["pnl"])) for r in (records or [])
                if _num(r.get("pnl")) > 0 and _usable(r)]
        losses = [abs(float(r["pnl"])) for r in (records or [])
                  if _num(r.get("pnl")) < 0 and _usable(r)]
        if not wins or not losses:
            R = 2.0   # one-sided sample (all wins/all losses): modest prior
        else:
            R = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
        if R <= 0:
            return 1.0
        full = mean - (1.0 - mean) / R          # Kelly f* in R units
        frac_risk = max(0.0, full) * config.SIZE_KELLY_FRACTION
        frac_risk = min(frac_risk, config.MAX_RISK_PER_TRADE)
        base = max(config.SIZE_KELLY_BASE_RISK_PCT, 1e-9)
        return max(lo, min(hi, frac_risk / base))
    except Exception:
        return 1.0


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _usable(rec: dict) -> bool:
    """A trade contributes to the payoff ratio only when its risk amount
    can be reconstructed (mirrors bucket_expectancy_ci's skip rule)."""
    try:
        return abs(float(rec["entry"]) - float(rec["stop_loss"])) \
            * abs(float(rec["qty"])) > 0
    except (KeyError, TypeError, ValueError):
        return False


# ──────────────────────────────────────────────────────────────────────
# Sanitizers
# ──────────────────────────────────────────────────────────────────────

def safe_stop(sl: float, entry: float, fallback_atr: float) -> float:
    """Ensure a stop is finite and on the correct side of entry."""
    if fallback_atr is None or not math.isfinite(fallback_atr) or fallback_atr <= 0:
        fallback_atr = entry * 0.01
    if sl is None or not math.isfinite(sl) or sl <= 0:
        sl = entry - fallback_atr
    if not math.isfinite(sl) or sl <= 0:
        sl = entry * 0.99
    return sl


def round_price(v: float, ref: float = None) -> float:
    """
    Price rounding that survives micro-priced coins (SHIB ≈ 0.00002).
    Uses 6 significant figures instead of fixed decimals, so BTC keeps
    cent-level precision while SHIB keeps its full price.
    """
    if v is None or not math.isfinite(v):
        return v
    return float(f"{v:.6g}")
