@echo off
chcp 65001 >nul
title 너무바쁜베짱이 - 핑퐁 봇
cd /d "%~dp0"
echo.
echo  *==========================================*
echo  *      🦗 너무바쁜베짱이 STUDIO 🦗         *
echo  *      P I N G - P O N G   B O T            *
echo  *      made by 코다 ^& 크룩스               *
echo  *==========================================*
echo.
if not exist "config.json" echo [!] config.json 이 없어요. 먼저 '설치.bat' 을 실행하세요.
if not exist "config.json" pause
if not exist "config.json" exit /b
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*pingpong.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
powershell -NoProfile -Command "try{Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 5 | Out-Null; exit 0}catch{exit 1}"
if errorlevel 1 echo [!] ComfyUI(Comfy Desktop)가 꺼져 있어요. 먼저 켜고 아무 키나 누르세요.
if errorlevel 1 pause
if not errorlevel 1 echo [OK] ComfyUI ON
echo [OK] LM스튜디오는 자동으로 켜집니다 (GUI 불필요)
echo.
echo  [ INSERT COIN ]  폰에서 봇에게 메시지를 보내세요
echo  (이 창을 닫으면 봇이 꺼집니다)
echo.
python pingpong.py
echo.
echo  -- GAME OVER --  봇이 종료되었습니다.
pause
