"""Full system diagnostic - act as senior quant auditing the system."""
import sys
sys.path.insert(0, "backend")
import sqlite3
import requests
import time

print("=" * 70)
print("SYSTEM DIAGNOSTIC - SENIOR QUANT AUDIT")
print("=" * 70)

# 1. Check bot status
print("\n[1] BOT STATUS")
try:
    r = requests.get("http://localhost:8501/api/bot/status", timeout=10)
    d = r.json()
    lc = d.get("last_cycle") or {}
    print(f"  Last cycle: {lc.get('ran_at', 'never')}")
    print(f"  Elapsed: {lc.get('elapsed_s', '?')}s")
    held = lc.get("held", [])
    reasons = {}
    for h in held:
        reasons[h["reason"]] = reasons.get(h["reason"], 0) + 1
    print(f"  Held: {len(held)} symbols")
    print(f"  Reasons: {reasons}")
    print(f"  Executed: {lc.get('executed', [])}")
    print(f"  Blocked: {lc.get('blocked', False)}")
except Exception as e:
    print(f"  ERROR: {e}")

# 2. Check API keys
print("\n[2] API KEYS")
import config
keys = {
    "GROQ_API_KEY": config.GROQ_API_KEY,
    "TAVILY_API_KEY": config.TAVILY_API_KEY,
    "ALPHA_VANTAGE_API_KEY": config.ALPHA_VANTAGE_API_KEY,
    "FIRECRAWL_API_KEY": config.FIRECRAWL_API_KEY,
    "FINNHUB_API_KEY": config.FINNHUB_API_KEY if hasattr(config, "FINNHUB_API_KEY") else "",
    "FRED_API_KEY": config.FRED_API_KEY if hasattr(config, "FRED_API_KEY") else "",
    "COINGECKO_API_KEY": config.COINGECKO_API_KEY if hasattr(config, "COINGECKO_API_KEY") else "",
}
for k, v in keys.items():
    status = "SET" if v else "MISSING"
    print(f"  {k}: {status}")

# 3. Check database
print("\n[3] DATABASE")
conn = sqlite3.connect("data/crypto_data.db")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print(f"  Tables: {[t[0] for t in tables]}")
for tname in ["news", "candles", "macro"]:
    try:
        cnt = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()[0]
        print(f"  {tname}: {cnt} rows")
    except:
        pass

# 4. Check news freshness
print("\n[4] NEWS FRESHNESS")
from news_analyst import fetch_headlines
headlines = fetch_headlines(conn, limit=10)
fresh = sum(1 for h in headlines if h.get("age_hours") is not None)
print(f"  Fresh headlines: {fresh}/{len(headlines)}")
if headlines:
    for h in headlines[:3]:
        print(f"    [{h.get('age_hours','?')}h] {h.get('title','')[:60]}")

# 5. Check LLM availability
print("\n[5] LLM AVAILABILITY")
print(f"  NEWS_ANALYST_ENABLED: {config.NEWS_ANALYST_ENABLED}")
print(f"  llm_available(): {config.llm_available()}")
print(f"  GROQ_MODEL: {config.GROQ_MODEL}")

# 6. Test LLM call
print("\n[6] LLM TEST (simple)")
from news_analyst import chat_json
t0 = time.time()
result = chat_json('Return {"test": true}', system="You are a test assistant.")
elapsed = time.time() - t0
print(f"  Result: {result}")
print(f"  Time: {elapsed:.1f}s")

# 7. Check trading universe
print("\n[7] TRADING UNIVERSE")
print(f"  Total symbols: {len(config.TRADING_UNIVERSE)}")
print(f"  Forex: {len([s for s in config.TRADING_UNIVERSE if '/' in s and not any(x in s for x in ['USD','BTC','ETH'])])}")
print(f"  Crypto: {len([s for s in config.TRADING_UNIVERSE if 'USDT' in s])}")
print(f"  Metals/Energy: {len([s for s in config.TRADING_UNIVERSE if s in ['XAU/USD','XAG/USD','WTI/USD','BRENT/USD']])}")
print(f"  Indices: {len([s for s in config.TRADING_UNIVERSE if s in ['SPX/USD','NDX/USD','DOW/USD','DAX','NIKKEI','FTSE']])}")

# 8. Check calendar guard
print("\n[8] CALENDAR GUARD")
from calendar_guard import calendar_allows_trade
cal = calendar_allows_trade(conn, "EUR/USD")
print(f"  EUR/USD allowed: {cal['allowed']}")
print(f"  Reasons: {cal.get('reasons', [])}")

print("\n" + "=" * 70)
print("DIAGNOSTIC COMPLETE")
print("=" * 70)
