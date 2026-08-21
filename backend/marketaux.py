"""
MarketAux — financial news with sentiment and ticker tagging.
Free API key required. 100 requests/day, 50 articles per request.
"""

import requests
import config
from datetime import datetime
from typing import Optional

BASE_URL = "https://api.marketaux.com/v1"


def _get(endpoint: str, params: dict = None) -> dict:
    """Make authenticated request to MarketAux."""
    if not config.MARKETAUX_API_KEY:
        return {"error": "API key not configured"}
    
    if params is None:
        params = {}
    params["api_token"] = config.MARKETAUX_API_KEY
    
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[MarketAux] request error: {e}")
        return {"error": str(e)}


def get_news(symbols: list = None, language: str = "en", 
             limit: int = 50, sort: str = "latest") -> list:
    """Get financial news articles.
    symbols: List of ticker symbols to filter (e.g., ['BTC', 'ETH'])
    language: Language code (en, es, fr, de, etc.)
    limit: Max articles to return (max 50)
    sort: 'latest' or 'relevancy'
    """
    params = {
        "language": language,
        "limit": min(limit, 50),
        "sort": sort,
    }
    
    if symbols:
        params["symbols"] = ",".join(symbols)
    
    data = _get("news/all", params)
    
    if "data" in data:
        return [
            {
                "title": article.get("title"),
                "description": article.get("description"),
                "url": article.get("url"),
                "source": article.get("source"),
                "published_at": article.get("published_at"),
                "sentiment": article.get("sentiment"),
                "sentiment_score": article.get("sentiment_score"),
                "entities": [
                    {
                        "symbol": e.get("symbol"),
                        "name": e.get("name"),
                        "type": e.get("type"),
                    }
                    for e in article.get("entities", [])
                ],
                "image_url": article.get("image_url"),
            }
            for article in data["data"]
        ]
    return []


def get_crypto_news(limit: int = 50) -> list:
    """Get crypto-specific news."""
    return get_news(symbols=["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE"], 
                    limit=limit)


def get_news_by_sentiment(sentiment: str = "bullish", limit: int = 20) -> list:
    """Get news filtered by sentiment.
    sentiment: 'bullish', 'bearish', or 'neutral'
    """
    params = {
        "sentiment": sentiment,
        "limit": min(limit, 50),
        "language": "en",
    }
    
    data = _get("news/all", params)
    
    if "data" in data:
        return [
            {
                "title": article.get("title"),
                "url": article.get("url"),
                "source": article.get("source"),
                "published_at": article.get("published_at"),
                "sentiment": article.get("sentiment"),
                "sentiment_score": article.get("sentiment_score"),
            }
            for article in data["data"]
        ]
    return []


def get_entity_news(entity: str, limit: int = 20) -> list:
    """Get news about a specific entity (company, person, etc.)."""
    params = {
        "entity": entity,
        "limit": min(limit, 50),
        "language": "en",
    }
    
    data = _get("news/all", params)
    
    if "data" in data:
        return [
            {
                "title": article.get("title"),
                "url": article.get("url"),
                "source": article.get("source"),
                "published_at": article.get("published_at"),
                "sentiment": article.get("sentiment"),
            }
            for article in data["data"]
        ]
    return []


def get_sentiment_summary(symbols: list = None) -> dict:
    """Get sentiment summary for symbols."""
    articles = get_news(symbols=symbols, limit=50)
    
    if not articles:
        return None
    
    bullish = sum(1 for a in articles if a.get("sentiment") == "bullish")
    bearish = sum(1 for a in articles if a.get("sentiment") == "bearish")
    neutral = sum(1 for a in articles if a.get("sentiment") == "neutral")
    total = len(articles)
    
    avg_score = sum(a.get("sentiment_score", 0) or 0 for a in articles) / total if total else 0
    
    return {
        "total_articles": total,
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "bullish_pct": (bullish / total * 100) if total else 0,
        "bearish_pct": (bearish / total * 100) if total else 0,
        "avg_sentiment_score": avg_score,
        "latest_articles": articles[:5],
    }


if __name__ == "__main__":
    print("=== MarketAux Test ===")
    print(f"API Key configured: {bool(config.MARKETAUX_API_KEY)}")
    
    print("\nCrypto News (latest 5):")
    news = get_crypto_news(limit=5)
    for article in news:
        sentiment = article.get("sentiment", "unknown")
        score = article.get("sentiment_score", 0)
        print(f"  [{sentiment}] {article['title'][:60]}...")
        print(f"    Source: {article['source']} | Score: {score}")
    
    print("\nBTC Sentiment Summary:")
    summary = get_sentiment_summary(symbols=["BTC"])
    if summary:
        print(f"  Total: {summary['total_articles']} articles")
        print(f"  Bullish: {summary['bullish']} ({summary['bullish_pct']:.1f}%)")
        print(f"  Bearish: {summary['bearish']} ({summary['bearish_pct']:.1f}%)")
        print(f"  Avg Score: {summary['avg_sentiment_score']:.3f}")
