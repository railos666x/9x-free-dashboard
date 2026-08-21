"""Check crypto orders."""
import requests

r = requests.get('http://localhost:8501/api/orders?limit=50')
orders = r.json().get('orders', [])

crypto = [o for o in orders if '/USDT' in o.get('symbol', '')]
print(f"Crypto orders: {len(crypto)}")
for o in crypto[:10]:
    print(f"  {o['symbol']}: {o['side']} qty={o.get('qty')} status={o['status']} source={o['source']}")

print("\nAll recent orders:")
for o in orders[:15]:
    print(f"  {o['symbol']}: {o['side']} status={o['status']} source={o['source']}")
