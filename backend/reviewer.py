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
import time
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
    "DXY": ["dollar index", "dxy", "greenback", "us dollar"],
    "EUR/USD": ["euro", "eur/usd", "single currency"],
    "GBP/USD": ["pound", "sterling", "gbp/usd", "british pound"],
    "USD/JPY": ["yen", "usd/jpy", "japanese yen"],
    "USD/CHF": ["swiss franc", "franc", "usd/chf"],
    "AUD/USD": ["aussie", "australian dollar", "aud/usd"],
    "USD/CAD": ["loonie", "canadian dollar", "usd/cad"],
    "USD/CNY": ["yuan", "renminbi", "chinese yuan", "usd/cny"],
    "EUR/GBP": ["eur/gbp", "pound-euro", "euro-sterling"],
    "EUR/JPY": ["eur/jpy", "euro-yen"],
    "GBP/JPY": ["gbp/jpy", "pound-yen", "sterling-yen"],
    "AUD/JPY": ["aud/jpy", "aussie-yen"],
    "NZD/USD": ["kiwi", "nzd/usd", "new zealand dollar"],
    "USD/SGD": ["singapore dollar", "usd/sgd"],
    "USD/MXN": ["mexican peso", "usd/mxn", "peso"],
    "EUR/CHF": ["eur/chf", "euro-franc"],
    "EUR/AUD": ["eur/aud", "euro-australian"],
    "EUR/CAD": ["eur/cad", "euro-canadian"],
    "EUR/NZD": ["eur/nzd"],
    "GBP/AUD": ["gbp/aud", "pound-aussie"],
    "GBP/CAD": ["gbp/cad"],
    "GBP/NZD": ["gbp/nzd"],
    "GBP/CHF": ["gbp/chf", "pound-franc"],
    "AUD/CAD": ["aud/cad"],
    "AUD/NZD": ["aud/nzd", "trans-tasman"],
    "NZD/CHF": ["nzd/chf"],
    "CAD/JPY": ["cad/jpy"],
    "CHF/JPY": ["chf/jpy"],
    "NZD/JPY": ["nzd/jpy"],
    "USD/ZAR": ["rand", "usd/zar", "south african rand"],
    "USD/TRY": ["lira", "usd/try", "turkish lira"],
    "USD/SEK": ["swedish krona", "usd/sek"],
    "USD/NOK": ["norwegian krone", "usd/nok"],
    "USD/PLN": ["zloty", "usd/pln", "polish zloty"],
    "DAX": ["dax", "german index", "german stocks"],
    "NIKKEI": ["nikkei", "japanese index", "japan stocks"],
    "FTSE": ["ftse", "ftse 100", "uk stocks", "british stocks"],
    "EUROSTOXX": ["euro stoxx", "stoxx 50", "european stocks", "eurostoxx"],
    "HANG_SENG": ["hang seng", "hsi", "hong kong stocks", "hk50"],
    "FR40": ["cac 40", "cac", "french stocks", "fr40"],
    "ES35": ["ibex", "spanish stocks", "es35"],
    "ITA40": ["ftse mib", "italian stocks", "ita40"],
    "AU200": ["asx 200", "asx", "australian stocks", "au200"],
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
# Optional LLM overlay (OpenAI-compatible chat completions, with failover)
# ----------------------------------------------------------------------

# If every configured provider fails (down / rate-limited / bad output), back
# off for a while instead of hammering them on every 5-minute refresh.
_llm_cooldown_until = 0.0
LLM_COOLDOWN_SECONDS = 600.0  # 10 minutes

# The last SUCCESSFUL LLM verdict, served from cache for
# LLM_MIN_INTERVAL_SECONDS so free tiers aren't re-queried on every 20s
# signals refresh (Gemini Flash ~tens of req/day, OpenRouter 50-1000/day,
# Groq ~1000/day).
_llm_result_cache = {"at": 0.0, "data": None}


def _llm_providers() -> list[dict]:
    """Primary provider first (LLM_*), then every configured fallback, then
    the local OmniRoute gateway when enabled (keyless, config.OMNIROUTE_BASE_URL).

    Providers without an API key are skipped UNLESS they are local
    (localhost / 127.0.0.1, e.g. llama.cpp, OmniRoute). Cloud free tiers
    all require a key — a keyless cloud entry would just burn a guaranteed
    401 on every cycle, so it's filtered out instead."""
    providers = [
        {"base_url": config.LLM_BASE_URL, "model": config.LLM_MODEL,
         "api_key": config.LLM_API_KEY}
    ]
    providers += list(config.LLM_FALLBACKS)
    if config.OMNIROUTE_ENABLED and config.OMNIROUTE_BASE_URL:
        providers.append({"base_url": config.OMNIROUTE_BASE_URL,
                          "model": config.OMNIROUTE_MODEL,
                          "api_key": config.OMNIROUTE_API_KEY})
    out = []
    for p in providers:
        base = (p.get("base_url") or "").rstrip("/")
        if not base or not p.get("model"):
            continue
        if not p.get("api_key") and "localhost" not in base \
                and "127.0.0.1" not in base:
            continue  # keyless cloud provider -> guaranteed 401, skip
        out.append(p)
    return out


def _parse_llm_json(content: str):
    """Strip markdown fences / prose and parse provider JSON.

    Models often wrap the answer in a code fence with a lead-in sentence
    ("Based on the headlines...\n\n```json\n{...}\n```") — the fence
    regex alone misses that, so as a fallback we extract the first
    balanced-looking {…} block and parse it. Returns dict or None.
    """
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (TypeError, ValueError):
        pass
    # Prose-wrapped JSON: pull the first {…} block out and try again.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except (TypeError, ValueError):
            pass
    return None


def _llm_request(provider: dict, prompt: str):
    """One chat-completions call to a provider. Returns parsed JSON dict or None."""
    headers = {"Content-Type": "application/json"}
    api_key = provider.get("api_key") or ""
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(
        provider["base_url"] + "/chat/completions",
        headers=headers,
        json={"model": provider["model"], "temperature": 0.0,
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
    return _parse_llm_json(content)


def _llm_data_to_coins(data: dict) -> list:
    """Convert a provider's {symbol: {score, stance, reasons}} payload into our
    per-coin rows. Returns [] if nothing usable came back."""
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
    return coins


def _llm_review(conn, limit: int) -> dict:
    """Ask the LLM chain (primary + fallbacks) for a per-coin call.

    Tries each provider in order; the first that returns usable JSON wins.
    Returns None when every provider fails (a cooldown prevents retry spam),
    when there is no news to analyze, or on any other failure.
    """
    global _llm_cooldown_until
    if time.time() < _llm_cooldown_until:
        return None
    # Serve the cached LLM verdict inside the min-interval window — free
    # tiers can't take a call every 20s.
    if (_llm_result_cache["data"] is not None
            and time.time() - _llm_result_cache["at"] < config.LLM_MIN_INTERVAL_SECONDS):
        return _llm_result_cache["data"]
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

        for provider in _llm_providers():
            try:
                data = _llm_request(provider, prompt)
            except Exception:
                continue  # this provider is down/rate-limited — try the next
            if not data:
                continue  # unusable output — try the next
            coins = _llm_data_to_coins(data)
            if not coins:
                continue
            avg = sum(c["score"] for c in coins) / len(coins)
            market = ("Bullish" if avg >= STANCE_BULL
                      else "Bearish" if avg <= STANCE_BEAR else "Neutral")
            result = {"coins": coins, "stories": len(items), "window_hours": None,
                      "market": market, "mode": "llm", "model": provider["model"],
                      "generated_at": _now_iso()}
            _llm_result_cache.update({"at": time.time(), "data": result})
            return result

        # Every provider failed (network, rate limit, or bad JSON) — back off.
        _llm_cooldown_until = time.time() + LLM_COOLDOWN_SECONDS
        return None
    except Exception:
        _llm_cooldown_until = time.time() + LLM_COOLDOWN_SECONDS
        return None


# ----------------------------------------------------------------------
# News verification of a proposed trade (the confirmation layer)
# ----------------------------------------------------------------------
def verdict_for(score, direction: str) -> dict:
    """Pure verdict from a news score (-100..+100) and a proposed trade
    direction — shared by verify_trade (bot gate) and the signal cards
    (dashboard). Returns {verdict, message, big_move}."""
    score = int(score or 0)
    direction = (direction or "").upper()
    big = abs(score) >= config.NEWS_BIG_MOVE_SCORE
    stance = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"
    if big:
        verdict = "veto"
        message = (f"big move expected (news {stance}, {score:+d}) — "
                   f"standing aside, the signal would be whipsawed")
    elif direction == "LONG" and score <= -config.NEWS_VETO_SCORE:
        verdict, message = "veto", f"news bearish ({score:+d}) against LONG"
    elif direction == "SHORT" and score >= config.NEWS_VETO_SCORE:
        verdict, message = "veto", f"news bullish ({score:+d}) against SHORT"
    elif (direction == "LONG" and score >= config.NEWS_CONFIRM_SCORE) or \
            (direction == "SHORT" and score <= -config.NEWS_CONFIRM_SCORE):
        verdict, message = "confirm", f"news {stance} ({score:+d}) supports {direction}"
    else:
        verdict, message = "neutral", "news neutral or too weak to matter — allow"
    return {"verdict": verdict, "message": message, "big_move": big}


def verify_trade(conn, symbol: str, direction: str) -> dict:
    """
    News verification of a proposed trade — the "confirmation AI" gate.

    Called by the bot AFTER the signal sources agree on an intent, BEFORE
    execution. It can only veto or endorse, never initiate:

      veto     — headlines strongly oppose the trade, OR an extreme score
                 in either direction means a big move is expected (the
                 math signal is likely to be whipsawed → stand aside).
      confirm  — headlines support the trade direction (journaled as a
                 "news confirms" reason on the intent).
      neutral  — no news or too weak to matter → never blocks a trade.

    Returns a dict with verdict, the raw news score, the top reasons, and
    which review mode produced the score ("rule" or the LLM overlay).
    """
    review = review_market(conn, limit=40)
    row = next((c for c in review.get("coins", []) if c.get("symbol") == symbol), None)
    base = {"mode": review.get("mode", "rule"), "model": review.get("model"),
            "big_move": False}
    if not row:
        base.update({"verdict": "neutral", "score": 0, "stance": "neutral",
                     "reasons": [], "message": "no news coverage — allow"})
        return base

    score = int(row.get("score") or 0)
    stance = row.get("stance", "neutral")
    reasons = [r.get("title") for r in (row.get("reasons") or [])
               if isinstance(r, dict) and r.get("title")]
    v = verdict_for(score, direction)
    base.update({"verdict": v["verdict"], "score": score, "stance": stance,
                 "big_move": v["big_move"], "reasons": reasons[:3],
                 "message": v["message"],
                 "stories": int(row.get("stories") or 0)})
    return base


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------
def review_market(conn, limit: int = 40, hours: float = 48.0) -> dict:
    """Market review: Groq news analyst (the main engine) when enabled and
    keyed, otherwise rule-based view, optionally overlaid by the LLM."""
    # Groq analyst first — it returns structured per-asset sentiment with
    # confidence/horizon and back-fills every coin from the rule-based
    # scorer, so this is a strict upgrade when an LLM is available (Groq
    # key or the OmniRoute local gateway).
    if config.NEWS_ANALYST_ENABLED and config.llm_available():
        from news_analyst import analyze_market  # lazy — avoids circular import
        try:
            groq = analyze_market(conn, limit=limit)
            if groq and groq.get("coins"):
                return groq
        except Exception:
            pass  # fall through to the existing chain on any failure

    result = _rule_review(conn, limit, hours)

    # LLM overlay is enabled if LLM_ENABLED=true AND at least one provider is
    # usable (primary key, or a configured fallback — e.g. fallback-only setup).
    if config.LLM_ENABLED and _llm_providers():
        llm = _llm_review(conn, limit)
        if llm:
            # Keep the rule-based reasons as a fallback trail, but take the
            # LLM's verdicts (it's the overlay, advisory by design).
            return llm

    result["generated_at"] = _now_iso()
    return result
