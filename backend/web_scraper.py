"""
cryptoautomation — Firecrawl web scraper (full article content for the AI).

Headlines alone lose most of the signal. This module turns a news URL into
clean markdown body text via Firecrawl (https://api.firecrawl.dev/v1/scrape),
so the AI agents (ai_agents.agent_news_sentiment) and the news analyst can
reason over the ACTUAL article instead of a bare title:

    /api/news/analyze?url=<article>&headline=<title>
        → web_scraper.get_article(conn, url)   (cached)
        → agent 1 scores the full context

Design rules:
  - The key comes from .env (FIRECRAWL_API_KEY) — never hardcoded.
  - Scraped content is cached in the `article_content` table for
    FIRECRAWL_CACHE_TTL_HOURS (24 h default) so credits aren't re-burned on
    repeat asks (free tier credits are limited).
  - Retry with exponential backoff on transient failures (429/5xx);
    "no credits left" (402) is fatal and NOT retried.
  - The module NEVER raises: on any failure it returns None and callers
    fall back to headline-only analysis.
  - `python web_scraper.py enrich [limit]` pre-warms the cache with the
    N most recent news articles (best-effort, one scrape each).
"""

from __future__ import annotations

import sys
import time

import requests

import config
import database

FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
USER_AGENT = "cryptoautomation/0.1 (personal research)"


def _strip_boilerplate(md: str) -> str:
    """Cut the cookie banners / nav that leak into markdown: drop leading
    junk until ~300 chars of real prose appear, then collapse blank runs."""
    if not md:
        return ""
    # The first meaningful line usually starts after the site chrome.
    lines = [l.rstrip() for l in md.splitlines()]
    text = "\n".join(lines).strip()
    text = "\n".join(l for l in text.splitlines() if l.strip())  # drop empties
    # Cap at a sane size for the LLM context window (agents truncate anyway).
    return text[:12000]


def _tavily_extract(url: str, timeout: int = 60) -> dict | None:
    """Fallback scraper via Tavily's `extract` endpoint (one credit per URL,
    on demand only — never in a hot loop). Returns {title, content} or
    None. Never raises."""
    if not config.TAVILY_API_KEY:
        return None
    try:
        resp = requests.post(
            TAVILY_EXTRACT_URL,
            headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}",
                     "Content-Type": "application/json"},
            json={"urls": [url], "extract_depth": "basic"},
            timeout=timeout,
        )
        if resp.status_code in (429, 500, 502, 503, 504):
            return None  # Tavily is a fallback — no backoff loop needed
        resp.raise_for_status()
        results = (resp.json() or {}).get("results") or []
        if not results:
            return None
        content = _strip_boilerplate(results[0].get("raw_content") or "")
        if not content:
            return None
        return {"title": (results[0].get("title") or ""),
                "content": content}
    except Exception as e:
        print(f"  [Tavily-extract] {url[:60]}… failed ({e})")
        return None


def scrape(url: str, timeout: int = None) -> dict | None:
    """Scrape one URL to clean markdown — Firecrawl first, Tavily extract
    as the fallback when Firecrawl is off, unkeyed, or fails.

    Returns {title, content} or None on failure. Firecrawl retries
    transient errors (429/5xx) with exponential backoff; treats 402 (out
    of credits) and 4xx as fatal, then hands off to Tavily. Never raises.
    """
    if not url or not url.startswith(("http://", "https://")):
        return None
    timeout = timeout or config.FIRECRAWL_TIMEOUT

    # Primary: Firecrawl.
    if config.FIRECRAWL_API_KEY and config.FIRECRAWL_ENABLED:
        last_err = "unknown error"
        for attempt in range(3):
            try:
                resp = requests.post(
                    FIRECRAWL_URL,
                    headers={"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}",
                             "Content-Type": "application/json",
                             "User-Agent": USER_AGENT},
                    json={"url": url, "formats": ["markdown"],
                          "onlyMainContent": True},
                    timeout=timeout,
                )
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(1.5 * (2 ** attempt))  # backoff, then retry
                    continue
                if resp.status_code == 402:
                    print("  [Firecrawl] out of credits (402) — falling back to Tavily.")
                    break
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                content = _strip_boilerplate(data.get("markdown") or "")
                if not content:
                    return None
                return {"title": (data.get("metadata") or {}).get("title") or "",
                        "content": content}
            except Exception as e:
                last_err = str(e) or last_err
                time.sleep(1.0 * (2 ** attempt))
        print(f"  [Firecrawl] {url[:60]}… failed ({last_err}) — falling back to Tavily.")

    # Fallback: Tavily extract (fresh news, on-demand only).
    return _tavily_extract(url, timeout)


def get_article(conn, url: str, force: bool = False) -> dict | None:
    """Scraped article for a URL — cache-first, scrape on miss.

    Returns {title, content} (from the DB cache or a fresh scrape) or
    None when scraping is off / fails. Never raises.
    """
    cached = database.get_article_cache(conn, url)
    if cached and not force:
        return {"title": cached["title"], "content": cached["content"]}
    fresh = scrape(url)
    if fresh:
        database.set_article_cache(conn, url, fresh["title"], fresh["content"])
    return fresh


def enrich_recent(conn, limit: int = 5) -> dict:
    """Best-effort pre-warm: scrape the `limit` most recent news articles
    with URLs that aren't cached yet. Returns {scraped, skipped, errors}."""
    rows = conn.execute(
        "SELECT title, url FROM news WHERE url LIKE 'http%' "
        "ORDER BY published_at DESC LIMIT ?", (min(limit, 20),)).fetchall()
    out = {"scraped": 0, "cached": 0, "errors": 0, "items": []}
    for r in rows:
        url = r["url"]
        if database.get_article_cache(conn, url):
            out["cached"] += 1
            continue
        art = scrape(url)
        if art:
            database.set_article_cache(conn, url, art["title"], art["content"])
            out["scraped"] += 1
            out["items"].append((r["title"][:60], len(art["content"])))
        else:
            out["errors"] += 1
    return out


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "enrich"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    conn = database.connect()
    database.init_db(conn)
    if cmd == "enrich":
        r = enrich_recent(conn, limit)
        print(f"enriched {r['scraped']} articles ({r['cached']} cached, "
              f"{r['errors']} failed)")
        for title, n in r["items"]:
            print(f"  + {title} ({n} chars)")
    else:
        print("Usage: python web_scraper.py [enrich [limit]]")


if __name__ == "__main__":
    main()
