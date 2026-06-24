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
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*pingpong_safe.py*' -or $_.CommandLine -like '*pingpong.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "try { $cfg = Get-Content -Raw 'config.json' | ConvertFrom-Json; $api = [string]$cfg.comfy_api; if ([string]::IsNullOrWhiteSpace($api)) { $api = 'http://127.0.0.1:8188' }; $api.TrimEnd('/') } catch { 'http://127.0.0.1:8188' }"`) do set "COMFY_API=%%A"
powershell -NoProfile -Command "try{Invoke-RestMethod '%COMFY_API%/system_stats' -TimeoutSec 5 | Out-Null; exit 0}catch{exit 1}"
if errorlevel 1 (
  echo [!] ComfyUI is not responding on %COMFY_API%.
  echo     Start ComfyUI first, then press any key.
  pause
) else (
  echo [OK] ComfyUI online: %COMFY_API%
)
echo [OK] LM Studio CLI will be started automatically when needed.
echo.
echo  Bot is running. Close this window to stop it.
echo.
python pingpong_safe.py
echo.
echo  Bot stopped.
pause
