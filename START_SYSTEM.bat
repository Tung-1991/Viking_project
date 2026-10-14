@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Money Hunter
color 0B

:menu
cls
echo ========================================
echo              Money Hunter
echo ========================================
echo   1. Mo Money Hunter
echo   2. Cap nhat
echo   3. Nap preset VA
echo   4. Cai dat / sua moi truong
echo   5. Chan doan
echo   0. Thoat
echo.
choice /c 123450 /n /m "Chon [1/2/3/4/5/0]: "
if errorlevel 6 exit /b 0
if errorlevel 5 goto diagnostics
if errorlevel 4 goto environment
if errorlevel 3 goto preset
if errorlevel 2 goto update
if errorlevel 1 goto start
goto menu

:environment
cls
echo   1. Chi ra soat (khong cai / khong doi Windows)
echo   2. Cai phan thieu / sua package sai phien ban
echo   0. Quay lai
choice /c 120 /n /m "Chon [1/2/0]: "
if errorlevel 3 goto menu
if errorlevel 2 goto packages
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action Check
echo.
pause
goto menu

:packages
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action Packages
echo.
pause
goto menu

:update
cls
rem Parse the whole block before Git updates this batch file itself.
(
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action Update
    echo.
    pause
    goto menu
)

:start
cls
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action Start
echo.
pause
goto menu

:preset
cls
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action PresetVA
echo.
pause
goto menu

:logs
cls
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action Logs
echo.
pause
goto menu

:diagnostics
cls
echo   1. Kiem tra moi truong
echo   2. Mo thu muc log
echo   3. Kiem tra du lieu local (chi doc)
echo   0. Quay lai
choice /c 1230 /n /m "Chon [1/2/3/0]: "
if errorlevel 4 goto menu
if errorlevel 3 goto statecheck
if errorlevel 2 goto logs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action Check
echo.
pause
goto diagnostics

:statecheck
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0support\launcher.ps1" -Action StateCheck
echo.
pause
goto diagnostics
