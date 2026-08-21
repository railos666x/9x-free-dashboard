"""
cryptoautomation — Economic Calendar Guard (news event filter).

Blocks trading around high-impact economic events (NFP, FOMC, CPI, GDP)
when spreads widen and slippage spikes. Based on prop firm news filters.

High-impact events cause:
    - Spread widening (2-10x normal)
    - Slippage on market orders
    - Whipsaw price action (stop hunts)
    - Liquidity vacuum (market makers pull quotes)

The guard uses a built-in calendar of recurring high-impact events
(estimated dates) plus a spread-based safety check that catches any
unexpected volatility event.

The module NEVER raises — on any failure it returns permissive results.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta

import config
import database

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Recurring high-impact events (approximate schedule)
# ----------------------------------------------------------------------
# These are the events that consistently cause spread widening and slippage.
# Exact dates vary monthly; we use rules to estimate them.
#
# NFP: First Friday of each month, 13:30 UTC
# FOMC: ~8 times/year, Wednesday 19:00 UTC (check Federal Reserve calendar)
# CPI: ~10th-15th of each month, 13:30 UTC
# GDP: End of month (advance estimate), 13:30 UTC
# ECB: Thursday 12:15 UTC (rate decisions)
# BOJ: Variable, usually overnight
# UK Employment: Second Thursday, 07:00 UTC

# High-impact event keywords (used to match against news headlines too)
# These are split into tiers:
#   - "always_block": unambiguous event names that block on their own
#   - "central_banks": require a co-occurring action word (decision, rate, etc.)
#     to avoid blocking on casual mentions in commentary
HIGH_IMPACT_ALWAYS_KW = [
    # Only truly unambiguous multi-word phrases that NEVER appear in
    # commentary. Single words like "cpi" or "fomc" appear in speculation
    # headlines ("markets expect…") and must NOT block trading.
    "nonfarm payrolls", "non-farm payrolls",
    "fomc statement", "fomc meeting",
    "powell speaks", "powell testifies", "powell press",
    "consumer price index", "cpi data", "cpi report",
    "gdp report", "gross domestic product",
    "jobs report", "employment situation",
]
HIGH_IMPACT_BANK_KW = [
    "ecb", "boe", "boj", "bank of england", "bank of japan",
    "federal reserve", "the fed",
]
# Must appear alongside a bank keyword to trigger a block.
# These are actual decision/action phrases, not expectations.
BANK_ACTION_KW = [
    "rate decision", "decides rate", "holds rate", "hold rates",
    "rates held", "hikes rate", "cuts rate",
    "hikes rates", "cuts rates", "unexpected hike", "unexpected cut",
    "tightens policy", "eases policy", "dissent", "unanimous vote",
    "policy minutes", "meeting minutes",
]

# Medium-impact keywords (also need word-boundary matching)
MEDIUM_IMPACT_KEYWORDS = [
    "pce", "personal consumption",
    "initial claims", "jobless claims",
    "housing", "building permits", "new home sales",
    "durable goods", "factory orders",
    "consumer confidence", "beige book",
]


def _is_high_impact_event(text: str) -> bool:
    """Check if a news headline mentions a high-impact event.

    Two-tier matching:
      1. Unambiguous keywords (nfp, fomc, payrolls) block immediately.
      2. Central-bank names (boe, boj, ecb) only block when paired with
         an action word (rate decision, hike, cut, etc.) — casual mentions
         in commentary should NOT trigger a block.
    """
    import re
    text_lower = text.lower()
    # Tier 1: unambiguous — word-boundary match on always-block keywords
    for kw in HIGH_IMPACT_ALWAYS_KW:
        if re.search(rf'\b{re.escape(kw)}\b', text_lower):
            return True
    # Tier 2: central bank + action word required
    bank_hit = any(re.search(rf'\b{re.escape(bk)}\b', text_lower)
                   for bk in HIGH_IMPACT_BANK_KW)
    if bank_hit:
        action_hit = any(ak in text_lower for ak in BANK_ACTION_KW)
        if action_hit:
            return True
    return False


def _is_medium_impact_event(text: str) -> bool:
    """Check if a news headline mentions a medium-impact event."""
    import re
    text_lower = text.lower()
    return any(re.search(rf'\b{re.escape(kw)}\b', text_lower)
               for kw in MEDIUM_IMPACT_KEYWORDS)


def _estimate_nfp_date(year: int, month: int) -> datetime:
    """Estimate NFP release: first Friday of the month, 13:30 UTC."""
    first_day = datetime(year, month, 1, 13, 30, tzinfo=timezone.utc)
    # Find first Friday
    days_ahead = (4 - first_day.weekday()) % 7  # Friday = 4
    if days_ahead == 0 and first_day.day == 1:
        pass  # first day IS Friday
    else:
        first_day += timedelta(days=days_ahead)
    return first_day


def _estimate_cpi_date(year: int, month: int) -> datetime:
    """Estimate CPI release: ~10th-14th of the month, 13:30 UTC."""
    # Approximate: second Wednesday of the month
    first_day = datetime(year, month, 10, 13, 30, tzinfo=timezone.utc)
    return first_day


def _upcoming_recurring_events(now: datetime, days_ahead: int = 3) -> list[dict]:
    """Estimate upcoming recurring high-impact events within the window."""
    events = []
    for d in range(days_ahead + 1):
        check_date = now + timedelta(days=d)
        year, month = check_date.year, check_date.month

        # NFP: first Friday
        nfp = _estimate_nfp_date(year, month)
        if abs((check_date.date() - nfp.date()).days) == 0:
            events.append({"event": "NFP (estimated)", "time": nfp,
                           "impact": "high", "currency": "USD"})

        # CPI: ~10th-14th
        cpi = _estimate_cpi_date(year, month)
        if abs((check_date.date() - cpi.date()).days) == 0:
            events.append({"event": "CPI (estimated)", "time": cpi,
                           "impact": "high", "currency": "USD"})

        # FOMC: check if it's a Wednesday and roughly every 6 weeks
        # Simplified: just flag all Wednesdays in FOMC months
        if check_date.weekday() == 2:  # Wednesday
            # FOMC meets roughly every 6 weeks; flag if within typical range
            # This is a rough estimate — real calendar would be fetched from API
            pass  # Too many false positives without real calendar

    return events


def is_in_restricted_window(conn=None, symbol: str = None) -> dict:
    """Check if current time is within a restricted trading window.

    Returns:
        restricted: bool
        event: str (event name or None)
        minutes_until: int (minutes until event, or -1 if not restricted)
        impact: "high" | "medium" | None
        reason: str (human-readable)
    """
    if not config.CALENDAR_GUARD_ENABLED:
        return {"restricted": False, "event": None, "minutes_until": -1,
                "impact": None, "reason": "calendar_guard_disabled"}

    now = datetime.now(timezone.utc)

    # Check 1: Scan recent news headlines for high-impact event mentions
    # (catches real events even without a calendar API)
    if conn is not None:
        try:
            df = database.load_news(conn, limit=30)
            if df is not None and len(df) > 0:
                for _, row in df.iterrows():
                    title = str(row.get("title", ""))
                    pub = str(row.get("published_at", ""))

                    if _is_high_impact_event(title):
                        # Check if this headline is recent (within 2 hours)
                        try:
                            pub_dt = datetime.fromisoformat(
                                pub.replace("Z", "+00:00"))
                            age_hours = (now - pub_dt).total_seconds() / 3600
                            if age_hours < 2.0:
                                return {
                                    "restricted": True,
                                    "event": title[:80],
                                    "minutes_until": 0,
                                    "impact": "high",
                                    "reason": (f"High-impact event detected: "
                                               f"'{title[:60]}' ({age_hours:.1f}h ago)")
                                }
                        except (ValueError, TypeError):
                            pass

                    if _is_medium_impact_event(title):
                        try:
                            pub_dt = datetime.fromisoformat(
                                pub.replace("Z", "+00:00"))
                            age_hours = (now - pub_dt).total_seconds() / 3600
                            if age_hours < 1.0:
                                return {
                                    "restricted": True,
                                    "event": title[:80],
                                    "minutes_until": 0,
                                    "impact": "medium",
                                    "reason": (f"Medium-impact event: "
                                               f"'{title[:60]}' ({age_hours:.1f}h ago)")
                                }
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            log.warning("Calendar guard news scan failed: %s", e)

    # Check 2: Recurring event estimates
    upcoming = _upcoming_recurring_events(now, days_ahead=0)
    for ev in upcoming:
        ev_time = ev["time"]
        diff_min = (ev_time - now).total_seconds() / 60

        if ev["impact"] == "high":
            block_min = config.CALENDAR_HIGH_IMPACT_BLOCK_MIN
        else:
            block_min = config.CALENDAR_MEDIUM_IMPACT_BLOCK_MIN

        # Block from `block_min` before to `block_min/2` after
        if -block_min / 2 <= diff_min <= block_min:
            return {
                "restricted": True,
                "event": ev["event"],
                "minutes_until": max(0, int(diff_min)),
                "impact": ev["impact"],
                "reason": (f"{ev['event']} in {int(diff_min)}min — "
                           f"blocking {block_min}min around event")
            }

    return {"restricted": False, "event": None, "minutes_until": -1,
            "impact": None, "reason": "no_events_detected"}


def get_spread_warning(conn, symbol: str) -> dict:
    """Check current spread vs average for a symbol.

    Uses the latest candle's high-low range as a proxy for spread
    (real spread data requires broker-specific API calls).

    Returns:
        warning: bool (spread > 2x average)
        block: bool (spread > 3x average)
        multiplier: float (current/average ratio)
    """
    try:
        df = database.load_candles(conn, symbol, "1h", limit=50)
        if df is None or len(df) < 10:
            return {"warning": False, "block": False, "multiplier": 1.0}

        # Use high-low range of the latest candle as spread proxy
        latest_range = float(df["high"].iloc[-1] - df["low"].iloc[-1])
        avg_range = float((df["high"] - df["low"]).iloc[-20:].mean())

        if avg_range <= 0:
            return {"warning": False, "block": False, "multiplier": 1.0}

        mult = latest_range / avg_range

        return {
            "warning": mult >= config.SPREAD_MULTIPLIER_WARN,
            "block": mult >= config.SPREAD_MULTIPLIER_BLOCK,
            "multiplier": round(mult, 2),
        }
    except Exception as e:
        log.warning("get_spread_warning(%s) failed: %s", symbol, e)
        return {"warning": False, "block": False, "multiplier": 1.0}


def calendar_allows_trade(conn, symbol: str) -> dict:
    """Master calendar guard function.

    Combines:
        1. Restricted window check (news events)
        2. Spread warning (volatility detection)

    Returns:
        allowed: bool
        restricted: bool
        spread_warning: bool
        event: str or None
        reasons: list[str]
    """
    if not config.CALENDAR_GUARD_ENABLED:
        return {"allowed": True, "restricted": False,
                "spread_warning": False, "event": None,
                "reasons": ["calendar_guard_disabled"]}

    reasons = []

    # Check 1: Restricted window
    rw = is_in_restricted_window(conn, symbol)
    if rw["restricted"]:
        reasons.append(f"BLOCKED: {rw['reason']}")
        return {"allowed": False, "restricted": True,
                "spread_warning": False, "event": rw.get("event"),
                "reasons": reasons}

    # Check 2: Spread warning
    sw = get_spread_warning(conn, symbol)
    if sw["block"]:
        reasons.append(f"BLOCKED: spread {sw['multiplier']}x normal")
        return {"allowed": False, "restricted": False,
                "spread_warning": True, "event": None,
                "reasons": reasons}
    elif sw["warning"]:
        reasons.append(f"WARNING: spread {sw['multiplier']}x normal")

    reasons.append("calendar_clear")
    return {"allowed": True, "restricted": False,
            "spread_warning": sw["warning"], "event": None,
            "reasons": reasons}
