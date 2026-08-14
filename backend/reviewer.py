"""
cryptoautomation — AI news reviewer.

Turns the raw news feed into a one-glance market view: for every watched
coin, a score (-100..+100), a stance (bullish / bearish / neutral), and the
handful of stories driving that call.

Two scoring paths:

  1. **rule-based** (default, works with no keys) — recency-weighted
     sentiment, cross-referenced to the coins we actually watch. Fast,
     deterministic, always available.
  2. **LLM overlay** (optional) — when `LLM_ENABLED=true` and a key is set
     in `.env`, batch the latest headlines to any OpenAI-compatible chat
     endpoint (OpenAI, DeepSeek, Qwen/DashScope, local llama.cpp…) and ask
     for a structured per-coin call. If the LLM is unreachable or returns
     bad JSON, we silently keep the rule-based view — the LLM is advisory,
     never a hard dependency.

Exposed to the terminal as `GET /api/review`.
"""

import json
import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

import config
import database

# Symbols we watch, with the name / ticker tokens that let us cross-reference
# a headline to a coin even when the news API didn't tag it. Short tickers
# (<= 4 chars) are matched as whole words only, so "sol" hits SOL, not
# "solution". Macro assets (gold/oil/indices/forex) are included so the
# expanded news feed scores them too.
COIN_HINTS = {
    "BTC/USDT": ["bitcoin", "btc"],
    "ETH/USDT": ["ethereum", "ether", "eth"],
    "SOL/USDT": ["solana", "sol"],
    "BNB/USDT": ["binance coin", "bnb"],
    "DOGE/USDT": ["dogecoin", "doge"],
    "XRP/USDT": ["ripple", "xrp"],
    "ADA/USDT": ["cardano", "ada"],
    "LINK/USDT": ["chainlink", "link"],
    "AVAX/USDT": ["avalanche", "avax"],
    "DOT/USDT": ["polkadot", "dot"],
    "MATIC/USDT": ["polygon", "matic"],
    "LTC/USDT": ["litecoin", "ltc"],
    "XLM/USDT": ["stellar", "xlm"],
    "ATOM/USDT": ["cosmos", "atom"],
    "UNI/USDT": ["uniswap", "uni"],
    "ARB/USDT": ["arbitrum", "arb"],
    "OP/USDT": ["optimism"],
    "SHIB/USDT": ["shiba", "shib"],
    "PEPE/USDT": ["pepe"],
    # ── Macro assets ──
    "XAU/USD": ["gold", "xau", "bullion"],
    "XAG/USD": ["silver", "xag"],
    "XPT/USD": ["platinum", "xpt"],
    "XPD/USD": ["palladium", "xpd"],
    "COPPER/USD": ["copper"],
    "WTI/USD": ["crude oil", "wti", "oil price"],
    "BRENT/USD": ["brent"],
    "NAT_GAS/USD": ["natural gas", "natgas"],
    "SPX/USD": ["s&p 500", "spx", "s&p"],
    "NDX/USD": ["nasdaq"],
    "DOW/USD": ["dow jones", "dow"],
    "VIX": ["vix", "volatility index"],
    "DXY": ["dollar index", "dxy"],
    "EUR/USD": ["euro", "eur/usd"],
    "USD/JPY": ["yen", "usd/jpy"],
    "US10Y": ["treasury yield", "10-year", "10 year", "bonds"],
}

# Stance thresholds and how fast old news fades.
STANCE_BULL = 12.0
STANCE_BEAR = -12.0
HALF_LIFE_HOURS = 12.0

# Fallback base weight when a story has a sentiment label but score 0.
_LABEL_FALLBACK = {"bullish": 0.4, "bearish": -0.4, "important": 0.35, "neutral": 0.0}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_hours(published_at) -> float:
    """Age of a story in hours (0 if unparseable)."""
    if not published_at:
        return 0.0
    text = str(published_at)
    dt = None
    # News sources send a mix of RFC 2822 ("Mon, 10 Aug 2026 09:09:32 +0000")
    # and ISO-8601 strings, so try both.
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)


def _story_weight(item) -> float:
    """Raw sentiment pull of a story, -1..1."""
    score = item.get("sentiment_score")
    score = float(score) if score else 0.0
    if score == 0.0:
        score = _LABEL_FALLBACK.get(item.get("sentiment", "neutral"), 0.0)
    return max(-1.0, min(1.0, score))


def _mentions(item, hints) -> bool:
    """True if a story is about a coin (tagged, or via its own keywords only).

    Tickers (<= 4 chars) are matched as whole words so "sol" hits SOL but not
    "solution"; coin names are plain substrings.
    """
    symbols = (item.get("symbols") or "").lower()
    title = (item.get("title") or "").lower()
    if symbols and any(hint in symbols for hint in hints):
        return True
    for hint in hints:
        if len(hint) <= 4:
            if re.search(r"\b" + re.escape(hint) + r"\b", title):
                return True
        elif hint in title:
            return True
    return False


def _reasons_for(coin, stories):
    """Top 2 stories driving a coin's score, newest-first with a tie on weight."""
    ranked = sorted(
        stories,
        key=lambda s: (abs(s["w"]), s["published_at"] or ""),
        reverse=True,
    )
    return [
        {"title": s["title"], "sentiment": s["sentiment"], "url": s.get("url", "")}
        for s in ranked[:2]
    ]


# ----------------------------------------------------------------------
# Rule-based scorer
# ----------------------------------------------------------------------
def _rule_review(conn, limit: int, hours: float) -> dict:
    df = database.load_news(conn, limit=min(limit, 200))
    items = df.to_dict(orient="records")

    # Only consider stories from the last `hours` (fall back to whatever we
    # have if the feed is sparse).
    fresh = [i for i in items if _age_hours(i.get("published_at")) <= hours]
    stories = fresh if len(fresh) >= len(config.SYMBOLS) else items
    if not stories:
        return {"coins": [], "stories": 0, "window_hours": hours, "error": "no news yet"}

    coins = []
    for symbol, hints in COIN_HINTS.items():
        hits = []
        for s in stories:
            if not _mentions(s, hints):
                continue
            weight = _story_weight(s)
            decay = 0.5 ** (_age_hours(s.get("published_at")) / HALF_LIFE_HOURS)
            hits.append({"title": s.get("title", ""), "sentiment": s.get("sentiment", "neutral"),
                         "url": s.get("url", ""), "published_at": s.get("published_at", ""),
                         "w": weight * decay, "score": weight})
        total = sum(h["w"] for h in hits)
        score = round(max(-100.0, min(100.0, total * 100.0)))
        stance = "bullish" if score >= STANCE_BULL else "bearish" if score <= STANCE_BEAR else "neutral"
        coins.append({
            "symbol": symbol,
            "score": score,
            "stance": stance,
            "reasons": _reasons_for(symbol, hits),
            "stories": len(hits),
        })

    avg = (sum(c["score"] for c in coins) / len(coins)) if coins else 0
    market = ("Bullish" if avg >= STANCE_BULL else "Bearish" if avg <= STANCE_BEAR else "Neutral")
    return {"coins": coins, "stories": len(stories), "window_hours": hours,
            "market": market, "mode": "rule", "model": None}


# ----------------------------------------------------------------------
# Optional LLM overlay (OpenAI-compatible chat completions)
# ----------------------------------------------------------------------
def _llm_review(conn, limit: int) -> dict:
    """Ask the configured LLM for a per-coin call. Returns None on any failure."""
    try:
        df = database.load_news(conn, limit=min(limit, 20))
        items = df.to_dict(orient="records")
        if not items:
            return None

        lines = "\n".join(
            f"{i + 1}. [{it.get('sentiment','neutral')}] {it.get('title','')} ({it.get('source','')})"
            for i, it in enumerate(items)
        )
        prompt = (
            "Analyze the latest crypto headlines below, each tagged with a rough "
            "sentiment. Return a JSON object keyed by coin symbol "
            "(\"BTC\", \"ETH\", \"SOL\", \"BNB\", \"DOGE\") where each value is "
            "{\"score\": int from -100 to 100, \"stance\": \"bullish\" | \"bearish\" | "
            "\"neutral\", \"reasons\": \"one short sentence\"}. Skip a coin if there "
            "is no meaningful signal. Reply with ONLY valid JSON.\n\n" + lines
        )

        resp = requests.post(
            config.LLM_BASE_URL + "/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": config.LLM_MODEL, "temperature": 0.0,
                  "messages": [{"role": "system",
                                "content": "You are a cautious crypto market analyst. "
                                           "You score coins from headlines alone, never "
                                           "invent news."},
                               {"role": "user", "content": prompt}],
                  "max_tokens": 600},
            timeout=45,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        data = json.loads(text)
        if not isinstance(data, dict):
            return None

        coin_map = {s.split("/")[0]: s for s in config.SYMBOLS}
        coins = []
        for key, val in data.items():
            symbol = coin_map.get(str(key).upper())
            if not symbol or not isinstance(val, dict):
                continue
            score = val.get("score", 0)
            stance = val.get("stance", "neutral")
            if stance not in ("bullish", "bearish", "neutral"):
                stance = "neutral"
            coins.append({"symbol": symbol, "score": int(score), "stance": stance,
                          "reasons": [{"title": val.get("reasons", ""), "sentiment": stance,
                                       "url": ""}], "stories": 0})
        if not coins:
            return None

        avg = sum(c["score"] for c in coins) / len(coins)
        market = ("Bullish" if avg >= STANCE_BULL else "Bearish" if avg <= STANCE_BEAR else "Neutral")
        return {"coins": coins, "stories": len(items), "window_hours": None,
                "market": market, "mode": "llm", "model": config.LLM_MODEL}
    except Exception:
        return None


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------
def review_market(conn, limit: int = 40, hours: float = 48.0) -> dict:
    """Market review: rule-based view, optionally overlaid by the LLM."""
    result = _rule_review(conn, limit, hours)

    if config.LLM_ENABLED and config.LLM_API_KEY:
        llm = _llm_review(conn, limit)
        if llm:
            # Keep the rule-based reasons as a fallback trail, but take the
            # LLM's verdicts (it's the overlay, advisory by design).
            return llm

    result["generated_at"] = _now_iso()
    return result
