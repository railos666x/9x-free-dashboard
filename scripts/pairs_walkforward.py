"""Walk-forward backtest for a cointegrated pairs spread (Engle-Granger).

Protocol (no look-ahead):
  * Rolling windows: TRAIN (estimate beta, mu, sigma, half-life) ->
    TEST (trade the NEXT window with FROZEN train params) -> step forward.
  * Entries evaluated on the bar AFTER the signal crosses (execution lag).
  * Exits: |z| < Z_EXIT (mean reversion), |z| >= Z_STOP (adverse stop),
    or MAX_HOLD bars (time stop), whichever first.
  * Costs: 2 legs x 2 sides x (taker fee + slippage), applied per trade.
  * Position: long-spread = long A, short beta*N of B (dollar N on leg A).

RESEARCH TOOL — prints OOS verdict; places no orders. Only if the OOS
aggregate is net-positive with a sane sample size is wiring considered.

Usage:
    ./.venv/Scripts/python.exe scripts/pairs_walkforward.py [SYM_A SYM_B]
"""
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "crypto_data.db"

TRAIN_BARS = 1000     # ~10.4 days of 15m bars
TEST_BARS = 240       # 2.5 days OOS per segment
Z_ENTRY = 2.0
Z_EXIT = 0.5
Z_STOP = 3.5
MAX_HOLD = 96         # 1 day, also roughly 4x typical half-life cap
FEE_BPS = 10.0        # Binance spot taker per side, per leg
SLIP_BPS = 2.0        # slippage per side, per leg
COST_PER_TRADE = 2 * 2 * (FEE_BPS + SLIP_BPS) / 10_000.0   # fraction of leg-A notional


def load15(conn, sym):
    df = pd.read_sql_query(
        "SELECT open_time, close FROM candles WHERE symbol=? AND interval='1m' ORDER BY open_time",
        conn, params=(sym,))
    if df.empty:
        return None
    s = df.set_index(pd.to_datetime(df.open_time, unit="ms")).close.astype(float)
    return s[~s.index.duplicated()].resample("15min").last().dropna()


def estimate(la, lb):
    """Train-window parameter fit. Returns None if unusable."""
    if len(la) < 200 or lb.std() == 0:
        return None
    fit = OLS(la.values, add_constant(lb.values)).fit()
    beta = fit.params[1]
    resid = la - beta * lb
    sigma = resid.std()
    if sigma <= 0:
        return None
    s_lag, ds = resid.shift(1).dropna(), resid.diff().dropna()
    lam = OLS(ds.values, add_constant(s_lag.values)).fit().params[1]
    hl = -np.log(2) / lam if lam < 0 else float("inf")
    p = adfuller(resid, maxlag=1, autolag=None)[1]
    return {"beta": beta, "mu": resid.mean(), "sigma": sigma,
            "hl": min(hl, 4 * MAX_HOLD), "adf_p": p}


def trade_test_window(df, params):
    """Trade the test window with frozen params. Returns list of trade dicts."""
    beta, mu, sigma = params["beta"], params["mu"], params["sigma"]
    spread = np.log(df.A) - beta * np.log(df.B)
    z = (spread - mu) / sigma
    trades, pos = [], None  # pos = dict(side, entry_i, entry_z, leg_entry)
    for i in range(1, len(df)):
        if pos is None:
            if abs(z.iloc[i - 1]) > Z_ENTRY and abs(z.iloc[i]) < Z_STOP:
                side = -1 if z.iloc[i - 1] > Z_ENTRY else 1  # 1=long spread, -1=short
                pos = {"side": side, "entry_i": i, "leg_entry": (float(df.A.iloc[i]), float(df.B.iloc[i]))}
        else:
            held = i - pos["entry_i"]
            exit_now = (
                abs(z.iloc[i]) < Z_EXIT            # reverted to mean
                or abs(z.iloc[i]) >= Z_STOP        # adverse stop
                or held >= MAX_HOLD                # time stop
                or held >= max(8, int(2 * params["hl"]))  # 2x half-life stop
            )
            if exit_now:
                a0, b0 = pos["leg_entry"]
                a1, b1 = float(df.A.iloc[i]), float(df.B.iloc[i])
                gross = pos["side"] * ((np.log(a1) - np.log(a0)) - beta * (np.log(b1) - np.log(b0)))
                trades.append({
                    "open": df.index[pos["entry_i"]], "close": df.index[i],
                    "held": held, "side": pos["side"],
                    "entry_z": round(float(z.iloc[pos["entry_i"]]), 2),
                    "gross": gross, "net": gross - COST_PER_TRADE,
                    "exit_z": round(float(z.iloc[i]), 2),
                })
                pos = None
    return trades


def main():
    a_sym = sys.argv[1] if len(sys.argv) > 2 else "ADA/USDT"
    b_sym = sys.argv[2] if len(sys.argv) > 2 else "DOGE/USDT"
    conn = sqlite3.connect(DB_PATH)
    sa, sb = load15(conn, a_sym), load15(conn, b_sym)
    df = pd.concat([sa, sb], axis=1, keys=["A", "B"]).dropna()
    print(f"{a_sym}/{b_sym}  |  {len(df)} x 15m bars  {df.index[0]} -> {df.index[-1]}")
    print(f"cost model: {FEE_BPS:.0f}bps fee + {SLIP_BPS:.0f}bps slip per side per leg = {COST_PER_TRADE*1e4:.0f}bps/trade\n")

    all_oos, seg_rows = [], []
    i = TRAIN_BARS
    while i + TEST_BARS <= len(df):
        tr = df.iloc[i - TRAIN_BARS:i]
        te = df.iloc[i:i + TEST_BARS]
        params = estimate(np.log(tr.A), np.log(tr.B))
        if params is None:
            i += TEST_BARS
            continue
        trades = trade_test_window(te, params)
        all_oos.extend(trades)
        seg_net = sum(t["net"] for t in trades)
        seg_rows.append((te.index[0].date(), params["adf_p"], len(trades), seg_net))
        i += TEST_BARS

    print(f"{'OOS segment':<12}{'adf_p':>8}{'trades':>8}{'net':>10}")
    for d, p, n, net in seg_rows:
        print(f"{str(d):<12}{p:>8.3f}{n:>8}{net*100:>+9.2f}%")

    n = len(all_oos)
    print(f"\n=== OOS aggregate ===")
    if n == 0:
        print("no trades triggered out-of-sample — strategy never fired; nothing to wire")
        return
    gross = sum(t["gross"] for t in all_oos)
    net = sum(t["net"] for t in all_oos)
    wins = sum(1 for t in all_oos if t["net"] > 0)
    eq = np.cumsum([t["net"] for t in all_oos])
    dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    print(f"trades: {n}   win rate: {wins}/{n} = {100*wins/n:.0f}%")
    print(f"gross: {gross*100:+.2f}%   net: {net*100:+.2f}%   (per trade avg {net/n*100:+.3f}%)")
    print(f"max drawdown (per-trade equity): {dd*100:.2f}%")
    verdict = net > 0 and n >= 10
    print(f"\nVERDICT: {'WIRE-CANDIDATE — OOS net positive with usable sample' if verdict else 'DO NOT WIRE — ' + ('negative OOS edge' if net <= 0 else 'sample too small to trust')}")


if __name__ == "__main__":
    main()
