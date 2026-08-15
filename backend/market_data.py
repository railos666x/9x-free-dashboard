"""
cryptoautomation — Secondary market-data feed (cross-source verification).

The primary feed is Binance (collector.py). This module provides an
INDEPENDENT second feed — Alpaca's free market-data API — so the bot can
cross-check prices before acting (verification.cross_source_ok). Alpaca
crypto quotes require API keys; without them every call returns None and
the cross-check is skipped silently (the bot still runs on Binance data).

No SDK needed — plain REST, like the rest of the codebase.
"""

from __future__ import annotations

import requests

import config


def alpaca_last_price(symbol: str) -> float | None:
    """
    Latest trade price for a symbol from Alpaca's crypto data API.
    Returns None when keys are missing, the symbol isn't available, or on
    any network error — the cross-check must never block the bot.

    Symbol mapping: BTC/USDT → BTC/USD (Alpaca crypto quotes in USD);
    stock tickers pass through unchanged. Uses the v1beta3 crypto feed
    (the old v1beta2 /latest/trade endpoint was retired by Alpaca).
    """
    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        return None
    # v1beta3 keeps the pair format (BTC/USD) in both request and response.
    sym = symbol
    if "/" in symbol:
        base, quote = symbol.split("/")
        sym = f"{base.upper()}/{'USD' if quote.upper() == 'USDT' else quote.upper()}"
    try:
        resp = requests.get(
            "https://data.alpaca.markets/v1beta3/crypto/us/latest/trades",
            params={"symbols": sym},
            headers={"APCA-API-KEY-ID": config.ALPACA_API_KEY,
                     "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        trades = resp.json().get("trades", {})
        trade = trades.get(sym) or {}
        price = trade.get("p")
        return float(price) if price else None
    except Exception:
        return None
