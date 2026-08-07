@echo off
chcp 65001 >nul
title Ping-Pong Bot
cd /d "%~dp0"
echo.
echo  ==========================================
echo    Ping-Pong Bot
echo  ==========================================
echo.
call "%~dp0ensure_venv.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*pingpong.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
powershell -NoProfile -Command "try{Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 5 | Out-Null; exit 0}catch{exit 1}"
if errorlevel 1 (
  echo [i] ComfyUI is not responding yet.
  echo     Ping-Pong will open the browser setup guide anyway.
) else (
  echo [OK] ComfyUI online
)
echo [i] Telegram and LM Studio are optional during first setup.
echo.
echo  Bot is running. Close this window to stop it.
echo.
"%~dp0.venv\Scripts\python.exe" pingpong.py
echo.
echo  Bot stopped.
pause
