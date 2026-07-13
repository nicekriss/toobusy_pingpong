@echo off
chcp 65001 >nul
title Ping-Pong Update
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_pingpong.ps1"
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
  echo 창을 닫으려면 아무 키나 누르세요.
) else (
  echo 오류 내용을 확인한 뒤 아무 키나 누르세요.
)
pause >nul
exit /b %RESULT%
