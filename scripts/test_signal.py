"""Test signal_for directly."""
import sys
sys.path.insert(0, "backend")
import sqlite3

conn = sqlite3.connect("data/crypto_data.db")

from news_analyst import signal_for, analytics_for

# Test USD/JPY which should have triggered SHORT
symbol = "USD/JPY"
print(f"=== Testing {symbol} ===\n")

# Check analytics
analytics = analytics_for(conn, symbol)
print(f"Analytics: {analytics}")

# Check signal
signal = signal_for(conn, symbol)
print(f"\nSignal: {signal}")
