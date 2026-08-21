"""
MT5/Exness broker tests — fully offline via a fake MetaTrader5 module.

Run:
    python tests/test_mt5_broker.py
"""
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config as _config  # noqa: E402
import database  # noqa: E402


# ── Fake MetaTrader5 module (never touches a real terminal) ─────────────
class _FakeMT5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self, account=None, positions=None, symbols=None, retcode=None):
        self.account = account or SimpleNamespace(equity=10000.0, margin_free=9000.0,
                                                  balance=10000.0)
        self.positions = positions or []
        self.symbols = symbols or {
            # name -> (contract, step, vmin, vmax)
            "EURUSD": (100000, 0.01, 0.01, 100),
            "XAUUSD": (100, 0.01, 0.01, 100),
            "US500": (10, 0.1, 0.1, 100),
            "BTCUSD": (1, 0.01, 0.01, 100),
        }
        self.retcode = retcode if retcode is not None else self.TRADE_RETCODE_DONE
        self.orders = []
        self.init_calls = 0
        self.shutdown_calls = 0

    def initialize(self, path=None):
        self.init_calls += 1
        return True

    def shutdown(self):
        self.shutdown_calls += 1

    def last_error(self):
        return (0, "fake ok")

    def symbol_select(self, sym, enable=True):
        return sym in self.symbols

    def symbol_info(self, sym):
        if sym not in self.symbols:
            return None
        c, step, vmin, vmax = self.symbols[sym]
        return SimpleNamespace(name=sym, trade_contract_size=c, volume_step=step,
                               volume_min=vmin, volume_max=vmax)

    def account_info(self):
        return self.account

    def order_send(self, request):
        self.orders.append(dict(request))
        if self.retcode != self.TRADE_RETCODE_DONE:
            return SimpleNamespace(retcode=10004, price=0.0, deal=0, order=0,
                                   comment="rejected")
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, price=request.get("price") or 1.08,
                               deal=4242, order=1717)

    def positions_get(self):
        return self.positions


def _inject(fake):
    import execution
    sys.modules["MetaTrader5"] = fake
    execution.MT5Broker._mt5 = None  # reset cached module handle


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _config.DB_PATH = Path(tmp.name)
    import database
    conn = database.connect()
    database.init_db(conn)
    now = int(time.time() * 1000)
    rows = []
    price = 1.08
    for i in range(200):
        t = now - (200 - i) * 3_600_000
        rows.append({"open_time": t, "open": price, "high": price + 0.001,
                     "low": price - 0.001, "close": price, "volume": 1000.0,
                     "close_time": t + 3_599_999})
        price += 0.0002
    database.upsert_candles(conn, "EUR/USD", "1h", rows)
    return conn


def _intent(symbol="EUR/USD", direction="LONG", entry=1.09, stop=1.088,
            tp=1.094, confidence=45.0):
    from arbitrator import Intent, intent_hash
    return Intent(symbol=symbol, direction=direction, entry=entry, stop_loss=stop,
                  take_profit=tp, sources=["math_engine"], window="1h",
                  confidence=confidence,
                  intent_hash=intent_hash(symbol, direction, "1h"))


def main():
    errors = []
    import execution

    print("1. Symbol mapping (display -> Exness MT5 name)...")
    try:
        _inject(_FakeMT5())
        _config.EXECUTION_BROKER = "mt5"
        _config.EXNESS_MT5_ENABLED = True
        b = execution.get_broker()
        assert b.name == "mt5"
        assert b.symbol_of("XAU/USD") == "XAUUSD"
        assert b.symbol_of("SPX/USD") == "US500"
        assert b.symbol_of("EUR/USD") == "EURUSD"
        assert b.symbol_of("WTI/USD") == "USOIL"
        assert b.symbol_of("BTC/USDT") == "BTCUSD"
        assert b.symbol_of("IBIT") == "IBIT"  # identity for unmapped
        print("   ✅ mapping OK")
    except Exception as e:
        traceback.print_exc(); errors.append(("mapping", e))

    print("2. Lot conversion (contract size + volume_step rounding)...")
    try:
        _inject(_FakeMT5())
        b = execution.get_broker()
        info = b._symbol_info("EUR/USD")
        assert b._lots(info, 100_000) == 1.0, "1 lot exactly"
        assert b._lots(info, 150_000) == 1.5
        assert b._lots(info, 12_345) == 0.12, "round DOWN to 0.01 step"
        info2 = b._symbol_info("XAU/USD")
        assert b._lots(info2, 100) == 1.0, "gold: 100 oz = 1 lot"
        assert b._lots(info2, 3.5) == 0.03, "gold: 3.5 oz -> 0.03 lot (floored)"
        print("   ✅ lots OK")
    except Exception as e:
        traceback.print_exc(); errors.append(("lots", e))

    print("3. Order submission (market IOC DEAL, fill + broker_ref)...")
    try:
        fake = _FakeMT5()
        _inject(fake)
        b = execution.get_broker()
        fill = b.place_market(None, {"symbol": "EUR/USD", "side": "LONG", "qty": 100_000})
        assert fill["status"] == "filled"
        assert fill["broker_ref"] == "4242"
        assert fill["fill_price"] is not None
        req = fake.orders[0]
        assert req["symbol"] == "EURUSD"
        assert req["volume"] == 1.0
        assert req["type"] == fake.ORDER_TYPE_BUY
        assert req["action"] == fake.TRADE_ACTION_DEAL
        # SHORT closes map to SELL
        fake2 = _FakeMT5()
        _inject(fake2)
        b = execution.get_broker()
        b.close_market(None, {"symbol": "XAU/USD", "side": "LONG", "qty": 100})
        assert fake2.orders[0]["type"] == fake2.ORDER_TYPE_SELL
        print("   ✅ orders OK")
    except Exception as e:
        traceback.print_exc(); errors.append(("orders", e))

    print("4. Broker rejection -> BrokerError (not silent)...")
    try:
        fake = _FakeMT5(retcode=10004)
        _inject(fake)
        b = execution.get_broker()
        try:
            b.place_market(None, {"symbol": "EUR/USD", "side": "LONG", "qty": 100_000})
            raise AssertionError("should have raised")
        except execution.BrokerError as e:
            assert "rejected" in str(e)
        print("   ✅ rejection OK")
    except Exception as e:
        traceback.print_exc(); errors.append(("rejection", e))

    print("5. fetch_positions (lots -> units)...")
    try:
        fake = _FakeMT5(positions=[
            SimpleNamespace(symbol="EURUSD", volume=0.5,
                            type=_FakeMT5.POSITION_TYPE_BUY),
            SimpleNamespace(symbol="XAUUSD", volume=0.01,
                            type=_FakeMT5.POSITION_TYPE_SELL),
        ])
        _inject(fake)
        b = execution.get_broker()
        pos = b.fetch_positions()
        assert pos[0]["qty"] == 50_000 and pos[0]["side"] == "long"
        assert pos[1]["qty"] == 1.0 and pos[1]["side"] == "short"
        print("   ✅ positions OK")
    except Exception as e:
        traceback.print_exc(); errors.append(("positions", e))

    print("6. Sizing: MT5_POSITION_PCT cap + min-lot floor...")
    try:
        _inject(_FakeMT5())  # equity 10k, margin_free 9k -> base 9000
        b = execution.get_broker()
        # EUR/USD: risk 1% of 9000 = 90 over |1.09-1.088| = 0.002 -> 45,000 units
        # cap 50% of 9000 / 1.09 = 4,128 units -> 0.04 lots -> 4,000 units
        size = execution._sized_qty(None, _intent(entry=1.09, stop=1.088), None)
        assert abs(size - 4000) < 1e-6, f"eur size={size}"
        # XAU/USD: risk qty = 90 / 20 = 4.5 oz -> 0.04 lot; cap 50% of 9000 /
        # (4400*100) -> 0.01 lots min-lot floor -> 1 oz
        size2 = execution._sized_qty(None, _intent(symbol="XAU/USD", entry=4400.0,
                                                   stop=4380.0), None)
        # min-lot floor: 0.01 lot × 100 oz contract = 1 oz (~$4.4k notional)
        assert abs(size2 - 1.0) < 1e-6, f"gold size={size2}"
        print(f"   ✅ sizing OK (EUR {size:.0f}u, XAU {size2:.0f}u = min-lot)")
    except Exception as e:
        traceback.print_exc(); errors.append(("sizing", e))

    print("7. broker_supported_symbols (mt5 = exactly the Exness universe)...")
    try:
        _inject(_FakeMT5())
        sup = execution.broker_supported_symbols()
        assert sup == set(_config.EXNESS_SYMBOLS), f"got {len(sup)}"
        assert "XAU/USD" in sup and "BTC/USDT" in sup
        assert "IBIT" not in sup and "BTC/USDT" in sup
        print(f"   ✅ mt5 universe OK ({len(sup)} symbols)")
    except Exception as e:
        traceback.print_exc(); errors.append(("universe", e))

    print("8. Graceful without the MetaTrader5 package...")
    try:
        _inject(_FakeMT5())
        import execution as ex
        # Simulate the package being absent even when it's installed:
        # sys.modules[None] makes `import MetaTrader5` raise ImportError.
        saved = sys.modules.get("MetaTrader5")
        sys.modules["MetaTrader5"] = None
        ex.MT5Broker._mt5 = None
        try:
            b = ex.get_broker()
            try:
                b.place_market(None, {"symbol": "EUR/USD", "side": "LONG", "qty": 100})
                raise AssertionError("should have raised")
            except ex.BrokerError as e:
                assert "pip install MetaTrader5" in str(e)
            # _broker_equity falls back to journal equity instead of crashing
            eq = ex._broker_equity(_fresh_db())
            assert eq > 0
        finally:
            if saved is not None:
                sys.modules["MetaTrader5"] = saved
            else:
                sys.modules.pop("MetaTrader5", None)
            ex.MT5Broker._mt5 = None
        print("   ✅ graceful OK")
    except Exception as e:
        traceback.print_exc(); errors.append(("graceful", e))

    print("9. Dry-run journaling for an Exness symbol (end-to-end)...")
    try:
        _inject(_FakeMT5())
        conn = _fresh_db()
        _config.EXECUTION_BROKER = "mt5"
        r = execution.execute_intent(conn, _intent(), dry_run=True)
        o = r["order"]
        assert o["status"] == "dry_run" and o["symbol"] == "EUR/USD"
        assert o["qty"] > 0
        print(f"   ✅ dry-run OK (qty={o['qty']:.0f})")
    except Exception as e:
        traceback.print_exc(); errors.append(("dryrun", e))

    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s):")
        for n, e in errors:
            print(f"   - {n}: {e}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — MT5/Exness broker working")


if __name__ == "__main__":
    main()
