# -*- coding: utf-8 -*-
"""Export/Import toàn bộ setting ra 1 file JSON duy nhất (mang máy khác import là xong).

Chỉ đóng gói SETTINGS (chiến thuật, watchlist, cấu hình) — KHÔNG đóng gói:
- Bí mật: API key/secret, trading token, bot token Telegram (env chứa KEY/SECRET/TOKEN bị lọc cứng)
- Trạng thái runtime: bot_state, live_signals, pending_orders, lịch sử paper/CSV
Import luôn backup file cũ (.bak_import_<timestamp>) trước khi ghi đè.
"""
import json
import logging
import os
import shutil
import time
from datetime import datetime

logger = logging.getLogger(__name__)

BUNDLE_VERSION = 1

# File settings trong data/<account>/ (tên cố định — import chỉ nhận đúng các tên này)
SETTINGS_FILES = [
    "brain_settings.json",
    "symbol_overrides.json",
    "tsl_settings.json",
    "presets_config.json",
    "advisor_api_settings.json",
    "telegram_settings.json",
]
# File markdown người dùng tự soạn trong data/<account>/advisor/
ADVISOR_MD_FILES = [
    "user_context.md",
    "advisor_prompt.md",
    "advisor_flow.md",
]
# Env không nhạy cảm được mang theo (watchlist, toggle, tuning)
ENV_KEYS = [
    "DNSE_CKPS_WATCHLIST",
    "DNSE_CKCS_WATCHLIST",
    "DNSE_DERIVATIVE_REAL_SYMBOLS",
    "SCAN_SNAPSHOT_ENABLED",
    "SCAN_SNAPSHOT_INTERVAL_MINUTES",
    "SCAN_SNAPSHOT_RETENTION_DAYS",
    "PAPER_TRADING",
    "MONEY_DISPLAY_ZERO_TRIM",
    "DNSE_WS_ENABLED",
    "DNSE_WS_MODE",
    "DNSE_WS_URL",
    "DNSE_WS_ENCODING",
    "DNSE_WS_BOARD_ID",
    "DNSE_WS_RECONCILE_SECONDS",
    "MARKET_PREOPEN_MINUTES",
    "MARKET_HOLIDAYS",
    "DNSE_TICK_CACHE_TTL_SECONDS",
    "DNSE_OHLC_CACHE_TTL_SECONDS",
    "DNSE_OHLC_CACHE_TTL_CLOSED_SECONDS",
    "DNSE_ACCOUNT_CACHE_TTL_SECONDS",
    "DNSE_POSITIONS_CACHE_TTL_SECONDS",
    "DNSE_RATE_LIMIT_RETRIES",
    "DNSE_OHLC_WINDOW_FACTOR_INTRADAY",
    "DNSE_OHLC_WINDOW_FACTOR_DAILY",
    "ADVISOR_API_TIMEOUT_SECONDS",
    "ADVISOR_API_RETRIES",
    "DNSE_DERIVATIVE_TAX_RATE",
    "DNSE_STOCK_TAX_RATE",
    "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE",
]
_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "OTP")


def _is_secret(env_key: str) -> bool:
    upper = str(env_key).upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def _account_dir(account_dir=None) -> str:
    if account_dir:
        return account_dir
    import core.storage_manager as storage_manager
    return os.path.dirname(getattr(storage_manager, "BRAIN_FILE", "data/brain_settings.json")) or "data"


def default_bundle_name(account_dir=None) -> str:
    acc = os.path.basename(_account_dir(account_dir)) or "default"
    return f"ratckvn_settings_{acc}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"


def default_export_dir() -> str:
    """Mặc định lưu bundle vào data/ — quy tụ 1 chỗ, không đẻ thư mục mới."""
    return os.path.abspath("data")


def export_bundle(dest_path: str, account_dir=None) -> dict:
    """Gom toàn bộ settings vào 1 file JSON. Trả về summary."""
    acc_dir = _account_dir(account_dir)
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "app": "RAT-CKVN",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account_id": os.path.basename(acc_dir),
        "files": {},
        "advisor_files": {},
        "env": {},
    }

    for name in SETTINGS_FILES:
        path = os.path.join(acc_dir, name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    bundle["files"][name] = json.load(f)
            except Exception as exc:
                logger.warning(f"config_bundle: bỏ qua {name} (đọc lỗi: {exc})")

    advisor_dir = os.path.join(acc_dir, "advisor")
    for name in ADVISOR_MD_FILES:
        path = os.path.join(advisor_dir, name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    bundle["advisor_files"][name] = f.read()
            except Exception as exc:
                logger.warning(f"config_bundle: bỏ qua advisor/{name} ({exc})")

    try:
        from core import env_utils
        for key in ENV_KEYS:
            if _is_secret(key):
                continue
            value = env_utils.get_env_value(key, None)
            if value is not None and str(value) != "":
                bundle["env"][key] = str(value)
    except Exception as exc:
        logger.warning(f"config_bundle: không đọc được env ({exc})")

    tmp = dest_path + ".tmp"
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)
    os.replace(tmp, dest_path)

    return {
        "ok": True,
        "path": os.path.abspath(dest_path),
        "files": len(bundle["files"]),
        "advisor_files": len(bundle["advisor_files"]),
        "env_keys": len(bundle["env"]),
    }


def import_bundle(src_path: str, account_dir=None) -> dict:
    """Đọc bundle, backup file hiện tại rồi ghi đè settings. Trả về summary."""
    with open(src_path, "r", encoding="utf-8") as f:
        bundle = json.load(f)

    if not isinstance(bundle, dict) or not isinstance(bundle.get("files"), dict):
        raise ValueError("File không phải bundle settings của RAT-CKVN")
    if int(bundle.get("bundle_version", 0)) > BUNDLE_VERSION:
        raise ValueError(
            f"Bundle version {bundle.get('bundle_version')} mới hơn app hỗ trợ ({BUNDLE_VERSION}) — cập nhật app trước"
        )

    acc_dir = _account_dir(account_dir)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backups = []
    restored = []

    # Chỉ nhận đúng tên file trong whitelist (chống path traversal từ bundle lạ)
    for name, data in bundle["files"].items():
        if name not in SETTINGS_FILES or not isinstance(data, dict):
            logger.warning(f"config_bundle: bỏ qua mục lạ '{name}' trong bundle")
            continue
        target = os.path.join(acc_dir, name)
        if os.path.exists(target):
            bak = f"{target}.bak_import_{stamp}"
            shutil.copy2(target, bak)
            backups.append(os.path.basename(bak))
        os.makedirs(acc_dir, exist_ok=True)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp, target)
        restored.append(name)

    advisor_dir = os.path.join(acc_dir, "advisor")
    for name, text in (bundle.get("advisor_files") or {}).items():
        if name not in ADVISOR_MD_FILES or not isinstance(text, str):
            continue
        os.makedirs(advisor_dir, exist_ok=True)
        target = os.path.join(advisor_dir, name)
        if os.path.exists(target):
            bak = f"{target}.bak_import_{stamp}"
            shutil.copy2(target, bak)
            backups.append(os.path.join("advisor", os.path.basename(bak)))
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
        restored.append(f"advisor/{name}")

    env_applied = {}
    env_in = bundle.get("env") or {}
    if env_in:
        try:
            from core import env_utils
            clean = {
                k: str(v) for k, v in env_in.items()
                if k in ENV_KEYS and not _is_secret(k)
            }
            if clean:
                env_utils.update_env(clean)
                env_applied = clean
        except Exception as exc:
            logger.warning(f"config_bundle: không ghi được env ({exc})")

    # Xóa cache settings để app/daemon đọc bản mới ngay
    try:
        from core.storage_manager import invalidate_settings_cache
        invalidate_settings_cache()
    except Exception:
        pass

    return {
        "ok": True,
        "from_account": bundle.get("account_id"),
        "created_at": bundle.get("created_at"),
        "restored": restored,
        "env_keys": len(env_applied),
        "backups": backups,
        "restart_required": True,  # env chỉ nạp lúc khởi động
    }
