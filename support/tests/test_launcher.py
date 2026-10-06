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


def test_batch_menu_returns_after_normal_close_and_retries_crash():
    batch = (ROOT / "START_SYSTEM.bat").read_text(encoding="utf-8")
    assert "choice /c 1230" in batch
    assert "if errorlevel 4 exit /b 0" in batch
    assert "-Action Packages" in batch and "-Action Update" in batch
    assert 'support\\launcher.ps1' in batch and 'scripts\\launcher.ps1' not in batch
    # Windows native crashes may have a negative exit code: only exactly zero is normal.
    assert 'if "%errorlevel%"=="0" goto menu' in batch
    assert "choice /c RM /n /t 10 /d R" in batch
    assert "cls" in batch
    assert all(forbidden not in batch.lower() for forbidden in ("del ", "rmdir", "taskkill", "reset --hard"))


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
        "$script:calls = @()\n"
        "function Invoke-Native { param($Command, $Arguments); $script:calls += ($Arguments -join '|') }\n"
        "Install-Packages\n"
        "Write-Output ('CALLS=' + (ConvertTo-Json -Compress -InputObject $script:calls))", tmp_path,
    )
    _assert_ok(result)
    calls = json.loads(result.stdout.split("CALLS=", 1)[1].splitlines()[0])
    assert len(calls) == 4
    assert "install|--quiet|-r|" in calls[0] and calls[0].endswith("requirements.txt")
    assert calls[1] == "-m|pip|check"
    assert calls[2].startswith("-c|import customtkinter, tkinter, numpy")
    assert calls[3].startswith("-m|compileall|-q|")


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


def _update_mock(*, behind=1, ahead=0, dirty=False, incoming="viking_v2/.env.example", app_running=False, package_failure=False, backup_failure=False):
    return f"""
$script:calls = @()
function Invoke-Native {{
    param($Command, $Arguments)
    $call = $Arguments -join '|'
    $script:calls += $call
    switch ($call) {{
        'rev-parse|--is-inside-work-tree' {{ 'true' }}
        'rev-parse|--abbrev-ref|--symbolic-full-name|@{{u}}' {{ 'origin/main' }}
        'fetch' {{}}
        'rev-parse|HEAD' {{ 'old-sha' }}
        'rev-parse|@{{u}}' {{ 'new-sha' }}
        'rev-list|--count|old-sha..new-sha' {{ '{behind}' }}
        'rev-list|--count|new-sha..old-sha' {{ '{ahead}' }}
        'status|--porcelain|--untracked-files=normal' {{ {"' M code.py'" if dirty else ''} }}
        'ls-tree|-r|--name-only|new-sha' {{ {_ps_quote(incoming)} }}
        'merge|--ff-only|new-sha' {{}}
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
    result = _run_ps(_update_mock(**kwargs), tmp_path)
    _assert_ok(result)
    calls = json.loads(result.stdout.split("CALLS=", 1)[1].splitlines()[0])
    return result, calls


@pytest.mark.parametrize("options,error", [
    ({"dirty": True}, "Co file sua/chua commit"),
    ({"ahead": 1}, "May nay co commit rieng"),
    ({"incoming": "viking_v2/.env"}, "Ban Git moi chua runtime/.env"),
    ({"incoming": "viking_v2/runtime/accounts/123/settings.json"}, "Ban Git moi chua runtime/.env"),
    ({"app_running": True}, "RUNNING_APP"),
    ({"backup_failure": True}, "BACKUP_FAILED"),
])
def test_update_refuses_unsafe_states_before_merge(tmp_path, options, error):
    result, calls = _mock_update_result(tmp_path, **options)
    assert "ERROR=" + error in result.stdout
    assert "merge|--ff-only|new-sha" not in calls
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
    assert calls[-2:] == ["merge|--ff-only|new-sha", "PACKAGES"]
    assert calls.count("fetch") == 1
    assert not any(call.startswith(("pull", "reset", "clean")) for call in calls)


def test_package_failure_after_update_is_reported_without_state_rollback(tmp_path):
    result, calls = _mock_update_result(tmp_path, package_failure=True)
    assert "ERROR=PACKAGE_FAILED" in result.stdout
    assert calls[-2:] == ["merge|--ff-only|new-sha", "PACKAGES"]
    assert "Da cap nhat. Chon muc 2" not in result.stdout


def test_real_native_nonzero_exit_is_not_ignored():
    result = _run_ps("Invoke-Native $PythonExe @('-c', 'raise SystemExit(7)')")
    assert result.returncode != 0
    assert "exit 7" in result.stderr


@pytest.mark.parametrize("dirty", [False, True])
def test_local_git_remote_update_integration(tmp_path, dirty):
    """Real Git fetch/merge and backup; local remote only, no pip/account access."""
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
    old_revision = git("rev-parse", "HEAD", cwd=deployed)
    runtime = deployed / "viking_v2" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "settings.json").write_text('{"machine": "VPS"}', encoding="utf-8")
    env = deployed / "viking_v2" / ".env"
    env.write_text("FAKE_KEY=VPS-only", encoding="utf-8")
    (author / "code.txt").write_text("updated revision", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "update")
    git("push")
    expected_revision = git("rev-parse", "HEAD")
    if dirty:
        (deployed / "uncommitted.txt").write_text("preserve my work", encoding="utf-8")
    result = _run_ps(
        "function Install-Packages { Write-Output 'PACKAGE_CHECK_MOCKED' }\nUpdate-Code", deployed,
    )
    if dirty:
        assert result.returncode != 0
        assert "Co file sua/chua commit" in result.stderr
        assert git("rev-parse", "HEAD", cwd=deployed) == old_revision
        assert not (deployed / ".artifacts").exists()
    else:
        _assert_ok(result)
        assert "PACKAGE_CHECK_MOCKED" in result.stdout
        assert git("rev-parse", "HEAD", cwd=deployed) == expected_revision
        assert (deployed / "code.txt").read_text() == "updated revision"
        backups = list((deployed / ".artifacts" / "update-backups").iterdir())
        assert len(backups) == 1
        assert (backups[0] / "runtime" / "settings.json").read_bytes() == (runtime / "settings.json").read_bytes()
        assert (backups[0] / ".env").read_bytes() == env.read_bytes()
        assert git("status", "--porcelain", cwd=deployed) == ""
    assert env.read_text() == "FAKE_KEY=VPS-only"
    assert (runtime / "settings.json").read_text() == '{"machine": "VPS"}'
