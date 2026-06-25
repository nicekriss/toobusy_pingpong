@echo off
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if exist "%PY%" (
  exit /b 0
)

echo [venv] Creating Ping-Pong virtual environment...
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -m venv "%ROOT%.venv"
) else (
  python -m venv "%ROOT%.venv"
)

if not exist "%PY%" (
  echo [!] Failed to create .venv. Install Python 3.10+ and enable Add to PATH.
  exit /b 1
)

echo [venv] Installing Python packages...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet --upgrade -r "%ROOT%requirements.txt"
exit /b %errorlevel%
