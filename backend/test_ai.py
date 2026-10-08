"""
cryptoautomation — Test AI Engine (paper-trading for AI model validation).

Simulates trades using the full 6-layer confirmation pipeline WITHOUT
touching any broker. All trades are recorded in the `test_trades` table
with complete metadata for later analysis by the feedback loop.

Purpose:
    - Test new AI models / confirmation thresholds risk-free
    - Build a statistical database of what works before going live
    - Validate the 6-layer pipeline's actual win rate
    - Feed the adaptive calibration loop (ai_feedback.py)

The engine:
    1. Receives a proposal + intent from bot.py (already filtered by
       the 6-layer pipeline in news_analyst.signal_for)
    2. Records the trade with ALL metadata (regime, confirm_score,
       dxy_score, smc_score, session, calendar status)
    3. Calculates ATR-based SL/TP levels
    4. Monitors open test trades against live prices
    5. Records outcomes (win/loss/breakeven) for feedback analysis

The test_trades table is separate from the orders table — live and test
trades never mix. Switch to live mode via AI_MODE=live in .env.

The module NEVER raises — on any failure it returns a safe default so
the bot cycle continues.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone

import config
import database
import indicators
import risk

log = logging.getLogger(__name__)


def _current_price(conn, symbol: str, interval: str = "1h") -> float | None:
    """Get the latest close price for a symbol. Returns None on failure."""
    try:
        df = database.load_candles(conn, symbol, interval, limit=3)
        if df.empty:
            return None
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def _atr_levels(conn, symbol: str, entry: float, direction: str) -> dict:
    """ATR-based SL/TP levels for a test trade.

    SL = 1.5 × ATR from entry (0.5% floor, 5% cap).
    TP = 2R (twice the SL distance).

    Returns {stop_loss, take_profit, atr}.
    """
    try:
        df = indicators.add_all(database.load_candles(conn, symbol, "1h", limit=60))
        atr = float(df["atr"].iloc[-1]) if (not df.empty and "atr" in df.columns) else 0.0
    except Exception:
        atr = 0.0

    if not atr or atr <= 0:
        sl_dist = entry * 0.01
    else:
        sl_dist = max(atr * 1.5, entry * 0.005)
    sl_dist = min(sl_dist, entry * 0.05)  # cap at 5%

    long = direction.upper() == "LONG"
    stop = entry - sl_dist if long else entry + sl_dist
    tp = entry + 2 * sl_dist if long else entry - 2 * sl_dist

    return {"stop_loss": round(stop, 8), "take_profit": round(tp, 8),
            "atr": round(atr, 8)}


def _intent_hash(symbol: str, direction: str) -> str:
    """Dedup key: same symbol+direction on the same UTC day = same hash.
    Prevents re-entry on the same signal within a day."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    raw = f"{symbol}|{direction}|{day}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def record_paper_trade(conn, proposal, intent, dry_run: bool = False,
                       ai_model: str | None = None) -> dict:
    """Record a paper trade in the test_trades table.

    This is the test-mode equivalent of execution.execute_intent().
    No broker calls — just records the trade with all metadata for
    later analysis.

    Args:
        conn: database connection
        proposal: the Proposal from _ai_news_proposals (has all metadata)
        intent: the Intent from arbitrate() (dedup-ready trade)
        dry_run: if True, don't actually record (preview mode)
        ai_model: override the journaled model tag (2026-10-05: the
            gov-shadow prototype journals 'groq-gov' here so its rows are
            separable from the live 'groq' paper book).

    Returns:
        {trade: dict, duplicate: bool, dry_run: bool}
    """
    try:
        # Dedup: same symbol+direction on the same day
        hash_key = _intent_hash(intent.symbol, intent.direction)
        existing = conn.execute(
            "SELECT id FROM test_trades WHERE symbol = ? AND direction = ? "
            "AND date(ran_at) = date('now') LIMIT 1",
            (intent.symbol, intent.direction)).fetchone()
        if existing:
            return {"trade": {"id": existing["id"]}, "duplicate": True,
                    "dry_run": False}

        if dry_run:
            return {"trade": {"status": "dry_run"}, "duplicate": False,
                    "dry_run": True}

        # Get current price for entry
        entry_price = _current_price(conn, intent.symbol)
        if not entry_price:
            return {"trade": None, "duplicate": False, "dry_run": dry_run,
                    "error": f"no price data for {intent.symbol}"}

        # Calculate SL/TP levels
        levels = _atr_levels(conn, intent.symbol, entry_price, intent.direction)

        # Extract metadata from the proposal (the 6-layer pipeline output)
        symbol = intent.symbol
        direction = intent.direction
        sentiment = float(getattr(proposal, "confidence", 0) or 0) / 100.0
        confidence = float(getattr(proposal, "confidence", 0) or 0) / 100.0
        confirm_score = int(getattr(proposal, "confirm_score", 0) or 0)
        regime = getattr(proposal, "regime", None)
        dxy_score = int(getattr(proposal, "dxy_score", 0) or 0)
        smc_score = int(getattr(proposal, "smc_score", 0) or 0)
        session = getattr(proposal, "session", None)
        calendar_blocked = 0  # already filtered upstream if blocked

        # AI model info
        ai_model = ai_model or "groq"
        ai_reason = "; ".join(getattr(intent, "reasons", None) or [])

        # Record the trade
        trade_id = database.record_test_trade(
            conn,
            symbol=symbol,
            direction=direction,
            sentiment=round(sentiment, 4),
            confidence=round(confidence, 4),
            confirm_score=confirm_score,
            regime=regime,
            dxy_score=dxy_score,
            smc_score=smc_score,
            session=session,
            calendar_blocked=calendar_blocked,
            entry_price=round(entry_price, 8),
            stop_loss=levels["stop_loss"],
            take_profit=levels["take_profit"],
            ai_model=ai_model,
            ai_reason=ai_reason,
        )

        trade = {
            "id": trade_id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(entry_price, 8),
            "stop_loss": levels["stop_loss"],
            "take_profit": levels["take_profit"],
            "atr": levels["atr"],
            "confirm_score": confirm_score,
            "dxy_score": dxy_score,
            "smc_score": smc_score,
            "regime": regime,
            "session": session,
            "status": "open",
        }

        log.info("Test trade recorded: %s %s @ %.8f SL=%.8f TP=%.8f "
                 "tc=%d dxy=%d smc=%d regime=%s",
                 symbol, direction, entry_price, levels["stop_loss"],
                 levels["take_profit"], confirm_score, dxy_score,
                 smc_score, regime)

        return {"trade": trade, "duplicate": False, "dry_run": False}

    except Exception as e:
        log.warning("record_paper_trade failed for %s: %s",
                    getattr(intent, "symbol", "?"), e)
        return {"trade": None, "duplicate": False, "dry_run": dry_run,
                "error": str(e)}


# ----------------------------------------------------------------------
# Test position management (SL/TP monitoring for paper trades)
# ----------------------------------------------------------------------
def check_test_position(trade: dict, price: float) -> str:
    """What should happen to an open test trade at `price`?

    Returns: 'tp' | 'sl' | 'hold'
    """
    if trade.get("outcome") not in (None, "open", ""):
        return "closed"

    entry = float(trade.get("entry_price") or 0)
    sl = float(trade.get("stop_loss") or 0)
    tp = float(trade.get("take_profit") or 0)
    if not entry:
        return "hold"

    long = trade.get("direction", "").upper() == "LONG"

    # Take profit check
    if tp:
        if long and price >= tp:
            return "tp"
        if not long and price <= tp:
            return "tp"

    # Stop loss check
    if sl:
        if long and price <= sl:
            return "sl"
        if not long and price >= sl:
            return "sl"

    return "hold"


def manage_test_positions(conn, interval: str = "1h") -> list[dict]:
    """Monitor all open test trades and close those that hit SL/TP.

    Returns a list of closed trade summaries.
    """
    closed = []
    try:
        open_trades = database.load_test_trades(conn, limit=200, status="open")
    except Exception as e:
        log.warning("manage_test_positions: failed to load open trades: %s", e)
        return closed

    for trade in open_trades:
        symbol = trade.get("symbol")
        if not symbol:
            continue

        price = _current_price(conn, symbol, interval)
        if price is None:
            continue

        action = check_test_position(trade, price)
        if action not in ("tp", "sl"):
            continue

        # Calculate PnL
        entry = float(trade.get("entry_price") or 0)
        qty = 1.0  # normalized qty for test trades (PnL in price units)
        if trade.get("direction", "").upper() == "LONG":
            pnl = (price - entry) * qty
        else:
            pnl = (entry - price) * qty

        pnl_pct = (pnl / entry * 100) if entry > 0 else 0.0
        trade_id = trade.get("id")

        try:
            database.update_test_trade_exit(
                conn, trade_id,
                exit_price=round(price, 8),
                reason=action,
                pnl=round(pnl, 8),
            )
            closed.append({
                "symbol": symbol,
                "direction": trade.get("direction"),
                "reason": action,
                "entry": entry,
                "exit": round(price, 8),
                "pnl": round(pnl, 8),
                "pnl_pct": round(pnl_pct, 4),
                "trade_id": trade_id,
            })
            log.info("Test trade closed: %s %s %s entry=%.8f exit=%.8f pnl=%.4f",
                     symbol, trade.get("direction"), action, entry, price, pnl)
        except Exception as e:
            log.warning("Failed to close test trade %s: %s", trade_id, e)

    return closed


def test_portfolio_summary(conn) -> dict:
    """Summary statistics for all test trades — for the dashboard.

    Returns:
        total_trades, wins, losses, breakevens, win_rate, total_pnl,
        avg_pnl, best_trade, worst_trade, by_regime, by_session
    """
    try:
        stats = database.test_trade_stats(conn)
        trades = database.load_test_trades(conn, limit=500)

        if not trades:
            return {"total_trades": 0, "open_trades": 0}

        open_count = sum(1 for t in trades
                        if t.get("outcome") in (None, "open", ""))

        closed = [t for t in trades
                  if t.get("outcome") in ("win", "loss", "breakeven")]

        best = max(closed, key=lambda t: float(t.get("pnl") or 0)) if closed else None
        worst = min(closed, key=lambda t: float(t.get("pnl") or 0)) if closed else None

        # By regime breakdown
        by_regime = {}
        for t in closed:
            r = str(t.get("regime") or "unknown")
            if r not in by_regime:
                by_regime[r] = {"total": 0, "wins": 0, "pnl": 0.0}
            by_regime[r]["total"] += 1
            if t.get("outcome") == "win":
                by_regime[r]["wins"] += 1
            by_regime[r]["pnl"] += float(t.get("pnl") or 0)
        for v in by_regime.values():
            v["win_rate"] = round(v["wins"] / v["total"], 3) if v["total"] > 0 else 0.0
            v["pnl"] = round(v["pnl"], 2)

        # By session breakdown
        by_session = {}
        for t in closed:
            s = str(t.get("session") or "unknown")
            if s not in by_session:
                by_session[s] = {"total": 0, "wins": 0, "pnl": 0.0}
            by_session[s]["total"] += 1
            if t.get("outcome") == "win":
                by_session[s]["wins"] += 1
            by_session[s]["pnl"] += float(t.get("pnl") or 0)
        for v in by_session.values():
            v["win_rate"] = round(v["wins"] / v["total"], 3) if v["total"] > 0 else 0.0
            v["pnl"] = round(v["pnl"], 2)

        return {
            **stats,
            "open_trades": open_count,
            "best_trade": {
                "symbol": best.get("symbol"),
                "pnl": round(float(best.get("pnl") or 0), 4),
            } if best else None,
            "worst_trade": {
                "symbol": worst.get("symbol"),
                "pnl": round(float(worst.get("pnl") or 0), 4),
            } if worst else None,
            "by_regime": by_regime,
            "by_session": by_session,
        }
    except Exception as e:
        log.warning("test_portfolio_summary failed: %s", e)
        return {"total_trades": 0, "error": str(e)}
