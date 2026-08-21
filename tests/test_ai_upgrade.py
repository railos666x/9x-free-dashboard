"""
Tests for the 2026-08-17 AI Trading System Upgrade.

Covers all new modules:
    - ai_confirm (regime detection, technical confirmation, session)
    - dxy_filter (DXY master filter)
    - smart_money (institutional order flow)
    - calendar_guard (economic calendar)
    - circuit_breaker (tiered risk control)
    - ai_feedback (adaptive calibration)
    - test_ai (paper-trading engine)

Run:
    python tests/test_ai_upgrade.py
"""
import sys
from pathlib import Path

# Tests run from tests/; the backend modules live one level up in backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import traceback
import numpy as np
import pandas as pd


def _make_candles(n=200, trend="up", seed=42):
    """Generate synthetic candle data for testing."""
    np.random.seed(seed)
    base = 1.1000  # forex-like price
    drift = 0.0003 if trend == "up" else -0.0003 if trend == "down" else 0.0
    prices = base + np.cumsum(np.random.randn(n) * 0.001 + drift)
    df = pd.DataFrame({
        "open_time": list(range(0, n * 3600000, 3600000)),
        "open": prices - np.random.rand(n) * 0.0005,
        "high": prices + np.random.rand(n) * 0.002,
        "low": prices - np.random.rand(n) * 0.002,
        "close": prices,
        "volume": np.random.rand(n) * 1000 + 500,
        "close_time": list(range(3600000, (n + 1) * 3600000, 3600000)),
    })
    return df


def _seed_db(conn, symbol="EUR/USD", n=200):
    """Load synthetic candles into the test DB."""
    import database
    df = _make_candles(n)
    database.upsert_candles(conn, symbol, "1h", df.to_dict("records"))
    conn.commit()


def main():
    errors = []
    passed = 0

    # ── 1. Module imports ────────────────────────────────────────────
    print("1. Testing imports...")
    try:
        import ai_confirm
        import dxy_filter
        import smart_money
        import calendar_guard
        import circuit_breaker
        import ai_feedback
        import test_ai
        print("   OK: All new modules import successfully")
        passed += 1
    except Exception as e:
        print(f"   FAIL: Import error: {e}")
        traceback.print_exc()
        errors.append(("import", e))
        print("\nCannot continue without imports.")
        return

    # ── 2. Database setup ────────────────────────────────────────────
    print("\n2. Setting up test database...")
    try:
        import database
        import config
        # Use a separate test DB to avoid touching production data
        original_db = config.DB_PATH
        config.DB_PATH = Path(str(original_db).replace("crypto_data.db", "test_upgrade.db"))
        conn = database.connect()
        database.init_db(conn)
        _seed_db(conn, "EUR/USD", 200)
        _seed_db(conn, "XAU/USD", 200)
        # Also seed DXY for the filter
        _seed_db(conn, "DXY", 200)
        print(f"   OK: Test DB seeded (EUR/USD, XAU/USD, DXY)")
        passed += 1
    except Exception as e:
        print(f"   FAIL: DB setup error: {e}")
        traceback.print_exc()
        errors.append(("db_setup", e))
        return

    # ── 3. AI Confirm — regime detection ─────────────────────────────
    print("\n3. Testing regime detection...")
    try:
        regime_info = ai_confirm.detect_regime(conn, "EUR/USD")
        assert "regime" in regime_info, "missing regime key"
        assert regime_info["regime"] in ("trending_up", "trending_down", "ranging", "volatile"), \
            f"unexpected regime: {regime_info['regime']}"
        assert "adx" in regime_info, "missing ADX"
        assert "atr" in regime_info, "missing ATR"
        print(f"   OK: regime={regime_info['regime']} ADX={regime_info['adx']:.1f} "
              f"ATR={regime_info['atr']:.6f}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("regime", e))

    # ── 4. AI Confirm — technical confirmation ───────────────────────
    print("\n4. Testing technical confirmation...")
    try:
        tc = ai_confirm.technical_confirmation(conn, "EUR/USD", "LONG")
        assert "confirm" in tc, "missing confirm key"
        assert "score" in tc, "missing score key"
        assert 0 <= tc["score"] <= 6, f"score out of range: {tc['score']}"
        print(f"   OK: confirm={tc['confirm']} score={tc['score']}/6")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("technical_confirm", e))

    # ── 5. AI Confirm — session awareness ────────────────────────────
    print("\n5. Testing session awareness...")
    try:
        sess = ai_confirm.current_session()
        assert "session" in sess, "missing session key"
        assert sess["session"] in ("asian", "london", "ny", "overlap", "dead"), \
            f"unexpected session: {sess['session']}"
        mult = ai_confirm.session_multiplier()
        assert 0 <= mult <= 1.0, f"multiplier out of range: {mult}"
        print(f"   OK: session={sess['session']} mult={mult}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("session", e))

    # ── 6. AI Confirm — master gate ──────────────────────────────────
    print("\n6. Testing master gate (should_trade)...")
    try:
        result = ai_confirm.should_trade(conn, "EUR/USD", 0.6, 0.8, "LONG")
        assert "pass" in result, "missing pass key"
        assert "score" in result, "missing score key"
        assert "regime" in result, "missing regime key"
        assert "session" in result, "missing session key"
        assert "session_mult" in result, "missing session_mult key"
        assert "reasons" in result, "missing reasons key"
        print(f"   OK: pass={result['pass']} score={result['score']} "
              f"regime={result['regime']} session={result['session']}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("master_gate", e))

    # ── 7. DXY filter ────────────────────────────────────────────────
    print("\n7. Testing DXY filter...")
    try:
        dxy_state = dxy_filter.get_dxy_state(conn)
        assert "trend" in dxy_state, "missing trend key"
        dxy_result = dxy_filter.dxy_allows_trade("EUR/USD", "LONG", dxy_state)
        assert "allowed" in dxy_result, "missing allowed key"
        assert "score" in dxy_result, "missing score key"
        print(f"   OK: dxy_trend={dxy_state['trend']} allowed={dxy_result['allowed']} "
              f"score={dxy_result['score']}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("dxy_filter", e))

    # ── 8. Smart Money ───────────────────────────────────────────────
    print("\n8. Testing smart money confluence...")
    try:
        smc = smart_money.smart_money_confluence(conn, "EUR/USD", "LONG")
        assert "score" in smc, "missing score key"
        assert 0 <= smc["score"] <= 4, f"score out of range: {smc['score']}"
        assert "details" in smc, "missing details key"
        print(f"   OK: smc_score={smc['score']}/4 details={smc['details']}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("smart_money", e))

    # ── 9. Calendar Guard ────────────────────────────────────────────
    print("\n9. Testing calendar guard...")
    try:
        cal = calendar_guard.calendar_allows_trade(conn, "EUR/USD")
        assert "allowed" in cal, "missing allowed key"
        assert "reasons" in cal, "missing reasons key"
        spread = calendar_guard.get_spread_warning(conn, "EUR/USD")
        assert "warning" in spread, "missing warning key"
        print(f"   OK: allowed={cal['allowed']} spread_warning={spread['warning']}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("calendar_guard", e))

    # ── 10. Circuit Breaker ──────────────────────────────────────────
    print("\n10. Testing circuit breaker...")
    try:
        cb = circuit_breaker.current_risk_state(conn)
        assert "level" in cb, "missing level key"
        assert "risk_multiplier" in cb, "missing risk_multiplier key"
        assert 0 <= cb["level"] <= 4, f"level out of range: {cb['level']}"
        assert 0 <= cb["risk_multiplier"] <= 1.0, f"mult out of range: {cb['risk_multiplier']}"
        health = circuit_breaker.strategy_health_check(conn)
        assert "healthy" in health, "missing healthy key"
        print(f"   OK: level={cb['level']} mult={cb['risk_multiplier']} "
              f"healthy={health['healthy']}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("circuit_breaker", e))

    # ── 11. Test AI engine ───────────────────────────────────────────
    print("\n11. Testing test_ai engine...")
    try:
        from arbitrator import Proposal, Intent
        # Create a mock proposal and intent
        proposal = Proposal(
            symbol="EUR/USD", direction="LONG", confidence=65.0,
            source="ai_news", window="news",
            reasons=["test signal"],
            regime="trending_up", confirm_score=4,
            dxy_score=2, smc_score=3,
            session="london", session_mult=1.0,
        )
        intent = Intent(
            symbol="EUR/USD", direction="LONG",
            entry=None, stop_loss=None, take_profit=None,
            sources=["ai_news"], window="news",
            confidence=65.0,
            intent_hash="test_hash_001",
            reasons=["test signal"],
        )
        result = test_ai.record_paper_trade(conn, proposal, intent)
        assert "trade" in result, "missing trade key"
        assert "duplicate" in result, "missing duplicate key"
        if result["trade"]:
            t = result["trade"]
            print(f"   OK: trade_id={t.get('id')} entry={t.get('entry_price')} "
                  f"SL={t.get('stop_loss')} TP={t.get('take_profit')}")
        else:
            print(f"   OK: trade not recorded (no price data or error)")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("test_ai", e))

    # ── 12. Test AI — position management ────────────────────────────
    print("\n12. Testing test position management...")
    try:
        closed = test_ai.manage_test_positions(conn)
        # May or may not close anything depending on synthetic price vs SL/TP
        print(f"   OK: closed {len(closed)} test positions")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("test_position_mgmt", e))

    # ── 13. Test AI — portfolio summary ──────────────────────────────
    print("\n13. Testing test portfolio summary...")
    try:
        summary = test_ai.test_portfolio_summary(conn)
        assert "total" in summary, "missing total key"
        print(f"   OK: total={summary.get('total', 0)} "
              f"open={summary.get('open_trades', 0)}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("test_portfolio", e))

    # ── 14. AI Feedback ──────────────────────────────────────────────
    print("\n14. Testing AI feedback loop...")
    try:
        report = ai_feedback.calibration_report(conn, days=30)
        assert "total_trades" in report, "missing total_trades"
        thresholds = ai_feedback.adaptive_thresholds(conn)
        assert "sentiment_long" in thresholds, "missing sentiment_long"
        assert "adapted" in thresholds, "missing adapted key"
        print(f"   OK: report_trades={report.get('total_trades', 0)} "
              f"adapted={thresholds.get('adapted')}")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("feedback", e))

    # ── 15. Duplicate detection ──────────────────────────────────────
    print("\n15. Testing duplicate detection...")
    try:
        from arbitrator import Proposal, Intent
        proposal2 = Proposal(
            symbol="EUR/USD", direction="LONG", confidence=65.0,
            source="ai_news", window="news", reasons=["dup test"])
        intent2 = Intent(
            symbol="EUR/USD", direction="LONG",
            entry=None, stop_loss=None, take_profit=None,
            sources=["ai_news"], window="news",
            confidence=65.0, intent_hash="test_hash_001",
            reasons=["dup test"])
        result2 = test_ai.record_paper_trade(conn, proposal2, intent2)
        assert result2.get("duplicate") == True, "should be duplicate"
        print(f"   OK: duplicate correctly detected")
        passed += 1
    except Exception as e:
        print(f"   FAIL: {e}")
        traceback.print_exc()
        errors.append(("duplicate", e))

    # ── Cleanup ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {len(errors)} failed")
    if errors:
        print(f"Failed tests: {[e[0] for e in errors]}")
        for name, err in errors:
            print(f"  {name}: {err}")
    else:
        print("All tests passed!")

    # Restore original DB path
    config.DB_PATH = original_db
    conn.close()

    # Clean up test DB
    import os
    test_db = str(config.DB_PATH).replace("crypto_data.db", "test_upgrade.db")
    for suffix in ["", "-shm", "-wal"]:
        try:
            os.remove(test_db + suffix)
        except FileNotFoundError:
            pass

    return len(errors) == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
