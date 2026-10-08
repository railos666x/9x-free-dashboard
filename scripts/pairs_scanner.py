"""Quant pairs scanner (longbridge-quant framework, Engle-Granger).

Screens the local candle store for cointegrated pairs on 15-minute bars,
reports hedge ratio, spread Z-score, half-life and the current signal.

RESEARCH ONLY — this tool reads the database and prints analysis; it never
places orders. Wire it into the bot loop only after a walk-forward
backtest proves out-of-sample profitability.

Usage (project root, cryptoautomation venv):
    ./.venv/Scripts/python.exe scripts/pairs_scanner.py            # screen all pairs
    ./.venv/Scripts/python.exe scripts/pairs_scanner.py BTC/USDT ETH/USDT   # detail on one pair
"""
import sqlite3
import sys
import warnings
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import OLS
from statsmodels.tools.tools import add_constant
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "crypto_data.db"
DEFAULT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "LTC/USDT",
    "ADA/USDT", "DOGE/USDT", "XRP/USDT", "LINK/USDT", "AVAX/USDT",
]
MIN_BARS = 1500


def load15(conn, sym):
    df = pd.read_sql_query(
        "SELECT open_time, close FROM candles WHERE symbol=? AND interval='1m' ORDER BY open_time",
        conn, params=(sym,))
    if df.empty:
        return None
    s = df.set_index(pd.to_datetime(df.open_time, unit="ms")).close.astype(float)
    return s[~s.index.duplicated()].resample("15min").last().dropna()


def analyze(df):
    la, lb = np.log(df.A), np.log(df.B)
    fit = OLS(la.values, add_constant(lb.values)).fit()
    beta = fit.params[1]
    resid = la - beta * lb
    p = adfuller(resid, maxlag=1, autolag=None)[1]
    mu, sigma = resid.mean(), resid.std()
    z = (resid.iloc[-1] - mu) / sigma if sigma > 0 else 0.0
    s_lag, ds = resid.shift(1).dropna(), resid.diff().dropna()
    lam = OLS(ds.values, add_constant(s_lag.values)).fit().params[1]
    hl = -np.log(2) / lam if lam < 0 else float("inf")
    return {"p": p, "beta": beta, "z": z, "half_life_bars": hl, "obs": len(df)}


def pair_df(conn, a, b):
    sa, sb = load15(conn, a), load15(conn, b)
    if sa is None or sb is None:
        return None
    return pd.concat([sa, sb], axis=1, keys=["A", "B"]).dropna()


def main():
    conn = sqlite3.connect(DB_PATH)
    args = sys.argv[1:]
    if args:
        df = pair_df(conn, args[0], args[1])
        if df is None:
            print("insufficient data for that pair"); return
        r = analyze(df)
        verdict = "PASS" if r["p"] < 0.05 else "FAIL"
        sig = ("SHORT A / LONG B" if r["z"] > 2 else "LONG A / SHORT B" if r["z"] < -2 else "NO TRADE (|Z|<2)")
        print(f"{args[0]} / {args[1]}")
        print(f"  cointegration p : {r['p']:.4f} ({verdict})  obs={r['obs']}  beta={r['beta']:.3f}")
        print(f"  Z-score         : {r['z']:+.2f}   half-life: {r['half_life_bars']:.1f} bars (15m)")
        print(f"  signal          : {sig}")
        return
    data = {s: load15(conn, s) for s in DEFAULT_SYMBOLS}
    results = []
    for a, b in combinations(DEFAULT_SYMBOLS, 2):
        if data[a] is None or data[b] is None:
            continue
        df = pd.concat([data[a], data[b]], axis=1, keys=["A", "B"]).dropna()
        if len(df) < MIN_BARS:
            continue
        r = analyze(df)
        results.append((r["p"], a, b, r))
    results.sort()
    print(f"Engle-Granger scan, 15m bars, {len(results)} pairs tested\n")
    print(f"{'pair':<20}{'p':>8}{'beta':>8}{'Z':>7}{'HL(bars)':>10}  verdict")
    for p, a, b, r in results:
        if p < 0.10:
            print(f"{a.split('/')[0]+'/'+b.split('/')[0]:<20}{p:>8.4f}{r['beta']:>8.3f}{r['z']:>+7.2f}{r['half_life_bars']:>10.1f}  {'COINTEGRATED' if p < 0.05 else 'close'}")


if __name__ == "__main__":
    main()
