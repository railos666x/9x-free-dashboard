"""
cryptoautomation — Backtesting Engine (v2).

Replays historical candles through the mean-reversion strategy (or the
legacy math_engine pipeline) with a production execution model:

  - Realistic fills: per-symbol slippage (config tiers) + taker fees
    on both sides, applied to every entry/exit.
  - Dynamic position sizing: risk a % of *current* equity per trade,
    capped by fractional Kelly once a measured rolling edge exists.
  - ATR-based stops from the strategy, conservative intrabar ordering
    (stop-loss assumed hit before take-profit within a bar).
  - Strict out-of-sample discipline:
        * chronological walk-forward folds (the real test)
        * single trade-count 60/40 split (legacy, kept for API compat)
        * Monte Carlo bootstrap of the trade sequence for confidence
          intervals on win rate, total return, and max drawdown.
        * Wilson score interval on the win rate itself.

Academic requirement (McLean & Pontiff 2016): returns are ~26% lower
out-of-sample and ~58% lower after publication — validate on held-out
data, and treat any claim of a 60-70% win rate as unproven until it
survives the walk-forward and Monte Carlo checks below.

Usage:
    from backtester import run_backtest
    result = run_backtest(conn, symbol="BTC/USDT", strategy="mean_reversion")
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
import database
import indicators
import risk
import strategy


# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────

# Legacy math_engine path constants (kept for the comparison backtest).
WARMUP_PERIOD = 100
TRAIN_RATIO = 0.60
SL_MULT = 1.5
TP_MULT = 2.5
MAX_HOLD_CANDLES = 50
MIN_HOLD_CANDLES = 12
MIN_ENTRY_CONFIDENCE = 30.0

# New strategy backtest.
WALK_FORWARD_FOLDS = 5
MONTE_CARLO_ITERS = 1000
ROLLING_EDGE_TRADES = 50   # trades needed to start trusting measured edge
MIN_EDGE_SAMPLE = 10       # minimum trades before Kelly is allowed to cut risk


# ──────────────────────────────────────────────────────────────────────
# Types
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    """One simulated trade with full execution detail."""
    entry_time: str
    exit_time: str
    direction: str
    entry: float
    exit_price: float
    stop_loss: float
    take_profit: float
    pnl: float            # net $ P&L (fees + slippage included)
    pnl_pct: float        # % return on notional (gross of sizing)
    hold_bars: int
    exit_reason: str      # "tp" | "sl" | "timeout" | "signal_flip"
    regime: str
    kelly_fraction: float
    confidence: float
    size: float = 0.0            # units traded
    slippage_bps: float = 0.0
    equity_before: float = 0.0
    equity_after: float = 0.0


@dataclass
class BacktestResult:
    """Complete backtest output (superset of the v1 API)."""
    symbol: str
    interval: str
    total_bars: int
    total_trades: int
    win_rate: float
    win_rate_ci: tuple = (0.0, 0.0)      # Wilson 95% CI
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    total_return_pct: float = 0.0
    annualised_return_pct: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    calmar_ratio: float = 0.0
    factor_win_rates: dict = field(default_factory=dict)
    regime_performance: dict = field(default_factory=dict)
    in_sample: dict = field(default_factory=dict)
    out_of_sample: dict = field(default_factory=dict)
    walk_forward: dict = field(default_factory=dict)
    monte_carlo: dict = field(default_factory=dict)
    equity_curve: list = field(default_factory=list)
    strategy: str = "mean_reversion"
    cost_model: dict = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────

def run_backtest(
    conn,
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    limit: int = 2000,
    fee: float = None,   # kept for API compat; costs now come from risk.py
    strategy: str = "mean_reversion",
    params: strategy.MeanReversionParams | None = None,
    capital: float = None,
) -> BacktestResult:
    """
    Run a full backtest with the chosen strategy.

    Args:
        conn: SQLite connection.
        symbol: e.g. "BTC/USDT".
        interval: candle interval.
        limit: how many candles to load.
        fee: legacy argument, ignored (costs from risk/config).
        strategy: "mean_reversion" (default) | "math_engine" (legacy).
        params: MeanReversionParams override.
        capital: starting equity (default config.INITIAL_CAPITAL).

    Returns: BacktestResult with full metrics.
    """
    df = database.load_candles(conn, symbol, interval, limit=limit)
    if df.empty or len(df) < 60:
        return _empty_result(symbol, interval, len(df), strategy)

    df = indicators.add_all(df).reset_index(drop=True)
    df["time_str"] = pd.to_datetime(df["open_time"], unit="ms").dt.strftime("%Y-%m-%d %H:%M")

    if strategy == "mean_reversion":
        trades, equity_curve = _run_mean_reversion(df, symbol, interval, params, capital)
    elif strategy == "math_engine":
        trades, equity_curve = _run_math_engine(conn, df, symbol, interval, capital)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    return _compute_metrics(
        trades, equity_curve, symbol, interval, len(df), strategy,
    )


# ──────────────────────────────────────────────────────────────────────
# Mean-reversion engine (vectorized signals, production fills)
# ──────────────────────────────────────────────────────────────────────

def _run_mean_reversion(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    params: strategy.MeanReversionParams | None,
    capital: float | None,
) -> tuple[list[Trade], list[float]]:
    params = params or strategy.DEFAULT_PARAMS
    sf = strategy.signal_frame(df, params)

    slip = risk.slippage_bps(symbol) / 10_000.0
    feebps = risk.fee_bps() / 10_000.0
    equity = capital or config.INITIAL_CAPITAL

    n = len(df)
    trades: list[Trade] = []
    equity_curve: list[float] = [equity]
    closed_pnls: list[float] = []      # for rolling Kelly edge
    closed_wins: list[bool] = []

    in_pos = False
    direction = None
    entry_fill = 0.0
    sl_price = 0.0
    tp_price = 0.0
    size = 0.0
    entry_bar = 0
    hold = 0
    cooldown = 0  # bars to wait after an exit before re-entering
    time_stop = int(sf["time_stop"].iloc[-1])
    kelly_now = 0.0
    risk_pct = risk.risk_per_trade_pct(0.0)

    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    entry_long = sf["entry_long"].to_numpy()
    entry_short = sf["entry_short"].to_numpy()
    sl_long = sf["sl_long"].to_numpy()
    sl_short = sf["sl_short"].to_numpy()
    tp_long = sf["tp_long"].to_numpy()
    tp_short = sf["tp_short"].to_numpy()
    rsi2_exit_ok = sf["rsi2_exit_ok"].to_numpy()

    for i in range(1, n):
        if in_pos:
            hold += 1
            exit_price = None
            reason = None

            bar_high = high[i]
            bar_low = low[i]

            # Conservative intrabar ordering: stop-loss assumed to fill
            # before take-profit when both are touched in one bar.
            if direction == "LONG":
                if bar_low <= sl_price:
                    exit_price = sl_price * (1.0 - slip)
                    reason = "sl"
                elif bar_high >= tp_price:
                    exit_price = tp_price * (1.0 - slip)
                    reason = "tp"
            else:
                if bar_high >= sl_price:
                    exit_price = sl_price * (1.0 + slip)
                    reason = "sl"
                elif bar_low <= tp_price:
                    exit_price = tp_price * (1.0 + slip)
                    reason = "tp"

            # RSI(2) profit-taking exit (Connors rule) — in rsi2 mode.
            if (exit_price is None and params.entry_mode == "rsi2"
                    and hold >= params.min_hold and rsi2_exit_ok[i]):
                exit_price = close[i] * (1.0 - slip if direction == "LONG" else 1.0 + slip)
                reason = "rsi_exit"

            # Time stop (OU half-life based, capped).
            if exit_price is None and hold >= time_stop:
                exit_price = close[i] * (1.0 - slip if direction == "LONG" else 1.0 + slip)
                reason = "timeout"

            # Opposite-signal flip after the minimum hold.
            if exit_price is None and hold >= params.min_hold:
                flipped = (direction == "LONG" and entry_short[i]) or \
                          (direction == "SHORT" and entry_long[i])
                if flipped:
                    exit_price = close[i] * (1.0 - slip if direction == "LONG" else 1.0 + slip)
                    reason = "signal_flip"

            if exit_price is not None:
                # Fees on both sides, on the actual fill notionals.
                entry_notional = size * entry_fill
                exit_notional = size * exit_price
                fees = feebps * (entry_notional + exit_notional)
                gross = size * (exit_price - entry_fill) if direction == "LONG" \
                    else size * (entry_fill - exit_price)
                pnl = gross - fees
                equity += pnl

                pnl_pct = (exit_price - entry_fill) / entry_fill * 100.0 \
                    if direction == "LONG" \
                    else (entry_fill - exit_price) / entry_fill * 100.0

                trades.append(Trade(
                    entry_time=str(df["time_str"].iloc[entry_bar]),
                    exit_time=str(df["time_str"].iloc[i]),
                    direction=direction,
                    entry=entry_fill,
                    exit_price=exit_price,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_bars=hold,
                    exit_reason=reason,
                    regime="ranging",
                    kelly_fraction=kelly_now,
                    confidence=0.0,
                    size=size,
                    slippage_bps=slip * 10_000.0,
                    equity_before=equity - pnl,
                    equity_after=equity,
                ))
                closed_pnls.append(pnl)
                closed_wins.append(pnl > 0)
                equity_curve.append(equity)
                in_pos = False
                direction = None
                cooldown = 1  # no re-entry on the same bar

                # Update rolling edge → fractional Kelly → risk %.
                if len(closed_pnls) >= MIN_EDGE_SAMPLE:
                    recent_wins = closed_wins[-ROLLING_EDGE_TRADES:]
                    recent_pnls = closed_pnls[-ROLLING_EDGE_TRADES:]
                    wr = sum(recent_wins) / len(recent_wins)
                    wins = [p for p in recent_pnls if p > 0]
                    losses = [-p for p in recent_pnls if p <= 0]
                    avg_w = statistics.mean(wins) if wins else 0.0
                    avg_l = statistics.mean(losses) if losses else 0.0
                    rr = avg_w / avg_l if avg_l > 0 else 0.0
                    kelly_now = risk.kelly_fraction(wr, rr)
                    risk_pct = risk.risk_per_trade_pct(kelly_now)
                continue

        # Entry — at the close of bar i (signal known at bar close).
        if not in_pos:
            if cooldown > 0:
                cooldown -= 1
                continue
            if entry_long[i] and not entry_short[i]:
                direction = "LONG"
            elif entry_short[i] and not entry_long[i]:
                direction = "SHORT"
            else:
                continue

            entry_fill = close[i] * (1.0 + slip if direction == "LONG" else 1.0 - slip)
            if direction == "LONG":
                sl_price = sl_long[i]
                tp_price = tp_long[i]
            else:
                sl_price = sl_short[i]
                tp_price = tp_short[i]
            atr_v = sf["atr"].iloc[i]
            fallback = params.sl_atr * atr_v if (math.isfinite(atr_v) and atr_v > 0) \
                else entry_fill * 0.01
            sl_price = risk.safe_stop(sl_price, entry_fill, fallback)
            if not math.isfinite(tp_price) or tp_price <= 0:
                tp_price = entry_fill  # degenerate: no TP → time stop only

            size = risk.position_size(equity, entry_fill, sl_price, risk_pct)
            if size <= 0:
                direction = None
                continue

            entry_bar = i
            hold = 0
            in_pos = True

    if in_pos:
        equity_curve.append(equity)  # mark-to-market at the end

    return trades, equity_curve


# ──────────────────────────────────────────────────────────────────────
# Legacy math_engine engine (kept for comparison; same cost model)
# ──────────────────────────────────────────────────────────────────────

def _run_math_engine(
    conn,
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    capital: float | None,
) -> tuple[list[Trade], list[float]]:
    """v1 pipeline (Bayesian fusion + factors) with the v2 execution model."""
    import factors
    import math_engine

    slip = risk.slippage_bps(symbol) / 10_000.0
    feebps = risk.fee_bps() / 10_000.0
    equity = capital or config.INITIAL_CAPITAL
    risk_pct = risk.risk_per_trade_pct(0.0)

    all_coins = {}
    for s in config.SYMBOLS:
        cdf = database.load_candles(conn, s, interval, limit=len(df))
        if not cdf.empty:
            all_coins[s] = indicators.add_all(cdf).reset_index(drop=True)

    trades: list[Trade] = []
    equity_curve: list[float] = [equity]
    in_pos = False
    position_dir = None
    entry_fill = 0.0
    sl_price = 0.0
    tp_price = 0.0
    size = 0.0
    entry_bar = 0
    hold = 0

    for i in range(WARMUP_PERIOD, len(df)):
        price = float(df["close"].iloc[i])
        time_str = str(df["time_str"].iloc[i])

        if in_pos:
            hold += 1
            exit_price = None
            reason = None
            bar_high = float(df["high"].iloc[i])
            bar_low = float(df["low"].iloc[i])

            if position_dir == "LONG":
                if bar_low <= sl_price:
                    exit_price = sl_price * (1.0 - slip)
                    reason = "sl"
                elif bar_high >= tp_price:
                    exit_price = tp_price * (1.0 - slip)
                    reason = "tp"
            else:
                if bar_high >= sl_price:
                    exit_price = sl_price * (1.0 + slip)
                    reason = "sl"
                elif bar_low <= tp_price:
                    exit_price = tp_price * (1.0 + slip)
                    reason = "tp"

            if exit_price is None and hold >= MAX_HOLD_CANDLES:
                exit_price = price * (1.0 - slip if position_dir == "LONG" else 1.0 + slip)
                reason = "timeout"

            if exit_price is None and hold >= MIN_HOLD_CANDLES:
                new_signal = _legacy_signal_at(df, all_coins, i)
                if new_signal is not None and new_signal != position_dir:
                    exit_price = price * (1.0 - slip if position_dir == "LONG" else 1.0 + slip)
                    reason = "signal_flip"

            if exit_price is not None:
                entry_notional = size * entry_fill
                exit_notional = size * exit_price
                fees = feebps * (entry_notional + exit_notional)
                gross = size * (exit_price - entry_fill) if position_dir == "LONG" \
                    else size * (entry_fill - exit_price)
                pnl = gross - fees
                equity += pnl
                pnl_pct = (exit_price - entry_fill) / entry_fill * 100.0 \
                    if position_dir == "LONG" \
                    else (entry_fill - exit_price) / entry_fill * 100.0
                trades.append(Trade(
                    entry_time=str(df["time_str"].iloc[entry_bar]),
                    exit_time=time_str,
                    direction=position_dir,
                    entry=entry_fill,
                    exit_price=exit_price,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_bars=hold,
                    exit_reason=reason,
                    regime="",
                    kelly_fraction=0.0,
                    confidence=0.0,
                    size=size,
                    slippage_bps=slip * 10_000.0,
                    equity_before=equity - pnl,
                    equity_after=equity,
                ))
                equity_curve.append(equity)
                in_pos = False
                position_dir = None

        if not in_pos:
            window = df.iloc[max(0, i - 250):i + 1].copy()
            if len(window) >= 60:
                all_win = {}
                for sym, cdf in all_coins.items():
                    if len(cdf) > i:
                        all_win[sym] = cdf.iloc[max(0, i - 250):i + 1]
                try:
                    bundle = factors.compute_all_factors(window, all_win, None)
                    result = math_engine.compute_trade_signal(
                        df_1h=window, df_4h=None, df_1d=None,
                        news_score=0.0, factor_bundle=bundle, all_coins=all_win,
                    )
                    signal = result.direction
                except Exception:
                    signal = None
            else:
                signal = None

            if signal in ("LONG", "SHORT"):
                atr_val = float(df["atr"].iloc[i])
                if not math.isfinite(atr_val) or atr_val <= 0:
                    atr_val = price * 0.01
                entry_fill = price * (1.0 + slip if signal == "LONG" else 1.0 - slip)
                if signal == "LONG":
                    sl_price = entry_fill - SL_MULT * atr_val
                    tp_price = entry_fill + TP_MULT * atr_val
                else:
                    sl_price = entry_fill + SL_MULT * atr_val
                    tp_price = entry_fill - TP_MULT * atr_val
                sl_price = risk.safe_stop(sl_price, entry_fill, atr_val)
                size = risk.position_size(equity, entry_fill, sl_price, risk_pct)
                if size <= 0:
                    continue
                position_dir = signal
                hold = 0
                entry_bar = i
                in_pos = True

    if in_pos:
        equity_curve.append(equity)
    return trades, equity_curve


def _legacy_signal_at(df, all_coins, idx):
    """v1 signal for the legacy path (math engine on a sliced window)."""
    import factors
    import math_engine
    window_1h = df.iloc[max(0, idx - 250):idx + 1].copy()
    if len(window_1h) < 60:
        return None
    all_win = {}
    for sym, cdf in all_coins.items():
        if len(cdf) > idx:
            all_win[sym] = cdf.iloc[max(0, idx - 250):idx + 1]
    try:
        bundle = factors.compute_all_factors(window_1h, all_win, None)
        result = math_engine.compute_trade_signal(
            df_1h=window_1h, df_4h=None, df_1d=None,
            news_score=0.0, factor_bundle=bundle, all_coins=all_win,
        )
        if result.direction != "HOLD":
            return result.direction
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────

def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for the win rate."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _compute_metrics(
    trades: list[Trade],
    equity_curve: list[float],
    symbol: str,
    interval: str,
    total_bars: int,
    strategy: str,
) -> BacktestResult:
    n = len(trades)
    if n == 0:
        return _empty_result(symbol, interval, total_bars, strategy)

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    win_rate = len(wins) / n
    win_rate_ci = _wilson_ci(len(wins), n)

    avg_win = statistics.mean(t.pnl for t in wins) if wins else 0.0
    avg_loss = abs(statistics.mean(t.pnl for t in losses)) if losses else 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Equity-curve based returns and drawdown.
    start = equity_curve[0] if equity_curve else config.INITIAL_CAPITAL
    end = equity_curve[-1]
    total_return = end / start - 1.0 if start > 0 else 0.0

    peak = start
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    # Time-based annualisation from the equity curve.
    bars_per_year = 365.0 * 24.0 / _interval_to_hours(interval)
    annualised = (1.0 + total_return) ** (bars_per_year / max(total_bars, 1)) - 1.0 \
        if total_return > -1.0 else -1.0

    # Sharpe from per-trade returns.
    returns = [t.pnl_pct / 100.0 for t in trades]
    avg_hold = statistics.mean(tr.hold_bars for tr in trades) if n else 0.0
    trades_per_year = bars_per_year / max(avg_hold, 1.0)
    if len(returns) > 1:
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        sharpe = (mean_r / std_r * math.sqrt(trades_per_year)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    calmar = annualised / max_dd if max_dd > 0 else 0.0

    # Legacy 60/40 split (API compat) + proper walk-forward.
    split = int(n * TRAIN_RATIO)
    in_sample = _subset_metrics(trades[:split], interval)
    out_of_sample = _subset_metrics(trades[split:], interval)
    walk_forward = _walk_forward(trades, interval)

    return BacktestResult(
        symbol=symbol,
        interval=interval,
        total_bars=total_bars,
        total_trades=n,
        win_rate=round(win_rate, 4),
        win_rate_ci=(round(win_rate_ci[0], 4), round(win_rate_ci[1], 4)),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2),
        total_return_pct=round(total_return * 100, 2),
        annualised_return_pct=round(annualised * 100, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        calmar_ratio=round(calmar, 2),
        factor_win_rates=_analyze_factor_contributions(trades),
        regime_performance=_analyze_regime_performance(trades),
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        walk_forward=walk_forward,
        monte_carlo=_monte_carlo(trades, equity_curve[0]),
        equity_curve=[round(v, 2) for v in equity_curve],
        strategy=strategy,
        cost_model={
            "fee_bps_per_side": round(risk.fee_bps(), 2),
            "slippage_bps_per_side": round(risk.slippage_bps(symbol), 2),
            "round_trip_cost_bps": round(risk.total_cost_bps(symbol), 2),
            "risk_pct_default": config.RISK_PER_TRADE,
            "risk_pct_cap": config.MAX_RISK_PER_TRADE,
            "initial_capital": round(equity_curve[0], 2) if equity_curve else config.INITIAL_CAPITAL,
        },
        trades=trades,
    )


def _subset_metrics(trades: list[Trade], interval: str) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0, "sharpe": 0, "return_pct": 0,
                "max_dd_pct": 0, "profit_factor": 0}
    wins = [t for t in trades if t.pnl > 0]
    win_rate = len(wins) / n
    returns = [t.pnl_pct / 100 for t in trades]
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    total_return = equity - 1.0
    avg_hold = statistics.mean(tr.hold_bars for tr in trades) if n else 0.0
    bars_per_year = 365.0 * 24.0 / _interval_to_hours(interval)
    trades_per_year = bars_per_year / max(avg_hold, 1.0)
    if len(returns) > 1:
        mean_r = statistics.mean(returns)
        std_r = statistics.stdev(returns)
        sharpe = (mean_r / std_r * math.sqrt(trades_per_year)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0
    return {
        "trades": n,
        "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 2),
        "return_pct": round(total_return * 100, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "profit_factor": round(pf, 2),
    }


def _walk_forward(trades: list[Trade], interval: str, folds: int = WALK_FORWARD_FOLDS) -> dict:
    """Chronological walk-forward: the OOS discipline that matters."""
    n = len(trades)
    if n < folds:
        return {"folds": 0, "detail": "too few trades"}
    size = n // folds
    folds_out = []
    for k in range(folds):
        start = k * size
        end = (k + 1) * size if k < folds - 1 else n
        folds_out.append(_subset_metrics(trades[start:end], interval))
    # Last fold = pure out-of-sample window.
    last = folds_out[-1]
    wr_is = statistics.mean(f["win_rate"] for f in folds_out[:-1])
    wr_oos = last["win_rate"]
    decay = (1 - wr_oos / wr_is) * 100 if wr_is > 0 else 0.0
    # McLean & Pontiff: OOS performance is typically ~26% WORSE than IS.
    # A positive decay > 26% is the red flag; a negative decay means OOS
    # actually beat in-sample folds — informative, but not a pass either.
    if decay > 26:
        verdict = "WARNING: high OOS decay (overfit risk)"
    elif decay < -26:
        verdict = "NOTE: OOS beat IS folds (verify on fresh data)"
    else:
        verdict = "OK"
    return {
        "folds": folds,
        "per_fold": folds_out,
        "oos_win_rate": wr_oos,
        "oos_return_pct": last["return_pct"],
        "oos_sharpe": last["sharpe"],
        "wr_decay_pct": round(decay, 1),
        "mc_lean_pontiff_check": verdict,
    }


def _monte_carlo(trades: list[Trade], start_equity: float, iters: int = MONTE_CARLO_ITERS) -> dict:
    """Bootstrap the trade P&L sequence → CI on return, max DD, win rate."""
    n = len(trades)
    if n == 0:
        return {"iterations": 0}
    pnls = np.array([t.pnl for t in trades])
    rng = np.random.default_rng(42)
    returns_out = np.empty(iters)
    dd_out = np.empty(iters)
    wr_out = np.empty(iters)
    for k in range(iters):
        sample = rng.choice(pnls, size=n, replace=True)
        curve = np.concatenate(([start_equity], start_equity + np.cumsum(sample)))
        total = curve[-1] / start_equity - 1.0
        peak = np.maximum.accumulate(curve)
        dd = float(np.max((peak - curve) / peak)) if peak[-1] > 0 else 0.0
        returns_out[k] = total
        dd_out[k] = dd
        wr_out[k] = float(np.mean(sample > 0))
    return {
        "iterations": iters,
        "return_pct": {
            "p5": round(float(np.percentile(returns_out, 5)) * 100, 2),
            "p50": round(float(np.percentile(returns_out, 50)) * 100, 2),
            "p95": round(float(np.percentile(returns_out, 95)) * 100, 2),
        },
        "max_dd_pct": {
            "p50": round(float(np.percentile(dd_out, 50)) * 100, 2),
            "p95": round(float(np.percentile(dd_out, 95)) * 100, 2),
        },
        "win_rate": {
            "p5": round(float(np.percentile(wr_out, 5)), 4),
            "p50": round(float(np.percentile(wr_out, 50)), 4),
            "p95": round(float(np.percentile(wr_out, 95)), 4),
        },
        "prob_profit": round(float(np.mean(returns_out > 0)), 3),
    }


def _analyze_factor_contributions(trades: list[Trade]) -> dict:
    """Win rate by exit-reason bucket (a proxy for edge attribution)."""
    out = {}
    if not trades:
        return out
    for reason in ("tp", "sl", "timeout", "signal_flip"):
        subset = [t for t in trades if t.exit_reason == reason]
        if subset:
            out[reason] = round(len([t for t in subset if t.pnl > 0]) / len(subset), 4)
    return out


def _analyze_regime_performance(trades: list[Trade]) -> dict:
    """Per-regime win rate + P&L (regime is 'ranging' for mean reversion)."""
    out = {}
    if not trades:
        return out
    regimes = {t.regime for t in trades}
    for rg in regimes:
        subset = [t for t in trades if t.regime == rg]
        wins = [t for t in subset if t.pnl > 0]
        out[rg] = {
            "trades": len(subset),
            "win_rate": round(len(wins) / len(subset), 4),
            "pnl": round(sum(t.pnl for t in subset), 2),
        }
    return out


def _interval_to_hours(interval: str) -> float:
    mapping = {"1m": 1/60, "5m": 5/60, "15m": 15/60, "1h": 1, "4h": 4, "1d": 24}
    return mapping.get(interval, 1)


def _empty_result(symbol, interval, total_bars, strategy="mean_reversion") -> BacktestResult:
    return BacktestResult(
        symbol=symbol, interval=interval, total_bars=total_bars,
        total_trades=0, win_rate=0, avg_win=0, avg_loss=0,
        profit_factor=0, total_return_pct=0, annualised_return_pct=0,
        sharpe_ratio=0, max_drawdown_pct=0, calmar_ratio=0,
        factor_win_rates={}, regime_performance={},
        in_sample={"trades": 0}, out_of_sample={"trades": 0},
        walk_forward={"folds": 0}, monte_carlo={"iterations": 0},
        equity_curve=[], strategy=strategy,
        cost_model={}, trades=[],
    )


# ──────────────────────────────────────────────────────────────────────
# Pretty-print
# ──────────────────────────────────────────────────────────────────────

def print_report(result: BacktestResult) -> str:
    """Human-readable backtest report."""
    lines = [
        f"=== BACKTEST REPORT: {result.symbol} ({result.interval}) ===",
        f"Strategy: {result.strategy} | Data: {result.total_bars} bars | Trades: {result.total_trades}",
        "",
        "-- PERFORMANCE (net of fees + slippage) --",
        f"  Total Return:    {result.total_return_pct:+.2f}%",
        f"  Annualised:      {result.annualised_return_pct:+.2f}%",
        f"  Win Rate:        {result.win_rate:.1%}  (95% CI: "
        f"{result.win_rate_ci[0]:.1%} - {result.win_rate_ci[1]:.1%})",
        f"  Profit Factor:   {result.profit_factor:.2f}",
        f"  Sharpe Ratio:    {result.sharpe_ratio:.2f}",
        f"  Max Drawdown:    {result.max_drawdown_pct:.2f}%",
        f"  Calmar Ratio:    {result.calmar_ratio:.2f}",
        f"  Avg Win:         ${result.avg_win:.2f}",
        f"  Avg Loss:        ${result.avg_loss:.2f}",
        "",
        "-- EXECUTION MODEL --",
    ]
    for k, v in (result.cost_model or {}).items():
        lines.append(f"  {k}: {v}")

    if result.walk_forward:
        wf = result.walk_forward
        lines.append("")
        lines.append("-- WALK-FORWARD (chronological OOS) --")
        if wf.get("folds", 0) > 0:
            for i, f in enumerate(wf["per_fold"]):
                lines.append(
                    f"  Fold {i+1}: {f['trades']} trades, WR {f['win_rate']:.1%}, "
                    f"ret {f['return_pct']:+.2f}%, Sharpe {f['sharpe']:.2f}"
                )
            lines.append(
                f"  OOS WR: {wf['oos_win_rate']:.1%} | OOS ret {wf['oos_return_pct']:+.2f}% | "
                f"WR decay {wf['wr_decay_pct']:+.1f}% -> {wf['mc_lean_pontiff_check']}"
            )
        else:
            lines.append(f"  {wf.get('detail', 'n/a')}")

    if result.monte_carlo and result.monte_carlo.get("iterations", 0) > 0:
        mc = result.monte_carlo
        lines.append("")
        lines.append("-- MONTE CARLO (bootstrap of trade sequence) --")
        lines.append(
            f"  Return p5/p50/p95: {mc['return_pct']['p5']:+.1f}% / "
            f"{mc['return_pct']['p50']:+.1f}% / {mc['return_pct']['p95']:+.1f}%"
        )
        lines.append(
            f"  MaxDD p50/p95:     {mc['max_dd_pct']['p50']:.1f}% / {mc['max_dd_pct']['p95']:.1f}%"
        )
        lines.append(
            f"  Win rate p5/p95:   {mc['win_rate']['p5']:.1%} / {mc['win_rate']['p95']:.1%}"
        )
        lines.append(f"  P(profit):         {mc['prob_profit']:.1%}")

    lines.append("")
    lines.append("-- EXIT ANALYSIS --")
    reasons = {}
    for t in result.trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        lines.append(f"  {reason:15s} {count:4d} ({count/result.total_trades:.1%})")

    if result.in_sample.get("trades", 0):
        lines.append("")
        lines.append("-- 60/40 SPLIT (legacy, informational) --")
        lines.append(
            f"  In-sample:  {result.in_sample.get('trades',0)} trades, "
            f"WR {result.in_sample.get('win_rate',0):.1%}, "
            f"ret {result.in_sample.get('return_pct',0):+.2f}%"
        )
        lines.append(
            f"  Out-sample: {result.out_of_sample.get('trades',0)} trades, "
            f"WR {result.out_of_sample.get('win_rate',0):.1%}, "
            f"ret {result.out_of_sample.get('return_pct',0):+.2f}%"
        )

    report = "\n".join(lines)
    print(report)
    return report
