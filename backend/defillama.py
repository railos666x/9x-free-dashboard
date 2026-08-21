"""
DefiLlama — DeFi TVL, stablecoin supply, yields
No auth required, no rate limit for normal traffic.
"""

import requests
from datetime import datetime
from typing import Optional

BASE_URL = "https://api.llama.fi"


def get_protocols_tvl() -> list:
    """Get TVL for all protocols."""
    try:
        r = requests.get(f"{BASE_URL}/protocols", timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[DefiLlama] protocols error: {e}")
    return []


def get_protocol_tvl(protocol: str) -> list:
    """Get historical TVL for a specific protocol."""
    try:
        r = requests.get(f"{BASE_URL}/protocol/{protocol}", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[DefiLlama] protocol_tvl error: {e}")
    return []


def get_chains_tvl() -> list:
    """Get TVL for all chains."""
    try:
        r = requests.get(f"{BASE_URL}/chains", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[DefiLlama] chains error: {e}")
    return []


def get_stablecoins() -> dict:
    """Get stablecoin data (supply, market cap, etc.)."""
    try:
        r = requests.get(f"{BASE_URL}/stablecoins?includePrices=true", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "peggedAssets" in data:
            total_mcap = sum(s.get("circulating", {}).get("peggedUSD", 0) 
                           for s in data["peggedAssets"])
            return {
                "total_market_cap": total_mcap,
                "count": len(data["peggedAssets"]),
                "top_stablecoins": [
                    {
                        "name": s.get("name"),
                        "symbol": s.get("symbol"),
                        "mcap": s.get("circulating", {}).get("peggedUSD", 0),
                    }
                    for s in sorted(data["peggedAssets"], 
                                   key=lambda x: x.get("circulating", {}).get("peggedUSD", 0),
                                   reverse=True)[:10]
                ],
                "timestamp": datetime.now().isoformat(),
            }
    except Exception as e:
        print(f"[DefiLlama] stablecoins error: {e}")
    return None


def get_yields() -> list:
    """Get DeFi yields/pools."""
    try:
        r = requests.get(f"{BASE_URL}/yields/pools", timeout=15)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"[DefiLlama] yields error: {e}")
    return []


def get_top_yields(chain: str = None, limit: int = 20) -> list:
    """Get top yield pools, optionally filtered by chain."""
    pools = get_yields()
    if chain:
        pools = [p for p in pools if p.get("chain", "").lower() == chain.lower()]
    
    # Sort by APY
    pools = sorted(pools, key=lambda x: x.get("apy", 0) or 0, reverse=True)
    
    return [
        {
            "chain": p.get("chain"),
            "project": p.get("project"),
            "symbol": p.get("symbol"),
            "tvl_usd": p.get("tvlUsd", 0),
            "apy": p.get("apy", 0),
        }
        for p in pools[:limit]
    ]


def get_defi_snapshot() -> dict:
    """Get DeFi ecosystem snapshot."""
    chains = get_chains_tvl()
    stablecoins = get_stablecoins()
    
    total_tvl = sum(c.get("tvl", 0) for c in chains) if chains else 0
    
    return {
        "source": "defillama.com",
        "total_tvl": total_tvl,
        "chains_count": len(chains),
        "stablecoins": stablecoins,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== DefiLlama Test ===")
    
    print("\nDeFi Snapshot:")
    snapshot = get_defi_snapshot()
    if snapshot:
        print(f"  Total TVL: ${snapshot['total_tvl']:,.0f}")
        print(f"  Chains: {snapshot['chains_count']}")
        if snapshot.get("stablecoins"):
            sc = snapshot["stablecoins"]
            print(f"  Stablecoin Market Cap: ${sc['total_market_cap']:,.0f}")
            print(f"  Top Stablecoins:")
            for s in sc["top_stablecoins"][:5]:
                print(f"    {s['symbol']}: ${s['mcap']:,.0f}")
    
    print("\nTop Yield Pools:")
    yields = get_top_yields(limit=5)
    for y in yields:
        print(f"  {y['chain']}/{y['project']} {y['symbol']}: {y['apy']:.2f}% APY (TVL: ${y['tvl_usd']:,.0f})")
