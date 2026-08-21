"""Check crypto signals."""
import sys
sys.path.insert(0, 'backend')
import sqlite3

conn = sqlite3.connect('data/crypto_data.db')

from news_analyst import signal_for, analytics_for

# Test crypto symbols
symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]

print("=== CRYPTO SIGNALS ===")
for symbol in symbols:
    analytics = analytics_for(conn, symbol)
    if analytics:
        score = analytics.get('score', 0)
        conf = analytics.get('confidence', 0)
        stance = analytics.get('stance', 'neutral')
        signal = signal_for(conn, symbol)
        action = signal.get('action', 'HOLD')
        reason = signal.get('reason', '')[:80]
        blocks = signal.get('block_reasons', [])
        print(f"\n{symbol}:")
        print(f"  Score: {score}, Conf: {conf}, Stance: {stance}")
        print(f"  Action: {action}")
        print(f"  Reason: {reason}")
        if blocks:
            print(f"  Blocks: {blocks}")
    else:
        print(f"\n{symbol}: No analytics")
