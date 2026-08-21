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
# Alpha Vantage News & Sentiment (structured per-ticker scores)
# ----------------------------------------------------------------------
ALPHA_URL = "https://www.alphavantage.co/query"
_alpha_last_fetch = 0.0  # process-local throttle (free tier: 25 req/day)


def _alpha_iso(ts: str) -> str:
    """Alpha Vantage timestamps are 'YYYYMMDDTHHMMSS' → ISO-8601 UTC."""
    if not ts:
        return _now_iso()
    try:
        return datetime.strptime(ts.strip(), "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc).isoformat()
    except ValueError:
        return str(ts).strip()


def _alpha_label(score: float, label: str = "") -> str:
    """AV label / score → our bullish|bearish|neutral. Uses the API's own
    scale when the label is missing: x >= 0.35 Bullish, 0.15-0.35
    Somewhat-Bullish, -0.15..0.15 Neutral, -0.35..-0.15 Somewhat-Bearish,
    x <= -0.35 Bearish."""
    lab = (label or "").strip().lower().replace("_", "-")
    if "bullish" in lab:
        return "bullish"
    if "bearish" in lab:
        return "bearish"
    if "neutral" in lab:
        return "neutral"
    if score >= 0.15:
        return "bullish"
    if score <= -0.15:
        return "bearish"
    return "neutral"


def _alpha_ticker_map() -> dict:
    """'CRYPTO:BTC' style ticker → our symbol base (BTC/USDT → BTC)."""
    return {s.split("/")[0].upper(): s.split("/")[0]
            for s in config.SYMBOLS}


def fetch_alpha_news(conn) -> list:
    """One NEWS_SENTIMENT call → news-table rows with REAL scores.

    Each feed item becomes one row carrying the API's per-ticker sentiment
    for the MOST relevant crypto ticker we watch (relevance-weighted, so a
    Solana story doesn't drag BTC's score along) and the list of crypto
    tickers it mentions. Throttled to one call per ALPHA_NEWS_FETCH_HOURS
    (free tier = 25 requests/day). Never raises.

    Note: on the free tier the crypto feed can lag (observed: newest items
    ~5 months old). The reviewer's 12h half-life decay zeroes stale scores
    out, so old items fill the feed but never move the gate.
    """
    global _alpha_last_fetch
    if not (config.ALPHA_VANTAGE_API_KEY and config.ALPHA_NEWS_ENABLED):
        return []
    if time.time() - _alpha_last_fetch < config.ALPHA_NEWS_FETCH_HOURS * 3600:
        return []  # throttle — keep the free tier alive

    # Free keys accept at most 5 tickers per request — filter to the
    # configured majors (config.ALPHA_NEWS_TICKERS).
    wanted = [b for b in config.ALPHA_NEWS_TICKERS if b]
    if not wanted:
        wanted = ["BTC", "ETH", "SOL", "BNB", "DOGE"]
    ticker_map = _alpha_ticker_map()
    try:
        resp = requests.get(
            ALPHA_URL,
            params={"function": "NEWS_SENTIMENT",
                    "apikey": config.ALPHA_VANTAGE_API_KEY,
                    "tickers": ",".join("CRYPTO:" + b for b in wanted[:5]),
                    "limit": config.ALPHA_NEWS_LIMIT},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [AlphaVantage] failed ({e}) — continuing without it.")
        return []
    _alpha_last_fetch = time.time()

    feed = data.get("feed") or []
    items = []
    for it in feed:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        # Collect the crypto tickers we actually watch, with their scores.
        matched = []
        for t in it.get("ticker_sentiment") or []:
            tk = str(t.get("ticker") or "")
            if not tk.upper().startswith("CRYPTO:"):
                continue
            base = tk.split(":", 1)[1].upper()
            if base not in ticker_map:
                continue
            try:
                score = float(t.get("ticker_sentiment_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            try:
                relevance = float(t.get("relevance_score") or 0.0)
            except (TypeError, ValueError):
                relevance = 0.0
            matched.append((base, score, relevance))
        if not matched:
            continue  # equities-only item — skip, we asked for crypto

        # The most relevant ticker's score drives this row's sentiment;
        # all matched tickers (relevance >= 0.1, drops the 0.04 noise) are
        # listed so the reviewer knows which coins the story touches.
        best = max(matched, key=lambda m: m[2])
        label = _alpha_label(best[1], str(it.get("overall_sentiment_label") or ""))
        symbols = ", ".join(sorted({b for b, _, rel in matched if rel >= 0.1})
                             or [best[0]])
        items.append({
            "source": str(it.get("source") or "AlphaVantage"),
            "title": title,
            "url": str(it.get("url") or ""),
            "published_at": _alpha_iso(str(it.get("time_published") or "")),
            "sentiment": label,
            "sentiment_score": round(max(-1.0, min(1.0, best[1])), 3),
            "symbols": symbols,
        })
    print(f"  [AlphaVantage] {len(items)} crypto stories")
    return items


# ----------------------------------------------------------------------
# Tavily news search (fresh stories with real published dates)
# ----------------------------------------------------------------------
TAVILY_URL = "https://api.tavily.com/search"
_tavily_last_fetch = 0.0  # process-local throttle (free tier: 1000 credits/mo)


def fetch_tavily_news(conn) -> list:
    """One Tavily `topic=news` search → news-table rows with REAL dates.

    Alpha Vantage's free crypto feed lags by months; Tavily returns the
    current stories (TAVILY_NEWS_DAYS window), so this is the fresh
    structured layer. Each result becomes one row: sentiment from the
    keyword scorer (Tavily has no per-ticker score), symbols detected from
    the title against our watched coins, and the API's own published date.

    Throttled to one call per TAVILY_NEWS_FETCH_HOURS (free tier ~1000
    credits/month, one search ≈ 1 credit). Never raises.
    """
    global _tavily_last_fetch
    if not (config.TAVILY_API_KEY and config.TAVILY_NEWS_ENABLED):
        return []
    if time.time() - _tavily_last_fetch < config.TAVILY_NEWS_FETCH_HOURS * 3600:
        return []  # throttle — keep the free tier alive

    try:
        resp = requests.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}",
                     "Content-Type": "application/json"},
            json={"query": config.TAVILY_NEWS_QUERY,
                  "topic": "news",
                  "search_depth": "basic",
                  "days": config.TAVILY_NEWS_DAYS,
                  "max_results": config.TAVILY_NEWS_LIMIT,
                  "include_answer": False,
                  "include_raw_content": False},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [Tavily] failed ({e}) — continuing without it.")
        return []
    _tavily_last_fetch = time.time()

    # Symbol cross-reference: reviewer.COIN_HINTS maps each watched symbol
    # to its name/ticker tokens (bitcoin, btc; ethereum, ether, eth; ...).
    # Reuse it so "Ethereum ETF approved" tags ETH even though the title
    # never spells the ticker.
    from reviewer import COIN_HINTS, _mentions  # lazy — avoids import cost
    symbols_for = lambda title: ", ".join(sorted(
        sym.split("/")[0] for sym, hints in COIN_HINTS.items()
        if _mentions({"title": title, "symbols": ""}, hints))) or ""

    items = []
    for r in (data.get("results") or []):
        title = (r.get("title") or "").strip()
        if not title:
            continue
        sentiment, score = fallback_sentiment(title)
        items.append({
            "source": str(r.get("source") or "Tavily"),
            "title": title,
            "url": str(r.get("url") or ""),
            "published_at": _normalize_time(r.get("published_date") or _now_iso()),
            "sentiment": sentiment,
            "sentiment_score": round(score, 3),
            "symbols": symbols_for(title),
        })
    print(f"  [Tavily] {len(items)} news stories")
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

    if config.ALPHA_VANTAGE_API_KEY and config.ALPHA_NEWS_ENABLED:
        try:
            items += fetch_alpha_news(database.connect())
        except Exception as e:
            print(f"  AlphaVantage failed ({e}) — continuing without it.")

    if config.TAVILY_API_KEY and config.TAVILY_NEWS_ENABLED:
        try:
            items += fetch_tavily_news(database.connect())
        except Exception as e:
            print(f"  Tavily failed ({e}) — continuing without it.")

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
