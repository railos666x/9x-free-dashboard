"""Test the analyze_headlines function specifically."""
import sys
sys.path.insert(0, "backend")
import time

# Test with actual headlines from DB
import sqlite3
conn = sqlite3.connect("data/crypto_data.db")

from news_analyst import fetch_headlines, analyze_headlines, _groq_call, _ANALYSIS_SYSTEM

print("=== Testing analyze_headlines ===")

# Get actual headlines
headlines = fetch_headlines(conn, limit=10)
print(f"Got {len(headlines)} headlines")
for h in headlines[:3]:
    print(f"  [{h.get('tag','?')}] {h.get('title','')[:60]}")

# Test analyze_headlines
print("\nCalling analyze_headlines...")
t0 = time.time()
result = analyze_headlines(headlines)
elapsed = time.time() - t0
print(f"Time: {elapsed:.1f}s")
print(f"Result: {result}")

if result is None:
    print("\n=== Debugging: Testing _groq_call with analysis prompt ===")
    lines = "\n".join(f"{i+1}. [{h['tag']}] {h['title']} ({h['source']})" for i, h in enumerate(headlines))
    prompt = f"{_ANALYSIS_SYSTEM}\n\nHeadlines:\n{lines}"
    print(f"Prompt length: {len(prompt)} chars")
    
    t0 = time.time()
    result2 = _groq_call(prompt)
    elapsed = time.time() - t0
    print(f"Time: {elapsed:.1f}s")
    print(f"Result: {result2}")
