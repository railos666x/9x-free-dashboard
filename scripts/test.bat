@echo off
title cryptoautomation - System Test
cd /d "%~dp0\.."
echo.
echo ============================================
echo   cryptoautomation - System Test
echo ============================================
echo.
.venv\Scripts\python.exe tests\test_system.py
.venv\Scripts\python.exe tests\test_strategy.py
echo.
echo ============================================
echo.
pause
