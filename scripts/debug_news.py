"""Debug the news analysis pipeline."""
import sys
sys.path.insert(0, "backend")
import sqlite3
import time

conn = sqlite3.connect("data/crypto_data.db")

# Check headlines
from news_analyst import fetch_headlines, analyze_headlines, chat_json, _groq_call

print("=== Debug News Analysis Pipeline ===\n")

# 1. Check headlines
headlines = fetch_headlines(conn, limit=10)
print(f"1. Headlines: {len(headlines)}")
fresh = sum(1 for h in headlines if h.get("age_hours") is not None)
print(f"   Fresh: {fresh}/{len(headlines)}")
for h in headlines[:3]:
    print(f"   [{h.get('age_hours','?')}h] {h.get('title','')[:50]}")

# 2. Test simple LLM call
print("\n2. Testing simple LLM call...")
t0 = time.time()
result = chat_json('Return {"test": true}', system="You are a test assistant.")
elapsed = time.time() - t0
print(f"   Result: {result}")
print(f"   Time: {elapsed:.1f}s")

# 3. Test analyze_headlines
print("\n3. Testing analyze_headlines...")
t0 = time.time()
result = analyze_headlines(headlines)
elapsed = time.time() - t0
print(f"   Result: {result}")
print(f"   Time: {elapsed:.1f}s")

# 4. Check if Groq is working
print("\n4. Testing _groq_call directly...")
t0 = time.time()
result = _groq_call('Return {"status": "ok"}')
elapsed = time.time() - t0
print(f"   Result: {result}")
print(f"   Time: {elapsed:.1f}s")
