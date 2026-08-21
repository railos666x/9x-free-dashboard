"""Debug MT5 order structure."""
import MetaTrader5 as mt5
import sys

# Initialize MT5
if not mt5.initialize():
    print(f"MT5 init failed: {mt5.last_error()}")
    sys.exit(1)

print("MT5 Connected!")
print(f"Version: {mt5.version()}")
print(f"Account: {mt5.account_info().login if mt5.account_info() else 'N/A'}")
print(f"Server: {mt5.account_info().server if mt5.account_info() else 'N/A'}")

# Check XAUUSD symbol info
sym = 'XAUUSD'
if not mt5.symbol_select(sym, True):
    print(f"Failed to select {sym}")
    sys.exit(1)

info = mt5.symbol_info(sym)
print(f"\n=== {sym} Symbol Info ===")
print(f"Trade mode: {info.trade_mode}")
print(f"Trade contract size: {info.trade_contract_size}")
print(f"Volume min: {info.volume_min}")
print(f"Volume max: {info.volume_max}")
print(f"Volume step: {info.volume_step}")
print(f"Filling mode: {info.filling_mode}")
print(f"Order mode: {info.order_mode}")

# Check what filling modes are supported
print(f"\nFilling modes:")
print(f"  FOK (1): {bool(info.filling_mode & 1)}")
print(f"  IOC (2): {bool(info.filling_mode & 2)}")

# Get current price
tick = mt5.symbol_info_tick(sym)
print(f"\nCurrent prices:")
print(f"  Bid: {tick.bid}")
print(f"  Ask: {tick.ask}")

# Try different order types
print(f"\n=== Testing Order Types ===")

# Test 1: Market order with FOK
print("\n1. Testing MARKET order with FOK...")
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": sym,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_BUY,
    "price": tick.ask,
    "deviation": 30,
    "magic": 20260818,
    "comment": "test_fok",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_FOK,
}
print(f"Request: {request}")
result = mt5.order_send(request)
print(f"Result: retcode={result.retcode}, comment={result.comment}")
if result.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"Error: {mt5.last_error()}")

# Test 2: Market order with IOC
print("\n2. Testing MARKET order with IOC...")
request["type_filling"] = mt5.ORDER_FILLING_IOC
request["comment"] = "test_ioc"
result = mt5.order_send(request)
print(f"Result: retcode={result.retcode}, comment={result.comment}")
if result.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"Error: {mt5.last_error()}")

# Test 3: Market order without price (let MT5 fill)
print("\n3. Testing MARKET order without price...")
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": sym,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_BUY,
    "deviation": 30,
    "magic": 20260818,
    "comment": "test_noprice",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_FOK,
}
print(f"Request: {request}")
result = mt5.order_send(request)
print(f"Result: retcode={result.retcode}, comment={result.comment}")
if result.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"Error: {mt5.last_error()}")

mt5.shutdown()
