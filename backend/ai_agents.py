"""
cryptoautomation — the three production AI agents (on Groq).

The user-provided production prompts, wired onto the same Groq transport as
the news analyst (news_analyst.chat_json — retry/backoff, model fallback,
never raises):

  1. agent_news_sentiment  — "News & Catalyst Sentiment": one headline →
       {sentiment, impact_score, is_black_swan, expected_duration,
        core_catalyst}. Used by the dashboard's news analyzer and by the
       bot's news gate as an extra lens on breaking stories.

  2. agent_hold_research   — "Deep Research & HODL Portfolio": a detected
       catalyst for a symbol → {macro_tailwinds, risk_falsification,
        hold_recommendation, confidence_percentage}. Answers "day-trade it
       or hold it long-term" — the input to portfolio allocation.

  3. agent_chart_confluence — "Price Action & Chart Confluence": a
       news-driven trade signal verified against live chart structure
       (price vs nearest support/resistance, 1h momentum, news sentiment)
       → {is_valid_setup, rejection_reason, entry_strategy,
        calculated_stop_loss}. Flags DIVERGENCE / TRAP setups so news
       signals are not bought blindly under resistance.

Every agent:
  - loads its key from .env (GROQ_API_KEY) — never hardcoded,
  - parses strictly (rejects answers that don't fit the schema),
  - NEVER raises: on any failure it returns None and the caller falls back
    to the deterministic path (news_analyst / no-op gate),
  - journals its structured answer into `ai_analyses` (see database.py)
    so the dashboard can show why a trade was taken or skipped.

Enabling the confluence gate (config.AI_CONFLUENCE_GATE) makes the
news-driven source B verify every LONG/SHORT proposal against the chart
before it reaches the arbitrator — off by default, on purpose: it changes
which trades fire.
"""

from __future__ import annotations

import time

import config
import database
import news_analyst

# Cache for the confluence gate: one chart verification per symbol per
# NEWS_ANALYST_CACHE_TTL (10 min) — the 5-min bot loop must not burn the
# free tier on the same symbol every cycle.
_confluence_cache: dict[str, dict] = {}


# ----------------------------------------------------------------------
# Shared plumbing
# ----------------------------------------------------------------------
def _agent_call(prompt: str, system: str, max_tokens: int = 900) -> dict | None:
    """One structured agent call on the shared LLM transport. Never raises."""
    if not config.AI_AGENTS_ENABLED or not config.llm_available():
        return None
    try:
        return news_analyst.chat_json(prompt, system=system,
                                      max_tokens=max_tokens)
    except Exception:
        return None


def _journal(conn, agent: str, symbol: str, request: str,
             verdict: str, payload: dict) -> None:
    """Best-effort journal of an agent answer (see database.py)."""
    database.record_ai_analysis(conn, agent, symbol, request, verdict,
                                payload, model=config.AI_AGENT_MODEL)


def _clean_float(value, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


# ----------------------------------------------------------------------
# Agent 1 — News & Catalyst Sentiment
# ----------------------------------------------------------------------
_AGENT1_SYSTEM = (
    "You are an elite quantitative crypto and forex analyst. Your job is to "
    "instantly evaluate breaking market news and extract the structural "
    "impact on price action.\n\n"
    "Ignore sensationalism. Focus on liquidity shifts, institutional "
    "adoption, protocol upgrades, and macroeconomic data.\n\n"
    "Analyze the news provided by the user and output strictly as a JSON "
    "object with no markdown formatting or conversational text, matching "
    "this exact schema:\n"
    '{"sentiment": "BULLISH" | "BEARISH" | "NEUTRAL", '
    '"impact_score": 0.0, "is_black_swan": false, '
    '"expected_duration": "IMMEDIATE" | "MULTI_DAY" | "LONG_TERM_MACRO", '
    '"core_catalyst": "one sentence"}\n'
    "impact_score is a float 0.0-1.0 where 0.8+ means a major liquidity "
    "event. Reply with ONLY valid JSON."
)

_AGENT1_SYSTEM_DICT = _AGENT1_SYSTEM  # (kept for parity with the prompt spec)


def agent_news_sentiment(headline: str, content: str = "",
                         conn=None, symbol: str = "") -> dict | None:
    """Agent 1: score one headline/article for structural price impact.

    Returns {sentiment, impact_score, is_black_swan, expected_duration,
    core_catalyst} or None (off / failure / unparseable). The answer is
    journaled into ai_analyses when `conn` is given.
    """
    if not headline or not headline.strip():
        return None
    prompt = (f"Analyze the following news:\n"
              f"Headline: {headline.strip()}\n"
              f"Context: {(content or '').strip()[:2000]}")
    data = _agent_call(prompt, _AGENT1_SYSTEM)
    if not data:
        return None
    sentiment = str(data.get("sentiment") or "").strip().upper()
    if sentiment not in ("BULLISH", "BEARISH", "NEUTRAL"):
        return None  # schema violation — don't trust it
    result = {
        "sentiment": sentiment,
        "impact_score": _clean_float(data.get("impact_score"), 0.0, 1.0, 0.0),
        "is_black_swan": bool(data.get("is_black_swan", False)),
        "expected_duration": str(data.get("expected_duration")
                                 or "IMMEDIATE").strip().upper()[:20],
        "core_catalyst": str(data.get("core_catalyst") or "").strip()[:400],
    }
    if conn is not None:
        _journal(conn, "news_sentiment", symbol, headline, sentiment, result)
    return result


# ----------------------------------------------------------------------
# Agent 2 — Deep Research & HODL Portfolio
# ----------------------------------------------------------------------
_AGENT2_SYSTEM = (
    "You are the portfolio architect for a decentralized trading platform. "
    "A major catalyst has just been detected for the asset the user names. "
    "Based on your internal knowledge base and current macro trends, "
    "analyze if this asset is a strategic long-term hold or just a "
    "short-term trading vehicle.\n\n"
    "Evaluate based on: tokenomics / fiat-inflation resistance; on-chain "
    "utility and ecosystem growth; counter-party risk.\n\n"
    "Output strictly as a JSON object with no markdown formatting or "
    "conversational text, matching this exact schema:\n"
    '{"macro_tailwinds": "brief description", '
    '"risk_falsification": "biggest risk that invalidates this trade", '
    '"hold_recommendation": "ACCUMULATE_AND_HOLD" | "TRADE_ONLY" | "AVOID", '
    '"confidence_percentage": 75}\n'
    "confidence_percentage is an integer 0-100. Reply with ONLY valid JSON."
)


def agent_hold_research(symbol: str, core_catalyst: str = "",
                        conn=None) -> dict | None:
    """Agent 2: is this asset a strategic hold or a short-term vehicle?

    Returns {macro_tailwinds, risk_falsification, hold_recommendation,
    confidence_percentage} or None (off / failure / unparseable).
    """
    if not symbol:
        return None
    prompt = (f"A major catalyst has just been detected for {symbol}.\n"
              f"Catalyst: {(core_catalyst or '').strip()[:1000]}\n")
    data = _agent_call(prompt, _AGENT2_SYSTEM)
    if not data:
        return None
    rec = str(data.get("hold_recommendation") or "").strip().upper()
    if rec not in ("ACCUMULATE_AND_HOLD", "TRADE_ONLY", "AVOID"):
        rec = "TRADE_ONLY"
    result = {
        "symbol": symbol,
        "macro_tailwinds": str(data.get("macro_tailwinds") or "").strip()[:600],
        "risk_falsification": str(data.get("risk_falsification") or "").strip()[:600],
        "hold_recommendation": rec,
        "confidence_percentage": int(_clean_float(
            data.get("confidence_percentage"), 0, 100, 50)),
    }
    if conn is not None:
        _journal(conn, "hold_research", symbol,
                 (core_catalyst or symbol)[:200], rec, result)
    return result


# ----------------------------------------------------------------------
# Chart structure helpers (S/R detection — feeds agent 3)
# ----------------------------------------------------------------------
def detect_support_resistance(conn, symbol: str, interval: str = "1h",
                              lookback: int = 200, swing: int = 5):
    """Nearest support / resistance for `symbol` from swing pivots on the
    last `lookback` candles.

    A pivot high is a bar whose high beats the `swing` bars on each side;
    resistance is the nearest pivot above the current price (fallback: the
    24-bar high). Symmetric for support. Returns (support, resistance) as
    floats, or (None, None) when there is no data.
    """
    df = database.load_candles(conn, symbol, interval, limit=lookback)
    if df.empty:
        return None, None
    price = float(df["close"].iloc[-1])
    highs = [float(x) for x in df["high"]]
    lows = [float(x) for x in df["low"]]
    n = len(df)
    resistances, supports = [], []
    for i in range(swing, n - swing):
        h = highs[i]
        if h > max(highs[i - swing:i]) and h >= max(highs[i + 1:i + swing + 1]):
            resistances.append(h)
        l = lows[i]
        if l < min(lows[i - swing:i]) and l <= min(lows[i + 1:i + swing + 1]):
            supports.append(l)
    above = [r for r in resistances if r > price]
    below = [s for s in supports if s < price]
    resistance = min(above) if above else max(highs[-24:]) if n >= 24 else max(highs)
    support = max(below) if below else min(lows[-24:]) if n >= 24 else min(lows)
    return support, resistance


# ----------------------------------------------------------------------
# Agent 3 — Price Action & Chart Confluence
# ----------------------------------------------------------------------
_AGENT3_SYSTEM = (
    "You are a highly disciplined trading execution bot. You are receiving "
    "a signal to trade the named asset based on news, but you must verify "
    "it against the current chart structure to avoid liquidity traps.\n\n"
    "The user provides: current price, 1H change, nearest support, nearest "
    "resistance, news sentiment, and the proposed direction.\n\n"
    "Rules:\n"
    "- Never buy immediately below heavy resistance.\n"
    "- Never short immediately above heavy support.\n"
    "- If the news is BULLISH but the price is dumping through support, "
    "flag as a \"DIVERGENCE / TRAP\".\n\n"
    "Output strictly as a JSON object with no markdown formatting or "
    "conversational text, matching this exact schema:\n"
    '{"is_valid_setup": true, "rejection_reason": "", '
    '"entry_strategy": "MARKET_BUY" | "WAIT_FOR_RETEST" | "ABORT", '
    '"calculated_stop_loss": 0.0}\n'
    "rejection_reason: leave blank if valid, otherwise explain why the "
    "chart invalidates the news. calculated_stop_loss: a float just "
    "outside support/resistance. Reply with ONLY valid JSON."
)


def _news_stance(conn, symbol: str) -> str:
    """BULLISH / BEARISH / NEUTRAL from the news analyst (best-effort)."""
    try:
        row = news_analyst.analytics_for(conn, symbol)
        if row:
            return str(row.get("stance") or "NEUTRAL").upper()
    except Exception:
        pass
    return "NEUTRAL"


def agent_chart_confluence(conn, symbol: str, direction: str = "LONG",
                           force: bool = False) -> dict | None:
    """Agent 3: verify a news-driven signal against the live chart.

    Pulls price / 1h change / S/R from the DB, news sentiment from the
    analyst, then asks the model whether the setup is valid. Cached per
    symbol (NEWS_ANALYST_CACHE_TTL) so the bot loop doesn't re-ask every
    cycle. Returns {is_valid_setup, rejection_reason, entry_strategy,
    calculated_stop_loss, support, resistance, price} or None.
    """
    if not config.AI_AGENTS_ENABLED or not config.llm_available():
        return None
    now = time.time()
    cached = _confluence_cache.get(symbol)
    if not force and cached and now - cached["at"] < config.NEWS_ANALYST_CACHE_TTL:
        return cached["data"]

    df = database.load_candles(conn, symbol, "1h", limit=3)
    if df.empty:
        return None
    price = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2]) if len(df) > 1 else price
    change_1h = ((price - prev) / prev * 100) if prev else 0.0
    support, resistance = detect_support_resistance(conn, symbol)
    sentiment = _news_stance(conn, symbol)

    prompt = (
        f"You are receiving a signal to trade {symbol} based on news, but "
        f"you must verify it against the current chart structure to avoid "
        f"liquidity traps.\n\n"
        f"Current Market Data:\n"
        f"Price: {price}\n"
        f"1H Change: {change_1h:.2f}%\n"
        f"Nearest Support: {support}\n"
        f"Nearest Resistance: {resistance}\n"
        f"News Sentiment: {sentiment}\n"
        f"Proposed Direction: {direction}\n"
    )
    data = _agent_call(prompt, _AGENT3_SYSTEM)
    if data is None:
        return None
    try:
        valid = bool(data.get("is_valid_setup"))
    except (TypeError, ValueError):
        valid = False
    result = {
        "symbol": symbol,
        "is_valid_setup": valid,
        "rejection_reason": str(data.get("rejection_reason") or "").strip()[:400],
        "entry_strategy": str(data.get("entry_strategy") or "ABORT").strip().upper()[:20],
        "calculated_stop_loss": _clean_float(data.get("calculated_stop_loss"), 0.0, price * 10, 0.0),
        "support": support, "resistance": resistance,
        "price": price, "change_1h": round(change_1h, 2),
        "news_sentiment": sentiment, "direction": str(direction).upper(),
    }
    _confluence_cache[symbol] = {"at": now, "data": result}
    if conn is not None:
        _journal(conn, "chart_confluence", symbol,
                 f"{direction} @ {price} (news {sentiment})",
                 "VALID" if valid else "INVALID", result)
    return result


# ----------------------------------------------------------------------
# The confluence gate for the news-driven source B (proprietary_signals)
# ----------------------------------------------------------------------
def gate_news_trade(conn, symbol: str, direction: str) -> dict:
    """Gate a news-driven proposal through agent 3. Returns a verdict dict:

        {"pass": True/False, "verdict": {…agent result…}, "reason": str}

    pass=True keeps the proposal; pass=False drops it (rejected by the
    chart). Off (AI_AGENTS_ENABLED/AI_CONFLUENCE_GATE) or agent failure →
    pass=True (the gate never blocks trades it can't run — the news gate
    and arbitrator still apply their own checks). Never raises.
    """
    default = {"pass": True, "verdict": None, "reason": "gate off or unavailable"}
    if not (config.AI_AGENTS_ENABLED and config.AI_CONFLUENCE_GATE
            and config.llm_available()):
        return default
    try:
        result = agent_chart_confluence(conn, symbol, direction)
    except Exception:
        return default
    if result is None:
        return default
    if result.get("is_valid_setup"):
        return {"pass": True, "verdict": result,
                "reason": f"chart confluence ok ({result.get('entry_strategy')})"}
    return {"pass": False, "verdict": result,
            "reason": (result.get("rejection_reason")
                       or f"{result.get('entry_strategy')} — chart invalidates news")}
