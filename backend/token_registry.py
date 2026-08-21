"""
cryptoautomation — Token Registry (Unified Symbol Metadata)

Central registry for all tradable instruments: crypto (Binance/Alpaca) + forex/metals/indices (Exness MT5).
Provides normalized metadata for portfolio construction, risk management, and execution.

Design principles (inspired by Freqtrade's pairlist + LEAN's SymbolProperties):
- Single source of truth for symbol metadata
- Asset class classification for portfolio-level risk parity (HRP)
- Exchange/venue mapping for execution routing
- Precision/tick size for order sizing
- Market hours / session awareness
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set

try:
    from . import config
except ImportError:
    import config


class AssetClass(str, Enum):
    """Asset class for portfolio construction (HRP clustering)."""
    CRYPTO = "crypto"
    FOREX = "forex"
    METALS = "metals"
    ENERGY = "energy"
    INDICES = "indices"
    ETF = "etf"
    UNKNOWN = "unknown"


class Venue(str, Enum):
    """Execution venue / exchange."""
    BINANCE = "binance"
    ALPACA = "alpaca"
    EXNESS_MT5 = "exness_mt5"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SymbolInfo:
    """
    Complete metadata for a tradable symbol.
    
    Attributes:
        symbol: Display symbol (e.g., "BTC/USDT", "EUR/USD")
        base: Base asset (e.g., "BTC", "EUR")
        quote: Quote asset (e.g., "USDT", "USD")
        asset_class: Asset class for portfolio clustering
        venue: Primary execution venue
        tick_size: Minimum price increment
        lot_size: Minimum order quantity (base asset)
        min_notional: Minimum order value (quote asset)
        leverage_max: Maximum leverage available
        market_hours: Dict with 'open', 'close' in UTC (24h format), None = 24/7
        is_active: Whether symbol is currently tradeable
        alt_symbols: Alternative symbol formats (e.g., Binance "BTCUSDT", MT5 "BTCUSD")
        sector: Crypto sector / forex category (for HRP correlation clustering)
    """
    symbol: str
    base: str
    quote: str
    asset_class: AssetClass
    venue: Venue
    tick_size: float
    lot_size: float
    min_notional: float
    leverage_max: int
    market_hours: Optional[Dict[str, str]] = None
    is_active: bool = True
    alt_symbols: Dict[str, str] = field(default_factory=dict)
    sector: str = ""
    
    @property
    def is_crypto(self) -> bool:
        return self.asset_class == AssetClass.CRYPTO
    
    @property
    def is_forex(self) -> bool:
        return self.asset_class == AssetClass.FOREX
    
    @property
    def is_macro(self) -> bool:
        return self.asset_class in (AssetClass.FOREX, AssetClass.METALS, 
                                     AssetClass.ENERGY, AssetClass.INDICES)
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "base": self.base,
            "quote": self.quote,
            "asset_class": self.asset_class.value,
            "venue": self.venue.value,
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "min_notional": self.min_notional,
            "leverage_max": self.leverage_max,
            "market_hours": self.market_hours,
            "is_active": self.is_active,
            "alt_symbols": self.alt_symbols,
            "sector": self.sector,
        }


# ──────────────────────────────────────────────────────────────────────────
# CRYPTO SYMBOLS (Binance Spot + Alpaca Crypto) - PART 1
# ──────────────────────────────────────────────────────────────────────────

_CRYPTO_SYMBOLS: List[SymbolInfo] = [
    # ── Major ──
    SymbolInfo("BTC/USDT", "BTC", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.00001, 10.0, 125, sector="store_of_value",
               alt_symbols={"binance": "BTCUSDT", "alpaca": "BTC/USD"}),
    SymbolInfo("ETH/USDT", "ETH", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.0001, 10.0, 100, sector="smart_contract_platform",
               alt_symbols={"binance": "ETHUSDT", "alpaca": "ETH/USD"}),
    SymbolInfo("SOL/USDT", "SOL", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 50, sector="smart_contract_platform",
               alt_symbols={"binance": "SOLUSDT", "alpaca": "SOL/USD"}),
    SymbolInfo("BNB/USDT", "BNB", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 50, sector="exchange_token",
               alt_symbols={"binance": "BNBUSDT"}),
    SymbolInfo("DOGE/USDT", "DOGE", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 1.0, 10.0, 25, sector="meme",
               alt_symbols={"binance": "DOGEUSDT"}),
    
    # ── Layer 1 ──
    SymbolInfo("ADA/USDT", "ADA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "ADAUSDT", "alpaca": "ADA/USD"}),
    SymbolInfo("XRP/USDT", "XRP", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="payments",
               alt_symbols={"binance": "XRPUSDT", "alpaca": "XRP/USD"}),
    SymbolInfo("AVAX/USDT", "AVAX", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 50, sector="smart_contract_platform",
               alt_symbols={"binance": "AVAXUSDT", "alpaca": "AVAX/USD"}),
    SymbolInfo("DOT/USDT", "DOT", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="interoperability",
               alt_symbols={"binance": "DOTUSDT", "alpaca": "DOT/USD"}),
    SymbolInfo("NEAR/USDT", "NEAR", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "NEARUSDT"}),
    SymbolInfo("ALGO/USDT", "ALGO", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "ALGOUSDT"}),
    SymbolInfo("ATOM/USDT", "ATOM", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 25, sector="interoperability",
               alt_symbols={"binance": "ATOMUSDT"}),
    SymbolInfo("TRX/USDT", "TRX", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 1.0, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "TRXUSDT"}),
    SymbolInfo("LTC/USDT", "LTC", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 50, sector="payments",
               alt_symbols={"binance": "LTCUSDT", "alpaca": "LTC/USD"}),
    SymbolInfo("BCH/USDT", "BCH", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 25, sector="payments",
               alt_symbols={"binance": "BCHUSDT"}),
    SymbolInfo("ETC/USDT", "ETC", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "ETCUSDT"}),
    SymbolInfo("ICP/USDT", "ICP", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "ICPUSDT"}),
    SymbolInfo("XLM/USDT", "XLM", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 1.0, 10.0, 25, sector="payments",
               alt_symbols={"binance": "XLMUSDT"}),
    SymbolInfo("FIL/USDT", "FIL", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 25, sector="storage",
               alt_symbols={"binance": "FILUSDT"}),
    SymbolInfo("HBAR/USDT", "HBAR", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 1.0, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "HBARUSDT"}),
    SymbolInfo("EGLD/USDT", "EGLD", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "EGLDUSDT"}),
    SymbolInfo("THETA/USDT", "THETA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="media",
               alt_symbols={"binance": "THETAUSDT"}),
    SymbolInfo("POL/USDT", "POL", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="scaling",
               alt_symbols={"binance": "POLUSDT"}),
    
    # ── Layer 2 ──
    SymbolInfo("ARB/USDT", "ARB", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="scaling",
               alt_symbols={"binance": "ARBUSDT"}),
    SymbolInfo("OP/USDT", "OP", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="scaling",
               alt_symbols={"binance": "OPUSDT"}),
    
    # ── DeFi ──
    SymbolInfo("AAVE/USDT", "AAVE", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 25, sector="lending",
               alt_symbols={"binance": "AAVEUSDT"}),
    SymbolInfo("UNI/USDT", "UNI", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="dex",
               alt_symbols={"binance": "UNIUSDT"}),
    SymbolInfo("LINK/USDT", "LINK", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="oracle",
               alt_symbols={"binance": "LINKUSDT", "alpaca": "LINK/USD"}),
    SymbolInfo("COMP/USDT", "COMP", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 25, sector="lending",
               alt_symbols={"binance": "COMPUSDT"}),
    SymbolInfo("CRV/USDT", "CRV", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="dex",
               alt_symbols={"binance": "CRVUSDT"}),
    SymbolInfo("SUSHI/USDT", "SUSHI", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="dex",
               alt_symbols={"binance": "SUSHIUSDT"}),
    SymbolInfo("SNX/USDT", "SNX", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="derivatives",
               alt_symbols={"binance": "SNXUSDT"}),
    SymbolInfo("YFI/USDT", "YFI", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.001, 10.0, 25, sector="yield",
               alt_symbols={"binance": "YFIUSDT"}),
    SymbolInfo("1INCH/USDT", "1INCH", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="dex",
               alt_symbols={"binance": "1INCHUSDT"}),
    SymbolInfo("BAND/USDT", "BAND", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="oracle",
               alt_symbols={"binance": "BANDUSDT"}),
    SymbolInfo("KNC/USDT", "KNC", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="dex",
               alt_symbols={"binance": "KNCUSDT"}),
    SymbolInfo("ZRX/USDT", "ZRX", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="dex",
               alt_symbols={"binance": "ZRXUSDT"}),
    SymbolInfo("RUNE/USDT", "RUNE", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="dex",
               alt_symbols={"binance": "RUNEUSDT"}),
    SymbolInfo("LPT/USDT", "LPT", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="infrastructure",
               alt_symbols={"binance": "LPTUSDT"}),
    SymbolInfo("DYDX/USDT", "DYDX", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="derivatives",
               alt_symbols={"binance": "DYDXUSDT"}),
    SymbolInfo("TRB/USDT", "TRB", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 25, sector="oracle",
               alt_symbols={"binance": "TRBUSDT"}),
    
    # ── Meme ──
    SymbolInfo("SHIB/USDT", "SHIB", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00000001, 1000.0, 10.0, 25, sector="meme",
               alt_symbols={"binance": "SHIBUSDT"}),
    SymbolInfo("FLOKI/USDT", "FLOKI", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0000001, 100.0, 10.0, 25, sector="meme",
               alt_symbols={"binance": "FLOKIUSDT"}),
    SymbolInfo("PEPE/USDT", "PEPE", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00000001, 1000.0, 10.0, 25, sector="meme",
               alt_symbols={"binance": "PEPEUSDT"}),
    SymbolInfo("WIF/USDT", "WIF", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 1.0, 10.0, 25, sector="meme",
               alt_symbols={"binance": "WIFUSDT"}),
    SymbolInfo("BONK/USDT", "BONK", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00000001, 1000.0, 10.0, 25, sector="meme",
               alt_symbols={"binance": "BONKUSDT"}),
    
    # ── Gaming / Metaverse ──
    SymbolInfo("SAND/USDT", "SAND", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "SANDUSDT"}),
    SymbolInfo("MANA/USDT", "MANA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "MANAUSDT"}),
    SymbolInfo("AXS/USDT", "AXS", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "AXSUSDT"}),
    SymbolInfo("ENJ/USDT", "ENJ", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "ENJUSDT"}),
    SymbolInfo("GALA/USDT", "GALA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "GALAUSDT"}),
    SymbolInfo("ALICE/USDT", "ALICE", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "ALICEUSDT"}),
    SymbolInfo("CHZ/USDT", "CHZ", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="sports",
               alt_symbols={"binance": "CHZUSDT"}),
    SymbolInfo("BAT/USDT", "BAT", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="advertising",
               alt_symbols={"binance": "BATUSDT"}),
    
    # ── Infrastructure / Storage ──
    SymbolInfo("GRT/USDT", "GRT", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="infrastructure",
               alt_symbols={"binance": "GRTUSDT"}),
    SymbolInfo("NEO/USDT", "NEO", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.01, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "NEOUSDT"}),
    SymbolInfo("VET/USDT", "VET", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="supply_chain",
               alt_symbols={"binance": "VETUSDT"}),
    SymbolInfo("IOTA/USDT", "IOTA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="iot",
               alt_symbols={"binance": "IOTAUSDT"}),
    SymbolInfo("STORJ/USDT", "STORJ", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="storage",
               alt_symbols={"binance": "STORJUSDT"}),
    SymbolInfo("CVC/USDT", "CVC", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="identity",
               alt_symbols={"binance": "CVCUSDT"}),
    SymbolInfo("CTSI/USDT", "CTSI", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="infrastructure",
               alt_symbols={"binance": "CTSIUSDT"}),
    SymbolInfo("CELR/USDT", "CELR", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="scaling",
               alt_symbols={"binance": "CELRUSDT"}),
    SymbolInfo("KAVA/USDT", "KAVA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="defi",
               alt_symbols={"binance": "KAVAUSDT"}),
    SymbolInfo("ONE/USDT", "ONE", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "ONEUSDT"}),
    SymbolInfo("MASK/USDT", "MASK", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="social",
               alt_symbols={"binance": "MASKUSDT"}),
    
    # ── Other ──
    SymbolInfo("DASH/USDT", "DASH", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 25, sector="payments",
               alt_symbols={"binance": "DASHUSDT"}),
    SymbolInfo("KSM/USDT", "KSM", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.01, 0.01, 10.0, 25, sector="interoperability",
               alt_symbols={"binance": "KSMUSDT"}),
    SymbolInfo("SFP/USDT", "SFP", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="defi",
               alt_symbols={"binance": "SFPUSDT"}),
    SymbolInfo("COTI/USDT", "COTI", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="payments",
               alt_symbols={"binance": "COTIUSDT"}),
    SymbolInfo("CELO/USDT", "CELO", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="mobile",
               alt_symbols={"binance": "CELOUSDT"}),
    SymbolInfo("IOST/USDT", "IOST", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="smart_contract_platform",
               alt_symbols={"binance": "IOSTUSDT"}),
    SymbolInfo("CKB/USDT", "CKB", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="infrastructure",
               alt_symbols={"binance": "CKBUSDT"}),
    
    # ── Additional from watchlist ──
    SymbolInfo("OG/USDT", "OG", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="sports",
               alt_symbols={"binance": "OGUSDT"}),
    SymbolInfo("BEL/USDT", "BEL", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.001, 0.1, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "BELUSDT"}),
    SymbolInfo("OGN/USDT", "OGN", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="nft",
               alt_symbols={"binance": "OGNUSDT"}),
    SymbolInfo("SLP/USDT", "SLP", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.00001, 10.0, 10.0, 25, sector="gaming",
               alt_symbols={"binance": "SLPUSDT"}),
    SymbolInfo("LUNA/USDT", "LUNA", "USDT", AssetClass.CRYPTO, Venue.BINANCE,
               0.0001, 1.0, 10.0, 25, sector="stablecoin",
               alt_symbols={"binance": "LUNAUSDT"}),
]


# ──────────────────────────────────────────────────────────────────────────
# FOREX SYMBOLS (Exness MT5) - PART 1: Majors
# ──────────────────────────────────────────────────────────────────────────

_FOREX_SYMBOLS: List[SymbolInfo] = [
    # ── Majors ──
    SymbolInfo("EUR/USD", "EUR", "USD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "EURUSD"}),
    SymbolInfo("GBP/USD", "GBP", "USD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "GBPUSD"}),
    SymbolInfo("USD/JPY", "USD", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "USDJPY"}),
    SymbolInfo("USD/CHF", "USD", "CHF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "USDCHF"}),
    SymbolInfo("AUD/USD", "AUD", "USD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "AUDUSD"}),
    SymbolInfo("USD/CAD", "USD", "CAD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "USDCAD"}),
    SymbolInfo("NZD/USD", "NZD", "USD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="major", alt_symbols={"mt5": "NZDUSD"}),
    
    # ── Minors ──
    SymbolInfo("EUR/GBP", "EUR", "GBP", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "EURGBP"}),
    SymbolInfo("EUR/JPY", "EUR", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "EURJPY"}),
    SymbolInfo("GBP/JPY", "GBP", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "GBPJPY"}),
    SymbolInfo("CHF/JPY", "CHF", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "CHFJPY"}),
    SymbolInfo("AUD/JPY", "AUD", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "AUDJPY"}),
    SymbolInfo("CAD/JPY", "CAD", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "CADJPY"}),
    SymbolInfo("NZD/JPY", "NZD", "JPY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "NZDJPY"}),
    SymbolInfo("EUR/AUD", "EUR", "AUD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "EURAUD"}),
    SymbolInfo("EUR/CHF", "EUR", "CHF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "EURCHF"}),
    SymbolInfo("EUR/CAD", "EUR", "CAD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "EURCAD"}),
    SymbolInfo("GBP/AUD", "GBP", "AUD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "GBPAUD"}),
    SymbolInfo("GBP/CHF", "GBP", "CHF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "GBPCHF"}),
    SymbolInfo("GBP/CAD", "GBP", "CAD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "GBPCAD"}),
    SymbolInfo("AUD/CAD", "AUD", "CAD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "AUDCAD"}),
    SymbolInfo("AUD/CHF", "AUD", "CHF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "AUDCHF"}),
    SymbolInfo("AUD/NZD", "AUD", "NZD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "AUDNZD"}),
    SymbolInfo("CAD/CHF", "CAD", "CHF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "CADCHF"}),
    SymbolInfo("NZD/CHF", "NZD", "CHF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "NZDCHF"}),
    SymbolInfo("NZD/CAD", "NZD", "CAD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 30,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="minor", alt_symbols={"mt5": "NZDCAD"}),
    
    # ── Exotics ──
    SymbolInfo("USD/TRY", "USD", "TRY", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDTRY"}),
    SymbolInfo("USD/ZAR", "USD", "ZAR", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDZAR"}),
    SymbolInfo("USD/MXN", "USD", "MXN", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDMXN"}),
    SymbolInfo("USD/SGD", "USD", "SGD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDSGD"}),
    SymbolInfo("USD/HKD", "USD", "HKD", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDHKD"}),
    SymbolInfo("USD/NOK", "USD", "NOK", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDNOK"}),
    SymbolInfo("USD/SEK", "USD", "SEK", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDSEK"}),
    SymbolInfo("USD/DKK", "USD", "DKK", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDDKK"}),
    SymbolInfo("USD/PLN", "USD", "PLN", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDPLN"}),
    SymbolInfo("USD/CZK", "USD", "CZK", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDCZK"}),
    SymbolInfo("USD/HUF", "USD", "HUF", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDHUF"}),
    SymbolInfo("USD/ILS", "USD", "ILS", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDILS"}),
    SymbolInfo("USD/RON", "USD", "RON", AssetClass.FOREX, Venue.EXNESS_MT5,
               0.00001, 0.01, 1000.0, 20,
               market_hours={"open": "22:00", "close": "21:00"},
               sector="exotic", alt_symbols={"mt5": "USDRON"}),
]


# ──────────────────────────────────────────────────────────────────────────
# METALS SYMBOLS (Exness MT5)
# ──────────────────────────────────────────────────────────────────────────

_METALS_SYMBOLS: List[SymbolInfo] = [
    SymbolInfo("XAU/USD", "XAU", "USD", AssetClass.METALS, Venue.EXNESS_MT5,
               0.01, 0.01, 1000.0, 50,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="precious", alt_symbols={"mt5": "XAUUSD"}),
    SymbolInfo("XAG/USD", "XAG", "USD", AssetClass.METALS, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 50,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="precious", alt_symbols={"mt5": "XAGUSD"}),
    SymbolInfo("XPT/USD", "XPT", "USD", AssetClass.METALS, Venue.EXNESS_MT5,
               0.01, 0.01, 1000.0, 20,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="precious", alt_symbols={"mt5": "XPTUSD"}),
    SymbolInfo("XPD/USD", "XPD", "USD", AssetClass.METALS, Venue.EXNESS_MT5,
               0.01, 0.01, 1000.0, 20,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="precious", alt_symbols={"mt5": "XPDUSD"}),
    SymbolInfo("COPPER/USD", "COPPER", "USD", AssetClass.METALS, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 20,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="industrial", alt_symbols={"mt5": "COPPER"}),
]


# ──────────────────────────────────────────────────────────────────────────
# ENERGY SYMBOLS (Exness MT5)
# ──────────────────────────────────────────────────────────────────────────

_ENERGY_SYMBOLS: List[SymbolInfo] = [
    SymbolInfo("XTI/USD", "XTI", "USD", AssetClass.ENERGY, Venue.EXNESS_MT5,
               0.01, 0.01, 1000.0, 20,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="oil", alt_symbols={"mt5": "XTIUSD"}),
    SymbolInfo("XBR/USD", "XBR", "USD", AssetClass.ENERGY, Venue.EXNESS_MT5,
               0.01, 0.01, 1000.0, 20,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="oil", alt_symbols={"mt5": "XBRUSD"}),
    SymbolInfo("XNG/USD", "XNG", "USD", AssetClass.ENERGY, Venue.EXNESS_MT5,
               0.001, 0.01, 1000.0, 20,
               market_hours={"open": "23:00", "close": "22:00"},
               sector="gas", alt_symbols={"mt5": "XNGUSD"}),
]


# ──────────────────────────────────────────────────────────────────────────
# INDICES SYMBOLS (Exness MT5)
# ──────────────────────────────────────────────────────────────────────────

_INDICES_SYMBOLS: List[SymbolInfo] = [
    # US Indices
    SymbolInfo("US30/USD", "US30", "USD", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_equity", alt_symbols={"mt5": "US30"}),
    SymbolInfo("US500/USD", "US500", "USD", AssetClass.INDICES, Venue.EXNESS_MT5,
               0.1, 0.01, 1000.0, 20,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_equity", alt_symbols={"mt5": "US500"}),
    SymbolInfo("USTEC/USD", "USTEC", "USD", AssetClass.INDICES, Venue.EXNESS_MT5,
               0.1, 0.01, 1000.0, 20,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_equity", alt_symbols={"mt5": "USTEC"}),
    SymbolInfo("US2000/USD", "US2000", "USD", AssetClass.INDICES, Venue.EXNESS_MT5,
               0.1, 0.01, 1000.0, 20,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_equity", alt_symbols={"mt5": "US2000"}),
    
    # European Indices
    SymbolInfo("DE40/EUR", "DE40", "EUR", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "08:00", "close": "22:00"},
               sector="eu_equity", alt_symbols={"mt5": "DE40"}),
    SymbolInfo("FR40/EUR", "FR40", "EUR", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "08:00", "close": "22:00"},
               sector="eu_equity", alt_symbols={"mt5": "FR40"}),
    SymbolInfo("EU50/EUR", "EU50", "EUR", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "08:00", "close": "22:00"},
               sector="eu_equity", alt_symbols={"mt5": "EU50"}),
    SymbolInfo("UK100/GBP", "UK100", "GBP", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "08:00", "close": "22:00"},
               sector="eu_equity", alt_symbols={"mt5": "UK100"}),
    SymbolInfo("ES35/EUR", "ES35", "EUR", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "08:00", "close": "22:00"},
               sector="eu_equity", alt_symbols={"mt5": "ES35"}),
    SymbolInfo("NL25/EUR", "NL25", "EUR", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "08:00", "close": "22:00"},
               sector="eu_equity", alt_symbols={"mt5": "NL25"}),
    
    # Asian Indices
    SymbolInfo("JP225/JPY", "JP225", "JPY", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "00:00", "close": "06:00"},
               sector="asia_equity", alt_symbols={"mt5": "JP225"}),
    SymbolInfo("HK50/HKD", "HK50", "HKD", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "01:15", "close": "08:00"},
               sector="asia_equity", alt_symbols={"mt5": "HK50"}),
    SymbolInfo("CN50/USD", "CN50", "USD", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "01:30", "close": "08:00"},
               sector="asia_equity", alt_symbols={"mt5": "CN50"}),
    SymbolInfo("AU200/AUD", "AU200", "AUD", AssetClass.INDICES, Venue.EXNESS_MT5,
               1.0, 0.01, 1000.0, 20,
               market_hours={"open": "22:50", "close": "06:30"},
               sector="asia_equity", alt_symbols={"mt5": "AU200"}),
]


# ──────────────────────────────────────────────────────────────────────────
# ETF SYMBOLS (Alpaca)
# ──────────────────────────────────────────────────────────────────────────

_ETF_SYMBOLS: List[SymbolInfo] = [
    SymbolInfo("SPY", "SPY", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_broad_market", alt_symbols={"alpaca": "SPY"}),
    SymbolInfo("QQQ", "QQQ", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_tech", alt_symbols={"alpaca": "QQQ"}),
    SymbolInfo("IWM", "IWM", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_small_cap", alt_symbols={"alpaca": "IWM"}),
    SymbolInfo("DIA", "DIA", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_blue_chip", alt_symbols={"alpaca": "DIA"}),
    SymbolInfo("VTI", "VTI", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="us_total_market", alt_symbols={"alpaca": "VTI"}),
    SymbolInfo("VEA", "VEA", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="developed_intl", alt_symbols={"alpaca": "VEA"}),
    SymbolInfo("VWO", "VWO", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="emerging_markets", alt_symbols={"alpaca": "VWO"}),
    SymbolInfo("GLD", "GLD", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="gold", alt_symbols={"alpaca": "GLD"}),
    SymbolInfo("SLV", "SLV", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="silver", alt_symbols={"alpaca": "SLV"}),
    SymbolInfo("USO", "USO", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="oil", alt_symbols={"alpaca": "USO"}),
    SymbolInfo("TLT", "TLT", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="long_term_bonds", alt_symbols={"alpaca": "TLT"}),
    SymbolInfo("HYG", "HYG", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="high_yield_bonds", alt_symbols={"alpaca": "HYG"}),
    SymbolInfo("XLF", "XLF", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="financials", alt_symbols={"alpaca": "XLF"}),
    SymbolInfo("XLK", "XLK", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="technology", alt_symbols={"alpaca": "XLK"}),
    SymbolInfo("XLE", "XLE", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="energy", alt_symbols={"alpaca": "XLE"}),
    SymbolInfo("XLV", "XLV", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="healthcare", alt_symbols={"alpaca": "XLV"}),
    SymbolInfo("XLI", "XLI", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="industrials", alt_symbols={"alpaca": "XLI"}),
    SymbolInfo("XLP", "XLP", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="consumer_staples", alt_symbols={"alpaca": "XLP"}),
    SymbolInfo("XLY", "XLY", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="consumer_discretionary", alt_symbols={"alpaca": "XLY"}),
    SymbolInfo("XLB", "XLB", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="materials", alt_symbols={"alpaca": "XLB"}),
    SymbolInfo("XLRE", "XLRE", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="real_estate", alt_symbols={"alpaca": "XLRE"}),
    SymbolInfo("XLU", "XLU", "USD", AssetClass.ETF, Venue.ALPACA,
               0.01, 1.0, 100.0, 2,
               market_hours={"open": "14:30", "close": "21:00"},
               sector="utilities", alt_symbols={"alpaca": "XLU"}),
]


# ──────────────────────────────────────────────────────────────────────────
# REGISTRY FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────

# Combined registry
_ALL_SYMBOLS: List[SymbolInfo] = (
    _CRYPTO_SYMBOLS + _FOREX_SYMBOLS + _METALS_SYMBOLS + _ENERGY_SYMBOLS +
    _INDICES_SYMBOLS + _ETF_SYMBOLS
)

# Symbol lookup by display symbol (e.g., "BTC/USDT")
_SYMBOL_BY_SYMBOL: Dict[str, SymbolInfo] = {s.symbol: s for s in _ALL_SYMBOLS}

# Symbol lookup by base asset (e.g., "BTC")
_SYMBOL_BY_BASE: Dict[str, List[SymbolInfo]] = {}
for s in _ALL_SYMBOLS:
    _SYMBOL_BY_BASE.setdefault(s.base.upper(), []).append(s)

# Symbol lookup by venue
_SYMBOL_BY_VENUE: Dict[Venue, List[SymbolInfo]] = {}
for s in _ALL_SYMBOLS:
    _SYMBOL_BY_VENUE.setdefault(s.venue, []).append(s)

# Symbol lookup by asset class
_SYMBOL_BY_ASSET_CLASS: Dict[AssetClass, List[SymbolInfo]] = {}
for s in _ALL_SYMBOLS:
    _SYMBOL_BY_ASSET_CLASS.setdefault(s.asset_class, []).append(s)


def get_symbol(symbol: str) -> Optional[SymbolInfo]:
    """Get symbol info by display symbol (e.g., 'BTC/USDT')."""
    return _SYMBOL_BY_SYMBOL.get(symbol.upper())


def get_symbol_by_base(base: str) -> List[SymbolInfo]:
    """Get all symbols for a base asset (e.g., 'BTC')."""
    return _SYMBOL_BY_BASE.get(base.upper(), [])


def get_symbols_by_venue(venue: Venue) -> List[SymbolInfo]:
    """Get all symbols for a venue."""
    return _SYMBOL_BY_VENUE.get(venue, [])


def get_symbols_by_asset_class(asset_class: AssetClass) -> List[SymbolInfo]:
    """Get all symbols for an asset class."""
    return _SYMBOL_BY_ASSET_CLASS.get(asset_class, [])


def get_all_symbols() -> List[SymbolInfo]:
    """Get all registered symbols."""
    return _ALL_SYMBOLS.copy()


def get_active_symbols() -> List[SymbolInfo]:
    """Get all active symbols."""
    return [s for s in _ALL_SYMBOLS if s.is_active]


def get_crypto_symbols() -> List[SymbolInfo]:
    """Get all crypto symbols."""
    return _CRYPTO_SYMBOLS.copy()


def get_forex_symbols() -> List[SymbolInfo]:
    """Get all forex symbols."""
    return _FOREX_SYMBOLS.copy()


def get_metals_symbols() -> List[SymbolInfo]:
    """Get all metals symbols."""
    return _METALS_SYMBOLS.copy()


def get_energy_symbols() -> List[SymbolInfo]:
    """Get all energy symbols."""
    return _ENERGY_SYMBOLS.copy()


def get_indices_symbols() -> List[SymbolInfo]:
    """Get all indices symbols."""
    return _INDICES_SYMBOLS.copy()


def get_etf_symbols() -> List[SymbolInfo]:
    """Get all ETF symbols."""
    return _ETF_SYMBOLS.copy()


def get_symbols_for_venue(venue: Venue) -> List[SymbolInfo]:
    """Get symbols for a specific venue (alias for get_symbols_by_venue)."""
    return get_symbols_by_venue(venue)


def get_symbols_for_asset_class(asset_class: AssetClass) -> List[SymbolInfo]:
    """Get symbols for a specific asset class (alias for get_symbols_by_asset_class)."""
    return get_symbols_by_asset_class(asset_class)


def resolve_symbol(symbol: str, venue: Optional[Venue] = None) -> Optional[SymbolInfo]:
    """
    Resolve a symbol to its SymbolInfo, optionally filtering by venue.
    
    Args:
        symbol: Display symbol (e.g., "BTC/USDT") or alt symbol (e.g., "BTCUSDT")
        venue: Optional venue to filter by
    
    Returns:
        SymbolInfo if found, None otherwise
    """
    # Try exact match first
    info = get_symbol(symbol)
    if info and (venue is None or info.venue == venue):
        return info
    
    # Try alt_symbols lookup
    for s in _ALL_SYMBOLS:
        if venue and s.venue != venue:
            continue
        for v_symbol in s.alt_symbols.values():
            if v_symbol.upper() == symbol.upper():
                return s
    
    return None


def get_symbol_stats() -> dict:
    """Get registry statistics."""
    stats = {
        "total": len(_ALL_SYMBOLS),
        "active": len([s for s in _ALL_SYMBOLS if s.is_active]),
        "by_venue": {},
        "by_asset_class": {},
        "by_sector": {},
    }
    
    for venue in Venue:
        symbols = get_symbols_by_venue(venue)
        if symbols:
            stats["by_venue"][venue.value] = len(symbols)
    
    for asset_class in AssetClass:
        symbols = get_symbols_by_asset_class(asset_class)
        if symbols:
            stats["by_asset_class"][asset_class.value] = len(symbols)
    
    for s in _ALL_SYMBOLS:
        if s.sector:
            stats["by_sector"][s.sector] = stats["by_sector"].get(s.sector, 0) + 1
    
    return stats


def export_registry_json(path: Optional[str] = None) -> str:
    """Export full registry to JSON string or file."""
    data = {
        "symbols": [s.to_dict() for s in _ALL_SYMBOLS],
        "stats": get_symbol_stats(),
    }
    json_str = json.dumps(data, indent=2)
    if path:
        Path(path).write_text(json_str)
    return json_str


# ──────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--export":
        out_path = sys.argv[2] if len(sys.argv) > 2 else "symbol_registry.json"
        export_registry_json(out_path)
        print(f"Registry exported to {out_path}")
    else:
        stats = get_symbol_stats()
        print("=== Symbol Registry Stats ===")
        print(f"Total symbols: {stats['total']}")
        print(f"Active symbols: {stats['active']}")
        print("\nBy Venue:")
        for venue, count in stats["by_venue"].items():
            print(f"  {venue}: {count}")
        print("\nBy Asset Class:")
        for ac, count in stats["by_asset_class"].items():
            print(f"  {ac}: {count}")
        print("\nBy Sector (top 20):")
        for sector, count in sorted(stats["by_sector"].items(), key=lambda x: -x[1])[:20]:
            print(f"  {sector}: {count}")