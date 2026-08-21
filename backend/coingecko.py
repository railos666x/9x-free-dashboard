"""
CoinGecko API integration — crypto market data (no API key required).

Provides:
- Current prices, volume, market cap
- Historical OHLC data
- Trending coins
- Global market stats
- Social/community data

Free tier: 10-30 req/min, unlimited total calls.
Pro key optional for higher rate limits.
"""
import requests
import time
from typing import Optional
import config


# Rate limiting: free tier is ~10-30 req/min
_last_call = 0.0
MIN_INTERVAL = 3.0  # seconds between calls (conservative for free tier)


def _rate_limit():
    """Ensure we don't exceed rate limits."""
    global _last_call
    now = time.time()
    elapsed = now - _last_call
    if elapsed < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - elapsed)
    _last_call = time.time()


def _get(endpoint: str, params: dict = None) -> Optional[dict]:
    """Make a GET request to CoinGecko API."""
    if not config.COINGECKO_ENABLED:
        return None
    
    _rate_limit()
    url = f"{config.COINGECKO_BASE_URL}{endpoint}"
    headers = {}
    if config.COINGECKO_API_KEY:
        headers["x-cg-pro-api-key"] = config.COINGECKO_API_KEY
    
    try:
        r = requests.get(url, params=params or {}, headers=headers, timeout=15)
        if r.status_code == 429:
            # Rate limited — back off
            time.sleep(60)
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[CoinGecko] {endpoint} failed: {e}")
        return None


def get_coin_price(coin_id: str, vs_currency: str = "usd") -> Optional[dict]:
    """Get current price, volume, market cap for a coin.
    
    Args:
        coin_id: CoinGecko coin ID (e.g., 'bitcoin', 'ethereum')
        vs_currency: Currency to price against (default: 'usd')
    
    Returns:
        {price, volume_24h, market_cap, price_change_24h_pct} or None
    """
    # Use /coins/{id} endpoint for single coin
    data = _get(f"/coins/{coin_id}", {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    })
    if data and "market_data" in data:
        md = data["market_data"]
        return {
            "price": md.get("current_price", {}).get(vs_currency),
            "volume_24h": md.get("total_volume", {}).get(vs_currency),
            "market_cap": md.get("market_cap", {}).get(vs_currency),
            "price_change_24h_pct": md.get("price_change_percentage_24h"),
        }
    return None


def get_multiple_prices(coin_ids: list, vs_currency: str = "usd") -> dict:
    """Get prices for multiple coins in one call.
    
    Args:
        coin_ids: List of CoinGecko coin IDs
        vs_currency: Currency to price against
    
    Returns:
        Dict of {coin_id: {price, volume_24h, market_cap, ...}}
    """
    # Use /coins/markets endpoint for multiple coins
    ids_str = ",".join(coin_ids)
    data = _get("/coins/markets", {
        "vs_currency": vs_currency,
        "ids": ids_str,
        "order": "market_cap_desc",
        "per_page": len(coin_ids),
        "page": 1,
        "sparkline": "false",
    })
    if not data:
        return {}
    
    result = {}
    for coin in data:
        coin_id = coin.get("id")
        if coin_id:
            result[coin_id] = {
                "price": coin.get("current_price"),
                "volume_24h": coin.get("total_volume"),
                "market_cap": coin.get("market_cap"),
                "price_change_24h_pct": coin.get("price_change_percentage_24h"),
            }
    return result


def get_ohlc(coin_id: str, vs_currency: str = "usd", days: int = 1) -> Optional[list]:
    """Get OHLC candles for a coin.
    
    Args:
        coin_id: CoinGecko coin ID
        vs_currency: Currency
        days: Number of days (1, 7, 14, 30, 90, 180, 365, max)
    
    Returns:
        List of [timestamp, open, high, low, close] or None
    """
    data = _get(f"/coins/{coin_id}/ohlc", {
        "vs_currency": vs_currency,
        "days": days,
    })
    return data


def get_trending() -> Optional[list]:
    """Get trending coins (top 7 by search activity)."""
    data = _get("/search/trending")
    if data and "coins" in data:
        return [c.get("item", {}) for c in data["coins"][:7]]
    return None


def get_global_stats() -> Optional[dict]:
    """Get global crypto market stats.
    
    Returns:
        {total_market_cap, total_volume, market_cap_pct, 
         active_cryptocurrencies, markets}
    """
    data = _get("/global")
    if data and "data" in data:
        d = data["data"]
        return {
            "total_market_cap_usd": d.get("total_market_cap", {}).get("usd"),
            "total_volume_usd": d.get("total_volume", {}).get("usd"),
            "btc_dominance_pct": d.get("market_cap_percentage", {}).get("btc"),
            "active_cryptocurrencies": d.get("active_cryptocurrencies"),
            "markets": d.get("markets"),
        }
    return None


def search_coin(query: str) -> Optional[list]:
    """Search for a coin by name or symbol.
    
    Returns:
        List of {id, name, symbol, market_cap_rank}
    """
    data = _get("/search", {"query": query})
    if data and "coins" in data:
        return [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "symbol": c.get("symbol"),
                "rank": c.get("market_cap_rank"),
            }
            for c in data["coins"][:10]
        ]
    return None


# Map common symbols to CoinGecko IDs
SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "ALGO": "algorand",
    "NEAR": "near",
    "XLM": "stellar",
    "ICP": "internet-computer",
    "FIL": "filecoin",
    "HBAR": "hedera-hashgraph",
    "TRX": "tron",
    "ETC": "ethereum-classic",
    "AAVE": "aave",
    "EGLD": "elrond-erd-2",
    "THETA": "theta-token",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SUSHI": "sushi",
    "CRV": "curve-dao-token",
    "COMP": "compound-governance-token",
    "YFI": "yearn-finance",
    "1INCH": "1inch",
    "BAND": "band-protocol",
    "KNC": "kyber-network-crystal",
    "ZRX": "0x",
    "RUNE": "thorchain",
    "LPT": "livepeer",
    "DYDX": "dydx",
    "TRB": "tellor",
    "SHIB": "shiba-inu",
    "SNX": "havven",
    "POL": "polygon-ecosystem-token",
}


def symbol_to_coingecko_id(symbol: str) -> Optional[str]:
    """Convert a trading symbol to CoinGecko ID."""
    return SYMBOL_TO_ID.get(symbol.upper())


if __name__ == "__main__":
    # Quick test
    print("=== CoinGecko API Test ===")
    
    # Test single price
    btc = get_coin_price("bitcoin")
    if btc:
        print(f"BTC: ${btc['price']:,.2f} (24h: {btc['price_change_24h_pct']:+.2f}%)")
    
    # Test multiple prices
    prices = get_multiple_prices(["bitcoin", "ethereum", "solana"])
    for coin, data in prices.items():
        print(f"{coin}: ${data['price']:,.2f}")
    
    # Test global stats
    stats = get_global_stats()
    if stats:
        print(f"\nGlobal market cap: ${stats['total_market_cap_usd']/1e9:.1f}B")
        print(f"BTC dominance: {stats['btc_dominance_pct']:.1f}%")
