# Math System — Documentation

This folder documents every core mathematical model, formula and
quantitative indicator used (or planned) by the CryptoAutomation trading
system. It is the reference for how the math works — not how the UI looks.

## Contents

| File | Covers |
|---|---|
| [`01-indicators.md`](01-indicators.md) | Technical indicators: SMA, EMA, RSI, MACD, ATR, Bollinger Bands — formulas + how the system uses them |
| [`02-risk-and-performance.md`](02-risk-and-performance.md) | Risk scoring: position sizing, fractional Kelly, slippage/fee model, Sharpe, drawdown, Calmar, Wilson CI |
| [`03-signal-engine.md`](03-signal-engine.md) | The Bayesian fusion engine: priors, likelihoods, posterior, regime detection (ADX), confluence, EV filter |
| [`04-news-sentiment.md`](04-news-sentiment.md) | News pipeline: rule-based keyword scoring, the Groq analyst schema, normalization, the news trade gate + signal thresholds |
| [`05-execution.md`](05-execution.md) | Trade execution math: ATR-based SL/TP, position caps, cooldowns, broker reconciliation |
| [`06-architecture.md`](06-architecture.md) | End-to-end data flow + system architecture diagrams |
| [`07-ai-agents.md`](07-ai-agents.md) | The three production AI agents: news sentiment, HODL research, chart confluence + the opt-in gate |

## The pipeline in one sentence

```
Binance REST + RSS/CryptoPanic
   → SQLite (candles, news)
   → Source A (math engine: Bayesian fusion + factors)
   → Source B (proprietary RSI(2) or Groq news analyst)
   → agreement gate → data-quality gate → news gate
   → risk sizing (Kelly-capped, ATR stops) → execution (paper/Alpaca/Binance)
   → SL/TP monitor → journal → dashboard/backtester
```

## Key code locations

- `backend/indicators.py` — indicator formulas (01)
- `backend/risk.py`, `backend/backtester.py` — risk + performance math (02)
- `backend/math_engine.py`, `backend/factors.py` — signal fusion (03)
- `backend/news_analyst.py`, `backend/reviewer.py`, `backend/news.py` — news sentiment (04)
- `backend/execution.py`, `backend/verification.py` — execution + gates (05)

All formulas below match the code; where the code differs from textbook
formulas, the deviation is called out.
