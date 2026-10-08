"""Run-window performance computations (2026-10-06).

Extracted from scripts/run_report.py so the daily digest can render the
win-rate trajectory and per-symbol tables WITHOUT a manual script run.
scripts/run_report.py is now a thin CLI printing wrapper over this module.

All functions take an open sqlite3 connection whose row_factory is
sqlite3.Row (scripts/daily_digest.py and the CLI both set it) and contain
ZERO print/io — pure query + math so tests can call them directly.

Journal numbers come from `orders` (MT5 rows are deal-history-reconciled,
i.e. truthful). BASE_ID=400 is the run-window baseline (2026-10-01
03:35 UTC); TARGET_WR=70.0 is the standing win-rate goal.
"""
from __future__ import annotations

import sqlite3

BASE_ID = 400          # run-window baseline (2026-10-01 03:35 UTC)
TARGET_WR = 70.0       # standing win-rate target


def closed_rows(conn: sqlite3.Connection, where: str = "1=1", args=()):
    """All journal closes (chronological by exit), optionally filtered."""
    return conn.execute(
        f"""SELECT * FROM orders
            WHERE status='closed' AND pnl IS NOT NULL AND {where}
            ORDER BY COALESCE(exit_time, fill_time), id""",
        args,
    ).fetchall()


def wr_stats(rows) -> dict:
    """Win-rate / net / cost / profit-factor stats over a row sequence."""
    n = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    wr = 100.0 * wins / n if n else 0.0
    net = sum(r["pnl"] for r in rows)
    fees = sum(r["fees"] or 0 for r in rows)
    slip = sum(r["slippage_cost"] or 0 for r in rows)
    avg_win = (sum(r["pnl"] for r in rows if r["pnl"] > 0) / wins) if wins else 0.0
    losses = n - wins
    avg_loss = (sum(r["pnl"] for r in rows if r["pnl"] <= 0) / losses) if losses else 0.0
    pf_den = -sum(r["pnl"] for r in rows if r["pnl"] < 0)
    pf = (sum(r["pnl"] for r in rows if r["pnl"] > 0) / pf_den) if pf_den > 0 \
        else float("inf")
    return {"n": n, "wins": wins, "wr": wr, "net": net, "fees": fees,
            "slip": slip, "avg_win": avg_win, "avg_loss": avg_loss, "pf": pf}


def window_summary(conn: sqlite3.Connection, base_id: int = BASE_ID) -> dict:
    """Journal-row census for the run window (id > base_id)."""
    total = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE id > ?", (base_id,)).fetchone()[0]
    by_status = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM orders WHERE id > ? GROUP BY status",
        (base_id,))}
    return {"rows": total, "by_status": by_status}


def window_closes(conn: sqlite3.Connection, base_id: int = BASE_ID):
    """Per-row closes inside the run window (the CLI's detail listing)."""
    return conn.execute(
        """SELECT id, symbol, side, pnl, exit_reason, slippage_cost
           FROM orders WHERE id > ? AND status='closed' ORDER BY id""",
        (base_id,)).fetchall()


def per_symbol_table(conn: sqlite3.Connection, days: int = 7,
                     min_n: int = 2):
    """(symbol, n, win_pct, net) for symbols with ≥ min_n closes in `days`.

    Ordered by net DESC (best performers first). SQLite date arithmetic
    (`date('now', ?)`) matches the digest's other weekly windows.
    """
    return conn.execute(
        """SELECT symbol, COUNT(*) n, SUM(pnl>0) w, SUM(pnl) net
           FROM orders
           WHERE status='closed' AND pnl IS NOT NULL
             AND COALESCE(exit_time, fill_time) >= date('now', ?)
           GROUP BY symbol HAVING n >= ? ORDER BY net DESC""",
        (f"-{days} day", min_n),
    ).fetchall()


def trajectory(conn: sqlite3.Connection, target: float = TARGET_WR) -> dict:
    """Win-rate trajectory over ALL journal closes, chronological.

    - all_time + last-10/20/50 windows (when the book is deep enough)
    - rolling-10 buckets: per-bucket win %, first-half vs last-half means
      and an improving/declining/flat trend verdict
    - path to target: wins still needed inside a rolling 20 to touch it
    """
    rows = closed_rows(conn)
    out: dict = {"all_time": wr_stats(rows), "windows": {}}
    n = len(rows)
    for k in (10, 20, 50):
        if n >= k:
            out["windows"][f"last-{k}"] = wr_stats(rows[-k:])
    buckets = [rows[i:i + 10] for i in range(0, n - n % 10, 10)]
    seq = [100.0 * sum(1 for r in b if r["pnl"] > 0) / len(b) for b in buckets]
    out["buckets"] = seq
    if len(seq) >= 2:
        half = len(seq) // 2
        first = sum(seq[:half]) / max(half, 1)
        last = sum(seq[half:]) / max(len(seq) - half, 1)
        out["first_half"] = first
        out["last_half"] = last
        out["trend"] = ("improving" if last > first
                        else "declining" if last < first else "flat")
    if n >= 20:
        w20 = sum(1 for r in rows[-20:] if r["pnl"] > 0)
        out["wr20"] = 100.0 * w20 / 20
        # ceil((target * 20) / 100) − w20, floor-safe integer math; int()
        # because a float target makes the whole expression a float
        # (rendered as 'need 5.0 more win(s)' — a count is an int).
        out["need_wins"] = int(max(0, -(-(target * 20) // 100) - w20))
    return out


def error_notes(conn: sqlite3.Connection, base_id: int = BASE_ID):
    """(id, symbol, source, error) rows for window errors (CLI gate notes)."""
    return conn.execute(
        """SELECT id, symbol, source, error FROM orders
           WHERE id > ? AND status='error' ORDER BY id""",
        (base_id,)).fetchall()


def news_30m(conn: sqlite3.Connection) -> int:
    """News rows fetched in the last 30 minutes (CLI gate-note line)."""
    return conn.execute(
        "SELECT COUNT(*) FROM news WHERE fetched_at >= "
        "datetime('now','-30 minutes')").fetchone()[0]
