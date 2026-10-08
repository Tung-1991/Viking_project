param(
    [ValidateSet('Check', 'Packages', 'Update', 'Start', 'PresetVA')]
    [string]$Action = 'Packages'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$PythonExe = Join-Path $ProjectRoot 'ckvnvenv\Scripts\python.exe'
$PythonVersion = '3.13.16'
$RepositoryUrl = 'https://github.com/Tung-1991/Viking_project.git'

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

function Find-SupportedPython {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('3.13', '3.12')) {
            try {
                Assert-SupportedPython $launcher.Source @("-$version")
                return [pscustomobject]@{Command=$launcher.Source; Prefix=@("-$version")}
            } catch { continue }
        }
    }
    $candidates = @()
    $basePython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($basePython) { $candidates += $basePython.Source }
    foreach ($version in @('313', '312')) {
        $candidates += Join-Path $env:ProgramFiles "Python$version\python.exe"
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            Assert-SupportedPython $candidate
            return [pscustomobject]@{Command=$candidate; Prefix=@()}
        } catch { continue }
    }
    return $null
}

function Get-GitExe {
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'Git\cmd\git.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Git\cmd\git.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw 'Chua co Git. Chon muc 1 > 2 de cai phan thieu.'
}

function Get-MissingVCRuntime {
    foreach ($name in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'ucrtbase.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $env:WINDIR "System32\$name") -PathType Leaf)) { $name }
    }
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Can cai phan mem he thong. Dong BAT, chon Run as administrator, vao muc 1 > 2.'
    }
}

function Install-SignedTool {
    param([string]$Name, [string]$Uri, [string]$Publisher, [string[]]$Arguments, [int[]]$AllowedCodes = @(0, 3010))
    Assert-Administrator
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $setupDir = Join-Path $env:TEMP ('Viking-setup-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $setupDir | Out-Null
    $installer = Join-Path $setupDir 'setup.exe'
    Write-Host "[SETUP] Tai $Name tu nguon chinh thuc..."
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $installer -TimeoutSec 120
    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $Publisher) {
        throw "Chu ky / nha phat hanh installer $Name khong hop le. Khong cai."
    }
    Write-Host "[SETUP] Cai $Name..."
    $result = Start-Process -FilePath $installer -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru
    if ($result.ExitCode -notin $AllowedCodes) { throw "Cai $Name loi (exit $($result.ExitCode))." }
    if ($result.ExitCode -eq 3010) {
        throw "$Name da cai; Windows yeu cau reboot. Reboot roi chon muc 1 > 2 de kiem tra tiep. Khong tu reboot."
    }
    $env:PATH = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + $env:PATH
}

function Ensure-SystemTools {
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) { throw 'Can Windows x64 va PowerShell x64.' }
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf) -and -not (Find-SupportedPython)) {
        Install-SignedTool -Name "Python $PythonVersion x64" `
            -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" `
            -Publisher 'Python Software Foundation' `
            -Arguments @('/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_launcher=1',
                'InstallLauncherAllUsers=1', 'Include_pip=1', 'Include_tcltk=1', 'Include_test=0')
    }
    try { $git = Get-GitExe } catch { $git = $null }
    if (-not $git) {
        Assert-Administrator
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' `
            -Headers @{'User-Agent'='Viking-setup'} -TimeoutSec 30
        $asset = @($release.assets | Where-Object { $_.name -match '^Git-[0-9.]+-64-bit\.exe$' })
        if ($asset.Count -ne 1 -or $asset[0].browser_download_url -notlike 'https://github.com/git-for-windows/git/releases/download/*') {
            throw 'Khong tim duoc installer Git x64 chinh thuc.'
        }
        Install-SignedTool -Name 'Git x64' -Uri $asset[0].browser_download_url `
            -Publisher 'Johannes Schindelin|Open Source Developer, Git for Windows' `
            -Arguments @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-')
    }
    Invoke-Native (Get-GitExe) @('--version')
    if (@(Get-MissingVCRuntime).Count -gt 0) {
        Install-SignedTool -Name 'Microsoft VC++ v14 x64' -Uri 'https://aka.ms/vc14/vc_redist.x64.exe' `
            -Publisher 'Microsoft Corporation' -Arguments @('/install', '/quiet', '/norestart') -AllowedCodes @(0, 3010, 1638)
    }
    $missing = @(Get-MissingVCRuntime)
    if ($missing.Count) { throw ('Windows con thieu DLL: ' + ($missing -join ', ') + '. Kiem tra ban Windows / installer.') }
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
    $basePython = Find-SupportedPython
    if (-not $basePython) { throw 'Chua co Python x64/Tk phu hop. Chon muc 1 > 2.' }
    Invoke-Native $basePython.Command ($basePython.Prefix + @('-m', 'venv', $venvRoot))
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

function Assert-PinnedPackages {
    $code = @'
import importlib.metadata as metadata
import pathlib, sys
failed = False
for line in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():
    if not line.strip() or line.lstrip().startswith('#'):
        continue
    name, expected = line.strip().split('==', 1)
    try:
        actual = metadata.version(name)
    except metadata.PackageNotFoundError:
        actual = 'MISSING'
    if actual != expected:
        print(name + ': ' + actual + ' (can ' + expected + ')')
        failed = True
raise SystemExit(1 if failed else 0)
'@
    Invoke-Native $PythonExe @('-c', $code, (Join-Path $ProjectRoot 'requirements.txt'))
}

function Test-Environment {
    $issues = 0
    Write-Host '[CHECK] Chi ra soat; khong cai, khong doi timezone, khong mo app.'
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
        Write-Host '[THIEU] Windows / PowerShell x64'; $issues++
    }
    try { Invoke-Native (Get-GitExe) @('--version') | Out-Host; Write-Host '[OK] Git' }
    catch { Write-Host "[THIEU] Git: $($_.Exception.Message)"; $issues++ }
    $missing = @(Get-MissingVCRuntime)
    if ($missing.Count) { Write-Host ('[THIEU] VC++ / Windows DLL: ' + ($missing -join ', ')); $issues++ }
    else { Write-Host '[OK] VC++ / UCRT DLL x64 co tren may; kiem tra nap that o buoc import.' }
    if ((Get-TimeZone).Id -ne 'SE Asia Standard Time') { Write-Host '[THIEU] Mui gio UTC+7'; $issues++ }
    else { Write-Host '[OK] Mui gio UTC+7 (khong dong nghia da dong bo NTP).' }
    Write-Host ('[TIME] Gio Windows: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        if (Find-SupportedPython) { Write-Host '[OK] Python x64/Tk nen' }
        else { Write-Host '[THIEU] Python 3.12/3.13 x64 kem Tk'; $issues++ }
        Write-Host '[THIEU] Venv / package cua app'; $issues++
    } else {
        foreach ($check in @('Python', 'pip', 'Pins', 'Dependencies', 'Imports')) {
            try {
                switch ($check) {
                    'Python' { Assert-SupportedPython $PythonExe | Out-Host }
                    'pip' { Invoke-Native $PythonExe @('-m', 'pip', '--version') | Out-Host }
                    'Pins' { Assert-PinnedPackages | Out-Host }
                    'Dependencies' { Invoke-Native $PythonExe @('-m', 'pip', 'check') | Out-Host }
                    'Imports' { Assert-PackageImports | Out-Host }
                }
                Write-Host "[OK] $check"
            } catch { Write-Host "[THIEU/LOI] ${check}: $($_.Exception.Message)"; $issues++ }
        }
    }
    Write-Host "[CHECK] $issues nhom thieu / loi. Chon muc 1 > 2 de cai; khong sua API / tai khoan."
    return ($issues -eq 0)
}

function Ensure-Pip {
    try { Invoke-Native $PythonExe @('-m', 'pip', '--version') | Out-Host }
    catch {
        Write-Host '[SETUP] Venv thieu pip; khoi phuc bang ensurepip cua Python.'
        Invoke-Native $PythonExe @('-m', 'ensurepip', '--upgrade')
        Invoke-Native $PythonExe @('-m', 'pip', '--version')
    }
}

function Install-Packages {
    Assert-AppStopped
    # Do not replace an existing invalid/partial venv or install tools on its behalf.
    $venvRoot = Join-Path $ProjectRoot 'ckvnvenv'
    if (Test-Path -LiteralPath $venvRoot) {
        if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'ckvnvenv thieu python.exe. Khong tu xoa/thay venv.' }
        Assert-SupportedPython $PythonExe
    }
    Ensure-SystemTools
    Ensure-VietnamTimeZone
    Ensure-Python
    Ensure-Pip
    $requirements = Join-Path $ProjectRoot 'requirements.txt'
    Write-Host '[PACKAGE] Kiem tra / cai dung phien ban trong requirements.txt...'
    Invoke-Native $PythonExe @('-m', 'pip', '--disable-pip-version-check', 'install', '--quiet', '-r', $requirements)
    Assert-PinnedPackages
    Invoke-Native $PythonExe @('-m', 'pip', 'check')
    Assert-PackageImports
    Invoke-Native $PythonExe @('-m', 'compileall', '-q', (Join-Path $ProjectRoot 'viking_v2'))
    Write-Host '[OK] Git, VC++ x64, UTC+7, Python x64/Tk, package/DLL va source da qua kiem tra.'
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

function Assert-UpdatePaths {
    param([string]$GitExe, [string]$Revision)
    $incoming = @(Invoke-Native $GitExe @('ls-tree', '-r', '--name-only', $Revision))
    $tracked = @(Invoke-Native $GitExe @('ls-files'))
    foreach ($name in ($incoming + $tracked)) {
        if ($name -match '^viking_v2$|^(viking_v2/runtime|ckvnvenv|venv|env|\.venv|\.artifacts|support/output)(/|$)|(^|/)\.env($|\.|/)' -and $name -ne 'viking_v2/.env.example') {
            throw 'Git chua runtime/.env/venv/backup. Tu choi ghi de de bao ve du lieu local.'
        }
    }
}

function Connect-ZipRepository {
    param([string]$GitExe)
    Assert-AppStopped
    $hasMetadata = Test-Path -LiteralPath (Join-Path $ProjectRoot '.git')
    if ($hasMetadata) {
        # Resume a first connection interrupted during fetch; never retarget another remote.
        $origin = Invoke-Native $GitExe @('remote', 'get-url', 'origin')
        if ([string]$origin -ne $RepositoryUrl) {
            throw 'Repo chua co revision va origin khong dung Viking. Khong tu thay remote.'
        }
    }
    Write-Host '[GIT] Ban ZIP / ket noi chua hoan tat. Backup va tu noi GitHub...'
    Backup-LocalData 'ZIP-before-git'
    if (-not $hasMetadata) {
        Invoke-Native $GitExe @('init', '--initial-branch=main')
        Invoke-Native $GitExe @('remote', 'add', 'origin', $RepositoryUrl)
    }
    Invoke-Native $GitExe @('fetch', 'origin')
    $target = Invoke-Native $GitExe @('rev-parse', 'origin/main')
    Assert-UpdatePaths $GitExe $target
    Assert-AppStopped
    # Only the source paths checked above are overwritten; no git clean or local-data restore.
    Invoke-Native $GitExe @('symbolic-ref', 'HEAD', 'refs/heads/main')
    Invoke-Native $GitExe @('reset', '--hard', $target)
    Invoke-Native $GitExe @('branch', '--set-upstream-to=origin/main', 'main')
    Install-Packages
    Write-Host '[OK] Da noi Git va cap nhat. Lan sau dung muc 2; chon muc 3 de khoi dong.'
    Write-Host '[SETTING] Muc 4 nap preset VA moi. Cap nhat code khong tu doi setting/API/Telegram.'
}

function Update-Code {
    $git = Get-GitExe
    Set-Location -LiteralPath $ProjectRoot
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
        Connect-ZipRepository $git
        return
    }
    $inside = Invoke-Native $git @('rev-parse', '--is-inside-work-tree')
    if ([string]$inside -ne 'true') { throw 'Thu muc nay khong phai Git worktree.' }
    $gitRoot = Invoke-Native $git @('rev-parse', '--show-toplevel')
    if ([IO.Path]::GetFullPath([string]$gitRoot).TrimEnd('\', '/') -ine $ProjectRoot.TrimEnd('\', '/')) {
        throw 'Git root khong trung thu muc Viking. Khong ghi de.'
    }
    try { $current = Invoke-Native $git @('rev-parse', '--verify', '--quiet', 'HEAD') }
    catch {
        Connect-ZipRepository $git
        return
    }
    $upstream = Invoke-Native $git @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    Write-Host "[GIT] Kiem tra $upstream..."
    Invoke-Native $git @('fetch')
    $target = Invoke-Native $git @('rev-parse', '@{u}')
    if ([string]$current -eq [string]$target) {
        Write-Host '[OK] Khong co ban cap nhat moi.'
        Write-Host '[SETTING] Muc 4 nap preset VA. Muc 2 chi cap nhat code, khong tu doi setting.'
        return
    }
    Assert-UpdatePaths $git $target
    Assert-AppStopped
    Write-Host '[GIT] Co phien ban khac tren GitHub. Se ghi de source local; giu .env, runtime, venv va backup.'
    Backup-LocalData $current
    # Deliberately overwrite tracked source, per operator request. Never git clean.
    Invoke-Native $git @('reset', '--hard', $target)
    Install-Packages
    Write-Host '[OK] Da cap nhat. Chon muc 3 de khoi dong.'
    Write-Host '[SETTING] Muc 4 nap preset VA moi. Cap nhat code khong tu doi setting/API/Telegram.'
}

function Wait-AppRetry {
    & "$env:WINDIR\System32\choice.exe" /c RM /n /t 10 /d R /m 'R = khoi dong lai, M = ve menu: ' | Out-Host
    return ($LASTEXITCODE -eq 1)
}

function Apply-VASettings {
    Assert-AppStopped
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Chua co venv. Chon muc 1 > 2 truoc.' }
    Invoke-Native $PythonExe @('-u', (Join-Path $ProjectRoot 'support\tools\apply_va_preset.py'))
}

function Start-App {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw 'Chua co venv. Chon muc 1 > 2 truoc.' }
    Assert-AppStopped
    Write-Host '[START] Giu console nay de xem log. Ctrl+C hoac dong cua so app de dung.'
    Write-Host '[LOG] viking_v2\runtime\accounts\<account>\logs\ (ui.log, daemon.log).'
    do {
        $savedPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $PythonExe -u -m viking_v2.main
            $appExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $savedPreference }
        # Ctrl+C is operator shutdown, including interruption before Tk initializes.
        if ($appExit -in @(0, 130, -1073741510)) { Write-Host '[STOP] App da dung.'; return }
        Write-Host "[LOI] App thoat bat thuong (exit $appExit). Thu lai sau 10 giay."
    } while (Wait-AppRetry)
}

# Dot-source exposes functions only, allowing offline tests without Git/pip actions.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Set-Location -LiteralPath $ProjectRoot
        switch ($Action) {
            'Check' { if (-not (Test-Environment)) { exit 1 } }
            'Packages' { Install-Packages }
            'Update' { Update-Code }
            'Start' { Start-App }
            'PresetVA' { Apply-VASettings }
        }
        exit 0
    } catch {
        Write-Host "[LOI] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host 'Khong tu khoi dong app hoac phuc hoi runtime cu. Xu ly loi roi chon lai trong menu.'
        exit 1
    }
}
