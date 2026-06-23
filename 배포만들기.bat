@echo off
chcp 65001 >nul
title 너무바쁜베짱이 - 배포 만들기
echo.
echo  *==========================================*
echo  *      🦗 너무바쁜베짱이 STUDIO 🦗         *
echo  *      P I N G - P O N G   B O T            *
echo  *      made by 코다 ^& 크룩스               *
echo  *==========================================*
echo.
echo  배포용 ZIP 만드는 중... (config.json 제외)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0배포만들기.ps1"
pause
