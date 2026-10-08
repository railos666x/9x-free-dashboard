"""Close ALL open MT5/Exness positions via the project's own MT5 broker wrapper.
Destructive and one-way — confirm before running."""
import sys

sys.path.insert(0, "backend")
import execution  # noqa: E402

broker = execution.get_broker()
mt5b = getattr(broker, "_mt5", broker)  # hybrid wraps MT5
positions = mt5b.fetch_positions()
print(f"open broker positions: {len(positions)}")
if not positions:
    print("nothing to close")
    sys.exit(0)

for p in positions:
    sym = p.get("symbol")
    order = {"symbol": sym, "side": "long" if str(p.get("side")).lower().startswith(("l", "b")) else "short",
             "qty": p.get("qty"), "interval": "1h"}
    try:
        res = mt5b.close_market(None, order)
        print(f"closed {sym}: {res}")
    except Exception as e:
        print(f"FAILED {sym}: {type(e).__name__}: {e}")

print("done")
