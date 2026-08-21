"""Run bot with forced fresh LLM analysis."""
import sys
sys.path.insert(0, 'backend')

import database
import news_analyst
import bot

conn = database.connect()

print("Forcing fresh LLM analysis (bypassing cache)...")
# Force refresh the market analysis cache
result = news_analyst.analyze_market(conn, force=True)
if result:
    coins = result.get('coins', [])
    with_sl = [c for c in coins if c.get('stop_loss_pct') and c['stop_loss_pct'] > 0]
    print(f"Analysis refreshed: {len(coins)} coins, {len(with_sl)} with SL/TP data")
else:
    print("ERROR: Analysis failed!")
    sys.exit(1)

print("\nRunning bot cycle (live mode)...")
summary = bot.run_once(conn, dry_run=False)

print(f"\n=== BOT RESULTS ===")
print(f"Executed: {len(summary.get('executed', []))}")
print(f"Held: {len(summary.get('held', []))}")
print(f"Errors: {len(summary.get('errors', []))}")
print(f"Elapsed: {summary.get('elapsed_s', 0):.1f}s")

print("\n--- EXECUTED TRADES ---")
for e in summary.get('executed', [])[:10]:
    symbol = e.get('symbol')
    direction = e.get('direction') or e.get('side')
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
for e in summary.get('errors', [])[:10]:
    symbol = e.get('symbol')
    error = e.get('error', 'unknown')[:100]
    print(f"  {symbol}: {error}")

print("\n--- HELD (sample) ---")
for e in summary.get('held', [])[:10]:
    symbol = e.get('symbol')
    reason = e.get('reason', 'unknown')[:80]
    print(f"  {symbol}: {reason}")
