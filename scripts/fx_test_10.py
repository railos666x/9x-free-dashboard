#!/usr/bin/env python3
"""Take 10 REAL forex trades through the live pipeline (no backtest).

Drives bot.run_once() over the FX universe — the exact news -> 6-layer AI
pipeline -> dual gate -> execute_intent path the server loop uses.

Why the FX-news injection: the always-on wire is crypto/politics-heavy, so
the LLM honestly scores 0.00 FX relevance and every pair correctly HOLDs.
A test round needs the sleeve to actually trade, so we first run the
project's own Tavily collector with an FX-targeted query and save those
REAL current stories into the news table (source labeled Tavily-FX-test).
No fake data is ever created — if Tavily returns nothing, we trade nothing.

Driver-level, in-process relaxations (documented in the changelog; .env and
code untouched, restored when this process exits):
  - config.MAX_OPEN_POSITIONS 5 -> 25 (book already holds 11 rows)
  - config.MAX_PER_CURRENCY 3 -> 8 (one macro narrative = several pairs)
  - config.AI_SENTIMENT_LONG/SHORT 0.40 -> 0.15 / -0.15,
    AI_CONFIDENCE_FLOOR 0.65 -> 0.35 (test-round sensitivity; corroboration,
    regime veto, DXY filter, session sizing and the R/R floor stay active)

Run:  .venv/Scripts/python.exe scripts/fx_test_10.py
"""
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config  # noqa: E402
import bot  # noqa: E402
import database  # noqa: E402
import news  # noqa: E402
import news_analyst  # noqa: E402
import forex as fx  # noqa: E402

TARGET_FILLS = 10
WAVES = 6

FX_QUERY = (
    "forex market today: US dollar euro yen pound aussie kiwi loonie franc, "
    "central bank Fed ECB BoJ BoE rate decision comments, inflation CPI jobs "
    "report, risk sentiment, currency outlook"
)


def inject_fx_news(conn) -> int:
    """One real Tavily search with an FX query, saved into the news table."""
    config.TAVILY_NEWS_QUERY = FX_QUERY
    config.TAVILY_NEWS_DAYS = 2
    news._tavily_last_fetch = 0.0  # reset the per-process throttle
    items = news.fetch_tavily_news(conn)
    if items:
        # Tavily returns "Tavily" as source for every result — but each item
        # HAS a real URL. Derive the outlet from the URL domain (reuters.com,
        # cnbc.com, ...) so the corroboration gate counts REAL distinct
        # outlets instead of one aggregator label.
        from urllib.parse import urlparse
        for it in items:
            host = urlparse(it.get("url") or "").netloc.lower()
            host = host[4:] if host.startswith("www.") else host
            it["source"] = host or "Tavily"
        added = database.save_news(conn, items)
        conn.commit()
        outlets = sorted({it.get("source") for it in items})
        print(f"[FX-news] saved {added} fresh FX stories from {len(outlets)} "
              f"outlets: {outlets[:6]}", flush=True)
        return added
    print("[FX-news] Tavily returned nothing — pipeline stays honest",
          flush=True)
    return 0


def main() -> int:
    conn = database.connect()
    fx_list = "','".join(fx.FX_PAIRS)
    baseline = conn.execute(
        f"SELECT COUNT(*) AS n FROM orders WHERE status IN ('filled','open') "
        f"AND symbol IN ('{fx_list}')"
    ).fetchone()["n"]
    print(f"baseline open FX rows: {baseline}", flush=True)

    # In-process cap relaxation (see module docstring).
    config.MAX_OPEN_POSITIONS = 25
    config.MAX_PER_CURRENCY = 8
    # In-process signal relaxation for this TEST round.
    config.AI_SENTIMENT_LONG = 0.15
    config.AI_SENTIMENT_SHORT = -0.15
    config.AI_CONFIDENCE_FLOOR = 0.35

    fills = 0
    held_log = []
    for wave in range(1, WAVES + 1):
        if fills >= TARGET_FILLS:
            break
        print(f"\n=== wave {wave}/{WAVES} ===", flush=True)
        if wave == 1:
            inject_fx_news(conn)
        # Reset the analyst cache so each wave re-scores the newest wire.
        news_analyst._market_cache.update({"at": 0.0, "data": None})
        news_analyst._cooldown_until = 0.0
        try:
            # Full FX list per wave: one news fetch + LLM pass scores all
            # pairs (per-symbol calls re-fetch the wire and are far slower).
            summary = bot.run_once(conn, symbols=list(fx.FX_PAIRS),
                                   dry_run=False, verbose=True)
        except Exception as e:
            print(f"  wave failed: {type(e).__name__}: {e}", flush=True)
            continue
        got = [e for e in (summary.get("executed") or [])
               if e.get("status") == "filled"]
        fills += len(got)
        for e in got:
            print(f"  [FILL #{fills}] {e.get('symbol')} {e.get('direction')} "
                  f"qty={e.get('qty')} tc={e.get('confirm_score')} "
                  f"dxy={e.get('dxy_score')} smc={e.get('smc_score')} "
                  f"regime={e.get('regime')} session={e.get('session')}",
                  flush=True)
        for h in (summary.get("held") or []):
            if h.get("reason") != "duplicate":
                held_log.append((h.get("symbol"), h.get("reason")))
        for err in (summary.get("errors") or []):
            print(f"  [ERR] {err.get('symbol')}: {str(err.get('error'))[:120]}",
                  flush=True)
        print(f"  wave {wave}: filled={len(got)} held={len(summary.get('held') or [])} "
              f"total_fills={fills}", flush=True)
        if fills < TARGET_FILLS and wave < WAVES:
            time.sleep(30)  # let fresh stories accumulate between waves

    print(f"\n=== done: {fills} new fills (target {TARGET_FILLS}) ===",
          flush=True)

    rows = conn.execute(
        f"""SELECT id, symbol, side, qty, fill_price, stop_loss, take_profit,
                   trend_regime, reasons
            FROM orders WHERE status IN ('filled','open')
            AND symbol IN ('{fx_list}') ORDER BY id DESC LIMIT {max(fills, 1)}"""
    ).fetchall()
    print(f"\nnewest {len(rows)} open FX journal rows:", flush=True)
    for r in rows:
        reasons = (r["reasons"] or "")[:110]
        print(f"  #{r['id']} {r['symbol']} {r['side']} qty={r['qty']} "
              f"@{r['fill_price']} sl={r['stop_loss']} tp={r['take_profit']} "
              f"regime={r['trend_regime']}")
        print(f"        {reasons}")

    if held_log:
        print("\nheld reasons seen:")
        for reason, n in Counter(r for _, r in held_log).most_common():
            print(f"  {reason}: {n}")

    conn.close()
    return 0 if fills >= TARGET_FILLS else 1


if __name__ == "__main__":
    sys.exit(main())
