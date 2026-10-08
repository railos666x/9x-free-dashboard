"""Close hardening + deal-history reconcile tests (2026-09-30).

Covers the AUD/JPY retcode-10013 close bug (stale tick, no filling-mode
fallback, single shot) and the falsified broker_closed exits (MT5 rows
journaled at candle close instead of the real closing deal).

Run:  .venv/Scripts/python.exe -m pytest tests/test_close_hardening.py -q
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pytest

import config as _config
import database


@pytest.fixture(autouse=True)
def _reset_broker_config():
    """Keep the mutated module-level broker config from leaking between
    tests (EXECUTION_BROKER/EXNESS_MT5_ENABLED/DB_PATH are globals — a
    leaked DB_PATH sent test_forex's real-DB test at an empty temp file)."""
    saved = (_config.EXECUTION_BROKER, _config.EXNESS_MT5_ENABLED,
             _config.DB_PATH)
    yield
    (_config.EXECUTION_BROKER, _config.EXNESS_MT5_ENABLED,
     _config.DB_PATH) = saved


class _FakeMT5:
    """Offline MetaTrader5 double — no terminal touched."""

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 2
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1

    def __init__(self, positions=None, ticks=None, results=None, history=None):
        self.account = SimpleNamespace(equity=10000.0, margin_free=9000.0,
                                       balance=10000.0)
        self.positions = positions or []
        self.ticks = list(ticks) if ticks else []
        self.results = list(results) if results is not None else None
        self.history = history or {}     # position_id -> [deal, ...]
        self.orders = []
        self.tick_calls = 0

    def initialize(self, path=None):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "fake ok")

    def symbol_select(self, sym, enable=True):
        return True

    def symbol_info(self, sym):
        return SimpleNamespace(name=sym, trade_contract_size=100_000,
                               volume_step=0.01, volume_min=0.01,
                               volume_max=100, filling_mode=3)  # FOK|IOC

    def account_info(self):
        return self.account

    def symbol_info_tick(self, sym):
        self.tick_calls += 1
        if self.ticks:
            return self.ticks.pop(0)
        return SimpleNamespace(bid=1.10, ask=1.1001)

    def order_send(self, request):
        self.orders.append(dict(request))
        if self.results is not None:
            r = self.results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE,
                               price=request.get("price"), deal=4242, order=7)

    def positions_get(self, symbol=None):
        return self.positions

    def history_deals_get(self, position=None, ticket=None, date_from=None):
        if position is not None:
            return list(self.history.get(int(position), []))
        if ticket is not None:
            # Production MT5: history_deals_get(ticket=X) returns the single
            # deal with that ticket — from it we read position_id.
            t = int(ticket)
            for deals in self.history.values():
                for d in deals:
                    if int(getattr(d, "ticket", 0)) == t:
                        return [d]
            return []
        return []


def _deal(entry, ticket, ts, price, profit, commission=0.0, swap=0.0,
          volume=0.01, symbol="AUDJPY", position_id=1):
    return SimpleNamespace(entry=entry, ticket=ticket, time=ts, price=price,
                           profit=profit, commission=commission, swap=swap,
                           volume=volume, symbol=symbol,
                           type=_FakeMT5.ORDER_TYPE_SELL, position_id=position_id)


def _inject(fake):
    import execution
    sys.modules["MetaTrader5"] = fake
    execution.MT5Broker._mt5 = None
    return execution


def _fresh_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _config.DB_PATH = Path(tmp.name)
    conn = database.connect()
    database.init_db(conn)
    return conn


_CLOSE_ORDER = {"symbol": "AUD/JPY", "side": "SHORT", "qty": 1000}


# ── close_market hardening ────────────────────────────────────────────────
def test_close_requotes_fresh_tick_every_attempt():
    fake = _FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)],
        ticks=[SimpleNamespace(bid=109.10, ask=109.12),  # tick at attempt 1
               SimpleNamespace(bid=109.37, ask=109.39)],  # newer tick later
        results=[
            SimpleNamespace(retcode=10013, price=0.0, deal=0, order=0,
                            comment="Invalid request"),
            SimpleNamespace(retcode=10009, price=109.37, deal=9, order=8),
        ])
    ex = _inject(fake)
    fill = ex.get_broker().close_market(None, _CLOSE_ORDER)
    assert fill["status"] == "filled" and fill["fill_price"] == 109.37
    # Each attempt quoted its OWN fresh tick (attempt 1 → older tick,
    # retry → re-quoted newer tick) — never a tick cached from earlier.
    assert fake.orders[0]["price"] == 109.12  # closing SHORT → buy at ask
    assert fake.orders[1]["price"] == 109.39
    assert fake.tick_calls == 2


def test_close_falls_back_fok_to_ioc_on_10013():
    fake = _FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)],
        results=[
            SimpleNamespace(retcode=10013, price=0.0, deal=0, order=0,
                            comment="Invalid request"),
            SimpleNamespace(retcode=10009, price=109.37, deal=9, order=8),
        ])
    ex = _inject(fake)
    fill = ex.get_broker().close_market(None, _CLOSE_ORDER)
    assert fill["status"] == "filled" and fill["fill_price"] == 109.37
    assert len(fake.orders) == 2
    assert fake.orders[0]["type_filling"] == _FakeMT5.ORDER_FILLING_FOK
    assert fake.orders[1]["type_filling"] == _FakeMT5.ORDER_FILLING_IOC


def test_close_retries_then_succeeds_on_third_attempt():
    fake = _FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)],
        results=[
            SimpleNamespace(retcode=10013, price=0.0, deal=0, order=0,
                            comment="Invalid request"),
            SimpleNamespace(retcode=10013, price=0.0, deal=0, order=0,
                            comment="Invalid request"),
            SimpleNamespace(retcode=10009, price=109.35, deal=9, order=8),
        ])
    ex = _inject(fake)
    fill = ex.get_broker().close_market(None, _CLOSE_ORDER)
    assert fill["status"] == "filled"
    assert len(fake.orders) == 3  # FOK, IOC, IOC-retry (one fresh-tick retry)


def test_close_raises_after_exhausted_retries():
    fake = _FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)],
        results=[
            SimpleNamespace(retcode=10013, price=0.0, deal=0, order=0,
                            comment="Invalid request"),
        ] * 3)
    ex = _inject(fake)
    with pytest.raises(ex.BrokerError, match="10013"):
        ex.get_broker().close_market(None, _CLOSE_ORDER)
    assert len(fake.orders) == 3  # tried FOK + IOC + retry, then gave up


def test_close_does_not_retry_market_closed():
    fake = _FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)],
        results=[
            SimpleNamespace(retcode=10018, price=0.0, deal=0, order=0,
                            comment="Market closed"),
        ])
    ex = _inject(fake)
    with pytest.raises(ex.BrokerError, match="10018"):
        ex.get_broker().close_market(None, _CLOSE_ORDER)
    assert len(fake.orders) == 1  # terminal reject: no pointless retries


# ── closing_deal_for_position (ground truth from MT5 history) ─────────────
def test_closing_deal_returns_real_exit_numbers():
    ex = _inject(_FakeMT5(history={
        1975186562: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1859451756, 1000, 109.879, 0.0,
                  commission=-0.08),
            _deal(_FakeMT5.DEAL_ENTRY_OUT, 1861357426, 2000, 109.37, 3.24),
        ]}))
    _config.EXECUTION_BROKER = "mt5"   # get_broker() defaults to hybrid
    _config.EXNESS_MT5_ENABLED = True
    d = ex.get_broker().closing_deal_for_position(1975186562)
    assert d["price"] == 109.37
    assert d["profit"] == 3.24
    assert d["commission"] == -0.08           # entry-deal commission summed in
    assert d["pnl"] == pytest.approx(3.16)    # 3.24 + 0 swap - 0.08 commission
    assert d["ticket"] == 1861357426


def test_closing_deal_none_without_history():
    ex = _inject(_FakeMT5())
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    assert ex.get_broker().closing_deal_for_position(123) is None


def test_closing_deal_resolves_entry_ticket_to_position_id():
    """The journal stores the ENTRY deal ticket as broker_ref (AUDJPY #394:
    ref 1859451756 = entry deal, position id 1975186562). The lookup must
    resolve the entry ticket → position id or it finds nothing and callers
    fall back to the candle-close guess."""
    ex = _inject(_FakeMT5(history={
        1975186562: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1859451756, 1000, 109.879, 0.0,
                  commission=-0.08, position_id=1975186562),
            _deal(_FakeMT5.DEAL_ENTRY_OUT, 1861357426, 2000, 109.37, 3.24,
                  position_id=1975186562),
        ]}))
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    b = ex.get_broker()
    by_entry_ticket = b.closing_deal_for_position(1859451756)  # journal ref
    by_position_id = b.closing_deal_for_position(1975186562)   # MT5 native
    assert by_entry_ticket == by_position_id
    assert by_entry_ticket["price"] == 109.37
    assert by_entry_ticket["pnl"] == pytest.approx(3.16)  # 3.24 - 0.08


# ── reconcile: MT5 broker_closed rows journal the real deal ───────────────
def test_reconcile_journals_mt5_deal_history_and_scrubs_ghost_exits():
    import execution
    fake = _FakeMT5(history={
        1859299030: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1859299030, 1000, 1.13419, 0.0,
                  commission=-0.20),
            _deal(_FakeMT5.DEAL_ENTRY_OUT, 1859394521, 2000, 1.13399, 0.80),
        ]})
    sys.modules["MetaTrader5"] = fake
    execution.MT5Broker._mt5 = None
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    conn = _fresh_db()
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    database.record_order(conn, {
        "intent_hash": "h1", "symbol": "EUR/USD", "side": "SHORT",
        "qty": 4000, "entry": 1.1342, "stop_loss": 1.14, "take_profit": 1.13,
        "source": "ai", "status": "filled", "fill_price": 1.13419,
        "fill_time": old, "fees": 0.0, "broker_ref": "1859299030",
    })
    # Ghost: open row carrying exit fields from an aborted close path.
    database.record_order(conn, {
        "intent_hash": "h2", "symbol": "BTC/USDT", "side": "LONG",
        "qty": 0.038, "entry": 83560.0, "stop_loss": 82000.0,
        "take_profit": 86000.0, "source": "ai", "status": "filled",
        "fill_price": 83552.0, "fill_time": old, "fees": 0.0,
        "broker_ref": "7822690", "exit_time": old,  # dangling exit field
    })
    updated = execution.reconcile_orders(conn)

    row = database.order_for_intent(conn, "h1")
    assert row["status"] == "closed"
    assert float(row["exit_price"]) == pytest.approx(1.13399)  # TEXT column
    assert float(row["pnl"]) == pytest.approx(0.60)   # 0.80 - 0.20 commission
    assert row["exit_reason"] == "broker_closed"
    assert any(u.get("source") == "mt5_deal_history" for u in updated)

    ghost = database.order_for_intent(conn, "h2")
    assert ghost["status"] == "filled"         # still open
    assert ghost["exit_time"] is None          # dangling fields scrubbed
    assert ghost["exit_price"] is None and ghost["pnl"] is None


# ── broker_position: journal rows carry the MT5 POSITION ticket ───────────
def test_submit_stores_position_ticket_from_result():
    """order_send's MqlTradeResult carries `position` on a filled DEAL — the
    journal must store THAT ticket (deals are grouped by position id), not
    the entry deal id that broker_ref holds."""
    ex = _inject(_FakeMT5(results=[
        SimpleNamespace(retcode=10009, price=109.879, deal=1859451756,
                        order=7, position=1975186562),
    ]))
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    fill = ex.get_broker().place_market(None, {
        "symbol": "AUD/JPY", "side": "LONG", "qty": 1000,
        "entry": 109.879, "stop_loss": 110.20, "take_profit": 109.30,
    })
    assert fill["broker_ref"] == "1859451756"        # entry DEAL ticket
    assert fill["broker_position"] == "1975186562"   # POSITION ticket


def test_submit_resolves_position_from_deal_history_when_result_lacks_it():
    """Some MT5 builds return no `position` field on MqlTradeResult —
    resolve the executed deal's position_id from deal history instead."""
    ex = _inject(_FakeMT5(history={
        1975186562: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1859451756, 1000, 109.879, 0.0,
                  position_id=1975186562),
        ]},
        results=[
            # NOTE: no `position` attribute on the result namespace.
            SimpleNamespace(retcode=10009, price=109.879, deal=1859451756,
                            order=7),
        ]))
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    fill = ex.get_broker().place_market(None, {
        "symbol": "AUD/JPY", "side": "LONG", "qty": 1000,
        "entry": 109.879, "stop_loss": 110.20, "take_profit": 109.30,
    })
    assert fill["broker_position"] == "1975186562"


def test_close_scopes_to_exact_position_ticket():
    """Hedging account, two AUDJPY positions open. A row stamped with the
    position ticket must close THAT position and never touch the newer
    same-symbol position (which the newest-first sweep would consume)."""
    fake = _FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL),
                   SimpleNamespace(ticket=222, time=200, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)],
        results=[SimpleNamespace(retcode=10009, price=109.37, deal=9,
                                 order=8)])
    ex = _inject(fake)
    fill = ex.get_broker().close_market(None, dict(_CLOSE_ORDER,
                                                   broker_position="222"))
    assert fill["status"] == "filled"
    assert len(fake.orders) == 1                      # no sweep into #111
    assert fake.orders[0]["position"] == 222
    assert fill["broker_position"] == "222"          # row keeps its ticket


def test_close_raises_when_exact_position_missing():
    """A stamped ticket that is no longer open must FAIL LOUDLY, not fall
    back to closing a different position — that fallback is what doubled
    exposure on the hedging account in the 10013 era."""
    ex = _inject(_FakeMT5(
        positions=[SimpleNamespace(ticket=111, time=100, volume=0.01,
                                   type=_FakeMT5.POSITION_TYPE_SELL)]))
    with pytest.raises(ex.BrokerError, match="not open"):
        ex.get_broker().close_market(None, dict(_CLOSE_ORDER,
                                                broker_position="999"))


def test_reconcile_stamps_broker_position_on_legacy_rows():
    """Rows closed before the broker_position column (broker_ref = entry
    DEAL ticket) self-heal: the deal history knows the position id — stamp
    it so future reconciliation/cleanup matches the position EXACTLY."""
    import execution
    fake = _FakeMT5(history={
        1975186562: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1859451756, 1000, 109.879, 0.0,
                  commission=-0.08, position_id=1975186562),
            _deal(_FakeMT5.DEAL_ENTRY_OUT, 1861357426, 2000, 109.37, 3.24,
                  position_id=1975186562),
        ]})
    sys.modules["MetaTrader5"] = fake
    execution.MT5Broker._mt5 = None
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    conn = _fresh_db()
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    oid = database.record_order(conn, {
        "intent_hash": "h5", "symbol": "AUD/JPY", "side": "SHORT",
        "qty": 1000, "entry": 109.879, "stop_loss": 110.20,
        "take_profit": 109.30, "source": "ai", "status": "closed",
        "fill_price": 109.879, "fill_time": old, "fees": 0.0,
        "broker_ref": "1859451756", "pnl": 3.16,   # already truthful
    })
    conn.execute("UPDATE orders SET exit_price = 109.37, exit_time = ?, "
                 "exit_reason = 'broker_closed' WHERE id = ?", (old, oid))
    conn.commit()

    updated = execution.reconcile_orders(conn)
    row = database.order_for_intent(conn, "h5")
    assert row["broker_position"] == "1975186562"    # stamped from history
    assert any(u.get("stamped_position") == "1975186562" for u in updated)
    # numbers untouched
    assert float(row["pnl"]) == pytest.approx(3.16)


def test_reconcile_stamps_open_legacy_rows_before_close():
    """An OPEN MT5 row placed by the pre-column build (broker_ref = entry
    deal, no position ticket) gets stamped the FIRST time reconcile sees
    it — so a later close/reconcile targets the exact position."""
    import execution
    fake = _FakeMT5(history={
        1975186564: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1868079607, 1000, 158.207, 0.0,
                  position_id=1975186564),
        ]})
    sys.modules["MetaTrader5"] = fake
    execution.MT5Broker._mt5 = None
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    conn = _fresh_db()
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    database.record_order(conn, {
        "intent_hash": "h6", "symbol": "USD/JPY", "side": "LONG",
        "qty": 600, "entry": 158.207, "stop_loss": 158.80,
        "take_profit": 157.60, "source": "ai", "status": "filled",
        "fill_price": 158.207, "fill_time": recent, "fees": 0.0,
        "broker_ref": "1868079607",   # entry deal, no broker_position
    })
    updated = execution.reconcile_orders(conn)
    row = database.order_for_intent(conn, "h6")
    assert row["status"] == "filled"                 # still open
    assert row["broker_position"] == "1975186564"    # stamped while open
    assert any(u.get("stamped_position") == "1975186564" for u in updated)


def test_record_order_and_exit_persist_broker_position():
    conn = _fresh_db()
    oid = database.record_order(conn, {
        "intent_hash": "h7", "symbol": "AUD/JPY", "side": "SHORT",
        "qty": 1000, "entry": 109.879, "stop_loss": 110.20,
        "take_profit": 109.30, "source": "ai", "status": "filled",
        "fill_price": 109.879, "fill_time": "2026-10-01T00:00:00+00:00",
        "fees": 0.0, "broker_ref": "1859451756",
        "broker_position": "1975186562",
    })
    assert database.order_for_intent(conn, "h7")["broker_position"] \
        == "1975186562"
    # update_order_exit COALESCE: stamps the ticket at close time...
    database.update_order_exit(conn, oid, 109.37,
                               "2026-10-01T01:00:00+00:00", "manual", 3.16,
                               0.0)
    row = database.order_for_intent(conn, "h7")
    assert row["broker_position"] == "1975186562"    # never erased
    # ...and stamps legacy rows that close with the ticket resolved.
    oid2 = database.record_order(conn, {
        "intent_hash": "h8", "symbol": "EUR/USD", "side": "LONG",
        "qty": 1000, "entry": 1.134, "stop_loss": 1.14,
        "take_profit": 1.13, "source": "ai", "status": "filled",
        "fill_price": 1.134, "fill_time": "2026-10-01T00:00:00+00:00",
        "fees": 0.0, "broker_ref": "900",
    })
    database.update_order_exit(conn, oid2, 1.13399,
                               "2026-10-01T01:00:00+00:00", "manual", 0.6,
                               0.0, broker_position=1975186563)
    assert database.order_for_intent(conn, "h8")["broker_position"] \
        == "1975186563"


def test_reconcile_corrects_falsified_broker_closed_numbers():
    """Rows closed BEFORE the deal-history fix carry candle-close guesses
    (XAU/USD #391 journaled +21.40; the real deal paid +4.62). The
    correction pass rewrites them from the terminal's deal history."""
    import execution
    fake = _FakeMT5(history={
        1859138095: [
            _deal(_FakeMT5.DEAL_ENTRY_IN, 1859138095, 1000, 4164.191, 0.0,
                  commission=-0.11, volume=0.01, symbol="XAUUSD"),
            _deal(_FakeMT5.DEAL_ENTRY_OUT, 1859394499, 2000, 4168.814, 4.62,
                  volume=0.01, symbol="XAUUSD"),
        ]})
    sys.modules["MetaTrader5"] = fake
    execution.MT5Broker._mt5 = None
    _config.EXECUTION_BROKER = "mt5"
    _config.EXNESS_MT5_ENABLED = True
    conn = _fresh_db()
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    oid = database.record_order(conn, {
        "intent_hash": "h9", "symbol": "XAU/USD", "side": "LONG",
        "qty": 1.0, "entry": 4175.70, "stop_loss": 4150.0,
        "take_profit": 4220.0, "source": "ai", "status": "closed",
        "fill_price": 4164.191, "fill_time": old, "fees": 0.0,
        "broker_ref": "1859138095", "pnl": 21.40,
    })
    # record_order has no exit columns — set them like the pre-fix reconcile
    # did when it journaled the candle-close guess.
    conn.execute("UPDATE orders SET exit_price = 4197.10, exit_time = ?, "
                 "exit_reason = 'broker_closed' WHERE id = ?", (old, oid))
    conn.commit()
    updated = execution.reconcile_orders(conn)

    row = database.order_for_intent(conn, "h9")
    assert float(row["exit_price"]) == pytest.approx(4168.814)
    assert float(row["pnl"]) == pytest.approx(4.51)   # 4.62 - 0.11 commission
    assert any(u.get("corrected") for u in updated)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
