"""Test commodities - Gold, Silver, Oil."""
import sys
sys.path.insert(0, "backend")
import sqlite3

conn = sqlite3.connect("data/crypto_data.db")

from news_analyst import signal_for, analytics_for

# Test commodities
symbols = ["XAU/USD", "XAG/USD", "WTI/USD", "BRENT/USD", "BTC/USDT", "ETH/USDT"]

for symbol in symbols:
    print(f"\n=== {symbol} ===")
    analytics = analytics_for(conn, symbol)
    if analytics:
        print(f"  Score: {analytics.get('score')}, Conf: {analytics.get('confidence')}")
        print(f"  Stance: {analytics.get('stance')}")
        print(f"  Summary: {analytics.get('summary')}")
        signal = signal_for(conn, symbol)
        print(f"  Action: {signal.get('action')}")
        print(f"  Reason: {signal.get('reason')}")
        if signal.get('block_reasons'):
            print(f"  Blocks: {signal.get('block_reasons')}")
    else:
        print("  No analytics")
