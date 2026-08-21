"""
cryptoautomation — tests for the Firecrawl web scraper (web_scraper.py).

Pure/offline: the Firecrawl HTTP call is monkeypatched, so no key or
network is needed. Covers the scrape mapping + boilerplate strip, retry on
transient errors, the DB content cache round-trip, the disabled state, and
best-effort enrichment.
"""

import sqlite3
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't print ✅/❌ — switch to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config  # noqa: E402
import web_scraper  # noqa: E402

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
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE article_content (
            url_hash TEXT PRIMARY KEY, url TEXT, title TEXT,
            content TEXT, fetched_at TEXT);
        CREATE TABLE news (
            id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
            published_at TEXT, sentiment TEXT, sentiment_score REAL,
            symbols TEXT, fetched_at TEXT);
    """)
    return conn


def _patch_post(responses):
    """responses: list of (status, body) consumed in order; last repeats."""
    orig = web_scraper.requests.post
    calls = {"n": 0}

    def fake_post(url, **kw):
        i = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        status, body = responses[i]

        class R:
            status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise IOError(f"http {status}")

            def json(self):
                return body

        return R()

    web_scraper.requests.post = fake_post
    return orig, calls


def _ok_body(md):
    return {"success": True, "data": {"markdown": md, "metadata": {"title": "T"}}}


def test_scrape():
    print("1. scrape() — mapping + boilerplate")
    orig, calls = _patch_post([(200, _ok_body(
        "\n\n\nCookie banner text\n\n# Real headline\n\nBody paragraph one.\n"
        "\n\nBody paragraph two with the actual analysis."))])
    try:
        r = web_scraper.scrape("https://example.com/a")
    finally:
        web_scraper.requests.post = orig
    ok("one call", calls["n"] == 1)
    ok("title kept", r and r["title"] == "T")
    ok("content stripped of blank runs",
       r and "\n\n\n" not in r["content"])
    ok("content capped at 12000",
       web_scraper._strip_boilerplate("x" * 30000) == "x" * 12000)

    # Disabled → None without calling (BOTH scrapers off: Firecrawl + Tavily).
    was = (config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED, config.TAVILY_API_KEY)
    config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = "fc-test", False
    config.TAVILY_API_KEY = ""
    try:
        ok("disabled -> None", web_scraper.scrape("https://example.com/b") is None)
    finally:
        config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED, config.TAVILY_API_KEY = was
    key_was = config.FIRECRAWL_API_KEY
    tav_was = config.TAVILY_API_KEY
    config.FIRECRAWL_API_KEY = ""
    config.TAVILY_API_KEY = ""
    try:
        ok("no key -> None",
           web_scraper.scrape("https://example.com/c") is None)
    finally:
        config.FIRECRAWL_API_KEY = key_was
        config.TAVILY_API_KEY = tav_was


def test_retry():
    print("2. scrape() — retry on transient, not on 402")
    # 429 → 500 → 200: two retries then success.
    orig, calls = _patch_post([
        (429, {}), (500, {}),
        (200, _ok_body("Final good content here.")),
    ])
    try:
        r = web_scraper.scrape("https://example.com/retry")
    finally:
        web_scraper.requests.post = orig
    ok("retried twice then succeeded", calls["n"] == 3 and r is not None)
    ok("final content returned", r and "Final good content" in r["content"])

    # 402 (no credits) — fatal for Firecrawl (no retry), falls to Tavily.
    # With no Tavily key → 1 call total, None.
    was = config.TAVILY_API_KEY
    config.TAVILY_API_KEY = ""
    orig, calls = _patch_post([(402, {})])
    try:
        r = web_scraper.scrape("https://example.com/credits")
    finally:
        web_scraper.requests.post = orig
        config.TAVILY_API_KEY = was
    ok("402 not retried", calls["n"] == 1 and r is None)

    # Persistent 5xx → None, never raises (no Tavily key → no fallback).
    was = config.TAVILY_API_KEY
    config.TAVILY_API_KEY = ""
    orig, calls = _patch_post([(503, {})] * 3)
    try:
        r = web_scraper.scrape("https://example.com/down")
    finally:
        web_scraper.requests.post = orig
        config.TAVILY_API_KEY = was
    ok("persistent 5xx -> None", calls["n"] == 3 and r is None)


def test_cache():
    print("3. get_article() — DB cache round-trip")
    conn = _mem_db()
    # Enable + patch the network once.
    was = (config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED)
    config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = "fc-test", True
    orig, calls = _patch_post([(200, _ok_body("Scraped body."))])
    try:
        a1 = web_scraper.get_article(conn, "https://example.com/cache")
        a2 = web_scraper.get_article(conn, "https://example.com/cache")
    finally:
        web_scraper.requests.post = orig
        config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = was
    ok("first call scrapes", a1 and a1["content"] == "Scraped body.")
    ok("second call served from cache (1 HTTP total)", calls["n"] == 1)

    # Stale cache → rescrape. TTL=-0.5 forces any age > -0.5h (always true;
    # TTL=0 would NOT work — a just-written row has age exactly 0.0 hours,
    # and 0.0 > 0.0 is False).
    config.FIRECRAWL_CACHE_TTL_HOURS = -0.5
    orig, calls = _patch_post([(200, _ok_body("Fresh body."))])
    try:
        a3 = web_scraper.get_article(conn, "https://example.com/cache")
    finally:
        web_scraper.requests.post = orig
        config.FIRECRAWL_CACHE_TTL_HOURS = 24.0
    ok("stale cache rescrapes", a3 and a3["content"] == "Fresh body."
       and calls["n"] == 1)


def test_tavily_fallback():
    print("4b. scrape() — Tavily extract fallback when Firecrawl is off")
    # Firecrawl disabled entirely → the call must go to Tavily's extract
    # endpoint and map raw_content into our {title, content} shape.
    was = (config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED,
           config.TAVILY_API_KEY)
    config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = "", False
    config.TAVILY_API_KEY = "tvly-test"
    tavily_body = {"results": [{"url": "https://example.com/tav",
                                "title": "Tav Article",
                                "raw_content": "Full body text from Tavily."}]}
    orig, calls = _patch_post([(200, tavily_body)])
    try:
        r = web_scraper.scrape("https://example.com/tav")
    finally:
        web_scraper.requests.post = orig
        config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED, config.TAVILY_API_KEY = was
    ok("one call routed to Tavily", calls["n"] == 1 and r is not None)
    ok("tavily title mapped", r and r["title"] == "Tav Article")
    ok("raw_content mapped to content", r and r["content"] == "Full body text from Tavily.")

    # No Tavily key at all → None, no crash.
    was_key = config.TAVILY_API_KEY
    config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = "", False
    config.TAVILY_API_KEY = ""
    orig, calls = _patch_post([(200, tavily_body)])
    try:
        r = web_scraper.scrape("https://example.com/nokey")
    finally:
        web_scraper.requests.post = orig
        config.TAVILY_API_KEY = was_key
    ok("no Tavily key → None without calling", r is None and calls["n"] == 0)


def test_enrich():
    print("4. enrich_recent() — best-effort pre-warm")
    conn = _mem_db()
    for i in range(3):
        conn.execute(
            "INSERT INTO news (id, source, title, url, published_at, "
            "sentiment, sentiment_score, symbols, fetched_at) "
            "VALUES (?, 'Test', ?, ?, '2026-08-17T00:00:00', 'neutral', 0, '', 'x')",
            (f"id{i}", f"Article {i}", f"https://example.com/{i}"))
    conn.commit()
    was = (config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED)
    config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = "fc-test", True
    orig, calls = _patch_post([(200, _ok_body("Body."))] * 10)
    try:
        r = web_scraper.enrich_recent(conn, limit=10)
    finally:
        web_scraper.requests.post = orig
        config.FIRECRAWL_API_KEY, config.FIRECRAWL_ENABLED = was
    ok("3 articles scraped", r["scraped"] == 3)
    ok("0 failed", r["errors"] == 0)
    ok("cache filled", conn.execute(
        "SELECT COUNT(*) AS n FROM article_content").fetchone()["n"] == 3)

    # Second run: everything already cached → no HTTP calls.
    orig, calls = _patch_post([(200, _ok_body("Body."))] * 10)
    try:
        r2 = web_scraper.enrich_recent(conn, limit=10)
    finally:
        web_scraper.requests.post = orig
    ok("idempotent — nothing rescraped", r2["cached"] == 3 and calls["n"] == 0)


def main():
    for fn in (test_scrape, test_retry, test_cache, test_tavily_fallback,
               test_enrich):
        fn()
    print(f"\n{'-' * 40}\nPASS: {PASS}  FAIL: {FAIL}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
