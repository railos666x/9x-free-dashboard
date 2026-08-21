"""
cryptoautomation — tests for the three AI agents (ai_agents.py).

Pure/offline: the Groq transport (news_analyst.chat_json) is monkeypatched,
so no key or network is needed. Covers schema validation per agent, the
support/resistance detector on synthetic candles, the confluence gate
(off / valid / invalid / failure), and the journal round-trip.
"""

import sys
import sqlite3
from pathlib import Path

# Windows consoles default to cp1252, which can't print ✅/❌ — switch to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config  # noqa: E402
import ai_agents  # noqa: E402
import news_analyst  # noqa: E402

FAIL = 0
PASS = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def _mem_db():
    """An in-memory SQLite DB with the candles + ai_analyses tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE candles (
            symbol TEXT, interval TEXT, open_time INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            close_time INTEGER,
            PRIMARY KEY (symbol, interval, open_time));
        CREATE TABLE ai_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT, symbol TEXT, request TEXT, verdict TEXT,
            payload TEXT, model TEXT, created_at TEXT);
    """)
    return conn


def _insert_candles(conn, symbol="TEST/USDT", interval="1h", n=60,
                    high=105.0, low=95.0, close=100.0,
                    pivot_high_at=None, pivot_high=0.0,
                    pivot_low_at=None, pivot_low=0.0):
    """Synthetic 1h candles ending at `close` (open_time = 1h steps)."""
    import time as _t
    base = int(_t.time() * 1000)
    for i in range(n):
        h, l = high, low
        if pivot_high_at is not None and i == pivot_high_at:
            h = pivot_high
        if pivot_low_at is not None and i == pivot_low_at:
            l = pivot_low
        conn.execute(
            """INSERT INTO candles (symbol, interval, open_time, open, high,
               low, close, volume, close_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (symbol, interval, base + i * 3600_000, close, h, l, close,
             1000.0, base + i * 3600_000 + 3600_000))
    conn.commit()


def _patch_groq(data):
    """Route chat_json to a fixture. Returns the original to restore."""
    orig = news_analyst.chat_json

    def fake(user_prompt, system=None, max_tokens=1500, temperature=0.1):
        if callable(data):
            return data(user_prompt, system)
        return data

    news_analyst.chat_json = fake
    return orig


# ── Agent 1 — news sentiment ─────────────────────────────────────────
def test_agent1_news_sentiment():
    print("1. Agent 1 — News & Catalyst Sentiment")
    orig = _patch_groq({"sentiment": "BULLISH", "impact_score": 0.85,
                        "is_black_swan": False, "expected_duration": "MULTI_DAY",
                        "core_catalyst": "ETF filing implies institutional demand"})
    try:
        r = ai_agents.agent_news_sentiment("BlackRock files for a spot ETH ETF")
    finally:
        news_analyst.chat_json = orig
    ok("BULLISH parsed", r and r["sentiment"] == "BULLISH")
    ok("impact_score parsed", r and r["impact_score"] == 0.85)
    ok("black swan parsed", r and r["is_black_swan"] is False)
    ok("duration parsed", r and r["expected_duration"] == "MULTI_DAY")
    ok("catalyst parsed", r and "ETF" in r["core_catalyst"])

    # Schema violation (sentiment not in enum) → None, not garbage.
    orig = _patch_groq({"sentiment": "MOONISH", "impact_score": 1.0})
    try:
        bad = ai_agents.agent_news_sentiment("Anything")
    finally:
        news_analyst.chat_json = orig
    ok("schema violation -> None", bad is None)

    ok("empty headline -> None",
       ai_agents.agent_news_sentiment("   ") is None)

    # Off switch → None.
    was = config.AI_AGENTS_ENABLED
    config.AI_AGENTS_ENABLED = False
    try:
        ok("disabled -> None",
           ai_agents.agent_news_sentiment("Headline") is None)
    finally:
        config.AI_AGENTS_ENABLED = was


# ── Agent 2 — hold research ──────────────────────────────────────────
def test_agent2_hold_research():
    print("2. Agent 2 — Deep Research & HODL Portfolio")
    orig = _patch_groq({"macro_tailwinds": "rate cuts + adoption",
                        "risk_falsification": "SEC rejection",
                        "hold_recommendation": "ACCUMULATE_AND_HOLD",
                        "confidence_percentage": 80})
    try:
        r = ai_agents.agent_hold_research("ETH/USDT", "ETF filing")
    finally:
        news_analyst.chat_json = orig
    ok("recommendation parsed", r and r["hold_recommendation"] == "ACCUMULATE_AND_HOLD")
    ok("confidence parsed", r and r["confidence_percentage"] == 80)
    ok("tailwinds parsed", r and "rate cuts" in r["macro_tailwinds"])
    ok("falsification parsed", r and "SEC" in r["risk_falsification"])

    # Unknown recommendation is coerced to TRADE_ONLY, never trusted raw.
    orig = _patch_groq({"hold_recommendation": "YOLO", "confidence_percentage": 500})
    try:
        bad = ai_agents.agent_hold_research("BTC/USDT")
    finally:
        news_analyst.chat_json = orig
    ok("unknown rec coerced", bad and bad["hold_recommendation"] == "TRADE_ONLY")
    ok("confidence clamped to 100", bad and bad["confidence_percentage"] == 100)

    ok("empty symbol -> None", ai_agents.agent_hold_research("") is None)


# ── Agent 3 — chart confluence ───────────────────────────────────────
def test_agent3_chart_confluence():
    print("3. Agent 3 — Price Action & Chart Confluence")
    conn = _mem_db()
    _insert_candles(conn, close=100.0, pivot_high_at=10, pivot_high=120.0,
                    pivot_low_at=20, pivot_low=80.0)

    orig = _patch_groq({"is_valid_setup": True, "rejection_reason": "",
                        "entry_strategy": "WAIT_FOR_RETEST",
                        "calculated_stop_loss": 95.5})
    try:
        r = ai_agents.agent_chart_confluence(conn, "TEST/USDT", "LONG", force=True)
    finally:
        news_analyst.chat_json = orig
    ok("valid setup parsed", r and r["is_valid_setup"] is True)
    ok("entry strategy parsed", r and r["entry_strategy"] == "WAIT_FOR_RETEST")
    ok("stop parsed", r and r["calculated_stop_loss"] == 95.5)
    ok("S/R fed to the model", r and r["support"] == 80.0 and r["resistance"] == 120.0)
    ok("price fed", r and r["price"] == 100.0)

    # TRAP: model rejects a bullish signal below support.
    orig = _patch_groq({"is_valid_setup": False,
                        "rejection_reason": "DIVERGENCE / TRAP",
                        "entry_strategy": "ABORT", "calculated_stop_loss": 0.0})
    try:
        r = ai_agents.agent_chart_confluence(conn, "TEST/USDT", "LONG", force=True)
    finally:
        news_analyst.chat_json = orig
    ok("trap flagged", r and r["is_valid_setup"] is False)
    ok("rejection reason kept", r and "TRAP" in r["rejection_reason"])

    # No candles → None (never raises). Clear the cache first so the
    # earlier force=True calls can't serve a stale fixture.
    empty = _mem_db()
    ai_agents._confluence_cache.clear()
    ok("no candles -> None",
       ai_agents.agent_chart_confluence(empty, "TEST/USDT", "LONG") is None)

    # Cache: a second call (same TTL window) doesn't re-ask the model.
    calls = {"n": 0}
    orig = _patch_groq(lambda u, s: (calls.__setitem__("n", calls["n"] + 1) or
                                     {"is_valid_setup": True, "rejection_reason": "",
                                      "entry_strategy": "MARKET_BUY",
                                      "calculated_stop_loss": 90.0}))
    ai_agents._confluence_cache.clear()
    try:
        ai_agents.agent_chart_confluence(conn, "TEST/USDT", "LONG")
        ai_agents.agent_chart_confluence(conn, "TEST/USDT", "LONG")
    finally:
        news_analyst.chat_json = orig
    ok("cached — one Groq call for two asks", calls["n"] == 1)


# ── S/R detector ─────────────────────────────────────────────────────
def test_support_resistance():
    print("4. Support/resistance detection on synthetic candles")
    conn = _mem_db()
    _insert_candles(conn, close=100.0, pivot_high_at=10, pivot_high=120.0,
                    pivot_low_at=20, pivot_low=80.0)
    support, resistance = ai_agents.detect_support_resistance(conn, "TEST/USDT")
    ok("pivot support found", support == 80.0)
    ok("pivot resistance found", resistance == 120.0)

    # No pivots → fallback to the window extremes (never None with data).
    flat = _mem_db()
    _insert_candles(flat, close=100.0)
    s2, r2 = ai_agents.detect_support_resistance(flat, "TEST/USDT")
    ok("fallback not None", s2 is not None and r2 is not None)

    ok("no data -> (None, None)",
       ai_agents.detect_support_resistance(_mem_db(), "TEST/USDT") == (None, None))


# ── Confluence gate ──────────────────────────────────────────────────
def test_gate():
    print("5. Confluence gate (news source-B verification)")
    conn = _mem_db()
    _insert_candles(conn, close=100.0)

    # Off by default → pass, never blocks.
    was_enabled, was_gate, was_key = (config.AI_AGENTS_ENABLED,
                                      config.AI_CONFLUENCE_GATE,
                                      config.GROQ_API_KEY)
    config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE = True, False
    config.GROQ_API_KEY = "test"
    try:
        r = ai_agents.gate_news_trade(conn, "TEST/USDT", "LONG")
        ok("gate off -> pass", r["pass"] is True)
        ok("gate off reason", "gate off" in r["reason"])
    finally:
        config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE = was_enabled, was_gate
        config.GROQ_API_KEY = was_key

    # On + valid setup → pass with the agent verdict attached.
    config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE, config.GROQ_API_KEY = True, True, "test"
    orig = _patch_groq({"is_valid_setup": True, "rejection_reason": "",
                        "entry_strategy": "MARKET_BUY", "calculated_stop_loss": 90.0})
    try:
        r = ai_agents.gate_news_trade(conn, "TEST/USDT", "LONG")
    finally:
        news_analyst.chat_json = orig
        config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE = was_enabled, was_gate
        config.GROQ_API_KEY = was_key
    ok("gate on + valid -> pass", r["pass"] is True)
    ok("verdict attached", r["verdict"] and r["verdict"]["is_valid_setup"] is True)

    # On + TRAP → block, reason = the model's rejection. Clear the cache
    # so the valid-setup call above can't leak into this scenario.
    ai_agents._confluence_cache.clear()
    config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE, config.GROQ_API_KEY = True, True, "test"
    orig = _patch_groq({"is_valid_setup": False,
                        "rejection_reason": "DIVERGENCE / TRAP",
                        "entry_strategy": "ABORT", "calculated_stop_loss": 0.0})
    try:
        r = ai_agents.gate_news_trade(conn, "TEST/USDT", "LONG")
    finally:
        news_analyst.chat_json = orig
        config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE = was_enabled, was_gate
        config.GROQ_API_KEY = was_key
    ok("gate on + trap -> block", r["pass"] is False)
    ok("rejection reason surfaced", "TRAP" in r["reason"])

    # Model failure (None) → pass (the gate never blocks what it can't run).
    ai_agents._confluence_cache.clear()
    config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE, config.GROQ_API_KEY = True, True, "test"
    orig = _patch_groq(None)
    try:
        r = ai_agents.gate_news_trade(conn, "TEST/USDT", "LONG")
    finally:
        news_analyst.chat_json = orig
        config.AI_AGENTS_ENABLED, config.AI_CONFLUENCE_GATE = was_enabled, was_gate
        config.GROQ_API_KEY = was_key
    ok("gate failure -> pass (never blocks)", r["pass"] is True)


# ── Journal ──────────────────────────────────────────────────────────
def test_journal():
    print("6. ai_analyses journal round-trip")
    import database as _db
    conn = _mem_db()
    ai_agents._journal(conn, "news_sentiment", "BTC/USDT",
                       "Headline", "BULLISH", {"sentiment": "BULLISH"})
    _db.record_ai_analysis(conn, "hold_research", "ETH/USDT", "catalyst",
                           "TRADE_ONLY", {"symbol": "ETH/USDT"})
    rows = _db.load_ai_analyses(conn, limit=10)
    ok("journal rows returned", len(rows) >= 2)
    ok("payload JSON parsed back", isinstance(rows[0]["payload"], dict))
    ok("verdict stored", rows[0]["verdict"] in ("BULLISH", "TRADE_ONLY"))
    # Unparseable payload degrades to raw text, never raises.
    conn.execute("INSERT INTO ai_analyses (agent, symbol, request, verdict, "
                 "payload, model, created_at) VALUES ('x','y','z','w','{broken','m','t')")
    conn.commit()
    ok("broken payload tolerated", _db.load_ai_analyses(conn, limit=10) is not None)


def main():
    for fn in (test_agent1_news_sentiment, test_agent2_hold_research,
               test_agent3_chart_confluence, test_support_resistance,
               test_gate, test_journal):
        fn()
    print(f"\n{'-' * 40}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
