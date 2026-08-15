"""
Central configuration for the cryptoautomation terminal.

Everything you might want to tweak lives here: symbols to watch,
timeframe defaults, how often to refresh, and news sources.
"""

import os
from pathlib import Path

# Load the API key from .env (if present). python-dotenv reads it for us.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ----------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------
# The SQLite file lives at the project root (one level above backend/).
DB_PATH = Path(__file__).resolve().parent.parent / "crypto_data.db"

# Local data dir (exchange API keys, logs) — never commit this.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------
# Market data (Binance)
# ----------------------------------------------------------------------
# Symbols to watch. Binance uses BASE/QUOTE format, e.g. "BTC/USDT".
# NOTE: deduplicated and pruned of pairs that are not on Binance Spot
# (verified 2026-08-15 against Binance exchangeInfo): removed ICON/USDT
# (ticker is ICX), QLC/USDT, OMN/USDT, BLOB/USDT, BTCDOM/USDT (index, not
# a pair), and the BREAK/delisted HNT/USDT, XMR/USDT, MATIC/USDT (renamed
# to POL — kept as POL/USDT below), MKR/USDT, OCEAN/USDT, EOS/USDT,
# FIO/USDT, CREAM/USDT, MDT/USDT, IRIS/USDT, BLZ/USDT.
# ── Major ──
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT",
    # ── Layer 1 ──
    "ADA/USDT", "XRP/USDT", "AVAX/USDT", "DOT/USDT", "NEAR/USDT",
    "ALGO/USDT", "ATOM/USDT", "TRX/USDT", "LTC/USDT",
    "BCH/USDT", "ETC/USDT", "ICP/USDT", "XLM/USDT",
    "FIL/USDT", "HBAR/USDT", "EGLD/USDT", "THETA/USDT", "POL/USDT",
    # ── Layer 2 ──
    "ARB/USDT", "OP/USDT",
    # ── DeFi ──
    "AAVE/USDT", "UNI/USDT", "LINK/USDT", "COMP/USDT", "CRV/USDT",
    "SUSHI/USDT", "SNX/USDT", "YFI/USDT", "1INCH/USDT",
    "BAND/USDT", "KNC/USDT", "ZRX/USDT", "RUNE/USDT", "LPT/USDT",
    "DYDX/USDT", "TRB/USDT",
    # ── Meme ──
    "SHIB/USDT", "FLOKI/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT",
    # ── Gaming / Metaverse ──
    "SAND/USDT", "MANA/USDT", "AXS/USDT", "ENJ/USDT", "GALA/USDT",
    "ALICE/USDT", "CHZ/USDT", "BAT/USDT",
    # ── Infrastructure / Storage ──
    "GRT/USDT", "NEO/USDT", "VET/USDT", "IOTA/USDT",
    "STORJ/USDT", "CVC/USDT", "CTSI/USDT", "CELR/USDT",
    "KAVA/USDT", "ONE/USDT", "MASK/USDT",
    # ── Other ──
    "DASH/USDT", "KSM/USDT", "SFP/USDT", "COTI/USDT", "CELO/USDT",
    "IOST/USDT", "CKB/USDT",
    # ── Additional from watchlist ──
    "OG/USDT", "BEL/USDT", "OGN/USDT", "SLP/USDT", "LUNA/USDT",
]

# Candle timeframes supported by Binance (named the way the API wants).
INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]
DEFAULT_INTERVAL = "15m"

# How many days of history to download on the first `backfill` run.
# The mean-reversion strategy needs 6-12 months of 1h data to validate
# a 60-70% win rate claim, so longer timeframes get deep history while
# 1m (expensive to download) stays short. Binance serves 1000 candles
# per request, so 365 days of 1h is only ~9 requests per symbol.
BACKFILL_DAYS = 365

# Per-interval backfill depth (days). Long timeframes = deep, 1m = shallow.
INTERVAL_BACKFILL_DAYS = {
    "1m": 30,
    "5m": 30,
    "15m": 90,
    "1h": 365,
    "4h": 365,
    "1d": 730,
}

# ----------------------------------------------------------------------
# Risk management & execution assumptions (used by risk.py / backtester.py)
# ----------------------------------------------------------------------
# Starting equity for backtests / paper trading.
INITIAL_CAPITAL = 10_000.0

# Default risk per trade as a fraction of equity (dynamic sizing uses this,
# capped by fractional Kelly when a measured edge exists).
RISK_PER_TRADE = 0.01        # 1% of equity per trade
MAX_RISK_PER_TRADE = 0.02    # hard cap: never risk > 2% of equity
KELLY_FRACTION = 0.25        # 25% fractional Kelly (variance control)

# Binance spot taker fee, per side (0.1%). Futures would be lower.
FEE_BPS = 10.0               # 10 bps per side

# Slippage assumption in basis points, per symbol tier. Conservative for a
# market-order execution model: majors ~0.5bps, mid-caps ~2bps, small/meme
# 5-15bps. Used by risk.slippage_bps().
SLIPPAGE_BPS_DEFAULT = 3.0
SLIPPAGE_BPS_TIERS = {
    # symbol -> bps (overrides the default)
    "BTC/USDT": 0.5,
    "ETH/USDT": 0.5,
    "SOL/USDT": 1.0,
    "BNB/USDT": 1.0,
    "DOGE/USDT": 2.0,
    "XRP/USDT": 1.0,
    "ADA/USDT": 2.0,
    "LINK/USDT": 2.0,
    "MATIC/USDT": 2.0,
    "AVAX/USDT": 2.0,
    "DOT/USDT": 2.0,
    "LTC/USDT": 2.0,
    "BCH/USDT": 2.0,
    "SHIB/USDT": 5.0,
    "PEPE/USDT": 5.0,
    "FLOKI/USDT": 5.0,
    "WIF/USDT": 5.0,
    "BONK/USDT": 5.0,
}

# Liquidity floor: a symbol must have a 1h close-volume above this multiple
# of its own trailing median volume to be tradable (kills illiquid listings).
MIN_VOLUME_MULT = 0.3

# Poll the newest candle this often (seconds) during `live`.
LIVE_REFRESH_SECONDS = 5

# How often each timeframe gets polled during `live`. Short timeframes change
# fast, long ones barely move — so we refresh 1m constantly and 1d almost never.
# This keeps every interval in the terminal live while staying far inside
# Binance's public rate limits.
INTERVAL_REFRESH = {
    "1m": 5,    # a new candle every minute
    "5m": 15,
    "15m": 30,
    "1h": 60,
    "4h": 300,
    "1d": 900,
}

# ----------------------------------------------------------------------
# News (CryptoPanic + RSS)
# ----------------------------------------------------------------------
# Free CryptoPanic API key. Create an account at https://cryptopanic.com/
# then add the key to a .env file:  CRYPTOPANIC_API_KEY=your_key_here
# If no key is set, the system quietly runs RSS-only — nothing breaks.
CRYPTOPANIC_API_KEY = os.environ.get("CRYPTOPANIC_API_KEY", "")

# How often to fetch new news (seconds) during `live`.
# Note: CryptoPanic's free tier allows ~600 requests/day, so 300s
# (288 requests/day) stays comfortably under the limit.
NEWS_REFRESH_SECONDS = 300

# ----------------------------------------------------------------------
# AI News Reviewer (optional LLM overlay — the reviewer works without this)
# ----------------------------------------------------------------------
# The reviewer scores news per coin with a built-in rule-based engine. To add
# an LLM overlay (any OpenAI-compatible endpoint: OpenAI, DeepSeek,
# Qwen/DashScope, local llama.cpp, …), set these in .env:
#   LLM_ENABLED=true
#   LLM_API_KEY=sk-...
#   LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
#   LLM_MODEL=qwen-plus
LLM_ENABLED = os.environ.get("LLM_ENABLED", "").lower() in ("1", "true", "yes")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")

# How often the LLM news overlay may actually re-ask a provider (seconds).
# Free tiers (Gemini Flash ~tens of req/day, OpenRouter 50-1000/day, Groq
# ~1000/day) would be exhausted in minutes by the 20s signals refresh, so
# the last successful LLM verdict is served from cache within this window.
LLM_MIN_INTERVAL_SECONDS = int(os.environ.get("LLM_MIN_INTERVAL_SECONDS", "600"))  # 10 min

# Optional LLM failover chain. When the primary (LLM_* above) is unreachable,
# rate-limited, or returns garbage, the reviewer tries each fallback in order
# and only gives up when every provider has failed. All providers must be
# OpenAI-compatible (OpenRouter, Groq, DeepSeek, Gemini's OpenAI-compat
# endpoint, …) — no code change, just config.
#
# Format: a `;`-separated list of `base_url|model[|api_key]` entries.
#   LLM_FALLBACKS="https://openrouter.ai/api/v1|meta-llama/llama-3.3-70b-instruct|sk-or-...;https://api.groq.com/openai/v1|llama-3.3-70b-versatile|gsk_..."
# Keyless providers work too (leave the api_key field empty).
_LLM_FALLBACKS_RAW = os.environ.get("LLM_FALLBACKS", "")
LLM_FALLBACKS: list[dict] = []
for _entry in [e.strip() for e in _LLM_FALLBACKS_RAW.split(";") if e.strip()]:
    _parts = [p.strip() for p in _entry.split("|")]
    if len(_parts) >= 2 and _parts[0] and _parts[1]:
        LLM_FALLBACKS.append({
            "base_url": _parts[0].rstrip("/"),
            "model": _parts[1],
            "api_key": _parts[2] if len(_parts) > 2 else "",
        })

# RSS feeds of trusted outlets. Format: (display name, feed URL).
# Crypto + macro/commodities/forex — each feed is fetched defensively,
# a dead feed is skipped, never fatal (see news.fetch_rss).
NEWS_FEEDS = [
    # ── Crypto ──
    ("CoinDesk",        "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph",   "https://cointelegraph.com/rss"),
    ("The Block",       "https://www.theblock.co/rss.xml"),
    ("Decrypt",         "https://decrypt.co/feed"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
    ("Bitcoin.com News", "https://news.bitcoin.com/feed/"),
    ("NewsBTC",         "https://www.newsbtc.com/feed/"),
    ("CryptoSlate",     "https://cryptoslate.com/feed/"),
    ("Blockworks",      "https://blockworks.co/feed"),
    ("The Defiant",     "https://thedefiant.io/feed"),
    ("BeInCrypto",      "https://beincrypto.com/feed/"),
    ("U.Today",         "https://u.today/rss"),
    ("AMBCrypto",       "https://ambcrypto.com/feed/"),
    ("CoinJournal",     "https://coinjournal.net/feed/"),
    ("Yahoo Crypto",    "https://finance.yahoo.com/news/cryptocurrency/rss"),
    # ── Macro / commodities / forex ──
    ("Reuters",         "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC Markets",    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch",     "https://feeds.content.dowjones.com/public/rss/mw_topstories"),
    ("Yahoo Markets",   "https://finance.yahoo.com/news/markets/rss/"),
    ("Kitco Gold",      "https://www.kitco.com/news/rss/"),
    ("OilPrice.com",    "https://oilprice.com/rss/main"),
    ("FXStreet",        "https://www.fxstreet.com/rss/news"),
    ("Zero Hedge",      "https://feeds.feedburner.com/zerohedge/feed"),
]

# Simple keyword sentiment for RSS stories that have no official score.
# A title hitting a BULLISH word counts +1, a BEARISH word counts -1.
BULLISH_KEYWORDS = [
    "surge", "soars", "surges", "spikes", "rally", "gains", "gain", "all-time high",
    "ath", "bull", "bullish", "buy", "buys", "adoption", "etf", "approved",
    "inflows", "breakout", "upgrade", "partnership", "hits", "record",
]
BEARISH_KEYWORDS = [
    "crash", "plunge", "plummets", "dump", "dumps", "drop", "falls", "falling",
    "selloff", "sell-off", "bear", "bearish", "hack", "hacked", "exploit",
    "ban", "banned", "lawsuit", "sues", "liquidation", "liquidated", "warning",
    "loss", "fines", "delisting", "collapses",
]

# ----------------------------------------------------------------------
# Execution (broker gateway — single path for all orders)
# ----------------------------------------------------------------------
# Which broker the execution layer uses: "paper" (default, offline fills
# from our candles + risk model), "alpaca" (paper or live REST, no SDK
# needed), or "binance" (signed spot orders).
EXECUTION_BROKER = os.environ.get("EXECUTION_BROKER", "paper").strip().lower()

# Alpaca keys. ALPACA_PAPER=true (default) talks to the free paper-trading
# endpoint; set ALPACA_PAPER=false for live (real orders — be careful).
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.environ.get("ALPACA_PAPER", "true").lower() in ("1", "true", "yes")
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"
ALPACA_LIVE_URL = "https://api.alpaca.markets"

# Alpaca crypto taker fee, in bps. Alpaca's order payload has no filled_fees
# for crypto and the activities feed doesn't report a fee either — so we
# estimate from their published schedule (0.25% taker below $100k volume;
# tier 2 is 0.22%). Override per account in .env.
ALPACA_FEE_BPS = float(os.environ.get("ALPACA_FEE_BPS", "25"))

# Max fraction of account equity one position may represent (notional cap,
# scaled by the confidence multiplier — a sure trade may take a bigger
# slice). Without it, a burst of simultaneous signals (e.g. 25 RSI(2)
# longs) would each try to spend ~100× their risk and blow past the cash.
MAX_POSITION_PCT = float(os.environ.get("MAX_POSITION_PCT", "0.15"))

# Max concurrently open positions the bot will hold. New entries are held
# back while this many positions are open, so a signal burst can't stack
# more exposure than the account can fund. 0 = unlimited.
MAX_OPEN_POSITIONS = int(os.environ.get("MAX_OPEN_POSITIONS", "5"))

# Binance execution keys (separate from the read-only portfolio keys in
# data/exchange_keys.json — keep those read-only, use these for trading).
BINANCE_EXEC_API_KEY = os.environ.get("BINANCE_EXEC_API_KEY", "")
BINANCE_EXEC_API_SECRET = os.environ.get("BINANCE_EXEC_API_SECRET", "")

# Proprietary signal source #2 (off by default — see proprietary_signals.py).
PROPRIETARY_SIGNALS_ENABLED = os.environ.get(
    "PROPRIETARY_SIGNALS_ENABLED", ""
).lower() in ("1", "true", "yes")

# Source B mode:
#   "rsi2" — Connors-style RSI(2) extremes on 15m (default, deterministic)
#   "llm"  — batched LLM analysis of the latest headlines via the failover
#            chain (Gemini → OpenRouter → Groq); requires LLM_ENABLED=true
PROPRIETARY_MODE = os.environ.get("PROPRIETARY_MODE", "rsi2").strip().lower()

# RSI(2) extreme thresholds for the rsi2 mode (LONG below low, SHORT above
# high). 10/90 is ultra-strict (rare signals, high conviction); 25/75 is a
# pragmatic default that actually produces agreement with source A.
PROPRIETARY_RSI_LOW = float(os.environ.get("PROPRIETARY_RSI_LOW", "25"))
PROPRIETARY_RSI_HIGH = float(os.environ.get("PROPRIETARY_RSI_HIGH", "75"))

# How often the scheduled bot loop (`python bot.py loop`) runs one cycle.
BOT_LOOP_SECONDS = int(os.environ.get("BOT_LOOP_SECONDS", "300"))  # 5 min

# Server-side auto-loop: when BOT_AUTO_LOOP=true, server.py starts the bot
# loop as a background thread (no manual `python bot.py loop` needed). The
# loop is DRY-RUN unless BOT_AUTO_LOOP_LIVE=true. Safety rail: a live
# auto-loop is refused for real-money brokers (live Alpaca / Binance) —
# only paper brokers auto-trade; real-money setups fall back to dry-run.
BOT_AUTO_LOOP = os.environ.get("BOT_AUTO_LOOP", "false").lower() in ("1", "true", "yes")
BOT_AUTO_LOOP_LIVE = os.environ.get("BOT_AUTO_LOOP_LIVE", "false").lower() in ("1", "true", "yes")

# Agreement gate (see arbitrator.arbitrate): how many signal sources must
# want a trade before the bot executes it.
#   2 (default) = BOTH sources must agree — production safety. Conflicts and
#                 missing sources never trade.
#   1 = a single source can trade alone, but only if it is at least
#       ARBITRATION_MIN_CONFIDENCE confident — handy for testing the
#       execution path / dry-testing the journal when source B is quiet.
# Confidence floor default is 45 = source B's flat conviction in rsi2 mode,
# so a lone RSI(2) extreme can be the inspectable test trade.
ARBITRATION_MIN_SOURCES = int(os.environ.get("ARBITRATION_MIN_SOURCES", "2"))
ARBITRATION_MIN_CONFIDENCE = float(os.environ.get("ARBITRATION_MIN_CONFIDENCE", "45"))

# Safety: when the gate is in single-source test mode, LIVE runs are blocked
# unless this is explicitly set to true. Prevents accidentally trading real
# money on a test-only gate. Dry runs are always allowed.
ALLOW_LIVE_SINGLE_SOURCE = os.environ.get("ALLOW_LIVE_SINGLE_SOURCE", "false").lower() in ("1", "true", "yes")

# Which generator feeds source A of the bot (see bot.py).
#   "math_engine" (default) — signals.py Bayesian fusion + factors. Strict
#       calibration (DIRECTIVE-001): often all-HOLD by design.
#   "strategy" — the validated mean-reversion v2 (strategy.compute_signal,
#       import-only, never edited). Produces full entry/SL/TP intents when
#       the market ranges; quiet in trending regimes by design.
#   "hybrid" — union of both: either generator can fire; when both fire the
#       same direction the stronger wins, opposing active opinions abstain.
SIGNAL_SOURCE_A = os.environ.get("SIGNAL_SOURCE_A", "math_engine").strip().lower()

# ----------------------------------------------------------------------
# News verification gate (see reviewer.verify_trade)
# ----------------------------------------------------------------------
# When the signal system agrees on a trade, the news review acts as a
# CONFIRMATION layer on top (the "confirmation AI"). It can only ever
# block or endorse — it never initiates trades:
#   - opposing headlines at or beyond NEWS_VETO_SCORE  → VETO (don't take)
#   - an extreme score in EITHER direction (|score| >= NEWS_BIG_MOVE_SCORE)
#     means a big move is expected → stand aside instead of trading into
#     the whipsaw (the confirmation AI says "don't take")
#   - aligned headlines at or beyond NEWS_CONFIRM_SCORE → confirm (the
#     intent is journaled with a "news confirms" reason)
#   - neutral / no news → never blocks a trade
NEWS_VERIFY_ENABLED = os.environ.get("NEWS_VERIFY_ENABLED", "true").lower() in ("1", "true", "yes")
NEWS_VETO_SCORE = float(os.environ.get("NEWS_VETO_SCORE", "30"))        # opposing |score| → veto
NEWS_CONFIRM_SCORE = float(os.environ.get("NEWS_CONFIRM_SCORE", "20"))  # aligned |score| → confirm
NEWS_BIG_MOVE_SCORE = float(os.environ.get("NEWS_BIG_MOVE_SCORE", "55"))  # |score| any direction → big move

# ----------------------------------------------------------------------
# Fear & Greed index (free API, no key)
# ----------------------------------------------------------------------
FEAR_GREED_API = "https://api.alternative.me/fng/"

# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
# Auto-refresh the dashboard this often (seconds).
DASHBOARD_REFRESH_SECONDS = 10
