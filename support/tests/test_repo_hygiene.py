"""Check Git packaging rules without reading secrets or changing the worktree."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(not GIT, reason="Git required for packaging checks")


def _git(directory, *args, input=None):
    # Do not inherit GIT_DIR/index overrides or the operator's global ignores.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        [GIT, "-C", str(directory), *args], input=input,
        capture_output=True, text=True, encoding="utf-8", errors="strict",
        timeout=15, env=env,
    )


@pytest.fixture
def ignore_repo(tmp_path):
    result = _git(tmp_path, "init", "--quiet")
    assert result.returncode == 0, result.stderr
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    return tmp_path


def _ignored(directory, paths):
    # NUL avoids Windows text-pipe CRLF conversion being treated as path data.
    result = _git(directory, "check-ignore", "--no-index", "--stdin", "-z",
                  input="\0".join(paths) + "\0")
    assert result.returncode in {0, 1}, result.stderr
    return set(result.stdout.split("\0")) - {""}


def test_secrets_runtime_exports_and_disposable_files_stay_local(ignore_repo):
    paths = [
        ".env", ".env.production", "viking_v2/.env", "support/local/.env",
        "viking_v2/runtime/accounts/LOCAL/settings.json",
        "viking_v2/runtime/accounts/LOCAL/trading_token.json",
        "viking_v2/runtime/accounts/LOCAL/pending_orders.json",
        "viking_v2/runtime/accounts/LOCAL/logs/ui.log",
        "viking_v2/runtime/accounts/LOCAL/state.sqlite3",
        ".artifacts/update-backups/local/.env",
        ".artifacts/ui_perf_check.py", ".artifacts/ui-review/test.png",
        ".pytest_cache/v/cache/nodeids", "support/output/report.csv",
        "support/tools/__pycache__/preflight.cpython-313.pyc",
        "ckvnvenv/pyvenv.cfg", "venv/pyvenv.cfg", ".venv/pyvenv.cfg",
        "audits/live/report.txt", "docs/old.txt", "scripts/old.py",
        "tests_v2/test_old.py", "settings.before-va-local.bak",
        "report.xlsx", "report.xls", "local.key", "local.pem",
        "local.pfx", "local.p12", "build/app.exe", "dist/app.exe",
        "copied-state.db", "copied-state.db-wal", "copied-state.db-shm", "copied-state.db-journal",
        "copied-state.sqlite", "copied-state.sqlite-wal", "copied-state.sqlite-shm", "copied-state.sqlite-journal",
        "copied-state.sqlite3", "copied-state.sqlite3-wal", "copied-state.sqlite3-shm", "copied-state.sqlite3-journal",
    ]
    assert _ignored(ignore_repo, paths) == set(paths)


def test_source_tools_docs_and_secret_free_va_preset_are_publishable(ignore_repo):
    paths = [
        ".gitignore", "START_SYSTEM.bat", "requirements.txt",
        "viking_v2/.env.example", "viking_v2/main.py",
        "viking_v2/trading/orders.py", "support/launcher.ps1",
        "support/presets/VA_4_MA_50M.json", "support/docs/VAN_HANH.md",
        "support/docs/VIKING.md", "support/tests/test_va_preset.py",
        "support/tools/apply_va_preset.py", "support/tools/preflight.py",
        "support/tools/recover_order.py", "support/tools/run_offline.py",
    ]
    assert not _ignored(ignore_repo, paths)


def test_no_ignored_local_data_is_already_tracked():
    if not (ROOT / ".git").exists():
        pytest.skip("ZIP download has no Git index")
    result = _git(ROOT, "ls-files")
    assert result.returncode == 0, result.stderr
    paths = result.stdout.splitlines()
    assert paths
    assert not _ignored(ROOT, paths), "Ignored runtime/secrets/cache must be untracked"
