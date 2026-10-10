# Switching from demo accounts to real money

The bot places real orders. Today those orders land on **demo** accounts
(MT5 trial, Binance testnet, Alpaca paper). The code does not need to
change when you go live — only **credentials** and **two checklists**.
Follow all sections below, in order.

---

## 0. What is connected today (checked 2026-10-10)

| Venue | Account | Mode | Evidence |
|---|---|---|---|
| Exness · MT5 | login `472561011`, server `Exness-MT5Trial16` | **Demo** | `trade_mode = 0` from the terminal's own `account_info()` |
| Binance | exec API keys | **Testnet (geo-blocked)** | served 451 "restricted location" on 2026-10-10 — even testnet refused; orders fail with `binance 451` until Binance access is resolved (VPN/region/IP whitelist or a different exchange) |
| Alpaca | key prefix **PKEH** | **Paper** | validates on `paper-api.alpaca.markets`, rejected 401 by live |

The dashboard's account chips (flow strip, `acct-demo` amber) read the same
status from `/api/broker-accounts` — when you convert an account to real,
its chip turns **green `real` on its own**, with no code change.

---

## 1. Safety rails before real money (set these FIRST)

These are the guards that exist so a bug cannot empty a real account.
Set them in `.env` **before** the real credentials go in:

```env
# Agreement gate back to production safety: BOTH signal sources must
# agree before a trade is placed. (Currently 1 for testing.)
ARBITRATION_MIN_SOURCES=2

# Circuit breaker / loss guard caps (already active — verify values):
MAX_DAILY_LOSS_PCT=3.0      # stop new entries after losing 3% in a day
MAX_DRAWDOWN_PCT=10.0       # bigger stop on peak-to-now drawdown
```

Also verify visually after restart:

- Dashboard → flow strip shows green **Upgrade... no — `LIVE · REALORDERS`** and
  the new account chips show one **green real** chip per converted venue.
- Circuit Breaker card in AI Test view shows level 1 (normal risk).

## 2. Exness MT5: real account

**Prerequisite (verified missing 2026-10-10):** the MT5 terminal must have
its **AutoTrading button** ON (green ▶ in the toolbar, or Tools → Options →
Expert Advisors → *Allow algorithmic/automated trading*). With it OFF,
every order dies with `retcode 10027 — AutoTrading disabled by client`.
This applies to demo and real accounts alike.

Also note the unit system the bot uses: `qty` is in CONTRACT UNITS
(gold: 1.0 = 1 oz = 0.01 lots). A manual `qty` below one contract's
`volume_min` (gold min = 0.01 lots = 1 oz) is refused with
`zero volume`.

1. In MetaTrader 5, open the terminal that this machine connects to
   (`mt5.initialize()` uses the terminal's currently-logged-in account).
2. Log out of the trial account (right-click → Login → *Log out*), then
   **File → Login to Trade Account**, enter your **real Exness account
   number, password and the real server** (e.g. `Exness-MT5Real8`).
3. Confirm on the dashboard: the MT5 chip now says **real**, and the
   Accounts card shows the REAL login/server.
4. The broker routes FX/gold to this account automatically (hybrid mode).

## 3. Binance: live spot keys

1. On binance.com → Profile → **API Management** → create key
   (spot trade permission, IP whitelist to your machine's IP).
2. Then in [execution.py](backend/execution.py) change ONE line:

   ```python
   BINANCE_BASE = "https://api.binance.com"   # was testnet.binance.vision
   ```

3. Replace `BINANCE_EXEC_API_KEY` / `BINANCE_EXEC_API_SECRET` in `.env`
   with the live pair. (Keys are local-only — `.env` is gitignored.)
4. Confirm: Binance chip → **real**, Accounts card shows live balances.

## 4. Alpaca: live keys

1. alpaca.markets → complete brokerage onboarding → generate
   **live API keys** (account number will start `AR…`, not `PA…`).
2. `.env`: `ALPACA_PAPER=false` and swap the keys.

## 5. First week on real money (checklist)

- [ ] Keep position sizes small: `MT5_POSITION_PCT` / sizing base comes
      from the real account equity automatically — verify on one trade.
- [ ] Watch the Orders journal: every row must have a real `broker_ref`
      (an ID the venue can confirm). A row with no broker_ref = report it.
- [ ] Reconcile button (Orders view) — journal statuses vs broker, run
      after any manual close you do inside Exness/Binance/Alpaca.
- [ ] The AI signals card: read WHY each trade was placed (confirm/DXY/
      SMC scores are on every executed row).

## 6. What NOT to touch

- `EXECUTION_BROKER=hybrid` stays — crypto→Binance, FX/gold→MT5.
- `AI_MODE=live` stays.
- The PaperBroker class exists only in tests; it cannot be selected via
  config anymore.
