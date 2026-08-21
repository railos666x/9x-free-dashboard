"""Test MT5 connection and execution."""
import sys
sys.path.insert(0, "backend")

try:
    import MetaTrader5 as mt5
    print("MetaTrader5 package: INSTALLED")
except ImportError:
    print("MetaTrader5 package: NOT INSTALLED")
    print("Run: pip install MetaTrader5")
    sys.exit(1)

import config
print(f"EXNESS_MT5_ENABLED: {config.EXNESS_MT5_ENABLED}")
print(f"MT5_TERMINAL_PATH: {config.MT5_TERMINAL_PATH}")

# Try to initialize
if config.MT5_TERMINAL_PATH:
    ok = mt5.initialize(config.MT5_TERMINAL_PATH)
else:
    ok = mt5.initialize()

if ok:
    print("MT5 initialize: SUCCESS")
    info = mt5.account_info()
    if info:
        print(f"Account: {info.login}")
        print(f"Balance: {info.balance}")
        print(f"Equity: {info.equity}")
        print(f"Margin free: {info.margin_free}")
    else:
        print(f"account_info failed: {mt5.last_error()}")
    
    # Check positions
    positions = mt5.positions_get()
    print(f"Open positions: {len(positions) if positions else 0}")
    if positions:
        for p in positions[:5]:
            print(f"  {p.symbol} {p.type} vol={p.volume} price={p.price_open}")
    
    mt5.shutdown()
else:
    print(f"MT5 initialize FAILED: {mt5.last_error()}")
    print("Make sure MT5 terminal is running and logged in")
