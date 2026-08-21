# API Integration Plan — Free APIs for Trading System
## Prioritized by Impact & Implementation Effort

**Date:** August 18, 2026  
**Status:** Research Complete — Ready for Implementation

---

## Executive Summary

Analyzed 800+ free APIs from the public-apis repository. Identified **15 high-value APIs** that would significantly enhance the trading system's data quality, signal generation, and execution capabilities.

**Immediate Priorities (Week 1):**
1. **CoinGecko** — Free crypto market data (no API key required)
2. **Finnhub** — Real-time forex/commodities + WebSocket support
3. **MarketAux** — Stock market news with sentiment (already configured but underutilized)

**Medium Priority (Week 2-3):**
4. **EconDB** — Global macroeconomic data (no API key)
5. **Federal Reserve (FRED)** — Already integrated, expand usage
6. **Goldprice.dev** — Precious metals spot prices (no API key)

**Nice-to-Have (Month 2):**
7. **CryptoCompare** — Historical crypto data
8. **Twelve Data** — Alternative market data source
9. **NewsAPI** — Broader news coverage

---

## Category 1: Market Data (Critical)

### 🥇 **CoinGecko** — Crypto Market Data
- **URL:** https://www.coingecko.com/api
- **Auth:** None required (free tier)
- **Rate Limit:** 10-30 calls/min (free)
- **Data:** 
  - Real-time crypto prices (10,000+ coins)
  - Historical OHLCV (up to 5 years)
  - Market cap, volume, circulating supply
  - Developer/social metrics
- **Why:** 
  - **No API key required** — instant integration
  - Broader coverage than Binance (altcoins, DeFi tokens)
  - Backup data source for resilience
- **Integration Effort:** 2-3 hours
- **Priority:** ⭐⭐⭐⭐⭐ (Critical)

**Implementation:**
```python
# backend/coingecko_data.py
import requests

def fetch_coingecko_ohlcv(symbol, days=30):
    """Fetch OHLCV from CoinGecko (no API key needed)."""
    url = f"https://api.coingecko.com/api/v3/coins/{symbol}/ohlc"
    params = {"vs_currency": "usd", "days": days}
    resp = requests.get(url, params=params, timeout=15)
    return resp.json()  # [[timestamp, open, high, low, close], ...]
```

---

### 🥈 **Finnhub** — Forex, Commodities, Stocks
- **URL:** https://finnhub.io/docs/api
- **Auth:** API key (free tier: 60 calls/min)
- **Data:**
  - Real-time forex (EUR/USD, USD/JPY, etc.)
  - Commodities (oil, gold, silver)
  - US stocks/ETFs
  - WebSocket support for real-time streaming
  - Economic calendar
- **Why:**
  - **WebSocket support** — replaces REST polling
  - Covers forex/commodities (currently using Yahoo Finance)
  - Economic calendar for event-driven trading
- **Integration Effort:** 4-6 hours
- **Priority:** ⭐⭐⭐⭐⭐ (Critical)

**Implementation:**
```python
# backend/finnhub_data.py
import requests
import websocket

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")

def fetch_forex_quote(symbol):
    """Real-time forex quote from Finnhub."""
    url = f"https://finnhub.io/api/v1/quote"
    params = {"symbol": symbol, "token": FINNHUB_API_KEY}
    resp = requests.get(url, params=params, timeout=10)
    return resp.json()  # {c: current, h: high, l: low, o: open, ...}

def start_websocket(symbols):
    """Real-time streaming via WebSocket."""
    ws = websocket.WebSocketApp(
        f"wss://ws.finnhub.io?token={FINNHUB_API_KEY}",
        on_message=lambda ws, msg: handle_message(msg),
    )
    ws.on_open = lambda ws: ws.send(json.dumps({"type": "subscribe", "symbol": symbols}))
    ws.run_forever()
```

---

### 🥉 **Goldprice.dev** — Precious Metals
- **URL:** https://goldprice.dev/docs
- **Auth:** None required
- **Data:**
  - Gold, silver, copper spot prices
  - Futures data
  - 30-year history
  - 13 currencies supported
- **Why:**
  - **No API key** — instant integration
  - Specialized for metals (better than Yahoo Finance)
  - Cross-validated data
- **Integration Effort:** 1-2 hours
- **Priority:** ⭐⭐⭐⭐ (High)

---

### **Twelve Data** — Alternative Market Data
- **URL:** https://twelvedata.com/
- **Auth:** API key (free tier: 800 calls/day)
- **Data:**
  - Real-time & historical data
  - Forex, stocks, crypto, ETFs
  - Technical indicators (built-in)
- **Why:**
  - Backup data source
  - Built-in indicators (RSI, MACD, etc.)
  - Good documentation
- **Integration Effort:** 3-4 hours
- **Priority:** ⭐⭐⭐ (Medium)

---

## Category 2: Economic Data (High Value)

### 🥇 **EconDB** — Global Macroeconomic Data
- **URL:** https://www.econdb.com/api/
- **Auth:** None required
- **Data:**
  - GDP, CPI, unemployment rates
  - Interest rates (100+ countries)
  - Trade balances
  - Historical data (50+ years)
- **Why:**
  - **No API key** — instant integration
  - Broader than FRED (global coverage)
  - Essential for macro-driven strategies
- **Integration Effort:** 2-3 hours
- **Priority:** ⭐⭐⭐⭐⭐ (Critical)

**Implementation:**
```python
# backend/econdb_data.py
import requests

def fetch_economic_indicator(country, indicator):
    """Fetch economic data from EconDB (no API key)."""
    url = f"https://www.econdb.com/api/indicators/{indicator}/"
    params = {"country": country, "format": "json"}
    resp = requests.get(url, params=params, timeout=15)
    return resp.json()
```

---

### 🥈 **FRED (Federal Reserve)** — Already Integrated
- **Status:** ✅ Already configured (FRED_API_KEY in .env)
- **Enhancement:** Expand usage to include:
  - Yield curve data (TIPS, spreads)
  - Monetary base (M1, M2)
  - Bank reserves
- **Integration Effort:** 1-2 hours
- **Priority:** ⭐⭐⭐⭐ (High)

---

### **CongressInvests** — Congressional Stock Trades
- **URL:** https://congressinvests.com
- **Auth:** API key (free tier available)
- **Data:**
  - Real-time U.S. congressional stock trade disclosures
  - Senate EFD and House Clerk data
- **Why:**
  - Unique alternative data source
  - Congressional trades often outperform market
- **Integration Effort:** 2-3 hours
- **Priority:** ⭐⭐⭐ (Medium)

---

## Category 3: News & Sentiment (Enhanced)

### 🥇 **MarketAux** — Already Configured
- **Status:** ✅ Already configured (MARKETAUX_API_KEY in .env)
- **Enhancement:** 
  - Enable sentiment analysis (currently disabled)
  - Add tagged tickers for better signal correlation
- **Integration Effort:** 1 hour
- **Priority:** ⭐⭐⭐⭐ (High)

---

### 🥈 **NewsAPI** — Broader News Coverage
- **URL:** https://newsapi.org/
- **Auth:** API key (free tier: 100 calls/day)
- **Data:**
  - 80,000+ news sources
  - Headlines + full articles
  - Category filtering (business, technology, etc.)
- **Why:**
  - Broader than CryptoPanic (general news)
  - Good for macro sentiment analysis
- **Integration Effort:** 2-3 hours
- **Priority:** ⭐⭐⭐ (Medium)

---

### **Helium** — News with Bias Scoring
- **URL:** https://heliumtrades.com/mcp-page/
- **Auth:** None required
- **Data:**
  - News with media bias scoring
  - Balanced news synthesis
  - Live market data
  - AI options pricing
- **Why:**
  - **No API key** — instant integration
  - Bias scoring helps filter fake news
  - Unique synthesis feature
- **Integration Effort:** 2-3 hours
- **Priority:** ⭐⭐⭐ (Medium)

---

## Category 4: On-Chain & Alternative Data (Advanced)

### **CoinLobster** — Whale Trading Data
- **URL:** https://coinlobster.com/developers
- **Auth:** None required
- **Data:**
  - Live executed whale trades across 15 exchanges
  - On-chain DEX data
  - Unusualness radar
- **Why:**
  - **No API key** — instant integration
  - Whale tracking for signal confirmation
  - Unique data source
- **Integration Effort:** 3-4 hours
- **Priority:** ⭐⭐ (Nice-to-Have)

---

### **btcnode.uk** — Bitcoin Sentiment
- **URL:** https://btcnode.uk
- **Auth:** None required (x402 micropayments for paid endpoints)
- **Data:**
  - Bitcoin blockchain data
  - Fees, mempool
  - SEC insider trades
  - Reddit sentiment
- **Why:**
  - **No API key** for basic endpoints
  - Reddit sentiment for crypto
  - On-chain metrics
- **Integration Effort:** 2-3 hours
- **Priority:** ⭐⭐ (Nice-to-Have)

---

## Category 5: SEC & Regulatory Data (Compliance)

### **SEC EDGAR Data** — Company Filings
- **URL:** https://www.sec.gov/edgar/sec-api-documentation
- **Auth:** None required
- **Data:**
  - Annual reports (10-K, 10-Q)
  - Insider trades (Form 4)
  - Institutional holdings (13F)
- **Why:**
  - **No API key** — instant integration
  - Essential for fundamental analysis
  - Regulatory compliance
- **Integration Effort:** 3-4 hours
- **Priority:** ⭐⭐⭐ (Medium)

---

## Implementation Roadmap

### Week 1: Critical APIs (Days 1-7)
1. **Day 1-2:** CoinGecko integration (crypto backup)
2. **Day 3-4:** Finnhub integration (forex/commodities + WebSocket)
3. **Day 5:** Goldprice.dev (precious metals)
4. **Day 6-7:** EconDB (macroeconomic data)

### Week 2: Enhanced Data (Days 8-14)
1. **Day 8-9:** Expand FRED usage (yield curve, M2)
2. **Day 10-11:** MarketAux sentiment enhancement
3. **Day 12-14:** NewsAPI integration

### Week 3: Alternative Data (Days 15-21)
1. **Day 15-16:** Twelve Data (backup market data)
2. **Day 17-18:** CongressInvests (congressional trades)
3. **Day 19-21:** Helium (bias-scored news)

### Week 4: Advanced Features (Days 22-30)
1. **Day 22-24:** CoinLobster (whale tracking)
2. **Day 25-27:** SEC EDGAR (fundamental data)
3. **Day 28-30:** Testing & optimization

---

## API Key Requirements

### Already Configured ✅
- **FRED_API_KEY** — Federal Reserve data
- **MARKETAUX_API_KEY** — Stock market news
- **CRYPTOPANIC_API_KEY** — Crypto news
- **LLM_API_KEY** — AI/LLM services

### Need to Obtain 🔑
1. **Finnhub** — https://finnhub.io/register (free, instant)
2. **NewsAPI** — https://newsapi.org/register (free, instant)
3. **Twelve Data** — https://twelvedata.com/register (free, instant)
4. **CongressInvests** — https://congressinvests.com (free tier)

### No API Key Required 🆓
- **CoinGecko** — No auth needed
- **EconDB** — No auth needed
- **Goldprice.dev** — No auth needed
- **Helium** — No auth needed
- **CoinLobster** — No auth needed
- **SEC EDGAR** — No auth needed

---

## Cost Analysis

### Free Tier Limits
| API | Free Limit | Overage Cost |
|-----|-----------|--------------|
| CoinGecko | 30 calls/min | $0 (wait) |
| Finnhub | 60 calls/min | $0 (wait) |
| NewsAPI | 100 calls/day | $0 (wait) |
| Twelve Data | 800 calls/day | $0 (wait) |
| EconDB | Unlimited | $0 |

### Estimated Monthly Cost: **$0** (all free tiers sufficient)

---

## Risk Mitigation

### Data Source Redundancy
- **Crypto:** Binance (primary) + CoinGecko (backup)
- **Forex:** Yahoo Finance (primary) + Finnhub (backup)
- **News:** CryptoPanic (primary) + MarketAux + NewsAPI
- **Macro:** FRED (primary) + EconDB (backup)

### Rate Limit Handling
```python
def fetch_with_retry(url, params, max_retries=3):
    """Retry with exponential backoff on rate limit."""
    for i in range(max_retries):
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 429:  # Rate limited
            wait = 2 ** i  # Exponential backoff
            time.sleep(wait)
            continue
        return resp.json()
    raise Exception("Rate limit exceeded after retries")
```

---

## Success Metrics

### Data Quality
- **Coverage:** 87 → 200+ symbols
- **Freshness:** 5s polling → WebSocket real-time
- **Reliability:** Single source → 2-3 backups per category

### Signal Enhancement
- **New signals:** Whale tracking, congressional trades, bias-scored news
- **Improved accuracy:** Multi-source confirmation
- **Reduced latency:** WebSocket streaming

### System Resilience
- **Uptime:** 95% → 99.9% (redundant data sources)
- **Error rate:** 5% → <1% (fallback sources)
- **Latency:** 5s → <100ms (WebSocket)

---

## Next Steps

1. **Immediate (Today):**
   - Register for Finnhub API key
   - Register for NewsAPI key
   - Test CoinGecko integration (no key needed)

2. **This Week:**
   - Implement CoinGecko data fetcher
   - Implement Finnhub WebSocket connector
   - Add EconDB macroeconomic data

3. **Next Week:**
   - Integrate MarketAux sentiment
   - Add Goldprice.dev for metals
   - Test all new data sources

4. **Week 3-4:**
   - Implement alternative data (whale tracking, congressional trades)
   - Optimize rate limit handling
   - Document all integrations

---

**Prepared by:** Senior Quant System Architect  
**Classification:** Internal — Implementation Plan  
**Version:** 1.0 (August 18, 2026)
