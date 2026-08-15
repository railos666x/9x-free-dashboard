@echo off
title cryptoautomation - server launcher
cd /d "%~dp0\..\backend"
echo.
echo ============================================
echo   cryptoautomation - Detached Server Start
echo ============================================
echo.
echo   Starting server in a minimized window...
echo   Dashboard: http://localhost:8501
echo.
start "cryptoautomation-server" /min "%~dp0\..\.venv\Scripts\python.exe" server.py
echo   Server launched. Close this window.
echo ============================================
timeout /t 2 >nul
