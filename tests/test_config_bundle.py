# -*- coding: utf-8 -*-

import json
import os

import pytest

from core import config_bundle


@pytest.fixture
def acc(tmp_path):
    """Account dir giả với vài file settings."""
    acc_dir = tmp_path / "0009999999"
    acc_dir.mkdir()
    (acc_dir / "brain_settings.json").write_text(
        json.dumps({"MASTER_EVAL_MODE": "VETO", "BOT_ACTIVE_SYMBOLS": ["HPG"]}), encoding="utf-8"
    )
    (acc_dir / "symbol_overrides.json").write_text(
        json.dumps({"VN30F1M": {"sandbox": {}}}), encoding="utf-8"
    )
    (acc_dir / "bot_state.json").write_text(json.dumps({"pnl_today": 123}), encoding="utf-8")
    (acc_dir / "trading_token.json").write_text(json.dumps({"token": "SECRET"}), encoding="utf-8")
    advisor = acc_dir / "advisor"
    advisor.mkdir()
    (advisor / "user_context.md").write_text("ghi chú của tao", encoding="utf-8")
    return acc_dir


def test_export_bundle_contents(acc, tmp_path):
    dest = tmp_path / "bundle.json"
    result = config_bundle.export_bundle(str(dest), account_dir=str(acc))
    assert result["ok"] and result["files"] == 2 and result["advisor_files"] == 1

    bundle = json.loads(dest.read_text(encoding="utf-8"))
    assert bundle["bundle_version"] == config_bundle.BUNDLE_VERSION
    assert bundle["account_id"] == "0009999999"
    assert set(bundle["files"]) == {"brain_settings.json", "symbol_overrides.json"}
    # Runtime state + token TUYỆT ĐỐI không được đóng gói
    assert "bot_state.json" not in bundle["files"]
    assert "trading_token.json" not in bundle["files"]
    assert bundle["advisor_files"]["user_context.md"] == "ghi chú của tao"
    # Env không chứa key bí mật
    assert not any(config_bundle._is_secret(k) for k in bundle["env"])


def test_import_bundle_round_trip(acc, tmp_path):
    dest = tmp_path / "bundle.json"
    config_bundle.export_bundle(str(dest), account_dir=str(acc))

    # Đổi settings hiện tại rồi import lại -> phải phục hồi + có backup
    (acc / "brain_settings.json").write_text(json.dumps({"MASTER_EVAL_MODE": "VOTING"}), encoding="utf-8")
    result = config_bundle.import_bundle(str(dest), account_dir=str(acc))
    assert result["ok"] and result["restart_required"]
    assert "brain_settings.json" in result["restored"]
    assert any("bak_import" in b for b in result["backups"])

    restored = json.loads((acc / "brain_settings.json").read_text(encoding="utf-8"))
    assert restored["MASTER_EVAL_MODE"] == "VETO"
    assert (acc / "advisor" / "user_context.md").read_text(encoding="utf-8") == "ghi chú của tao"


def test_import_rejects_garbage_and_traversal(acc, tmp_path):
    # File không phải bundle
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        config_bundle.import_bundle(str(bad), account_dir=str(acc))

    # Bundle chứa tên file lạ / path traversal -> bị bỏ qua, không ghi ra ngoài
    evil = tmp_path / "evil.json"
    evil.write_text(json.dumps({
        "bundle_version": 1,
        "files": {
            "../../evil.json": {"x": 1},
            "trading_token.json": {"token": "hack"},
            "brain_settings.json": {"MASTER_EVAL_MODE": "VETO"},
        },
    }), encoding="utf-8")
    result = config_bundle.import_bundle(str(evil), account_dir=str(acc))
    assert result["restored"] == ["brain_settings.json"]
    assert not (tmp_path / "evil_out.json").exists()
    # trading_token không nằm trong whitelist SETTINGS_FILES -> giữ nguyên bản gốc
    token = json.loads((acc / "trading_token.json").read_text(encoding="utf-8"))
    assert token["token"] == "SECRET"


def test_import_rejects_newer_version(acc, tmp_path):
    newer = tmp_path / "newer.json"
    newer.write_text(json.dumps({"bundle_version": 99, "files": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        config_bundle.import_bundle(str(newer), account_dir=str(acc))
