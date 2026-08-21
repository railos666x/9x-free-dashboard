"""Check bot live/dry-run status."""
import requests
import json
r = requests.get("http://localhost:8501/api/bot/status", timeout=10)
d = r.json()
print("Full response:")
print(json.dumps(d, indent=2))
