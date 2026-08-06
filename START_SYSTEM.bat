@echo off
cd /d "%~dp0"
title RAT6 CKVN
color 0B

echo [UPDATE] Dang kiem tra source code moi...
where git >nul 2>&1
if errorlevel 1 (
    echo [WARN] Khong tim thay Git. Chay source hien tai.
) else (
    git pull --ff-only origin main
    if errorlevel 1 echo [WARN] Khong cap nhat duoc. Giu nguyen source hien tai.
)

if not exist ".\ckvnvenv\Scripts\python.exe" (
    echo [ERROR] Khong tim thay ckvnvenv\Scripts\python.exe
    pause
    exit /b 1
)

if not exist ".\data\logs" mkdir ".\data\logs"

set "RAT_LAUNCHED_BY_WATCHDOG=1"

:loop
echo [%date% %time%] Khoi chay RAT6 CKVN...
.\ckvnvenv\Scripts\python.exe main.py

echo.
echo [WARN] App da dong. Tu khoi dong lai sau 10 giay...
echo [%date% %time%] SYSTEM EXIT DETECTED >> data/logs/system_watchdog.log
timeout /t 10 /nobreak >nul
goto loop
