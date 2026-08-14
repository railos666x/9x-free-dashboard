"""
Quick system test — run this to verify the math engine + factors work.

    python test_system.py
"""
import sys
from pathlib import Path

# Tests run from tests/; the backend modules live one level up in backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import traceback

# Windows consoles default to cp1252, which can't print the emoji used in
# this script's output — switch stdout to UTF-8 (best-effort) so the test
# output never crashes the console.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def main():
    errors = []

    # 1. Test imports
    print("1. Testing imports...")
    try:
        import math_engine
        import factors
        import indicators
        import signals
        import backtester
        print("   ✅ All modules import successfully")
    except Exception as e:
        print(f"   ❌ Import error: {e}")
        traceback.print_exc()
        errors.append(("import", e))

    # 2. Test factors with synthetic data
    print("\n2. Testing factor computation...")
    try:
        import pandas as pd
        import numpy as np

        # Create synthetic candle data (100 bars, uptrend)
        np.random.seed(42)
        n = 200
        prices = 50000 + np.cumsum(np.random.randn(n) * 200 + 50)
        df = pd.DataFrame({
            "open": prices - np.random.rand(n) * 100,
            "high": prices + np.random.rand(n) * 200,
            "low": prices - np.random.rand(n) * 200,
            "close": prices,
            "volume": np.random.rand(n) * 1000 + 500,
        })

        # Test each factor individually
        trend = factors.trend_factor(df)
        print(f"   Trend:     {trend.score:+.3f} (conf={trend.confidence:.2f}) — {trend.detail}")

        momentum = factors.momentum_factor(df)
        print(f"   Momentum:  {momentum.score:+.3f} (conf={momentum.confidence:.2f}) — {momentum.detail}")

        carry = factors.carry_factor(0.0001)
        print(f"   Carry:     {carry.score:+.3f} (conf={carry.confidence:.2f}) — {carry.detail}")

        reversal = factors.reversal_factor(df)
        print(f"   Reversal:  {reversal.score:+.3f} (conf={reversal.confidence:.2f}) — {reversal.detail}")

        value = factors.value_factor(df)
        print(f"   Value:     {value.score:+.3f} (conf={value.confidence:.2f}) — {value.detail}")

        bundle = factors.compute_all_factors(df, funding_rate=0.0001)
        print(f"   Composite: {bundle.composite:+.3f}")
        print("   ✅ Factors working")
    except Exception as e:
        print(f"   ❌ Factor error: {e}")
        traceback.print_exc()
        errors.append(("factors", e))

    # 3. Test math engine
    print("\n3. Testing math engine...")
    try:
        import indicators as ind

        # Add indicators to synthetic data
        df_with_ind = ind.add_all(df)

        # Run through math engine
        result = math_engine.compute_trade_signal(
            df_1h=df_with_ind,
            news_score=0.0,
        )
        print(f"   Direction:  {result.direction}")
        print(f"   Confidence: {result.confidence}")
        print(f"   Regime:     {result.regime.value}")
        print(f"   ADX:        {result.adx_value}")
        print(f"   P(bull):    {result.posterior_bull:.3f}")
        print(f"   P(bear):    {result.posterior_bear:.3f}")
        print(f"   Kelly:      {result.kelly_fraction:.3f}")
        print(f"   EV/unit:    {result.ev_per_unit:.3f}")
        print(f"   Confluence: {result.confluence_score:.3f}")
        print(f"   R:R:        {result.risk_reward:.2f}")
        print(f"   Entry:      {result.entry:,.2f}")
        print(f"   SL:         {result.stop_loss:,.2f}" if result.stop_loss else "   SL: N/A")
        print(f"   TP:         {result.take_profit:,.2f}" if result.take_profit else "   TP: N/A")
        print(f"   Reasons:    {len(result.reasons)}")
        print("   ✅ Math engine working")
    except Exception as e:
        print(f"   ❌ Math engine error: {e}")
        traceback.print_exc()
        errors.append(("math_engine", e))

    # 4. Test factor-to-signal conversion
    print("\n4. Testing factor→signal conversion...")
    try:
        factor_sigs = math_engine.factors_to_signals(bundle)
        print(f"   Generated {len(factor_sigs)} signals from factors")
        for s in factor_sigs:
            d = ["BEARISH", "NEUTRAL", "BULLISH"][s.direction + 1]
            print(f"   {s.name:12s} {d:8s} str={s.strength:.2f} group={s.group}")
        print("   ✅ Factor→signal conversion working")
    except Exception as e:
        print(f"   ❌ Conversion error: {e}")
        traceback.print_exc()
        errors.append(("conversion", e))

    # 5. Test database connection
    print("\n5. Testing database...")
    try:
        import database
        conn = database.connect()
        database.init_db(conn)  # create schema if the DB is fresh (e.g. CI)
        n = database.count_candles(conn)
        print(f"   Candles in DB: {n}")
        n_news = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
        print(f"   News stories: {n_news}")
        print("   ✅ Database working")
    except Exception as e:
        print(f"   ❌ Database error: {e}")
        errors.append(("database", e))

    # Summary
    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED — system is ready")
        print("\nNext steps:")
        print("  1. Start the server:  .venv/Scripts/python.exe server.py")
        print("  2. Open http://localhost:8501")
        print("  3. Check /api/signals for factor scores")
        print("  4. Run backtest: http://localhost:8501/api/backtest?symbol=BTC/USDT")


if __name__ == "__main__":
    main()
