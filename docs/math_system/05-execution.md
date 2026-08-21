# 05 — Execution & Risk Controls

Implemented in `backend/execution.py` (the only module that touches a
broker) + `backend/verification.py` (the gates). One idempotent entry point:
`execute_intent(conn, intent)`.

## Order path (idempotent)

```
intent (agreed signal) → intent_hash = sha256(symbol|direction|window)
→ if intent_hash already journaled → return existing order (no double orders)
→ dry_run? journal a preview row, done
→ size (risk-based, see 02) → broker market order → journal fill
```

The `intent_hash` UNIQUE constraint on the `orders` table is what makes
duplicate/conflicting orders impossible, even if two sources fire the same
trade.

## ATR-based SL/TP defaults

When a signal carries no risk levels (e.g. a news-driven source-B trade):

```
sl_dist = max(1.5 × ATR(1h), 0.5% of entry), capped at 5% of entry
stop    = entry ∓ sl_dist        (LONG below, SHORT above)
take    = entry ± 2 × sl_dist    (2R target)
```

The math engine's own signals carry regime-adaptive levels instead:
1.2–2.0× ATR in ranging regimes, 2.0–3.0× ATR in trends.

## SL/TP monitor

Every bot cycle prices each open journal position against the latest close:

```
hit = (price ≥ TP and LONG) or (price ≤ TP and SHORT)  → close, reason "tp"
hit = (price ≤ SL and LONG) or (price ≥ SL and SHORT)  → close, reason "sl"
```

Positions on assets the broker cannot trade are skipped (a phantom paper
position from an older setup must not 422-crash the cycle), and one failed
close never aborts the monitor — it retries next cycle.

## Risk controls (the "cooldown" family)

| Control | Mechanism |
|---|---|
| Max open positions | `MAX_OPEN_POSITIONS` (5) — new intents held `max_positions` while the cap is full |
| Per-position notional cap | `MAX_POSITION_PCT` (15% of equity, × confidence multiplier) |
| Duplicate / cooldown | same `intent_hash` never trades twice; intents are windowed (`window`), so a repeat signal inside the same window is a duplicate |
| Risk % sizing | 1% of equity per trade, Kelly-capped, confidence-scaled to 2% max (see 02) |

## Gates before execution (verification.py)

1. **Data gate** — candles must be structurally sane, **fresh**
   (newest open within 2.5× the interval), and spike-free (moves > 8× ATR
   without volume → bad tick).
2. **Agreement gate** — both sources must want the trade
   (`ARBITRATION_MIN_SOURCES=2` production; `=1` is an explicit test mode
   that requires `ALLOW_LIVE_SINGLE_SOURCE=true` for live runs).
3. **Cross-source price check** — Binance and Alpaca prices must agree
   within 1% before an Alpaca order fires.
4. **News gate** — see 04 (veto/confirm only).
5. **Intent re-gate** — the entry must sit within tolerance of the fresh
   close right before execution (no stale levels).

## Broker reconciliation

Journal ↔ broker sync: broker statuses mapped to the journal vocabulary
(`filled`/`open`/`cancelled`), fills and fees pulled in, positions that
disappeared broker-side are journaled `broker_closed` with realized PnL, and
broker fills our journal never placed are flagged as **foreign fills**
instead of being silently ignored.
