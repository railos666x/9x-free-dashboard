"""Phase-2 sizing tests (roadmap 2026-09-30, implemented 2026-10-01).

P1 inverse-volatility scaling: risk_pct × (target_vol / realized_vol),
clamped [0.5×, 2×]. P2 rolling-bucket fractional-Kelly: per
(venue, regime, news-tag) bucket, the last-30 closed trades' expectancy
drives a multiplier clamped [0.5×, 1.5×] — 0 when the bucket's 95% CI on
expectancy is negative (the data-driven ranging-regime gate).

Run:  .venv/Scripts/python.exe -m pytest tests/test_sizing.py -q
"""
import sys
import tempfile
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
import execution
import risk


@pytest.fixture(autouse=True)
def _restore_config():
    """Snapshot the SIZE_* knobs this suite flips — never leak into other
    test files (same class of leak that once broke test_forex via DB_PATH)."""
    keys = [k for k in dir(_config) if k.startswith("SIZE_")]
    saved = {k: getattr(_config, k) for k in keys}
    yield
    for k, v in saved.items():
        setattr(_config, k, v)


@pytest.fixture()
def conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    _config.DB_PATH = Path(tmp.name)
    c = database.connect()
    database.init_db(c)
    yield c


def _win(pnl=100.0):
    return {"pnl": pnl, "entry": 100.0, "stop_loss": 90.0, "qty": 10.0}


def _loss(pnl=-45.0):
    return {"pnl": pnl, "entry": 100.0, "stop_loss": 90.0, "qty": 10.0}


# ── P1: inverse-volatility multiplier ─────────────────────────────────────
def test_inverse_vol_scales_quiet_tape_up():
    # realized 0.5% vs 1% target → risk doubles, clamped at 2×
    assert risk.inverse_vol_multiplier(0.005) == 2.0


def test_inverse_vol_scales_wild_tape_down():
    # realized 2.5% vs 1% target → risk halves, clamped at 0.5×
    assert risk.inverse_vol_multiplier(0.025) == 0.5


def test_inverse_vol_neutral_when_vol_unknown():
    # Missing/zero/invalid ATR must never invent an opinion
    assert risk.inverse_vol_multiplier(None) == 1.0
    assert risk.inverse_vol_multiplier(0.0) == 1.0
    assert risk.inverse_vol_multiplier(float("nan")) == 1.0


def test_inverse_vol_respects_custom_clamp():
    assert risk.inverse_vol_multiplier(0.001, lo=0.8, hi=1.2) == 1.2
    assert risk.inverse_vol_multiplier(0.10, lo=0.8, hi=1.2) == 0.8


# ── P2: bucket Kelly multiplier ────────────────────────────────────────────
def test_bucket_kelly_neutral_without_evidence():
    assert risk.bucket_kelly_multiplier([]) == 1.0
    assert risk.bucket_kelly_multiplier([_win()]) == 1.0   # n=1: no opinion


def test_bucket_kelly_zero_when_ci_negative():
    # 25 losses, 0 wins: mean = -1.5R, CI far below 0 → starve the bucket
    recs = [_loss(-150.0)] * 25
    assert risk.bucket_kelly_multiplier(recs) == 0.0


def test_bucket_kelly_neutral_when_negative_but_not_significant():
    # 1 win + 1 heavy loss: negative mean, n=2 → CI spans 0 → neutral 1.0,
    # never the kill switch
    recs = [_win(+100.0), _loss(-200.0)]
    assert risk.bucket_kelly_multiplier(recs) == 1.0


def test_bucket_kelly_clamped_positive_edge():
    # implausibly good bucket (all wins) still capped at 1.5×
    assert risk.bucket_kelly_multiplier([_win()] * 30) \
        == pytest.approx(_config.SIZE_KELLY_MAX_MULT)


def test_bucket_kelly_scales_with_edge_quality():
    strong = risk.bucket_kelly_multiplier(
        [_win(200.0) if i % 3 else _loss(-45.0) for i in range(30)])
    weak = risk.bucket_kelly_multiplier(
        [_win(60.0) if i % 3 else _loss(-45.0) for i in range(30)])
    assert strong >= weak > 0           # both positive, better edge ≥ worse
    assert strong <= _config.SIZE_KELLY_MAX_MULT


def test_bucket_kelly_skips_rows_without_levels():
    recs = [_win(), {"pnl": 5.0, "entry": None, "stop_loss": None, "qty": 1},
            {"pnl": 7.0}] * 8
    # unusable rows ignored, not crashing and not poisoning the stats
    assert risk.bucket_kelly_multiplier(recs) == 1.5


# ── bucket keys / news tags ───────────────────────────────────────────────
def test_news_tag_classification():
    tag = execution._news_tag
    assert tag("ai/news SHORT: AUD weakness due to regulatory news. | regime=ranging") \
        == "regulation"
    assert tag("Bitcoin gains momentum from bullish news and ETF expansion") == "etf"
    assert tag("General market risk-off sentiment due to high bond yields.") \
        == "inflation"
    assert tag("Fed signals rate cut chatter ahead of FOMC") == "central_bank"
    assert tag("Institutional inflows lift BTC momentum") == "macro"
    assert tag("something entirely unclassifiable") == "other"
    assert tag(None) == "none"
    assert tag("") == "none"


def test_row_bucket_keys_most_specific_first():
    keys = execution._row_bucket_keys("mt5", "AUD/USD", "ranging",
                                      "AUD weakness due to regulatory news")
    assert keys == ["mt5|AUD/USD|ranging|regulation", "mt5|ranging|regulation",
                    "mt5|ranging"]
    # generic tags don't get the venue-wide fallback bucket
    keys2 = execution._row_bucket_keys("mt5", "AUD/USD", "ranging", "meh")
    assert keys2 == ["mt5|AUD/USD|ranging|other", "mt5|ranging|other"]
    keys3 = execution._row_bucket_keys("binance", "BTC/USDT", None, None)
    assert keys3 == ["binance|BTC/USDT|unknown|none", "binance|unknown|none"]


def test_bucket_records_filters_by_key_and_caps_lookback(conn):
    venue = "mt5"
    common = dict(side="LONG", qty=10.0, entry=100.0, stop_loss=90.0,
                  take_profit=120.0, source=venue, status="closed",
                  fill_price=100.0, fill_time="2026-09-30T00:00:00+00:00",
                  fees=0.0, broker_ref="1")
    for i in range(40):
        database.record_order(conn, dict(
            common, intent_hash=f"rb{i}", symbol="AUD/USD",
            trend_regime="ranging",
            reasons="AUD weakness due to regulatory news", pnl=10.0 if i % 2 else -4.0))
    # same venue, DIFFERENT bucket (symbol) — must be excluded
    for i in range(5):
        database.record_order(conn, dict(
            common, intent_hash=f"rx{i}", symbol="EUR/USD",
            trend_regime="ranging",
            reasons="AUD weakness due to regulatory news", pnl=99.0))
    # same symbol, other venue — must be excluded
    database.record_order(conn, dict(
        common, intent_hash="rv1", symbol="AUD/USD", source="binance",
        trend_regime="ranging",
        reasons="AUD weakness due to regulatory news", pnl=99.0))
    recs = execution._bucket_records(
        conn, "mt5|AUD/USD|ranging|regulation", lookback=30)
    assert len(recs) == 30
    assert all(r["pnl"] != 99.0 for r in recs)
    assert all(abs(float(r["entry"]) - 100.0) < 1e-9 for r in recs)


def test_kelly_multiplier_picks_most_specific_informed_bucket(conn):
    # Only the venue|symbol|regime|tag bucket has >= 2 usable trades; the
    # trade must inherit ITS verdict (starved), not a neutral fallback.
    common = dict(side="SHORT", qty=1000.0, entry=0.70, stop_loss=0.71,
                  take_profit=0.68, source="mt5", status="closed",
                  fill_price=0.70, fill_time="2026-09-30T00:00:00+00:00",
                  fees=0.0, broker_ref="2")
    for i in range(6):
        database.record_order(conn, dict(
            common, intent_hash=f"sb{i}", symbol="AUD/USD",
            trend_regime="ranging",
            reasons="AUD weakness due to regulatory news", pnl=-90.0))
    mult = execution._kelly_multiplier_for(
        conn, "AUD/USD", "ranging", "AUD weakness due to regulatory news")
    assert mult == 0.0


# ── integration: _sized_qty wiring ─────────────────────────────────────────
def _intent(symbol="BTC/USDT", direction="LONG", entry=84000.0, stop=83000.0,
            confidence=10.0, regime="ranging", reasons=None):
    from arbitrator import Intent, intent_hash
    return Intent(symbol=symbol, direction=direction, entry=entry,
                  stop_loss=stop, take_profit=entry + 2 * (entry - stop),
                  sources=["math_engine"], window="1h", confidence=confidence,
                  intent_hash=intent_hash(symbol, direction, "1h"),
                  trend_regime=regime, reasons=reasons or [])


def test_sized_qty_applies_inverse_vol_and_kelly(conn, monkeypatch):
    _config.SIZE_VOL_ENABLED = True
    _config.SIZE_KELLY_ENABLED = True
    # lift the notional cap out of the way so the risk-path math is visible
    monkeypatch.setattr(_config, "MAX_POSITION_PCT", 1.0)
    # quiet tape: ATR 0.0005 on 1.10 → 0.045% realized vs 1% target → 2×
    monkeypatch.setattr(execution, "_get_atr",
                        lambda conn_, sym, iv="1h": 0.0005)
    tel = {}
    boosted = execution._sized_qty(conn, _intent(), None, 1.10, 1.05,
                                   telemetry=tel)
    assert tel["vol_mult"] == 2.0 and tel["kelly_mult"] == 1.0
    # no bucket evidence → kelly neutral; vol off → plain 1% risk
    _config.SIZE_VOL_ENABLED = False
    plain = execution._sized_qty(conn, _intent(), None, 1.10, 1.05)
    _config.SIZE_VOL_ENABLED = True
    equity = database.paper_equity(conn)
    assert plain == pytest.approx(0.01 * equity / 0.05)
    assert boosted == pytest.approx(2.0 * plain)
    # hard cap survives the boost: risk stays ≤ 2% of equity
    assert boosted <= (0.02 * equity) / 0.05 + 1e-6


def test_sized_qty_kelly_starved_bucket_vetoes_trade(conn, monkeypatch):
    _config.SIZE_VOL_ENABLED = False
    _config.SIZE_KELLY_ENABLED = True
    monkeypatch.setattr(execution, "_get_atr",
                        lambda conn_, sym, iv="1h": 0.0)
    common = dict(side="SHORT", qty=1000.0, entry=1.10, stop_loss=1.11,
                  take_profit=1.08, source="mt5", status="closed",
                  fill_price=1.10, fill_time="2026-09-30T00:00:00+00:00",
                  fees=0.0, broker_ref="3")
    for i in range(6):
        database.record_order(conn, dict(
            common, intent_hash=f"sv{i}", symbol="EUR/USD",
            trend_regime="ranging",
            reasons="risk-off sentiment on bond yields",
            pnl=-120.0))
    tel = {}
    size = execution._sized_qty(
        conn, _intent(symbol="EUR/USD",
                      reasons=["risk-off sentiment on bond yields"]), None,
        1.10, 1.09, telemetry=tel)
    assert size == 0.0
    assert tel["kelly_mult"] == 0.0


def test_sized_qty_never_exceeds_hard_risk_cap(conn, monkeypatch):
    _config.SIZE_VOL_ENABLED = True
    _config.SIZE_KELLY_ENABLED = True
    monkeypatch.setattr(execution, "_get_atr",
                        lambda conn_, sym, iv="1h": 0.0002)  # 2× clamp
    # max confidence (2×) on top → 4× base would be 4% without the cap
    size = execution._sized_qty(conn, _intent(confidence=80.0), None,
                                1.10, 1.09)
    equity = database.paper_equity(conn)
    max_qty = (0.02 * equity) / 0.01
    assert size <= max_qty + 1e-6


def test_sized_qty_disabled_knobs_keep_legacy_behavior(conn, monkeypatch):
    _config.SIZE_VOL_ENABLED = False
    _config.SIZE_KELLY_ENABLED = False
    monkeypatch.setattr(execution, "_get_atr",
                        lambda conn_, sym, iv="1h": 0.0005)
    tel = {}
    s1 = execution._sized_qty(conn, _intent(), None, 1.10, 1.09,
                              telemetry=tel)
    s2 = execution._sized_qty(conn, _intent(), None, 1.10, 1.09)
    assert tel == {"vol_mult": 1.0, "kelly_mult": 1.0, "conf_mult": 1.0}
    assert s1 == pytest.approx(s2)


def test_execute_intent_records_size_multiplier_telemetry(conn, monkeypatch):
    from arbitrator import intent_hash as ih
    _config.SIZE_VOL_ENABLED = True
    _config.SIZE_KELLY_ENABLED = True
    monkeypatch.setattr(execution, "_get_atr",
                        lambda conn_, sym, iv="1h": 0.0005)
    intent = _intent(symbol="BTC/USDT", entry=84000.0, stop=83000.0)
    # dry run: no broker touched, but sizing telemetry must land on the row
    res = execution.execute_intent(conn, intent, dry_run=True)
    assert res["order"] is not None
    assert "size=vol2.0,kelly1.0,conf1.0" in res["order"]["reasons"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
