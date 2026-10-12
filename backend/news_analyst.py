"""
cryptoautomation — News Analyst (the news sentiment & analytics engine).

Turns the raw headline feed into structured, algorithm-ready analytics using
an OpenAI-compatible LLM transport. The provider chain (see `_providers()`)
prefers Groq (default `qwen/qwen3.6-27b`, fallback `llama-3.3-70b-versatile`,
key from .env) and adds OmniRoute (config.OMNIROUTE_BASE_URL — a free local
AI gateway, keyless `auto` combo) whenever it is enabled:

    https://api.groq.com/openai/v1        (primary, when GROQ_API_KEY set)
    http://localhost:20128/v1             (OmniRoute, when configured)

For every asset with meaningful news coverage it returns:

    asset            ticker (BTC, ETH, EURUSD, XAU, US500, DXY, …) — crypto
                     coins AND the Exness macro universe (forex/metals/energy/
                     indices) that the bot trades on
    sentiment_score  -1.0 .. +1.0   (directional market sentiment)
    confidence       0.0 .. 1.0     (how sure the model is)
    impact_horizon   short_term | medium_term | long_term
    summary          one-sentence plain-English rationale
    headlines        the titles that drove the call

Design rules (see docs/math_system/04-news-sentiment.md):
  - API keys come from .env (GROQ_API_KEY / OMNIROUTE_API_KEY) — never
    hardcoded; OmniRoute is a local gateway and is usually keyless.
  - Strict retry with exponential backoff on rate limits / 5xx; the next
    model / provider is tried before giving up, and a cooldown prevents
    hammering.
  - Cache: one Groq analysis per NEWS_ANALYST_CACHE_TTL (10 min default) —
    news moves slowly and free tiers are limited.
  - The module NEVER raises: on any failure it returns None / rule-based
    scores, so the bot and the dashboard keep working.
  - Assets the model skips are back-filled from the rule-based scorer, so
    every watched coin still has a score.

Exposed to the bot as `signal_for` (news-driven LONG/SHORT/HOLD entries) and
to the dashboard via `/api/news/analytics`.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

import config
import database

# ── Cache + cooldown (free-tier safe) ────────────────────────────────
_market_cache = {"at": 0.0, "data": None}
_cooldown_until = 0.0
COOLDOWN_SECONDS = 600.0

# Rough sentiment tag carried into the prompt (from the rule-based scorer),
# so the model sees an independent prior instead of a blank slate.
_LABEL_TAG = {"bullish": "bullish", "bearish": "bearish",
              "important": "notable", "neutral": "neutral"}


# ----------------------------------------------------------------------
# Asset → display-symbol resolution (crypto + the Exness macro universe)
# ----------------------------------------------------------------------
# The Groq model returns ticker-ish asset names (BTC, EURUSD, XAU, US500,
# DXY…). Normalize (strip non-alphanumerics, uppercase) and map to the
# display symbols the bot + dashboard know. Crypto bases are derived from
# config.SYMBOLS; the Exness instruments are explicit aliases so forex /
# metals / energy / index headlines actually drive trades (the bot trades
# ONLY this macro universe since 2026-08-17).
_MACRO_ASSET_MAP = {
    # forex majors + dollar index
    "EURUSD": "EUR/USD", "EUR": "EUR/USD",
    "GBPUSD": "GBP/USD", "GBP": "GBP/USD", "CABLE": "GBP/USD",
    "USDJPY": "USD/JPY", "JPY": "USD/JPY",
    "USDCHF": "USD/CHF", "CHF": "USD/CHF",
    "AUDUSD": "AUD/USD", "AUD": "AUD/USD", "AUSSIE": "AUD/USD",
    "USDCAD": "USD/CAD", "CAD": "USD/CAD", "LOONIE": "USD/CAD",
    "USDCNY": "USD/CNY", "CNY": "USD/CNY",
    # forex crosses + minors (2026-08-18: wider trading universe)
    "EURGBP": "EUR/GBP", "EUR/GBP": "EUR/GBP",
    "EURJPY": "EUR/JPY", "EUR/JPY": "EUR/JPY", "EUROYEN": "EUR/JPY",
    "GBPJPY": "GBP/JPY", "GBP/JPY": "GBP/JPY", "STERLINGYEN": "GBP/JPY",
    "AUDJPY": "AUD/JPY", "AUD/JPY": "AUD/JPY", "AUSSIEYEN": "AUD/JPY",
    "NZDUSD": "NZD/USD", "NZD": "NZD/USD", "KIWI": "NZD/USD",
    "USDSGD": "USD/SGD", "SGD": "USD/SGD", "SINGAPORE": "USD/SGD",
    "USDMXN": "USD/MXN", "MXN": "USD/MXN", "PESO": "USD/MXN",
    "EURCHF": "EUR/CHF", "EUR/CHF": "EUR/CHF",
    "EURAUD": "EUR/AUD", "EUR/AUD": "EUR/AUD",
    "EURCAD": "EUR/CAD", "EUR/CAD": "EUR/CAD",
    "EURNZD": "EUR/NZD", "EUR/NZD": "EUR/NZD",
    "GBPAUD": "GBP/AUD", "GBP/AUD": "GBP/AUD",
    "GBPCAD": "GBP/CAD", "GBP/CAD": "GBP/CAD",
    "GBPNZD": "GBP/NZD", "GBP/NZD": "GBP/NZD",
    "GBPCHF": "GBP/CHF", "GBP/CHF": "GBP/CHF", "POUNDFRANC": "GBP/CHF",
    "AUDCAD": "AUD/CAD", "AUD/CAD": "AUD/CAD",
    "AUDNZD": "AUD/NZD", "AUD/NZD": "AUD/NZD", "TRANSTASMAN": "AUD/NZD",
    "NZDCHF": "NZD/CHF", "NZD/CHF": "NZD/CHF",
    "CADJPY": "CAD/JPY", "CAD/JPY": "CAD/JPY",
    "CHFJPY": "CHF/JPY", "CHF/JPY": "CHF/JPY",
    "NZDJPY": "NZD/JPY", "NZD/JPY": "NZD/JPY",
    "USDZAR": "USD/ZAR", "ZAR": "USD/ZAR", "RAND": "USD/ZAR",
    "USDTRY": "USD/TRY", "TRY": "USD/TRY", "LIRA": "USD/TRY",
    "USDSEK": "USD/SEK", "SEK": "USD/SEK", "KRONA": "USD/SEK",
    "USDNOK": "USD/NOK", "NOK": "USD/NOK", "KRONE": "USD/NOK",
    "USDPLN": "USD/PLN", "PLN": "USD/PLN", "ZLOTY": "USD/PLN",
    "DXY": "DXY", "DOLLARINDEX": "DXY", "USDOLLARINDEX": "DXY",
    "USDOLLAR": "DXY", "DOLLAR": "DXY", "GREENBACK": "DXY",
    # metals + energy
    "XAU": "XAU/USD", "GOLD": "XAU/USD", "XAUUSD": "XAU/USD",
    "XAG": "XAG/USD", "SILVER": "XAG/USD", "XAGUSD": "XAG/USD",
    "XPT": "XPT/USD", "PLATINUM": "XPT/USD", "XPTUSD": "XPT/USD",
    "XPD": "XPD/USD", "PALLADIUM": "XPD/USD", "XPDUSD": "XPD/USD",
    "WTI": "WTI/USD", "USOIL": "WTI/USD", "OIL": "WTI/USD",
    "CRUDE": "WTI/USD", "WTIUSD": "WTI/USD",
    "BRENT": "BRENT/USD", "BRENTUSD": "BRENT/USD",
    "NGAS": "NAT_GAS/USD", "NATGAS": "NAT_GAS/USD", "NATURALGAS": "NAT_GAS/USD",
    # equity indices
    "SPX": "SPX/USD", "SP500": "SPX/USD", "US500": "SPX/USD",
    "S&P500": "SPX/USD", "SPXUSD": "SPX/USD",
    "NDX": "NDX/USD", "NAS100": "NDX/USD", "US100": "NDX/USD",
    "USTEC": "NDX/USD", "NASDAQ100": "NDX/USD",
    "DOW": "DOW/USD", "US30": "DOW/USD", "DJIA": "DOW/USD",
    "DOWJONES": "DOW/USD", "DOWINDUS": "DOW/USD",
    "DAX": "DAX", "DE30": "DAX", "GER30": "DAX", "DAX40": "DAX",
    "NIKKEI": "NIKKEI", "N225": "NIKKEI", "JP225": "NIKKEI",
    "NIKKEI225": "NIKKEI", "NI225": "NIKKEI",
    "FTSE": "FTSE", "UK100": "FTSE", "FTSE100": "FTSE",
    "EUROSTOXX": "EUROSTOXX", "EU50": "EUROSTOXX", "STOXX": "EUROSTOXX",
    "STOXX50": "EUROSTOXX",
    "HANGSENG": "HANG_SENG", "HANG_SENG": "HANG_SENG", "HSI": "HANG_SENG",
    "HK50": "HANG_SENG",
    "FR40": "FR40", "CAC": "FR40", "CAC40": "FR40", "FCHI": "FR40",
    "ES35": "ES35", "IBEX": "ES35", "IBEX35": "ES35",
    "ITA40": "ITA40", "FTSEMIB": "ITA40", "FTSE MIB": "ITA40",
    "MIB": "ITA40",
    "AU200": "AU200", "ASX": "AU200", "ASX200": "AU200",
}

# Crypto bases (BTC/USDT → "BTC") derived from the configured watchlist.
_ASSET_SYMBOL_MAP = dict(_MACRO_ASSET_MAP)
for _sym in config.SYMBOLS:
    _base = _sym.split("/")[0].upper()
    _ASSET_SYMBOL_MAP.setdefault(re.sub(r"[^A-Z0-9]", "", _base), _sym)


def _resolve_symbol(asset: str) -> str | None:
    """Map a model-returned asset name to a display symbol (or None)."""
    key = re.sub(r"[^A-Z0-9]", "", str(asset or "").upper())
    return _ASSET_SYMBOL_MAP.get(key)


def _parse_date(raw: str) -> datetime | None:
    """Parse a date string in ISO 8601 or RFC 2822 format.

    The news DB stores ISO dates directly, but ``database.load_news`` may
    return RFC 2822 strings (e.g. ``Mon, 17 Aug 2026 10:01:25 GMT``).
    Returns a timezone-aware UTC datetime, or None on failure.
    """
    if not raw:
        return None
    # Try ISO 8601 first (what the DB stores natively)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    # Fall back to RFC 2822 (from email/feed parsers)
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ----------------------------------------------------------------------
# Headline ingestion
# ----------------------------------------------------------------------
def fetch_headlines(conn, limit: int = 25) -> list[dict]:
    """Latest news stories from the DB, newest first, tagged with their
    stored sentiment so the model gets an independent prior.

    The bot trades the Exness macro universe on news, so stories that
    mention a TRADED instrument (gold, dollar, yen, oil, indices — matched
    against reviewer.COIN_HINTS, re-derived at read time so older rows are
    tagged too) are prioritized over the general feed. Without this the
    300+ RSS rows per fetch buried the macro stories outside the model's
    25-headline window and forex signals never fired.

    Freshness enforcement (2026-08-17): headlines older than
    NEWS_MAX_AGE_HOURS are rejected. If fewer than NEWS_MIN_FRESH_HEADLINES
    survive the filter, the list is returned short — the caller (signal_for)
    will return HOLD when there aren't enough fresh stories."""
    df = database.load_news(conn, limit=min(limit * 2, 80))
    rows = df.to_dict(orient="records")

    from reviewer import COIN_HINTS, _mentions  # lazy — avoids import cost
    traded = [s for s in config.TRADING_UNIVERSE if s in COIN_HINTS]

    def _is_traded(row):
        return int(any(_mentions(row, COIN_HINTS[s]) for s in traded))

    # ── Freshness filter: reject stale headlines ──────────────────────
    now = datetime.now(timezone.utc)
    max_age_hours = config.NEWS_MAX_AGE_HOURS
    fresh_rows = []
    for r in rows:
        pub = str(r.get("published_at") or "")
        pub_dt = _parse_date(pub)
        if pub_dt is not None:
            age_hours = (now - pub_dt).total_seconds() / 3600
            if age_hours <= max_age_hours:
                r["_age_hours"] = round(age_hours, 1)
                fresh_rows.append(r)
        else:
            # Can't parse date — include it (better to have than lose)
            r["_age_hours"] = None
            fresh_rows.append(r)
    rows = fresh_rows

    # ── Wire-copy dedup (2026-09-30): one story reprinted 2-3x from one
    # outlet used to stack the sentiment window and push directional
    # trades on single-source noise. Newest copy wins.
    if config.NEWS_NOISE_FILTER_ENABLED:
        import news_noise
        rows, _ndup = news_noise.dedupe_headlines(rows)

    # Traded-instrument stories first, then newest first; cap at `limit`.
    rows.sort(key=lambda r: (_is_traded(r), str(r.get("published_at") or "")),
              reverse=True)
    rows = rows[:limit]

    out = []
    for it in rows:
        tag = _LABEL_TAG.get(str(it.get("sentiment", "neutral")), "neutral")
        src = str(it.get("source", "") or "")
        age = it.get("_age_hours")
        age_tag = f" [{age}h ago]" if age is not None else ""
        out.append({
            "title": str(it.get("title", "") or ""),
            "tag": tag,
            "source": src,
            "published_at": str(it.get("published_at", "") or "")[:16],
            "age_hours": age,
            "title_with_age": str(it.get("title", "") or "") + age_tag,
            # 2026-10-12: the corroboration gate (news_noise.corroboration_stats)
            # reads r["score"] — without this the stored sentiment was invisible
            # to the gate, EVERY single-outlet headline scored 0.00 and the
            # "one clearly strong story" escape hatch could never fire. FX/oil
            # signals (WTI +70 conf 0.7!) died here while crypto kept trading.
            "sentiment_score": float(it.get("sentiment_score") or 0.0),
            "score": float(it.get("sentiment_score") or 0.0),
        })
    return out


# ----------------------------------------------------------------------
# Groq transport (retry + backoff + model fallback)
# ----------------------------------------------------------------------
def _parse_json(content: str) -> dict | None:
    """Parse model JSON, tolerating <think> reasoning blocks (Qwen), code
    fences and trailing commas. Returns dict or None."""
    if not content:
        return None
    text = content.strip()
    # Qwen-style reasoning block: strip everything between <think>...</think>.
    text = re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", text, flags=re.S)
    # Markdown fences.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    # Trailing commas (models are sloppy) — strip before the closing brace.
    try:
        text = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    # Prose-wrapped JSON ("Based on the headlines...\n```\n{...}\n```"):
    # pull the first {…} block out and try that.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (TypeError, ValueError):
            pass
    return None


def _chat_once(base_url: str, api_key: str, model: str, prompt: str,
               timeout: int = 60, system: str = None, temperature: float = 0.1,
               max_tokens: int = 1500) -> dict | None:
    """One chat-completions call to an OpenAI-compatible endpoint. Returns
    parsed JSON dict or None."""
    system = system or (
        "You are a cautious crypto market analyst. You score coins from "
        "headlines alone and NEVER invent news. Always answer with valid "
        "JSON only.")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(
        base_url + "/chat/completions",
        headers=headers,
        json={
            "model": model,
            "temperature": temperature,   # low = deterministic structured output
            "max_tokens": max_tokens,
            # Non-streaming: local gateways like OmniRoute stream SSE by
            # default, which would break our plain-JSON response parsing.
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=timeout,
    )
    if resp.status_code == 429 or resp.status_code >= 500:
        raise _RateLimited(resp)
    resp.raise_for_status()
    content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content", "")
    return _parse_json(content)


class _RateLimited(Exception):
    """A retryable provider response (429/5xx)."""

    def __init__(self, resp):
        super().__init__(f"groq {resp.status_code}")
        self.retry_after = resp.headers.get("Retry-After")


def _providers() -> list[dict]:
    """The OpenAI-compatible endpoints this transport can talk to, in order:

      1. Groq (primary) when GROQ_API_KEY is set — GROQ_MODEL (3 attempts)
         then GROQ_FALLBACK_MODEL.
      2. The keyed LLM_* provider (Gemini OpenAI-compat endpoint) — fast and
         verified against this prompt; it answers in ~5s when Groq is down.
      3. OmniRoute (config.OMNIROUTE_BASE_URL, keyless local gateway) when
         enabled — the `auto` combo (or a named one) with 2 attempts.
      4. Every configured LLM_FALLBACKS entry (OpenRouter, DeepSeek, … —
         same list the reviewer chain rides).

    Order was verified live 2026-08-18: the OmniRoute `auto` combo times out
    on the batched analysis prompt (~40s read timeout) and its free backends
    return prose instead of JSON, while the keyed Gemini endpoint returns
    parseable JSON in seconds — so Gemini outranks the local gateway.

    Never a hard dependency: `chat_json` returns None when every provider
    fails, and callers fall back to rule-based scoring."""
    out = []
    if config.GROQ_API_KEY:
        out.append({"base_url": config.GROQ_BASE_URL,
                    "api_key": config.GROQ_API_KEY,
                    "models": [config.GROQ_MODEL] * 3 + [config.GROQ_FALLBACK_MODEL],
                    "label": "groq"})
    if config.LLM_API_KEY and config.LLM_BASE_URL:
        out.append({"base_url": config.LLM_BASE_URL.rstrip("/"),
                    "api_key": config.LLM_API_KEY,
                    "models": [config.LLM_MODEL],
                    "label": "llm_primary"})
    if config.OMNIROUTE_ENABLED and config.OMNIROUTE_BASE_URL:
        out.append({"base_url": config.OMNIROUTE_BASE_URL,
                    "api_key": config.OMNIROUTE_API_KEY,
                    "models": [config.OMNIROUTE_MODEL] * 2,
                    "label": "omniroute"})
    for fb in config.LLM_FALLBACKS:
        base = (fb.get("base_url") or "").rstrip("/")
        if base and fb.get("model"):
            out.append({"base_url": base,
                        "api_key": fb.get("api_key", ""),
                        "models": [fb["model"]],
                        "label": "fallback"})
    return out


def chat_json(user_prompt: str, system: str = None, max_tokens: int = 1500,
              temperature: float = 0.1) -> dict | None:
    """One structured LLM call with retry/backoff across the provider chain.

    Shared by the news analyst and the AI agents (ai_agents.py). Tries
    Groq first (3 attempts on GROQ_MODEL, then GROQ_FALLBACK_MODEL once),
    then OmniRoute's configured combo (2 attempts) when the gateway is up.
    Returns parsed JSON dict or None.
    """
    attempt_no = 0
    for provider in _providers():
        prev_model = None
        skip_same = False
        for model in provider["models"]:
            # Skip duplicate retries of a model whose quota just died — a
            # long Retry-After (daily tokens-per-day limit) won't clear in
            # 30s, so hammering its 2 more copies only stalls the cycle.
            if skip_same and model == prev_model:
                continue
            attempt_no += 1
            try:
                data = _chat_once(provider["base_url"], provider["api_key"],
                                  model, user_prompt, system=system,
                                  temperature=temperature, max_tokens=max_tokens)
                if data is not None:
                    return data
            except _RateLimited as e:
                retry_after = float(e.retry_after or 0) or 0.0
                if retry_after >= 60.0:
                    # Quota exhausted for the period — retrying won't help
                    # now; give up on this model quickly and move to the
                    # next provider (measured: Groq TPD 429 with Retry-After
                    # 406 was sleeping 30s x 4 attempts = 2min per cycle).
                    time.sleep(1.0)
                    skip_same = True
                else:
                    # Transient rate limit: respect Retry-After but cap at
                    # 30s so a dead chain never stalls the 5-min bot cycle.
                    time.sleep(min(retry_after or 2.0, 30.0))
                    skip_same = False
            except requests.ConnectionError:
                # Dead endpoint (e.g. OmniRoute gateway not running) — fail
                # fast with no backoff: connection-refused won't recover in
                # seconds, and 2 attempts x up to 8s stalls the cycle.
                time.sleep(0.5)
                skip_same = False
            except Exception:
                # Cap exponential backoff at 8s for the same reason.
                time.sleep(min(1.0 * (2 ** min(attempt_no, 3)), 8.0))
                skip_same = False
            prev_model = model
    return None


def _groq_call(prompt: str) -> dict | None:
    """The news analyst's batched-analysis call (back-compat wrapper)."""
    # max_tokens=8000: the batched prompt covers 75+ assets with SL/TP
    # percentages — 4000 tokens truncated mid-JSON (finish=length, verified
    # 2026-08-18: 11k chars response cut off at char 11294). 8000 gives
    # enough room for the full JSON payload.
    return chat_json(prompt, system=_ANALYSIS_SYSTEM, max_tokens=8000)


# ----------------------------------------------------------------------
# Market analysis (structured, per-asset)
# ----------------------------------------------------------------------
_ANALYSIS_SYSTEM = (
    "Analyze the latest market headlines below, each tagged with a rough "
    "sentiment ([bullish]/[bearish]/[notable]/[neutral]). Return a JSON "
    "object of the form: "
    '{"assets": [{"asset": "BTC", "sentiment_score": 0.6, "confidence": 0.8, '
    '"impact_horizon": "short_term", "summary": "one short sentence", '
    '"headlines": ["title 1"], "stop_loss_pct": 1.5, "take_profit_pct": 3.0}]}. '
    "Rules: sentiment_score is -1.0 (very bearish) to +1.0 (very bullish); "
    "confidence 0..1 reflects how sure you are the signal is real; "
    "impact_horizon is short_term | medium_term | long_term. "
    "stop_loss_pct: suggested stop loss distance as % from entry (e.g., 1.5 means 1.5% below entry for LONG, above for SHORT); "
    "take_profit_pct: suggested take profit distance as % from entry (e.g., 3.0 means 3% above entry for LONG, below for SHORT); "
    "Set SL/TP based on volatility, support/resistance, and risk/reward ratio (aim for 1:2 or better). "
    "For high volatility assets (crypto, JPY pairs), use wider SL (2-3%). For low volatility (EURCHF, USDCNY), use tighter SL (0.5-1%). "
    "The asset universe: crypto coins (BTC, ETH, SOL, ...), forex majors "
    "(EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, USDCNY), forex "
    "crosses and minors (EURGBP, EURJPY, GBPJPY, AUDJPY, NZDUSD, EURCHF, "
    "USDSGD, USDMXN, EURAUD, EURCAD, EURNZD, GBPAUD, GBPCAD, GBPNZD, "
    "GBPCHF, AUDCAD, AUDNZD, NZDCHF, CADJPY, CHFJPY, NZDJPY, USDZAR, "
    "USDTRY, USDSEK, USDNOK, USDPLN), the dollar index (DXY), metals "
    "(XAU, XAG, XPT, XPD), energy (WTI, BRENT, NGAS) and equity indices "
    "(US500, US100, US30, DAX, FTSE, EU50, NIKKEI, HK50, FR40, ES35, "
    "ITA40, AU200). Use these tickers exactly. "
    "DIRECTION CONVENTIONS — critical for inverse pairs: in USDJPY, USDCHF, "
    "USDCAD and USDCNY the BASE is USD, so a story about the SECOND currency "
    "(JPY/CHF/CAD/CNY) strengthening means the PAIR FALLS (negative score = "
    "SHORT), and the second currency weakening means the pair rises. A "
    "'yen strengthens' story is therefore BEARISH for USDJPY. Gold and oil "
    "rise when their story is bullish; equity indices follow their story. "
    "Include ALL assets from the universe above in your response, even if "
    "the signal is weak or neutral — set confidence low (0.1-0.3) for assets "
    "with minimal coverage. This ensures every trading symbol gets analyzed. "
    "Reply with ONLY valid JSON."
)


def analyze_headlines(headlines: list[dict]) -> dict | None:
    """One batched Groq analysis of the given headlines.

    Returns {"assets": [{asset, sentiment_score, confidence, impact_horizon,
    summary, headlines}], "model", "generated_at"} or None on failure.
    """
    if not headlines:
        return None
    lines = "\n".join(
        f"{i + 1}. [{h['tag']}] {h['title']} ({h['source']})"
        for i, h in enumerate(headlines)
    )
    data = _groq_call(f"{_ANALYSIS_SYSTEM}\n\nHeadlines:\n{lines}")
    if not data:
        return None
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        return None
    cleaned = []
    for a in assets:
        if not isinstance(a, dict):
            continue
        asset = str(a.get("asset", "")).strip().upper()
        if not asset:
            continue
        try:
            score = float(a.get("sentiment_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        try:
            conf = float(a.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        try:
            sl_pct = float(a.get("stop_loss_pct", 0.0))
        except (TypeError, ValueError):
            sl_pct = 0.0
        try:
            tp_pct = float(a.get("take_profit_pct", 0.0))
        except (TypeError, ValueError):
            tp_pct = 0.0
        cleaned.append({
            "asset": asset,
            "sentiment_score": round(max(-1.0, min(1.0, score)), 3),
            "confidence": round(max(0.0, min(1.0, conf)), 3),
            "impact_horizon": str(a.get("impact_horizon", "short_term"))[:24],
            "summary": str(a.get("summary", ""))[:240],
            "headlines": [str(h)[:200] for h in (a.get("headlines") or [])][:4],
            "stop_loss_pct": round(max(0.0, min(10.0, sl_pct)), 2),
            "take_profit_pct": round(max(0.0, min(20.0, tp_pct)), 2),
        })
    if not cleaned:
        return None
    return {"assets": cleaned, "model": config.GROQ_MODEL,
            "generated_at": datetime.now(timezone.utc).isoformat()}


def analyze_market(conn, limit: int = 25, force: bool = False) -> dict | None:
    """Cached per-asset market analytics in the reviewer-compatible shape:

        {coins: [{symbol, score (-100..100), stance, reasons, stories,
                  confidence, impact_horizon}], stories, market, mode, model}

    Assets the model didn't score are back-filled from the rule-based scorer
    so every watched coin has a row. Returns None only when everything fails.
    """
    global _cooldown_until
    now = time.time()
    if not force and _market_cache["data"] is not None \
            and now - _market_cache["at"] < config.NEWS_ANALYST_CACHE_TTL:
        return _market_cache["data"]
    if now < _cooldown_until and _market_cache["data"] is None:
        return None

    try:
        headlines = fetch_headlines(conn, limit=limit)
        result = analyze_headlines(headlines)
        if result is None:
            # Silent provider failure (the retry chain returned nothing —
            # no exception). Back off here too, otherwise every symbol ×
            # every bot cycle re-burns the full ~30s retry chain and the
            # cycle crawls (16+ symbols × 30s = minutes) while the LLM
            # is down or quota-exhausted.
            _cooldown_until = now + COOLDOWN_SECONDS
            return None
        return _merge_with_rule_based(conn, result, len(headlines))
    except Exception:
        _cooldown_until = now + COOLDOWN_SECONDS
        return None


def _merge_with_rule_based(conn, groq: dict, stories: int) -> dict:
    """Merge Groq analytics with the rule-based scorer (which always covers
    every coin) and return the reviewer-compatible payload."""
    from reviewer import _rule_review  # lazy import — avoids circular import

    rule = _rule_review(conn, limit=60, hours=48.0)
    rule_by_sym = {c["symbol"]: c for c in (rule or {}).get("coins", [])}

    coins = []
    for a in groq.get("assets", []):
        symbol = _resolve_symbol(a.get("asset", ""))
        if not symbol:
            continue
        score = round(a["sentiment_score"] * 100)
        stance = "bullish" if score > 0 else "bearish" if score < 0 else "neutral"
        rule_row = rule_by_sym.get(symbol)
        reasons = [{"title": h, "sentiment": stance, "url": ""}
                   for h in a.get("headlines", [])]
        if not reasons and rule_row:
            reasons = rule_row.get("reasons") or []
        coins.append({
            "symbol": symbol, "score": int(score), "stance": stance,
            "reasons": reasons,
            "stories": int(rule_row.get("stories", 0)) if rule_row else len(a.get("headlines", [])),
            "confidence": a["confidence"],            # extra: model self-assessment
            "impact_horizon": a["impact_horizon"],    # extra: horizon
            "summary": a["summary"],
            # AI-suggested SL/TP percentages (pass-through for signal_for):
            "stop_loss_pct": a.get("stop_loss_pct", 0.0),
            "take_profit_pct": a.get("take_profit_pct", 0.0),
        })
        rule_by_sym.pop(symbol, None)

    # Back-fill every coin the model skipped with the rule-based view.
    for symbol, rc in rule_by_sym.items():
        coins.append({
            "symbol": symbol, "score": rc["score"], "stance": rc["stance"],
            "reasons": rc.get("reasons") or [], "stories": rc.get("stories", 0),
            "confidence": None, "impact_horizon": None,
            "summary": None, "_rule_based": True,
        })

    avg = (sum(c["score"] for c in coins) / len(coins)) if coins else 0.0
    market = ("Bullish" if avg >= 12.0 else "Bearish" if avg <= -12.0 else "Neutral")
    payload = {"coins": coins, "stories": stories, "window_hours": None,
               "market": market, "mode": "groq", "model": groq["model"],
               "generated_at": groq["generated_at"]}
    _market_cache.update({"at": time.time(), "data": payload})
    return payload


def analytics_for(conn, symbol: str) -> dict | None:
    """Structured analytics for ONE symbol, or None when the analyst is off
    / no coverage. Used by the bot's news gate when the LLM is the main
    engine."""
    if not (config.NEWS_ANALYST_ENABLED and config.llm_available()):
        return None
    review = analyze_market(conn)
    if not review:
        return None
    row = next((c for c in review["coins"] if c["symbol"] == symbol), None)
    return row


# ----------------------------------------------------------------------
# News-driven trade signals (decision logic on the analytics)
# ----------------------------------------------------------------------
def signal_for(conn, symbol: str) -> dict:
    """A trade signal from news + multi-factor confirmation.

    Pipeline (2026-08-17 upgrade):
        1. Raw sentiment from news analyst (Groq LLM)
        2. News freshness check (reject if too few fresh headlines)
        3. Calendar guard (block around NFP/FOMC/CPI events)
        4. AI confirmation layer (regime + technical + session)
        5. DXY master filter (dollar index confirmation)
        6. Smart money confluence (OB/FVG/BOS/CHoCH alignment)

    Returns:
        {action, confidence, score, reason, horizon,
         regime, confirm_score, dxy_score, smc_score, session,
         confirm_details, block_reasons}
    Never raises.
    """
    # ── Step 1: Raw sentiment ─────────────────────────────────────────
    row = analytics_for(conn, symbol)
    if row is None:
        return {"action": "HOLD", "confidence": 0.0, "score": 0.0,
                "reason": "news analyst unavailable or no coverage",
                "horizon": None, "regime": None, "confirm_score": 0,
                "dxy_score": 0, "smc_score": 0, "session": None,
                "confirm_details": {}, "block_reasons": ["no_analyst"]}
    sentiment = (row.get("score") or 0) / 100.0        # -1..+1
    conf = float(row.get("confidence") or 0.0)
    if row.get("_rule_based") or row.get("confidence") is None:
        # Rule-based back-fill is DISPLAY-ONLY — never a trade signal.
        return {"action": "HOLD", "confidence": 0.0,
                "score": round(sentiment, 3),
                "reason": ("rule-based backfill is display-only — no model "
                           "confidence, not a trade signal"),
                "horizon": row.get("impact_horizon"),
                "regime": None, "confirm_score": 0, "dxy_score": 0,
                "smc_score": 0, "session": None,
                "confirm_details": {}, "block_reasons": ["rule_based_only"]}

    # Determine raw direction from sentiment
    if sentiment >= config.AI_SENTIMENT_LONG and conf >= config.AI_CONFIDENCE_FLOOR:
        direction = "LONG"
    elif sentiment <= config.AI_SENTIMENT_SHORT and conf >= config.AI_CONFIDENCE_FLOOR:
        direction = "SHORT"
    else:
        return {"action": "HOLD", "confidence": round(conf, 3),
                "score": round(sentiment, 3),
                "reason": f"sentiment {sentiment:+.2f} below threshold (conf {conf:.2f})",
                "horizon": row.get("impact_horizon"),
                "regime": None, "confirm_score": 0, "dxy_score": 0,
                "smc_score": 0, "session": None,
                "confirm_details": {}, "block_reasons": ["below_threshold"]}

    block_reasons = []

    # ── Step 1.5: news noise gate (2026-09-30) ────────────────────────
    # Distinct outlets must corroborate the story before it risks money —
    # a lone weak headline (or wire copies of one) is exactly the noise
    # that produced the low-quality, loss-heavy entries.
    news_stats = None
    if config.NEWS_NOISE_FILTER_ENABLED:
        import news_noise
        from reviewer import COIN_HINTS as _HINTS
        hints = _HINTS.get(symbol)
        if hints:
            try:
                # 2026-10-12: the 15-headline window made the multi-outlet
                # gate nearly unpassable for macro assets — the top-15 was
                # mostly crypto so an oil story saw 1 outlet and every
                # WTI/BRENT/+70-conf signal died on "single-outlet noise".
                # The gate needs the WHOLE fresh pool (the same 80-row
                # window fetch_headlines reads for analysis) so two real
                # outlets within NEWS_MAX_AGE_HOURS can corroborate.
                _hh = fetch_headlines(conn, limit=80)
                news_stats = news_noise.corroboration_stats(_hh, hints)
                _ok, _why = news_noise.corroboration_ok(
                    news_stats, config.NEWS_MIN_DISTINCT_OUTLETS,
                    config.NEWS_SINGLE_OUTLET_MIN_SCORE)
                if not _ok:
                    block_reasons.append(_why)
                    return {"action": "HOLD", "confidence": round(conf, 3),
                            "score": round(sentiment, 3),
                            "reason": f"news noise gate: {_why}",
                            "horizon": row.get("impact_horizon"),
                            "regime": None, "confirm_score": 0,
                            "dxy_score": 0, "smc_score": 0, "session": None,
                            "confirm_details": {"news_corroboration": news_stats},
                            "block_reasons": block_reasons,
                            "news_corroboration": news_stats}
            except Exception:
                pass  # the noise filter must never break the signal path

    # ── Step 2: News freshness ────────────────────────────────────────
    headlines = fetch_headlines(conn, limit=10)
    fresh_count = sum(1 for h in headlines if h.get("age_hours") is not None)
    if fresh_count < config.NEWS_MIN_FRESH_HEADLINES:
        block_reasons.append(f"insufficient_fresh_news ({fresh_count} < {config.NEWS_MIN_FRESH_HEADLINES})")
        return {"action": "HOLD", "confidence": round(conf, 3),
                "score": round(sentiment, 3),
                "reason": f"only {fresh_count} fresh headlines (need {config.NEWS_MIN_FRESH_HEADLINES})",
                "horizon": row.get("impact_horizon"),
                "regime": None, "confirm_score": 0, "dxy_score": 0,
                "smc_score": 0, "session": None,
                "confirm_details": {}, "block_reasons": block_reasons}

    # ── Step 3: Calendar guard ────────────────────────────────────────
    import calendar_guard
    cal = calendar_guard.calendar_allows_trade(conn, symbol)
    if not cal["allowed"]:
        block_reasons.extend(cal["reasons"])
        return {"action": "HOLD", "confidence": round(conf, 3),
                "score": round(sentiment, 3),
                "reason": f"calendar blocked: {cal['reasons']}",
                "horizon": row.get("impact_horizon"),
                "regime": None, "confirm_score": 0, "dxy_score": 0,
                "smc_score": 0, "session": None,
                "confirm_details": {}, "block_reasons": block_reasons}

    # ── Step 4: AI confirmation layer (regime + technical + session) ──
    import ai_confirm
    confirm = ai_confirm.should_trade(conn, symbol, sentiment, conf, direction)
    if not confirm["pass"]:
        block_reasons.extend(confirm["reasons"])
        return {"action": "HOLD", "confidence": round(conf, 3),
                "score": round(sentiment, 3),
                "reason": f"confirmation blocked: {'; '.join(confirm['reasons'])}",
                "horizon": row.get("impact_horizon"),
                "regime": confirm["regime"],
                "confirm_score": confirm["score"],
                "dxy_score": 0, "smc_score": 0,
                "session": confirm["session"],
                "confirm_details": {}, "block_reasons": block_reasons}

    # ── Step 5: DXY master filter ─────────────────────────────────────
    import dxy_filter
    dxy_state = dxy_filter.get_dxy_state(conn)
    dxy_result = dxy_filter.dxy_allows_trade(symbol, direction, dxy_state)
    if not dxy_result["allowed"]:
        block_reasons.extend(dxy_result["reasons"])
        return {"action": "HOLD", "confidence": round(conf, 3),
                "score": round(sentiment, 3),
                "reason": f"DXY blocked: {'; '.join(dxy_result['reasons'])}",
                "horizon": row.get("impact_horizon"),
                "regime": confirm["regime"],
                "confirm_score": confirm["score"],
                "dxy_score": dxy_result["score"],
                "smc_score": 0,
                "session": confirm["session"],
                "confirm_details": {}, "block_reasons": block_reasons}

    # ── Step 6: Smart money confluence ────────────────────────────────
    import smart_money
    smc_result = smart_money.smart_money_confluence(conn, symbol, direction)
    smc_score = smc_result["score"]
    if config.SMC_ENABLED and smc_score < config.SMC_MIN_SCORE:
        block_reasons.append(f"smc_score {smc_score} < {config.SMC_MIN_SCORE}")
        return {"action": "HOLD", "confidence": round(conf, 3),
                "score": round(sentiment, 3),
                "reason": f"SMC blocked: score {smc_score} < {config.SMC_MIN_SCORE}",
                "horizon": row.get("impact_horizon"),
                "regime": confirm["regime"],
                "confirm_score": confirm["score"],
                "dxy_score": dxy_result["score"],
                "smc_score": smc_score,
                "session": confirm["session"],
                "confirm_details": smc_result.get("details", {}),
                "block_reasons": block_reasons}

    # ── All gates passed — signal is valid ────────────────────────────
    reason = row.get("summary") or f"news score {sentiment:+.2f} (conf {conf:.2f}) → {direction}"
    reason += f" | regime={confirm['regime']} tc={confirm['score']} dxy={dxy_result['score']} smc={smc_score}"
    if news_stats:
        reason += (f" | outlets={news_stats['distinct_outlets']}"
                   f" stories={news_stats['story_count']}"
                   f" mean={news_stats['mean_score']:+.2f}")
    
    # Get AI-suggested SL/TP percentages from analytics
    sl_pct = float(row.get("stop_loss_pct") or 0.0)
    tp_pct = float(row.get("take_profit_pct") or 0.0)
    
    return {"action": direction, "confidence": round(conf, 3),
            "score": round(sentiment, 3), "reason": reason,
            "horizon": row.get("impact_horizon"),
            "regime": confirm["regime"],
            "confirm_score": confirm["score"],
            "dxy_score": dxy_result["score"],
            "smc_score": smc_score,
            "session": confirm["session"],
            "session_mult": confirm["session_mult"],
            "confirm_details": smc_result.get("details", {}),
            "block_reasons": [],
            "stop_loss_pct": sl_pct,
            "take_profit_pct": tp_pct}
