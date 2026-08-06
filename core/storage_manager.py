# -*- coding: utf-8 -*-
import json
import os
import csv
import time
import copy
import hashlib
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import config 

STATE_FILE = "data/bot_state.json"
BRAIN_FILE = "data/brain_settings.json"
HISTORY_FILE = "data/trade_history_log.csv" 
MASTER_LOG_FILE = "data/trade_history_master.csv"
SYMBOL_OVERRIDES_FILE = "data/symbol_overrides.json"
SYSTEM_META_FILE = "data/system_meta.json"
GROUP_STATUS_TRACKER_FILE = "data/group_status_tracker.json"

_active_account_dir = "data"
_active_account_id = None
_state_lock = threading.RLock()


def _atomic_json_write(path: str, data: Any, attempts: int = 5) -> bool:
    """Ghi JSON an toàn khi UI/daemon cùng lưu trên Windows."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _state_lock:
        for attempt in range(attempts):
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(data if isinstance(data, dict) else {}, handle, indent=4, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
                return True
            except (PermissionError, OSError):
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
                if attempt + 1 < attempts:
                    time.sleep(0.05)
    return False


def _move_legacy_file(old_path: str, new_path: str):
    try:
        if old_path == new_path:
            return
        if os.path.exists(old_path) and not os.path.exists(new_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.replace(old_path, new_path)
    except Exception:
        pass


def _derive_trade_times(time_display, session_id, open_time_str="", close_time_str="", default_close_now=True):
    if close_time_str:
        return open_time_str or "", close_time_str
    close_dt = None
    open_dt = None
    try:
        if open_time_str:
            open_dt = datetime.fromisoformat(str(open_time_str))
    except Exception:
        open_dt = None
    try:
        sid = str(session_id or "")
        if len(sid) >= 8 and sid[:8].isdigit():
            base_date = datetime.strptime(sid[:8], "%Y%m%d").date()
            parts = str(time_display or "").split("->")
            open_part = parts[0].strip()
            close_part = parts[-1].strip()
            if len(close_part) >= 8:
                close_dt = datetime.combine(base_date, datetime.strptime(close_part[:8], "%H:%M:%S").time())
            if not open_dt and len(open_part) >= 8:
                open_dt = datetime.combine(base_date, datetime.strptime(open_part[:8], "%H:%M:%S").time())
    except Exception:
        pass
    if not default_close_now and not close_time_str and not close_dt:
        return (
            open_dt.isoformat(timespec="seconds") if open_dt else (open_time_str or ""),
            "",
        )
    close_dt = close_dt or datetime.now()
    return (
        open_dt.isoformat(timespec="seconds") if open_dt else (open_time_str or ""),
        close_dt.isoformat(timespec="seconds"),
    )


def _normalize_master_log_schema():
    try:
        if not os.path.exists(MASTER_LOG_FILE):
            return
        with open(MASTER_LOG_FILE, mode="r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            rows = [r for r in reader if r]
        if not header:
            return
        changed = False
        for col in ["MAE ($)", "MFE ($)", "Open Time", "Close Time"]:
            if col not in header:
                header.append(col)
                changed = True
        idx = {name: i for i, name in enumerate(header)}
        for row in rows:
            while len(row) < len(header):
                row.append("")
                changed = True
            ot_idx = idx.get("Open Time")
            ct_idx = idx.get("Close Time")
            if ot_idx is not None and ct_idx is not None:
                session_id = row[13] if len(row) > 13 else ""
                has_session_date = len(str(session_id or "")) >= 8 and str(session_id or "")[:8].isdigit()
                if row[ct_idx] and not row[ot_idx] and not has_session_date:
                    row[ct_idx] = ""
                    changed = True
                if row[ct_idx]:
                    continue
                old_open, old_close = _derive_trade_times(
                    row[0] if len(row) > 0 else "",
                    session_id,
                    open_time_str=row[ot_idx] if ot_idx < len(row) else "",
                    default_close_now=False,
                )
                if old_open and not row[ot_idx]:
                    row[ot_idx] = old_open
                    changed = True
                if old_close:
                    row[ct_idx] = old_close
                    changed = True
        if changed:
            with open(MASTER_LOG_FILE, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(rows)
    except Exception:
        pass

def _merge_timestamp_map(state: Dict[str, Any], current_state: Dict[str, Any], key: str):
    current_map = current_state.get(key, {})
    next_map = state.setdefault(key, {})
    if not isinstance(current_map, dict) or not isinstance(next_map, dict):
        return

    for symbol, current_ts in current_map.items():
        try:
            if float(current_ts) > float(next_map.get(symbol, 0.0)):
                next_map[symbol] = current_ts
        except (TypeError, ValueError):
            if symbol not in next_map:
                next_map[symbol] = current_ts

def set_active_account(account_id: str):
    global _active_account_dir, _active_account_id
    global STATE_FILE, BRAIN_FILE, HISTORY_FILE, MASTER_LOG_FILE, SYMBOL_OVERRIDES_FILE, SYSTEM_META_FILE, GROUP_STATUS_TRACKER_FILE
    
    _active_account_id = str(account_id)
    _active_account_dir = os.path.join("data", str(account_id))
    os.makedirs(_active_account_dir, exist_ok=True)
    history_dir = os.path.join(_active_account_dir, "history")
    os.makedirs(history_dir, exist_ok=True)
    
    STATE_FILE = os.path.join(_active_account_dir, "bot_state.json")
    BRAIN_FILE = os.path.join(_active_account_dir, "brain_settings.json")
    HISTORY_FILE = os.path.join(history_dir, "trade_history_log.csv")
    MASTER_LOG_FILE = os.path.join(history_dir, "trade_history_master.csv")
    SYMBOL_OVERRIDES_FILE = os.path.join(_active_account_dir, "symbol_overrides.json")
    SYSTEM_META_FILE = os.path.join(_active_account_dir, "system_meta.json")
    GROUP_STATUS_TRACKER_FILE = os.path.join(_active_account_dir, "group_status_tracker.json")
    _move_legacy_file(os.path.join(_active_account_dir, "trade_history_log.csv"), HISTORY_FILE)
    _move_legacy_file(os.path.join(_active_account_dir, "trade_history_master.csv"), MASTER_LOG_FILE)
    _normalize_master_log_schema()
    
    invalidate_settings_cache()

def load_group_status_tracker() -> Dict[str, Any]:
    if not os.path.exists(GROUP_STATUS_TRACKER_FILE):
        return {}
    try:
        with open(GROUP_STATUS_TRACKER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_group_status_tracker(tracker: Dict[str, Any]):
    os.makedirs(os.path.dirname(GROUP_STATUS_TRACKER_FILE), exist_ok=True)
    tmp_file = f"{GROUP_STATUS_TRACKER_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(tracker, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, GROUP_STATUS_TRACKER_FILE)

def update_group_status_tracker(contexts: Dict[str, Any], tracker: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    QOL-only tracker for Sandbox preview status duration.
    It observes group_details status and never feeds trading decisions.
    """
    if tracker is None:
        tracker = load_group_status_tracker()
    if not isinstance(contexts, dict):
        return tracker

    now = time.time()
    changed = False
    for symbol, ctx in contexts.items():
        if not isinstance(ctx, dict):
            continue
        group_details = ctx.get("group_details", {})
        if not isinstance(group_details, dict):
            continue
        for grp in ["G0", "G1", "G2", "G3"]:
            data = group_details.get(grp, {})
            if not isinstance(data, dict):
                continue
            try:
                status = int(data.get("status", 0))
            except (TypeError, ValueError):
                status = 0
            key = f"{symbol}:{grp}"
            state = tracker.get(key)
            if not isinstance(state, dict):
                tracker[key] = {
                    "status": status,
                    "since": now,
                    "last_duration": {},
                    "updated_at": now,
                }
                changed = True
                continue

            try:
                prev_status = int(state.get("status", 0))
            except (TypeError, ValueError):
                prev_status = 0
            if prev_status != status:
                try:
                    prev_since = float(state.get("since", now))
                except (TypeError, ValueError):
                    prev_since = now
                last_duration = state.setdefault("last_duration", {})
                if not isinstance(last_duration, dict):
                    last_duration = {}
                    state["last_duration"] = last_duration
                last_duration[str(prev_status)] = max(0.0, now - prev_since)
                state["status"] = status
                state["since"] = now
                state["updated_at"] = now
                changed = True

    if changed:
        save_group_status_tracker(tracker)
    return tracker

def get_magic_numbers() -> Dict[str, int]:
    """
    Đọc system_meta.json để lấy cặp MagicNumber. Nếu chưa có, tạo mới không trùng lặp.
    """
    import random

    def generate_unique(used_magics):
        while True:
            m = random.randint(1000, 99999)
            if m not in used_magics:
                used_magics.add(m)
                return m
    
    if os.path.exists(SYSTEM_META_FILE):
        try:
            with open(SYSTEM_META_FILE, "r") as f:
                data = json.load(f)
                if "bot_magic" in data and "manual_magic" in data:
                    used_magics = set()
                    for k, v in data.items():
                        if k.endswith("_magic"):
                            try:
                                used_magics.add(int(v))
                            except Exception:
                                pass
                    changed = False
                    if "grid_magic" not in data:
                        data["grid_magic"] = generate_unique(used_magics)
                        changed = True
                    if "hedge_magic" not in data:
                        data["hedge_magic"] = generate_unique(used_magics)
                        changed = True
                    if changed:
                        os.makedirs(_active_account_dir, exist_ok=True)
                        with open(SYSTEM_META_FILE, "w") as wf:
                            json.dump(data, wf, indent=4)
                    return data
        except:
            pass

    # Quét toàn bộ data/ để tìm các Magic Number đã dùng
    used_magics = set()
    if os.path.exists("data"):
        for folder in os.listdir("data"):
            meta_path = os.path.join("data", folder, "system_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        d = json.load(f)
                        if "bot_magic" in d: used_magics.add(int(d["bot_magic"]))
                        if "manual_magic" in d: used_magics.add(int(d["manual_magic"]))
                        if "grid_magic" in d: used_magics.add(int(d["grid_magic"]))
                        if "hedge_magic" in d: used_magics.add(int(d["hedge_magic"]))
                except:
                    pass

    # Sinh 2 số mới
    def generate_unique(used_magics=used_magics):
        while True:
            m = random.randint(1000, 99999)
            if m not in used_magics:
                used_magics.add(m)
                return m

    new_bot_magic = generate_unique()
    new_manual_magic = generate_unique()
    new_grid_magic = generate_unique()
    new_hedge_magic = generate_unique()
    
    meta_data = {
        "bot_magic": new_bot_magic,
        "manual_magic": new_manual_magic,
        "grid_magic": new_grid_magic,
        "hedge_magic": new_hedge_magic
    }
    
    os.makedirs(_active_account_dir, exist_ok=True)
    with open(SYSTEM_META_FILE, "w") as f:
        json.dump(meta_data, f, indent=4)
        
    return meta_data

# ==================== IN-MEMORY CACHE (TTL 2s) ====================
_cache_brain = {"data": None, "ts": 0.0}        # Cache brain_settings.json
_cache_overrides = {"data": None, "ts": 0.0}     # Cache symbol_overrides.json
_cache_merged = {}                                # Cache kết quả merge theo symbol
_CACHE_TTL = 2.0                                  # Thời gian sống cache (giây)

def invalidate_settings_cache():
    """Xóa cache khi UI lưu config mới. Gọi hàm này sau mỗi lần save."""
    _cache_brain["data"] = None
    _cache_brain["ts"] = 0.0
    _cache_overrides["data"] = None
    _cache_overrides["ts"] = 0.0
    _cache_merged.clear()

def get_reset_hour():
    try:
        bs = load_brain_settings()
        if "bot_safeguard" in bs and "RESET_HOUR" in bs["bot_safeguard"]:
            return int(bs["bot_safeguard"]["RESET_HOUR"])
    except:
        pass
    return getattr(config, "RESET_HOUR", 6)

def get_today_str(now=None):
    now = now or datetime.now()
    reset_hour = get_reset_hour()
    if now.hour < reset_hour:
        prev_day = now - timedelta(days=1)
        return prev_day.strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")

def _new_session_id(now=None):
    return (now or datetime.now()).strftime("%Y%m%d_%H%M%S")

def apply_state_defaults(state: Dict[str, Any]) -> Dict[str, Any]:
    if "daily_history" not in state: state["daily_history"] = []
    if "tsl_disabled_tickets" not in state: state["tsl_disabled_tickets"] = []
    if "trade_tactics" not in state: state["trade_tactics"] = {}
    if "entry_exit_tactics" not in state: state["entry_exit_tactics"] = {}
    if "pending_entry_exit" not in state: state["pending_entry_exit"] = {}
    if "initial_r_dist" not in state: state["initial_r_dist"] = {}
    if "initial_r_usd" not in state: state["initial_r_usd"] = {}
    if "parent_baskets" not in state: state["parent_baskets"] = {}
    if "child_to_parent" not in state: state["child_to_parent"] = {}
    if "last_child_bar_time" not in state: state["last_child_bar_time"] = {}
    if "bot_last_entry_times" not in state: state["bot_last_entry_times"] = {}
    if "bot_last_fail_times" not in state: state["bot_last_fail_times"] = {}
    if "exit_reasons" not in state: state["exit_reasons"] = {}
    if "last_close_times" not in state: state["last_close_times"] = {}
    if "last_dca_pca_close_time" not in state: state["last_dca_pca_close_time"] = {}
    if "last_dca_pca_signal_time" not in state: state["last_dca_pca_signal_time"] = {}
    if "bot_pnl_today" not in state: state["bot_pnl_today"] = 0.0
    if "bot_trades_today" not in state: state["bot_trades_today"] = 0
    if "bot_daily_loss_count" not in state: state["bot_daily_loss_count"] = 0
    if "bot_losing_streak" not in state: state["bot_losing_streak"] = 0
    if "bot_symbol_losing_streak" not in state: state["bot_symbol_losing_streak"] = {}
    if "daily_loss_count" not in state: state["daily_loss_count"] = 0
    if "fee_today" not in state: state["fee_today"] = 0.0
    if "manual_pnl_today" not in state: state["manual_pnl_today"] = 0.0
    if "manual_trades_today" not in state: state["manual_trades_today"] = 0
    if "manual_daily_loss_count" not in state: state["manual_daily_loss_count"] = 0
    if "highest_pnl_recorded" not in state: state["highest_pnl_recorded"] = {}
    if "highest_pnl_tickets" not in state: state["highest_pnl_tickets"] = {}
    if "trade_excursions" not in state: state["trade_excursions"] = {}
    if "anti_cash_locks" not in state: state["anti_cash_locks"] = {}
    if "be_sl_locks" not in state: state["be_sl_locks"] = {}
    if "be_sl_arms" not in state: state["be_sl_arms"] = {}
    if "rev_confirmations" not in state: state["rev_confirmations"] = {}
    if "current_session_id" not in state: state["current_session_id"] = _new_session_id()
    if "cooldown_until" not in state: state["cooldown_until"] = 0.0
    if "active_brake" not in state: state["active_brake"] = {"global": None, "symbols": {}}
    if "volatility_events" not in state: state["volatility_events"] = []
    if "volatility_symbol_cooldowns" not in state: state["volatility_symbol_cooldowns"] = {}
    return state

def rollover_daily_session(state: Dict[str, Any], now=None) -> bool:
    """Move to a new daily session while preserving active safeguard brakes."""
    now = now or datetime.now()
    apply_state_defaults(state)
    current_date = get_today_str(now)
    saved_date = state.get("date")
    if saved_date == current_date:
        return False

    save_daily_history_to_csv(
        saved_date,
        state.get("pnl_today", 0),
        state.get("trades_today_count", 0),
        0,
        state.get("losing_streak", 0),
    )

    state["date"] = current_date
    state["pnl_today"] = 0.0
    state["fee_today"] = 0.0
    state["trades_today_count"] = 0
    state["daily_loss_count"] = 0
    state["losing_streak"] = 0
    state["bot_pnl_today"] = 0.0
    state["bot_trades_today"] = 0
    state["bot_daily_loss_count"] = 0
    state["bot_losing_streak"] = 0
    state["bot_symbol_losing_streak"] = {}
    state["manual_pnl_today"] = 0.0
    state["manual_trades_today"] = 0
    state["manual_daily_loss_count"] = 0
    state["highest_pnl_recorded"] = {}
    state["highest_pnl_tickets"] = {}
    state["trade_excursions"] = {}
    state["anti_cash_locks"] = {}
    state["be_sl_locks"] = {}
    state["be_sl_arms"] = {}
    state["current_session_id"] = _new_session_id(now)
    return True

def _normalize_active_brake(state: Dict[str, Any]) -> Dict[str, Any]:
    brake = state.get("active_brake")
    if not isinstance(brake, dict) or "scope" in brake:
        brake = {"global": brake if isinstance(brake, dict) else None, "symbols": {}}
    if not isinstance(brake.get("symbols"), dict):
        brake["symbols"] = {}
    if "global" not in brake:
        brake["global"] = None
    state["active_brake"] = brake
    return brake

def _brake_until(item: Any) -> float:
    if not isinstance(item, dict):
        return 0.0
    try:
        return float(item.get("until", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0

def get_active_safeguard_brake(state: Dict[str, Any], scope: str, symbol: str = None, now=None):
    """Return an active brake item for the requested scope, if one is still valid."""
    now = time.time() if now is None else float(now)
    brake = _normalize_active_brake(state)
    scope = str(scope or "").upper()
    item = brake.get("symbols", {}).get(symbol) if scope == "SYMBOL" and symbol else brake.get("global")
    return item if _brake_until(item) > now else None

def mark_safeguard_brake(state: Dict[str, Any], scope: str, reason: str, until: float, symbol: str = None, trigger: Dict[str, Any] = None):
    """Arm a safeguard brake. Existing active brakes win and are not extended."""
    apply_state_defaults(state)
    brake = _normalize_active_brake(state)
    scope = str(scope or "").upper()
    now = time.time()
    existing = get_active_safeguard_brake(state, scope, symbol=symbol, now=now)
    if existing:
        return existing, False

    item = {
        "scope": scope,
        "symbol": symbol,
        "reason": reason,
        "until": float(until or 0.0),
        "trigger": copy.deepcopy(trigger or {}),
        "created_at": now,
    }
    if scope == "SYMBOL" and symbol:
        brake["symbols"][symbol] = item
    else:
        brake["global"] = item
    state["active_brake"] = brake
    return item, True

def _clear_safeguard_runtime_state(state: Dict[str, Any], symbol: str = None, clear_symbol_cooldowns: bool = True):
    state["bot_pnl_today"] = 0.0
    state["bot_trades_today"] = 0
    state["bot_daily_loss_count"] = 0
    state["bot_losing_streak"] = 0
    state["daily_loss_count"] = 0
    state["losing_streak"] = 0
    if symbol:
        state.setdefault("bot_symbol_losing_streak", {})[symbol] = 0
        state.setdefault("bot_last_fail_times", {}).pop(symbol, None)
    else:
        state["bot_symbol_losing_streak"] = {}
        if clear_symbol_cooldowns:
            state["bot_last_fail_times"] = {}

def release_expired_safeguard_brakes(state: Dict[str, Any], now=None) -> bool:
    """Release expired safeguard punishment so old counters cannot re-lock the bot."""
    now = time.time() if now is None else float(now)
    apply_state_defaults(state)
    changed = False
    brake = _normalize_active_brake(state)

    try:
        global_until = float(state.get("cooldown_until", 0.0) or 0.0)
    except (TypeError, ValueError):
        global_until = 0.0

    if global_until > 0 and now >= global_until:
        state["cooldown_until"] = 0.0
        brake["global"] = None
        _clear_safeguard_runtime_state(state, clear_symbol_cooldowns=False)
        changed = True
    else:
        global_brake = brake.get("global")
        if isinstance(global_brake, dict):
            try:
                brake_until = float(global_brake.get("until", 0.0) or 0.0)
            except (TypeError, ValueError):
                brake_until = 0.0
            if brake_until > 0 and now >= brake_until:
                state["cooldown_until"] = 0.0
                brake["global"] = None
                _clear_safeguard_runtime_state(state, clear_symbol_cooldowns=False)
                changed = True

    fail_times = state.get("bot_last_fail_times", {})
    if isinstance(fail_times, dict):
        for sym, deadline in list(fail_times.items()):
            try:
                deadline_f = float(deadline or 0.0)
            except (TypeError, ValueError):
                deadline_f = 0.0
            if deadline_f > 0 and now >= deadline_f:
                _clear_safeguard_runtime_state(state, sym)
                brake["symbols"].pop(sym, None)
                changed = True

    for sym, item in list(brake.get("symbols", {}).items()):
        try:
            until = float(item.get("until", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            until = 0.0
        if until > 0 and now >= until:
            _clear_safeguard_runtime_state(state, sym)
            brake["symbols"].pop(sym, None)
            changed = True

    state["active_brake"] = brake
    return changed

def append_trade_log(ticket, symbol, type_str, volume, entry_price, sl, tp, fee, pnl, close_reason, market_mode="ANY", trigger_signal="UNK", session_id="LEGACY", open_time_str="", mae_usd=0.0, mfe_usd=0.0, close_time_str="", exit_price=0.0):
    _state_lock.acquire()
    try:
        file_exists = os.path.isfile(MASTER_LOG_FILE)
        now_str = close_time_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_display = f"{open_time_str[11:]} -> {now_str[11:]}" if open_time_str else now_str
        open_time_full, close_time_full = _derive_trade_times(
            time_display, session_id, open_time_str=open_time_str, close_time_str=now_str
        )
        header = [
            "Time", "Ticket", "Symbol", "Type", "Vol", "Entry", "SL", "TP", "Fee",
            "PnL ($)", "Reason", "Market Mode", "Trigger", "Session_ID", "MAE ($)",
            "MFE ($)", "Open Time", "Close Time", "Exit Price",
        ]
        new_row = [
            time_display, ticket, symbol, type_str, volume,
            f"{entry_price:.5f}", f"{sl:.5f}", f"{tp:.5f}", f"{fee:.2f}",
            f"{pnl:.2f}", close_reason, market_mode, trigger_signal, session_id,
            f"{mae_usd:.2f}", f"{mfe_usd:.2f}", open_time_full, close_time_full,
            f"{float(exit_price or 0.0):.5f}",
        ]

        def reason_rank(reason):
            normalized = str(reason or "").strip().upper()
            if normalized in ("MANUAL_CLOSE", "WATERMARK_HIT", "BASKET_DRAWDOWN_HIT"):
                return 100
            if normalized.startswith("SL_") or normalized in ("HIT_TP", "BASKET_TP", "BASKET_TP_ORDER_LOSS", "HIT_SL", "STOP_OUT", "SL", "TP"):
                return 80
            if normalized in ("BOT_CLOSE", "CLOSED"):
                return 10
            return 50

        rows = []
        if file_exists:
            with open(MASTER_LOG_FILE, mode='r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                existing_header = next(reader, None)
                rows = [r for r in reader if r]
                if existing_header:
                    header = existing_header
                    for col in ["MAE ($)", "MFE ($)", "Open Time", "Close Time", "Exit Price"]:
                        if col not in header:
                            header.append(col)
                    idx = {name: i for i, name in enumerate(header)}
                    for row in rows:
                        while len(row) < len(header):
                            row.append("")
                        ot_idx = idx.get("Open Time")
                        ct_idx = idx.get("Close Time")
                        if ot_idx is not None and ct_idx is not None:
                            session_id_old = row[13] if len(row) > 13 else ""
                            has_session_date = len(str(session_id_old or "")) >= 8 and str(session_id_old or "")[:8].isdigit()
                            if row[ct_idx] and not row[ot_idx] and not has_session_date:
                                row[ct_idx] = ""
                            if row[ct_idx]:
                                continue
                            old_open, old_close = _derive_trade_times(
                                row[0] if len(row) > 0 else "",
                                session_id_old,
                                open_time_str=row[ot_idx] if ot_idx < len(row) else "",
                                default_close_now=False,
                            )
                            row[ot_idx] = row[ot_idx] or old_open
                            row[ct_idx] = old_close

        ticket_str = str(ticket)
        replaced = False
        for idx, row in enumerate(rows):
            if len(row) >= 2 and str(row[1]) == ticket_str:
                old_reason = row[10] if len(row) > 10 else ""
                if reason_rank(close_reason) >= reason_rank(old_reason):
                    rows[idx] = [str(v) for v in new_row]
                replaced = True
                break

        if not replaced:
            rows.append([str(v) for v in new_row])

        tmp_log_file = f"{MASTER_LOG_FILE}.tmp"
        with open(tmp_log_file, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(tmp_log_file, MASTER_LOG_FILE)

        try:
            from ai_advisor.history import record_closed_trade

            record_closed_trade(
                ticket,
                symbol,
                type_str,
                volume,
                entry_price,
                sl,
                tp,
                fee,
                pnl,
                close_reason,
                market_mode=market_mode,
                trigger_signal=trigger_signal,
                session_id=session_id,
                open_time_str=open_time_str,
                mae_usd=mae_usd,
                mfe_usd=mfe_usd,
                exit_time=close_time_full,
                exit_price=exit_price,
                state=load_state(),
            )
        except Exception:
            pass
    except:
        pass
    finally:
        _state_lock.release()

def delete_session_log(session_id: str):
    """Xóa tất cả các lệnh thuộc một Session cụ thể khỏi CSV"""
    if not os.path.exists(MASTER_LOG_FILE):
        return
    try:
        rows = []
        with open(MASTER_LOG_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header:
                rows.append(header)
                for row in reader:
                    # Nếu có Session_ID (cột cuối) và nó khớp thì bỏ qua (xóa)
                    # Support cả format cũ (10 cột) và mới (14 cột)
                    row_session_id = row[13] if len(row) >= 14 else row[-1]
                    if len(row) >= 10 and row_session_id == session_id:
                        continue
                    rows.append(row)
        
        with open(MASTER_LOG_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(rows)
    except:
        pass


def _master_row_close_day(row, header):
    index = {name: idx for idx, name in enumerate(header or [])}
    close_idx = index.get("Close Time")
    if close_idx is not None and close_idx < len(row) and row[close_idx]:
        try:
            return datetime.fromisoformat(str(row[close_idx]).replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            pass
    session_idx = index.get("Session_ID", 13)
    session_id = str(row[session_idx] if session_idx < len(row) else "")
    digits = "".join(ch for ch in session_id if ch.isdigit())[:8]
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    time_idx = index.get("Time", 0)
    value = str(row[time_idx] if time_idx < len(row) else "")
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def delete_history_day(day: str) -> int:
    """Xóa các dòng lịch sử chi tiết theo đúng ngày đóng lệnh."""
    try:
        target = datetime.strptime(str(day or "").strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return 0
    if not os.path.exists(MASTER_LOG_FILE):
        return 0
    with _state_lock:
        try:
            with open(MASTER_LOG_FILE, mode="r", newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if not header:
                    return 0
                rows = [row for row in reader if row]
            kept = [row for row in rows if _master_row_close_day(row, header) != target]
            removed = len(rows) - len(kept)
            if removed:
                tmp_path = f"{MASTER_LOG_FILE}.tmp"
                with open(tmp_path, mode="w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(header)
                    writer.writerows(kept)
                os.replace(tmp_path, MASTER_LOG_FILE)
            return removed
        except (OSError, csv.Error):
            return 0

def save_daily_history_to_csv(prev_date, pnl, trades_count, win_streak, lose_streak):
    file_exists = os.path.isfile(HISTORY_FILE)
    try:
        with open(HISTORY_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Date", "PnL ($)", "Total Trades", "End Streak"])
            streak_str = f"L{lose_streak}" if lose_streak > 0 else f"W{win_streak}"
            writer.writerow([prev_date, f"{pnl:.2f}", trades_count, streak_str])
    except:
        pass

def load_state(paper: Any = None) -> Dict[str, Any]:
    requested_paper = (
        bool(getattr(config, "PAPER_TRADING", True))
        if paper is None
        else bool(paper)
    )
    default_state = {
        "date": get_today_str(),
        "pnl_today": 0.0,
        "fee_today": 0.0,
        "starting_balance": 0.0,
        "trades_today_count": 0,
        "losing_streak": 0,
        "bot_losing_streak": 0,
        "bot_symbol_losing_streak": {},
        "daily_loss_count": 0,
        "active_trades": [],
        "tsl_disabled_tickets": [], 
        "daily_history": [],
        "trade_tactics": {},
        "entry_exit_tactics": {},
        "pending_entry_exit": {},
        "initial_r_dist": {},
        "initial_r_usd": {},
        "parent_baskets": {},       
        "child_to_parent": {},       
        "last_child_bar_time": {},
        "bot_last_entry_times": {},
        "exit_reasons": {},          # [NEW V4.4] Tracking lý do đóng lệnh
        "last_close_times": {},      # [NEW V4.4] Tracking thời gian đóng lệnh cho Cooldown
        "last_dca_pca_close_time": {}, # [NEW V4.4] Tracking DCA/PCA Cooldown
        "last_dca_pca_signal_time": {},# [NEW V5] Tracking Cooldown theo tín hiệu bóp cò
        "highest_pnl_recorded": {},    # [NEW V5] Đỉnh PnL cho Watermark
        "highest_pnl_tickets": {},     # Ticket set đang tạo mốc Watermark
        "last_rev_log_time": {},       # [NEW V5] Bộ nhớ chống spam log đảo chiều
        "trade_excursions": {},
        "anti_cash_locks": {},
        "be_sl_locks": {},
        "rev_confirmations": {},
        "current_session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "cooldown_until": 0.0,
        "state_mode": "PAPER" if requested_paper else "REAL",
    }

    state_file = _mode_state_file(requested_paper)
    os.makedirs(os.path.dirname(state_file), exist_ok=True)

    # bot_state.json là file cũ dùng chung. Chỉ nhập một lần vào đúng chế độ
    # dựa trên ticket; tuyệt đối không để PAPER và REAL tiếp tục ghi đè nhau.
    source_file = state_file
    if not os.path.exists(source_file) and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as legacy_file:
                legacy_state = json.load(legacy_file)
            tickets = list(legacy_state.get("active_trades", []) or [])
            tickets.extend((legacy_state.get("trade_symbols", {}) or {}).keys())
            has_paper = any(str(ticket).upper().startswith("PAPER-") for ticket in tickets)
            has_real = any(not str(ticket).upper().startswith("PAPER-") for ticket in tickets)
            paper_mode = requested_paper
            if (paper_mode and (has_paper or not has_real)) or ((not paper_mode) and has_real):
                source_file = STATE_FILE
        except Exception:
            source_file = state_file

    if not os.path.exists(source_file):
        return apply_state_defaults(default_state)

    try:
        with open(source_file, "r", encoding="utf-8") as f:
            state = json.load(f)
            apply_state_defaults(state)
            state["state_mode"] = "PAPER" if requested_paper else "REAL"
            if source_file == STATE_FILE and state["state_mode"] == "PAPER":
                # PNL PAPER cũ từng có thể bị tính sai hệ số. Sổ PaperBroker mới là
                # nguồn thật và sẽ đồng bộ lại; không mang con số legacy sang file mới.
                state["pnl_today"] = 0.0
                state["fee_today"] = 0.0
                state["bot_pnl_today"] = 0.0
            changed = rollover_daily_session(state)
            changed = release_expired_safeguard_brakes(state) or changed
            if changed or source_file != state_file:
                save_state(state)
            return state
    except:
        return apply_state_defaults(default_state)


def _mode_state_file(paper: Any = None) -> str:
    """Runtime state riêng cho PAPER/REAL; STATE_FILE vẫn là đường dẫn legacy."""
    is_paper = bool(getattr(config, "PAPER_TRADING", True)) if paper is None else bool(paper)
    stem, ext = os.path.splitext(STATE_FILE)
    return f"{stem}.{'paper' if is_paper else 'real'}{ext or '.json'}"


def save_state(state: Dict[str, Any]):
    with _state_lock:
        try:
            state_mode = str(state.get("state_mode", "") or "").upper()
            if state_mode not in ("PAPER", "REAL"):
                state_mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
            state_file = _mode_state_file(state_mode == "PAPER")
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            state["state_mode"] = state_mode
            if os.path.exists(state_file):
                with open(state_file, "r", encoding="utf-8") as current_file:
                    current_state = json.load(current_file)
                _merge_timestamp_map(state, current_state, "last_dca_pca_close_time")
                _merge_timestamp_map(state, current_state, "last_dca_pca_signal_time")

            tmp_file = f"{state_file}.tmp"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=4, ensure_ascii=False)
            os.replace(tmp_file, state_file)
        except:
            pass

def reset_bot_session(reason="Manual"):
    """Dọn dẹp cache bot hiện hành và tạo Session_ID mới"""
    state = load_state()
    apply_state_defaults(state)
    # Save session tổng kết (nếu cần thiết)
    
    # Đặt lại cache của bot
    state["bot_pnl_today"] = 0.0
    state["bot_trades_today"] = 0
    state["bot_daily_loss_count"] = 0
    state["bot_losing_streak"] = 0
    state["bot_symbol_losing_streak"] = {}
    state["losing_streak"] = 0
    state["cooldown_until"] = 0.0
    state["bot_last_fail_times"] = {}
    state["bot_last_entry_times"] = {}
    state["last_close_times"] = {}
    state["active_brake"] = {"global": None, "symbols": {}}
    
    # Tạo Session_ID mới
    state["current_session_id"] = _new_session_id()
    save_state(state)

def get_last_dca_pca_close_time(symbol: str) -> float:
    with _state_lock:
        state = load_state()
        return state.get("last_dca_pca_close_time", {}).get(symbol, 0.0)

def update_last_dca_pca_close_time(symbol: str, timestamp: float):
    with _state_lock:
        state = load_state()
        if "last_dca_pca_close_time" not in state:
            state["last_dca_pca_close_time"] = {}
        state["last_dca_pca_close_time"][symbol] = timestamp
        save_state(state)

def load_brain_settings() -> Dict[str, Any]:
    sandbox_defaults = getattr(config, "SANDBOX_CONFIG", {})
    default_entry_exit = {
        "enabled": True,
        "preview_only": False,
        "active_tactics": ["SWING_REJECTION"],
        "entry_tactics": ["SWING_REJECTION"],
        "exit_tactic": "FALLBACK_R",
        "sl_mode": "SANDBOX",
        "fallback_tactic": "OFF",
        "signal_ttl_seconds": 900,
        "missing_data_policy": "BLOCK",
        "tp_policy": "TACTIC_FIRST",
        "sl_source_group": "BASE_SL",
        "default_exit": {
            "use_rr_tp": True,
            "tp_rr_ratio": 1.5,
            "use_swing_tp": False,
        },
        "sl_distance": {"min_atr": 0.3, "max_atr": 2.0},
        "fib_retrace": {
            "swing_source_group": "G2",
            "entry_levels": "0.5,0.618",
            "entry_tolerance_atr": 0.15,
            "tp_levels": "1.272,1.618",
            "use_tactic_tp": True,
        },
        "breakout_retest": {
            "source_group": "G2",
            "max_bars_after_breakout": 6,
            "retest_atr": 0.5,
            "use_tactic_tp": False,
        },
        "swing_rejection": {
            "source_group": "G2",
            "max_atr_from_swing": 0.7,
            "sl_atr_buffer": 0.2,
            "require_rejection_candle": False,
            "allow_breakout_entry": False,
            "max_breakout_atr": 0.5,
        },
        "swing_structure": {
            "source_group": "G2",
            "entry_atr": 0.7,
            "sl_atr_buffer": 0.2,
            "allow_breakout_entry": True,
            "max_breakout_atr": 0.5,
        },
        "pullback_zone": {
            "source": "EMA20",
            "max_atr_from_zone": 0.5,
            "sl_atr_buffer": 0.2,
            "tp_atr_multiplier": 1.5,
        },
        "bb_reclaim": {"band": "MID", "max_atr_from_band": 0.5},
    }
    default_brain = {
        "MASTER_EVAL_MODE": getattr(config, "MASTER_EVAL_MODE", "VETO"),
        "MIN_MATCHING_VOTES": getattr(config, "MIN_MATCHING_VOTES", 3),
        "FORCE_ANY_MODE": getattr(config, "FORCE_ANY_MODE", False),
        "G0_TIMEFRAME": getattr(config, "G0_TIMEFRAME", "1d"),
        "G1_TIMEFRAME": getattr(config, "G1_TIMEFRAME", "1h"),
        "G2_TIMEFRAME": getattr(config, "G2_TIMEFRAME", "15m"),
        "G3_TIMEFRAME": getattr(config, "G3_TIMEFRAME", "15m"),
        "BOT_ACTIVE_SYMBOLS": copy.deepcopy(getattr(config, "BOT_ACTIVE_SYMBOLS", [])),
        "PRIORITY_SYMBOLS": copy.deepcopy(getattr(config, "PRIORITY_SYMBOLS", [])),
        "SCAN_SNAPSHOT_ENABLED": bool(getattr(config, "SCAN_SNAPSHOT_ENABLED", True)),
        "SCAN_SNAPSHOT_INTERVAL_MINUTES": float(
            getattr(config, "SCAN_SNAPSHOT_INTERVAL_MINUTES", 15)
        ),
        "SCAN_SNAPSHOT_RETENTION_DAYS": int(
            getattr(config, "SCAN_SNAPSHOT_RETENTION_DAYS", 250)
        ),
        "SCAN_SNAPSHOT_SYMBOLS": copy.deepcopy(
            getattr(
                config,
                "SCAN_SNAPSHOT_SYMBOLS",
                list(getattr(config, "CKPS_SYMBOLS", []) or [])
                + list(getattr(config, "CKCS_WATCHLIST", []) or []),
            )
        ),
        "voting_rules": copy.deepcopy(sandbox_defaults.get("voting_rules", {
            "G0": {"max_opposite": 0, "max_none": 0, "master_rule": "FIX"},
            "G1": {"max_opposite": 0, "max_none": 1, "master_rule": "PASS"},
            "G2": {"max_opposite": 0, "max_none": 2, "master_rule": "PASS"},
            "G3": {"max_opposite": 0, "max_none": 1, "master_rule": "IGNORE"}
        })),
        "risk_tsl": {
            "base_risk": getattr(config, "BOT_RISK_PERCENT", 0.3),
            "base_sl": getattr(config, "BOT_BASE_SL", "G2"),
            "sl_atr_multiplier": getattr(config, "sl_atr_multiplier", 0.2),
            "tsl_mode": getattr(config, "TSL_LOGIC_MODE", "STATIC"),
            "bot_tsl": getattr(config, "BOT_DEFAULT_TSL", "BE+STEP_R+SWING"),
            "mode_multipliers": {
                "ANY": 1.0,
                "TREND": 1.0,
                "RANGE": 0.5,
                "BREAKOUT": 1.5,
                "EXHAUSTION": 1.0,
            },
            "strict_risk": getattr(config, "STRICT_RISK_CALC", False),
        },
        "indicators": copy.deepcopy(sandbox_defaults.get("indicators", {})),
        "check_indicators": copy.deepcopy(getattr(config, "CHECK_INDICATORS", {})),
        "dca_config": copy.deepcopy(getattr(config, "DCA_CONFIG", {})),
        "pca_config": copy.deepcopy(getattr(config, "PCA_CONFIG", {})),
        "entry_exit": copy.deepcopy(default_entry_exit),
        "manual_margin": copy.deepcopy(getattr(config, "MANUAL_MARGIN_CONFIG", {})),
        "market_calendar": copy.deepcopy(getattr(config, "MARKET_CALENDAR_DEFAULT", {})),
        "opportunity_settings": copy.deepcopy(getattr(config, "BOT_OPPORTUNITY_DEFAULT", {})),
        # Lịch Advisor/CKCS là setting theo tài khoản. Giữ nguyên khối này khi
        # load/save brain_settings; nếu thiếu, UI sẽ rơi về mặc định sau restart.
        "ai_advisor_schedule": {},
        "bot_safeguard": copy.deepcopy(getattr(config, "BOT_SAFEGUARD", {})),
        "TSL_CONFIG": copy.deepcopy(getattr(config, "TSL_CONFIG", {})),
        "TSL_LOGIC_MODE": getattr(config, "TSL_LOGIC_MODE", "STATIC"),
        "symbol_configs": {},
    }

    def merge_dict(dst, src):
        for key, val in src.items():
            if isinstance(val, dict) and isinstance(dst.get(key), dict):
                merge_dict(dst[key], val)
            else:
                dst[key] = copy.deepcopy(val)
    
    os.makedirs(os.path.dirname(BRAIN_FILE), exist_ok=True)
    if not os.path.exists(BRAIN_FILE):
        return _normalize_brain_settings_shape(default_brain)

    try:
        with open(BRAIN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

            legacy_sandbox = data.get("SANDBOX_CONFIG", {})
            if isinstance(legacy_sandbox, dict):
                if "voting_rules" not in data and isinstance(legacy_sandbox.get("voting_rules"), dict):
                    merge_dict(default_brain["voting_rules"], legacy_sandbox["voting_rules"])
                if "indicators" not in data and isinstance(legacy_sandbox.get("indicators"), dict):
                    merge_dict(default_brain["indicators"], legacy_sandbox["indicators"])
            
            for key in [
                "MASTER_EVAL_MODE",
                "MIN_MATCHING_VOTES",
                "FORCE_ANY_MODE",
                "G0_TIMEFRAME",
                "G1_TIMEFRAME",
                "G2_TIMEFRAME",
                "G3_TIMEFRAME",
                "BOT_ACTIVE_SYMBOLS",
                "PRIORITY_SYMBOLS",
                "TSL_LOGIC_MODE",
                # [SCAN SNAPSHOT] Phải nằm trong whitelist, không thì mỗi lần app save
                # brain_settings sẽ strip mất -> toggle live của daemon chết, chỉ còn fallback env.
                "SCAN_SNAPSHOT_ENABLED",
                "SCAN_SNAPSHOT_INTERVAL_MINUTES",
                "SCAN_SNAPSHOT_RETENTION_DAYS",
                "SCAN_SNAPSHOT_SYMBOLS",
            ]:
                if key in data:
                    default_brain[key] = copy.deepcopy(data[key])

            if "BOT_SAFEGUARD" in data and "bot_safeguard" not in data:
                merge_dict(default_brain["bot_safeguard"], data["BOT_SAFEGUARD"])
            if "TSL_LOGIC_MODE" in data and (
                "risk_tsl" not in data or "tsl_mode" not in data.get("risk_tsl", {})
            ):
                default_brain["risk_tsl"]["tsl_mode"] = data["TSL_LOGIC_MODE"]
            
            if "voting_rules" in data:
                for grp in ["G0", "G1", "G2", "G3"]:
                    if grp in data["voting_rules"]:
                        merge_dict(default_brain["voting_rules"][grp], data["voting_rules"][grp])
            
            if "indicators" in data:
                for ind, cfg in data["indicators"].items():
                    if ind in default_brain["indicators"]:
                        merge_dict(default_brain["indicators"][ind], cfg)
                    else:
                        default_brain["indicators"][ind] = copy.deepcopy(cfg)

            if "check_indicators" in data and isinstance(data["check_indicators"], dict):
                for ind, cfg in data["check_indicators"].items():
                    if ind in default_brain["check_indicators"] and isinstance(cfg, dict):
                        merge_dict(default_brain["check_indicators"][ind], cfg)
                    elif isinstance(cfg, dict):
                        default_brain["check_indicators"][ind] = copy.deepcopy(cfg)

            for key in [
                "risk_tsl",
                "entry_exit",
                "dca_config",
                "pca_config",
                "manual_margin",
                "market_calendar",
                "opportunity_settings",
                "ai_advisor_schedule",
                "bot_safeguard",
                "TSL_CONFIG",
                "symbol_configs",
            ]:
                if key in data and isinstance(data[key], dict):
                    merge_dict(default_brain[key], data[key])

            if default_brain.get("risk_tsl", {}).get("tsl_mode"):
                default_brain["TSL_LOGIC_MODE"] = default_brain["risk_tsl"]["tsl_mode"]

            return _normalize_brain_settings_shape(default_brain)
    except:
        return _normalize_brain_settings_shape(default_brain)

def save_brain_settings(data: Dict[str, Any]):
    try:
        if not _atomic_json_write(BRAIN_FILE, data):
            return False
        invalidate_settings_cache()
        try:
            from ai_advisor.history import ensure_config_snapshot, record_event

            snapshot_id = ensure_config_snapshot(reason="save_brain_settings")
            record_event(
                "config_saved",
                "brain_settings.json saved",
                payload={"source": BRAIN_FILE, "config_snapshot_id": snapshot_id},
            )
        except Exception:
            pass
        return True
    except Exception:
        return False

def _normalize_brain_settings_shape(data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from core.market_calendar import normalize_settings

        data["market_calendar"] = normalize_settings(data.get("market_calendar", {}))
    except Exception:
        data["market_calendar"] = copy.deepcopy(getattr(config, "MARKET_CALENDAR_DEFAULT", {}))

    try:
        from core.signal_opportunities import normalize_settings as normalize_opportunity_settings

        data["opportunity_settings"] = normalize_opportunity_settings(data.get("opportunity_settings", {}))
    except Exception:
        data["opportunity_settings"] = copy.deepcopy(getattr(config, "BOT_OPPORTUNITY_DEFAULT", {}))

    # E/E BOT mặc định là cổng WAIT/READY. Manual Preview là luồng riêng.
    entry_exit = data.get("entry_exit")
    if isinstance(entry_exit, dict):
        entry_exit["preview_only"] = bool(entry_exit.get("preview_only", False))

    indicators = data.get("indicators", {})
    if not isinstance(indicators, dict):
        data["indicators"] = {}
        return data

    valid_groups = {"G0", "G1", "G2", "G3"}
    valid_macro_roles = {"NONE", "BASE", "BREAKOUT", "EXHAUSTION"}
    valid_modes = {"ANY", "TREND", "RANGE", "BREAKOUT", "EXHAUSTION"}
    valid_trigger_modes = {"STRICT_CLOSE", "REALTIME_TICK"}

    for ind_name, cfg in indicators.items():
        if not isinstance(cfg, dict):
            continue

        groups = cfg.get("groups")
        if groups is None:
            groups = [cfg.get("group", "G2")]
        elif isinstance(groups, str):
            groups = [groups]
        elif not isinstance(groups, list):
            groups = list(groups) if isinstance(groups, (tuple, set)) else []
        groups = [str(g).upper() for g in groups if str(g).upper() in valid_groups]
        cfg["groups"] = groups or ["G2"]

        modes = cfg.get("active_modes", ["ANY"])
        if isinstance(modes, str):
            modes = [modes]
        elif not isinstance(modes, list):
            modes = ["ANY"]
        modes = [str(m).upper() for m in modes if str(m).upper() in valid_modes]
        cfg["active_modes"] = modes or ["ANY"]

        macro_role = str(cfg.get("macro_role", "NONE")).upper()
        cfg["macro_role"] = macro_role if macro_role in valid_macro_roles else "NONE"

        trigger_mode = str(cfg.get("trigger_mode", "STRICT_CLOSE")).upper()
        cfg["trigger_mode"] = trigger_mode if trigger_mode in valid_trigger_modes else "STRICT_CLOSE"

        group_trigger_modes = cfg.get("group_trigger_modes", {})
        if not isinstance(group_trigger_modes, dict):
            group_trigger_modes = {}
        cfg["group_trigger_modes"] = {
            str(g).upper(): str(mode).upper()
            for g, mode in group_trigger_modes.items()
            if str(g).upper() in valid_groups and str(mode).upper() in valid_trigger_modes
        }

        group_params = cfg.get("group_params", {})
        if not isinstance(group_params, dict):
            group_params = {}
        cfg["group_params"] = {
            str(g).upper(): params
            for g, params in group_params.items()
            if str(g).upper() in valid_groups and isinstance(params, dict)
        }

        if ind_name == "simple_breakout":
            params = cfg.get("params", {})
            if not isinstance(params, dict):
                params = {}
            if "atr_buffer" not in params and "buffer_points" in params:
                params["atr_buffer"] = params["buffer_points"]
            params.pop("buffer_points", None)
            cfg["params"] = params
            for params in cfg["group_params"].values():
                if "atr_buffer" not in params and "buffer_points" in params:
                    params["atr_buffer"] = params["buffer_points"]
                params.pop("buffer_points", None)

    check_indicators = data.get("check_indicators", {})
    if not isinstance(check_indicators, dict):
        data["check_indicators"] = {}
        check_indicators = data["check_indicators"]
    for ind_name, cfg in check_indicators.items():
        if not isinstance(cfg, dict):
            continue
        cfg["active"] = bool(cfg.get("active", False))
        groups = cfg.get("groups", ["G2"])
        if isinstance(groups, str):
            groups = [groups]
        cfg["groups"] = [str(g).upper() for g in groups if str(g).upper() in valid_groups]
        if not cfg["groups"]:
            cfg["groups"] = ["G2"]
        if not isinstance(cfg.get("params"), dict):
            cfg["params"] = {}
        group_params = cfg.get("group_params", {})
        if not isinstance(group_params, dict):
            group_params = {}
        cfg["group_params"] = {
            str(g).upper(): params
            for g, params in group_params.items()
            if str(g).upper() in valid_groups and isinstance(params, dict)
        }
        if ind_name == "simple_breakout":
            params = cfg["params"]
            if "atr_buffer" not in params and "buffer_points" in params:
                params["atr_buffer"] = params["buffer_points"]
            params.pop("buffer_points", None)

    return data

# =====================================================================
# [NEW V4.4] SYMBOL OVERRIDES (MẸ - CON)
# =====================================================================
# SYMBOL_OVERRIDES_FILE is now dynamically updated in set_active_account

def load_symbol_overrides() -> Dict[str, Any]:
    try:
        if os.path.exists(SYMBOL_OVERRIDES_FILE):
            with open(SYMBOL_OVERRIDES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def save_symbol_overrides(data: Dict[str, Any]):
    try:
        if not _atomic_json_write(SYMBOL_OVERRIDES_FILE, data):
            return False
        invalidate_settings_cache()  # Xóa cache khi lưu override mới
        try:
            from ai_advisor.history import ensure_config_snapshot, record_event

            snapshot_id = ensure_config_snapshot(reason="save_symbol_overrides")
            record_event(
                "config_saved",
                "symbol_overrides.json saved",
                payload={"source": SYMBOL_OVERRIDES_FILE, "config_snapshot_id": snapshot_id},
            )
        except Exception:
            pass
        return True
    except Exception:
        return False

def _load_brain_cached() -> Dict[str, Any]:
    """Đọc brain_settings.json với cache TTL."""
    now = time.monotonic()
    if _cache_brain["data"] is not None and (now - _cache_brain["ts"]) < _CACHE_TTL:
        return _cache_brain["data"]
    
    data = load_brain_settings()
    
    _cache_brain["data"] = data
    _cache_brain["ts"] = now
    return data

def _load_overrides_cached() -> Dict[str, Any]:
    """Đọc symbol_overrides.json với cache TTL."""
    now = time.monotonic()
    if _cache_overrides["data"] is not None and (now - _cache_overrides["ts"]) < _CACHE_TTL:
        return _cache_overrides["data"]
    
    data = load_symbol_overrides()
    _cache_overrides["data"] = data
    _cache_overrides["ts"] = now
    return data

def get_brain_settings_for_symbol(symbol: str = None) -> Dict[str, Any]:
    """
    Hàm chuẩn mới: Đọc toàn bộ brain_settings.json (Mẹ),
    sau đó nếu có symbol và symbol có config riêng, sẽ merge đè lên.
    Kết quả được cache trong RAM (TTL 2s) để tối ưu hiệu năng.
    """
    # Cache key: symbol hoặc "__GLOBAL__"
    cache_key = symbol or "__GLOBAL__"
    now = time.monotonic()
    
    # Kiểm tra cache merged đã có và còn hạn không
    if cache_key in _cache_merged:
        cached = _cache_merged[cache_key]
        if (now - cached["ts"]) < _CACHE_TTL:
            return copy.deepcopy(cached["data"])  # deepcopy để tránh mutation
    
    # Cache miss → đọc file (qua cache layer 1)
    base_brain = copy.deepcopy(_load_brain_cached())
        
    if not symbol:
        _cache_merged[cache_key] = {"data": base_brain, "ts": now}
        return copy.deepcopy(base_brain)
        
    overrides = _load_overrides_cached()
    if symbol in overrides:
        sym_override = overrides[symbol]
        
        # Merge Sandbox config
        if "sandbox" in sym_override:
            sb = sym_override["sandbox"]
            for k in ["MASTER_EVAL_MODE", "MIN_MATCHING_VOTES", "FORCE_ANY_MODE", 
                      "G0_TIMEFRAME", "G1_TIMEFRAME", "G2_TIMEFRAME", "G3_TIMEFRAME"]:
                if k in sb: base_brain[k] = sb[k]
                
            if "voting_rules" in sb:
                if "voting_rules" not in base_brain: base_brain["voting_rules"] = {}
                for grp, rules in sb["voting_rules"].items():
                    base_brain["voting_rules"][grp] = rules
                    
            if "risk_tsl" in sb:
                if "risk_tsl" not in base_brain: base_brain["risk_tsl"] = {}
                base_brain["risk_tsl"].update(sb["risk_tsl"])
                if base_brain["risk_tsl"].get("tsl_mode"):
                    base_brain["TSL_LOGIC_MODE"] = base_brain["risk_tsl"]["tsl_mode"]

            if "entry_exit" in sb:
                if "entry_exit" not in base_brain: base_brain["entry_exit"] = {}
                for k, v in sb["entry_exit"].items():
                    if isinstance(v, dict) and isinstance(base_brain["entry_exit"].get(k), dict):
                        base_brain["entry_exit"][k].update(v)
                    else:
                        base_brain["entry_exit"][k] = v
                
            if "indicators" in sb:
                if "indicators" not in base_brain: base_brain["indicators"] = {}
                for ind_name, ind_cfg in (sb["indicators"] or {}).items():
                    if (
                        isinstance(ind_cfg, dict)
                        and isinstance(base_brain["indicators"].get(ind_name), dict)
                    ):
                        base_brain["indicators"][ind_name].update(copy.deepcopy(ind_cfg))
                    else:
                        base_brain["indicators"][ind_name] = copy.deepcopy(ind_cfg)

            if "check_indicators" in sb:
                base_brain["check_indicators"] = copy.deepcopy(sb["check_indicators"])
                
            if "dca_config" in sb:
                if "dca_config" not in base_brain: base_brain["dca_config"] = {}
                base_brain["dca_config"].update(sb["dca_config"])
                
            if "pca_config" in sb:
                if "pca_config" not in base_brain: base_brain["pca_config"] = {}
                base_brain["pca_config"].update(sb["pca_config"])
                
            if "bot_safeguard" in sb:
                if "bot_safeguard" not in base_brain: base_brain["bot_safeguard"] = {}
                base_brain["bot_safeguard"].update(sb["bot_safeguard"])
                
        # Merge TSL config
        if "tsl" in sym_override:
            tsl = sym_override["tsl"]
            if "TSL_CONFIG" in tsl:
                if "TSL_CONFIG" not in base_brain: base_brain["TSL_CONFIG"] = {}
                base_brain["TSL_CONFIG"].update(tsl["TSL_CONFIG"])
            if "TSL_LOGIC_MODE" in tsl:
                base_brain["TSL_LOGIC_MODE"] = tsl["TSL_LOGIC_MODE"]

        if "entry_exit" in sym_override:
            if "entry_exit" not in base_brain:
                base_brain["entry_exit"] = {}
            for k, v in sym_override["entry_exit"].items():
                if isinstance(v, dict) and isinstance(base_brain["entry_exit"].get(k), dict):
                    base_brain["entry_exit"][k].update(v)
                else:
                    base_brain["entry_exit"][k] = v

    _normalize_brain_settings_shape(base_brain)
    _cache_merged[cache_key] = {"data": base_brain, "ts": now}
    return copy.deepcopy(base_brain)


def brain_strategy_fingerprint(settings: Dict[str, Any]) -> str:
    """Dấu vân tay phần cấu hình trực tiếp sinh signal/group context."""
    settings = settings if isinstance(settings, dict) else {}
    payload = {
        "MASTER_EVAL_MODE": settings.get("MASTER_EVAL_MODE"),
        "MIN_MATCHING_VOTES": settings.get("MIN_MATCHING_VOTES"),
        "FORCE_ANY_MODE": settings.get("FORCE_ANY_MODE"),
        "timeframes": {
            group: settings.get(f"{group}_TIMEFRAME")
            for group in ("G0", "G1", "G2", "G3")
        },
        "voting_rules": settings.get("voting_rules") or {},
        "indicators": settings.get("indicators") or {},
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]

def _dca_pca_signal_key(symbol: str, signal_class: str = None) -> str:
    if not signal_class:
        return symbol
    return f"{symbol}|{str(signal_class).upper()}"

def get_last_dca_pca_signal_time(symbol: str, signal_class: str = None) -> float:
    with _state_lock:
        state = load_state()
        signals = state.get("last_dca_pca_signal_time", {})
        key = _dca_pca_signal_key(symbol, signal_class)
        return signals.get(key, signals.get(symbol, 0.0))

def update_last_dca_pca_signal_time(symbol: str, timestamp: float, signal_class: str = None):
    with _state_lock:
        state = load_state()
        if "last_dca_pca_signal_time" not in state:
            state["last_dca_pca_signal_time"] = {}
        key = _dca_pca_signal_key(symbol, signal_class)
        state["last_dca_pca_signal_time"][key] = timestamp
        save_state(state)
