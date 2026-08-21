"""Check positions."""
import requests

r = requests.get('http://localhost:8501/api/positions')
data = r.json()

print(f"Total positions: {data.get('count', 0)}")
print()
for p in data.get('positions', []):
    symbol = p['symbol']
    side = p['side']
    qty = p['qty']
    entry = p.get('entry', 'N/A')
    current = p.get('current_price', 'N/A')
    pnl = p.get('unrealized_pnl', 'N/A')
    source = p.get('source', 'unknown')
    print(f"  {symbol}: {side} qty={qty} entry={entry} current={current} pnl={pnl} source={source}")
