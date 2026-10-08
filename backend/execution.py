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
import json
import math
import time
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP
from decimal import Decimal as _Dec

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
BINANCE_OCO_PLACE = f"{BINANCE_BASE}/api/v3/orderList/oco"   # POST (place)
BINANCE_OCO_LIST = f"{BINANCE_BASE}/api/v3/orderList"        # GET/DELETE (query/cancel)

# Local clock skew correction. Binance rejects any signed request whose
# timestamp falls outside recvWindow with -1021 ('Timestamp for this request
# is outside of the recvWindow') — this machine tripped it three times on
# 2026-09-28. We measure the offset from the public /api/v3/time endpoint
# once every 10 minutes and apply it to every signed timestamp.
_BINANCE_CLOCK = {"offset_ms": 0, "at": 0.0}


def _binance_ts() -> int:
    """Current time in ms, corrected for local clock skew vs Binance."""
    now = time.time()
    clock = _BINANCE_CLOCK
    if now - clock["at"] > 600.0:
        try:
            resp = requests.get(f"{BINANCE_BASE}/api/v3/time", timeout=5)
            resp.raise_for_status()
            clock["offset_ms"] = int(resp.json()["serverTime"]) - int(now * 1000)
        except Exception:
            pass  # keep the last known offset (0 on the first failure)
        clock["at"] = now
    return int(now * 1000) + clock["offset_ms"]

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
            # Pre-order mid — lets execute_intent journal MEASURED slippage
            # (|fill − ref|) instead of the bps estimate.
            "ref_price": price,
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
                # Keep serving the stale cache — resetting it to 0.0 made every
                # order skip step-rounding while the network hiccupped.
                pass
        
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
                # Stale cache beats zeros (zeros → LOT_SIZE rejections).
                pass
        
        data = BinanceBroker._exchange_info_cache["data"]
        if not data:
            return {"stepSize": 0.0, "minQty": 0.0, "minNotional": 5.0}
        
        result = {"stepSize": 0.0, "minQty": 0.0, "minNotional": 5.0}
        for s in data.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    ftype = f.get("filterType")
                    if ftype == "MARKET_LOT_SIZE":
                        # The testnet sends stepSize/minQty as literal 0 here —
                        # never let zeros overwrite the LOT_SIZE values: orders
                        # then go out unrounded → 'Filter failure: LOT_SIZE'.
                        if float(f.get("stepSize", 0) or 0) > 0:
                            result["stepSize"] = float(f.get("stepSize"))
                        if float(f.get("minQty", 0) or 0) > 0:
                            result["minQty"] = float(f.get("minQty"))
                    elif ftype == "LOT_SIZE":
                        # LOT_SIZE is the binding grid for market orders too;
                        # fill gaps only (order-independent of the loop above).
                        if result["minQty"] == 0:
                            result["minQty"] = float(f.get("minQty", 0) or 0)
                        if result["stepSize"] == 0:
                            result["stepSize"] = float(f.get("stepSize", 0) or 0)
                    elif ftype == "NOTIONAL":
                        result["minNotional"] = float(f.get("minNotional", 5.0))
                break
        return result

    def place_market(self, conn, order: dict) -> dict:
        side = "BUY" if order["side"].upper() == "LONG" else "SELL"
        fill = self._submit(conn, order, side)
        # Venue-side protection (2026-10-05): rest the SL/TP bracket on the
        # exchange the moment the entry fills. A failure here only degrades
        # to software monitoring — it must NEVER turn a filled entry into
        # an error (the position exists at the venue regardless).
        self._attach_oco(conn, order, fill, side)
        return fill

    def close_market(self, conn, order: dict) -> dict:
        side = "SELL" if order["side"].upper() == "LONG" else "BUY"
        return self._submit(conn, order, side)

    # ── Venue-side OCO bracket (2026-10-05) ───────────────────────────
    # Spot legs used to rely solely on check_position() in-process: bot
    # offline (or the thread wedged on an MT5 IPC call) = naked position.
    # The MT5 basket gets deal-attached sl/tp from the venue; this gives
    # Binance spot the same guarantee via a resting OCO list.

    def _signed(self, method: str, path: str,
                params: dict | None = None) -> requests.Response:
        """Signed Spot-API request with the shared clock-skew-corrected
        timestamp (same offset _submit uses for market orders)."""
        p = {"timestamp": _binance_ts(), "recvWindow": 10000, **(params or {})}
        query = "&".join(f"{k}={v}" for k, v in p.items())
        sig = hmac.new(config.BINANCE_EXEC_API_SECRET.encode(), query.encode(),
                       hashlib.sha256).hexdigest()
        return requests.request(method, path, params={**p, "signature": sig},
                                headers={"X-MBX-APIKEY": config.BINANCE_EXEC_API_KEY},
                                timeout=20)

    def _tick_size(self, symbol: str) -> float:
        """PRICE_FILTER tickSize for `symbol` (0.0 when unavailable)."""
        import time as _time
        now = _time.time()
        if (BinanceBroker._exchange_info_cache["data"] is None
                or now - BinanceBroker._exchange_info_cache["at"] > self._EXCHANGE_INFO_TTL):
            try:
                resp = requests.get(f"{BINANCE_BASE}/api/v3/exchangeInfo", timeout=10)
                resp.raise_for_status()
                BinanceBroker._exchange_info_cache = {"at": now, "data": resp.json()}
            except Exception:
                pass  # stale cache beats a metadata outage
        data = BinanceBroker._exchange_info_cache["data"] or {}
        for s in data.get("symbols", []):
            if s.get("symbol") == symbol:
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        return float(f.get("tickSize", 0) or 0)
        return 0.0

    @staticmethod
    def _round_price(price: float, tick: float) -> str:
        """Format a price ON the tick grid (nearest tick). An off-grid stop
        gets -1111 'Precision is over the maximum' from the venue."""
        if tick and tick > 0:
            dec = BinanceBroker._step_decimals(tick)
            q = ((_Dec(str(price)) / _Dec(str(tick)))
                 .to_integral_value(rounding=ROUND_HALF_UP) * _Dec(str(tick)))
            return f"{q:.{dec}f}"
        return f"{price:.8f}"

    def place_oco(self, symbol: str, side: str, qty: str, stop: float,
                  tp: float, stop_limit: float | None = None) -> dict:
        """Rest an OCO list on the Spot /api/v3/orderList/oco LEG-BASED schema
        (the venue rejects the legacy flat price/stopPrice form with
        "-1102 Mandatory parameter 'aboveType'" — live 2026-10-05).

        Legs (relative to the exit side):
          SELL exit (long position):  aboveType=TAKE_PROFIT @ tp,
                                      belowType=STOP_LOSS @ stop
          BUY exit  (short covered):  belowType=TAKE_PROFIT @ tp,
                                      aboveType=STOP_LOSS @ stop
        Market-on-trigger legs (TAKE_PROFIT / STOP_LOSS) execute as MARKET
        the moment the trigger touches — a fast tape cannot leave the stop
        resting unfilled, which is why they beat STOP_LOSS_LIMIT here
        (stop_limit is accepted for call compatibility but no longer sent).
        `side` is the EXIT side (SELL after a LONG entry, BUY after a
        covered SHORT). Prices are pre-rounded to the tick grid; `qty` must
        already be on the LOT_SIZE step grid. Returns {orderListId,
        orderIds, listStatus}; raises BrokerError on refusal.
        """
        tick = self._tick_size(symbol)
        params = {
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "listClientOrderId": f"cb-{int(time.time() * 1000)}",
        }
        if side == "SELL":
            params["aboveType"] = "TAKE_PROFIT"
            params["aboveStopPrice"] = self._round_price(tp, tick)
            params["belowType"] = "STOP_LOSS"
            params["belowStopPrice"] = self._round_price(stop, tick)
        else:
            params["belowType"] = "TAKE_PROFIT"
            params["belowStopPrice"] = self._round_price(tp, tick)
            params["aboveType"] = "STOP_LOSS"
            params["aboveStopPrice"] = self._round_price(stop, tick)
        resp = None
        for attempt in range(3):  # same transient-network tolerance as _submit
            try:
                resp = self._signed("POST", BINANCE_OCO_PLACE, params)
                break
            except requests.RequestException as e:
                if attempt >= 2:
                    raise BrokerError(f"binance oco connection failed after 3 tries: {e}")
                time.sleep(1.5 * (attempt + 1))
        if resp is not None and resp.status_code == 400 and '"code":-1021' in resp.text:
            _BINANCE_CLOCK["at"] = 0.0  # clock drift → re-sign once
            resp = self._signed("POST", BINANCE_OCO_PLACE, params)
        if resp.status_code >= 400:
            raise BrokerError(f"binance oco rejected ({resp.status_code}): "
                              f"{resp.text[:300]}")
        d = resp.json()
        return {"orderListId": str(d.get("orderListId")),
                "orderIds": [str(o.get("orderId")) for o in d.get("orders", [])],
                "listStatus": str(d.get("listStatusType")
                                  or d.get("listStatus") or "EXECUTING")}

    def cancel_oco(self, symbol: str, order_list_id: str) -> bool:
        """Cancel a resting OCO list. True when gone; -2011 'Unknown order
        list' counts as gone (already fired/cancelled). A REAL cancel
        failure raises — the caller must know the bracket survived."""
        try:
            resp = self._signed("DELETE", BINANCE_OCO_LIST,
                                {"symbol": symbol,
                                 "orderListId": int(order_list_id)})
        except (ValueError, TypeError):
            return False  # malformed ref — nothing to cancel on the venue
        if resp.status_code >= 400 and '"code":-2011' not in resp.text:
            raise BrokerError(f"binance oco cancel failed ({resp.status_code}): "
                              f"{resp.text[:200]}")
        return True

    def order_fill_price(self, symbol: str, order_id) -> float | None:
        """Executed price of a (filled) order id from /api/v3/myTrades.
        None when unknown — callers fall back to the last candle close."""
        if not order_id:
            return None
        try:
            resp = self._signed("GET", f"{BINANCE_BASE}/api/v3/myTrades",
                                {"symbol": symbol, "limit": 50})
            if resp.status_code >= 400:
                return None
            want = str(order_id)
            for t in resp.json():
                if str(t.get("orderId")) == want:
                    return float(t.get("price") or 0) or None
        except Exception:
            return None
        return None

    def oco_status(self, symbol: str, order_list_id: str) -> dict | None:
        """State of an OCO list: None when unknown/unreachable, else
        {status, fired_leg, orders[]} where fired_leg is 'sl' | 'tp' when
        the list EXECUTED, so reconcile can journal the REAL exit."""
        try:
            # GET /api/v3/orderList takes orderListId ONLY — sending the
            # symbol alongside it 400s with -1104 'Not all sent parameters
            # were read' (live 2026-10-05).
            resp = self._signed("GET", BINANCE_OCO_LIST,
                                {"orderListId": int(order_list_id)})
        except (ValueError, TypeError):
            return None
        if resp.status_code >= 400:
            return None
        try:
            d = resp.json()
        except Exception:
            return None
        status = str(d.get("listStatusType") or "").upper()
        # The list response's orders[] is SLIM ({symbol, orderId,
        # clientOrderId}) — leg type/status/price live on the per-order
        # Query endpoint, so enrich each leg (2 extra signed calls, only
        # for rows that actually carry a bracket).
        fired = None
        legs = []
        for o in d.get("orders", []):
            leg = {"orderId": str(o.get("orderId")), "type": None,
                   "status": None, "price": None, "stopPrice": None}
            try:
                r2 = self._signed("GET", BINANCE_ORDER,
                                  {"symbol": symbol,
                                   "orderId": int(o.get("orderId"))})
                if r2.status_code < 400:
                    od = r2.json()
                    ot = str(od.get("type") or "").upper()
                    ost = str(od.get("status") or "").upper()
                    leg.update(type=ot, status=ost,
                               price=od.get("price"),
                               stopPrice=od.get("stopPrice"))
                    if ost == "FILLED":
                        # A FILLED leg IS the execution proof — the list
                        # status spelling varies (EXECUTED on some paths,
                        # ALL_DONE as the live terminal state, live
                        # 2026-10-06: OCO #457561 came back ALL_DONE with
                        # the SL FILLED and fired_leg was MISSED), so the
                        # mapping must never gate on it. STOP_LOSS
                        # (market-on-trigger, what we place) or legacy
                        # STOP_LOSS_LIMIT → 'sl'; TAKE_PROFIT(_LIMIT) → 'tp'.
                        if ot in ("STOP_LOSS", "STOP_LOSS_LIMIT"):
                            fired = "sl"
                        elif ot in ("TAKE_PROFIT", "TAKE_PROFIT_LIMIT"):
                            fired = "tp"
            except Exception:
                pass  # leg detail is best-effort; the list status still stands
            legs.append(leg)
        return {"status": status or "UNKNOWN", "fired_leg": fired,
                "orders": legs}

    def _attach_oco(self, conn, order: dict, fill: dict, entry_side: str) -> None:
        """Rest the OCO bracket for a filled entry. Best-effort BY DESIGN:
        any skip/failure prints and leaves software monitoring in charge —
        the journal fill result gains `oco_ref` only when the list actually
        rested (so `oco_ref == NULL` always means 'no venue bracket').
        """
        if not config.BINANCE_OCO_ENABLED:
            return
        symbol = order["symbol"].replace("/", "")
        px = float(fill.get("fill_price") or order.get("entry") or 0)
        sl = float(order.get("stop_loss") or 0)
        tp = float(order.get("take_profit") or 0)
        if not (px > 0 and sl > 0 and tp > 0):
            return  # no complete levels → nothing to protect venue-side
        is_long = entry_side == "BUY"
        # Levels must strictly bracket the fill or the venue rejects the
        # whole list — skip loudly instead (software monitor still covers).
        if (is_long and not (sl < px < tp)) or (not is_long and not (tp < px < sl)):
            print(f"[Binance] OCO skipped for {symbol}: levels don't bracket "
                  f"fill {px} (sl={sl}, tp={tp})")
            return
        qty = abs(float(order["qty"]))
        lot = self._get_market_lot_info(symbol)
        if lot["stepSize"] > 0:
            dec = self._step_decimals(lot["stepSize"])
            q = self._floor_to_step(qty, lot["stepSize"])
            if q < _Dec(str(lot["minQty"] or 0)):
                print(f"[Binance] OCO skipped for {symbol}: qty {q} below "
                      f"minQty {lot['minQty']} after step-floor")
                return
            qty_str = f"{q:.{dec}f}"
        else:
            qty_str = f"{qty:.8f}"
        # Legs are market-on-trigger (TAKE_PROFIT / STOP_LOSS): they execute
        # as MARKET the moment the trigger touches, so no limit buffer is
        # needed — a fast tape cannot leave the stop resting unfilled.
        stop_side = "SELL" if is_long else "BUY"
        try:
            res = self.place_oco(symbol, stop_side, qty_str, sl, tp)
            fill["oco_ref"] = res["orderListId"]
            print(f"[Binance] OCO #{res['orderListId']} resting for {symbol} "
                  f"qty={qty_str} sl={sl} tp={tp} — venue-side protection on")
        except BrokerError as e:
            print(f"[Binance] OCO placement failed for {symbol}: {e} — "
                  "software SL/TP monitor stays authoritative")

    def symbol_of(self, symbol: str) -> str:
        """Display symbol → Binance pair, SAME normalization fetch_positions
        reports. 2026-09-30 win-rate post-mortem: this override was missing,
        so the reconcile position pass compared 'BTC/USDT' against
        'BTCUSDT' and judged every Binance holding 'gone' — ghost-closing
        journal rows at the last candle price (pnl ≈ 0) while the actual
        BTC/ETH/SOL accumulated on the exchange, unmanaged."""
        return symbol.replace("/", "").upper()

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
        orders on a flaky metadata call.

        Reads the SIGNED /account cache — this used to call the endpoint
        unsigned, which 400s every time, so the fundability pre-check in
        _submit silently never worked."""
        try:
            d = self._account_snapshot()
            for b in d.get("balances", []):
                if b.get("asset") == asset:
                    return float(b.get("free") or 0)
            return 0.0
        except Exception:
            return None

    def fundability(self, symbol: str, side: str, qty: float,
                    price) -> str | None:
        """None when the venue can fund this order; a reason string when it
        provably cannot (the same message _submit would raise). 'hold'
        when the balance endpoint is unavailable — never block all trading
        on a flaky metadata call.

        A market BUY needs free quote (USDT) ≥ notional; a SELL needs free
        base ≥ qty. Used by execute_intent as a pre-submit check so
        unfundable intents fail fast with a clear reason instead of an
        'error' journal row (live: #403 XRP/USDT SHORT).
        """
        try:
            parts = symbol.split("/")
            base, quote = (parts[0], parts[1]) if len(parts) == 2 \
                else (parts[0], "USDT")
            need_asset = quote if side.upper() == "BUY" else base
            have = self._free_balance(need_asset)
            if have is None:
                return "hold"
            lacking = float(qty) * float(price or 0) if side.upper() == "BUY" \
                else float(qty)
            if have < lacking:
                return (f"binance: insufficient free {need_asset} "
                        f"(have {have:.2f}, need {lacking:.2f}) — "
                        "fund the account or lower risk size")
            return None
        except Exception:
            return "hold"

    def _account_snapshot(self) -> dict:
        """Signed /account JSON with a 30s shared cache (UI polls + the
        fundability pre-check hit it; Binance rate-limits signed calls)."""
        now = time.time()
        c = _binance_account_cache
        if c["data"] is None or now - c["at"] > 30.0:
            # /account is a signed endpoint — an unsigned GET 400s. Sign
            # exactly like _submit does.
            params = {"timestamp": _binance_ts(), "recvWindow": 10000}
            query = "&".join(f"{k}={v}" for k, v in params.items())
            sig = hmac.new(config.BINANCE_EXEC_API_SECRET.encode(), query.encode(),
                           hashlib.sha256).hexdigest()
            resp = requests.get(BINANCE_ACCOUNT,
                                params={**params, "signature": sig},
                                headers={"X-MBX-APIKEY": config.BINANCE_EXEC_API_KEY},
                            timeout=15)
            resp.raise_for_status()
            c["data"] = resp.json()
            c["at"] = now
        return c["data"]

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

        ts = _binance_ts()
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
        if resp is not None and resp.status_code == 400 and '"code":-1021' in resp.text:
            # Clock drift between this box and Binance: force-refresh the
            # measured offset, re-sign with a corrected timestamp and retry
            # once instead of burning the order (-1021).
            _BINANCE_CLOCK["at"] = 0.0
            params["timestamp"] = _binance_ts()
            query = "&".join(f"{k}={v}" for k, v in params.items())
            sig = hmac.new(config.BINANCE_EXEC_API_SECRET.encode(), query.encode(),
                           hashlib.sha256).hexdigest()
            try:
                resp = requests.post(
                    BINANCE_ORDER,
                    params={**params, "signature": sig},
                    headers={"X-MBX-APIKEY": config.BINANCE_EXEC_API_KEY},
                    timeout=20,
                )
            except requests.ConnectionError as e:
                raise BrokerError(f"binance connection failed on -1021 retry: {e}")
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
        per-symbol allOrders query. /openOrders is a SIGNED endpoint — an
        unsigned GET (the old behavior) 400s every time, so reconcile
        always failed."""
        params = {"timestamp": _binance_ts(), "recvWindow": 10000}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(config.BINANCE_EXEC_API_SECRET.encode(), query.encode(),
                       hashlib.sha256).hexdigest()
        resp = requests.get(BINANCE_OPEN_ORDERS,
                            params={**params, "signature": sig},
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

    def fetch_positions(self, strict: bool = False) -> list[dict]:
        """Spot holdings as pseudo-positions: a spot account has no
        leveraged positions — a base-asset balance IS the exposure.
        Lets journal reconciliation detect broker-side sells/resets
        (e.g. a testnet that was wiped). Quote currencies and assets
        without a USDT price are skipped.

        strict=True (used by reconcile): raise instead of returning []
        when the signed snapshot or the price batch fails — a transient
        API blip must NOT look like 'no holdings' or the position pass
        ghost-closes every open journal row (live 2026-09-30: BTC/ETH/SOL
        buys marked broker_closed pnl≈0 while the coins sat on the
        exchange, unmanaged)."""
        try:
            d = self._account_snapshot()
        except Exception:
            if strict:
                raise
            return []
        quotes = {"USDT", "BUSD", "USDC", "FDUSD", "TUSD", "USD"}
        holdings = []
        for b in d.get("balances", []):
            asset = b.get("asset")
            qty = float(b.get("free") or 0) + float(b.get("locked") or 0)
            if not asset or asset in quotes or qty <= 1e-9:
                continue
            holdings.append((asset, qty))
        if not holdings:
            return []
        # One batched price call per 100 symbols. 2026-09-30: a single
        # holding without a USDT pair (airdrop token) made the whole batch
        # 400 — ALL prices dropped, positions looked empty, reconcile
        # ghost-closed every Binance journal row. Fix: pre-filter against
        # the exchangeInfo symbol list (public, cached) so only REAL
        # USDT pairs are batched; junk tokens are skipped upfront instead
        # of poisoning the request.
        valid = set()
        try:
            ex = self._get_market_lot_info.__self__._exchange_info_cache  # noqa: F841
        except Exception:
            ex = None
        try:
            data = BinanceBroker._exchange_info_cache["data"]
            if not data:
                resp = requests.get(f"{BINANCE_BASE}/api/v3/exchangeInfo", timeout=10)
                resp.raise_for_status()
                BinanceBroker._exchange_info_cache = {"at": time.time(),
                                                      "data": resp.json()}
                data = BinanceBroker._exchange_info_cache["data"]
            valid = {s.get("symbol") for s in data.get("symbols", [])}
        except Exception:
            valid = set()  # unknown list → try the batch anyway
        priceable = [(a, q) for a, q in holdings
                     if not valid or f"{a}USDT" in valid]
        if not priceable:
            return []
        prices: dict[str, float] = {}
        for i in range(0, len(priceable), 100):
            chunk = priceable[i:i + 100]
            syms = [f"{a}USDT" for a, _ in chunk]
            try:
                r = requests.get("https://api.binance.com/api/v3/ticker/price",
                                 params={"symbols": json.dumps(syms, separators=(",", ":"))},
                                 timeout=10)
                if r.ok:
                    for d2 in r.json():
                        prices[d2["symbol"]] = float(d2.get("price") or 0)
                elif strict and not prices:
                    raise BrokerError(f"price batch failed: HTTP {r.status_code}")
            except BrokerError:
                raise
            except Exception:
                if strict and not prices:
                    raise
                continue
        if strict and priceable and not prices:
            # Every price lookup failed → the network is down, not just one
            # symbol. The caller must treat this as 'unknown', not 'empty'.
            raise BrokerError("all Binance price lookups failed")
        out = []
        for asset, qty in holdings:
            cur = prices.get(f"{asset}USDT")
            if not cur:
                continue  # no USDT market → not a trackable exposure here
            out.append({"symbol": f"{asset}/USDT", "qty": qty, "side": "long",
                        "entry": None, "current": cur, "profit": None})
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

    def detailed_account_info(self) -> dict:
        """Full account card for the UI — login, holder name, server,
        currency, company, leverage, balances. Read-only."""
        mt5 = self._ensure()
        try:
            i = mt5.account_info()
            if i is None:
                raise BrokerError(f"MT5 account_info failed: {mt5.last_error()}")
            return {
                "login": str(getattr(i, "login", "") or ""),
                "name": getattr(i, "name", "") or "",
                "server": getattr(i, "server", "") or "",
                "currency": getattr(i, "currency", "") or "",
                "company": getattr(i, "company", "") or "",
                "leverage": float(getattr(i, "leverage", 0) or 0),
                "trade_mode": int(getattr(i, "trade_mode", 0) or 0),  # 0=demo 1=contest 2=real
                "balance": float(getattr(i, "balance", 0) or 0),
                "equity": float(getattr(i, "equity", 0) or 0),
                "margin": float(getattr(i, "margin", 0) or 0),
                "margin_free": float(getattr(i, "margin_free", 0) or 0),
                "margin_level": float(getattr(i, "margin_level", 0) or 0),
                "floating_pnl": float(getattr(i, "profit", 0) or 0),
            }
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
        return self._submit(conn, order, side, attach_levels=True)

    def close_market(self, conn, order: dict) -> dict:
        """Close the journal position by closing the OPEN MT5 POSITIONS
        (ticket-scoped opposite DEALs), never by sending a naked opposite
        order. 2026-09-29: ticket-less closes on this hedging account
        OPENED a mirror position instead of closing — 18 phantom positions
        in one night, every SL exit doubling the exposure while the
        original trade kept running."""
        mt5 = self._ensure()
        sym = self.symbol_of(order["symbol"])
        # Exact target when the journal row carries the MT5 POSITION ticket
        # (2026-10-01): closes then hit THAT position and never touch a
        # newer same-symbol position that isn't ours to close. Rows from
        # before the broker_position column (or non-MT5 rows) fall back to
        # the newest-first same-symbol sweep below.
        want_pos = str(order.get("broker_position") or "").strip()
        want_pos = want_pos if want_pos.isdigit() else ""
        try:
            mt5.symbol_select(sym, True)
            positions = mt5.positions_get(symbol=sym) or []
            if want_pos:
                exact = [p for p in positions
                         if str(getattr(p, "ticket", "")) == want_pos]
                if not exact:
                    raise BrokerError(
                        f"MT5 position #{want_pos} for {sym} not open at "
                        "the broker (already closed or ticket mismatch)")
                positions = exact
            if not positions:
                # Nothing open at the broker — the position was closed
                # outside the bot (manual, or the server-side SL/TP we now
                # attach fired first). Journal the close at the live price
                # and place NOTHING (a naked opposite deal would open a
                # hedge on this account).
                tick = mt5.symbol_info_tick(sym)
                px = 0.0
                if tick is not None:
                    px = float(getattr(tick, "bid", 0) or 0)\
                        or float(getattr(tick, "ask", 0) or 0)
                if px <= 0:
                    df = database.load_candles(conn, order["symbol"], "1h", limit=1)
                    px = float(df["close"].iloc[-1]) if not df.empty else 0.0
                if px <= 0:
                    raise BrokerError(
                        f"no open MT5 position for {sym} and no price to "
                        "journal the close")
                return {"status": "already_closed", "fill_price": px,
                        "fill_time": datetime.now(timezone.utc).isoformat(),
                        "fees": 0.0, "broker_ref": ""}
            info = mt5.symbol_info(sym)
            if info is None:
                raise BrokerError(f"MT5 symbol {sym} not found for close")
            volume = self._lots(info, abs(float(order["qty"])))
            if volume <= 0:
                raise BrokerError(f"zero volume for {sym} close "
                                  f"(qty {order['qty']})")
            filling_mode = getattr(info, "filling_mode", 0)
            # 2026-09-30 10013 root cause: `journal.side` is the POSITION's
            # direction (SHORT = we sold). Closing it needs the OPPOSITE
            # deal: LONG → sell, SHORT → buy. The old `!= "LONG"` inverted
            # this and sent a SELL to close the AUD/JPY SHORT — MT5 rejects
            # a same-type deal on a ticket with 10013 (and in the pre-
            # ticket era the same inversion OPENED mirror positions).
            is_long_close = order["side"].upper() == "LONG"
            otype = mt5.ORDER_TYPE_SELL if is_long_close else mt5.ORDER_TYPE_BUY
            # 2026-09-30 close hardening: AUD/JPY died with retcode 10013
            # (Invalid request) cycle after cycle on the exact request that
            # succeeded from a live probe seconds later — a stale tick /
            # filling-mode quirk, not a structurally bad request. Now every
            # attempt re-quotes a FRESH tick, tries FOK then IOC when the
            # symbol allows both, and gets one extra fresh-tick retry.
            fillings = ([mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC]
                        if filling_mode & 1 else [mt5.ORDER_FILLING_IOC])
            attempts = fillings + [fillings[-1]]

            def _send_close(pos, vol, filling):
                tk = mt5.symbol_info_tick(sym)   # fresh per attempt
                if tk is None:
                    raise BrokerError(f"no tick for {sym}")
                req = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": sym,
                    "volume": vol,
                    "type": otype,
                    "position": pos.ticket,   # ← close THIS position, no hedge
                    "price": tk.bid if is_long_close else tk.ask,
                    "deviation": 30,
                    "magic": 20260817,
                    "comment": "cryptoautomation-close",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                result = mt5.order_send(req)
                rc = getattr(result, "retcode", None)
                if result is None or rc != mt5.TRADE_RETCODE_DONE:
                    comment = (getattr(result, "comment", "") or "").strip()
                    err = BrokerError(
                        f"mt5 close rejected for {sym} #{pos.ticket}: "
                        f"retcode {rc}" + (f" — {comment}" if comment else ""))
                    err.retcode = rc
                    raise err
                return result

            # Newest positions first: the position opened most recently (with
            # our attached SL/TP) is consumed first.
            remaining = volume
            last_price, last_deal, last_pos = 0.0, 0, 0
            for pos in sorted(positions, key=lambda p: p.time, reverse=True):
                if remaining <= 1e-9:
                    break
                vol = min(float(pos.volume), remaining)
                result, last_err = None, None
                for attempt, filling in enumerate(attempts):
                    try:
                        result = _send_close(pos, vol, filling)
                        break
                    except BrokerError as e:
                        last_err = e
                        # Transient rejects worth a fresh-tick retry:
                        # 10013 invalid request (stale tick), 10004 requote,
                        # 10020 prices changed, 10021 no quotes, or a None
                        # result (IPC hiccup). Terminal rejects like 10018
                        # market-closed are NOT retried.
                        rc = getattr(e, "retcode", None)
                        retryable = rc in (None, 10004, 10013, 10020, 10021)
                        if attempt + 1 < len(attempts) and retryable:
                            time.sleep(0.3)
                            continue
                        raise
                if result is None:
                    raise last_err or BrokerError(
                        f"mt5 close failed for {sym} #{pos.ticket}")
                last_price = (float(getattr(result, "price", 0) or 0)
                              or last_price)
                last_deal = int(getattr(result, "deal", 0) or 0) or last_deal
                last_pos = int(getattr(pos, "ticket", 0) or 0) or last_pos
                remaining = round(remaining - vol, 8)
            return {"status": "filled", "fill_price": last_price or None,
                    "fill_time": datetime.now(timezone.utc).isoformat(),
                    "fees": 0.0, "broker_ref": str(last_deal),
                    "broker_position": str(last_pos) if last_pos else None}
        finally:
            self._close()

    def _submit(self, conn, order: dict, side: str,
                attach_levels: bool = False) -> dict:
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
            if attach_levels:
                # Server-side protection: attach the bot's SL/TP to the DEAL
                # itself. Without this the position is only monitored by
                # check_position() while this process runs — offline bot =
                # naked position (found live 2026-09-28: 40 open positions
                # with sl=0 on the venue). Levels are validated against the
                # entry side first; a wrong-side level would get the WHOLE
                # order rejected with 'Invalid stops', so a bad level is
                # dropped instead (software monitoring still covers it).
                ref = float(order.get("entry") or order.get("fill_price") or 0)
                sl = float(order.get("stop_loss") or 0)
                tp = float(order.get("take_profit") or 0)
                is_long = side == "buy"
                if ref > 0 and sl > 0 and ((is_long and sl < ref) or (not is_long and sl > ref)):
                    request["sl"] = sl
                if ref > 0 and tp > 0 and ((is_long and tp > ref) or (not is_long and tp < ref)):
                    request["tp"] = tp
            result = mt5.order_send(request)
            if result is None or getattr(result, "retcode", None) != mt5.TRADE_RETCODE_DONE:
                # Report the broker's own comment, not mt5.last_error() — after a
                # rejected order last_error() returns (1, 'Success'), which read
                # as a success in the journal ("10027 (1, 'Success')").
                retcode = getattr(result, "retcode", "?")
                comment = (getattr(result, "comment", "") or "").strip()
                raise BrokerError(
                    f"mt5 order rejected for {sym}: retcode {retcode}"
                    + (f" — {comment}" if comment else ""))
            # Resolve the POSITION ticket this deal opened/affected. Deals are
            # grouped by position id in the terminal — the journal must store
            # THAT ticket (not the entry deal id) so reconciliation and
            # cleanup match the exact position on this hedging account
            # (2026-10-01: broker_ref held the entry DEAL ticket, e.g.
            # AUD/JPY #394 deal 1859451756 vs position 1975186562). Some
            # builds return no `position` field on MqlTradeResult, so fall
            # back to the executed deal's position_id from deal history.
            pos_ticket = int(getattr(result, "position", 0) or 0)
            if not pos_ticket:
                deal_id = int(getattr(result, "deal", 0) or 0)
                if deal_id:
                    try:
                        seed = mt5.history_deals_get(ticket=deal_id) or []
                        pos_ids = {int(getattr(d, "position_id", 0) or 0)
                                   for d in seed}
                        pos_ids.discard(0)
                        if pos_ids:
                            pos_ticket = pos_ids.pop()
                    except Exception:
                        pos_ticket = 0  # best-effort; reconcile self-heals
        finally:
            self._close()
        return {
            "status": "filled",
            "fill_price": float(getattr(result, "price", 0) or 0) or None,
            "fill_time": datetime.now(timezone.utc).isoformat(),
            "fees": 0.0,  # CFD costs are inside the spread
            "broker_ref": str(getattr(result, "deal", 0)
                              or getattr(result, "order", 0)),
            # MT5 POSITION ticket (≠ entry deal id) — see resolution above.
            "broker_position": str(pos_ticket) if pos_ticket else None,
        }

    def modify_position_sltp(self, symbol: str, new_sl: float | None, new_tp: float | None) -> dict:
        """Amend SL/TP on the open MT5 position for `symbol` (TRADE_ACTION_SLTP).
        Pass None to leave a level unchanged. The position is found by symbol;
        if the terminal holds several, the oldest is modified."""
        mt5 = self._ensure()
        sym = self.symbol_of(symbol)
        try:
            mt5.symbol_select(sym, True)
            positions = mt5.positions_get(symbol=sym) or []
            if not positions:
                raise BrokerError(f"no open MT5 position for {sym} to modify")
            pos = sorted(positions, key=lambda p: p.time)[0]
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": sym,
                "sl": float(new_sl) if new_sl else float(pos.sl or 0),
                "tp": float(new_tp) if new_tp else float(pos.tp or 0),
            }
            result = mt5.order_send(request)
            rc = getattr(result, "retcode", None)
            if result is None or rc != mt5.TRADE_RETCODE_DONE:
                raise BrokerError(f"mt5 SLTP modify rejected for {sym}: "
                                  f"{rc} {mt5.last_error()}")
            return {"status": "ok", "sl": request["sl"] or None,
                    "tp": request["tp"] or None, "ticket": pos.ticket}
        finally:
            self._close()

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

    def closing_deal_for_position(self, position_ticket: int) -> dict | None:
        """Ground-truth close for an MT5 position: the OUT (exit) deal from
        the terminal's deal history, keyed by position id.

        2026-09-30 win-rate audit: broker-side closes used to be journaled at
        the last 1h candle close because market IOC DEALs leave no ORDER
        history — which fabricated exits (EUR/USD #392 got pnl=0.0 with
        exit==entry; XAU/USD #391 showed +21.40 while the real deal paid
        +4.62). The history HAS the exact answer.

        NOTE: the journal's broker_ref stores the ENTRY deal ticket, but
        deals are grouped by position id — resolve the entry ticket to its
        position id first (2026-09-30: AUD/JPY #394 fell through to the
        candle fallback, journaling pnl 420.99 for a 0.01-lot trade whose
        real deal paid +3.24, because its ref 1859451756 was the entry deal
        while the position id is 1975186562)."""
        mt5 = self._ensure()
        try:
            deals = mt5.history_deals_get(position=int(position_ticket)) or []
            if not deals:
                # broker_ref may be the ENTRY deal ticket → resolve to the
                # position id and retry.
                seed = (mt5.history_deals_get(
                            ticket=int(position_ticket)) or [])
                pos_ids = {int(getattr(d, "position_id", 0) or 0)
                           for d in seed}
                pos_ids.discard(0)
                for pid in pos_ids:
                    deals = mt5.history_deals_get(position=pid) or []
                    if deals:
                        break
            if not deals:
                return None
            out_deals = [d for d in deals
                         if int(getattr(d, "entry", -1))
                         == int(getattr(mt5, "DEAL_ENTRY_OUT", 1))]
            if not out_deals:
                return None
            d = max(out_deals, key=lambda x: int(getattr(x, "time", 0)))
            profit = float(getattr(d, "profit", 0.0) or 0.0)
            swap = float(getattr(d, "swap", 0.0) or 0.0)
            # Commission may sit on the entry deal — sum the whole position.
            commission = sum(float(getattr(x, "commission", 0.0) or 0.0)
                             for x in deals)
            # The POSITION id the deals belong to — reconcile self-heal
            # stamps this onto journal rows that predate broker_position.
            pos_id = int(getattr(d, "position_id", 0) or 0)
            return {
                "ticket": int(getattr(d, "ticket", 0) or 0),
                "position_id": pos_id or None,
                "price": float(getattr(d, "price", 0.0) or 0.0),
                "time": int(getattr(d, "time", 0) or 0),
                "profit": profit,
                "swap": swap,
                "commission": round(commission, 8),
                "pnl": profit + swap + commission,
            }
        finally:
            self._close()

    def position_id_for_entry_deal(self, deal_ticket: int) -> int | None:
        """Resolve an ENTRY deal ticket to its POSITION id (the terminal
        groups deals by position id). None when it can't be resolved.
        Used by reconcile to self-heal journal rows that predate the
        broker_position column."""
        mt5 = self._ensure()
        try:
            seed = mt5.history_deals_get(ticket=int(deal_ticket)) or []
            pos_ids = {int(getattr(d, "position_id", 0) or 0) for d in seed}
            pos_ids.discard(0)
            return pos_ids.pop() if pos_ids else None
        except Exception:
            return None
        finally:
            self._close()


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
    """The configured broker — the only gateway the executor talks to.
    Every trade MUST land on a real broker we have access to (hybrid /
    mt5 / binance / alpaca). An unknown EXECUTION_BROKER value now fails
    loudly instead of silently falling back to the fake PaperBroker."""
    mode = config.EXECUTION_BROKER
    if mode == "alpaca":
        return AlpacaBroker()
    if mode == "binance":
        return BinanceBroker()
    if mode == "mt5":
        return MT5Broker()
    if mode == "hybrid":
        return HybridBroker()
    if mode == "paper":
        # Explicit only. Nothing may reach the simulated book by accident.
        return PaperBroker()
    raise BrokerError(
        f"EXECUTION_BROKER={mode!r} is not a real broker. "
        "Valid: hybrid, mt5, binance, alpaca (or explicit 'paper' for dry tests). "
        "Set it in .env — refusing to fall back to a simulated broker.")


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


# ── Execution-cost model (journaling + entry veto) ──────────────────
# Estimated slippage in $ for a fill at `fill_price` on `notional`.
# Measured slippage needs the PRE-ORDER mid/quote — none of the fill
# paths carries one today, so every number in this feature is an
# estimate: venue tier bps × notional (risk.slippage_bps). When future
# paths do carry a reference price, |fill − ref| slots in here with no
# schema change.
def _estimate_slippage_cost(symbol: str, qty: float,
                            fill_price: float | None) -> float | None:
    try:
        fp = float(fill_price or 0)
        if fp <= 0:
            return None
        return round(abs(float(qty)) * fp * risk.slippage_bps(symbol) / 10_000.0, 8)
    except Exception:
        return None


def _venue_of(symbol: str) -> str:
    """The broker that actually fills `symbol` (hybrid-aware) — the cost
    table and veto must price the REAL venue, not 'hybrid'."""
    return _actual_broker_for(symbol)


def _rrtp_cost_pct(symbol: str) -> float:
    """Estimated round-trip execution cost for `symbol` as a fraction of
    notional: (slippage + taker fee) × 2 sides, in decimal (1 bp = 1e-4)."""
    return risk.total_cost_bps(symbol) / 10_000.0


def _cost_clearance(intent, entry: float | None) -> str | None:
    """Entry veto: refuse trades whose planned WINNER can't pay 2× the
    estimated round-trip execution cost (2026-10-01).

    Edge = |TP − entry| — the take-profit hit is the plan being sized; the
    stop is the loss case, not the edge. Threshold = COST_VETO_MIN_EDGE_X
    (default 2.0) × round-trip cost fraction × entry. Returns the veto
    reason, or None when the trade clears (or the check can't run —
    missing entry/TP or broker refusal must never false-veto)."""
    if not config.COST_VETO_ENABLED:
        return None
    try:
        tp_lvl = float(getattr(intent, "take_profit", None) or 0)
        entry_lvl = float(entry or getattr(intent, "entry", None) or 0)
        if entry_lvl <= 0 or tp_lvl <= 0:
            return None
        edge = abs(tp_lvl - entry_lvl)
        min_edge = (config.COST_VETO_MIN_EDGE_X
                    * _rrtp_cost_pct(intent.symbol) * entry_lvl)
        if edge < min_edge:
            return (f"cost-veto: TP edge {edge:.6g} < "
                    f"{config.COST_VETO_MIN_EDGE_X:g}× round-trip cost "
                    f"{min_edge:.6g} ({_venue_of(intent.symbol)} "
                    f"{_rrtp_cost_pct(intent.symbol) * 1e4:.1f}bps RT)")
    except Exception:
        return None  # never break sizing on veto-math hiccups
    return None


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
        # Dynamic TP: scale with volatility. 2026-09-30: the old low-vol
        # "quick win" (TP = 1.0x SL distance, R/R 1.0) is the exact
        # small-profit/big-loss profile that bleeds an account — every
        # default now clears the MIN_RR floor.
        if vol_pct < 0.25:
            tp_mult = 1.5   # low vol → symmetric target (R/R 1.5)
        elif vol_pct > 0.75:
            tp_mult = 2.0   # high vol → let winners run
        else:
            tp_mult = 1.5   # mid vol → balanced
    sl_dist = min(sl_dist, entry * 0.05)  # never stop out more than 5% away
    long = direction.upper() == "LONG"
    stop = entry - sl_dist if long else entry + sl_dist
    tp = entry + tp_mult * sl_dist if long else entry - tp_mult * sl_dist
    return round(stop, 8), round(tp, 8)


# ── Phase-2 sizing buckets (roadmap 2026-09-30) ──────────────────────────

def _news_tag(reasons: str | None) -> str:
    """Classify a trade's news reason into ONE stable tag for bucket keys.

    Bucket identity must be reproducible at sizing time and at post-mortem
    time, so this reads only the journal's own `reasons` text ("ai/news
    LONG: <headline> | regime=… tc=…") — never live state. Journal evidence:
    AUD-regulation shorts, BTC-ETF/inflow longs, risk-off shorts, central-
    bank rate-cut chatter. Unrecognized text → "other"."""
    if not reasons:
        return "none"
    s = str(reasons).lower()
    if any(k in s for k in ("central bank", "central-bank", "rate cut",
                            "rate hike", "fomc", "fed ", "ecb ", "boj",
                            "boe ", "policy")):
        return "central_bank"
    if any(k in s for k in ("inflation", "cpi", "pce", "bond yields",
                            "yields")):
        return "inflation"
    if "etf" in s:
        return "etf"
    if "regulat" in s:
        return "regulation"
    if any(k in s for k in ("risk-off", "risk off", "weakness", "strength",
                            "momentum", "inflows", "outflows")):
        return "macro"
    return "other"


def _row_bucket_keys(venue: str, symbol: str, regime: str | None,
                     reasons: str | None) -> list[str]:
    """Bucket identities for one trade, most specific first:
    venue|symbol|regime|tag → venue|regime|tag → venue|regime (real news
    tags only — the generic "other" bucket must not blur into venue-wide)."""
    tag = _news_tag(reasons)
    reg = str(regime or "unknown")
    keys = [f"{venue}|{symbol}|{reg}|{tag}", f"{venue}|{reg}|{tag}"]
    if tag not in ("none", "other"):
        keys.append(f"{venue}|{reg}")
    return keys


def _risk_bucket_keys(conn, symbol: str, regime: str | None,
                      reasons: str | None) -> list[str]:
    """Bucket keys for a NEW trade — the venue that will actually fill it."""
    return _row_bucket_keys(_actual_broker_for(symbol), symbol, regime, reasons)


def _bucket_records(conn, key: str, lookback: int) -> list[dict]:
    """The last `lookback` closed trades belonging to bucket `key`
    (venue|…), newest first, as risk.py-shaped records. Legacy rows
    without entry/SL are skipped by risk.py's usability checks."""
    venue = key.split("|", 1)[0]
    out: list[dict] = []
    try:
        rows = conn.execute(
            "SELECT symbol, trend_regime, reasons, pnl, entry, stop_loss, qty "
            "FROM orders WHERE status = 'closed' AND pnl IS NOT NULL "
            "AND source = ? ORDER BY id DESC LIMIT 800", (venue,)).fetchall()
    except Exception:
        return []
    for r in rows:
        d = dict(r)
        if key in _row_bucket_keys(venue, str(d.get("symbol") or ""),
                                   d.get("trend_regime"), d.get("reasons")):
            out.append({"pnl": d.get("pnl"), "entry": d.get("entry"),
                        "stop_loss": d.get("stop_loss"),
                        "qty": d.get("qty")})
            if len(out) >= lookback:
                break
    return out


def _kelly_multiplier_for(conn, symbol: str, regime: str | None,
                          reasons: str | None) -> float:
    """Rolling-bucket fractional-Kelly multiplier for a NEW trade.
    The most specific bucket holding >= 2 usable closed trades decides
    (1.0 neutral, 0.5–1.5 scaled, 0 when its 95% CI on expectancy is
    negative). Any failure → 1.0: sizing additions must never break the
    order path."""
    if not config.SIZE_KELLY_ENABLED:
        return 1.0
    try:
        for key in _risk_bucket_keys(conn, symbol, regime, reasons):
            recs = _bucket_records(conn, key, config.SIZE_KELLY_LOOKBACK)
            if len(recs) < 2:
                continue
            return risk.bucket_kelly_multiplier(recs)
    except Exception:
        return 1.0
    return 1.0


def _sized_qty(conn, intent, qty: float | None, entry: float | None = None,
               stop: float | None = None,
               telemetry: dict | None = None) -> float:
    """Risk-based size: (risk% × equity) / |entry − SL|, capped by Kelly.
    Equity = the Alpaca account when that's the broker, else journal equity.
    Returns 0.0 when the cost-clearance veto refuses the entry (telemetry
    then carries the veto reason)."""
    if qty is not None and qty > 0:
        return float(qty)
    # ── Cost-clearance veto (2026-10-01): entries whose PLANNED winning
    # trade can't pay 2× the estimated round-trip cost never reach the
    # broker. Edge = |TP − entry| (a TP hit is the plan; SL is the loss
    # case, not the edge). 2× covers cost variance across the round trip
    # (news-tape slippage spikes, requotes). Everything degrades open:
    # unknown entry/TP or broker refusal → 1.0, never a false veto.
    veto = _cost_clearance(intent, entry)
    if veto:
        if telemetry is not None:
            telemetry.update({"veto": veto})
        return 0.0
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
    # ── Phase-2 sizing (roadmap 2026-09-30) ──────────────────────────
    # P1 inverse-vol: risk_pct × (target_vol / realized_vol) with
    # realized_vol = ATR(14)/price on the 1h entry timeframe, clamped
    # [0.5×, 2×]. Neutral 1.0 when ATR/price is unknown — missing data
    # must not invent an opinion.
    vol_mult = 1.0
    if config.SIZE_VOL_ENABLED:
        try:
            atr = _get_atr(conn, intent.symbol, "1h")
            if atr > 0 and entry:
                vol_mult = risk.inverse_vol_multiplier(atr / float(entry))
        except Exception:
            vol_mult = 1.0
    # P2 rolling-bucket Kelly: fractional-Kelly multiplier from the last
    # SIZE_KELLY_LOOKBACK closed trades in the (venue, regime, news-tag)
    # bucket, clamped [0.5×, 1.5×]; 0 when that bucket's 95% CI on
    # expectancy is negative — the roadmap's data-driven gate: a bucket
    # that measurably loses money stops getting size.
    kelly_mult = _kelly_multiplier_for(
        conn, intent.symbol, getattr(intent, "trend_regime", None),
        "; ".join(getattr(intent, "reasons", None) or []))
    if telemetry is not None:
        telemetry.update({"vol_mult": round(vol_mult, 4),
                          "kelly_mult": kelly_mult,
                          "conf_mult": mult})
    # Combined multiplier = product of the two (each individually clamped);
    # kelly 0 (starved bucket) wins over everything → zero risk → the trade
    # is skipped by the caller with an explanatory error.
    combined = min(3.0, max(0.0, vol_mult * kelly_mult))
    risk_pct = min(risk_pct * mult * combined, config.MAX_RISK_PER_TRADE)
    size = risk.position_size(equity, entry or 0, stop or 0, risk_pct)
    if size <= 0:
        return 0.0

    try:
        broker = get_broker()
    except Exception:
        broker = None
    # Lot-aware sizing must also run in hybrid mode for FX/metals/indices —
    # they route to MT5 ("zero volume for USDJPY…" came from skipping this).
    mt5b = None
    if broker is not None:
        if broker.name == "mt5":
            mt5b = broker
        elif broker.name == "hybrid" and not broker._is_crypto(intent.symbol):
            mt5b = getattr(broker, "_mt5", None)
    if mt5b is not None and entry:
        # MT5/Exness (lot-based CFDs): the standard 15% notional cap is
        # BELOW the broker's minimum lot for metals/indices (a 0.01 XAUUSD
        # lot is ~$4.4k), which would zero-size every gold trade. Use
        # MT5_POSITION_PCT instead, floor at the broker's min lot and let
        # the exchange margin rules be the hard backstop. Terminal down /
        # symbol unknown → fall back to the standard cap below.
        try:
            info = mt5b._symbol_info(intent.symbol)
            contract = float(getattr(info, "trade_contract_size", 0) or 100_000.0)
            step = float(getattr(info, "volume_step", 0) or 0.01)
            vmin = float(getattr(info, "volume_min", 0) or 0.01)
            vmax = float(getattr(info, "volume_max", 0) or 100.0)
            vol = mt5b._lots(info, size)
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


def _hash_freed(existing: dict | None) -> bool:
    """True when a finished (closed/error) journal row is past the
    re-entry cooldown, so its day-bucketed intent hash can be released.
    Filled/open rows always hold their hash (no stacking the same trade)."""
    if not existing or existing.get("status") not in ("closed", "error"):
        return False
    done_at = str(existing.get("fill_time")
                  or existing.get("created_at") or "")
    try:
        age_min = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(
                       done_at.replace("Z", "+00:00"))).total_seconds() / 60.0
    except Exception:
        return True  # unparseable timestamp → ancient, treat as stale
    return age_min >= config.REENTRY_COOLDOWN_MIN


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
        # ── Re-entry cooldown (2026-09-30) ───────────────────────────
        # The hash is day-bucketed, so one CLOSED trade used to squat on
        # its symbol+direction for the rest of the UTC day — blocking
        # every re-entry even after the position was long gone (seen
        # live: BTC/USDT LONG held 'duplicate' all evening behind a
        # 0-PnL row closed hours earlier). A finished row past
        # REENTRY_COOLDOWN_MIN releases its hash (archived under a
        # row-unique key) so a fresh signal can trade again.
        if _hash_freed(existing):
            database.archive_order_hash(conn, existing["id"],
                                        intent.intent_hash)
            existing = None
        else:
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
        "trend_regime": getattr(intent, "trend_regime", None),
        "reasons": "; ".join(getattr(intent, "reasons", None) or []),
    }
    # The pre-order mid/backfilled close — the reference for MEASURED entry
    # slippage once fill_price exists (|fill − ref|); None when no price.
    order["slippage_ref"] = order.get("entry")

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

        # ── Risk/reward floor (2026-09-30) ────────────────────────────
        # A trade whose TP is closer than its SL pays the spread AND the
        # odds at the same time — the "always small profit, always big
        # loss" profile. Enforce TP distance >= MIN_RR * SL distance, and
        # cap an unrealistic TP (MAX_TP_PCT) by widening nothing: if the
        # AI/ATR TP is too far, it is pulled back to the cap only when
        # that still clears the RR floor; otherwise the trade is refused.
        is_long = intent.direction == "LONG"
        sl_p = float(order.get("stop_loss") or 0)
        tp_p = float(order.get("take_profit") or 0)
        if sl_p > 0 and tp_p > 0:
            risk_d = abs(entry - sl_p)
            rew_d = abs(tp_p - entry)
            if risk_d > 0:
                rr = rew_d / risk_d
                cap_d = entry * (config.MAX_TP_PCT / 100.0)
                if rew_d > cap_d:
                    rew_d = cap_d
                    order["take_profit"] = round(
                        entry + cap_d if is_long else entry - cap_d, 8)
                if rew_d / risk_d < config.MIN_RR:
                    raise ValueError(
                        f"rr_floor: TP {abs((order.get('take_profit') or 0) - entry):.5f} "
                        f"vs SL {risk_d:.5f} (R/R {rr:.2f} < {config.MIN_RR}) "
                        f"for {intent.symbol} — low-reward risk refused")

    size_meta: dict = {}
    size = _sized_qty(conn, intent, qty, order.get("entry"),
                      order.get("stop_loss"), telemetry=size_meta)
    if size <= 0:
        # Distinguish WHY the sizer refused — a cost veto or a starved
        # Kelly bucket is a deliberate data-driven refusal, not a data
        # problem. The veto reason IS the message when present.
        why = (size_meta.get("veto")
               or ("zero size (kelly bucket starved: negative expectancy "
                   "CI for its venue/regime/news-tag bucket)"
                   if size_meta.get("kelly_mult") == 0
                   else "zero size (no equity or no stop distance)"))
        return {"order": None, "duplicate": False, "dry_run": dry_run,
                "error": why}
    order["qty"] = size
    # Entry slippage estimate (venue bps × entry notional). When the fill
    # path later provides a pre-order reference price, |fill − ref| replaces
    # this as the measured number — same column, better data.
    order["slippage_cost"] = _estimate_slippage_cost(
        intent.symbol, size, order.get("entry"))
    # Size forensics on the row itself (keyword-safe: _news_tag only reads
    # its own keyword substrings, which this never introduces).
    if size_meta:
        order["reasons"] = (order["reasons"] + " | " if order["reasons"]
                            else "") + \
            "size=vol{vol},kelly{kelly},conf{conf}".format(
                vol=size_meta.get("vol_mult"), kelly=size_meta.get("kelly_mult"),
                conf=size_meta.get("conf_mult"))

    if dry_run:
        order["status"] = "dry_run"
        # Surface the cost veto in previews too — a vetoed dry-run row
        # explains WHY the trade wouldn't be taken live.
        if size_meta.get("veto"):
            order["error"] = size_meta["veto"]
        database.record_order(conn, order)
        return {"order": order, "duplicate": False, "dry_run": True}

    # Drop any preview row for this intent so the real order takes its place.
    database.discard_dry_run(conn, intent.intent_hash)

    # ── Balance precheck (2026-10-05) ────────────────────────────
    # Intents the venue cannot fund used to die at SUBMIT as an 'error'
    # journal row (live: #403 XRP/USDT SHORT — binance held 349 free XRP,
    # the position needed 2177). Check BEFORE submitting: return the same
    # reason the broker would raise so the intent is held (no journal row,
    # no half-state), and HOLD instead of blocking when the balance
    # endpoint is unavailable. Runs after the dry-run return: previews must
    # never depend on venue reachability. Sizing already happened, so the
    # check uses the honest final `size`.
    broker = get_broker()
    rb = broker._get_broker_for(intent.symbol) \
        if broker.name == "hybrid" else broker
    if hasattr(rb, "fundability"):
        fund = rb.fundability(
            intent.symbol, "BUY" if intent.direction == "LONG" else "SELL",
            size, order.get("entry"))
        if fund:
            return {"order": None, "duplicate": False, "dry_run": dry_run,
                    "error": fund}

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

    # Entry slippage: MEASURED |fill − ref| when the fill path carried a
    # pre-order reference price (paper broker today; others when they do),
    # else the venue-tier bps estimate recorded during sizing.
    ref_price = fill.get("ref_price")
    fill_price = fill.get("fill_price")
    if ref_price and fill_price:
        order["slippage_cost"] = round(
            abs(float(order["qty"]))
            * abs(float(fill_price) - float(ref_price)), 8)
    order.update({
        "status": fill.get("status", "error"),
        "fill_price": fill.get("fill_price"),
        "fill_time": fill.get("fill_time"),
        "fees": fill.get("fees", 0.0),
        # The broker's order id is what reconciliation matches on (and what
        # lets the position pass journal broker-side closes). Dropping it
        # here silently orphaned every real order from the broker.
        "broker_ref": fill.get("broker_ref"),
        # MT5 POSITION ticket (2026-10-01): the exact broker position this
        # row owns — closes and reconcile hit THIS ticket, not a symbol
        # guess. None for brokers without position ids (crypto spot).
        "broker_position": fill.get("broker_position"),
        # Binance OCO orderListId when the venue-side bracket rested
        # (2026-10-05). NULL = no bracket → software monitor is the only
        # SL/TP for that row.
        "oco_ref": fill.get("oco_ref"),
    })
    rowid = database.record_order(conn, order)
    if rowid == 0:
        # Hash still occupied (a row inserted between our check and write).
        existing = database.order_for_intent(conn, intent.intent_hash)
        if _hash_freed(existing):
            database.archive_order_hash(conn, existing["id"],
                                        intent.intent_hash)
            rowid = database.record_order(conn, order)
            if rowid == 0:
                return {"order": database.order_for_intent(
                            conn, intent.intent_hash),
                        "duplicate": True, "dry_run": False}
        elif existing is not None:
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

    # ── Venue-side bracket lifecycle (2026-10-05) ────────────────────
    # The journal is the trade's brain: when IT closes the position, the
    # resting OCO must not outlive it (a live bracket would sell the next
    # refill). And if the venue ALREADY fired a leg (we were offline), the
    # coins are gone — a market close would sell air. In that case journal
    # the exit with the TRUE leg ('sl'/'tp') and the OCO's real fill price
    # instead of issuing an impossible close.
    oco_ref = str(order.get("oco_ref") or "").strip()
    fired = None
    leg_orders: list = []
    if oco_ref:
        broker = get_broker()
        if broker.name == "hybrid":
            broker = broker._get_broker_for(order["symbol"])
        venue_sym = (broker.symbol_of(order["symbol"])
                     if hasattr(broker, "symbol_of") else order["symbol"])
        if hasattr(broker, "oco_status"):
            try:
                st = broker.oco_status(venue_sym, oco_ref)
                if st and st.get("fired_leg"):
                    fired = st["fired_leg"]
                    leg_orders = [o for o in st.get("orders", [])
                                  if str(o.get("status") or "").upper() == "FILLED"]
            except Exception:
                pass  # status check is advisory — never blocks the close
        if hasattr(broker, "cancel_oco"):
            try:
                if broker.cancel_oco(venue_sym, oco_ref):
                    print(f"  [OCO] #{oco_ref} cancelled — journal closed "
                          "the position first")
            except Exception as e:
                print(f"  [OCO] cancel failed for #{oco_ref}: {e} — bracket "
                      "may rest at the venue; reconcile will clean it up")
    if fired:
        px = None
        for lo in leg_orders:
            try:
                px = broker.order_fill_price(venue_sym, lo.get("orderId"))
            except Exception:
                px = None
            if px:
                break
        if not px:
            df = database.load_candles(conn, order["symbol"], "1h", limit=3)
            px = float(df["close"].iloc[-1]) if not df.empty else None
        if px:
            # Recurse WITHOUT the bracket ref: the second pass must not
            # re-query the fired list (infinite loop) — it just journals
            # the exit at the real fill price with the true leg reason.
            return close_position(conn, {**order, "oco_ref": None},
                                  price=float(px), reason=fired)
        return {"order": order, "pnl": 0.0,
                "reason": f"oco_fired_{fired}_no_price"}

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
    # Journal the EXIT slippage estimate — update_order_exit adds it to the
    # entry estimate recorded at fill time (round-trip total on the row).
    exit_slip = _estimate_slippage_cost(order["symbol"], qty, exit_price)
    if order["side"].upper() == "LONG":
        gross = (exit_price - entry) * qty
    else:
        gross = (entry - exit_price) * qty
    pnl = gross - entry_fees - exit_fees

    database.update_order_exit(conn, order["id"], round(exit_price, 8),
                               fill.get("fill_time") or datetime.now(timezone.utc).isoformat(),
                               reason, round(pnl, 8), round(entry_fees + exit_fees, 8),
                               slippage_cost=exit_slip)
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
        else:
            # 2026-10-06: the venue bracket still protects the ORIGINAL
            # stop — a trailed journal stop that never reaches the
            # exchange is protection theater. Sync it (best-effort).
            try:
                _sync_oco_with_stop(conn, order, new_sl)
            except Exception as e:
                print(f"[Binance] OCO trail sync error for "
                      f"{order.get('symbol')}: {e}")


def _sync_oco_with_stop(conn, order: dict, new_sl: float) -> None:
    """Re-place the venue OCO bracket at a trailed stop (2026-10-06).

    _update_trailing_stop moves the JOURNAL stop; this moves the EXCHANGE
    bracket to match: cancel the old list, re-place at (new stop, same
    TP, same qty), and stamp the NEW orderListId on the row. Best-effort
    by design:
      - cancel reports already-gone (fired/cancelled) → return; the
        reconcile fired-OCO pass owns that case.
      - cancel ok but re-place fails → journal oco_ref=NULL (truthful:
        no venue bracket any more) and print LOUD — software monitoring
        is then the only protection until the next trail update.
    Rows without an oco_ref or on a broker without OCO calls: untouched.
    """
    oco_ref = order.get("oco_ref")
    if not oco_ref or not order.get("id"):
        return
    broker = get_broker()
    rb = broker._get_broker_for(order["symbol"]) \
        if broker.name == "hybrid" else broker
    if not (hasattr(rb, "cancel_oco") and hasattr(rb, "place_oco")):
        return  # no venue-bracket concept on this broker
    venue_symbol = rb.symbol_of(order["symbol"])
    is_long = str(order.get("side") or "").upper() == "LONG"
    exit_side = "SELL" if is_long else "BUY"   # a long exits by selling
    tp = float(order.get("take_profit") or 0)
    if tp <= 0:
        return  # no TP → nothing to re-place around
    qty = abs(float(order.get("qty") or 0))
    try:  # re-floor to the venue LOT_SIZE grid like _attach_oco does
        lot = rb._get_market_lot_info(venue_symbol)
        if lot and float(lot.get("stepSize") or 0) > 0:
            qty = rb._floor_to_step(qty, float(lot["stepSize"]))
    except Exception:
        pass  # fake brokers / missing info — send the journal qty as-is
    try:
        if not rb.cancel_oco(venue_symbol, str(oco_ref)):
            return  # list already gone — reconcile handles a fired leg
    except Exception as e:
        print(f"[Binance] OCO cancel FAILED during trail sync for "
              f"{order['symbol']} (#{order['id']}): {e} — old bracket "
              "left resting at the original stop")
        return
    try:
        res = rb.place_oco(venue_symbol, exit_side, qty, new_sl, tp)
    except Exception as e:
        try:  # truthful journal: the bracket is GONE, not re-placed
            conn.execute("UPDATE orders SET oco_ref = NULL WHERE id = ?",
                         (order["id"],))
            conn.commit()
        except Exception:
            pass
        print(f"[Binance] OCO re-place FAILED for {order['symbol']} "
              f"(#{order['id']}) at trailed stop {new_sl}: {e} — "
              "software monitoring is the only protection now")
        return
    new_ref = str((res or {}).get("orderListId") or "")
    if new_ref:
        try:
            conn.execute("UPDATE orders SET oco_ref = ? WHERE id = ?",
                         (new_ref, order["id"]))
            conn.commit()
        except Exception:
            pass
    print(f"[Binance] OCO re-placed at trailed stop for "
          f"{order['symbol']} (#{order['id']}): sl={new_sl} tp={tp} "
          f"list={new_ref or '?'}")


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
_reconcile_state: dict = {"at": None, "ok": None, "updated": 0, "error": ""}


def reconcile_orders(conn, limit: int = 100) -> list[dict]:
    """Journal ↔ broker sync (see _reconcile_orders_impl) with health
    tracking: /api/broker-health reports the last run's time, outcome and
    error so the UI can show sync freshness at a glance."""
    started = time.time()
    try:
        updated = _reconcile_orders_impl(conn, limit)
    except Exception as e:
        _reconcile_state.update({"at": started, "ok": False, "updated": 0,
                                 "error": str(e)[:200]})
        raise
    _reconcile_state.update({"at": started, "ok": True,
                             "updated": len(updated), "error": ""})
    return updated


def _reconcile_orders_impl(conn, limit: int = 100) -> list[dict]:
    """
    Pull the broker's order list and sync every journal order that has a
    broker_ref: status (pending_new → filled, canceled → cancelled), fill
    price/time and fees. Returns the list of updated order summaries.

    No-op for the offline paper broker (nothing to reconcile). Network is
    wrapped defensively — a broker hiccup never breaks the bot cycle.

    Hybrid mode IS supported: rows are grouped by their journal source and
    each group is checked against that actual sub-broker (crypto → Binance
    spot holdings, FX/metals → MT5 positions). Without this, broker-side
    closes (manual exits in Exness, a wiped Binance testnet) never reached
    the journal while AUTO was off.
    """
    updated = []
    # ── Ghost scrub: an OPEN row must never carry exit fields (2026-09-30:
    # rows 386/387/400 sat at status 'filled' with an exit_time stamped by an
    # aborted close path — reports then read a row that was simultaneously
    # open and half-closed). Clear dangling exit fields on non-closed rows.
    try:
        conn.execute(
            "UPDATE orders SET exit_price = NULL, exit_time = NULL, "
            "exit_reason = NULL, pnl = NULL "
            "WHERE status NOT IN ('closed', 'cancelled', 'canceled', 'error') "
            "AND (exit_price IS NOT NULL OR exit_time IS NOT NULL "
            "OR exit_reason IS NOT NULL OR pnl IS NOT NULL)")
        conn.commit()
    except Exception:
        pass  # journal-only hygiene — never blocks reconciliation
    try:
        broker = get_broker()
    except BrokerError:
        return updated
    if broker.name == "paper":
        return updated

    # Resolve the per-symbol broker: hybrid fans out to its sub-brokers,
    # everything else is a single broker for all rows.
    def _broker_for(sym: str) -> Broker:
        if broker.name == "hybrid":
            return broker._get_broker_for(sym)
        return broker

    class _BrokerUnreachable(Exception):
        """The broker's position snapshot FAILED — not 'empty'."""

    def _positions_of(b: Broker) -> set[str]:
        """Symbols with live exposure at broker b, normalized for compare.
        2026-09-30 win-rate post-mortem: a FAILED snapshot (network, auth,
        rate limit) used to return an empty set — indistinguishable from
        'no positions' — so every open journal row was closed at the last
        candle price while the assets sat unmanaged at the broker (live:
        BTC/ETH/SOL buys marked broker_closed pnl=0.0). Now it RAISES and
        the caller skips the pass. BinanceBroker.fetch_positions runs in
        strict mode so its internal fail-opens can't hide either."""
        try:
            if getattr(b, "name", "") == "binance":
                rows = b.fetch_positions(strict=True)
            else:
                rows = b.fetch_positions()
            return {str(p["symbol"]).replace("/", "").upper() for p in rows
                    if abs(float(p.get("qty") or 0)) > 1e-9}
        except Exception as e:
            raise _BrokerUnreachable(str(e)[:160]) from e

    try:
        remote = broker.fetch_orders(limit=limit)
    except Exception as e:
        raise BrokerError(f"reconcile fetch failed: {e}") from e
    by_id = {o["id"]: o for o in remote}

    # ── Foreign-fill pass: FILLED broker orders that our journal knows
    # nothing about (no matching broker_ref) were placed OUTSIDE this bot
    # (manual, another tool, an earlier config). Flag them so the dashboard
    # can warn instead of silently ignoring broker-side activity.
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

    # ── Position pass: a journal order whose broker position is GONE was
    # closed broker-side (manually in the broker dashboard, a stop/TP hit,
    # or a wiped testnet). Hybrid resolves each row to its real sub-broker,
    # so crypto rows are checked against Binance spot holdings and FX rows
    # against MT5 positions — with per-broker position caches.
    broker_down = False  # set True only when a snapshot FAILS (skip notice)
    if broker.name in ("alpaca", "mt5", "hybrid"):
        pos_cache: dict[str, set[str]] = {}
        for journal in database.load_orders(conn, limit=2000):
            if journal.get("status") not in ("filled", "open"):
                continue
            if not journal.get("broker_ref"):
                continue
            rb = _broker_for(journal["symbol"])
            if rb.name not in ("alpaca", "mt5", "binance"):
                continue
            if rb.name not in pos_cache:
                try:
                    pos_cache[rb.name] = _positions_of(rb)
                except _BrokerUnreachable:
                    # Broker snapshot FAILED: skip the whole pass. Closing
                    # journal rows without broker evidence fabricates
                    # outcomes (pnl=0.0 ghosts) and abandons real assets.
                    print(f"  reconcile: {rb.name} snapshot failed — "
                          "position pass skipped this run")
                    broker_down = True
                    break
            open_syms = pos_cache[rb.name]
            sym = rb.symbol_of(journal["symbol"]).replace("/", "").upper()
            # Self-heal stamping: OPEN MT5 rows that predate the
            # broker_position column get their exact POSITION ticket
            # resolved from the entry deal — so the NEXT close/reconcile
            # targets the precise position instead of a symbol sweep
            # (2026-10-01: #402 was placed by the pre-column build). Runs
            # only for rows missing the stamp, so it costs nothing once
            # every row is stamped.
            if (rb.name == "mt5"
                    and not str(journal.get("broker_position") or "").strip()
                    and str(journal.get("broker_ref") or "").isdigit()
                    and hasattr(rb, "position_id_for_entry_deal")):
                try:
                    pid = rb.position_id_for_entry_deal(
                        int(journal["broker_ref"]))
                except Exception:
                    pid = None
                if pid:
                    conn.execute(
                        "UPDATE orders SET broker_position = ? WHERE id = ?",
                        (str(pid), journal["id"]))
                    conn.commit()
                    journal["broker_position"] = str(pid)
                    updated.append({
                        "id": journal["id"], "symbol": journal["symbol"],
                        "stamped_position": str(pid),
                        "source": "mt5_deal_history"})
            if sym in open_syms:
                continue
            # Grace window: a row filled seconds ago must never be declared
            # dead on snapshot evidence (2026-09-29: BTC/ETH/SOL buys were
            # closed as broker_closed at the same candle price minutes after
            # filling, because the holdings snapshot raced the fills).
            filled_at = str(journal.get("fill_time")
                            or journal.get("created_at") or "")
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(
                           filled_at.replace("Z", "+00:00"))).total_seconds()
                if 0 <= age < 900:
                    continue
            except Exception:
                pass
            # Second look with a fresh position query before killing the row.
            try:
                fresh = _positions_of(rb)
            except _BrokerUnreachable:
                print(f"  reconcile: {rb.name} re-query failed — row "
                      f"#{journal['id']} left open this run")
                broker_down = True
                break
            pos_cache[rb.name] = fresh
            if sym in fresh:
                continue
            close = None
            # NOTE: use THIS row's broker_ref — `ref` is a leftover from the
            # order-history pass above and may point at another row's deal
            # (caught by test_close_hardening: the ghost BTC row would have
            # been journaled with the EUR/USD closing deal).
            row_ref = str(journal.get("broker_ref") or "")
            if rb.name == "mt5" and row_ref.isdigit():
                # MT5 market IOC DEALs leave no order history, but the
                # terminal's DEAL HISTORY holds the real closing deal —
                # journal the broker's own numbers instead of a candle-close
                # guess (2026-09-30: candle-close journaling fabricated
                # exits — EUR/USD #392 pnl=0.0 with exit==entry, XAU/USD
                # #391 +21.40 vs the real +4.62).
                # 2026-10-01: try the row's exact POSITION ticket first
                # (broker_position), then the legacy entry-deal ref — a
                # stale/foreign stamp must never lose the deal-history
                # numbers the entry-deal path can still resolve.
                row_pos = str(journal.get("broker_position") or "").strip()
                candidates = ([int(row_pos)] if row_pos.isdigit() else []) \
                    + [int(row_ref)]
                deal = None
                for cand in candidates:
                    try:
                        deal = rb.closing_deal_for_position(cand)
                    except Exception as e:
                        print(f"  reconcile: mt5 deal history unavailable "
                              f"for #{journal['id']} ({e}) — trying next ref")
                        deal = None
                    if deal and deal.get("price", 0) > 0:
                        break
                if deal and deal.get("price", 0) > 0:
                    exit_iso = (datetime.fromtimestamp(
                                    deal["time"], tz=timezone.utc).isoformat()
                                if deal.get("time")
                                else datetime.now(timezone.utc).isoformat())
                    database.update_order_exit(
                        conn, journal["id"], round(deal["price"], 8),
                        exit_iso, "broker_closed", round(deal["pnl"], 8),
                        float(journal.get("fees") or 0.0),
                        broker_position=deal.get("position_id"),
                        slippage_cost=_estimate_slippage_cost(
                            journal["symbol"],
                            abs(float(journal["qty"])),
                            deal["price"]))
                    updated.append({
                        "id": journal["id"], "symbol": journal["symbol"],
                        "closed": True, "exit_reason": "broker_closed",
                        "exit_price": round(deal["price"], 8),
                        "pnl": round(deal["pnl"], 8),
                        "side": journal["side"], "qty": abs(float(journal["qty"])),
                        "source": "mt5_deal_history",
                        "deal_ticket": deal.get("ticket"),
                    })
                    continue
            if rb.name == "alpaca":
                close = _find_closing_order(remote or [], journal, sym)
            entry = float(journal.get("entry") or journal.get("fill_price") or 0)
            if close:
                exit_price = float(close["fill_price"])
                exit_time = close.get("fill_time") or close.get("created_at") or ""
                exit_fees = float(close.get("fees") or 0)
            else:
                # No order history (MT5/Binance spot): mark the exit at
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

    # ── Fired-OCO pass (2026-10-05): a Binance row with a resting OCO whose
    # list EXECUTED at the venue (SL or TP leg fired while this bot was
    # offline/wedged — the very scenario the bracket exists for). The coins
    # are already gone; without this pass the position pass would see no
    # holding and ghost-close the row at a candle price with the wrong
    # reason. Journal the REAL leg ('sl'/'tp') at the OCO fill price.
    if broker.name in ("binance", "hybrid"):
        for journal in database.load_orders(conn, limit=2000):
            if journal.get("status") not in ("filled", "open"):
                continue
            oref = str(journal.get("oco_ref") or "").strip()
            if not oref:
                continue
            rb = _broker_for(journal["symbol"])
            if rb.name != "binance" or not hasattr(rb, "oco_status"):
                continue
            try:
                st = rb.oco_status(rb.symbol_of(journal["symbol"]), oref)
            except Exception:
                st = None
            if not st or not st.get("fired_leg"):
                continue
            fired = st["fired_leg"]
            px = None
            for lo in st.get("orders", []):
                if str(lo.get("status") or "").upper() != "FILLED":
                    continue
                try:
                    px = rb.order_fill_price(
                        rb.symbol_of(journal["symbol"]), lo.get("orderId"))
                except Exception:
                    px = None
                if px:
                    break
            if not px:
                df = database.load_candles(conn, journal["symbol"], "1h", limit=3)
                px = float(df["close"].iloc[-1]) if not df.empty else None
            if not px:
                continue  # no price evidence → leave the row for next run
            qty = abs(float(journal["qty"]))
            entry = float(journal.get("entry") or journal.get("fill_price") or 0)
            exit_fees = qty * float(px) * risk.fee_bps() / 10_000.0
            entry_fees = float(journal.get("fees") or 0)
            if str(journal["side"]).upper() == "LONG":
                gross = (px - entry) * qty
            else:
                gross = (entry - px) * qty
            pnl = round(gross - entry_fees - exit_fees, 8)
            database.update_order_exit(
                conn, journal["id"], round(px, 8),
                datetime.now(timezone.utc).isoformat(), fired, pnl,
                round(entry_fees + exit_fees, 8),
                slippage_cost=_estimate_slippage_cost(
                    journal["symbol"], qty, px))
            updated.append({"id": journal["id"], "symbol": journal["symbol"],
                            "closed": True, "exit_reason": fired,
                            "exit_price": round(px, 8), "pnl": pnl,
                            "side": journal["side"], "qty": qty,
                            "source": "binance_oco"})

    # ── Deal-history correction pass: rows already marked broker_closed whose
    # numbers were journaled from candle-close guesses (pre-2026-09-30). The
    # MT5 closing deal is ground truth — correct price/pnl/exit_time where
    # they disagree. Only the broker_closed bucket is audited; manual/sl/tp
    # exits were journaled from real fills and are left alone.
    if broker.name in ("mt5", "hybrid"):
        for journal in database.load_orders(conn, limit=2000):
            if journal.get("status") != "closed":
                continue
            if str(journal.get("exit_reason") or "") != "broker_closed":
                continue
            ref = str(journal.get("broker_ref") or "")
            if not ref.isdigit():
                continue
            rb = _broker_for(journal["symbol"])
            if rb.name != "mt5":
                continue
            try:
                row_pos = str(journal.get("broker_position") or "").strip()
                deal = None
                for cand in ([int(row_pos)] if row_pos.isdigit() else []) \
                        + [int(ref)]:
                    deal = rb.closing_deal_for_position(cand)
                    if deal and deal.get("price", 0) > 0:
                        break
            except Exception:
                continue  # history unavailable — never break reconciliation
            if not deal or deal.get("price", 0) <= 0:
                continue
            cur_pnl = float(journal.get("pnl") or 0.0)
            cur_px = float(journal.get("exit_price") or 0.0)
            if (abs(deal["pnl"] - cur_pnl) < 0.005
                    and abs(deal["price"] - cur_px) < 1e-8):
                # Numbers already truthful — but rows that predate the
                # broker_position column still lack the exact POSITION
                # ticket. The deal history knows it (all deals of the
                # position share position_id): stamp it once so future
                # reconciliation/cleanup matches the position EXACTLY and
                # never needs the entry-deal fallback again.
                if (not str(journal.get("broker_position") or "").strip()
                        and deal.get("position_id")):
                    conn.execute(
                        "UPDATE orders SET broker_position = ? WHERE id = ?",
                        (str(deal["position_id"]), journal["id"]))
                    conn.commit()
                    updated.append({
                        "id": journal["id"], "symbol": journal["symbol"],
                        "stamped_position": str(deal["position_id"]),
                        "source": "mt5_deal_history"})
                continue  # already truthful
            sets = ["exit_price = ?", "pnl = ?",
                    "broker_position = COALESCE(broker_position, ?)"]
            vals = [round(deal["price"], 8), round(deal["pnl"], 8),
                    str(deal["position_id"]) if deal.get("position_id") else None]
            if deal.get("time"):
                sets.append("exit_time = ?")
                vals.append(datetime.fromtimestamp(
                    deal["time"], tz=timezone.utc).isoformat())
            vals.append(journal["id"])
            conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id = ?",
                         vals)
            conn.commit()
            updated.append({"id": journal["id"], "symbol": journal["symbol"],
                            "corrected": True, "old_pnl": round(cur_pnl, 8),
                            "pnl": round(deal["pnl"], 8),
                            "exit_price": round(deal["price"], 8),
                            "source": "mt5_deal_history"})

    if broker_down:
        # Surface the skip so the dashboard/analytics know rows were left
        # open deliberately (no broker evidence), not by omission.
        updated.append({"skipped": "position pass aborted: broker snapshot "
                                    "failed — open rows left untouched"})
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


_binance_account_cache = {"at": 0.0, "data": None}


def _mt5_account_card() -> dict:
    """Live MT5/Exness account details for the dashboard card — read-only."""
    i = MT5Broker().detailed_account_info()
    mode = {0: "Demo", 1: "Contest"}.get(i.get("trade_mode"), "Real")
    return {
        "broker": "mt5",
        "name": f"Exness · MT5",
        "account_type": f"{mode} · 1:{i.get('leverage') or '—'}",
        "currency": i.get("currency") or "USD",
        "balance": i.get("balance"),
        "equity": i.get("equity"),
        "floating_pnl": i.get("floating_pnl"),
        "leverage": i.get("leverage") or 0,
        "details": [
            ["Login", i.get("login") or "—"],
            ["Holder", i.get("name") or "—"],
            ["Server", i.get("server") or "—"],
            ["Company", i.get("company") or "—"],
            ["Account", mode],
            ["Margin", str(i.get("margin") or 0)],
            ["Free margin", str(i.get("margin_free") or 0)],
            ["Margin level", f"{i.get('margin_level') or 0:.1f}%"],
        ],
    }


def _binance_account_card() -> dict:
    """Signed GET /api/v3/account on the EXEC spot account. Cached 30s —
    the UI polls and Binance rate-limits signed account calls."""
    d = BinanceBroker()._account_snapshot()
    balances = [b for b in d.get("balances", [])
                if float(b.get("free") or 0) + float(b.get("locked") or 0) > 0]
    usdt = next((b for b in balances if b.get("asset") == "USDT"), {})
    free, locked = float(usdt.get("free") or 0), float(usdt.get("locked") or 0)
    key = config.BINANCE_EXEC_API_KEY or ""
    return {
        "broker": "binance",
        "name": "Binance Spot (testnet)",
        "account_type": "Spot · exec API",
        "currency": "USDT",
        "balance": free + locked,
        "equity": free + locked,
        "floating_pnl": 0.0,
        "details": [
            ["API key", f"…{key[-6:]}" if len(key) > 6 else "—"],
            ["Server", "testnet.binance.vision"],
            ["Free USDT", f"{free:.2f}"],
            ["Locked USDT", f"{locked:.2f}"],
            ["Assets held", str(len(balances))],
        ],
    }


def _alpaca_account_card() -> dict:
    """GET /v2/account on the Alpaca paper (or live) account."""
    b = AlpacaBroker()
    d = requests.get(f"{b.base}/v2/account", headers=b.headers, timeout=15).json()
    num = str(d.get("account_number") or "—")
    return {
        "broker": "alpaca",
        "name": "Alpaca (paper)",
        "account_type": str(d.get("status") or "—"),
        "currency": d.get("currency") or "USD",
        "balance": float(d.get("equity") or 0),
        "equity": float(d.get("equity") or 0),
        "floating_pnl": float(d.get("equity") or 0) - float(d.get("last_equity") or 0),
        "details": [
            ["Account #", num],
            ["Status", str(d.get("status") or "—")],
            ["Market", "paper" if "paper" in b.base else "live"],
            ["Cash", f"{float(d.get('cash') or 0):.2f}"],
            ["Buying power", f"{float(d.get('buying_power') or 0):.2f}"],
            ["Portfolio value", f"{float(d.get('portfolio_value') or 0):.2f}"],
        ],
    }


# ── Broker registry: THE single place to add a new account ──────────
# One entry per ACCOUNT. The dashboard (donuts, tags, account cards, journal
# filters, routing metadata) is generated from this — adding an account means
# adding ONE entry here (+ an account-card builder + its .env keys) and every
# surface picks it up. No frontend edits.
#   key     — journal `source` value (orders rows carry this; NEVER rename
#             once trades exist)
#   name    — display name everywhere in the UI
#   letter  — one-letter tag badge
#   color   — accent color (tags, donut dots)
#   modes   — EXECUTION_BROKER values under which this account is active
BROKER_REGISTRY: list[dict] = [
    {"key": "mt5", "name": "Exness · MT5", "letter": "M", "color": "#0ecb81",
     "modes": ("hybrid", "mt5")},
    {"key": "binance", "name": "Binance", "letter": "B", "color": "#f5b50a",
     "modes": ("hybrid", "binance")},
    {"key": "alpaca", "name": "Alpaca (paper)", "letter": "A", "color": "#ffd03e",
     "modes": ("hybrid", "alpaca")},
]


def _fingerprint(key: str) -> str:
    """Stable short key fingerprint — enough to confirm WHICH account a
    broker talks to (the "my testnet looks empty" class of confusion)
    without leaking the key."""
    if not key:
        return ""
    return f"..{key[-6:]}" if len(key) > 6 else ".."


def _probe(fn):
    """Time a probe call; return (ok, latency_ms, error, payload)."""
    t0 = time.time()
    try:
        return True, round((time.time() - t0) * 1000), "", fn() or {}
    except Exception as e:
        return False, round((time.time() - t0) * 1000), str(e)[:160], {}


def broker_health() -> list[dict]:
    """Live diagnostics for every active registry broker: reachable or not,
    which key/account it talks to, and probe latency. A broker that is down
    degrades to ok:false with the error — never silently absent."""
    out = []
    mode = config.EXECUTION_BROKER
    for entry in active_brokers():
        key = entry["key"]
        base = {"key": key, "name": entry["name"], "letter": entry["letter"],
                "color": entry["color"], "routed": mode in entry["modes"],
                "mode": mode if mode in entry["modes"] else "",
                "last_reconcile": dict(_reconcile_state)}
        if key == "mt5":
            def _p():
                i = MT5Broker().detailed_account_info()
                return {"account": str(i.get("login") or ""), "detail": i.get("server") or "",
                        "extra": {"balance": i.get("balance"), "currency": i.get("currency") or "USD"}}
            ok, ms, err, payload = _probe(_p)
            out.append({**base, "ok": ok, "latency_ms": ms, "error": err,
                        "fingerprint": payload.get("account", ""), "detail": payload.get("detail", ""),
                        "extra": payload.get("extra", {})})
        elif key == "binance":
            has_keys = bool(config.BINANCE_EXEC_API_KEY and config.BINANCE_EXEC_API_SECRET)
            if not has_keys:
                out.append({**base, "ok": False, "latency_ms": 0,
                            "error": "no API key configured", "fingerprint": "", "detail": "", "extra": {}})
            else:
                def _p():
                    snap = BinanceBroker()._account_snapshot()
                    bal = next((b for b in snap.get("balances", []) if b.get("asset") == "USDT"), {})
                    return {"fingerprint": _fingerprint(config.BINANCE_EXEC_API_KEY),
                            "detail": BINANCE_BASE.replace("https://", ""),
                            "extra": {"free_usdt": bal.get("free")}}
                ok, ms, err, payload = _probe(_p)
                out.append({**base, "ok": ok, "latency_ms": ms, "error": err,
                            "fingerprint": payload.get("fingerprint", ""), "detail": payload.get("detail", ""),
                            "extra": payload.get("extra", {})})
        elif key == "alpaca":
            has_keys = bool(config.ALPACA_API_KEY and config.ALPACA_SECRET_KEY)
            if not has_keys:
                out.append({**base, "ok": False, "latency_ms": 0,
                            "error": "no API key configured", "fingerprint": "", "detail": "", "extra": {}})
            else:
                def _p():
                    import portfolio as pf
                    acct = pf._alpaca_account() or {}
                    return {"fingerprint": _fingerprint(config.ALPACA_API_KEY),
                            # detail/extra filled below from the live account
                            "detail": acct.get("account_number") or acct.get("acct") or "",
                            "extra": {"status": acct.get("status"), "equity": acct.get("equity")}}
                ok, ms, err, payload = _probe(_p)
                out.append({**base, "ok": ok, "latency_ms": ms, "error": err,
                            "fingerprint": payload.get("fingerprint", ""), "detail": payload.get("detail", ""),
                            "extra": payload.get("extra", {})})
    return out

_ACCOUNT_CARD_BUILDERS = {
    "mt5": lambda: _mt5_account_card(),
    "binance": lambda: _binance_account_card(),
    "alpaca": lambda: _alpaca_account_card(),
}


def active_brokers() -> list[dict]:
    """Registry entries active under the current EXECUTION_BROKER mode."""
    mode = config.EXECUTION_BROKER
    return [b for b in BROKER_REGISTRY if mode in b["modes"]]


def broker_keys() -> list[str]:
    """Active journal source keys (what the orders table filters on)."""
    return [b["key"] for b in active_brokers()]


def all_account_cards() -> list[dict]:
    """One card per active registry account, same layout for all.
    A broker that fails (terminal down, network) degrades to an error card —
    never a fake number."""
    cards: list[dict] = []
    for b in active_brokers():
        builder = _ACCOUNT_CARD_BUILDERS.get(b["key"])
        if builder is None:
            continue
        try:
            cards.append(builder())
        except Exception as e:
            cards.append({"broker": b["key"], "name": b["name"], "error": str(e)[:200]})
    return cards


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

