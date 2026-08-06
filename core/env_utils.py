# -*- coding: utf-8 -*-
"""Helpers for reading and updating the project .env file.

The .env file holds DNSE credentials and selected sub-account numbers. The UI
account picker writes selections back here so they persist across restarts.
The functions below preserve unrelated lines, comments and ordering.
"""

import os
import re
import threading
import uuid
from typing import Dict, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")

# Writing .env from the UI thread while the daemon may also read it: guard writes.
_env_lock = threading.Lock()
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_value(raw: str) -> str:
    return raw.strip().strip('"').strip("'")


def load_env(path: str = DEFAULT_ENV_PATH) -> Dict[str, str]:
    """Parse a .env file into a dict. Missing file -> empty dict."""
    values: Dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            values[key.strip()] = _strip_value(val)
    return values


def apply_env(path: str = DEFAULT_ENV_PATH, override: bool = False) -> Dict[str, str]:
    """Load .env and push values into os.environ. Returns the parsed dict."""
    values = load_env(path)
    for key, val in values.items():
        if override or key not in os.environ:
            os.environ[key] = val
    return values


def get_env_value(key: str, default: Optional[str] = None, path: str = DEFAULT_ENV_PATH) -> Optional[str]:
    """Prefer a live os.environ value, fall back to the .env file, then default."""
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    return load_env(path).get(key, default)


def update_env(updates: Dict[str, str], path: str = DEFAULT_ENV_PATH) -> None:
    """Read-modify-write the .env file for the given key/value pairs.

    Existing keys are updated in place (preserving surrounding lines/comments);
    new keys are appended. Also mirrors values into os.environ so the running
    process sees them immediately.
    """
    if not updates:
        return
    clean_updates = {str(key): str(value) for key, value in updates.items()}
    for key, value in clean_updates.items():
        if not _ENV_NAME_RE.fullmatch(key):
            raise ValueError(f"Invalid environment variable name: {key}")
        if "\x00" in value or "\r" in value or "\n" in value:
            raise ValueError(f"Environment value for {key} contains a line break.")

    with _env_lock:
        lines = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        remaining = dict(clean_updates)
        written = set()
        out_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in clean_updates:
                    # Collapse duplicates: a stale blank duplicate below a newly
                    # written secret otherwise wins on the next read.
                    if key not in written:
                        out_lines.append(f"{key}={clean_updates[key]}\n")
                        written.add(key)
                        remaining.pop(key, None)
                    continue
            out_lines.append(line if line.endswith("\n") else line + "\n")

        if remaining:
            if out_lines and not out_lines[-1].endswith("\n"):
                out_lines[-1] += "\n"
            for key, val in remaining.items():
                out_lines.append(f"{key}={val}\n")

        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        temp_path = os.path.join(
            parent,
            f".{os.path.basename(path)}.{os.getpid()}.{uuid.uuid4().hex}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.writelines(out_lines)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

        saved = load_env(path)
        missing = [key for key, value in clean_updates.items() if saved.get(key) != value]
        if missing:
            raise OSError(f"Could not verify saved environment keys: {', '.join(missing)}")

    for key, val in clean_updates.items():
        os.environ[key] = str(val)


def _write_windows_user_environment(name: str, value: str) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    try:
        import ctypes

        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            0x001A,  # WM_SETTINGCHANGE
            0,
            "Environment",
            0x0002,  # SMTO_ABORTIFHUNG
            2000,
            None,
        )
    except Exception:
        pass


def set_user_environment_secret(name: str, value: str, *, persist: bool = True) -> Dict[str, object]:
    """Lưu secret cho process hiện tại và Windows User Environment.

    Hàm này không ghi vào .env, JSON, log hoặc workspace. Giá trị trả về chỉ
    chứa trạng thái, tuyệt đối không trả lại secret.
    """
    env_name = str(name or "").strip()
    secret = str(value or "").strip()
    if not _ENV_NAME_RE.fullmatch(env_name):
        raise ValueError("Tên biến môi trường không hợp lệ.")
    if not secret:
        raise ValueError(f"{env_name} không được để trống.")
    if "\x00" in secret or "\r" in secret or "\n" in secret:
        raise ValueError("Token không được chứa ký tự xuống dòng.")
    if persist:
        if os.name != "nt":
            raise RuntimeError("Lưu token vĩnh viễn qua UI hiện chỉ hỗ trợ Windows.")
        _write_windows_user_environment(env_name, secret)
    os.environ[env_name] = secret
    return {"ok": True, "name": env_name, "persisted": bool(persist), "length": len(secret)}


def user_environment_secret_present(name: str) -> bool:
    """Chỉ trả có/không; không đưa secret ra UI hoặc log."""
    env_name = str(name or "").strip()
    if not _ENV_NAME_RE.fullmatch(env_name):
        return False
    if os.environ.get(env_name):
        return True
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, env_name)
        return bool(str(value or "").strip())
    except OSError:
        return False
