"""
Manual dashboard trading endpoints (2026-10-07) — /api/manual/order and
/api/manual/close. The trader's orders must ride the SAME path as the bot
(sizing / R/R floor / protection / journal / loss guard); close must go
through close_position (OCO lifecycle + realized PnL). These tests use
TestClient with execution monkeypatched — NO broker is touched.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi.testclient import TestClient

# Test DB (same pattern as test_oco/test_execution)
_TEST_DB = Path(__file__).resolve().parents[1] / "data" / "test_manual.db"

import database  # noqa: E402
import config  # noqa: E402

# The DB lives at config.DB_PATH (not a database.* constant) and
# database.connect() reads it at CALL time, so repointing it redirects the
# whole server to our scratch file. It MUST be saved/restored per test: an
# earlier version repointed it at import time, and because pytest imports
# every test module during collection that leak made OTHER modules' real-DB
# tests read this empty file (test_forex's "pairs_with_data >= 15" saw 0 —
# a test-ordering flake with nothing to do with the code under test).
_SAVED_DB = None


def _unlink_db(path: Path) -> None:
    """Delete a SQLite file plus its WAL/shm sidecars.

    Windows keeps a delete-lock until the last handle closes (a leaked
    connection from a previous test), and the sidecars matter too: leaving
    `-wal` behind re-attaches stale rows to a "fresh" file.
    """
    import time
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        for _ in range(20):
            if not candidate.exists():
                break
            try:
                candidate.unlink()
            except (PermissionError, OSError):
                time.sleep(0.05)   # let the holder's handle go before retrying


@pytest.fixture(autouse=True)
def _clean_manual_db():
    """Fresh scratch DB + config.DB_PATH pointed at it for ONE test."""
    global _SAVED_DB
    _SAVED_DB = config.DB_PATH
    config.DB_PATH = _TEST_DB
    _unlink_db(_TEST_DB)
    conn = database.connect()
    database.init_db(conn)
    conn.close()
    yield conn
    # Restore the real path even if the test failed — the next module's
    # tests must never inherit a scratch DB.
    config.DB_PATH = _SAVED_DB


@pytest.fixture()
def client(monkeypatch, _clean_manual_db):
    """Fresh TestClient + empty test database, execution faked."""
    import execution as ex
    import server

    # Capture what the execution path receives; return a journaled filled row.
    seen = {}

    def fake_execute(conn, intent, qty=None, interval="1h", dry_run=False):
        seen["intent"] = intent
        seen["qty"] = qty
        seen["dry_run"] = dry_run
        order = {
            "id": 901, "intent_hash": intent.intent_hash,
            "symbol": intent.symbol, "side": intent.direction,
            "qty": qty or 0.01, "entry": intent.entry or 100.0,
            "stop_loss": intent.stop_loss, "take_profit": intent.take_profit,
            "source": "binance", "status": "filled" if not dry_run else "dry_run",
            "fill_price": intent.entry or 100.0, "fill_time": "2026-10-07T08:00:00+00:00",
            "fees": 0.05, "pnl": None, "error": None,
        }
        if not dry_run:
            c = database.connect()
            database.record_order(c, order)
            c.close()
        seen["order"] = order
        return {"order": order, "duplicate": False, "dry_run": dry_run}

    def fake_close(conn, order, price=None, reason="manual"):
        seen["closed_reason"] = reason
        closed = dict(order, status="closed", exit_reason="manual",
                      exit_time="2026-10-07T08:05:00+00:00", pnl=12.34)
        return {"order": closed, "pnl": 12.34, "reason": "manual"}

    monkeypatch.setattr(ex, "execute_intent", fake_execute)
    monkeypatch.setattr(ex, "close_position", fake_close)
    # The real loss guard runs against the EMPTY test DB → never blocked.
    with TestClient(server.app) as c:
        yield c, seen


def test_manual_order_rides_execution_path(client):
    c, seen = client
    r = c.post("/api/manual/order", json={
        "symbol": "BTC/USDT", "side": "LONG",
        "stop_loss": 99000, "take_profit": 105000,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["order"]["status"] == "filled"
    # The bot's executor received a manual Intent with the trader's levels
    intent = seen["intent"]
    assert intent.direction == "LONG"
    assert intent.sources == ["manual"]
    assert float(intent.stop_loss) == 99000.0
    assert float(intent.take_profit) == 105000.0
    assert intent.reasons == ["manual order from dashboard"]


def test_manual_order_dry_run_never_journals_filled(client):
    c, seen = client
    r = c.post("/api/manual/order", json={
        "symbol": "XAU/USD", "side": "SHORT", "dry": True,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dry_run"] is True
    assert d["order"]["status"] == "dry_run"


def test_manual_order_rejects_bad_direction(client):
    c, _ = client
    r = c.post("/api/manual/order", json={"symbol": "BTC/USDT", "side": "SIDEWAYS"})
    assert r.status_code == 400
    assert "LONG or SHORT" in r.json()["error"]


def test_manual_order_rejects_wrong_side_stop(client, monkeypatch):
    c, _ = client
    # The 200/400 split is decided inside _manual_intent — but execute_intent
    # would only run AFTER guard+sizer, so patch it to explode spies. With no
    # entry given, the side-check needs an entry too: send one explicitly.
    called = {}
    monkeypatch.setattr(database, "connect", lambda *a, **k: _fail_conn(called))
    r = c.post("/api/manual/order", json={
        "symbol": "BTC/USDT", "side": "LONG",
        "entry": 100000, "stop_loss": 200000})
    assert r.status_code == 400
    assert "BELOW" in r.json()["error"]
    assert called.get("ran") is not True  # refused BEFORE any execution ran


def _fail_conn(called):
    called["ran"] = True
    raise RuntimeError("execution must not run for an invalid manual order")


def test_manual_close_unknown_id_is_404(client):
    c, _ = client
    r = c.post("/api/manual/close/999999")
    assert r.status_code == 404
    assert "not found" in r.json()["error"]


def test_manual_close_goes_through_close_position(client):
    c, seen = client
    conn = database.connect()
    # Unique hash per run: record_order is INSERT OR IGNORE, so a leftover
    # row from an earlier run would silently return rowid 0 ("#0 not found") —
    # the assertion below then failed for a reason unrelated to close logic.
    import uuid
    oid = database.record_order(conn, {
        "intent_hash": f"manual-close-test-{uuid.uuid4().hex[:10]}", "symbol": "ETH/USDT",
        "side": "LONG", "qty": 0.5, "entry": 2500.0, "stop_loss": 2400.0,
        "take_profit": 2700.0, "source": "binance", "status": "filled",
        "fill_price": 2500.0, "fill_time": "2026-10-07T07:00:00+00:00",
    })
    conn.close()
    assert oid, "the journal row must insert (0 = duplicate hash, not a close bug)"
    r = c.post(f"/api/manual/close/{oid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["pnl"] == 12.34
    assert d["order"]["status"] == "closed"
    assert seen["closed_reason"] == "manual"
