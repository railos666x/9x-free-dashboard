"""
Mempool.space — BTC mempool/fee data
No key required. Bitcoin network activity signals.
"""

import requests
from datetime import datetime
from typing import Optional

BASE_URL = "https://mempool.space/api"


def get_mempool() -> dict:
    """Get current mempool state."""
    try:
        r = requests.get(f"{BASE_URL}/mempool", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        return {
            "count": data.get("count", 0),  # Number of transactions
            "vsize": data.get("vsize", 0),  # Total virtual size
            "total_fee": data.get("total_fee", 0),
            "fee_histogram": data.get("fee_histogram", []),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[Mempool] mempool error: {e}")
    return None


def get_recommended_fees() -> dict:
    """Get recommended fees for different priority levels."""
    try:
        r = requests.get(f"{BASE_URL}/v1/fees/recommended", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        return {
            "fastest": data.get("fastestFee", 0),  # ~10 min
            "half_hour": data.get("halfHourFee", 0),  # ~30 min
            "hour": data.get("hourFee", 0),  # ~1 hour
            "economy": data.get("economyFee", 0),  # Next block
            "minimum": data.get("minimumFee", 0),  # Minimum relay
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"[Mempool] fees error: {e}")
    return None


def get_blocks(tip_height: int = None, limit: int = 10) -> list:
    """Get recent blocks."""
    url = f"{BASE_URL}/v1/blocks"
    if tip_height:
        url = f"{BASE_URL}/v1/blocks/{tip_height}"
    
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        blocks = r.json()
        
        return [
            {
                "height": b.get("height"),
                "hash": b.get("id"),
                "timestamp": b.get("timestamp"),
                "tx_count": b.get("tx_count"),
                "size": b.get("size"),
                "difficulty": b.get("difficulty"),
            }
            for b in blocks[:limit]
        ]
    except Exception as e:
        print(f"[Mempool] blocks error: {e}")
    return []


def get_block_height() -> int:
    """Get current block height."""
    try:
        r = requests.get(f"{BASE_URL}/blocks/tip/height", timeout=15)
        r.raise_for_status()
        return int(r.text)
    except Exception as e:
        print(f"[Mempool] height error: {e}")
    return None


def get_network_hashrate() -> dict:
    """Get network hashrate (last 3 months)."""
    try:
        r = requests.get(f"{BASE_URL}/v1/mining/hashrate/3m", timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if "currentHashrate" in data:
            return {
                "hashrate": data.get("currentHashrate"),
                "difficulty": data.get("currentDifficulty"),
                "timestamp": datetime.now().isoformat(),
            }
    except Exception as e:
        print(f"[Mempool] hashrate error: {e}")
    return None


def get_btc_network_status() -> dict:
    """Get comprehensive BTC network status."""
    mempool = get_mempool()
    fees = get_recommended_fees()
    height = get_block_height()
    
    # Calculate network congestion score (0-100)
    congestion = 0
    if mempool and mempool.get("count"):
        # Normalize: <10k txs = low, >100k txs = high
        congestion = min(100, (mempool["count"] / 100000) * 100)
    
    return {
        "source": "mempool.space",
        "block_height": height,
        "mempool": mempool,
        "fees": fees,
        "congestion_score": round(congestion, 1),
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    print("=== Mempool.space Test ===")
    
    print("\nBTC Network Status:")
    status = get_btc_network_status()
    if status:
        print(f"  Block Height: {status['block_height']}")
        print(f"  Congestion: {status['congestion_score']}%")
        if status.get("mempool"):
            m = status["mempool"]
            print(f"  Mempool: {m['count']} txs, {m['vsize']/1e6:.2f} MB")
        if status.get("fees"):
            f = status["fees"]
            print(f"  Fees (sat/vB):")
            print(f"    Fastest: {f['fastest']}")
            print(f"    30 min: {f['half_hour']}")
            print(f"    1 hour: {f['hour']}")
            print(f"    Economy: {f['economy']}")
