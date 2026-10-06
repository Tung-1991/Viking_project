param(
    [ValidateSet('Packages', 'Update')]
    [string]$Action = 'Packages'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$PythonExe = Join-Path $ProjectRoot 'ckvnvenv\Scripts\python.exe'

function Invoke-Native {
    param([string]$Command, [string[]]$Arguments)
    # Windows PowerShell must not treat native stderr progress as a fatal error.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Command @Arguments
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($nativeExitCode -ne 0) {
        throw "$Command khong thanh cong (exit $nativeExitCode)."
    }
}

function Assert-AppStopped {
    $processes = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"
    foreach ($candidate in $processes) {
        $commandLine = [string]$candidate.CommandLine
        $sameEnvironment = (
            [string]$candidate.ExecutablePath -ieq $PythonExe -or
            $commandLine.IndexOf($PythonExe, [StringComparison]::OrdinalIgnoreCase) -ge 0
        )
        if ($sameEnvironment -and $commandLine -match 'viking_v2\.(main|services\.daemon)\b') {
            throw 'Viking dang chay. Dong app va launcher cu truoc khi cai package/cap nhat.'
        }
    }
}

function Assert-SupportedPython {
    param([string]$Command, [string[]]$Prefix = @())
    $version = Invoke-Native $Command ($Prefix + @('-c', 'import struct, sys, tkinter; assert struct.calcsize(chr(80)) == 8, ''Can Python x64.''; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))'))
    if ([string]$version -notin @('3.12', '3.13')) {
        throw 'Bo package nay can Python 3.12 hoac 3.13. Khong tu xoa/thay venv hien co.'
    }
}

function Ensure-Python {
    if (Test-Path -LiteralPath $PythonExe -PathType Leaf) {
        Assert-SupportedPython $PythonExe
        return
    }
    $venvRoot = Join-Path $ProjectRoot 'ckvnvenv'
    if (Test-Path -LiteralPath $venvRoot) {
        throw 'ckvnvenv da ton tai nhung thieu python.exe. Kiem tra venv; khong tu ghi de.'
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('3.13', '3.12')) {
            try {
                Assert-SupportedPython $launcher.Source @("-$version")
            } catch {
                continue
            }
            Invoke-Native $launcher.Source @("-$version", '-m', 'venv', $venvRoot)
            Assert-SupportedPython $PythonExe
            return
        }
    }
    $basePython = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $basePython) {
        throw 'Chua co Python. Cai Python 3.13 x64 (kem Python Launcher), sau do chon muc 1.'
    }
    Assert-SupportedPython $basePython.Source
    Invoke-Native $basePython.Source @('-m', 'venv', $venvRoot)
    Assert-SupportedPython $PythonExe
}

function Ensure-VietnamTimeZone {
    $desired = 'SE Asia Standard Time'
    if ((Get-TimeZone).Id -ne $desired) {
        Write-Host '[TIME] Dat mui gio UTC+7 (Bangkok, Hanoi, Jakarta)...'
        try {
            Set-TimeZone -Id $desired -ErrorAction Stop
        } catch {
            throw 'Khong dat duoc UTC+7. Chay BAT bang Run as administrator hoac dat mui gio trong Windows roi thu lai.'
        }
        if ((Get-TimeZone).Id -ne $desired) {
            throw 'Windows chua chuyen sang UTC+7. Kiem tra Time zone roi thu lai.'
        }
    }
    Write-Host '[TIME] UTC+7 OK.'
}

function Assert-PackageImports {
    Write-Host '[PACKAGE] Kiem tra Tk va kha nang nap thu vien/DLL...'
    Invoke-Native $PythonExe @('-c', 'import customtkinter, tkinter, numpy, pandas, numba, llvmlite.binding, requests, dotenv, websocket, msgpack, openpyxl; print(''IMPORT_OK'')')
}

function Install-Packages {
    Assert-AppStopped
    Ensure-VietnamTimeZone
    Ensure-Python
    $requirements = Join-Path $ProjectRoot 'requirements.txt'
    Write-Host '[PACKAGE] Kiem tra / cai dung phien ban trong requirements.txt...'
    Invoke-Native $PythonExe @('-m', 'pip', '--disable-pip-version-check', 'install', '--quiet', '-r', $requirements)
    Invoke-Native $PythonExe @('-m', 'pip', 'check')
    Assert-PackageImports
    Invoke-Native $PythonExe @('-m', 'compileall', '-q', (Join-Path $ProjectRoot 'viking_v2'))
    Write-Host '[OK] UTC+7, Python x64/Tk, package/DLL va source da qua kiem tra.'
}

function Backup-LocalData {
    param([string]$Revision)
    $stamp = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $backup = Join-Path $ProjectRoot ".artifacts\update-backups\$stamp"
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    foreach ($name in @('runtime', '.env')) {
        $source = Join-Path $ProjectRoot "viking_v2\$name"
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $backup $name) -Recurse -ErrorAction Stop
        }
    }
    [IO.File]::WriteAllText((Join-Path $backup 'revision.txt'), $Revision + [Environment]::NewLine)
    Write-Host "[BACKUP] $backup"
}

function Update-Code {
    $git = (Get-Command git.exe -ErrorAction Stop).Source
    Set-Location -LiteralPath $ProjectRoot
    $inside = Invoke-Native $git @('rev-parse', '--is-inside-work-tree')
    if ([string]$inside -ne 'true') { throw 'Thu muc nay khong phai Git worktree.' }
    $upstream = Invoke-Native $git @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    Write-Host "[GIT] Kiem tra $upstream..."
    Invoke-Native $git @('fetch')
    $current = Invoke-Native $git @('rev-parse', 'HEAD')
    $target = Invoke-Native $git @('rev-parse', '@{u}')
    $behind = [int](Invoke-Native $git @('rev-list', '--count', "${current}..${target}"))
    $ahead = [int](Invoke-Native $git @('rev-list', '--count', "${target}..${current}"))
    if ($ahead -gt 0) { throw 'May nay co commit rieng. Khong tu merge/reset; can xu ly Git truoc.' }
    if ($behind -eq 0) {
        Write-Host '[OK] Khong co ban cap nhat moi.'
        return
    }
    $dirty = @(Invoke-Native $git @('status', '--porcelain', '--untracked-files=normal'))
    if ($dirty.Count -gt 0) { throw 'Co file sua/chua commit. Khong ghi de; commit hoac xu ly truoc.' }
    $incoming = @(Invoke-Native $git @('ls-tree', '-r', '--name-only', $target))
    foreach ($name in $incoming) {
        if ($name -match '^viking_v2/runtime(/|$)|(^|/)\.env($|\.)' -and $name -ne 'viking_v2/.env.example') {
            throw 'Ban Git moi chua runtime/.env. Tu choi cap nhat de bao ve du lieu local.'
        }
    }
    Assert-AppStopped
    Write-Host "[GIT] Co $behind commit moi. Backup truoc khi cap nhat..."
    Backup-LocalData $current
    Invoke-Native $git @('merge', '--ff-only', $target)
    Install-Packages
    Write-Host '[OK] Da cap nhat. Chon muc 2 de khoi dong.'
}

# Dot-source exposes functions only, allowing offline tests without Git/pip actions.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Set-Location -LiteralPath $ProjectRoot
        switch ($Action) {
            'Packages' { Install-Packages }
            'Update' { Update-Code }
        }
        exit 0
    } catch {
        Write-Host "[LOI] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host 'Khong tu khoi dong app hoac phuc hoi runtime cu. Xu ly loi roi chon lai trong menu.'
        exit 1
    }
}
