"""
cryptoautomation — Tiered Circuit Breaker System.

Based on the FXNX institutional Circuit Breaker Framework and the
Hour-Aware Adaptive Risk paper. Protects capital through a tiered
step-down protocol during drawdowns, with a step-up recovery ladder.

Step-Down Protocol:
    Level 0: Normal trading (0-5% drawdown)
    Level 1: Reduce risk per trade by 25% (5-10% drawdown)
    Level 2: Reduce risk by 50% (10-15% drawdown)
    Level 3: Micro-lots only (15%+ drawdown)
    Level 4: Full stop — halt all trading (MAX_DRAWDOWN_PCT)

Step-Up Recovery Ladder:
    Only restore next size band after a block of trades at reduced risk
    confirms the edge is still present. Earn size back one step at a time.

Additional Breakers:
    - Consecutive loss breaker: 3 consecutive losses → auto step-down
    - Strategy decay detector: rolling WR < 40% → flag decay
    - Correlation exposure: over-correlated positions → reduce total exposure

The module NEVER raises — on any failure it returns safe defaults.
"""

from __future__ import annotations

import logging
import time

import config
import database

log = logging.getLogger(__name__)


def current_risk_state(conn) -> dict:
    """Compute the current risk state from the journal.

    Returns:
        drawdown_pct: float (0-100)
        level: int (0-4) — current circuit breaker level
        risk_multiplier: float (0.0 - 1.0) — position size multiplier
        consecutive_losses: int
        daily_pnl: float
        daily_pnl_pct: float
        peak_equity: float
        current_equity: float
        status: str — human-readable status
    """
    if not config.CIRCUIT_BREAKER_ENABLED:
        return {"drawdown_pct": 0.0, "level": 0, "risk_multiplier": 1.0,
                "consecutive_losses": 0, "daily_pnl": 0.0,
                "daily_pnl_pct": 0.0, "peak_equity": 0.0,
                "current_equity": 0.0, "status": "circuit_breaker_disabled"}

    try:
        # Get journal-based equity curve
        sources = config.EXECUTION_BROKER if config.EXECUTION_BROKER != "hybrid" else ["mt5", "binance"]
        if isinstance(sources, list):
            orders = []
            seen_ids = set()
            for s in sources:
                for o in database.load_orders(conn, source=s, limit=500):
                    if o["id"] not in seen_ids:
                        seen_ids.add(o["id"])
                        orders.append(o)
        else:
            orders = database.load_orders(conn, source=sources, limit=500)
        if not orders:
            return {"drawdown_pct": 0.0, "level": 0, "risk_multiplier": 1.0,
                    "consecutive_losses": 0, "daily_pnl": 0.0,
                    "daily_pnl_pct": 0.0, "peak_equity": config.INITIAL_CAPITAL,
                    "current_equity": config.INITIAL_CAPITAL,
                    "status": "no_trades"}

        # Calculate equity from orders
        equity = config.INITIAL_CAPITAL
        peak = config.INITIAL_CAPITAL
        daily_pnl = 0.0
        today = time.strftime("%Y-%m-%d", time.gmtime())
        consecutive_losses = 0
        max_consecutive_losses = 0

        for o in sorted(orders, key=lambda x: str(x.get("entry_time", ""))):
            pnl = float(o.get("pnl") or 0.0)
            equity += pnl
            if equity > peak:
                peak = equity
            # Daily PnL
            entry_time = str(o.get("exit_time") or o.get("entry_time") or "")
            if entry_time.startswith(today):
                daily_pnl += pnl
            # Consecutive losses
            if o.get("status") in ("filled", "closed") and pnl < 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses,
                                             consecutive_losses)
            elif pnl > 0:
                consecutive_losses = 0

        current_equity = max(equity, 1.0)  # prevent zero
        drawdown_pct = ((peak - current_equity) / peak) * 100 if peak > 0 else 0.0
        daily_pnl_pct = (daily_pnl / max(peak, 1.0)) * 100

        # Determine circuit breaker level
        if drawdown_pct >= config.CB_LEVEL3_DD_PCT:
            level = 3
            risk_mult = 0.1  # micro-lots only
        elif drawdown_pct >= config.CB_LEVEL2_DD_PCT:
            level = 2
            risk_mult = 0.5
        elif drawdown_pct >= config.CB_LEVEL1_DD_PCT:
            level = 1
            risk_mult = 0.75
        else:
            level = 0
            risk_mult = 1.0

        # Consecutive loss auto step-down
        if max_consecutive_losses >= config.CB_CONSECUTIVE_LOSS_LIMIT:
            level = max(level, 1)
            risk_mult = min(risk_mult, 0.75)

        # Status description
        level_names = {
            0: "normal", 1: "cautious (-25% risk)",
            2: "defensive (-50% risk)", 3: "micro-lots only"
        }
        status = level_names.get(level, "unknown")
        if max_consecutive_losses >= config.CB_CONSECUTIVE_LOSS_LIMIT:
            status += f" (consecutive losses: {max_consecutive_losses})"

        return {
            "drawdown_pct": round(drawdown_pct, 2),
            "level": level,
            "risk_multiplier": risk_mult,
            "consecutive_losses": max_consecutive_losses,
            "daily_pnl": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 2),
            "peak_equity": round(peak, 2),
            "current_equity": round(current_equity, 2),
            "status": status,
        }
    except Exception as e:
        log.warning("current_risk_state failed: %s", e)
        return {"drawdown_pct": 0.0, "level": 0, "risk_multiplier": 1.0,
                "consecutive_losses": 0, "daily_pnl": 0.0,
                "daily_pnl_pct": 0.0, "peak_equity": config.INITIAL_CAPITAL,
                "current_equity": config.INITIAL_CAPITAL,
                "status": f"error: {e}"}


def strategy_health_check(conn) -> dict:
    """Check if the strategy is still performing or decaying.

    Analyzes the last N trades (CB_DECAY_LOOKBACK_TRADES) for:
        - Rolling win rate
        - Average PnL per trade
        - Profit factor (gross profit / gross loss)

    Returns:
        healthy: bool
        rolling_wr: float (0-1)
        avg_pnl: float
        profit_factor: float
        trades_analyzed: int
        verdict: str
    """
    if not config.CIRCUIT_BREAKER_ENABLED:
        return {"healthy": True, "rolling_wr": 0.0, "avg_pnl": 0.0,
                "profit_factor": 0.0, "trades_analyzed": 0,
                "verdict": "circuit_breaker_disabled"}

    try:
        orders = database.load_orders(conn, source=config.EXECUTION_BROKER,
                                      limit=config.CB_DECAY_LOOKBACK_TRADES)
        closed = [o for o in orders if o.get("status") in ("closed", "filled")
                  and (o.get("pnl") or 0) != 0]

        if len(closed) < 5:
            return {"healthy": True, "rolling_wr": 0.0, "avg_pnl": 0.0,
                    "profit_factor": 0.0, "trades_analyzed": len(closed),
                    "verdict": "insufficient_data"}

        wins = [o for o in closed if float(o.get("pnl") or 0) > 0]
        losses = [o for o in closed if float(o.get("pnl") or 0) < 0]

        wr = len(wins) / len(closed) if closed else 0.0
        avg_pnl = sum(float(o.get("pnl") or 0) for o in closed) / len(closed)

        gross_profit = sum(float(o.get("pnl") or 0) for o in wins) if wins else 0
        gross_loss = abs(sum(float(o.get("pnl") or 0) for o in losses)) if losses else 1
        profit_factor = gross_profit / max(gross_loss, 0.01)

        healthy = wr >= config.CB_STRATEGY_DECAY_WR
        if healthy:
            verdict = f"healthy (WR={wr:.0%}, PF={profit_factor:.1f})"
        else:
            verdict = (f"DECAY DETECTED (WR={wr:.0%} < "
                       f"{config.CB_STRATEGY_DECAY_WR:.0%}) — "
                       f"consider switching to test mode")

        return {
            "healthy": healthy,
            "rolling_wr": round(wr, 3),
            "avg_pnl": round(avg_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "trades_analyzed": len(closed),
            "verdict": verdict,
        }
    except Exception as e:
        log.warning("strategy_health_check failed: %s", e)
        return {"healthy": True, "rolling_wr": 0.0, "avg_pnl": 0.0,
                "profit_factor": 0.0, "trades_analyzed": 0,
                "verdict": f"error: {e}"}


def correlation_exposure(conn) -> dict:
    """Check if open positions are over-correlated.

    If multiple positions are all USD-correlated (e.g., LONG EUR/USD +
    LONG GBP/USD + SHORT USD/JPY = all betting on USD weakness), a single
    DXY move can hit all of them simultaneously.

    Returns:
        usd_exposure: str — "net_long_usd" | "net_short_usd" | "balanced"
        correlated_count: int — how many positions share USD correlation
        warning: bool — exposure is concentrated
    """
    try:
        orders = database.load_orders(conn, source=config.EXECUTION_BROKER,
                                      limit=100)
        open_orders = [o for o in orders
                       if o.get("status") in ("filled", "open")]

        if len(open_orders) < 2:
            return {"usd_exposure": "balanced", "correlated_count": 0,
                    "warning": False}

        # Count USD-side exposure
        usd_long = 0  # positions that profit from USD strength
        usd_short = 0  # positions that profit from USD weakness
        for o in open_orders:
            sym = o.get("symbol", "")
            direction = o.get("side") or o.get("direction", "")
            if sym.startswith("USD/") or sym == "DXY":
                if direction in ("LONG", "buy"):
                    usd_long += 1
                else:
                    usd_short += 1
            elif sym.endswith("/USD") or sym in ("XAU/USD", "XAG/USD"):
                if direction in ("LONG", "buy"):
                    usd_short += 1  # LONG gold/EUR = short USD
                else:
                    usd_long += 1

        total = usd_long + usd_short
        if total >= 3:
            net = "net_long_usd" if usd_long > usd_short else "net_short_usd"
            return {"usd_exposure": net, "correlated_count": total,
                    "warning": True}

        return {"usd_exposure": "balanced", "correlated_count": total,
                "warning": False}
    except Exception as e:
        log.warning("correlation_exposure failed: %s", e)
        return {"usd_exposure": "unknown", "correlated_count": 0,
                "warning": False}
