"""Test multiple symbols for signals."""
import sys
sys.path.insert(0, "backend")
import sqlite3

conn = sqlite3.connect("data/crypto_data.db")

from news_analyst import signal_for, analytics_for

# Test multiple symbols
symbols = ["USD/JPY", "EUR/GBP", "GBP/USD", "XAU/USD", "EUR/USD", "AUD/USD"]

for symbol in symbols:
    print(f"\n=== {symbol} ===")
    analytics = analytics_for(conn, symbol)
    if analytics:
        print(f"  Score: {analytics.get('score')}, Conf: {analytics.get('confidence')}")
        signal = signal_for(conn, symbol)
        print(f"  Action: {signal.get('action')}")
        print(f"  Reason: {signal.get('reason')}")
        if signal.get('block_reasons'):
            print(f"  Blocks: {signal.get('block_reasons')}")
    else:
        print("  No analytics")
