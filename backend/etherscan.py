"""
Etherscan V2 — on-chain data
Free API key required. Ethereum blockchain data.
Note: Free tier limited to 1,000 records per request, 5 calls/sec.
"""

import requests
import config
from datetime import datetime
from typing import Optional

BASE_URL = "https://api.etherscan.io/v2/api"
CHAIN_ID = "1"  # Ethereum mainnet


def _get(params: dict) -> dict:
    """Make authenticated request to Etherscan V2."""
    if not config.ETHERSCAN_API_KEY:
        return {"status": "0", "message": "API key not configured"}
    
    params["apikey"] = config.ETHERSCAN_API_KEY
    params["chainid"] = CHAIN_ID
    
    try:
        r = requests.get(BASE_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[Etherscan] request error: {e}")
        return {"status": "0", "message": str(e)}


def get_eth_balance(address: str) -> dict:
    """Get ETH balance for an address."""
    data = _get({
        "module": "account",
        "action": "balance",
        "address": address,
        "tag": "latest"
    })
    
    if data.get("status") == "1":
        balance_wei = int(data["result"])
        return {
            "address": address,
            "balance_wei": balance_wei,
            "balance_eth": balance_wei / 1e18,
        }
    return None


def get_token_balance(address: str, contract_address: str) -> dict:
    """Get ERC-20 token balance for an address."""
    data = _get({
        "module": "account",
        "action": "tokenbalance",
        "address": address,
        "contractaddress": contract_address,
        "tag": "latest"
    })
    
    if data.get("status") == "1":
        balance = int(data["result"])
        return {
            "address": address,
            "contract": contract_address,
            "balance": balance,
        }
    return None


def get_transactions(address: str, start_block: int = 0, 
                       end_block: int = 99999999, limit: int = 100) -> list:
    """Get normal transactions for an address."""
    data = _get({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": end_block,
        "page": 1,
        "offset": min(limit, 1000),  # Free tier max
        "sort": "desc"
    })
    
    if data.get("status") == "1" and data.get("result"):
        return [
            {
                "hash": tx.get("hash"),
                "block": tx.get("blockNumber"),
                "timestamp": datetime.fromtimestamp(int(tx.get("timeStamp", 0))).isoformat(),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "value_eth": int(tx.get("value", 0)) / 1e18,
                "gas_used": tx.get("gasUsed"),
                "gas_price": tx.get("gasPrice"),
                "is_error": tx.get("isError") == "1",
            }
            for tx in data["result"]
        ]
    return []


def get_internal_transactions(address: str, limit: int = 100) -> list:
    """Get internal transactions (contract calls)."""
    data = _get({
        "module": "account",
        "action": "txlistinternal",
        "address": address,
        "page": 1,
        "offset": min(limit, 1000),
        "sort": "desc"
    })
    
    if data.get("status") == "1" and data.get("result"):
        return [
            {
                "hash": tx.get("hash"),
                "block": tx.get("blockNumber"),
                "timestamp": datetime.fromtimestamp(int(tx.get("timeStamp", 0))).isoformat(),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "value_eth": int(tx.get("value", 0)) / 1e18,
                "type": tx.get("type"),
            }
            for tx in data["result"]
        ]
    return []


def get_token_transfers(address: str, limit: int = 100) -> list:
    """Get ERC-20 token transfer events."""
    data = _get({
        "module": "account",
        "action": "tokentx",
        "address": address,
        "page": 1,
        "offset": min(limit, 1000),
        "sort": "desc"
    })
    
    if data.get("status") == "1" and data.get("result"):
        return [
            {
                "hash": tx.get("hash"),
                "block": tx.get("blockNumber"),
                "timestamp": datetime.fromtimestamp(int(tx.get("timeStamp", 0))).isoformat(),
                "from": tx.get("from"),
                "to": tx.get("to"),
                "contract": tx.get("contractAddress"),
                "token_name": tx.get("tokenName"),
                "token_symbol": tx.get("tokenSymbol"),
                "value": int(tx.get("value", 0)),
                "decimals": int(tx.get("tokenDecimal", 18)),
            }
            for tx in data["result"]
        ]
    return []


def get_gas_oracle() -> dict:
    """Get current gas prices."""
    data = _get({
        "module": "gastracker",
        "action": "gasoracle"
    })
    
    if data.get("status") == "1" and data.get("result"):
        result = data["result"]
        return {
            "safe_gas": result.get("SafeGasPrice"),
            "propose_gas": result.get("ProposeGasPrice"),
            "fast_gas": result.get("FastGasPrice"),
            "suggested_base_fee": result.get("suggestedBaseFee"),
        }
    return None


def get_eth_price() -> dict:
    """Get ETH price."""
    data = _get({
        "module": "stats",
        "action": "ethprice"
    })
    
    if data.get("status") == "1" and data.get("result"):
        result = data["result"]
        return {
            "eth_btc": result.get("ethbtc"),
            "eth_usd": result.get("ethusd"),
            "timestamp": datetime.fromtimestamp(int(result.get("ethbtc_timestamp", 0))).isoformat(),
        }
    return None


def get_on_chain_snapshot(address: str = None) -> dict:
    """Get comprehensive on-chain snapshot."""
    snapshot = {
        "source": "etherscan.io",
        "timestamp": datetime.now().isoformat(),
    }
    
    # Get gas prices
    gas = get_gas_oracle()
    if gas:
        snapshot["gas"] = gas
    
    # Get ETH price
    price = get_eth_price()
    if price:
        snapshot["eth_price"] = price
    
    # Get address data if provided
    if address:
        balance = get_eth_balance(address)
        if balance:
            snapshot["address_balance"] = balance
        
        txs = get_transactions(address, limit=10)
        if txs:
            snapshot["recent_transactions"] = txs
    
    return snapshot


if __name__ == "__main__":
    print("=== Etherscan V2 Test ===")
    print(f"API Key configured: {bool(config.ETHERSCAN_API_KEY)}")
    
    print("\nETH Price:")
    price = get_eth_price()
    if price:
        print(f"  ETH/USD: ${price['eth_usd']}")
        print(f"  ETH/BTC: {price['eth_btc']}")
    
    print("\nGas Prices:")
    gas = get_gas_oracle()
    if gas:
        print(f"  Safe: {gas['safe_gas']} Gwei")
        print(f"  Propose: {gas['propose_gas']} Gwei")
        print(f"  Fast: {gas['fast_gas']} Gwei")
    
    # Test with Vitalik's address
    test_address = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
    print(f"\nAddress {test_address[:10]}...:")
    balance = get_eth_balance(test_address)
    if balance:
        print(f"  Balance: {balance['balance_eth']:.4f} ETH")
