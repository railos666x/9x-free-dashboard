"""
Token Registry tests — verify symbol metadata, lookup, and cross-venue resolution.

Run:
    python tests/test_token_registry.py
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    errors = []

    # 1. Test imports
    print("1. Testing imports...")
    try:
        import token_registry as tr
        from token_registry import (
            AssetClass,
            Venue,
            SymbolInfo,
            get_symbol,
            get_symbol_by_base,
            get_symbols_by_venue,
            get_symbols_by_asset_class,
            get_all_symbols,
            get_active_symbols,
            get_crypto_symbols,
            get_forex_symbols,
            get_metals_symbols,
            get_energy_symbols,
            get_indices_symbols,
            get_etf_symbols,
            resolve_symbol,
            get_symbol_stats,
            export_registry_json,
        )
        print("   ✅ All token_registry exports import successfully")
    except Exception as e:
        print(f"   ❌ Import error: {e}")
        traceback.print_exc()
        errors.append(("import", e))

    # 2. Test enum completeness
    print("\n2. Testing enum definitions...")
    try:
        assert AssetClass.CRYPTO.value == "crypto"
        assert AssetClass.FOREX.value == "forex"
        assert AssetClass.METALS.value == "metals"
        assert AssetClass.ENERGY.value == "energy"
        assert AssetClass.INDICES.value == "indices"
        assert AssetClass.ETF.value == "etf"

        assert Venue.BINANCE.value == "binance"
        assert Venue.ALPACA.value == "alpaca"
        assert Venue.EXNESS_MT5.value == "exness_mt5"

        print("   ✅ Enums correct")
    except Exception as e:
        print(f"   ❌ Enum error: {e}")
        traceback.print_exc()
        errors.append(("enums", e))

    # 3. Test SymbolInfo dataclass & properties
    print("\n3. Testing SymbolInfo...")
    try:
        btc = SymbolInfo(
            symbol="BTC/USDT",
            base="BTC",
            quote="USDT",
            asset_class=AssetClass.CRYPTO,
            venue=Venue.BINANCE,
            tick_size=0.01,
            lot_size=0.00001,
            min_notional=10.0,
            leverage_max=125,
            sector="store_of_value",
            alt_symbols={"binance": "BTCUSDT", "alpaca": "BTC/USD"},
        )
        assert btc.symbol == "BTC/USDT"
        assert btc.base == "BTC"
        assert btc.quote == "USDT"
        assert btc.asset_class == AssetClass.CRYPTO
        assert btc.venue == Venue.BINANCE
        assert btc.tick_size == 0.01
        assert btc.lot_size == 0.00001
        assert btc.min_notional == 10.0
        assert btc.leverage_max == 125
        assert btc.sector == "store_of_value"
        assert btc.is_crypto is True
        assert btc.is_forex is False
        assert btc.is_macro is False
        assert btc.alt_symbols["binance"] == "BTCUSDT"
        d = btc.to_dict()
        assert d["symbol"] == "BTC/USDT"
        assert d["base"] == "BTC"
        assert d["asset_class"] == "crypto"
        print("   ✅ SymbolInfo works correctly")
    except Exception as e:
        print(f"   ❌ SymbolInfo error: {e}")
        traceback.print_exc()
        errors.append(("SymbolInfo", e))

    # 4. Test registry population & counts
    print("\n4. Testing registry population...")
    try:
        all_syms = get_all_symbols()
        active_syms = get_active_symbols()

        assert len(all_syms) == 160, f"Expected 160, got {len(all_syms)}"
        assert len(active_syms) == 160, "All symbols should be active"

        crypto_syms = get_crypto_symbols()
        forex_syms = get_forex_symbols()
        metals_syms = get_metals_symbols()
        energy_syms = get_energy_symbols()
        indices_syms = get_indices_symbols()
        etf_syms = get_etf_symbols()

        assert len(crypto_syms) == 77, f"Crypto: expected 77, got {len(crypto_syms)}"
        assert len(forex_syms) == 39, f"Forex: expected 39, got {len(forex_syms)}"
        assert len(metals_syms) == 5, f"Metals: expected 5, got {len(metals_syms)}"
        assert len(energy_syms) == 3, f"Energy: expected 3, got {len(energy_syms)}"
        assert len(indices_syms) == 14, f"Indices: expected 14, got {len(indices_syms)}"
        assert len(etf_syms) == 22, f"ETF: expected 22, got {len(etf_syms)}"

        assert (len(crypto_syms) + len(forex_syms) + len(metals_syms) +
                len(energy_syms) + len(indices_syms) + len(etf_syms)) == 160

        print("   ✅ Registry counts: 77 crypto, 39 forex, 5 metals, 3 energy, 14 indices, 22 ETF = 160 total")
    except Exception as e:
        print(f"   ❌ Registry population error: {e}")
        traceback.print_exc()
        errors.append(("population", e))

    # 5. Test symbol lookup by display symbol
    print("\n5. Testing get_symbol / get_symbol_by_base...")
    try:
        btc = get_symbol("BTC/USDT")
        assert btc is not None, "BTC/USDT not found"
        assert btc.symbol == "BTC/USDT"
        assert btc.base == "BTC"
        assert btc.quote == "USDT"
        assert btc.venue == Venue.BINANCE

        eth = get_symbol("eth/usdt")
        assert eth is not None, "eth/usdt (lowercase) not found"
        assert eth.symbol == "ETH/USDT"

        assert get_symbol("NONEXISTENT") is None

        btc_list = get_symbol_by_base("BTC")
        assert len(btc_list) >= 1
        assert all(s.base == "BTC" for s in btc_list)

        eth_list = get_symbol_by_base("ETH")
        assert len(eth_list) >= 1

        _ = get_symbol_by_base("XYZ")
        print("   ✅ get_symbol and get_symbol_by_base work")
    except Exception as e:
        print(f"   ❌ Lookup error: {e}")
        traceback.print_exc()
        errors.append(("lookup", e))

    # 6. Test venue filtering
    print("\n6. Testing get_symbols_by_venue...")
    try:
        binance = get_symbols_by_venue(Venue.BINANCE)
        alpaca = get_symbols_by_venue(Venue.ALPACA)
        exness = get_symbols_by_venue(Venue.EXNESS_MT5)

        assert len(binance) == 77, f"Binance: expected 77, got {len(binance)}"
        assert len(alpaca) == 22, f"Alpaca: expected 22, got {len(alpaca)}"
        assert len(exness) == 61, f"Exness: expected 61, got {len(exness)}"

        assert all(s.venue == Venue.BINANCE for s in binance)
        assert all(s.asset_class == AssetClass.CRYPTO for s in binance)

        assert all(s.venue == Venue.ALPACA for s in alpaca)
        assert all(s.asset_class == AssetClass.ETF for s in alpaca)

        exness_classes = {s.asset_class for s in exness}
        assert AssetClass.FOREX in exness_classes
        assert AssetClass.METALS in exness_classes
        assert AssetClass.ENERGY in exness_classes
        assert AssetClass.INDICES in exness_classes

        print("   ✅ Venue filtering correct (77 binance, 22 alpaca, 61 exness)")
    except Exception as e:
        print(f"   ❌ Venue filter error: {e}")
        traceback.print_exc()
        errors.append(("venue_filter", e))

    # 7. Test asset class filtering
    print("\n7. Testing get_symbols_by_asset_class...")
    try:
        for ac in AssetClass:
            if ac == AssetClass.UNKNOWN:
                continue
            syms = get_symbols_by_asset_class(ac)
            assert all(s.asset_class == ac for s in syms), f"Bad class for {ac}"
        print("   ✅ Asset class filtering correct")
    except Exception as e:
        print(f"   ❌ Asset class filter error: {e}")
        traceback.print_exc()
        errors.append(("asset_class_filter", e))

    # 8. Test resolve_symbol (display + alt_symbols)
    print("\n8. Testing resolve_symbol (cross-venue mapping)...")
    try:
        btc = resolve_symbol("BTC/USDT")
        assert btc is not None and btc.symbol == "BTC/USDT"

        btc_alt = resolve_symbol("BTCUSDT")
        assert btc_alt is not None and btc_alt.symbol == "BTC/USDT"
        assert btc_alt.alt_symbols["binance"] == "BTCUSDT"

        btc_alpaca = resolve_symbol("BTC/USD")
        assert btc_alpaca is not None and btc_alpaca.symbol == "BTC/USDT"
        assert btc_alpaca.alt_symbols.get("alpaca") == "BTC/USD"

        eur = resolve_symbol("EURUSD")
        assert eur is not None and eur.symbol == "EUR/USD"
        assert eur.alt_symbols["mt5"] == "EURUSD"

        btc_binance = resolve_symbol("BTC/USDT", venue=Venue.BINANCE)
        assert btc_binance is not None and btc_binance.venue == Venue.BINANCE

        btc_alpaca_venue = resolve_symbol("BTC/USDT", venue=Venue.ALPACA)
        assert btc_alpaca_venue is None, "Should not find BTC/USDT on Alpaca venue"

        spy = resolve_symbol("SPY", venue=Venue.ALPACA)
        assert spy is not None and spy.symbol == "SPY" and spy.venue == Venue.ALPACA

        assert resolve_symbol("NONEXISTENT") is None

        print("   ✅ resolve_symbol works for display + alt_symbols + venue filter")
    except Exception as e:
        print(f"   ❌ resolve_symbol error: {e}")
        traceback.print_exc()
        errors.append(("resolve_symbol", e))

    # 9. Test stats
    print("\n9. Testing get_symbol_stats...")
    try:
        stats = get_symbol_stats()
        assert stats["total"] == 160
        assert stats["active"] == 160
        assert stats["by_venue"]["binance"] == 77
        assert stats["by_venue"]["alpaca"] == 22
        assert stats["by_venue"]["exness_mt5"] == 61
        assert stats["by_asset_class"]["crypto"] == 77
        assert stats["by_asset_class"]["forex"] == 39
        assert stats["by_asset_class"]["metals"] == 5
        assert stats["by_asset_class"]["energy"] == 3
        assert stats["by_asset_class"]["indices"] == 14
        assert stats["by_asset_class"]["etf"] == 22
        assert "minor" in stats["by_sector"]
        assert "smart_contract_platform" in stats["by_sector"]
        print("   ✅ Stats correct")
    except Exception as e:
        print(f"   ❌ Stats error: {e}")
        traceback.print_exc()
        errors.append(("stats", e))

    # 10. Test export JSON round-trip
    print("\n10. Testing export_registry_json...")
    try:
        import json
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            json_str = export_registry_json(tmp_path)
            assert isinstance(json_str, str)
            assert len(json_str) > 1000

            data = json.loads(open(tmp_path).read())
            assert len(data["symbols"]) == 160
            assert data["stats"]["total"] == 160
            first = data["symbols"][0]
            assert "symbol" in first
            assert "base" in first
            assert "asset_class" in first
            assert "venue" in first
            print("   ✅ JSON export/import round-trip works")
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    except Exception as e:
        print(f"   ❌ Export error: {e}")
        traceback.print_exc()
        errors.append(("export", e))

    # 11. Self-validation: no duplicate display symbols
    print("\n11. Self-validation: duplicate symbol check...")
    try:
        all_syms = get_all_symbols()
        seen = set()
        dupes = []
        for s in all_syms:
            if s.symbol in seen:
                dupes.append(s.symbol)
            seen.add(s.symbol)
        assert not dupes, f"Duplicate display symbols: {dupes}"
        print("   ✅ No duplicate display symbols")
    except Exception as e:
        print(f"   ❌ Duplicate check error: {e}")
        traceback.print_exc()
        errors.append(("duplicates", e))

    # 12. Self-validation: no duplicate alt_symbols per venue
    print("\n12. Self-validation: duplicate alt_symbol check...")
    try:
        all_syms = get_all_symbols()
        for venue_key in ("binance", "alpaca", "mt5"):
            seen = set()
            dupes = []
            for s in all_syms:
                if venue_key in s.alt_symbols:
                    alt = s.alt_symbols[venue_key]
                    if alt in seen:
                        dupes.append(f"{venue_key}:{alt}")
                    seen.add(alt)
            assert not dupes, f"Duplicate alt_symbols for {venue_key}: {dupes}"
        print("   ✅ No duplicate alt_symbols per venue")
    except Exception as e:
        print(f"   ❌ Alt symbol duplicate check error: {e}")
        traceback.print_exc()
        errors.append(("alt_duplicates", e))

    # 13. Self-validation: config TRADING_UNIVERSE coverage
    print("\n13. Self-validation: config TRADING_UNIVERSE coverage...")
    try:
        import config
        missing = []
        for sym in config.TRADING_UNIVERSE:
            info = resolve_symbol(sym)
            if info is None:
                missing.append(sym)
        if missing:
            print(f"   ⚠️  {len(missing)} TRADING_UNIVERSE symbols not in registry: {missing[:10]}...")
        else:
            print("   ✅ All TRADING_UNIVERSE symbols covered")
    except Exception as e:
        print(f"   ❌ Config coverage check error: {e}")
        traceback.print_exc()
        errors.append(("config_coverage", e))

    # Summary
    print("\n" + "═" * 50)
    if errors:
        print(f"❌ {len(errors)} test group(s) failed:")
        for name, err in errors:
            print(f"   - {name}: {err}")
        sys.exit(1)
    else:
        print("✅ ALL TOKEN_REGISTRY TESTS PASSED")
        print("\nRegistry is healthy and ready for integration.")


if __name__ == "__main__":
    main()