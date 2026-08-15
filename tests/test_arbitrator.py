"""
Arbitrator tests — the two-source agreement gate + intent dedup hashing.

Run:
    python tests/test_arbitrator.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import traceback


def main():
    errors = []

    print("1. Testing agreement gate...")
    try:
        from arbitrator import Proposal, arbitrate

        a = Proposal("BTC/USDT", "LONG", 65.0, entry=50000.0,
                     stop_loss=49500.0, take_profit=51500.0,
                     source="math_engine", window="1h")
        b = Proposal("BTC/USDT", "LONG", 55.0, source="proprietary", window="15m")

        intent = arbitrate(a, b)
        assert intent is not None, "both LONG must produce an intent"
        assert intent.direction == "LONG"
        assert intent.sources == ["math_engine", "proprietary"]
        assert intent.entry == 50000.0 and intent.stop_loss == 49500.0, \
            "levels must come from the source that provides them"
        assert intent.confidence == 55.0, "confidence must be the weaker of the two"
        assert intent.window == "1h", "window from source A when B has no levels"
        print("   ✅ Agreement produces one merged Intent (weakest confidence)")
    except Exception as e:
        print(f"   ❌ Agreement error: {e}")
        traceback.print_exc()
        errors.append(("agreement", e))

    print("\n2. Testing conflicts and HOLD...")
    try:
        from arbitrator import Proposal, arbitrate

        long = Proposal("BTC/USDT", "LONG", 60.0, source="math_engine")
        short = Proposal("BTC/USDT", "SHORT", 60.0, source="proprietary")
        assert arbitrate(long, short) is None, "LONG vs SHORT = conflict = no trade"

        hold = Proposal("BTC/USDT", "HOLD", 0.0, source="proprietary")
        assert arbitrate(long, hold) is None, "HOLD vetoes"

        assert arbitrate(long, None) is None, "missing source = no trade"
        assert arbitrate(None, long) is None
        print("   ✅ Conflicts/HOLD/missing source all degrade to no trade")
    except Exception as e:
        print(f"   ❌ Conflict error: {e}")
        traceback.print_exc()
        errors.append(("conflict", e))

    print("\n3. Testing single-source mode (ARBITRATION_MIN_SOURCES=1)...")
    try:
        from arbitrator import Proposal, arbitrate

        long = Proposal("BTC/USDT", "LONG", 60.0, source="math_engine",
                        reasons=["trend@1h BULLISH"])

        # Missing source B: default gate refuses, single-source mode allows
        # (with sufficient confidence).
        assert arbitrate(long, None) is None, "2-source default must refuse a lone source"
        intent = arbitrate(long, None, min_sources=1, min_confidence=50)
        assert intent is not None and intent.direction == "LONG"
        assert intent.sources == ["math_engine"], "lone source keeps its own name"
        assert intent.confidence == 60.0
        assert intent.reasons == ["trend@1h BULLISH"], "lone source keeps its reasons"

        # Confidence floor: a too-unsure lone source still never trades.
        weak = Proposal("ETH/USDT", "LONG", 30.0, source="proprietary")
        assert arbitrate(weak, None, min_sources=1, min_confidence=50) is None, \
            "single-source mode must respect the confidence floor"

        # A HOLD second source is an ABSTENTION, not a veto — the lone
        # active source still trades (useful while source A is all-HOLD).
        hold = Proposal("BTC/USDT", "HOLD", 0.0, source="proprietary")
        intent = arbitrate(long, hold, min_sources=1, min_confidence=50)
        assert intent is not None and intent.sources == ["math_engine"], \
            "abstaining HOLD must not block a lone active source in 1-source mode"

        # A CONFLICTING active source still blocks even in single-source mode.
        short = Proposal("BTC/USDT", "SHORT", 60.0, source="proprietary")
        assert arbitrate(long, short, min_sources=1, min_confidence=50) is None, \
            "conflicting active source must veto even in 1-source mode"
        print("   ✅ Single-source mode works (confidence floor, abstentions ignored, conflicts veto)")
    except Exception as e:
        print(f"   ❌ Single-source error: {e}")
        traceback.print_exc()
        errors.append(("single_source", e))

    print("\n4. Testing intent hash stability (dedup key)...")
    try:
        from arbitrator import Proposal, arbitrate, intent_hash

        h1 = intent_hash("BTC/USDT", "LONG", "1h")
        h2 = intent_hash("BTC/USDT", "LONG", "1h")
        h3 = intent_hash("BTC/USDT", "LONG", "4h")
        h4 = intent_hash("BTC/USDT", "SHORT", "1h")
        assert h1 == h2, "same intent must hash identically"
        assert h1 != h3 and h1 != h4, "different window/direction must hash differently"

        # The full path: two sources, same trade → same hash as a re-run.
        a1 = Proposal("ETH/USDT", "SHORT", 50.0, source="math_engine")
        b1 = Proposal("ETH/USDT", "SHORT", 50.0, source="proprietary")
        i1 = arbitrate(a1, b1)
        i2 = arbitrate(a1, b1)
        assert i1.intent_hash == i2.intent_hash
        print(f"   ✅ Stable hash: {h1} (24 chars, deterministic)")
    except Exception as e:
        print(f"   ❌ Hash error: {e}")
        traceback.print_exc()
        errors.append(("hash", e))

    print("\n5. Testing signals.py dict → Proposal conversion...")
    try:
        from arbitrator import proposal_from_signal_dict

        d = {"symbol": "SOL/USDT", "direction": "LONG", "confidence": 42,
             "entry": 150.0, "stop_loss": 145.0, "take_profit": 165.0}
        p = proposal_from_signal_dict(d)
        assert p.direction == "LONG" and p.entry == 150.0
        assert p.source == "math_engine" and p.window == "1h"

        p2 = proposal_from_signal_dict({"symbol": "X", "direction": "weird"})
        assert p2.direction == "HOLD", "unknown direction must map to HOLD"
        print("   ✅ Dict conversion works (and sanitizes unknown directions)")
    except Exception as e:
        print(f"   ❌ Conversion error: {e}")
        traceback.print_exc()
        errors.append(("convert", e))

    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — arbitrator working")


if __name__ == "__main__":
    main()
