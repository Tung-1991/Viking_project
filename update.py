# -*- coding: utf-8 -*-
"""Kéo code mới và ghi đè setting PUBLIC vào tài khoản trên VPS."""

from __future__ import annotations

import subprocess

from settings_transfer import (
    DATA_ROOT,
    PROJECT_ROOT,
    PUBLIC_COPY_ROOT,
    _read_env_values,
    discover_accounts,
    import_settings,
)


def main() -> None:
    subprocess.run(
        ["git", "restore", "--", "data/copy/public"],
        cwd=PROJECT_ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "pull", "--ff-only", "origin", "main"],
        cwd=PROJECT_ROOT,
        check=True,
    )

    account_no = _read_env_values(PROJECT_ROOT / ".env").get("DNSE_ACCOUNT_NO", "").strip()
    target = DATA_ROOT / account_no if account_no else None
    if target is None or not target.is_dir():
        accounts = [path for path in discover_accounts() if path.name.isdigit()]
        if len(accounts) != 1:
            raise RuntimeError("Không xác định được tài khoản đích.")
        target = accounts[0]

    try:
        result = import_settings(
            PUBLIC_COPY_ROOT,
            target,
            env_path=PROJECT_ROOT / ".env",
        )
    except ValueError as exc:
        raise SystemExit(
            f"UPDATE STOPPED: PUBLIC package manifest mismatch: {exc}"
        ) from None
    print(f"Đã cập nhật code và ghi đè {len(result['restored'])} mục PUBLIC vào {target.name}.")


if __name__ == "__main__":
    main()
