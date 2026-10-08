"""
cryptoautomation — AI Feedback Loop (adaptive calibration).

Tracks AI prediction accuracy and adapts thresholds based on actual
trade outcomes. The feedback loop analyzes which layers (regime, DXY,
SMC, session) are contributing to wins and which aren't.

Key functions:
    - record_outcome(): log a trade result
    - calibration_report(): analyze performance by factor
    - adaptive_thresholds(): suggest threshold adjustments

The feedback loop SUGGESTS but doesn't auto-apply — the user approves
via dashboard or the system auto-applies after N validated trades.

Based on the BlindTrade paper's finding that the Risk-Regime agent had
the strongest predictive power, and the Hybrid AI paper's multi-factor
scoring approach.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import config
import database

log = logging.getLogger(__name__)


def record_outcome(conn, trade_id: int, outcome: str, pnl: float,
                   regime: str = None, session: str = None,
                   confirm_score: int = None, dxy_score: int = None,
                   smc_score: int = None) -> None:
    """Record a trade outcome for feedback analysis.

    Updates the test_trades or orders table with the outcome and
    metadata needed for calibration analysis.
    """
    try:
        database.update_test_trade_exit(
            conn, trade_id,
            exit_price=None,  # caller should fill
            reason=outcome,
            pnl=pnl
        )
    except Exception as e:
        log.warning("record_outcome(%s) failed: %s", trade_id, e)


def calibration_report(conn, days: int = 30) -> dict:
    """Analyze the last N days of trades for calibration insights.

    Returns a comprehensive report breaking down win rate by:
        - Sentiment strength bucket
        - Regime (trending_up, ranging, volatile)
        - Session (Asian, London, NY, overlap)
        - Confirm score (0-6)
        - DXY score (0, 1, 2)
        - SMC score (0-4)
        - Day of week
        - Overall statistics

    This tells us WHICH layers are actually contributing to wins.
    """
    try:
        trades = database.load_test_trades(conn, limit=500)
        if not trades:
            return {"error": "no_trades", "period_days": days,
                    "total_trades": 0}

        # Filter to recent trades
        cutoff = time.time() - (days * 86400)
        recent = [t for t in trades
                  if t.get("outcome") in ("win", "loss", "breakeven")]

        if not recent:
            return {"error": "no_closed_trades", "period_days": days,
                    "total_trades": 0}

        total = len(recent)
        wins = sum(1 for t in recent if t.get("outcome") == "win")
        losses = sum(1 for t in recent if t.get("outcome") == "loss")
        bes = sum(1 for t in recent if t.get("outcome") == "breakeven")
        wr = wins / total if total > 0 else 0.0

        total_pnl = sum(float(t.get("pnl") or 0) for t in recent)
        avg_pnl = total_pnl / total if total > 0 else 0.0

        report = {
            "period_days": days,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakevens": bes,
            "win_rate": round(wr, 3),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "by_factor": {},
        }

        # Break down by each factor
        for factor in ["regime", "session", "confirm_score", "dxy_score",
                       "smc_score"]:
            buckets = {}
            for t in recent:
                key = str(t.get(factor, "unknown"))
                if key not in buckets:
                    buckets[key] = {"total": 0, "wins": 0, "pnl": 0.0}
                buckets[key]["total"] += 1
                if t.get("outcome") == "win":
                    buckets[key]["wins"] += 1
                buckets[key]["pnl"] += float(t.get("pnl") or 0)

            # Calculate WR per bucket
            for k, v in buckets.items():
                v["win_rate"] = round(v["wins"] / v["total"], 3) if v["total"] > 0 else 0.0
                v["pnl"] = round(v["pnl"], 2)

            report["by_factor"][factor] = buckets

        # Identify best/worst performing factors
        recommendations = []
        for factor, buckets in report["by_factor"].items():
            for key, stats in buckets.items():
                if stats["total"] >= 3:  # need minimum sample
                    if stats["win_rate"] >= 0.70:
                        recommendations.append(
                            f"STRONG: {factor}={key} WR={stats['win_rate']:.0%} "
                            f"({stats['total']} trades)")
                    elif stats["win_rate"] < 0.40:
                        recommendations.append(
                            f"WEAK: {factor}={key} WR={stats['win_rate']:.0%} "
                            f"({stats['total']} trades) — consider blocking")

        report["recommendations"] = recommendations
        return report

    except Exception as e:
        log.warning("calibration_report failed: %s", e)
        return {"error": str(e), "period_days": days, "total_trades": 0}


def adaptive_thresholds(conn) -> dict:
    """Suggest threshold adjustments based on actual trade data.

    Logic:
        - If WR > 70% at current thresholds → lower slightly (more trades)
        - If WR < 50% → raise them (fewer, better trades)
        - If certain regimes consistently lose → auto-block them
        - If certain sessions consistently win → boost them

    Returns:
        sentiment_long: float (suggested)
        sentiment_short: float (suggested)
        confidence_floor: float (suggested)
        min_confirm_score: int (suggested)
        blocked_regimes: list[str]
        boosted_sessions: list[str]
        reasoning: str
    """
    try:
        report = calibration_report(conn, days=30)
        if report.get("total_trades", 0) < 10:
            # Paper book too thin — fall back to the REAL orders journal so the
            # live book (the one that actually pays) drives calibration too.
            report = _live_book_report(conn)
        if report.get("total_trades", 0) < 10:
            # Not enough data anywhere — return current config values
            return {
                "sentiment_long": config.AI_SENTIMENT_LONG,
                "sentiment_short": config.AI_SENTIMENT_SHORT,
                "confidence_floor": config.AI_CONFIDENCE_FLOOR,
                "min_confirm_score": config.AI_CONFIRM_MIN_SCORE,
                "blocked_regimes": [],
                "boosted_sessions": [],
                "reasoning": "insufficient_data (< 10 trades)",
                "adapted": False,
            }

        wr = report["win_rate"]
        current_sent_long = config.AI_SENTIMENT_LONG
        current_sent_short = config.AI_SENTIMENT_SHORT
        current_conf = config.AI_CONFIDENCE_FLOOR
        current_min_score = config.AI_CONFIRM_MIN_SCORE
        blocked = []
        boosted = []
        reasoning_parts = []

        # Adjust sentiment thresholds
        if wr > 0.70:
            # Winning well — can afford to be slightly more aggressive
            new_long = max(0.35, current_sent_long - 0.05)
            new_short = min(-0.35, current_sent_short + 0.05)
            reasoning_parts.append(f"WR={wr:.0%} > 70% → lowering sentiment thresholds")
        elif wr < 0.50:
            # Losing — tighten thresholds
            new_long = min(0.70, current_sent_long + 0.05)
            new_short = max(-0.70, current_sent_short - 0.05)
            reasoning_parts.append(f"WR={wr:.0%} < 50% → raising sentiment thresholds")
        else:
            new_long = current_sent_long
            new_short = current_sent_short
            reasoning_parts.append(f"WR={wr:.0%} → keeping current thresholds")

        # Adjust confidence floor
        if wr < 0.45:
            new_conf = min(0.85, current_conf + 0.05)
            reasoning_parts.append(f"raising confidence floor to {new_conf}")
        else:
            new_conf = current_conf

        # Check regime performance
        regime_data = report.get("by_factor", {}).get("regime", {})
        for regime, stats in regime_data.items():
            if stats["total"] >= 3 and stats["win_rate"] < 0.30:
                blocked.append(regime)
                reasoning_parts.append(f"blocking regime={regime} (WR={stats['win_rate']:.0%})")

        # Check session performance
        session_data = report.get("by_factor", {}).get("session", {})
        for session, stats in session_data.items():
            if stats["total"] >= 3 and stats["win_rate"] >= 0.70:
                boosted.append(session)
                reasoning_parts.append(f"boosting session={session} (WR={stats['win_rate']:.0%})")

        # Adjust confirm score
        score_data = report.get("by_factor", {}).get("confirm_score", {})
        high_score_wr = {}
        for score_str, stats in score_data.items():
            try:
                score_val = int(score_str)
            except (ValueError, TypeError):
                continue
            if stats["total"] >= 2:
                high_score_wr[score_val] = stats["win_rate"]

        # If higher scores consistently win more, raise the minimum
        if len(high_score_wr) >= 2:
            sorted_scores = sorted(high_score_wr.items())
            if all(sorted_scores[i][1] <= sorted_scores[i+1][1]
                   for i in range(len(sorted_scores)-1)):
                # Monotonically increasing WR with score → raise minimum
                new_min = min(5, current_min_score + 1)
                reasoning_parts.append(f"raising min_confirm_score to {new_min}")
            else:
                new_min = current_min_score
        else:
            new_min = current_min_score

        return {
            "sentiment_long": round(new_long, 2),
            "sentiment_short": round(new_short, 2),
            "confidence_floor": round(new_conf, 2),
            "min_confirm_score": new_min,
            "blocked_regimes": blocked,
            "boosted_sessions": boosted,
            "reasoning": "; ".join(reasoning_parts),
            "adapted": True,
        }
    except Exception as e:
        log.warning("adaptive_thresholds failed: %s", e)
        return {
            "sentiment_long": config.AI_SENTIMENT_LONG,
            "sentiment_short": config.AI_SENTIMENT_SHORT,
            "confidence_floor": config.AI_CONFIDENCE_FLOOR,
            "min_confirm_score": config.AI_CONFIRM_MIN_SCORE,
            "blocked_regimes": [],
            "boosted_sessions": [],
            "reasoning": f"error: {e}",
            "adapted": False,
        }


def _live_book_report(conn, days: int = 45) -> dict:
    """Same shape as calibration_report(), but built from the REAL orders
    journal instead of the paper test_trades. Excludes bookkeeping rows
    (account_retired) and breaks win rate down by trend_regime — the field
    that matters most (the 2026-09-28 post-mortem: with-trend 71% WR,
    counter-trend 17% WR)."""
    import json as _json
    import sqlite3
    try:
        cutoff = time.time() - days * 86400
        rows = conn.execute(
            """SELECT symbol, side, pnl, exit_reason, trend_regime, exit_time
               FROM orders
               WHERE pnl IS NOT NULL AND exit_reason != 'account_retired'
                 AND COALESCE(exit_time, created_at) >= ?""",
            (datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        # Pre-migration DB without trend_regime — degrade to pnl-only stats
        try:
            rows = conn.execute(
                """SELECT symbol, side, pnl, exit_reason, NULL AS trend_regime,
                          exit_time FROM orders
                   WHERE pnl IS NOT NULL AND exit_reason != 'account_retired'
                     AND COALESCE(exit_time, created_at) >= ?""",
                (datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),),
            ).fetchall()
        except Exception as e:
            log.warning("_live_book_report fallback failed: %s", e)
            return {"total_trades": 0, "period_days": days}
    except Exception as e:
        log.warning("_live_book_report failed: %s", e)
        return {"total_trades": 0, "period_days": days}

    trades = []
    for r in rows:
        try:
            pnl = float(r["pnl"] or 0)
        except (TypeError, ValueError):
            continue
        trades.append({
            "pnl": pnl,
            "outcome": "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven"),
            "regime": r["trend_regime"] or "unknown",
        })
    if not trades:
        return {"total_trades": 0, "period_days": days}

    total = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "win")
    by_regime = {}
    for t in trades:
        b = by_regime.setdefault(t["regime"], {"total": 0, "wins": 0, "pnl": 0.0})
        b["total"] += 1
        if t["outcome"] == "win":
            b["wins"] += 1
        b["pnl"] += t["pnl"]
    for v in by_regime.values():
        v["win_rate"] = round(v["wins"] / v["total"], 3) if v["total"] else 0.0
        v["pnl"] = round(v["pnl"], 2)
    return {
        "period_days": days,
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "breakevens": 0,
        "win_rate": round(wins / total, 3) if total else 0.0,
        "total_pnl": round(sum(t["pnl"] for t in trades), 2),
        "avg_pnl_per_trade": round(sum(t["pnl"] for t in trades) / total, 2),
        "by_factor": {"regime": by_regime},
    }
