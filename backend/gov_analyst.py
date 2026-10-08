"""Government-officials shadow analyst (PROTOTYPE, 2026-10-05).

Runs the OFFICIAL release pool (gov_news table — Fed/FOMC/BoC/BoE/ECB/SEC/
CFTC) through the SAME pipeline the live ai_news path uses:

    1. news_noise.dedupe_headlines        — wire-copy dedup
    2. news_analyst.analyze_headlines     — the existing Groq scorer
    3. noise gate (corroboration_ok)      — distinct-outlet gate
    4. test_ai.record_paper_trade         — PAPER-ONLY shadow journal
    5. expiry pass                        — stale shadow rows close at last
                                            price ('shadow_expired') so the
                                            book never accumulates zombies

Nothing here touches the `orders` table or any broker: the two-week
evaluation journals proposed trades into test_trades (ai_model='groq-gov')
and a separate success criterion — the Groq call today receives ONLY gov
headlines, and its sentiment decides direction exactly as the live path's
does. The module NEVER raises upward: any failure is logged and the cycle
continues.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import database
import news_noise

log = logging.getLogger(__name__)

ASSET_TO_SYMBOL = {
    "BTC": "BTC/USDT", "BITCOIN": "BTC/USDT",
    "ETH": "ETH/USDT", "ETHEREUM": "ETH/USDT",
    "SOL": "SOL/USDT",
    "XRP": "XRP/USDT",
    "USD": "EUR/USD",        # dollar policy → EUR/USD as the traded proxy
    "DOLLAR": "EUR/USD",
    "EUR": "EUR/USD",
    "JPY": "USD/JPY",
    "YEN": "USD/JPY",
    "GBP": "GBP/USD",
    "GOLD": "XAU/USD",
    "SILVER": "XAG/USD",
    "OIL": "WTI/USD",
}


def gov_headlines(conn, limit: int = 25) -> list[dict]:
    """Official releases in the SAME dict shape news_analyst.analyze_headlines
    consumes (title / tag / source / published_at / age_hours), after wire
    dedup. No candles-table or live-pool leakage."""
    rows = database.load_gov_news(conn, limit=max(limit * 2, 60))
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        pub = str(r.get("published_ts") or r.get("published_at") or "")
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (now - dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            age = None
        if age is not None and (age > config.NEWS_MAX_AGE_HOURS or age < -1):
            # Same freshness rule as the live pool, plus a future-date guard:
            # some official feeds pre-schedule notices (BoC holidays,
            # conference announcements dated 2027) that would otherwise
            # linger in the pool for weeks.
            continue
        out.append({
            "title": str(r.get("title") or ""),
            "tag": str(r.get("sentiment") or "neutral"),
            "source": str(r.get("source") or ""),
            "published_at": str(r.get("published_at") or "")[:16],
            "age_hours": age,
        })
    if config.NEWS_NOISE_FILTER_ENABLED:
        out, _n = news_noise.dedupe_headlines(out)
    return out[:limit]


def score_official_pool(conn):
    """The existing Groq scorer over ONLY official headlines.
    Returns the analyst dict ({assets: [...]}) or None."""
    import news_analyst
    try:
        heads = gov_headlines(conn)
        if not heads:
            return None
        return news_analyst.analyze_headlines(heads)
    except Exception as e:
        log.warning("gov score failed: %s", e)
        return None


def noise_gate_ok(conn, symbol: str, heads: list[dict]) -> tuple[bool, str]:
    """Same distinct-outlet corroboration gate the live path uses
    (news_noise.corroboration_ok over reviewer.COIN_HINTS keywords)."""
    import news_analyst
    from reviewer import COIN_HINTS
    hints = COIN_HINTS.get(symbol)
    if not hints or not config.NEWS_NOISE_FILTER_ENABLED:
        return True, "no_hints_or_gate_off"
    try:
        stats = news_noise.corroboration_stats(heads, hints)
        return news_noise.corroboration_ok(
            stats, config.NEWS_MIN_DISTINCT_OUTLETS,
            config.NEWS_SINGLE_OUTLET_MIN_SCORE)
    except Exception as e:
        return True, f"gate_error_failopen: {e}"


def _direction_from_score(score: float, confidence: float) -> str:
    """Same thresholds as the live path (AI_SENTIMENT_*_PCT /
    AI_CONFIDENCE_FLOOR) applied to the official-pool sentiment."""
    if score >= config.AI_SENTIMENT_LONG and confidence >= config.AI_CONFIDENCE_FLOOR:
        return "LONG"
    if score <= config.AI_SENTIMENT_SHORT and confidence >= config.AI_CONFIDENCE_FLOOR:
        return "SHORT"
    return "HOLD"


def run_shadow_cycle(conn) -> list[dict]:
    """One shadow pass: fetch-free (gov_news is already populated), score,
    gate, journal paper trades, expire stale rows. Returns summaries."""
    out: list[dict] = []
    result = score_official_pool(conn)
    if not result or not result.get("assets"):
        _expire_stale(conn)
        return out
    heads = gov_headlines(conn)
    for asset in result["assets"]:
        try:
            sym = ASSET_TO_SYMBOL.get(str(asset.get("asset", "")).upper())
            if not sym:
                continue
            score = float(asset.get("sentiment_score") or 0.0)
            conf = float(asset.get("confidence") or 0.0)
            direction = _direction_from_score(score, conf)
            if direction == "HOLD":
                continue
            ok, why = noise_gate_ok(conn, sym, heads)
            if not ok:
                out.append({"symbol": sym, "direction": direction,
                            "gated": why})
                continue
            trade = _journal_shadow(conn, sym, direction, score, conf, asset)
            if trade:
                out.append(trade)
        except Exception as e:
            log.warning("gov shadow row failed: %s", e)
    _expire_stale(conn)
    return out


def _journal_shadow(conn, symbol: str, direction: str, score: float,
                    conf: float, asset: dict):
    """Paper-journal one proposal via test_ai.record_paper_trade
    (ai_model='groq-gov' so the shadow book is separable) with an explicit
    last-candle price and 1.5×ATR stop / 3×ATR target.
    """
    import test_ai
    df = database.load_candles(conn, symbol, "1h", limit=3)
    if df.empty:
        return None
    entry = float(df["close"].iloc[-1])
    atr = _atr_1h(conn, symbol) or entry * 0.01
    sign = 1 if direction == "LONG" else -1
    stop = entry - sign * 1.5 * atr
    tp = entry + sign * 3.0 * atr

    class _P:  # minimal proposal stand-in with the attrs test_ai reads
        confidence = round(max(30.0, min(75.0, conf * 100.0)), 1)
        confirm_score = 0
        regime = None
        dxy_score = 0
        smc_score = 0
        session = None

    _sym, _dir = symbol, direction  # class bodies can't close over locals

    class _I:
        symbol = _sym
        direction = _dir
        reasons = [f"gov-shadow {direction}: {asset.get('summary', '')[:120]} "
                   f"(official-pool score {score:+.2f}, conf {conf:.2f})"]

    res = test_ai.record_paper_trade(conn, _P(), _I(), dry_run=False,
                                     ai_model="groq-gov")
    if res.get("trade") and res["trade"].get("id") and not res.get("duplicate"):
        tid = res["trade"]["id"]
        # Stamp our explicit levels (record_paper_trade computed its own
        # ATR levels; ours carry the 2R shape we want for the evaluation).
        conn.execute(
            "UPDATE test_trades SET entry_price = ?, stop_loss = ?, "
            "take_profit = ?, sentiment = ? WHERE id = ?",
            (round(entry, 8), round(stop, 8), round(tp, 8),
             round(score, 4), tid))
        conn.commit()
        return {"symbol": symbol, "direction": direction, "shadow_id": tid,
                "entry": round(entry, 8), "paper": True}
    if res.get("duplicate"):
        return {"symbol": symbol, "direction": direction, "duplicate": True}
    return None


def _atr_1h(conn, symbol: str) -> float | None:
    """ATR(14) on 1h candles via the same indicator module the bot uses."""
    try:
        import indicators
        df = database.load_candles(conn, symbol, "1h", limit=60)
        if df.empty or len(df) < 15:
            return None
        atr = indicators.atr(df, period=14)
        return float(atr.iloc[-1]) if atr is not None and len(atr) else None
    except Exception:
        return None


def _expire_stale(conn) -> list[int]:
    """Close open shadow rows older than GOV_SHADOW_MAX_HOLD_BARS at the
    last 1h close ('shadow_expired') — the shadow book stays clean."""
    closed_ids: list[int] = []
    cut = (datetime.now(timezone.utc)
           - timedelta(hours=config.GOV_SHADOW_MAX_HOLD_BARS)).isoformat()
    for t in database.load_test_trades(conn, limit=200, status="open"):
        if str(t.get("ai_model") or "") != "groq-gov":
            continue  # only manage the shadow book
        if str(t.get("ran_at") or "") >= cut:
            continue
        df = database.load_candles(conn, t["symbol"], "1h", limit=3)
        if df.empty:
            continue
        px = float(df["close"].iloc[-1])
        entry = float(t.get("entry_price") or 0)
        if entry <= 0:
            continue
        qty = 1.0
        pnl = ((px - entry) if str(t["direction"]).upper() == "LONG"
               else (entry - px)) * qty
        database.update_test_trade_exit(conn, t["id"], exit_price=round(px, 8),
                                        reason="shadow_expired",
                                        pnl=round(pnl, 8))
        closed_ids.append(t["id"])
    return closed_ids
