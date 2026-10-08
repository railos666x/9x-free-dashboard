"""Trade forensics: why did each closed trade win or lose?

Buckets the closed book by outcome driver so the strategy can be tuned on
evidence, not vibes:
  - exit reason (sl / tp / trailing / time_decay / broker_closed)
  - regime at entry (with-trend / counter-trend / ranging)
  - news corroboration (outlets= tag on 2026-09-30+ rows)
  - R/R geometry (TP distance vs SL distance at entry)

Run:  .venv/Scripts/python.exe scripts/trade_forensics.py [--recent 20]
"""
import sys
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import config  # noqa: E402

RECENT = 20
if "--recent" in sys.argv:
    try:
        RECENT = int(sys.argv[sys.argv.index("--recent") + 1])
    except (IndexError, ValueError):
        pass

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT * FROM orders WHERE status='closed' AND pnl IS NOT NULL "
    "ORDER BY id DESC LIMIT ?", (RECENT,)).fetchall()

if not rows:
    print("no closed trades yet")
    sys.exit(0)

wins = [r for r in rows if (r["pnl"] or 0) > 0]
losses = [r for r in rows if (r["pnl"] or 0) <= 0]
total = sum(r["pnl"] or 0 for r in rows)
print(f"closed trades analyzed: {len(rows)} | wins {len(wins)} "
      f"({100*len(wins)/len(rows):.1f}%) | losses {len(losses)} | "
      f"net PnL {total:+.2f}")

print("\n── by exit reason ──")
by_reason = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    e = (r["exit_reason"] or "?")[:24]
    by_reason[e][0] += 1
    by_reason[e][1] += 1 if (r["pnl"] or 0) > 0 else 0
    by_reason[e][2] += r["pnl"] or 0
for e, (n, w, p) in sorted(by_reason.items(), key=lambda kv: -kv[1][2]):
    print(f"  {e:24s} n={n:3d} win%={100*w/n:5.1f} pnl={p:+10.2f}")

print("\n── by regime at entry ──")
by_reg = defaultdict(lambda: [0, 0, 0.0])
for r in rows:
    g = (r["trend_regime"] or "unknown")[:20]
    by_reg[g][0] += 1
    by_reg[g][1] += 1 if (r["pnl"] or 0) > 0 else 0
    by_reg[g][2] += r["pnl"] or 0
for g, (n, w, p) in sorted(by_reg.items(), key=lambda kv: -kv[1][2]):
    print(f"  {g:20s} n={n:3d} win%={100*w/n:5.1f} pnl={p:+10.2f}")

print("\n── R/R geometry of SL-closed losers (TP too close?) ──")
bad_rr = 0
for r in losses:
    if (r["exit_reason"] or "") not in ("sl", "time_decay_small_loss"):
        continue
    entry = r["entry"] or r["fill_price"] or 0
    sl, tp = r["stop_loss"] or 0, r["take_profit"] or 0
    if entry and sl and tp:
        risk = abs(entry - sl)
        rew = abs(tp - entry)
        rr = rew / risk if risk else 0
        if rr < 1.2:
            bad_rr += 1
            if bad_rr <= 8:
                print(f"  {r['symbol']:10s} R/R {rr:.2f}  SL {risk:.5f} vs TP {rew:.5f}"
                      f"  pnl {r['pnl']:+.2f}  {(r['reasons'] or '')[:60]}")
print(f"  → {bad_rr} losing SL exits had R/R < 1.2 (now refused by the rr_floor)")

print("\n── single-outlet news on losers (noise?) ──")
solo = [r for r in losses if "outlets=1" in (r["reasons"] or "")]
print(f"  → {len(solo)} losers carried a single-outlet news tag "
      "(noise gate now blocks these)")

print("\n── newest 10 outcomes ──")
for r in rows[:10]:
    why = (r["reasons"] or "").replace("\n", " ")[:90]
    print(f"  #{r['id']} {r['symbol']:10s} {r['side']:5s} pnl={r['pnl']:+9.2f} "
          f"via {r['exit_reason']} | {why}")
