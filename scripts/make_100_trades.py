"""One-shot: place 100 live orders on BTC/USDT the way a dashboard user
does — through the SAME API the Place / Close buttons call
(/api/manual/order + /api/manual/close/{id}) — then verify the journal.

100 placed+closed round trips alternate side so exposure never nets out
of sync with a flat book. No simulation, no DB stamping: every fill comes
back from Binance testnet with a broker_ref.

Run:  .venv/Scripts/python.exe scripts/make_100_trades.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8501"
SYMBOL = "BTC/USDT"
QTY = 0.001          # 0.001 BTC ≈ $83 notional on testnet
TARGET = 100
PAUSE_S = 1.5        # polite spacing between round trips


def post(path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ok = err = 0
    pnl_total = 0.0
    side = "SHORT"
    used_ids: set = set()   # journal ids already owned by this run
    for i in range(1, TARGET + 1):
        side = "LONG" if side == "SHORT" else "SHORT"
        # ── open ──
        try:
            res = post("/api/manual/order",
                       {"symbol": SYMBOL, "side": side, "qty": QTY})
            o = res.get("order") or {}
            oid = o.get("id")
            # Accept the row ONLY when it is genuinely NEW:
            # execution_intent is minute-bucketed, so a second POST in the
            # same minute can return an ALREADY-CLOSED row as a duplicate —
            # closing that id again would double-count an old trade. A
            # duplicate answer with a used id (never closed-traded below
            # this run) is retried up to 5 times with fresh payloads.
            attempts = 0
            while (oid is None or res.get("duplicate")
                   or oid in used_ids) and attempts < 5:
                attempts += 1
                time.sleep(2.0)
                res = post("/api/manual/order",
                           {"symbol": SYMBOL, "side": side,
                            "qty": QTY})
                o = res.get("order") or {}
                oid = o.get("id")
            if not oid or res.get("error"):
                raise RuntimeError(
                    f"order not filled: {o.get('status')} / {res.get('error')}")
            used_ids.add(oid)
        except Exception as e:
            err += 1
            print(f"[{i:3d}/{TARGET}] OPEN-FAIL {e}", flush=True)
            continue
        # ── close (real user: click Close on the row) ──
        try:
            try:
                post(f"/api/manual/close/{oid}")
                he = None
            except urllib.error.HTTPError as close_err:
                he = close_err
                if close_err.code != 409:
                    raise
            # 409 = the same-second double-fire ALREADY closed the row:
            # never re-place, only read its final PnL from the journal.
            final = None
            for _ in range(6):   # wait for the exit to stamp (pnl=null until then)
                final = next((x for x in
                              get_json("/api/orders?limit=50&status=closed")
                              .get("orders", []) if x.get("id") == oid), None)
                if final is not None and final.get("pnl") is not None:
                    break
                time.sleep(0.5)
            if final is None:
                raise RuntimeError(f"#{oid} not found after close")
            pnl = float(final.get("pnl") or 0.0)
            pnl_total += pnl
            ok += 1
            print(f"[{i:3d}/{TARGET}] #{oid} {side} fill={o.get('fill_price')} "
                  f"exit={final.get('exit_price')} pnl={pnl:+.4f} "
                  f"(cum {pnl_total:+.4f}){'' if he is None else ' [409-already-closed]'}",
                  flush=True)
        except Exception as e:
            err += 1
            print(f"[{i:3d}/{TARGET}] #{oid} OPEN-OK but CLOSE-FAIL: {e}",
                  flush=True)
        time.sleep(PAUSE_S)

    print(f"\nDone: {ok} round trips closed, {err} failures, "
          f"total pnl {pnl_total:+.4f}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
