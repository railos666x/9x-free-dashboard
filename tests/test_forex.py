"""Tests for backend/forex.py — FX mastery layer.

Run:  .venv/Scripts/python.exe -m pytest tests/test_forex.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from datetime import datetime

import pandas as pd
import pytest

import forex


# ── Session model ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("h,want", [
    (2, "asian"), (7, "london"), (12, "london"), (13, "overlap"),
    (15, "overlap"), (16, "ny"), (21, "ny"), (22, "dead"), (23, "asian"),
])
def test_session_boundaries(h, want):
    s = forex.session_info(datetime(2026, 9, 30, h))
    assert s["session"] == want
    assert 0 < s["hours_to_next"] <= 24
    assert s["liquidity"] > 0


def test_session_weekend_flag():
    assert forex.session_info(datetime(2026, 10, 3, 12))["weekend"] is True   # Saturday
    assert forex.session_info(datetime(2026, 10, 1, 12))["weekend"] is False  # Thursday


def test_pair_sessions():
    assert "asian" in forex.pair_sessions("USD/JPY")
    assert "london" in forex.pair_sessions("EUR/GBP")
    assert "ny" in forex.pair_sessions("USD/CAD")
    assert forex.pair_sessions("AUD/NZD") == ["asian"]


# ── Currency strength + synthetic USD index (pure functions) ────────────────
def test_currency_strength_credits_debits():
    chg = {"EUR/USD": 1.0, "USD/JPY": 0.5}  # EUR strong, JPY weak, USD mixed
    scores = dict(forex.currency_strength(chg))
    assert scores["EUR"] == pytest.approx(1.0)
    assert scores["JPY"] == pytest.approx(-0.5)
    assert scores["USD"] == pytest.approx(-0.5)  # -1 (EUR/USD) + 0.5 (USD/JPY)
    ranked = [c for c, _ in forex.currency_strength(chg)]
    assert ranked[0] == "EUR"
    assert ranked[-1] == "JPY"


def test_synthetic_usd_index_matches_real_weights():
    # USD weakening against every component → index -1.0 (weights sum to 1.0)
    chg = {"EUR/USD": 1.0, "GBP/USD": 1.0, "USD/JPY": -1.0,
           "USD/CAD": -1.0, "USD/SEK": -1.0, "USD/CHF": -1.0}
    assert forex.synthetic_usd_index(chg) == pytest.approx(-1.0)
    # Missing component → None (never a half-baked index)
    assert forex.synthetic_usd_index({"EUR/USD": 1.0}) is None


# ── Carry ───────────────────────────────────────────────────────────────────
def test_carry_bias_live_swaps():
    b = forex.carry_bias("USD/JPY", swap_long=12.3, swap_short=-8.1)
    assert b["favored_side"] == "LONG"
    assert b["source"] == "mt5"
    b2 = forex.carry_bias("EUR/USD", swap_long=-5.0, swap_short=-2.0)
    assert b2["favored_side"] == "SHORT"
    assert b2["source"] == "mt5"


def test_carry_bias_priors():
    assert forex.carry_bias("USD/MXN")["favored_side"] == "LONG"
    assert forex.carry_bias("EUR/USD")["favored_side"] == "SHORT"
    unknown = forex.carry_bias("XAU/USD")
    assert unknown["favored_side"] is None
    assert unknown["source"] == "none"


# ── Exposure grouping (journal rows) ────────────────────────────────────────
def test_fx_exposure_groups_correlated_risk():
    rows = [
        {"symbol": "AUD/CAD", "side": "SHORT"},   # -AUD +CAD
        {"symbol": "AUD/JPY", "side": "SHORT"},   # -AUD +JPY
        {"symbol": "AUD/NZD", "side": "SHORT"},   # -AUD +NZD
        {"symbol": "EUR/AUD", "side": "LONG"},    # +EUR -AUD
    ]
    ex = forex.fx_exposure(rows)
    assert ex["by_currency"]["AUD"] == pytest.approx(-4.0)
    assert ex["by_currency"]["CAD"] == pytest.approx(1.0)
    assert ex["top_currency"] == "AUD"
    assert ex["top_net"] == -4
    assert ex["concentration"] == 4


def test_fx_exposure_ignores_non_fx():
    rows = [{"symbol": "BTC/USDT", "side": "LONG"},
            {"symbol": "EUR/USD", "side": "closed"}]
    ex = forex.fx_exposure(rows)
    assert ex["by_currency"] == {}
    assert ex["concentration"] == 0


def test_fx_exposure_empty():
    assert forex.fx_exposure([])["top_currency"] is None


# ── pair_snapshot with injected fakes ───────────────────────────────────────
def _mk_df(closes):
    n = len(closes)
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * n, "open_time": range(n),
    })


def test_pair_snapshot_trend_labels():
    def fake_candles(conn, symbol, interval, limit):
        if symbol == "uptrend":
            return _mk_df([1.0 + 0.002 * i for i in range(220)])
        return _mk_df([1.0 - 0.002 * i for i in range(220)])

    def fake_add_all(df):
        df = df.copy()
        df["rsi"] = 62.0 if df["close"].iloc[-1] > df["close"].iloc[0] else 38.0
        df["atr"] = 0.002
        return df

    def fake_ema(series, n):
        return series.ewm(span=n, adjust=False).mean()

    up = forex.pair_snapshot(None, "uptrend", fake_candles, fake_add_all, fake_ema)
    down = forex.pair_snapshot(None, "downtrend", fake_candles, fake_add_all, fake_ema)
    assert up["trend"] == "bullish" and up["momentum"] == "bull"
    assert down["trend"] == "bearish" and down["momentum"] == "bear"
    assert up["chg_24h"] > 0.3  # 24 bars x 0.2%/bar climb


def test_pair_snapshot_short_history():
    def fake_candles(conn, symbol, interval, limit):
        return _mk_df([1.0] * 30)

    snap = forex.pair_snapshot(None, "thin", fake_candles, lambda df: df, lambda s, n: s)
    assert snap["has_data"] is False


# ── overview() against the real DB ──────────────────────────────────────────
def test_overview_runs_against_real_db():
    import database

    conn = database.connect()
    data = forex.overview(conn)
    assert data["ok"] is True
    assert 0 <= data["session"]["utc_hour"] <= 23
    assert data["pairs_total"] == len(forex.FX_PAIRS)
    assert data["pairs_with_data"] >= 15  # majors should all have 1h candles
    assert isinstance(data["strength"], list)
    assert "concentration" in data["exposure"]
