"""
cryptoautomation — Execution layer (the ONLY module that touches a broker).

Design:
  - One idempotent entry point: `execute_intent(conn, intent)`.
  - Idempotency by `intent.intent_hash`: the `orders` table has a UNIQUE
    constraint on intent_hash, so the same agreed signal can never place
    a second order — this is what stops two signal sources from producing
    duplicate or conflicting orders.
  - Broker-agnostic gateway: `get_broker()` returns the configured broker
    (paper | alpaca | binance). Paper fills offline from our candles +
    the risk model (slippage + fees), so the whole loop works with zero
    keys. Alpaca uses plain REST (no SDK dependency). Binance reuses the
    signed-request pattern from portfolio.py.
  - Every order is journaled in the `orders` table (trade journal).

Sizing: risk-based via risk.position_size (risk % of equity, capped by
Kelly). Equity is the Alpaca account when that's the broker (so a real
paper/live account is sized correctly), otherwise the journal's paper
equity. An explicit `qty` overrides sizing (e.g. a live broker account
that sizes itself).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import time
from datetime import datetime, timezone
from decimal import ROUND_FLOOR

import requests

import config
import database
import risk

# Token registry for cross-venue symbol resolution
try:
    from . import token_registry
except ImportError:
    import token_registry

# Binance Spot Demo: https://testnet.binance.vision
# Binance Spot Live: https://api.binance.com
BINANCE_BASE = "https://testnet.binance.vision"
BINANCE_ORDER = f"{BINANCE_BASE}/api/v3/order"
BINANCE_ACCOUNT = f"{BINANCE_BASE}/api/v3/account"
BINANCE_OPEN_ORDERS = f"{BINANCE_BASE}/api/v3/openOrders"

# Alpaca's crypto universe is small (~30 assets) vs our 87 Binance symbols;
# orders for anything else 422 at the broker. The tradable set is fetched
# once and cached for an hour — the universe rarely changes.
_assets_cache = {"at": 0.0, "tradable": None}
ASSETS_CACHE_TTL = 3600.0

# Account equity used for sizing when the broker is Alpaca (paper or live):
# the LOCAL journal equity (~$10k INITIAL_CAPITAL + realized PnL) massively
# under-sizes a real Alpaca account (e.g. a $100k paper account would trade
# ~$1.5k positions instead of ~$15k). Fetched once per minute, best-effort;
# falls back to the journal equity on any failure.
#
# IMPORTANT: sizing is capped at AVAILABLE CASH, not equity. Equity is
# ~$99k while most of it sits in open positions (cash ~$14k) — sizing a
# burst of concurrent entries on full equity over-commits the account and
# every order past the cash 403s "insufficient balance" (seen live 2026-08-17:
# 7 of 10 RSI(2) longs rejected in one cycle). min(equity, cash) keeps each
# position within what the account can actually fund.
_equity_cache = {"at": 0.0, "equity": None}
EQUITY_CACHE_TTL = 60.0


def _broker_equity(conn) -> float:
    """The base the risk model sizes against: min(equity, available cash)
    for Alpaca (cash is the binding constraint) and min(equity, free margin
    × 0.8) for MT5/Exness; else the journal equity."""
    try:
        broker = get_broker()
    except BrokerError:
        return database.paper_equity(conn)
    if broker.name not in ("alpaca", "mt5"):
        return database.paper_equity(conn)
    now = time.time()
    if _equity_cache["equity"] and now - _equity_cache["at"] < EQUITY_CACHE_TTL:
        return _equity_cache["equity"]
    sizing = None
    try:
        if broker.name == "alpaca":
            resp = requests.get(f"{broker.base}/v2/account", headers=broker.headers,
                                timeout=15)
            resp.raise_for_status()
            acct = resp.json()
            equity = float(acct.get("equity") or 0)
            cash = float(acct.get("cash") or equity or 0)
            sizing = min(equity, cash * 0.8)
        else:  # mt5 — free margin already nets used margin, so it IS the
            # headroom (no extra shrink): min-lot gold would otherwise zero
            # out of the sizing base on a demo account.
            info = broker.account_info()
            sizing = min(info["equity"], info["margin_free"])
    except Exception:
        pass
    if not sizing:
        sizing = database.paper_equity(conn)
    _equity_cache.update({"at": now, "equity": sizing})
    return sizing


def broker_supported_symbols() -> set[str]:
    """Our symbol format (BTC/USDT) for assets the configured broker can
    actually trade. The offline paper broker trades everything; Alpaca only
    a subset. Safe by default: if the metadata call fails we return an EMPTY
    set, so the bot holds instead of firing orders the broker will reject.
    A held signal is harmless — a rejected order is permanent journal noise."""
    try:
        broker = get_broker()
    except BrokerError:
        return set(config.TRADABLE_UNIVERSE)
    if broker.name == "mt5":
        # MT5/Exness: exactly the mapped universe — the terminal rejects
        # anything else (and margin rules cap the sizes). No metadata call.
        return set(config.EXNESS_SYMBOLS)
    if broker.name != "alpaca":
        return set(config.TRADABLE_UNIVERSE)
    now = time.time()
    if (_assets_cache["tradable"] is not None
            and now - _assets_cache["at"] < ASSETS_CACHE_TTL):
        return _assets_cache["tradable"]
    tradable = set()
    try:
        resp = requests.get(f"{broker.base}/v2/assets",
                            headers=broker.headers,
                            params={"asset_class": "crypto"}, timeout=15)
        resp.raise_for_status()
        # The /v2/assets API returns "BTC/USD" while order endpoints want
        # "BTCUSD" — normalise by stripping the slash before matching.
        alp = {str(a.get("symbol", "")).replace("/", "")
               for a in resp.json() if a.get("tradable")}
        tradable = {s for s in config.TRADABLE_UNIVERSE
                    if AlpacaBroker._symbol(s) in alp}
        # US-listed spot Bitcoin ETFs pass through unchanged (AlpacaBroker
        # leaves symbols without a quote part alone) — they're equity
        # assets, not crypto, so the crypto asset_class call above can't
        # see them. Inside the try: a metadata failure still holds ALL.
        tradable |= set(config.ETF_SYMBOLS)
    except Exception:
        # Metadata API hiccup (rate limit / timeout / parse) → hold ALL
        # symbols. Ordering an asset Alpaca doesn't list always 422s, and
        # shorting a spot account 403s — the bot must not fire those. The
        # empty result is cached (1h) so we don't hammer a flaky API, and
        # next cycle re-tries automatically.
        tradable = set()
    _assets_cache.update({"at": now, "tradable": tradable})
    return tradable


class BrokerError(Exception):
    """Order placement failed and must not be retried silently."""


# ----------------------------------------------------------------------
# Broker interface
# ----------------------------------------------------------------------
class Broker:
    """Base class. Subclasses implement place/close (pure network/IO)."""
    name = "base"

    def place_market(self, conn, order: dict) -> dict:
        raise NotImplementedError

    def close_market(self, conn, order: dict) -> dict:
        raise NotImplementedError

    def symbol_of(self, symbol: str) -> str:
        """Our display symbol → the broker's own symbol (identity default)."""
        return symbol


class PaperBroker(Broker):
    """Offline broker: fills at the latest candle close, minus/plus
    slippage (LONG pays up, SHORT pays down) and charges the configured
    fee. No network, no keys — ideal default and for tests."""

    name = "paper"

    def _fill(self, conn, order: dict, direction: str) -> dict:
        """Paper fill at latest close, paying slippage into the market.
        direction: 'enter' (pay up on LONG) or 'exit' (pay up on SHORT close)."""
        df = database.load_candles(conn, order["symbol"], order.get("interval", "1h"),
                                   limit=5)
        if df.empty:
            raise BrokerError(f"no candles to price {order['symbol']}")
        price = float(df["close"].iloc[-1])
        slip_bps = risk.slippage_bps(order["symbol"])
        slip = price * slip_bps / 10_000.0
        side = order["side"].upper()
        if direction == "enter":
            fill = price + slip if side == "LONG" else price - slip
        else:  # exit: unwind the position, pay slippage on the way out
            fill = price - slip if side == "LONG" else price + slip
        fees = fill * abs(order["qty"]) * risk.fee_bps() / 10_000.0
        return {
            "status": "filled",
            "fill_price": round(fill, 8),
            "fill_time": datetime.now(timezone.utc).isoformat(),
            "fees": round(fees, 8),
            "broker_ref": f"paper-{int(time.time() * 1000)}",
        }

    def place_market(self, conn, order: dict) -> dict:
        return self._fill(conn, order, "enter")

    def close_market(self, conn, order: dict) -> dict:
        return self._fill(conn, order, "exit")


class AlpacaBroker(Broker):
    """
    Alpaca via plain REST (alpaca-py is NOT required). Paper endpoint by
    default (free, no funding needed); ALPACA_PAPER=false → live endpoint.

    Crypto pairs are quoted in USD on Alpaca: BTC/USDT → BTC/USD. Stocks
    (e.g. AAPL) pass through unchanged when the symbol has no quote part.
    """

    name = "alpaca"

    def __init__(self):
        if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
            raise BrokerError(
                "EXECUTION_BROKER=alpaca but ALPACA_API_KEY / ALPACA_SECRET_KEY "
                "are not set in .env (free paper keys at alpaca.markets)")
        base = config.ALPACA_PAPER_URL if config.ALPACA_PAPER else config.ALPACA_LIVE_URL
        self.base = base
        self.headers = {
            "APCA-API-KEY-ID": config.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _symbol(symbol: str) -> str:
        if "/" not in symbol:
            return symbol.upper()          # stock ticker, e.g. AAPL
        base, quote = symbol.split("/")
        quote = "USD" if quote.upper() == "USDT" else quote.upper()
        return f"{base.upper()}{quote}"     # crypto, e.g. BTC/USDT -> BTCUSD

    def place_market(self, conn, order: dict) -> dict:
        side = "buy" if order["side"].upper() == "LONG" else "sell"
        return self._submit(conn, order, side)

    def close_market(self, conn, order: dict) -> dict:
        side = "sell" if order["side"].upper() == "LONG" else "buy"
        return self._submit(conn, order, side)

    def symbol_of(self, symbol: str) -> str:
        return self._symbol(symbol)

    def fetch_orders(self, limit: int = 100) -> list[dict]:
        """Recent orders (all statuses) for reconciliation."""
        resp = requests.get(f"{self.base}/v2/orders", headers=self.headers,
                            params={"status": "all", "limit": min(limit, 500)},
                            timeout=20)
        resp.raise_for_status()
        out = []
        for d in resp.json():
            out.append({
                "id": d.get("id"),
                # /v2/orders returns "BTC/USD" — normalise to our "BTCUSD"
                # so reconciliation matches _symbol() exactly.
                "symbol": str(d.get("symbol", "")).replace("/", ""),
                "side": d.get("side"),
                "status": d.get("status"),
                "fill_price": d.get("filled_avg_price"),
                "fill_time": d.get("filled_at"),
                "fees": d.get("filled_fees"),
                "created_at": d.get("created_at"),
            })
        return out

    def fetch_positions(self) -> list[dict]:
        """Open positions: [{symbol, qty, side}]. Empty when flat."""
        resp = requests.get(f"{self.base}/v2/positions", headers=self.headers,
                            timeout=20)
        resp.raise_for_status()
        out = []
        for p in resp.json():
            out.append({
                "symbol": p.get("symbol"),
                "qty": float(p.get("qty") or 0),
                "side": p.get("side"),
            })
        return out

    def _submit(self, conn, order: dict, side: str) -> dict:
        client_oid = f"ca-{int(time.time() * 1000)}"
        payload = {
            "symbol": self._symbol(order["symbol"]),
            "qty": str(abs(order["qty"])),
            "side": side,
            "type": "market",
            "time_in_force": "ioc",
            "client_order_id": client_oid,
        }
        resp = requests.post(f"{self.base}/v2/orders", headers=self.headers,
                             json=payload, timeout=20)
        if resp.status_code >= 400:
            raise BrokerError(f"alpaca {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        # Alpaca crypto IOC can respond async (pending_new / accepted) and
        # occasionally without the order id — re-fetch by id, falling back
        # to client_order_id, so the journal always gets the real id, fill
        # and status (reconciliation matches on broker_ref).
        oid = data.get("id")
        if data.get("status") in ("pending_new", "accepted", "new", "open")\
                or not oid:
            try:
                if oid:
                    o2 = requests.get(f"{self.base}/v2/orders/{oid}",
                                      headers=self.headers, timeout=15).json()
                else:
                    o2 = requests.get(
                        f"{self.base}/v2/orders:by_client_order_id",
                        headers=self.headers,
                        params={"client_order_id": client_oid},
                        timeout=15).json()
                if o2 and isinstance(o2, dict) and o2.get("id"):
                    data = o2
            except Exception:
                pass  # keep the original response — best effort
        fill_price = float(data.get("filled_avg_price") or 0) or None
        qty = abs(order["qty"])
        # Alpaca crypto orders carry no filled_fees — estimate from their
        # published taker schedule (config.ALPACA_FEE_BPS, default 25 bps)
        # so the journal PnL matches the account instead of understating
        # costs by the full entry fee.
        fees = float(data.get("filled_fees") or 0)
        if not fees and fill_price:
            fees = fill_price * qty * config.ALPACA_FEE_BPS / 10_000.0
        return {
            "status": "filled" if data.get("status") in ("filled", "accepted")
                      else str(data.get("status", "unknown")),
            "fill_price": fill_price,
            "fill_time": data.get("submitted_at"),
            "fees": round(fees, 8),
            "broker_ref": data.get("id"),
        }


class BinanceBroker(Broker):
    """
    Signed Binance SPOT market orders (the pattern from portfolio.py).
    Requires BINANCE_EXEC_API_KEY / BINANCE_EXEC_API_SECRET in .env.
    """

    name = "binance"
    _exchange_info_cache = {"at": 0.0, "data": None}
    _EXCHANGE_INFO_TTL = 3600.0  # 1 hour

    def __init__(self):
        if not config.BINANCE_EXEC_API_KEY or not config.BINANCE_EXEC_API_SECRET:
            raise BrokerError(
                "EXECUTION_BROKER=binance but BINANCE_EXEC_API_KEY / "
                "BINANCE_EXEC_API_SECRET are not set in .env")

    def _get_step_size(self, symbol: str) -> float:
        """Get the LOT_SIZE stepSize for a symbol from Binance exchange info."""
        import time as _time
        now = _time.time()
        # Refresh cache if needed
        if (BinanceBroker._exchange_info_cache["data"] is None
                or now - BinanceBroker._exchange_info_cache["at"] > self._EXCHANGE_INFO_TTL):
            try:
                resp = requests.get(f"{BINANCE_BASE}/api/v3/exchangeInfo", timeout=10)
                resp.raise_for_status()
                BinanceBroker._exchange_info_cache = {"at": now, "data": resp.json()}
            except Exception:
                return 0.0
        
        data = BinanceBroker._exchange_info_cache["data"]
        if not data:
            return 0.0
        
        for s in data.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "LOT_SIZE":
                        return float(f.get("stepSize", 0))
        return 0.0

    def _get_market_lot_info(self, symbol: str) -> dict:
        """Get MARKET_LOT_SIZE and NOTIONAL filters for market orders."""
        import time as _time
        now = _time.time()
        # Refresh cache if needed
        if (BinanceBroker._exchange_info_cache["data"] is None
                or now - BinanceBroker._exchange_info_cache["at"] > self._EXCHANGE_INFO_TTL):
            try:
                resp = requests.get(f"{BINANCE_BASE}/api/v3/exchangeInfo", timeout=10)
                resp.raise_for_status()
                BinanceBroker._exchange_info_cache = {"at": now, "data": resp.json()}
            except Exception:
                return {"stepSize": 0.0, "minQty": 0.0, "minNotional": 5.0}
        
        data = BinanceBroker._exchange_info_cache["data"]
        if not data:
            return {"stepSize": 0.0, "minQty": 0.0, "minNotional": 5.0}
        
        result = {"stepSize": 0.0, "minQty": 0.0, "minNotional": 5.0}
        for s in data.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    ftype = f.get("filterType")
                    if ftype == "MARKET_LOT_SIZE":
                        result["stepSize"] = float(f.get("stepSize", 0))
                        result["minQty"] = float(f.get("minQty", 0))
                    elif ftype == "LOT_SIZE":
                        # Use LOT_SIZE minQty as fallback if MARKET_LOT_SIZE minQty is 0
                        if result["minQty"] == 0:
                            result["minQty"] = float(f.get("minQty", 0))
                        # Use LOT_SIZE stepSize if MARKET_LOT_SIZE stepSize is 0
                        if result["stepSize"] == 0:
                            result["stepSize"] = float(f.get("stepSize", 0))
                    elif ftype == "NOTIONAL":
                        result["minNotional"] = float(f.get("minNotional", 5.0))
                break
        return result

    def place_market(self, conn, order: dict) -> dict:
        side = "BUY" if order["side"].upper() == "LONG" else "SELL"
        return self._submit(conn, order, side)

    def close_market(self, conn, order: dict) -> dict:
        side = "SELL" if order["side"].upper() == "LONG" else "BUY"
        return self._submit(conn, order, side)

    @staticmethod
    def _step_decimals(step: float) -> int:
        """Decimal places a stepSize encodes, via its STRING form (0.00001 → 5).
        `int(-log10(step))` truncates (0.00001 → 4.999… → 4) and produced
        under-precise quantity strings → 'Filter failure: LOT_SIZE' (2026-08-19)."""
        if step >= 1:
            return 0
        s = f"{step:.12f}".rstrip("0")
        return len(s.split(".", 1)[1]) if "." in s else 0

    @staticmethod
    def _floor_to_step(qty: float, step: float):
        """Exact decimal floor to the step grid (float floor(q/step)*step
        drifts off-grid at small steps)."""
        from decimal import Decimal
        if not step:
            return Decimal(str(qty))
        return (Decimal(str(qty)) / Decimal(str(step))
                ).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(step))

    def _free_balance(self, asset: str):
        """Free balance of one asset; None when it couldn't be determined
        (network/API failure) so callers fail-open instead of holding all
        orders on a flaky metadata call."""
        try:
            resp = requests.get(BINANCE_ACCOUNT,
                                headers={"X-MBX-APIKEY": config.BINANCE_EXEC_API_KEY},
                                timeout=15)
            resp.raise_for_status()
            for b in resp.json().get("balances", []):
                if b.get("asset") == asset:
                    return float(b.get("free") or 0)
            return 0.0
        except Exception:
            return None

    def _submit(self, conn, order: dict, side: str) -> dict:
        from decimal import Decimal
        symbol = order["symbol"].replace("/", "")   # BTC/USDT -> BTCUSDT
        raw_qty = abs(order['qty'])

        # Fetch ALL market order filters from Binance
        lot_info = self._get_market_lot_info(symbol)
        step_size = lot_info["stepSize"]
        min_qty = lot_info["minQty"]
        min_notional = lot_info["minNotional"]

        # Ensure quantity meets minimum requirements
        if raw_qty < min_qty:
            print(f"[Binance] {symbol}: qty {raw_qty} below minQty {min_qty}, skipping")
            raise BrokerError(f"binance: quantity {raw_qty} below minQty {min_qty}")

        # Round DOWN to the step grid with exact decimal arithmetic.
        if step_size and step_size > 0:
            dec = self._step_decimals(step_size)
            qty_dec = self._floor_to_step(raw_qty, step_size)
            if qty_dec < Decimal(str(min_qty)):
                print(f"[Binance] {symbol}: {qty_dec} below minQty after step-floor, skipping")
                raise BrokerError(f"binance: quantity {qty_dec} below minQty {min_qty} after rounding")
            qty_str = f"{qty_dec:.{dec}f}"
        else:
            qty_str = f"{raw_qty:.8f}"

        # Check notional value (price * qty >= minNotional)
        entry_price = order.get("entry") or order.get("fill_price") or 0
        notional = float(entry_price) * float(qty_str)
        if notional < min_notional:
            print(f"[Binance] {symbol}: notional ${notional:.2f} below min ${min_notional}, skipping")
            raise BrokerError(f"binance: notional ${notional:.2f} below min ${min_notional}")

        # Fundability pre-check: a market BUY needs free USDT, a SELL needs
        # free base asset. Catches -2010 (insufficient balance) locally with
        # a readable journal reason instead of burning the order. Fail-open
        # when the balance call itself fails.
        parts = order["symbol"].split("/")
        base, quote = (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "USDT")
        need_asset = quote if side == "BUY" else base
        have = self._free_balance(need_asset)
        if have is not None:
            lacking = notional if side == "BUY" else float(qty_str)
            if have < lacking:
                raise BrokerError(f"binance: insufficient free {need_asset} "
                                  f"(have {have:.2f}, need {lacking:.2f}) — fund the account or lower risk size")

        # Debug logging
        print(f"[Binance] {symbol}: raw_qty={order['qty']}, step_size={step_size}, "
              f"min_qty={min_qty}, rounded_qty={qty_str}, "
              f"notional=${notional:.2f} (min ${min_notional})")

        ts = int(time.time() * 1000)
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty_str,
            "timestamp": ts,
            "recvWindow": 10000,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(config.BINANCE_EXEC_API_SECRET.encode(), query.encode(),
                       hashlib.sha256).hexdigest()
        resp = None
        for attempt in range(3):  # transient network drops used to kill whole orders (2026-08-19)
            try:
                resp = requests.post(
                    BINANCE_ORDER,
                    params={**params, "signature": sig},
                    headers={"X-MBX-APIKEY": config.BINANCE_EXEC_API_KEY},
                    timeout=20,
                )
                break
            except requests.ConnectionError as e:
                if attempt >= 2:
                    raise BrokerError(f"binance connection failed after 3 tries: {e}")
                time.sleep(1.5 * (attempt + 1))
        if resp.status_code >= 400:
            raise BrokerError(f"binance {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        fill = next((f for f in data.get("fills", []) if f), {})
        return {
            "status": "filled" if data.get("status") == "FILLED" else
                      str(data.get("status", "unknown")),
            "fill_price": float(fill.get("price") or data.get("price") or 0) or None,
            "fill_time": datetime.now(timezone.utc).isoformat(),
            "fees": sum(float(f.get("commission") or 0) for f in data.get("fills", [])),
            "broker_ref": data.get("orderId"),
        }

    def fetch_orders(self, limit: int = 100) -> list[dict]:
        """Open spot orders (one call, no symbol = all). Filled orders are
        only picked up on the next open-cycle; full history would need a
        per-symbol allOrders query."""
        resp = requests.get(BINANCE_OPEN_ORDERS,
                            headers={"X-MBX-APIKEY": config.BINANCE_EXEC_API_KEY},
                            timeout=20)
        resp.raise_for_status()
        out = []
        for d in resp.json():
            out.append({
                "id": str(d.get("orderId")),
                "symbol": d.get("symbol"),
                "status": d.get("status"),
                "fill_price": d.get("price"),
                "fill_time": None,
                "fees": None,
            })
        return out


class MT5Broker(Broker):
    """
    MetaTrader 5 — the Exness demo path. Exness retail accounts have no
    public REST API; the standard (Exness-endorsed) automation route is the
    `MetaTrader5` Python package (Windows-only) talking to the LOCAL MT5
    terminal, which must be RUNNING and logged into the Exness demo.

    Symbol mapping via config.EXNESS_MT5_SYMBOLS (XAU/USD → XAUUSD, SPX/USD
    → US500, ...). Orders are market IOC DEALs; our unit sizing is converted
    to lots via the symbol's contract size, rounded down to volume_step and
    floored at volume_min. CFD costs live in the spread, so fees stay 0.

    Graceful everywhere: terminal down / package missing / symbol unknown →
    BrokerError, so the bot holds instead of crashing the cycle.
    """

    name = "mt5"

    def __init__(self):
        if not config.EXNESS_MT5_ENABLED:
            raise BrokerError("EXNESS_MT5_ENABLED=false — MT5 broker disabled")
        self._mt5 = None

    def _module(self):
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError:
                raise BrokerError(
                    "MetaTrader5 package not installed — run: pip install MetaTrader5")
            self._mt5 = mt5
        return self._mt5

    def _ensure(self):
        mt5 = self._module()
        # initialize() with no args connects to the running terminal's
        # current account; a path is only needed for a specific install.
        if config.MT5_TERMINAL_PATH:
            ok = mt5.initialize(config.MT5_TERMINAL_PATH)
        else:
            ok = mt5.initialize()
        if not ok:
            raise BrokerError(f"MT5 terminal not reachable: {mt5.last_error()}")
        return mt5

    def _close(self):
        try:
            self._module().shutdown()
        except Exception:
            pass  # terminal may already be gone — fine

    def symbol_of(self, symbol: str) -> str:
        """Our display symbol → the MT5 symbol (identity for unknown)."""
        # Try token registry first for cross-venue resolution
        info = token_registry.resolve_symbol(symbol, venue=token_registry.Venue.EXNESS_MT5)
        if info and "mt5" in info.alt_symbols:
            return info.alt_symbols["mt5"]
        # Fallback to config mapping for backward compatibility
        return config.EXNESS_MT5_SYMBOLS.get(symbol) or symbol

    def account_info(self) -> dict:
        """{equity, margin_free, balance} — sizes positions like Alpaca's
        cash: the free margin is the binding constraint, not the equity."""
        mt5 = self._ensure()
        try:
            info = mt5.account_info()
            if info is None:
                raise BrokerError(f"MT5 account_info failed: {mt5.last_error()}")
            return {"equity": float(info.equity or 0),
                    "margin_free": float(info.margin_free or 0),
                    "balance": float(info.balance or 0)}
        finally:
            self._close()

    def _symbol_info(self, symbol: str):
        mt5 = self._ensure()
        sym = self.symbol_of(symbol)
        try:
            mt5.symbol_select(sym, True)  # make sure the quote stream is on
            info = mt5.symbol_info(sym)
            if info is None:
                raise BrokerError(
                    f"MT5 symbol {sym} not found — terminal logged into the right account?")
            return info
        finally:
            self._close()

    @staticmethod
    def _lots(info, qty: float) -> float:
        """Unit qty → lot volume (contract size + volume_step rounding)."""
        contract = float(getattr(info, "trade_contract_size", 0) or 100_000.0)
        step = float(getattr(info, "volume_step", 0) or 0.01)
        raw = qty / contract if contract else 0.0
        return math.floor(raw / step + 1e-9) * step

    def place_market(self, conn, order: dict) -> dict:
        side = "buy" if order["side"].upper() == "LONG" else "sell"
        return self._submit(conn, order, side)

    def close_market(self, conn, order: dict) -> dict:
        side = "sell" if order["side"].upper() == "LONG" else "buy"
        return self._submit(conn, order, side)

    def _submit(self, conn, order: dict, side: str) -> dict:
        mt5 = self._ensure()
        sym = self.symbol_of(order["symbol"])
        try:
            # Get symbol info (this closes the connection in its finally)
            info = self._symbol_info(order["symbol"])
            # _symbol_info() closes the MT5 connection when it returns
            # (its own finally calls _close()), so the API is SHUT DOWN
            # here. Re-open it before order_send — otherwise every real
            # order dies with "-10004 No IPC connection" (2026-08-18:
            # this is why every live WTI/XAU attempt was rejected).
            mt5 = self._ensure()
            # Re-fetch symbol info after re-opening connection
            mt5.symbol_select(sym, True)
            info = mt5.symbol_info(sym)
            if info is None:
                raise BrokerError(f"MT5 symbol {sym} not found after reconnect")
            
            volume = self._lots(info, abs(float(order["qty"])))
            if volume <= 0:
                raise BrokerError(f"zero volume for {sym} (qty {order['qty']})")
            
            # Determine correct filling type based on symbol's supported modes.
            # filling_mode is a bitmask: bit 0 (1) = FOK supported,
            # bit 1 (2) = IOC supported, 3 = both. Sending an unsupported
            # filling type gets rejected (-10030 / invalid filling), so pick
            # FOK only when the symbol advertises it, otherwise IOC.
            filling_mode = getattr(info, "filling_mode", 0)
            if filling_mode & 1:
                type_filling = mt5.ORDER_FILLING_FOK
            else:
                type_filling = mt5.ORDER_FILLING_IOC
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
                "deviation": 30,
                "magic": 20260817,
                "comment": "cryptoautomation",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": type_filling,
            }
            result = mt5.order_send(request)
            if result is None or getattr(result, "retcode", None) != mt5.TRADE_RETCODE_DONE:
                raise BrokerError(f"mt5 order rejected for {sym}: "
                                  f"{getattr(result, 'retcode', '?')} {mt5.last_error()}")
        finally:
            self._close()
        return {
            "status": "filled",
            "fill_price": float(getattr(result, "price", 0) or 0) or None,
            "fill_time": datetime.now(timezone.utc).isoformat(),
            "fees": 0.0,  # CFD costs are inside the spread
            "broker_ref": str(getattr(result, "deal", 0)
                              or getattr(result, "order", 0)),
        }

    def fetch_positions(self) -> list[dict]:
        """Open MT5 positions: [{symbol (MT5 name), qty (units), side, entry, current}]."""
        mt5 = self._ensure()
        out = []
        try:
            for p in mt5.positions_get() or []:
                info = mt5.symbol_info(p.symbol)
                contract = float(getattr(info, "trade_contract_size", 0) or 100_000.0)
                out.append({
                    "symbol": p.symbol,
                    "qty": round(float(p.volume) * contract, 8),
                    "side": "long" if p.type == mt5.POSITION_TYPE_BUY else "short",
                    "entry": float(p.price_open) if hasattr(p, "price_open") else None,
                    "current": float(p.price_current) if hasattr(p, "price_current") else None,
                    "profit": float(p.profit) if hasattr(p, "profit") else None,
                })
        finally:
            self._close()
        return out

    def fetch_orders(self, limit: int = 100) -> list[dict]:
        """Market IOC DEALs leave no pending orders; deal history isn't part
        of MT5 reconciliation v1 — the position pass covers broker-side
        closes."""
        return []

    def fetch_deals(self, days: int = 30) -> list[dict]:
        """Fetch closed deals (trades) from MT5 history.
        Returns list of closed trades with PnL.
        """
        mt5 = self._ensure()
        out = []
        try:
            from datetime import datetime, timedelta
            date_from = datetime.now() - timedelta(days=days)
            # Fetch all deals (closed trades) in the date range
            deals = mt5.history_deals_get(date_from=date_from) or []
            # Group deals by position_id to calculate PnL per trade
            positions = {}
            for deal in deals:
                pos_id = deal.position_id
                if pos_id not in positions:
                    positions[pos_id] = {
                        "symbol": deal.symbol,
                        "deals": [],
                        "open_time": deal.time,
                        "close_time": deal.time,
                    }
                positions[pos_id]["deals"].append(deal)
                if deal.time > positions[pos_id]["close_time"]:
                    positions[pos_id]["close_time"] = deal.time
            
            # Calculate PnL for each position
            for pos_id, pos_data in positions.items():
                # Only include closed positions (have both entry and exit deals)
                if len(pos_data["deals"]) >= 2:
                    entry_deal = pos_data["deals"][0]
                    exit_deal = pos_data["deals"][-1]
                    # Calculate total profit/loss from all deals
                    total_profit = sum(d.profit for d in pos_data["deals"])
                    total_commission = sum(d.commission for d in pos_data["deals"])
                    info = mt5.symbol_info(pos_data["symbol"])
                    contract = float(getattr(info, "trade_contract_size", 0) or 100_000.0)
                    out.append({
                        "symbol": pos_data["symbol"],
                        "side": "long" if entry_deal.type == mt5.DEAL_TYPE_BUY else "short",
                        "qty": round(float(entry_deal.volume) * contract, 8),
                        "entry": float(entry_deal.price),
                        "exit": float(exit_deal.price),
                        "entry_time": pos_data["open_time"],
                        "exit_time": pos_data["close_time"],
                        "profit": round(total_profit, 2),
                        "commission": round(total_commission, 2),
                        "pnl": round(total_profit + total_commission, 2),
                    })
        finally:
            self._close()
        return out


class HybridBroker(Broker):
    """
    Routes crypto to Binance and forex/commodities/indices to MT5.
    Crypto symbols: BTC/USDT, ETH/USDT, etc. (anything ending in /USDT)
    Everything else goes to MT5.
    """

    name = "hybrid"

    def __init__(self):
        self._mt5 = MT5Broker()
        self._binance = BinanceBroker()

    def _is_crypto(self, symbol: str) -> bool:
        """Check if symbol is crypto by looking up in token registry."""
        info = token_registry.resolve_symbol(symbol)
        if info:
            return info.venue == token_registry.Venue.BINANCE or info.asset_class == token_registry.AssetClass.CRYPTO
        # Fallback for symbols not in registry
        s = symbol.upper()
        if s.endswith("/USDT") or s.endswith("USDT"):
            return True
        crypto_bases = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
                        "DOT", "MATIC", "LINK", "UNI", "ATOM", "FIL", "ARB", "OP"}
        base = s.split("/")[0] if "/" in s else s.replace("USDT", "")
        return base in crypto_bases

    def _get_broker_for(self, symbol: str) -> Broker:
        """Route to correct broker based on symbol."""
        if self._is_crypto(symbol):
            return self._binance
        return self._mt5

    def place_market(self, conn, order: dict) -> dict:
        broker = self._get_broker_for(order["symbol"])
        return broker.place_market(conn, order)

    def close_market(self, conn, order: dict) -> dict:
        broker = self._get_broker_for(order["symbol"])
        return broker.close_market(conn, order)

    def fetch_orders(self, limit: int = 100) -> list[dict]:
        # Combine orders from both brokers
        orders = []
        try:
            orders.extend(self._mt5.fetch_orders(limit))
        except Exception:
            pass
        try:
            orders.extend(self._binance.fetch_orders(limit))
        except Exception:
            pass
        return orders

    def fetch_positions(self) -> list[dict]:
        # Combine positions from both brokers
        positions = []
        try:
            positions.extend(self._mt5.fetch_positions())
        except Exception:
            pass
        try:
            positions.extend(self._binance.fetch_positions())
        except Exception:
            pass
        return positions

    def fetch_deals(self, days: int = 30) -> list[dict]:
        # Combine closed deals from both brokers
        deals = []
        try:
            deals.extend(self._mt5.fetch_deals(days))
        except Exception:
            pass
        try:
            # Binance doesn't have deal history in the same way
            # Could add binance.fetch_deals() if needed
            pass
        except Exception:
            pass
        return deals


def get_broker() -> Broker:
    """The configured broker — the only gateway the executor talks to."""
    mode = config.EXECUTION_BROKER
    if mode == "alpaca":
        return AlpacaBroker()
    if mode == "binance":
        return BinanceBroker()
    if mode == "mt5":
        return MT5Broker()
    if mode == "hybrid":
        return HybridBroker()
    return PaperBroker()


# ----------------------------------------------------------------------
# Execution entry point
# ----------------------------------------------------------------------
def _actual_broker_for(symbol: str) -> str:
    """Determine which broker will actually execute this symbol.
    For hybrid mode: crypto -> binance, everything else -> mt5.
    Otherwise: return the configured EXECUTION_BROKER.
    """
    if config.EXECUTION_BROKER != "hybrid":
        return config.EXECUTION_BROKER
    # Hybrid routing: use token registry for authoritative venue mapping
    info = token_registry.resolve_symbol(symbol)
    if info:
        if info.venue == token_registry.Venue.BINANCE or info.asset_class == token_registry.AssetClass.CRYPTO:
            return "binance"
        if info.venue == token_registry.Venue.EXNESS_MT5:
            return "mt5"
        if info.venue == token_registry.Venue.ALPACA:
            return "alpaca"
    # Fallback for symbols not in registry
    s = symbol.upper()
    if s.endswith("/USDT") or s.endswith("USDT"):
        return "binance"
    crypto_bases = {"BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX",
                    "DOT", "MATIC", "LINK", "UNI", "ATOM", "FIL", "ARB", "OP"}
    base = s.split("/")[0] if "/" in s else s.replace("USDT", "")
    if base in crypto_bases:
        return "binance"
    return "mt5"


def _default_levels(conn, symbol: str, entry: float, direction: str) -> tuple[float, float]:
    """
    ATR-based SL/TP for intents that carry none (e.g. source-B-only).
    SL = 1.5 x ATR(1h) from entry (0.5% floor, 5% cap).
    TP = volatility-scaled (dynamic TP):
        low vol (< 25th pct)  -> 1.0x ATR (quick wins, tight target)
        mid vol (25-75th pct) -> 1.5x ATR (balanced)
        high vol (> 75th pct) -> 2.0x ATR (let winners run)
    Returns (stop_loss, take_profit).
    """
    try:
        import indicators
        df = indicators.add_all(database.load_candles(conn, symbol, "1h", limit=200))
        atr = float(df["atr"].iloc[-1]) if (not df.empty and "atr" in df.columns) else 0.0
        # Volatility percentile for dynamic TP scaling
        if not df.empty and "atr" in df.columns and len(df) >= 50:
            atr_series = df["atr"].dropna()
            vol_pct = (atr_series.searchsorted(atr) / len(atr_series)) if len(atr_series) > 0 else 0.5
        else:
            vol_pct = 0.5
    except Exception:
        atr = 0.0
        vol_pct = 0.5
    if not atr or atr <= 0:
        sl_dist = entry * 0.01
        tp_mult = 2.0
    else:
        sl_dist = max(atr * 1.5, entry * 0.005)
        # Dynamic TP: scale with volatility
        if vol_pct < 0.25:
            tp_mult = 1.0   # low vol → quick wins
        elif vol_pct > 0.75:
            tp_mult = 2.0   # high vol → let winners run
        else:
            tp_mult = 1.5   # mid vol → balanced
    sl_dist = min(sl_dist, entry * 0.05)  # never stop out more than 5% away
    long = direction.upper() == "LONG"
    stop = entry - sl_dist if long else entry + sl_dist
    tp = entry + tp_mult * sl_dist if long else entry - tp_mult * sl_dist
    return round(stop, 8), round(tp, 8)


def _sized_qty(conn, intent, qty: float | None, entry: float | None = None,
               stop: float | None = None) -> float:
    """Risk-based size: (risk% × equity) / |entry − SL|, capped by Kelly.
    Equity = the Alpaca account when that's the broker, else journal equity."""
    if qty is not None and qty > 0:
        return float(qty)
    equity = _broker_equity(conn)
    entry = entry or intent.entry
    stop = stop or intent.stop_loss
    if not entry:
        df = database.load_candles(conn, intent.symbol, "1h", limit=3)
        entry = float(df["close"].iloc[-1]) if not df.empty else None
    if not entry:
        return 0.0  # cannot price the symbol -> cannot size
    if not stop:  # no stop level from either source - use a 1% ATR-style gap
        stop = entry * 0.99 if intent.direction == "LONG" else entry * 1.01
    risk_pct = risk.risk_per_trade_pct(0.0)  # 1% default, capped at 2%
    # Confidence-scaled lot: when the bot is SURE of a trade it risks more
    # of the book, so wins move the needle. Thresholds follow the user's
    # calibration — 60-70%+ confidence boosts size up to the 2% hard cap.
    conf = float(getattr(intent, "confidence", 0) or 0)
    mult = 1.0
    if conf >= 70:
        mult = 2.0
    elif conf >= 60:
        mult = 1.5
    risk_pct = min(risk_pct * mult, config.MAX_RISK_PER_TRADE)
    size = risk.position_size(equity, entry or 0, stop or 0, risk_pct)
    if size <= 0:
        return 0.0

    try:
        broker = get_broker()
    except Exception:
        broker = None
    if broker is not None and broker.name == "mt5" and entry:
        # MT5/Exness (lot-based CFDs): the standard 15% notional cap is
        # BELOW the broker's minimum lot for metals/indices (a 0.01 XAUUSD
        # lot is ~$4.4k), which would zero-size every gold trade. Use
        # MT5_POSITION_PCT instead, floor at the broker's min lot and let
        # the exchange margin rules be the hard backstop. Terminal down /
        # symbol unknown → fall back to the standard cap below.
        try:
            info = broker._symbol_info(intent.symbol)
            contract = float(getattr(info, "trade_contract_size", 0) or 100_000.0)
            step = float(getattr(info, "volume_step", 0) or 0.01)
            vmin = float(getattr(info, "volume_min", 0) or 0.01)
            vmax = float(getattr(info, "volume_max", 0) or 100.0)
            vol = broker._lots(info, size)
            if vol < vmin:
                vol = vmin  # min-lot floor — the risk stop keeps risk ~1-2%
            cap_vol = math.floor(
                (equity * config.MT5_POSITION_PCT) /
                (float(entry) * contract) / step + 1e-9) * step
            # If cap_vol is below vmin, use vmin (let margin rules be the backstop)
            if cap_vol < vmin:
                cap_vol = vmin  # Allow minimum lot size even if equity is small
            return min(vol, cap_vol, vmax) * contract  # back to units
        except Exception:
            pass  # terminal down / symbol missing → standard cap below

    # Notional cap (non-MT5): one position may never represent more than
    # MAX_POSITION_PCT × confidence-multiplier of the book — a sure trade
    # is allowed a bigger slice (the user's confidence-scaling ask) but a
    # burst of simultaneous signals can't each spend ~100× their risk.
    if entry:
        cap_qty = (equity * config.MAX_POSITION_PCT * mult) / float(entry)
        size = min(size, cap_qty)
    return size


def execute_intent(conn, intent, qty: float | None = None,
                   interval: str = "1h", dry_run: bool = False) -> dict:
    """
    The single order path. Idempotent: an intent_hash already journaled
    returns the existing order (duplicate flag) and never places again.

    Returns {order: {...}, duplicate: bool, dry_run: bool}.
    """
    existing = database.order_for_intent(conn, intent.intent_hash)
    # A dry_run row is only a preview — it must NOT consume the trade, so a
    # later real order of the same intent is allowed (it replaces the row).
    if existing and existing.get("status") != "dry_run":
        return {"order": existing, "duplicate": True, "dry_run": False}

    order = {
        "intent_hash": intent.intent_hash,
        "symbol": intent.symbol,
        "side": intent.direction,          # LONG | SHORT
        "qty": 0.0,
        "entry": intent.entry,
        "stop_loss": intent.stop_loss,
        "take_profit": intent.take_profit,
        "source": _actual_broker_for(intent.symbol),  # Actual broker, not "hybrid"
        "status": "error",
        "fill_price": None, "fill_time": None, "fees": 0.0, "pnl": None,
        "error": None,
        "interval": interval,
        "reasons": "; ".join(getattr(intent, "reasons", None) or []),
    }

    # Source-B-only intents carry no entry level — backfill from the last
    # close so the journal can price market value / unrealized PnL.
    if not order.get("entry"):
        df = database.load_candles(conn, intent.symbol, "1h", limit=3)
        if not df.empty:
            order["entry"] = float(df["close"].iloc[-1])

    # Never leave a position without risk levels: fill missing SL/TP from
    # AI-suggested percentages or ATR fallback.
    if order.get("entry"):
        entry = float(order["entry"])
        # Use AI-suggested SL/TP percentages if available
        ai_sl_pct = getattr(intent, 'stop_loss_pct', 0.0) or 0.0
        ai_tp_pct = getattr(intent, 'take_profit_pct', 0.0) or 0.0
        
        if not order.get("stop_loss") and ai_sl_pct > 0:
            # Calculate SL from AI percentage
            if intent.direction == "LONG":
                order["stop_loss"] = entry * (1 - ai_sl_pct / 100)
            else:  # SHORT
                order["stop_loss"] = entry * (1 + ai_sl_pct / 100)
        
        if not order.get("take_profit") and ai_tp_pct > 0:
            # Calculate TP from AI percentage
            if intent.direction == "LONG":
                order["take_profit"] = entry * (1 + ai_tp_pct / 100)
            else:  # SHORT
                order["take_profit"] = entry * (1 - ai_tp_pct / 100)
        
        # Fallback to ATR-based levels if still missing
        if not order.get("stop_loss") or not order.get("take_profit"):
            sl, tp = _default_levels(conn, intent.symbol, entry, intent.direction)
            order["stop_loss"] = order.get("stop_loss") or sl
            order["take_profit"] = order.get("take_profit") or tp

    size = _sized_qty(conn, intent, qty, order.get("entry"), order.get("stop_loss"))
    if size <= 0:
        return {"order": None, "duplicate": False, "dry_run": dry_run,
                "error": "zero size (no equity or no stop distance)"}
    order["qty"] = size

    if dry_run:
        order["status"] = "dry_run"
        database.record_order(conn, order)
        return {"order": order, "duplicate": False, "dry_run": True}

    # Drop any preview row for this intent so the real order takes its place.
    database.discard_dry_run(conn, intent.intent_hash)

    broker = get_broker()
    try:
        fill = broker.place_market(conn, order)
    except BrokerError as e:
        order["error"] = str(e)
        database.record_order(conn, order)
        raise
    except Exception as e:  # network / JSON issues — same treatment
        order["error"] = f"{type(e).__name__}: {e}"
        database.record_order(conn, order)
        raise BrokerError(order["error"]) from e

    order.update({
        "status": fill.get("status", "error"),
        "fill_price": fill.get("fill_price"),
        "fill_time": fill.get("fill_time"),
        "fees": fill.get("fees", 0.0),
        # The broker's order id is what reconciliation matches on (and what
        # lets the position pass journal broker-side closes). Dropping it
        # here silently orphaned every real order from the broker.
        "broker_ref": fill.get("broker_ref"),
    })
    rowid = database.record_order(conn, order)
    if rowid == 0:
        # Lost a race: another call placed this intent first. Return it.
        existing = database.order_for_intent(conn, intent.intent_hash)
        return {"order": existing, "duplicate": True, "dry_run": False}
    return {"order": database.order_for_intent(conn, intent.intent_hash),
            "duplicate": False, "dry_run": False}


# ----------------------------------------------------------------------
# Position management (SL/TP monitoring + close)
# ----------------------------------------------------------------------
def check_position(conn, order: dict, price: float | None = None) -> str:
    """
    What should happen to an open order at `price` (last close if None)?
    Returns 'tp' | 'sl' | 'hold' | 'closed'.
    """
    if order.get("status") not in ("filled", "open"):
        return "closed"
    if price is None:
        df = database.load_candles(conn, order["symbol"], "1h", limit=3)
        if df.empty:
            return "hold"
        price = float(df["close"].iloc[-1])
    entry, sl, tp = (order.get("entry"), order.get("stop_loss"),
                     order.get("take_profit"))
    long = order["side"].upper() == "LONG"
    if tp:
        hit = price >= float(tp) if long else price <= float(tp)
        if hit:
            return "tp"
    if sl:
        hit = price <= float(sl) if long else price >= float(sl)
        if hit:
            return "sl"
    return "hold"


def close_position(conn, order: dict, price: float | None = None,
                   reason: str = "manual") -> dict:
    """
    Close an open order at `price` (market exit via the broker if None).
    Realizes PnL (gross − entry fees − exit fees) into the journal.
    Returns {order, pnl, reason}.
    """
    if order.get("status") not in ("filled", "open"):
        return {"order": order, "pnl": 0.0, "reason": "not_open"}

    if price is not None:
        fees = abs(order["qty"]) * float(price) * risk.fee_bps() / 10_000.0
        fill = {"fill_price": float(price), "fees": fees,
                "fill_time": datetime.now(timezone.utc).isoformat()}
    else:
        fill = get_broker().close_market(conn, order)

    exit_price = float(fill.get("fill_price") or price or 0.0)
    qty = abs(order["qty"])
    entry = float(order.get("entry") or 0.0)
    entry_fees = float(order.get("fees") or 0.0)
    exit_fees = float(fill.get("fees") or 0.0)
    if order["side"].upper() == "LONG":
        gross = (exit_price - entry) * qty
    else:
        gross = (entry - exit_price) * qty
    pnl = gross - entry_fees - exit_fees

    database.update_order_exit(conn, order["id"], round(exit_price, 8),
                               fill.get("fill_time") or datetime.now(timezone.utc).isoformat(),
                               reason, round(pnl, 8), round(entry_fees + exit_fees, 8))
    closed = database.order_for_intent(conn, order["intent_hash"])
    return {"order": closed, "pnl": round(pnl, 8), "reason": reason}


# ----------------------------------------------------------------------
# Loss guard (circuit breaker — see config.MAX_DAILY_LOSS_PCT)
# ----------------------------------------------------------------------
def loss_guard_status(conn) -> dict:
    """The bleeding check, run BEFORE any new entry in the bot cycle.

    Two hard caps on the JOURNAL equity (works with zero keys / any broker):

      daily_loss_pct  — realized PnL today (closed orders whose exit_time
          falls in the current UTC day) vs start-of-day equity. Breach of
          config.MAX_DAILY_LOSS_PCT → blocked until the day rolls over.
      drawdown_pct    — equity vs its all-time journal peak (running sum
          of realized PnL in exit order). Breach of
          config.MAX_DRAWDOWN_PCT → blocked until the drawdown recovers.

    Open positions are still SL/TP-managed (closing is not new exposure);
    only NEW entries are held. Returns
    {blocked, reason, daily_loss_pct, drawdown_pct, equity_now, peak_equity}.
    A guard threshold of 0 disables that check. Never raises.
    """
    # The guard measures ONLY the current broker's closed rows (source =
    # config.EXECUTION_BROKER). A retired account's closes are bookkeeping,
    # not trading results — counting them would freeze the fresh account
    # (seen live 2026-08-17: the Alpaca retirement pushed daily loss to
    # 9.65% / drawdown ~13.5%, blocking Exness on day one). The dashboard
    # scopes the same way, so the guard and the views stay consistent.
    # Same logic per-row: closes relabeled `account_retired` (broker-swap
    # bookkeeping within this broker) are excluded too.
    try:
        rows = conn.execute(
            "SELECT pnl, exit_time, created_at FROM orders "
            "WHERE status = 'closed' AND source = ? "
            "AND (exit_reason IS NULL OR exit_reason != 'account_retired')",
            (config.EXECUTION_BROKER,)).fetchall()
        pnls = [(float(r["pnl"] or 0.0), str(r["exit_time"] or r["created_at"] or ""))
                for r in rows]
    except Exception:
        pnls = []

    # Equity curve from realized PnL in exit order (closed rows only).
    ordered = sorted(pnls, key=lambda x: x[1])
    equity = config.INITIAL_CAPITAL
    peak = equity
    for pnl, _ts in ordered:
        equity += pnl
        peak = max(peak, equity)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_pnl = sum(p for p, ts in pnls if ts.startswith(today))
    start_of_day = equity - daily_pnl  # equity before today's closed PnL
    daily_loss_pct = (-daily_pnl / start_of_day * 100.0) if daily_pnl < 0 and start_of_day > 0 else 0.0
    drawdown_pct = ((peak - equity) / peak * 100.0) if peak > 0 and equity < peak else 0.0

    blocked, reason = False, ""
    if config.MAX_DAILY_LOSS_PCT > 0 and daily_loss_pct >= config.MAX_DAILY_LOSS_PCT:
        blocked = True
        reason = (f"daily loss guard: {daily_loss_pct:.2f}% ≥ "
                  f"{config.MAX_DAILY_LOSS_PCT:.2f}% — new entries held until "
                  "tomorrow (SL/TP monitor still runs)")
    elif config.MAX_DRAWDOWN_PCT > 0 and drawdown_pct >= config.MAX_DRAWDOWN_PCT:
        blocked = True
        reason = (f"drawdown guard: {drawdown_pct:.2f}% from peak ≥ "
                  f"{config.MAX_DRAWDOWN_PCT:.2f}% — new entries held until "
                  "the drawdown recovers")
    return {"blocked": blocked, "reason": reason,
            "daily_loss_pct": round(daily_loss_pct, 3),
            "drawdown_pct": round(drawdown_pct, 3),
            "equity_now": round(equity, 2),
            "peak_equity": round(peak, 2)}


def manage_open_positions(conn, interval: str = "1h") -> list[dict]:
    """
    The SL/TP monitor with trailing stop, partial close, and time-decay.

    For every open order:
      1. Trailing SL: activate after 1.0 ATR move, trail at 1.5 ATR,
         breakeven at 0.5 ATR. Stop only moves in trade's favor.
      2. Partial close: at 50% of TP distance, close half the position.
      3. Time-decay TP: after 12 bars, tighten TP or exit at market.
      4. Standard SL/TP: close when stop-loss or take-profit is hit.

    Returns closed summaries.
    """
    try:
        supported = broker_supported_symbols()
    except Exception:
        supported = None
    closed = []
    for o in database.load_orders(conn, limit=1000):
        if o.get("status") not in ("filled", "open"):
            continue
        if supported is not None and o["symbol"] not in supported:
            continue  # broker doesn't carry it — nothing to manage

        # ── Trailing stop update (before SL/TP check) ─────────────
        if config.TRAILING_STOP_ENABLED:
            try:
                _update_trailing_stop(conn, o, interval)
            except Exception:
                pass  # trailing stop update must never kill the cycle

        # ── Standard SL/TP check ──────────────────────────────────
        action = check_position(conn, o)
        if action in ("tp", "sl"):
            try:
                res = close_position(conn, o, reason=action)
            except Exception as e:
                print(f"  sl/tp close {o['symbol']} failed: {e} — retrying next cycle")
                continue
            closed.append({"symbol": o["symbol"], "side": o["side"],
                           "reason": action, "qty": o["qty"],
                           "pnl": round(res["pnl"], 4)})
            continue

        # ── Time-decay TP: after N bars, exit if not profitable ───
        if config.TP_MAX_HOLD_BARS > 0:
            try:
                decay_action = _check_time_decay(conn, o, interval)
                if decay_action:
                    try:
                        res = close_position(conn, o, reason=decay_action)
                        closed.append({"symbol": o["symbol"], "side": o["side"],
                                       "reason": decay_action, "qty": o["qty"],
                                       "pnl": round(res["pnl"], 4)})
                    except Exception:
                        pass  # decay close failure → retry next cycle
            except Exception:
                pass

    return closed


# ----------------------------------------------------------------------
# Trailing stop-loss management
# ----------------------------------------------------------------------
def _get_atr(conn, symbol: str, interval: str = "1h") -> float:
    """Current ATR for a symbol. Returns 0 on failure."""
    try:
        import indicators
        df = indicators.add_all(database.load_candles(conn, symbol, interval, limit=60))
        return float(df["atr"].iloc[-1]) if (not df.empty and "atr" in df.columns) else 0.0
    except Exception:
        return 0.0


def _update_trailing_stop(conn, order: dict, interval: str = "1h") -> None:
    """Update the trailing stop for an open position.

    Activation: price must move TRAIL_ACTIVATE_ATR (1.0) ATR in trade's favor.
    Breakeven: stop moves to entry + TRAIL_BREAKEVEN_ATR (0.5) ATR.
    Trailing: stop trails at TRAIL_DISTANCE_ATR (1.5) ATR behind price.
    Rule: stop only moves in trade's favor (never widens).

    Updates the order's stop_loss in the database if improved.
    """
    if order.get("status") not in ("filled", "open"):
        return

    symbol = order["symbol"]
    entry = float(order.get("entry") or order.get("fill_price") or 0)
    current_sl = float(order.get("stop_loss") or 0)
    if not entry:
        return

    atr = _get_atr(conn, symbol, interval)
    if atr <= 0:
        return

    # Get current price
    df = database.load_candles(conn, symbol, interval, limit=3)
    if df.empty:
        return
    price = float(df["close"].iloc[-1])
    long = order["side"].upper() == "LONG"

    activate_dist = atr * config.TRAIL_ACTIVATE_ATR
    trail_dist = atr * config.TRAIL_DISTANCE_ATR
    be_dist = atr * config.TRAIL_BREAKEVEN_ATR

    new_sl = current_sl

    if long:
        # Check if trailing should activate
        if price >= entry + activate_dist:
            # Breakeven floor: at least entry + breakeven distance
            be_sl = entry + be_dist
            # Trailing level: price minus trail distance
            trail_sl = price - trail_dist
            # Use the higher of breakeven and trailing
            candidate = max(be_sl, trail_sl)
            # Only move stop UP (in trade's favor)
            if candidate > current_sl:
                new_sl = round(candidate, 8)
    else:
        # SHORT: check if trailing should activate
        if price <= entry - activate_dist:
            # Breakeven floor: entry - breakeven distance
            be_sl = entry - be_dist
            # Trailing level: price plus trail distance
            trail_sl = price + trail_dist
            # Use the lower of breakeven and trailing
            candidate = min(be_sl, trail_sl)
            # Only move stop DOWN (in trade's favor)
            if candidate < current_sl:
                new_sl = round(candidate, 8)

    # Persist the improved stop loss
    if new_sl != current_sl and new_sl > 0:
        try:
            conn.execute(
                "UPDATE orders SET stop_loss = ? WHERE id = ?",
                (new_sl, order["id"]))
            conn.commit()
        except Exception:
            pass  # best effort — next cycle will retry


def _check_time_decay(conn, order: dict, interval: str = "1h") -> str | None:
    """Time-decay TP: after TP_MAX_HOLD_BARS, exit if not meaningfully
    profitable.

    Logic:
        - Count bars since entry (from fill_time or created_at).
        - After N bars: if PnL is positive but small (< 0.5 ATR), exit
          at market (take the small win rather than risk reversal).
        - After 2x N bars: exit regardless (time is up).

    Returns the exit reason string, or None to hold.
    """
    max_bars = config.TP_MAX_HOLD_BARS
    if max_bars <= 0:
        return None

    entry_time = order.get("fill_time") or order.get("created_at") or ""
    if not entry_time:
        return None

    # Estimate bars elapsed (1h bars, so hours elapsed ≈ bars)
    try:
        from datetime import datetime, timezone
        entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours_elapsed = (now - entry_dt).total_seconds() / 3600.0
    except Exception:
        return None

    entry = float(order.get("entry") or order.get("fill_price") or 0)
    if not entry:
        return None

    atr = _get_atr(conn, order["symbol"], interval)

    # Get current price for PnL estimate
    df = database.load_candles(conn, order["symbol"], interval, limit=3)
    if df.empty:
        return None
    price = float(df["close"].iloc[-1])
    long = order["side"].upper() == "LONG"
    if long:
        pnl_dist = price - entry
    else:
        pnl_dist = entry - price

    # After 2x max bars: exit regardless
    if hours_elapsed >= max_bars * 2:
        return "time_decay_exit"

    # After max bars: exit if small profit (< 0.5 ATR)
    if hours_elapsed >= max_bars:
        if atr > 0 and 0 < pnl_dist < atr * 0.5:
            return "time_decay_small_profit"
        elif pnl_dist <= 0 and atr > 0 and pnl_dist > -atr * 0.25:
            # Small loss — cut it rather than hoping
            return "time_decay_small_loss"

    return None


# ----------------------------------------------------------------------
# Broker reconciliation (journal ↔ Alpaca/Binance order status)
# ----------------------------------------------------------------------
def reconcile_orders(conn, limit: int = 100) -> list[dict]:
    """
    Pull the broker's order list and sync every journal order that has a
    broker_ref: status (pending_new → filled, canceled → cancelled), fill
    price/time and fees. Returns the list of updated order summaries.

    No-op for the offline paper broker (nothing to reconcile). Network is
    wrapped defensively — a broker hiccup never breaks the bot cycle.
    """
    updated = []
    try:
        broker = get_broker()
    except BrokerError:
        return updated
    if broker.name not in ("alpaca", "binance", "mt5"):
        return updated

    try:
        remote = broker.fetch_orders(limit=limit)
    except Exception as e:
        raise BrokerError(f"reconcile fetch failed: {e}") from e
    by_id = {o["id"]: o for o in remote}

    # MT5 reports no order list (market IOC DEALs) — only the position pass
    # below applies; the foreign/status passes need a real order feed.
    if not remote:
        remote = None

    # ── Foreign-fill pass: FILLED broker orders that our journal knows
    # nothing about (no matching broker_ref) were placed OUTSIDE this bot
    # (manual, another tool, an earlier config). Flag them so the dashboard
    # can warn instead of silently ignoring broker-side activity.
    if remote is not None:
        try:
            journal_refs = {j.get("broker_ref") for j in database.load_orders(conn, limit=2000)
                            if j.get("broker_ref")}
            foreign = []
            for o in remote:
                oid = str(o.get("id") or "")
                if not oid or oid in journal_refs:
                    continue
                if str(o.get("status", "")).lower() != "filled":
                    continue
                s = str(o.get("symbol") or "").replace("/", "")
                ours = next((c for c in config.TRADABLE_UNIVERSE
                             if broker.name == "alpaca" and AlpacaBroker._symbol(c) == s), None)
                if not ours:
                    continue
                foreign.append({"foreign": True, "symbol": ours,
                                "broker_symbol": str(o.get("symbol")),
                                "side": o.get("side"), "qty": o.get("qty"),
                                "fill_price": o.get("fill_price"),
                                "fill_time": o.get("fill_time"),
                                "created_at": o.get("created_at")})
            if foreign:
                updated.extend(foreign[:50])  # cap: don't flood the journal view
        except Exception:
            pass  # foreign-fill detection is best-effort, never fatal

    if remote is not None:
        for journal in database.load_orders(conn, limit=2000):
            ref = journal.get("broker_ref")
            if not ref or ref not in by_id:
                continue
            remote_o = by_id[ref]
            status = _map_broker_status(remote_o.get("status", ""))
            updates = {}
            if status and status != journal.get("status"):
                updates["status"] = status
            fill = remote_o.get("fill_price")
            if fill and journal.get("fill_price") is None:
                updates["fill_price"] = round(float(fill), 8)
            ft = remote_o.get("fill_time")
            if ft and not journal.get("fill_time"):
                updates["fill_time"] = ft
            fees = remote_o.get("fees")
            if fees:
                updates["fees"] = round(float(fees), 8)
            if updates:
                sets = ", ".join(f"{k}=?" for k in updates)
                conn.execute(f"UPDATE orders SET {sets} WHERE id=?",
                             [*updates.values(), journal["id"]])
                conn.commit()
                updated.append({"id": journal["id"], "symbol": journal["symbol"],
                                "broker_ref": ref, **updates})

    # ── Position pass (Alpaca + MT5): a journal order whose broker position
    # is GONE was closed broker-side (manually in the broker dashboard, or a
    # stop/TP placed outside the bot). Alpaca finds the closing order; MT5
    # has no order history in v1, so the exit is journaled at the last close.
    if broker.name in ("alpaca", "mt5"):
        try:
            positions = broker.fetch_positions()
        except Exception:
            positions = []
        open_syms = {p["symbol"] for p in positions if abs(p.get("qty") or 0) > 1e-9}
        for journal in database.load_orders(conn, limit=2000):
            if journal.get("status") not in ("filled", "open"):
                continue
            if not journal.get("broker_ref"):
                continue
            sym = broker.symbol_of(journal["symbol"])
            if sym in open_syms:
                continue
            close = None
            if broker.name == "alpaca":
                close = _find_closing_order(remote or [], journal, sym)
            entry = float(journal.get("entry") or journal.get("fill_price") or 0)
            if close:
                exit_price = float(close["fill_price"])
                exit_time = close.get("fill_time") or close.get("created_at") or ""
                exit_fees = float(close.get("fees") or 0)
            else:
                # MT5 fallback (or no matching close found): mark the exit at
                # the last candle close — the journal stays truthful even
                # without an order history.
                df = database.load_candles(conn, journal["symbol"], "1h", limit=3)
                if df.empty:
                    continue
                exit_price = float(df["close"].iloc[-1])
                exit_time = ""
                exit_fees = 0.0
            qty = abs(float(journal["qty"]))
            entry_fees = float(journal.get("fees") or 0)
            if journal["side"].upper() == "LONG":
                gross = (exit_price - entry) * qty
            else:
                gross = (entry - exit_price) * qty
            pnl = round(gross - entry_fees - exit_fees, 8)
            database.update_order_exit(conn, journal["id"], round(exit_price, 8),
                                       exit_time or datetime.now(timezone.utc).isoformat(),
                                       "broker_closed", pnl,
                                       round(entry_fees + exit_fees, 8))
            updated.append({"id": journal["id"], "symbol": journal["symbol"],
                            "closed": True, "exit_reason": "broker_closed",
                            "exit_price": round(exit_price, 8), "pnl": pnl,
                            "side": journal["side"], "qty": qty})
    return updated


def _find_closing_order(remote: list[dict], journal: dict, sym: str) -> dict | None:
    """The earliest filled opposite-side order for `sym` after our entry —
    i.e. the broker-side close of this journal position."""
    want_side = "sell" if journal["side"].upper() == "LONG" else "buy"
    cands = [o for o in remote
             if o.get("symbol") == sym and o.get("side") == want_side
             and o.get("status") == "filled" and o.get("fill_price")]
    after = str(journal.get("fill_time") or "")
    if after:
        cands = [o for o in cands if str(o.get("created_at") or "") >= after]
    if not cands:
        return None
    cands.sort(key=lambda o: str(o.get("created_at") or ""))
    return cands[0]


def _map_broker_status(status: str) -> str:
    """Alpaca/Binance order status → our journal vocabulary."""
    s = (status or "").lower()
    if s in ("filled",):
        return "filled"
    if s in ("partially_filled", "pending_new", "accepted", "new", "open"):
        return "open"
    if s in ("canceled", "cancelled", "expired", "rejected", "stopped",
             "suspended", "done_for_day"):
        return "cancelled"
    return status or ""

