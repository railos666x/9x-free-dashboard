"""Check if AI is returning SL/TP percentages."""
import requests

r = requests.get('http://localhost:8501/api/news/analytics', timeout=10)
data = r.json()
coins = data.get('coins', [])
print(f"Total coins: {len(coins)}")
print(f"Model: {data.get('model')}")
print()

# Show coins with actual signals
active = [c for c in coins if c.get('confidence') and c['confidence'] > 0.1]
print(f"Coins with confidence > 0.1: {len(active)}")
print()

for c in sorted(active, key=lambda x: abs(x.get('score', 0)), reverse=True)[:20]:
    sym = c['symbol']
    score = c.get('score', 0)
    conf = c.get('confidence', 0)
    sl = c.get('stop_loss_pct', 'N/A')
    tp = c.get('take_profit_pct', 'N/A')
    stance = c.get('stance', '?')
    print(f"  {sym:12s} score={score:+4d} conf={conf:.2f} stance={stance:8s} SL={sl} TP={tp}")

print()
print("--- All coins (first 30) ---")
for c in coins[:30]:
    sym = c['symbol']
    score = c.get('score', 0)
    conf = c.get('confidence')
    sl = c.get('stop_loss_pct', 'N/A')
    tp = c.get('take_profit_pct', 'N/A')
    rule = c.get('_rule_based', False)
    conf_str = f"{conf:.2f}" if conf else "None"
    print(f"  {sym:12s} score={score:+4d} conf={conf_str:6s} SL={sl} TP={tp} {'(rule)' if rule else ''}")
