"""Run the bot and check results."""
import requests

print("Running bot (this may take 60-120s for LLM analysis)...")
r = requests.post('http://localhost:8501/api/bot/run', json={'dry_run': False}, timeout=300)
data = r.json()

print(f"\n=== BOT RESULTS ===")
print(f"Executed: {len(data.get('executed', []))}")
print(f"Held: {len(data.get('held', []))}")
print(f"Errors: {len(data.get('errors', []))}")

print("\n--- EXECUTED TRADES ---")
for e in data.get('executed', [])[:10]:
    symbol = e.get('symbol')
    direction = e.get('direction')
    status = e.get('status')
    qty = e.get('qty')
    entry = e.get('entry') or e.get('fill_price')
    sl = e.get('stop_loss')
    tp = e.get('take_profit')
    print(f"  {symbol}: {direction} qty={qty} status={status}")
    print(f"    entry={entry} SL={sl} TP={tp}")
    if entry and sl:
        sl_pct = abs(float(entry) - float(sl)) / float(entry) * 100
        print(f"    SL distance: {sl_pct:.2f}%")
    if entry and tp:
        tp_pct = abs(float(tp) - float(entry)) / float(entry) * 100
        print(f"    TP distance: {tp_pct:.2f}%")

print("\n--- ERRORS ---")
for e in data.get('errors', [])[:10]:
    symbol = e.get('symbol')
    error = e.get('error', 'unknown')[:100]
    print(f"  {symbol}: {error}")

print("\n--- HELD (sample) ---")
for e in data.get('held', [])[:5]:
    symbol = e.get('symbol')
    reason = e.get('reason', 'unknown')
    print(f"  {symbol}: {reason}")
