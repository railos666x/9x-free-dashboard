"""Behavioral tests for the 2026-09-12 trade-error fixes in execution.py.
Drives the REAL code paths (live testnet for filters, stubbed HTTP for the
-1021 retry, stubbed MT5 info for hybrid sizing). Run from project root:
    .venv/Scripts/python.exe scripts/execution_error_fixes_test.py
"""
import sys, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

fails = 0
def check(name, ok, detail=""):
    global fails
    print(("PASS" if ok else "FAIL"), "-", name, ("| " + detail) if detail else "")
    if not ok:
        fails += 1

import execution  # noqa: E402

# ── TEST 1: filter merge against the LIVE testnet ────────────────────────
b = execution.BinanceBroker.__new__(execution.BinanceBroker)  # skip __init__ (no keys needed)
execution.BinanceBroker._exchange_info_cache = {"at": 0.0, "data": None}
info = b._get_market_lot_info("BTCUSDT")
check("T1 testnet BTCUSDT stepSize is the LOT_SIZE grid (0.00001)",
      abs(info["stepSize"] - 0.00001) < 1e-12, f"got {info['stepSize']}")
check("T1 testnet BTCUSDT minQty is 0.00001 (not the 0 from MARKET_LOT_SIZE)",
      abs(info["minQty"] - 0.00001) < 1e-12, f"got {info['minQty']}")
check("T1 minNotional still picked up (5.0)", abs(info["minNotional"] - 5.0) < 1e-9,
      f"got {info['minNotional']}")

# ── TEST 2: failing refresh keeps the stale cache instead of zeros ───────
execution.BinanceBroker._exchange_info_cache = {"at": 1.0, "data": {"symbols": [{
    "symbol": "ETHUSDT",
    "filters": [{"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"}],
}]}}
class _Boom:
    def get(self, *a, **k):
        raise ConnectionError("network down")
_real_requests = execution.requests
execution.requests = _Boom()
info2 = b._get_market_lot_info("ETHUSDT")   # TTL expired + fetch fails → must serve stale
check("T2 stale cache served on network failure (stepSize 0.001)",
      abs(info2["stepSize"] - 0.001) < 1e-12, f"got {info2['stepSize']}")
check("T2 cache NOT wiped after failed refresh",
      execution.BinanceBroker._exchange_info_cache["data"] is not None)
execution.requests = _real_requests

# ── TEST 3: -1021 clock-drift → re-sign + retry once ─────────────────────
class _Resp:
    def __init__(self, texts):
        self._texts = list(texts)
        self.status_code = 400
        self.text = self._texts[0]
    def json(self):
        import json as _j
        return _j.loads(self._texts[-1])
attempts = {"n": 0}
posts = []
def _post(url, params=None, headers=None, timeout=None):
    attempts["n"] += 1
    posts.append(dict(params or {}))
    if attempts["n"] == 1:
        return _Resp(['{"code":-1021,"msg":"Timestamp for this request is outside of the recvWindow."}'])
    r = _Resp(['{"symbol":"BTCUSDT","orderId":123,"status":"FILLED","fills":[{"price":"64000","commission":"0.1"}],"executedQty":"0.001"}'])
    r.status_code = 200
    return r
execution.requests = types.SimpleNamespace(get=_real_requests.get, post=_post, ConnectionError=_real_requests.ConnectionError)
import config as _cfg
_cfg.BINANCE_EXEC_API_KEY = "k" * 20
_cfg.BINANCE_EXEC_API_SECRET = "s" * 20
order = {"symbol": "BTC/USDT", "side": "LONG", "qty": 0.001, "entry": 64000.0}
try:
    res = b._submit(None, order, "BUY")
    ok3 = res.get("status") == "filled" and attempts["n"] == 2
    resigned = len(posts) == 2 and posts[1]["timestamp"] >= posts[0]["timestamp"]
    check("T3 -1021 retried once with a fresh signature and filled",
          ok3 and resigned, f"attempts={attempts['n']}, resigned={resigned}")
except Exception as e:
    check("T3 -1021 retried once with a fresh signature and filled", False, f"threw {e}")
execution.requests = _real_requests

# ── TEST 4: -1021 twice (real rejection) must still raise ────────────────
attempts["n"] = 0
class _Resp1021:
    status_code = 400
    text = '{"code":-1021,"msg":"Timestamp outside recvWindow"}'
def _post2(url, params=None, headers=None, timeout=None):
    attempts["n"] += 1
    return _Resp1021()
execution.requests = types.SimpleNamespace(get=_real_requests.get, post=_post2, ConnectionError=_real_requests.ConnectionError)
try:
    b._submit(None, order, "BUY")
    check("T4 persistent -1021 raises BrokerError", False, "no exception raised")
except execution.BrokerError:
    check("T4 persistent -1021 raises BrokerError", attempts["n"] == 2, f"attempts={attempts['n']}")
except Exception as e:
    check("T4 persistent -1021 raises BrokerError", False, f"wrong exc {type(e).__name__}")
execution.requests = _real_requests

# ── TEST 5: hybrid routes FX/metals to the MT5 lot-aware sizing branch ───
from decimal import Decimal
import math as _math

class _FakeInfo:
    trade_contract_size = 100.0   # XAG/USD-style CFD
    volume_step = 1.0
    volume_min = 1.0
    volume_max = 50.0

class _FakeMT5:
    name = "mt5"
    def _symbol_info(self, sym): return _FakeInfo()
    @staticmethod
    def _lots(info, units):
        return float((Decimal(str(units)) / Decimal("100")).to_integral_value(rounding="ROUND_FLOOR"))

class _FakeHybrid:
    name = "hybrid"
    _mt5 = _FakeMT5()
    @staticmethod
    def _is_crypto(sym): return False

import risk as _risk, config as _cfgmod
_orig_get_broker = execution.get_broker
execution.get_broker = lambda: _FakeHybrid()   # inject AT the source
_orig_equity = execution._broker_equity
execution._broker_equity = lambda conn: 10_000.0
_orig_ps = _risk.position_size
_risk.position_size = lambda *a, **k: 750.0    # risk sizer wants 750 units = 7.5 lots
_orig_pct = getattr(_cfgmod, "MT5_POSITION_PCT", None)
_cfgmod.MT5_POSITION_PCT = 0.5

class _Intent:
    symbol = "XAG/USD"; direction = "LONG"; entry = 2500.0; stop_loss = 2400.0; confidence = 50

qty = execution._sized_qty(None, _Intent(), None, 2500.0, 2400.0)
# expected: vol = floor(750/100) = 7 lots; equity cap = floor(10000*0.5/(2500*100)/1)*1 = 0
# → cap below vmin=1.0 → capped up to vmin → 1 lot × contract 100 = 100 units
check("T5 hybrid FX sizing goes through MT5 lot math (units = lots × contract)",
      abs(qty - 100.0) < 1e-9, f"got {qty} units (expect 100 = vmin 1 lot × contract 100)")

_risk.position_size = _orig_ps
if _orig_pct is not None: _cfgmod.MT5_POSITION_PCT = _orig_pct
execution._broker_equity = _orig_equity
execution.get_broker = _orig_get_broker

print()
print("ALL PASSED" if fails == 0 else f"{fails} FAILURE(S)")
sys.exit(0 if fails == 0 else 1)
