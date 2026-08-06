# Kaiser Trading System - Tài Liệu Ghi Chú & Lệnh Khởi Chạy

Tài liệu này lưu trữ các câu lệnh PowerShell và mã nguồn Batch script được sử dụng cho việc quản lý, reset và vận hành bot.

## 1. Lệnh Kích Hoạt Môi Trường PowerShell (Venv)

Để chạy các lệnh bằng PowerShell, chạy các lệnh sau:

```powershell
# Cho phép thực thi script trong phiên làm việc hiện tại
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# Kích hoạt môi trường ảo Python
.\ckvnvenv\Scripts\Activate.ps1
```

## 2. Script Tự Động Reset Session & Cập Nhật Hệ Thống (Draft)

Dưới đây là bản nháp script Batch để tự động Reset Session cho các Account, dọn dẹp bộ nhớ đệm `__pycache__`, cập nhật code từ Git và khởi chạy Bot:

```batch
@echo off
setlocal enabledelayedexpansion

:: 1. Quay lại đúng đường dẫn gốc của Ngài
cd /d "C:\Users\Administrator\Downloads\RAT"

:: --- QUY TRÌNH RESET SESSION & DỌN DẸP THÔNG MINH ---
if exist "data" (
    echo [INFO] Dang tu dong Reset Session cho tat ca cac Account...
    
    :: Quét qua từng thư mục con trong data
    for /d %%d in ("data\*") do (
        set "dirname=%%~nxd"
        
        :: Kiểm tra bỏ qua thư mục hệ thống
        set "skip=0"
        if /i "!dirname!"=="logs" set "skip=1"
        if /i "!dirname!"=="templates" set "skip=1"
        
        if "!skip!"=="0" (
            pushd "%%d" >nul 2>&1
            if !errorlevel! equ 0 (
                :: A. XOÁ CÁC FILE TRẠNG THÁI (Reset Safeguard)
                if exist "bot_state.json" del /q "bot_state.json"
                if exist "live_signals.json" del /q "live_signals.json"
                if exist "current_signal_state.json" del /q "current_signal_state.json"
                
                :: B. XOÁ FILE TẠM
                del /q *.tmp >nul 2>&1
                del /q *.bak >nul 2>&1
                
                popd
                echo [SUCCESS] Da Reset trang thai cho Account: !dirname!
            )
        )
    )
)

:: Quét và xoá __pycache__
echo [INFO] Dang don dep cac thu muc __pycache__...
for /d /r . %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"

:: Cập nhật code từ Git
echo [INFO] Dang kiem tra va cap nhat code tu Git...
git fetch origin
git reset --hard origin/main

:: Khởi chạy Bot
echo [INFO] Dang khoi chay Bot...
call ratvenv\Scripts\activate.bat
python main.py

pause
```
