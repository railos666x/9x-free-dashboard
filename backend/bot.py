"""
cryptoautomation — Bot orchestrator.

One bot cycle (`run_once`), matching the target architecture:

    data pipeline (SQLite candles)
        → verification (data-quality gate)
        → Source A (math engine signals) + Source B (proprietary signals)
        → arbitrator (agreement gate + intent-hash dedup)
        → verification (re-gate the intent against fresh data, incl. a
          second-price cross-check when Alpaca keys are configured)
        → execution (single broker path, journaled)
        → SL/TP monitor (manage_open_positions) closes hits

Run:
    python bot.py                 # one full cycle, real execution (paper)
    python bot.py --dry-run       # journal intents without touching a broker
    python bot.py --symbol BTC/USDT
    python bot.py loop [--dry-run]  # scheduled cycles every BOT_LOOP_SECONDS
"""

from __future__ import annotations

import sys
import time

# Windows consoles default to cp1252, which can't print the box-drawing / emoji
# used in summary output — switch stdout to UTF-8 (best-effort) so the loop
# log never crashes, on console or under a redirect.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
import database
import signals
import verification
from arbitrator import arbitrate, proposal_from_signal_dict, Proposal
import execution
import proprietary_signals
import market_data
import reviewer


def _strategy_proposals(conn) -> dict[str, Proposal]:
    """Source A via the validated mean-reversion v2 (strategy.compute_signal,
    imported, never edited) — full entry/SL/TP intents + filter diagnostics."""
    import indicators
    import strategy
    out = {}
    for symbol in config.SYMBOLS:
        try:
            df = indicators.add_all(
                database.load_candles(conn, symbol, "1h", limit=300))
            if df.empty or len(df) < 60:
                continue
            r = strategy.compute_signal(df)
            if r["direction"] not in ("LONG", "SHORT"):
                continue
            reasons = []
            if r.get("adx") is not None:
                reasons.append(f"ADX={r['adx']:.1f} "
                               f"({'ranging' if r['ranging'] else 'trending'})")
            if r.get("z") is not None:
                reasons.append(f"z={r['z']:.2f} (entry |z|≥2, SMA{strategy.DEFAULT_PARAMS.n})")
            reasons.append("strategy v2 mean-reversion (validated backtest)")
            out[symbol] = Proposal(
                symbol=symbol, direction=r["direction"], confidence=55.0,
                entry=r.get("entry"), stop_loss=r.get("stop_loss"),
                take_profit=r.get("take_profit"),
                source="strategy_v2", window="1h", reasons=reasons)
        except Exception:
            continue  # one bad symbol must not kill source A
    return out


def _math_engine_proposals(conn) -> dict[str, Proposal]:
    """Source A via the strict-calibrated math engine (default)."""
    all_signals = signals.generate_all_signals(conn)
    return {s["symbol"]: proposal_from_signal_dict(s, source="math_engine",
                                                    window="1h")
            for s in all_signals["signals"]}


def _hybrid_proposals(conn) -> dict[str, Proposal]:
    """Source A as a union of math_engine + strategy v2. Either generator can
    fire; when BOTH fire the same direction the stronger wins with reasons
    merged; opposing active opinions abstain (treated as HOLD, so they never
    conflict-trade). Gives source A more activity than either generator
    alone while staying conservative when the two disagree."""
    me = _math_engine_proposals(conn)
    st = _strategy_proposals(conn)
    out = {}
    for sym in set(me) | set(st):
        a, b = me.get(sym), st.get(sym)
        if a is None:
            out[sym] = b
        elif b is None:
            out[sym] = a
        elif a.direction == b.direction and a.direction in ("LONG", "SHORT"):
            # Both generators agree → keep the stronger, merge the reasons.
            p = a if a.confidence >= b.confidence else b
            other = b if p is a else a
            merged = list(p.reasons or [])
            for r in other.reasons or []:
                if r not in merged:
                    merged.append(r)
            p.reasons = merged
            out[sym] = p
        elif a.direction == "HOLD":
            out[sym] = b
        elif b.direction == "HOLD":
            out[sym] = a
        else:
            continue  # opposing active opinions → abstain
    return out


def _source_a_proposals(conn) -> dict[str, Proposal]:
    """Build source A's per-symbol proposals from the configured generator.

    "math_engine" (default): signals.py Bayesian fusion + factors.
    "strategy": the validated mean-reversion v2 (strategy.compute_signal,
    imported, never edited) — full entry/SL/TP intents + filter diagnostics.
    "hybrid": union of both (see _hybrid_proposals).
    """
    if config.SIGNAL_SOURCE_A == "strategy":
        return _strategy_proposals(conn)
    if config.SIGNAL_SOURCE_A == "hybrid":
        return _hybrid_proposals(conn)
    return _math_engine_proposals(conn)


def run_once(conn, symbols: list[str] | None = None,
             dry_run: bool = False, verbose: bool = False) -> dict:
    """One full bot cycle. Returns a summary dict."""
    t0 = time.time()
    summary = {"executed": [], "held": [], "errors": [], "closed": [],
               "dry_run": dry_run, "elapsed_s": 0.0, "blocked": False,
               "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Kill-switch: never trade live on a test-only (single-source) gate.
    if not dry_run and config.ARBITRATION_MIN_SOURCES < 2 \
            and not config.ALLOW_LIVE_SINGLE_SOURCE:
        summary["blocked"] = True
        summary["block_reason"] = (
            "ARBITRATION_MIN_SOURCES=1 (test gate) blocks LIVE runs — set "
            "ALLOW_LIVE_SINGLE_SOURCE=true in .env to override, or use "
            "ARBITRATION_MIN_SOURCES=2. Dry runs are always allowed.")
        return summary

    # Reconcile journal statuses with the broker (Alpaca) before deciding.
    # Broker-side closes (manual, or SL/TP placed outside the bot) are
    # journaled as broker_closed and counted in summary["closed"].
    if not dry_run:
        try:
            for u in execution.reconcile_orders(conn):
                if u.get("closed"):
                    summary["closed"].append({
                        "symbol": u["symbol"], "side": u.get("side"),
                        "reason": u.get("exit_reason", "broker_closed"),
                        "qty": u.get("qty"), "pnl": u.get("pnl")})
        except Exception as e:
            summary["errors"].append({"symbol": "*", "error": f"reconcile: {e}"})

    # ── Source A: configured generator (one call, all symbols) ────────────
    proposals_a = _source_a_proposals(conn)

    # ── Source B: proprietary signals (independent generator) ─────────────
    proposals_b = {p.symbol: p for p in proprietary_signals.generate_all_proposals(conn)}

    wanted = set(symbols or config.SYMBOLS)

    # Broker capability filters: skip assets the broker can't trade and
    # directions it can't take, instead of firing orders that 422/403.
    broker_spot = config.EXECUTION_BROKER in ("alpaca", "binance")
    try:
        supported = execution.broker_supported_symbols()
    except Exception:
        supported = None
    if supported is not None:
        unsupported = wanted - supported
        for sym in sorted(unsupported):
            summary["held"].append({"symbol": sym, "reason": "not_on_broker"})
        wanted &= supported

    for symbol in sorted(wanted):
        proposal_a = proposals_a.get(symbol)
        proposal_b = proposals_b.get(symbol)
        if proposal_a is None:
            # Source A has no opinion for this symbol (quiet by design).
            summary["held"].append({"symbol": symbol, "reason": "no_signal_a",
                                    "a": "HOLD",
                                    "b": proposal_b.direction if proposal_b else "off"})
            continue
        try:
            # Data gate BEFORE trusting either source.
            checks = verification.verify_candle_df(
                _load_frame(conn, symbol), interval="1h")
            if not checks["ok"]:
                summary["held"].append({"symbol": symbol, "reason": "data",
                                        "detail": checks["checks"]})
                continue

            # Agreement gate: both sources by default; a single high-
            # confidence source when ARBITRATION_MIN_SOURCES=1 (testing).
            intent = arbitrate(proposal_a, proposal_b,
                               min_sources=config.ARBITRATION_MIN_SOURCES,
                               min_confidence=config.ARBITRATION_MIN_CONFIDENCE)
            if intent is None:
                summary["held"].append({"symbol": symbol, "reason": "no_agreement",
                                        "a": proposal_a.direction,
                                        "b": proposal_b.direction if proposal_b else "off"})
                continue
            if broker_spot and intent.direction == "SHORT":
                # Spot brokers (Alpaca crypto, Binance spot) can't short —
                # selling what you don't own always 403s at the broker.
                summary["held"].append({"symbol": symbol, "reason": "no_short_on_spot"})
                continue

            # News verification (the confirmation layer): once the signal
            # sources agree, the news review checks the trade. Strongly
            # opposing headlines VETO it; an extreme score in EITHER
            # direction means a big move is expected, so the bot stands
            # aside instead of trading into the whipsaw. Aligned headlines
            # CONFIRM it (the intent is journaled with a "news confirms"
            # reason). Neutral / no news never blocks a trade.
            if config.NEWS_VERIFY_ENABLED:
                nv = reviewer.verify_trade(conn, symbol, intent.direction)
                # Journal the decision so the dashboard can show how often
                # the confirmation layer vetoes / confirms / stays neutral.
                database.record_news_verdict(
                    conn, symbol, intent.direction, nv.get("verdict"),
                    nv.get("score"), nv.get("big_move"), nv.get("mode"),
                    nv.get("message"))
                if nv.get("verdict") == "veto":
                    summary["held"].append({"symbol": symbol, "reason": "news_veto",
                                            "detail": nv})
                    continue
                if nv.get("verdict") == "confirm":
                    intent.reasons = list(intent.reasons or []) + [nv.get("message", "news confirms")]

            # Re-gate the intent right before execution (fresh data).
            v = verification.verify_intent(intent, conn, interval="1h")
            if not v["ok"]:
                summary["held"].append({"symbol": symbol, "reason": "intent",
                                        "detail": v["checks"]})
                continue

            # Cross-source check: if Alpaca keys are set, the independent
            # feed must roughly agree with our Binance price.
            alt_price = market_data.alpaca_last_price(symbol)
            if alt_price:
                df = _load_frame(conn, symbol)
                if not df.empty:
                    last = float(df["close"].iloc[-1])
                    if not verification.cross_source_ok(last, alt_price):
                        summary["held"].append({"symbol": symbol,
                                                "reason": "cross_source",
                                                "detail": {"binance": last,
                                                            "alpaca": alt_price}})
                        continue

            # Concurrent-position limit: don't stack more exposure than the
            # account can fund when a burst of signals agrees at once.
            if not dry_run and config.MAX_OPEN_POSITIONS > 0:
                open_now = len([o for o in database.load_orders(conn, limit=1000)
                                if o.get("status") in ("filled", "open")])
                if open_now >= config.MAX_OPEN_POSITIONS:
                    summary["held"].append({"symbol": symbol,
                                            "reason": "max_positions"})
                    continue

            result = execution.execute_intent(conn, intent, dry_run=dry_run)
            if result.get("duplicate"):
                summary["held"].append({"symbol": symbol, "reason": "duplicate"})
                continue
            order = result.get("order") or {}
            status = order.get("status", "?")
            if status == "error":
                summary["errors"].append({"symbol": symbol,
                                          "error": order.get("error")})
            else:
                summary["executed"].append({"symbol": symbol,
                                            "direction": intent.direction,
                                            "qty": order.get("qty"),
                                            "status": status,
                                            "sources": intent.sources,
                                            "reasons": intent.reasons or []})
            if verbose:
                print(f"  {symbol}: {intent.direction} qty={order.get('qty')} "
                      f"status={status} src={intent.sources}")
        except Exception as e:
            summary["errors"].append({"symbol": symbol, "error": str(e)})

    # ── SL/TP monitor: close any open position that hit its levels ──────
    if not dry_run:
        summary["closed"] = execution.manage_open_positions(conn)

    summary["elapsed_s"] = round(time.time() - t0, 2)
    return summary


def bot_loop(conn, dry_run: bool = True, interval_seconds: int | None = None,
             stop_event=None, on_cycle=None) -> None:
    """Scheduled bot: run a cycle every BOT_LOOP_SECONDS until stopped.

    `stop_event` (threading.Event) lets the server stop the loop cleanly
    after the current cycle — the dashboard's Auto-loop Stop button.
    `on_cycle` (callable) receives every cycle summary, so the server can
    surface auto-loop cycles in /api/bot/status (the dashboard's held-
    reasons panel reads them).
    """
    interval_seconds = interval_seconds or config.BOT_LOOP_SECONDS
    print(f"Bot loop started — one cycle every {interval_seconds}s "
          f"({'dry-run' if dry_run else 'LIVE'}). Ctrl+C to stop.")
    while True:
        if stop_event is not None and stop_event.is_set():
            print("  Bot loop stopped.")
            return
        try:
            summary = run_once(conn, dry_run=dry_run, verbose=False)
            print_summary(summary)
            if on_cycle is not None:
                try:
                    on_cycle(summary)
                except Exception:
                    pass  # a listener must never break the loop
        except Exception as e:
            print(f"  bot cycle error: {e}")
        if stop_event is not None and stop_event.wait(interval_seconds):
            print("  Bot loop stopped.")
            return


def _load_frame(conn, symbol: str):
    import indicators
    return indicators.add_all(database.load_candles(conn, symbol, "1h", limit=200))


def print_summary(summary: dict) -> None:
    if summary.get("blocked"):
        print("┌─ bot cycle BLOCKED")
        print(f"│  🛑 {summary.get('block_reason', 'no reason')}")
        print("└─ done")
        return
    n = len(summary["executed"])
    print(f"┌─ bot cycle ({'dry-run' if summary['dry_run'] else 'live'}): "
          f"{n} executed, {len(summary['held'])} held, "
          f"{len(summary['errors'])} errors, {summary['elapsed_s']}s")
    for e in summary["executed"][:10]:
        print(f"│  ✅ {e['symbol']} {e['direction']} qty={e['qty']:.6f} "
              f"({e['status']}) via {e['sources']}")
    for h in summary["held"][:5]:
        print(f"│  ⏸  {h['symbol']} held: {h['reason']}")
    for err in summary["errors"][:5]:
        print(f"│  ❌ {err['symbol']}: {err['error']}")
    print("└─ done")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    loop_mode = "loop" in args
    # Default is DRY-RUN for safety: only `--live` turns real orders on.
    dry_run = "--live" not in args
    verbose = "-v" in args or "--verbose" in args
    symbol = None
    for a in args:
        if a.startswith("--symbol="):
            symbol = a.split("=", 1)[1]
    conn = database.connect()
    database.init_db(conn)
    if loop_mode:
        bot_loop(conn, dry_run=dry_run)
        return
    summary = run_once(conn, symbols=[symbol] if symbol else None,
                       dry_run=dry_run, verbose=verbose)
    print_summary(summary)


if __name__ == "__main__":
    main()
