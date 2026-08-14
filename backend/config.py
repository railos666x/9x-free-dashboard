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
# (verified against crypto_data.db): removed ICON/USDT (ticker is ICX),
# QLC/USDT, OMN/USDT, BLOB/USDT, BTCDOM/USDT (index, not a pair).
# ── Major ──
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT",
    # ── Layer 1 ──
    "ADA/USDT", "XRP/USDT", "AVAX/USDT", "DOT/USDT", "NEAR/USDT",
    "ALGO/USDT", "ATOM/USDT", "EOS/USDT", "TRX/USDT", "LTC/USDT",
    "BCH/USDT", "ETC/USDT", "ICP/USDT", "XLM/USDT", "XMR/USDT",
    "FIL/USDT", "HBAR/USDT", "EGLD/USDT", "THETA/USDT",
    # ── Layer 2 ──
    "ARB/USDT", "MATIC/USDT", "OP/USDT",
    # ── DeFi ──
    "AAVE/USDT", "UNI/USDT", "LINK/USDT", "COMP/USDT", "CRV/USDT",
    "SUSHI/USDT", "SNX/USDT", "YFI/USDT", "1INCH/USDT", "MKR/USDT",
    "BAND/USDT", "KNC/USDT", "ZRX/USDT", "RUNE/USDT", "LPT/USDT",
    "DYDX/USDT", "TRB/USDT", "OCEAN/USDT",
    # ── Meme ──
    "SHIB/USDT", "FLOKI/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT",
    # ── Gaming / Metaverse ──
    "SAND/USDT", "MANA/USDT", "AXS/USDT", "ENJ/USDT", "GALA/USDT",
    "ALICE/USDT", "CHZ/USDT", "BAT/USDT",
    # ── Infrastructure / Storage ──
    "GRT/USDT", "HNT/USDT", "NEO/USDT", "VET/USDT", "IOTA/USDT",
    "STORJ/USDT", "BLZ/USDT", "CVC/USDT", "CTSI/USDT", "CELR/USDT",
    "KAVA/USDT", "ONE/USDT", "MASK/USDT",
    # ── Other ──
    "DASH/USDT", "KSM/USDT", "SFP/USDT", "COTI/USDT", "CELO/USDT",
    "IOST/USDT", "CKB/USDT",
    # ── Additional from watchlist ──
    "OG/USDT", "BEL/USDT", "OGN/USDT", "SLP/USDT",
    "FIO/USDT", "CREAM/USDT", "MDT/USDT", "IRIS/USDT", "LUNA/USDT",
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
# Fear & Greed index (free API, no key)
# ----------------------------------------------------------------------
FEAR_GREED_API = "https://api.alternative.me/fng/"

# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
# Auto-refresh the dashboard this often (seconds).
DASHBOARD_REFRESH_SECONDS = 10
