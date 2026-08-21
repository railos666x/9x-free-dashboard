# 04 — News Sentiment & Analytics Pipeline

Three sources, three scoring layers, one pipeline:

```
RSS feeds (23) + CryptoPanic (optional) + Alpha Vantage News & Sentiment (optional)
   → SQLite `news` table (title, source, published_at, sentiment, sentiment_score, symbols)
   → ① rule-based scorer (always on)
   → ② Groq news analyst (main engine when GROQ_API_KEY is set)
   → review_market / news gate / news-driven signals
```

## ⓪ Alpha Vantage News & Sentiment (`news.py` — optional structured source)

One `NEWS_SENTIMENT` call per `ALPHA_NEWS_FETCH_HOURS` (6 h default → ≤4
calls/day on the free tier's 25/day budget) returns the top crypto stories
with **real per-ticker sentiment scores + relevance** — no keyword
heuristics. The 5 configured majors (`ALPHA_NEWS_TICKERS`, default
BTC,ETH,SOL,BNB,DOGE) are requested because **free keys accept at most 5
symbols per request** (verified: 50+ → `Invalid inputs`).

Per feed item, the row's score comes from the **most-relevant matched
crypto ticker** (`relevance_score` weighted — a Solana story doesn't drag
BTC's score along), and `symbols` lists every matched ticker with
relevance ≥ 0.1 (drops the API's 0.04-noise BTC/ETH/SOL default listing):

```
label = API label (Bullish / Somewhat-Bullish / Neutral / ...)
      → else score scale:  x ≥ 0.15 bullish · x ≤ −0.15 bearish · else neutral
score = clamp(best_ticker.ticker_sentiment_score, −1, 1)
```

Note: on the free tier the crypto feed can lag by months (observed newest
items ~6 months old). The 12 h half-life decay below zeroes those scores
out, so stale items populate the feed but never move the gate.

## ① Rule-based scorer (`reviewer.py`)

## ① Rule-based scorer (`reviewer.py`)

CryptoPanic stories carry community votes; RSS stories get keyword
sentiment (`news.fallback_sentiment`) from `BULLISH_KEYWORDS` /
`BEARISH_KEYWORDS`:

```
bull_hits = Σ keyword matches in title (bullish list)
bear_hits = Σ keyword matches in title (bearish list)
score     = +min(bull_hits/3, 1)   if bull_hits > bear_hits
          = −min(bear_hits/3, 1)   if bear_hits > bull_hits
          = 0                      otherwise
```

Per-coin score with recency decay (12 h half-life):

```
w(story) = raw_score × 0.5^(age_hours / 12)
score(coin) = clamp( Σ w(story) × 100, −100, +100 )
```

Stance: bullish ≥ +12, bearish ≤ −12, else neutral.

## ② Groq news analyst (`news_analyst.py`) — the main engine

One batched call per cache window (10 min) to
`https://api.groq.com/openai/v1/chat/completions` with a fast,
high-reasoning model (default `qwen/qwen3.6-27b`, fallback
`llama-3.3-70b-versatile`). The model ingests the latest ~25 headlines
(tagged with the rule-based sentiment as a prior) and returns strict JSON:

```json
{
  "assets": [{
    "asset": "BTC",
    "sentiment_score": 0.6,        // -1.0 .. +1.0  (directional)
    "confidence": 0.8,             // 0.0 .. 1.0    (model self-assessment)
    "impact_horizon": "short_term",// short_term | medium_term | long_term
    "summary": "one short sentence",
    "headlines": ["driving title 1"]
  }]
}
```

**Normalization**: `score(dashboard) = round(sentiment_score × 100)` so the
rest of the system (news gate, signal cards) keeps its −100..+100 scale.
Assets the model skips are back-filled from the rule-based scorer, so every
watched coin still has a row (`_rule_based: true` marks the fallback).

**Robustness** (per the design rules): retry with exponential backoff on
429/5xx honoring `Retry-After`; a second model is tried before giving up; a
10-minute cooldown follows total failure; the module never raises — the
reviewer falls back to the Gemini chain and then the rule-based scorer.

## ③ The news trade gate (`reviewer.verify_trade`)

After the signal sources agree, the gate scores the trade (veto/confirm only,
never initiates):

| Condition | Verdict |
|---|---|
| `|score| ≥ NEWS_BIG_MOVE_SCORE (55)` — big move expected either way | **veto** (stand aside) |
| LONG and `score ≤ −NEWS_VETO_SCORE (30)` | **veto** |
| SHORT and `score ≥ +NEWS_VETO_SCORE (30)` | **veto** |
| LONG and `score ≥ NEWS_CONFIRM_SCORE (20)` (or SHORT ≤ −20) | **confirm** (journal reason) |
| otherwise | **neutral** (never blocks) |

Every decision is journaled to `news_verdicts` and shown as `NEWS ✓/✗/−`
badges. Thresholds are tuned with `scripts/analyze_news_gate.py` against
recorded history.

## ④ News-driven trade signals (`news_analyst.signal_for`)

News can also *initiate* (not just gate) when configured as source B
(`PROPRIETARY_MODE=news`). Quantitative thresholds on the analyst's output:

```
LONG   ⇔ sentiment_score ≥ +NEWS_SIGNAL_LONG (0.35)  AND confidence ≥ 0.6
SHORT  ⇔ sentiment_score ≤ −NEWS_SIGNAL_SHORT (0.35) AND confidence ≥ 0.6
HOLD   otherwise (inside the noise threshold)
```

Risk is applied downstream by the executor (ATR SL/TP, risk-% sizing,
`MAX_OPEN_POSITIONS` cap, duplicate/cooldown) — see 05.

## Privacy / keys

`GROQ_API_KEY` is read from `.env` only, never hardcoded or printed. Groq's
Cloudflare layer rejects default `urllib` requests (HTTP 403 code 1010) —
all calls go through the `requests` library, same as the rest of the system.
