#!/usr/bin/env python3
"""Daily trading digest — one command answers "is the machine healthy today?"

Appends a dated section to docs/MONITORING-LOG.md with:
  - AI-only win rate (7d and 30d) with net PnL and expectancy
  - Exit-reason economics (last 50 closed)
  - Open basket: floating P/L per leg (MT5 raw positions, Binance journal legs)
  - Guard status (daily-loss / drawdown / circuit-breaker level)
  - Cost totals (journal fees; slippage estimate where entry mid was recorded)
  - Per-venue weekly cost table (fees + slippage vs gross/net PnL, cost drag)
  - Run-window report (2026-10-06): win-rate trajectory + per-symbol table
    via backend/run_report.py — no manual scripts/run_report.py run needed

The server runs this once per UTC day (daily-digest daemon thread in
backend/server.py, with startup catch-up when today's section is missing);
a manual `python scripts/daily_digest.py` still works and exits
0 = healthy, 1 = guard tripped or anomaly detected.
"""
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import config  # noqa: E402
import database  # noqa: E402
import execution as ex  # noqa: E402
import forex as fx  # noqa: E402

LOG = ROOT / "docs" / "MONITORING-LOG.md"
DB = config.DB_PATH

AI_LIKE = "(reasons LIKE '%ai/news%' OR source LIKE '%ai%')"


def _fmt(x, spec="+.2f"):
    try:
        return format(float(x), spec)
    except (TypeError, ValueError):
        return "n/a"


def ai_only_stats(cur, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
    cur.execute(
        f"""SELECT pnl FROM orders
            WHERE status='closed' AND pnl IS NOT NULL
              AND fill_time >= ? AND {AI_LIKE}""",
        (since,),
    )
    pnls = [r[0] for r in cur.fetchall() if r[0] is not None]
    if not pnls:
        return None
    wins = [p for p in pnls if p > 0]
    net = sum(pnls)
    return {
        "n": len(pnls),
        "win_pct": 100.0 * len(wins) / len(pnls),
        "net": net,
        "expectancy": net / len(pnls),
    }


def exit_reason_table(cur):
    cur.execute(
        f"""SELECT COALESCE(exit_reason,'?'), COUNT(*),
                   SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), SUM(pnl)
            FROM orders
            WHERE status='closed' AND pnl IS NOT NULL
              AND {AI_LIKE}
            GROUP BY COALESCE(exit_reason,'?')
            ORDER BY SUM(pnl) DESC"""
    )
    return cur.fetchall()


def mt5_floating():
    try:
        import MetaTrader5 as mt5

        if not mt5.initialize():
            return None, "MT5 initialize failed"
        poss = mt5.positions_get() or []
        legs, total = [], 0.0
        for p in poss:
            pnl = float(p.profit) + float(p.swap)
            total += pnl
            legs.append((p.symbol, "buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
                         float(p.volume), pnl))
        mt5.shutdown()
        return legs, total
    except Exception as e:  # pragma: no cover - best-effort telemetry
        return None, f"{type(e).__name__}: {e}"


def binance_journal_legs(cur):
    """Software-managed Binance legs: journal rows vs live mark prices."""
    cur.execute(
        """SELECT id, symbol, side, qty, fill_price, take_profit, stop_loss
           FROM orders WHERE status='filled'"""
    )
    rows = cur.fetchall()
    out, total, skipped = [], 0.0, 0
    for oid, sym, side, qty, fill, tp, sl in rows:
        mark = None
        try:
            b = sym.replace("/", "")
            url = "https://testnet.binance.vision/api/v3/ticker/price?symbol=" + b
            with urllib.request.urlopen(url, timeout=8) as r:
                mark = float(json.loads(r.read())["price"])
        except Exception:
            pass
        if mark is None:
            # not a Binance-tradable symbol (e.g. MT5 FX rows journaled as filled)
            skipped += 1
            continue
        upl = (mark - fill) * qty if side == "LONG" else (fill - mark) * qty if fill else None
        if upl is not None:
            total += upl
        out.append((oid, sym, side, fill, mark, upl, tp, sl))
    return out, total, skipped


def guard_status(conn):
    try:
        g = ex.loss_guard_status(conn)
        return g
    except Exception as e:
        return {"blocked": None, "reason": f"guard check failed: {type(e).__name__}: {e}"}


def fees_total(cur):
    cur.execute(
        f"""SELECT COALESCE(SUM(fees),0) FROM orders
            WHERE status='closed' AND fill_time >= date('now','-30 day') AND {AI_LIKE}"""
    )
    return cur.fetchone()[0] or 0.0


def cost_table(cur, lines, problems):
    """Per-venue WEEKLY execution-cost table (2026-10-01).

    Journal fees + slippage estimates (orders.slippage_cost: measured
    |fill−ref| where a pre-order reference exists, else venue-tier bps ×
    notional) against gross/net PnL — the drag each venue's turnover
    actually pays. Gross = net + fees + slippage (costs added back).
    Flags a venue when round-trip costs eat > 25% of its gross PnL."""
    lines.append("\n### Costs by venue (7d, closed round trips)\n")
    lines.append("| Venue | n | Fees | Slip* | Total cost | Gross PnL | Net PnL | Cost drag |")
    lines.append("|---|---|---|---|---|---|---|---|")
    try:
        cur.execute(
            """SELECT COALESCE(source,'?'), COUNT(*),
                      COALESCE(SUM(fees),0), COALESCE(SUM(slippage_cost),0),
                      COALESCE(SUM(pnl),0)
               FROM orders
               WHERE status='closed' AND pnl IS NOT NULL
                 AND COALESCE(exit_time, fill_time) >= date('now','-7 day')
               GROUP BY COALESCE(source,'?')
               ORDER BY COUNT(*) DESC"""
        )
        rows = cur.fetchall()
    except Exception as e:
        lines.append(f"| error | | | | | | | {type(e).__name__} |")
        problems.append(f"cost table failed: {type(e).__name__}: {e}")
        return
    if not rows:
        lines.append("| (no closed trades this week) | | | | | | | |")
    any_row = False
    for venue, n, fees, slip, net in rows:
        any_row = True
        fees, slip, net = float(fees or 0), float(slip or 0), float(net or 0)
        cost = fees + slip
        gross = net + cost
        drag = (100.0 * cost / gross) if gross > 0 else None
        lines.append(
            f"| {venue} | {n} | {_fmt(fees, '.2f')} | {_fmt(slip, '.2f')} | "
            f"{_fmt(cost, '.2f')} | {_fmt(gross)} | {_fmt(net)} | "
            + (f"{drag:.1f}%" if drag is not None else "n/a") + " |"
        )
        if drag is not None and drag > 25:
            problems.append(
                f"cost drag: {venue} round-trip costs {_fmt(cost, '.2f')} "
                f"= {drag:.0f}% of gross {_fmt(gross)} this week")
    if any_row:
        lines.append("")
        lines.append("\\* slip = journaled slippage_cost — measured |fill − "
                     "ref| where a pre-order reference exists, else venue "
                     "bps × notional. MT5 fees are usually 0 (cost inside "
                     "the spread).")


def run_window_section(conn, lines, problems, base_id=None):
    """Run-window report folded into the digest (2026-10-06).

    The same numbers scripts/run_report.py prints — win-rate trajectory
    (all-time / last-10/20/50, rolling-10 buckets, path to the 70% target)
    and the per-symbol weekly table — rendered as markdown so they land
    every day without a manual script run. Flag an anomaly when the
    rolling last-20 win rate drops below 50% (the digest's other
    win-rate threshold) so a trajectory slide is never silent.
    """
    import run_stats as rr  # backend flat-import module (run-window math)

    base = rr.BASE_ID if base_id is None else base_id
    ws = rr.window_summary(conn, base_id=base)
    lines.append(f"\n### Run window (journal id > {base})\n")
    census = (", ".join(f"{k}: {v}" for k, v in sorted(ws["by_status"].items()))
              or "no rows")
    lines.append(f"- journal rows: {ws['rows']} ({census})")
    wst = rr.wr_stats(rr.closed_rows(conn, "id > ?", (base,)))
    if wst["n"]:
        lines.append(
            f"- window closed: n={wst['n']} wr={wst['wr']:.1f}% "
            f"net={_fmt(wst['net'])} PF={_fmt(wst['pf'])}")

    lines.append("\n### Win-rate trajectory (all journal closes)\n")
    lines.append("| Window | n | Win% | Net PnL | Avg win | Avg loss | PF |")
    lines.append("|---|---|---|---|---|---|---|")

    def _pf(pf):
        return "inf" if pf == float("inf") else _fmt(pf)

    traj = rr.trajectory(conn)
    for label, st in (("all-time", traj["all_time"]),
                      *sorted(traj["windows"].items(), reverse=True)):
        lines.append(
            f"| {label} | {st['n']} | {st['wr']:.1f}% | {_fmt(st['net'])} | "
            f"{_fmt(st['avg_win'])} | {_fmt(st['avg_loss'])} | {_pf(st['pf'])} |")
    seq = traj["buckets"]
    if len(seq) >= 2:
        lines.append("")
        lines.append(
            f"- rolling-10 buckets: {' '.join(f'{v:.0f}' for v in seq)} — "
            f"first-half {traj['first_half']:.1f}% → last-half "
            f"{traj['last_half']:.1f}% ({traj['trend']})")
    if "wr20" in traj:
        lines.append(
            f"- path to {rr.TARGET_WR:.0f}%: last-20 wr={traj['wr20']:.0f}% — "
            f"need {traj['need_wins']} more win(s) inside a rolling 20")
        if traj["wr20"] < 50:
            problems.append(
                f"trajectory: last-20 win rate {traj['wr20']:.0f}% < 50%")

    lines.append("\n### Per-symbol (7d, min 2 closed)\n")
    lines.append("| Symbol | n | Win% | Net PnL |")
    lines.append("|---|---|---|---|")
    rows = rr.per_symbol_table(conn)
    if not rows:
        lines.append("| (no symbol with ≥2 closes in 7d) | | | |")
    for r in rows:
        lines.append(f"| {r['symbol']} | {r['n']} | "
                     f"{100.0*r['w']/r['n']:.0f}% | {_fmt(r['net'])} |")


def fx_section(cur, lines, problems):
    """FX mastery snapshot: session, open exposure by currency, FX economics."""
    s = fx.session_info()
    lines.append("\n### FX (forex sleeve)\n")
    lines.append(
        f"- session: {s['label']} (liquidity {int(s['liquidity']*100)}%), "
        f"next: {s['next_session']} in {s['hours_to_next']}h"
        + (" — WEEKEND, FX closed" if s["weekend"] else "")
    )
    try:
        cur.execute("SELECT symbol, side FROM orders WHERE status IN ('filled','open')")
        # Works with any cursor: raw sqlite3 cursors yield tuples (no
        # row_factory here) — index positionally, Row cursors via keys.
        rows = [{"symbol": r[0], "side": r[1]} for r in cur.fetchall()]
        exposure = fx.fx_exposure(rows)
        if exposure["by_currency"]:
            tops = sorted(exposure["by_currency"].items(),
                          key=lambda kv: abs(kv[1]), reverse=True)[:5]
            lines.append("- open FX exposure: " + ", ".join(
                f"{c} {int(v):+d}" for c, v in tops))
            if exposure["concentration"] >= 3:
                problems.append(
                    f"FX concentration: net {exposure['top_currency']} "
                    f"{exposure['top_net']:+d} across open positions")
        else:
            lines.append("- open FX exposure: none")
    except Exception as e:
        lines.append(f"- open FX exposure: failed ({type(e).__name__})")

    fx_list = "','".join(fx.FX_PAIRS)
    cur.execute(
        f"""SELECT COUNT(*), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),
                   SUM(pnl)
            FROM orders
            WHERE status='closed' AND pnl IS NOT NULL
              AND fill_time >= date('now','-7 day')
              AND symbol IN ('{fx_list}')"""
    )
    n, w, pnl = cur.fetchone()
    if n:
        lines.append(f"- FX closed (7d): n={n} wins={w} net={_fmt(pnl)}")
        if n and (w or 0) / n < 0.45:
            problems.append(f"FX 7d win rate {100*(w or 0)/n:.0f}% < 45%")
    else:
        lines.append("- FX closed (7d): none")


def main():
    now = datetime.now(timezone.utc)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row   # init_db's PRAGMA reads need name access
    # Run the schema migrations (idempotent) so new columns like
    # slippage_cost exist even when the digest runs before the server.
    try:
        database.init_db(conn)
    except Exception:
        pass  # a failed migration must not kill the digest
    cur = conn.cursor()
    problems = []

    s7 = ai_only_stats(cur, 7)
    s30 = ai_only_stats(cur, 30)

    lines = []
    lines.append(f"\n---\n")
    lines.append(f"## Digest {now.strftime('%Y-%m-%d %H:%M UTC')}\n")
    lines.append("### AI-only closed-trade performance\n")
    lines.append("| Window | n | Win% | Net PnL | Expectancy |")
    lines.append("|---|---|---|---|---|")
    for label, s in (("7d", s7), ("30d", s30)):
        if s:
            lines.append(
                f"| {label} | {s['n']} | {s['win_pct']:.1f}% | {_fmt(s['net'])} | {_fmt(s['expectancy'])} |"
            )
            if s["win_pct"] < 50:
                problems.append(f"AI-only {label} win rate {s['win_pct']:.1f}% < 50%")
        else:
            lines.append(f"| {label} | 0 | - | - | - |")

    lines.append("\n### Exit-reason economics (all-time AI-only)\n")
    lines.append("| Exit | n | Wins | PnL |")
    lines.append("|---|---|---|---|")
    for reason, n, w, pnl in exit_reason_table(cur):
        lines.append(f"| {reason} | {n} | {w} | {_fmt(pnl)} |")

    lines.append("\n### Open basket — floating P/L\n")
    legs, mt5_total = mt5_floating()
    if legs is None:
        lines.append(f"- MT5: unavailable ({mt5_total})")
        problems.append("MT5 snapshot unavailable")
    else:
        lines.append(f"- MT5 ({len(legs)} legs): total {_fmt(mt5_total)}")
        for sym, side, vol, pnl in legs:
            lines.append(f"  - {sym} {side} vol={vol} {_fmt(pnl)}")
        if mt5_total < -50:
            problems.append(f"MT5 floating {_fmt(mt5_total)} below -50")

    bn_legs, bn_total, bn_skipped = binance_journal_legs(cur)
    if bn_legs:
        note = f" ({bn_skipped} non-Binance rows skipped)" if bn_skipped else ""
        lines.append(f"- Binance journal legs ({len(bn_legs)}){note}: total {_fmt(bn_total)}")
        for oid, sym, side, fill, mark, upl, tp, sl in bn_legs:
            lines.append(
                f"  - #{oid} {sym} {side} fill={fill} mark={_fmt(mark, '.8g')} "
                f"upl={_fmt(upl)} tp={_fmt(tp, '.8g')} sl={_fmt(sl, '.8g')}"
            )
    else:
        lines.append("- Binance journal legs: none open")

    g = guard_status(conn)
    lines.append("\n### Guards\n")
    lines.append(
        f"- loss_guard: blocked={g.get('blocked')} daily={g.get('daily_loss_pct')}% "
        f"drawdown={g.get('drawdown_pct')}%{(' — ' + g['reason']) if g.get('reason') else ''}"
    )
    if g.get("blocked"):
        problems.append("loss guard BLOCKED new entries: " + str(g.get("reason")))

    fees = fees_total(cur)
    lines.append("\n### Costs (30d journal fees, AI-only)\n")
    lines.append(f"- fees journaled: {_fmt(fees)}")
    cost_table(cur, lines, problems)

    try:
        run_window_section(conn, lines, problems)
    except Exception as e:
        lines.append(f"\n### Run window\n- section failed: "
                     f"{type(e).__name__}: {e}")
        problems.append(f"run window section failed: "
                        f"{type(e).__name__}: {e}")

    try:
        fx_section(cur, lines, problems)
    except Exception as e:
        lines.append(f"\n### FX\n- section failed: {type(e).__name__}: {e}")

    if problems:
        lines.append("\n### Anomalies\n")
        for p in problems:
            lines.append(f"- [!] {p}")
    else:
        lines.append("\n### Anomalies\n- none")

    lines.append("")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(lines))

    print("[OK] digest appended to", LOG)
    for p in problems:
        print("[ANOMALY]", p)
    conn.close()
    return problems


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
