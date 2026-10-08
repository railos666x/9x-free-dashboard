"""One-shot cleanup: close orphaned MT5 positions after the phantom-hedge fix.

A position is orphaned when:
  - its symbol has a journal row CLOSED since 2026-09-28 (the SL-close
    artifact batches), AND
  - its ticket is NOT the broker_ref of any still-open journal row.

Those positions are either the phantom mirror (opened by ticket-less
closes on the hedging account) or the original leg the journal already
closed + realized at the SL price. Closing them realizes the position at
market but does NOT touch the journal (its outcome is already recorded).

Run:  .venv/Scripts/python.exe scripts/cleanup_phantom_positions.py
      add --dry to list without closing
"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import MetaTrader5 as mt5  # noqa: E402
import config  # noqa: E402

DRY = "--dry" in sys.argv
conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row

open_refs = {r["broker_ref"] for r in conn.execute(
    "SELECT broker_ref FROM orders "
    "WHERE status IN ('filled','open') AND COALESCE(broker_ref,'') != ''")}
closed_syms = {r["symbol"].replace("/", "") for r in conn.execute(
    "SELECT DISTINCT symbol FROM orders "
    "WHERE status='closed' AND COALESCE(fill_time,'') >= '2026-09-28'")}

print(f"open journal refs: {len(open_refs)}, "
      f"recently closed symbols: {len(closed_syms)}")

mt5.initialize()
positions = mt5.positions_get() or []
orphans = []
for p in positions:
    if str(p.ticket) in open_refs:
        continue                      # journal still owns this position
    if p.symbol in closed_syms:
        orphans.append(p)

print(f"orphaned positions to close: {len(orphans)} "
      f"(of {len(positions)} open)")
total_pnl = 0.0
for p in orphans:
    is_long = p.type == mt5.POSITION_TYPE_BUY
    tick = mt5.symbol_info_tick(p.symbol)
    if tick is None:
        print(f"  SKIP {p.symbol} #{p.ticket}: no tick")
        continue
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": p.symbol,
        "volume": float(p.volume),
        "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
        "position": p.ticket,
        "price": tick.bid if is_long else tick.ask,
        "deviation": 30,
        "magic": 20260817,
        "comment": "phantom-cleanup",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    if DRY:
        print(f"  [dry] would close {p.symbol} #{p.ticket} vol {p.volume} "
              f"pnl {p.profit:.2f}")
        total_pnl += p.profit
        continue
    r = mt5.order_send(req)
    rc = getattr(r, "retcode", None)
    if r is not None and rc == mt5.TRADE_RETCODE_DONE:
        print(f"  [OK] closed {p.symbol} #{p.ticket} vol {p.volume} "
              f"realized {p.profit:.2f}")
        total_pnl += p.profit
    else:
        comment = (getattr(r, "comment", "") or "").strip()
        print(f"  [FAIL] {p.symbol} #{p.ticket}: retcode {rc} {comment}")

print(f"done. {'would ' if DRY else ''}release PnL: {total_pnl:.2f} "
      f"(vs journal PnL already recorded at SL closes)")
mt5.shutdown()
