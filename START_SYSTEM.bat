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
echo   1. Ra soat / cai moi truong
echo   2. Kiem tra / cap nhat GitHub (clone / ZIP, ghi de source)
echo   3. Khoi dong
echo   4. Nap setting VA: MSN 15 / CTS 15 / HDB 15 / IDC 5 trieu
echo      Dung 100%%, P1 100%%, E AUTO; giu API / Telegram tren may
echo   0. Thoat
echo.
choice /c 12340 /n /m "Chon [1/2/3/4/0]: "
if errorlevel 5 exit /b 0
if errorlevel 4 goto preset
if errorlevel 3 goto start
if errorlevel 2 goto update
if errorlevel 1 goto environment
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
