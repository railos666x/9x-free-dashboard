"""
NewsData.io — news from 150+ countries.
Free API key required. Commercial use allowed on free tier.
"""

import requests
import config
from datetime import datetime
from typing import Optional

BASE_URL = "https://newsdata.io/api/1"


def _get(endpoint: str, params: dict = None) -> dict:
    """Make authenticated request to NewsData.io."""
    if not config.NEWSDATA_API_KEY:
        return {"error": "API key not configured"}
    
    if params is None:
        params = {}
    params["apikey"] = config.NEWSDATA_API_KEY
    
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[NewsData] request error: {e}")
        return {"error": str(e)}


def get_news(q: str = None, category: str = None, language: str = "en",
             country: str = None, limit: int = 10) -> list:
    """Get news articles.
    q: Search query (e.g., 'bitcoin', 'crypto')
    category: 'business', 'technology', 'science', 'health', 'sports', 'entertainment'
    language: Language code (en, es, fr, de, etc.)
    country: 2-letter country code (us, gb, etc.)
    limit: Max articles to return
    """
    params = {
        "language": language,
        "size": min(limit, 10),  # Free tier max
    }
    
    if q:
        params["q"] = q
    if category:
        params["category"] = category
    if country:
        params["country"] = country
    
    data = _get("news", params)
    
    if "results" in data:
        return [
            {
                "title": article.get("title"),
                "description": article.get("description"),
                "url": article.get("link"),
                "source": article.get("source_id"),
                "published_at": article.get("pubDate"),
                "category": article.get("category"),
                "image_url": article.get("image_url"),
                "sentiment": article.get("sentiment"),
            }
            for article in data["results"]
        ]
    return []


def get_crypto_news(limit: int = 10) -> list:
    """Get crypto-specific news."""
    return get_news(q="bitcoin OR ethereum OR crypto", limit=limit)


def get_forex_news(limit: int = 10) -> list:
    """Get forex-related news."""
    return get_news(q="forex OR currency OR dollar OR euro", 
                    category="business", limit=limit)


def get_business_news(country: str = "us", limit: int = 10) -> list:
    """Get business news for a country."""
    return get_news(category="business", country=country, limit=limit)


def get_latest_headlines(category: str = None, limit: int = 10) -> list:
    """Get latest headlines, optionally filtered by category."""
    return get_news(category=category, limit=limit)


if __name__ == "__main__":
    print("=== NewsData.io Test ===")
    print(f"API Key configured: {bool(config.NEWSDATA_API_KEY)}")
    
    print("\nCrypto News (latest 5):")
    news = get_crypto_news(limit=5)
    for article in news:
        print(f"  [{article.get('category', 'unknown')}] {article['title'][:60]}...")
        print(f"    Source: {article['source']} | {article['published_at']}")
    
    print("\nBusiness News (US, latest 3):")
    biz = get_business_news("us", limit=3)
    for article in biz:
        print(f"  {article['title'][:60]}...")
    
    print("\nForex News (latest 3):")
    forex = get_forex_news(limit=3)
    for article in forex:
        print(f"  {article['title'][:60]}...")
