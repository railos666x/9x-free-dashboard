@echo off
title cryptoautomation terminal
cd /d "%~dp0\.."
echo.
echo ============================================
echo   cryptoautomation - Starting Server
echo ============================================
echo.
echo   Dashboard: http://localhost:8501
echo   Terminal:  http://localhost:8501/terminal
echo   Signals:   http://localhost:8501/api/signals
echo   Backtest:  http://localhost:8501/api/backtest?symbol=BTC/USDT
echo.
echo   Press Ctrl+C to stop.
echo ============================================
echo.
start "" http://localhost:8501
.venv\Scripts\python.exe backend\server.py
