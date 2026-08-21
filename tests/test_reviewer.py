"""
Reviewer tests — LLM overlay failover chain (no network, no real DB).

Run:
    python tests/test_reviewer.py
"""
import sys
from pathlib import Path

# Tests run from tests/; the backend modules live one level up in backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

# Windows consoles default to cp1252, which can't print the emoji used in
# this script's output — switch stdout to UTF-8 (best-effort) so the test
# output never crashes the console.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import traceback


class FakeResp:
    """Minimal requests.Response stand-in with the fields the reviewer reads."""

    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _headline_df():
    import pandas as pd
    return pd.DataFrame([{
        "sentiment": "bullish", "title": "Bitcoin ETF inflows surge",
        "source": "CoinDesk", "published_at": None,
    }])


def main():
    errors = []

    # 1. Config: LLM_FALLBACKS parsing
    print("1. Testing LLM_FALLBACKS config parsing...")
    try:
        import config
        import os

        os.environ["LLM_FALLBACKS"] = (
            "https://openrouter.ai/api/v1|meta-llama/llama-3.3-70b-instruct|sk-or-1;"
            "https://api.groq.com/openai/v1|llama-3.3-70b-versatile;"
            "||garbage-entry-with-no-model"
        )
        import importlib
        importlib.reload(config)
        assert len(config.LLM_FALLBACKS) == 2, f"expected 2 valid fallbacks, got {len(config.LLM_FALLBACKS)}"
        assert config.LLM_FALLBACKS[0] == {
            "base_url": "https://openrouter.ai/api/v1",
            "model": "meta-llama/llama-3.3-70b-instruct",
            "api_key": "sk-or-1",
        }
        assert config.LLM_FALLBACKS[1]["api_key"] == "", "keyless provider must parse with empty key"
        # clean up
        del os.environ["LLM_FALLBACKS"]
        importlib.reload(config)
        print(f"   ✅ Parsed {len(config.LLM_FALLBACKS)} fallback(s) after cleanup")
    except Exception as e:
        print(f"   ❌ Config parsing error: {e}")
        traceback.print_exc()
        errors.append(("config", e))

    # 2. Failover: primary down -> fallback answers
    print("\n2. Testing provider failover (primary down → fallback)...")
    try:
        import reviewer

        reviewer._llm_cooldown_until = 0.0  # reset any cooldown

        # Stub the DB so the reviewer sees headlines without touching SQLite.
        reviewer.database.load_news = lambda conn, limit: _headline_df()

        calls = []

        def fake_post(url, **kw):
            calls.append(url)
            if "openrouter" in url:
                raise IOError("rate limited")
            return FakeResp(
                '```json\n{"BTC": {"score": 30, "stance": "bullish", '
                '"reasons": "ETF inflows"}}\n```'
            )

        reviewer.requests.post = fake_post
        reviewer.config.LLM_BASE_URL = "https://openrouter.ai/api/v1"
        reviewer.config.LLM_MODEL = "meta-llama/llama-3.3-70b-instruct"
        reviewer.config.LLM_API_KEY = "sk-or-1"
        # Cloud providers require a key (keyless entries are skipped now).
        reviewer.config.LLM_FALLBACKS = [{
            "base_url": "https://api.groq.com/openai/v1",
            "model": "llama-3.3-70b-versatile",
            "api_key": "gsk-fake",
        }]

        result = reviewer._llm_review(None, limit=20)
        assert result is not None, "failover should produce a result"
        assert result["model"] == "llama-3.3-70b-versatile", \
            f"expected fallback model, got {result['model']!r}"
        assert result["mode"] == "llm"
        assert any(c["symbol"] == "BTC/USDT" for c in result["coins"])
        assert result.get("generated_at"), "LLM result should carry generated_at"
        assert len(calls) == 2, f"expected 2 provider calls, got {calls}"
        print(f"   ✅ Primary ({calls[0].split('/')[2]}) failed → fallback ({calls[1].split('/')[2]}) answered")
    except Exception as e:
        print(f"   ❌ Failover error: {e}")
        traceback.print_exc()
        errors.append(("failover", e))

    # 2b. _parse_llm_json handles prose-wrapped code fences (seen live:
    # models reply "Based on the headlines...\n\n```\n{...}\n```")
    print("\n2b. Testing prose-wrapped JSON parsing...")
    try:
        import reviewer
        prose = ('Based on recent headlines, here is my analysis:\n\n'
                 '```json\n{"BTC": {"score": 8, "stance": "bullish", '
                 '"reasons": "institutional adoption"}}\n```')
        parsed = reviewer._parse_llm_json(prose)
        assert parsed and parsed["BTC"]["score"] == 8, f"got {parsed!r}"
        assert parsed["BTC"]["stance"] == "bullish"
        # Garbage still yields None.
        assert reviewer._parse_llm_json("not json at all") is None
        print("   ✅ Prose-wrapped JSON extracted; garbage → None")
    except Exception as e:
        print(f"   ❌ Prose-wrapped parse error: {e}")
        traceback.print_exc()
        errors.append(("parse_llm_json", e))

    # 3. All providers fail -> None + cooldown; next call skips network
    print("\n3. Testing all-fail → cooldown...")
    try:
        import reviewer

        # Isolate: this section counts the EXACT cloud-provider chain
        # (primary + fallbacks), so keep the local OmniRoute gateway out.
        omni_was = reviewer.config.OMNIROUTE_ENABLED
        reviewer.config.OMNIROUTE_ENABLED = False
        try:
            reviewer._llm_cooldown_until = 0.0
            reviewer._llm_result_cache = {"at": 0.0, "data": None}
            reviewer.requests.post = lambda url, **kw: (_ for _ in ()).throw(
                IOError("down"))

            n_calls = {"n": 0}
            orig_post = reviewer.requests.post

            def counting_post(url, **kw):
                n_calls["n"] += 1
                return orig_post(url, **kw)

            reviewer.requests.post = counting_post

            assert reviewer._llm_review(None, limit=20) is None, "all-fail must return None"
            assert reviewer._llm_cooldown_until > 0, "cooldown must be armed after total failure"
            assert n_calls["n"] == 2, f"both providers should be tried, got {n_calls['n']}"

            # Immediately after, the cooldown must short-circuit (no network).
            before = n_calls["n"]
            assert reviewer._llm_review(None, limit=20) is None
            assert n_calls["n"] == before, "cooldown must skip provider calls"
        finally:
            reviewer.config.OMNIROUTE_ENABLED = omni_was
        print("   ✅ Total failure → None, cooldown armed, retries suppressed")
    except Exception as e:
        print(f"   ❌ Cooldown error: {e}")
        traceback.print_exc()
        errors.append(("cooldown", e))

    # 4. review_market falls back to rule-based when LLM is off
    print("\n4. Testing review_market → rule-based when LLM disabled...")
    try:
        import reviewer

        reviewer.config.LLM_ENABLED = False
        reviewer.config.LLM_FALLBACKS = []
        reviewer._llm_cooldown_until = 0.0

        class FakeConn:
            pass

        reviewer.database.load_news = lambda conn, limit: _headline_df()

        out = reviewer.review_market(FakeConn(), limit=40)
        assert out["mode"] == "rule", f"expected rule mode, got {out['mode']!r}"
        assert out.get("generated_at"), "rule result should carry generated_at"
        print("   ✅ Rule-based fallback intact (mode='rule')")
    except Exception as e:
        print(f"   ❌ Rule fallback error: {e}")
        traceback.print_exc()
        errors.append(("rule", e))

    # 5. News verification gate (verify_trade) — the confirmation AI
    print("\n5. Testing verify_trade (news confirmation / veto gate)...")
    try:
        import reviewer

        def fake_review(coins):
            def _rm(conn, limit=40):
                return {"coins": coins, "mode": "rule", "model": None}
            return _rm

        row = lambda sym, score, stance, stories=1: {   # noqa: E731
            "symbol": sym, "score": score, "stance": stance,
            "reasons": [{"title": f"story {sym} {score}"}], "stories": stories}

        # LONG + strongly bearish headlines → VETO
        reviewer.review_market = fake_review([row("BTC/USDT", -40, "bearish")])
        v = reviewer.verify_trade(None, "BTC/USDT", "LONG")
        assert v["verdict"] == "veto", v
        assert "bearish" in v["message"], v

        # SHORT + strongly bullish headlines → VETO
        reviewer.review_market = fake_review([row("ETH/USDT", 45, "bullish")])
        v = reviewer.verify_trade(None, "ETH/USDT", "SHORT")
        assert v["verdict"] == "veto", v
        assert "bullish" in v["message"], v

        # Extreme score in EITHER direction (big move expected) → VETO, even
        # when it would otherwise align with the trade.
        reviewer.review_market = fake_review([row("SOL/USDT", 60, "bullish")])
        v = reviewer.verify_trade(None, "SOL/USDT", "LONG")
        assert v["verdict"] == "veto" and v["big_move"] is True, v
        assert "big move expected" in v["message"], v
        reviewer.review_market = fake_review([row("SOL/USDT", -60, "bearish")])
        v = reviewer.verify_trade(None, "SOL/USDT", "SHORT")
        assert v["verdict"] == "veto" and v["big_move"] is True, v

        # Aligned headlines → CONFIRM (supports the direction)
        reviewer.review_market = fake_review([row("ADA/USDT", 25, "bullish")])
        v = reviewer.verify_trade(None, "ADA/USDT", "LONG")
        assert v["verdict"] == "confirm", v
        reviewer.review_market = fake_review([row("ADA/USDT", -25, "bearish")])
        v = reviewer.verify_trade(None, "ADA/USDT", "SHORT")
        assert v["verdict"] == "confirm", v

        # Weak / no news → NEUTRAL, never blocks a trade
        reviewer.review_market = fake_review([row("LTC/USDT", 5, "neutral")])
        v = reviewer.verify_trade(None, "LTC/USDT", "LONG")
        assert v["verdict"] == "neutral", v
        reviewer.review_market = fake_review([])
        v = reviewer.verify_trade(None, "DOT/USDT", "SHORT")
        assert v["verdict"] == "neutral" and v["score"] == 0, v
        assert "no news coverage" in v["message"], v
        print("   ✅ Veto (opposing) · veto (big move) · confirm · neutral all correct")
    except Exception as e:
        print(f"   ❌ verify_trade error: {e}")
        traceback.print_exc()
        errors.append(("verify_trade", e))

    # 6. LLM overlay cadence throttle + keyless-cloud filtering (free tiers)
    print("\n6. Testing LLM cadence throttle + keyless provider filter...")
    try:
        import reviewer

        # Isolate the cloud chain: keep the local OmniRoute gateway out of
        # the count (its own enable/disable behavior is covered in §6b).
        omni_was = reviewer.config.OMNIROUTE_ENABLED
        reviewer.config.OMNIROUTE_ENABLED = False
        try:
            # Keyless cloud providers must be skipped (would 401 every cycle).
            reviewer.config.LLM_BASE_URL = "https://api.openai.com/v1"
            reviewer.config.LLM_MODEL = "gpt-4o-mini"
            reviewer.config.LLM_API_KEY = ""
            reviewer.config.LLM_FALLBACKS = [{"base_url": "https://api.groq.com/openai/v1",
                                              "model": "llama-3.3-70b-versatile",
                                              "api_key": ""}]
            provs = reviewer._llm_providers()
            assert provs == [], f"keyless cloud providers must be skipped, got {provs}"
            reviewer.config.LLM_FALLBACKS = [{"base_url": "http://localhost:11434/v1",
                                              "model": "local-model", "api_key": ""}]
            provs = reviewer._llm_providers()
            assert len(provs) == 1, "localhost keyless providers must be allowed"

            # Free-tier throttle: the second call inside the window must reuse
            # the cached verdict without any network call.
            reviewer._llm_cooldown_until = 0.0
            reviewer._llm_result_cache = {"at": 0.0, "data": None}
            reviewer.config.LLM_MIN_INTERVAL_SECONDS = 600
            reviewer.config.LLM_BASE_URL = "https://api.groq.com/openai/v1"
            reviewer.config.LLM_MODEL = "llama-3.3-70b-versatile"
            reviewer.config.LLM_API_KEY = "gsk-fake"
            reviewer.config.LLM_FALLBACKS = []
            reviewer.database.load_news = lambda conn, limit: _headline_df()
            n = {"calls": 0}
            orig_post = reviewer.requests.post

            def counting_post(url, **kw):
                n["calls"] += 1
                return FakeResp('{"BTC": {"score": 30, "stance": "bullish", '
                                '"reasons": "ETF inflows"}}')

            reviewer.requests.post = counting_post
            try:
                r1 = reviewer._llm_review(None, limit=20)
                assert r1 and r1["mode"] == "llm", r1
                r2 = reviewer._llm_review(None, limit=20)  # inside the 600s window
                assert n["calls"] == 1, f"cache must serve 2nd call, got {n['calls']}"
                assert r2 == r1, "cached verdict must equal the first"
                reviewer._llm_result_cache["at"] = 0.0  # expire the window
                r3 = reviewer._llm_review(None, limit=20)
                assert n["calls"] == 2, "expired cache must re-ask the provider"
            finally:
                reviewer.requests.post = orig_post
        finally:
            reviewer.config.OMNIROUTE_ENABLED = omni_was
        print("   ✅ Keyless clouds skipped; cached verdict served for 600s (free-tier safe)")
    except Exception as e:
        print(f"   ❌ Throttle error: {e}")
        traceback.print_exc()
        errors.append(("throttle", e))

    # 6b. OmniRoute — keyless LOCAL provider joins the chain when enabled
    print("\n6b. Testing OmniRoute in the provider chain...")
    try:
        import reviewer

        omni_was = reviewer.config.OMNIROUTE_ENABLED
        base_was = reviewer.config.OMNIROUTE_BASE_URL
        model_was = reviewer.config.OMNIROUTE_MODEL
        key_was = reviewer.config.OMNIROUTE_API_KEY
        try:
            reviewer.config.OMNIROUTE_ENABLED = True
            reviewer.config.OMNIROUTE_BASE_URL = "http://localhost:20128/v1"
            reviewer.config.OMNIROUTE_MODEL = "auto"
            reviewer.config.OMNIROUTE_API_KEY = ""
            # Keyless clouds are still skipped; only local (OmniRoute) remains.
            reviewer.config.LLM_BASE_URL = "https://api.openai.com/v1"
            reviewer.config.LLM_MODEL = "gpt-4o-mini"
            reviewer.config.LLM_API_KEY = ""
            reviewer.config.LLM_FALLBACKS = [{"base_url": "https://api.groq.com/openai/v1",
                                              "model": "llama-3.3-70b-versatile",
                                              "api_key": ""}]
            provs = reviewer._llm_providers()
            assert len(provs) == 1 and provs[0]["base_url"] == "http://localhost:20128/v1", \
                f"OmniRoute must be the keyless local provider, got {provs}"
            assert provs[0]["model"] == "auto"
            # Disabled → gone from the chain.
            reviewer.config.OMNIROUTE_ENABLED = False
            assert reviewer._llm_providers() == [], \
                "disabled OmniRoute must not appear"
        finally:
            reviewer.config.OMNIROUTE_ENABLED = omni_was
            reviewer.config.OMNIROUTE_BASE_URL = base_was
            reviewer.config.OMNIROUTE_MODEL = model_was
            reviewer.config.OMNIROUTE_API_KEY = key_was
        print("   ✅ OmniRoute in the chain when enabled; gone when disabled")
    except Exception as e:
        print(f"   ❌ OmniRoute chain error: {e}")
        traceback.print_exc()
        errors.append(("omniroute_chain", e))

    # 7. verdict_for — pure helper shared by the bot gate + signal cards
    print("\n7. Testing verdict_for (pure verdict helper)...")
    try:
        import reviewer
        assert reviewer.verdict_for(-40, "LONG")["verdict"] == "veto"
        assert reviewer.verdict_for(40, "SHORT")["verdict"] == "veto"
        assert reviewer.verdict_for(60, "LONG")["verdict"] == "veto"
        assert reviewer.verdict_for(60, "LONG")["big_move"] is True
        assert reviewer.verdict_for(25, "LONG")["verdict"] == "confirm"
        assert reviewer.verdict_for(-25, "SHORT")["verdict"] == "confirm"
        assert reviewer.verdict_for(3, "LONG")["verdict"] == "neutral"
        assert reviewer.verdict_for(0, "HOLD")["verdict"] == "neutral"
        print("   ✅ verdict_for: veto/confirm/neutral/big-move all correct")
    except Exception as e:
        print(f"   ❌ verdict_for error: {e}")
        traceback.print_exc()
        errors.append(("verdict_for", e))

    # Summary
    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} error(s) found:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    print("✅ ALL TESTS PASSED — reviewer failover chain working")


if __name__ == "__main__":
    main()
