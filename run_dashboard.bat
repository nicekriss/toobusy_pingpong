@echo off
chcp 65001 >nul
title Ping-Pong Gallery Dashboard
cd /d "%~dp0"
echo.
echo  ==========================================
echo    Ping-Pong Gallery Dashboard
echo  ==========================================
echo.
rem Resolve the port the same way dashboard.py does: env, then config.json, then 8910.
set "PP_PORT=%PINGPONG_DASHBOARD_PORT%"
if not defined PP_PORT (
  for /f "usebackq delims=" %%P in (`powershell.exe -NoLogo -NoProfile -Command "try{ $v=(Get-Content -Raw 'config.json' -ErrorAction Stop | ConvertFrom-Json).dashboard_port; if($v){$v} }catch{}"`) do set "PP_PORT=%%P"
)
if not defined PP_PORT set "PP_PORT=8910"

rem dashboard.py binds with allow_reuse_address, so Windows lets a second copy
rem grab the same port instead of failing. Both then answer at random and the
rem older one keeps serving stale code, so refuse to stack another instance.
powershell.exe -NoLogo -NoProfile -Command "$p=%PP_PORT%; $l=[System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners(); if($l | Where-Object { $_.Port -eq $p }){ exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo  [!] 대시보드가 이미 %PP_PORT% 포트에서 실행 중입니다.
  echo.
  echo      브라우저에서 바로 여세요:  http://127.0.0.1:%PP_PORT%
  echo      새로 시작하려면 기존 대시보드 창을 먼저 닫아 주세요.
  echo.
  pause
  exit /b 1
)

call "%~dp0ensure_venv.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo Starting dashboard. Browser opens at http://127.0.0.1:%PP_PORT%
echo Close this window to stop the dashboard.
echo.
"%~dp0.venv\Scripts\python.exe" dashboard.py
echo.
echo Dashboard stopped.
pause
