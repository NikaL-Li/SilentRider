@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "MIMO_API_KEY=sk-cez3sxbtl2sf4hm0pgsxe11spyufa7x9lo5iyhv5mrrj390p"

echo ============================================
echo   Yi Lu Chang Tong - MiMo TTS
echo   Voice: Female(Bingtang) / Male(Suda)
echo   Open:  http://127.0.0.1:8818
echo   Close this window to stop the server.
echo ============================================

start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8818"
python server.py

pause
