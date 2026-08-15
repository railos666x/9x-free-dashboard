"""
Proprietary signal source tests — default-off safety + RSI(2) extremes.

Run:
    python tests/test_proprietary.py
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

import config as _config  # noqa: E402


def _fresh_db():
    """Temp DB with two symbols of 15m candles: BTC crashing, ETH pumping."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _config.DB_PATH = Path(tmp.name)
    import database
    conn = database.connect()
    database.init_db(conn)
    now = int(time.time() * 1000)
    for symbol, trend in (("BTC/USDT", -1.0), ("ETH/USDT", +1.0)):
        rows = []
        price = 50000.0 if symbol.startswith("BTC") else 3000.0
        for i in range(120):
            t = now - (120 - i) * 15 * 60_000
            rows.append({"open_time": t, "open": price, "high": price + price * 0.001,
                         "low": price - price * 0.001, "close": price,
                         "volume": 1000.0, "close_time": t + 14 * 60_000})
            price *= (1 + trend * 0.01)  # BTC -1%/bar, ETH +1%/bar
        database.upsert_candles(conn, symbol, "15m", rows)
    return conn


def main():
    errors = []

    print("1. Testing default-off (PROPRIETARY_SIGNALS_ENABLED=false)...")
    try:
        import proprietary_signals
        _config.PROPRIETARY_SIGNALS_ENABLED = False
        out = proprietary_signals.generate_all_proposals(None)
        assert out == [], "disabled source must emit nothing (bot stays HOLD)"
        print("   ✅ Disabled by default — existing behavior unchanged")
    except Exception as e:
        print(f"   ❌ Default-off error: {e}")
        traceback.print_exc()
        errors.append(("default_off", e))

    print("\n2. Testing RSI(2) extremes when enabled...")
    try:
        import proprietary_signals
        _config.PROPRIETARY_SIGNALS_ENABLED = True
        conn = _fresh_db()
        props = proprietary_signals.generate_all_proposals(conn)
        by_symbol = {p.symbol: p for p in props}
        assert "BTC/USDT" in by_symbol and "ETH/USDT" in by_symbol, by_symbol

        # BTC is crashing → RSI(2) deep in oversold → LONG.
        assert by_symbol["BTC/USDT"].direction == "LONG", by_symbol["BTC/USDT"]
        # ETH is pumping → RSI(2) overbought → SHORT.
        assert by_symbol["ETH/USDT"].direction == "SHORT", by_symbol["ETH/USDT"]
        for p in props:
            assert p.source == "proprietary" and p.window == "15m"
            assert p.entry is None and p.stop_loss is None, \
                "source B must not invent price levels"
        print(f"   ✅ BTC→LONG, ETH→SHORT from independent RSI(2) rule "
              f"({len(props)} symbols)")
    except Exception as e:
        print(f"   ❌ Enabled error: {e}")
        traceback.print_exc()
        errors.append(("enabled", e))

    print("\n3. Testing llm mode (graceful without LLM keys)...")
    try:
        import proprietary_signals
        _config.PROPRIETARY_SIGNALS_ENABLED = True
        _config.PROPRIETARY_MODE = "llm"
        _config.LLM_ENABLED = False
        out = proprietary_signals.generate_all_proposals(None)
        assert out == [], "llm mode without keys must degrade to [] (never a hard dependency)"
        _config.PROPRIETARY_MODE = "rsi2"
        print("   ✅ llm mode degrades safely to no signals without keys")
    except Exception as e:
        print(f"   ❌ llm mode error: {e}")
        traceback.print_exc()
        errors.append(("llm_mode", e))

    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — proprietary source working")


if __name__ == "__main__":
    main()
