"""Government-officials shadow feed prototype (2026-10-05).

- gov_news is a SEPARATE pool: save/load/purge + zero contact with `news`.
- gov_analyst: official headlines → the existing Groq scorer + noise gate →
  paper-only journaling (test_trades, ai_model='groq-gov') → stale-row
  expiry. Direction thresholds identical to the live path.
- The Groq call and every signed/network call is faked; nothing here can
  reach a broker.

Run:  .venv/Scripts/python.exe -m pytest tests/test_gov_feed.py -q
"""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pytest
from unittest.mock import patch

import config as _config
import database
import gov_analyst


@pytest.fixture(autouse=True)
def _restore_config():
    keys = [k for k in dir(_config) if k.startswith(
        ("GOV_", "NEWS_", "AI_SENTIMENT", "AI_CONFIDENCE"))]
    saved = {k: getattr(_config, k) for k in keys}
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


def _iso(hours_ago: float = 1.0) -> str:
    """Relative timestamp — fixed dates went stale when the calendar turned
    and the freshness gate (correctly) filtered every 'fresh' fixture."""
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours_ago)).isoformat()


def _item(source="Fed", title="Federal Reserve holds rates steady",
          published=None):
    return {"source": source, "title": title, "url": "https://x.gov/a",
            "published_at": published or _iso(1.0), "sentiment": "neutral",
            "sentiment_score": 0.0}


# ── gov_news pool: isolated save/load/purge ────────────────────────────
def test_save_and_load_gov_news_roundtrip(conn):
    added = database.save_gov_news(conn, [_item(), _item(source="ECB")])
    assert added == 2
    rows = database.load_gov_news(conn)
    assert {r["source"] for r in rows} == {"Fed", "ECB"}


def test_save_gov_news_dedupes_on_source_title(conn):
    database.save_gov_news(conn, [_item()])
    assert database.save_gov_news(conn, [_item()]) == 0  # same story again


def test_gov_news_never_touches_live_news_pool(conn):
    n_before = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    database.save_gov_news(conn, [_item(), _item(source="SEC")])
    n_after = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    assert n_before == n_after  # the whole point of the separate pool


def test_purge_gov_news_removes_only_old_rows(conn):
    database.save_gov_news(conn, [_item()])
    conn.execute("UPDATE gov_news SET published_ts = '2026-09-01T00:00:00'")
    conn.commit()
    database.save_gov_news(conn, [_item(source="CFTC",
                                       published=_iso(0.5))])
    removed = database.purge_gov_news(conn, days=7)
    assert removed == 1
    left = {r["source"] for r in database.load_gov_news(conn)}
    assert left == {"CFTC"}


# ── gov_headlines shape + freshness ────────────────────────────────────
def test_gov_headlines_shape_and_freshness(conn):
    database.save_gov_news(conn, [
        _item(title="Fresh release", published=_iso(1.0)),
        _item(source="BoE", title="Fossil release",
              published="2020-01-01T00:00:00+00:00"),
    ])
    heads = gov_analyst.gov_headlines(conn)
    titles = [h["title"] for h in heads]
    assert "Fresh release" in titles and "Fossil release" not in titles
    h = heads[0]
    assert set(h) >= {"title", "tag", "source", "published_at", "age_hours"}


# ── direction thresholds mirror the live path ──────────────────────────
def test_direction_from_score_matches_live_thresholds():
    assert gov_analyst._direction_from_score(0.5, 0.9) == "LONG"
    assert gov_analyst._direction_from_score(-0.5, 0.9) == "SHORT"
    assert gov_analyst._direction_from_score(0.0, 0.9) == "HOLD"
    # below the confidence floor → HOLD regardless of score
    assert gov_analyst._direction_from_score(0.5, 0.2) == "HOLD"


# ── the full shadow cycle with a faked Groq scorer ─────────────────────
class FakeAnalyst:
    """Stands in for news_analyst.analyze_headlines (no network)."""
    payload = None
    calls = 0

    @classmethod
    def analyze(cls, heads):
        cls.calls += 1
        cls.seen_titles = [h["title"] for h in heads]
        return cls.payload


def _patch_scorer(monkeypatch, assets):
    FakeAnalyst.payload = {"assets": assets, "model": "fake"}
    FakeAnalyst.calls = 0
    monkeypatch.setattr(gov_analyst, "score_official_pool",
                        lambda conn_: ({"assets": assets, "model": "fake"}
                                       if assets else None))


def test_shadow_cycle_journals_paper_trade(conn, monkeypatch):
    # A crypto universe symbol so candles exist via the normal tables.
    conn.execute("INSERT OR REPLACE INTO candles(symbol, interval, open_time, open, "
                 "high, low, close, volume) VALUES ('BTC/USDT','1h',1,100,101,"
                 "99,100,1)")
    conn.commit()
    # Two distinct official outlets — the corroboration gate requires it
    # (a single-outlet story must be held back by the shadow gate too).
    database.save_gov_news(conn, [
        _item(source="SEC", title="SEC approves Bitcoin ETF"),
        _item(source="CFTC", title="CFTC clears Bitcoin product"),
    ])
    asset = {"asset": "BTC", "sentiment_score": 0.6, "confidence": 0.8,
             "impact_horizon": "short_term", "summary": "SEC approves ETF",
             "headlines": []}
    _patch_scorer(monkeypatch, [asset])
    import test_ai  # real paper path, but pin the candle price
    with patch.object(test_ai, "_current_price",
                      lambda c, s, interval="1h": 100.0):
        out = gov_analyst.run_shadow_cycle(conn)
    assert out and out[0].get("paper") is True and out[0]["symbol"] == "BTC/USDT"
    row = conn.execute("SELECT * FROM test_trades WHERE id = ?",
                       (out[0]["shadow_id"],)).fetchone()
    row = dict(row)
    assert row["ai_model"] == "groq-gov"          # tagged for separation
    assert row["direction"] == "LONG" and row["outcome"] == "open"
    assert row["stop_loss"] < row["entry_price"] < row["take_profit"]


def test_shadow_cycle_skips_hold_and_gated(conn, monkeypatch):
    conn.execute("INSERT OR REPLACE INTO candles(symbol, interval, open_time, open, "
                 "high, low, close, volume) VALUES ('BTC/USDT','1h',1,100,101,"
                 "99,100,1)")
    conn.commit()
    # HOLD (weak score) → nothing journaled at all.
    _patch_scorer(monkeypatch, [{"asset": "BTC", "sentiment_score": 0.1,
                                 "confidence": 0.9, "summary": "", "headlines": []}])
    out = gov_analyst.run_shadow_cycle(conn)
    assert out == []
    # Unknown asset → no mapping → skipped silently.
    _patch_scorer(monkeypatch, [{"asset": "BOGUS", "sentiment_score": 0.6,
                                 "confidence": 0.9, "summary": "", "headlines": []}])
    assert gov_analyst.run_shadow_cycle(conn) == []


def test_shadow_cycle_dedupes_same_day(conn, monkeypatch):
    conn.execute("INSERT OR REPLACE INTO candles(symbol, interval, open_time, open, "
                 "high, low, close, volume) VALUES ('BTC/USDT','1h',1,100,101,"
                 "99,100,1)")
    # Two distinct outlets mention the asset → the corroboration gate passes.
    database.save_gov_news(conn, [
        _item(source="SEC", title="SEC approves Bitcoin ETF"),
        _item(source="CFTC", title="CFTC clears Bitcoin product"),
    ])
    conn.commit()
    asset = {"asset": "BTC", "sentiment_score": 0.6, "confidence": 0.8,
             "summary": "same signal", "headlines": []}
    _patch_scorer(monkeypatch, [asset])
    import test_ai
    with patch.object(test_ai, "_current_price",
                      lambda c, s, interval="1h": 100.0):
        first = gov_analyst.run_shadow_cycle(conn)
        second = gov_analyst.run_shadow_cycle(conn)
    assert first[0].get("paper") is True
    assert second and second[0].get("duplicate") is True
    n = conn.execute("SELECT COUNT(*) FROM test_trades WHERE "
                     "ai_model='groq-gov'").fetchone()[0]
    assert n == 1


def test_expire_stale_closes_old_shadow_rows_only(conn, monkeypatch):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc)
           - timedelta(hours=_config.GOV_SHADOW_MAX_HOLD_BARS + 5)).isoformat()
    recent = datetime.now(timezone.utc).isoformat()
    tid_old = database.record_test_trade(
        conn, symbol="BTC/USDT", direction="LONG", sentiment=0.5,
        confidence=0.5, confirm_score=0, regime=None, dxy_score=0, smc_score=0,
        session=None, calendar_blocked=0, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, ai_model="groq-gov", ai_reason="shadow")
    tid_live = database.record_test_trade(
        conn, symbol="ETH/USDT", direction="LONG", sentiment=0.5,
        confidence=0.5, confirm_score=0, regime=None, dxy_score=0, smc_score=0,
        session=None, calendar_blocked=0, entry_price=2000.0, stop_loss=1900.0,
        take_profit=2200.0, ai_model="groq", ai_reason="live paper book")
    conn.execute("UPDATE test_trades SET ran_at = ? WHERE id = ?",
                 (old, tid_old))
    conn.execute("UPDATE test_trades SET ran_at = ? WHERE id = ?",
                 (recent, tid_live))
    conn.commit()
    conn.execute("INSERT OR REPLACE INTO candles(symbol, interval, open_time, open, "
                 "high, low, close, volume) VALUES ('BTC/USDT','1h',2,100,101,"
                 "99,103,1)")
    conn.commit()
    closed = gov_analyst._expire_stale(conn)
    assert closed == [tid_old]                       # only the shadow row
    old_row = dict(conn.execute("SELECT * FROM test_trades WHERE id = ?",
                                (tid_old,)).fetchone())
    assert old_row["outcome"] == "win"               # 103 > 100 exit
    assert old_row["exit_reason"] == "shadow_expired"
    live_row = dict(conn.execute("SELECT * FROM test_trades WHERE id = ?",
                                 (tid_live,)).fetchone())
    assert live_row["outcome"] == "open"             # live book untouched


def test_shadow_cycle_never_writes_orders(conn, monkeypatch):
    conn.execute("INSERT OR REPLACE INTO candles(symbol, interval, open_time, open, "
                 "high, low, close, volume) VALUES ('BTC/USDT','1h',1,100,101,"
                 "99,100,1)")
    # Two distinct outlets mentioning Bitcoin → gate passes; SHORT proposal.
    database.save_gov_news(conn, [
        _item(source="Fed", title="Fed slashes rates, bitcoin rallies"),
        _item(source="BoE", title="BoE notes bitcoin market impact"),
    ])
    conn.commit()
    asset = {"asset": "BTC", "sentiment_score": -0.7, "confidence": 0.85,
             "summary": "Fed slashes rates", "headlines": []}
    _patch_scorer(monkeypatch, [asset])
    import test_ai
    with patch.object(test_ai, "_current_price",
                      lambda c, s, interval="1h": 100.0):
        gov_analyst.run_shadow_cycle(conn)
    n_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert n_orders == 0                     # paper-only, guaranteed
    n_shadow = conn.execute("SELECT COUNT(*) FROM test_trades WHERE "
                            "ai_model='groq-gov'").fetchone()[0]
    assert n_shadow == 1
