@echo off
chcp 65001 >nul
title 너무바쁜베짱이 - 갤러리 대시보드
cd /d "%~dp0"
echo.
echo  *==========================================*
echo  *      너무바쁜베짱이 STUDIO               *
echo  *      PING-PONG  GALLERY  DASHBOARD         *
echo  *      made by 코다 ^& 크룩스               *
echo  *==========================================*
echo.
if not exist "config.json" echo [!] config.json 이 없어요. 먼저 '설치.bat' 을 실행하세요.
if not exist "config.json" pause
if not exist "config.json" exit /b
echo  갤러리 대시보드 시작... 브라우저가 곧 열립니다.
echo  (이 창을 닫으면 대시보드가 꺼집니다)
echo.
python dashboard.py
echo.
echo  -- 대시보드 종료 --
pause
