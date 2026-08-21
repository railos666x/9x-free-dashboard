"""Force a fresh news analysis and check SL/TP percentages."""
import sys
sys.path.insert(0, 'backend')

import database
import news_analyst

conn = database.connect()
print("Forcing fresh LLM analysis (this may take 30-60s)...")
result = news_analyst.analyze_market(conn, force=True)

if not result:
    print("ERROR: Analysis returned None!")
    sys.exit(1)

coins = result.get('coins', [])
print(f"\nTotal coins: {len(coins)}")
print(f"Model: {result.get('model')}")
print()

# Show coins with SL/TP data
with_sl = [c for c in coins if c.get('stop_loss_pct') or c.get('take_profit_pct')]
print(f"Coins with SL/TP data: {len(with_sl)}")
print()

for c in sorted(coins, key=lambda x: abs(x.get('score', 0)), reverse=True)[:20]:
    sym = c['symbol']
    score = c.get('score', 0)
    conf = c.get('confidence')
    sl = c.get('stop_loss_pct', 'N/A')
    tp = c.get('take_profit_pct', 'N/A')
    conf_str = f"{conf:.2f}" if conf else "None"
    print(f"  {sym:12s} score={score:+4d} conf={conf_str:6s} SL={sl}% TP={tp}%")
