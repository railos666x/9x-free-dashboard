"""Debug JSON parsing issue."""
import sys
sys.path.insert(0, 'backend')

import config
import database
import news_analyst
import requests
import json

conn = database.connect()
headlines = news_analyst.fetch_headlines(conn, limit=10)

# Build the prompt
lines = "\n".join(
    f"{i + 1}. [{h['tag']}] {h['title']} ({h['source']})"
    for i, h in enumerate(headlines)
)
prompt = f"{news_analyst._ANALYSIS_SYSTEM}\n\nHeadlines:\n{lines}"

# Try Gemini directly
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
        "max_tokens": 4000,
        "stream": False,
        "messages": [
            {"role": "system", "content": news_analyst._ANALYSIS_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    },
    timeout=90,
)

data = resp.json()
content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
print(f"Response length: {len(content)} chars")
print(f"Finish reason: {(data.get('choices') or [{}])[0].get('finish_reason')}")

# Show last 500 chars to see if truncated
print(f"\n=== Last 500 chars ===")
print(content[-500:])

# Try parsing step by step
print(f"\n=== Parsing attempts ===")
text = content.strip()
print(f"1. Stripped: {len(text)} chars, starts with '{text[0]}', ends with '{text[-1]}'")

# Try direct parse
try:
    parsed = json.loads(text)
    print(f"2. DIRECT PARSE SUCCESS! Keys: {list(parsed.keys())}")
except Exception as e:
    print(f"2. Direct parse failed: {e}")

# Try the _parse_json function
parsed = news_analyst._parse_json(content)
if parsed:
    print(f"3. _parse_json SUCCESS!")
else:
    print(f"3. _parse_json FAILED")
    
    # Try stripping think blocks
    import re
    text2 = re.sub(r"<\s*think\s*>.*?<\s*/\s*think\s*>", "", text, flags=re.S)
    print(f"4. After think strip: {len(text2)} chars")
    try:
        json.loads(text2)
        print(f"   PARSE SUCCESS after think strip!")
    except Exception as e:
        print(f"   Still fails: {e}")
    
    # Try stripping markdown
    text3 = re.sub(r"^```(?:json)?\s*|\s*```$", "", text2.strip(), flags=re.I)
    print(f"5. After markdown strip: {len(text3)} chars")
    try:
        json.loads(text3)
        print(f"   PARSE SUCCESS after markdown strip!")
    except Exception as e:
        print(f"   Still fails: {e}")
    
    # Try trailing comma strip
    text4 = re.sub(r",\s*([}\]])", r"\1", text3)
    print(f"6. After trailing comma strip: {len(text4)} chars")
    try:
        json.loads(text4)
        print(f"   PARSE SUCCESS after comma strip!")
    except Exception as e:
        print(f"   Still fails: {e}")
    
    # Try extracting first {...} block
    start = text4.find("{")
    end = text4.rfind("}")
    print(f"7. First {{ at {start}, last }} at {end}")
    if start != -1 and end > start:
        text5 = text4[start:end+1]
        print(f"   Extracted: {len(text5)} chars")
        try:
            json.loads(text5)
            print(f"   PARSE SUCCESS after extraction!")
        except Exception as e:
            print(f"   Still fails: {e}")
            # Show around the error position
            err_pos = int(str(e).split("char")[-1].split()[0]) if "char" in str(e) else -1
            if err_pos > 0:
                print(f"   Error at char {err_pos}: ...{text5[max(0,err_pos-50):err_pos+50]}...")
