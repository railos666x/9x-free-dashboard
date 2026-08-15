# Followups & Session Tracker

*Created: 2026-08-15 · Branch: `feature/ai-execution-news-gate` · All work committed
and pushed to GitHub (`origin/feature/ai-execution-news-gate`).*

This document is the single source of truth for what this session completed and
what is still open, so any future session (human or AI) can pick up exactly
where we left off.

---

## ✅ DONE — what was completed this session

All three commits are on `origin/feature/ai-execution-news-gate`:

| Commit | What it delivered |
|---|---|
| `ca92fa4` | Execution stack (paper/Alpaca/Binance brokers, idempotent journal, SL/TP monitor, reconciliation + foreign-fill flagging), two-source agreement gate (arbitrator + proprietary RSI(2)), verification gate, **news-verification layer** (`reviewer.verify_trade` — veto/confirm/big-move stand-aside), free-LLM chain (Gemini `gemini-3.1-flash-lite` + OpenRouter/Groq fallbacks, 10-min throttle), Alpaca paper broker on with account-equity sizing, legacy terminal removed |
| `1b031b8` | Trades analytics: date-range filter (Today/7d/30d/All + custom from→to), every-closed-trade list, live-trades panel (lean `/api/positions`, 10s poll), donut tooltip sign fix |
| `b6c3a0c` | Side-badge wrapping fix (`white-space: nowrap`) + emoji → inline-SVG icon set |

### Key system state (verified live)
- **Broker**: `EXECUTION_BROKER=alpaca`, `ALPACA_PAPER=true` — bot auto-loop LIVE on
  the ~$100k Alpaca paper account, sized from account equity (`execution._broker_equity`).
- **News review**: `LLM_ENABLED=true`, Gemini `gemini-3.1-flash-lite` (2.5-flash is
  deprecated for new users), cached 10 min. News gate vetoes/confirms every agreed
  trade; decisions journaled to `news_verdicts`.
- **Dashboard**: Orders view (bot status, position chart, trades grid with closed
  list + live panel, date-filtered journal), Signals view (news verdict badges +
  journal chips), SVG icon set, 0 ERROR rows.
- **Tests**: `tests/test_execution.py`, `test_arbitrator.py`, `test_verification.py`,
  `test_reviewer.py`, `test_proprietary.py` — all pass.

---

## ⏳ PENDING — followups not yet done

### A. "Reconnecting…" indicator on the dashboard
- **What**: a visible badge/flash when a dashboard poll fails (server restart /
  brief downtime) instead of silent console `ERR_CONNECTION_REFUSED` noise.
- **How**: `api()` in `web/dashboard/dashboard.js` is the single choke point —
  count consecutive failures, show a `#reconnectBadge` in the topbar after ≥2,
  hide on first success. `serverDot` already exists (red/green).
- **Effort**: small (~15 min).

### B. Server auto-reload on file change
- **What**: `python server.py --reload` so code edits restart the server
  automatically (no more manual kill/relaunch).
- **Status**: half-investigated — `backend/requirements.txt` has plain `uvicorn`
  (no `watchfiles`). Add a `--reload` flag to `uvicorn.run(...)` in `backend/server.py`
  (`reload=True`), and either `pip install watchfiles` into `.venv` or rely on
  uvicorn's stat-based reloader (needs a quick check that reload works with the
  daemon live-data threads).
- **Effort**: small–medium.

### C. Verify the first Alpaca-paper bot cycle end-to-end
- **What**: run one cycle and review the journal fills + held-by-reason breakdown
  (not_on_broker, no_short_on_spot, max_positions, news_veto, …) to confirm the
  switched broker behaves as designed.
- **How**: `cd backend && python bot.py --dry-run` (preview only) or watch the next
  auto-loop cycle in the Orders view; check `/api/orders` + `/api/bot/status`.
- **Effort**: small. (Note: 5 live positions were already observed — DOGE −$438,
  ADA −$131, CELO +$4, ALICE −$12, 1INCH −$2.)

### D. Tune `NEWS_VETO_SCORE` / `NEWS_BIG_MOVE_SCORE`
- **What**: pick veto thresholds from real gate history.
- **Status**: blocked on data — 72 decisions recorded, all neutral (news too weak
  today). Replay tool exists: `python scripts/analyze_news_gate.py`. Revisit after
  ~a week of live vetoes.
- **Effort**: tiny now, revisit later.

### E. OpenRouter + Groq free fallback keys
- **What**: add fallbacks so the news review survives Gemini rate-limits/downtime.
- **Status**: blocked on user — keys from `openrouter.ai/keys` and
  `console.groq.com/keys` (free). Wire with
  `python scripts/setup_free_llm.py --openrouter … --groq …`.
- **Effort**: tiny, needs user keys.

---

## 🔁 Useful commands (resume kit)

```bash
# Start the server (dashboard :8501, auto-loop included)
cd backend && python server.py

# Add free LLM keys to .env (Gemini/OpenRouter/Groq)
python scripts/setup_free_llm.py

# Replay news-gate thresholds against recorded decisions
python scripts/analyze_news_gate.py

# Tests
python tests/test_execution.py && python tests/test_reviewer.py \
  && python tests/test_arbitrator.py && python tests/test_verification.py
```

## 📌 Known quirks / gotchas (save for later)
- `gemini-2.5-flash` (and `-lite`) are **deprecated for new users** — use
  `gemini-3.1-flash-lite`. If the /models list changes, re-check with a tiny
  chat-completions call before updating `.env`.
- Alpaca only trades ~25 of our 77 symbols — the rest are held `not_on_broker`
  (by design, no more 422 spam). Alpaca crypto is **long-only** (SHORTs held).
- The bot sizes from the **Alpaca account equity** (not the ~$10k journal) — that
  is intentional; switching `EXECUTION_BROKER` back to `paper` reverts sizing.
- `server.log` and `.vscode/` are gitignored (added this session).
- Uvicorn is plain (no `[standard]` extras) — relevant for task B (auto-reload).
