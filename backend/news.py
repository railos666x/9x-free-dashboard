"""
News engine: pulls stories from CryptoPanic (aggregator with sentiment
scores) plus a set of trusted RSS feeds, normalizes everything into one
list, and stores it in SQLite (de-duplicated).

Run:
    python news.py fetch    # fetch once now
    python news.py live     # keep fetching every NEWS_REFRESH_SECONDS

If CRYPTOPANIC_API_KEY is empty, it simply runs RSS-only.
"""

import sys
import time
from datetime import datetime, timezone

import feedparser
import requests

import config
import database

CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
USER_AGENT = "cryptoautomation/0.1 (personal research)"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_time(raw) -> str:
    """Best-effort conversion of a source timestamp to ISO format."""
    if not raw:
        return _now_iso()
    if isinstance(raw, str):
        return raw
    try:
        return raw.isoformat()
    except Exception:
        return str(raw)


# ----------------------------------------------------------------------
# CryptoPanic (has real sentiment votes)
# ----------------------------------------------------------------------
def fetch_cryptopanic(api_key: str) -> list:
    """
    Pull the latest crypto posts from CryptoPanic.
    Each post carries community votes we turn into a sentiment:
      bullish votes > bearish votes  -> bullish (score = how lopsided)
      bearish votes > bullish votes  -> bearish
      else                           -> neutral
    """
    params = {"auth_token": api_key, "public": "true", "kind": "news"}
    resp = requests.get(
        CRYPTOPANIC_URL, params=params, timeout=20,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for post in data.get("results", []):
        votes = post.get("votes", {})
        bull = votes.get("positive", 0)
        bear = votes.get("negative", 0)
        total = bull + bear

        if bull > bear:
            sentiment, score = "bullish", (bull - bear) / max(total, 1)
        elif bear > bull:
            sentiment, score = "bearish", (bear - bull) / max(total, 1)
        else:
            sentiment, score = "neutral", 0.0

        currencies = post.get("currencies") or []
        symbols = ", ".join(c.get("code", "") for c in currencies)

        items.append(
            {
                "source": (post.get("source") or {}).get("title", "CryptoPanic"),
                "title": post.get("title", ""),
                "url": post.get("url", ""),
                "published_at": _normalize_time(post.get("published_at")),
                "sentiment": sentiment,
                "sentiment_score": round(score, 3),
                "symbols": symbols,
            }
        )
    return items


# ----------------------------------------------------------------------
# RSS feeds (keyword-based sentiment fallback)
# ----------------------------------------------------------------------
def fallback_sentiment(title: str) -> tuple:
    """
    Simple keyword sentiment for RSS stories that have no official score.
    Returns (sentiment_label, score) with score in -1..1.
    """
    text = title.lower()
    bull_hits = sum(1 for w in config.BULLISH_KEYWORDS if w in text)
    bear_hits = sum(1 for w in config.BEARISH_KEYWORDS if w in text)

    if bull_hits > bear_hits:
        return "bullish", round(min(bull_hits / 3.0, 1.0), 3)
    if bear_hits > bull_hits:
        return "bearish", round(-min(bear_hits / 3.0, 1.0), 3)
    return "neutral", 0.0


def fetch_rss(feeds: list) -> list:
    """Pull the latest items from every RSS feed."""
    items = []
    for name, url in feeds:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [RSS] {name}: failed ({e})")
            continue

        for entry in feed.entries[:20]:  # newest 20 per feed per fetch
            title = entry.get("title", "").strip()
            if not title:
                continue
            sentiment, score = fallback_sentiment(title)
            items.append(
                {
                    "source": name,
                    "title": title,
                    "url": entry.get("link", ""),
                    "published_at": _normalize_time(
                        entry.get("published", entry.get("updated", ""))
                    ),
                    "sentiment": sentiment,
                    "sentiment_score": score,
                    "symbols": "",
                }
            )
    return items


# ----------------------------------------------------------------------
# Combine + store
# ----------------------------------------------------------------------
def fetch_all() -> list:
    """Pull from every source and merge into one list."""
    items = []
    if config.CRYPTOPANIC_API_KEY:
        print("  Fetching CryptoPanic...")
        try:
            items += fetch_cryptopanic(config.CRYPTOPANIC_API_KEY)
        except Exception as e:
            print(f"  CryptoPanic failed ({e}) — continuing with RSS only.")
    else:
        print("  No CryptoPanic key set — using RSS feeds only.")

    print(f"  Fetching {len(config.NEWS_FEEDS)} RSS feeds...")
    items += fetch_rss(config.NEWS_FEEDS)
    return items


def live_loop() -> None:
    print(f"News loop started — refreshing every "
          f"{config.NEWS_REFRESH_SECONDS}s. Ctrl+C to stop.")
    while True:
        try:
            items = fetch_all()
            added = database.save_news(database.connect(), items)
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] fetched "
                  f"{len(items)}, {added} new")
        except Exception as e:
            print(f"  error: {e}")
        time.sleep(config.NEWS_REFRESH_SECONDS)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    conn = database.connect()
    database.init_db(conn)

    if cmd == "fetch":
        items = fetch_all()
        added = database.save_news(conn, items)
        print(f"Fetched {len(items)} stories, added {added} new to the DB.")
    elif cmd == "live":
        live_loop()
    else:
        print("Usage: python news.py [fetch|live]")


if __name__ == "__main__":
    main()
