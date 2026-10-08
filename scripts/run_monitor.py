"""One-shot monitoring snapshot for the live run (compact single line)."""
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "crypto_data.db"
BASE_ID = 400  # journal baseline when the run window started (2026-10-01 03:35 UTC)

import json
conn = sqlite3.connect(DB)
new = conn.execute("SELECT COUNT(*) FROM orders WHERE id > ?", (BASE_ID,)).fetchone()[0]
fills = conn.execute(
    "SELECT COUNT(*) FROM orders WHERE id > ? AND status IN ('filled','closed')",
    (BASE_ID,)).fetchone()[0]
closed = conn.execute(
    "SELECT COUNT(*) FROM orders WHERE id > ? AND status = 'closed'",
    (BASE_ID,)).fetchone()[0]
news = conn.execute(
    "SELECT COUNT(*) FROM news WHERE fetched_at >= datetime('now', '-30 minutes')"
).fetchone()[0]

loop, ran, errs, cyc = "?", "?", "?", "?"
try:
    with urllib.request.urlopen("http://127.0.0.1:8501/api/bot/status", timeout=5) as r:
        d = json.loads(r.read())
    loop = d.get("auto_loop_running")
    lc = d.get("last_cycle") or {}
    ran = (lc.get("ran_at") or "?")[11:19]
    errs = len(lc.get("errors") or [])
    cyc = f"{lc.get('elapsed_s')}s"
except Exception as e:
    loop = f"DOWN({type(e).__name__})"

print(f"{new} new | {fills} fills | {closed} closed | news30m={news} | "
      f"loop={loop} ran={ran} err={errs} cyc={cyc}")

rows = conn.execute(
    "SELECT id, symbol, side, status, pnl, exit_reason FROM orders "
    "WHERE id > ? ORDER BY id DESC LIMIT 6", (BASE_ID,)).fetchall()
for r in rows:
    print("  ", r)
