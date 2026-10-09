"""Apply agreed VA allocation/E/notification settings; no broker or Telegram calls."""
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
    "bot_em_modes", "rule_parameters", "telegram_notifications", "telegram_cooldown_minutes",
    "telegram_buy_delivery_mode",
    "signal_trace_enabled", "signal_trace_interval_minutes", "signal_trace_start", "signal_trace_end",
}
PRESET_RULE_FIELDS = {"max_positions", "indicator_exit_policy", "buy_signal_require_ema_cross"}


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
            or not isinstance(preset["telegram_notifications"], dict)
            or set(preset["telegram_notifications"]) != set(config.TELEGRAM_NOTIFICATION_DEFAULTS)
            or any(value is not True for value in preset["telegram_notifications"].values())
            or not isinstance(preset["telegram_cooldown_minutes"], dict)
            or preset["telegram_cooldown_minutes"] != {"blocked_buy": 60, "buy_lost": 60, "system": 30}
            or any(type(value) is not int for value in preset["telegram_cooldown_minutes"].values())
            or preset["telegram_buy_delivery_mode"] != "IMMEDIATE"
            or preset["bot_em_modes"] != ["IND_EXIT"]
            or preset["rule_parameters"]["indicator_exit_policy"] != "AUTO"
            or preset["rule_parameters"]["buy_signal_require_ema_cross"] is not True
            or preset["signal_trace_enabled"] is not True
            or preset["signal_trace_interval_minutes"] != 2
            or preset["signal_trace_start"] != "14:00" or preset["signal_trace_end"] != "14:30"):
        raise ValueError("Preset sai pham vi; chi nap von VA, E AUTO, BUY gui ngay, tin ky thuat/PROTECT va gian tin da chot.")
    rules = {**current["rule_parameters"], **preset["rule_parameters"]}
    # Operator-approved VA preset enables every notification category. Keep
    # the connection, unrelated intervals and remembered batching interval.
    notifications = {**current["telegram_notifications"], **preset["telegram_notifications"]}
    cooldowns = {**current["telegram_cooldown_minutes"], **preset["telegram_cooldown_minutes"]}
    # Enable E on future BOT trades without removing TP/PROTECT already selected.
    # Existing trades and their management flags stay in their own runtime store.
    modes = normalize_exit_modes([*current["bot_em_modes"], *preset["bot_em_modes"]])
    settings = config.AppSettings.from_dict(
        {**current, **preset, "rule_parameters": rules, "bot_em_modes": modes,
         "telegram_notifications": notifications, "telegram_cooldown_minutes": cooldowns}
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
        caps = {
            symbol: settings.priority_allocations[symbol]["limit_vnd"] / 1_000_000
            for symbol in settings.priority_symbols
        }
        allocation_review = " / ".join(f"{symbol} {cap:g}" for symbol, cap in caps.items())
        total_millions = settings.priority_total_capital / 1_000_000
        print(f"[VA] {allocation_review} trieu; moi ma dung 100%, MAX LENH 1.")
        print(f"[VA] Tong {total_millions:g} trieu; P1 override {settings.market_phase_override_exposure_pct:g}%; "
              f"toi da {settings.rule_parameters['max_positions']} ma. Chi thay setting bo thu.")
        other_caps = " - ".join(f"{cap:g}" for symbol, cap in caps.items() if symbol != "IDC")
        print(f"[IDC] {caps['IDC']:g} trieu = {total_millions:g} - {other_caps}; "
              "khong muon ngan sach cac ma khac.")
        print("[E] AUTO: tu tao SELL 100% khi du dieu kien. Bat E mac dinh cho trade BOT moi.")
        print("[E] Vi the dang co chi ap dung AUTO neu E da bat; khong tu gan E vao vi the cu.")
        print(f"[TELE] Ket noi {'ON' if settings.telegram_enabled else 'OFF'} (giu cua may nay); "
              "BUY gui ngay (co the chon GOM TIN trong app).")
        for row in (
            (("buy_queued", "BUY"), ("closed", "CLOSED"),
             ("indicator_exit", "E ALERT"), ("blocked_buy", "TIN HIEU")),
            (("protect", "PROTECT"), ("corporate_action", "Lich/quyen"),
             ("external_sell", "Ban mobile"), ("system", "He thong")),
        ):
            print("[TELE] " + " / ".join(
                f"{label} {'ON' if settings.telegram_notifications[key] else 'OFF'}"
                for key, label in row
            ))
        print(f"[TIN HIEU] Gui ngay theo EMA/RSI; gian {settings.telegram_cooldown_minutes['blocked_buy']} phut/ma/so; "
              "khong doi gio mua / khoa von.")
        print(f"[MAT BUY] {'ON' if settings.telegram_notifications['buy_lost'] else 'OFF'}; "
              f"gian {settings.telegram_cooldown_minutes['buy_lost']} phut.")
        print(f"[HE THONG] Gian canh bao {settings.telegram_cooldown_minutes['system']} phut.")
        print("[PROTECT] Bat tin cham muc; khong thay doi cong tac bao ve hay AUTO/ALERT.")
        print("[ENTRY] Bat EMA vua cat len + RSI phien truoc; khong ep mua khi EMA da o tren.")
        print("[TRACE] ON, 2 phut/lan tu 14:00 den 14:30; lich su -> TIN HIEU -> GHI TIN HIEU; mau nam chung tung ma.")
        print("[GIU] API, token, chat ID, cong tac Telegram tong, cac gian tin khac, chu ky EMA/RSI, SL/PROTECT/TP, gio mua va giao dich.")
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
