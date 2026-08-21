"""
Coinpaprika — crypto price data backup
No key required for basic endpoints. Good lightweight backup to CoinGecko.
"""

import requests
from datetime import datetime
from typing import Optional

BASE_URL = "https://api.coinpaprika.com/v1"


def get_coins() -> list:
    """Get list of all coins."""
    try:
        r = requests.get(f"{BASE_URL}/coins", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[Coinpaprika] coins error: {e}")
    return []


def get_ticker(coin_id: str = "btc-bitcoin") -> dict:
    """Get ticker data for a specific coin."""
    try:
        r = requests.get(f"{BASE_URL}/tickers/{coin_id}", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        return {
            "id": data.get("id"),
            "name": data.get("name"),
            "symbol": data.get("symbol"),
            "rank": data.get("rank"),
            "price_usd": float(data.get("quotes", {}).get("USD", {}).get("price", 0)),
            "volume_24h": float(data.get("quotes", {}).get("USD", {}).get("volume_24h", 0)),
            "market_cap": float(data.get("quotes", {}).get("USD", {}).get("market_cap", 0)),
            "percent_change_1h": float(data.get("quotes", {}).get("USD", {}).get("percent_change_1h", 0)),
            "percent_change_24h": float(data.get("quotes", {}).get("USD", {}).get("percent_change_24h", 0)),
            "percent_change_7d": float(data.get("quotes", {}).get("USD", {}).get("percent_change_7d", 0)),
            "ath_price": float(data.get("quotes", {}).get("USD", {}).get("ath_price", 0)),
            "timestamp": data.get("last_updated"),
        }
    except Exception as e:
        print(f"[Coinpaprika] ticker error: {e}")
    return None


def get_multiple_tickers(coin_ids: list = None) -> list:
    """Get tickers for multiple coins."""
    if coin_ids is None:
        coin_ids = ["btc-bitcoin", "eth-ethereum", "sol-solana", "xrp-xrp", "bnb-bnb"]
    
    results = []
    for coin_id in coin_ids:
        ticker = get_ticker(coin_id)
        if ticker:
            results.append(ticker)
    
    return results


def get_global_market() -> dict:
    """Get global crypto market data."""
    try:
        r = requests.get(f"{BASE_URL}/global", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        return {
            "market_cap_usd": float(data.get("market_cap_usd", 0)),
            "volume_24h_usd": float(data.get("volume_24h_usd", 0)),
            "bitcoin_dominance": float(data.get("bitcoin_dominance_percentage", 0)),
            "altcoin_dominance": 100 - float(data.get("bitcoin_dominance_percentage", 0)),
            "active_currencies": data.get("active_currencies", 0),
            "active_markets": data.get("active_markets", 0),
            "percent_change_24h": float(data.get("percent_change_24h", 0)),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[Coinpaprika] global error: {e}")
    return None


def search_coin(query: str) -> list:
    """Search for coins by name or symbol."""
    try:
        coins = get_coins()
        query_lower = query.lower()
        results = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "symbol": c.get("symbol"),
                "rank": c.get("rank"),
            }
            for c in coins
            if query_lower in c.get("name", "").lower() or 
               query_lower in c.get("symbol", "").lower()
        ]
        return results[:20]  # Limit results
    except Exception as e:
        print(f"[Coinpaprika] search error: {e}")
    return []


def get_ohlcv(coin_id: str = "btc-bitcoin", quote: str = "usd", 
              limit: int = 30) -> list:
    """Get OHLCV data for a coin."""
    try:
        r = requests.get(f"{BASE_URL}/tickers/{coin_id}/ohlcv/latest",
                        params={"quote": quote, "limit": limit},
                        timeout=15)
        r.raise_for_status()
        data = r.json()
        
        return [
            {
                "time": item.get("time_open"),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume", 0)),
            }
            for item in data
        ]
    except Exception as e:
        print(f"[Coinpaprika] ohlcv error: {e}")
    return []


# Symbol to coin_id mapping
SYMBOL_MAP = {
    "BTC": "btc-bitcoin",
    "ETH": "eth-ethereum",
    "SOL": "sol-solana",
    "XRP": "xrp-xrp",
    "BNB": "bnb-bnb",
    "ADA": "ada-cardano",
    "DOGE": "doge-dogecoin",
    "DOT": "dot-polkadot",
    "MATIC": "matic-polygon",
    "AVAX": "avax-avalanche",
}


def get_price(symbol: str) -> float:
    """Quick price lookup by symbol."""
    coin_id = SYMBOL_MAP.get(symbol.upper())
    if not coin_id:
        return None
    ticker = get_ticker(coin_id)
    if ticker:
        return ticker.get("price_usd")
    return None


if __name__ == "__main__":
    print("=== Coinpaprika Test ===")
    
    print("\nGlobal Market:")
    global_data = get_global_market()
    if global_data:
        print(f"  Market Cap: ${global_data['market_cap_usd']:,.0f}")
        print(f"  24h Volume: ${global_data['volume_24h_usd']:,.0f}")
        print(f"  BTC Dominance: {global_data['bitcoin_dominance']:.2f}%")
        print(f"  24h Change: {global_data['percent_change_24h']:.2f}%")
    
    print("\nTop Coins:")
    tickers = get_multiple_tickers()
    for t in tickers:
        print(f"  {t['symbol']}: ${t['price_usd']:,.2f} ({t['percent_change_24h']:+.2f}%)")
    
    print("\nBTC Price (quick):")
    btc_price = get_price("BTC")
    if btc_price:
        print(f"  ${btc_price:,.2f}")
