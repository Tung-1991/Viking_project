# -*- coding: utf-8 -*-
# FILE: bot_daemon.py
# V4.2.1: DECOUPLED THREADS & DYNAMIC TREND COMPASS (FIXED UI SYNC) (KAISER EDITION)

import time
import json
import os
import random
import uuid
import logging
import threading

import config
from core.dnse_connector import DNSEConnector
from core.data_engine import data_engine
from core.market_hours import (
    is_any_network_window_open,
    is_symbol_trade_window_open,
    seconds_until_network_open,
)
from core.position_classifier import is_bot_position, is_manual_position
from core.volatility_brake import VolatilityBrakeDetector, settings_from_safeguard
from signals.signal_generator import signal_generator
from core.storage_manager import get_brain_settings_for_symbol
from core.logger_setup import setup_logging  # [NEW V4.3] Import hệ thống Log

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [DAEMON] %(message)s")
logger = logging.getLogger("BotDaemon")

SIGNAL_FILE = "data/live_signals.json"
SIGNAL_FILE_TMP = SIGNAL_FILE + ".tmp"
BRAIN_SETTINGS_FILE = "data/brain_settings.json"
DEBUG_STATE_FILE = "data/current_signal_state.json"

# Serialize every live-signal write inside this daemon process. RLock is used
# because _add_signal writes while already holding the same guard.
_SIGNAL_WRITE_LOCK = threading.RLock()


def _is_telegram_auto_scale_position(pos, magics, trade_tactics):
    if not is_manual_position(pos, magics):
        return False
    comment = str(getattr(pos, "comment", "") or "")
    if "[USER]_TELEGRAM" not in comment:
        return False
    tactic = str((trade_tactics or {}).get(str(getattr(pos, "ticket", "")), "") or "")
    return "AUTO_DCA" in tactic or "AUTO_PCA" in tactic

def update_daemon_paths(account_id: str):
    global SIGNAL_FILE, SIGNAL_FILE_TMP, BRAIN_SETTINGS_FILE, DEBUG_STATE_FILE
    with _SIGNAL_WRITE_LOCK:
        base_dir = os.path.join("data", str(account_id))
        os.makedirs(base_dir, exist_ok=True)
        SIGNAL_FILE = os.path.join(base_dir, "live_signals.json")
        SIGNAL_FILE_TMP = SIGNAL_FILE + ".tmp"
        BRAIN_SETTINGS_FILE = os.path.join(base_dir, "brain_settings.json")
        DEBUG_STATE_FILE = os.path.join(base_dir, "current_signal_state.json")


class StandaloneBotDaemon:
    def __init__(self):
        self.running = False
        data_engine.configure_market_data_owner(True)
        self.connector = DNSEConnector()
        if not self.connector.connect():
            logger.error("Không thể kết nối DNSE. Daemon sẽ dừng.")
            return

        acc_info = self.connector.get_account_info()
        if acc_info:
            import core.storage_manager as storage_manager
            storage_manager.set_active_account(acc_info['login'])
            update_daemon_paths(acc_info['login'])
            logger.info(f"✅ Daemon đã thiết lập Workspace cho tài khoản: {acc_info['login']}")

        self.dca_pca_interval = 2
        self.last_dca_pca_scan = 0
        self.pending_signals = []
        self.heartbeat_contexts, restored_symbols = self._restore_heartbeat_snapshot()
        self._last_heartbeat_write = 0.0
        self.last_entry_signal_times = {}
        self.volatility_brake = VolatilityBrakeDetector()
        self._volatility_brake_inflight = False
        self._volatility_brake_lock = threading.Lock()
        try:
            import core.storage_manager as storage_manager

            volatility_state = storage_manager.load_state()
            storage_manager.apply_state_defaults(volatility_state)
            self._volatility_brake_latched_until = float(
                volatility_state.get("cooldown_until", 0.0) or 0.0
            )
            self._volatility_symbol_cooldowns = dict(
                volatility_state.get("volatility_symbol_cooldowns") or {}
            )
        except Exception:
            self._volatility_brake_latched_until = 0.0
            self._volatility_symbol_cooldowns = {}
        self._tick_thread = None
        self._active_symbols = restored_symbols
        # Chỉ dọn hàng đợi lệnh cũ. Context cuối dùng cho giá/Preview phải được giữ
        # qua lần restart, đặc biệt khi app mở lại sau giờ giao dịch và không thể quét lại.
        self._atomic_write_signals(self._active_symbols)

    def _restore_heartbeat_snapshot(self):
        """Khôi phục context hiển thị cuối; không khôi phục pending order signal."""
        contexts = {}
        active_symbols = []
        try:
            with open(SIGNAL_FILE, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            heartbeat = payload.get("brain_heartbeat", {}) if isinstance(payload, dict) else {}
            saved_contexts = heartbeat.get("contexts", {}) if isinstance(heartbeat, dict) else {}
            saved_symbols = heartbeat.get("active_symbols", []) if isinstance(heartbeat, dict) else []
            if isinstance(saved_contexts, dict):
                contexts.update(
                    (str(symbol).upper(), dict(context))
                    for symbol, context in saved_contexts.items()
                    if isinstance(context, dict) and not str(symbol).startswith("__")
                )
            if isinstance(saved_symbols, list):
                active_symbols = [str(symbol).upper() for symbol in saved_symbols if str(symbol).strip()]
        except (OSError, ValueError, TypeError):
            pass

        # Nếu file heartbeat đã từng bị ghi rỗng, lấy bản tổng hợp cuối ngày làm
        # phương án khôi phục tối thiểu cho giá, trend và market mode trên Preview.
        if not contexts:
            try:
                account_root = os.path.dirname(os.path.abspath(SIGNAL_FILE))
                # Đọc cache cũ trước để còn fallback CKPS, sau đó CKCS RAW DATA
                # mới ghi đè bằng bản mới hơn. Cả hai đều nằm cùng workspace với
                # live_signals nên đổi tài khoản không thể đọc nhầm thư mục.
                cache_candidates = [
                    os.path.join(account_root, "scan_snapshot_cache.json"),
                    os.path.join(account_root, "ckcs_research", "scan_snapshot_cache.json"),
                ]
                for cache_path in cache_candidates:
                    if not os.path.isfile(cache_path):
                        continue
                    try:
                        with open(cache_path, "r", encoding="utf-8") as handle:
                            cache = json.load(handle)
                    except (OSError, ValueError, TypeError):
                        continue
                    symbols = cache.get("symbols", {}) if isinstance(cache, dict) else {}
                    for symbol, symbol_data in symbols.items():
                        days = symbol_data.get("days", {}) if isinstance(symbol_data, dict) else {}
                        if not isinstance(days, dict) or not days:
                            continue
                        day = days[sorted(days)[-1]]
                        if not isinstance(day, dict):
                            continue
                        price = day.get("price", {}) if isinstance(day.get("price"), dict) else {}
                        bot = day.get("bot", {}) if isinstance(day.get("bot"), dict) else {}
                        current = float(price.get("current", price.get("close", 0.0)) or 0.0)
                        context = dict(bot)
                        if current > 0:
                            context.update(
                                {
                                    "current_price": current,
                                    "bid": current,
                                    "ask": current,
                                    "spread": 0.0,
                                    "synthetic_quote": True,
                                }
                            )
                        if context:
                            contexts[str(symbol).upper()] = context
                if not active_symbols:
                    active_symbols = list(contexts)
            except (OSError, ValueError, TypeError):
                pass

        if not active_symbols:
            configured = getattr(
                config,
                "BOT_ACTIVE_SYMBOLS",
                getattr(config, "SYMBOLS", []),
            )
            active_symbols = [str(symbol).upper() for symbol in (configured or []) if str(symbol).strip()]
        return contexts, active_symbols

    def _get_entry_signal_cooldown(self, symbol):
        try:
            brain = get_brain_settings_for_symbol(symbol)
            cooldown_min = float(brain.get("bot_safeguard", {}).get("COOLDOWN_MINUTES", 1.0))
            return max(0.0, cooldown_min * 60)
        except Exception:
            return 60.0

    def _entry_signal_on_cooldown(self, action, symbol):
        if action == "NONE":
            return False

        cooldown_sec = self._get_entry_signal_cooldown(symbol)
        if cooldown_sec <= 0:
            return False

        key = f"{symbol}_{action}"
        now = time.time()
        last_time = self.last_entry_signal_times.get(key, 0)
        if now - last_time < cooldown_sec:
            return True

        self.last_entry_signal_times[key] = now
        return False

    def _atomic_write_signals(self, active_symbols):
        with _SIGNAL_WRITE_LOCK:
            payload = {
                "brain_heartbeat": {
                    "status": "HEALTHY",
                    "wakeup_time": time.time(),
                    "active_symbols": list(active_symbols or []),
                    "contexts": self.heartbeat_contexts,
                    "market_data_health": data_engine.get_api_health_snapshot(),
                },
                "pending_signals": self.pending_signals[-10:],
            }
            os.makedirs(os.path.dirname(SIGNAL_FILE), exist_ok=True)

            # A unique temp path prevents daemon threads from competing for the
            # same live_signals.json.tmp file. Bounded retries handle the short
            # Windows interval where the UI may still have the destination open.
            max_tries = 20
            last_error = None
            for attempt in range(max_tries):
                temp_path = (
                    f"{SIGNAL_FILE_TMP}.{os.getpid()}."
                    f"{threading.get_ident()}.{uuid.uuid4().hex}"
                )
                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=4)
                    os.replace(temp_path, SIGNAL_FILE)
                    previous_fault = getattr(self, "signal_write_fault", None)
                    self.signal_write_fault = None
                    if previous_fault:
                        logger.info("Ghi tín hiệu đã hoạt động lại bình thường.")
                    self._last_heartbeat_write = time.time()
                    return True
                except (PermissionError, OSError) as exc:
                    last_error = exc
                    try:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
                    except OSError:
                        pass
                    if attempt < max_tries - 1:
                        time.sleep(0.05 + random.uniform(0.0, 0.05))

            # Windows có thể cho tiến trình đọc giữ file nhưng không cho đổi tên/
            # thay thế đích (sharing violation). Khi đó ghi đè trực tiếp dưới
            # cùng RLock. UI đã bỏ qua JSON đang ghi dở và đọc lại ở nhịp sau,
            # nên tín hiệu không bị mất và daemon không mắc kẹt ngoài phiên.
            for attempt in range(max_tries):
                try:
                    with open(SIGNAL_FILE, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=4)
                        f.flush()
                        os.fsync(f.fileno())
                    previous_fault = getattr(self, "signal_write_fault", None)
                    self.signal_write_fault = None
                    if previous_fault:
                        logger.info("Ghi tín hiệu đã hoạt động lại bình thường.")
                    logger.warning(
                        "Windows đang giữ file tín hiệu; daemon đã ghi đè trực tiếp thành công."
                    )
                    return True
                except (PermissionError, OSError) as exc:
                    last_error = exc
                    if attempt < max_tries - 1:
                        time.sleep(0.05 + random.uniform(0.0, 0.05))

            self.signal_write_fault = str(last_error or "unknown write error")
            logger.error(
                f"Lỗi ghi tín hiệu sau {max_tries} lần thử: {self.signal_write_fault}"
            )
            return False

    def _write_signal_debugger(self, debug_state):
        try:
            os.makedirs(os.path.dirname(DEBUG_STATE_FILE), exist_ok=True)
            with open(DEBUG_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "states": debug_state,
                    },
                    f,
                    indent=4,
                    ensure_ascii=False,
                )
        except Exception:
            pass

    def _add_signal(self, action, symbol, context, signal_class="ENTRY"):
        with _SIGNAL_WRITE_LOCK:
            if signal_class == "ENTRY" and self._entry_signal_on_cooldown(action, symbol):
                logger.debug(f"Cooldown signal ENTRY {action} {symbol}: skip duplicate")
                return False

            sig_id = str(uuid.uuid4())
            self.pending_signals.append(
                {
                    "signal_id": sig_id,
                    "timestamp": time.time(),
                    "valid_for": 300 if signal_class == "ENTRY" else 60,
                    "action": action,
                    "symbol": symbol,
                    "signal_class": signal_class,
                    "context": context,
                    "market_mode": context.get("market_mode", "ANY"),
                }
            )
            logger.debug(f"Đã phát tín hiệu {action} cho {symbol} ({signal_class})")
            live_cfg = self._read_live_config()
            syms = live_cfg.get("BOT_ACTIVE_SYMBOLS", getattr(config, "SYMBOLS", []))
            return self._atomic_write_signals(syms)

    def _read_live_config(self):
        try:
            if os.path.exists(BRAIN_SETTINGS_FILE):
                with open(BRAIN_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except:
            pass
        return {}

    def _tick_symbols(self, cache_seconds=15.0):
        """Chọn mã cần tick real-time 2s = mã ĐANG GIỮ VỊ THẾ MỞ (để canh SL/TSL).

        [FIX 429 v2] Bản trước tick vô điều kiện toàn bộ mã CKPS -> VN30F1M (scan-only,
        không bao giờ trade) vẫn bị poll /quotes/latest mỗi 2s -> 429 triền miên trên
        endpoint phái sinh. Tick chỉ có ý nghĩa cho mã đang giữ vị thế; không giữ gì
        thì poll = 0. Vào lệnh dựa trên vòng quét nến (15s), không cần tick 2s.
        Ai muốn tick CKPS kể cả chưa có vị thế (scalping phái sinh) thì bật cờ config.
        """
        now = time.time()
        cached = getattr(self, "_tick_symbols_cache", None)
        if cached and (now - cached[0]) < max(1.0, float(cache_seconds or 15.0)):
            return cached[1]

        picked = []
        def add(symbol):
            symbol = str(symbol or "").upper()
            if symbol and symbol not in picked:
                picked.append(symbol)

        # 1) Vị thế mở luôn đứng đầu để quản lý SL/TSL có giá trước.
        try:
            for pos in (self.connector.get_positions() or []):
                sym = getattr(pos, "symbol", None) or (pos.get("symbol") if isinstance(pos, dict) else None)
                add(sym)
        except Exception:
            pass

        # 2) Lệnh chờ local và tín hiệu đang chờ.
        try:
            from core import pending_orders
            for item in pending_orders.list_active():
                add(item.get("symbol"))
        except Exception:
            pass
        for item in list(getattr(self, "pending_signals", []) or []):
            add(item.get("symbol"))

        # 3) Mã người dùng đang xem trên UI.
        live_cfg = self._read_live_config()
        add(live_cfg.get("UI_ACTIVE_SYMBOL", getattr(config, "UI_ACTIVE_SYMBOL", "")))

        # 4) Khi BOT bật, stream các mã có quyền trade. RAW-only không được thêm ở đây.
        bot_active = bool(live_cfg.get("BOT_ACTIVE", live_cfg.get("AUTO_TRADE_ENABLED", False)))
        if bot_active:
            for sym in live_cfg.get("BOT_ACTIVE_SYMBOLS", getattr(config, "BOT_ACTIVE_SYMBOLS", [])) or []:
                add(sym)

        # Opt-in: scalping phái sinh cần tick CKPS ngay cả khi chưa có vị thế.
        if getattr(config, "DAEMON_TICK_INCLUDE_CKPS", False):
            for sym in (getattr(config, "CKPS_SYMBOLS", []) or []):
                add(sym)

        # Cung nguon gia hien co cho bo theo doi; khong mo them ket noi rieng.
        monitor_cfg = settings_from_safeguard(live_cfg.get("bot_safeguard", {}))
        if monitor_cfg["VOLATILITY_BRAKE_ENABLED"]:
            for sym in monitor_cfg["VOLATILITY_BRAKE_SYMBOLS"]:
                add(sym)

        self._tick_symbols_cache = (now, picked)
        return picked

    @staticmethod
    def _order_result_ok(result):
        if isinstance(result, dict):
            return bool(result.get("ok"))
        return bool(getattr(result, "ok", False))

    def _arm_volatility_global_cooldown(self, event, safeguard_cfg):
        from core import storage_manager

        state = storage_manager.load_state()
        storage_manager.apply_state_defaults(state)
        cooldown_hours = max(
            0.01, float((safeguard_cfg or {}).get("GLOBAL_COOLDOWN_HOURS", 4.0) or 4.0)
        )
        until = time.time() + cooldown_hours * 3600.0
        reason = (
            f"VOLATILITY_BRAKE {event.get('symbol')} {event.get('direction')} "
            f"{event.get('change_pct', 0.0):+.2f}%"
        )
        item, created = storage_manager.mark_safeguard_brake(
            state,
            "GLOBAL",
            reason,
            until,
            trigger=dict(event),
        )
        if not created and float(item.get("until", 0.0) or 0.0) < until:
            item.update(
                {
                    "reason": reason,
                    "until": until,
                    "trigger": dict(event),
                    "created_at": time.time(),
                }
            )
            state.setdefault("active_brake", {}).update({"global": item})
        state["cooldown_until"] = max(
            float(state.get("cooldown_until", 0.0) or 0.0),
            until,
        )
        storage_manager.save_state(state)
        return cooldown_hours, until

    def _cancel_pending_entries_for_brake(self, symbols):
        cancelled_local = cancelled_broker = failed_broker = 0
        try:
            from core import pending_orders

            for item in pending_orders.list_active():
                if pending_orders.mark(
                    item.get("id"),
                    pending_orders.CANCELLED,
                    "VOLATILITY_BRAKE",
                ):
                    cancelled_local += 1
        except Exception as exc:
            logger.warning("[VOLATILITY BRAKE] Hủy lệnh chờ local lỗi: %s", exc)

        if getattr(config, "PAPER_TRADING", True):
            return cancelled_local, cancelled_broker, failed_broker

        open_statuses = {
            "NEW", "PENDING", "WAITING", "PARTIAL", "PARTIALLY_FILLED",
            "PARTIALLYFILLED", "OPEN",
        }
        seen = set()
        for symbol in symbols:
            try:
                orders = self.connector.get_orders(
                    symbol=symbol,
                    orderCategory=getattr(self.connector, "order_category", "NORMAL"),
                )
            except Exception:
                orders = []
            for order in orders or []:
                status = str(order.get("status", "") or "").strip().upper()
                if status not in open_statuses:
                    continue
                order_id = str(
                    order.get("id")
                    or order.get("orderId")
                    or order.get("order_id")
                    or ""
                )
                order_symbol = str(order.get("symbol") or symbol or "").upper()
                if not order_id or order_id in seen:
                    continue
                seen.add(order_id)
                try:
                    result = self.connector.cancel_order(order_id, symbol=order_symbol)
                    if self._order_result_ok(result):
                        cancelled_broker += 1
                    else:
                        failed_broker += 1
                except Exception:
                    failed_broker += 1
        return cancelled_local, cancelled_broker, failed_broker

    def _close_all_for_volatility_brake(self):
        try:
            positions = list(self.connector.get_all_open_positions() or [])
        except Exception as exc:
            logger.error("[VOLATILITY BRAKE] Không đọc được vị thế để đóng: %s", exc)
            return 0, 1, []

        closed = failed = 0
        failures = []
        for position in positions:
            ticket = str(
                getattr(position, "position_id", None)
                or getattr(position, "ticket", None)
                or ""
            )
            symbol = str(getattr(position, "symbol", "") or "")
            try:
                result = self.connector.close_position(
                    position,
                    comment="VOLATILITY_BRAKE",
                )
                if self._order_result_ok(result):
                    closed += 1
                else:
                    failed += 1
                    failures.append(f"{symbol}#{ticket}")
            except Exception:
                failed += 1
                failures.append(f"{symbol}#{ticket}")
        return closed, failed, failures

    @staticmethod
    def _volatility_action_label(action):
        return {
            "ALERT_ONLY": "CHỈ CẢNH BÁO",
            "BLOCK_NEW_EXPOSURE": "CHẶN BOT TĂNG VỊ THẾ",
            "CLOSE_ALL": "ĐÓNG HẾT + GLOBAL COOLDOWN",
        }.get(str(action or "").upper(), "CHỈ CẢNH BÁO")

    @staticmethod
    def _volatility_cooldown_key(symbol, direction):
        return f"{str(symbol or '').upper()}|{str(direction or '').upper()}"

    def _volatility_symbol_on_cooldown(self, symbol, direction):
        cooldowns = getattr(self, "_volatility_symbol_cooldowns", {}) or {}
        try:
            until = float(
                cooldowns.get(self._volatility_cooldown_key(symbol, direction), 0.0)
                or 0.0
            )
        except (TypeError, ValueError):
            return False
        return time.time() < until

    def _arm_volatility_symbol_cooldown(self, symbol, direction, safeguard_cfg):
        from core import storage_manager

        cfg = settings_from_safeguard(safeguard_cfg)
        minutes = cfg["VOLATILITY_BRAKE_SYMBOL_COOLDOWN_MINUTES"]
        until = time.time() + minutes * 60.0
        state = storage_manager.load_state()
        storage_manager.apply_state_defaults(state)
        cooldowns = state.setdefault("volatility_symbol_cooldowns", {})
        cooldowns[self._volatility_cooldown_key(symbol, direction)] = until
        now = time.time()
        state["volatility_symbol_cooldowns"] = {
            key: float(value)
            for key, value in cooldowns.items()
            if float(value or 0.0) > now
        }
        self._volatility_symbol_cooldowns = dict(
            state["volatility_symbol_cooldowns"]
        )
        storage_manager.save_state(state)
        return minutes, until

    def _run_volatility_brake_action(self, event, safeguard_cfg):
        try:
            cfg = settings_from_safeguard(safeguard_cfg)
            action = cfg["VOLATILITY_BRAKE_ACTION"]
            cooldown_hours = cooldown_until = 0.0
            local_cancelled = broker_cancelled = broker_cancel_failed = 0
            closed = close_failed = 0
            close_failures = []

            if action in {"BLOCK_NEW_EXPOSURE", "CLOSE_ALL"}:
                cooldown_hours, cooldown_until = self._arm_volatility_global_cooldown(
                    event, safeguard_cfg
                )
                self._volatility_brake_latched_until = cooldown_until

            if action == "CLOSE_ALL":
                with _SIGNAL_WRITE_LOCK:
                    self.pending_signals.clear()
                    self._atomic_write_signals(self._active_symbols)
                symbols = list(dict.fromkeys(
                    list(self._active_symbols or [])
                    + [
                        str(getattr(pos, "symbol", "") or "").upper()
                        for pos in (self.connector.get_all_open_positions() or [])
                    ]
                ))
                local_cancelled, broker_cancelled, broker_cancel_failed = (
                    self._cancel_pending_entries_for_brake(symbols)
                )
                closed, close_failed, close_failures = (
                    self._close_all_for_volatility_brake()
                )

            event = dict(event)
            event.update(
                {
                    "action": action,
                    "action_label": self._volatility_action_label(action),
                    "cooldown_hours": cooldown_hours,
                    "cooldown_until": cooldown_until,
                    "closed_positions": closed,
                    "failed_positions": close_failed,
                    "failed_position_ids": close_failures,
                    "cancelled_local_orders": local_cancelled,
                    "cancelled_broker_orders": broker_cancelled,
                    "failed_broker_cancels": broker_cancel_failed,
                    "completed_at": time.time(),
                }
            )

            from core import storage_manager

            state = storage_manager.load_state()
            storage_manager.apply_state_defaults(state)
            state.setdefault("volatility_events", []).append(event)
            state["volatility_events"] = state["volatility_events"][-100:]
            storage_manager.save_state(state)

            direction = "TĂNG" if event.get("direction") == "UP" else "GIẢM"
            unit_value = (
                f"{event.get('change_points', 0.0):+.2f} điểm"
                if event.get("threshold_unit") == "POINTS"
                else f"{event.get('change_pct', 0.0):+.2f}%"
            )
            movement_window = (
                "trong phiên"
                if str(event.get("movement_type") or "").upper() == "SESSION"
                else f"trong {event.get('window_seconds', 0.0):.0f} giây"
            )
            message = (
                "⚠️ CẢNH BÁO BIẾN ĐỘNG\n"
                f"{event.get('symbol')} {direction} {unit_value} {movement_window}\n"
                f"Hành động: {self._volatility_action_label(action)}"
            )
            if action == "CLOSE_ALL":
                message += (
                    f"\nĐã đóng: {closed} | Đóng lỗi: {close_failed}"
                    f"\nHủy lệnh chờ: local {local_cancelled}, DNSE {broker_cancelled}, "
                    f"lỗi {broker_cancel_failed}"
                    f"\nBOT khóa Global Cooldown {cooldown_hours:g} giờ."
                )
            elif action == "BLOCK_NEW_EXPOSURE":
                message += f"\nBOT không tăng vị thế trong {cooldown_hours:g} giờ."
            logger.log(
                logging.CRITICAL if action == "CLOSE_ALL" else logging.WARNING,
                message.replace("\n", " | "),
            )

            if cfg["VOLATILITY_BRAKE_TELEGRAM_ENABLED"]:
                try:
                    from telegram_notify.opportunity_alerts import send_volatility_event

                    result = send_volatility_event(
                        event,
                        title="RAT6 CẢNH BÁO BIẾN ĐỘNG",
                        require_enabled=False,
                    )
                    if not result.get("ok") and not result.get("skipped"):
                        logger.warning(
                            "[VOLATILITY BRAKE] Telegram lỗi: %s",
                            result.get("error", "unknown"),
                        )
                except Exception as exc:
                    logger.warning("[VOLATILITY BRAKE] Telegram lỗi: %s", exc)

            try:
                if close_failures:
                    from telegram_notify.system_alerts import notify_emergency_close_failure

                    notify_emergency_close_failure(close_failures)
                from ai_advisor.scan_report import append_volatility_event_to_existing_reports

                append_volatility_event_to_existing_reports(event)
            except Exception as exc:
                logger.warning("[VOLATILITY BRAKE] Ghi báo cáo MD lỗi: %s", exc)
        finally:
            with self._volatility_brake_lock:
                self._volatility_brake_inflight = False

    def _observe_volatility_brake(self, symbol, tick, safeguard_cfg):
        cfg = settings_from_safeguard(safeguard_cfg)
        if (
            cfg["VOLATILITY_BRAKE_ACTION"] != "ALERT_ONLY"
            and time.time() < float(self._volatility_brake_latched_until or 0.0)
        ):
            return
        event = self.volatility_brake.observe(
            symbol,
            tick.get("last", 0.0),
            safeguard_cfg,
            timestamp=tick.get("timestamp", time.time()),
            freshness=tick.get("freshness", "FRESH"),
        )
        if not event:
            return
        if self._volatility_symbol_on_cooldown(symbol, event.get("direction")):
            return
        with self._volatility_brake_lock:
            if self._volatility_brake_inflight:
                return
            self._volatility_brake_inflight = True
        cooldown_minutes, cooldown_until = self._arm_volatility_symbol_cooldown(
            symbol, event.get("direction"), safeguard_cfg
        )
        event["symbol_cooldown_minutes"] = cooldown_minutes
        event["symbol_cooldown_until"] = cooldown_until
        threading.Thread(
            target=self._run_volatility_brake_action,
            args=(event, dict(safeguard_cfg or {})),
            daemon=True,
            name="volatility-brake",
        ).start()

    def _tick_update_loop(self):
        """Luồng riêng: poll giá real-time mỗi 2 giây qua trades/latest + quotes/latest."""
        tick_interval = 2.0
        idle_interval = 30.0  # [FIX 429] Ngoài giờ nghỉ dài, khỏi đập quotes/latest.
        while self.running:
            try:
                live_cfg = self._read_live_config()
                safeguard_cfg = live_cfg.get("bot_safeguard", {}) or {}
                watched = self._active_symbols or getattr(config, "BOT_ACTIVE_SYMBOLS", [])
                if not is_any_network_window_open(watched or None, include_preopen=False):
                    time.sleep(min(idle_interval, max(1.0, seconds_until_network_open(watched or None))))
                    continue
                symbols = self._tick_symbols()
                # [FIX 429] Ngoài giờ giao dịch KHÔNG có tick để lấy -> bỏ poll để tránh
                # bão HTTP 429 triền miên trên /quotes/latest (check giờ thuần, không mạng).
                open_symbols = [s for s in symbols if is_symbol_trade_window_open(s)[0]]
                if not open_symbols:
                    time.sleep(idle_interval)
                    continue
                updated = False
                for sym in open_symbols:
                    if not self.running:
                        break
                    tick = data_engine.fetch_realtime_tick(sym)
                    if tick:
                        self._observe_volatility_brake(sym, tick, safeguard_cfg)
                        # Ghi tick vào heartbeat context để UI nhận
                        with _SIGNAL_WRITE_LOCK:
                            ctx = self.heartbeat_contexts.get(sym, {})
                            if "last" in tick:
                                ctx["current_price"] = tick["last"]
                            if "bid" in tick:
                                ctx["bid"] = tick["bid"]
                            if "ask" in tick:
                                ctx["ask"] = tick["ask"]
                            if "spread" in tick:
                                ctx["spread"] = tick["spread"]
                            if "high" in tick:
                                ctx["day_high"] = tick["high"]
                            if "low" in tick:
                                ctx["day_low"] = tick["low"]
                            ctx["tick_timestamp"] = tick.get("timestamp", time.time())
                            ctx["tick_source"] = tick.get("source", "CACHE")
                            ctx["tick_age_seconds"] = tick.get("age_seconds", 0.0)
                            ctx["tick_freshness"] = tick.get("freshness", "STALE")
                            ctx["market_state"] = tick.get("market_state", data_engine.market_data_state())
                            self.heartbeat_contexts[sym] = ctx
                        updated = True
                # [FIX I/O] Ghi file signals 1 lần/vòng thay vì sau TỪNG mã (file ~245KB)
                if updated:
                    self._atomic_write_signals(self._active_symbols or symbols)
            except Exception as e:
                logger.debug(f"Tick update error: {e}")
            time.sleep(tick_interval)

    def run(self):
        self.running = True
        logger.info(
            "RAT6 CKVN Daemon started."
        )
        from core import safeguard_report
        safeguard_report.log_effective_safeguard(logger)
        last_signal_scan = 0
        last_acc_check = 0
        acc_info = None

        while self.running:
            try:
                now = time.time()
                live_cfg = self._read_live_config()
                bot_active = live_cfg.get(
                    "BOT_ACTIVE",
                    live_cfg.get("AUTO_TRADE_ENABLED", getattr(config, "AUTO_TRADE_ENABLED", False)),
                )
                trade_symbols = live_cfg.get(
                    "BOT_ACTIVE_SYMBOLS",
                    getattr(config, "BOT_ACTIVE_SYMBOLS", getattr(config, "SYMBOLS", [])),
                )
                self._scan_snapshot_enabled = bool(
                    live_cfg.get(
                        "SCAN_SNAPSHOT_ENABLED",
                        getattr(config, "SCAN_SNAPSHOT_ENABLED", True),
                    )
                )
                raw_symbols = live_cfg.get(
                    "SCAN_SNAPSHOT_SYMBOLS",
                    getattr(
                        config,
                        "SCAN_SNAPSHOT_SYMBOLS",
                        list(getattr(config, "CKPS_SYMBOLS", []) or [])
                        + list(getattr(config, "CKCS_WATCHLIST", []) or []),
                    ),
                )
                trade_symbols = [str(item).upper() for item in (trade_symbols or []) if str(item).strip()]
                raw_symbols = [str(item).upper() for item in (raw_symbols or []) if str(item).strip()]
                priority_symbols = [
                    str(item).upper()
                    for item in (live_cfg.get("PRIORITY_SYMBOLS", []) or [])
                    if str(item).strip()
                ]
                priority_set = set(priority_symbols)
                trade_symbols = (
                    [item for item in priority_symbols if item in trade_symbols]
                    + [item for item in trade_symbols if item not in priority_set]
                )
                symbols = list(dict.fromkeys(trade_symbols + (raw_symbols if self._scan_snapshot_enabled else [])))
                self._active_symbols = symbols

                def finalize_scan_day():
                    if not self._scan_snapshot_enabled or not raw_symbols:
                        return
                    try:
                        from ai_advisor.scan_cache import recorder as _scan_recorder
                        if _scan_recorder.finalize_closed_day(raw_symbols):
                            _scan_recorder.flush()
                    except Exception as exc:
                        logger.debug("scan_cache finalize lỗi: %s", exc)

                # Lịch DNSE được thử tối đa 1 lần/ngày và lưu ra đĩa. Hàm giờ thị
                # trường chỉ đọc cache, vì vậy ngày lễ không phát sinh market-data call.
                try:
                    from core.market_calendar import refresh_from_dnse

                    refresh_from_dnse(self.connector.get_working_dates)
                except Exception as exc:
                    logger.debug("market calendar refresh lỗi: %s", exc)

                market_data_window = is_any_network_window_open(symbols or None, include_preopen=True)
                private_sync_seconds = (
                    15.0
                    if market_data_window
                    else max(
                        60.0,
                        float(
                            getattr(
                                config,
                                "DNSE_CLOSED_PRIVATE_SYNC_SECONDS",
                                300.0,
                            )
                            or 300.0
                        ),
                    )
                )
                try:
                    # Chỉ stream nhóm cần realtime. Danh sách RAW DATA vẫn được quét
                    # OHLC theo chu kỳ nhưng không ép mở tick cho hàng chục mã.
                    data_engine.set_stream_symbols(
                        self._tick_symbols(cache_seconds=private_sync_seconds)
                    )
                except Exception:
                    pass
                if acc_info is None or (now - last_acc_check > private_sync_seconds):
                    acc_info = self.connector.get_account_info()
                    last_acc_check = now
                    if acc_info is None:
                        self.connector._is_connected = False
                        self.connector.connect()
                        acc_info = self.connector.get_account_info()
                    
                if acc_info:
                    import core.storage_manager as storage_manager
                    current_acc_id = str(acc_info['login'])
                    if current_acc_id and current_acc_id != storage_manager._active_account_id:
                        storage_manager.set_active_account(current_acc_id)
                        update_daemon_paths(current_acc_id)
                        logger.info(f"🔄 Daemon phát hiện đổi tài khoản DNSE sang {current_acc_id}. Đã cập nhật Workspace.")

                # Heartbeat phải sống cả ngoài phiên/nghỉ trưa. Nếu không, UI sẽ
                # hiểu nhầm daemon chết dù account/trading sync vẫn hoạt động.
                if now - float(getattr(self, "_last_heartbeat_write", 0.0) or 0.0) >= 5.0:
                    self._atomic_write_signals(symbols)

                # Ngoài phiên chỉ market-data ngủ. Account REST + trading WS phía trên
                # vẫn hoạt động để balances/positions/orders luôn đồng bộ.
                if not market_data_window:
                    finalize_scan_day()
                    time.sleep(5.0)
                    continue
                if not any(is_symbol_trade_window_open(symbol)[0] for symbol in (symbols or [])):
                    finalize_scan_day()
                    time.sleep(5.0)  # warm-up: market WS + account APIs, chưa quét giá
                    continue
                        
                # Khởi động luồng tick nếu chưa chạy
                if self._tick_thread is None or not self._tick_thread.is_alive():
                    self._tick_thread = threading.Thread(target=self._tick_update_loop, daemon=True)
                    self._tick_thread.start()
                    logger.info("🔥 Tick update thread started (2s interval).")

                # [NEW] Lấy thời gian quét động từ UI
                daemon_delay = live_cfg.get("bot_safeguard", {}).get(
                    "DAEMON_LOOP_DELAY", getattr(config, "DAEMON_LOOP_DELAY", 15)
                )
                self.dca_pca_interval = live_cfg.get("bot_safeguard", {}).get(
                    "DCA_PCA_SCAN_INTERVAL", 2
                )
                # [SCAN SNAPSHOT] Toggle + interval live từ brain_settings (UI ghi), fallback env/config
                if "SCAN_SNAPSHOT_INTERVAL_MINUTES" in live_cfg:
                    try:
                        config.SCAN_SNAPSHOT_INTERVAL_MINUTES = float(
                            live_cfg.get("SCAN_SNAPSHOT_INTERVAL_MINUTES")
                        )
                    except Exception:
                        pass
                if "SCAN_SNAPSHOT_RETENTION_DAYS" in live_cfg:
                    try:
                        config.SCAN_SNAPSHOT_RETENTION_DAYS = max(
                            1, int(live_cfg.get("SCAN_SNAPSHOT_RETENTION_DAYS"))
                        )
                    except Exception:
                        pass
                for _ttl_key in (
                    "DNSE_TICK_CACHE_TTL_SECONDS",
                    "DNSE_OHLC_CACHE_TTL_SECONDS",
                    "DNSE_ACCOUNT_CACHE_TTL_SECONDS",
                    "DNSE_POSITIONS_CACHE_TTL_SECONDS",
                ):
                    if _ttl_key in live_cfg:
                        try:
                            setattr(config, _ttl_key, float(live_cfg.get(_ttl_key)))
                        except Exception:
                            pass

                now = time.time()

                # 1. QUÉT TÍN HIỆU ENTRY (Chu kỳ động)
                if symbols and (now - last_signal_scan >= daemon_delay):
                    self._scan_signals(
                        symbols,
                        bot_active,
                        trade_symbols=trade_symbols,
                        raw_symbols=raw_symbols,
                    )
                    last_signal_scan = now
                    self._atomic_write_signals(symbols)

                # 2. QUÉT DCA/PCA (Chu kỳ động - Độc lập hoàn toàn)
                if (
                    bot_active
                    and symbols
                    and (now - self.last_dca_pca_scan >= self.dca_pca_interval)
                ):
                    self._scan_dca_pca()
                    self.last_dca_pca_scan = now

                # Luồng gốc ngủ rất ngắn để không gây kẹt tiến trình
                time.sleep(0.5)

            except Exception as e:
                logger.exception("Lỗi Loop trong Daemon")
                time.sleep(2)

    def _scan_signals(self, symbols, bot_active, trade_symbols=None, raw_symbols=None):
        signal_debug_state = {}
        trade_symbols = {str(item).upper() for item in (trade_symbols if trade_symbols is not None else symbols)}
        raw_symbols = {str(item).upper() for item in (raw_symbols if raw_symbols is not None else symbols)}
        live_cfg = self._read_live_config()
        priority_symbols = {
            str(item).upper()
            for item in (live_cfg.get("PRIORITY_SYMBOLS", []) or [])
            if str(item).strip()
        }

        for sym in symbols:
            if not self.running:
                break

            is_open, closed_reason = is_symbol_trade_window_open(sym)
            if not is_open:
                signal_debug_state[sym] = f"[PAUSE] {closed_reason}"
                continue
            dfs, context = data_engine.fetch_data_v4(sym)
            if dfs is None or context is None:
                signal_debug_state[sym] = f"[PAUSE] {closed_reason}" if not is_open else "Đang tải dữ liệu..."
                continue

            # [FIX CORE]: Luôn chạy hàm generate_signal_v4 để tính toán và lưu Trend, Mode vào biến context
            # Đảm bảo UI luôn nhận được cấu trúc thị trường mới nhất ngay cả khi Bot đang tắt (Manual Mode)
            signal = signal_generator.generate_signal_v4(dfs, context, symbol=sym)
            context["latest_signal"] = signal # [NEW V4.4] Phục vụ logic REV_C
            context["priority_symbol"] = sym in priority_symbols
            context["market_open"] = bool(is_open)
            context["market_closed_reason"] = "" if is_open else closed_reason
            if not is_open:
                context["block_reason"] = f"MARKET_CLOSED: {closed_reason}"

            # --- [V4.2.1] Gói toàn bộ context vào Heartbeat ---
            with _SIGNAL_WRITE_LOCK:
                self.heartbeat_contexts[sym] = context.copy()
                self.heartbeat_contexts[sym].update({"timestamp": time.time()})

            # CKCS RAW DATA độc lập với BOT Advisor; lỗi lưu trữ không được làm gãy vòng quét.
            if getattr(self, "_scan_snapshot_enabled", False) and sym in raw_symbols:
                try:
                    from ai_advisor.scan_cache import recorder as _scan_recorder
                    _scan_recorder.maybe_record(sym, dfs, context, signal)
                except Exception as e:
                    logger.debug(f"scan_cache maybe_record lỗi ({sym}): {e}")

            # Mã chỉ được chọn cho RAW DATA dừng tại đây: có dữ liệu và context,
            # nhưng không phát signal sang hàng chờ, không tạo gợi ý/lệnh BOT.
            if sym not in trade_symbols:
                signal_debug_state[sym] = "[RAW DATA] Đã cập nhật, không có quyền đặt lệnh"
                continue

            if not is_open:
                signal_debug_state[sym] = f"[PAUSE/PREVIEW] {closed_reason}"
                continue

            # [FIX] Luôn gửi tín hiệu vào hàng chờ (để SignalListener hiển thị Thinking Logs)
            if signal == 1:
                self._add_signal("BUY", sym, context, "ENTRY")
                signal_debug_state[sym] = (
                    "✅ ĐÃ BÓP CÒ BUY" if bot_active else "⏸️ (TEST) Tín hiệu BUY"
                )
            elif signal == -1:
                self._add_signal("SELL", sym, context, "ENTRY")
                signal_debug_state[sym] = (
                    "✅ ĐÃ BÓP CÒ SELL" if bot_active else "⏸️ (TEST) Tín hiệu SELL"
                )
            else:
                try:
                    from core.storage_manager import get_brain_settings_for_symbol
                    none_rev = get_brain_settings_for_symbol(sym).get("bot_safeguard", {}).get("REV_CLOSE_ON_NONE", False)
                except Exception:
                    none_rev = False
                if none_rev:
                    self._add_signal("NONE", sym, context, "ENTRY")
                signal_debug_state[sym] = (
                    "⏳ Đang chờ điều kiện bóp cò..."
                    if bot_active
                    else "⏸️ Đang theo dõi..."
                )

        self._write_signal_debugger(signal_debug_state)

        # [SCAN SNAPSHOT] Ghi đĩa 1 lần cuối vòng quét (chỉ khi có thay đổi)
        if getattr(self, "_scan_snapshot_enabled", False):
            try:
                from ai_advisor.scan_cache import recorder as _scan_recorder
                _scan_recorder.flush()
            except Exception as e:
                logger.debug(f"scan_cache flush lỗi: {e}")

    def _scan_dca_pca(self):
        # [NEW V4.4] Bỏ get brain global, dời xuống từng vòng lặp Symbol để lấy config riêng
        positions = self.connector.get_positions()
        if not positions:
            return

        import core.storage_manager as storage_manager
        magics = storage_manager.get_magic_numbers()
        state = storage_manager.load_state()
        trade_tactics = state.get("trade_tactics", {}) if isinstance(state, dict) else {}
        bot_positions = {}
        for pos in positions:
            if is_bot_position(pos, magics) or _is_telegram_auto_scale_position(pos, magics, trade_tactics):
                if pos.symbol not in bot_positions:
                    bot_positions[pos.symbol] = []
                bot_positions[pos.symbol].append(pos)

        from core.storage_manager import get_last_dca_pca_close_time

        for symbol, pos_list in bot_positions.items():
            # Lấy cấu hình riêng cho từng symbol
            brain = get_brain_settings_for_symbol(symbol)
            dca_cfg = brain.get("dca_config", getattr(config, "DCA_CONFIG", {}))
            pca_cfg = brain.get("pca_config", getattr(config, "PCA_CONFIG", {}))
            
            if not dca_cfg.get("ENABLED", False) and not pca_cfg.get("ENABLED", False):
                continue
            
            context = self.heartbeat_contexts.get(symbol)
            if not context:
                dfs, ctx = data_engine.fetch_data_v4(symbol)
                if not ctx:
                    continue
                context = ctx
            
            # [NEW V5] Check Cooldown DCA/PCA theo Signal Time (Chống xả lệnh liên thanh)
            from core.storage_manager import get_last_dca_pca_signal_time
            now = time.time()
            dca_cd_time = dca_cfg.get("COOLDOWN", 60)
            pca_cd_time = pca_cfg.get("COOLDOWN", dca_cd_time)

            current_price = context.get("current_price", 0)
            
            # [KAISER FIX] Lấy ATR theo đúng Group mà Ngài đã chọn ở Risk & TSL (Nguồn cắm SL)
            risk_tsl = brain.get("risk_tsl", {})
            sl_group = risk_tsl.get("base_sl", "G2")
            if "DYNAMIC" in sl_group:
                # Giả định DYNAMIC dùng G1 cho Trend, G2 cho Sideway (theo logic TradeManager)
                market_mode = context.get("market_mode", "ANY")
                sl_group = "G1" if market_mode in ["TREND", "BREAKOUT"] else "G2"
            
            atr_val = context.get(f"atr_{sl_group}", 0)
            # NẾU MẤT DATA ATR -> BỎ QUA KHÔNG NHỒI LỆNH (log throttle 5 phút để biết).
            if not atr_val or atr_val <= 0:
                if not hasattr(self, "_dca_atr_warn_ts"):
                    self._dca_atr_warn_ts = {}
                if now - self._dca_atr_warn_ts.get(symbol, 0) > 300:
                    logger.warning(f"[DCA/PCA] {symbol}: thiếu ATR ({sl_group}) -> bỏ qua nhồi lần này.")
                    self._dca_atr_warn_ts[symbol] = now
                continue

            pos_list.sort(key=lambda x: x.time)
            first_pos = pos_list[0]
            # [GUARD] Lệnh gốc (ENTRY) đã đóng, chỉ còn lệnh con DCA/PCA -> KHÔNG nhồi tiếp (mồ côi).
            has_entry = any(
                not ("_AUTO_DCA" in str(getattr(p, "comment", "")) or "_AUTO_PCA" in str(getattr(p, "comment", "")))
                for p in pos_list
            )
            if not has_entry:
                logger.warning(f"[DCA/PCA] {symbol}: lệnh gốc đã đóng, chỉ còn lệnh con -> bỏ qua nhồi.")
                continue
            is_buy = first_pos.type == 0
            expected_sig = 1 if is_buy else -1
            
            profit_points = (
                (current_price - first_pos.price_open)
                if is_buy
                else (first_pos.price_open - current_price)
            )

            # DCA LOGIC
            if (
                dca_cfg.get("ENABLED", False)
                and profit_points < 0
                and len(pos_list) < dca_cfg.get("MAX_STEPS", 3)
                and now - get_last_dca_pca_signal_time(symbol, "DCA") >= dca_cd_time
            ):
                if abs(profit_points) >= (dca_cfg.get("DISTANCE_ATR_R", 1.0) * atr_val):
                    # ==========================================
                    # [NEW V5.1] MINI-BRAIN EVALUATION (DCA)
                    # ==========================================
                    dca_signal = context.get("latest_signal", 0)
                    dca_mb = dca_cfg.get("MINI_BRAIN", {})
                    if dca_mb.get("active", False):
                        # [Way B] Tự động kế thừa thông số params từ Sandbox hiện tại
                        sandbox_inds = brain.get("indicators", {})
                        for k, v in dca_mb.get("indicators", {}).items():
                            if k in sandbox_inds:
                                v["params"] = sandbox_inds[k].get("params", {})
                        
                        tf = dca_mb.get("timeframe", "15m")
                        df_mb = data_engine._fetch_bars(symbol, tf, 50, dca_mb.get("indicators", {}))
                        dca_signal = signal_generator.evaluate_mini_brain(df_mb, context, dca_mb, context.get("market_mode", "ANY"))

                    if dca_signal == expected_sig:  # [V5.1] Cần Mini-Brain đồng thuận
                        self._add_signal(
                            "BUY" if is_buy else "SELL",
                            symbol,
                            context,
                            "DCA",
                        )
                        from core.storage_manager import update_last_dca_pca_signal_time
                        update_last_dca_pca_signal_time(symbol, time.time(), "DCA")
                        time.sleep(0.5)
                        continue
                    else:
                        mb_log_key = f"mb_dca_reject_{symbol}"
                        last_mb_log = self.heartbeat_contexts.get(mb_log_key, 0)
                        log_cooldown = float(brain.get("bot_safeguard", {}).get("LOG_COOLDOWN_MINUTES", 60.0)) * 60.0
                        if time.time() - last_mb_log > log_cooldown:
                            logger.info(f"🚫 [MINI-BRAIN] Tạm chặn DCA {symbol} (Chưa đồng thuận xu hướng).")
                            with _SIGNAL_WRITE_LOCK:
                                self.heartbeat_contexts[mb_log_key] = time.time()

            # PCA LOGIC
            if (
                pca_cfg.get("ENABLED", False)
                and profit_points > 0
                and len(pos_list) < pca_cfg.get("MAX_STEPS", 2)
                and now - get_last_dca_pca_signal_time(symbol, "PCA") >= pca_cd_time
            ):
                is_safe = (
                    is_buy and first_pos.sl > first_pos.price_open
                ) or (
                    not is_buy and (0 < first_pos.sl < first_pos.price_open)
                )
                if is_safe and profit_points >= (pca_cfg.get("DISTANCE_ATR_R", 1.5) * atr_val):
                    # ==========================================
                    # [NEW V5.1] MINI-BRAIN EVALUATION (PCA)
                    # ==========================================
                    pca_signal = context.get("latest_signal", 0)
                    pca_mb = pca_cfg.get("MINI_BRAIN", {})
                    if pca_mb.get("active", False):
                        # [Way B] Tự động kế thừa thông số params từ Sandbox hiện tại
                        sandbox_inds = brain.get("indicators", {})
                        for k, v in pca_mb.get("indicators", {}).items():
                            if k in sandbox_inds:
                                v["params"] = sandbox_inds[k].get("params", {})

                        tf = pca_mb.get("timeframe", "15m")
                        df_mb = data_engine._fetch_bars(symbol, tf, 50, pca_mb.get("indicators", {}))
                        pca_signal = signal_generator.evaluate_mini_brain(df_mb, context, pca_mb, context.get("market_mode", "ANY"))

                    if pca_signal == expected_sig:  # [V5.1] Cần Mini-Brain đồng thuận
                        self._add_signal(
                            "BUY" if is_buy else "SELL",
                            symbol,
                            context,
                            "PCA",
                        )
                        from core.storage_manager import update_last_dca_pca_signal_time
                        update_last_dca_pca_signal_time(symbol, time.time(), "PCA")
                        time.sleep(0.5)
                    else:
                        mb_log_key = f"mb_pca_reject_{symbol}"
                        last_mb_log = self.heartbeat_contexts.get(mb_log_key, 0)
                        log_cooldown = float(brain.get("bot_safeguard", {}).get("LOG_COOLDOWN_MINUTES", 60.0)) * 60.0
                        if time.time() - last_mb_log > log_cooldown:
                            logger.info(f"🚫 [MINI-BRAIN] Tạm chặn PCA {symbol} (Chưa đồng thuận xu hướng).")
                            with _SIGNAL_WRITE_LOCK:
                                self.heartbeat_contexts[mb_log_key] = time.time()


if __name__ == "__main__":
    # [FIX V5] Đổi tên tiến trình thành "daemon" để không đụng file log của UI (WinError 32)
    logger = setup_logging(debug_mode=getattr(config, "ENABLE_DEBUG_LOGGING", False), process_name="daemon")

    from core.process_lock import ProcessLock

    daemon_lock = ProcessLock(os.path.join("data", "run", "rat_ckvn_daemon.lock"))
    if not daemon_lock.acquire():
        logger.error("Bot Daemon đang chạy; tiến trình trùng sẽ thoát.")
        raise SystemExit(2)
    try:
        daemon = StandaloneBotDaemon()
        try:
            if daemon.connector._is_connected:
                daemon.run()
        except KeyboardInterrupt:
            logger.info("Đang tắt tiến trình Daemon...")
    finally:
        daemon_lock.release()
