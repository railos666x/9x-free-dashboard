"""Test Binance testnet trade."""
import sys
sys.path.insert(0, 'backend')

import sqlite3
import config
from execution import BinanceBroker

# Connect to database
conn = sqlite3.connect('data/crypto_data.db')

# Create a test order
order = {
    'symbol': 'BTC/USDT',
    'side': 'LONG',
    'qty': 0.001,  # Small amount for testing
}

print(f"Testing Binance trade: {order}")
print(f"Using testnet: {config.BINANCE_EXEC_API_KEY[:10]}...")

try:
    broker = BinanceBroker()
    result = broker.place_market(conn, order)
    print(f"\nSUCCESS!")
    print(f"Status: {result.get('status')}")
    print(f"Fill price: {result.get('fill_price')}")
    print(f"Broker ref: {result.get('broker_ref')}")
except Exception as e:
    print(f"\nERROR: {e}")
