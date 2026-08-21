"""Trigger bot cycle and check results."""
import requests

r = requests.post("http://localhost:8501/api/bot/run", json={}, timeout=300)
d = r.json()

print("=== Bot Cycle Results ===")
print(f"Executed: {d.get('executed', [])}")
print(f"Held: {len(d.get('held', []))} symbols")
print(f"Dry run: {d.get('dry_run')}")
print(f"AI mode: {d.get('ai_mode')}")
print(f"Elapsed: {d.get('elapsed_s')}s")

print("\nHeld symbols (first 10):")
for h in d.get('held', [])[:10]:
    print(f"  {h['symbol']}: {h.get('reason', 'unknown')}")
