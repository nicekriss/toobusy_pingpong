@echo off
chcp 65001 >nul
title Ping-Pong Gallery Dashboard
cd /d "%~dp0"
echo.
echo  ==========================================
echo    Ping-Pong Gallery Dashboard
echo  ==========================================
echo.
if not exist "config.json" (
  echo [!] config.json was not found. Run install.bat first.
  pause
  exit /b 1
)
call "%~dp0ensure_venv.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo Starting dashboard. Browser opens at http://127.0.0.1:8910
echo Close this window to stop the dashboard.
echo.
"%~dp0.venv\Scripts\python.exe" dashboard.py
echo.
echo Dashboard stopped.
pause
