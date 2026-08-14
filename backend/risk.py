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
