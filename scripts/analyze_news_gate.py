"""
cryptoautomation — news-gate threshold replay (tuning support).

Replays every recorded news-gate decision (backend/news_verdicts table)
against candidate NEWS_VETO_SCORE / NEWS_BIG_MOVE_SCORE values and shows
what each setting WOULD have decided — so you can tune the confirmation
layer from real veto history instead of guessing.

Run:
    python scripts/analyze_news_gate.py [--days 7] [--veto 25,30,35] [--big 45,55,65]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config  # noqa: E402
import database  # noqa: E402
import reviewer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay news-gate decisions against candidate thresholds")
    ap.add_argument("--days", type=int, default=7, help="look back this many days (default 7)")
    ap.add_argument("--veto", default="25,30,35", help="candidate NEWS_VETO_SCORE values")
    ap.add_argument("--big", default="45,55,65", help="candidate NEWS_BIG_MOVE_SCORE values")
    args = ap.parse_args()

    conn = database.connect()
    cutoff = (__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
              - __import__("datetime").timedelta(days=args.days)).isoformat()
    rows = conn.execute(
        "SELECT symbol, direction, score, verdict FROM news_verdicts WHERE created_at >= ?",
        (cutoff,)).fetchall()

    if not rows:
        print(f"No news-gate decisions recorded in the last {args.days}d — nothing to replay yet.\n"
              "Decisions are journaled every bot cycle; tune after a few days of live data.")
        return 0

    # What the CURRENT config decided (what actually happened).
    cur = {"veto": 0, "confirm": 0, "neutral": 0}
    for r in rows:
        cur[r["verdict"]] = cur.get(r["verdict"], 0) + 1

    print(f"Recorded decisions (last {args.days}d): {len(rows)}")
    print(f"  actual with current config (veto={config.NEWS_VETO_SCORE:.0f}, "
          f"big={config.NEWS_BIG_MOVE_SCORE:.0f}): "
          f"veto={cur['veto']} confirm={cur['confirm']} neutral={cur['neutral']}\n")

    veto_opts = [float(v) for v in args.veto.split(",") if v.strip()]
    big_opts = [float(v) for v in args.big.split(",") if v.strip()]
    orig = (config.NEWS_VETO_SCORE, config.NEWS_BIG_MOVE_SCORE)
    print(f"{'VETO':>6} {'BIG':>6} | {'veto':>6} {'confirm':>6} {'neutral':>6}   sample vetoed symbols")
    for v in veto_opts:
        for b in big_opts:
            config.NEWS_VETO_SCORE, config.NEWS_BIG_MOVE_SCORE = v, b
            tally = {"veto": 0, "confirm": 0, "neutral": 0}
            samples = []
            for r in rows:
                d = reviewer.verdict_for(r["score"], r["direction"])
                tally[d["verdict"]] += 1
                if d["verdict"] == "veto" and len(samples) < 6:
                    samples.append(f"{r['symbol'].split('/')[0]} ({r['score']:+d})")
            print(f"{v:>6.0f} {b:>6.0f} | {tally['veto']:>6} {tally['confirm']:>6} "
                  f"{tally['neutral']:>6}   {', '.join(samples)}")
    config.NEWS_VETO_SCORE, config.NEWS_BIG_MOVE_SCORE = orig
    print("\nInterpretation: pick the row where vetoes match your risk appetite —\n"
          "too many vetoes = the news gate blocks trades you'd have taken; zero vetoes\n"
          "= thresholds too loose to matter. Confirm stays advisory (never blocks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
