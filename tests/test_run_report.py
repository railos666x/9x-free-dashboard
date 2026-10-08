"""Run-window report computations + daily-digest fold-in (2026-10-06).

- backend/run_report.py: wr_stats / per_symbol_table / trajectory math
  extracted from scripts/run_report.py so the daily digest renders the
  trajectory and per-symbol tables without a manual script run.
- daily_digest.run_window_section: the markdown fold-in + the last-20
  win-rate anomaly flag.

Run:  .venv/Scripts/python.exe -m pytest tests/test_run_report.py -q
"""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pytest

import config as _config
import database
import run_stats as rr


@pytest.fixture()
def conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    saved_db = _config.DB_PATH
    _config.DB_PATH = Path(tmp.name)
    try:
        c = database.connect()
        database.init_db(c)
        yield c
    finally:
        _config.DB_PATH = saved_db


def _iso(hours_ago: float = 1.0) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours_ago)).isoformat()


def _closed(conn, hash_, symbol, pnl, win=None, hours_ago=1.0,
            status="closed", error=None, fees=0.0):
    """Insert one journal row (closed unless told otherwise)."""
    oid = database.record_order(conn, dict(
        intent_hash=hash_, symbol=symbol,
        side="LONG", qty=1.0, entry=100.0, stop_loss=98.0,
        take_profit=104.0, source="paper", status=status,
        fill_price=100.0, fill_time=_iso(hours_ago), fees=fees))
    conn.execute(
        "UPDATE orders SET exit_time = ?, exit_reason = 'tp', pnl = ?, "
        "error = ? WHERE id = ?",
        (_iso(hours_ago), pnl, error, oid))
    conn.commit()
    return oid


# ── wr_stats ───────────────────────────────────────────────────────────────
def test_wr_stats_math(conn):
    for i, pnl in enumerate((10.0, 20.0, -5.0, 5.0, -15.0)):
        _closed(conn, f"w{i}", "BTC/USDT", pnl)
    rows = rr.closed_rows(conn)
    st = rr.wr_stats(rows)
    assert st["n"] == 5 and st["wins"] == 3
    assert st["wr"] == pytest.approx(60.0)
    assert st["net"] == pytest.approx(15.0)
    assert st["avg_win"] == pytest.approx((10 + 20 + 5) / 3)
    assert st["avg_loss"] == pytest.approx(-10.0)
    assert st["pf"] == pytest.approx(35.0 / 20.0)


def test_wr_stats_empty_and_all_win_pf(conn):
    assert rr.wr_stats([])["n"] == 0
    _closed(conn, "pf1", "BTC/USDT", 5.0)
    _closed(conn, "pf2", "BTC/USDT", 7.0)
    assert rr.wr_stats(rr.closed_rows(conn))["pf"] == float("inf")


# ── per-symbol table ───────────────────────────────────────────────────────
def test_per_symbol_table_min_two_ordered_by_net(conn):
    # EUR/USD x3 (2 wins) net +15; BTC x1 → excluded (min_n=2);
    # GBP/AUD x2 big wins → first; XAU old row → outside the 7d window.
    _closed(conn, "e1", "EUR/USD", 10.0)
    _closed(conn, "e2", "EUR/USD", 10.0)
    _closed(conn, "e3", "EUR/USD", -5.0)
    _closed(conn, "g1", "GBP/AUD", 50.0)
    _closed(conn, "g2", "GBP/AUD", 50.0)
    _closed(conn, "b1", "BTC/USDT", 99.0)
    _closed(conn, "x1", "XAU/USD", 77.0, hours_ago=24 * 30)
    rows = rr.per_symbol_table(conn, days=7, min_n=2)
    assert [r["symbol"] for r in rows] == ["GBP/AUD", "EUR/USD"]
    gbp = dict(rows[0])
    assert gbp["n"] == 2 and gbp["w"] == 2 and gbp["net"] == 100.0


# ── trajectory ─────────────────────────────────────────────────────────────
def test_trajectory_buckets_trend_and_need_wins(conn):
    # 30 closes → 3 rolling-10 buckets: 100%, 100%, 20% wins → declining;
    # last-20 = buckets 2+3 → 12 wins → wr20=60%, need ceil(14)-12 = 2.
    i = 0
    for _ in range(10):
        _closed(conn, f"t{i}", "BTC/USDT", 10.0); i += 1
    for _ in range(10):
        _closed(conn, f"t{i}", "BTC/USDT", 10.0); i += 1
    for _ in range(2):
        _closed(conn, f"t{i}", "BTC/USDT", 10.0); i += 1
    for _ in range(8):
        _closed(conn, f"t{i}", "BTC/USDT", -5.0); i += 1
    traj = rr.trajectory(conn)
    assert traj["buckets"] == [100.0, 100.0, 20.0]
    assert traj["trend"] == "declining"
    assert traj["first_half"] == pytest.approx(100.0)
    assert traj["last_half"] == pytest.approx(60.0)
    assert traj["wr20"] == pytest.approx(60.0)
    assert traj["need_wins"] == 2          # ceil(70*20/100)=14 − 12 wins
    assert traj["all_time"]["n"] == 30
    assert set(traj["windows"]) == {"last-10", "last-20"}


def test_trajectory_shallow_book_has_no_target_math(conn):
    _closed(conn, "s1", "BTC/USDT", 5.0)
    traj = rr.trajectory(conn)
    # buckets form only on COMPLETE groups of 10 (ported CLI semantics)
    assert traj["buckets"] == []
    assert "wr20" not in traj and "trend" not in traj
    assert traj["windows"] == {}


# ── window helpers ─────────────────────────────────────────────────────────
def test_window_summary_and_closes_and_errors(conn):
    _closed(conn, "c1", "BTC/USDT", 5.0)
    _closed(conn, "c2", "BTC/USDT", -3.0)
    _closed(conn, "c3", "ETH/USDT", 0.0, status="filled")
    _closed(conn, "c4", "XRP/USDT", 0.0, status="error", error="boom")
    _closed(conn, "c5", "SOL/USDT", 7.0)
    ws = rr.window_summary(conn, base_id=0)
    assert ws["rows"] == 5
    assert ws["by_status"] == {"closed": 3, "filled": 1, "error": 1}
    closes = rr.window_closes(conn, base_id=0)
    assert len(closes) == 3 and closes[0]["pnl"] == 5.0
    errs = rr.error_notes(conn, base_id=0)
    assert len(errs) == 1 and "boom" in errs[0]["error"]


# ── digest fold-in ─────────────────────────────────────────────────────────
def test_digest_run_window_section_renders(conn):
    import daily_digest as dd
    for i in range(21):
        # rows d1..d20 (the last-20) carry exactly 9 wins → wr20=45% < 50%;
        # buckets = [10, 80, 100] → improving
        win = i == 0 or i >= 12
        _closed(conn, f"d{i}", "BTC/USDT", 10.0 if win else -4.0)
    lines, problems = [], []
    dd.run_window_section(conn, lines, problems, base_id=0)
    text = "\n".join(lines)
    assert "### Run window (journal id > 0)" in text
    assert "closed: 21" in text
    assert "- window closed: n=21 wr=47.6%" in text
    assert "### Win-rate trajectory (all journal closes)" in text
    assert "| all-time | 21 | 47.6% |" in text
    assert "| last-20 | 20 | 45.0% |" in text
    assert "| last-10 | 10 | 90.0% |" in text
    assert "rolling-10 buckets: 10 80" in text   # complete-10 groups only
    assert "(improving)" in text
    assert "path to 70%" in text and "need 5 more win(s)" in text
    assert "### Per-symbol (7d, min 2 closed)" in text
    assert "| BTC/USDT | 21 | 48% | +56.00 |" in text
    # 45% < 50% → the trajectory slide is never silent
    assert any("last-20 win rate 45% < 50%" in p for p in problems)


def test_digest_run_window_section_healthy_book(conn):
    import daily_digest as dd
    for i in range(20):
        # 17 wins / 3 losses → wr20=85%, buckets [100, 70] → improving
        win = i < 17
        _closed(conn, f"h{i}", "ETH/USDT", 8.0 if win else -3.0)
    lines, problems = [], []
    dd.run_window_section(conn, lines, problems, base_id=0)
    text = "\n".join(lines)
    assert "path to 70%" in text
    assert problems == []                  # wr20=85% → no anomaly


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
