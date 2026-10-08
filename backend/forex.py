"""
cryptoautomation — FX mastery layer.

One module that makes the forex side of the dashboard *informed*, not just
quoted. Everything here is aggregation over data we already collect (1h
candles in SQLite, MT5 symbol_info, the orders journal); it never trades and
never raises — every public function degrades gracefully.

Pieces:
  - session_info():      which FX session is live, liquidity weight, countdown
  - currency_strength(): 24h % changes → per-currency strength scores
  - synthetic_usd_index: DXY-style USD index from the 6 majors (real DXY weights)
  - carry_bias():        MT5 swap points → which side pays/earns overnight
  - pair_snapshot():     per-pair trend/momentum/ATR regime from candles
  - fx_exposure():       open journal rows grouped by currency (correlated-risk view)
  - overview():          everything above in one dashboard payload
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import config
import indicators

log = logging.getLogger(__name__)

# ── FX universe (mirrors config.EXNESS_SYMBOLS forex section; DXY excluded —
#    it is an index and gets the synthetic-USD treatment below) ────────────
FX_MAJORS = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
             "USD/CAD", "NZD/USD"]
FX_CROSSES = ["EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "EUR/CHF",
              "EUR/AUD", "EUR/CAD", "EUR/NZD", "GBP/AUD", "GBP/CAD",
              "GBP/NZD", "GBP/CHF", "AUD/CAD", "AUD/NZD", "NZD/CHF",
              "CAD/JPY", "CHF/JPY", "NZD/JPY"]
FX_MINORS = ["USD/CNY", "USD/SGD", "USD/MXN", "USD/ZAR", "USD/TRY",
             "USD/SEK", "USD/NOK", "USD/PLN"]
FX_PAIRS = FX_MAJORS + FX_CROSSES + FX_MINORS

# Real DXY weights (ICE): EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%
_DXY_COMPONENTS = [
    ("EUR/USD", 0.576), ("USD/JPY", 0.136), ("GBP/USD", 0.119),
    ("USD/CAD", 0.091), ("USD/SEK", 0.042), ("USD/CHF", 0.036),
]

# Static annualized carry priors for LONG-the-base direction (display fallback
# when MT5 swap points are unavailable). Values are typical recent differentials,
# NOT live data — the playbook labels them as such. EM pairs carry devaluation risk.
_PRIOR_CARRY_PCT = {
    "EUR/USD": -1.0, "GBP/USD": -0.5, "USD/JPY": 4.0, "USD/CHF": 1.5,
    "AUD/USD": 0.5, "USD/CAD": 1.0, "NZD/USD": 1.5, "USD/MXN": 4.5,
    "USD/ZAR": 5.0, "USD/SGD": 0.5, "USD/SEK": 1.0, "USD/NOK": 2.0,
}

# Which region's market hours dominate each currency (approximate).
_CURRENCY_REGION = {
    "JPY": "asian", "AUD": "asian", "NZD": "asian", "CNY": "asian",
    "EUR": "london", "GBP": "london", "CHF": "london", "SEK": "london",
    "NOK": "london", "TRY": "london", "PLN": "london",
    "USD": "ny", "CAD": "ny", "MXN": "ny", "ZAR": "ny",
}

# ── Sessions (UTC boundaries mirror ai_confirm.current_session) ─────────────
_SESSIONS = {
    "asian":  {"hours": (23, 7),  "liquidity": 0.55, "label": "Tokyo + Sydney",
               "note": "Smaller ranges; JPY/AUD/NZD most active"},
    "london": {"hours": (7, 13),  "liquidity": 0.9,  "label": "London",
               "note": "Deepest EUR/GBP/CHF liquidity; breakout-prone open"},
    "overlap": {"hours": (13, 16), "liquidity": 1.0, "label": "London + New York overlap",
                "note": "Prime window — highest volume and cleanest trends"},
    "ny":     {"hours": (16, 22), "liquidity": 0.75, "label": "New York",
               "note": "USD data releases; trends fade into the close"},
    "dead":   {"hours": (22, 23), "liquidity": 0.15, "label": "Post-NY dead zone",
               "note": "Spreads widen; the bot blocks new trades here"},
}


def session_info(now: datetime | None = None) -> dict:
    """Current FX session with liquidity weight and countdown to the next."""
    now = now or datetime.now(timezone.utc)
    h = now.hour
    name = "asian" if (h >= 23 or h < 7) else "london" if h < 13 else \
           "overlap" if h < 16 else "ny" if h < 22 else "dead"

    order = ["asian", "london", "overlap", "ny", "dead"]
    idx = order.index(name)
    # hours until the *next* session's start boundary
    boundaries = {"asian": 23, "london": 7, "overlap": 13, "ny": 16, "dead": 22}
    nxt = order[(idx + 1) % len(order)]
    hours_to = (boundaries[nxt] - h) % 24 or 24

    meta = _SESSIONS[name]
    return {
        "session": name,
        "label": meta["label"],
        "note": meta["note"],
        "liquidity": meta["liquidity"],
        "utc_hour": h,
        "next_session": nxt,
        "hours_to_next": round(hours_to, 1),
        "weekend": now.weekday() >= 5,  # FX closed Sat/Sun (UTC)
    }


def pair_sessions(pair: str) -> list[str]:
    """Sessions in which at least one leg of the pair trades its home hours."""
    legs = pair.split("/")
    out = []
    for session in ("asian", "london", "ny"):
        if any(_CURRENCY_REGION.get(leg) == session or
               (session == "ny" and leg == "USD") for leg in legs):
            out.append(session)
    return out or ["ny"]


# ── Currency strength (pure) ────────────────────────────────────────────────
def currency_strength(chg_by_symbol: dict[str, float]) -> list[tuple[str, float]]:
    """Per-currency strength scores from 24h % changes.

    Each pair's change credits its base and debits its quote equally.
    Returns [(currency, score), ...] sorted strongest first; scores are
    arbitrary units (sum of contributions) suitable for bar charts.
    """
    scores: dict[str, float] = {}
    for sym, chg in chg_by_symbol.items():
        if "/" not in sym or chg is None:
            continue
        base, quote = sym.split("/", 1)
        scores[base] = scores.get(base, 0.0) + float(chg)
        scores[quote] = scores.get(quote, 0.0) - float(chg)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def synthetic_usd_index(chg_by_symbol: dict[str, float]) -> float | None:
    """DXY-style 24h USD index change from the 6 real-weight majors.

    USD-as-quote legs (EUR/USD, GBP/USD) contribute -change * weight;
    USD-as-base legs (USD/JPY, ...) contribute +change * weight.
    Returns None if any component is missing (no half-baked index).
    """
    total = 0.0
    for sym, w in _DXY_COMPONENTS:
        chg = chg_by_symbol.get(sym)
        if chg is None:
            return None
        base, quote = sym.split("/", 1)
        sign = -1.0 if quote == "USD" else 1.0
        total += sign * float(chg) * w
    return total


# ── Carry ───────────────────────────────────────────────────────────────────
def carry_bias(pair: str, swap_long: float | None = None,
               swap_short: float | None = None) -> dict:
    """Which side of `pair` earns (vs pays) the overnight swap.

    Live swap points (MT5 symbol_info) win when provided; otherwise the
    static prior is used and flagged. Swap values are per-lot per-night in
    account currency — sign is what matters here.
    """
    if swap_long is not None and swap_short is not None:
        side = "LONG" if swap_long >= swap_short else "SHORT"
        return {"pair": pair, "favored_side": side, "swap_long": swap_long,
                "swap_short": swap_short, "source": "mt5"}
    prior = _PRIOR_CARRY_PCT.get(pair)
    if prior is None:
        return {"pair": pair, "favored_side": None, "source": "none"}
    return {"pair": pair, "favored_side": "LONG" if prior >= 0 else "SHORT",
            "annualized_prior_pct": prior, "source": "static_prior"}


# ── Per-pair analytics from candles ─────────────────────────────────────────
def _chg_24h(df) -> float | None:
    if df is None or len(df) < 25:
        return None
    last = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-25])
    return (last - prev) / prev * 100 if prev else None


def pair_snapshot(conn, symbol: str, candles_fn, add_all_fn, ema_fn) -> dict:
    """Trend/momentum/vol snapshot for one pair (deps injected for tests).

    candles_fn(conn, symbol, interval, limit) -> raw OHLC df;
    add_all_fn(df) -> df with rsi/atr indicator columns added;
    ema_fn(series, n) -> EMA series.
    """
    out: dict = {"symbol": symbol, "has_data": False}
    try:
        df = candles_fn(conn, symbol, "1h", limit=220)
        if df is None or len(df) < 60:
            return out
        df = add_all_fn(df)
        out["has_data"] = True
        last = float(df["close"].iloc[-1])
        ema50 = float(ema_fn(df["close"], 50).iloc[-1])
        ema200 = float(ema_fn(df["close"], 200).iloc[-1]) if len(df) >= 200 else ema50
        rsi = float(df["rsi"].iloc[-1])
        atr = float(df["atr"].iloc[-1])
        chg = _chg_24h(df)

        if last > ema50 > ema200:
            trend = "bullish"
        elif last < ema50 < ema200:
            trend = "bearish"
        else:
            trend = "neutral"
        momentum = "bull" if rsi >= 55 else "bear" if rsi <= 45 else "neutral"

        out.update({
            "last": round(last, 6),
            "chg_24h": round(chg, 3) if chg is not None else None,
            "trend": trend,
            "momentum": momentum,
            "rsi": round(rsi, 1),
            "atr_pct": round(atr / last * 100, 3) if last else None,
            "sessions": pair_sessions(symbol),
        })
    except Exception as e:  # never break the dashboard over one pair
        log.debug("pair_snapshot %s failed: %s", symbol, e)
        out["has_data"] = False
    return out


def load_pair_candles(conn, symbol, interval, limit):
    from database import load_candles
    return load_candles(conn, symbol, interval, limit=limit)


# ── Open FX exposure from the journal ───────────────────────────────────────
def fx_exposure(rows: list[dict]) -> dict:
    """Group open FX rows by currency: net direction per currency and the
    largest correlated group (the AUD-basket pattern — concentration check).

    rows: [{symbol, side}] for status='filled' FX rows only.
    """
    by_ccy: dict[str, int] = {}
    for r in rows:
        sym, side = r.get("symbol"), (r.get("side") or "").upper()
        if sym not in FX_PAIRS or side not in ("LONG", "SHORT"):
            continue
        base, quote = sym.split("/", 1)
        d = 1 if side == "LONG" else -1
        by_ccy[base] = by_ccy.get(base, 0) + d
        by_ccy[quote] = by_ccy.get(quote, 0) - d
    exposure = {c: v for c, v in by_ccy.items() if v != 0}
    top = max(exposure.items(), key=lambda kv: abs(kv[1]), default=(None, 0))
    return {"by_currency": exposure, "top_currency": top[0],
            "top_net": top[1], "concentration": abs(top[1]) if top[0] else 0}


# ── Dashboard aggregation ───────────────────────────────────────────────────
def overview(conn) -> dict:
    """Everything the Forex view needs, in one defensive payload."""
    from database import load_candles

    session = session_info()
    chg_by_symbol: dict[str, float] = {}

    pairs = []
    for sym in FX_PAIRS:
        snap = pair_snapshot(conn, sym, load_candles, indicators.add_all,
                             indicators.ema)
        if snap.get("chg_24h") is not None:
            chg_by_symbol[sym] = snap["chg_24h"]
        pairs.append(snap)

    usd_idx = synthetic_usd_index(chg_by_symbol)
    strength = currency_strength(chg_by_symbol)

    # Open FX exposure from the journal
    exposure: dict = {"by_currency": {}, "top_currency": None, "top_net": 0,
                      "concentration": 0}
    open_rows: list[dict] = []
    try:
        import database
        cur = conn.execute(
            "SELECT symbol, side FROM orders WHERE status IN ('filled','open')")
        open_rows = [dict(r) for r in cur.fetchall()]
        exposure = fx_exposure(open_rows)
    except Exception as e:
        log.debug("fx_exposure failed: %s", e)

    # Carry for majors: live MT5 swaps when available
    carry: list[dict] = []
    try:
        import execution
        mt5b = execution.MT5Broker()
        for sym in FX_MAJORS:
            info = mt5b._symbol_info(sym)
            if info is not None:
                carry.append(carry_bias(sym, float(getattr(info, "swap_long", 0.0) or 0.0),
                                        float(getattr(info, "swap_short", 0.0) or 0.0)))
            else:
                carry.append(carry_bias(sym))
    except Exception as e:
        log.debug("carry via MT5 unavailable: %s", e)
        carry = [carry_bias(sym) for sym in FX_MAJORS]

    with_data = sum(1 for p in pairs if p.get("has_data"))
    return {
        "ok": True,
        "session": session,
        "usd_index_24h": round(usd_idx, 3) if usd_idx is not None else None,
        "strength": [{"currency": c, "score": round(s, 3)} for c, s in strength],
        "pairs": pairs,
        "pairs_with_data": with_data,
        "pairs_total": len(pairs),
        "exposure": exposure,
        "carry": carry,
    }
