"""
cryptoautomation — Portfolio module.

Three modes (first that works wins):
  1. **alpaca** — when EXECUTION_BROKER=alpaca and Alpaca keys are set in
     .env, the paper/live Alpaca account is the trading account: equity,
     cash, buying power and open positions are fetched and priced.
  2. **exchange** — when the user connects a Binance account via the
     dashboard's "Connect exchange" modal, their real spot balances are
     fetched (signed API calls) and priced the same way.
  3. **demo** (fallback) — a sample portfolio of real coins, priced with
     LIVE market data from the candles table.

Binance API keys are stored locally in data/exchange_keys.json (never
committed, never sent anywhere except Binance itself). Alpaca keys live in
.env (shared with the execution layer). Best-effort: any error falls back
to the next mode.
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
# MT5/Exness account + positions (the execution/trading account)
# ──────────────────────────────────────────────────────────────────────

def _mt5_account() -> dict | None:
    """Snapshot the MT5/Exness trading account: balance, equity, free
    margin and open positions (mapped to our BTC/USDT symbol format).
    Returns None when the broker isn't configured / terminal is down."""
    try:
        from execution import MT5Broker, BrokerError
        broker = MT5Broker()
        info = broker.account_info()
        positions = broker.fetch_positions()
    except Exception:
        return None

    holdings = {}
    unreal = 0.0
    pos_map = {}   # symbol -> {qty, side, entry, current, unrealized_pl} (legacy, single position per symbol)
    positions_list = []  # list of all positions (supports multiple per symbol)
    for p in positions or []:
        sym = str(p.get("symbol") or "")
        qty = float(p.get("qty") or 0)
        if abs(qty) < 1e-9:
            continue
        # MT5 name (EURUSD, XAUUSD, BTCUSD…) → our BTC/USDT style.
        ours = _mt5_to_ours(sym)
        if not ours:
            continue
        holdings[ours] = holdings.get(ours, 0) + abs(qty)
        entry = p.get("entry")
        current = p.get("current")
        profit = p.get("profit")
        # Calculate unrealized PnL if we have entry and current
        if entry and current:
            side = str(p.get("side") or "long")
            if side == "long":
                calc_unreal = (current - entry) * abs(qty)
            else:
                calc_unreal = (entry - current) * abs(qty)
        elif profit is not None:
            calc_unreal = profit
        else:
            calc_unreal = None
        pos_data = {
            "symbol": ours,
            "qty": abs(qty),
            "side": str(p.get("side") or "long"),
            "entry": entry,
            "current": current,
            "unrealized_pl": calc_unreal,
        }
        positions_list.append(pos_data)
        # Legacy pos_map for backward compatibility (last position wins)
        pos_map[ours] = pos_data
    balance = float(info.get("balance") or 0)
    equity = float(info.get("equity") or 0)
    if equity > 0:
        holdings["USDT"] = round(equity, 2)  # pseudo-holding: account value priced 1.0
    if not holdings and balance <= 0:
        return None
    return {
        "holdings": holdings,
        "positions": pos_map,
        "positions_list": positions_list,  # all positions (supports multiple per symbol)
        "unrealized_pl": unreal,
        "equity": equity or balance,
        "cash": float(info.get("margin_free") or 0),
        "buying_power": float(info.get("margin_free") or 0),
        "balance": balance,
        "account_number": "",
        "paper": True,
    }


_MT5_TO_OURS = {v: k for k, v in config.EXNESS_MT5_SYMBOLS.items()}

def _mt5_to_ours(sym: str) -> str | None:
    """MT5 symbol name → our BTC/USDT-style symbol (or None for unknown)."""
    if sym in _MT5_TO_OURS:
        return _MT5_TO_OURS[sym]
    # MT5 base-quote pairs (EURUSD, BTCUSD, XAUUSD…): base/QUOTE → base/USDT
    # is wrong for FX, but the dashboard prices from our candle table which
    # stores EXNESS_SYMBOLS; keep the identity so lookups don't miss.
    return sym


# ──────────────────────────────────────────────────────────────────────
# Alpaca account + positions (the execution/trading account)
# ──────────────────────────────────────────────────────────────────────

def _alpaca_account() -> dict | None:
    """
    Snapshot the Alpaca trading account: equity, cash, buying power and
    open positions (mapped to our BTC/USDT symbol format). Returns None
    when keys are missing or the account call fails.
    """
    if not (config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY):
        return None
    base = config.ALPACA_PAPER_URL if config.ALPACA_PAPER else config.ALPACA_LIVE_URL
    headers = {"APCA-API-KEY-ID": config.ALPACA_API_KEY,
               "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY}
    try:
        acc = requests.get(f"{base}/v2/account", headers=headers, timeout=15).json()
        pos = requests.get(f"{base}/v2/positions", headers=headers, timeout=15).json()
    except Exception:
        return None

    holdings = {}
    unreal = 0.0
    positions = {}   # symbol -> {qty, entry, current, market_value, unrealized_pl}
    for p in pos or []:
        sym = str(p.get("symbol") or "")
        qty = float(p.get("qty") or 0)
        if abs(qty) < 1e-9:
            continue
        if sym.endswith("USD") and len(sym) > 3:
            sym = sym[:-3] + "/USDT"   # BTCUSD -> BTC/USDT
        else:
            continue  # non-crypto (stocks etc.) — out of scope for this dashboard
        holdings[sym] = abs(qty)
        try:
            upl = float(p.get("unrealized_pl") or 0)
            unreal += upl
        except (TypeError, ValueError):
            upl = 0.0
        positions[sym] = {
            "qty": abs(qty),
            "entry": float(p.get("entry_price") or 0) or None,
            "current": float(p.get("current_price") or 0) or None,
            "market_value": float(p.get("market_value") or 0),
            "unrealized_pl": round(upl, 2),
        }
    cash = float(acc.get("cash") or 0)
    if cash > 0:
        holdings["USDT"] = round(cash, 2)  # stablecoin pseudo-holding (priced 1.0)
    if not holdings:
        return None
    # Account-level PnL: equity vs the last recorded equity. Alpaca paper
    # crypto does NOT populate portfolio/history (returns zeros), so this
    # equity delta is the only broker-side number that captures fees too
    # (Alpaca's order payload has no filled_fees for crypto — the journal
    # records 0 and would otherwise understate costs).
    equity = float(acc.get("equity") or 0)
    last_equity = float(acc.get("last_equity") or 0)
    account_pnl = round(equity - last_equity, 2) if last_equity else None
    return {
        "holdings": holdings,
        "positions": positions,
        "equity": equity,
        "cash": cash,
        "buying_power": float(acc.get("buying_power") or 0),
        "unrealized_pl": round(unreal, 2),
        "account_pnl": account_pnl,
        "account_number": acc.get("account_number"),
        "paper": config.ALPACA_PAPER,
    }


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

    Mode priority: alpaca (the connected trading account) → binance
    exchange (connect-exchange modal) → demo.
    """
    keys = load_keys()
    mode = "demo"
    holdings = dict(DEMO_HOLDINGS)
    account_meta = {}

    # 1. MT5/Exness trading account (execution broker).
    if config.EXECUTION_BROKER == "mt5":
        try:
            mt = _mt5_account()
        except Exception:
            mt = None
        if mt:
            holdings = mt["holdings"]
            mode = "mt5"
            account_meta = mt

    # 2. Alpaca trading account (execution broker).
    if mode == "demo" and config.EXECUTION_BROKER == "alpaca":
        try:
            alp = _alpaca_account()
        except Exception:
            alp = None
        if alp:
            holdings = alp["holdings"]
            mode = "alpaca"
            account_meta = alp

    # 3. Binance exchange (explicitly connected via the modal).
    if mode == "demo" and keys and keys.get("api_key"):
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

    result = {
        "mode": mode,
        "exchange_name": ("MT5/Exness (demo)" if mode == "mt5"
                           else keys.get("name") if keys else None),
        "total_usd": round(total, 2),
        "items": items,
        "distribution": distribution,
        "history": _value_history(conn, holdings, days=90),
    }
    if mode == "alpaca":
        result.update({
            "exchange_name": f"Alpaca {'paper' if account_meta.get('paper') else 'live'}",
            "equity": round(account_meta.get("equity") or 0, 2),
            "cash": round(account_meta.get("cash") or 0, 2),
            "buying_power": round(account_meta.get("buying_power") or 0, 2),
            "unrealized_pl": account_meta.get("unrealized_pl") or 0.0,
            "account_pnl": account_meta.get("account_pnl"),
            "account_number": account_meta.get("account_number"),
            "total_usd": round(account_meta.get("equity") or total, 2),
        })
    elif mode == "mt5":
        result.update({
            "exchange_name": "MT5/Exness (demo)",
            "equity": round(account_meta.get("equity") or 0, 2),
            "cash": round(account_meta.get("cash") or 0, 2),
            "buying_power": round(account_meta.get("buying_power") or 0, 2),
            "unrealized_pl": account_meta.get("unrealized_pl") or 0.0,
            "account_pnl": None,
            "account_number": account_meta.get("account_number") or "",
            "total_usd": round(account_meta.get("equity") or total, 2),
        })
    return result


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
