@echo off
title cryptoautomation - Backtest
cd /d "%~dp0\.."
echo.
echo ============================================
echo   cryptoautomation - Running Backtest
echo   (this may take 30-60 seconds)
echo ============================================
echo.
.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0, 'backend')
import database, backtester
conn = database.connect()
for sym in ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']:
    print(f'\n--- {sym} ---')
    r = backtester.run_backtest(conn, symbol=sym, interval='1h', limit=2000)
    backtester.print_report(r)
    print()
"
echo.
echo ============================================
echo   Done! Check results above.
echo ============================================
echo.
pause
