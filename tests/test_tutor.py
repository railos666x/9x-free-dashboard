"""
cryptoautomation — tests for the trading tutor (tutor.py).

Pure module tests: topic catalogue completeness, explain() contract, and
the explain_trade / explain_last_cycle helpers (why a trade was taken or
skipped).
"""

import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't print ✅/❌ — switch to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import tutor  # noqa: E402

FAIL = 0
PASS = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def test_catalogue():
    print("1. Topic catalogue")
    topics = tutor.list_topics()
    ok("≥ 10 topics", len(topics) >= 10, str(len(topics)))
    cats = {t["category"] for t in topics}
    ok("both basic and advanced present", cats >= {"basic", "advanced"}, str(cats))
    for t in topics:
        ok(f"topic '{t['topic']}' has title+summary", t["title"] and t["explanation"])
    # The user's requested curriculum is covered.
    names = {t["topic"] for t in topics}
    required = {"market_order", "limit_order", "stop_limit", "bid_ask_spread",
                "leverage", "margin", "risk_reward", "slippage", "atr_volatility",
                "liquidity_traps", "order_book_imbalance", "multi_timeframe"}
    ok("all required curriculum topics covered", required <= names,
       str(required - names))


def test_explain():
    print("2. explain() contract")
    e = tutor.explain("leverage")
    ok("known topic returns dict", isinstance(e, dict) and e["topic"] == "leverage")
    ok("explanation has example + why_it_matters",
       e and e.get("example") and e.get("why_it_matters"))
    ok("unknown topic -> None", tutor.explain("nonsense") is None)
    ok("case-insensitive lookup", tutor.explain("Market_Order") is not None)


def test_explain_trade():
    print("3. explain_trade — why a trade was taken / skipped")
    order = {"symbol": "BTC/USDT", "side": "LONG", "status": "filled",
             "reasons": "RSI(2)=17 → oversold, mean-reversion LONG; news confirms",
             "source": "alpaca"}
    r = tutor.explain_trade(order)
    ok("taken trade explained", r["ok"] and "BTC/USDT" in r["headline"])
    ok("reasons surfaced", "RSI(2)=17" in r["why"])

    held = {"symbol": "CELO/USDT", "reason": "no_short_on_spot",
            "detail": {"message": "SHORT on spot broker (long-only)"}}
    r = tutor.explain_trade(None, held)
    ok("held trade explained", r["ok"] and "held back" in r["headline"])
    ok("held reason named", "no_short_on_spot" in r["why"])

    ok("neither -> not ok", not tutor.explain_trade(None)["ok"])


def test_explain_last_cycle():
    print("4. explain_last_cycle — cycle summary")
    summary = {"executed": [{"symbol": "ADA/USDT"}],
               "held": [{"symbol": "X", "reason": "not_on_broker"},
                        {"symbol": "Y", "reason": "not_on_broker"},
                        {"symbol": "Z", "reason": "news_veto"}],
               "errors": []}
    r = tutor.explain_last_cycle(summary)
    ok("cycle summarised", r["ok"] and "1 traded" in r["headline"])
    ok("held reasons tallied", any(h["reason"] == "not_on_broker" and h["count"] == 2
                                   for h in r["held_top"]))
    ok("empty summary handled", not tutor.explain_last_cycle(None)["ok"])


def main():
    test_catalogue()
    test_explain()
    test_explain_trade()
    test_explain_last_cycle()
    print(f"\n{'=' * 50}\n{'✅ ALL TESTS PASSED' if FAIL == 0 else f'❌ {FAIL} FAILED'}"
          f" — tutor ({PASS} checks)")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
