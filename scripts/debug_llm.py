"""Debug LLM calls to see what's happening."""
import sys
sys.path.insert(0, 'backend')

import config
import database
import news_analyst

print("=== LLM Provider Config ===")
print(f"GROQ_API_KEY: {'SET' if config.GROQ_API_KEY else 'NOT SET'}")
print(f"GROQ_MODEL: {config.GROQ_MODEL}")
print(f"GROQ_FALLBACK_MODEL: {config.GROQ_FALLBACK_MODEL}")
print(f"LLM_API_KEY: {'SET' if config.LLM_API_KEY else 'NOT SET'}")
print(f"LLM_BASE_URL: {config.LLM_BASE_URL}")
print(f"LLM_MODEL: {config.LLM_MODEL}")
print(f"OMNIROUTE_ENABLED: {config.OMNIROUTE_ENABLED}")
print(f"OMNIROUTE_BASE_URL: {config.OMNIROUTE_BASE_URL}")
print()

providers = news_analyst._providers()
print(f"=== Active Providers ({len(providers)}) ===")
for p in providers:
    print(f"  {p['label']}: {p['base_url']} models={p['models']}")
print()

conn = database.connect()
print("Fetching headlines...")
headlines = news_analyst.fetch_headlines(conn, limit=10)
print(f"Got {len(headlines)} headlines")
for h in headlines[:3]:
    print(f"  [{h['tag']}] {h['title'][:60]}...")
print()

if not headlines:
    print("No headlines - can't test LLM")
    sys.exit(1)

# Build the prompt
lines = "\n".join(
    f"{i + 1}. [{h['tag']}] {h['title']} ({h['source']})"
    for i, h in enumerate(headlines)
)
prompt = f"{news_analyst._ANALYSIS_SYSTEM}\n\nHeadlines:\n{lines}"
print(f"Prompt length: {len(prompt)} chars")
print()

# Try each provider individually
print("=== Testing Each Provider ===")
import requests

for provider in providers:
    print(f"\n--- {provider['label']} ---")
    for model in provider['models'][:1]:  # Just first model
        print(f"  Model: {model}")
        try:
            result = news_analyst._chat_once(
                provider['base_url'],
                provider['api_key'],
                model,
                prompt,
                system=news_analyst._ANALYSIS_SYSTEM,
                timeout=90
            )
            if result:
                print(f"  SUCCESS!")
                assets = result.get('assets', [])
                print(f"  Assets returned: {len(assets)}")
                if assets:
                    a = assets[0]
                    print(f"  Sample: {a.get('asset')} sl_pct={a.get('stop_loss_pct', 'N/A')} tp_pct={a.get('take_profit_pct', 'N/A')}")
            else:
                print(f"  FAILED: returned None")
        except news_analyst._RateLimited as e:
            print(f"  RATE LIMITED: {e}")
        except requests.exceptions.Timeout:
            print(f"  TIMEOUT")
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
