"""Exercise the Windows launcher without installing packages or using real accounts."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "support" / "launcher.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="Windows PowerShell launcher")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _run_ps(code, project=None):
    script = f"$ErrorActionPreference = 'Stop'\n. {_ps_quote(HELPER)}\n"
    if project is not None:
        script += f"$ProjectRoot = {_ps_quote(project)}\n"
        script += "$PythonExe = Join-Path $ProjectRoot 'ckvnvenv\\Scripts\\python.exe'\n"
    script += code + "\nexit 0\n"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )


def _assert_ok(result):
    assert result.returncode == 0, result.stdout + result.stderr


def test_helper_parses_on_windows_powershell_and_actual_python_version():
    result = _run_ps(
        f"$tokens=$null; $errors=$null\n"
        f"[System.Management.Automation.Language.Parser]::ParseFile({_ps_quote(HELPER)}, [ref]$tokens, [ref]$errors) | Out-Null\n"
        "if ($errors.Count) { throw ($errors | Out-String) }\n"
        "Assert-SupportedPython $PythonExe\n"
        "Write-Output 'PASSED'"
    )
    _assert_ok(result)
    assert "PASSED" in result.stdout


def test_actual_native_package_imports_on_windows_powershell():
    result = _run_ps("Assert-PackageImports")
    _assert_ok(result)
    assert "IMPORT_OK" in result.stdout


def test_actual_pin_check_on_windows_powershell():
    _assert_ok(_run_ps("Assert-PinnedPackages; Write-Output 'PINS_OK'"))


def test_pin_check_reports_missing_and_mismatched_packages(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy==0.0.0\nviking-fake-missing-package==1.0\n", encoding="utf-8")
    result = _run_ps(f"$PythonExe = {_ps_quote(ROOT / 'ckvnvenv/Scripts/python.exe')}\nAssert-PinnedPackages", tmp_path)
    assert result.returncode != 0
    assert "numpy:" in result.stdout and "viking-fake-missing-package: MISSING" in result.stdout


def test_environment_audit_is_read_only_and_reports_all_missing_groups(tmp_path):
    result = _run_ps(
        "function Get-GitExe { throw 'NO_GIT' }\n"
        "function Get-MissingVCRuntime { 'msvcp140.dll' }\n"
        "function Get-TimeZone { [pscustomobject]@{Id='UTC'} }\n"
        "function Find-SupportedPython { $null }\n"
        "function Install-SignedTool { throw 'UNEXPECTED_INSTALL' }\n"
        "function Ensure-VietnamTimeZone { throw 'UNEXPECTED_TIME_CHANGE' }\n"
        "function Install-Packages { throw 'UNEXPECTED_PACKAGES' }\n"
        "$ok=Test-Environment; if ($ok -isnot [bool] -or $ok) { throw 'WRONG_RESULT' }; 'READ_ONLY_OK'",
        tmp_path,
    )
    _assert_ok(result)
    assert "NO_GIT" in result.stdout and "msvcp140.dll" in result.stdout
    assert "[CHECK] 5 nhom" in result.stdout and "READ_ONLY_OK" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_environment_audit_success_returns_only_boolean():
    result = _run_ps(
        "function Get-GitExe { 'mock-git' }\nfunction Get-MissingVCRuntime {}\n"
        "function Get-TimeZone { [pscustomobject]@{Id='SE Asia Standard Time'} }\n"
        "function Assert-SupportedPython {}\nfunction Assert-PinnedPackages { 'PINS_OUTPUT' }\n"
        "function Assert-PackageImports { 'IMPORT_OUTPUT' }\nfunction Invoke-Native { 'NATIVE_OUTPUT' }\n"
        "$ok=Test-Environment; if ($ok -isnot [bool] -or -not $ok) { throw 'WRONG_RESULT' }; 'AUDIT_OK'"
    )
    _assert_ok(result)
    assert "AUDIT_OK" in result.stdout


def test_environment_audit_continues_after_package_errors():
    result = _run_ps(
        "function Get-GitExe { 'mock-git' }\nfunction Get-MissingVCRuntime {}\n"
        "function Get-TimeZone { [pscustomobject]@{Id='SE Asia Standard Time'} }\n"
        "function Assert-SupportedPython {}\nfunction Assert-PinnedPackages { throw 'PINS_BAD' }\n"
        "function Assert-PackageImports { throw 'IMPORT_BAD' }\nfunction Invoke-Native {}\n"
        "$ok=Test-Environment; if ($ok) { throw 'FALSE_SUCCESS' }; 'CHECKED_ALL'"
    )
    _assert_ok(result)
    assert "PINS_BAD" in result.stdout and "IMPORT_BAD" in result.stdout
    assert "[CHECK] 2 nhom" in result.stdout


def test_existing_tools_are_not_reinstalled(tmp_path):
    python = tmp_path / "ckvnvenv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"mock existing interpreter")
    result = _run_ps(
        "function Get-GitExe { 'mock-git' }\nfunction Get-MissingVCRuntime {}\n"
        "function Invoke-Native {}\nfunction Install-SignedTool { throw 'UNEXPECTED_INSTALL' }\n"
        "function Find-SupportedPython { throw 'UNEXPECTED_PYTHON_SEARCH' }\nEnsure-SystemTools; 'SKIPPED_ALL'",
        tmp_path,
    )
    _assert_ok(result)
    assert "SKIPPED_ALL" in result.stdout


@pytest.mark.parametrize("missing", ["Python", "Git", "VC"])
def test_system_tools_install_only_missing_prerequisite(tmp_path, missing):
    result = _run_ps(
        "$script:installed=@(); $script:gitInstalled=$false; $script:vcInstalled=$false\n"
        f"function Find-SupportedPython {{ {'$null' if missing == 'Python' else '[pscustomobject]@{Command=\'mock-python\'; Prefix=@()}'} }}\n"
        f"function Get-GitExe {{ {'if (-not $script:gitInstalled) { throw \'NO_GIT\' };' if missing == 'Git' else ''} 'mock-git' }}\n"
        f"function Get-MissingVCRuntime {{ {'if (-not $script:vcInstalled) { \'msvcp140.dll\' }' if missing == 'VC' else ''} }}\n"
        "function Assert-Administrator {}\nfunction Invoke-Native {}\n"
        "function Invoke-RestMethod { [pscustomobject]@{assets=@([pscustomobject]@{"
        "name='Git-2.56.0.2-64-bit.exe'; browser_download_url='https://github.com/git-for-windows/git/releases/download/test/Git-2.56.0.2-64-bit.exe'})} }\n"
        "function Install-SignedTool { param($Name, $Uri, $Publisher, $Arguments, $AllowedCodes); "
        "$script:installed += $Name; $script:gitInstalled=$true; $script:vcInstalled=$true }\n"
        "Ensure-SystemTools\nWrite-Output ('INSTALLED=' + (ConvertTo-Json -Compress -InputObject $script:installed))",
        tmp_path,
    )
    _assert_ok(result)
    installed = json.loads(result.stdout.split("INSTALLED=", 1)[1].splitlines()[0])
    assert len(installed) == 1 and missing in installed[0]


@pytest.mark.parametrize("exit_code,allowed,expected", [
    (0, "@(0,3010)", "INSTALLED_OK"),
    (1638, "@(0,3010,1638)", "INSTALLED_OK"),
    (3010, "@(0,3010)", "Windows yeu cau reboot"),
    (1603, "@(0,3010)", "exit 1603"),
])
def test_signed_installer_exit_codes_and_no_automatic_reboot(tmp_path, exit_code, allowed, expected):
    result = _run_ps(
        f"$env:TEMP={_ps_quote(tmp_path)}\nfunction Assert-Administrator {{}}\n"
        "function Invoke-WebRequest {}\n"
        "function Get-AuthenticodeSignature { [pscustomobject]@{Status='Valid'; SignerCertificate="
        "[pscustomobject]@{Subject='CN=Microsoft Corporation'}} }\n"
        f"function Start-Process {{ param($FilePath,$ArgumentList,$WindowStyle,[switch]$Wait,[switch]$PassThru); "
        "if ($WindowStyle -ne 'Hidden' -or -not $Wait -or $ArgumentList -notcontains '/norestart') { throw 'UNSAFE_INSTALL' }; "
        f"[pscustomobject]@{{ExitCode={exit_code}}} }}\n"
        f"Install-SignedTool -Name 'VC' -Uri 'https://example.invalid/mock.exe' -Publisher 'Microsoft Corporation' "
        f"-Arguments @('/install','/quiet','/norestart') -AllowedCodes {allowed}\n'INSTALLED_OK'"
    )
    assert (result.returncode == 0) == (exit_code in {0, 1638})
    assert expected in result.stdout + result.stderr


@pytest.mark.parametrize("status,publisher", [("NotSigned", "Microsoft Corporation"), ("Valid", "Wrong Publisher")])
def test_invalid_signature_or_publisher_blocks_installer(tmp_path, status, publisher):
    result = _run_ps(
        f"$env:TEMP={_ps_quote(tmp_path)}\nfunction Assert-Administrator {{}}\nfunction Invoke-WebRequest {{}}\n"
        f"function Get-AuthenticodeSignature {{ [pscustomobject]@{{Status='{status}'; SignerCertificate="
        f"[pscustomobject]@{{Subject='CN={publisher}'}}}} }}\n"
        "function Start-Process { throw 'LAUNCHED_UNTRUSTED_INSTALLER' }\n"
        "Install-SignedTool -Name 'VC' -Uri 'https://example.invalid/mock.exe' -Publisher 'Microsoft Corporation' -Arguments @('/quiet')"
    )
    assert result.returncode != 0
    assert "Chu ky / nha phat hanh" in result.stderr
    assert "LAUNCHED_UNTRUSTED_INSTALLER" not in result.stderr


def test_admin_error_stops_before_downloading_installer():
    result = _run_ps(
        "function Assert-Administrator { throw 'NEED_ADMIN' }\n"
        "function Invoke-WebRequest { throw 'DOWNLOADED_WITHOUT_ADMIN' }\n"
        "Install-SignedTool -Name 'VC' -Uri 'https://example.invalid/mock.exe' -Publisher 'Microsoft' -Arguments @('/quiet')"
    )
    assert result.returncode != 0
    assert "NEED_ADMIN" in result.stderr and "DOWNLOADED_WITHOUT_ADMIN" not in result.stderr


def test_missing_pip_is_bootstrapped_and_verified():
    result = _run_ps(
        "$script:ready=$false; $script:calls=@()\n"
        "function Invoke-Native { param($Command,$Arguments); $call=$Arguments -join '|'; $script:calls+=$call; "
        "if ($Arguments -contains 'ensurepip') { $script:ready=$true } "
        "elseif (-not $script:ready) { throw 'NO_PIP' } }\n"
        "Ensure-Pip; Write-Output ('CALLS=' + (ConvertTo-Json -Compress -InputObject $script:calls))"
    )
    _assert_ok(result)
    calls = json.loads(result.stdout.split("CALLS=", 1)[1].splitlines()[0])
    assert calls == ["-m|pip|--version", "-m|ensurepip|--upgrade", "-m|pip|--version"]


def test_vc_install_is_not_assumed_to_repair_missing_dll(tmp_path):
    result = _run_ps(
        "function Find-SupportedPython { [pscustomobject]@{Command='mock-python'; Prefix=@()} }\n"
        "function Get-GitExe { 'mock-git' }\nfunction Invoke-Native {}\n"
        "function Get-MissingVCRuntime { 'msvcp140.dll' }\n"
        "function Install-SignedTool {}\nEnsure-SystemTools", tmp_path,
    )
    assert result.returncode != 0
    assert "Windows con thieu DLL: msvcp140.dll" in result.stderr


def test_git_release_asset_must_use_official_download_url(tmp_path):
    result = _run_ps(
        "function Find-SupportedPython { [pscustomobject]@{Command='mock-python'; Prefix=@()} }\n"
        "function Get-GitExe { throw 'NO_GIT' }\nfunction Assert-Administrator {}\n"
        "function Invoke-RestMethod { [pscustomobject]@{assets=@([pscustomobject]@{"
        "name='Git-2.56.0.2-64-bit.exe'; browser_download_url='https://example.invalid/untrusted.exe'})} }\n"
        "function Install-SignedTool { throw 'INSTALLED_UNTRUSTED_ASSET' }\nEnsure-SystemTools", tmp_path,
    )
    assert result.returncode != 0
    assert "installer Git x64 chinh thuc" in result.stderr
    assert "INSTALLED_UNTRUSTED_ASSET" not in result.stderr


@pytest.mark.parametrize("exit_code", [0, 130, -1073741510])
def test_start_normal_close_and_ctrl_c_do_not_restart(tmp_path, exit_code):
    fake = tmp_path / "fake-python.ps1"
    fake.write_text(f"Write-Output 'FAKE_APP_LOG'; $global:LASTEXITCODE={exit_code}\n", encoding="utf-8")
    result = _run_ps(
        f"$PythonExe={_ps_quote(fake)}\nfunction Assert-AppStopped {{}}\n"
        "function Wait-AppRetry { throw 'RESTARTED_AFTER_OPERATOR_STOP' }\nStart-App; 'START_STOP_OK'", tmp_path,
    )
    _assert_ok(result)
    assert "FAKE_APP_LOG" in result.stdout and "START_STOP_OK" in result.stdout


def test_start_crash_retries_and_stops_when_next_run_closes_normally(tmp_path):
    fake = tmp_path / "fake-python.ps1"
    fake.write_text(
        "$global:appRuns++; if ($global:appRuns -eq 1) { $global:LASTEXITCODE=-123 } "
        "else { $global:LASTEXITCODE=0 }; Write-Output ('ARGS=' + ($args -join '|'))\n", encoding="utf-8",
    )
    result = _run_ps(
        f"$PythonExe={_ps_quote(fake)}\n$global:appRuns=0; $script:retries=0\nfunction Assert-AppStopped {{}}\n"
        "function Wait-AppRetry { $script:retries++; $true }\nStart-App\n"
        "if ($global:appRuns -ne 2 -or $script:retries -ne 1) { throw 'WRONG_RETRY' }; 'RETRY_OK'", tmp_path,
    )
    _assert_ok(result)
    assert "RETRY_OK" in result.stdout and "ARGS=-u|-m|viking_v2.main" in result.stdout


def test_start_crash_can_return_to_menu_without_retry(tmp_path):
    fake = tmp_path / "fake-python.ps1"
    fake.write_text("$global:appRuns++; $global:LASTEXITCODE=8\n", encoding="utf-8")
    result = _run_ps(
        f"$PythonExe={_ps_quote(fake)}\n$global:appRuns=0\nfunction Assert-AppStopped {{}}\n"
        "function Wait-AppRetry { $false }\nStart-App\n"
        "if ($global:appRuns -ne 1) { throw 'DID_NOT_RETURN_TO_MENU' }; 'MENU_OK'", tmp_path,
    )
    _assert_ok(result)
    assert "MENU_OK" in result.stdout


def test_batch_menu_routes_environment_update_start_and_preserves_logs():
    batch = (ROOT / "START_SYSTEM.bat").read_text(encoding="utf-8")
    assert "choice /c 12340" in batch
    assert "if errorlevel 5 exit /b 0" in batch
    assert "if errorlevel 4 goto preset" in batch
    assert all(f"-Action {action}" in batch for action in ("Check", "Packages", "Update", "Start", "PresetVA"))
    assert "if errorlevel 3 goto start" in batch
    assert "if errorlevel 2 goto update" in batch
    assert "choice /c 120" in batch
    assert 'support\\launcher.ps1' in batch and 'scripts\\launcher.ps1' not in batch
    helper = HELPER.read_text(encoding="utf-8")
    assert "/c RM /n /t 10 /d R" in helper
    assert "@(0, 130, -1073741510)" in helper
    assert "cls" in batch
    assert all(forbidden not in batch.lower() for forbidden in ("del ", "rmdir", "taskkill", "reset --hard"))


def test_va_preset_menu_checks_stopped_app_and_uses_existing_venv_only(tmp_path):
    python = tmp_path / "ckvnvenv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"mock existing interpreter")
    result = _run_ps(
        "$script:stopped=$false\n"
        "function Assert-AppStopped { $script:stopped=$true }\n"
        "function Invoke-Native { param($Command,$Arguments); "
        "if (-not $script:stopped -or $Command -ne $PythonExe "
        "-or $Arguments[1] -notlike '*apply_va_preset.py') { throw 'WRONG_PRESET_CALL' }; 'PRESET_ROUTED' }\n"
        "function Install-Packages { throw 'UNEXPECTED_INSTALL' }\n"
        "function Start-App { throw 'UNEXPECTED_START' }\nApply-VASettings",
        tmp_path,
    )
    _assert_ok(result)
    assert "PRESET_ROUTED" in result.stdout


def test_va_preset_menu_refuses_running_app(tmp_path):
    result = _run_ps(
        "function Assert-AppStopped { throw 'RUNNING_APP' }\n"
        "function Invoke-Native { throw 'UNEXPECTED_PRESET' }\nApply-VASettings", tmp_path,
    )
    assert result.returncode != 0
    assert "RUNNING_APP" in result.stderr + result.stdout


@pytest.mark.parametrize("module", ["viking_v2.main", "viking_v2.services.daemon"])
def test_running_project_blocks_changes(tmp_path, module):
    result = _run_ps(
        "function Get-CimInstance { [pscustomobject]@{ExecutablePath=$PythonExe; "
        f"CommandLine=($PythonExe + ' -m {module}')}} }}\n"
        "try { Assert-AppStopped; throw 'MISSED_RUNNING_APP' } catch {\n"
        "if ($_.Exception.Message -notlike '*Viking dang chay*') { throw }; Write-Output 'BLOCKED' }",
        tmp_path,
    )
    _assert_ok(result)
    assert "BLOCKED" in result.stdout


def test_unrelated_python_is_not_killed_or_blocked(tmp_path):
    result = _run_ps(
        "function Get-CimInstance { [pscustomobject]@{ExecutablePath='C:\\other\\python.exe'; "
        "CommandLine='C:\\other\\python.exe -m viking_v2.main'} }\n"
        "Assert-AppStopped; Write-Output 'ALLOWED'", tmp_path,
    )
    _assert_ok(result)
    assert "ALLOWED" in result.stdout


@pytest.mark.parametrize("version,supported", [("3.12", True), ("3.13", True), ("3.11", False), ("3.14", False)])
def test_pinned_python_version_check(version, supported):
    result = _run_ps(
        f"function Invoke-Native {{ Write-Output '{version}' }}\n"
        "Assert-SupportedPython 'unused'"
    )
    assert (result.returncode == 0) == supported


def test_partial_venv_is_not_overwritten(tmp_path):
    (tmp_path / "ckvnvenv").mkdir()
    result = _run_ps(
        "try { Ensure-Python; throw 'OVERWROTE_VENV' } catch {\n"
        "if ($_.Exception.Message -notlike '*thieu python.exe*') { throw } }", tmp_path,
    )
    _assert_ok(result)
    assert list((tmp_path / "ckvnvenv").iterdir()) == []


def test_package_action_installs_pins_and_checks_syntax_without_start(tmp_path):
    result = _run_ps(
        "function Assert-AppStopped {}\nfunction Ensure-VietnamTimeZone {}\nfunction Ensure-Python {}\n"
        "function Ensure-SystemTools {}\n"
        "$script:calls = @()\n"
        "function Invoke-Native { param($Command, $Arguments); $script:calls += ($Arguments -join '|') }\n"
        "Install-Packages\n"
        "Write-Output ('CALLS=' + (ConvertTo-Json -Compress -InputObject $script:calls))", tmp_path,
    )
    _assert_ok(result)
    calls = json.loads(result.stdout.split("CALLS=", 1)[1].splitlines()[0])
    assert len(calls) == 6
    assert calls[0] == "-m|pip|--version"
    assert "install|--quiet|-r|" in calls[1] and calls[1].endswith("requirements.txt")
    assert "import importlib.metadata" in calls[2]
    assert calls[3] == "-m|pip|check"
    assert calls[4].startswith("-c|import customtkinter, tkinter, numpy")
    assert calls[5].startswith("-m|compileall|-q|")


@pytest.mark.parametrize("current,changes", [("SE Asia Standard Time", 0), ("UTC", 1)])
def test_timezone_check_only_sets_utc7_when_needed(current, changes):
    result = _run_ps(
        f"$script:zone = '{current}'; $script:changes = 0\n"
        "function Get-TimeZone { [pscustomobject]@{Id=$script:zone} }\n"
        "function Set-TimeZone { param($Id, $ErrorAction); "
        "if ($Id -ne 'SE Asia Standard Time') { throw 'WRONG_ZONE' }; "
        "$script:zone=$Id; $script:changes++ }\n"
        "Ensure-VietnamTimeZone\nWrite-Output ('CHANGES=' + $script:changes)"
    )
    _assert_ok(result)
    assert f"CHANGES={changes}" in result.stdout


def test_timezone_permission_error_has_actionable_message():
    result = _run_ps(
        "function Get-TimeZone { [pscustomobject]@{Id='UTC'} }\n"
        "function Set-TimeZone { throw 'Access denied' }\nEnsure-VietnamTimeZone"
    )
    assert result.returncode != 0
    assert "Run as administrator" in result.stderr


def test_timezone_change_is_verified_instead_of_assumed():
    result = _run_ps(
        "function Get-TimeZone { [pscustomobject]@{Id='UTC'} }\n"
        "function Set-TimeZone {}\nEnsure-VietnamTimeZone"
    )
    assert result.returncode != 0
    assert "Windows chua chuyen sang UTC+7" in result.stderr


def test_running_app_blocks_timezone_and_package_changes(tmp_path):
    result = _run_ps(
        "function Assert-AppStopped { throw 'RUNNING_APP' }\n"
        "function Ensure-VietnamTimeZone { throw 'CHANGED_ZONE' }\n"
        "function Ensure-Python { throw 'INSTALLED_PYTHON' }\nInstall-Packages", tmp_path,
    )
    assert result.returncode != 0
    assert "RUNNING_APP" in result.stderr
    assert "CHANGED_ZONE" not in result.stderr


def test_python_preflight_requires_x64_and_tk_before_creating_venv():
    result = _run_ps(
        "function Invoke-Native { param($Command, $Arguments); "
        "$code = $Arguments[-1]; "
        "if ($code -notlike '*import struct, sys, tkinter*' -or "
        "$code -notlike '*struct.calcsize(chr(80)) == 8*') { throw 'MISSING_RUNTIME_CHECK' }; '3.13' }\n"
        "Assert-SupportedPython 'unused' @('-3.13')"
    )
    _assert_ok(result)


def test_native_import_failure_stops_before_success_message(tmp_path):
    result = _run_ps(
        "function Assert-AppStopped {}\nfunction Ensure-VietnamTimeZone {}\nfunction Ensure-Python {}\n"
        "function Ensure-SystemTools {}\nfunction Assert-PinnedPackages {}\n"
        "function Invoke-Native { param($Command, $Arguments); "
        "if ($Arguments[0] -eq '-c') { throw 'DLL_IMPORT_FAILED' }; "
        "if ($Arguments -contains 'compileall') { throw 'CONTINUED_AFTER_FAILURE' } }\nInstall-Packages",
        tmp_path,
    )
    assert result.returncode != 0
    assert "DLL_IMPORT_FAILED" in result.stderr
    assert "CONTINUED_AFTER_FAILURE" not in result.stderr
    assert "package/DLL va source da qua kiem tra" not in result.stdout


def test_gitignore_keeps_local_data_private_but_tracks_source_templates():
    git_exe = shutil.which("git.exe")
    if not git_exe:
        pytest.skip("Git not installed")
    excluded = [
        "viking_v2/.env", "viking_v2/.env.backup", "viking_v2/runtime/accounts/test/settings.json",
        "viking_v2/runtime/accounts/test/trading.sqlite3", "ckvnvenv/Scripts/python.exe",
        ".venv/Lib/package.py", ".artifacts/update-backups/test/.env", "audits/temp/report.md",
        "support/output/positions.json", "credentials.pem", "backup.bak", "report.xlsx", "daemon.log",
    ]
    included = [
        "viking_v2/.env.example", "viking_v2/config.py", "support/launcher.ps1",
        "support/docs/VIKING.md", "support/tests/test_live_safety.py", "support/tools/preflight.py",
    ]
    result = subprocess.run(
        [git_exe, "check-ignore", "--no-index", "--stdin", "-z"], cwd=ROOT,
        input="\0".join(excluded + included) + "\0", capture_output=True, text=True, timeout=15,
    )
    _assert_ok(result)
    assert set(filter(None, result.stdout.split("\0"))) == set(excluded)


def _update_mock(*, behind=1, incoming="viking_v2/.env.example", tracked="code.py", app_running=False, package_failure=False, backup_failure=False):
    return f"""
$script:calls = @()
function Invoke-Native {{
    param($Command, $Arguments)
    $call = $Arguments -join '|'
    $script:calls += $call
    switch ($call) {{
        'rev-parse|--is-inside-work-tree' {{ 'true' }}
        'rev-parse|--show-toplevel' {{ $ProjectRoot }}
        'rev-parse|--abbrev-ref|--symbolic-full-name|@{{u}}' {{ 'origin/main' }}
        'fetch' {{}}
        'rev-parse|--verify|--quiet|HEAD' {{ 'old-sha' }}
        'rev-parse|@{{u}}' {{ '{'new-sha' if behind else 'old-sha'}' }}
        'ls-tree|-r|--name-only|new-sha' {{ {_ps_quote(incoming)} }}
        'ls-files' {{ {_ps_quote(tracked)} }}
        'reset|--hard|new-sha' {{}}
        default {{ throw ('UNEXPECTED_CALL: ' + $call) }}
    }}
}}
function Assert-AppStopped {{ {"throw 'RUNNING_APP'" if app_running else ''} }}
function Install-Packages {{ $script:calls += 'PACKAGES'; {"throw 'PACKAGE_FAILED'" if package_failure else ''} }}
{"function Backup-LocalData { throw 'BACKUP_FAILED' }" if backup_failure else ''}
try {{ Update-Code }} catch {{ Write-Output ('ERROR=' + $_.Exception.Message) }}
Write-Output ('CALLS=' + (ConvertTo-Json -Compress -InputObject $script:calls))
"""


def _mock_update_result(tmp_path, **kwargs):
    (tmp_path / '.git').mkdir()
    result = _run_ps(_update_mock(**kwargs), tmp_path)
    _assert_ok(result)
    calls = json.loads(result.stdout.split("CALLS=", 1)[1].splitlines()[0])
    return result, calls


@pytest.mark.parametrize("options,error", [
    ({"incoming": "viking_v2/.env"}, "Git chua runtime/.env/venv/backup"),
    ({"incoming": "viking_v2/runtime/accounts/123/settings.json"}, "Git chua runtime/.env/venv/backup"),
    ({"incoming": "viking_v2/runtime"}, "Git chua runtime/.env/venv/backup"),
    ({"incoming": "viking_v2"}, "Git chua runtime/.env/venv/backup"),
    ({"incoming": "ckvnvenv/Scripts/python.exe"}, "Git chua runtime/.env/venv/backup"),
    ({"incoming": ".artifacts/update-backups/private/.env"}, "Git chua runtime/.env/venv/backup"),
    ({"tracked": "viking_v2/runtime/settings.json"}, "Git chua runtime/.env/venv/backup"),
    ({"app_running": True}, "RUNNING_APP"),
    ({"backup_failure": True}, "BACKUP_FAILED"),
])
def test_update_refuses_private_paths_and_running_app_before_overwrite(tmp_path, options, error):
    result, calls = _mock_update_result(tmp_path, **options)
    assert "ERROR=" + error in result.stdout
    assert "reset|--hard|new-sha" not in calls
    assert "PACKAGES" not in calls


def test_no_new_revision_does_not_backup_or_install(tmp_path):
    result, calls = _mock_update_result(tmp_path, behind=0)
    assert "Khong co ban cap nhat moi" in result.stdout
    assert "PACKAGES" not in calls
    assert not (tmp_path / ".artifacts").exists()


def test_clean_update_backs_up_local_files_before_exact_revision(tmp_path):
    runtime = tmp_path / "viking_v2" / "runtime" / "accounts" / "test"
    runtime.mkdir(parents=True)
    (runtime / "settings.json").write_text('{"local": true}', encoding="utf-8")
    (runtime / "trading.sqlite3").write_bytes(b"fake-db-for-backup-test")
    env = tmp_path / "viking_v2" / ".env"
    env.write_text("FAKE_KEY=only-test", encoding="utf-8")
    result, calls = _mock_update_result(tmp_path)
    assert "ERROR=" not in result.stdout
    backups = list((tmp_path / ".artifacts" / "update-backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / ".env").read_bytes() == env.read_bytes()
    assert (backups[0] / "runtime" / "accounts" / "test" / "trading.sqlite3").read_bytes() == (runtime / "trading.sqlite3").read_bytes()
    assert (backups[0] / "revision.txt").read_text().strip() == "old-sha"
    assert calls[-2:] == ["reset|--hard|new-sha", "PACKAGES"]
    assert calls.count("fetch") == 1
    assert not any(call.startswith(("pull", "clean", "push")) for call in calls)


def test_package_failure_after_update_is_reported_without_state_rollback(tmp_path):
    result, calls = _mock_update_result(tmp_path, package_failure=True)
    assert "ERROR=PACKAGE_FAILED" in result.stdout
    assert calls[-2:] == ["reset|--hard|new-sha", "PACKAGES"]
    assert "Da cap nhat. Chon muc 3" not in result.stdout


def test_real_native_nonzero_exit_is_not_ignored():
    result = _run_ps("Invoke-Native $PythonExe @('-c', 'raise SystemExit(7)')")
    assert result.returncode != 0
    assert "exit 7" in result.stderr


@pytest.mark.parametrize("local_changes", ["clean", "dirty", "staged", "commit", "untracked_collision"])
def test_local_git_remote_update_integration(tmp_path, local_changes):
    """Real Git overwrite and backup; disposable local remote, no pip/account access."""
    git_exe = shutil.which("git.exe")
    if not git_exe:
        pytest.skip("Git not installed")
    remote = tmp_path / "remote.git"
    author = tmp_path / "author"
    deployed = tmp_path / "VPS with spaces"
    author.mkdir()

    def git(*arguments, cwd=author):
        result = subprocess.run(
            [git_exe, "-c", "user.name=Launcher Test", "-c", "user.email=launcher@example.invalid", *map(str, arguments)],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        _assert_ok(result)
        return result.stdout.strip()

    git("init", "--bare", "--initial-branch=main", remote)
    git("init", "--initial-branch=main")
    (author / ".gitignore").write_text("viking_v2/runtime/\nviking_v2/.env\n.artifacts/\n", encoding="utf-8")
    (author / "code.txt").write_text("old revision", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "initial")
    git("remote", "add", "origin", remote)
    git("push", "-u", "origin", "main")
    git("clone", remote, deployed)
    (deployed / "keep-untracked.txt").write_text("keep my unrelated file", encoding="utf-8")
    if local_changes in {"dirty", "staged", "commit"}:
        (deployed / "code.txt").write_text("local changes to overwrite", encoding="utf-8")
    if local_changes in {"staged", "commit"}:
        git("add", "code.txt", cwd=deployed)
    if local_changes == "commit":
        git("commit", "-m", "VPS local commit", cwd=deployed)
    old_revision = git("rev-parse", "HEAD", cwd=deployed)
    runtime = deployed / "viking_v2" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "settings.json").write_text('{"machine": "VPS"}', encoding="utf-8")
    env = deployed / "viking_v2" / ".env"
    env.write_text("FAKE_KEY=VPS-only", encoding="utf-8")
    venv = deployed / "ckvnvenv"
    venv.mkdir()
    (venv / "keep.txt").write_text("venv data", encoding="utf-8")
    git("config", "--local", "core.excludesFile", str(author / "local-ignore"), cwd=deployed)
    (author / "local-ignore").write_text("ckvnvenv/\n", encoding="utf-8")
    if local_changes == "untracked_collision":
        (deployed / "new-code.txt").write_text("old untracked source", encoding="utf-8")
    (author / "new-code.txt").write_text("new source", encoding="utf-8")
    (author / "code.txt").write_text("updated revision", encoding="utf-8")
    git("add", "code.txt", "new-code.txt")
    git("commit", "-m", "update")
    git("push")
    expected_revision = git("rev-parse", "HEAD")
    result = _run_ps(
        "function Install-Packages { Write-Output 'PACKAGE_CHECK_MOCKED' }\nUpdate-Code", deployed,
    )
    _assert_ok(result)
    assert "PACKAGE_CHECK_MOCKED" in result.stdout
    assert git("rev-parse", "HEAD", cwd=deployed) == expected_revision
    assert (deployed / "code.txt").read_text() == "updated revision"
    assert (deployed / "new-code.txt").read_text() == "new source"
    assert (deployed / "keep-untracked.txt").read_text() == "keep my unrelated file"
    assert (venv / "keep.txt").read_text() == "venv data"
    backups = list((deployed / ".artifacts" / "update-backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "runtime" / "settings.json").read_bytes() == (runtime / "settings.json").read_bytes()
    assert (backups[0] / ".env").read_bytes() == env.read_bytes()
    assert (backups[0] / "revision.txt").read_text().strip() == old_revision
    assert git("status", "--porcelain", cwd=deployed) == "?? keep-untracked.txt"
    assert env.read_text() == "FAKE_KEY=VPS-only"
    assert (runtime / "settings.json").read_text() == '{"machine": "VPS"}'


@pytest.fixture
def zip_repository(tmp_path):
    git_exe = shutil.which("git.exe")
    if not git_exe:
        pytest.skip("Git not installed")
    author = tmp_path / "author"
    author.mkdir()
    remote = tmp_path / "remote.git"
    deployed = tmp_path / "Viking ZIP with spaces"
    deployed.mkdir()

    def git(*arguments, cwd=author):
        result = subprocess.run(
            [git_exe, "-c", "user.name=Launcher Test", "-c", "user.email=launcher@example.invalid", *map(str, arguments)],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        _assert_ok(result)
        return result.stdout.strip()

    git("init", "--bare", "--initial-branch=main", remote)
    git("init", "--initial-branch=main")
    (author / ".gitignore").write_text("viking_v2/runtime/\nviking_v2/.env\nckvnvenv/\n.artifacts/\n", encoding="utf-8")
    (author / "code.txt").write_text("new GitHub source", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "source")
    git("remote", "add", "origin", remote)
    git("push", "-u", "origin", "main")

    runtime = deployed / "viking_v2" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "settings.json").write_text('{"machine":"ZIP"}', encoding="utf-8")
    (deployed / "viking_v2" / ".env").write_text("FAKE_KEY=ZIP-only", encoding="utf-8")
    (deployed / "ckvnvenv").mkdir()
    (deployed / "ckvnvenv" / "keep.txt").write_text("venv data", encoding="utf-8")
    (deployed / "code.txt").write_text("old ZIP source", encoding="utf-8")
    return git, remote, deployed, author


@pytest.mark.parametrize("retry_fetch", [False, True])
def test_zip_update_connects_and_preserves_data_even_after_interrupted_fetch(zip_repository, retry_fetch):
    git, remote, deployed, _author = zip_repository
    retry = ""
    if retry_fetch:
        retry = """
$script:realNative = ${function:Invoke-Native}
$script:failFetch = $true
function Invoke-Native {
    param($Command, $Arguments)
    if ($script:failFetch -and $Arguments[0] -eq 'fetch') {
        $script:failFetch = $false
        throw 'FETCH_INTERRUPTED'
    }
    & $script:realNative $Command $Arguments
}
try { Update-Code; throw 'MISSED_FETCH_FAILURE' }
catch { if ($_.Exception.Message -ne 'FETCH_INTERRUPTED') { throw } }
"""
    result = _run_ps(
        f"$RepositoryUrl={_ps_quote(remote)}\n"
        "$script:packages=0\nfunction Install-Packages { $script:packages++; 'PACKAGE_CHECK_MOCKED' }\n"
        + retry + "\nUpdate-Code\nUpdate-Code\n"
        "if ($script:packages -ne 1) { throw 'WRONG_PACKAGE_COUNT' }; 'ZIP_OK'", deployed,
    )
    _assert_ok(result)
    assert "ZIP_OK" in result.stdout
    assert git("rev-parse", "HEAD", cwd=deployed) == git("rev-parse", "HEAD")
    assert git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=deployed) == "origin/main"
    assert (deployed / "code.txt").read_text() == "new GitHub source"
    assert (deployed / "viking_v2" / ".env").read_text() == "FAKE_KEY=ZIP-only"
    assert (deployed / "viking_v2" / "runtime" / "settings.json").read_text() == '{"machine":"ZIP"}'
    assert (deployed / "ckvnvenv" / "keep.txt").read_text() == "venv data"
    backups = list((deployed / ".artifacts" / "update-backups").iterdir())
    assert len(backups) == (2 if retry_fetch else 1)
    assert all((backup / ".env").read_text() == "FAKE_KEY=ZIP-only" for backup in backups)
    assert git("status", "--porcelain", cwd=deployed) == ""


@pytest.mark.parametrize("failure", ["running", "backup"])
def test_zip_update_refuses_to_initialize_before_stop_and_backup(tmp_path, failure):
    override = (
        "function Assert-AppStopped { throw 'RUNNING_APP' }\n" if failure == "running"
        else "function Backup-LocalData { throw 'BACKUP_FAILED' }\n"
    )
    result = _run_ps(override + "Update-Code", tmp_path)
    assert result.returncode != 0
    assert ("RUNNING_APP" if failure == "running" else "BACKUP_FAILED") in result.stderr
    assert not (tmp_path / ".git").exists()


@pytest.mark.parametrize("private_path", ["viking_v2/runtime/private.txt", "ckvnvenv/private.txt", "viking_v2/.env"])
def test_zip_update_rejects_remote_private_paths_before_overwrite(zip_repository, private_path):
    git, remote, deployed, author = zip_repository
    incoming = author / private_path
    incoming.parent.mkdir(parents=True, exist_ok=True)
    incoming.write_text("fake private data", encoding="utf-8")
    git("add", "-f", private_path)
    git("commit", "-m", "unsafe remote")
    git("push")
    result = _run_ps(
        f"$RepositoryUrl={_ps_quote(remote)}\n"
        "function Install-Packages { throw 'UNEXPECTED_INSTALL' }\nUpdate-Code", deployed,
    )
    assert result.returncode != 0
    assert "Git chua runtime/.env/venv/backup" in result.stderr
    assert "UNEXPECTED_INSTALL" not in result.stderr
    assert (deployed / "code.txt").read_text() == "old ZIP source"
    assert (deployed / "viking_v2" / ".env").read_text() == "FAKE_KEY=ZIP-only"
    if private_path != "viking_v2/.env":
        assert not (deployed / private_path).exists()


def test_unborn_repository_with_another_origin_is_not_retargeted(zip_repository):
    git, _remote, deployed, _author = zip_repository
    git("init", "--initial-branch=main", cwd=deployed)
    git("remote", "add", "origin", "https://example.invalid/not-viking.git", cwd=deployed)
    result = _run_ps("Update-Code", deployed)
    assert result.returncode != 0
    assert "origin khong dung Viking" in result.stderr
    assert git("remote", "get-url", "origin", cwd=deployed) == "https://example.invalid/not-viking.git"
    assert (deployed / "code.txt").read_text() == "old ZIP source"
