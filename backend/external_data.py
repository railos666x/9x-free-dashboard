"""
External data sources aggregator — combines all free API sources.
Provides unified interface for macro, crypto, forex, sentiment data.
"""

import config
from datetime import datetime
from typing import Optional


def get_all_data() -> dict:
    """Fetch all available external data sources."""
    data = {
        "timestamp": datetime.now().isoformat(),
        "sources": {},
    }
    
    # Crypto data
    if config.COINGECKO_ENABLED:
        try:
            import coingecko
            data["sources"]["coingecko"] = {
                "status": "ok",
                "data": coingecko.get_global_stats(),
            }
        except Exception as e:
            data["sources"]["coingecko"] = {"status": "error", "error": str(e)}
    
    if config.COINPAPRIKA_ENABLED:
        try:
            import coinpaprika
            data["sources"]["coinpaprika"] = {
                "status": "ok",
                "data": coinpaprika.get_global_market(),
            }
        except Exception as e:
            data["sources"]["coinpaprika"] = {"status": "error", "error": str(e)}
    
    # Sentiment
    if config.FEAR_GREED_ENABLED:
        try:
            import fear_greed
            data["sources"]["fear_greed"] = {
                "status": "ok",
                "data": fear_greed.get_sentiment_signal(),
            }
        except Exception as e:
            data["sources"]["fear_greed"] = {"status": "error", "error": str(e)}
    
    # Macro data
    if config.FED_TREASURY_ENABLED:
        try:
            import fed_treasury
            data["sources"]["fed_treasury"] = {
                "status": "ok",
                "data": fed_treasury.get_macro_snapshot(),
            }
        except Exception as e:
            data["sources"]["fed_treasury"] = {"status": "error", "error": str(e)}
    
    if config.ECONDB_ENABLED:
        try:
            import econdb
            data["sources"]["econdb"] = {
                "status": "ok",
                "data": econdb.get_global_macro_snapshot(),
            }
        except Exception as e:
            data["sources"]["econdb"] = {"status": "error", "error": str(e)}
    
    # Forex data
    if config.FRANKFURTER_ENABLED:
        try:
            import frankfurter
            data["sources"]["frankfurter"] = {
                "status": "ok",
                "data": frankfurter.get_latest_rates("EUR"),
            }
        except Exception as e:
            data["sources"]["frankfurter"] = {"status": "error", "error": str(e)}
    
    if config.CURRENCY_API_ENABLED:
        try:
            import currency_api
            data["sources"]["currency_api"] = {
                "status": "ok",
                "data": currency_api.get_latest_rates("usd"),
            }
        except Exception as e:
            data["sources"]["currency_api"] = {"status": "error", "error": str(e)}
    
    # On-chain data
    if config.DEFILLAMA_ENABLED:
        try:
            import defillama
            data["sources"]["defillama"] = {
                "status": "ok",
                "data": defillama.get_defi_snapshot(),
            }
        except Exception as e:
            data["sources"]["defillama"] = {"status": "error", "error": str(e)}
    
    if config.MEMPOOL_ENABLED:
        try:
            import mempool
            data["sources"]["mempool"] = {
                "status": "ok",
                "data": mempool.get_btc_network_status(),
            }
        except Exception as e:
            data["sources"]["mempool"] = {"status": "error", "error": str(e)}
    
    return data


def get_crypto_prices() -> dict:
    """Get crypto prices from available sources (with fallback)."""
    # Try CoinGecko first
    if config.COINGECKO_ENABLED:
        try:
            import coingecko
            prices = coingecko.get_multiple_prices(["bitcoin", "ethereum", "solana"])
            if prices:
                return {"source": "coingecko", "prices": prices}
        except Exception:
            pass
    
    # Fallback to Coinpaprika
    if config.COINPAPRIKA_ENABLED:
        try:
            import coinpaprika
            tickers = coinpaprika.get_multiple_tickers()
            if tickers:
                return {
                    "source": "coinpaprika",
                    "prices": {t["symbol"]: t["price_usd"] for t in tickers if t.get("price_usd")}
                }
        except Exception:
            pass
    
    return {"source": None, "prices": {}}


def get_sentiment() -> dict:
    """Get market sentiment from Fear & Greed Index."""
    if not config.FEAR_GREED_ENABLED:
        return None
    
    try:
        import fear_greed
        return fear_greed.get_sentiment_signal()
    except Exception:
        return None


def get_forex_rates(base: str = "USD") -> dict:
    """Get forex rates from available sources (with fallback)."""
    # Try Frankfurter (ECB)
    if config.FRANKFURTER_ENABLED:
        try:
            import frankfurter
            rates = frankfurter.get_latest_rates(base)
            if rates:
                return {"source": "frankfurter", "rates": rates}
        except Exception:
            pass
    
    # Fallback to Currency API
    if config.CURRENCY_API_ENABLED:
        try:
            import currency_api
            rates = currency_api.get_latest_rates(base.lower())
            if rates:
                return {"source": "currency_api", "rates": rates}
        except Exception:
            pass
    
    return {"source": None, "rates": {}}


def get_macro_data() -> dict:
    """Get macro economic data from available sources."""
    macro = {}
    
    if config.FED_TREASURY_ENABLED:
        try:
            import fed_treasury
            macro["treasury"] = fed_treasury.get_macro_snapshot()
        except Exception:
            pass
    
    if config.ECONDB_ENABLED:
        try:
            import econdb
            macro["econdb"] = econdb.get_global_macro_snapshot()
        except Exception:
            pass
    
    return macro


def get_defi_data() -> dict:
    """Get DeFi/on-chain data."""
    defi = {}
    
    if config.DEFILLAMA_ENABLED:
        try:
            import defillama
            defi["tvl"] = defillama.get_defi_snapshot()
        except Exception:
            pass
    
    if config.MEMPOOL_ENABLED:
        try:
            import mempool
            defi["mempool"] = mempool.get_btc_network_status()
        except Exception:
            pass
    
    return defi


if __name__ == "__main__":
    import json
    print("=== External Data Sources Test ===\n")
    
    data = get_all_data()
    print(f"Timestamp: {data['timestamp']}")
    print(f"\nSources ({len(data['sources'])}):")
    
    for name, info in data["sources"].items():
        status = info.get("status", "unknown")
        if status == "ok":
            print(f"  ✓ {name}: OK")
        else:
            print(f"  ✗ {name}: {info.get('error', 'unknown error')}")
    
    print("\n--- Quick Tests ---")
    
    print("\nCrypto Prices:")
    prices = get_crypto_prices()
    print(f"  Source: {prices.get('source')}")
    for sym, price in list(prices.get("prices", {}).items())[:5]:
        print(f"    {sym}: ${price:,.2f}")
    
    print("\nSentiment:")
    sentiment = get_sentiment()
    if sentiment:
        print(f"  Value: {sentiment.get('value')} ({sentiment.get('classification')})")
        print(f"  Signal: {sentiment.get('signal')}")
    
    print("\nForex Rates (USD):")
    forex = get_forex_rates("USD")
    print(f"  Source: {forex.get('source')}")
    rates = forex.get("rates", {}).get("rates", {})
    for cur, rate in list(rates.items())[:5]:
        print(f"    USD/{cur}: {rate}")
