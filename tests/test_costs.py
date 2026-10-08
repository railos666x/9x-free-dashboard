"""Execution-cost journaling, per-venue cost table, and the entry
cost-clearance veto (2026-10-01).

- orders.slippage_cost: entry estimate at fill (venue bps × notional;
  measured |fill − ref| when the fill path carries a pre-order reference),
  exit estimate added at close — a running round-trip total on the row.
- daily_digest.cost_table: per-venue weekly fees+slippage vs gross/net PnL.
- _cost_clearance: veto entries whose TP edge < 2× round-trip cost.

Run:  .venv/Scripts/python.exe -m pytest tests/test_costs.py -q
"""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pytest

import config as _config
import database
import execution


@pytest.fixture(autouse=True)
def _restore_config():
    keys = [k for k in dir(_config) if k.startswith(("SIZE_", "COST_",
                                                     "SLIPPAGE_", "FEE_"))]
    saved = {k: getattr(_config, k) for k in keys}
    yield
    for k, v in saved.items():
        setattr(_config, k, v)


@pytest.fixture()
def conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    saved_db = _config.DB_PATH          # restore: a leaked DB_PATH sent
    _config.DB_PATH = Path(tmp.name)    # test_forex's real-DB test at an
    try:                                # empty temp file before
        c = database.connect()
        database.init_db(c)
        yield c
    finally:
        _config.DB_PATH = saved_db


def _intent(symbol="BTC/USDT", entry=100.0, tp=102.0, stop=99.0,
            direction="LONG", confidence=10.0):
    from arbitrator import Intent, intent_hash
    return Intent(symbol=symbol, direction=direction, entry=entry,
                  stop_loss=stop, take_profit=tp, sources=["math_engine"],
                  window="1h", confidence=confidence,
                  intent_hash=intent_hash(symbol, direction, "1h"))


def _force_default_slip(bps):
    """Veto tests use EUR-style symbols with no tier override, so mutating
    SLIPPAGE_BPS_DEFAULT genuinely controls the round-trip cost."""
    saved = _config.SLIPPAGE_BPS_DEFAULT
    _config.SLIPPAGE_BPS_DEFAULT = bps
    return saved


# ── estimator ──────────────────────────────────────────────────────────────
def test_estimate_slippage_uses_venue_tier_bps():
    # default tier: 3 bps → 10 units @ 100 = 1000 notional → 0.3
    assert execution._estimate_slippage_cost("EUR/USD", 10.0, 100.0) \
        == pytest.approx(0.3)
    assert execution._estimate_slippage_cost("BTC/USDT", 1.0, 50_000.0) \
        == pytest.approx(50_000.0 * _config.SLIPPAGE_BPS_TIERS.get(
            "BTC/USDT", _config.SLIPPAGE_BPS_DEFAULT) / 10_000.0)


def test_estimate_slippage_none_without_price():
    assert execution._estimate_slippage_cost("BTC/USDT", 1.0, None) is None
    assert execution._estimate_slippage_cost("BTC/USDT", 1.0, 0.0) is None


# ── cost-clearance veto ────────────────────────────────────────────────────
def test_cost_veto_blocks_edge_below_2x_round_trip():
    saved = _force_default_slip(80.0)       # RT = (80+10)*2 = 180bps = 1.8%
    try:
        assert _config.COST_VETO_MIN_EDGE_X == 2.0
        veto = execution._cost_clearance(
            _intent(symbol="EUR/USD", entry=100.0, tp=102.0), 100.0)
        assert veto and veto.startswith("cost-veto:"), veto
        assert "180.0bps" in veto or "180bps" in veto
    finally:
        _config.SLIPPAGE_BPS_DEFAULT = saved


def test_cost_veto_passes_wide_enough_edge():
    saved = _force_default_slip(80.0)       # RT 1.8% → need ≥ 3.6% TP edge
    try:
        assert execution._cost_clearance(
            _intent(symbol="EUR/USD", entry=100.0, tp=104.0), 100.0) is None
    finally:
        _config.SLIPPAGE_BPS_DEFAULT = saved


def test_cost_veto_degrades_open_without_levels():
    # missing TP / entry must NEVER false-veto
    assert execution._cost_clearance(_intent(tp=0.0), 100.0) is None
    assert execution._cost_clearance(_intent(tp=102.0), 0.0) is None


def test_cost_veto_disabled_kills_check():
    _config.COST_VETO_ENABLED = False
    saved = _force_default_slip(80.0)
    try:
        assert execution._cost_clearance(
            _intent(symbol="EUR/USD", entry=100.0, tp=101.0), 100.0) is None
    finally:
        _config.SLIPPAGE_BPS_DEFAULT = saved
        _config.COST_VETO_ENABLED = True


def test_sized_qty_returns_zero_on_veto_with_telemetry(conn):
    saved = _force_default_slip(80.0)
    try:
        tel = {}
        size = execution._sized_qty(
            conn, _intent(symbol="EUR/USD", tp=101.0), None, 100.0, 99.0,
            telemetry=tel)
        assert size == 0.0
        assert "cost-veto:" in tel["veto"]
    finally:
        _config.SLIPPAGE_BPS_DEFAULT = saved


def test_execute_intent_dry_run_carries_veto_explanation(conn):
    saved = _force_default_slip(80.0)
    try:
        # R/R 1.67 clears the rr_floor (MIN_RR 1.2) so the veto — not the
        # reward/risk guard — is what refuses this trade. A vetoed trade
        # creates NO row (it never becomes a journal order); the veto
        # reason comes back as the call's error.
        res = execution.execute_intent(
            conn, _intent(symbol="EUR/USD", tp=101.0, stop=99.4),
            dry_run=True)
        assert res["order"] is None
        assert "cost-veto:" in (res.get("error") or "")
    finally:
        _config.SLIPPAGE_BPS_DEFAULT = saved


# ── slippage journaling through the order lifecycle ───────────────────────
def test_entry_and_exit_slippage_journal_as_round_trip_total(conn):
    oid = database.record_order(conn, {
        "intent_hash": "c1", "symbol": "BTC/USDT", "side": "LONG",
        "qty": 0.1, "entry": 50_000.0, "stop_loss": 49_000.0,
        "take_profit": 52_000.0, "source": "binance", "status": "filled",
        "fill_price": 50_000.0,
        "fill_time": "2026-10-01T00:00:00+00:00",
        "fees": 5.0, "broker_ref": "1",
        "slippage_cost": execution._estimate_slippage_cost(
            "BTC/USDT", 0.1, 50_000.0),
    })
    assert oid
    row = database.order_for_intent(conn, "c1")
    entry_slip = float(row["slippage_cost"])
    assert entry_slip > 0
    # close: update_order_exit ADDS the exit estimate to the entry one
    exit_slip = execution._estimate_slippage_cost("BTC/USDT", 0.1, 51_000.0)
    database.update_order_exit(conn, oid, 51_000.0,
                               "2026-10-01T02:00:00+00:00", "tp", 90.0, 5.0,
                               slippage_cost=exit_slip)
    row = database.order_for_intent(conn, "c1")
    assert float(row["slippage_cost"]) == pytest.approx(entry_slip + exit_slip)


def test_paper_fill_carries_ref_price_for_measured_slippage(conn):
    """The paper broker simulates slippage off a mid — it must return that
    mid so execute_intent can journal MEASURED |fill − ref| slippage."""
    # the paper broker prices off candles — seed one at 100.0
    now_ms = 1791129600000  # 2026-10-01 00:00 UTC
    database.upsert_candles(conn, "BTC/USDT", "1h", [{
        "open_time": now_ms, "open": 100.0, "high": 100.5,
        "low": 99.5, "close": 100.0, "volume": 1.0,
        "close_time": now_ms + 3_599_999,
    }])
    broker = execution.PaperBroker()
    fill = broker.place_market(conn, {
        "symbol": "BTC/USDT", "side": "LONG", "qty": 0.1, "entry": 100.0,
    })
    assert fill["ref_price"] > 0
    assert fill["fill_price"] != fill["ref_price"]   # slip applied
    # the simulated slip equals the bps estimate off the same reference
    implied = abs(fill["fill_price"] - fill["ref_price"]) * abs(0.1)
    est = execution._estimate_slippage_cost("BTC/USDT", 0.1,
                                            fill["ref_price"])
    assert implied == pytest.approx(est)


def test_execute_intent_journals_measured_slippage(conn, monkeypatch):
    class _PaperRT:
        name = "paper"

        def place_market(self, conn_, order):
            return {"status": "filled", "fill_price": 100.5,
                    "fill_time": "2026-10-01T00:00:00+00:00", "fees": 0.1,
                    "broker_ref": "p1", "ref_price": 100.0}

        def close_market(self, conn_, order):
            return {"status": "filled", "fill_price": 101.0,
                    "fill_time": "2026-10-01T01:00:00+00:00", "fees": 0.1}

    monkeypatch.setattr(execution, "get_broker", lambda: _PaperRT())
    res = execution.execute_intent(conn, _intent(entry=100.0, tp=104.0,
                                                 stop=98.0), dry_run=False)
    row = res["order"]
    # measured: |100.5 − 100.0| × qty(=size) — NOT the bps estimate
    assert float(row["slippage_cost"]) == pytest.approx(
        abs(100.5 - 100.0) * float(row["qty"]))
    assert row["status"] == "filled"


def test_close_position_journals_exit_slippage(conn):
    oid = database.record_order(conn, {
        "intent_hash": "c2", "symbol": "EUR/USD", "side": "LONG",
        "qty": 1.0, "entry": 100.0, "stop_loss": 98.0,
        "take_profit": 104.0, "source": "paper", "status": "filled",
        "fill_price": 100.0, "fill_time": "2026-10-01T00:00:00+00:00",
        "fees": 0.0, "broker_ref": "9",
    })
    order = database.order_for_intent(conn, "c2")
    out = execution.close_position(conn, order, price=104.0, reason="tp")
    row = database.order_for_intent(conn, "c2")
    # 104 exit @ 1 unit, default 3bps → 0.0312
    assert float(row["slippage_cost"]) == pytest.approx(
        1.0 * 104.0 * _config.SLIPPAGE_BPS_DEFAULT / 10_000.0)
    assert out["pnl"] > 0


# ── digest cost table ──────────────────────────────────────────────────────
def test_digest_cost_table_per_venue_weekly():
    import daily_digest as dd
    import sqlite3
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    c = sqlite3.connect(tmp.name)
    c.row_factory = sqlite3.Row
    database.init_db(c)
    cur = c.cursor()
    base = dict(side="LONG", qty=1.0, entry=100.0, stop_loss=98.0,
                take_profit=104.0, status="closed", fill_price=100.0,
                fill_time="2026-10-01T00:00:00+00:00")
    # Relative to now — hard-coded dates age out of the 7d window and
    # silently break the "recent" fixtures (same time-bomb class the gov
    # feed tests hit on 2026-10-06).
    from datetime import datetime, timedelta, timezone
    _now = datetime.now(timezone.utc)
    recent = (_now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    old = (_now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def _closed(hash_, symbol, source, pnl, fees, slip, exit_time):
        oid = database.record_order(c, dict(
            base, intent_hash=hash_, symbol=symbol, source=source,
            pnl=pnl, fees=fees))
        # record_order has no exit columns — set them like reconcile's
        # SQL does (same pattern as test_close_hardening)
        c.execute("UPDATE orders SET exit_time = ?, exit_reason = 'tp', "
                  "slippage_cost = ? WHERE id = ?",
                  (exit_time, slip, oid))
        c.commit()

    # mt5: 2 recent trades, fees 1.0 total, slip 2.0 total, net +8.0
    _closed("v1", "EUR/USD", "mt5", 5.0, 0.5, 1.0, recent)
    _closed("v2", "XAU/USD", "mt5", 3.0, 0.5, 1.0, recent)
    # binance: 1 recent losing trade (drag = n/a on negative gross)
    _closed("v3", "BTC/USDT", "binance", -2.0, 0.2, 0.5, recent)
    # OLD trade (>7d) must be excluded from the weekly window
    _closed("v4", "EUR/USD", "mt5", 99.0, 9.0, 9.0, old)
    lines, problems = [], []
    dd.cost_table(cur, lines, problems)
    text = "\n".join(lines)
    assert "| mt5 | 2 | 1.00 | 2.00 | 3.00 |" in text
    assert "| binance | 1 | 0.20 | 0.50 | 0.70 |" in text
    assert "99.00" not in text.split("###")[1].split("###")[0]  # old excluded
    # mt5 drag = 3 / (8+3) = 27.3% → flagged > 25%
    assert any("cost drag: mt5" in p for p in problems)
    assert not any("cost drag: binance" in p for p in problems)


def test_digest_cost_table_handles_empty_week():
    import daily_digest as dd
    import sqlite3
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    c = sqlite3.connect(tmp.name)
    c.row_factory = sqlite3.Row
    database.init_db(c)
    lines, problems = [], []
    dd.cost_table(c.cursor(), lines, problems)
    assert "no closed trades this week" in "\n".join(lines)
    assert problems == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
