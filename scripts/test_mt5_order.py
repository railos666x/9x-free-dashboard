"""Test MT5 order with fill-type fix."""
import sys
sys.path.insert(0, 'backend')

import sqlite3
import MetaTrader5 as mt5
from execution import MT5Broker

# Initialize MT5
if not mt5.initialize():
    print(f"MT5 init failed: {mt5.last_error()}")
    sys.exit(1)

print("MT5 Connected!")

# Check XAUUSD filling mode
info = mt5.symbol_info('XAUUSD')
if info:
    print(f"\nXAUUSD info:")
    print(f"  Volume step: {info.volume_step}")
    print(f"  Volume min: {info.volume_min}")
    print(f"  Filling modes available: {info.filling_mode if hasattr(info, 'filling_mode') else 'N/A'}")
    
    # Check what filling types are supported
    filling = getattr(info, 'filling', None)
    print(f"  Filling attribute: {filling}")
    
    # Try to get all attributes
    print(f"  All attrs with 'fill': {[a for a in dir(info) if 'fill' in a.lower()]}")

# Test order - use proper lot size for XAUUSD (1 lot = 100 oz)
# Minimum is usually 0.01 lots = 1 oz
conn = sqlite3.connect('data/crypto_data.db')
order = {
    'symbol': 'XAU/USD',
    'side': 'LONG',
    'qty': 1.0,  # 1 unit = 0.01 lots for XAUUSD (100 contract size)
}

print(f"\nTesting MT5 trade: {order}")

# Debug volume calculation
contract = float(getattr(info, 'trade_contract_size', 100000))
step = float(getattr(info, 'volume_step', 0.01))
raw = order['qty'] / contract if contract else 0.0
import math
volume = math.floor(raw / step + 1e-9) * step
print(f"\nVolume debug:")
print(f"  Contract size: {contract}")
print(f"  Volume step: {step}")
print(f"  Raw lots: {raw}")
print(f"  Rounded volume: {volume}")
print(f"  Filling mode: {getattr(info, 'filling_mode', 'N/A')}")

try:
    broker = MT5Broker()
    result = broker.place_market(conn, order)
    print(f"\nSUCCESS!")
    print(f"Status: {result.get('status')}")
    print(f"Fill price: {result.get('fill_price')}")
    print(f"Broker ref: {result.get('broker_ref')}")
except Exception as e:
    print(f"\nERROR: {e}")

mt5.shutdown()
