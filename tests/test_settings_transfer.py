import json
from pathlib import Path

import pytest

import settings_transfer


def _write(path: Path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_checked_in_public_package_manifest_matches_files():
    package = settings_transfer.PROJECT_ROOT / "data" / "copy" / "public"

    assert settings_transfer.validate_package(package)["valid"] is True


def test_export_splits_public_private_and_never_exports_secrets(tmp_path):
    data_root = tmp_path / "data"
    account = data_root / "123456"
    _write(
        account / "brain_settings.json",
        json.dumps({"BOT": True, "OPENAI_API_KEY": "secret", "telegram_chat_id": "123"}),
    )
    _write(account / "symbol_overrides.json", '{"AAA": {"risk": 1}}')
    _write(account / "tsl_settings.json")
    _write(account / "advisor" / "advisor_prompt.md", "public prompt")
    _write(account / "advisor" / "expert_context.md", "private expert")
    _write(account / "advisor" / "user_context.md", "private user")
    _write(account / "ckcs_research" / "private_context.md", "private research")
    _write(account / "templates" / "custom.json", '{"x": 1}')
    _write(account / ".env", "DNSE_API_KEY=secret")
    _write(account / "advisor_api_settings.json", '{"model": "gpt-5.6", "api_key": "secret"}')
    _write(account / "telegram_settings.json", '{"chat_id": "secret"}')
    _write(account / "bot_state.json", '{"position": 1}')
    _write(account / "ckcs_research" / "scan_snapshot_cache.json", '{"raw": true}')
    env_path = tmp_path / ".env"
    _write(env_path, "DNSE_CKCS_WATCHLIST=AAA,FPT\nDNSE_API_KEY=secret")

    result = settings_transfer.export_split_settings(
        account,
        copy_root=data_root / "copy",
        stamp="20260722_120000",
        env_path=env_path,
    )
    public = Path(result["public_dir"])
    private = Path(result["private_dir"])

    assert (public / "brain_settings.json").is_file()
    assert (public / "symbol_overrides.json").is_file()
    assert (public / "advisor_prompt.md").is_file()
    assert json.loads((public / "templates_bundle.json").read_text(encoding="utf-8")) == {
        "custom.json": {"x": 1}
    }
    assert not (public / "expert_context.md").exists()
    assert json.loads((public / "advisor_api_settings.json").read_text(encoding="utf-8")) == {
        "model": "gpt-5.6"
    }
    assert not (public / "telegram_settings.json").exists()
    public_brain = json.loads((public / "brain_settings.json").read_text(encoding="utf-8"))
    assert public_brain == {"BOT": True}
    portable = json.loads((public / "portable_settings.json").read_text(encoding="utf-8"))
    assert portable == {"DNSE_CKCS_WATCHLIST": "AAA,FPT"}
    assert "123456" not in (public / "manifest.json").read_text(encoding="utf-8")

    assert (private / "expert_context.md").is_file()
    assert (private / "user_context.md").is_file()
    assert (private / "private_context.md").is_file()
    assert not (private / ".env").exists()
    assert not (private / "advisor_api_settings.json").exists()
    assert not (private / "telegram_settings.json").exists()
    assert not (private / "bot_state.json").exists()
    assert json.loads((private / "scan_snapshot_cache.json").read_text(encoding="utf-8")) == {
        "raw": True
    }


def test_import_pair_backs_up_and_restores_public_and_private(tmp_path):
    data_root = tmp_path / "data"
    source = data_root / "111"
    target = data_root / "222"
    _write(source / "brain_settings.json", '{"source": true}')
    _write(source / "advisor" / "expert_context.md", "source expert")
    _write(target / "brain_settings.json", '{"target": true}')
    _write(target / "advisor" / "expert_context.md", "target expert")
    source_env = tmp_path / "source.env"
    target_env = tmp_path / "target.env"
    _write(source_env, "DNSE_CKCS_WATCHLIST=AAA,FPT\nDNSE_API_KEY=source-secret")
    _write(target_env, "DNSE_CKCS_WATCHLIST=OLD\nDNSE_API_KEY=target-secret")
    exported = settings_transfer.export_split_settings(
        source,
        copy_root=data_root / "copy",
        stamp="20260722_120000",
        env_path=source_env,
    )

    result = settings_transfer.import_split_settings(
        exported["public_dir"],
        target,
        copy_root=data_root / "copy",
        env_path=target_env,
    )

    assert (target / "brain_settings.json").read_text(encoding="utf-8").strip() == '{\n  "source": true\n}'
    assert (target / "advisor" / "expert_context.md").read_text(encoding="utf-8") == "source expert"
    backup_root = data_root / "copy" / "rollback"
    backups = list(backup_root.rglob("brain_settings.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '{"target": true}'
    target_env_text = target_env.read_text(encoding="utf-8")
    assert "DNSE_CKCS_WATCHLIST=AAA,FPT" in target_env_text
    assert "DNSE_API_KEY=target-secret" in target_env_text
    assert result["restored"] == 3


def test_restore_raw_merges_by_symbol_day_and_prefers_eod(tmp_path):
    data_root = tmp_path / "data"
    source = data_root / "111"
    target = data_root / "222"
    _write(source / "brain_settings.json", '{"SCAN_SNAPSHOT_RETENTION_DAYS": 250}')
    _write(target / "brain_settings.json", '{"SCAN_SNAPSHOT_RETENTION_DAYS": 250}')
    _write(
        source / "ckcs_research" / "scan_snapshot_cache.json",
        json.dumps({
            "schema_version": 2,
            "updated_at": "2026-07-24 15:00:00",
            "symbols": {
                "AAA": {"days": {
                    "2026-07-23": {"day_status": "EOD", "eod_final": True, "last_scan": "14:50", "samples": 9},
                    "2026-07-24": {"day_status": "INTRADAY", "last_scan": "10:00", "samples": 2},
                }}
            },
        }),
    )
    _write(
        target / "ckcs_research" / "scan_snapshot_cache.json",
        json.dumps({
            "schema_version": 2,
            "updated_at": "2026-07-24 14:00:00",
            "symbols": {
                "AAA": {"days": {
                    "2026-07-23": {"day_status": "INTRADAY", "last_scan": "14:59", "samples": 20},
                    "2026-07-24": {"day_status": "INTRADAY", "last_scan": "11:00", "samples": 3},
                }},
                "FPT": {"days": {
                    "2026-07-24": {"day_status": "EOD", "eod_final": True, "last_scan": "14:50", "samples": 7}
                }},
            },
        }),
    )
    exported = settings_transfer.export_split_settings(
        source,
        data_root / "copy",
        env_path=tmp_path / ".env",
    )

    settings_transfer.import_split_settings(
        exported["public_dir"],
        target,
        copy_root=data_root / "copy",
        env_path=tmp_path / ".env",
    )

    merged = json.loads(
        (target / "ckcs_research" / "scan_snapshot_cache.json").read_text(encoding="utf-8")
    )
    assert merged["symbols"]["AAA"]["days"]["2026-07-23"]["day_status"] == "EOD"
    assert merged["symbols"]["AAA"]["days"]["2026-07-24"]["last_scan"] == "11:00"
    assert "FPT" in merged["symbols"]


def test_repeated_restore_keeps_only_one_fixed_rollback(tmp_path):
    data_root = tmp_path / "data"
    source = data_root / "111"
    target = data_root / "222"
    _write(source / "brain_settings.json", '{"version": 2}')
    _write(target / "brain_settings.json", '{"version": 1}')
    exported = settings_transfer.export_split_settings(
        source, data_root / "copy", env_path=tmp_path / ".env"
    )
    settings_transfer.import_split_settings(
        exported["public_dir"], target, data_root / "copy", env_path=tmp_path / ".env"
    )
    _write(target / "brain_settings.json", '{"version": 3}')
    settings_transfer.import_split_settings(
        exported["public_dir"], target, data_root / "copy", env_path=tmp_path / ".env"
    )

    rollback = data_root / "copy" / "rollback"
    assert sorted(
        path.name for path in data_root.joinpath("copy").iterdir() if path.is_dir()
    ) == ["private", "public", "rollback"]
    saved = rollback / "public" / "brain_settings.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == {"version": 3}


def test_validate_allows_edited_valid_json_package(tmp_path):
    account = tmp_path / "data" / "111"
    _write(account / "brain_settings.json", '{"ok": true}')
    result = settings_transfer.export_split_settings(
        account,
        tmp_path / "data" / "copy",
        "stamp",
        tmp_path / ".env",
    )
    public = Path(result["public_dir"])
    assert settings_transfer.validate_package(public)["valid"] is True

    _write(public / "brain_settings.json", '{"changed": true}')
    assert settings_transfer.validate_package(public)["valid"] is True


def test_validate_ignores_windows_line_ending_conversion(tmp_path):
    account = tmp_path / "data" / "111"
    _write(account / "brain_settings.json", '{\n  "ok": true\n}\n')
    result = settings_transfer.export_split_settings(
        account,
        tmp_path / "data" / "copy",
        env_path=tmp_path / ".env",
    )
    public = Path(result["public_dir"])
    brain = public / "brain_settings.json"
    lf_content = brain.read_bytes().replace(b"\r\n", b"\n")
    brain.write_bytes(lf_content.replace(b"\n", b"\r\n"))

    assert settings_transfer.validate_package(public)["valid"] is True


def test_current_public_manifest_v3_remains_readable(tmp_path):
    account = tmp_path / "data" / "111"
    _write(account / "brain_settings.json", '{"ok": true}')
    result = settings_transfer.export_split_settings(
        account,
        tmp_path / "data" / "copy",
        env_path=tmp_path / ".env",
    )
    public = Path(result["public_dir"])
    manifest_path = public / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = 3
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert settings_transfer.validate_package(public)["valid"] is True


def test_delete_removes_public_private_pair_only(tmp_path):
    account = tmp_path / "data" / "111"
    _write(account / "brain_settings.json")
    _write(account / "advisor" / "expert_context.md", "private")
    result = settings_transfer.export_split_settings(
        account,
        tmp_path / "data" / "copy",
        "stamp",
        tmp_path / ".env",
    )
    settings_transfer.delete_package(result["public_dir"], tmp_path / "data" / "copy")

    assert Path(result["public_dir"]).is_dir()
    assert Path(result["private_dir"]).is_dir()
    assert list(Path(result["public_dir"]).iterdir()) == []
    assert list(Path(result["private_dir"]).iterdir()) == []
    assert account.is_dir()


def test_export_again_overwrites_in_place_without_more_folders(tmp_path):
    account = tmp_path / "data" / "111"
    copy_root = tmp_path / "data" / "copy"
    _write(account / "brain_settings.json", '{"version": 1}')
    first = settings_transfer.export_split_settings(account, copy_root, env_path=tmp_path / ".env")
    _write(account / "brain_settings.json", '{"version": 2}')
    second = settings_transfer.export_split_settings(account, copy_root, env_path=tmp_path / ".env")

    assert first["public_dir"] == second["public_dir"] == copy_root / "public"
    assert json.loads((copy_root / "public" / "brain_settings.json").read_text(encoding="utf-8")) == {"version": 2}
    assert not any(path.is_dir() for path in (copy_root / "public").iterdir())
    assert not any(path.is_dir() for path in (copy_root / "private").iterdir())


def test_discover_accounts_excludes_runtime_directories(tmp_path):
    data_root = tmp_path / "data"
    _write(data_root / "123" / "brain_settings.json")
    _write(data_root / "named-account" / "brain_settings.json")
    _write(data_root / "copy" / "public" / "pkg" / "manifest.json")
    _write(data_root / "paper" / "123_state.json")

    assert [item.name for item in settings_transfer.discover_accounts(data_root)] == [
        "123",
        "named-account",
    ]
