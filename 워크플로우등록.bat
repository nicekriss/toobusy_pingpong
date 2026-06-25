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
  echo ComfyUI API Format JSON 파일 경로를 붙여넣어 주세요.
  echo 또는 이 bat 파일 위로 JSON 파일을 드래그앤드랍해도 됩니다.
  echo.
  set /p WF=workflow json path: 
)

"%~dp0.venv\Scripts\python.exe" register_workflow.py "%WF%"
echo.
pause
