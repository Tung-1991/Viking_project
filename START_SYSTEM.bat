@echo off
cd /d "%~dp0"
title VIKING V2
color 0B

if not exist ".\ckvnvenv\Scripts\python.exe" (
    echo [LOI] Khong tim thay ckvnvenv\Scripts\python.exe
    pause
    exit /b 1
)

:loop
echo [%date% %time%] Khoi chay VIKING V2...
.\ckvnvenv\Scripts\python.exe -m viking_v2.main

echo.
echo [CANH BAO] App da dong. Tu khoi dong lai sau 10 giay...
timeout /t 10 /nobreak >nul
goto loop
