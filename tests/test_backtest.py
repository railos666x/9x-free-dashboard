import sys
# Force reimport
if 'math_engine' in sys.modules:
    del sys.modules['math_engine']
if 'backtester' in sys.modules:
    del sys.modules['backtester']
if 'signals' in sys.modules:
    del sys.modules['signals']
if 'factors' in sys.modules:
    del sys.modules['factors']

import database
import backtester

conn = database.connect()
database.init_db(conn)

for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']:
    print(f'\n########## {sym} ##########')
    result = backtester.run_backtest(conn, symbol=sym, interval='1h', limit=1000)
    backtester.print_report(result)