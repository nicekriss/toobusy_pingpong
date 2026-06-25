@echo off
chcp 65001 >nul
title Ping-Pong Health Check
cd /d "%~dp0"

call "%~dp0ensure_venv.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

"%~dp0.venv\Scripts\python.exe" healthcheck.py
echo.
pause
