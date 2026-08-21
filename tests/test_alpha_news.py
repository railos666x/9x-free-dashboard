"""
cryptoautomation — tests for the Alpha Vantage news fetcher (news.py).

Pure/offline: the HTTP call is monkeypatched, so no key or network is
needed. Covers the feed-item → news-row mapping (labels, relevance floor,
symbols, timestamp conversion), the free-tier throttle, and the disabled
state.
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


def _sample_feed():
    """One feed item per edge case: strong bull, strong bear, neutral,
    low-relevance noise ticker, equities-only (skipped)."""
    def item(title, ts, label, score, tickers):
        return {"title": title, "time_published": ts, "source": "Benzinga",
                "url": "https://example.com/" + title[:20],
                "overall_sentiment_label": label,
                "overall_sentiment_score": score,
                "ticker_sentiment": [
                    {"ticker": t, "ticker_sentiment_score": s,
                     "ticker_sentiment_label": l, "relevance_score": rel}
                    for t, s, l, rel in tickers]}

    return {"feed": [
        item("Bitcoin ETF inflows surge",
             "20260817T063211", "Bullish", 0.61,
             [("CRYPTO:BTC", "0.62", "Bullish", "0.85"),
              ("CRYPTO:ETH", "0.10", "Neutral", "0.05")]),  # ETH: low relevance
        item("Solana crashes below support",
             "20260817T054100", "Bearish", -0.52,
             [("CRYPTO:SOL", "-0.55", "Bearish", "0.78"),
              ("CRYPTO:BTC", "-0.10", "Neutral", "0.04")]),
        item("Mixed week for markets",
             "20260817T040000", "Neutral", 0.0,
             [("CRYPTO:ETH", "0.02", "Neutral", "0.3"),
              ("CRYPTO:BTC", "-0.01", "Neutral", "0.28")]),
        item("Only equities here", "20260817T030000", "Bullish", 0.4,
             [("AMT", "0.2", "Bullish", "0.9")]),  # no CRYPTO: ticker → skipped
    ]}


def _patch_get(feed):
    orig = news_mod.requests.get
    calls = {"n": 0}

    def fake_get(url, **kw):
        calls["n"] += 1
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return feed

        return R()

    news_mod.requests.get = fake_get
    return orig, calls


def test_mapping():
    print("1. Feed → news-row mapping")
    orig, calls = _patch_get(_sample_feed())
    news_mod._alpha_last_fetch = 0.0  # no throttle
    try:
        rows = news_mod.fetch_alpha_news(None)
    finally:
        news_mod.requests.get = orig
    ok("one HTTP call", calls["n"] == 1)
    ok("3 crypto rows (equities-only skipped)", len(rows) == 3)

    by_title = {r["title"]: r for r in rows}
    btc = by_title["Bitcoin ETF inflows surge"]
    ok("bullish label", btc["sentiment"] == "bullish")
    ok("score from best-relevance ticker (0.62)", btc["sentiment_score"] == 0.62)
    ok("low-relevance ETH excluded from symbols",
       btc["symbols"] == "BTC", f"got {btc['symbols']!r}")

    sol = by_title["Solana crashes below support"]
    ok("bearish label", sol["sentiment"] == "bearish")
    ok("negative score kept", sol["sentiment_score"] < 0)
    ok("SOL + BTC both listed (relevance >= 0.1) → SOL only",
       sol["symbols"] == "SOL", f"got {sol['symbols']!r}")

    ok("timestamp converted to ISO",
       btc["published_at"].startswith("2026-08-17T06:32:11"))
    ok("score clamped to [-1, 1]", -1.0 <= sol["sentiment_score"] <= 1.0)


def test_label_from_score():
    print("2. Label from score (API scale) when label missing")
    ok(">= 0.15 → bullish", news_mod._alpha_label(0.3, "") == "bullish")
    ok("<= -0.15 → bearish", news_mod._alpha_label(-0.3, "") == "bearish")
    ok("mid → neutral", news_mod._alpha_label(0.0, "") == "neutral")
    ok("explicit label wins over score",
       news_mod._alpha_label(0.0, "Bearish") == "bearish")
    ok("Somewhat-Bullish → bullish",
       news_mod._alpha_label(0.2, "Somewhat-Bullish") == "bullish")
    ok("Somewhat_Bearish underscore tolerated",
       news_mod._alpha_label(0.2, "Somewhat_Bearish") == "bearish")


def test_throttle_and_off():
    print("3. Free-tier throttle + disabled state")
    orig, calls = _patch_get(_sample_feed())
    news_mod._alpha_last_fetch = time.time()  # fetched just now
    try:
        rows = news_mod.fetch_alpha_news(None)
    finally:
        news_mod.requests.get = orig
    ok("within window → no HTTP call", calls["n"] == 0 and rows == [])

    # Disabled → no call even past the window.
    was = config.ALPHA_NEWS_ENABLED
    config.ALPHA_NEWS_ENABLED = False
    news_mod._alpha_last_fetch = 0.0
    try:
        rows = news_mod.fetch_alpha_news(None)
    finally:
        config.ALPHA_NEWS_ENABLED = was
    ok("disabled → [] without calling", rows == [])

    # API failure → [] and never raises.
    news_mod._alpha_last_fetch = 0.0
    orig = news_mod.requests.get
    def boom(url, **kw):
        raise IOError("down")
    news_mod.requests.get = boom
    try:
        ok("API down → [] (never raises)", news_mod.fetch_alpha_news(None) == [])
    finally:
        news_mod.requests.get = orig


def main():
    for fn in (test_mapping, test_label_from_score, test_throttle_and_off):
        fn()
    print(f"\n{'-' * 40}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
