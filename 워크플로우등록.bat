@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0ensure_venv.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

set "WF=%~1"
if "%WF%"=="" (
  echo.
  echo Drag a ComfyUI API-format JSON file onto this .bat,
  echo or paste its full path below.
  echo.
  set /p "WF=workflow json path: "
)

"%~dp0.venv\Scripts\python.exe" register_workflow.py "%WF%"
echo.
pause
