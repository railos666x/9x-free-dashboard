"""
cryptoautomation — one-command setup for the FREE AI news-review chain.

Wires three free, OpenAI-compatible providers into .env (see
documentation/tool-evaluation-free-ai-trading-tools.md and .env.example):

    Primary : Gemini Flash   — free key from https://aistudio.google.com/apikey
    Fallback: OpenRouter     — free key from https://openrouter.ai/keys
    Fallback: Groq           — free key from https://console.groq.com/keys

All free tiers, no credit card. The keys are written to .env only (never
printed back), and the reviewer falls back to rule-based scoring until at
least the Gemini key is present.

Usage:
    python scripts/setup_free_llm.py
    # or pass keys as args to skip the prompts:
    python scripts/setup_free_llm.py --gemini AIza... --openrouter sk-or-... --groq gsk_...
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_URL = "https://openrouter.ai/api/v1"
GROQ_URL = "https://api.groq.com/openai/v1"


def _read_env() -> dict:
    if not ENV.exists():
        return {}
    out = {}
    for ln in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_env(updates: dict) -> None:
    """Update KEY=VALUE lines in .env, preserving every other line."""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    keys = set(updates)
    out, seen = [], set()
    for ln in lines:
        k = ln.split("=", 1)[0].strip() if "=" in ln and not ln.strip().startswith("#") else None
        if k in keys:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(ln)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def _mask(key: str) -> str:
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "(missing)"


def main() -> None:
    ap = argparse.ArgumentParser(description="Wire free LLM keys into .env")
    ap.add_argument("--gemini", help="Gemini API key (AIza...), from aistudio.google.com/apikey")
    ap.add_argument("--openrouter", help="OpenRouter key (sk-or-...), from openrouter.ai/keys")
    ap.add_argument("--groq", help="Groq key (gsk_...), from console.groq.com/keys")
    args = ap.parse_args()

    gemini = (args.gemini or "").strip()
    openrouter = (args.openrouter or "").strip()
    groq = (args.groq or "").strip()
    any_arg = bool(args.gemini or args.openrouter or args.groq)

    # Interactive prompts only when run with no arguments at all.
    if not any_arg:
        gemini = input("Gemini key (free, https://aistudio.google.com/apikey) [Enter to skip]: ").strip()
        openrouter = input("OpenRouter key (free, https://openrouter.ai/keys) [Enter to skip]: ").strip()
        groq = input("Groq key (free, https://console.groq.com/keys) [Enter to skip]: ").strip()

    if not any([gemini, openrouter, groq]):
        print("No keys provided — nothing changed. The reviewer stays rule-based.")
        return

    updates = {
        "LLM_ENABLED": "true",
        "LLM_BASE_URL": GEMINI_URL if gemini else "",      # primary = Gemini Flash
        "LLM_MODEL": "gemini-3.1-flash-lite" if gemini else "",  # current free-tier Flash
        "LLM_API_KEY": gemini,
        "LLM_MIN_INTERVAL_SECONDS": "600",                 # free-tier throttle (10 min)
        # Fallbacks: OpenRouter then Groq, only the ones with keys.
        "LLM_FALLBACKS": ";".join(
            e for e in [
                f"{OPENROUTER_URL}|deepseek/deepseek-r1:free|{openrouter}" if openrouter else "",
                f"{GROQ_URL}|llama-3.3-70b-versatile|{groq}" if groq else "",
            ] if e
        ),
    }
    _write_env(updates)

    print("\nWritten to .env:")
    print(f"  LLM_ENABLED=true")
    print(f"  Primary   : Gemini Flash (gemini-2.5-flash)  key {_mask(gemini) if gemini else '(skipped)'}")
    if openrouter:
        print(f"  Fallback 1: OpenRouter deepseek/deepseek-r1:free  key {_mask(openrouter)}")
    if groq:
        print(f"  Fallback 2: Groq llama-3.3-70b-versatile  key {_mask(groq)}")
    print("Restart the server (python backend/server.py) — the news reviewer will")
    print("then use this chain, throttled to once per 10 minutes (free-tier safe).")
    print("\nNeed keys? All free, no card:")
    print("  Gemini     -> https://aistudio.google.com/apikey")
    print("  OpenRouter -> https://openrouter.ai/keys")
    print("  Groq       -> https://console.groq.com/keys")


if __name__ == "__main__":
    sys.exit(main())
