"""Debug Gemini LLM response."""
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

# Try Gemini directly
print("\n=== Testing Gemini directly ===")
base_url = config.LLM_BASE_URL
api_key = config.LLM_API_KEY
model = config.LLM_MODEL

headers = {"Content-Type": "application/json"}
if api_key:
    headers["Authorization"] = f"Bearer {api_key}"

print(f"URL: {base_url}/chat/completions")
print(f"Model: {model}")
print(f"Prompt length: {len(prompt)} chars")

resp = requests.post(
    base_url + "/chat/completions",
    headers=headers,
    json={
        "model": model,
        "temperature": 0.1,
        "max_tokens": 4000,
        "stream": False,
        "messages": [
            {"role": "system", "content": news_analyst._ANALYSIS_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    },
    timeout=90,
)

print(f"Status: {resp.status_code}")
if resp.status_code != 200:
    print(f"Error: {resp.text[:1000]}")
else:
    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    print(f"Response length: {len(content)} chars")
    print(f"\n=== Raw Response (first 3000 chars) ===")
    print(content[:3000])
    print("\n=== Trying to parse ===")
    parsed = news_analyst._parse_json(content)
    if parsed:
        print(f"SUCCESS! Keys: {list(parsed.keys())}")
        assets = parsed.get('assets', [])
        print(f"Assets: {len(assets)}")
        if assets:
            a = assets[0]
            print(f"First asset keys: {list(a.keys())}")
            print(f"First asset: {a}")
    else:
        print("PARSE FAILED")
