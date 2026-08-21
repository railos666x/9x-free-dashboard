"""
cryptoautomation — Interactive Trading Tutor (educational layer).

An on-demand educational service that explains trading mechanics — basics
(order types, bid-ask spread, leverage, margin, risk/reward) and advanced
(slippage mitigation, ATR volatility weighting, liquidity traps, order-book
imbalance, multi-timeframe confirmation) — and ties them back to THIS system
with concrete examples and the reasons a trade was taken or skipped.

Exposed to the dashboard via /api/tutor?topic=... and /api/tutor/trade?id=...
(the "why this trade" explainer reads the live journal + bot-cycle state).

Every function is pure / typed and never touches the network.
"""

from __future__ import annotations

import database

# ── Topic catalogue ───────────────────────────────────────────────────
# Each entry: category (basic|advanced), explanation (markdown), a concrete
# example with numbers, and why it matters to a systematic trading bot.
TOPICS: dict[str, dict] = {
    # ── Basics ──────────────────────────────────────────────────────
    "market_order": {
        "title": "Market Order",
        "category": "basic",
        "explanation": (
            "A market order buys or sells immediately at the best available "
            "price in the order book. It guarantees execution but not price: "
            "you get whatever the book offers at that instant.\n\n"
            "The bot's default execution path uses market orders (IOC on "
            "Alpaca). It trades the size needed to hit the risk target, and "
            "pays the ask (buying) or bid (selling) plus the broker fee."),
        "example": (
            "A LONG intent on DOGE sizes to risk 1% of equity. A market order "
            "fills at the current ask, so the fill price can be a few bps "
            "worse than the signal's entry level — the journal records the "
            "actual fill, not the theoretical entry."),
        "why_it_matters": (
            "Certain execution, but you accept slippage. This is why the "
            "risk model budgets slippage (see the slippage tiers in "
            "config.py) on top of the fee."),
    },
    "limit_order": {
        "title": "Limit Order",
        "category": "basic",
        "explanation": (
            "A limit order buys at or below your price (or sells at or "
            "above). It guarantees the price but not execution — if the "
            "market moves away, it may never fill.\n\n"
            "A systematic bot often uses limits for entries (resting at a "
            "level the strategy computed) and markets for exits (you must "
            "get out)."),
        "example": (
            "A mean-reversion LONG at SMA(20) support would rest a limit "
            "buy slightly below the current price; if price dips to it the "
            "fill is at a known level, improving expectancy by the spread."),
        "why_it_matters": "Better price, but fill risk — a signal can expire unfilled.",
    },
    "stop_limit": {
        "title": "Stop-Limit Order",
        "category": "basic",
        "explanation": (
            "A stop-limit becomes a limit order once the market trades "
            "through a trigger price. It combines a stop (activation) with "
            "a limit (the worst price you accept) — used to enter on "
            "breakouts or exit on breakdowns without chasing.\n\n"
            "This system implements stops as risk levels (stop_loss on each "
            "journal order) that the SL/TP monitor checks every cycle "
            "against the latest close, rather than resting broker-side "
            "orders."),
        "example": (
            "A SHORT with stop_loss above entry: if price rises through the "
            "stop, the position is closed at market — same intent as a "
            "stop-limit, executed by the monitor."),
        "why_it_matters": "Stops turn 'the market moved against me' into a bounded, pre-planned loss.",
    },
    "bid_ask_spread": {
        "title": "Bid-Ask Spread",
        "category": "basic",
        "explanation": (
            "The spread is the difference between the highest bid (someone "
            "will buy) and the lowest ask (someone will sell). It is the "
            "liquidity cost of a round trip: buy at ask, sell at bid, and "
            "you lose the spread even before fees.\n\n"
            "The paper broker simulates this by paying the spread's "
            "direction — LONGs fill at price + slippage, SHORTs at price − "
            "slippage."),
        "example": (
            "BTC at 63,600/63,602 is a 2-tick spread — nearly free to trade. "
            "A micro-cap at 0.0012/0.0016 is a 30% spread: a LONG would need "
            "+30% just to break even. This is why the bot holds illiquid "
            "symbols."),
        "why_it_matters": "Wide spreads are a hidden cost that quietly kills strategies backtested on mid prices.",
    },
    "leverage": {
        "title": "Leverage",
        "category": "basic",
        "explanation": (
            "Leverage lets you control a position larger than your equity "
            "(e.g. 5× means a $10k account controls $50k notional). It "
            "scales both gains and losses — a 20% adverse move at 5× wipes "
            "the account.\n\n"
            "This system trades UNLEVERAGED spot (Alpaca crypto), so "
            "position size equals notional exposure. The risk model sizes "
            "each position to a fixed risk % of equity instead."),
        "example": (
            "1% of a $100k account risks $1,000. Without leverage that's a "
            "$1,000 notional position; at 10× it's $10,000 notional — same "
            "dollar risk on paper, but liquidation risk on a gap."),
        "why_it_matters": "Leverage is risk multiplication; sizing to risk % is the safer way to scale.",
    },
    "margin": {
        "title": "Margin",
        "category": "basic",
        "explanation": (
            "Margin is the collateral required to open and hold a leveraged "
            "or short position. When losses erode the collateral below the "
            "maintenance level you get a margin call (or forced liquidation).\n\n"
            "Spot trading uses cash margin: you can only buy what you own or "
            "fund. Alpaca crypto is long-only spot here, so SHORT signals "
            "are held back (`no_short_on_spot`) rather than rejected."),
        "example": (
            "A SHORT signal on an asset the spot broker can't short is held "
            "with reason `no_short_on_spot` — the bot refuses to sell what "
            "it doesn't own instead of risking a margin call."),
        "why_it_matters": "Margin mechanics decide whether a strategy can even express SHORT at the chosen broker.",
    },
    "risk_reward": {
        "title": "Risk/Reward Ratio",
        "category": "basic",
        "explanation": (
            "The R:R ratio compares the distance to the take-profit against "
            "the distance to the stop-loss. A 1:2 trade risks 1 to make 2.\n\n"
            "You need win rate × average win > loss rate × average loss — "
            "the backtester reports this as profit factor and the signal "
            "cards show '1:2.1'-style ratios. Regime-adaptive SL/TP in "
            "math_engine widen stops in trends and tighten them in ranges."),
        "example": (
            "Entry 100, SL 98, TP 104 → risk 2, reward 4 → 1:2. Win 40% of "
            "the time and expectancy is still positive: 0.4×4 − 0.6×2 = +0.4 "
            "per trade."),
        "why_it_matters": "R:R and win rate are two sides of one equation — neither means anything alone.",
    },

    # ── Advanced ────────────────────────────────────────────────────
    "slippage": {
        "title": "Slippage Mitigation",
        "category": "advanced",
        "explanation": (
            "Slippage is the difference between the expected and actual fill "
            "price, from spread, market impact and latency. Mitigations:\n\n"
            "  1. Trade liquid instruments (tight tiers in config.py).\n"
            "  2. Size to a fraction of the book, not a fixed notional.\n"
            "  3. Use limits for entries, markets only for urgent exits.\n"
            "  4. Budget it explicitly: the risk model adds slippage bps to "
            "every simulated fill (SLIPPAGE_BPS_TIERS)."),
        "example": (
            "BTC trades at 0.5 bps slippage in this system's tiers, a meme "
            "coin at 5 bps. The backtester charges both, so a strategy that "
            "only works on majors shouldn't be traded on meme names."),
        "why_it_matters": "Slippage is the #1 reason a live system underperforms its backtest.",
    },
    "atr_volatility": {
        "title": "Volatility Weighting (ATR)",
        "category": "advanced",
        "explanation": (
            "Average True Range (ATR) measures how far price actually moves, "
            "regardless of direction gaps. Weighting levels by ATR makes "
            "stops and targets adapt to the market's current volatility "
            "instead of being fixed percentages.\n\n"
            "Formulas: TR = max(high−low, |high−prev_close|, |low−prev_close|); "
            "ATR = EMA(TR). This system uses ATR for regime-adaptive SL/TP "
            "(1.2–2.0× ATR in ranges, 2.0–3.0× in trends) and for the "
            "executor's default levels (1.5× ATR stop, 2R target)."),
        "example": (
            "A 1.5× ATR stop on a 2% ATR coin sits 3% away; on a 0.5% ATR "
            "coin it sits 0.75% away — both are 'one and a half days of "
            "noise' in their own market."),
        "why_it_matters": "Fixed % stops get chopped out in volatile markets and give back too much in calm ones.",
    },
    "liquidity_traps": {
        "title": "Liquidity Traps",
        "category": "advanced",
        "explanation": (
            "A liquidity trap looks tradable from a chart (price, volume) "
            "but has no real depth — one large order moves it, or the "
            "volume is wash trading. Signs: thin order book vs high printed "
            "volume, wide spread, price gaps between candles.\n\n"
            "This system's guards: a minimum-volume multiple (MIN_VOLUME_MULT "
            "against trailing median) in risk.py, per-symbol slippage tiers, "
            "and the data-quality gate that rejects spike candles without "
            "volume confirmation."),
        "example": (
            "A listing with $5k/hour printed volume but a 50-tick book "
            "fails the volume sanity check and is held `data` — never "
            "traded at a mid price that doesn't exist."),
        "why_it_matters": "Backtests on trapped names fill at mid; live fills happen at the book — catastrophic variance.",
    },
    "order_book_imbalance": {
        "title": "Order Book Imbalance",
        "category": "advanced",
        "explanation": (
            "Order-book imbalance measures whether resting liquidity leans "
            "bid or ask: imbalance = (bid_size − ask_size) / (bid_size + "
            "ask_size). Strong positive imbalance often precedes upward "
            "pressure (buyers standing), strong negative precedes selling.\n\n"
            "The current system reads trades/candles, not the live book; "
            "adding a WebSocket book feed (documented in the roadmap) is the "
            "natural upgrade for this signal."),
        "example": (
            "BTC book: 40 BTC bid vs 10 BTC ask → imbalance +0.6 — buyers "
            "are defending; a momentum strategy would weight longs higher."),
        "why_it_matters": "Candles tell you what happened; the book tells you who is willing to transact next.",
    },
    "multi_timeframe": {
        "title": "Multi-Timeframe Confirmation",
        "category": "advanced",
        "explanation": (
            "Signals are stronger when several timeframes agree: a LONG on "
            "1h that also shows an uptrend on 4h/1d is a higher-conviction "
            "trade than a 1h blip against the daily trend.\n\n"
            "The math engine already fuses multiple indicator families on "
            "one timeframe; multi-TF confirmation (e.g. 15m entry signal "
            "filtered by 4h regime) is the documented next step in "
            "docs/updates/TASKS_TODO.md."),
        "example": (
            "ADX trending up on 4h + RSI(2) oversold on 15m = a mean-"
            "reversion LONG in the direction of the larger trend — the "
            "strategy v2 trend filter works exactly this way."),
        "why_it_matters": "Single-timeframe signals fire into opposing larger trends; confirmation cuts whipsaws.",
    },
}


def list_topics() -> list[dict]:
    """Summaries of every topic, grouped for the UI."""
    return [
        {"topic": t, "title": v["title"], "category": v["category"],
         "explanation": v["explanation"].split("\n\n")[0]}
        for t, v in TOPICS.items()
    ]


def explain(topic: str) -> dict | None:
    """Full explanation for one topic, or None if unknown."""
    t = TOPICS.get((topic or "").strip().lower())
    if not t:
        return None
    return {"topic": topic.lower(), **t}


# ── Why this trade (journal + bot-cycle context) ─────────────────────
def explain_trade(order: dict | None, held: dict | None = None) -> dict:
    """Plain-English explanation of why a journal order happened, or why a
    symbol was held back. `held` is the held-reason detail from the bot
    cycle (reason, detail, a/b source opinions)."""
    if order is None and held is None:
        return {"ok": False, "message": "No trade or held entry to explain."}

    if order is not None:
        side = str(order.get("side", "?")).upper()
        symbol = order.get("symbol", "?")
        status = order.get("status", "?")
        reasons = str(order.get("reasons") or "") or "no recorded reasons"
        source = order.get("source", "?")
        if status == "dry_run":
            headline = f"{symbol} {side} was a DRY-RUN preview — no order reached the broker."
        elif status in ("filled", "open"):
            headline = f"{symbol} {side} was taken and is {status}."
        else:
            headline = f"{symbol} {side} ended {status}."
        return {
            "ok": True, "symbol": symbol, "side": side, "status": status,
            "headline": headline,
            "why": (
                "The agreement gate let this trade through because the "
                "signal sources met the bar (see 'Why' row: " + reasons + "). "
                "The entry/SL/TP and size were then set by the executor's "
                "risk model (risk % of equity, ATR-based stop)."),
            "sources": source,
            "reasons": reasons,
        }

    # Held-back explanation.
    reason = str(held.get("reason") or "unknown")
    symbol = held.get("symbol", "?")
    detail = held.get("detail") or ""
    label = {
        "not_on_broker": "the asset isn't listed on the configured broker, so an order would be rejected",
        "no_short_on_spot": "the broker is a spot (long-only) account — a SHORT can't be expressed",
        "no_agreement": "the signal sources disagreed or were quiet, so the two-source gate held",
        "no_signal_ai": "the AI/news analyst had no signal for this symbol this cycle (no headline above the noise threshold)",
        "no_signal_a": "source A produced no opinion for this symbol this cycle",
        "data": "the candles failed the data-quality gate (stale / spiked / insufficient)",
        "cross_source": "two independent price feeds disagreed beyond the tolerance",
        "max_positions": "the maximum open-position cap was already reached",
        "duplicate": "the same intent was already traded today (cooldown)",
        "intent": "the re-gate before execution rejected the intent",
        "news_veto": "the news review vetoed the trade (opposing headlines or a big move expected)",
    }.get(reason, "a gate held it back")
    return {
        "ok": True, "symbol": symbol, "headline": f"{symbol} was held back: {label}.",
        "why": f"Reason: {reason}. {detail if detail else 'See the held-by-reason panel for the full cycle context.'}",
        "reason": reason,
    }


def explain_last_cycle(summary: dict | None) -> dict:
    """Summarise a bot-cycle summary into the top reasons trades were taken
    or skipped (used by /api/tutor/trade without an order id)."""
    if not summary:
        return {"ok": False, "message": "No bot cycle has run yet."}
    n_exec = len(summary.get("executed", []))
    n_held = len(summary.get("held", []))
    reasons = {}
    for h in summary.get("held", []):
        reasons[str(h.get("reason"))] = reasons.get(str(h.get("reason")), 0) + 1
    top = sorted(reasons.items(), key=lambda kv: -kv[1])[:4]
    return {
        "ok": True,
        "headline": f"Last cycle: {n_exec} traded, {n_held} held back, "
                    f"{len(summary.get('errors', []))} errors.",
        "why": "Trades happened when both signal sources agreed (or a single "
               "high-confidence source in test mode) AND every gate passed.",
        "held_top": [{"reason": r, "count": c} for r, c in top],
    }
