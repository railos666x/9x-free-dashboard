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
# The SQLite file lives next to this file, in the project folder.
DB_PATH = Path(__file__).parent / "crypto_data.db"

# ----------------------------------------------------------------------
# Market data (Binance)
# ----------------------------------------------------------------------
# Symbols to watch. Binance uses BASE/QUOTE format, e.g. "BTC/USDT".
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT"]

# Candle timeframes supported by Binance (named the way the API wants).
INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]
DEFAULT_INTERVAL = "15m"

# How many days of history to download on the first `backfill` run.
BACKFILL_DAYS = 30

# Poll the newest candle this often (seconds) during `live`.
LIVE_REFRESH_SECONDS = 5

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

# RSS feeds of trusted outlets. Format: (display name, feed URL).
NEWS_FEEDS = [
    ("CoinDesk",        "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph",   "https://cointelegraph.com/rss"),
    ("The Block",       "https://www.theblock.co/rss.xml"),
    ("Decrypt",         "https://decrypt.co/feed"),
    ("Reuters",         "https://feeds.reuters.com/reuters/businessNews"),
    ("CNBC",            "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("Yahoo Crypto",    "https://finance.yahoo.com/news/cryptocurrency/rss"),
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
