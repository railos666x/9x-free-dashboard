"""
Execution layer tests — paper broker + idempotency, on a temp SQLite DB.

Run:
    python tests/test_execution.py
"""
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config as _config  # noqa: E402  (import order matters: config first)
import database  # noqa: E402


def _fresh_db():
    """A temp SQLite DB on disk, seeded with one symbol of 1h candles."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _config.DB_PATH = Path(tmp.name)
    import database
    conn = database.connect()
    database.init_db(conn)
    now = int(time.time() * 1000)
    rows = []
    price = 50000.0
    for i in range(30):
        t = now - (30 - i) * 3_600_000
        rows.append({"open_time": t, "open": price, "high": price + 50,
                     "low": price - 50, "close": price, "volume": 1000.0,
                     "close_time": t + 3_599_999})
        price += 10.0  # gentle uptrend to 50290
    database.upsert_candles(conn, "BTC/USDT", "1h", rows)
    return conn


def _intent(direction="LONG", entry=50290.0, stop=49800.0, tp=51200.0,
            window="1h"):
    from arbitrator import Intent, intent_hash
    # confidence 45 = rsi2's flat conviction → BASE sizing (1% risk, no boost).
    return Intent(symbol="BTC/USDT", direction=direction, entry=entry,
                  stop_loss=stop, take_profit=tp, sources=["math_engine", "proprietary"],
                  window=window, confidence=45.0,
                  intent_hash=intent_hash("BTC/USDT", direction, window))


def main():
    errors = []
    # Paper is no longer config-selectable (get_broker refuses "paper" now);
    # while EXECUTION_BROKER stays "paper" these white-box tests route
    # get_broker at the direct PaperBroker construction. Any test that needs
    # the REAL selection (e.g. the Alpaca-failure test below) restores the
    # original first.
    import execution as _ex
    _orig_get_broker = _ex.get_broker
    _ex.get_broker = lambda: _ex.PaperBroker()

    print("1. Testing paper broker fill math...")
    try:
        import execution
        import risk

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"   # class is white-box-only now
        broker = execution.PaperBroker()     # direct construction — get_broker refuses paper
        assert broker.name == "paper"

        result = execution.execute_intent(conn, _intent())
        order = result["order"]
        assert result["duplicate"] is False
        assert order["status"] == "filled", order

        # LONG pays up: fill = last close + slippage(BTC 0.5bps) = 50290 + 2.5145
        last_close = 50290.0
        expect_fill = last_close * (1 + risk.slippage_bps("BTC/USDT") / 10_000)
        assert abs(order["fill_price"] - expect_fill) < 0.01, order

        # Sizing: risk 1% of 10k equity over |50290-49800| = 490 → 0.2041,
        # but the 15% notional cap (10k × 0.15 / 50290) binds → 0.0298.
        cap_qty = (10000 * 0.15) / 50290
        assert abs(order["qty"] - min((10000 * 0.01) / 490, cap_qty)) < 1e-6, order
        assert order["fees"] > 0, "fees must be charged"
        # Regression: the fill's broker_ref must reach the journal —
        # reconciliation matches on it, and without it every real order is
        # orphaned from the broker (no status sync, no broker-side closes).
        assert order["broker_ref"].startswith("paper-"), order
        db_row = database.load_orders(conn, status="filled")[0]
        assert db_row["broker_ref"] == order["broker_ref"], db_row
        print(f"   ✅ Paper fill: qty={order['qty']:.4f} fill={order['fill_price']:.2f} "
              f"fees={order['fees']:.4f} broker_ref={order['broker_ref']}")
    except Exception as e:
        print(f"   ❌ Paper broker error: {e}")
        traceback.print_exc()
        errors.append(("paper", e))

    print("\n1b. Testing confidence-scaled lot sizes (sure trades risk more)...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        base = (10000 * 0.01) / 490  # 1% of 10k over |50290-49800| = 490
        cap_for = lambda m: (10000 * 0.15 * m) / 50290  # notional cap, conf-scaled
        sizes = {}
        for conf, mult in ((30.0, 1.0), (50.0, 1.0), (60.0, 1.5),
                           (70.0, 2.0), (80.0, 2.0)):
            it = _intent()
            it.confidence = conf
            r = execution.execute_intent(conn, it, dry_run=True)
            sizes[conf] = r["order"]["qty"]
            conn.execute("DELETE FROM orders")
            conn.commit()
        for conf, mult in ((30.0, 1.0), (50.0, 1.0), (60.0, 1.5),
                           (70.0, 2.0), (80.0, 2.0)):
            want = min(base * mult, cap_for(mult))
            assert abs(sizes[conf] - want) < 1e-9, (conf, sizes[conf], want)
        # The sure-trade boost must actually be visible (cap scales too).
        assert sizes[70.0] > sizes[30.0] * 1.9, sizes
        print(f"   ✅ Confidence scaling: conf<60 → 1.0×, 60 → 1.5×, "
              f"70+ → 2.0× (qty {sizes[30.0]:.4f} → {sizes[70.0]:.4f})")
    except Exception as e:
        print(f"   ❌ Confidence scaling error: {e}")
        traceback.print_exc()
        errors.append(("confsize", e))

    print("\n2. Testing idempotency (no duplicate orders)...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        r1 = execution.execute_intent(conn, _intent())
        r2 = execution.execute_intent(conn, _intent())
        assert r1["duplicate"] is False and r2["duplicate"] is True, (r1, r2)
        assert r1["order"]["id"] == r2["order"]["id"], "must be the SAME order row"
        orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        assert orders == 1, f"expected 1 order row, got {orders}"

        # A different intent (different window) must place a NEW order.
        r3 = execution.execute_intent(conn, _intent(direction="SHORT"))
        assert r3["duplicate"] is False and r3["order"]["side"] == "SHORT"
        orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        assert orders == 2, f"expected 2 order rows, got {orders}"
        print("   ✅ Same intent → one order forever; new intent → new order")
    except Exception as e:
        print(f"   ❌ Idempotency error: {e}")
        traceback.print_exc()
        errors.append(("idempotency", e))

    print("\n3. Testing zero-size guard (no equity/stop)...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        bad = _intent()
        bad.entry = 50000.0
        bad.stop_loss = 50000.0  # zero unit risk -> zero size
        r = execution.execute_intent(conn, bad)
        assert r.get("error") == "zero size (no equity or no stop distance)", r
        print("   ✅ Degenerate intent → refused with clear error, no order")
    except Exception as e:
        print(f"   ❌ Zero-size error: {e}")
        traceback.print_exc()
        errors.append(("zerosize", e))

    print("\n4. Testing dry-run journaling (preview ≠ consumed trade)...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        r = execution.execute_intent(conn, _intent(), dry_run=True)
        assert r["dry_run"] is True and r["order"]["status"] == "dry_run"
        row = conn.execute("SELECT * FROM orders").fetchone()
        assert row["status"] == "dry_run"
        # A preview must NOT consume the intent: the real order replaces it.
        r2 = execution.execute_intent(conn, _intent())
        assert r2["duplicate"] is False and r2["order"]["status"] == "filled", r2
        rows = conn.execute("SELECT * FROM orders").fetchall()
        assert len(rows) == 1 and rows[0]["status"] == "filled", rows
        # And once it's a REAL order, it is idempotent again.
        r3 = execution.execute_intent(conn, _intent())
        assert r3["duplicate"] is True
        print("   ✅ Dry-run is a preview; only placed orders are idempotent")
    except Exception as e:
        print(f"   ❌ Dry-run error: {e}")
        traceback.print_exc()
        errors.append(("dryrun", e))

    print("\n5. Testing SL/TP position management...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        r = execution.execute_intent(conn, _intent(entry=50000.0, stop=49500.0,
                                                   tp=51500.0))
        order = r["order"]

        # TP hit: LONG at price 51600 (> TP 51500) → closed with pnl > 0.
        res = execution.close_position(conn, order, price=51600.0, reason="tp")
        assert res["order"]["status"] == "closed"
        assert res["order"]["exit_reason"] == "tp"
        # qty = min(1% of 10k / 500, 15% notional cap) = 0.03
        # gross = (51600-50000)*0.03 = 48, minus entry+exit fees (~2.5)
        assert 30 < res["pnl"] < 60, res
        assert res["order"]["pnl"] == res["pnl"]

        # SL/TP detection on a SHORT (stop above entry, TP below).
        r2 = execution.execute_intent(conn, _intent(direction="SHORT", entry=50000.0,
                                                    stop=50500.0, tp=49000.0))
        o2 = r2["order"]
        assert execution.check_position(conn, o2, price=50600.0) == "sl"
        assert execution.check_position(conn, o2, price=48000.0) == "tp"
        assert execution.check_position(conn, o2, price=50000.0) == "hold"
        assert execution.check_position(conn, o2, price=50000.0) == "hold"
        print("   ✅ SL/TP detection + close + PnL math work")
    except Exception as e:
        print(f"   ❌ Position mgmt error: {e}")
        traceback.print_exc()
        errors.append(("positions", e))

    print("\n6. Testing manage_open_positions (monitor loop)...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        execution.execute_intent(conn, _intent(entry=50000.0, stop=49500.0,
                                               tp=51500.0, window="4h"))
        assert execution.manage_open_positions(conn) == [], \
            "no close while price sits below TP"

        # Push a fresh candle ABOVE the TP → the monitor must close it.
        now = int(time.time() * 1000)
        database.upsert_candles(conn, "BTC/USDT", "1h", [{
            "open_time": now, "open": 52000.0, "high": 52100.0,
            "low": 51900.0, "close": 52000.0, "volume": 1500.0,
            "close_time": now + 3_599_999}]
        )
        closed = execution.manage_open_positions(conn)
        assert len(closed) == 1 and closed[0]["reason"] == "tp", closed
        row = conn.execute("SELECT status, exit_reason, pnl FROM orders").fetchone()
        assert row["status"] == "closed" and row["exit_reason"] == "tp"
        assert row["pnl"] is not None
        print(f"   ✅ Monitor closed the position at TP (pnl={row['pnl']:.2f})")
    except Exception as e:
        print(f"   ❌ Monitor error: {e}")
        traceback.print_exc()
        errors.append(("monitor", e))

    print("\n7. Testing broker_supported_symbols (Alpaca metadata failure → hold all)...")
    try:
        import execution
        import requests as _requests

        # Regression: when Alpaca's /v2/assets metadata call fails, the bot
        # must HOLD every symbol — NOT fall back to all 87 and fire orders
        # that 422 at the broker (the morning's 37 ERROR rows).
        # Restore the real get_broker first: this test exercises the actual
        # broker selection, not the white-box PaperBroker override.
        execution.get_broker = _orig_get_broker
        _config.EXECUTION_BROKER = "alpaca"
        _config.ALPACA_API_KEY = "fake-key"
        _config.ALPACA_SECRET_KEY = "fake-secret"
        execution._assets_cache = {"at": 0.0, "tradable": None}

        orig_get = execution.requests.get
        def boom(*a, **k):
            raise _requests.ConnectionError("metadata API down")
        execution.requests.get = boom
        try:
            out = execution.broker_supported_symbols()
        finally:
            execution.requests.get = orig_get
        assert out == set(), f"must hold ALL symbols on failure, got {len(out)}"

        # Non-Alpaca brokers trade the whole universe without a metadata call.
        _config.EXECUTION_BROKER = "mt5"
        execution._assets_cache = {"at": 0.0, "tradable": None}
        all_syms = execution.broker_supported_symbols()
        assert all_syms == set(_config.EXNESS_SYMBOLS), "mt5 → the mapped Exness universe"
        print(f"   ✅ Alpaca failure → empty set ({len(out)} held); mt5 → all {len(all_syms)} symbols")

        # Success path: the crypto assets API + the US Bitcoin ETFs (they're
        # equity assets, invisible to the crypto asset_class query) are both
        # tradable — the AI's extra tradeable universe.
        _config.EXECUTION_BROKER = "alpaca"
        execution._assets_cache = {"at": 0.0, "tradable": None}
        def fake_assets(*a, **k):
            class R:
                def raise_for_status(self):
                    pass
                def json(self):
                    return [{"symbol": "BTC/USD", "tradable": True},
                            {"symbol": "DOGE/USD", "tradable": True},
                            {"symbol": "IBIT", "tradable": True}]
            return R()
        execution.requests.get = fake_assets
        try:
            ok = execution.broker_supported_symbols()
        finally:
            execution.requests.get = orig_get
        assert "IBIT" in ok and "BITB" in ok, "ETFs must be tradable on Alpaca"
        assert "BTC/USDT" in ok and "DOGE/USDT" in ok, "crypto pairs still matched"
        assert len(ok) == 2 + len(_config.ETF_SYMBOLS), f"got {len(ok)}"
        print(f"   ✅ Alpaca success → crypto pairs + all {len(_config.ETF_SYMBOLS)} ETFs tradable")
    except Exception as e:
        print(f"   ❌ broker_supported_symbols error: {e}")
        traceback.print_exc()
        errors.append(("broker_supported_symbols", e))

    print("\n8. Testing the loss guard (circuit breaker)...")
    try:
        import execution

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        _config.MAX_DAILY_LOSS_PCT = 3.0
        _config.MAX_DRAWDOWN_PCT = 10.0

        # Flat journal → never blocked.
        g = execution.loss_guard_status(conn)
        assert g["blocked"] is False, g

        # Seed two closed losing trades today (exit_time today): total -$400
        # on a $10k book = 4% daily loss → guard trips.
        today = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, created_at)
               VALUES ('l1', 'BTC/USDT', 'LONG', 0.1, 50000, 'closed', 'paper',
                       50000, ?, ?, 10, -200, ?)""",
            (today, today, today))
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, created_at)
               VALUES ('l2', 'ETH/USDT', 'LONG', 1.0, 3000, 'closed', 'paper',
                       3000, ?, ?, 5, -200, ?)""",
            (today, today, today))
        conn.commit()
        g = execution.loss_guard_status(conn)
        assert g["blocked"] is True, g
        assert g["daily_loss_pct"] >= 3.0, g
        assert "daily loss guard" in g["reason"], g

        # A win today offsets the losses → unblocked again.
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, created_at)
               VALUES ('w1', 'SOL/USDT', 'SHORT', 2.0, 150, 'closed', 'paper',
                       150, ?, ?, 5, 500, ?)""",
            (today, today, today))
        conn.commit()
        g = execution.loss_guard_status(conn)
        assert g["blocked"] is False, g

        # Drawdown guard: peak equity 10k, equity down 15% → blocked.
        conn = _fresh_db()
        # Peak is set by a closed WIN first (chronologically), then a big loss.
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, created_at)
               VALUES ('pk', 'BTC/USDT', 'LONG', 0.1, 50000, 'closed', 'paper',
                       50000, '2026-08-15T10:00:00Z', '2026-08-15T11:00:00Z',
                       10, 1000, '2026-08-15T10:00:00Z')""")
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, created_at)
               VALUES ('dd', 'ETH/USDT', 'LONG', 1.0, 3000, 'closed', 'paper',
                       3000, '2026-08-16T10:00:00Z', '2026-08-16T11:00:00Z',
                       5, -2500, '2026-08-16T10:00:00Z')""")
        conn.commit()
        g = execution.loss_guard_status(conn)
        assert g["peak_equity"] == 11000.0, g
        assert g["equity_now"] == 8500.0, g
        assert g["drawdown_pct"] >= 22.0, g  # 2500/11000 ≈ 22.7%
        assert g["blocked"] is True, g
        assert "drawdown guard" in g["reason"], g

        # Disabled guard (threshold 0) never blocks.
        _config.MAX_DAILY_LOSS_PCT = 0.0
        _config.MAX_DRAWDOWN_PCT = 0.0
        g = execution.loss_guard_status(conn)
        assert g["blocked"] is False, g
        print(f"   ✅ Loss guard: daily {g['daily_loss_pct']}% / "
              f"drawdown {g['drawdown_pct']}% caps work + disabled = off")

    except Exception as e:
        print(f"   ❌ Loss guard error: {e}")
        traceback.print_exc()
        errors.append(("loss_guard", e))

    print("\n8b. Account-retired exits don't trip the guard...")
    try:
        import execution

        conn = _fresh_db()
        _config.MAX_DAILY_LOSS_PCT = 3.0
        _config.MAX_DRAWDOWN_PCT = 10.0
        today = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # A big loss closed as account_retired (broker-swap bookkeeping) must
        # not freeze the fresh account: -40% would trip a 10% drawdown cap.
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, exit_reason, created_at)
               VALUES ('ar1', 'BTC/USDT', 'LONG', 0.1, 50000, 'closed', 'paper',
                       50000, ?, ?, 10, -4000, 'account_retired', ?)""",
            (today, today, today))
        conn.commit()
        g = execution.loss_guard_status(conn)
        assert g["blocked"] is False, g
        assert g["equity_now"] == _config.INITIAL_CAPITAL, g
        # Same loss WITHOUT the retirement flag trips the drawdown guard.
        conn.execute(
            """INSERT INTO orders (intent_hash, symbol, side, qty, entry, status,
               source, fill_price, fill_time, exit_time, fees, pnl, exit_reason, created_at)
               VALUES ('sl1', 'ETH/USDT', 'LONG', 1.0, 3000, 'closed', 'paper',
                       3000, ?, ?, 5, -4000, 'sl', ?)""",
            (today, today, today))
        conn.commit()
        g = execution.loss_guard_status(conn)
        assert g["blocked"] is True and g["drawdown_pct"] >= 10.0, g
        print("   ✅ account_retired exits excluded; real losses still trip the guard")
    except Exception as e:
        print(f"   ❌ Loss-guard retirement error: {e}")
        traceback.print_exc()
        errors.append(("loss_guard_retired", e))
    except Exception as e:
        print(f"   ❌ Loss guard error: {e}")
        traceback.print_exc()
        errors.append(("loss_guard", e))

    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — execution layer working")


if __name__ == "__main__":
    main()
