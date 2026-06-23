@echo off
chcp 65001 >nul
title 너무바쁜베짱이 - 핑퐁 봇 설치
cd /d "%~dp0"
echo.
echo  *==========================================*
echo  *      🦗 너무바쁜베짱이 STUDIO 🦗         *
echo  *      P I N G - P O N G   B O T            *
echo  *      made by 코다 ^& 크룩스               *
echo  *==========================================*
echo.
where python >nul 2>&1
if errorlevel 1 echo [!] 파이썬이 없어요. https://python.org 에서 설치(Add to PATH 체크) 후 다시 실행.
if errorlevel 1 pause
if errorlevel 1 exit /b
echo [1/2] 패키지 설치 중...
python -m pip install --quiet --upgrade requests
echo [2/2] 설정 마법사 시작
echo.
python setup.py
