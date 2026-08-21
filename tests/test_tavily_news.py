"""
cryptoautomation — tests for the Tavily news search fetcher (news.py).

Pure/offline: the HTTP call is monkeypatched, so no key or network is
needed. Covers the result → news-row mapping (sentiment via the keyword
scorer, whole-word ticker detection, published date), the free-tier
throttle, and the disabled state.
"""

import sys
import time
from pathlib import Path

# Windows consoles default to cp1252, which can't print ✅/❌ — switch to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config  # noqa: E402
import news as news_mod  # noqa: E402

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


def _sample_results():
    """One result per edge case: strong bull, strong bear, neutral, an
    irrelevant story (no watched coin → symbols empty)."""
    return {"results": [
        {"title": "Bitcoin ETF inflows surge to record",
         "url": "https://example.com/1", "source": "CoinDesk",
         "published_date": "2026-08-17T06:32:11Z"},
        {"title": "Ethereum crashes below key support",
         "url": "https://example.com/2", "source": "Cointelegraph",
         "published_date": "2026-08-17T05:41:00Z"},
        {"title": "Mixed week for markets",
         "url": "https://example.com/3", "source": "Reuters",
         "published_date": "2026-08-17T04:00:00Z"},
        {"title": "New software update ships today",
         "url": "https://example.com/4", "source": "TechCrunch",
         "published_date": "2026-08-17T03:00:00Z"},
    ]}


def _patch_post(payload):
    orig = news_mod.requests.post
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return R()

    news_mod.requests.post = fake_post
    return orig, calls


def test_mapping():
    print("1. Tavily result → news-row mapping")
    orig, calls = _patch_post(_sample_results())
    news_mod._tavily_last_fetch = 0.0  # no throttle
    try:
        rows = news_mod.fetch_tavily_news(None)
    finally:
        news_mod.requests.post = orig
    ok("one HTTP call", calls["n"] == 1)
    ok("4 rows (all results kept)", len(rows) == 4)

    by_title = {r["title"]: r for r in rows}
    btc = by_title["Bitcoin ETF inflows surge to record"]
    ok("bullish via keyword scorer", btc["sentiment"] == "bullish")
    ok("BTC detected whole-word", btc["symbols"] == "BTC", f"got {btc['symbols']!r}")

    eth = by_title["Ethereum crashes below key support"]
    ok("bearish via keyword scorer", eth["sentiment"] == "bearish")
    ok("negative score", eth["sentiment_score"] < 0)
    ok("ETH detected (whole-word 'ethereum' base)", eth["symbols"] == "ETH",
       f"got {eth['symbols']!r}")

    neut = by_title["Mixed week for markets"]
    ok("neutral sentiment", neut["sentiment"] == "neutral")

    # 'Software' title must NOT light up on SOL (whole-word rule).
    soft = by_title["New software update ships today"]
    ok("no false SOL match on 'software'", soft["symbols"] == "",
       f"got {soft['symbols']!r}")
    ok("published date kept", btc["published_at"].startswith("2026-08-17T06:32:11"))


def test_throttle_and_off():
    print("2. Free-tier throttle + disabled state")
    orig, calls = _patch_post(_sample_results())
    news_mod._tavily_last_fetch = time.time()  # fetched just now
    try:
        rows = news_mod.fetch_tavily_news(None)
    finally:
        news_mod.requests.post = orig
    ok("within window → no HTTP call", calls["n"] == 0 and rows == [])

    was = config.TAVILY_NEWS_ENABLED
    config.TAVILY_NEWS_ENABLED = False
    news_mod._tavily_last_fetch = 0.0
    try:
        rows = news_mod.fetch_tavily_news(None)
    finally:
        config.TAVILY_NEWS_ENABLED = was
    ok("disabled → [] without calling", rows == [])

    news_mod._tavily_last_fetch = 0.0
    orig = news_mod.requests.post

    def boom(url, **kw):
        raise IOError("down")

    news_mod.requests.post = boom
    try:
        ok("API down → [] (never raises)",
           news_mod.fetch_tavily_news(None) == [])
    finally:
        news_mod.requests.post = orig


def main():
    for fn in (test_mapping, test_throttle_and_off):
        fn()
    print(f"\n{'-' * 40}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
