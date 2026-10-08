"""
Central configuration for the cryptoautomation terminal.

Everything you might want to tweak lives here: symbols to watch,
timeframe defaults, how often to refresh, and news sources.
"""

import os
from pathlib import Path

# Load the API key from .env (if present). python-dotenv reads it for us.
# A shell/profile that exports an EMPTY value for a key (seen in the wild:
# an empty LLM_FALLBACKS= silently disabled every configured fallback
# provider) must not block the .env value — so after load_dotenv(), any
# key that is still empty/missing in the environment is filled from .env.
# A genuinely-set (non-empty) env var still wins — standard precedence.
try:
    from dotenv import load_dotenv, dotenv_values
    load_dotenv()
    for _k, _v in dotenv_values().items():
        if _v is not None and not os.environ.get(_k):
            os.environ[_k] = _v
except ImportError:
    pass

# ----------------------------------------------------------------------
# Database & data directory
# ----------------------------------------------------------------------
# Local data dir (DB, exchange API keys, logs) — never commit this.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# The SQLite DB lives inside data/ (gitignored).
DB_PATH = DATA_DIR / "crypto_data.db"

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

# ── US-listed spot Bitcoin ETFs (tradeable on Alpaca like any stock) ────
# Real tickers — note ARK's Bitcoin ETF is ARKB, not ARKX (ARKX is ARK's
# space ETF; the watchlist mockup had it wrong). These are NOT on Binance:
# their 1h candles come from Yahoo (macro_data.ETF_TICKERS), and Alpaca
# executes them with no symbol conversion (they pass through unchanged).
# Purpose: more trade opportunities for the AI — crypto pairs are hard to
# trade profitably; these give the same strategy 8 more liquid instruments.
ETF_SYMBOLS = [
    "IBIT",   # iShares Bitcoin Trust
    "BITB",   # Bitwise Bitcoin ETF
    "FBTC",   # Fidelity Wise Origin Bitcoin Fund
    "BTCW",   # WisdomTree Bitcoin Fund
    "BTCO",   # Invesco Galaxy Bitcoin ETF
    "HODL",   # VanEck Bitcoin Trust
    "EZBC",   # Franklin Templeton Digital Holdings Trust
    "ARKB",   # ARK 21Shares Bitcoin ETF
]

# ── Exness demo via MetaTrader 5 (the AI's forex/metals/indices broker) ──
# Exness retail accounts have NO public REST API — the standard (and
# Exness-endorsed) automation path is the `MetaTrader5` Python package
# talking to the LOCAL MT5 terminal: the terminal must be running and logged
# into the Exness demo on this machine (Windows-only package). These display
# symbols are the subset of the macro universe Exness actually quotes, plus
# BTC/ETH as CFDs (LONG and SHORT both work — unlike spot Alpaca).
# 1h candles come from Yahoo (macro_data.ETF_TICKERS-style backfill); the
# signal engine / bot trade them exactly like any other symbol.
EXNESS_SYMBOLS = [
    # forex majors
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD", "USD/CAD",
    "USD/CNY", "DXY",
    # forex crosses + minors (added 2026-08-18 — more setups per cycle)
    "EUR/GBP", "EUR/JPY", "GBP/JPY", "AUD/JPY", "NZD/USD",
    "USD/SGD", "USD/MXN", "EUR/CHF",
    "EUR/AUD", "EUR/CAD", "EUR/NZD", "GBP/AUD", "GBP/CAD", "GBP/NZD",
    "GBP/CHF", "AUD/CAD", "AUD/NZD", "NZD/CHF", "CAD/JPY", "CHF/JPY",
    "NZD/JPY", "USD/ZAR", "USD/TRY", "USD/SEK", "USD/NOK", "USD/PLN",
    # metals + energy
    "XAU/USD", "XAG/USD", "XPT/USD", "XPD/USD", "WTI/USD",
    "BRENT/USD", "NAT_GAS/USD",
    # indices
    "SPX/USD", "NDX/USD", "DOW/USD", "DAX", "NIKKEI", "FTSE",
    "EUROSTOXX", "HANG_SENG", "FR40", "ES35", "ITA40", "AU200",
    # crypto CFDs (long + short)
    "BTC/USDT", "ETH/USDT",
]

# Display symbol -> Exness MT5 symbol. Names are the standard Exness MT5
# demo listings (indices as cash CFDs, WTI = USOIL). Adjust here if the
# account's server names differ (symbol_info() will surface mismatches).
EXNESS_MT5_SYMBOLS = {
    "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
    "USD/CHF": "USDCHF", "AUD/USD": "AUDUSD", "USD/CAD": "USDCAD",
    "USD/CNY": "USDCNY", "DXY": "DXY",
    "EUR/GBP": "EURGBP", "EUR/JPY": "EURJPY", "GBP/JPY": "GBPJPY",
    "AUD/JPY": "AUDJPY", "NZD/USD": "NZDUSD", "USD/SGD": "USDSGD",
    "USD/MXN": "USDMXN", "EUR/CHF": "EURCHF",
    "EUR/AUD": "EURAUD", "EUR/CAD": "EURCAD", "EUR/NZD": "EURNZD",
    "GBP/AUD": "GBPAUD", "GBP/CAD": "GBPCAD", "GBP/NZD": "GBPNZD",
    "GBP/CHF": "GBPCHF", "AUD/CAD": "AUDCAD", "AUD/NZD": "AUDNZD",
    "NZD/CHF": "NZDCHF", "CAD/JPY": "CADJPY", "CHF/JPY": "CHFJPY",
    "NZD/JPY": "NZDJPY", "USD/ZAR": "USDZAR", "USD/TRY": "USDTRY",
    "USD/SEK": "USDSEK", "USD/NOK": "USDNOK", "USD/PLN": "USDPLN",
    "XAU/USD": "XAUUSD", "XAG/USD": "XAGUSD", "XPT/USD": "XPTUSD",
    "XPD/USD": "XPDUSD", "WTI/USD": "USOIL",
    "BRENT/USD": "UKOIL", "NAT_GAS/USD": "NGAS",
    "SPX/USD": "US500", "NDX/USD": "USTEC", "DOW/USD": "US30",
    "DAX": "DE30", "NIKKEI": "JP225", "FTSE": "UK100",
    "EUROSTOXX": "EU50", "HANG_SENG": "HK50", "FR40": "FR40",
    "ES35": "ES35", "ITA40": "ITA40", "AU200": "AU200",
    "BTC/USDT": "BTCUSD", "ETH/USDT": "ETHUSD",
}

# Everything the bot can watch + trade: Binance crypto pairs + the US
# Bitcoin ETFs (Alpaca) + the Exness forex/metals/indices universe (MT5).
# Collector/Binance-only code keeps iterating config.SYMBOLS; signal/execution
# code iterates this wider universe.
TRADABLE_UNIVERSE = SYMBOLS + ETF_SYMBOLS + EXNESS_SYMBOLS

# ── The bot's TRADING universe (2026-08-18: hybrid broker routing) ────
# Forex/metals/energy/indices → MT5/Exness
# Crypto → Binance testnet
# Crypto was retired 2026-08-17 but re-enabled 2026-08-18 for Binance routing.
CRYPTO_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
                  "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT"]
TRADING_UNIVERSE = [s for s in EXNESS_SYMBOLS
                    if s not in ("BTC/USDT", "ETH/USDT")] + CRYPTO_SYMBOLS

# MT5 terminal executable (the MetaTrader5 package connects to it). Path to
# the installed terminal64.exe; the package also finds the default install.
MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH", "").strip() or None
# Master switch — set false to keep MT5 out of the broker chain entirely.
EXNESS_MT5_ENABLED = os.environ.get("EXNESS_MT5_ENABLED", "true").lower() in ("1", "true", "yes")
# Notional cap for MT5 positions (share of the sizing base = equity or
# free margin). Lot-based CFDs (gold, indices) have big minimum lots — a
# 0.01 XAUUSD lot is ~$4.4k — so the 15% crypto cap would zero-size them.
# 50% lets a min-lot gold position through on a ~$10k demo; per-trade RISK
# is still bounded by the ATR stop (~1-2%), and MT5 margin rules reject
# anything the account can't fund. Lower it for a real account.
MT5_POSITION_PCT = float(os.environ.get("MT5_POSITION_PCT", "0.5"))

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

# Vol-aware sizing (roadmap Phase 2, P1): scale the base risk by
# target_vol / realized_vol — risk MORE in quiet tape, LESS in wild tape —
# clamped so a single trade can never be half-vanished or doubled.
SIZE_VOL_ENABLED = os.environ.get("SIZE_VOL_ENABLED", "true").lower() in (
    "1", "true", "yes")
SIZE_VOL_TARGET_ATR_PCT = float(
    os.environ.get("SIZE_VOL_TARGET_ATR_PCT", "0.01"))   # 1% of price
SIZE_VOL_MIN_MULT = float(os.environ.get("SIZE_VOL_MIN_MULT", "0.5"))
SIZE_VOL_MAX_MULT = float(os.environ.get("SIZE_VOL_MAX_MULT", "2.0"))

# Rolling-bucket Kelly sizing (roadmap Phase 2, P2): per
# (venue, regime, news-tag) bucket, expectancy from the last SIZE_KELLY_LOOKBACK
# closed trades converts to a fractional-Kelly multiplier in
# [SIZE_KELLY_MIN_MULT, SIZE_KELLY_MAX_MULT]; 0 when the bucket's 95% CI on
# expectancy is negative (a data-driven stop for buckets that lose money).
SIZE_KELLY_ENABLED = os.environ.get("SIZE_KELLY_ENABLED", "true").lower() in (
    "1", "true", "yes")
SIZE_KELLY_LOOKBACK = int(os.environ.get("SIZE_KELLY_LOOKBACK", "30"))
SIZE_KELLY_MIN_MULT = float(os.environ.get("SIZE_KELLY_MIN_MULT", "0.5"))
SIZE_KELLY_MAX_MULT = float(os.environ.get("SIZE_KELLY_MAX_MULT", "1.5"))
SIZE_KELLY_ANNUALIZE = float(os.environ.get("SIZE_KELLY_ANNUALIZE", "50"))
SIZE_KELLY_FRACTION = float(os.environ.get("SIZE_KELLY_FRACTION", "0.25"))
# Base risk used to translate bucket expectancy into Kelly f* per trade.
SIZE_KELLY_BASE_RISK_PCT = float(
    os.environ.get("SIZE_KELLY_BASE_RISK_PCT", "0.01"))

# Cost-clearance veto (2026-10-01): refuse entries whose planned winning
# trade (TP distance) doesn't clear MULT × the estimated round-trip
# execution cost (slippage + taker fee, both sides — risk.total_cost_bps).
# 2× covers cost variance across the round trip (news-tape slippage,
# requotes). Missing entry/TP degrades open — never a false veto.
COST_VETO_ENABLED = os.environ.get("COST_VETO_ENABLED", "true").lower() in (
    "1", "true", "yes")
COST_VETO_MIN_EDGE_X = float(os.environ.get("COST_VETO_MIN_EDGE_X", "2.0"))

# Venue-side protection for Binance spot legs (2026-10-05): when an entry
# market order fills, rest an OCO bracket on the exchange — STOP_LOSS_LIMIT
# + TAKE_PROFIT_LIMIT, one cancels the other — so SL/TP fire even with this
# bot offline (the same guarantee the MT5 basket gets from deal-attached
# sl/tp). The software monitor (check_position) stays authoritative: it
# closes via the journal first and cancels the bracket; a failed/skipped
# bracket only degrades to today's behavior, never to a worse one.
BINANCE_OCO_ENABLED = os.environ.get("BINANCE_OCO_ENABLED", "true").lower() in (
    "1", "true", "yes")
# OCO legs are market-on-trigger (TAKE_PROFIT / STOP_LOSS on the leg-based
# orderList/oco schema): they execute as MARKET the moment the trigger
# touches, so no stop-limit buffer is needed — a fast tape cannot leave the
# stop resting unfilled. (Legacy flat-schema knob BINANCE_OCO_STOP_LIMIT_PCT
# removed 2026-10-05 when the venue rejected the old parameter set.)

# ── Government-officials feed (2026-10-05, PROTOTYPE — two-week shadow) ──
# Official .gov / central-bank RSS releases (Fed, FOMC speeches, BoC, BoE,
# ECB, SEC, CFTC — every feed HTTP-probed live before inclusion; the dead
# ones are listed in the CHANGELOG). Rows land in a SEPARATE gov_news table
# and NEVER touch the live news pool, so live ai_news sentiment is
# untouched. A shadow cycle runs them through the SAME noise gate and Groq
# scorer as the live path and journals proposed trades PAPER-ONLY into
# test_trades (ai_model='groq-gov') for a two-week evaluation.
GOV_FEEDS_ENABLED = os.environ.get("GOV_FEEDS_ENABLED", "true").lower() in (
    "1", "true", "yes")
GOV_FETCH_SECONDS = int(os.environ.get("GOV_FETCH_SECONDS", "180"))
GOV_SHADOW_ENABLED = os.environ.get("GOV_SHADOW_ENABLED", "true").lower() in (
    "1", "true", "yes")
# Open shadow trades older than this many bars close at last price
# ('shadow_expired') — the shadow book must never accumulate zombie rows.
GOV_SHADOW_MAX_HOLD_BARS = int(os.environ.get("GOV_SHADOW_MAX_HOLD_BARS", "72"))
# gov_news rows older than this are purged (news stays tidy; nothing in the
# shadow book reads headlines older than NEWS_MAX_AGE_HOURS anyway).
GOV_SHADOW_TTL_DAYS = int(os.environ.get("GOV_SHADOW_TTL_DAYS", "7"))
GOV_FEEDS = [
    # Verified live 2026-10-05 (HTTP probe: 200 + parseable items):
    ("Fed",         "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("FOMC",        "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("BoC",         "https://www.bankofcanada.ca/feed/"),
    ("BoE",         "https://www.bankofengland.co.uk/rss/news"),
    ("ECB",         "https://www.ecb.europa.eu/rss/press.html"),
    ("SEC",         "https://www.sec.gov/news/pressreleases.rss"),
    ("CFTC",        "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
]

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
# Alpha Vantage News & Sentiment (fast structured news — news.py)
# ----------------------------------------------------------------------
# Free key from https://www.alphavantage.co/support/#api-key (prefix
# `ak_live_...`). One NEWS_SENTIMENT call returns the top crypto stories
# with per-ticker sentiment scores + relevance — real structured scores,
# not keyword guesses. Free tier = 25 requests/day, so the fetch is
# throttled to ALPHA_NEWS_FETCH_HOURS (6h default → ≤4 calls/day) and is
# fully optional: no key = nothing breaks.
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
ALPHA_NEWS_ENABLED = os.environ.get("ALPHA_NEWS_ENABLED", "true").lower() in ("1", "true", "yes")
ALPHA_NEWS_FETCH_HOURS = float(os.environ.get("ALPHA_NEWS_FETCH_HOURS", "6"))
ALPHA_NEWS_LIMIT = int(os.environ.get("ALPHA_NEWS_LIMIT", "50"))
# Which coins the NEWS_SENTIMENT call filters on. FREE KEYS ARE LIMITED TO
# 5 TICKERS PER REQUEST (verified: 50+ tickers -> "Invalid inputs"), so the
# majors — whose stories dominate the crypto news feed — get structured
# scores here; the long tail is covered by RSS + the Groq analyst.
ALPHA_NEWS_TICKERS = [t.strip().upper() for t in
                      os.environ.get("ALPHA_NEWS_TICKERS",
                                     "BTC,ETH,SOL,BNB,DOGE").split(",") if t.strip()]

# ----------------------------------------------------------------------
# Tavily — real-time news search + article extraction (news.py / web_scraper.py)
# ----------------------------------------------------------------------
# Tavily is the AI-agent search API (free key at tavily.com, prefix
# `tvly-...`). Two jobs in this system:
#   1. news.py — one `topic=news` search call returns FRESH crypto stories
#      with real published dates (Alpha Vantage's free feed lags by months,
#      so this is the fresh structured layer). Free tier = 1000 credits/
#      month, one search ≈ 1 credit, so the fetch is throttled to
#      TAVILY_NEWS_FETCH_HOURS (default 6h → ≤4 calls/day ≈ 120/mo).
#   2. web_scraper.py — `extract` endpoint as a Firecrawl fallback for full
#      article content (one credit per URL, only used on demand).
# Optional: no key = Alpha Vantage + RSS + Groq still work.
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_NEWS_ENABLED = os.environ.get("TAVILY_NEWS_ENABLED", "true").lower() in ("1", "true", "yes")
TAVILY_NEWS_FETCH_HOURS = float(os.environ.get("TAVILY_NEWS_FETCH_HOURS", "6"))
TAVILY_NEWS_LIMIT = int(os.environ.get("TAVILY_NEWS_LIMIT", "20"))
# How far back the news search looks (days). 1 = only today's stories.
TAVILY_NEWS_DAYS = int(os.environ.get("TAVILY_NEWS_DAYS", "1"))
# Search query. Since 2026-08-17 the bot trades the EXNESS forex/metals/
# indices universe on AI/news signals, so the feed must include MACRO
# stories (dollar, gold, oil, equity indices) as well as crypto — the Groq
# analyst scores instruments from whatever headlines are here. Free tier =
# one search per TAVILY_NEWS_FETCH_HOURS, so breadth matters more than
# precision.
TAVILY_NEWS_QUERY = os.environ.get(
    "TAVILY_NEWS_QUERY",
    "forex dollar gold oil stock market macro news OR cryptocurrency market news")

# ----------------------------------------------------------------------
# Firecrawl web scraping (web_scraper.py — full article content)
# ----------------------------------------------------------------------
# Firecrawl turns any news URL into clean markdown. That gives the AI
# agents (ai_agents.agent_news_sentiment) the full article as CONTEXT
# instead of a bare headline. Free key from firecrawl.dev (prefix
# `fc-...`). Scraped content is cached in the `article_content` table for
# FIRECRAWL_CACHE_TTL_HOURS so credits aren't re-burned on repeat asks.
# When Firecrawl is OFF or fails, web_scraper falls back to Tavily's
# `extract` endpoint (TAVILY_API_KEY above).
FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
FIRECRAWL_ENABLED = os.environ.get("FIRECRAWL_ENABLED", "true").lower() in ("1", "true", "yes")
FIRECRAWL_CACHE_TTL_HOURS = float(os.environ.get("FIRECRAWL_CACHE_TTL_HOURS", "24"))
FIRECRAWL_TIMEOUT = int(os.environ.get("FIRECRAWL_TIMEOUT", "60"))

# ----------------------------------------------------------------------
# Finnhub — real-time market data + news (finnhub.io)
# ----------------------------------------------------------------------
# Finnhub provides real-time quotes, company news, and market sentiment.
# Free tier: 60 API calls/minute, includes forex, stocks, and crypto.
# Get free key at: https://finnhub.io/register
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_ENABLED = bool(FINNHUB_API_KEY)

# ----------------------------------------------------------------------
# FRED (Federal Reserve Economic Data) — macro economic indicators
# ----------------------------------------------------------------------
# FRED provides 816K+ economic time series: GDP, CPI, unemployment, interest
# rates, etc. Free, no API key required for basic access.
# Documentation: https://fred.stlouisfed.org/docs/api/fred/
FRED_ENABLED = os.environ.get("FRED_ENABLED", "true").lower() in ("1", "true", "yes")
# FRED API key (optional, increases rate limits from 120 to unlimited)
# Get free key at: https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ----------------------------------------------------------------------
# CoinGecko — crypto market data (no API key required)
# ----------------------------------------------------------------------
# CoinGecko provides comprehensive crypto data: prices, volume, market cap,
# developer stats, social metrics. Free tier is rate-limited but unlimited
# in total calls. No API key needed for basic endpoints.
# Documentation: https://docs.coingecko.com/reference/introduction
COINGECKO_ENABLED = os.environ.get("COINGECKO_ENABLED", "true").lower() in ("1", "true", "yes")
COINGECKO_BASE_URL = os.environ.get("COINGECKO_BASE_URL", "https://api.coingecko.com/api/v3")
# Optional: Pro API key for higher rate limits
# Get at: https://www.coingecko.com/en/api/pricing
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "")

# ----------------------------------------------------------------------
# NewsAPI.org — 80K+ news sources worldwide
# ----------------------------------------------------------------------
# NewsAPI provides real-time news from 80,000+ sources. Free tier: 100
# requests/day, developer plan. Good for broader news coverage beyond Tavily.
# Get free key at: https://newsapi.org/register
NEWSAPI_ORG_KEY = os.environ.get("NEWSAPI_ORG_KEY", "")
NEWSAPI_ORG_ENABLED = bool(NEWSAPI_ORG_KEY)

# ----------------------------------------------------------------------
# ExchangeRate-API — forex rate backup
# ----------------------------------------------------------------------
# ExchangeRate-API provides 165+ currency rates. Free tier: 1,500 req/month.
# Useful as backup when MT5 forex data lags or for cross-rate calculations.
# Get free key at: https://www.exchangerate-api.com/pricing
EXCHANGERATE_API_KEY = os.environ.get("EXCHANGERATE_API_KEY", "")
EXCHANGERATE_ENABLED = bool(EXCHANGERATE_API_KEY)

# ----------------------------------------------------------------------
# Fed Treasury — US Treasury yields/debt (no key required)
# ----------------------------------------------------------------------
# fiscaldata.treasury.gov — official US Treasury data
FED_TREASURY_ENABLED = os.environ.get("FED_TREASURY_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Frankfurter — ECB forex rates (no key required)
# ----------------------------------------------------------------------
# European Central Bank reference rates
FRANKFURTER_ENABLED = os.environ.get("FRANKFURTER_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Currency API — 150+ currencies via GitHub CDN (no key required)
# ----------------------------------------------------------------------
# fawazahmed0/currency-api — free, no rate limit
CURRENCY_API_ENABLED = os.environ.get("CURRENCY_API_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Coinpaprika — crypto price backup (no key required)
# ----------------------------------------------------------------------
# Backup to CoinGecko for crypto data
COINPAPRIKA_ENABLED = os.environ.get("COINPAPRIKA_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# DefiLlama — DeFi TVL, stablecoins, yields (no key required)
# ----------------------------------------------------------------------
# On-chain DeFi data, stablecoin supply
DEFILLAMA_ENABLED = os.environ.get("DEFILLAMA_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Mempool.space — BTC mempool/fees (no key required)
# ----------------------------------------------------------------------
# Bitcoin network activity signals
MEMPOOL_ENABLED = os.environ.get("MEMPOOL_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Fear & Greed Index — Alternative.me (no key required)
# ----------------------------------------------------------------------
# Crypto sentiment indicator, contrarian signal
FEAR_GREED_ENABLED = os.environ.get("FEAR_GREED_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Econdb — global macro data (no key required)
# ----------------------------------------------------------------------
# Economic indicators for multiple countries
ECONDB_ENABLED = os.environ.get("ECONDB_ENABLED", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Etherscan V2 — on-chain data (free API key required)
# ----------------------------------------------------------------------
# Etherscan provides Ethereum blockchain data: transactions, token balances,
# contract interactions. Free tier: 1,000 records per request.
# Get free key at: https://etherscan.io/apis
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
ETHERSCAN_ENABLED = bool(ETHERSCAN_API_KEY)

# ----------------------------------------------------------------------
# MarketAux — financial news with sentiment (free API key required)
# ----------------------------------------------------------------------
# MarketAux provides pre-tagged financial news with tickers and sentiment.
# Free tier: 100 requests/day, 50 articles per request.
# Get free key at: https://www.marketaux.com/
MARKETAUX_API_KEY = os.environ.get("MARKETAUX_API_KEY", "")
MARKETAUX_ENABLED = bool(MARKETAUX_API_KEY)

# ----------------------------------------------------------------------
# NewsData.io — news backup (free API key required)
# ----------------------------------------------------------------------
# NewsData.io provides news from 150+ countries. Free tier allows
# commercial use. Good backup to CryptoPanic and MarketAux.
# Get free key at: https://newsdata.io/register
NEWSDATA_API_KEY = os.environ.get("NEWSDATA_API_KEY", "")
NEWSDATA_ENABLED = bool(NEWSDATA_API_KEY)

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

# ----------------------------------------------------------------------
# Groq News Analyst (news_analyst.py — the news sentiment & analytics engine)
# ----------------------------------------------------------------------
# Groq is the MAIN news engine when a key is set: it ingests the latest
# headlines and returns structured per-asset analytics — sentiment score
# (-1..+1), confidence, impact horizon and the assets mentioned. The key is
# read from .env only (never hardcoded). See docs/math_system/04-
# news-sentiment.md for the exact schema and prompt.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL",
                               "https://api.groq.com/openai/v1").rstrip("/")
# Primary + fallback. Verified live against this account's /models on
# 2026-08-17: the previous defaults were qwen/qwen3.6-27b (free-tier quota
# was exhausted → 429 on the analysis prompt, stalling the bot cycle for
# minutes of backoff) and llama-3.3-70b-versatile (404 — NOT a model on
# this account, so the failover never actually failed over). GPT-OSS-120B
# answers the full batched news analysis in ~2s; 20B is the lighter spare.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = os.environ.get("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")

# ----------------------------------------------------------------------
# OmniRoute — local AI gateway (news_analyst / ai_agents / reviewer)
# ----------------------------------------------------------------------
# OmniRoute (https://github.com/diegosouzapw/OmniRoute) is a free, MIT,
# LOCAL-FIRST OpenAI-compatible gateway: one endpoint in front of 300+
# providers / 1200+ models with quota-aware auto-fallback. Point the same
# transport at it and every LLM call in this system (news analyst, the
# three AI agents, the reviewer overlay) can ride free tiers the gateway
# picks — zero-config `auto` combo, or explicit combos like
# `auto/best-reasoning`, `auto/pro-coding` (see the OmniRoute dashboard).
#
# Default base URL is the standard local install. Local gateways are
# keyless, so OMNIROUTE_API_KEY is optional — the transport skips the
# Authorization header when empty (matching the localhost rule in
# reviewer._llm_providers). When OMNIROUTE_BASE_URL is set, the news
# analyst tries Groq first (if keyed), then OmniRoute; the reviewer chain
# gains OmniRoute as a keyless local provider. Never a hard dependency:
# all callers fall back to rule-based scoring when nothing answers.
OMNIROUTE_BASE_URL = os.environ.get(
    "OMNIROUTE_BASE_URL", "http://localhost:20128/v1").rstrip("/")
OMNIROUTE_API_KEY = os.environ.get("OMNIROUTE_API_KEY", "")
# `auto` = the gateway's balanced default combo (health/quota/cost/latency
# scored live). Change to a named combo (auto/coding, auto/fast, …) if the
# dashboard shows one that suits the workload.
OMNIROUTE_MODEL = os.environ.get("OMNIROUTE_MODEL", "auto")
# Set false to keep OmniRoute out of the LLM chain entirely.
OMNIROUTE_ENABLED = os.environ.get("OMNIROUTE_ENABLED", "true").lower() in ("1", "true", "yes")


def llm_available() -> bool:
    """True when at least one LLM transport is configured for the news
    analyst / AI agents: a Groq key OR the OmniRoute local gateway.
    Everything downstream falls back to rule-based scoring when this is
    False, so it is the single gate for "is there an LLM at all?"."""
    return bool(GROQ_API_KEY) or (OMNIROUTE_ENABLED and bool(OMNIROUTE_BASE_URL))

# Master switch: when true AND a Groq key is set, review_market / the news
# gate use the Groq analyst first, falling back to the Gemini chain and the
# rule-based scorer.
NEWS_ANALYST_ENABLED = os.environ.get("NEWS_ANALYST_ENABLED", "true").lower() in ("1", "true", "yes")

# How long one Groq market analysis is cached (seconds). News moves slowly;
# the 5-min bot loop and dashboard polls must not burn the rate limit.
NEWS_ANALYST_CACHE_TTL = int(os.environ.get("NEWS_ANALYST_CACHE_TTL", "600"))  # 10 min

# Trade-signal thresholds for news_analyst.signal_for (sentiment -1..+1):
#   score >=  NEWS_SIGNAL_LONG   AND confidence >= NEWS_SIGNAL_MIN_CONFIDENCE → LONG
#   score <=  NEWS_SIGNAL_SHORT  AND confidence >= NEWS_SIGNAL_MIN_CONFIDENCE → SHORT
#   otherwise → HOLD (noise threshold). Confidence comes from the model's own
#   self-assessment (0..1); "high confidence" trades only fire above the floor.
NEWS_SIGNAL_LONG = float(os.environ.get("NEWS_SIGNAL_LONG", "0.35"))
NEWS_SIGNAL_SHORT = float(os.environ.get("NEWS_SIGNAL_SHORT", "-0.35"))
NEWS_SIGNAL_MIN_CONFIDENCE = float(os.environ.get("NEWS_SIGNAL_MIN_CONFIDENCE", "0.6"))

# ----------------------------------------------------------------------
# AI Agents (ai_agents.py — the three production agent prompts on Groq)
# ----------------------------------------------------------------------
# The three agents (news catalyst sentiment, HODL/portfolio research, and
# chart confluence verification) run on the same Groq transport as the news
# analyst. They are on-demand (dashboard) or gated (bot) — never in a hot
# loop. AI_AGENT_MODEL defaults to GROQ_MODEL; override per role if needed.
AI_AGENTS_ENABLED = os.environ.get("AI_AGENTS_ENABLED", "true").lower() in ("1", "true", "yes")
AI_AGENT_MODEL = os.environ.get("AI_AGENT_MODEL", "") or GROQ_MODEL

# Chart-confluence gate (agent 3) for the news-driven source B: when true,
# a news LONG/SHORT signal is verified against the chart structure (price
# vs nearest support/resistance) BEFORE it becomes a proposal — the model
# can flag DIVERGENCE / TRAP setups and reject them. Off by default: the
# gate changes trade behavior, enable deliberately.
AI_CONFLUENCE_GATE = os.environ.get("AI_CONFLUENCE_GATE", "false").lower() in ("1", "true", "yes")

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
    # ── Macro / commodities / forex ──
    # 2026-09-28 feed audit (live HTTP probes): removed the dead/stale feeds
    # that were starving FX sentiment — FXStreet & CryptoSlate 403, Reuters &
    # MarketWatch connection-refused, both Yahoo feeds 404, Kitco empty,
    # DailyFX 403. FXStreet's Aug-17 staleness was a direct cause of bad FX
    # entries. Replaced with working FX/macro feeds (all verified 200):
    ("CNBC Markets",    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("OilPrice.com",    "https://oilprice.com/rss/main"),
    ("Zero Hedge",      "https://feeds.feedburner.com/zerohedge/feed"),
    ("ForexLive",       "https://www.forexlive.com/feed/news"),
    ("FX Empire",       "https://www.fxempire.com/api/v1/en/articles/rss/news"),
    ("Investing Forex", "https://www.investing.com/rss/news_1.rss"),
    ("Investing Econ",  "https://www.investing.com/rss/news_14.rss"),
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
EXECUTION_BROKER = os.environ.get("EXECUTION_BROKER", "hybrid").strip().lower()

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

# Currency-concentration cap: one macro narrative ("dollar strength") can
# otherwise open a dozen USD-* positions in a single cycle — one reversal
# then hits them all simultaneously (seen live 2026-09-28: 9 USD-base longs
# from one news cycle). No single currency/index may appear in more than
# MAX_PER_CURRENCY open positions. 0 disables the cap.
MAX_PER_CURRENCY = int(os.environ.get("MAX_PER_CURRENCY", "3"))

# ── Loss guard (circuit breaker — see execution.loss_guard_status) ────
# Two hard caps on bleeding, checked at the top of every bot cycle BEFORE
# any new entry is placed:
#   MAX_DAILY_LOSS_PCT  — realized PnL today (closed orders, exit_time in
#       the current UTC day) may never exceed this % of start-of-day
#       equity. Breach → new entries held for the rest of the day; the
#       SL/TP monitor still closes open positions (no new exposure).
#   MAX_DRAWDOWN_PCT    — equity may never sit more than this % below its
#       all-time journal peak. Breach → new entries held until the drawdown
#       recovers above the threshold (or the peak moves down).
# Both are measured on the JOURNAL equity (paper) so they work with zero
# keys and any broker. 0 disables the guard (not recommended).
MAX_DAILY_LOSS_PCT = float(os.environ.get("MAX_DAILY_LOSS_PCT", "3.0"))
MAX_DRAWDOWN_PCT = float(os.environ.get("MAX_DRAWDOWN_PCT", "10.0"))

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

# Which generator feeds the bot's trade decisions (see bot.py).
#
#   "ai_news" (default since 2026-08-17): the Groq news analyst
#       (news_analyst.signal_for) reads the Tavily / Alpha Vantage / RSS
#       headline feed and returns structured per-asset sentiment signals —
#       the ONLY source the bot trades on. The chart-confluence AI agent
#       (agent 3) verifies each news signal against price structure before
#       it becomes a proposal when AI_CONFLUENCE_GATE is on.
#
#   Legacy engines (math_engine / strategy / hybrid) are RETIRED from the
#       trade path (they stayed HOLD-heavy and never agreed with source B).
#       They still power /api/signals for the dashboard (display only) and
#       the backtester, but are never used for trading. The setting is kept
#       so they can be re-enabled for dry-testing; LIVE runs with a legacy
#       engine require the two-source gate (see the kill-switch in bot.py).
SIGNAL_SOURCE_A = os.environ.get("SIGNAL_SOURCE_A", "ai_news").strip().lower()

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
# AI Confirmation Layer (ai_confirm.py — multi-factor gate)
# ----------------------------------------------------------------------
# The technical confirmation layer that validates/rejects AI news signals.
# NOT a trade generator — only referees. Each layer can be individually
# disabled. The master gate in ai_confirm.should_trade() combines all.
AI_CONFIRM_ENABLED = os.environ.get("AI_CONFIRM_ENABLED", "true").lower() in ("1", "true", "yes")
AI_CONFIRM_MIN_SCORE = int(os.environ.get("AI_CONFIRM_MIN_SCORE", "3"))  # 0-6
AI_REGIME_BLOCK_VOLATILE = os.environ.get("AI_REGIME_BLOCK_VOLATILE", "true").lower() in ("1", "true", "yes")

# HARD counter-trend veto (2026-09-28). Post-mortem of the real closed book
# (39 trades, excluding the retired-account event): entries WITH the 1h trend
# (price vs EMA50/200) won 71.4% (14 trades, +639.59); entries AGAINST it won
# 16.7% (12 trades, -378.90). The old code only *warned* on counter-trend
# (ADX>30) and traded anyway — every USD/CHF, NZD/USD, ADA/USDT style loss
# came through that hole. When true, a LONG in trending_down or a SHORT in
# trending_up (ADX>20) is vetoed outright by ai_confirm.should_trade.
AI_BLOCK_COUNTER_TREND = os.environ.get("AI_BLOCK_COUNTER_TREND", "true").lower() in ("1", "true", "yes")
# ADX must exceed this for the veto to fire (below this a 'trend' is chop).
AI_COUNTER_TREND_MIN_ADX = float(os.environ.get("AI_COUNTER_TREND_MIN_ADX", "20"))
AI_SENTIMENT_LONG = float(os.environ.get("AI_SENTIMENT_LONG", "0.50"))    # was 0.35 — extreme sentiment only
AI_SENTIMENT_SHORT = float(os.environ.get("AI_SENTIMENT_SHORT", "-0.50"))  # was -0.35
AI_CONFIDENCE_FLOOR = float(os.environ.get("AI_CONFIDENCE_FLOOR", "0.70")) # was 0.6 — calibrated floor
AI_MTF_ENABLED = os.environ.get("AI_MTF_ENABLED", "true").lower() in ("1", "true", "yes")

# Dual mode: "live" (real MT5 trades) or "test" (dashboard paper trades)
# Defaults to "test" for safety — user explicitly switches to "live" when confident.
AI_MODE = os.environ.get("AI_MODE", "test").strip().lower()

# ----------------------------------------------------------------------
# DXY Master Filter (dxy_filter.py — dollar index confirmation)
# ----------------------------------------------------------------------
# The DXY is the "invisible hand" of forex. 57.6% Euro weighting makes it
# a mirror of EUR/USD. Gold is priced in dollars. Never trade a USD pair
# without checking DXY. Based on FXNX institutional research.
DXY_FILTER_ENABLED = os.environ.get("DXY_FILTER_ENABLED", "true").lower() in ("1", "true", "yes")
DXY_SYMBOL = os.environ.get("DXY_SYMBOL", "DXY")  # Exness symbol for Dollar Index
DXY_BLOCK_ON_CONTRADICTION = os.environ.get("DXY_BLOCK_ON_CONTRADICTION", "true").lower() in ("1", "true", "yes")

# ----------------------------------------------------------------------
# Smart Money Concepts (smart_money.py — institutional order flow)
# ----------------------------------------------------------------------
# ICT-based detection of Order Blocks, Fair Value Gaps, Liquidity Sweeps,
# Break of Structure, Change of Character. Uses the smartmoneyconcepts
# Python library. Detects where institutions are placing orders.
SMC_ENABLED = os.environ.get("SMC_ENABLED", "true").lower() in ("1", "true", "yes")
SMC_MIN_SCORE = int(os.environ.get("SMC_MIN_SCORE", "2"))  # 0-4

# ----------------------------------------------------------------------
# Economic Calendar Guard (calendar_guard.py — news event filter)
# ----------------------------------------------------------------------
# Blocks trading around high-impact events (NFP, FOMC, CPI, GDP) when
# spreads widen and slippage spikes. Based on prop firm news filters.
CALENDAR_GUARD_ENABLED = os.environ.get("CALENDAR_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")
CALENDAR_HIGH_IMPACT_BLOCK_MIN = int(os.environ.get("CALENDAR_HIGH_IMPACT_BLOCK_MIN", "30"))
CALENDAR_MEDIUM_IMPACT_BLOCK_MIN = int(os.environ.get("CALENDAR_MEDIUM_IMPACT_BLOCK_MIN", "15"))
SPREAD_MULTIPLIER_WARN = float(os.environ.get("SPREAD_MULTIPLIER_WARN", "2.0"))
SPREAD_MULTIPLIER_BLOCK = float(os.environ.get("SPREAD_MULTIPLIER_BLOCK", "3.0"))

# ----------------------------------------------------------------------
# Session-Aware Trading (in ai_confirm.py)
# ----------------------------------------------------------------------
# Asian session = 30-50% smaller ranges, lower WR. London/NY overlap =
# prime window (highest liquidity, tightest spreads, best WR).
SESSION_AWARE_ENABLED = os.environ.get("SESSION_AWARE_ENABLED", "true").lower() in ("1", "true", "yes")
SESSION_BLOCK_ASIAN = os.environ.get("SESSION_BLOCK_ASIAN", "false").lower() in ("1", "true", "yes")
SESSION_OVERLAP_BOOST = float(os.environ.get("SESSION_OVERLAP_BOOST", "1.0"))
SESSION_ASIAN_SCALE = float(os.environ.get("SESSION_ASIAN_SCALE", "0.5"))

# ----------------------------------------------------------------------
# Tiered Circuit Breaker (circuit_breaker.py)
# ----------------------------------------------------------------------
# Step-down protocol: 5% DD → -25% risk, 10% DD → -50% risk, 15% DD → micro-lots.
# Step-up recovery ladder mirrors step-down in reverse.
# Based on FXNX institutional framework + Hour-Aware Adaptive Risk paper.
CIRCUIT_BREAKER_ENABLED = os.environ.get("CIRCUIT_BREAKER_ENABLED", "true").lower() in ("1", "true", "yes")
CB_LEVEL1_DD_PCT = float(os.environ.get("CB_LEVEL1_DD_PCT", "5.0"))
CB_LEVEL2_DD_PCT = float(os.environ.get("CB_LEVEL2_DD_PCT", "10.0"))
CB_LEVEL3_DD_PCT = float(os.environ.get("CB_LEVEL3_DD_PCT", "15.0"))
CB_CONSECUTIVE_LOSS_LIMIT = int(os.environ.get("CB_CONSECUTIVE_LOSS_LIMIT", "3"))
CB_STRATEGY_DECAY_WR = float(os.environ.get("CB_STRATEGY_DECAY_WR", "0.40"))
CB_DECAY_LOOKBACK_TRADES = int(os.environ.get("CB_DECAY_LOOKBACK_TRADES", "20"))

# ----------------------------------------------------------------------
# Trailing Stop-Loss + Dynamic Take-Profit (in execution.py)
# ----------------------------------------------------------------------
# Trailing SL activates after 1.0 ATR move in trade's favor. Breakeven
# stop at 0.5 ATR. Dynamic TP scales with volatility. Partial close at
# 50% of TP distance. Time-decay TP after 12 bars.
TRAILING_STOP_ENABLED = os.environ.get("TRAILING_STOP_ENABLED", "true").lower() in ("1", "true", "yes")
TRAIL_ACTIVATE_ATR = float(os.environ.get("TRAIL_ACTIVATE_ATR", "1.0"))
TRAIL_DISTANCE_ATR = float(os.environ.get("TRAIL_DISTANCE_ATR", "1.5"))
TRAIL_BREAKEVEN_ATR = float(os.environ.get("TRAIL_BREAKEVEN_ATR", "0.5"))
PARTIAL_TP_ENABLED = os.environ.get("PARTIAL_TP_ENABLED", "true").lower() in ("1", "true", "yes")
PARTIAL_TP_CLOSE_PCT = float(os.environ.get("PARTIAL_TP_CLOSE_PCT", "0.5"))
TP_MAX_HOLD_BARS = int(os.environ.get("TP_MAX_HOLD_BARS", "12"))

# ----------------------------------------------------------------------
# News Freshness Enforcement (in news_analyst.py)
# ----------------------------------------------------------------------
# Reject headlines older than NEWS_MAX_AGE_HOURS. If fewer than
# NEWS_MIN_FRESH_HEADLINES available, return HOLD.
NEWS_MAX_AGE_HOURS = int(os.environ.get("NEWS_MAX_AGE_HOURS", "4"))
NEWS_MIN_FRESH_HEADLINES = int(os.environ.get("NEWS_MIN_FRESH_HEADLINES", "3"))

# ── News noise filter (2026-09-30) ────────────────────────────────────
# One story reprinted 2-3x from a single outlet used to push the sentiment
# window over the trade threshold. Directional trades now need distinct
# outlets agreeing, or one very strong headline (NEWS_SINGLE_OUTLET_MIN_SCORE).
NEWS_NOISE_FILTER_ENABLED = os.environ.get(
    "NEWS_NOISE_FILTER_ENABLED", "true").lower() in ("1", "true", "yes")
NEWS_MIN_DISTINCT_OUTLETS = int(os.environ.get("NEWS_MIN_DISTINCT_OUTLETS", "2"))
NEWS_SINGLE_OUTLET_MIN_SCORE = float(os.environ.get(
    "NEWS_SINGLE_OUTLET_MIN_SCORE", "0.65"))

# ── Risk/reward floor ───────────────────────────────────────────────
# A trade whose TP is closer than its SL pays the spread and the odds at
# the same time. Entries now need TP distance >= MIN_RR * SL distance,
# with a hard cap so "take profit" can't sit a day's range away.
MIN_RR = float(os.environ.get("MIN_RR", "1.2"))
MAX_TP_PCT = float(os.environ.get("MAX_TP_PCT", "6.0"))

# A finished trade (closed/error) older than this frees its day-bucketed
# intent hash, so a fresh signal can re-enter the same symbol+direction.
# Filled/open positions always block (no stacking the same trade).
# 60 min: blocks same-hour churn, but lets a genuinely fresh signal (new
# LLM cycle, new levels) trade again within the day.
REENTRY_COOLDOWN_MIN = float(os.environ.get("REENTRY_COOLDOWN_MIN", "60"))

# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
# Auto-refresh the dashboard this often (seconds).
DASHBOARD_REFRESH_SECONDS = 10
