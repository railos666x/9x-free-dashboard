"""
cryptoautomation — Bot orchestrator.

One bot cycle (`run_once`), matching the AI/news architecture (since
2026-08-17 — the old candle-factor sources are retired from trading):

    data pipeline (SQLite candles + news feed)
        -> verification (data-quality gate)
        -> AI/news source: the Groq news analyst (news_analyst.signal_for)
          6-layer confirmation pipeline:
            1. Raw sentiment  2. Freshness  3. Calendar guard
            4. AI confirm (regime+technical+session)
            5. DXY master filter  6. Smart money confluence
          the chart-confluence agent (ai_agents.gate_news_trade) verifies
          each LONG/SHORT against price structure when AI_CONFLUENCE_GATE
          is on
        -> circuit breaker (tiered step-down: 5%/10%/15% DD)
        -> arbitrator (single-source intent + intent-hash dedup)
        -> verification (re-gate the intent against fresh data)
        -> dual-mode execution:
          AI_MODE=live -> real MT5/Exness trades via execution.py
          AI_MODE=test -> paper trades in test_trades table (test_ai.py)
        -> SL/TP monitor (manage_open_positions) closes hits

The bot trades ONLY config.TRADING_UNIVERSE -- the Exness forex / metals /
energy / indices instruments on MT5.

Run:
    python bot.py                 # one full cycle, real execution (paper)
    python bot.py --dry-run       # journal intents without touching a broker
    python bot.py --symbol EUR/USD
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
import verification
from arbitrator import arbitrate, Proposal
import execution
import market_data

# Token registry for unified symbol management
try:
    from . import token_registry
except ImportError:
    import token_registry


def _ai_news_proposals(conn, symbols: list[str]) -> dict[str, Proposal]:
    """The ONLY trade source since 2026-08-17: the Groq news analyst.

    news_analyst.signal_for(conn, symbol) runs the full 6-layer pipeline:
        1. Raw sentiment  2. Freshness  3. Calendar guard
        4. AI confirm (regime+technical+session)
        5. DXY master filter  6. Smart money confluence
    and returns LONG / SHORT / HOLD with all confirmation metadata.

    When AI_CONFLUENCE_GATE is on, agent 3 (ai_agents.gate_news_trade)
    verifies each LONG/SHORT against the live chart structure — DIVERGENCE
    / TRAP setups are dropped before they become proposals.

    Entry/SL/TP levels are filled by the executor (live price entry, ATR
    stops) — or agent 3's calculated stop when the gate supplied one.
    Returns {} when no LLM transport is configured (the bot then simply
    holds everything; nothing else generates trades anymore)."""
    if not config.NEWS_ANALYST_ENABLED or not config.llm_available():
        return {}
    import news_analyst
    import ai_agents

    out = {}
    for symbol in symbols:
        try:
            s = news_analyst.signal_for(conn, symbol)
        except Exception:
            continue  # one bad symbol must not kill the source
        if s.get("action") not in ("LONG", "SHORT"):
            continue
        # Chart-confluence gate (agent 3) — opt-in, see config.
        gate = ai_agents.gate_news_trade(conn, symbol, s["action"])
        if not gate["pass"]:
            continue  # chart invalidates the news signal — drop the proposal
        stop_loss = None
        verdict = gate.get("verdict") or {}
        if verdict.get("calculated_stop_loss"):
            stop_loss = float(verdict["calculated_stop_loss"])
        why = [f"ai/news {s['action']}: {s.get('reason')}"]
        if s.get("horizon"):
            why.append(f"impact horizon: {s['horizon']}")
        if gate.get("reason") and "unavailable" not in str(gate["reason"]):
            why.append(str(gate["reason"]))
        # Carry the full confirmation metadata into the proposal so the
        # executor and test_ai engine can use it for sizing and logging.
        out[symbol] = Proposal(
            symbol=symbol, direction=s["action"],
            confidence=round(max(30.0, min(75.0,
                float(s.get("confidence") or 0.5) * 100.0)), 1),
            entry=None, stop_loss=stop_loss, take_profit=None,
            source="ai_news", window="news", reasons=why,
            # Extended metadata from the 6-layer pipeline:
            regime=s.get("regime"),
            confirm_score=s.get("confirm_score", 0),
            dxy_score=s.get("dxy_score", 0),
            smc_score=s.get("smc_score", 0),
            session=s.get("session"),
            session_mult=s.get("session_mult", 1.0),
            block_reasons=s.get("block_reasons", []),
            # AI-suggested SL/TP percentages:
            stop_loss_pct=float(s.get("stop_loss_pct") or 0.0),
            take_profit_pct=float(s.get("take_profit_pct") or 0.0),
            # Regime the confirmation pipeline saw at signal time — journaled
            # onto the order row so the book can be audited by-trend later.
            trend_regime=s.get("regime"))
    return out


def run_once(conn, symbols: list[str] | None = None,
             dry_run: bool = False, verbose: bool = False) -> dict:
    """One full bot cycle. Returns a summary dict."""
    t0 = time.time()
    summary = {"executed": [], "held": [], "errors": [], "closed": [],
               "dry_run": dry_run, "elapsed_s": 0.0, "blocked": False,
               "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    # Kill-switch: a LEGACY engine in single-source test mode must never
    # trade live. The ai_news path is a single production source BY DESIGN
    # (the news analyst applies its own confidence floor, then the
    # chart-confluence agent + data gates verify), so it is exempt.
    if (config.SIGNAL_SOURCE_A in ("math_engine", "strategy", "hybrid")
            and config.ARBITRATION_MIN_SOURCES < 2
            and not config.ALLOW_LIVE_SINGLE_SOURCE and not dry_run):
        summary["blocked"] = True
        summary["block_reason"] = (
            f"SIGNAL_SOURCE_A={config.SIGNAL_SOURCE_A} with "
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

    # ── Loss guard: a circuit breaker that stops NEW exposure when the
    # journal is bleeding (daily loss or drawdown over the config caps).
    # Open positions are still SL/TP-managed below — closing is not new
    # exposure — but no fresh entries are placed while the guard is tripped.
    loss_guard = execution.loss_guard_status(conn)
    summary["loss_guard"] = loss_guard

    # ── Tiered circuit breaker (2026-08-17 upgrade): step-down protocol
    # based on drawdown severity. Supplements the loss guard with finer-
    # grained risk reduction (25%/50%/micro-lots) instead of a binary block.
    import circuit_breaker
    cb_state = circuit_breaker.current_risk_state(conn)
    summary["circuit_breaker"] = cb_state

    # ── AI mode: live (real MT5 trades) or test (paper trades in DB) ──
    ai_mode = config.AI_MODE  # "live" or "test" (default)
    summary["ai_mode"] = ai_mode

    # Get trading universe from token registry (single source of truth)
    # Filter by venue based on execution broker mode
    if symbols:
        wanted = set(symbols)
    else:
        if config.EXECUTION_BROKER == "binance":
            wanted = {s.symbol for s in token_registry.get_symbols_by_venue(token_registry.Venue.BINANCE)}
        elif config.EXECUTION_BROKER == "alpaca":
            wanted = {s.symbol for s in token_registry.get_symbols_by_venue(token_registry.Venue.ALPACA)}
        elif config.EXECUTION_BROKER == "mt5":
            wanted = {s.symbol for s in token_registry.get_symbols_by_venue(token_registry.Venue.EXNESS_MT5)}
        elif config.EXECUTION_BROKER == "hybrid":
            # Hybrid: all active symbols (Binance + MT5 + Alpaca)
            wanted = {s.symbol for s in token_registry.get_active_symbols()}
        else:
            # Paper broker: all active symbols
            wanted = {s.symbol for s in token_registry.get_active_symbols()}

    # ── AI/news source: the Groq news analyst (one call, all symbols) ─────
    proposals = _ai_news_proposals(conn, sorted(wanted))

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

    # ── Live exposure snapshot for the caps below (2026-09-30) ──────
    # Rows are journaled with their ACTUAL broker (mt5/binance), not
    # "hybrid" — filtering by EXECUTION_BROKER made both caps count 0 and
    # they were no-ops (six AUD trades from one narrative slipped through
    # in a single cycle). Fetched ONCE before the loop and grown in-place
    # as trades execute, so intra-cycle stacking is capped too.
    def _currencies(sym: str) -> set:
        """Currencies/indices a symbol is exposed to. FX pair "USD/CHF" →
        {USD, CHF}; single-name "BTC/USDT" → {BTC}; index "DXY" → {DXY}."""
        parts = [p for p in sym.split("/") if p]
        if len(parts) == 2:
            return {parts[0], parts[1]}
        return {parts[0]} if parts else set()

    _live_rows = [o for o in database.load_orders(conn, limit=1000)
                  if o.get("status") in ("filled", "open")]
    _cur_counts: dict[str, int] = {}
    for o in _live_rows:
        for c in _currencies(o["symbol"]):
            _cur_counts[c] = _cur_counts.get(c, 0) + 1

    for symbol in sorted(wanted):
        proposal = proposals.get(symbol)
        if loss_guard["blocked"]:
            summary["held"].append({"symbol": symbol, "reason": "loss_guard",
                                    "detail": loss_guard["reason"]})
            continue
        # Circuit breaker level 4 = full stop (max drawdown exceeded)
        if cb_state["level"] >= 4:
            summary["held"].append({"symbol": symbol, "reason": "circuit_breaker",
                                    "detail": cb_state["status"]})
            continue
        if proposal is None:
            # The AI/news analyst has no opinion for this symbol (no
            # headline signal above the noise threshold — quiet by design).
            summary["held"].append({"symbol": symbol, "reason": "no_signal_ai",
                                    "a": "HOLD", "b": "HOLD"})
            continue
        try:
            # Data gate BEFORE trusting the news signal. Pass the symbol so
            # market-hours instruments (forex, equity indices) get the
            # weekend-tolerant staleness window — their bars are stale on
            # Monday pre-market by design.
            checks = verification.verify_candle_df(
                _load_frame(conn, symbol), interval="1h", symbol=symbol)
            if not checks["ok"]:
                summary["held"].append({"symbol": symbol, "reason": "data",
                                        "detail": checks["checks"]})
                continue

            # Single-source intent: the news analyst IS the signal, and its
            # own confidence floor (NEWS_SIGNAL_MIN_CONFIDENCE) was already
            # applied inside signal_for — so no extra agreement gate here.
            intent = arbitrate(proposal, None, min_sources=1, min_confidence=0)
            if intent is None:
                summary["held"].append({"symbol": symbol, "reason": "no_signal_ai",
                                        "a": proposal.direction, "b": "HOLD"})
                continue
            if broker_spot and intent.direction == "SHORT":
                # Spot brokers (Alpaca crypto, Binance spot) can't short —
                # selling what you don't own always 403s at the broker.
                summary["held"].append({"symbol": symbol, "reason": "no_short_on_spot"})
                continue

            # No reviewer.verify_trade news gate here (retired 2026-08-17):
            # the trade signal itself comes from the news analyst, so a
            # second news review would double-gate the same headlines. The
            # chart-confluence agent already validated the setup upstream.

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
                if len(_live_rows) >= config.MAX_OPEN_POSITIONS:
                    summary["held"].append({"symbol": symbol,
                                            "reason": "max_positions"})
                    continue

            # Currency-concentration cap: one macro narrative must not become
            # a dozen same-currency bets (2026-09-28: 9 USD-base longs; seen
            # again 2026-09-30: 6 AUD trades from one "regulatory news"
            # story). A pair counts against BOTH legs; grows intra-cycle as
            # trades execute, so a burst cannot stack past the cap.
            if not dry_run and config.MAX_PER_CURRENCY > 0:
                focus = _currencies(symbol)
                blocked_cur = [c for c in sorted(focus)
                               if _cur_counts.get(c, 0) >= config.MAX_PER_CURRENCY]
                if blocked_cur:
                    summary["held"].append({
                        "symbol": symbol, "reason": "currency_concentration",
                        "detail": {"currencies": blocked_cur,
                                   "open": {c: _cur_counts.get(c, 0)
                                            for c in blocked_cur},
                                   "cap": config.MAX_PER_CURRENCY}})
                    continue

            # ── Dual-mode execution ───────────────────────────────────
            # AI_MODE=test → paper trade in test_trades table (no broker)
            # AI_MODE=live → real MT5/Exness trade via execution.execute_intent
            if ai_mode == "test":
                import test_ai
                result = test_ai.record_paper_trade(
                    conn, proposal, intent, dry_run=dry_run)
                if result.get("duplicate"):
                    summary["held"].append({"symbol": symbol, "reason": "duplicate"})
                    continue
                trade = result.get("trade") or {}
                summary["executed"].append({
                    "symbol": symbol,
                    "direction": intent.direction,
                    "qty": trade.get("entry_price", 0),
                    "status": "test_trade",
                    "sources": intent.sources,
                    "reasons": intent.reasons or [],
                    "confirm_score": proposal.confirm_score,
                    "dxy_score": proposal.dxy_score,
                    "smc_score": proposal.smc_score,
                    "regime": proposal.regime,
                    "session": proposal.session})
                if verbose:
                    print(f"  {symbol}: TEST {intent.direction} "
                          f"tc={proposal.confirm_score} dxy={proposal.dxy_score} "
                          f"smc={proposal.smc_score} regime={proposal.regime}")
            else:
                # Live mode — apply circuit breaker risk multiplier to sizing
                result = execution.execute_intent(
                    conn, intent, dry_run=dry_run)
                if result.get("duplicate"):
                    summary["held"].append({"symbol": symbol, "reason": "duplicate"})
                    continue
                order = result.get("order") or {}
                status = order.get("status", "?")
                if status == "error":
                    summary["errors"].append({"symbol": symbol,
                                              "error": order.get("error")})
                else:
                    summary["executed"].append({
                        "symbol": symbol,
                        "direction": intent.direction,
                        "qty": order.get("qty"),
                        "status": status,
                        "sources": intent.sources,
                        "reasons": intent.reasons or [],
                        "confirm_score": proposal.confirm_score,
                        "dxy_score": proposal.dxy_score,
                        "smc_score": proposal.smc_score,
                        "regime": proposal.regime,
                        "session": proposal.session,
                        "cb_level": cb_state["level"],
                        "cb_mult": cb_state["risk_multiplier"]})
                if verbose:
                    print(f"  {symbol}: {intent.direction} qty={order.get('qty')} "
                          f"status={status} src={intent.sources} "
                          f"cb={cb_state['level']}x{cb_state['risk_multiplier']}")
        except Exception as e:
            summary["errors"].append({"symbol": symbol, "error": str(e)})
        finally:
            # Bookkeeping for the caps: an executed (or errored-then-filled)
            # trade occupies its exposure immediately, so a same-cycle burst
            # cannot stack past MAX_PER_CURRENCY / MAX_OPEN_POSITIONS.
            try:
                if intent is not None and intent.direction in ("LONG", "SHORT"):
                    executed_row = database.order_for_intent(
                        conn, getattr(intent, "intent_hash", "") or "")
                    if (executed_row and
                            executed_row.get("status") in ("filled", "open")):
                        _live_rows.append(executed_row)
                        for c in _currencies(symbol):
                            _cur_counts[c] = _cur_counts.get(c, 0) + 1
            except Exception:
                pass

    # ── SL/TP monitor: close any open position that hit its levels ──────
    if not dry_run:
        try:
            summary["closed"] = execution.manage_open_positions(conn)
        except Exception as e:
            # One broker hiccup in the monitor must never kill the cycle.
            summary["errors"].append({"symbol": "*",
                                      "error": f"sl/tp monitor: {e}"})

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
