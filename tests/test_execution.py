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

    print("1. Testing paper broker fill math...")
    try:
        import execution
        import risk

        conn = _fresh_db()
        _config.EXECUTION_BROKER = "paper"
        broker = execution.get_broker()
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
        print(f"   ✅ Paper fill: qty={order['qty']:.4f} fill={order['fill_price']:.2f} "
              f"fees={order['fees']:.4f}")
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

        # Paper broker is untouched: everything tradable, no network.
        _config.EXECUTION_BROKER = "paper"
        execution._assets_cache = {"at": 0.0, "tradable": None}
        all_syms = execution.broker_supported_symbols()
        assert all_syms == set(_config.SYMBOLS), "paper broker trades everything"
        print(f"   ✅ Alpaca failure → empty set ({len(out)} held); paper → all {len(all_syms)} symbols")
    except Exception as e:
        print(f"   ❌ broker_supported_symbols error: {e}")
        traceback.print_exc()
        errors.append(("broker_supported_symbols", e))

    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — execution layer working")


if __name__ == "__main__":
    main()
