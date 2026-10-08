"""Run-window performance report (one-shot, re-runnable CLI).

Answers the standing analysis debt: win rate + trajectory toward the 70%
target, per-symbol breakdown, execution-cost drag, and gate/loop
characterization. Numbers come from the journal (MT5 rows are
deal-history-reconciled, i.e. truthful). No emoji; [OK]/[WARN] markers.

The computations live in backend/run_report.py (2026-10-06) so the DAILY
DIGEST can render the same trajectory/per-symbol tables automatically —
this CLI keeps the human-readable one-shot form with identical output.
"""
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import run_stats as rr  # noqa: E402  (backend flat-import module)

DB = ROOT / "data" / "crypto_data.db"
BASE_ID = rr.BASE_ID
TARGET_WR = rr.TARGET_WR

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row


def wr_block(label, rows):
    st = rr.wr_stats(rows)
    pf = st["pf"]
    print(f"[{label}] n={st['n']} wins={st['wins']} wr={st['wr']:.1f}% "
          f"net={st['net']:+.2f} fees={st['fees']:.2f} slip={st['slip']:.2f} "
          f"avgW={st['avg_win']:+.2f} avgL={st['avg_loss']:+.2f} "
          f"PF={pf:.2f}")
    return st["wr"]


print("=== Run window (journal id > %d) ===" % BASE_ID)
ws = rr.window_summary(conn)
print("journal rows:", ws["rows"])
for status, count in sorted(ws["by_status"].items()):
    print(f"  {status}: {count}")
wr_block("window closed", rr.closed_rows(conn, "id > ?", (BASE_ID,)))
for r in rr.window_closes(conn):
    print(f"  #{r['id']} {r['symbol']} {r['side']} pnl={r['pnl']:+.2f} "
          f"exit={r['exit_reason']} slip={r['slippage_cost']}")

print("\n=== 7d closed (both venues) ===")
sev = rr.wr_stats(rr.closed_rows(
    conn, "COALESCE(exit_time, fill_time) >= date('now','-7 day')"))
print(f"n={sev['n']} wr={sev['wr']:.1f}% net={sev['net']:+.2f} "
      f"fees={sev['fees']:.2f} slip={sev['slip']:.2f}")

print("\n=== Per-symbol 7d (min 2 closed) ===")
for r in rr.per_symbol_table(conn):
    print(f"  {r['symbol']:<12} n={r['n']:<3} wr={100.0*r['w']/r['n']:.0f}% "
          f"net={r['net']:+.2f}")

print("\n=== Win-rate trajectory (all journal closes, chronological) ===")
traj = rr.trajectory(conn)
rows = rr.closed_rows(conn)
wr_block("all-time", rows)
for k in (10, 20, 50):
    key = f"last-{k}"
    if key in traj["windows"]:
        wr_block(key, rows[-k:])
seq = traj["buckets"]
if len(seq) >= 2:
    print("  rolling-10 by bucket:", " ".join(f"{v:.0f}" for v in seq))
    first, last = traj["first_half"], traj["last_half"]
    trend = traj["trend"]
    print(f"  bucket-mean first-half={first:.1f}% last-half={last:.1f}% "
          f"-> {trend}")
if "wr20" in traj:
    print(f"  last-20 wr={traj['wr20']:.0f}%: need {traj['need_wins']} more "
          f"win(s) inside a rolling 20 to touch {TARGET_WR:.0f}%")

print("\n=== Errors / gate notes (window + 7d) ===")
for r in rr.error_notes(conn):
    e = (r["error"] or "")[:90]
    print(f"  #{r['id']} {r['symbol']} [{r['source']}] {e}")
print("  news30m rows:", rr.news_30m(conn))

conn.close()
print("[OK] run_report done")
