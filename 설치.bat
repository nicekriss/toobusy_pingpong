@echo off
chcp 65001 >nul
title Ping-Pong Install
cd /d "%~dp0"

echo.
echo  ==========================================
echo    Ping-Pong install
echo  ==========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo [!] Python 3.10+ is required. Install Python and enable Add to PATH.
    pause
    exit /b 1
  )
)

echo [1/2] Setting up .venv...
call "%~dp0ensure_venv.bat"
if errorlevel 1 (
  echo [!] Virtual environment setup failed.
  pause
  exit /b 1
)

echo [2/2] Preparing the browser setup...
"%~dp0.venv\Scripts\python.exe" -c "from onboarding import ensure_config; ensure_config()"
if errorlevel 1 (
  echo [!] Could not create the first-run configuration.
  pause
  exit /b 1
)
echo [OK] Opening Ping-Pong. Follow the browser guide.
call "%~dp0run_bot.bat"
