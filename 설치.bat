@echo off
chcp 65001 >nul
title 너무바쁜베짱이 - 핑퐁 봇 설치
cd /d "%~dp0"
echo.
echo  ==========================================
echo    너무바쁜베짱이 STUDIO - 핑퐁 봇 설치
echo  ==========================================
echo.
where python >nul 2>&1
if errorlevel 1 (
  echo [!] Python이 없어요. Python 3.10+ 설치 후 다시 실행하세요.
  echo     설치할 때 "Add to PATH" 체크가 필요합니다.
  pause
  exit /b 1
)
echo [1/2] Python 패키지 설치 중...
python -m pip install --quiet --upgrade -r requirements.txt
if errorlevel 1 (
  echo [!] 패키지 설치 실패.
  pause
  exit /b 1
)
echo [2/2] 설정 마법사 시작...
echo.
python setup.py
