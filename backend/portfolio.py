"""
cryptoautomation — Portfolio module.

Two modes:
  1. **demo** (default) — a sample portfolio of real coins, priced with
     LIVE market data from the candles table. Enough to make the dashboard
     fully functional out of the box.
  2. **exchange** — when the user connects a Binance account via the
     dashboard's "Connect exchange" modal, their real spot balances are
     fetched (signed API calls) and priced the same way.

API keys are stored locally in data/exchange_keys.json (never committed,
never sent anywhere except Binance itself). Best-effort: any error falls
back to demo mode.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import requests

import config
import database

KEYS_FILE = config.DATA_DIR / "exchange_keys.json"

# Demo holdings (amounts per symbol) — a representative small portfolio.
DEMO_HOLDINGS = {
    "BTC/USDT": 0.25,
    "ETH/USDT": 3.5,
    "SOL/USDT": 12.0,
    "BNB/USDT": 5.0,
    "DOGE/USDT": 8000.0,
}

# Colors for the distribution bar (stable per symbol).
SYMBOL_COLORS = {
    "BTC/USDT": "#f7931a", "ETH/USDT": "#627eea", "SOL/USDT": "#9945ff",
    "BNB/USDT": "#f3ba2f", "DOGE/USDT": "#c2a633", "XRP/USDT": "#25a768",
    "ADA/USDT": "#0033ad", "LTC/USDT": "#bfbbbb", "DOT/USDT": "#e6007a",
    "LINK/USDT": "#2a5ada", "USDT": "#26a17b",
}

BINANCE_ACCOUNT = "https://api.binance.com/api/v3/account"
BINANCE_PRICE = "https://api.binance.com/api/v3/ticker/price"


# ──────────────────────────────────────────────────────────────────────
# Keys
# ──────────────────────────────────────────────────────────────────────

def load_keys() -> dict | None:
    try:
        if KEYS_FILE.exists():
            return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_keys(name: str, api_key: str, api_secret: str) -> dict:
    data = {"name": name, "api_key": api_key, "api_secret": api_secret,
            "connected_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    KEYS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def clear_keys() -> None:
    if KEYS_FILE.exists():
        KEYS_FILE.unlink()


# ──────────────────────────────────────────────────────────────────────
# Prices
# ──────────────────────────────────────────────────────────────────────

def _latest_price(conn, symbol: str, interval: str = "1h") -> float | None:
    """Latest close for a symbol from the candles table."""
    df = database.load_candles(conn, symbol, interval, limit=2)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])


def _latest_prices(conn, symbols: list[str]) -> dict[str, float]:
    out = {}
    for s in symbols:
        p = _latest_price(conn, s)
        if p:
            out[s] = p
    return out


# ──────────────────────────────────────────────────────────────────────
# Binance exchange balances (signed)
# ──────────────────────────────────────────────────────────────────────

def _signed_request(keys: dict) -> dict:
    """Fetch non-zero spot balances via a signed Binance account call."""
    api_key, api_secret = keys["api_key"], keys["api_secret"]
    ts = int(time.time() * 1000)
    query = f"timestamp={ts}&recvWindow=10000"
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    resp = requests.get(
        BINANCE_ACCOUNT,
        params={"timestamp": ts, "recvWindow": 10000, "signature": sig},
        headers={"X-MBX-APIKEY": api_key},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    balances = {
        b["asset"]: float(b["free"]) + float(b["locked"])
        for b in data.get("balances", [])
        if float(b["free"]) + float(b["locked"]) > 0
    }
    # Map assets to our symbol format and price the stablecoin/quote ones.
    holdings = {}
    for asset, amount in balances.items():
        if amount <= 0:
            continue
        symbol = f"{asset}/USDT"
        price = _binance_price(symbol) or _binance_price(f"{asset}/BTC")
        if price and asset != "USDT":
            holdings[symbol] = amount
        elif asset == "USDT":
            holdings["USDT"] = amount
    return holdings


def _binance_price(symbol: str) -> float | None:
    try:
        r = requests.get(BINANCE_PRICE, params={"symbol": symbol.replace("/", "")},
                         timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────
# Portfolio assembly
# ──────────────────────────────────────────────────────────────────────

def get_portfolio(conn) -> dict:
    """
    Full portfolio snapshot: mode, holdings priced in USD, distribution,
    and a daily value history (from real 1d candles).
    """
    keys = load_keys()
    mode = "demo"
    holdings = dict(DEMO_HOLDINGS)
    if keys and keys.get("api_key"):
        try:
            ex = _signed_request(keys)
            if ex:
                holdings = ex
                mode = "exchange"
        except Exception:
            pass  # fall back to demo silently

    prices = _latest_prices(conn, [s for s in holdings if s != "USDT"])
    items = []
    total = 0.0
    for symbol, amount in holdings.items():
        if symbol == "USDT":
            value = amount
            price = 1.0
        else:
            price = prices.get(symbol, 0.0)
            value = amount * price
        total += value
        items.append({
            "symbol": symbol,
            "name": symbol.split("/")[0],
            "amount": round(amount, 6),
            "price": round(price, 6),
            "value": round(value, 2),
        })

    items.sort(key=lambda x: -x["value"])
    distribution = [
        {"symbol": i["symbol"], "value": round(i["value"], 2),
         "pct": round(i["value"] / total * 100, 1) if total > 0 else 0.0,
         "color": SYMBOL_COLORS.get(i["symbol"], "#4c9aff")}
        for i in items if i["value"] > 0
    ]

    return {
        "mode": mode,
        "exchange_name": keys.get("name") if keys else None,
        "total_usd": round(total, 2),
        "items": items,
        "distribution": distribution,
        "history": _value_history(conn, holdings, days=90),
    }


def _value_history(conn, holdings: dict, days: int = 90) -> list[dict]:
    """
    Daily portfolio value over the last `days`, computed from real 1d
    candles of every holding (aligned by date). USDT is constant 1.0.
    """
    frames = {}
    for symbol in holdings:
        if symbol == "USDT":
            continue
        df = database.load_candles(conn, symbol, "1d", limit=days + 10)
        if not df.empty:
            frames[symbol] = df[["open_time", "close"]]
    if not frames:
        return []

    # Align on the union of dates, oldest first.
    all_days = set()
    for df in frames.values():
        all_days.update(int(t) for t in df["open_time"])
    all_days = sorted(all_days)[-days:]

    usdt_amount = holdings.get("USDT", 0.0)
    history = []
    for t in all_days:
        value = usdt_amount
        for symbol, df in frames.items():
            row = df[df["open_time"] == t]
            if not row.empty:
                value += holdings[symbol] * float(row["close"].iloc[0])
        history.append({
            "time": t,
            "date": time.strftime("%Y-%m-%d", time.gmtime(t / 1000)),
            "value": round(value, 2),
        })
    return history
