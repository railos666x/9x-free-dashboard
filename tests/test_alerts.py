"""Real-time dashboard alerts (2026-10-06).

- server._order_protection: per-leg protection class (module-level so
  /api/positions and /api/alerts agree).
- server._compute_alerts: naked-leg / loss-guard / digest-failure feed
  between daily digests (the digest thread records its state in
  _DIGEST_STATE; a failed write must surface IMMEDIATELY, not at 00:05).

Run:  .venv/Scripts/python.exe -m pytest tests/test_alerts.py -q
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pytest

import config as _config
import database
import server


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


def _open_row(conn, hash_, symbol, source, status="filled", sl=95.0,
              tp=110.0, oco_ref=None):
    oid = database.record_order(conn, dict(
        intent_hash=hash_, symbol=symbol, side="LONG", qty=1.0,
        entry=100.0, stop_loss=sl, take_profit=tp, source=source,
        status=status, fill_price=100.0, fill_time="2026-10-06T00:00:00",
        fees=0.0, oco_ref=oco_ref))
    conn.commit()
    return oid


def _alerts(conn):
    return {a["id"]: a for a in server._compute_alerts(conn)}


# ── protection classification (shared with /api/positions) ─────────────────
def test_protection_classes():
    assert server._order_protection({"oco_ref": "457561"}) == "venue-oco"
    assert server._order_protection(
        {"source": "mt5", "stop_loss": 1.1}) == "venue-sltp"
    assert server._order_protection(
        {"source": "binance", "stop_loss": 1.1}) == "software-only"
    assert server._order_protection({"source": "binance"}) == "none"
    assert server._order_protection(
        {"source": "mt5", "stop_loss": None, "take_profit": None}) == "none"


# ── naked-leg alerts ────────────────────────────────────────────────────────
def test_naked_software_only_leg_warns(conn):
    _open_row(conn, "a1", "BTC/USDT", "binance")
    al = _alerts(conn)
    assert "naked:1" in al
    assert al["naked:1"]["severity"] == "warning"
    assert al["naked:1"]["kind"] == "naked_leg"
    assert "software monitoring only" in al["naked:1"]["msg"]


def test_no_levels_at_all_is_critical(conn):
    _open_row(conn, "a2", "ETH/USDT", "binance", sl=None, tp=None)
    al = _alerts(conn)
    assert al["naked:1"]["severity"] == "critical"
    assert "no stop at all" in al["naked:1"]["msg"]


def test_venue_protected_legs_raise_nothing(conn):
    _open_row(conn, "a3", "BTC/USDT", "binance", oco_ref="457561")
    _open_row(conn, "a4", "EUR/USD", "mt5")
    assert _alerts(conn) == {}            # nothing naked, guard fine


def test_closed_rows_never_alert(conn):
    _open_row(conn, "a5", "BTC/USDT", "binance", status="closed")
    assert _alerts(conn) == {}


# ── loss-guard + digest-state alerts ────────────────────────────────────────
def test_loss_guard_blocked_alerts_critical(conn, monkeypatch):
    import execution
    monkeypatch.setattr(
        execution, "loss_guard_status",
        lambda c: {"blocked": True, "reason": "daily loss 3.4% > 3.0%"})
    al = _alerts(conn)
    assert al["loss-guard"]["severity"] == "critical"
    assert "BLOCKED" in al["loss-guard"]["msg"]
    assert al["loss-guard"]["kind"] == "loss_guard"


def test_digest_failure_alerts_until_cleared(conn, monkeypatch):
    saved = dict(server._DIGEST_STATE)
    try:
        server._DIGEST_STATE.update(
            last_error="OSError: disk full", last_ok=None)
        al = _alerts(conn)
        assert al["digest-failed"]["kind"] == "digest_failed"
        assert "disk full" in al["digest-failed"]["msg"]
        # cleared state → the alert disappears
        server._DIGEST_STATE.update(last_error=None)
        assert "digest-failed" not in _alerts(conn)
    finally:
        server._DIGEST_STATE.clear()
        server._DIGEST_STATE.update(saved)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
