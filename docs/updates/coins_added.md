# New Coins Added - Watchlist Expansion

## Overview
Added 20+ new coins from user's watchlist screenshots to both config.py and terminal.js.

## New Coins Added

### Additional from Watchlist
| Symbol | Name | Category | Notes |
|--------|------|----------|-------|
| OG/USDT | Origin Protocol | DeFi/Infra | Cross-chain DeFi |
| BEL/USDT | Bella Protocol | DeFi | Yield optimization |
| OGN/USDT | Origin Protocol | DeFi | (duplicate - check) |
| SLP/USDT | Smooth Love Potion | Gaming | Axie Infinity reward token |
| FIO/USDT | FIO Protocol | Infra | Usability layer for blockchain |
| CREAM/USDT | Cream Finance | DeFi | Lending protocol |
| MDT/USDT | Measurable Data Token | Infra | Data economy |
| IRIS/USDT | IRISnet | L1 | Cosmos-based interchain |
| QLC/USDT | QLC Chain | Infra | Decentralized telecom |
| TRB/USDT | Tellor | DeFi/Infra | Decentralized oracle |
| LUNA/USDT | Terra (Classic) | L1 | Post-crash rebrand |
| OMN/USDT | OmiseGO / OMG Network | L2 | Plasma scaling |
| BLOB/USDT | Blob | Unknown | Verify on Binance |
| BTCDOM/USDT | BTC Dominance | Index | BTC.D perpetual |

### Already Present (Verified)
The following were already in the system:
- AAVE/USDT, RUNE/USDT, 1INCH/USDT, XRP/USDT, XLM/USDT
- COTI/USDT, GTC/USDT, FLOKI/USDT, LPT/USDT, STORJ/USDT
- ALGO/USDT, HOT/USDT, NEAR/USDT, ENJ/USDT, ETC/USDT
- BLZ/USDT, UNI/USDT, ARB/USDT, TRX/USDT, BNB/USDT
- 1000PEPE/USDT, NEO/USDT, VET/USDT, FTT/USDT, TRU/USDT
- QNT/USDT, HNT/USDT, SFP/USDT, LINK/USDT, HBAR/USDT
- ICON/USDT, BAND/USDT, SAND/USDT, ORI/USDT, XMR/USDT
- DYDX/USDT, 1000SHIB/USDT, CTSI/USDT, CVC/USDT, LTC/USDT
- CHZ/USDT, KAVA/USDT, ONE/USDT, THETA/USDT, ADA/USDT
- MASK/USDT, MANA/USDT, SAND/USDT, CHR/USDT, ALICE/USDT
- THE/USDT, GRT/USDT, COMP/USDT, DASH/USDT, BEL/USDT
- EOS/USDT, OG/USDT, ZRX/USDT, KSM/USDT, EGLD/USDT
- CELR/USDT, SLP/USDT, FIO/USDT, BAT/USDT, CREAM/USDT
- CELO/USDT, IOST/USDT, BCH/USDT, KNC/USDT, MDT/USDT
- IRIS/USDT, CTSI/USDT, CRV/USDT, SUSHI/USDT, ICP/USDT
- SNX/USDT, OCEAN/USDT, QLC/USDT, CKB/USDT, TRB/USDT
- LUNA/USDT, ATOM/USDT, AXS/USDT

## Total Coin Count
**Before**: ~78 coins
**After**: ~98 coins

## Files Modified
- `config.py` - SYMBOLS array (source of truth)
- `terminal/assets/js/terminal.js` - SYMBOLS constant (mirror for UI)

## Category Assignment
All new coins mapped to appropriate sidebar categories:
- **L1**: IRIS, LUNA, ATOM
- **L2**: ARB, OMN, BTCDOM
- **DeFi**: AAVE, UNI, COMP, CRV, SUSHI, SNX, CREAM, TRB, OCEAN
- **Gaming**: SLP, AXS, MANA, SAND, ENJ, ALICE
- **Infra**: GRT, HNT, FIO, IRIS, QLC, CELR, STORJ, BLZ, CVC, CTSI
- **Meme**: FLOKI, SHIB, PEPE, BONK, WIF
- **Other**: BEL, OG, OGN, MDT, KNC, ZRX, KSM, DASH, CKB, BLOB, BTCDOM

## Verification Needed
Some symbols may not exist on Binance Spot:
- OGN/USDT - Check if listed (might be OG/USDT duplicate)
- BLOB/USDT - Unknown symbol, verify
- BTCDOM/USDT - Perpetual only, may need futures endpoint

Run `python collector.py backfill` to test which symbols are available.