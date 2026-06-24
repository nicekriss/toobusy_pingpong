@echo off
chcp 65001 >nul
title Ping-Pong Bot
cd /d "%~dp0"
echo.
echo  ==========================================
echo    Ping-Pong Bot
echo  ==========================================
echo.
if not exist "config.json" (
  echo [!] config.json was not found. Run install.bat first.
  pause
  exit /b 1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*pingpong.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
powershell -NoProfile -Command "try{Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 5 | Out-Null; exit 0}catch{exit 1}"
if errorlevel 1 (
  echo [!] ComfyUI is not responding on http://127.0.0.1:8188.
  echo     Start ComfyUI first, then press any key.
  pause
) else (
  echo [OK] ComfyUI online
)
echo [OK] LM Studio CLI will be started automatically when needed.
echo.
echo  Bot is running. Close this window to stop it.
echo.
python pingpong.py
echo.
echo  Bot stopped.
pause
