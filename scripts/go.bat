@echo off
title cryptoautomation
cd /d "%~dp0\.."
color 0A
echo.
echo  ================================================
echo   cryptoautomation - One-Click Launch
echo  ================================================
echo.
echo  [1/3] Testing system...
echo.
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); import math_engine, factors, signals, backtester, strategy, risk; print('  All modules OK')" 2>nul
if errorlevel 1 (
    echo  [ERROR] Module import failed. Run test.bat for details.
    echo.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'backend'); import database; conn=database.connect(); n=database.count_candles(conn); print(f'  Database OK ({n} candles)')" 2>nul
if errorlevel 1 (
    echo  [ERROR] Database not found. Run: python backend/collector.py backfill
    echo.
    pause
    exit /b 1
)
echo.
echo  [2/3] Starting server...
echo.
start "" http://localhost:8501
.venv\Scripts\python.exe backend\server.py
