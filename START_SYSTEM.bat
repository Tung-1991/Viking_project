@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title VIKING V2
color 0B

:menu
cls
echo ========================================
echo               VIKING V2
echo ========================================
echo   1. Ra soat / cai package
echo   2. Khoi dong
echo   3. Kiem tra / cap nhat code Git
echo   0. Thoat
echo.
choice /c 1230 /n /m "Chon [1/2/3/0]: "
if errorlevel 4 exit /b 0
if errorlevel 3 goto update
if errorlevel 2 goto start
if errorlevel 1 goto packages
goto menu

:packages
cls
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" -Action Packages
echo.
pause
goto menu

:update
cls
rem Parse the whole block before Git updates this batch file itself.
(
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" -Action Update
    echo.
    pause
    goto menu
)

:start
cls
if not exist "%~dp0ckvnvenv\Scripts\python.exe" (
    echo [LOI] Chua co venv. Chon muc 1 de cai package truoc.
    pause
    goto menu
)

:run
echo [%date% %time%] Khoi chay VIKING V2...
"%~dp0ckvnvenv\Scripts\python.exe" -m viking_v2.main
if "%errorlevel%"=="0" goto menu
echo.
echo [LOI] App thoat bat thuong. Thu lai sau 10 giay.
choice /c RM /n /t 10 /d R /m "R = khoi dong lai, M = ve menu: "
if errorlevel 2 goto menu
goto run
