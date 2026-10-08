"""Apply the agreed VA allocation/E settings; no broker or Telegram calls."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from viking_v2 import config
from viking_v2.exit_modes import normalize_exit_modes
from viking_v2.trading.durable import AccountLease

PRESET_PATH = PROJECT_ROOT / "support" / "presets" / "VA_4_MA_50M.json"
PRESET_FIELDS = {
    "watchlist", "priority_symbols", "priority_capital_enabled", "priority_total_capital",
    "priority_allocations", "market_phase_override_enabled", "market_phase_override_exposure_pct",
    "bot_em_modes", "rule_parameters",
}
PRESET_RULE_FIELDS = {"max_positions", "indicator_exit_policy"}


def prepared_settings(account_id: str) -> config.AppSettings:
    path = config.account_root(account_id) / "settings.json"
    # Do not use load_settings' recovery defaults to overwrite a damaged file.
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("settings.json khong hop le; khong ghi de.")
    current = config.AppSettings.from_dict(raw).to_dict()
    preset = json.loads(PRESET_PATH.read_text(encoding="utf-8-sig"))
    if (not isinstance(preset, dict) or set(preset) != PRESET_FIELDS
            or not isinstance(preset["rule_parameters"], dict)
            or set(preset["rule_parameters"]) != PRESET_RULE_FIELDS
            or preset["bot_em_modes"] != ["IND_EXIT"]
            or preset["rule_parameters"]["indicator_exit_policy"] != "AUTO"):
        raise ValueError("Preset sai pham vi; chi nap bo von VA va E AUTO da chot.")
    rules = {**current["rule_parameters"], **preset["rule_parameters"]}
    # Enable E on future BOT trades without removing TP/PROTECT already selected.
    # Existing trades and their management flags stay in their own runtime store.
    modes = normalize_exit_modes([*current["bot_em_modes"], *preset["bot_em_modes"]])
    settings = config.AppSettings.from_dict(
        {**current, **preset, "rule_parameters": rules, "bot_em_modes": modes}
    )
    config.validate_priority_capital(settings.priority_total_capital,
                                    settings.priority_symbols, settings.priority_allocations)
    return settings


def apply_preset(account_id: str) -> tuple[Path, Path]:
    root = config.account_root(account_id)
    with ExitStack() as locks:
        # Both GUI/execution and a detached market worker must be stopped.
        for name in ("execution.lock", "market-worker.lock"):
            lease = AccountLease(root, name)
            locks.callback(lease.close)
        settings = prepared_settings(account_id)
        target = root / "settings.json"
        backup = root / f"settings.before-va-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.bak"
        shutil.copy2(target, backup)
        config.save_settings(settings, account_id)
    return target, backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="Workspace; mac dinh tai khoan API da luu tren may nay.")
    parser.add_argument("--yes", action="store_true", help="Xac nhan nap preset.")
    args = parser.parse_args()
    account = config.normalize_account_id(args.account or config.active_account_id())
    try:
        settings = prepared_settings(account)
        print(f"[VA] Tai khoan tren MAY NAY: {account} | so {'PAPER' if settings.paper_mode else 'REAL'}")
        print("[VA] MSN 15 / CTS 15 / HDB 15 / IDC 5 trieu; moi ma dung 100%, MAX LENH 1.")
        print("[VA] Tong 50 trieu; P1 override 100%; toi da 4 ma. Chi thay setting bo thu.")
        print("[IDC] 5 trieu = 50 - 15 - 15 - 15; khong muon ngan sach 3 ma dau.")
        print("[E] AUTO: tu tao SELL 100% khi du dieu kien. Bat E mac dinh cho trade BOT moi.")
        print("[E] Vi the dang co chi ap dung AUTO neu E da bat; khong tu gan E vao vi the cu.")
        print("[GIU] API, token, Telegram, EMA/RSI, SL/PROTECT/TP, gio mua va toan bo giao dich.")
        print(f"[GIO MUA] {'Tu ' + str(settings.rule_parameters['buy_window_start']) if settings.rule_parameters['buy_window_enabled'] else 'Trong phien, theo tin hieu'}")
        if not args.yes and input("Nap vao dung tai khoan nay? [y/N]: ").strip().lower() != "y":
            print("[HUY] Khong doi setting.")
            return 0
        target, backup = apply_preset(account)
        print(f"[OK] {target}")
        print(f"[BACKUP] {backup}")
        print("[NEXT] Chon BAT > 3; kiem tra 4 ma / han muc, OTP va Telegram; BUY BOT van OFF khi mo app.")
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, EOFError) as exc:
        print(f"[LOI] {exc}. Dong app/daemon truoc; khong reset runtime.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
