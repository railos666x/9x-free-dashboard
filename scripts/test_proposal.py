"""Test the full proposal pipeline for EUR/GBP."""
import sys
sys.path.insert(0, "backend")
import sqlite3

conn = sqlite3.connect("data/crypto_data.db")

from news_analyst import signal_for
import ai_agents

symbol = "EUR/GBP"
print(f"=== Testing {symbol} ===\n")

# Step 1: Get signal
signal = signal_for(conn, symbol)
print(f"1. Signal: {signal.get('action')}")
print(f"   Reason: {signal.get('reason')}")
print(f"   Conf: {signal.get('confidence')}")

# Step 2: Check gate
if signal.get('action') in ('LONG', 'SHORT'):
    print(f"\n2. Checking chart-confluence gate...")
    gate = ai_agents.gate_news_trade(conn, symbol, signal['action'])
    print(f"   Gate pass: {gate.get('pass')}")
    print(f"   Gate reason: {gate.get('reason')}")
    print(f"   Verdict: {gate.get('verdict')}")
else:
    print(f"\n2. Signal is HOLD, skipping gate check")
