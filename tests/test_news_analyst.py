"""
cryptoautomation — tests for the Groq news analyst (news_analyst.py).

Pure/offline: the Groq HTTP call is monkeypatched, so no key or network is
needed. Covers JSON parsing (incl. Qwen <think> blocks), the structured
analytics shape, the news trade-signal thresholds and graceful failure.
"""

import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't print ✅/❌ — switch to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import news_analyst as na  # noqa: E402

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


# ── _parse_json ──────────────────────────────────────────────────────
def test_parse_json():
    print("1. JSON parsing (think blocks / fences / trailing commas)")
    qwen = "<think>\nlet me reason\n</think>\n{\"assets\":[{\"asset\":\"BTC\"}]}"
    ok("Qwen <think> block stripped", na._parse_json(qwen) == {"assets": [{"asset": "BTC"}]})
    fenced = '```json\n{"a": 1}\n```'
    ok("markdown fences stripped", na._parse_json(fenced) == {"a": 1})
    trailing = '{"a": 1, "b": [2, 3,],}'
    ok("trailing commas tolerated", na._parse_json(trailing) == {"a": 1, "b": [2, 3]})
    ok("garbage -> None", na._parse_json("not json at all") is None)
    ok("empty -> None", na._parse_json("") is None)
    # Models wrap JSON in a code fence WITH a prose lead-in — the fence
    # regex alone misses it; the {…}-extraction fallback must catch it.
    prose = ('Based on the headlines, my analysis:\n\n```\n{"a": 1}\n```')
    ok("prose-wrapped fence extracted", na._parse_json(prose) == {"a": 1})


# ── analyze_headlines (Groq call patched) ────────────────────────────
def test_analyze_headlines():
    print("2. analyze_headlines — structured payload from a patched Groq call")

    def fake_groq(prompt):
        return {"assets": [
            {"asset": "btc", "sentiment_score": 0.6, "confidence": 0.9,
             "impact_horizon": "short_term", "summary": "adoption story intact",
             "headlines": ["Grayscale: adoption intact"]},
            {"asset": "NOTACOIN", "sentiment_score": 1.0, "confidence": 1.0,
             "impact_horizon": "long_term", "summary": "irrelevant"},
            {"asset": "ETH", "sentiment_score": "0.2", "confidence": "0.4",
             "impact_horizon": "medium_term", "summary": "mild"},
        ]}

    orig = na._groq_call
    na._groq_call = fake_groq
    try:
        hd = [{"title": "headline 1", "tag": "bullish", "source": "CoinDesk",
               "published_at": "2026-08-17T12:00:00"}]
        r = na.analyze_headlines(hd)
    finally:
        na._groq_call = orig

    ok("non-None result", r is not None)
    ok("2 usable assets (irrelevant skipped in merge, kept here)",
       r and len(r["assets"]) == 3)
    ok("sentiment clamped to -1..1", r and abs(r["assets"][0]["sentiment_score"]) <= 1.0)
    ok("numeric coercion (string '0.2' -> 0.2)",
       r and r["assets"][2]["sentiment_score"] == 0.2)
    ok("confidence clamped to 0..1", r and 0 <= r["assets"][0]["confidence"] <= 1)
    ok("model recorded", r and r["model"] == na.config.GROQ_MODEL)


# ── signal_for thresholds ────────────────────────────────────────────
def test_signal_thresholds():
    print("3. signal_for — LONG / SHORT / HOLD from score+confidence thresholds")
    # The 6-layer gates (calendar / DXY / SMC / AI-confirm) are unit-tested
    # elsewhere — here we isolate the raw sentiment + confidence thresholds.
    # They are disabled (and fetch_headlines patched to a fresh feed) so the
    # test needs no DB and can't crash on conn=None.
    flag_keys = ("AI_SENTIMENT_LONG", "AI_SENTIMENT_SHORT", "AI_CONFIDENCE_FLOOR",
                 "CALENDAR_GUARD_ENABLED", "DXY_FILTER_ENABLED", "SMC_ENABLED",
                 "AI_CONFIRM_ENABLED")
    saved = {k: getattr(na.config, k) for k in flag_keys}
    orig_analytics = na.analytics_for
    orig_fetch = na.fetch_headlines
    try:
        # Pin the trade thresholds: live .env values (e.g. 0.05/0.10 when
        # over-filtering was relaxed) must not leak into this unit test —
        # these checks assert the threshold LOGIC, with a known baseline.
        na.config.AI_SENTIMENT_LONG = 0.50
        na.config.AI_SENTIMENT_SHORT = -0.50
        na.config.AI_CONFIDENCE_FLOOR = 0.70
        na.config.CALENDAR_GUARD_ENABLED = False
        na.config.DXY_FILTER_ENABLED = False
        na.config.SMC_ENABLED = False
        na.config.AI_CONFIRM_ENABLED = False
        na.fetch_headlines = lambda conn, limit=10: [{"age_hours": 0.5}] * 5

        na.analytics_for = lambda conn, sym: {"score": 60, "confidence": 0.7,
                                              "impact_horizon": "short_term",
                                              "summary": "bullish news", "_rule_based": False}
        s = na.signal_for(None, "BTC/USDT")
        ok("score 0.60 conf 0.70 -> LONG", s["action"] == "LONG", str(s))

        na.analytics_for = lambda conn, sym: {"score": -50, "confidence": 0.8,
                                              "impact_horizon": "short_term",
                                              "summary": "bearish news", "_rule_based": False}
        s = na.signal_for(None, "BTC/USDT")
        ok("score -0.50 conf 0.80 -> SHORT", s["action"] == "SHORT", str(s))

        na.analytics_for = lambda conn, sym: {"score": 10, "confidence": 0.7,
                                              "impact_horizon": None,
                                              "summary": "weak news", "_rule_based": False}
        s = na.signal_for(None, "BTC/USDT")
        ok("score 0.10 inside noise -> HOLD", s["action"] == "HOLD", str(s))

        # High score but low confidence -> HOLD (the confidence floor matters).
        na.analytics_for = lambda conn, sym: {"score": 60, "confidence": 0.3,
                                              "impact_horizon": None,
                                              "summary": "loud but unsure", "_rule_based": False}
        s = na.signal_for(None, "BTC/USDT")
        ok("score 0.60 conf 0.30 -> HOLD (confidence floor)", s["action"] == "HOLD", str(s))

        # Rule-based back-fill has no model confidence -> never a trade signal.
        na.analytics_for = lambda conn, sym: {"score": 60, "confidence": None,
                                              "impact_horizon": None,
                                              "summary": None, "_rule_based": True}
        s = na.signal_for(None, "BTC/USDT")
        ok("rule backfill 0.60 -> HOLD (no confidence)", s["action"] == "HOLD", str(s))

        na.analytics_for = lambda conn, sym: None
        s = na.signal_for(None, "BTC/USDT")
        ok("no analytics -> HOLD, never raises", s["action"] == "HOLD", str(s))
    finally:
        na.analytics_for = orig_analytics
        na.fetch_headlines = orig_fetch
        for k, v in saved.items():
            setattr(na.config, k, v)


# ── provider chain (Groq + OmniRoute) ───────────────────────────────
def test_provider_chain():
    print("3b. _providers() — Groq first, OmniRoute second, fallbacks after")
    groq_was = na.config.GROQ_API_KEY
    omni_was = (na.config.OMNIROUTE_ENABLED, na.config.OMNIROUTE_BASE_URL,
                na.config.OMNIROUTE_MODEL, na.config.OMNIROUTE_API_KEY)
    fb_was = na.config.LLM_FALLBACKS
    llm_was = (na.config.LLM_API_KEY, na.config.LLM_BASE_URL)
    try:
        na.config.GROQ_API_KEY = "gsk-test"
        na.config.OMNIROUTE_ENABLED = True
        na.config.OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
        na.config.OMNIROUTE_MODEL = "auto"
        na.config.OMNIROUTE_API_KEY = ""
        na.config.LLM_FALLBACKS = []
        na.config.LLM_API_KEY = ""
        prov = na._providers()
        ok("2 providers (groq + omniroute)", len(prov) == 2)
        ok("groq first", prov[0]["label"] == "groq")
        ok("omniroute second", prov[1]["label"] == "omniroute")
        ok("omniroute keyless (no auth header needed)", prov[1]["api_key"] == "")

        # LLM_FALLBACKS entries are appended after the local gateway.
        na.config.LLM_FALLBACKS = [{"base_url": "https://openrouter.ai/api/v1",
                                    "model": "meta-llama/llama-3.3-70b-instruct",
                                    "api_key": "sk-or-test"}]
        prov = na._providers()
        ok("fallbacks appended after omniroute", len(prov) == 3
           and prov[2]["label"] == "fallback"
           and prov[2]["models"] == ["meta-llama/llama-3.3-70b-instruct"])

        # No Groq key → OmniRoute first, fallbacks after.
        na.config.GROQ_API_KEY = ""
        prov = na._providers()
        ok("no groq key → omniroute first", prov[0]["label"] == "omniroute")

        # Keyed LLM_* provider (Gemini) rides second, right after Groq —
        # it answers the batched analysis fast, before the local gateway.
        na.config.GROQ_API_KEY = "gsk-test"
        na.config.LLM_API_KEY = "gem-test"
        prov = na._providers()
        ok("keyed LLM_* second after groq", prov[1]["label"] == "llm_primary")
        ok("omniroute third", prov[2]["label"] == "omniroute")
        ok("fallback last", prov[-1]["label"] == "fallback")

        # Everything disabled → empty chain (never a hard dependency).
        na.config.GROQ_API_KEY = ""
        na.config.LLM_FALLBACKS = []
        na.config.LLM_API_KEY = ""
        na.config.OMNIROUTE_ENABLED = False
        ok("all disabled → []", na._providers() == [])
    finally:
        na.config.GROQ_API_KEY = groq_was
        (na.config.OMNIROUTE_ENABLED, na.config.OMNIROUTE_BASE_URL,
         na.config.OMNIROUTE_MODEL, na.config.OMNIROUTE_API_KEY) = omni_was
        na.config.LLM_FALLBACKS = fb_was
        na.config.LLM_API_KEY, na.config.LLM_BASE_URL = llm_was

    print("3c. chat_json — routes to OmniRoute when Groq is unkeyed")
    omni_was = (na.config.OMNIROUTE_ENABLED, na.config.OMNIROUTE_BASE_URL,
                na.config.OMNIROUTE_MODEL, na.config.OMNIROUTE_API_KEY)
    groq_was = na.config.GROQ_API_KEY
    fb_was = na.config.LLM_FALLBACKS
    llm_was = (na.config.LLM_API_KEY, na.config.LLM_BASE_URL)
    seen = {"urls": [], "headers": []}
    orig = na.requests.post

    def fake_post(url, **kw):
        seen["urls"].append(url)
        seen["headers"].append(kw.get("headers") or {})

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": "{\"ok\": 1}"}}]}

        return R()

    try:
        na.config.GROQ_API_KEY = ""
        na.config.OMNIROUTE_ENABLED = True
        na.config.OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
        na.config.OMNIROUTE_MODEL = "auto"
        na.config.OMNIROUTE_API_KEY = ""
        na.config.LLM_FALLBACKS = []
        na.config.LLM_API_KEY = ""
        na.requests.post = fake_post
        data = na.chat_json("ping")
        ok("omniroute answered", data == {"ok": 1})
        ok("hit the omniroute chat endpoint",
           seen["urls"][0] == "http://localhost:20128/v1/chat/completions",
           f"got {seen['urls'][0]!r}")
        # Keyless local gateway → NO Authorization header.
        ok("keyless request carries no Authorization",
           "Authorization" not in seen["headers"][0])
    finally:
        na.requests.post = orig
        na.config.GROQ_API_KEY = groq_was
        (na.config.OMNIROUTE_ENABLED, na.config.OMNIROUTE_BASE_URL,
         na.config.OMNIROUTE_MODEL, na.config.OMNIROUTE_API_KEY) = omni_was
        na.config.LLM_FALLBACKS = fb_was
        na.config.LLM_API_KEY, na.config.LLM_BASE_URL = llm_was


# ── graceful failure ─────────────────────────────────────────────────
def test_failure_is_graceful():
    print("4. analyze_market — None (not an exception) when Groq is down")
    orig_groq = na._groq_call
    orig_cooldown = na._cooldown_until
    na._groq_call = lambda prompt: None
    try:
        na._cooldown_until = 0.0
        r = na.analyze_market(None, limit=5, force=True)
        ok("returns None on provider failure", r is None)
    finally:
        na._groq_call = orig_groq
        na._cooldown_until = orig_cooldown


def main():
    test_parse_json()
    test_analyze_headlines()
    test_signal_thresholds()
    test_provider_chain()
    test_failure_is_graceful()
    print(f"\n{'=' * 50}\n{'✅ ALL TESTS PASSED' if FAIL == 0 else f'❌ {FAIL} FAILED'}"
          f" — news analyst ({PASS} checks)")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
