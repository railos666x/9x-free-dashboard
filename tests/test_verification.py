"""
Verification gate tests — data-quality checks (pure, no network/DB).

Run:
    python tests/test_verification.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import time
import traceback

import pandas as pd
import numpy as np


def _df(n=100, base=50000.0, open_time=None):
    """Synthetic candles: gentle uptrend, columns in our DB shape."""
    closes = base + np.cumsum(np.random.default_rng(7).normal(20, 80, n))
    times = [open_time or int((time.time() - (n - i) * 3600) * 1000) for i in range(n)]
    df = pd.DataFrame({
        "open_time": times,
        "open": closes - 10,
        "high": closes + 40,
        "low": closes - 40,
        "close": closes,
        "volume": np.full(n, 1000.0),
    })
    return df


def main():
    errors = []

    # 1. staleness_ok
    print("1. Testing staleness...")
    try:
        import verification as v
        now = time.time() * 1000
        assert v.staleness_ok(now - 60_000, "1h", now_ms=now), "1min-old 1h candle should be fresh"
        assert v.staleness_ok(now - 3_600_000, "1h", now_ms=now), "1h-old 1h candle should be fresh (2.5x mult)"
        assert not v.staleness_ok(now - 20 * 3_600_000, "1h", now_ms=now), "20h-old candle must be stale"
        assert not v.staleness_ok(None, "1h", now_ms=now), "None timestamp must be stale"
        print("   ✅ Staleness checks pass")
    except Exception as e:
        print(f"   ❌ Staleness error: {e}")
        traceback.print_exc()
        errors.append(("staleness", e))

    # 2. candle_sanity
    print("\n2. Testing candle sanity...")
    try:
        import verification as v
        df = _df()
        ok = v.candle_sanity(df)
        assert ok["ok"] is True and ok["problems"] == [], ok

        bad = df.copy()
        bad.loc[bad.index[-1], "high"] = bad["open"].iloc[-1] - 5  # high < open
        r = v.candle_sanity(bad)
        assert r["ok"] is False and any("high" in p for p in r["problems"]), r

        zero = df.copy()
        zero.loc[zero.index[-1], "close"] = 0.0
        r = v.candle_sanity(zero)
        assert r["ok"] is False, "zero close must fail"

        r = v.candle_sanity(pd.DataFrame())
        assert r["ok"] is False, "empty frame must fail"
        print("   ✅ Candle sanity checks pass")
    except Exception as e:
        print(f"   ❌ Candle sanity error: {e}")
        traceback.print_exc()
        errors.append(("sanity", e))

    # 3. spike_check
    print("\n3. Testing spike detection...")
    try:
        import indicators
        import verification as v

        df = _df()
        df = indicators.add_all(df)
        assert v.spike_check(df) == [], "no spikes in a gentle trend"

        spiked = df.copy()
        # Crash the last close 20x ATR with LOW volume -> bad tick.
        last_atr = float(spiked["atr"].iloc[-2])
        spiked.loc[spiked.index[-1], "close"] = (
            spiked["close"].iloc[-2] + 20 * last_atr)
        spiked.loc[spiked.index[-1], "volume"] = 100.0  # vs median 1000
        spikes = v.spike_check(spiked)
        assert spikes and spikes[-1]["index"] == len(spiked) - 1, spikes

        vol_confirmed = spiked.copy()
        vol_confirmed.loc[vol_confirmed.index[-1], "volume"] = 100_000.0
        assert v.spike_check(vol_confirmed) == [], "big move WITH volume = news, not a tick"
        print("   ✅ Spike detection works (and respects volume confirmation)")
    except Exception as e:
        print(f"   ❌ Spike error: {e}")
        traceback.print_exc()
        errors.append(("spike", e))

    # 4. cross_source_ok + verify_candle_df
    print("\n4. Testing cross-source + full gate...")
    try:
        import verification as v
        assert v.cross_source_ok(50000.0, 50010.0, tolerance_pct=1.0)
        assert not v.cross_source_ok(50000.0, 51000.0, tolerance_pct=1.0)
        assert not v.cross_source_ok(0.0, 50000.0), "zero price must fail"

        df = _df(open_time=int(time.time() * 1000))  # newest bar is NOW
        import indicators
        df = indicators.add_all(df)
        res = v.verify_candle_df(df, interval="1h")
        assert res["ok"] is True, res
        stale = _df(open_time=int((time.time() - 72 * 3600) * 1000))
        res = v.verify_candle_df(stale, interval="1h")
        assert res["ok"] is False, "stale feed must fail the gate"
        print("   ✅ Cross-source + full gate checks pass")
    except Exception as e:
        print(f"   ❌ Gate error: {e}")
        traceback.print_exc()
        errors.append(("gate", e))

    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — verification gate working")


if __name__ == "__main__":
    main()
