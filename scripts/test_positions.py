"""Test positions endpoint."""
import requests
r = requests.get("http://localhost:8501/api/positions", timeout=10)
data = r.json()
print(f"Count: {data['count']}")
for p in data["positions"]:
    sym = p["symbol"]
    side = p["side"].upper()
    qty = p["qty"]
    entry = p["entry"]
    cur = p["current_price"]
    pnl = p["unrealized_pnl"]
    src = p.get("source", "journal")
    print(f"  {sym} {side} qty={qty} entry={entry} cur={cur} pnl={pnl} src={src}")
