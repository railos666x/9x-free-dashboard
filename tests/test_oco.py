"""Venue-side OCO brackets for Binance spot legs (2026-10-05).

- BinanceBroker.place_oco/cancel_oco/oco_status: resting STOP_LOSS_LIMIT +
  TAKE_PROFIT_LIMIT list on the exchange so SL/TP fire with the bot offline.
- Entry attach: place_market rests the bracket after a fill (best-effort —
  failure degrades to software monitoring, never errors the fill).
- Lifecycle: close_position cancels the bracket BEFORE closing; a fired
  bracket (bot was offline) is journaled with the TRUE leg and price.
- Reconcile fired-OCO pass: journal rows close as sl/tp, not ghost
  broker_closed at a candle price.

Network is never touched — every signed call is faked on the instance.
Run:  .venv/Scripts/python.exe -m pytest tests/test_oco.py -q
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
    saved = {k: getattr(_config, k) for k in dir(_config)
             if k.startswith(("BINANCE_OCO", "FEE_", "COST_", "TRAIL_"))}
    # BinanceBroker.__init__ requires keys; tests never hit the network.
    _config.BINANCE_EXEC_API_KEY = "test-key"
    _config.BINANCE_EXEC_API_SECRET = "test-secret"
    yield
    for k, v in saved.items():
        setattr(_config, k, v)


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


def _binance():
    b = execution.BinanceBroker()
    return b


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or (str(self._payload) if status >= 400 else "")

    def json(self):
        return self._payload


def _oco_payload(list_id=9001):
    return {"orderListId": list_id, "listStatusType": "EXECUTING",
            "orders": [{"orderId": 111, "symbol": "BTCUSDT",
                        "type": "STOP_LOSS_LIMIT", "status": "NEW"},
                       {"orderId": 222, "symbol": "BTCUSDT",
                        "type": "TAKE_PROFIT_LIMIT", "status": "NEW"}]}


# ── price formatting / place_oco ───────────────────────────────────────────
def test_round_price_snaps_to_tick_grid():
    assert execution.BinanceBroker._round_price(95.123456, 0.01) == "95.12"
    assert execution.BinanceBroker._round_price(95.126, 0.01) == "95.13"
    assert execution.BinanceBroker._round_price(0.54321, 0.0001) == "0.5432"
    # no tick info → 8dp fallback, never scientific notation
    assert execution.BinanceBroker._round_price(95.123456789, 0.0) == "95.12345679"


def test_place_oco_sends_bracket_params():
    b = _binance()
    seen = {}

    def fake_signed(method, path, params=None):
        seen["method"], seen["path"], seen["params"] = method, path, params or {}
        return FakeResp(payload=_oco_payload(9001))

    # Pin the tick: testnet exchangeInfo intermittently 502s (2026-10-07
    # outage), which makes a live fetch flake this test instead of testing it.
    b._tick_size = lambda s: 0.01
    b._signed = fake_signed
    res = b.place_oco("BTCUSDT", "SELL", "0.010", 95.0, 110.0)
    assert res["orderListId"] == "9001"
    assert res["orderIds"] == ["111", "222"]
    assert seen["method"] == "POST"
    assert seen["path"].endswith("/api/v3/orderList/oco")
    p = seen["params"]
    assert p["side"] == "SELL"
    assert p["quantity"] == "0.010"
    # Leg-based schema (the venue rejects the legacy flat price/stopPrice
    # form with -1102 'aboveType was not sent' — live 2026-10-05):
    # above the market = take-profit leg, below = stop leg. Market-on-
    # trigger types (TAKE_PROFIT / STOP_LOSS) execute as MARKET on touch.
    assert p["aboveType"] == "TAKE_PROFIT"
    assert p["aboveStopPrice"] == "110.00"
    assert p["belowType"] == "STOP_LOSS"
    assert p["belowStopPrice"] == "95.00"
    assert "price" not in p and "stopPrice" not in p


def test_place_oco_flips_legs_for_buy_exit():
    b = _binance()
    seen = {}

    def fake_signed(method, path, params=None):
        seen["params"] = params or {}
        return FakeResp(payload=_oco_payload(9002))

    b._tick_size = lambda s: 0.01  # tick pinned — see bracket-params test
    b._signed = fake_signed
    b.place_oco("BTCUSDT", "BUY", "0.010", 95.0, 110.0)
    p = seen["params"]
    # Short covered: the stop sits ABOVE the market, the TP BELOW.
    assert p["aboveType"] == "STOP_LOSS" and p["aboveStopPrice"] == "95.00"
    assert p["belowType"] == "TAKE_PROFIT" and p["belowStopPrice"] == "110.00"


def test_place_oco_raises_on_rejection():
    b = _binance()
    b._signed = lambda *a, **k: FakeResp(
        status=400, payload={"code": -1165, "msg": "bad"},
        text='{"code":-1165,"msg":"bad"}')
    with pytest.raises(execution.BrokerError, match="oco rejected"):
        b.place_oco("BTCUSDT", "SELL", "0.01", 95.0, 110.0)


def test_place_oco_retries_then_succeeds():
    b = _binance()
    calls = {"n": 0}

    def flaky(method, path, params=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise execution.requests.ConnectionError("drop")
        return FakeResp(payload=_oco_payload(7))

    b._signed = flaky
    assert b.place_oco("BTCUSDT", "SELL", "0.01", 95.0, 110.0)[
        "orderListId"] == "7"
    assert calls["n"] == 3


# ── cancel / status / fill price ───────────────────────────────────────────
def test_cancel_oco_true_and_already_gone_counts_as_gone():
    b = _binance()
    b._signed = lambda *a, **k: FakeResp(payload={"orderListId": 9001})
    assert b.cancel_oco("BTCUSDT", "9001") is True
    b._signed = lambda *a, **k: FakeResp(
        status=400, payload={"code": -2011, "msg": "Unknown order sent"},
        text='{"code":-2011,"msg":"Unknown order sent"}')
    assert b.cancel_oco("BTCUSDT", "9001") is True  # already fired/cancelled


def test_cancel_oco_raises_on_real_failure_and_false_on_bad_ref():
    b = _binance()
    b._signed = lambda *a, **k: FakeResp(status=503, text="oops")
    with pytest.raises(execution.BrokerError, match="cancel failed"):
        b.cancel_oco("BTCUSDT", "9001")
    assert b.cancel_oco("BTCUSDT", "not-a-number") is False


def test_oco_status_detects_fired_legs():
    # The list response carries SLIM legs ({symbol, orderId}); the per-order
    # query (BINANCE_ORDER) is enriched for type/status/price.
    b = _binance()
    list_resp = {"orderListId": 9001, "listStatusType": "EXECUTED",
                 "orders": [{"symbol": "BTCUSDT", "orderId": 111},
                            {"symbol": "BTCUSDT", "orderId": 222}]}
    per_order = {
        ("GET", 111): {"orderId": 111, "type": "STOP_LOSS",
                       "status": "FILLED", "price": "0", "stopPrice": "95"},
        ("GET", 222): {"orderId": 222, "type": "TAKE_PROFIT",
                       "status": "CANCELED", "price": "110", "stopPrice": "110"},
    }

    def fake(method, path, params=None):
        p = params or {}
        if path.endswith("/api/v3/orderList"):
            return FakeResp(payload=list_resp)
        od = per_order.get((method, p.get("orderId")))
        return FakeResp(payload=od) if od else FakeResp(status=404, text="{}")

    b._signed = fake
    st = b.oco_status("BTCUSDT", "9001")
    assert st["status"] == "EXECUTED" and st["fired_leg"] == "sl"
    assert st["orders"][0]["type"] == "STOP_LOSS"
    assert st["orders"][0]["status"] == "FILLED"

    per_order[("GET", 111)]["status"] = "CANCELED"
    per_order[("GET", 222)]["status"] = "FILLED"
    st = b.oco_status("BTCUSDT", "9001")
    assert st["fired_leg"] == "tp"

    # Live terminal state is ALL_DONE, not EXECUTED (OCO #457561 came back
    # ALL_DONE with the SL FILLED and fired_leg was MISSED, 2026-10-06) —
    # a FILLED leg is the proof, the list-status spelling must not gate it.
    list_resp["listStatusType"] = "ALL_DONE"
    per_order[("GET", 111)]["status"] = "FILLED"
    per_order[("GET", 222)]["status"] = "EXPIRED"
    st = b.oco_status("BTCUSDT", "9001")
    assert st["status"] == "ALL_DONE" and st["fired_leg"] == "sl"
    # An ALL_DONE list with NO filled leg (we cancelled it) is NOT fired.
    per_order[("GET", 111)]["status"] = "CANCELED"
    st = b.oco_status("BTCUSDT", "9001")
    assert st["fired_leg"] is None

    b._signed = lambda *a, **k: FakeResp(status=404, text="{}")
    assert b.oco_status("BTCUSDT", "9001") is None


def test_order_fill_price_matches_order_id():
    b = _binance()
    trades = [{"orderId": 111, "price": "95.10", "qty": "0.01"},
              {"orderId": 222, "price": "110.00", "qty": "0.01"}]
    b._signed = lambda *a, **k: FakeResp(payload=trades)
    assert b.order_fill_price("BTCUSDT", "111") == pytest.approx(95.10)
    assert b.order_fill_price("BTCUSDT", "999") is None
    assert b.order_fill_price("BTCUSDT", None) is None


# ── entry attach ───────────────────────────────────────────────────────────
def _patch_attach(b, calls):
    b._get_market_lot_info = lambda s: {"stepSize": 0.001, "minQty": 0.001,
                                        "minNotional": 5.0}
    b._tick_size = lambda s: 0.01

    def fake_oco(symbol, side, qty, stop, tp, stop_limit=None):
        calls.update(symbol=symbol, side=side, qty=qty, stop=stop, tp=tp,
                     stop_limit=stop_limit)
        return {"orderListId": "9001", "orderIds": ["111", "222"],
                "listStatus": "EXECUTING"}

    b.place_oco = fake_oco


def test_place_market_rests_oco_after_fill():
    b = _binance()
    calls = {}
    _patch_attach(b, calls)
    b._submit = lambda conn, order, side: {
        "status": "filled", "fill_price": 100.0, "fill_time": "t",
        "fees": 0.0, "broker_ref": "555"}
    fill = b.place_market(None, {"symbol": "BTC/USDT", "side": "LONG",
                                 "qty": 0.5, "entry": 100.0,
                                 "stop_loss": 95.0, "take_profit": 110.0})
    assert fill["oco_ref"] == "9001"
    assert calls["side"] == "SELL"                    # exit side for a LONG
    assert calls["stop"] == 95.0 and calls["tp"] == 110.0
    assert calls["stop_limit"] is None    # market-on-trigger legs: no buffer


def test_place_market_skips_oco_when_levels_dont_bracket():
    b = _binance()
    calls = {}
    _patch_attach(b, calls)
    b._submit = lambda conn, order, side: {
        "status": "filled", "fill_price": 100.0, "fill_time": "t",
        "fees": 0.0, "broker_ref": "555"}
    # SL above the fill for a LONG — the venue would reject the whole list
    fill = b.place_market(None, {"symbol": "BTC/USDT", "side": "LONG",
                                 "qty": 0.5, "entry": 100.0,
                                 "stop_loss": 105.0, "take_profit": 110.0})
    assert "oco_ref" not in fill and calls == {}


def test_place_market_oco_disabled_and_failed_attach_never_errors_fill():
    order = {"symbol": "BTC/USDT", "side": "LONG", "qty": 0.5, "entry": 100.0,
             "stop_loss": 95.0, "take_profit": 110.0}
    fill_base = {"status": "filled", "fill_price": 100.0, "fill_time": "t",
                 "fees": 0.0, "broker_ref": "555"}

    _config.BINANCE_OCO_ENABLED = False
    b = _binance()
    b._submit = lambda conn, o, s: dict(fill_base)
    assert "oco_ref" not in b.place_market(None, order)

    _config.BINANCE_OCO_ENABLED = True
    b2 = _binance()
    b2._submit = lambda conn, o, s: dict(fill_base)

    def boom(*a, **k):
        raise execution.BrokerError("venue down")
    b2.place_oco = boom
    assert b2.place_market(None, order)["status"] == "filled"  # degrade, not die


def test_attach_oco_skips_qty_below_min_after_step_floor(capsys):
    b = _binance()
    calls = {}
    _patch_attach(b, calls)
    b._get_market_lot_info = lambda s: {"stepSize": 1.0, "minQty": 1.0,
                                        "minNotional": 5.0}
    b._attach_oco(None, {"symbol": "BTC/USDT", "side": "LONG", "qty": 0.5,
                         "stop_loss": 95.0, "take_profit": 110.0},
                  {"fill_price": 100.0}, "BUY")
    assert calls == {}
    assert "below minQty" in capsys.readouterr().out


# ── journal/lifecycle integration ──────────────────────────────────────────
class FakeBinance:
    """Stands in for BinanceBroker at the get_broker() seam."""
    name = "binance"

    def __init__(self, fired=None, fundability=None):
        self.cancels = []
        self.closes = []
        self.fired = fired or {}
        self._fundability = fundability
        self.places = 0
        self.oco_places = []

    def fundability(self, symbol, side, qty, price):
        return self._fundability

    def place_oco(self, symbol, side, qty, stop, tp, stop_limit=None):
        self.oco_places.append((symbol, side, qty, stop, tp))
        return {"orderListId": "9002", "orderIds": ["777", "888"]}

    def symbol_of(self, s):
        return s.replace("/", "")

    def fetch_orders(self, limit: int = 100):
        return []

    def place_market(self, conn, order):
        self.places += 1
        return {"status": "filled", "fill_price": 100.0,
                "fill_time": "2026-10-05T00:00:00+00:00", "fees": 0.1,
                "broker_ref": "555", "oco_ref": "9001"}

    def close_market(self, conn, order):
        self.closes.append(order)
        return {"status": "filled", "fill_price": 108.0,
                "fill_time": "2026-10-05T01:00:00+00:00", "fees": 0.1}

    def oco_status(self, symbol, list_id):
        return self.fired.get(str(list_id))

    def cancel_oco(self, symbol, list_id):
        self.cancels.append(str(list_id))
        return True

    def order_fill_price(self, symbol, order_id):
        return {"111": 94.98, "222": 110.05}.get(str(order_id))


def _intent(symbol="BTC/USDT", entry=100.0, tp=110.0, stop=95.0,
            direction="LONG", confidence=10.0):
    from arbitrator import Intent, intent_hash
    return Intent(symbol=symbol, direction=direction, entry=entry,
                  stop_loss=stop, take_profit=tp, sources=["math_engine"],
                  window="1h", confidence=confidence,
                  intent_hash=intent_hash(symbol, direction, "1h"))


def test_execute_intent_journals_oco_ref(conn, monkeypatch):
    fake = FakeBinance()
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    res = execution.execute_intent(conn, _intent())
    row = database.order_for_intent(conn, _intent().intent_hash)
    assert row["status"] == "filled" and row["oco_ref"] == "9001"
    assert row["broker_ref"] == "555"


# ── balance precheck (2026-10-06, the #403 fix) ────────────────────────────
# ── trailing stop ↔ venue OCO sync (2026-10-06) ────────────────────────────
def _trail_row(conn, oco_ref="9001"):
    """One filled LONG row (entry 100, sl 95, tp 110) + 30 rising 1h bars
    so the trail activates and moves the stop UP."""
    import time as _t
    now = int(_t.time() * 1000)
    rows = []
    price = 100.0
    for i in range(30):
        c = price + i
        rows.append({"open_time": now - (30 - i) * 3_600_000, "open": c - 1,
                     "high": c + 1, "low": c - 1, "close": c,
                     "volume": 1000.0, "close_time": 0})
    database.upsert_candles(conn, "BTC/USDT", "1h", rows)
    oid = database.record_order(conn, dict(
        intent_hash="trail1", symbol="BTC/USDT", side="LONG", qty=1.0,
        entry=100.0, stop_loss=95.0, take_profit=110.0, source="paper",
        status="filled", fill_price=100.0,
        fill_time="2026-10-06T00:00:00+00:00", fees=0.0,
        oco_ref=oco_ref))
    return dict(conn.execute("SELECT * FROM orders WHERE id = ?",
                             (oid,)).fetchone())


def test_trailing_stop_replaces_oco_bracket(conn, monkeypatch):
    fake = FakeBinance()
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    row = _trail_row(conn)
    execution._update_trailing_stop(conn, row)
    after = dict(conn.execute("SELECT * FROM orders WHERE id = ?",
                              (row["id"],)).fetchone())
    new_sl = float(after["stop_loss"])     # re-read: the fn updates the DB
    assert 95.0 < new_sl < 130.0            # trail moved the stop UP
    assert fake.cancels == ["9001"]         # old bracket cancelled first
    assert fake.oco_places == [("BTCUSDT", "SELL", 1.0, new_sl, 110.0)]
    assert after["oco_ref"] == "9002"       # NEW list stamped on the row


def test_trailing_stop_replace_failure_journals_no_bracket(
        conn, monkeypatch):
    fake = FakeBinance()

    def boom(*a, **k):
        raise RuntimeError("venue down")

    fake.place_oco = boom
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    row = _trail_row(conn)
    execution._update_trailing_stop(conn, row)   # must NOT raise
    assert fake.cancels == ["9001"]
    after = dict(conn.execute("SELECT oco_ref FROM orders WHERE id = ?",
                              (row["id"],)).fetchone())
    assert after["oco_ref"] is None         # truthful: no bracket any more


def test_trailing_stop_without_oco_ref_skips_broker(conn, monkeypatch):
    fake = FakeBinance()
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    row = _trail_row(conn, oco_ref=None)
    execution._update_trailing_stop(conn, row)
    assert fake.cancels == [] and fake.oco_places == []
    after = dict(conn.execute("SELECT stop_loss FROM orders WHERE id = ?",
                              (row["id"],)).fetchone())
    assert float(after["stop_loss"]) > 95.0  # journal stop still trails


def test_fundability_buy_sell_hold_semantics():
    b = _binance()
    b._free_balance = lambda asset: {"USDT": 100.0, "BTC": 0.5,
                                     "XRP": 349.0}.get(asset, 0.0)
    # BUY needs free quote >= notional.
    assert b.fundability("BTC/USDT", "BUY", 0.001, 50_000.0) is None
    reason = b.fundability("BTC/USDT", "BUY", 0.01, 50_000.0)
    assert "insufficient free USDT" in reason and "need 500.00" in reason
    # SELL needs free base >= qty.
    assert b.fundability("BTC/USDT", "SELL", 0.1, 50_000.0) is None
    assert "insufficient free BTC" in b.fundability(
        "BTC/USDT", "SELL", 1.0, 50_000.0)
    # Balance endpoint unavailable → fail-open 'hold', never block trading.
    b._free_balance = lambda asset: None
    assert b.fundability("BTC/USDT", "BUY", 0.01, 50_000.0) == "hold"


def test_execute_intent_precheck_blocks_unfundable_before_journal(
        conn, monkeypatch):
    # The #403 failure mode: an intent the venue cannot fund dies BEFORE
    # submit with a clear reason and NO journal row (no 'error' row).
    fake = FakeBinance(fundability="binance: insufficient free XRP "
                                    "(have 349.00, need 2177.60) — fund "
                                    "the account or lower risk size")
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    res = execution.execute_intent(conn, _intent())
    assert res["order"] is None
    assert "insufficient free XRP" in res["error"]
    assert res["duplicate"] is False
    assert fake.places == 0                       # never reached the venue
    assert conn.execute(
        "SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    # A fundable intent still flows through normally.
    fake2 = FakeBinance()
    monkeypatch.setattr(execution, "get_broker", lambda: fake2)
    ok = execution.execute_intent(conn, _intent())
    assert ok["order"] is not None and not ok.get("error")
    assert fake2.places == 1


def test_close_position_cancels_bracket_before_market_close(conn, monkeypatch):
    fake = FakeBinance()
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    execution.execute_intent(conn, _intent())
    row = database.order_for_intent(conn, _intent().intent_hash)
    res = execution.close_position(conn, row, reason="manual")
    assert fake.cancels == ["9001"]          # bracket dead before the close
    assert len(fake.closes) == 1             # and a market close happened
    assert res["reason"] == "manual" and res["pnl"] > 0


def test_close_position_journals_fired_bracket_truthfully(conn, monkeypatch):
    # The bot was offline; the SL leg fired at 94.98 on the venue.
    fired = {"9001": {"status": "EXECUTED", "fired_leg": "sl",
                      "orders": [{"orderId": "111", "status": "FILLED",
                                  "type": "STOP_LOSS_LIMIT"},
                                 {"orderId": "222", "status": "CANCELED",
                                  "type": "TAKE_PROFIT_LIMIT"}]}}
    fake = FakeBinance(fired=fired)
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    execution.execute_intent(conn, _intent())
    row = database.order_for_intent(conn, _intent().intent_hash)
    qty = float(row["qty"])
    res = execution.close_position(conn, row, reason="manual")
    assert fake.closes == []                 # coins are gone — no market close
    assert res["reason"] == "sl"
    exit_fees = qty * 94.98 * _config.FEE_BPS / 10_000.0
    assert res["pnl"] == pytest.approx(
        (94.98 - 100.0) * qty - 0.1 - exit_fees, abs=1e-6)
    closed = database.order_for_intent(conn, _intent().intent_hash)
    assert closed["exit_reason"] == "sl"
    assert float(closed["exit_price"]) == pytest.approx(94.98)
    assert closed["oco_ref"] == "9001"


def test_reconcile_closes_fired_oco_rows(conn, monkeypatch):
    fake = FakeBinance()                     # patch BEFORE execute_intent:
    monkeypatch.setattr(execution, "get_broker", lambda: fake)  # no network
    execution.execute_intent(conn, _intent())
    row = database.order_for_intent(conn, _intent().intent_hash)
    qty = float(row["qty"])
    # TP leg fired while the bot was offline.
    fired = {"9001": {"status": "EXECUTED", "fired_leg": "tp",
                      "orders": [{"orderId": "111", "status": "CANCELED",
                                  "type": "STOP_LOSS_LIMIT"},
                                 {"orderId": "222", "status": "FILLED",
                                  "type": "TAKE_PROFIT_LIMIT"}]}}
    fake.fired.update(fired)
    monkeypatch.setattr(fake, "fetch_orders", lambda limit=100: [])
    updated = execution._reconcile_orders_impl(conn, limit=100)
    hit = [u for u in updated if u.get("id") == row["id"]]
    assert hit and hit[0]["exit_reason"] == "tp"
    assert hit[0]["source"] == "binance_oco"
    closed = database.order_for_intent(conn, _intent().intent_hash)
    assert closed["status"] == "closed"
    assert float(closed["exit_price"]) == pytest.approx(110.05)
    exit_fees = qty * 110.05 * _config.FEE_BPS / 10_000.0
    expected = (110.05 - 100.0) * qty - 0.1 - exit_fees
    assert closed["pnl"] == pytest.approx(expected, abs=1e-6)
    assert closed["slippage_cost"] is not None   # exit slip estimate stamped


def test_reconcile_ignores_rows_without_oco_ref(conn, monkeypatch):
    fake = FakeBinance()
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    execution.execute_intent(conn, _intent())
    row = database.order_for_intent(conn, _intent().intent_hash)
    conn.execute("UPDATE orders SET oco_ref = NULL WHERE id = ?", (row["id"],))
    conn.commit()
    fake = FakeBinance()
    status_calls = []
    monkeypatch.setattr(execution, "get_broker", lambda: fake)
    monkeypatch.setattr(fake, "fetch_orders", lambda limit=100: [])
    monkeypatch.setattr(fake, "oco_status",
                        lambda s, lid: status_calls.append(lid) or None)
    execution._reconcile_orders_impl(conn, limit=100)
    assert status_calls == []                # nothing to ask the venue about
    still = database.order_for_intent(conn, _intent().intent_hash)
    assert still["status"] == "filled"
