# Institutional News & Data Sources - Research Notes

*Provided by user for future reference when upgrading news infrastructure*

## Paid Institutional News & Data Sources (The "Terminal" Level)

These platforms provide real-time, ultra-low latency news, deep market data, economic indicators, and advanced analytics. They are the backbone of institutional trading desks.

### Bloomberg Terminal
- **The industry standard** - comprehensive real-time news, market data across ALL asset classes
- Pricing, trading tools, and messaging (Instant Bloomberg - IB)
- Essential for many professional traders
- Cost: ~$24,000/year per terminal

### Refinitiv Eikon (formerly Thomson Reuters Eikon)
- **Main competitor to Bloomberg**
- Similar comprehensive real-time news, data, analytics, and messaging (Messenger)
- Widely used across financial industry
- Cost: ~$22,000/year per seat

### FactSet
- Focuses heavily on company data, financial modeling, portfolio analytics
- Offers news but strength is deep fundamental data integration
- Cost: ~$12,000-18,000/year

### Dow Jones Newswires
- Major provider of real-time financial news
- Often integrated into Bloomberg/Refinitiv
- Also available as standalone feed for algorithmic trading systems
- Cost: Varies by feed

### S&P Global Market Intelligence (Capital IQ)
- Deep fundamental data, research, and news
- Particularly useful for investment banking, private equity, fundamental analysis
- Cost: Enterprise pricing

---

## High-Frequency & Machine-Readable News (For Quants)

Quants need news that algorithms can process instantly (NLP). These services deliver structured data, sentiment scores, and metadata directly to trading models.

### RavenPack
- **Leading provider of news analytics**
- Digests news from thousands of sources
- Real-time sentiment analysis, event detection, relevance scoring
- Formatted specifically for quantitative models
- Machine-readable JSON/XML feeds
- Cost: Enterprise pricing

### Refinitiv Machine Readable News
- Real-time news feeds optimized for algorithmic consumption and NLP
- Structured format with sentiment, relevance, entity tags

### Bloomberg Event-Driven Trading (EDT) Feeds
- Structured feeds delivering economic data releases and corporate events
- Ultra-low latency for automated trading
- Pre-parsed, normalized data

### StockTwits/Twitter (Enterprise API)
- Accessing the "firehose" of social media data for sentiment analysis
- While platforms are free, enterprise-level high-volume API access is paid
- Useful for crypto/narrative tracking

---

## Free (or Freemium) News Sources

While institutional traders rely on paid terminals, they still monitor major free/freemium outlets for broader market narratives, opinion pieces, and general awareness.

### CNBC
- Widely watched on trading floors
- Breaking news, CEO/policymaker interviews, general market commentary
- Video + text

### Reuters.com / Bloomberg.com
- Public-facing websites
- Solid overview of major financial news
- **Caveat**: Delayed or less comprehensive than terminals, often have paywalls

### The Wall Street Journal (WSJ) / Financial Times (FT)
- Essential for in-depth analysis, investigative reporting, economic commentary
- Usually paid subscriptions (many firms provide corporate access)

### Yahoo Finance
- Popular aggregator for quick quotes, basic charts, general financial news
- Good for retail-level monitoring

### Seeking Alpha / Motley Fool
- Crowdsourced analysis, opinion pieces, specific stock deep-dives
- **Heavily skewed toward retail investors**
- Use with caution for signal generation

---

## Key Insight: Speed & Format Matter Most

> **The crucial difference isn't just the information itself, but the speed and format.**
> 
> A retail trader reads a headline on Yahoo Finance; a quant's algorithm reads a machine-formatted sentiment score from RavenPack **milliseconds before** the Yahoo article is even published.

### Implications for CryptoAutomation

| Current State | Target State |
|---------------|--------------|
| RSS + CryptoPanic (free, ~5 min delay) | Machine-readable feeds (sub-second) |
| Keyword sentiment (±1 score) | NLP sentiment (-1 to +1 with confidence) |
| Article-level only | Event-level (earnings, M&A, macro, on-chain) |
| No structured data | Entity-tagged, relevance-scored, timestamped |

### Future Upgrade Path

1. **Phase 1**: Add machine-readable feed (RavenPack crypto, or Kaiko/Amberdata news)
2. **Phase 2**: NLP pipeline for entity extraction + sentiment scoring
3. **Phase 3**: Event-driven architecture (webhooks for economic releases)
4. **Phase 4**: Social sentiment (Twitter/Reddit firehose via enterprise API)

### Current Crypto News Sources (Already Integrated)
- CryptoPanic API (free tier: 600 req/day)
- CoinDesk, Cointelegraph, The Block, Decrypt RSS
- Reuters, CNBC, Yahoo Crypto RSS
- Fear & Greed Index (Alternative.me)

### Recommended Crypto-Specific Paid Sources
- **Kaiko** - Institutional market data + news
- **Amberdata** - On-chain + market data + news
- **The Block Pro** - Research + data terminal
- **Messari Pro** - Crypto research + data
- **Glassnode** - On-chain metrics + alerts

---

## Integration Notes for CryptoAutomation

### Current Architecture (`news.py` + `reviewer.py`)
```
RSS Feeds → Parser → Keyword Sentiment → SQLite → reviewer.review_market()
CryptoPanic API → JSON → Normalize → SQLite → reviewer.review_market()
```

### Target Architecture
```
Machine Feed (WebSocket/REST) → NLP Pipeline → Structured Events
                                    ↓
                    ┌──────────────┴──────────────┐
                    ↓                             ↓
            Sentiment Engine              Event Classifier
            (per-coin scores)             (macro/micro/on-chain)
                    ↓                             ↓
                    └──────────────┬──────────────┘
                                   ↓
                        Bayesian News Overlay
                        (math_engine.py line 609-615)
```

### Budget Estimate
| Service | Annual Cost | Priority |
|---------|-------------|----------|
| RavenPack Crypto | ~$15-30k | High |
| Kaiko Market Data | ~$10-25k | High |
| Twitter Enterprise API | ~$5-15k | Medium |
| Glassnode/Amberdata | ~$5-15k | Medium |

---

*Document created: 2026-08-12*
*Source: User-provided research notes*