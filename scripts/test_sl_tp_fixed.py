"""Test SL/TP with increased max_tokens."""
import sys
sys.path.insert(0, 'backend')

import config
import database
import news_analyst
import requests

conn = database.connect()
headlines = news_analyst.fetch_headlines(conn, limit=10)
print(f"Got {len(headlines)} headlines")

# Build the prompt
lines = "\n".join(
    f"{i + 1}. [{h['tag']}] {h['title']} ({h['source']})"
    for i, h in enumerate(headlines)
)
prompt = f"{news_analyst._ANALYSIS_SYSTEM}\n\nHeadlines:\n{lines}"

# Try Gemini with increased max_tokens
print("\n=== Testing Gemini with max_tokens=8000 ===")
base_url = config.LLM_BASE_URL
api_key = config.LLM_API_KEY
model = config.LLM_MODEL

headers = {"Content-Type": "application/json"}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"

resp = requests.post(
    base_url + "/chat/completions",
    headers=headers,
    json={
        "model": model,
        "temperature": 0.1,
        "max_tokens": 8000,
        "stream": False,
        "messages": [
            {"role": "system", "content": news_analyst._ANALYSIS_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    },
    timeout=120,
)

data = resp.json()
content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
finish = (data.get("choices") or [{}])[0].get("finish_reason")
print(f"Response length: {len(content)} chars")
print(f"Finish reason: {finish}")

parsed = news_analyst._parse_json(content)
if parsed:
    print(f"\nPARSE SUCCESS!")
    assets = parsed.get('assets', [])
    print(f"Assets: {len(assets)}")
    
    with_sl = [a for a in assets if a.get('stop_loss_pct') and a['stop_loss_pct'] > 0]
    print(f"Assets with SL/TP: {len(with_sl)}")
    
    print("\n=== Sample Assets ===")
    for a in sorted(assets, key=lambda x: abs(x.get('sentiment_score', 0)), reverse=True)[:10]:
        sym = a.get('asset', '?')
        score = a.get('sentiment_score', 0)
        conf = a.get('confidence', 0)
        sl = a.get('stop_loss_pct', 0)
        tp = a.get('take_profit_pct', 0)
        print(f"  {sym:10s} score={score:+.2f} conf={conf:.2f} SL={sl}% TP={tp}%")
else:
    print(f"\nPARSE FAILED")
    print(f"Last 200 chars: {content[-200:]}")
