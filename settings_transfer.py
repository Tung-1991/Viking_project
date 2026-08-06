# -*- coding: utf-8 -*-
"""Chuyển setting bằng ba thư mục cố định: public, private và rollback."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT / "data"
COPY_ROOT = DATA_ROOT / "copy"
PUBLIC_COPY_ROOT = COPY_ROOT / "public"
PRIVATE_COPY_ROOT = COPY_ROOT / "private"
ROLLBACK_ROOT = COPY_ROOT / "rollback"

PUBLIC_SOURCE_MAP = {
    "brain_settings.json": Path("brain_settings.json"),
    "symbol_overrides.json": Path("symbol_overrides.json"),
    "tsl_settings.json": Path("tsl_settings.json"),
    "presets_config.json": Path("presets_config.json"),
    "advisor_prompt.md": Path("advisor/advisor_prompt.md"),
    "advisor_flow.md": Path("advisor/advisor_flow.md"),
    "advisor_api_settings.json": Path("advisor_api_settings.json"),
}
PRIVATE_SOURCE_MAP = {
    "user_context.md": Path("advisor/user_context.md"),
    "expert_context.md": Path("advisor/expert_context.md"),
    "private_context.md": Path("ckcs_research/private_context.md"),
    "scan_snapshot_cache.json": Path("ckcs_research/scan_snapshot_cache.json"),
}
PUBLIC_SPECIAL_FILES = {"portable_settings.json", "templates_bundle.json"}
MANIFEST_FILE = "manifest.json"

PUBLIC_ENV_KEYS = (
    "DNSE_CKPS_WATCHLIST", "DNSE_CKCS_WATCHLIST", "DNSE_DERIVATIVE_REAL_SYMBOLS",
    "SCAN_SNAPSHOT_ENABLED", "SCAN_SNAPSHOT_INTERVAL_MINUTES", "SCAN_SNAPSHOT_RETENTION_DAYS",
    "PAPER_TRADING", "MONEY_DISPLAY_ZERO_TRIM", "DNSE_WS_ENABLED", "DNSE_WS_MODE",
    "DNSE_WS_URL", "DNSE_WS_ENCODING", "DNSE_WS_BOARD_ID", "DNSE_WS_RECONCILE_SECONDS",
    "MARKET_PREOPEN_MINUTES", "MARKET_HOLIDAYS", "DNSE_TICK_CACHE_TTL_SECONDS",
    "DNSE_OHLC_CACHE_TTL_SECONDS", "DNSE_OHLC_CACHE_TTL_CLOSED_SECONDS",
    "DNSE_ACCOUNT_CACHE_TTL_SECONDS", "DNSE_POSITIONS_CACHE_TTL_SECONDS",
    "DNSE_RATE_LIMIT_RETRIES", "DNSE_OHLC_WINDOW_FACTOR_INTRADAY",
    "DNSE_OHLC_WINDOW_FACTOR_DAILY", "ADVISOR_API_TIMEOUT_SECONDS", "ADVISOR_API_RETRIES",
    "DNSE_DERIVATIVE_TAX_RATE", "DNSE_STOCK_TAX_RATE", "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE",
)
SENSITIVE_KEY_PARTS = (
    "api_key", "api_secret", "access_token", "refresh_token", "trading_token",
    "bot_token", "password", "passcode", "otp", "chat_id", "account_no", "account_id",
    "active_account", "customer_id", "custody",
)
IGNORED_ACCOUNT_DIRS = {"copy", "logs", "paper", "templates", "rollback"}


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitize_json(item) for key, item in value.items() if not _is_sensitive_key(key)}
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    return value


def _sanitize_public_text(text: str, env_values: dict[str, str]) -> str:
    clean = str(text or "")
    for key, value in env_values.items():
        if _is_sensitive_key(key) and value:
            clean = clean.replace(str(value), "[REDACTED]")
    clean = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", clean)
    clean = re.sub(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", clean)
    clean = re.sub(r"(?<![\d.-])\d{9,14}(?![\d.-])", "[ACCOUNT]", clean)
    clean = re.sub(r"[A-Za-z]:\\[^\r\n`\"']+", "[LOCAL_PATH]", clean)
    return clean


def _safe_target(path: Path) -> Path:
    if path.is_absolute() or ".." in path.parts or path.name.lower() == ".env":
        raise ValueError(f"Đường dẫn không hợp lệ: {path}")
    return path


def _read_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _write_safe_env_values(env_path: Path, values: dict[str, object]) -> None:
    clean = {str(k): str(v) for k, v in values.items() if k in PUBLIC_ENV_KEYS and not _is_sensitive_key(k)}
    if not clean:
        return
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.is_file() else []
    output: list[str] = []
    applied: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in clean:
                if key not in applied:
                    output.append(f"{key}={clean[key]}")
                    applied.add(key)
                continue
        output.append(line)
    for key in PUBLIC_ENV_KEYS:
        if key in clean and key not in applied:
            output.append(f"{key}={clean[key]}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    temp = env_path.with_name(f".{env_path.name}.settings.tmp")
    temp.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.replace(temp, env_path)


def ensure_copy_layout(copy_root: Path = COPY_ROOT) -> None:
    (Path(copy_root) / "public").mkdir(parents=True, exist_ok=True)
    (Path(copy_root) / "private").mkdir(parents=True, exist_ok=True)
    (Path(copy_root) / "rollback").mkdir(parents=True, exist_ok=True)


def clear_generated_packages(copy_root: Path = COPY_ROOT) -> list[Path]:
    """Dọn sạch nội dung hai vùng copy; không đụng data tài khoản."""
    root = Path(copy_root).resolve()
    ensure_copy_layout(root)
    removed: list[Path] = []
    for category in ("public", "private"):
        folder = (root / category).resolve()
        if folder.parent != root:
            raise ValueError("Thư mục copy không hợp lệ.")
        for child in list(folder.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed.append(child)
    return removed


def discover_accounts(data_root: Path = DATA_ROOT) -> list[Path]:
    if not Path(data_root).exists():
        return []
    accounts: list[Path] = []
    for child in Path(data_root).iterdir():
        if not child.is_dir() or child.name.lower() in IGNORED_ACCOUNT_DIRS:
            continue
        has_settings = any((child / target).is_file() for target in PUBLIC_SOURCE_MAP.values())
        if child.name.isdigit() or child.name.upper() == "PAPER" or has_settings:
            accounts.append(child)
    return sorted(accounts, key=lambda item: item.name)


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _export_flat_files(account_dir: Path, folder: Path, category: str, env_path: Path) -> list[str]:
    mapping = PUBLIC_SOURCE_MAP if category == "public" else PRIVATE_SOURCE_MAP
    files: list[str] = []
    env_values = _read_env_values(env_path)
    for output_name, source_relative in mapping.items():
        source = account_dir / source_relative
        if not source.is_file():
            continue
        target = folder / output_name
        if category == "public" and source.suffix.lower() == ".json":
            with source.open("r", encoding="utf-8-sig") as handle:
                _write_json(target, _sanitize_json(json.load(handle)))
        elif category == "public" and source.suffix.lower() == ".md":
            target.write_text(
                _sanitize_public_text(source.read_text(encoding="utf-8-sig"), env_values),
                encoding="utf-8",
            )
        else:
            shutil.copy2(source, target)
        files.append(output_name)

    if category == "public":
        portable = {key: env_values[key] for key in PUBLIC_ENV_KEYS if key in env_values and not _is_sensitive_key(key)}
        _write_json(folder / "portable_settings.json", portable)
        files.append("portable_settings.json")

        templates: dict[str, Any] = {}
        template_root = account_dir / "templates"
        if template_root.is_dir():
            for source in sorted(template_root.rglob("*.json")):
                relative = source.relative_to(template_root)
                if ".." in relative.parts:
                    continue
                with source.open("r", encoding="utf-8-sig") as handle:
                    templates[relative.as_posix()] = _sanitize_json(json.load(handle))
        _write_json(folder / "templates_bundle.json", templates)
        files.append("templates_bundle.json")
    return files


def _write_manifest(folder: Path, category: str, files: list[str]) -> None:
    mapping = PUBLIC_SOURCE_MAP if category == "public" else PRIVATE_SOURCE_MAP
    targets = {name: target.as_posix() for name, target in mapping.items() if name in files}
    _write_json(folder / MANIFEST_FILE, {
        "version": 4,
        "category": category,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": files,
        "targets": targets,
    })


def export_split_settings(
    account_dir: Path,
    copy_root: Path = COPY_ROOT,
    stamp: str | None = None,
    env_path: Path | None = None,
) -> dict[str, Any]:
    del stamp  # Không tạo phiên bản theo thời gian; luôn ghi đè bản hiện tại.
    account_dir = Path(account_dir).resolve()
    if not account_dir.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục tài khoản: {account_dir}")
    root = Path(copy_root)
    clear_generated_packages(root)
    public_dir, private_dir = root / "public", root / "private"
    env_path = Path(env_path) if env_path is not None else PROJECT_ROOT / ".env"
    public_files = _export_flat_files(account_dir, public_dir, "public", env_path)
    private_files = _export_flat_files(account_dir, private_dir, "private", env_path)
    _write_manifest(public_dir, "public", public_files)
    _write_manifest(private_dir, "private", private_files)
    return {
        "package_id": "current",
        "public_dir": public_dir,
        "private_dir": private_dir,
        "public_files": public_files,
        "private_files": private_files,
    }


def export_settings(account_dir: Path, copy_root: Path = COPY_ROOT, stamp: str | None = None, env_path: Path | None = None) -> Path:
    return Path(export_split_settings(account_dir, copy_root, stamp, env_path)["public_dir"])


def discover_packages(copy_root: Path = COPY_ROOT, category: str = "public") -> list[Path]:
    folder = Path(copy_root) / category
    return [folder] if (folder / MANIFEST_FILE).is_file() else []


def load_manifest(package_dir: Path) -> dict[str, Any]:
    with (Path(package_dir) / MANIFEST_FILE).open("r", encoding="utf-8-sig") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict) or manifest.get("category") not in {"public", "private"}:
        raise ValueError("Manifest không hợp lệ.")
    if int(manifest.get("version", 0) or 0) not in {3, 4}:
        raise ValueError("Phiên bản manifest không được hỗ trợ.")
    if not isinstance(manifest.get("files"), list):
        raise ValueError("Manifest thiếu danh sách kiểm tra.")
    return manifest


def validate_package(package_dir: Path) -> dict[str, Any]:
    folder = Path(package_dir).resolve()
    manifest = load_manifest(folder)
    category = manifest["category"]
    allowed = set(PUBLIC_SOURCE_MAP if category == "public" else PRIVATE_SOURCE_MAP)
    if category == "public":
        allowed |= PUBLIC_SPECIAL_FILES
    checked: list[str] = []
    for raw in manifest["files"]:
        name = str(raw)
        if Path(name).name != name or name not in allowed:
            raise ValueError(f"File không được phép: {name}")
        source = folder / name
        if not source.is_file():
            raise FileNotFoundError(f"Thiếu file: {name}")
        if source.suffix.lower() == ".json":
            with source.open("r", encoding="utf-8-sig") as handle:
                json.load(handle)
        checked.append(name)
    return {"valid": True, "category": category, "package_id": "current", "files": checked}


def _backup_file(source: Path, backup: Path, backed_up: list[str]) -> None:
    if source.is_file():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
        backed_up.append(backup.as_posix())


def _prepare_rollback(copy_root: Path) -> Path:
    root = Path(copy_root).resolve()
    rollback = (root / "rollback").resolve()
    if rollback.parent != root:
        raise ValueError("Thư mục rollback không hợp lệ.")
    rollback.mkdir(parents=True, exist_ok=True)
    for child in list(rollback.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    return rollback


def _entry_is_eod(entry: Any) -> bool:
    return isinstance(entry, dict) and (
        bool(entry.get("eod_final")) or str(entry.get("day_status", "")).upper() == "EOD"
    )


def _entry_freshness(entry: Any) -> tuple[str, int]:
    if not isinstance(entry, dict):
        return ("", 0)
    return (
        str(entry.get("last_scan") or ""),
        int(entry.get("samples", 0) or 0),
    )


def _merge_raw_cache(target_value: Any, source_value: Any, retention_days: int = 250) -> dict[str, Any]:
    """Gộp đúng một bản ghi cho mỗi mã/ngày: EOD thắng, sau đó bản mới hơn."""
    target = target_value if isinstance(target_value, dict) else {}
    source = source_value if isinstance(source_value, dict) else {}
    merged: dict[str, Any] = dict(target)
    merged.update({key: value for key, value in source.items() if key != "symbols"})
    target_symbols = target.get("symbols", {}) if isinstance(target.get("symbols"), dict) else {}
    source_symbols = source.get("symbols", {}) if isinstance(source.get("symbols"), dict) else {}
    merged_symbols: dict[str, Any] = {}
    for symbol in sorted(set(target_symbols) | set(source_symbols)):
        target_node = target_symbols.get(symbol, {})
        source_node = source_symbols.get(symbol, {})
        node = dict(target_node) if isinstance(target_node, dict) else {}
        if isinstance(source_node, dict):
            node.update({key: value for key, value in source_node.items() if key != "days"})
        target_days = target_node.get("days", {}) if isinstance(target_node, dict) else {}
        source_days = source_node.get("days", {}) if isinstance(source_node, dict) else {}
        if not isinstance(target_days, dict):
            target_days = {}
        if not isinstance(source_days, dict):
            source_days = {}
        days: dict[str, Any] = {}
        for day in sorted(set(target_days) | set(source_days)):
            old = target_days.get(day)
            new = source_days.get(day)
            if old is None:
                chosen = new
            elif new is None:
                chosen = old
            elif _entry_is_eod(new) != _entry_is_eod(old):
                chosen = new if _entry_is_eod(new) else old
            else:
                chosen = new if _entry_freshness(new) >= _entry_freshness(old) else old
            days[str(day)] = chosen
        keep = max(1, int(retention_days or 250))
        if len(days) > keep:
            days = {day: days[day] for day in sorted(days)[-keep:]}
        node["days"] = days
        merged_symbols[str(symbol).upper()] = node
    merged["symbols"] = merged_symbols
    merged["schema_version"] = max(
        int(target.get("schema_version", 0) or 0),
        int(source.get("schema_version", 0) or 0),
        2,
    )
    merged["updated_at"] = max(
        str(target.get("updated_at") or ""),
        str(source.get("updated_at") or ""),
    ) or None
    return merged


def _raw_retention_days(target_account_dir: Path) -> int:
    path = target_account_dir / "brain_settings.json"
    if path.is_file():
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            return max(1, int(value.get("SCAN_SNAPSHOT_RETENTION_DAYS", 250) or 250))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return 250


def _restore_raw_cache(source: Path, target: Path, retention_days: int) -> None:
    with source.open("r", encoding="utf-8-sig") as handle:
        source_value = json.load(handle)
    if target.is_file():
        with target.open("r", encoding="utf-8-sig") as handle:
            target_value = json.load(handle)
        value = _merge_raw_cache(target_value, source_value, retention_days)
    else:
        value = _merge_raw_cache({}, source_value, retention_days)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.import.tmp")
    _write_json(temp, value)
    os.replace(temp, target)


def import_settings(
    package_dir: Path,
    target_account_dir: Path,
    copy_root: Path = COPY_ROOT,
    stamp: str | None = None,
    env_path: Path | None = None,
    _rollback_dir: Path | None = None,
) -> dict[str, Any]:
    del stamp
    folder = Path(package_dir).resolve()
    check = validate_package(folder)
    manifest = load_manifest(folder)
    target_account_dir = Path(target_account_dir).resolve()
    env_path = Path(env_path) if env_path is not None else PROJECT_ROOT / ".env"
    backup_dir = Path(_rollback_dir) if _rollback_dir is not None else _prepare_rollback(copy_root)
    restored: list[str] = []
    backed_up: list[str] = []
    targets = manifest.get("targets", {}) if isinstance(manifest.get("targets"), dict) else {}

    for name in manifest["files"]:
        source = folder / name
        if name == "portable_settings.json":
            current_public_env = {
                key: value
                for key, value in _read_env_values(env_path).items()
                if key in PUBLIC_ENV_KEYS and not _is_sensitive_key(key)
            }
            if current_public_env:
                _write_json(backup_dir / "portable_settings.json", current_public_env)
                backed_up.append("portable_settings.json")
            with source.open("r", encoding="utf-8-sig") as handle:
                values = json.load(handle)
            _write_safe_env_values(env_path, values if isinstance(values, dict) else {})
            restored.append("watchlist/cấu hình chung")
            continue
        if name == "templates_bundle.json":
            with source.open("r", encoding="utf-8-sig") as handle:
                templates = json.load(handle)
            if not isinstance(templates, dict):
                raise ValueError("templates_bundle.json không hợp lệ.")
            for raw_relative, value in templates.items():
                relative = _safe_target(Path(str(raw_relative)))
                if relative.suffix.lower() != ".json":
                    continue
                target = target_account_dir / "templates" / relative
                _backup_file(target, backup_dir / "templates" / relative, backed_up)
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_json(target, value)
                restored.append(f"templates/{relative.as_posix()}")
            continue
        target_relative = _safe_target(Path(str(targets.get(name, ""))))
        expected = (PUBLIC_SOURCE_MAP if check["category"] == "public" else PRIVATE_SOURCE_MAP).get(name)
        if expected is None or target_relative != expected:
            raise ValueError(f"Đích khôi phục không hợp lệ: {name}")
        target = target_account_dir / target_relative
        _backup_file(target, backup_dir / check["category"] / target_relative, backed_up)
        if name == "scan_snapshot_cache.json":
            _restore_raw_cache(source, target, _raw_retention_days(target_account_dir))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f".{target.name}.import.tmp")
            shutil.copy2(source, temp)
            os.replace(temp, target)
        restored.append(target_relative.as_posix())
    return {
        "restored": restored,
        "backed_up": backed_up,
        "backup_dir": str(backup_dir) if backed_up else "",
        "category": check["category"],
        "package_id": "current",
        "target_account": target_account_dir.name,
    }


def import_split_settings(public_package_dir: Path, target_account_dir: Path, copy_root: Path = COPY_ROOT, include_private: bool = True, env_path: Path | None = None) -> dict[str, Any]:
    public_check = validate_package(public_package_dir)
    if public_check["category"] != "public":
        raise ValueError("Gói chính phải là PUBLIC.")
    private_result = None
    private_dir = Path(copy_root) / "private"
    if include_private and (private_dir / MANIFEST_FILE).is_file():
        private_check = validate_package(private_dir)
        if private_check["category"] != "private":
            raise ValueError("Gói PRIVATE không hợp lệ.")
    rollback_dir = _prepare_rollback(copy_root)
    public_result = import_settings(
        public_package_dir,
        target_account_dir,
        copy_root,
        env_path=env_path,
        _rollback_dir=rollback_dir,
    )
    if include_private and (private_dir / MANIFEST_FILE).is_file():
        private_result = import_settings(
            private_dir,
            target_account_dir,
            copy_root,
            env_path=env_path,
            _rollback_dir=rollback_dir,
        )
    legacy_backups = Path(copy_root) / "_backups"
    if legacy_backups.is_dir():
        shutil.rmtree(legacy_backups)
    return {
        "package_id": "current",
        "public": public_result,
        "private": private_result,
        "restored": len(public_result["restored"]) + (len(private_result["restored"]) if private_result else 0),
    }


def delete_package(package_dir: Path, copy_root: Path = COPY_ROOT, delete_pair: bool = True) -> list[str]:
    del package_dir, delete_pair
    return [str(path) for path in clear_generated_packages(copy_root)]


def open_copy_folder(copy_root: Path = COPY_ROOT) -> None:
    ensure_copy_layout(copy_root)
    if os.name != "nt":
        raise OSError("Nút mở thư mục hiện chỉ hỗ trợ Windows.")
    os.startfile(str(Path(copy_root).resolve()))  # type: ignore[attr-defined]


def _choose(title: str, items: list[Path]) -> Path | None:
    if not items:
        print("Không có dữ liệu phù hợp.")
        return None
    print(f"\n{title}")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {item.name}")
    print("  0. Quay lại")
    while True:
        raw = input("Chọn: ").strip()
        if raw == "0":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("Lựa chọn không hợp lệ.")


def main() -> int:
    if os.name == "nt":
        os.system("chcp 65001 >nul")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ensure_copy_layout()
    while True:
        print("\n1. Tạo/ghi đè bản sao  2. Khôi phục  3. Kiểm tra  4. Xóa  5. Mở thư mục  0. Thoát")
        choice = input("Chọn: ").strip()
        try:
            if choice == "1":
                account = _choose("TÀI KHOẢN NGUỒN", discover_accounts())
                if account:
                    result = export_split_settings(account)
                    print(f"Đã ghi PUBLIC {len(result['public_files'])}, PRIVATE {len(result['private_files'])} file.")
            elif choice == "2":
                target = _choose("TÀI KHOẢN ĐÍCH", discover_accounts())
                if target and input("Gõ YES để khôi phục: ").upper() == "YES":
                    print(f"Đã khôi phục {import_split_settings(PUBLIC_COPY_ROOT, target)['restored']} file.")
                    if input("Mở app ngay? (Y/N): ").strip().upper() == "Y":
                        subprocess.Popen([sys.executable, str(PROJECT_ROOT / "main.py")], cwd=str(PROJECT_ROOT))
                        return 0
            elif choice == "3":
                print(validate_package(PUBLIC_COPY_ROOT))
            elif choice == "4":
                if input("Gõ DELETE: ").upper() == "DELETE":
                    clear_generated_packages()
                    print("Đã xóa.")
            elif choice == "5":
                open_copy_folder()
            elif choice == "0":
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"LỖI: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
