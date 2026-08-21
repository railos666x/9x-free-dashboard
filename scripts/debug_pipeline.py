import sys
sys.path.insert(0, "backend")
from news_analyst import analyze_headlines, _ANALYSIS_SYSTEM, _groq_call

# Test with simple headlines
headlines = [
    {"title": "Fed holds rates steady at 5.5%", "tag": "neutral", "source": "Reuters"},
    {"title": "Dollar index rises on strong jobs data", "tag": "bullish", "source": "Bloomberg"},
    {"title": "EUR/USD falls below 1.08", "tag": "bearish", "source": "FT"},
]

print("=== Testing analyze_headlines ===")
result = analyze_headlines(headlines)
print(f"Result: {result}")

if result is None:
    print("\n=== Testing _groq_call with actual prompt ===")
    lines = "\n".join(f"{i+1}. [{h['tag']}] {h['title']} ({h['source']})" for i, h in enumerate(headlines))
    prompt = f"{_ANALYSIS_SYSTEM}\n\nHeadlines:\n{lines}"
    print(f"Prompt length: {len(prompt)} chars")
    print(f"First 500 chars: {prompt[:500]}...")
    
    result2 = _groq_call(prompt)
    print(f"\n_groq_call result: {result2}")
