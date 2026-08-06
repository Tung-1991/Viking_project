# -*- coding: utf-8 -*-
# FILE: main.py
# V6.9.3: REFACTORED CORE - UI CONTEXT SYNC HOTFIX (KAISER EDITION)

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, messagebox, Menu
import threading
import time
import sys
import json
import os
import subprocess
from datetime import datetime
from types import SimpleNamespace
import logging
from core.logger_setup import setup_logging  # [NEW V4.3] Import hệ thống Log 3 lớp
import config
from core.dnse_connector import DNSEConnector, ModeBoundConnector
from core.checklist_manager import ChecklistManager
from core.trade_manager import TradeManager
from core.storage_manager import load_brain_settings, load_state, save_brain_settings, save_state
from core.signal_listener import SignalListener
from core.data_engine import data_engine
from core.money import (
    format_vnd,
    money_display_scale,
    money_unit_note,
    set_money_display_zero_trim,
)
from core.position_classifier import (
    is_bot_position,
    is_external_position,
    is_manual_position,
)
from core import margin_rules, pending_orders, settlement, stock_rules
from signals.signal_generator import signal_generator
import traceback

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger = logging.getLogger("RAT_CKVN")
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logger.critical(f"💥 TOÀN BỘ HỆ THỐNG CRASH:\n{error_msg}")
    
    try:
        os.makedirs("data/logs", exist_ok=True)
        with open("data/logs/CRASH_REPORT.log", "a", encoding="utf-8") as f:
            f.write(f"\n--- CRASH AT {datetime.now()} ---\n")
            f.write(error_msg)
            f.write("-" * 50 + "\n")
    except:
        pass

sys.excepthook = handle_exception

import ui_panels
import ui_popups

# [V3.0] Import giao diện Strategy Sandbox
from ui_bot_strategy import BotStrategyUI

# [V6.9.4] Paths giờ được đọc động từ storage_manager sau khi set_active_account
# Khai báo tạm thời, sẽ được cập nhật trong __init__ sau khi biết Account ID
TSL_SETTINGS_FILE = "data/tsl_settings.json"
PRESETS_FILE = "data/presets_config.json"
BRAIN_SETTINGS_FILE = "data/brain_settings.json"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")

FONT_MAIN = ("Roboto", 13)
FONT_BOLD = ("Roboto", 13, "bold")
FONT_EQUITY = ("Roboto", 36, "bold")
FONT_PNL = ("Roboto", 18, "bold")
FONT_SECTION = ("Roboto", 12, "bold")
FONT_BIG_VAL = ("Consolas", 20, "bold")
FONT_PRICE = ("Roboto", 32, "bold")
FONT_FEE = ("Roboto", 13, "bold")

COL_GREEN = "#00C853"
COL_RED = "#D50000"
COL_BLUE_ACCENT = "#0D47A1"
COL_BLUE_ACCENT_HOVER = "#0A3578"
COL_GRAY_BTN = "#424242"
COL_WARN = "#FFAB00"
COL_BOT_TAG = "#E040FB"


class Suppress10025Filter(logging.Filter):
    def filter(self, record):
        return "Retcode: 10025" not in record.getMessage()


main_logger = logging.getLogger("RAT_CKVN")
main_logger.addFilter(Suppress10025Filter())


def _read_appended_lines(log_path, offsets):
    """Read only newly appended log lines without holding the file open.

    The first observation starts at EOF. If rotation or truncation makes the
    file smaller, reading resumes from the beginning of the replacement file.
    """
    stat = os.stat(log_path)
    size = stat.st_size
    identity = (stat.st_dev, stat.st_ino)
    state = offsets.get(log_path)
    if state is None:
        offsets[log_path] = {"pos": size, "identity": identity}
        return []
    if isinstance(state, dict):
        pos = int(state.get("pos", 0))
        if state.get("identity") != identity:
            pos = 0
    else:
        # Accept the integer offsets used by older in-memory callers.
        pos = int(state)
    if size < pos:
        pos = 0
    if size <= pos:
        offsets[log_path] = {"pos": pos, "identity": identity}
        return []
    with open(log_path, "r", encoding="utf-8") as handle:
        handle.seek(pos)
        lines = handle.readlines()
        offsets[log_path] = {"pos": handle.tell(), "identity": identity}
    return lines


class BotUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RAT6 CKVN - Master Control (Kaiser Edition)")
        screen_w = max(1024, int(self.winfo_screenwidth() or 1024))
        screen_h = max(720, int(self.winfo_screenheight() or 720))
        window_w = min(1650, max(980, screen_w - 60))
        window_h = min(950, max(680, screen_h - 90))
        pos_x = max(0, (screen_w - window_w) // 2)
        pos_y = max(0, (screen_h - window_h) // 3)
        self.geometry(f"{window_w}x{window_h}+{pos_x}+{pos_y}")
        self.minsize(min(980, window_w), min(680, window_h))

        self.var_auto_trade = tk.BooleanVar(value=False)  # Cờ TỔNG (legacy) = OR(CKPS, CKCS)
        # [2-BOT] Tách công tắc riêng: Phái sinh (VN30F/CKPS) và Cơ sở (CKCS).
        self.var_bot_ckps = tk.BooleanVar(value=False)
        self.var_bot_ckcs = tk.BooleanVar(value=False)
        self.var_assist_math_sl = tk.BooleanVar(value=False)
        self.var_assist_preset_tp = tk.BooleanVar(value=False)
        self.var_assist_dca = tk.BooleanVar(value=False)
        self.var_assist_pca = tk.BooleanVar(value=False)

        self.var_strict_mode = tk.BooleanVar(value=config.STRICT_MODE_DEFAULT)
        self.var_confirm_close = tk.BooleanVar(value=True)
        # Chỉ ẩn/hiện dòng gợi ý trên bảng; cache và lịch sử vẫn được giữ nguyên.
        self.var_show_bot_opportunities = tk.BooleanVar(value=True)
        self.var_account_type = tk.StringVar(value=config.DEFAULT_ACCOUNT_TYPE)

        self.var_manual_lot = tk.StringVar(value="")
        self.var_manual_entry = tk.StringVar(value="")
        self.var_manual_tp = tk.StringVar(value="")
        self.var_manual_sl = tk.StringVar(value="")
        self.var_bypass_checklist = tk.BooleanVar(
            value=config.MANUAL_CONFIG["BYPASS_CHECKLIST"]
        )
        self.var_direction = tk.StringVar(value="BUY")
        self.var_manual_trade_mode = tk.StringVar(value="NORMAL")
        # Tick "Thị trường" cho nút gộp: True -> lệnh thị trường, False -> LO.
        self.var_manual_market = tk.BooleanVar(value=False)
        # Phiên đấu giá để HẸN khi đặt ngoài giờ: ATO (mở cửa) | ATC (đóng cửa).
        self.var_manual_schedule_session = tk.StringVar(value="ATO")
        # [ORDER MODE 24/7] Chọn tay kiểu lệnh: NORMAL (liên tục) | ATO | ATC.
        self.var_manual_order_mode = tk.StringVar(value="NORMAL")
        self._auction_price_cache = {}  # {symbol: {"ts":.., "ato":.., "atc":..}}
        self.var_preview_trade_after_apply = tk.BooleanVar(value=False)

        self.tactic_states = {
            "BE": True,
            "PNL": False,
            "STEP_R": True,
            "SWING": True,
            "BE_CASH": False,  # [FIX] Thêm State cho TSL CASH
            "PSAR_TRAIL": False,  # [FIX] Thêm State cho TSL PSAR
            "AUTO_DCA": False,
            "AUTO_PCA": False,
            "REV_C": False,  # [NEW V4.4] Recovery/Safelock
            "ANTI_CASH": False,  # [NEW V4.4] Hard stop logic
        }
        self.entry_exit_tactic_states = {
            "FALLBACK_R": False,
            "SWING_REJECTION": False,
            "SWING_STRUCTURE": False,
            "FIB_RETRACE": False,
            "PULLBACK_ZONE": False,
        }
        self.running = True
        self._mode_switching = False
        self.tsl_states_map = {}
        self.last_price_val = 0.0
        self.latest_market_context = {}
        self.market_data_health = {}
        self._last_market_health_state = ""
        self._pending_market_health_state = ""
        self._pending_market_health_since = 0.0
        # [SANDBOX-FETCH] Fetch context on-demand cho symbol dashboard (mirror ui_bot_strategy)
        self.ctx_fetch_lock = threading.Lock()
        self.ctx_fetch_inflight = set()
        self.ctx_fetch_last = {}
        self.latest_entry_exit_decisions = {}
        self.group_status_tracker = {}
        self.manual_preview_models = {}

        self.brain_status = "CHỜ KẾT NỐI..."
        self.brain_wakeup_time = 0
        self.brain_active_symbols = []

        self.daemon_process = None
        self.daemon_output_file = None
        self.log_cooldown_cache = {}
        self.var_advisor_export_days = tk.StringVar(value="7")
        self.var_ckcs_report_days = tk.StringVar(value="15")
        self.var_advisor_mode = tk.StringVar(value="Manual Only")
        self.var_advisor_save_archive = tk.BooleanVar(value=False)
        self.var_advisor_fixed_time = tk.StringVar(value="")
        self.var_advisor_global_emergency = tk.BooleanVar(value=True)
        self.var_advisor_send_response_file = tk.BooleanVar(value=False)
        self.var_advisor_send_previous_response = self.var_advisor_send_response_file
        self.var_ckcs_auto_report_morning = tk.BooleanVar(value=True)
        self.var_ckcs_auto_report_afternoon = tk.BooleanVar(value=True)
        self.var_ckcs_send_api_morning = tk.BooleanVar(value=False)
        self.var_ckcs_send_api_afternoon = tk.BooleanVar(value=False)
        self.var_ckcs_morning_time = tk.StringVar(value="11:35")
        self.var_ckcs_afternoon_time = tk.StringVar(value="14:50")
        self.var_ckcs_liquidity_filter_enabled = tk.BooleanVar(value=True)
        self.var_ckcs_liquidity_sessions = tk.StringVar(value="60")
        self.var_ckcs_liquidity_min_billion = tk.StringVar(value="10")
        self.advisor_api_preview_text = "API payload: not estimated"
        self.advisor_api_preview_detail_text = ""
        self.advisor_last_export_status = "Never"
        self.advisor_last_error = ""
        self._advisor_worker_active = False
        self._ckcs_api_worker_active = False
        self._advisor_last_trigger_check = 0.0
        self._advisor_last_trigger_fire = {}
        self._advisor_last_fixed_date = ""
        self._ckcs_last_morning_date = ""
        self._ckcs_last_afternoon_date = ""
        self._ckcs_auto_retry_after = {"morning": 0.0, "afternoon": 0.0}
        self._api_health_refresh_job = None

        # Hiển thị cửa sổ ngay và dựng UI từ cấu hình cục bộ. Không chờ DNSE
        # và không yêu cầu .env phải có sẵn.
        self.connector = DNSEConnector()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._loading_label = ctk.CTkLabel(
            self, text="⏳ Đang chuẩn bị giao diện...", font=("Roboto", 22, "bold")
        )
        self._loading_label.place(relx=0.5, rely=0.5, anchor="center")
        # Đợi Windows vẽ cửa sổ rồi mới dựng các panel nặng; tuyệt đối không
        # gọi Tk từ worker thread.
        self.after_idle(self._bootstrap_connection)

    def _bootstrap_connection(self):
        """Dựng UI mà không gọi mạng DNSE.

        Connector và daemon tự kết nối sau khi giao diện đã sẵn sàng. Bản cài
        mới vì vậy vẫn vào được Advanced để nhập API key/secret và vẫn dùng
        được PAPER khi DNSE chưa được cấu hình.
        """
        configured = bool(
            getattr(self.connector, "api_key", "")
            and getattr(self.connector, "api_secret", "")
            and getattr(self.connector, "account_no", "")
        )
        paper_mode = bool(getattr(config, "PAPER_TRADING", True))
        account_no = str(getattr(self.connector, "account_no", "") or "PAPER")
        initial_balance = (
            float(getattr(config, "PAPER_INITIAL_BALANCE", 100000000.0) or 0.0)
            if paper_mode
            else 0.0
        )
        acc_info = {
            "login": account_no,
            "server": "DNSE_CONFIGURED_PENDING" if configured else "DNSE_NOT_CONFIGURED",
            "status": "PAPER" if paper_mode else ("CONNECTING" if configured else "NOT_CONFIGURED"),
            "balance": initial_balance,
            "equity": initial_balance,
            "margin": 0.0,
            "free_margin": initial_balance,
            "margin_free": initial_balance,
            "cash_available": initial_balance,
            "buying_power": initial_balance,
            "stock_cash": initial_balance,
            "deriv_avail": 0.0,
        }
        self._finish_init(acc_info)
        if configured:
            # connect() chỉ cập nhật connector cục bộ; request mạng thật chạy
            # trong các luồng nền sau khi UI đã hiện.
            try:
                self.connector.connect()
            except Exception as exc:
                self.log_message(f"⚠️ DNSE chưa kết nối: {exc}", error=True)
        else:
            self.log_message(
                "⚠️ DNSE chưa được cấu hình. PAPER vẫn chạy; vào Advanced để nhập API key/secret.",
                target="manual",
            )

    def _finish_init(self, acc_info):
        """UI thread: dựng workspace, panel và khởi động dịch vụ sau khi đã kết nối."""
        
        import core.storage_manager as storage_manager
        if acc_info:
            storage_manager.set_active_account(acc_info['login'])
            
            # [V6.9.4] Cập nhật lại đường dẫn file cho đúng thư mục workspace
            global TSL_SETTINGS_FILE, PRESETS_FILE, BRAIN_SETTINGS_FILE
            acc_dir = storage_manager._active_account_dir
            TSL_SETTINGS_FILE = os.path.join(acc_dir, "tsl_settings.json")
            PRESETS_FILE = os.path.join(acc_dir, "presets_config.json")
            BRAIN_SETTINGS_FILE = os.path.join(acc_dir, "brain_settings.json")
            
            
            self.log_message(f"✅ Đã tải Workspace cho tài khoản: {acc_info['login']}")
        else:
            self.log_message("⚠️ Không thể xác định Account ID. Dùng Workspace mặc định.", error=True)

        # Khởi chạy luồng theo dõi Daemon Log
        self.group_status_tracker = storage_manager.load_group_status_tracker()
        threading.Thread(target=self._tail_daemon_logs, daemon=True).start()

        self.load_settings()
        # Khởi động/restart luôn ở trạng thái chưa cấp quyền mở lệnh tự động.
        # Đồng bộ cả UI, config trong RAM và brain_settings trước khi bật daemon,
        # tránh trường hợp file cũ ghi BOT=ON nhưng đèn UI lại đang OFF.
        self._disarm_auto_trade_on_startup()
        setattr(config, "UI_ACTIVE_SYMBOL", config.DEFAULT_SYMBOL)

        # UI chỉ tiêu thụ feed giá do daemon ghi vào heartbeat. Từ đây mọi lời gọi
        # get_tick/fetch_realtime_tick trong UI và TradeManager đều không được tự
        # mở market WS hoặc gọi /trades|quotes/latest.
        data_engine.configure_market_data_owner(
            False,
            tick_provider=self._shared_market_tick,
            state_provider=self._shared_market_state,
        )
        self.connector.set_market_data_provider(
            self._shared_market_tick,
            self._shared_market_state,
        )

        self.checklist_mgr = ChecklistManager(self.connector)
        self.trade_mgr = TradeManager(
            self.connector, self.checklist_mgr, log_callback=self.log_message
        )
        self._inactive_trade_managers = {}

        self.grid_columnconfigure(0, weight=0, minsize=420)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.frm_left = ctk.CTkScrollableFrame(
            self, width=405, corner_radius=0, label_text=""
        )
        self.frm_left.grid(row=0, column=0, sticky="nswe")
        self.frm_left.grid_columnconfigure(0, weight=1)

        self.frm_right = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.frm_right.grid(row=0, column=1, sticky="nswe", padx=10, pady=10)

        ui_panels.setup_left_panel(self, self.frm_left)
        ui_panels.setup_right_panel(self, self.frm_right)

        try:
            self._loading_label.destroy()
        except Exception:
            pass

        self.start_daemon_process()
        self.thread = threading.Thread(target=self.bg_update_loop, daemon=True)
        self.thread.start()

        self.signal_listener = SignalListener(
            trade_manager=self.trade_mgr,
            get_auto_trade_cb=lambda symbol=None: self._bot_enabled_for(symbol),
            get_preset_cb=lambda: getattr(config, "DEFAULT_PRESET", "SCALPING"),
            get_tsl_mode_cb=self.get_current_tactic_string,
            ui_heartbeat_cb=self.update_brain_heartbeat,
            log_cb=lambda msg, error=False: self.log_message(
                msg, error=error, target="bot"
            ),
            # Gợi ý Telegram là ảnh chụp kế hoạch BOT, không đọc Manual Preset.
            build_opportunity_plan_cb=self.trade_mgr.build_telegram_signal_order,
        )
        self.signal_listener.start()
        try:
            from telegram_notify.control import TelegramControlService

            self.telegram_control_service = TelegramControlService(
                connector=self.connector,
                get_state_cb=lambda: getattr(self.trade_mgr, "state", {}),
                get_bot_enabled_cb=lambda: bool(self.var_auto_trade.get()),
                set_bot_enabled_cb=self.set_auto_trade_enabled,
                get_brain_status_cb=lambda: getattr(self, "brain_status", ""),
                get_active_symbols_cb=lambda: list(getattr(self, "brain_active_symbols", []) or []),
                execute_order_cb=lambda symbol, side, lot, sl, tp: self.trade_mgr.execute_telegram_sandbox_order(
                    symbol,
                    side,
                    lot,
                    sl,
                    tp,
                    bypass_checklist=bool(self.var_bypass_checklist.get()),
                ),
                log_cb=lambda msg, error=False: self.log_message(msg, error=error, target="manual"),
            )
            self.telegram_control_service.start()
            self.log_message("[TELEGRAM CONTROL] Service ready.", target="manual")
        except Exception as exc:
            self.telegram_control_service = None
            self.log_message(f"[TELEGRAM CONTROL] Service failed: {exc}", error=True, target="manual")

        self.log_message(
            "RAT6 CKVN ready."
        )
        try:
            from core import safeguard_report
            for _line in safeguard_report.format_effective_table().splitlines():
                self.log_message(_line, target="bot")
        except Exception:
            pass

    def start_daemon_process(self):
        current = getattr(self, "daemon_process", None)
        if current is not None and getattr(current, "poll", lambda: None)() is None:
            self.log_message("ℹ️ Bot Daemon đang chạy; bỏ qua yêu cầu khởi động trùng.", target="bot")
            return
        try:
            previous_output = getattr(self, "daemon_output_file", None)
            if previous_output and not previous_output.closed:
                previous_output.close()
            os.makedirs(os.path.join("data", "logs"), exist_ok=True)
            self.daemon_output_file = open(
                os.path.join("data", "logs", "daemon_stdout.log"),
                "a",
                encoding="utf-8",
                buffering=1,
            )
            self.daemon_process = subprocess.Popen(
                [sys.executable, "bot_daemon.py"],
                stdout=self.daemon_output_file,
                stderr=subprocess.STDOUT,
            )
            self.log_message("🚀 Đã kích hoạt Bot Daemon ngầm.", target="bot")
        except Exception as e:
            daemon_output = getattr(self, "daemon_output_file", None)
            if daemon_output:
                try:
                    daemon_output.close()
                except Exception:
                    pass
            self.daemon_output_file = None
            self.daemon_process = None
            self.log_message(f"❌ Lỗi kích hoạt Daemon: {e}", error=True, target="bot")

    def on_closing(self):
        self.running = False
        if hasattr(self, "signal_listener"):
            self.signal_listener.stop()
        if getattr(self, "telegram_control_service", None):
            self.telegram_control_service.stop()
        daemon_process = getattr(self, "daemon_process", None)
        if daemon_process:
            try:
                if getattr(daemon_process, "poll", lambda: None)() is None:
                    daemon_process.terminate()
                    daemon_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon_process.kill()
                daemon_process.wait(timeout=5)
            except Exception as exc:
                self.log_message(f"❌ Lỗi dừng Daemon: {exc}", error=True, target="bot")
        daemon_output = getattr(self, "daemon_output_file", None)
        if daemon_output:
            try:
                daemon_output.close()
            except Exception:
                pass
        self.destroy()
        sys.exit(0)

    def _save_brain_live_config(self):
        try:
            existing_data = load_brain_settings()
            existing_data["AUTO_TRADE_ENABLED"] = bool(getattr(config, "AUTO_TRADE_ENABLED", False))
            existing_data["MONEY_DISPLAY_ZERO_TRIM"] = getattr(
                config, "MONEY_DISPLAY_ZERO_TRIM", "000"
            )
            existing_data["MONEY_DISPLAY_UNIT"] = getattr(
                config, "MONEY_DISPLAY_UNIT", "K_VND"
            )
            # Thuế là cấu hình pháp lý/env, không cho brain_settings cũ (từng
            # lưu 0 trước 01/07/2026) ghi đè ngược lên mức hiện hành.
            for legal_key in (
                "DNSE_TAX_RATE",
                "DNSE_STOCK_TAX_RATE",
                "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE",
            ):
                existing_data.pop(legal_key, None)
            for key in (
                "MONEY_DISPLAY_ZERO_TRIM",
                "MONEY_DISPLAY_UNIT",
                "PAPER_INITIAL_BALANCE",
                "PAPER_SPREAD_POINTS",
                "DNSE_BROKER_FEE_PER_CONTRACT",
                "DNSE_EXCHANGE_FEE_PER_CONTRACT",
                "DNSE_CLEARING_FEE_PER_CONTRACT",
                "DNSE_CUSTODY_CODE",
                "DNSE_STOCK_ACCOUNT_NO",
                "DNSE_DERIVATIVE_ACCOUNT_NO",
                "CKCS_WATCHLIST",
                "CKPS_SYMBOLS",
            ):
                if hasattr(config, key):
                    existing_data[key] = getattr(config, key)
            if hasattr(config, "UI_ACTIVE_SYMBOL"):
                existing_data["UI_ACTIVE_SYMBOL"] = config.UI_ACTIVE_SYMBOL
            save_brain_settings(existing_data)
        except Exception as e:
            self.log_message(f"Live config sync error: {e}", error=True)

    def _merge_dict(self, target, source):
        for k, v in source.items():
            if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                self._merge_dict(target[k], v)
            else:
                target[k] = v

    def reload_config_from_json(self):
        """[JOB 1] Đọc lại cấu hình từ file JSON (Master) vào bộ nhớ App"""
        if os.path.exists(BRAIN_SETTINGS_FILE):
            try:
                with open(BRAIN_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    bs = json.load(f)
                    for k, v in bs.items():
                        if k in {"MONEY_DISPLAY_ZERO_TRIM", "MONEY_DISPLAY_UNIT"}:
                            continue
                        if k in {
                            "DNSE_TAX_RATE",
                            "DNSE_STOCK_TAX_RATE",
                            "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE",
                        }:
                            continue
                        if hasattr(config, k) and k != "COIN_LIST":
                            current_val = getattr(config, k)
                            if isinstance(current_val, dict) and isinstance(v, dict):
                                self._merge_dict(current_val, v)
                            else:
                                setattr(config, k, v)
                    set_money_display_zero_trim(
                        bs.get(
                            "MONEY_DISPLAY_ZERO_TRIM",
                            bs.get("MONEY_DISPLAY_UNIT", "000"),
                        )
                    )
            except Exception as e:
                self.log_message(f"Lỗi Reload JSON: {e}", error=True)

    def _market_health_log_event(self, new_state, now=None):
        """Gộp trạng thái WS thoáng qua lúc khởi động; trả về (nội dung, là_lỗi)."""
        now = time.time() if now is None else float(now)
        new_state = str(new_state or "RECOVERING").upper()
        unavailable = new_state in ("RECOVERING", "MARKET DATA DOWN", "MARKET_DATA_DOWN")
        previous = str(getattr(self, "_last_market_health_state", "") or "").upper()

        if unavailable:
            pending = str(getattr(self, "_pending_market_health_state", "") or "").upper()
            if pending != new_state:
                self._pending_market_health_state = new_state
                self._pending_market_health_since = now
                return None
            grace = max(
                0.0,
                float(getattr(config, "DNSE_MARKET_WARNING_GRACE_SECONDS", 5.0) or 0.0),
            )
            started = float(getattr(self, "_pending_market_health_since", now) or now)
            if now - started < grace or previous == new_state:
                return None
            self._last_market_health_state = new_state
            self._pending_market_health_state = ""
            self._pending_market_health_since = 0.0
            return (
                f"[MARKET DATA] {new_state}: BOT không mở lệnh mới; giá cache được đánh dấu GIÁ CŨ.",
                True,
            )

        self._pending_market_health_state = ""
        self._pending_market_health_since = 0.0
        if previous == new_state:
            return None
        self._last_market_health_state = new_state
        if previous in ("RECOVERING", "MARKET DATA DOWN", "MARKET_DATA_DOWN"):
            return (f"[MARKET DATA] Đã phục hồi: {new_state}.", False)
        return None

    def update_brain_heartbeat(self, heartbeat: dict):
        self.brain_status = heartbeat.get("status", "UNKNOWN")
        self.brain_wakeup_time = heartbeat.get("wakeup_time", 0)
        self.brain_active_symbols = heartbeat.get("active_symbols", [])
        health = heartbeat.get("market_data_health", {})
        if isinstance(health, dict) and health:
            self.market_data_health = health
            new_state = str(health.get("market_state") or "RECOVERING").upper()
            event = self._market_health_log_event(new_state)
            if event:
                message, is_error = event
                self.log_message(
                    message,
                    error=is_error,
                    target="api-health",
                )
            try:
                from telegram_notify.system_alerts import observe_health

                observe_health(
                    "MARKET DATA",
                    new_state,
                    {"MARKET DATA DOWN", "MARKET_DATA_DOWN"},
                    threshold_seconds=60.0,
                )
                connector_health = (
                    health.get("connector")
                    if isinstance(health.get("connector"), dict)
                    else {}
                )
                observe_health(
                    "BROKER API",
                    str(connector_health.get("broker_api_state") or "LIVE"),
                    {"BROKER_API_DOWN", "BROKER API DOWN"},
                    threshold_seconds=60.0,
                )
            except Exception:
                pass

        contexts = heartbeat.get("contexts", {})
        if contexts:
            # MERGE (không ghi đè cả dict) để giữ context fetch on-demand của sandbox
            # cho các mã CKCS daemon không quét — tránh bị xoá mỗi nhịp heartbeat.
            if isinstance(self.latest_market_context, dict):
                self.latest_market_context.update(contexts)
            else:
                self.latest_market_context = dict(contexts)
            try:
                import core.storage_manager as storage_manager
                self.group_status_tracker = storage_manager.update_group_status_tracker(
                    contexts, self.group_status_tracker
                )
            except Exception:
                pass

    def _shared_market_state(self):
        health = self.market_data_health if isinstance(self.market_data_health, dict) else {}
        heartbeat_age = max(0.0, time.time() - float(self.brain_wakeup_time or 0.0)) if self.brain_wakeup_time else 999999.0
        if heartbeat_age > 60.0:
            return "MARKET DATA DOWN"
        if heartbeat_age > 15.0:
            return "RECOVERING"
        return str(health.get("market_state") or "RECOVERING").upper()

    def _shared_market_tick(self, symbol):
        symbol = str(symbol or "").upper()
        context = (self.latest_market_context or {}).get(symbol, {}) or {}
        last = float(context.get("current_price", context.get("last", 0.0)) or 0.0)
        bid = float(context.get("bid", last) or last)
        ask = float(context.get("ask", last) or last)
        if not (last or bid or ask):
            return None
        timestamp = float(context.get("tick_timestamp", context.get("timestamp", 0.0)) or 0.0)
        return {
            "symbol": symbol,
            "last": last or bid or ask,
            "bid": bid or last,
            "ask": ask or last,
            "spread": float(context.get("spread", max(0.0, ask - bid)) or 0.0),
            "high": float(context.get("day_high", context.get("high", 0.0)) or 0.0),
            "low": float(context.get("day_low", context.get("low", 0.0)) or 0.0),
            "open": float(context.get("day_open", context.get("open", 0.0)) or 0.0),
            "timestamp": timestamp or self.brain_wakeup_time or time.time(),
            "source": str(context.get("tick_source", context.get("source", "CACHE")) or "CACHE"),
            "market_state": str(context.get("market_state") or self._shared_market_state()),
            "synthetic_quote": bool(context.get("synthetic_quote", False)),
        }

    def _bot_enabled_for(self, symbol=None):
        """Cờ bật-bot theo nhóm mã. symbol=None -> cờ tổng (legacy)."""
        if symbol:
            from core import settlement
            if settlement.is_cash_stock(symbol):
                return bool(self.var_bot_ckcs.get())
            return bool(self.var_bot_ckps.get())
        return bool(self.var_auto_trade.get())

    def _disarm_auto_trade_on_startup(self):
        """Đồng bộ trạng thái BOT=OFF sau mỗi lần app khởi động/restart."""
        self.var_auto_trade.set(False)
        self.var_bot_ckps.set(False)
        self.var_bot_ckcs.set(False)
        config.AUTO_TRADE_ENABLED = False
        self._save_brain_live_config()

    def _ensure_trading_otp(self):
        return self._ensure_trading_otp_for_mode(
            bool(getattr(config, "PAPER_TRADING", True))
        )

    def _ensure_trading_otp_for_mode(self, paper_mode):
        """Hỏi & xác thực OTP DNSE (token 8h). True nếu OK / đã có token.

        PAPER mode: KHÔNG cần OTP (lệnh route sang paper broker, không gọi DNSE đặt lệnh)
        -> bỏ qua, cho bật bot test luôn.
        """
        import customtkinter
        import os
        # [FIX] OTP phải set token lên CHÍNH connector đặt lệnh (self.connector),
        # KHÔNG phải dnse_api (data_engine chỉ lo market-data, không cần token).
        if bool(paper_mode):
            self.log_message("📝 PAPER mode: bỏ qua OTP, bật bot bằng tiền ảo.", target="bot")
            return True
        # Đã có trading-token còn hiệu lực -> khỏi hỏi lại (vd vừa xác thực ở ⚙ ADVANCED).
        try:
            if self.connector.has_trading_token():
                self.log_message("✅ Đã có trading-token, bật bot luôn (không cần OTP lại).", target="bot")
                return True
        except Exception:
            pass
        otp_type = os.getenv("DNSE_OTP_TYPE", "email_otp")
        if str(otp_type).lower() == "email_otp":
            if not self.connector.send_email_otp():
                self.log_message("❌ Không gửi được Email OTP DNSE.", target="bot", error=True)
                return False
            prompt_text = "DNSE đã gửi Email OTP (hiệu lực khoảng 2 phút). Nhập mã để tạo Trading Token 8 giờ:"
        else:
            prompt_text = "Nhập Smart OTP DNSE hiện tại (mã thường đổi sau khoảng 30 giây) để tạo Trading Token 8 giờ:"
        dialog = customtkinter.CTkInputDialog(
            text=prompt_text,
            title="Xác thực OTP",
        )
        otp_code = dialog.get_input()
        if not otp_code:
            self.log_message("⚠ Đã huỷ nhập OTP. Bot chưa được bật.", target="bot", error=True)
            return False
        if not self.connector.verify_otp(otp_type, otp_code):
            self.log_message("❌ Xác thực OTP DNSE thất bại. Không thể bật Bot.", target="bot", error=True)
            return False
        return True

    def _refresh_bot_lights(self):
        """Đồng bộ màu 2 đèn nhóm + đèn tổng (legacy) theo cờ hiện tại."""
        ckps_on = bool(self.var_bot_ckps.get())
        ckcs_on = bool(self.var_bot_ckcs.get())
        for attr, on in (("ind_light_ckps", ckps_on), ("ind_light_ckcs", ckcs_on),
                         ("ind_auto_light", ckps_on or ckcs_on)):
            w = getattr(self, attr, None)
            try:
                if w is not None and w.winfo_exists():
                    w.configure(fg_color=COL_GREEN if on else COL_RED)
            except Exception:
                pass

    def _sync_bot_master_state(self):
        """Master = OR(CKPS, CKCS); lưu live config + refresh đèn."""
        master = bool(self.var_bot_ckps.get() or self.var_bot_ckcs.get())
        self.var_auto_trade.set(master)
        config.AUTO_TRADE_ENABLED = master
        self._save_brain_live_config()
        self._refresh_bot_lights()

    def on_bot_group_toggle(self, group):
        """Bật/tắt riêng 1 nhóm bot. group = 'CKPS' | 'CKCS'."""
        group = str(group).upper()
        var = self.var_bot_ckcs if group == "CKCS" else self.var_bot_ckps
        other = self.var_bot_ckps if group == "CKCS" else self.var_bot_ckcs
        label = "CƠ SỞ (CKCS)" if group == "CKCS" else "PHÁI SINH"
        if var.get():
            # Bật: chỉ hỏi OTP nếu chưa nhóm nào chạy (token chưa có).
            if not other.get() and not self._ensure_trading_otp():
                var.set(False)
                return
            self._sync_bot_master_state()
            self.log_message(f"🤖 BOT {label} ĐÃ BẬT. Bot sẽ tự bắn lệnh nhóm này.", target="bot")
        else:
            self._sync_bot_master_state()
            self.log_message(f"🛑 BOT {label} TẮT.", target="bot")

    def on_auto_trade_toggle(self):
        """Công tắc TỔNG (legacy): bật/tắt cả 2 nhóm cùng lúc."""
        if self.var_auto_trade.get():
            if not (self.var_bot_ckps.get() or self.var_bot_ckcs.get()):
                if not self._ensure_trading_otp():
                    self.var_auto_trade.set(False)
                    return
            self.var_bot_ckps.set(True)
            self.var_bot_ckcs.set(True)
            config.AUTO_TRADE_ENABLED = True
            self._save_brain_live_config()
            self._refresh_bot_lights()
            self.log_message("🤖 AUTO-TRADE DAEMON ĐÃ BẬT (cả 2 nhóm). Bot sẽ tự động bắn lệnh.", target="bot")
        else:
            self.var_bot_ckps.set(False)
            self.var_bot_ckcs.set(False)
            config.AUTO_TRADE_ENABLED = False
            self._save_brain_live_config()
            self._refresh_bot_lights()
            self.log_message("🛑 AUTO-TRADE DAEMON TẮT (cả 2 nhóm). Chuyển về chế độ bằng tay.", target="bot")

    def set_auto_trade_enabled(self, enabled, reason=""):
        enabled = bool(enabled)
        self.var_auto_trade.set(enabled)
        self.var_bot_ckps.set(enabled)
        self.var_bot_ckcs.set(enabled)
        config.AUTO_TRADE_ENABLED = enabled
        self._save_brain_live_config()
        self._refresh_bot_lights()

        if enabled:
            self.log_message("AUTO-TRADE DAEMON ON. Bot can open new trades.", target="bot")
        else:
            msg = "AUTO-TRADE DAEMON OFF. Manual mode."
            if reason:
                msg = f"{msg} Reason={reason}"
            self.log_message(msg, target="bot")

    def get_current_tactic_string(self):
        active = [k for k, v in self.tactic_states.items() if v]
        base_tactic = "+".join(active) if active else "OFF"
        if self.var_assist_dca.get() and "AUTO_DCA" not in base_tactic:
            base_tactic += "+AUTO_DCA"
        if self.var_assist_pca.get() and "AUTO_PCA" not in base_tactic:
            base_tactic += "+AUTO_PCA"
        return base_tactic

    def get_current_entry_exit_tactic_string(self):
        active = [k for k, v in self.entry_exit_tactic_states.items() if v]
        return "+".join(active) if active else "OFF"

    def _save_entry_exit_live_config(self):
        try:
            existing_data = load_brain_settings()
        except Exception:
            existing_data = {}

        entry_exit = existing_data.setdefault("entry_exit", {})
        active = [k for k, v in self.entry_exit_tactic_states.items() if v]
        non_r_active = [k for k in active if k != "FALLBACK_R"]
        entry_exit["enabled"] = bool(active)
        # Nút tactic ngoài màn chính chỉ bật/tắt tactic; không được tự đổi
        # lựa chọn CHỈ PREVIEW của người dùng trong Risk & TSL.
        entry_exit["preview_only"] = bool(entry_exit.get("preview_only", False))
        entry_exit["active_tactics"] = active
        entry_exit["entry_tactics"] = active
        if len(non_r_active) == 1:
            entry_exit["exit_tactic"] = non_r_active[0]
        elif active:
            entry_exit["exit_tactic"] = "AUTO"
        else:
            entry_exit["exit_tactic"] = "AUTO"

        try:
            save_brain_settings(existing_data)
        except Exception as e:
            self.log_message(f"Lỗi lưu Entry/Exit live config: {e}", error=True)

    def toggle_tactic(self, mode):
        next_state = not self.tactic_states[mode]
        self.tactic_states[mode] = next_state
        if mode == "BE_CASH" and next_state:
            self.tactic_states["BE"] = False
        elif mode == "BE" and next_state:
            self.tactic_states["BE_CASH"] = False
        self.update_tactic_buttons_ui()
        self.refresh_manual_preview_tab()

    def toggle_entry_exit_tactic(self, mode):
        self.entry_exit_tactic_states[mode] = not self.entry_exit_tactic_states[mode]
        self.update_entry_exit_buttons_ui()
        self._save_entry_exit_live_config()
        self.refresh_manual_preview_tab()

    def update_tactic_buttons_ui(self):
        def set_btn(btn, is_active):
            btn.configure(
                fg_color=COL_BLUE_ACCENT if is_active else COL_GRAY_BTN,
                hover_color=COL_BLUE_ACCENT_HOVER if is_active else "#616161",
            )

        set_btn(self.btn_tactic_be, self.tactic_states["BE"])
        set_btn(self.btn_tactic_pnl, self.tactic_states["PNL"])
        set_btn(self.btn_tactic_step, self.tactic_states["STEP_R"])
        set_btn(self.btn_tactic_swing, self.tactic_states["SWING"])

        if hasattr(self, "btn_tactic_cash"):  # [FIX] Update màu nút CASH
            set_btn(self.btn_tactic_cash, self.tactic_states["BE_CASH"])
        if hasattr(self, "btn_tactic_psar"):  # [FIX] Update màu nút PSAR
            set_btn(self.btn_tactic_psar, self.tactic_states["PSAR_TRAIL"])

        if hasattr(self, "btn_tactic_dca"):
            set_btn(self.btn_tactic_dca, self.tactic_states["AUTO_DCA"])
        if hasattr(self, "btn_tactic_pca"):
            set_btn(self.btn_tactic_pca, self.tactic_states["AUTO_PCA"])
        if hasattr(self, "btn_tactic_rev_c"):
            set_btn(self.btn_tactic_rev_c, self.tactic_states["REV_C"])
        if hasattr(self, "btn_tactic_anti_cash"):
            set_btn(self.btn_tactic_anti_cash, self.tactic_states["ANTI_CASH"])

    def update_entry_exit_buttons_ui(self):
        def set_btn(btn, is_active):
            btn.configure(
                fg_color=COL_BLUE_ACCENT if is_active else COL_GRAY_BTN,
                hover_color=COL_BLUE_ACCENT_HOVER if is_active else "#616161",
            )

        if hasattr(self, "btn_entry_r"):
            set_btn(self.btn_entry_r, self.entry_exit_tactic_states["FALLBACK_R"])
        if hasattr(self, "btn_entry_swing"):
            set_btn(self.btn_entry_swing, self.entry_exit_tactic_states["SWING_REJECTION"])
        if hasattr(self, "btn_entry_struct"):
            set_btn(self.btn_entry_struct, self.entry_exit_tactic_states["SWING_STRUCTURE"])
        if hasattr(self, "btn_entry_fib"):
            set_btn(self.btn_entry_fib, self.entry_exit_tactic_states["FIB_RETRACE"])
        if hasattr(self, "btn_entry_pullback"):
            set_btn(self.btn_entry_pullback, self.entry_exit_tactic_states["PULLBACK_ZONE"])

    def on_symbol_change(self, new_symbol):
        config.UI_ACTIVE_SYMBOL = new_symbol
        # Manual inputs belong to the previously selected symbol.  Never carry
        # a VN30F price/lot/SL/TP into a CKCS preview (or vice versa).
        for variable_name in (
            "var_manual_entry",
            "var_manual_lot",
            "var_manual_tp",
            "var_manual_sl",
        ):
            variable = getattr(self, variable_name, None)
            if variable is not None:
                variable.set("")
        unit = self._quantity_unit(new_symbol)
        label = self._quantity_label(new_symbol)
        if hasattr(self, "lbl_manual_qty_title"):
            self.lbl_manual_qty_title.configure(text=label)
        if hasattr(self, "lbl_prev_lot"):
            self.lbl_prev_lot.configure(text=f"{unit}: 0")
        if hasattr(self, "lbl_preview_symbol"):
            self.lbl_preview_symbol.configure(text=new_symbol)
        self._save_brain_live_config()
        self.lbl_dashboard_price.configure(
            text="ĐANG NẠP GIÁ...",
            text_color="#9E9E9E",
            font=("Roboto", 14, "bold"),
        )
        # Ngày hợp đồng là dữ liệu lịch, phải hiện ngay cả khi đóng phiên,
        # token hết hạn hoặc chưa tải được snapshot giá.
        self._update_contract_dates_label(new_symbol)
        self.on_direction_change(self.var_direction.get())
        # Grid preview removed
        self.refresh_manual_preview_tab()
        # Đổi mã phải nạp ngay snapshot đã lưu, kể cả ngoài phiên. Trước đây đoạn này
        # khởi tạo một thread rỗng nên UI bị kẹt ở "Đang nạp/chưa có giá" cho tới khi
        # vòng nền chạy lại (hoặc lâu hơn nếu thị trường đã đóng cửa).
        if hasattr(self, "running_tabs") and hasattr(self, "running_trees"):
            market = "CKPS" if self._is_derivative_symbol(new_symbol) else "CKCS"
            mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
            scope = f"{market} {mode}"
            if scope in self.running_trees:
                self.running_tabs.set(scope)
                self.tree = self.running_trees[scope]
        if getattr(self, "connector", None) is not None and getattr(self, "trade_mgr", None) is not None:
            threading.Thread(
                target=self._render_cached_ui_snapshot,
                args=(new_symbol,),
                daemon=True,
            ).start()

    def on_preset_change(self, value):
        try:
            config.DEFAULT_PRESET = value
        except Exception:
            pass
        self.refresh_manual_preview_tab()

    def on_manual_input_change(self, *_):
        try:
            self.refresh_limit_order_hint()
            self.refresh_manual_preview_tab()
        except Exception:
            pass

    def on_direction_change(self, value):
        self.var_direction.set(value)
        if hasattr(self, "btn_dir_buy") and hasattr(self, "btn_dir_sell"):
            buy_on = value == "BUY"
            self.btn_dir_buy.configure(
                fg_color=COL_GREEN if buy_on else "#424242",
                hover_color="#009624" if buy_on else "#616161",
            )
            self.btn_dir_sell.configure(
                fg_color=COL_RED if not buy_on else "#424242",
                hover_color="#B71C1C" if not buy_on else "#616161",
            )
        sym = self.cbo_symbol.get()
        is_stock = not self._is_derivative_symbol(sym)
        if value == "BUY":
            self.btn_action.configure(
                text=f"VÀO LỆNH MUA {sym}", fg_color=COL_GREEN, hover_color="#009624"
            )
        elif is_stock:
            # CK cơ sở KHÔNG bán khống — SELL = bán/đóng cổ phiếu ĐÃ VỀ đang giữ.
            self.btn_action.configure(
                text=f"BÁN (ĐÓNG) {sym}", fg_color=COL_RED, hover_color="#B71C1C"
            )
        else:
            self.btn_action.configure(
                text=f"VÀO LỆNH BÁN {sym}", fg_color=COL_RED, hover_color="#B71C1C"
            )

    def on_paper_mode_change(self, value):
        requested_paper = str(value or "").upper() == "PAPER"
        old_paper = bool(getattr(config, "PAPER_TRADING", True))
        if requested_paper == old_paper:
            return

        # Đổi mode tự do nhưng luôn thu hồi quyền MỞ lệnh mới. Vị thế mode cũ
        # vẫn được một TradeManager mode-bound quản lý BE/SWING ở nền.
        self._mode_switching = True
        self.set_auto_trade_enabled(False, reason="Đổi PAPER/REAL")
        try:
            save_state(self.trade_mgr.state)
        except Exception:
            pass
        config.PAPER_TRADING = requested_paper
        self.log_message(f"🔄 Chuyển sang chế độ: {'PAPER' if requested_paper else 'REAL'}", target="bot")
        # Ghi vào .env để NHỚ sau khi tắt/mở lại app.
        try:
            from core import env_utils
            env_utils.update_env({"PAPER_TRADING": "True" if config.PAPER_TRADING else "False"})
        except Exception as exc:
            self.log_message(f"Lưu PAPER_TRADING vào .env lỗi: {exc}", error=True, target="bot")
        self._save_brain_live_config()
        # MODE chỉ đổi nơi đặt lệnh mới. Giữ snapshot/cache của cả hai mode để
        # bốn tab không nhấp nháy hoặc mất vị thế khi người dùng chuyển mode.
        try:
            if getattr(self, "connector", None) is not None:
                if not config.PAPER_TRADING:
                    self.connector.connect()  # đảm bảo kết nối REAL
            # Runtime state tách riêng: PAPER không dùng chung PnL/TSL/ticket với REAL.
            if getattr(self, "trade_mgr", None) is not None:
                self.trade_mgr.state = load_state()
            self.update_portfolio_table()  # refresh tổng tài sản ngay
            if hasattr(self, "running_tabs"):
                market = "CKPS" if self._is_derivative_symbol(self.cbo_symbol.get()) else "CKCS"
                self.running_tabs.set(f"{market} {'PAPER' if config.PAPER_TRADING else 'REAL'}")
                self.tree = self.running_trees[self.running_tabs.get()]
            tip = "" if config.PAPER_TRADING else " — nhớ OTP để đặt lệnh thật (⚙ ADVANCED → Gửi OTP email)."
            self.log_message(f"✅ Đã áp dụng {value} ngay (không cần restart){tip}", target="bot")
        except Exception as exc:
            self.log_message(f"Áp dụng {value} lỗi: {exc} — nếu số dư chưa đúng thì restart app.", error=True, target="bot")
        finally:
            self._mode_switching = False

    def on_market_type_change(self, value):
        if str(value or "").upper() in ("CK CƠ SỞ", "CK CO SO", "CKCS"):
            symbols = list(getattr(config, "CKCS_WATCHLIST", []) or [])
            if not symbols:
                symbols = ["FPT"]
            self.cbo_symbol.configure(values=symbols)
            self.cbo_symbol.set(symbols[0])
        else:
            symbols = list(getattr(config, "CKPS_SYMBOLS", []) or ["VN30F1M"])
            self.cbo_symbol.configure(values=symbols)
            self.cbo_symbol.set(symbols[0])
        self.on_symbol_change(self.cbo_symbol.get())
        if hasattr(self, "running_tabs"):
            market = "CKPS" if self._is_derivative_symbol(self.cbo_symbol.get()) else "CKCS"
            mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
            self.running_tabs.set(f"{market} {mode}")
            self.tree = self.running_trees[self.running_tabs.get()]

    def on_preview_group_change(self, value):
        preset = getattr(config, "DEFAULT_PRESET", "SCALPING")
        group = self._preview_group_value()
        preset_cfg = config.PRESETS.setdefault(preset, {})
        preset_cfg["MANUAL_SL_GROUP"] = group
        preset_cfg["MANUAL_TP_GROUP"] = group
        preset_cfg["MANUAL_SWING_SL_GROUP"] = group
        preset_cfg["MANUAL_SWING_TP_GROUP"] = group
        try:
            self.save_settings()
        except Exception as exc:
            self.log_message(f"Save preview group error: {exc}", error=True, target="manual")
        self.refresh_manual_preview_tab()

    def on_preview_manual_sl_group_change(self, value):
        self.on_preview_group_change(value)

    def _preview_group_from_value(self, value, default="G2"):
        raw = str(value or default)
        if raw.startswith("G"):
            return raw.split(" ", 1)[0]
        if "DYNAMIC" in raw.upper():
            return "DYNAMIC"
        return default

    def on_preview_sl_group_change(self, value):
        preset = getattr(config, "DEFAULT_PRESET", "SCALPING")
        group = self._preview_group_from_value(value)
        preset_cfg = config.PRESETS.setdefault(preset, {})
        preset_cfg["MANUAL_SL_GROUP"] = group
        preset_cfg["MANUAL_SWING_SL_GROUP"] = group
        try:
            self.save_settings()
        except Exception as exc:
            self.log_message(f"Save preview SL group error: {exc}", error=True, target="manual")
        self.refresh_manual_preview_tab()

    def on_preview_tp_group_change(self, value):
        preset = getattr(config, "DEFAULT_PRESET", "SCALPING")
        group = self._preview_group_from_value(value)
        preset_cfg = config.PRESETS.setdefault(preset, {})
        preset_cfg["MANUAL_TP_GROUP"] = group
        preset_cfg["MANUAL_SWING_TP_GROUP"] = group
        try:
            self.save_settings()
        except Exception as exc:
            self.log_message(f"Save preview TP group error: {exc}", error=True, target="manual")
        self.refresh_manual_preview_tab()

    def on_preview_sl_mode_change(self, value):
        preset_cfg = config.PRESETS.setdefault(getattr(config, "DEFAULT_PRESET", "SCALPING"), {})
        mode = self._manual_mode_value(value, "PERCENT")
        preset_cfg["MANUAL_SL_MODE"] = mode
        preset_cfg["USE_SWING_SL"] = mode in ("SWING_REJECTION", "SWING_STRUCTURE")
        try:
            self.save_settings()
        except Exception as exc:
            self.log_message(f"Save preview SL mode error: {exc}", error=True, target="manual")
        self.update_manual_entry_exit_buttons_ui()
        self.refresh_manual_preview_tab()

    def on_preview_tp_mode_change(self, value):
        preset_cfg = config.PRESETS.setdefault(getattr(config, "DEFAULT_PRESET", "SCALPING"), {})
        mode = self._manual_mode_value(value, "RR")
        preset_cfg["MANUAL_TP_MODE"] = mode
        preset_cfg["USE_SWING_TP"] = mode in ("SWING_REJECTION", "SWING_STRUCTURE")
        try:
            self.save_settings()
        except Exception as exc:
            self.log_message(f"Save preview TP mode error: {exc}", error=True, target="manual")
        self.update_manual_entry_exit_buttons_ui()
        self.refresh_manual_preview_tab()

    def set_manual_entry_exit_quick_mode(self, mode):
        """Select one coherent SL/TP pair for Manual Preview/Preset only."""
        mode = str(mode or "").upper()
        pairs = {
            "R": ("PERCENT", "RR"),
            "RETEST": ("SWING_REJECTION", "SWING_REJECTION"),
            "STRUCT": ("SWING_STRUCTURE", "SWING_STRUCTURE"),
            "FIB": ("FIB", "FIB"),
            "PULL": ("PULLBACK", "PULLBACK"),
        }
        if mode not in pairs:
            return

        sl_mode, tp_mode = pairs[mode]
        preset_cfg = config.PRESETS.setdefault(
            getattr(config, "DEFAULT_PRESET", "SCALPING"),
            {},
        )
        preset_cfg["MANUAL_SL_MODE"] = sl_mode
        preset_cfg["MANUAL_TP_MODE"] = tp_mode
        preset_cfg["USE_SWING_SL"] = sl_mode in (
            "SWING_REJECTION",
            "SWING_STRUCTURE",
        )
        preset_cfg["USE_SWING_TP"] = tp_mode in (
            "SWING_REJECTION",
            "SWING_STRUCTURE",
        )

        if hasattr(self, "var_preview_sl_mode"):
            self.var_preview_sl_mode.set(self._manual_mode_display(sl_mode, "SL"))
        if hasattr(self, "var_preview_tp_mode"):
            self.var_preview_tp_mode.set(self._manual_mode_display(tp_mode, "TP"))
        try:
            self.save_settings()
        except Exception as exc:
            self.log_message(
                f"Save manual Entry/Exit quick mode error: {exc}",
                error=True,
                target="manual",
            )
        self.update_manual_entry_exit_buttons_ui()
        self.refresh_manual_preview_tab()

    def update_manual_entry_exit_buttons_ui(self):
        preset_cfg = config.PRESETS.get(
            getattr(config, "DEFAULT_PRESET", "SCALPING"),
            {},
        )
        sl_mode = self._manual_mode_value(
            preset_cfg.get("MANUAL_SL_MODE", "PERCENT"),
            "PERCENT",
        )
        tp_mode = self._manual_mode_value(
            preset_cfg.get("MANUAL_TP_MODE", "RR"),
            "RR",
        )
        active = {
            "R": sl_mode == "PERCENT" and tp_mode == "RR",
            "RETEST": sl_mode == "SWING_REJECTION" and tp_mode == "SWING_REJECTION",
            "STRUCT": sl_mode == "SWING_STRUCTURE" and tp_mode == "SWING_STRUCTURE",
            "FIB": sl_mode == "FIB" and tp_mode == "FIB",
            "PULL": sl_mode == "PULLBACK" and tp_mode == "PULLBACK",
        }
        buttons = {
            "R": getattr(self, "btn_manual_entry_r", None),
            "RETEST": getattr(self, "btn_manual_entry_retest", None),
            "STRUCT": getattr(self, "btn_manual_entry_struct", None),
            "FIB": getattr(self, "btn_manual_entry_fib", None),
            "PULL": getattr(self, "btn_manual_entry_pull", None),
        }
        for name, button in buttons.items():
            if button is None:
                continue
            enabled = active[name]
            button.configure(
                fg_color=COL_BLUE_ACCENT if enabled else COL_GRAY_BTN,
                hover_color=COL_BLUE_ACCENT_HOVER if enabled else "#616161",
            )

    def on_manual_trade_mode_change(self, value):
        # Vẫn cho XEM cả 3 mode, nhưng ngoài phiên đấu giá thì KHÔNG cho dùng ATO/ATC
        # -> tự bật lại NORMAL + báo. (Xem chỉ báo PHIÊN để biết khi nào chọn được.)
        if value in ("ATO", "ATC"):
            try:
                from core.market_hours import market_session_phase
                sym = self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else getattr(config, "DEFAULT_SYMBOL", "")
                phase, _ = market_session_phase(sym)
            except Exception:
                phase = ""
            if phase != value:
                self.var_manual_trade_mode.set("NORMAL")
                self.log_message(
                    f"⏳ {value} chỉ chọn được trong phiên {value} (xem chỉ báo PHIÊN). Giữ NORMAL.",
                    target="manual",
                )
                value = "NORMAL"
        self.var_manual_trade_mode.set(value)
        if value != "NORMAL" and hasattr(self, "var_preview_trade_after_apply"):
            self.var_preview_trade_after_apply.set(False)
        if hasattr(self, "chk_preview_trade_after_apply"):
            self.chk_preview_trade_after_apply.configure(
                state="normal" if value == "NORMAL" else "disabled"
            )
        self.refresh_limit_order_hint()
        self.refresh_manual_preview_tab()

    def refresh_limit_order_hint(self):
        label = getattr(self, "lbl_limit_order_hint", None)
        # Nút gộp: dùng dòng trạng thái mới (dưới nút) -> ẩn hint cũ để khỏi trùng.
        if bool(getattr(config, "UNIFIED_ORDER_BUTTON", True)):
            if label:
                label.configure(text="")
            self.refresh_order_status()
            return
        if not label:
            return
        try:
            symbol = self.cbo_symbol.get()
            mode = self.var_manual_trade_mode.get()
            entry = self._price_input_to_internal(self.var_manual_entry.get(), symbol)
            from core.market_hours import market_session_phase
            phase, phase_label = market_session_phase(symbol)
            if entry > 0:
                if phase == "OPEN":
                    text = f"LO @{self._fmt_price(entry, symbol)} -> DNSE"
                    color = "#81C784"
                else:
                    text = f"LO @{self._fmt_price(entry, symbol)} -> OPEN"
                    color = "#FFD54F"
            elif mode in ("ATO", "ATC"):
                action = "DNSE" if phase == mode else "VIEW"
                text = f"{mode} {action} | auction"
                color = "#26C6DA" if phase == mode else "#FFD54F"
            else:
                action = "DNSE" if phase in ("ATO", "OPEN", "ATC") else "CACHE"
                text = f"NORMAL {action}"
                color = "#81C784" if phase in ("ATO", "OPEN", "ATC") else "#FFD54F"
            label.configure(text=text, text_color=color)
        except Exception:
            label.configure(text="LO -> OPEN | blank -> ATO", text_color="#B0BEC5")
        # Nút gộp: cập nhật luôn dòng trạng thái mới.
        self.refresh_order_status()

    def refresh_order_status(self):
        """Dòng trạng thái cho nút gộp: nói rõ nút sắp gửi lệnh gì theo phiên + tick."""
        lbl = getattr(self, "lbl_order_status", None)
        if lbl is None:
            return
        if not bool(getattr(config, "UNIFIED_ORDER_BUTTON", True)):
            return
        try:
            from core.market_hours import market_session_phase
            symbol = self.cbo_symbol.get()
            phase = market_session_phase(symbol)[0]
            market = bool(self.var_manual_market.get())
            mode = self.var_manual_order_mode.get() if hasattr(self, "var_manual_order_mode") else "NORMAL"
            entry = self._price_input_to_internal(self.var_manual_entry.get(), symbol)
            if mode in ("ATO", "ATC"):
                if phase == mode:
                    text, color = f"→ Gửi {mode} ngay (đấu giá)", "#26C6DA"
                else:
                    ref = self._last_auction_price(symbol, mode)
                    reftxt = f" · giá {mode} gần nhất @{self._fmt_price(ref, symbol)}" if ref > 0 else ""
                    text, color = f"→ Hẹn {mode} (cache bot){reftxt}", "#FFD54F"
            else:
                price = entry if entry > 0 else float(getattr(self, "last_price_val", 0.0) or 0.0)
                ptxt = f"@{self._fmt_price(price, symbol)}" if price > 0 else ""
                if market and phase == "OPEN":
                    text, color = "→ Thị trường: khớp ngay mọi giá", "#81C784"
                elif phase == "OPEN":
                    text, color = f"→ LO {ptxt}: gửi ngay (phiên liên tục)", "#81C784"
                else:
                    text, color = f"→ NORMAL: hẹn LO {ptxt} (cache bot) tới phiên liên tục", "#FFD54F"
            lbl.configure(text=text, text_color=color)
        except Exception:
            lbl.configure(text="", text_color="#81C784")

    def on_schedule_session_change(self, value):
        """Dropdown kiểu lệnh 24/7: ⚡ NORMAL | ☀️ ATO | 🌙 ATC."""
        v = str(value).upper()
        mode = "ATC" if "ATC" in v else ("ATO" if "ATO" in v else "NORMAL")
        self.var_manual_order_mode.set(mode)
        if mode in ("ATO", "ATC"):
            self.var_manual_schedule_session.set(mode)
        self.refresh_order_status()

    def _last_auction_price(self, symbol, kind):
        """Giá ATO/ATC gần nhất = open/close nến NGÀY gần nhất. Cache ~5 phút."""
        try:
            cache = self._auction_price_cache.get(symbol) or {}
            if (time.time() - float(cache.get("ts", 0.0))) > 300.0:
                df = data_engine._fetch_bars(symbol, "1d", 2, {})
                if df is not None and len(df) > 0:
                    last = df.iloc[-1]
                    cache = {"ts": time.time(), "ato": float(last.get("open", 0.0) or 0.0),
                             "atc": float(last.get("close", 0.0) or 0.0)}
                    self._auction_price_cache[symbol] = cache
            return float(cache.get("ato" if kind == "ATO" else "atc", 0.0) or 0.0)
        except Exception:
            return 0.0

    def on_click_smart_order(self):
        """Nút gộp: theo kiểu lệnh đã chọn (NORMAL/ATO/ATC) -> gửi ngay hoặc hẹn (cache bot)."""
        try:
            from core.market_hours import market_session_phase
            symbol = self.cbo_symbol.get()
            phase = market_session_phase(symbol)[0]
        except Exception:
            phase = ""
        market = bool(self.var_manual_market.get())
        mode = self.var_manual_order_mode.get() if hasattr(self, "var_manual_order_mode") else "NORMAL"

        if mode in ("ATO", "ATC"):
            # Lệnh đấu giá: không có giá LO. Gửi ngay nếu đang đúng phiên, không thì hẹn.
            self.var_manual_entry.set("")
            self.var_manual_trade_mode.set(mode)
            self.var_manual_schedule_session.set(mode)
            send_now = (phase == mode)
        else:
            # NORMAL = phiên liên tục.
            self.var_manual_trade_mode.set("NORMAL")
            if market and phase == "OPEN":
                self.var_manual_entry.set("")            # Thị trường: khớp ngay mọi giá
                send_now = True
            else:
                # LO: cần giá cụ thể (trống -> điền giá đang chạy). Ngoài giờ -> hẹn LO (cache bot).
                if self._price_input_to_internal(self.var_manual_entry.get(), symbol) <= 0:
                    live = float(getattr(self, "last_price_val", 0.0) or 0.0)
                    if live > 0:
                        self.var_manual_entry.set(self._price_internal_to_input(live, symbol))
                send_now = (phase == "OPEN")

        if send_now:
            self.on_click_trade()
        else:
            self.on_click_schedule_order()

    def _preview_tf_group(self, symbol, context):
        raw = ""
        if hasattr(self, "var_preview_tf"):
            raw = str(self.var_preview_tf.get() or "Auto")
        if raw.startswith("G"):
            return raw.split(" ", 1)[0]
        mode = str((context or {}).get("market_mode", "ANY") or "ANY").upper()
        return "G1" if mode in ("TREND", "BREAKOUT") else "G2"

    def _preview_group_value(self):
        raw = ""
        if hasattr(self, "var_preview_tf"):
            raw = str(self.var_preview_tf.get() or "G2")
        if raw.startswith("G"):
            return raw.split(" ", 1)[0]
        if "DYNAMIC" in raw.upper():
            return "DYNAMIC"
        return "G2"

    def _fmt_price(self, value, symbol=None):
        try:
            value = float(value)
            if value <= 0:
                return "--"
            symbol = symbol or (self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else "")
            if symbol and settlement.is_cash_stock(symbol):
                shown = value * 1000.0 / money_display_scale()
                scale = money_display_scale()
                digits = 0 if scale <= 1 else (2 if scale <= 1000 else 6)
                text = f"{shown:,.{digits}f}"
                return text.rstrip("0").rstrip(".") if "." in text else text
            return f"{value:.2f}"
        except Exception:
            return "--"

    def _price_input_to_internal(self, value, symbol=None):
        """UI CKCS dùng VND/CP; bên trong và DNSE vẫn dùng giá bảng điện ÷1.000."""
        raw = self._safe_float(value or 0.0)
        symbol = symbol or (self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else "")
        if symbol and settlement.is_cash_stock(symbol):
            return raw * money_display_scale() / 1000.0
        return raw

    def _price_internal_to_input(self, value, symbol=None):
        raw = self._safe_float(value or 0.0)
        symbol = symbol or (self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else "")
        if raw > 0 and symbol and settlement.is_cash_stock(symbol):
            shown = raw * 1000.0 / money_display_scale()
            return f"{shown:.6f}".rstrip("0").rstrip(".")
        return f"{raw:g}" if raw > 0 else ""

    def _update_contract_dates_label(self, symbol):
        label = getattr(self, "lbl_contract_dates", None)
        if label is None:
            return
        is_front_contract = str(symbol or "").upper() == "VN30F1M"
        if not is_front_contract:
            try:
                label.pack_forget()
            except Exception:
                pass
            return
        try:
            from core.market_calendar import vn30_front_contract_period

            first_date, last_date = vn30_front_contract_period()
            text = f"HĐ CKPS: {first_date:%d/%m/%Y} → {last_date:%d/%m/%Y}"
        except Exception:
            text = "HĐ CKPS: --/--/---- → --/--/----"
        try:
            if not label.winfo_manager():
                label.pack(
                    fill="x",
                    padx=10,
                    pady=(0, 1),
                    before=getattr(self, "dashboard_separator", None),
                )
            label.configure(text=text)
        except Exception:
            pass

    def _preview_color_for_status(self, status, direction=None):
        status = str(status or "").upper()
        direction = str(direction or "").upper()
        if status == "READY":
            return COL_GREEN if direction != "SELL" else COL_RED
        if status == "BLOCK":
            return COL_RED
        if status == "WAIT":
            return COL_WARN
        return "#78909C"

    def _safe_float(self, value, default=0.0):
        try:
            return float(value)
        except Exception:
            return default

    def _resolve_manual_preset_group(self, params, key, context):
        group = str((params or {}).get(key, "G2") or "G2")
        if "DYNAMIC" in group:
            mode = str((context or {}).get("market_mode", "ANY") or "ANY").upper()
            return "G1" if mode in ("TREND", "BREAKOUT") else "G2"
        return group

    def _sandbox_sl_group_for_symbol(self, symbol, context, fallback="G2"):
        """SL Sandbox luôn đọc base_sl hiệu lực của đúng mã."""
        resolver = getattr(self.trade_mgr, "_resolve_base_sl_group", None)
        if callable(resolver):
            return resolver(symbol, context, fallback=fallback)
        try:
            brain = self.trade_mgr._get_brain_settings(symbol)
        except Exception:
            brain = {}
        risk_tsl = (brain or {}).get("risk_tsl", {}) or {}
        group = str(risk_tsl.get("base_sl") or fallback or "G2")
        if "DYNAMIC" in group.upper():
            mode = str((context or {}).get("market_mode", "ANY") or "ANY").upper()
            return "G1" if mode in ("TREND", "BREAKOUT") else "G2"
        return group

    def _manual_rule_mode(self, params, mode_key, legacy_key, default):
        mode = str((params or {}).get(mode_key, "") or "").upper()
        if mode:
            if "SANDBOX" in mode:
                # Gói preset cũ: chuyển về Swing Retest riêng của lệnh tay.
                return "SWING_REJECTION"
            if mode in ("SWING_REJECTION", "SWING_RETEST", "RETEST"):
                return "SWING_REJECTION"
            if mode in ("SWING_STRUCTURE", "SWING_STRUCT", "STRUCT"):
                return "SWING_STRUCTURE"
            if "FIB" in mode:
                return "FIB"
            if "PULL" in mode:
                return "PULLBACK"
            if "SWING" in mode:
                return "SWING_REJECTION"
            if "RR" in mode:
                return "RR"
            if "OFF" in mode or "NO_TP" in mode:
                return "OFF"
            if "PERCENT" in mode:
                return "PERCENT"
            return default
        return "SWING_REJECTION" if bool((params or {}).get(legacy_key, False)) else default

    def _manual_mode_display(self, mode, kind="SL"):
        mode = str(mode or "").upper()
        mapping = {
            "PERCENT": "Percent",
            "SANDBOX": "Swing Retest",
            "RR": "RR",
            "OFF": "OFF",
            "NO_TP": "OFF",
            "SWING": "Swing Retest",
            "SWING_REJECTION": "Swing Retest",
            "SWING_RETEST": "Swing Retest",
            "RETEST": "Swing Retest",
            "SWING_STRUCTURE": "Swing Struct",
            "SWING_STRUCT": "Swing Struct",
            "STRUCT": "Swing Struct",
            "FIB": "FIB",
            "FIB_RETRACE": "FIB",
            "PULLBACK": "Pullback",
            "PULLBACK_ZONE": "Pullback",
            "PULL": "Pullback",
        }
        return mapping.get(mode, "Percent" if kind == "SL" else "RR")

    def _manual_mode_value(self, value, default):
        mode = str(value or default).upper()
        if "STRUCT" in mode:
            return "SWING_STRUCTURE"
        if "SANDBOX" in mode:
            return "SWING_REJECTION"
        if "RETEST" in mode:
            return "SWING_REJECTION"
        if "FIB" in mode:
            return "FIB"
        if "PULL" in mode:
            return "PULLBACK"
        if "SWING" in mode:
            return "SWING_REJECTION"
        if "PERCENT" in mode:
            return "PERCENT"
        if mode == "R" or "RR" in mode:
            return "RR"
        if "OFF" in mode or "NO_TP" in mode:
            return "OFF"
        return default

    def _manual_source_label(self, source):
        raw = str(source or "--")
        upper = raw.upper()
        if upper == "MANUAL_SL":
            return "Manual Input"
        if upper == "MANUAL_TP":
            return "Manual Input"
        if upper.startswith("SANDBOX") or upper.startswith("MANUAL_SANDBOX"):
            return "Swing Retest"
        if "SWING_RETEST" in upper or "SWING_REJECTION" in upper:
            return "Swing Retest"
        if "SWING_STRUCTURE" in upper:
            return "Swing Struct"
        if upper.startswith("MANUAL_SWING"):
            return "Swing Retest"
        if upper.startswith("MANUAL_FIB"):
            return "FIB"
        if upper.startswith("MANUAL_PULLBACK"):
            return "Pullback"
        if upper == "PERCENT":
            return "Percent"
        if upper == "OFF" or upper == "NO_TP":
            return "OFF"
        if upper == "RR" or upper.endswith("R"):
            return "RR"
        return raw

    def _pullback_zone_from_context(self, direction, context, group, params):
        atr = self._safe_float((context or {}).get(f"atr_{group}", (context or {}).get("atr_entry", 0.0)))
        if atr <= 0:
            return None, atr, "PULLBACK"
        source = str((params or {}).get("MANUAL_PULLBACK_SOURCE", "EMA20") or "EMA20").upper()
        if source == "SWING":
            high = self._safe_float((context or {}).get(f"swing_high_{group}", 0.0))
            low = self._safe_float((context or {}).get(f"swing_low_{group}", 0.0))
            mid = low if direction == "BUY" else high
        elif source == "BB_MID":
            mid = self._safe_float((context or {}).get(f"bb_mid_{group}", (context or {}).get("bb_mid", 0.0)))
        else:
            mid = self._safe_float(
                (context or {}).get(f"ema20_{group}", (context or {}).get("ema20", (context or {}).get(f"EMA_20_{group}", 0.0)))
            )
            source = "EMA20"
        if mid <= 0:
            return None, atr, f"PULLBACK_{source}"
        width = atr * self._safe_float((params or {}).get("MANUAL_PULLBACK_ATR_WIDTH", 0.5), 0.5)
        return (mid - width, mid + width), atr, f"PULLBACK_{source}"

    def _fmt_zone(self, zone):
        try:
            if not zone or len(zone) < 2:
                return "--"
            lo, hi = float(zone[0]), float(zone[1])
            if lo <= 0 or hi <= 0:
                return "--"
            return f"{lo:.2f}-{hi:.2f}"
        except Exception:
            return "--"

    def _format_ee_gate(self, decision, current_price=0.0):
        status = str((decision or {}).get("status", "OFF") or "OFF").upper()
        tactic = str((decision or {}).get("entry_tactic", "OFF") or "OFF").upper()
        short = {
            "FALLBACK_R": "R",
            "SWING_REJECTION": "RETEST",
            "SWING_STRUCTURE": "STRUCT",
            "FIB_RETRACE": "FIB",
            "PULLBACK_ZONE": "PULL",
            "MULTI_WAIT": "MULTI",
            "OFF": "OFF",
        }.get(tactic, tactic)
        zone = decision.get("entry_zone") if isinstance(decision, dict) else None
        zone_txt = self._fmt_zone(zone)
        if zone_txt != "--":
            return f"{status} {short} | Zone {zone_txt}"
        reason = str((decision or {}).get("reason", "") or "")
        if tactic == "FALLBACK_R" and "MISSING E/E DATA" in reason.upper():
            reason = ""
        reason = (
            reason.replace("Missing pullback source", "Missing")
            .replace("Missing ATR", "Missing ATR")
            .replace("Fallback R entry", "")
        ).strip()
        return f"{status} {short}" + (f" | {reason}" if reason else "")

    def _entry_zone_decision(self, decision):
        if not isinstance(decision, dict):
            return {}
        if self._fmt_zone(decision.get("entry_zone")) != "--":
            return decision
        waits = decision.get("wait_decisions") or []
        order = ["SWING_REJECTION", "SWING_STRUCTURE", "FIB_RETRACE", "PULLBACK_ZONE", "FALLBACK_R"]
        for tactic in order:
            for item in waits:
                if item.get("entry_tactic") == tactic and self._fmt_zone(item.get("entry_zone")) != "--":
                    return item
        return decision

    def _manual_preview_entry_exit_cfg(self, base_cfg):
        cfg = dict(base_cfg or {})
        active = [k for k, v in getattr(self, "entry_exit_tactic_states", {}).items() if v]
        if active:
            cfg["enabled"] = True
            cfg["active_tactics"] = active
            cfg["entry_tactics"] = active
        return cfg

    def _preview_entry_exit_decision(self, symbol, direction, price, context, ee_cfg):
        from core.entry_exit_engine import evaluate_entry_exit

        active = list((ee_cfg or {}).get("entry_tactics") or (ee_cfg or {}).get("active_tactics") or [])
        non_r = [mode for mode in active if mode != "FALLBACK_R"]
        if non_r:
            technical_cfg = dict(ee_cfg or {})
            technical_cfg["active_tactics"] = non_r
            technical_cfg["entry_tactics"] = non_r
            technical_cfg["enabled"] = True
            technical_cfg["missing_data_policy"] = "ERROR"
            technical_decision = evaluate_entry_exit(symbol, direction, price, context, technical_cfg)
            if technical_decision.get("status") in ("READY", "WAIT"):
                return technical_decision
            if "PULLBACK_ZONE" in non_r or len(non_r) == 1:
                if technical_decision.get("status") == "ERROR" and technical_decision.get("entry_tactic") in (None, "", "OFF"):
                    technical_decision["entry_tactic"] = non_r[0]
                return technical_decision
        return evaluate_entry_exit(symbol, direction, price, context, ee_cfg)

    def _symbol_group_timeframe(self, symbol, group, context=None):
        group = str(group or "")
        if not group.startswith("G"):
            return group
        context = context or {}
        resolved = context.get("group_timeframes", {})
        if isinstance(resolved, dict) and resolved.get(group):
            return str(resolved[group])
        try:
            brain = self.trade_mgr._get_brain_settings(symbol)
        except Exception:
            brain = {}
        return str(
            (brain or {}).get(
                f"{group}_TIMEFRAME",
                getattr(config, f"{group}_TIMEFRAME", group),
            )
            or group
        )

    def _group_tf_label(self, group, symbol=None, context=None):
        group = str(group or "--")
        tf = self._symbol_group_timeframe(symbol, group, context)
        return f"{group} ({tf})" if group.startswith("G") else group

    def _resolve_manual_sl_price(self, symbol, direction, price, params, context, manual_sl=0.0):
        context = context or {}
        params = params or {}
        if manual_sl and manual_sl > 0:
            return manual_sl, abs(price - manual_sl), "MANUAL", False

        sl_mode = self._manual_rule_mode(params, "MANUAL_SL_MODE", "USE_SWING_SL", "PERCENT")
        sl_group = self._resolve_manual_preset_group(params, "MANUAL_SL_GROUP", context)
        market_mode = str(context.get("market_mode", "ANY") or "ANY").upper()

        if sl_mode == "SANDBOX":
            brain = self.trade_mgr._get_brain_settings(symbol)
            risk_tsl = brain.get("risk_tsl", {}) or {}
            sandbox_group = self._sandbox_sl_group_for_symbol(
                symbol,
                context,
                sl_group or getattr(config, "BOT_BASE_SL", "G2"),
            )
            atr_val = self._safe_float(context.get(f"atr_{sandbox_group}", context.get("atr_entry", 0.0)))
            swing_low = self._safe_float(context.get(f"swing_low_{sandbox_group}", 0.0))
            swing_high = self._safe_float(context.get(f"swing_high_{sandbox_group}", 0.0))
            sl_mult = self._safe_float(risk_tsl.get("sl_atr_multiplier", getattr(config, "sl_atr_multiplier", 0.2)), 0.2)
            if atr_val > 0 and swing_low > 0 and swing_high > 0:
                buffer = atr_val * sl_mult
                sl_price = swing_low - buffer if direction == "BUY" else swing_high + buffer
                # [WRONG-SIDE GUARD] Swing nằm sai phía so với giá (vd giá đã thủng đáy swing)
                # -> SL vô nghĩa + distance hẹp giả làm lot phồng -> coi như thiếu data, về Percent.
                _side_ok = sl_price < price if direction == "BUY" else sl_price > price
                if _side_ok:
                    return sl_price, abs(price - sl_price), f"SANDBOX:{sandbox_group}", False
            sl_mode = "PERCENT"

        if sl_mode in ("SWING_REJECTION", "SWING_STRUCTURE"):
            if "MANUAL_SL_GROUP" not in params and "MANUAL_SWING_SL_GROUP" in params:
                sl_group = self._resolve_manual_preset_group(params, "MANUAL_SWING_SL_GROUP", context)
            atr_val = self._safe_float(context.get(f"atr_{sl_group}", context.get("atr", 0.0)))
            swing_low = self._safe_float(context.get(f"swing_low_{sl_group}", 0.0))
            swing_high = self._safe_float(context.get(f"swing_high_{sl_group}", 0.0))
            if atr_val > 0 and swing_low > 0 and swing_high > 0:
                sl_mult = self._safe_float(params.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2)), 0.2)
                buffer = atr_val * sl_mult
                sl_price = swing_low - buffer if direction == "BUY" else swing_high + buffer
                # [WRONG-SIDE GUARD] Swing sai phía -> báo missing để caller fallback Percent.
                _side_ok = sl_price < price if direction == "BUY" else sl_price > price
                if _side_ok:
                    return sl_price, abs(price - sl_price), f"SWING:{sl_group}", False
            return 0.0, 0.0, f"{sl_mode}:MISSING", True

        sl_dist = price * (float(params.get("SL_PERCENT", 0.5) or 0.5) / 100.0)
        sl_price = price - sl_dist if direction == "BUY" else price + sl_dist
        return sl_price, sl_dist, f"PERCENT:{float(params.get('SL_PERCENT', 0.5) or 0.5):g}%", False

    # --- [SANDBOX-FETCH] On-demand context cho mã dashboard (CKCS không nằm trong BOT_ACTIVE_SYMBOLS) ---

    def _manual_context_ready(self, params, context, symbol=None):
        """True nếu context đã đủ key kỹ thuật (atr/swing theo group) cho SL/TP mode của preset."""
        params = params or {}
        context = context or {}
        tech_modes = ("SANDBOX", "SWING_REJECTION", "SWING_STRUCTURE", "FIB", "PULLBACK")
        needed = set()
        sl_mode = self._manual_rule_mode(params, "MANUAL_SL_MODE", "USE_SWING_SL", "PERCENT")
        tp_mode = self._manual_rule_mode(params, "MANUAL_TP_MODE", "USE_SWING_TP", "RR")
        if sl_mode in tech_modes:
            key = "MANUAL_SL_GROUP" if "MANUAL_SL_GROUP" in params else "MANUAL_SWING_SL_GROUP"
            preset_group = self._resolve_manual_preset_group(params, key, context)
            needed.add(
                self._sandbox_sl_group_for_symbol(symbol, context, preset_group)
                if sl_mode == "SANDBOX" and symbol
                else preset_group
            )
        if tp_mode in tech_modes:
            key = "MANUAL_TP_GROUP" if "MANUAL_TP_GROUP" in params else "MANUAL_SWING_TP_GROUP"
            needed.add(self._resolve_manual_preset_group(params, key, context))
        if not needed:
            return True
        for g in needed:
            if not (
                self._safe_float(context.get(f"atr_{g}", 0.0)) > 0
                and self._safe_float(context.get(f"swing_low_{g}", 0.0)) > 0
                and self._safe_float(context.get(f"swing_high_{g}", 0.0)) > 0
            ):
                return False
        return True

    def _schedule_symbol_context_fetch(self, symbol):
        sym_raw = str(symbol or "").strip()
        if not sym_raw:
            return
        sym_up = sym_raw.upper()
        now = time.time()
        with self.ctx_fetch_lock:
            if sym_up in self.ctx_fetch_inflight:
                return
            if now - self.ctx_fetch_last.get(sym_up, 0) < 15:
                return
            self.ctx_fetch_last[sym_up] = now
            self.ctx_fetch_inflight.add(sym_up)
        threading.Thread(
            target=self._fetch_symbol_context_worker, args=(sym_raw,), daemon=True
        ).start()

    def _fetch_symbol_context_worker(self, symbol):
        sym_up = str(symbol).strip().upper()
        try:
            from core.data_engine import data_engine
            from signals.signal_generator import signal_generator

            dfs, context = data_engine.fetch_data_v4(symbol)
            if dfs is None or context is None:
                return
            signal = signal_generator.generate_signal_v4(dfs, context, symbol=symbol)
            context["latest_signal"] = signal
            context["timestamp"] = time.time()
            context["preview_fallback"] = True
            try:
                self.after(0, lambda s=symbol, c=context: self._store_symbol_context(s, c))
            except Exception:
                pass
        except Exception:
            pass
        finally:
            with self.ctx_fetch_lock:
                self.ctx_fetch_inflight.discard(sym_up)

    def _store_symbol_context(self, symbol, context):
        try:
            sym_raw = str(symbol or "").strip()
            sym_up = sym_raw.upper()
            contexts = self.latest_market_context
            if not isinstance(contexts, dict):
                contexts = {}
                self.latest_market_context = contexts
            # MERGE vào dict đang có (giữ bid/ask/current_price từ bg_update_loop);
            # alias cả key raw + upper về CÙNG một dict object (bg loop dùng key as-is,
            # ui_bot_strategy lưu theo .upper()).
            merged = contexts.get(sym_raw) or contexts.get(sym_up) or {}
            merged.update(context or {})
            contexts[sym_raw] = merged
            if sym_up != sym_raw:
                contexts[sym_up] = merged
        except Exception:
            pass

    def _ctx_fetch_pending(self, symbol):
        sym_up = str(symbol or "").strip().upper()
        with self.ctx_fetch_lock:
            return sym_up in self.ctx_fetch_inflight or (
                time.time() - self.ctx_fetch_last.get(sym_up, 0) < 15
            )

    def _resolve_manual_setup_preview(
        self,
        symbol,
        direction,
        preset_name,
        context,
        manual_values=None,
        *,
        tick=None,
        sym_info=None,
        account=None,
    ):
        params = config.PRESETS.get(
            preset_name,
            next(iter(config.PRESETS.values()), {"SL_PERCENT": 0.5, "TP_RR_RATIO": 1.5, "RISK_PERCENT": 0.3}),
        )
        context = context or {}
        if tick is None and context and ("current_price" in context or "bid" in context):
            bid_val = float(context.get("bid", context.get("current_price", 0)))
            ask_val = float(context.get("ask", context.get("current_price", 0)))
            class MockTick:
                def __init__(self, b, a):
                    self.bid = b
                    self.ask = a
            tick = MockTick(bid_val, ask_val)
        if sym_info is None:
            try:
                sym_info = self.connector.get_symbol_info(symbol) if self.connector else None
            except Exception:
                sym_info = None
        if sym_info is None:
            class MockSymInfo:
                def __init__(self, is_derivative):
                    self.trade_contract_size = (
                        float(getattr(config, "DNSE_POINT_VALUE", 100000.0) or 100000.0)
                        if is_derivative
                        else float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0)
                    )
                    # CKCS (cơ sở) bước/min = lô chẵn 100; phái sinh theo LOT_STEP.
                    _stock_lot = float(getattr(config, "STOCK_ROUND_LOT", 100) or 100)
                    self.volume_min = 1.0 if is_derivative else _stock_lot
                    self.volume_max = config.MAX_LOT_SIZE if is_derivative else 1000000.0
                    self.volume_step = getattr(config, "LOT_STEP", 1.0) if is_derivative else _stock_lot
                    self.point = float(getattr(config, "DNSE_PRICE_POINT", 0.1) or 0.1)
            sym_info = MockSymInfo(self._is_derivative_symbol(symbol))

        if not tick or not sym_info:
            return {"ready": False, "reason": "NO_TICK_OR_SYMBOL_INFO"}

        manual_values = manual_values if isinstance(manual_values, dict) else None
        if manual_values is None:
            manual_entry = self._price_input_to_internal(
                self.var_manual_entry.get() if hasattr(self, "var_manual_entry") else 0.0,
                symbol,
            )
            manual_lot = self._safe_float(self.var_manual_lot.get() or 0.0)
            manual_sl = self._price_input_to_internal(self.var_manual_sl.get(), symbol)
            manual_tp = self._price_input_to_internal(self.var_manual_tp.get(), symbol)
        else:
            # Daemon dùng rule Preview đã lưu; không mượn ô nhập tay của mã
            # đang mở trên giao diện.
            manual_entry = self._safe_float(manual_values.get("entry", 0.0))
            manual_lot = self._safe_float(manual_values.get("lot", 0.0))
            manual_sl = self._safe_float(manual_values.get("sl", 0.0))
            manual_tp = self._safe_float(manual_values.get("tp", 0.0))
        price = manual_entry if manual_entry > 0 else float(tick.ask if direction == "BUY" else tick.bid)
        c_size = float(getattr(sym_info, "trade_contract_size", 1.0) or 1.0)
        vol_min = float(getattr(sym_info, "volume_min", getattr(config, "MIN_LOT_SIZE", 1.0)) or getattr(config, "MIN_LOT_SIZE", 1.0))
        vol_max = float(getattr(sym_info, "volume_max", getattr(config, "MAX_LOT_SIZE", 200.0)) or getattr(config, "MAX_LOT_SIZE", 200.0))
        vol_step = float(getattr(sym_info, "volume_step", getattr(config, "LOT_STEP", 0.01)) or 0.01)
        point = float(getattr(sym_info, "point", 0.00001) or 0.00001)

        market_mode = str(context.get("market_mode", "ANY") or "ANY").upper()

        sl_source = "PERCENT"
        if "MANUAL_SL_GROUP" not in params and "MANUAL_SWING_SL_GROUP" in params:
            params = dict(params)
            params["MANUAL_SL_GROUP"] = params.get("MANUAL_SWING_SL_GROUP")
        if "MANUAL_TP_GROUP" not in params and "MANUAL_SWING_TP_GROUP" in params:
            params = dict(params)
            params["MANUAL_TP_GROUP"] = params.get("MANUAL_SWING_TP_GROUP")
        sl_mode = self._manual_rule_mode(params, "MANUAL_SL_MODE", "USE_SWING_SL", "PERCENT")
        tp_mode = self._manual_rule_mode(params, "MANUAL_TP_MODE", "USE_SWING_TP", "RR")
        preset_sl_group = self._resolve_manual_preset_group(params, "MANUAL_SL_GROUP", context)
        sl_group = (
            self._sandbox_sl_group_for_symbol(symbol, context, preset_sl_group)
            if sl_mode == "SANDBOX"
            else preset_sl_group
        )
        tp_group = self._resolve_manual_preset_group(params, "MANUAL_TP_GROUP", context)
        # Khi SL/TP trong preset đang liên kết cùng một group, symbol override
        # (ví dụ VN30F: base_sl=G1/15m) phải dịch cả TP Retest/FIB theo cùng
        # khung hiệu lực. Chỉ giữ group TP riêng khi người dùng đã cấu hình
        # nó khác group SL.
        if tp_group == preset_sl_group and sl_group != preset_sl_group:
            tp_group = sl_group
        atr_key = f"atr_{sl_group}"
        swing_low_key = f"swing_low_{sl_group}"
        swing_high_key = f"swing_high_{sl_group}"
        atr_val = self._safe_float(context.get(atr_key, context.get("atr", 0.0)))
        swing_low = self._safe_float(context.get(swing_low_key, 0.0))
        swing_high = self._safe_float(context.get(swing_high_key, 0.0))
        if manual_sl > 0:
            sl_price = manual_sl
            sl_source = "MANUAL_SL"
        elif sl_mode == "SANDBOX":
            brain = self.trade_mgr._get_brain_settings(symbol)
            risk_tsl = brain.get("risk_tsl", {}) or {}
            sandbox_group = self._sandbox_sl_group_for_symbol(
                symbol,
                context,
                sl_group or getattr(config, "BOT_BASE_SL", "G2"),
            )
            sandbox_atr = self._safe_float(context.get(f"atr_{sandbox_group}", context.get("atr_entry", 0.0)))
            sandbox_low = self._safe_float(context.get(f"swing_low_{sandbox_group}", 0.0))
            sandbox_high = self._safe_float(context.get(f"swing_high_{sandbox_group}", 0.0))
            sandbox_mult = self._safe_float(risk_tsl.get("sl_atr_multiplier", getattr(config, "sl_atr_multiplier", 0.2)), 0.2)
            _sandbox_sl = None
            if sandbox_atr > 0 and sandbox_low > 0 and sandbox_high > 0:
                buffer = sandbox_atr * sandbox_mult
                _sandbox_sl = sandbox_low - buffer if direction == "BUY" else sandbox_high + buffer
                # [WRONG-SIDE GUARD] Swing sai phía so với giá -> bỏ, về Percent.
                if not (_sandbox_sl < price if direction == "BUY" else _sandbox_sl > price):
                    _sandbox_sl = None
            if _sandbox_sl is not None:
                sl_price = _sandbox_sl
                sl_source = f"SANDBOX:{sandbox_group}"
                sl_group = sandbox_group
                atr_val = sandbox_atr
            else:
                sl_dist = price * (float(params.get("SL_PERCENT", 0.5) or 0.5) / 100.0)
                sl_price = price - sl_dist if direction == "BUY" else price + sl_dist
                sl_source = "PERCENT"
        elif sl_mode == "SWING_REJECTION" and atr_val > 0 and swing_low > 0 and swing_high > 0:
            sl_mult = float(params.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2)) or 0.2)
            buffer = atr_val * sl_mult
            sl_price = swing_low - buffer if direction == "BUY" else swing_high + buffer
            sl_source = f"MANUAL_SWING_RETEST:{sl_group}"
        elif sl_mode == "SWING_STRUCTURE" and atr_val > 0:
            try:
                from core.market_structure import structure_from_context
                ms = structure_from_context(context, sl_group)
            except Exception:
                ms = {}
            sl_mult = float(params.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2)) or 0.2)
            buffer = atr_val * sl_mult
            if direction == "BUY" and self._safe_float(ms.get("hl")) > 0:
                sl_price = self._safe_float(ms.get("hl")) - buffer
                sl_source = f"MANUAL_SWING_STRUCTURE:{sl_group}"
            elif direction == "SELL" and self._safe_float(ms.get("lh")) > 0:
                sl_price = self._safe_float(ms.get("lh")) + buffer
                sl_source = f"MANUAL_SWING_STRUCTURE:{sl_group}"
            elif swing_low > 0 and swing_high > 0:
                sl_price = swing_low - buffer if direction == "BUY" else swing_high + buffer
                sl_source = f"MANUAL_SWING_RETEST:{sl_group}"
            else:
                sl_dist = price * (float(params.get("SL_PERCENT", 0.5) or 0.5) / 100.0)
                sl_price = price - sl_dist if direction == "BUY" else price + sl_dist
                sl_source = "PERCENT"
        elif sl_mode == "FIB" and atr_val > 0 and swing_low > 0 and swing_high > 0:
            tol = atr_val * float(params.get("MANUAL_FIB_SL_ATR_MULT", params.get("MANUAL_SWING_SL_ATR_MULT", 0.2)) or 0.2)
            sl_price = swing_low - tol if direction == "BUY" else swing_high + tol
            sl_source = f"MANUAL_FIB:{sl_group}"
        elif sl_mode == "PULLBACK":
            zone, pull_atr, pull_src = self._pullback_zone_from_context(direction, context, sl_group, params)
            if zone and pull_atr > 0:
                buffer = pull_atr * float(params.get("MANUAL_PULLBACK_SL_ATR_MULT", params.get("MANUAL_SWING_SL_ATR_MULT", 0.2)) or 0.2)
                sl_price = zone[0] - buffer if direction == "BUY" else zone[1] + buffer
                sl_source = f"MANUAL_{pull_src}:{sl_group}"
            else:
                sl_dist = price * (float(params.get("SL_PERCENT", 0.5) or 0.5) / 100.0)
                sl_price = price - sl_dist if direction == "BUY" else price + sl_dist
                sl_source = "PERCENT"
        else:
            sl_dist = price * (float(params.get("SL_PERCENT", 0.5) or 0.5) / 100.0)
            sl_price = price - sl_dist if direction == "BUY" else price + sl_dist

        sl_distance = abs(price - sl_price)
        if sl_distance <= 0:
            return {"ready": False, "reason": "INVALID_SL_DISTANCE"}

        tp_source = "RR"
        tp_targets = [None, None, None]
        tp_target_sources = [None, None, None]
        tp_target_multipliers = [None, None, None]

        def _rr_levels(rr_value):
            base = max(0.1, float(rr_value or 1.5))
            return [base, base + 0.25, base + 0.5]

        def _rr_ladder(rr_value):
            levels = _rr_levels(rr_value)
            return [
                price + sl_distance * level
                if direction == "BUY"
                else price - sl_distance * level
                for level in levels
            ]

        def _target_valid(value):
            value = self._safe_float(value, 0.0)
            if value <= 0:
                return False
            return value > price if direction == "BUY" else value < price

        def _target_after(value, previous):
            value = self._safe_float(value, 0.0)
            previous = self._safe_float(previous, 0.0)
            if not _target_valid(value) or previous <= 0:
                return False
            return value > previous if direction == "BUY" else value < previous

        def _fib_candidates():
            low = self._safe_float(context.get(f"swing_low_{tp_group}", 0.0))
            high = self._safe_float(context.get(f"swing_high_{tp_group}", 0.0))
            leg = abs(high - low)
            if low <= 0 or high <= 0 or leg <= 0:
                return []
            levels = self._parse_preview_levels(
                params.get("MANUAL_FIB_TP_LEVELS", "1.272,1.618,2.0")
            )
            return [
                low + leg * level if direction == "BUY" else high - leg * level
                for level in levels[:3]
            ]

        rr = float(params.get("TP_RR_RATIO", 1.5) or 1.5)
        rr_levels = _rr_levels(rr)
        rr_targets = _rr_ladder(rr)

        if manual_tp > 0:
            tp_price = manual_tp
            tp_source = "MANUAL_TP"
            tp_targets[0] = tp_price
            tp_target_sources[0] = "M"
        elif tp_mode == "OFF":
            tp_price = 0.0
            tp_source = "OFF"
        elif tp_mode in ("SWING_REJECTION", "SWING_STRUCTURE"):
            tp_atr_key = f"atr_{tp_group}"
            tp_low_key = f"swing_low_{tp_group}"
            tp_high_key = f"swing_high_{tp_group}"
            tp_atr = self._safe_float(context.get(tp_atr_key, 0.0))
            tp_low = self._safe_float(context.get(tp_low_key, 0.0))
            tp_high = self._safe_float(context.get(tp_high_key, 0.0))
            if tp_atr > 0 and tp_low > 0 and tp_high > 0:
                tp_mult = float(params.get("MANUAL_SWING_TP_ATR_MULT", params.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2))) or 0.2)
                buffer = tp_atr * tp_mult
                tp_price = tp_high - buffer if direction == "BUY" else tp_low + buffer
                # Swing chỉ cung cấp một mốc đích thật. Không kéo dài khoảng
                # cách đó thành TP2/TP3 giả.
                tp_targets = [tp_price, None, None]
                tp_target_sources = ["S", None, None]
                tp_source = f"MANUAL_{'SWING_STRUCTURE' if tp_mode == 'SWING_STRUCTURE' else 'SWING_RETEST'}:{tp_group}"
            else:
                tp_price = 0.0
                tp_source = f"MANUAL_SWING_MISSING:{tp_group}"
        elif tp_mode == "FIB":
            tp_atr_key = f"atr_{tp_group}"
            tp_low_key = f"swing_low_{tp_group}"
            tp_high_key = f"swing_high_{tp_group}"
            tp_low = self._safe_float(context.get(tp_low_key, 0.0))
            tp_high = self._safe_float(context.get(tp_high_key, 0.0))
            leg = abs(tp_high - tp_low)
            if tp_low > 0 and tp_high > 0 and leg > 0:
                levels = self._parse_preview_levels(params.get("MANUAL_FIB_TP_LEVELS", "1.272,1.618,2.0"))
                vals = [tp_low + leg * lvl if direction == "BUY" else tp_high - leg * lvl for lvl in levels[:3]]
                tp_targets = vals + [None] * max(0, 3 - len(vals))
                tp_target_sources = ["F" if value is not None else None for value in tp_targets]
                tp_price = tp_targets[0]
                tp_source = f"MANUAL_FIB:{tp_group}"
            else:
                tp_price = 0.0
                tp_source = f"MANUAL_FIB_MISSING:{tp_group}"
        elif tp_mode == "PULLBACK":
            zone, pull_atr, pull_src = self._pullback_zone_from_context(direction, context, tp_group, params)
            mult = float(params.get("MANUAL_PULLBACK_TP_ATR_MULT", 1.5) or 1.5)
            if pull_atr > 0:
                # Pullback cấu hình một ATR multiplier nên chỉ có một TP.
                tp_targets = [
                    price + pull_atr * mult
                    if direction == "BUY"
                    else price - pull_atr * mult,
                    None,
                    None,
                ]
                tp_target_sources = ["P", None, None]
                tp_price = tp_targets[0]
                tp_source = f"MANUAL_{pull_src}:{tp_group}"
            else:
                tp_price = 0.0
                tp_source = f"MANUAL_PULLBACK_MISSING:{tp_group}"
        else:
            tp_targets = list(rr_targets)
            tp_target_sources = ["R", "R", "R"]
            tp_target_multipliers = list(rr_levels)
            tp_price = tp_targets[0]
            tp_source = f"{rr:g}R"

        # FIB chỉ dùng mức FIB thật. Thiếu/sai dữ liệu thì Preview báo chưa sẵn
        # sàng; không tự đổi sang RR.
        if tp_mode == "FIB":
            fib_ladder_ok = len(tp_targets) >= 3 and all(
                value is not None for value in tp_targets[:3]
            )
            if fib_ladder_ok:
                for index, value in enumerate(tp_targets[:3]):
                    previous = tp_targets[index - 1] if index > 0 else price
                    if not (
                        _target_valid(value)
                        if index == 0
                        else _target_after(value, previous)
                    ):
                        fib_ladder_ok = False
                        break
            if not fib_ladder_ok:
                tp_targets = [None, None, None]
                tp_target_sources = [None, None, None]
                tp_target_multipliers = [None, None, None]
                tp_price = 0.0
                tp_source = f"MANUAL_FIB_MISSING:{tp_group}"

        # TP2/TP3 chỉ là mốc hiển thị. Với mode kỹ thuật, chỉ bổ sung FIB thật;
        # RR chỉ xuất hiện khi người dùng chủ động chọn mode RR.
        if tp_mode != "OFF":
            fib_values = [] if tp_mode == "RR" else _fib_candidates()
            used_fib = set()
            start_slot = 0 if tp_mode == "FIB" else 1
            for index in range(start_slot, 3):
                previous = tp_targets[index - 1] if index > 0 else price
                current = tp_targets[index]
                current_ok = (
                    _target_valid(current)
                    if index == 0
                    else _target_after(current, previous)
                )
                if current_ok:
                    if tp_target_sources[index] == "F":
                        used_fib.add(round(float(current), 10))
                    continue

                replacement = None
                for fib_value in fib_values:
                    key = round(float(fib_value), 10)
                    if key in used_fib:
                        continue
                    if (
                        _target_valid(fib_value)
                        if index == 0
                        else _target_after(fib_value, previous)
                    ):
                        replacement = float(fib_value)
                        tp_target_sources[index] = "F"
                        used_fib.add(key)
                        break

                if replacement is None and tp_mode == "RR":
                    for rr_index in range(index, len(rr_targets)):
                        rr_value = rr_targets[rr_index]
                        if (
                            _target_valid(rr_value)
                            if index == 0
                            else _target_after(rr_value, previous)
                        ):
                            replacement = float(rr_value)
                            tp_target_sources[index] = "R"
                            tp_target_multipliers[index] = rr_levels[rr_index]
                            break
                tp_targets[index] = replacement

            if tp_targets[0] is not None:
                tp_price = tp_targets[0]
                if tp_target_sources[0] == "R":
                    multiplier = tp_target_multipliers[0] or rr
                    tp_source = f"{multiplier:g}R"

        order_type = 0 if direction == "BUY" else 1
        if account is None:
            account = self.connector.get_account_info() if self.connector else None
        equity = float((account or {}).get("equity", (account or {}).get("balance", 0.0)) or 0.0)
        risk_pct = float(params.get("RISK_PERCENT", 0.3) or 0.3)
        brain = self.trade_mgr._get_brain_settings(symbol)
        margin_cfg = margin_rules.settings_from_brain(brain)
        margin_enabled = settlement.is_cash_stock(symbol) and bool(margin_cfg.get("ENABLE_MANUAL_MARGIN"))
        risk_base_amount = equity
        risk_base_label = "EQUITY_NAV"
        risk_base_warning = ""
        margin_snapshot = {}
        if margin_enabled:
            risk_base_amount, risk_base_label, risk_base_warning = margin_rules.resolve_risk_base(account, margin_cfg)
            margin_snapshot = margin_rules.account_snapshot(account, margin_cfg)
        strict_fee = 0.0
        spread_cost_per_lot = float(tick.ask - tick.bid) * c_size
        if params.get("STRICT_RISK", False):
            strict_fee = self.calculate_round_trip_trade_fee(
                symbol, price, sl_price, 1, direction
            ) + spread_cost_per_lot

        if manual_lot > 0:
            lot_size = self._normalize_contracts(manual_lot, vol_min, vol_max)
            lot_source = "MANUAL_LOT"
        else:
            risk_budget = risk_base_amount * (risk_pct / 100.0)
            lot_size = None
            try:
                lot_size, safe_sl = self.connector.calculate_lot_size(
                    symbol,
                    risk_budget,
                    sl_price,
                    order_type,
                    strict_fee,
                    entry_price=price,
                )
                if lot_size is not None:
                    sl_price = float(safe_sl)
                    sl_distance = abs(price - sl_price)
            except Exception:
                lot_size = None
            # Fallback chỉ dành cho connector giả/test. Cùng quy tắc với BOT:
            # làm tròn theo bước rồi ép volume_min nếu ngân sách > 0.
            if lot_size is None:
                loss_per_lot = sl_distance * c_size
                raw_lot = (
                    risk_budget / (loss_per_lot + strict_fee)
                    if loss_per_lot + strict_fee > 0
                    else 0.0
                )
                lot_size = (
                    round(raw_lot / vol_step) * vol_step
                    if raw_lot > 0 and vol_step > 0
                    else raw_lot
                )
                if raw_lot > 0:
                    lot_size = max(vol_min, min(float(lot_size), vol_max))
            lot_size = self._normalize_contracts(lot_size, vol_min, vol_max)
            lot_source = f"AUTO_RISK:{risk_pct:g}%"

        # DNSE có thể trả safe_sl đã chuẩn hóa theo bước giá/rule khối lượng.
        # Với TP kiểu R, luôn tính lại từ SL cuối cùng để Preview, BOT payload
        # và Telegram cùng dùng đúng một khoảng R.
        for index, source in enumerate(tp_target_sources):
            if source != "R":
                continue
            multiplier = tp_target_multipliers[index]
            if multiplier is None:
                multiplier = rr_levels[index]
                tp_target_multipliers[index] = multiplier
            tp_targets[index] = (
                price + sl_distance * multiplier
                if direction == "BUY"
                else price - sl_distance * multiplier
            )
        if tp_target_sources[0] == "R":
            tp_price = tp_targets[0]
            tp_source = f"{float(tp_target_multipliers[0] or rr):g}R"

        max_lot_cap = float((brain.get("symbol_configs", {}).get(symbol, {}) or {}).get("max_lot_cap", 0.0) or 0.0)
        if max_lot_cap <= 0:
            max_lot_cap = float(getattr(config, "MAX_LOT_CAP", 0.0) or 0.0)
        cap_note = ""
        if max_lot_cap > 0 and lot_size > max_lot_cap:
            lot_size = max_lot_cap
            cap_note = f" | CAP={max_lot_cap:g}"
        lot_size = self._normalize_contracts(lot_size, vol_min, vol_max) if lot_size > 0 else 0.0

        # [NAV CAP] CKCS không đòn bẩy: giá trị 1 lệnh ≤ % NAV (chống SL hẹp -> lot khổng lồ, dồn vốn 1 mã).
        ckcs_cap_lot = None
        if manual_lot <= 0 and settlement.is_cash_stock(symbol):
            _nav_pct = float((brain.get("bot_safeguard", {}) or {}).get(
                "STOCK_MAX_ORDER_NAV_PCT", getattr(config, "STOCK_MAX_ORDER_NAV_PCT", 20.0)) or 0.0)
            if _nav_pct > 0:
                _cap_value = equity * (_nav_pct / 100.0)
                # [CASH CAP] notional CKCS không vượt tiền mặt khả dụng (chừa ~1% phí).
                _cash = float((account or {}).get("cash_available", 0.0) or (account or {}).get("stock_cash", 0.0) or 0.0)
                _cap_src = "NAV_CAP"
                if _cash > 0 and _cash * 0.99 < _cap_value:
                    _cap_value = _cash * 0.99
                    _cap_src = "CASH_CAP"
                ckcs_cap_lot = stock_rules.max_shares_for_value(
                    _cap_value, price, c_size, getattr(config, "STOCK_ROUND_LOT", 100)
                )
                if lot_size > ckcs_cap_lot:
                    lot_size = float(ckcs_cap_lot)
                    lot_source = _cap_src

        # [FORCE MIN LOT] CKCS: lô tính < tối thiểu -> ép lên 1 lô chẵn nếu bật (và NAV đủ 1 lô).
        if lot_size <= 0 and manual_lot <= 0 and settlement.is_cash_stock(symbol):
            try:
                _sg = (self.trade_mgr._get_brain_settings(symbol) or {}).get("bot_safeguard", {})
            except Exception:
                _sg = {}
            _round = float(getattr(config, "STOCK_ROUND_LOT", 100) or 100)
            if _sg.get("FORCE_MIN_LOT", False) and (ckcs_cap_lot is None or ckcs_cap_lot >= _round):
                lot_size = _round
                lot_source = "FORCE_MIN_LOT"

        commission = self.calculate_trade_fee(symbol, price, lot_size, side=direction)
        spread_cost = spread_cost_per_lot * lot_size
        risk_usd = sl_distance * lot_size * c_size if lot_size > 0 else 0.0
        reward_usd = abs(tp_price - price) * lot_size * c_size if lot_size > 0 and tp_price > 0 else 0.0
        actual_risk_pct = (
            risk_usd / risk_base_amount * 100.0
            if risk_base_amount > 0
            else 0.0
        )
        rr_actual = reward_usd / risk_usd if risk_usd > 0 else 0.0
        valid_sl = (direction == "BUY" and sl_price < price) or (direction == "SELL" and sl_price > price)
        valid_tp = (
            tp_mode == "OFF"
            or (tp_price > 0 and direction == "BUY" and tp_price > price)
            or (tp_price > 0 and direction == "SELL" and tp_price < price)
        )

        return {
            "ready": bool(valid_sl and valid_tp and lot_size > 0),
            "reason": "OK" if valid_sl and valid_tp and lot_size > 0 else "INVALID_SL_TP_OR_LOT",
            "symbol": symbol,
            "direction": direction,
            "price": price,
            "entry_mode": f"LO @{price:g}" if manual_entry > 0 else "MARKET",
            "sl": sl_price,
            "tp": tp_price,
            "lot": lot_size,
            "lot_source": lot_source + cap_note,
            "sl_source": sl_source,
            "tp_source": tp_source,
            "sl_source_label": self._manual_source_label(sl_source),
            "tp_source_label": self._manual_source_label(tp_source),
            "manual_sl_mode": sl_mode,
            "manual_tp_mode": tp_mode,
            "risk_usd": risk_usd,
            "risk_pct": actual_risk_pct,
            "target_risk_pct": risk_pct,
            "risk_base": risk_base_label,
            "risk_base_amount": risk_base_amount,
            "risk_base_warning": risk_base_warning,
            "margin_enabled": margin_enabled,
            "margin_snapshot": margin_snapshot,
            "reward_usd": reward_usd,
            "rr": rr_actual,
            "equity": equity,
            "commission": commission,
            "spread_cost": spread_cost,
            "timeframe": self._symbol_group_timeframe(symbol, sl_group, context),
            "group": sl_group,
            "manual_sl_group": sl_group,
            "manual_tp_group": tp_group,
            "tp_targets": tp_targets[:3],
            "tp_target_sources": tp_target_sources[:3],
            "manual_sl_buffer": float(params.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2)) or 0.2),
            "manual_tp_buffer": float(params.get("MANUAL_SWING_TP_ATR_MULT", params.get("MANUAL_SWING_SL_ATR_MULT", getattr(config, "sl_atr_multiplier", 0.2))) or 0.2),
            "atr_key": atr_key,
            "swing_low_key": swing_low_key,
            "swing_high_key": swing_high_key,
            "atr": atr_val,
            "swing_low": swing_low,
            "swing_high": swing_high,
            "point": point,
        }

    def build_telegram_preview_order(self, symbol, side, context=None, market_mode="ANY"):
        """Rút gọn Manual Preview/Preset; không đọc E/E BOT và không đặt lệnh."""
        symbol = str(symbol or "").strip().upper()
        side = str(side or "").strip().upper()
        if not symbol or side not in {"BUY", "SELL"}:
            return {"ok": False, "error": "BAD_SIGNAL"}
        context = context if isinstance(context, dict) else {}
        if market_mode and not context.get("market_mode"):
            context = dict(context)
            context["market_mode"] = str(market_mode).upper()
        preset = getattr(config, "DEFAULT_PRESET", "SCALPING")
        try:
            setup = self._resolve_manual_setup_preview(
                symbol,
                side,
                preset,
                context,
                manual_values={"entry": 0.0, "lot": 0.0, "sl": 0.0, "tp": 0.0},
            )
        except Exception as exc:
            return {"ok": False, "error": f"PREVIEW_ERROR|{exc}"}
        if not setup.get("ready"):
            reason = str(setup.get("reason") or "PREVIEW_NOT_READY")
            if "INVALID_SL" in reason or "INVALID_TP" in reason:
                reason = f"INVALID_LEVELS|{reason}"
            return {
                "ok": False,
                "error": reason,
                "preview_source": True,
                "preview_version": 4,
            }

        try:
            brain = self.trade_mgr._get_brain_settings(symbol)
        except Exception:
            brain = {}
        is_stock = settlement.is_cash_stock(symbol)
        display_quantity = float(setup.get("lot", 0.0) or 0.0)
        if is_stock and side == "SELL":
            try:
                display_quantity = float(
                    self.trade_mgr._stock_settled_long_volume(symbol) or 0.0
                )
            except Exception:
                display_quantity = 0.0
        current_price = float(setup.get("price", 0.0) or 0.0)
        sl_price = float(setup.get("sl", 0.0) or 0.0)
        primary_tp = float(setup.get("tp", 0.0) or 0.0)
        raw_target_values = list(setup.get("tp_targets") or [])[:3]
        raw_target_sources = list(setup.get("tp_target_sources") or [])[:3]
        target_pairs = [
            (
                self._safe_float(value, 0.0),
                str(raw_target_sources[index] or "").upper()
                if index < len(raw_target_sources)
                else "",
            )
            for index, value in enumerate(raw_target_values)
            if value is not None
        ]
        targets = [value for value, _source in target_pairs]
        target_sources = [source for _value, source in target_pairs]
        valid_sl = (
            sl_price < current_price if side == "BUY" else sl_price > current_price
        )
        valid_target = (
            lambda value: value > current_price
            if side == "BUY"
            else 0 < value < current_price
        )
        if (
            current_price <= 0
            or sl_price <= 0
            or primary_tp <= 0
            or not valid_sl
            or not valid_target(primary_tp)
            or not targets
            or any(not valid_target(value) for value in targets)
        ):
            return {
                "ok": False,
                "error": "INVALID_LEVELS|DIRECTION_OR_NON_POSITIVE",
                "preview_source": True,
                "preview_version": 4,
            }
        ordered_targets = sorted(targets, reverse=side == "SELL")
        if targets != ordered_targets:
            return {
                "ok": False,
                "error": "INVALID_LEVELS|TP_ORDER",
                "preview_source": True,
                "preview_version": 4,
            }

        atr_value = self._safe_float(setup.get("atr", 0.0), 0.0)
        sl_distance_atr = (
            abs(current_price - sl_price) / atr_value if atr_value > 0 else 0.0
        )
        sl_distance_cfg = (
            ((brain or {}).get("entry_exit", {}) or {}).get("sl_distance", {}) or {}
        )
        max_sl_atr = self._safe_float(sl_distance_cfg.get("max_atr", 0.0), 0.0)
        if (
            max_sl_atr > 0
            and sl_distance_atr > max_sl_atr + 1e-9
        ):
            return {
                "ok": False,
                "error": (
                    f"INVALID_LEVELS|SL_TOO_WIDE|"
                    f"{sl_distance_atr:.2f}ATR>{max_sl_atr:g}ATR"
                ),
                "preview_source": True,
                "preview_version": 4,
            }

        return {
            "ok": True,
            "preview_source": True,
            "preview_version": 4,
            "symbol": symbol,
            "side": side,
            "price": current_price,
            "current_price": current_price,
            "entry_price": current_price,
            "entry_zone": None,
            "entry_status": "MANUAL",
            "entry_tactic": "PRESET",
            "lot": float(setup.get("lot", 0.0) or 0.0),
            "display_quantity": display_quantity,
            "quantity_unit": "CP" if is_stock else "HĐ",
            "analysis_only": bool(is_stock and side == "SELL"),
            "sl": sl_price,
            "tp": primary_tp,
            "tp_targets": targets,
            "tp_target_sources": target_sources,
            "sl_source": str(setup.get("sl_source") or ""),
            "tp_source": str(setup.get("tp_source") or ""),
            "risk_amount": float(setup.get("risk_usd", 0.0) or 0.0),
            "reward_amount": float(setup.get("reward_usd", 0.0) or 0.0),
            "risk_pct": float(setup.get("risk_pct", 0.0) or 0.0),
            "tactic": str(
                ((brain or {}).get("risk_tsl", {}) or {}).get("bot_tsl")
                or getattr(config, "BOT_DEFAULT_TSL", "OFF")
                or "OFF"
            ),
            "preset": preset,
            "market_mode": str(context.get("market_mode") or market_mode or "ANY"),
            "sl_distance_atr": sl_distance_atr,
            "max_sl_atr": max_sl_atr,
        }

    def _make_preview_model(self, key, title, status, reason, setup, source, can_apply=False):
        direction = (setup or {}).get("direction", "")
        visible_targets = self._preview_target_values(setup)
        tp_targets = visible_targets[:3] + [None] * max(0, 3 - len(visible_targets))
        tp_target_sources = list((setup or {}).get("tp_target_sources") or [])[:3]
        tp_target_sources += [None] * max(0, 3 - len(tp_target_sources))
        show_tp_ladder = len(visible_targets) > 1
        return {
            "key": key,
            "title": title,
            "symbol": (setup or {}).get("symbol", getattr(self, "cbo_symbol", None).get() if hasattr(self, "cbo_symbol") else "VN30F1M"),
            "timeframe": (setup or {}).get("timeframe", "--"),
            "source": source,
            "direction": direction,
            "bias": "LONG" if direction == "BUY" else "SHORT" if direction == "SELL" else "WAIT",
            "status": status,
            "reason": reason,
            "entry_signal": (setup or {}).get("entry_signal", "--"),
            "entry_price": self._fmt_price((setup or {}).get("price")),
            "entry": self._fmt_price((setup or {}).get("price")),
            "ee_gate": (setup or {}).get("ee_gate", "--"),
            "entry_zone": (setup or {}).get("entry_zone", "--"),
            "sl_rule": (setup or {}).get("sl_rule", "--"),
            "exit_rule": (setup or {}).get("exit_rule", "--"),
            "tsl_rule": (setup or {}).get("tsl_rule", "--"),
            "sl": (setup or {}).get("sl", 0.0),
            "tp_main": (setup or {}).get("tp", 0.0),
            "tp1": tp_targets[0],
            "tp2": tp_targets[1],
            "tp3": tp_targets[2],
            "tp_target_sources": tp_target_sources,
            "show_tp_ladder": show_tp_ladder,
            "tp_source": (setup or {}).get("tp_source", "--"),
            "sl_source_label": (setup or {}).get("sl_source_label", "--"),
            "tp_source_label": (setup or {}).get("tp_source_label", "--"),
            "ee_status": (setup or {}).get("ee_status", "--"),
            "chips": (setup or {}).get("chips", []),
            "can_apply": bool(can_apply),
            "apply_direction": direction if can_apply else "",
            "apply_sl": (setup or {}).get("sl", 0.0),
            "apply_tp": (setup or {}).get("tp", 0.0),
            "setup": setup or {},
        }

    def _explain_pullback_data(self, context, ee_cfg):
        active = ee_cfg.get("active_tactics") or ee_cfg.get("entry_tactics") or []
        if "PULLBACK_ZONE" not in active:
            return ""
        group = self._resolve_preview_group_name(ee_cfg.get("sl_source_group", "G2"), context)
        pull = ee_cfg.get("pullback_zone", {}) or {}
        source = str(pull.get("source", "EMA20") or "EMA20").upper()
        missing = []
        if not self._safe_float((context or {}).get(f"atr_{group}", (context or {}).get("atr_entry", 0.0))):
            missing.append(f"atr_{group}")
        if source == "BB_MID":
            if not self._safe_float((context or {}).get(f"bb_mid_{group}", (context or {}).get("bb_mid", 0.0))):
                missing.append(f"bb_mid_{group}")
        elif source == "SWING":
            if not self._safe_float((context or {}).get(f"swing_low_{group}", 0.0)):
                missing.append(f"swing_low_{group}")
            if not self._safe_float((context or {}).get(f"swing_high_{group}", 0.0)):
                missing.append(f"swing_high_{group}")
        else:
            ema = (
                (context or {}).get(f"ema20_{group}")
                or (context or {}).get("ema20")
                or (context or {}).get(f"EMA_20_{group}")
            )
            if not self._safe_float(ema):
                missing.append(f"ema20_{group}")
        return f" | Pullback thiếu data: {', '.join(missing)}" if missing else f" | Pullback source={source} group={group}"

    def _build_tsl_preview_lines(self, setup=None, context=None):
        tactic = self.get_current_tactic_string()
        if tactic == "OFF":
            return "TSL: OFF", "Không có rule trailing đang bật."
        setup = setup or {}
        context = context or {}
        modes = [m for m in tactic.split("+") if m]
        t_cfg = getattr(config, "TSL_CONFIG", {}) or {}
        group = str(setup.get("group") or "G2")
        detail = []
        price = self._safe_float(setup.get("price", 0.0))
        sl = self._safe_float(setup.get("sl", 0.0))
        one_r = abs(price - sl) if price and sl else 0.0
        direction = str(setup.get("direction", self.var_direction.get()) or "BUY").upper()
        is_buy = direction == "BUY"

        if "BE_CASH" in modes:
            cash_type = str(t_cfg.get("BE_CASH_TYPE", "USD") or "USD").upper()
            cash_value = t_cfg.get("BE_VALUE", 0.0)
            cash_strat = t_cfg.get("BE_CASH_STRAT", "TRAILING (Gap)")
            buffer_type = t_cfg.get("BE_CASH_SOFT_BUFFER_TYPE", cash_type)
            buffer_val = t_cfg.get("BE_CASH_SOFT_BUFFER", 0.0)
            min_lock = t_cfg.get("BE_CASH_MIN_LOCK", 0.0)
            detail.append(
                f"CASH: {cash_type} {cash_value} | {cash_strat} | buffer {buffer_val} {buffer_type} | min lock {min_lock}"
            )
        if "BE" in modes and one_r > 0:
            rr = float(t_cfg.get("BE_OFFSET_RR", 0.8) or 0.8)
            trig = price + one_r * rr if is_buy else price - one_r * rr
            detail.append(f"BE: {rr:g}R trigger {trig:.2f}")
        elif "BE" in modes:
            detail.append("BE: chờ entry/SL hợp lệ")
        if "STEP_R" in modes and one_r > 0:
            sz = float(t_cfg.get("STEP_R_SIZE", 1.0) or 1.0)
            trig = price + one_r * sz if is_buy else price - one_r * sz
            detail.append(f"STEP_R: step {sz:g}R trigger {trig:.2f}")
        elif "STEP_R" in modes:
            detail.append("STEP_R: chờ entry/SL hợp lệ")
        if "PNL" in modes:
            levels = t_cfg.get("PNL_LEVELS") or []
            detail.append(f"PNL: level đầu {levels[0][0]}%" if levels else "PNL: chưa có level")
        if "SWING" in modes:
            atr = context.get(f"atr_{group}")
            low = context.get(f"swing_low_{group}")
            high = context.get(f"swing_high_{group}")
            if atr and low and high:
                detail.append(f"SWING: {group} L={self._fmt_price(low)} H={self._fmt_price(high)} ATR={self._fmt_price(atr)}")
            else:
                detail.append(f"SWING: thiếu swing/ATR {group}")
        if "PSAR_TRAIL" in modes:
            psar_group = str(t_cfg.get("PSAR_GROUP", group) or group)
            psar = context.get(f"psar_{psar_group}") or context.get(f"PSAR_{psar_group}") or context.get("psar")
            detail.append(f"PSAR: {psar_group}={self._fmt_price(psar)}" if psar else f"PSAR: thiếu psar_{psar_group}")
        if "ANTI_CASH" in modes:
            detail.append("ANTI: theo ngưỡng lỗ tiền mặt")
        if any(m in modes for m in ("AUTO_DCA", "AUTO_PCA", "REV_C")):
            defs = [m for m in ("AUTO_DCA", "AUTO_PCA", "REV_C") if m in modes]
            detail.append(f"DEF: {'+'.join(defs)} xử lý theo điều kiện vị thế")

        line1 = f"TSL: {' + '.join(modes)}"
        winner = next((d for d in detail if "thi" not in d.lower() and "missing" not in d.lower()), detail[0] if detail else "")
        line2 = f"WIN {winner}" if winner else "Waiting rule data"
        return line1, line2

    def _parse_preview_levels(self, raw, default=None):
        default = default or []
        try:
            vals = [float(x.strip()) for x in str(raw or "").split(",") if x.strip()]
            return vals or default
        except Exception:
            return default

    def _resolve_preview_group_name(self, group, context):
        group = str(group or "G2")
        if "DYNAMIC" in group:
            mode = str((context or {}).get("market_mode", "ANY") or "ANY").upper()
            return "G1" if mode in ("TREND", "BREAKOUT") else "G2"
        if group == "BASE_SL":
            return "G2"
        return group

    def _resolve_ee_tp_ladder(self, setup, context, ee_cfg, ee_decision):
        setup = setup or {}
        context = context or {}
        ee_cfg = ee_cfg or {}
        direction = str(setup.get("direction", "BUY") or "BUY").upper()
        price = self._safe_float(setup.get("price", 0.0))
        tp_source = str((ee_decision or {}).get("tp_source") or setup.get("tp_source") or "--").upper()
        exit_tactic = str((ee_decision or {}).get("exit_tactic") or "").upper()
        if (ee_decision or {}).get("tp_disabled") or tp_source == "OFF" or exit_tactic in ("NO_TP", "OFF"):
            return [None, None, None], "OFF"

        targets = []
        if exit_tactic == "FIB_RETRACE" or tp_source == "FIB":
            fib = ee_cfg.get("fib_retrace", {}) or {}
            group = self._resolve_preview_group_name(fib.get("swing_source_group", "G2"), context)
            sh = self._safe_float(context.get(f"swing_high_{group}", 0.0))
            sl = self._safe_float(context.get(f"swing_low_{group}", 0.0))
            leg = abs(sh - sl)
            if sh > 0 and sl > 0 and leg > 0:
                for level in self._parse_preview_levels(fib.get("tp_levels", "1.272,1.618"))[:3]:
                    targets.append(sl + leg * level if direction == "BUY" else sh - leg * level)
            source = f"FIB {fib.get('tp_levels', '1.272,1.618')}"
            if len(targets) < 3:
                source = f"{source} ({len(targets)} lv)"
        elif exit_tactic == "PULLBACK_ZONE" or tp_source == "PULLBACK":
            tp = self._safe_float((ee_decision or {}).get("tp", 0.0))
            if tp > 0:
                targets.append(tp)
            source = "PULL"
        elif exit_tactic in ("SWING_REJECTION", "SWING_STRUCTURE") or tp_source == "SWING":
            tp = self._safe_float((ee_decision or {}).get("tp", 0.0))
            if tp > 0:
                targets.append(tp)
            source = "SWING"
        elif exit_tactic in ("FALLBACK_R", "R", "AUTO") or tp_source in ("R", "--"):
            rr = self._safe_float((ee_cfg.get("default_exit", {}) or {}).get("tp_rr_ratio", 1.5), 1.5)
            tp = self._safe_float((ee_decision or {}).get("tp", 0.0))
            if tp > 0:
                targets.append(tp)
            source = f"{rr:g}R"
        else:
            tp = self._safe_float((ee_decision or {}).get("tp", 0.0))
            if tp > 0:
                targets.append(tp)
            source = tp_source

        while len(targets) < 3:
            targets.append(None)
        return targets[:3], source

    def _chip_color(self, kind, text):
        raw = str(text or "").upper()
        if kind == "danger" or "ERROR" in raw or "INVALID" in raw:
            return "#4A1116", "#FF5252", "#FFB3AD"
        if kind == "warn" or "WAIT" in raw or "MISSING" in raw or "THIẾU" in raw or "FALLBACK" in raw:
            return "#40350D", "#FFD600", "#FFF0A3"
        if kind == "good" or "READY" in raw or "OK" in raw:
            return "#07351D", "#00E676", "#9AFFC4"
        if kind == "tp":
            return "#062F27", "#00E676", "#9AFFD2"
        if kind == "sl":
            return "#3A1117", "#FF5252", "#FFB3AD"
        return "#132326", "#37565C", "#D9EEF2"

    def _make_preview_chip(self, label, value, kind="info"):
        fg, border, text_color = self._chip_color(kind, value)
        return {"label": label, "value": value, "fg": fg, "border": border, "text_color": text_color}

    def _preview_target_values(self, setup):
        targets = []
        for val in list((setup or {}).get("tp_targets") or [])[:3]:
            num = self._safe_float(val, 0.0)
            if num > 0:
                targets.append(num)
        return targets

    def _preview_rule_notes(self, setup):
        setup = setup or {}
        notes = []
        sl_mode = str(setup.get("manual_sl_mode", "") or "").upper()
        tp_mode = str(setup.get("manual_tp_mode", "") or "").upper()
        sl_source = str(setup.get("sl_source", "") or "").upper()
        tp_source = str(setup.get("tp_source", "") or "").upper()
        if sl_mode not in ("", "PERCENT") and sl_source == "PERCENT":
            _detail = (
                "Đang tải data…"
                if self._ctx_fetch_pending(str(setup.get("symbol", "") or ""))
                else "Missing rule data, using Percent"
            )
            notes.append(self._make_preview_chip("SL Fallback", _detail, "warn"))
        if tp_mode not in ("", "RR") and (tp_source == "RR" or tp_source.endswith("R")):
            notes.append(self._make_preview_chip("Exit Fallback", "Missing target data, using RR", "warn"))
        return notes

    def build_manual_preview_models(self):
        symbol = self.cbo_symbol.get()
        context = getattr(self, "latest_market_context", {}).get(symbol, {}) or {}
        direction = self.var_direction.get()
        preset = getattr(config, "DEFAULT_PRESET", "SCALPING")
        group = self._preview_tf_group(symbol, context)
        latest_signal = int(context.get("latest_signal", 0) or 0)
        trend = str(context.get(f"trend_{group}", context.get("trend", "NONE")) or "NONE").upper()
        market_mode = str(context.get("market_mode", "ANY") or "ANY").upper()
        mode = self.var_manual_trade_mode.get() if hasattr(self, "var_manual_trade_mode") else "NORMAL"

        setup = self._resolve_manual_setup_preview(symbol, direction, preset, context)
        setup["preview_group"] = group
        setup["timeframe"] = self._symbol_group_timeframe(symbol, group, context)
        status = "READY" if setup.get("ready") and mode == "NORMAL" else "BLOCK" if mode != "NORMAL" else "WAIT"
        mode_note = "NORMAL manual only" if mode != "NORMAL" else setup.get("reason", "OK")
        tsl_line1, tsl_line2 = self._build_tsl_preview_lines(setup, context)
        tp_targets = list(setup.get("tp_targets") or [setup.get("tp"), None, None])
        tp_targets = tp_targets[:3] + [None] * max(0, 3 - len(tp_targets))
        tp_ladder_source = setup.get("tp_source_label", setup.get("tp_source", "--"))
        setup["tp_targets"] = tp_targets
        setup["tp_ladder_source"] = tp_ladder_source
        setup["ee_status"] = "MANUAL"
        setup["ee_gate"] = f"Preset {preset}"
        setup["entry_zone"] = "Giá nhập tay / giá live"
        setup["ee_reason"] = "Manual Preview không dùng cổng E/E của BOT"
        signal_text = "NONE" if latest_signal == 0 else "BUY" if latest_signal > 0 else "SELL"
        trend_bias = "BUY" if trend == "UP" else "SELL" if trend == "DOWN" else "NONE"
        if trend_bias == "NONE":
            trend_kind = "warn"
        elif trend_bias == direction:
            trend_kind = "good"
        else:
            trend_kind = "danger"
        tsl_kind = "warn" if "thiếu" in tsl_line2.lower() or "missing" in tsl_line2.lower() else "good"
        manual_sl_group = setup.get("manual_sl_group", setup.get("group", "--"))
        manual_tp_group = setup.get("manual_tp_group", "--")
        manual_sl_label = self._group_tf_label(manual_sl_group, symbol, context)
        manual_tp_label = self._group_tf_label(manual_tp_group, symbol, context)
        setup["trend"] = trend
        setup["market_mode"] = market_mode
        setup["manual_mode"] = mode
        setup["entry_signal"] = f"{signal_text} | Trend {self._group_tf_label(group, symbol, context)} {trend}"
        setup["sl_rule"] = f"{self._fmt_price(setup.get('sl'))} | {setup.get('sl_source_label', '--')} {manual_sl_label}"
        setup["exit_rule"] = (
            "OFF"
            if setup.get("tp_source") == "OFF"
            else f"{self._fmt_price(setup.get('tp'))} | {setup.get('tp_source_label', '--')} {manual_tp_label}"
        )
        setup["tsl_rule"] = f"{tsl_line1.replace('TSL: ', '')} | {tsl_line2}"
        setup["chips"] = [
            self._make_preview_chip("Trend", f"Signal {signal_text} | {self._group_tf_label(group, symbol, context)} {trend} | {market_mode}", trend_kind),
            self._make_preview_chip("ATR", f"SL {manual_sl_label}={self._fmt_price(setup.get('atr'))} | TP {manual_tp_label}", "info"),
            self._make_preview_chip("TSL", tsl_line1.replace("TSL: ", ""), tsl_kind),
            self._make_preview_chip("Preset", f"{preset} · không dùng E/E BOT", "info"),
        ]
        if setup.get("margin_enabled"):
            snap = setup.get("margin_snapshot", {}) or {}
            rtt = snap.get("rtt")
            rtt_txt = "UNKNOWN" if rtt is None else f"{float(rtt):.1f}%"
            margin_txt = f"{setup.get('risk_base', 'EQUITY_NAV')} | RTT {rtt_txt}"
            margin_kind = "warn" if rtt is None else "good"
            setup["chips"] = [self._make_preview_chip("Margin", margin_txt, margin_kind)] + setup["chips"][:3]
        fallback_notes = self._preview_rule_notes(setup)
        if fallback_notes:
            setup["chips"] = (fallback_notes + setup["chips"])[:4]
        primary_reason = ""
        primary = self._make_preview_model(
            "primary",
            "MANUAL PREVIEW",
            status,
            primary_reason,
            setup,
            f"NORMAL | {market_mode}",
            can_apply=status == "READY",
        )

        self.manual_preview_models = {
            "primary": primary,
        }
        return self.manual_preview_models

    def refresh_manual_preview_tab(self):
        if not hasattr(self, "preview_cards"):
            return
        try:
            preset_cfg = config.PRESETS.get(getattr(config, "DEFAULT_PRESET", "SCALPING"), {})
            symbol = self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else ""
            context = getattr(self, "latest_market_context", {}).get(symbol, {}) or {}
            tf_display = {
                "G0": self._group_tf_label("G0", symbol, context),
                "G1": self._group_tf_label("G1", symbol, context),
                "G2": self._group_tf_label("G2", symbol, context),
                "G3": self._group_tf_label("G3", symbol, context),
                "DYNAMIC": "DYNAMIC",
            }
            if hasattr(self, "cbo_preview_sl_group"):
                self.cbo_preview_sl_group.configure(
                    values=[tf_display[g] for g in ("G0", "G1", "G2", "G3", "DYNAMIC")]
                )
            if hasattr(self, "var_preview_sl_group"):
                sl_mode = str(preset_cfg.get("MANUAL_SL_MODE", "PERCENT") or "PERCENT").upper()
                preset_sl_group = str(
                    preset_cfg.get(
                        "MANUAL_SL_GROUP",
                        preset_cfg.get("MANUAL_SWING_SL_GROUP", "G2"),
                    )
                    or "G2"
                )
                sl_group = (
                    self._sandbox_sl_group_for_symbol(symbol, context, preset_sl_group)
                    if sl_mode == "SANDBOX"
                    else preset_sl_group
                )
                sl_group = "DYNAMIC" if "DYNAMIC" in sl_group else sl_group
                self.var_preview_sl_group.set(tf_display.get(sl_group, tf_display["G2"]))
                if getattr(self, "var_preview_tp_group", None) is getattr(self, "var_preview_sl_group", None):
                    preset_cfg["MANUAL_TP_GROUP"] = sl_group
                    preset_cfg["MANUAL_SWING_TP_GROUP"] = sl_group
            if hasattr(self, "var_preview_tp_group"):
                tp_group = str(preset_cfg.get("MANUAL_TP_GROUP", preset_cfg.get("MANUAL_SWING_TP_GROUP", preset_cfg.get("MANUAL_SL_GROUP", "G2"))) or "G2")
                tp_group = "DYNAMIC" if "DYNAMIC" in tp_group else tp_group
                if getattr(self, "var_preview_tp_group", None) is not getattr(self, "var_preview_sl_group", None):
                    self.var_preview_tp_group.set(tf_display.get(tp_group, tf_display["G2"]))
            if hasattr(self, "var_preview_sl_mode"):
                sl_mode = str(preset_cfg.get("MANUAL_SL_MODE", "PERCENT") or "PERCENT").upper()
                self.var_preview_sl_mode.set(self._manual_mode_display(sl_mode, "SL"))
            if hasattr(self, "var_preview_tp_mode"):
                tp_mode = str(preset_cfg.get("MANUAL_TP_MODE", "RR") or "RR").upper()
                self.var_preview_tp_mode.set(self._manual_mode_display(tp_mode, "TP"))
            models = self.build_manual_preview_models()
            for key, widgets in self.preview_cards.items():
                model = models.get(key, {})
                status = model.get("status", "WAIT")
                direction = model.get("apply_direction") or model.get("direction")
                color = self._preview_color_for_status(status, direction)
                bias = model.get("bias", "WAIT")
                widgets["frame"].configure(border_color=color)
                widgets["title"].configure(text=model.get("title", key.upper()), text_color=color)
                widgets["badge"].configure(text=f"{bias} | {status}", text_color=color)
                setup = model.get("setup", {}) or {}
                atr_group = self._group_tf_label(
                    setup.get("manual_sl_group", setup.get("group", "--")),
                    symbol,
                    context,
                )
                atr_txt = self._fmt_price(setup.get("atr"))
                trend_raw = str(setup.get("trend", "") or "").upper()
                meta_color = "#FF5252" if trend_raw == "DOWN" else "#00E676" if trend_raw == "UP" else "#FFD600" if trend_raw in ("NONE", "FLAT", "SIDEWAY") else "#B2EBF2"
                widgets["meta"].configure(
                    text=f"{model.get('symbol', '--')} | {model.get('timeframe', '--')} | ATR {atr_group}={atr_txt} | {setup.get('market_mode', '--')} | {trend_raw or '--'}",
                    text_color=meta_color,
                )
                def _set_preview_text(widget, text):
                    if isinstance(widget, (tuple, list)) and len(widget) >= 2:
                        widget[1].configure(text=text)
                    elif widget is not None:
                        widget.configure(text=text)

                levels = widgets.get("levels", {})
                if isinstance(levels, dict):
                    if "ee_detail" in levels:
                        gate = str(model.get("ee_gate", "") or "")
                        zone = str(model.get("entry_zone", "--") or "--")
                        zone_txt = zone if zone != "--" else "Market"
                        entry_txt = f"{model.get('entry_signal', '--')} | Price {model.get('entry_price', '--')} | Zone {zone_txt}"
                        _set_preview_text(levels["entry_signal"], entry_txt)
                        if hasattr(levels["entry_signal"], "configure"):
                            levels["entry_signal"].configure(text_color=meta_color)
                        tp_parts = []
                        tp_sources = list(model.get("tp_target_sources") or [])
                        for idx, target_key in enumerate(("tp1", "tp2", "tp3"), start=1):
                            val = model.get(target_key)
                            source = (
                                str(tp_sources[idx - 1] or "").upper()
                                if idx - 1 < len(tp_sources)
                                else ""
                            )
                            source_text = (
                                f" ({source})"
                                if val and source in {"F", "R"}
                                else ""
                            )
                            tp_parts.append(
                                f"TP{idx} {self._fmt_price(val) if val else '--'}{source_text}"
                            )
                        if model.get("tp_source") == "OFF":
                            tp_parts = ["TP OFF"]
                        _set_preview_text(levels["sl"], f"SL {self._fmt_price(model.get('sl'))}")
                        _set_preview_text(levels["tp_main"], " | ".join(tp_parts))
                        if "stats" in levels:
                            lot = float(setup.get("lot", 0.0) or 0.0)
                            risk_usd = float(setup.get("risk_usd", 0.0) or 0.0)
                            reward_usd = float(setup.get("reward_usd", 0.0) or 0.0)
                            equity = float(setup.get("equity", 0.0) or 0.0)
                            pnl_pct = (reward_usd / equity * 100.0) if equity > 0 else 0.0
                            pnl_pct_txt = f"{pnl_pct:.4f}%" if 0 < abs(pnl_pct) < 0.01 else f"{pnl_pct:.2f}%"
                            risk_pct = float(setup.get("risk_pct", 0.0) or 0.0)
                            _set_preview_text(levels["stats"], f"{self._quantity_unit(model.get('symbol'))} {lot:.0f} | Rủi ro {self._fmt_money(risk_usd)} ({risk_pct:g}%) | Kỳ vọng TP1 {self._fmt_money(reward_usd)} | PnL {pnl_pct_txt}")
                        tsl_txt = str(model.get("tsl_rule", "--") or "--").replace(" trigger ", " @").replace("WIN ", "")
                        _set_preview_text(levels["tsl"], tsl_txt)
                        ee_reason = str(model.get("setup", {}).get("ee_reason", "") or "")
                        ee_txt = gate or "Entry filter OFF"
                        if "MISSING E/E DATA" in ee_reason.upper() and "FALLBACK R" in ee_reason.upper():
                            ee_reason = ""
                        if ee_reason and ee_reason not in ee_txt:
                            ee_txt = f"{ee_txt} | {ee_reason}"
                        ee_txt = ee_txt.replace("Giá đã vào vùng ", "In ")
                        _set_preview_text(levels["ee_detail"], ee_txt)
                    else:
                        if "entry_signal" in levels:
                            _set_preview_text(levels["entry_signal"], model.get("entry_signal", "--"))
                        if "entry" in levels:
                            _set_preview_text(levels["entry"], model.get("entry_price", model.get("entry", "--")))
                        if "entry_zone" in levels:
                            gate = str(model.get("ee_gate", "") or "")
                            zone = str(model.get("entry_zone", "--") or "--")
                            zone_txt = zone if zone != "--" else gate or "Entry filter OFF"
                            _set_preview_text(levels["entry_zone"], zone_txt[:80])
                        if "sl" in levels:
                            _set_preview_text(levels["sl"], model.get("sl_rule", "--"))
                        if "tp_main" in levels:
                            _set_preview_text(levels["tp_main"], model.get("exit_rule", "--"))
                        if "tsl" in levels:
                            _set_preview_text(levels["tsl"], model.get("tsl_rule", "--"))
                        if "rr" in levels:
                            _set_preview_text(levels["rr"], f"{model.get('setup', {}).get('rr', 0.0):.2f}")
                        if "lot" in levels:
                            _set_preview_text(levels["lot"], f"{model.get('setup', {}).get('lot', 0.0):.0f}")
                        if "risk" in levels:
                            _set_preview_text(levels["risk"], self._fmt_money(model.get('setup', {}).get('risk_usd', 0.0)))
                        if "reward" in levels:
                            _set_preview_text(levels["reward"], self._fmt_money(model.get('setup', {}).get('reward_usd', 0.0)))
                targets = widgets.get("targets", {})
                if isinstance(targets, dict):
                    for target_key in ("tp1", "tp2", "tp3"):
                        if target_key in targets and targets[target_key] is not None:
                            val = model.get(target_key)
                            target_widget = targets[target_key]
                            show_target = bool(model.get("show_tp_ladder") and val)
                            if isinstance(target_widget, (tuple, list)) and target_widget:
                                if show_target:
                                    target_widget[0].grid()
                                    target_widget[1].configure(text=self._fmt_price(val))
                                else:
                                    target_widget[0].grid_remove()
                            elif show_target:
                                target_widget.configure(text=self._fmt_price(val))
                            else:
                                target_widget.configure(text="")
                    if "rr" in targets and targets["rr"] is not None:
                        _set_preview_text(targets["rr"], f"{model.get('setup', {}).get('rr', 0.0):.2f}")
                chip_widgets = widgets.get("chip_widgets", [])
                if chip_widgets:
                    model_chips = list(model.get("chips", []))
                    for idx, (chip_box, chip_label) in enumerate(chip_widgets):
                        chip = model_chips[idx] if idx < len(model_chips) else {}
                        text = f"{chip.get('label', '')}: {chip.get('value', '')}" if chip else "--"
                        chip_box.configure(
                            fg_color=chip.get("fg", "#102326"),
                            border_color=chip.get("border", "#263238"),
                        )
                        chip_label.configure(
                            text=text,
                            text_color=chip.get("text_color", "#607D8B"),
                            font=("Roboto", 12 if idx == 6 else 11, "bold"),
                            wraplength=680 if idx in (1, 6, 7, 8, 9) else 440,
                        )
                if model.get("reason"):
                    widgets["reason"].grid()
                    widgets["reason"].configure(text=model.get("reason", "---"))
                else:
                    widgets["reason"].grid_remove()
                apply_text = "APPLY"
                if direction == "BUY":
                    apply_text = "APPLY LONG"
                elif direction == "SELL":
                    apply_text = "APPLY SHORT"
                widgets["apply"].configure(
                    text=apply_text,
                    state="normal" if model.get("can_apply") else "disabled",
                    fg_color=color if model.get("can_apply") else "#37474F",
                    hover_color=color if model.get("can_apply") else "#37474F",
                )
        except Exception as exc:
            self.log_message(f"[PREVIEW] Error: {exc}", error=True, target="manual")

    def apply_manual_preview_setup(self, key):
        model = getattr(self, "manual_preview_models", {}).get(key)
        if not model:
            model = self.build_manual_preview_models().get(key)
        if not model or not model.get("can_apply"):
            return
        if self.var_manual_trade_mode.get() != "NORMAL":
            self.log_message("[PREVIEW] Direct apply is only available in NORMAL manual mode.", error=True, target="manual")
            return
        direction = model.get("apply_direction")
        if direction in ("BUY", "SELL"):
            self.on_direction_change(direction)
        if model.get("apply_tp", 0) > 0:
            self.var_manual_tp.set(
                self._price_internal_to_input(model["apply_tp"], model.get("symbol"))
            )
        if model.get("apply_sl", 0) > 0:
            self.var_manual_sl.set(
                self._price_internal_to_input(model["apply_sl"], model.get("symbol"))
            )
        self.log_message(
            f"[PREVIEW] Applied {model.get('source')} {model.get('symbol')} {direction} TP={self.var_manual_tp.get()} SL={self.var_manual_sl.get()}",
            target="manual",
        )
        if self.var_preview_trade_after_apply.get():
            self.log_message(
                "[PREVIEW] Trade after Apply is ON. Sending via unified order flow.",
                target="manual",
            )
            self.on_click_smart_order()

    # ==========================================
    # CÁC HÀM MỞ POPUP & GIAO DIỆN PHỤ
    # ==========================================
    def open_bot_setting_popup(self):
        ui_popups.open_bot_setting_popup(self)

    def open_preset_config_popup(self):
        ui_popups.open_preset_config_popup(self)

    def open_tsl_popup(self):
        ui_popups.open_tsl_popup(self)

    def open_entry_exit_popup(self):
        from ui_entry_exit_popup import open_entry_exit_popup

        open_entry_exit_popup(self)

    def open_edit_popup(self, ticket):
        ui_popups.open_edit_popup(self, ticket)

    def show_history_popup(self):
        ui_popups.show_history_popup(self)

    # [V3.0] Hàm gọi Strategy Sandbox
    def open_strategy_sandbox(self):
        sandbox_window = BotStrategyUI(self)

        def on_sandbox_close():
            # [FIX JOB 1]: XÓA bỏ hàm tự động save ghi đè cấu hình cũ
            # GỌI hàm Reload để nạp dữ liệu Sandbox vừa lưu trên file vào bộ nhớ
            self.reload_config_from_json()
            self.log_message(
                "📡 [V3.0] Đã đóng Sandbox. Hệ thống đã nạp cấu hình mới (Hot-Reload) từ JSON.",
                error=False,
            )
            sandbox_window.destroy()

        sandbox_window.protocol("WM_DELETE_WINDOW", on_sandbox_close)

    def open_advanced_tools_popup(self):
        ui_popups.open_advanced_tools_popup(self)

    def open_advisor_popup(self):
        return ui_popups.open_advisor_popup(self)

    # ==========================================
    # LOG TAILER - THEO DÕI DAEMON VÀ HIỂN THỊ LÊN UI
    # ==========================================
    def _tail_daemon_logs(self):
        import time
        import os

        # [FIX WinError 32] Trước đây giữ file log mở suốt phiên -> chặn daemon xoay log
        # (os.rename thất bại vì "file đang bị tiến trình khác dùng"), gây spam lỗi +
        # phình daemon_stdout.log. Nay đọc kiểu mở→đọc phần mới→ĐÓNG mỗi nhịp, nhớ offset,
        # để daemon xoay log thoải mái. Nếu file nhỏ đi (đã xoay/cắt) thì đọc lại từ đầu.
        offsets = {}
        while self.running:
            # Wait until workspace is ready
            log_candidates = [os.path.join("data", "logs", "daemon_system_events.log")]
            try:
                import core.storage_manager as storage_manager
                if getattr(storage_manager, "_active_account_dir", None):
                    log_candidates.append(os.path.join(storage_manager._active_account_dir, "daemon_system_events.log"))
            except:
                pass
            log_path = next((p for p in log_candidates if os.path.exists(p)), log_candidates[0])

            if not os.path.exists(log_path):
                time.sleep(2)
                continue

            try:
                for line in _read_appended_lines(log_path, offsets):
                    self._process_daemon_line(line)
            except Exception:
                pass
            time.sleep(0.5)

    def _process_daemon_line(self, line: str):
        line = line.strip()
        if not line: return
        msg = line.split("] - ")[-1] if "] - " in line else line

        # Nhận diện các sự kiện quan trọng (Vào lệnh, Chốt lệnh, Watermark)
        if "Bóp cò" in line or "WATERMARK" in line or "ĐÓNG LỆNH" in line or "REVERSE TACTIC" in line:
            # Cắt bớt phần timestamp của daemon nếu có
            msg = line.split("] - ")[-1] if "] - " in line else line
            self.log_message(f"[DAEMON] {msg}", target="bot")
            return
            
        # Lọc rác (Scanned, No Signal)
        if "Scanned" in line or "No Signal" in line or "Đang tìm và kết nối" in line or "Sẵn sàng kết nối" in line:
            return
            
        # Nhận diện Checklist Fail / Lỗi
        if "FAIL" in line or "ERROR" in line or "TỪ CHỐI" in line or "bị chặn" in line or "SAFEGUARD" in line or "Lỗi" in line or "Bỏ qua" in line:
            # Tạo signature cho lỗi để filter spam
            msg = line.split("] - ")[-1] if "] - " in line else line
            # Trích xuất lý do chính để filter (loại bỏ timestamp, số dư, etc)
            import re
            sig = re.sub(r'\d+', '', msg) # Xoá số đi để gom nhóm lỗi
            
            import time
            now = time.time()
            last_time = self.log_cooldown_cache.get(sig, 0)
            
            # Đọc cooldown time
            try:
                import json
                from core.storage_manager import BRAIN_FILE
                with open(BRAIN_FILE, "r", encoding="utf-8") as cf:
                    b_set = json.load(cf)
                    cd_mins = float(b_set.get("bot_safeguard", {}).get("LOG_COOLDOWN_MINUTES", 60.0))
            except:
                cd_mins = 60.0
                
            if now - last_time > (cd_mins * 60):
                self.log_message(f"⚠️ [DAEMON LOGIC]: {msg}", target="bot-log")
                self.log_cooldown_cache[sig] = now

    # ==========================================

    def load_settings(self):
        if os.path.exists(TSL_SETTINGS_FILE):
            try:
                with open(TSL_SETTINGS_FILE, "r") as f:
                    config.TSL_CONFIG.update(json.load(f))
            except:
                pass
        self._load_advisor_schedule_settings()
        if os.path.exists(PRESETS_FILE):
            try:
                with open(PRESETS_FILE, "r") as f:
                    config.PRESETS.update(json.load(f))
            except:
                pass
        if os.path.exists(BRAIN_SETTINGS_FILE):
            try:
                with open(BRAIN_SETTINGS_FILE, "r") as f:
                    bs = json.load(f)
                    for k, v in bs.items():
                        if k in {"MONEY_DISPLAY_ZERO_TRIM", "MONEY_DISPLAY_UNIT"}:
                            continue
                        if k in {
                            "DNSE_TAX_RATE",
                            "DNSE_STOCK_TAX_RATE",
                            "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE",
                        }:
                            continue
                        if hasattr(config, k) and k != "COIN_LIST":
                            current_val = getattr(config, k)
                            if isinstance(current_val, dict) and isinstance(v, dict):
                                self._merge_dict(current_val, v)
                            else:
                                setattr(config, k, v)
                    set_money_display_zero_trim(
                        bs.get(
                            "MONEY_DISPLAY_ZERO_TRIM",
                            bs.get("MONEY_DISPLAY_UNIT", "000"),
                        )
                    )
                    ee_cfg = bs.get("entry_exit", {})
                    active_entry_tactics = set(ee_cfg.get("entry_tactics", ee_cfg.get("active_tactics", [])))
                    for key in self.entry_exit_tactic_states:
                        self.entry_exit_tactic_states[key] = key in active_entry_tactics
                    if hasattr(self, "btn_entry_swing"):
                        self.update_entry_exit_buttons_ui()
            except:
                pass

    def save_settings(self):
        try:
            os.makedirs(os.path.dirname(TSL_SETTINGS_FILE), exist_ok=True)
            with open(TSL_SETTINGS_FILE, "w") as f:
                json.dump(config.TSL_CONFIG, f, indent=4)
            with open(PRESETS_FILE, "w") as f:
                json.dump(config.PRESETS, f, indent=4)

            # [HOTFIX V4.4] Đồng bộ ngay lập tức sang brain_settings.json để không bị Sandbox ghi đè ngược
            brain = load_brain_settings()
            brain["TSL_CONFIG"] = dict(config.TSL_CONFIG)
            brain["TSL_LOGIC_MODE"] = getattr(config, "TSL_LOGIC_MODE", "STATIC")
            brain.setdefault("risk_tsl", {})["tsl_mode"] = brain["TSL_LOGIC_MODE"]
            save_brain_settings(brain)
            self._save_brain_live_config()
        except:
            pass

    def get_fee_config(self, symbol):
        broker_fee = getattr(config, "DNSE_BROKER_FEE_PER_CONTRACT", None)
        if broker_fee is not None:
            return float(broker_fee or 0.0)
        specific_rate = config.COMMISSION_RATES.get(symbol, -1)
        if specific_rate != -1:
            return float(specific_rate or 0.0)
        acc_type = "STANDARD"
        acc_cfg = config.ACCOUNT_TYPES_CONFIG.get(
            acc_type, config.ACCOUNT_TYPES_CONFIG["STANDARD"]
        )
        return float(acc_cfg.get("COMMISSION_PER_LOT", 0.0) or 0.0)

    def get_derivative_fee_profile(self, symbol=None):
        connector = getattr(self, "connector", None)
        if connector and hasattr(connector, "get_fee_profile"):
            try:
                profile = connector.get_fee_profile(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M"))
                if hasattr(profile, "as_dict"):
                    return profile.as_dict()
                if isinstance(profile, dict):
                    return dict(profile)
            except Exception:
                pass
        if settlement.is_cash_stock(symbol or ""):
            return {
                "broker_fee_per_contract": 0.0,
                "exchange_fee_per_contract": 0.0,
                "clearing_fee_per_contract": 0.0,
                "broker_fee_rate": float(
                    getattr(config, "DNSE_STOCK_BROKER_FEE_RATE", 0.0) or 0.0
                ),
                "tax_rate": float(getattr(config, "DNSE_STOCK_TAX_RATE", 0.001) or 0.001),
                "initial_margin_rate": 0.0,
                "point_value": float(
                    getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0
                ),
                "market_type": "STOCK",
                "quantity_unit": "CP",
                "fee_available": False,
            }
        return {
            "broker_fee_per_contract": self.get_fee_config(symbol or getattr(config, "DEFAULT_SYMBOL", "VN30F1M")),
            "exchange_fee_per_contract": float(getattr(config, "DNSE_EXCHANGE_FEE_PER_CONTRACT", 2700.0) or 0.0),
            "clearing_fee_per_contract": float(getattr(config, "DNSE_CLEARING_FEE_PER_CONTRACT", 2550.0) or 0.0),
            "tax_rate": float(getattr(config, "DNSE_TAX_RATE", 0.0) or 0.0),
            "broker_fee_rate": 0.0,
            "initial_margin_rate": float(
                getattr(config, "DNSE_DERIVATIVE_INITIAL_MARGIN_RATE", 0.20) or 0.20
            ),
            "point_value": float(getattr(config, "DNSE_POINT_VALUE", 100000.0) or 100000.0),
            "market_type": "DERIVATIVE",
            "quantity_unit": "HĐ",
            "fee_available": True,
        }

    def calculate_trade_fee(self, symbol, price, contracts, side=None):
        connector = getattr(self, "connector", None)
        if connector and hasattr(connector, "calculate_trade_fee"):
            try:
                return float(connector.calculate_trade_fee(symbol, price, contracts, side=side))
            except Exception:
                pass
        qty = max(0.0, float(contracts or 0.0))
        profile = self.get_derivative_fee_profile(symbol)
        fixed = qty * (
            profile["broker_fee_per_contract"]
            + profile["exchange_fee_per_contract"]
            + profile["clearing_fee_per_contract"]
        )
        notional = max(0.0, float(price or 0.0)) * qty * profile["point_value"]
        rate_fee = notional * float(profile.get("broker_fee_rate", 0.0) or 0.0)
        if str(profile.get("market_type", "DERIVATIVE")).upper() == "DERIVATIVE":
            tax_base = notional * float(profile.get("initial_margin_rate", 0.0) or 0.0) / 2.0
            tax = tax_base * float(profile.get("tax_rate", 0.0) or 0.0)
        else:
            side_key = str(side or "").upper()
            tax = (
                notional * float(profile.get("tax_rate", 0.0) or 0.0)
                if not side_key or side_key in {"1", "SELL", "SHORT", "NS", "S"}
                else 0.0
            )
        return fixed + rate_fee + tax

    def calculate_round_trip_trade_fee(
        self, symbol, entry_price, exit_price, contracts, entry_side
    ):
        open_side = str(entry_side or "BUY").upper()
        close_side = "SELL" if open_side in {"BUY", "LONG", "NB", "0"} else "BUY"
        return self.calculate_trade_fee(
            symbol, entry_price, contracts, side=open_side
        ) + self.calculate_trade_fee(
            symbol, exit_price, contracts, side=close_side
        )

    def _fmt_money(self, value, signed=False, suffix=False):
        return format_vnd(value, signed=signed, suffix=suffix)

    @staticmethod
    def _combined_display_pnl(realized_pnl, positions):
        """PNL đầu trang = đã chốt + lãi/lỗ nổi; không sửa sổ backend."""
        realized = float(realized_pnl or 0.0)
        floating = 0.0
        for position in positions or []:
            floating += float(getattr(position, "profit", 0.0) or 0.0)
            floating += float(getattr(position, "swap", 0.0) or 0.0)
        return realized + floating, realized, floating

    @staticmethod
    def _responsive_money_font_size(text, base_size, min_size, comfortable_chars):
        """Co chữ theo chuỗi thực tế; khung không đổi và không cắt mất số."""
        longest = max((len(line) for line in str(text or "").splitlines()), default=0)
        if longest <= comfortable_chars:
            return int(base_size)
        scaled = int(round(float(base_size) * float(comfortable_chars) / float(longest)))
        return max(int(min_size), min(int(base_size), scaled))

    def _set_money_label(
        self,
        label,
        text,
        *,
        base_size,
        min_size,
        comfortable_chars,
        family="Roboto",
        weight="bold",
        **options,
    ):
        if label is None:
            return
        size = self._responsive_money_font_size(
            text, base_size, min_size, comfortable_chars
        )
        font = (family, size, weight) if weight else (family, size)
        label.configure(text=text, font=font, **options)

    @staticmethod
    def _fmt_qty(value):
        try:
            return f"{int(round(float(value or 0.0))):,}"
        except (TypeError, ValueError):
            return "0"

    def open_portfolio_popup(self):
        """Mở cửa sổ Danh mục cổ phiếu nắm giữ (CKCS)."""
        ui_popups.open_portfolio_popup(self)

    def update_portfolio_table(self, acc=None, positions=None, force_portfolio=False):
        """Cập nhật tách Tổng/Tiền/Giá trị CP (luôn) + nạp bảng danh mục (khi popup mở).

        Dùng get_positions() KHÔNG lọc magic (thấy cả cổ mua ngoài bot).
        """
        try:
            from core import portfolio
            if positions is None:
                positions = self.connector.get_positions()
            if isinstance(acc, dict):
                account_info = acc
            else:
                account_info = self.connector.get_account_info()
            is_paper_account = str((account_info or {}).get("status", "")).upper() == "PAPER"
            summary = (
                portfolio.paper_portfolio_summary(positions, account_info)
                if is_paper_account
                else portfolio.portfolio_summary(positions, account_info)
            )
        except Exception as exc:
            main_logger.debug("update_portfolio_table failed: %s", exc)
            return

        assets = summary["assets"]

        # 1) Tách tài sản ở header chính (luôn cập nhật, kể cả khi popup đóng)
        if getattr(self, "lbl_cash", None) is not None:
            self.lbl_cash.configure(text=f"Tiền: {self._fmt_money(assets['cash'])}")
        if getattr(self, "lbl_stock_value", None) is not None:
            self.lbl_stock_value.configure(text=f"CP: {self._fmt_money(assets['stock_value'])}")
        # Balances cổ phiếu không trả sẵn NAV -> dùng tổng tự tính KHI đang giữ CP.
        # Không giữ CP (paper chưa khớp / TK phái sinh) -> giữ nguyên equity của broker.
        if assets["stock_value"] > 0 and getattr(self, "lbl_equity", None) is not None:
            self._set_money_label(
                self.lbl_equity,
                self._fmt_money(assets["total"]),
                base_size=32,
                min_size=18,
                comfortable_chars=8,
            )

        # Popup má»›i cÃ³ 4 scope Ä‘á»™c láº­p. Header chÃ­nh phÃ­a trÃªn váº«n theo mode
        # Ä‘ang chá»n, cÃ²n popup Ä‘á»c cáº£ REAL/PAPER mÃ  khÃ´ng Ä‘á»•i config runtime.
        if getattr(self, "portfolio_trees", None):
            self._update_portfolio_scope_tables(force=bool(force_portfolio))
            return

        # 2) Tách tài sản trong popup (nếu đang mở)
        if getattr(self, "lbl_port_total", None) is not None:
            self._set_money_label(
                self.lbl_port_total,
                f"Tổng tài sản: {self._fmt_money(assets['total'])}",
                base_size=16,
                min_size=10,
                comfortable_chars=24,
            )
        if getattr(self, "lbl_port_cash", None) is not None:
            self._set_money_label(
                self.lbl_port_cash,
                f"Tiền mặt: {self._fmt_money(assets['cash'])}",
                base_size=14,
                min_size=9,
                comfortable_chars=22,
            )
        # Sức mua: chỉ hiện khi khác tiền mặt (có vay margin) -> đỡ rối cho cash-only.
        if getattr(self, "lbl_port_avail", None) is not None:
            _avail = assets.get("available_cash", assets.get("cash", 0.0))
            _txt = f"Sức mua: {self._fmt_money(_avail)}" if _avail > assets.get("cash", 0.0) + 1 else ""
            self._set_money_label(
                self.lbl_port_avail, _txt,
                base_size=12, min_size=9, comfortable_chars=22,
            )
        if getattr(self, "lbl_port_stock", None) is not None:
            self._set_money_label(
                self.lbl_port_stock,
                f"Cổ phiếu: {self._fmt_money(assets['stock_value'])}",
                base_size=12, min_size=9, comfortable_chars=22,
            )
        if getattr(self, "lbl_port_odd", None) is not None:
            _odd_val = assets.get("odd_lot_value", 0.0)
            _odd_n = assets.get("odd_lot_count", 0)
            _odd_txt = f"Lô lẻ: {self._fmt_money(_odd_val)} ({_odd_n} mã)" if _odd_n else "Lô lẻ: 0"
            self._set_money_label(
                self.lbl_port_odd, _odd_txt,
                base_size=11, min_size=8, comfortable_chars=24,
            )
        # Nợ vay & cổ tức sắp về: chỉ hiện khi > 0.
        if getattr(self, "lbl_port_debt", None) is not None:
            _debt = assets.get("debt", 0.0)
            _debt_text = f"Nợ vay: {self._fmt_money(_debt)}" if _debt > 0 else ""
            self._set_money_label(
                self.lbl_port_debt, _debt_text,
                base_size=11, min_size=8, comfortable_chars=22,
            )
        if getattr(self, "lbl_port_dividend", None) is not None:
            _div = assets.get("dividend", 0.0)
            _div_text = f"Cổ tức sắp về: {self._fmt_money(_div)}" if _div > 0 else ""
            self._set_money_label(
                self.lbl_port_dividend, _div_text,
                base_size=11, min_size=8, comfortable_chars=26,
            )

        # 3) Bảng danh mục (chỉ khi popup đang mở -> tree_portfolio tồn tại)
        tree = getattr(self, "tree_portfolio", None)
        if tree is None:
            return
        try:
            existing = set(tree.get_children(""))
            seen = set()
            for h in summary["holdings"]:
                iid = h.symbol
                seen.add(iid)
                values = (
                    h.symbol,
                    self._fmt_qty(h.quantity),
                    self._fmt_qty(h.sellable),
                    self._fmt_qty(h.pending) if h.pending else "—",
                    self._fmt_price(h.avg_cost, h.symbol),
                    self._fmt_price(h.market_price, h.symbol),
                    self._fmt_money(h.market_value),
                    f"{self._fmt_money(h.pnl, signed=True)} ({h.pnl_pct:+.2f}%)",
                    h.note,
                )
                if h.is_odd_lot:
                    tag = "odd_lot"
                elif h.pnl > 0:
                    tag = "profit_row"
                elif h.pnl < 0:
                    tag = "loss_row"
                else:
                    tag = "flat_row"
                if iid in existing:
                    tree.item(iid, values=values, tags=(tag,))
                else:
                    tree.insert("", "end", iid=iid, values=values, tags=(tag,))
            for iid in existing - seen:
                tree.delete(iid)
        except Exception as exc:
            main_logger.debug("portfolio tree render failed: %s", exc)

    def _update_portfolio_scope_tables(self, force=False):
        """Nạp bốn tab danh mục và thông tin ký quỹ CKPS."""
        from core import portfolio

        trees = getattr(self, "portfolio_trees", None) or {}
        labels_by_scope = getattr(self, "portfolio_summary_labels", None) or {}
        if not trees:
            return

        def is_derivative_position(pos):
            raw = getattr(pos, "raw", {}) if isinstance(getattr(pos, "raw", {}), dict) else {}
            market_type = str(raw.get("marketType") or raw.get("market") or "").upper()
            return market_type == "DERIVATIVE" or self._is_derivative_symbol(getattr(pos, "symbol", ""))

        now = time.time()
        cached = getattr(self, "_portfolio_scope_cache", None)
        if force or not cached or now - float(cached.get("time", 0.0) or 0.0) >= 5.0:
            real_acc = self.connector.get_account_info(paper_mode=False) or {}
            real_positions = self.connector.get_positions(paper_mode=False) or []
            paper_acc = self.connector.get_account_info(paper_mode=True) or {}
            paper_positions = self.connector.get_positions(paper_mode=True) or []

            aliases = list(getattr(config, "CKPS_SYMBOLS", []) or ["VN30F1M"])
            selected = str(self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else "").upper()
            derivative_symbol = selected if self._is_derivative_symbol(selected) else str(aliases[0]).upper()
            context_map = getattr(self, "latest_market_context", {}) or {}
            context = context_map.get(derivative_symbol, {}) if isinstance(context_map, dict) else {}
            derivative_price = float(
                (context or {}).get("ask")
                or (context or {}).get("current_price")
                or (self.last_price_val if self._is_derivative_symbol(selected) else 0.0)
                or 0.0
            )
            if derivative_price <= 0:
                candidates = [
                    pos for pos in (real_positions + paper_positions)
                    if is_derivative_position(pos)
                ]
                if candidates:
                    derivative_price = float(
                        getattr(candidates[0], "price_current", 0.0)
                        or getattr(candidates[0], "price_open", 0.0)
                        or 0.0
                    )
            if derivative_price <= 0:
                try:
                    tick = self.connector.get_tick(derivative_symbol)
                    derivative_price = float(getattr(tick, "ask", 0.0) or getattr(tick, "last", 0.0) or 0.0)
                except Exception:
                    derivative_price = 0.0

            real_capacity = self.connector.get_derivative_margin_capacity(
                derivative_symbol,
                derivative_price,
                paper_mode=False,
                positions=real_positions,
                # Nút Làm mới không được phép bỏ cache PPSE 10 giây,
                # tránh người dùng bấm liên tục làm dồn API DNSE.
                force_refresh=False,
            )
            paper_capacity = self.connector.get_derivative_margin_capacity(
                derivative_symbol,
                derivative_price,
                paper_mode=True,
                positions=paper_positions,
                force_refresh=force,
            )
            cached = {
                "time": now,
                "real_acc": real_acc,
                "real_positions": real_positions,
                "paper_acc": paper_acc,
                "paper_positions": paper_positions,
                "real_capacity": real_capacity,
                "paper_capacity": paper_capacity,
            }
            self._portfolio_scope_cache = cached

        datasets = {
            "CKPS REAL": (cached["real_acc"], cached["real_positions"], cached["real_capacity"]),
            "CKCS REAL": (cached["real_acc"], cached["real_positions"], None),
            "CKPS PAPER": (cached["paper_acc"], cached["paper_positions"], cached["paper_capacity"]),
            "CKCS PAPER": (cached["paper_acc"], cached["paper_positions"], None),
        }

        def set_labels(scope, texts):
            labels = labels_by_scope.get(scope, [])
            for index, label in enumerate(labels):
                label.configure(text=texts[index] if index < len(texts) else "")

        for scope, (account, all_positions, capacity) in datasets.items():
            tree = trees.get(scope)
            if tree is None:
                continue
            for iid in tree.get_children(""):
                tree.delete(iid)

            if scope.startswith("CKCS"):
                stock_positions = [
                    pos for pos in all_positions
                    if not is_derivative_position(pos)
                ]
                summary = (
                    portfolio.paper_portfolio_summary(stock_positions, account)
                    if scope.endswith("PAPER")
                    else portfolio.portfolio_summary(stock_positions, account)
                )
                assets = summary["assets"]
                extra = []
                if assets.get("debt", 0.0) > 0:
                    extra.append(f"Nợ vay: {self._fmt_money(assets['debt'])}")
                if assets.get("dividend", 0.0) > 0:
                    extra.append(f"Cổ tức chờ: {self._fmt_money(assets['dividend'])}")
                set_labels(scope, [
                    f"Tổng: {self._fmt_money(assets['total'])}",
                    f"Tiền: {self._fmt_money(assets['cash'])}",
                    f"Cổ phiếu: {self._fmt_money(assets['stock_value'])}",
                    f"Sức mua: {self._fmt_money(assets.get('available_cash', 0.0))}",
                    f"Lô lẻ: {self._fmt_money(assets.get('odd_lot_value', 0.0))} ({assets.get('odd_lot_count', 0)} mã)",
                    " | ".join(extra),
                ])
                for holding in summary["holdings"]:
                    tag = "odd_lot" if holding.is_odd_lot else (
                        "profit_row" if holding.pnl > 0 else "loss_row" if holding.pnl < 0 else "flat_row"
                    )
                    tree.insert("", "end", iid=holding.symbol, values=(
                        holding.symbol,
                        self._fmt_qty(holding.quantity),
                        self._fmt_qty(holding.sellable),
                        self._fmt_qty(holding.pending) if holding.pending else "—",
                        self._fmt_price(holding.avg_cost, holding.symbol),
                        self._fmt_price(holding.market_price, holding.symbol),
                        self._fmt_money(holding.market_value),
                        f"{self._fmt_money(holding.pnl, signed=True)} ({holding.pnl_pct:+.2f}%)",
                        holding.note,
                    ), tags=(tag,))
                continue

            capacity = capacity or {}
            source = str(capacity.get("source", "FORMULA") or "FORMULA")
            source_text = "DNSE xác nhận" if source == "DNSE_PPSE" else (
                "Mô phỏng PAPER" if source == "PAPER_ESTIMATE" else "Ước tính; PPSE chưa xác nhận"
            )
            set_labels(scope, [
                f"Ký quỹ 1 HĐ: ~{self._fmt_money(capacity.get('margin_per_contract', 0.0))}",
                f"Cọc còn lại{' (mô phỏng)' if scope.endswith('PAPER') else ''}: {self._fmt_money(capacity.get('remaining_secure', 0.0))}",
                f"Sức mở: M {int(capacity.get('qmax_buy', 0) or 0)} / B {int(capacity.get('qmax_sell', 0) or 0)} HĐ",
                f"Tỷ lệ KQ: {float(capacity.get('initial_rate', 0.0) or 0.0) * 100:.2f}%",
                f"Cọc đã dùng: {self._fmt_money(capacity.get('used_secure', 0.0))}",
                source_text,
            ])
            initial_rate = float(capacity.get("initial_rate", 0.0) or 0.0)
            point_value = float(getattr(config, "DNSE_POINT_VALUE", 100000.0) or 100000.0)
            derivative_positions = [
                pos for pos in all_positions
                if is_derivative_position(pos)
            ]
            for index, pos in enumerate(derivative_positions):
                symbol = str(getattr(pos, "symbol", "") or "")
                side = "BUY" if int(getattr(pos, "type", 0) or 0) == 0 else "SELL"
                qty = abs(float(getattr(pos, "volume", 0.0) or 0.0))
                entry = float(getattr(pos, "price_open", 0.0) or 0.0)
                price = float(getattr(pos, "price_current", 0.0) or entry)
                pnl = float(getattr(pos, "profit", 0.0) or 0.0) + float(getattr(pos, "commission", 0.0) or 0.0)
                margin = qty * price * point_value * initial_rate
                raw = getattr(pos, "raw", {}) if isinstance(getattr(pos, "raw", {}), dict) else {}
                status = str(raw.get("status") or getattr(pos, "comment", "") or "OPEN")
                iid = f"{scope}:{getattr(pos, 'ticket', index)}"
                tree.insert("", "end", iid=iid, values=(
                    symbol,
                    side,
                    f"{qty:g}",
                    self._fmt_price(entry, symbol),
                    self._fmt_price(price, symbol),
                    self._fmt_price(float(getattr(pos, "sl", 0.0) or 0.0), symbol) if getattr(pos, "sl", 0.0) else "OFF",
                    self._fmt_price(float(getattr(pos, "tp", 0.0) or 0.0), symbol) if getattr(pos, "tp", 0.0) else "OFF",
                    self._fmt_money(margin),
                    self._fmt_money(pnl, signed=True),
                    status,
                ), tags=("buy_row" if side == "BUY" else "sell_row",))

    def _is_derivative_symbol(self, symbol):
        connector = getattr(self, "connector", None)
        if connector and hasattr(connector, "market_type_for_symbol"):
            try:
                return connector.market_type_for_symbol(symbol) == "DERIVATIVE"
            except Exception:
                pass
        sym = str(symbol or "").upper()
        return sym.startswith("VN30F") or sym in {str(s).upper() for s in getattr(config, "CKPS_SYMBOLS", []) or []}

    def _quantity_unit(self, symbol):
        return "HĐ" if self._is_derivative_symbol(symbol) else "CP"

    def _quantity_label(self, symbol):
        return "Hợp đồng" if self._is_derivative_symbol(symbol) else "Cổ phiếu"

    def _symbol_contract_size(self, symbol):
        connector = getattr(self, "connector", None)
        if connector and hasattr(connector, "get_symbol_info"):
            try:
                return float(connector.get_symbol_info(symbol).trade_contract_size or 1.0)
            except Exception:
                pass
        return float(getattr(config, "DNSE_POINT_VALUE", 100000.0) or 100000.0) if self._is_derivative_symbol(symbol) else float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0)

    def _collect_position_market(self, positions):
        """[FREEZE FIX] Gom I/O mạng cho từng vị thế NGAY TRÊN THREAD NỀN (bg_update_loop),
        để update_ui (thread UI) chỉ đọc số liệu đã có sẵn, không gọi mạng -> hết đơ.

        Trả về dict keyed theo ticket_str: {"c_size", "spread", "comm"}.
        c_size lấy bằng poll_tick=False (tĩnh, không đụng endpoint tick).
        spread lấy từ fetch_realtime_tick (cache 2s/WS). comm = phí ước tính (fee profile cache 1h).
        """
        extras = {}
        for p in (positions or []):
            tkt = str(p.ticket)
            c_size = None
            try:
                if self.connector:
                    c_size = self.connector.get_symbol_info(p.symbol, poll_tick=False).trade_contract_size
            except Exception:
                c_size = None
            if not c_size:
                c_size = self._symbol_contract_size(p.symbol)

            spread = 0.0
            try:
                td = data_engine.fetch_realtime_tick(p.symbol)
            except Exception:
                td = None
            if td and not td.get("synthetic_quote"):
                b = float(td.get("bid", 0.0) or 0.0)
                a = float(td.get("ask", 0.0) or 0.0)
                if b > 0 and a >= b:
                    spread = a - b

            try:
                close_side = "SELL" if int(getattr(p, "type", 0)) == 0 else "BUY"
                comm = self.calculate_trade_fee(
                    p.symbol, p.price_current or p.price_open, p.volume, side=close_side
                )
            except Exception:
                comm = 0.0

            extras[tkt] = {
                "c_size": float(c_size or 1.0),
                "spread": float(spread or 0.0),
                "comm": float(comm or 0.0),
            }
        return extras

    def _normalize_contracts(self, value, min_contracts=None, max_contracts=None):
        try:
            qty = float(value or 0.0)
        except Exception:
            qty = 0.0
        if qty <= 0:
            return 0.0
        min_c = int(max(1, round(float(min_contracts or getattr(config, "MIN_LOT_SIZE", 1.0) or 1.0))))
        max_c = int(max(min_c, round(float(max_contracts or getattr(config, "MAX_LOT_SIZE", 200.0) or 200.0))))
        qty = int(round(qty))
        qty = max(min_c, min(qty, max_c))
        return float(qty)

    def _pending_target_for_entry(self, entry_price):
        if float(entry_price or 0.0) > 0:
            return "OPEN"
        mode = self.var_manual_trade_mode.get() if hasattr(self, "var_manual_trade_mode") else "ATO"
        return mode if mode in ("ATO", "ATC") else "ATO"

    def _manual_inputs_for_order(self):
        symbol = self.cbo_symbol.get()
        side = self.var_direction.get()
        preset = getattr(config, "DEFAULT_PRESET", "SCALPING")
        tactic = self.get_current_tactic_string()
        ee_tactic = self.get_current_entry_exit_tactic_string()
        lot = self._safe_float(self.var_manual_lot.get() or 0.0)
        entry_price = self._price_input_to_internal(self.var_manual_entry.get(), symbol)
        tp = self._price_input_to_internal(self.var_manual_tp.get(), symbol)
        sl = self._price_input_to_internal(self.var_manual_sl.get(), symbol)
        ctx = self.latest_market_context.get(symbol, {})
        lot_source = "MANUAL_LOT" if lot > 0 else ""
        sl_source = "MANUAL_SL" if sl > 0 else ""
        tp_source = "MANUAL_TP" if tp > 0 else ""
        try:
            ee_decision = self.latest_entry_exit_decisions.get(symbol, {})
            if ee_decision.get("status") == "READY":
                if sl == 0.0 and ee_decision.get("sl"):
                    sl = float(ee_decision["sl"])
                    sl_source = "E/E"
                if tp == 0.0 and ee_decision.get("tp"):
                    tp = float(ee_decision["tp"])
                    tp_source = "E/E"
        except Exception:
            pass
        try:
            setup = self._resolve_manual_setup_preview(symbol, side, preset, ctx)
            if setup.get("ready"):
                if lot <= 0:
                    lot = float(setup.get("lot", 0.0) or 0.0)
                    lot_source = str(setup.get("lot_source", "") or "AUTO_RISK")
                if sl <= 0:
                    sl = float(setup.get("sl", 0.0) or 0.0)
                    sl_source = str(setup.get("sl_source_label", setup.get("sl_source", "")) or "PRESET")
                if tp <= 0:
                    tp = float(setup.get("tp", 0.0) or 0.0)
                    tp_source = str(setup.get("tp_source_label", setup.get("tp_source", "")) or "PRESET")
        except Exception:
            pass
        return {
            "symbol": symbol,
            "side": side,
            "preset": preset,
            "tactic": tactic,
            "entry_exit_tactic": ee_tactic,
            "lot": lot,
            "lot_source": lot_source,
            "entry_price": entry_price,
            "entry_source": f"LO:{entry_price:g}" if entry_price > 0 else self._pending_target_for_entry(entry_price),
            "tp": tp,
            "tp_source": tp_source,
            "sl": sl,
            "sl_source": sl_source,
            "context": ctx,
        }

    def on_click_schedule_order(self):
        data = self._manual_inputs_for_order()
        target = self._pending_target_for_entry(data["entry_price"])
        try:
            order_plan = (
                f"OPEN -> DNSE LO @{data['entry_price']:g}"
                if data["entry_price"] > 0
                else f"{target} -> DNSE {target}"
            )
            item = pending_orders.add_order(
                symbol=data["symbol"],
                side=data["side"],
                preset=data["preset"],
                lot=data["lot"],
                entry_price=data["entry_price"],
                sl=data["sl"],
                tp=data["tp"],
                target=target,
                note=f"TSL={data['tactic']}",
                manual_entry_tactic=data["entry_exit_tactic"],
                lot_source=data.get("lot_source", ""),
                sl_source=data.get("sl_source", ""),
                tp_source=data.get("tp_source", ""),
                entry_source=data.get("entry_source", ""),
                plan=order_plan,
                bypass_checklist=bool(self.var_bypass_checklist.get()),
            )
            mode = "LO" if data["entry_price"] > 0 else target
            self.log_message(
                f"[LIMIT ORDER] Cache {data['side']} {data['symbol']} {mode} id={item['id'][:8]} plan={order_plan} lot={data['lot']:g} sl={data['sl']:g} tp={data['tp']:g}.",
                target="manual",
            )
            from core.market_hours import is_symbol_trade_window_open
            if getattr(config, "PAPER_TRADING", True) or is_symbol_trade_window_open(data["symbol"])[0]:
                cached_acc = self.connector.get_account_info()
                cached_positions = self.connector.get_all_open_positions()
            else:
                cached_acc = getattr(self.connector, "_account_cache", None) or {
                    "login": getattr(self.connector, "account_no", ""),
                    "balance": 0.0,
                    "equity": 0.0,
                    "margin": 0.0,
                    "free_margin": 0.0,
                }
                cached_positions = list(getattr(self.connector, "_positions_cache", []) or [])
            cached_positions = BotUI._merge_position_snapshots(
                getattr(self, "_ui_all_positions_snapshot", []),
                cached_positions,
            )
            self.update_ui(
                cached_acc,
                self.trade_mgr.state,
                self.checklist_mgr.run_pre_trade_checks(
                    cached_acc, self.trade_mgr.state, data["symbol"], self.var_strict_mode.get()
                ),
                None,
                data["preset"],
                data["symbol"],
                cached_positions,
                [],
            )
        except Exception as exc:
            self.log_message(f"[LIMIT ORDER] Khong tao duoc cache: {exc}", error=True, target="manual")

    def _run_pending_order_scheduler(self):
        if self.__dict__.get("_mode_switching", False):
            return
        try:
            for rec in pending_orders.recover_stuck():
                self.log_message(f"[HẸN LỆNH] Khôi phục lệnh kẹt {rec.get('symbol')} id={str(rec.get('id'))[:8]}", target="manual")
            expired = pending_orders.expire_pending()
            for item in expired:
                self.log_message(f"[HẸN LỆNH] Hết hạn {item.get('symbol')} id={str(item.get('id'))[:8]}", error=True, target="manual")
            # Dọn hẳn lệnh EXPIRED/FAILED/CANCELLED cũ khỏi bảng running (không cần bấm X tay).
            for item in pending_orders.purge_stale():
                self.log_message(f"[HẸN LỆNH] Đã dọn lệnh chết {item.get('symbol')} id={str(item.get('id'))[:8]}", target="manual")
            self._reconcile_working_pending_closes()
            from core.market_hours import market_session_phase
            def _quote(symbol, side):
                ctx = self.latest_market_context.get(str(symbol or "").upper(), {}) or {}
                key = "ask" if str(side or "BUY").upper() == "BUY" else "bid"
                return float(ctx.get(key, ctx.get("current_price", 0.0)) or 0.0)

            def _point(symbol):
                try:
                    return float(self.connector.get_symbol_info(symbol, poll_tick=False).point)
                except Exception:
                    return float(getattr(config, "DNSE_PRICE_POINT", 0.1) or 0.1)

            due_items = pending_orders.claim_due(
                market_session_phase,
                quote_fn=_quote,
                point_fn=_point,
                include_inactive_closes=True,
            )
            for item in due_items:
                self._send_pending_order(item)
        except Exception as exc:
            self.log_message(f"[HẸN LỆNH] Scheduler lỗi: {exc}", error=True, target="manual")

    def _reconcile_working_pending_closes(self):
        bad_statuses = {
            "CANCELLED",
            "CANCELED",
            "REJECTED",
            "REJECT",
            "EXPIRED",
            "FAILED",
            "ERROR",
        }
        filled_statuses = {"FILLED", "MATCHED", "COMPLETED", "DONE"}
        order_cache = {}
        for item in pending_orders.list_all():
            status = str(item.get("status", "") or "").upper()
            is_unknown_submission = bool(item.get("unknown_submission", False))
            if str(item.get("action", "OPEN") or "OPEN").upper() != "CLOSE":
                continue
            if status != pending_orders.WORKING and not (
                status == pending_orders.ERROR and is_unknown_submission
            ):
                continue
            local_id = str(item.get("id", "") or "")
            ticket = str(item.get("position_ticket", "") or "")
            symbol = str(item.get("symbol", "") or "").upper()
            position = BotUI._find_open_position(self, ticket)
            position_is_open = position is not None
            broker_order_id = str(item.get("dnse_order_id", "") or "")
            request_tag = str(item.get("request_tag", "") or "")
            if not broker_order_id and not request_tag:
                if not position_is_open:
                    pending_orders.mark(
                        local_id,
                        pending_orders.SENT,
                        "POSITION_CLOSED",
                    )
                continue
            if symbol not in order_cache:
                try:
                    order_cache[symbol] = list(
                        self.connector.get_orders(
                            paper_mode=False,
                            symbol=symbol,
                        )
                        or []
                    )
                except Exception:
                    order_cache[symbol] = []
            broker_order = next(
                (
                    row
                    for row in order_cache[symbol]
                    if (
                        broker_order_id
                        and str(
                            row.get("orderId")
                            or row.get("id")
                            or row.get("orderID")
                            or ""
                        )
                        == broker_order_id
                    )
                    or (
                        request_tag
                        and request_tag
                        in str(
                            row.get("remark")
                            or row.get("comment")
                            or row.get("note")
                            or ""
                        )
                    )
                ),
                None,
            )
            if broker_order and not broker_order_id:
                broker_order_id = str(
                    broker_order.get("orderId")
                    or broker_order.get("id")
                    or broker_order.get("orderID")
                    or ""
                )
                pending_orders.mark(
                    local_id,
                    pending_orders.WORKING,
                    "RECONCILED_AFTER_UNKNOWN_SUBMISSION",
                    dnse_order_id=broker_order_id,
                    unknown_submission=False,
                )
            broker_status = str(
                (broker_order or {}).get("orderStatus")
                or (broker_order or {}).get("status")
                or (broker_order or {}).get("state")
                or ""
            ).upper()
            if not position_is_open:
                if broker_status in filled_statuses or broker_status in bad_statuses:
                    pending_orders.mark(
                        local_id,
                        pending_orders.SENT,
                        f"POSITION_CLOSED|DNSE_{broker_status or 'FINAL'}",
                    )
                    continue
                if not broker_order_id:
                    continue
                # The position may have been closed by SL/TP while this LO was
                # resting. Cancel any remainder before it can open a reverse
                # futures position.
                try:
                    cancel_result = self.connector.cancel_order(
                        broker_order_id,
                        symbol=symbol,
                        paper_mode=False,
                    )
                except Exception as exc:
                    cancel_result = SimpleNamespace(
                        ok=False,
                        message=str(exc),
                        error=str(exc),
                    )
                if getattr(cancel_result, "ok", False):
                    pending_orders.mark(
                        local_id,
                        pending_orders.SENT,
                        "POSITION_CLOSED|LIMIT_REMAINDER_CANCELLED",
                    )
                else:
                    cancel_message = (
                        getattr(cancel_result, "message", "")
                        or getattr(cancel_result, "error", "")
                        or "CANCEL_REMAINDER_FAILED"
                    )
                    pending_orders.mark(
                        local_id,
                        pending_orders.WORKING,
                        f"POSITION_GONE|{cancel_message}",
                    )
                continue
            if broker_order:
                def _broker_number(*keys):
                    for key in keys:
                        value = broker_order.get(key)
                        if value not in (None, ""):
                            try:
                                return abs(float(value))
                            except (TypeError, ValueError):
                                return 0.0
                    return 0.0

                current_volume = abs(
                    float(getattr(position, "volume", 0.0) or 0.0)
                )
                order_volume = _broker_number(
                    "quantity",
                    "orderQuantity",
                    "volume",
                    "qty",
                )
                matched_volume = _broker_number(
                    "matchedQuantity",
                    "filledQuantity",
                    "filledQty",
                    "executedQuantity",
                )
                remaining_volume = max(0.0, order_volume - matched_volume)
                raw_side = str(
                    broker_order.get("side")
                    or broker_order.get("orderSide")
                    or broker_order.get("orderType")
                    or ""
                ).upper()
                broker_side = (
                    "BUY"
                    if raw_side in {"BUY", "NB", "0"}
                    else "SELL"
                    if raw_side in {"SELL", "NS", "1"}
                    else ""
                )
                expected_side = (
                    "SELL"
                    if int(getattr(position, "type", 0) or 0) == 0
                    else "BUY"
                )

                # A position can be reduced or even flipped from DNSE Mobile
                # while our close LO is resting.  Never leave an order that can
                # exceed/reinforce the live broker position.
                side_mismatch = bool(
                    broker_side and broker_side != expected_side
                )
                over_close = bool(
                    order_volume > 0
                    and remaining_volume > current_volume + 1e-9
                )
                if side_mismatch:
                    try:
                        cancel_result = self.connector.cancel_order(
                            broker_order_id,
                            symbol=symbol,
                            paper_mode=False,
                        )
                    except Exception as exc:
                        cancel_result = SimpleNamespace(
                            ok=False,
                            message=str(exc),
                            error=str(exc),
                        )
                    pending_orders.mark(
                        local_id,
                        pending_orders.ERROR,
                        (
                            "POSITION_DIRECTION_CHANGED|LIMIT_CANCELLED"
                            if getattr(cancel_result, "ok", False)
                            else "POSITION_DIRECTION_CHANGED|CANCEL_FAILED"
                        ),
                        broker_terminal=bool(
                            getattr(cancel_result, "ok", False)
                        ),
                    )
                    continue
                if over_close:
                    # DNSE replacement quantity is the total order quantity,
                    # therefore already matched volume must be retained.
                    replacement_quantity = matched_volume + current_volume
                    try:
                        replace_result = self.connector.replace_order(
                            broker_order_id,
                            quantity=replacement_quantity,
                            symbol=symbol,
                        )
                    except Exception as exc:
                        replace_result = SimpleNamespace(
                            ok=False,
                            message=str(exc),
                            error=str(exc),
                        )
                    if getattr(replace_result, "ok", False):
                        pending_orders.mark(
                            local_id,
                            pending_orders.WORKING,
                            (
                                f"POSITION_VOLUME_SYNCED|"
                                f"REMAINING={current_volume:g}"
                            ),
                            lot=current_volume,
                            broker_order_quantity=replacement_quantity,
                        )
                        continue

                    # If resize is rejected, cancel the unsafe remainder.  Do
                    # not automatically resubmit until cancellation is certain.
                    try:
                        cancel_result = self.connector.cancel_order(
                            broker_order_id,
                            symbol=symbol,
                            paper_mode=False,
                        )
                    except Exception as exc:
                        cancel_result = SimpleNamespace(
                            ok=False,
                            message=str(exc),
                            error=str(exc),
                        )
                    pending_orders.mark(
                        local_id,
                        pending_orders.ERROR,
                        (
                            "OVER_CLOSE_GUARD|UNSAFE_LIMIT_CANCELLED"
                            if getattr(cancel_result, "ok", False)
                            else "OVER_CLOSE_GUARD|CANCEL_FAILED"
                        ),
                        broker_terminal=bool(
                            getattr(cancel_result, "ok", False)
                        ),
                    )
                    continue
            if broker_status in bad_statuses:
                pending_orders.mark(
                    local_id,
                    pending_orders.ERROR,
                    f"DNSE_{broker_status}",
                    broker_terminal=True,
                )

    def _send_pending_order(self, item):
        order_id = str(item.get("id", ""))
        action = str(item.get("action", "OPEN") or "OPEN").upper()
        item_mode = str(item.get("execution_mode", "PAPER") or "PAPER").upper()
        current_mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
        if item_mode != current_mode and action != "CLOSE":
            pending_orders.mark(order_id, pending_orders.PENDING, "WAITING_FOR_MATCHING_MODE")
            return
        expected_paper_mode = item_mode == "PAPER"
        symbol = str(item.get("symbol", "")).upper()
        side = str(item.get("side", "BUY")).upper()
        entry_price = float(item.get("entry_price", 0.0) or 0.0)
        target = str(item.get("target", "")).upper()
        order_kind = target if target in ("ATO", "ATC") and entry_price <= 0 else None
        if not expected_paper_mode and not self.connector.has_trading_token():
            pending_orders.mark(order_id, pending_orders.PENDING, "WAITING_FOR_TRADING_TOKEN")
            if not getattr(self, "_otp_prompt_scheduled", False):
                self._otp_prompt_scheduled = True

                def _prompt_pending_otp():
                    try:
                        self._ensure_trading_otp()
                    finally:
                        self._otp_prompt_scheduled = False

                self.after(0, _prompt_pending_otp)
            return
        if action == "CLOSE":
            self._send_pending_close(item)
            return
        try:
            result = self.trade_mgr.execute_manual_trade(
                side,
                item.get("preset") or getattr(config, "DEFAULT_PRESET", "SCALPING"),
                symbol,
                self.var_strict_mode.get(),
                self.latest_market_context.get(symbol, {}),
                float(item.get("lot", 0.0) or 0.0),
                float(item.get("tp", 0.0) or 0.0),
                float(item.get("sl", 0.0) or 0.0),
                bool(item.get("bypass_checklist", False)),
                str(item.get("note", "")).replace("TSL=", "") or self.get_current_tactic_string(),
                order_kind=order_kind,
                manual_entry_price=entry_price,
                entry_exit_tactic=str(item.get("manual_entry_tactic", "") or "OFF"),
                expected_paper_mode=expected_paper_mode,
            )
            if "SUCCESS" in str(result):
                dnse_order_id = str(result).split("|", 1)[1] if "|" in str(result) else ""
                pending_orders.mark(order_id, pending_orders.SENT, str(result), dnse_order_id=dnse_order_id)
                self._update_opportunity_order_result(item, "SENT", str(result))
                self.log_message(f"[LIMIT ORDER] Da gui {side} {symbol} -> {result}", target="manual")
            elif "MODE_CHANGED" in str(result):
                pending_orders.mark(order_id, pending_orders.PENDING, "WAITING_FOR_MATCHING_MODE")
            elif "MARKET_DATA_CONFIRM" in str(result) or "MARKET_DATA_DOWN" in str(result):
                # Lệnh hẹn không có người đứng trước UI để xác nhận dùng giá LO khi
                # feed hỏng; giữ nguyên hàng chờ, tuyệt đối không tự gửi bằng cache.
                pending_orders.mark(order_id, pending_orders.PENDING, "WAITING_FOR_MARKET_DATA")
            elif "ORDER_STATUS_UNKNOWN" in str(result):
                pending_orders.mark(order_id, pending_orders.UNKNOWN, str(result))
                self._update_opportunity_order_result(item, "UNKNOWN", str(result))
                self.log_message(
                    f"[LIMIT ORDER] Chưa xác định DNSE đã nhận {side} {symbol}; không tự gửi lại: {result}",
                    error=True,
                    target="manual",
                )
            else:
                pending_orders.mark(order_id, pending_orders.FAILED, str(result))
                self._update_opportunity_order_result(item, "FAILED", str(result))
                self.log_message(f"[LIMIT ORDER] Gui that bai {side} {symbol}: {result}", error=True, target="manual")
        except Exception as exc:
            pending_orders.mark(order_id, pending_orders.FAILED, str(exc))
            self._update_opportunity_order_result(item, "FAILED", str(exc))
            self.log_message(f"[LIMIT ORDER] Exception {side} {symbol}: {exc}", error=True, target="manual")

    def _send_pending_close(self, item):
        order_id = str(item.get("id", "") or "")
        ticket = str(item.get("position_ticket", "") or "")
        symbol = str(item.get("symbol", "") or "").upper()
        close_mode = str(item.get("entry_mode", "MARKET") or "MARKET").upper()
        limit_price = float(item.get("entry_price", 0.0) or 0.0)
        position = BotUI._find_open_position(self, ticket)
        if position is None:
            pending_orders.mark(
                order_id,
                pending_orders.CANCELLED,
                "POSITION_ALREADY_CLOSED",
            )
            self.log_message(
                f"[ĐÓNG LỆNH] Bỏ cache #{ticket}: vị thế không còn mở.",
                target="manual",
            )
            return
        try:
            position_manager = BotUI._trade_manager_for_position(self, position)
            if close_mode == "LIMIT" and not BotUI._position_is_paper(position):
                close_lot = abs(
                    float(getattr(position, "volume", 0.0) or 0.0)
                )
                close_side = (
                    "SELL"
                    if int(getattr(position, "type", 0) or 0) == 0
                    else "BUY"
                )
                if close_lot <= 0:
                    pending_orders.mark(
                        order_id,
                        pending_orders.CANCELLED,
                        "POSITION_ALREADY_CLOSED",
                    )
                    return
                result = position_manager.connector.place_close_limit_order(
                    symbol,
                    close_side,
                    close_lot,
                    limit_price,
                    comment=f"[USER]_CLOSE_LIMIT#{ticket}",
                )
                if getattr(result, "ok", False):
                    broker_order_id = str(
                        getattr(result, "order_id", "")
                        or getattr(result, "ticket", "")
                        or ""
                    )
                    pending_orders.mark(
                        order_id,
                        pending_orders.WORKING,
                        getattr(result, "message", "") or "LIMIT_WAITING_FILL",
                        dnse_order_id=broker_order_id,
                        lot=close_lot,
                        side=close_side,
                    )
                    self.log_message(
                        f"[ĐÓNG LỆNH] LIMIT {symbol} #{ticket} "
                        f"@{self._fmt_price(limit_price, symbol)} đã lên DNSE; chờ khớp.",
                        target="manual",
                    )
                    return
            else:
                position_manager.set_exit_reason(position.ticket, "Manual_Close")
                result = BotUI._close_position_for_mode(
                    self,
                    position,
                    str(item.get("close_comment", "") or "Manual_Close"),
                )
            if getattr(result, "ok", False):
                pending_orders.mark(
                    order_id,
                    pending_orders.SENT,
                    getattr(result, "message", "") or "CLOSED",
                )
                self.log_message(
                    f"[ĐÓNG LỆNH] Đã đóng {symbol} #{ticket}.",
                    target="manual",
                )
                return

            error = str(getattr(result, "error", "") or "")
            message = str(getattr(result, "message", "") or error or "CLOSE_FAILED")
            if error == "ORDER_STATUS_UNKNOWN":
                raw = getattr(result, "raw", {}) or {}
                request_tag = str(
                    raw.get("request_tag", "")
                    if isinstance(raw, dict)
                    else ""
                )
                pending_orders.mark(
                    order_id,
                    pending_orders.ERROR,
                    message,
                    unknown_submission=True,
                    request_tag=request_tag,
                )
                self.log_message(
                    f"[ĐÓNG LỆNH] Chưa xác định DNSE đã nhận LIMIT {symbol} #{ticket}; "
                    "không tự gửi lại.",
                    error=True,
                    target="manual",
                )
                return
            if error == "STOCK_NOT_SETTLED_T2":
                pending_orders.mark(
                    order_id,
                    pending_orders.PENDING,
                    message,
                    retry_at=time.time() + 3600.0,
                )
                self.log_message(
                    f"[ĐÓNG LỆNH] {symbol} #{ticket} đang chờ cổ phiếu về T+2.",
                    target="manual",
                )
                return
            pending_orders.mark(order_id, pending_orders.ERROR, message)
            self.log_message(
                f"[ĐÓNG LỆNH] Không đóng được {symbol} #{ticket}: {message}",
                error=True,
                target="manual",
            )
        except Exception as exc:
            pending_orders.mark(order_id, pending_orders.ERROR, str(exc))
            self.log_message(
                f"[ĐÓNG LỆNH] Lỗi đóng {symbol} #{ticket}: {exc}",
                error=True,
                target="manual",
            )

    def _update_opportunity_order_result(self, item, status, result):
        """Cập nhật bản lịch sử gợi ý đã kích hoạt theo kết quả gửi lệnh."""
        opportunity_id = str((item or {}).get("opportunity_id", "") or "")
        if not opportunity_id:
            return
        try:
            from core import signal_opportunities
            signal_opportunities.update_history(
                opportunity_id,
                order_status=str(status),
                order_result=str(result),
            )
        except Exception:
            pass

    @staticmethod
    def _position_is_paper(position_or_ticket):
        ticket = (
            getattr(position_or_ticket, "ticket", None)
            or getattr(position_or_ticket, "position_id", None)
            or position_or_ticket
            or ""
        )
        return str(ticket).upper().startswith("PAPER")

    @staticmethod
    def _positions_for_execution_mode(positions, paper_mode):
        """Keep account totals/PNL isolated while tables may show both modes."""
        expected_paper = bool(paper_mode)
        return [
            position
            for position in (positions or [])
            if BotUI._position_is_paper(position) == expected_paper
        ]

    @staticmethod
    def _merge_position_snapshots(*snapshots):
        merged = {}
        for positions in snapshots:
            for position in positions or ():
                ticket = str(
                    getattr(position, "ticket", "")
                    or getattr(position, "position_id", "")
                    or ""
                )
                if ticket:
                    merged[ticket] = position
        return list(merged.values())

    def _get_all_mode_positions(self, *, refresh_real=True, real_fallback=None):
        """Return PAPER + REAL positions without changing the order-entry mode."""
        paper_positions = []
        real_positions = list(real_fallback or [])
        try:
            paper_positions = list(
                self.connector.get_all_open_positions(paper_mode=True) or []
            )
        except TypeError:
            current = list(real_positions)
            if not current:
                current = list(
                    self.__dict__.get("_ui_all_positions_snapshot", []) or []
                )
            if not current:
                current = list(self.connector.get_all_open_positions() or [])
            paper_positions = (
                list(current)
                if bool(getattr(config, "PAPER_TRADING", True))
                else [p for p in current if BotUI._position_is_paper(p)]
            )
            if not real_positions:
                real_positions = [
                    p for p in current if not BotUI._position_is_paper(p)
                ]
        except Exception:
            paper_positions = [
                p
                for p in list(
                    self.__dict__.get("_ui_all_positions_snapshot", []) or []
                )
                if BotUI._position_is_paper(p)
            ]

        if refresh_real:
            try:
                real_positions = list(
                    self.connector.get_all_open_positions(paper_mode=False) or []
                )
            except TypeError:
                current = list(self.connector.get_all_open_positions() or [])
                real_positions = [
                    p for p in current if not BotUI._position_is_paper(p)
                ]
            except Exception:
                real_positions = list(real_fallback or [])

        return BotUI._merge_position_snapshots(
            paper_positions,
            real_positions,
        )

    def _find_open_position(self, ticket):
        ticket = str(ticket or "")
        position = next(
            (
                p
                for p in list(
                    self.__dict__.get("_ui_all_positions_snapshot", []) or []
                )
                if str(getattr(p, "ticket", "") or "") == ticket
                or str(getattr(p, "position_id", "") or "") == ticket
            ),
            None,
        )
        if position is not None:
            return position
        try:
            positions = self.connector.get_all_open_positions(
                paper_mode=ticket.upper().startswith("PAPER")
            )
        except TypeError:
            positions = self.connector.get_all_open_positions()
        except Exception:
            positions = []
        return next(
            (
                p
                for p in positions or []
                if str(getattr(p, "ticket", "") or "") == ticket
                or str(getattr(p, "position_id", "") or "") == ticket
            ),
            None,
        )

    def _modify_position_for_mode(self, position, sl, tp):
        try:
            return self.connector.modify_position(
                position,
                sl,
                tp,
                paper_mode=BotUI._position_is_paper(position),
            )
        except TypeError:
            return self.connector.modify_position(position, sl, tp)

    def _close_position_for_mode(self, position, comment=""):
        try:
            return self.connector.close_position(
                position,
                comment,
                paper_mode=BotUI._position_is_paper(position),
            )
        except TypeError:
            return self.connector.close_position(position, comment)

    def _trade_manager_for_mode(self, paper_mode):
        paper_mode = bool(paper_mode)
        if paper_mode == bool(getattr(config, "PAPER_TRADING", True)):
            return self.trade_mgr
        if not hasattr(self, "checklist_mgr"):
            return self.trade_mgr

        mode = "PAPER" if paper_mode else "REAL"
        managers = getattr(self, "_inactive_trade_managers", None)
        if managers is None:
            managers = {}
            self._inactive_trade_managers = managers
        manager = managers.get(mode)
        if manager is None:
            bound_connector = ModeBoundConnector(self.connector, paper_mode)

            def _inactive_log(message, error=False, target=None, _mode=mode):
                self.log_message(
                    f"[NỀN {_mode}] {message}",
                    error=error,
                    target=target or "bot",
                )

            manager = TradeManager(
                bound_connector,
                self.checklist_mgr,
                log_callback=_inactive_log,
            )
            manager.state = load_state(paper=paper_mode)
            managers[mode] = manager
        return manager

    def _trade_manager_for_position(self, position):
        return BotUI._trade_manager_for_mode(
            self,
            BotUI._position_is_paper(position),
        )

    def _render_cached_ui_snapshot(self, sym):
        """Render account state while market-data endpoints are asleep."""
        import core.storage_manager as storage_manager

        paper_mode = bool(getattr(config, "PAPER_TRADING", True))
        if paper_mode:
            acc = self.connector.get_account_info()
            all_positions = list(self.connector.get_all_open_positions() or [])
            cached_orders = list(
                getattr(self.connector, "_orders_cache", []) or []
            )
            open_orders = [
                order
                for order in cached_orders
                if not sym
                or str(order.get("symbol", "")).upper() == str(sym).upper()
            ]
            now = time.time()
            closed_sync_seconds = max(
                60.0,
                float(
                    getattr(config, "DNSE_CLOSED_PRIVATE_SYNC_SECONDS", 300.0)
                    or 300.0
                ),
            )
            if (
                now
                - float(self.__dict__.get("_closed_private_sync_ts", 0.0) or 0.0)
                >= closed_sync_seconds
            ):
                self._closed_private_sync_ts = now
                try:
                    self.connector.get_all_open_positions(paper_mode=False)
                except Exception:
                    pass
                try:
                    open_orders = list(
                        self.connector.get_orders(
                            paper_mode=False,
                            symbol=sym,
                            orderCategory=getattr(
                                self.connector,
                                "order_category",
                                "NORMAL",
                            ),
                        )
                    )
                except Exception:
                    pass
        else:
            cached_acc = getattr(self.connector, "_account_cache", None)
            cached_positions = list(getattr(self.connector, "_positions_cache", []) or [])
            cached_orders = list(getattr(self.connector, "_orders_cache", []) or [])
            # Ngoài phiên chỉ đồng bộ private REST theo nhịp chậm. Bản cũ gọi
            # balances/positions/orders mỗi 5 giây dù tên hàm là "render cache",
            # làm DNSE maintenance cuối ngày tạo retry/log không cần thiết.
            now = time.time()
            closed_sync_seconds = max(
                60.0,
                float(
                    getattr(config, "DNSE_CLOSED_PRIVATE_SYNC_SECONDS", 300.0)
                    or 300.0
                ),
            )
            refresh_private = (
                now
                - float(self.__dict__.get("_closed_private_sync_ts", 0.0) or 0.0)
                >= closed_sync_seconds
            )
            if refresh_private:
                self._closed_private_sync_ts = now
                try:
                    acc = self.connector.get_account_info() or cached_acc
                except Exception:
                    acc = cached_acc
                try:
                    all_positions = list(self.connector.get_all_open_positions() or [])
                except Exception:
                    all_positions = cached_positions
                try:
                    refreshed_orders = list(
                        self.connector.get_orders(
                            symbol=sym,
                            orderCategory=getattr(
                                self.connector, "order_category", "NORMAL"
                            ),
                        )
                        if hasattr(self.connector, "get_orders")
                        else []
                    )
                    open_orders = refreshed_orders
                except Exception:
                    open_orders = [
                        order
                        for order in cached_orders
                        if not sym
                        or str(order.get("symbol", "")).upper()
                        == str(sym).upper()
                    ]
            else:
                acc = cached_acc
                all_positions = cached_positions
                open_orders = [
                    order
                    for order in cached_orders
                    if not sym
                    or str(order.get("symbol", "")).upper()
                    == str(sym).upper()
                ]
            acc = acc or {
                "login": getattr(self.connector, "account_no", ""),
                "balance": 0.0,
                "equity": 0.0,
                "margin": 0.0,
                "free_margin": 0.0,
            }

        if paper_mode:
            real_fallback = list(
                getattr(self.connector, "_positions_cache", []) or []
            )
        else:
            real_fallback = list(all_positions)
        all_positions = BotUI._get_all_mode_positions(
            self,
            refresh_real=False,
            real_fallback=real_fallback,
        )

        tick_data = data_engine.fetch_realtime_tick(sym)  # outside session: cache-only by contract
        if not tick_data:
            cached_ctx = self.latest_market_context.get(sym, {}) or {}
            cached_price = float(cached_ctx.get("current_price", 0.0) or 0.0)
            if cached_price <= 0:
                matching = [p for p in all_positions if str(getattr(p, "symbol", "")).upper() == str(sym).upper()]
                if matching:
                    cached_price = float(
                        getattr(matching[0], "price_current", 0.0)
                        or getattr(matching[0], "price_open", 0.0)
                        or 0.0
                    )
            if cached_price > 0:
                tick_data = {
                    "symbol": sym,
                    "last": cached_price,
                    "bid": cached_price,
                    "ask": cached_price,
                    "spread": 0.0,
                    "synthetic_quote": True,
                    "timestamp": float(cached_ctx.get("tick_timestamp", 0.0) or 0.0),
                }

        tick = None
        if tick_data:
            bid_val = float(tick_data.get("bid", tick_data.get("last", 0.0)) or 0.0)
            ask_val = float(tick_data.get("ask", tick_data.get("last", bid_val)) or bid_val)
            last_val = float(tick_data.get("last", ask_val or bid_val) or 0.0)
            self.latest_market_context.setdefault(sym, {}).update(
                {
                    "bid": bid_val,
                    "ask": ask_val,
                    "current_price": last_val,
                    "spread": float(tick_data.get("spread", 0.0) or 0.0),
                    "synthetic_quote": True,
                    "tick_timestamp": tick_data.get("timestamp", 0.0),
                }
            )
            tick = SimpleNamespace(bid=bid_val, ask=ask_val, synthetic=True)

        magics = storage_manager.get_magic_numbers()
        self._ui_all_positions_snapshot = list(all_positions)
        # The running tables are also an account reconciliation view.  Keep
        # broker positions opened from DNSE Mobile visible even though they are
        # intentionally not enrolled in this app's TSL manager.
        positions = list(all_positions)
        cached_spread = float((self.latest_market_context.get(sym, {}) or {}).get("spread", 0.0) or 0.0)
        pos_extras = {}
        for position in positions:
            derivative = self._is_derivative_symbol(position.symbol)
            pos_extras[str(position.ticket)] = {
                "c_size": float(
                    getattr(config, "DNSE_POINT_VALUE", 100000.0)
                    if derivative
                    else getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0)
                ),
                "spread": cached_spread if str(position.symbol).upper() == str(sym).upper() else 0.0,
                "comm": float(getattr(position, "commission", 0.0) or 0.0),
            }
        try:
            checks = self.checklist_mgr.run_pre_trade_checks(
                acc, self.trade_mgr.state, sym, self.var_strict_mode.get()
            )
        except Exception:
            checks = {"passed": False, "checks": []}
        self.after(
            0,
            self.update_ui,
            acc,
            self.trade_mgr.state,
            checks,
            tick,
            getattr(config, "DEFAULT_PRESET", "SCALPING"),
            sym,
            positions,
            open_orders,
            pos_extras,
        )

    def _update_inactive_mode_trades(self):
        """Manage open positions in the mode that is not currently shown."""
        inactive_paper = not bool(getattr(config, "PAPER_TRADING", True))
        manager = BotUI._trade_manager_for_mode(self, inactive_paper)
        manager.state = load_state(paper=inactive_paper)
        return manager.update_running_trades(
            "STANDARD",
            self.latest_market_context,
        )

    def bg_update_loop(self):
        while self.running:
            if self.__dict__.get("_mode_switching", False):
                time.sleep(0.1)
                continue
            try:
                sym = self.cbo_symbol.get()
                # Advisor là tác vụ báo cáo độc lập với phiên giá. Đặt lịch ở đây
                # để giờ hẹn vẫn chạy khi thị trường nghỉ/đóng cửa.
                self.run_advisor_triggers_tick()
                from core.market_hours import (
                    is_symbol_network_window_open,
                    is_symbol_trade_window_open,
                    seconds_until_network_open,
                )
                network_open = is_symbol_network_window_open(sym, include_preopen=True)[0]
                trade_open = is_symbol_trade_window_open(sym)[0]
                if not network_open:
                    try:
                        data_engine.set_stream_symbols([sym])
                    except Exception:
                        pass
                elif not trade_open:
                    # Pre-open warm-up: establish WS only. Do not poll balances/positions/OHLC.
                    try:
                        data_engine.set_stream_symbols([sym])
                    except Exception:
                        pass
                if not trade_open:
                    self._render_cached_ui_snapshot(sym)
                    self._run_pending_order_scheduler()  # local-only; claim_due remains closed
                    wait_s = min(5.0, max(1.0, seconds_until_network_open([sym]))) if not network_open else 5.0
                    time.sleep(wait_s)
                    continue
                new_map = self.trade_mgr.update_running_trades(
                    "STANDARD", self.latest_market_context
                )
                self.tsl_states_map.update(new_map)
                inactive_map = self._update_inactive_mode_trades()
                self.tsl_states_map.update(inactive_map)

                acc = self.connector.get_account_info()
                
                import core.storage_manager as storage_manager
                
                # [FIX V6.9.5] Nếu mất kết nối hoặc đang Swap tài khoản trên DNSE -> Ép reconnect
                if acc is None:
                    self.connector._is_connected = False
                    self.connector.connect()
                    acc = self.connector.get_account_info()
                    
                if acc:
                    current_acc_id = str(acc['login'])
                    if current_acc_id != storage_manager._active_account_id:
                        storage_manager.set_active_account(current_acc_id)
                        self.log_message(f"🔄 DNSE ĐỔI TÀI KHOẢN SANG {current_acc_id}. ĐANG TỰ ĐỘNG CHUYỂN SINH WORKSPACE...", target="bot")
                        
                        # Cập nhật biến global
                        global TSL_SETTINGS_FILE, PRESETS_FILE, BRAIN_SETTINGS_FILE
                        TSL_SETTINGS_FILE = os.path.join(storage_manager._active_account_dir, "tsl_settings.json")
                        PRESETS_FILE = os.path.join(storage_manager._active_account_dir, "presets_config.json")
                        BRAIN_SETTINGS_FILE = storage_manager.BRAIN_FILE
                        self.group_status_tracker = storage_manager.load_group_status_tracker()
                        
                        # Reload account-scoped state instead of creating an incomplete in-memory state.
                        self.trade_mgr.state = load_state()
                        
                        # Reload config trên UI thread
                        self.after(100, self.load_settings)

                tick = None
                try:
                    tick_data = data_engine.fetch_realtime_tick(sym)
                    if tick_data:
                        bid_val = float(tick_data.get("bid", tick_data.get("last", 0.0)) or 0.0)
                        ask_val = float(tick_data.get("ask", tick_data.get("last", bid_val)) or bid_val)
                        last_val = float(tick_data.get("last", ask_val or bid_val) or 0.0)
                        ctx = self.latest_market_context.setdefault(sym, {})
                        ctx.update(
                            {
                                "bid": bid_val,
                                "ask": ask_val,
                                "current_price": last_val or ask_val or bid_val,
                                "spread": float(tick_data.get("spread", max(0.0, ask_val - bid_val)) or 0.0),
                                "synthetic_quote": bool(tick_data.get("synthetic_quote", False)),
                                "tick_timestamp": tick_data.get("timestamp", time.time()),
                            }
                        )

                        class LiveTick:
                            def __init__(self, bid, ask, synthetic=False):
                                self.bid = bid
                                self.ask = ask
                                self.synthetic = synthetic

                        tick = LiveTick(bid_val, ask_val, bool(tick_data.get("synthetic_quote", False)))
                except Exception as e:
                    tick = None
                    logger.error(f"[TICK_ERROR] {sym}: {e}", exc_info=True)
                magics = storage_manager.get_magic_numbers()
                
                all_pos = BotUI._get_all_mode_positions(
                    self,
                    refresh_real=True,
                )
                self._ui_all_positions_snapshot = list(all_pos)
                # Show every broker position.  Ownership filtering belongs to
                # TradeManager (TSL), not to the account/running-position UI.
                pos = list(all_pos)
                open_orders = []
                try:
                    if hasattr(self.connector, "get_orders"):
                        open_orders = self.connector.get_orders(
                            paper_mode=False,
                            symbol=sym,
                            all_symbols=True,
                            orderCategory=getattr(
                                self.connector,
                                "order_category",
                                "NORMAL",
                            ),
                        )
                except Exception:
                    open_orders = []
                # [FREEZE FIX] Gom giá/spread/phí cho mọi vị thế NGAY TẠI ĐÂY (thread nền),
                # để update_ui trên thread UI không phải gọi mạng -> không đơ.
                pos_extras = self._collect_position_market(pos)
                self.after(
                    0,
                    self.update_ui,
                    acc,
                    self.trade_mgr.state,
                    self.checklist_mgr.run_pre_trade_checks(
                        acc, self.trade_mgr.state, sym, self.var_strict_mode.get()
                    ),
                    tick,
                    getattr(config, "DEFAULT_PRESET", "SCALPING"),
                    sym,
                    pos,
                    open_orders,
                    pos_extras,
                )
                self._run_pending_order_scheduler()
            except Exception as e:
                main_logger.exception("[BG_LOOP ERROR] %s", e)
            time.sleep(config.LOOP_SLEEP_SECONDS)

    def update_ui(self, acc, state, check_res, tick, preset, sym, positions, open_orders=None, pos_extras=None):
        # [FREEZE FIX] pos_extras do bg_update_loop gom sẵn (thread nền). Nếu caller khác
        # (vd đường tạo lệnh hẹn thủ công) không truyền -> tự gom ở đây để giữ hành vi cũ.
        self._update_contract_dates_label(sym)
        current_mode_positions = self._positions_for_execution_mode(
            positions,
            getattr(config, "PAPER_TRADING", True),
        )
        if pos_extras is None:
            pos_extras = self._collect_position_market(positions)
        sym_count = len(self.brain_active_symbols)
        if "SLEEPING" in self.brain_status:
            rem = int(self.brain_wakeup_time - time.time())
            if rem > 0:
                self.lbl_brain_status.configure(
                    text=f"BRAIN: SLEEP ({rem}s) [{sym_count} Sym]",
                    text_color="#2196F3",
                )
            else:
                self.lbl_brain_status.configure(
                    text=f"BRAIN: SYNC... [{sym_count} Sym]", text_color=COL_WARN
                )
        elif self.brain_status in ["HEALTHY", "MONITORING"]:
            self.lbl_brain_status.configure(
                text=f"BRAIN: ONLINE [{sym_count} Sym]", text_color=COL_GREEN
            )
        else:
            self.lbl_brain_status.configure(
                text=f"BRAIN: {self.brain_status}", text_color=COL_RED
            )

        sym_ctx = self.latest_market_context.get(sym, {})

        # [SANDBOX-FETCH] Preset cần data kỹ thuật (Sandbox/Swing/FIB/Pullback) mà context
        # thiếu atr/swing -> fetch nến on-demand (thread nền, throttle 15s/mã).
        try:
            _params_active = config.PRESETS.get(preset, {})
            from core.market_hours import is_symbol_trade_window_open
            if (
                sym
                and is_symbol_trade_window_open(sym)[0]
                and not self._manual_context_ready(_params_active, sym_ctx, sym)
            ):
                self._schedule_symbol_context_fetch(sym)
        except Exception:
            pass

        if sym_ctx:
            # 1. Đọc khung thời gian Ngài đang chọn xem trên Dashboard
            selected_tf = getattr(
                self, "var_dashboard_tf", tk.StringVar(value="G1")
            ).get()

            # 2. Lấy dữ liệu kỹ thuật theo khung đó
            sh = sym_ctx.get(f"swing_high_{selected_tf}", "--")
            sl = sym_ctx.get(f"swing_low_{selected_tf}", "--")
            atr = sym_ctx.get(f"atr_{selected_tf}", "--")

            # Ép UI chỉ đọc Trend của Group đang được chọn ở ComboBox
            tr = sym_ctx.get(f"trend_{selected_tf}", "NONE")

            mode = sym_ctx.get("market_mode", "ANY")
            mode_src = sym_ctx.get("mode_source", "NONE")

            # 3. Đổ dữ liệu vào DÒNG 1
            m_color = (
                COL_GREEN if tr == "UP" else (COL_RED if tr == "DOWN" else "#78909C")
            )
            self.lbl_market_mode.configure(
                text=f"Mode: {mode} (by {mode_src}) | Trend: {tr}  (xem trước)", text_color=m_color
            )

            # 4. Đổ dữ liệu vào DÒNG 2: Thông số (Cái Label lbl_market_context)
            if tr == "UP":
                ctx_color = COL_GREEN
            elif tr == "DOWN":
                ctx_color = COL_RED
            else:
                ctx_color = "white"

            if atr == 0.0 or atr == "--":
                self.lbl_market_context.configure(
                    text="Syncing Data...", text_color="#FFA500"
                )
            else:
                sh_str = f"{sh:.2f}" if isinstance(sh, (int, float)) else "--"
                sl_str = f"{sl:.2f}" if isinstance(sl, (int, float)) else "--"
                atr_str = f"{atr:.2f}" if isinstance(atr, (int, float)) else "--"

                self.lbl_market_context.configure(
                    text=f"H: {sh_str} | L: {sl_str} | ATR: {atr_str}",
                    text_color=ctx_color,
                )
        else:
            # [FIX CORE UI]: Làm sạch nhãn nếu không có dữ liệu (Ví dụ đổi sang đồng Coin không có trong Watchlist)
            self.lbl_market_mode.configure(
                text=f"Mode: -- | Trend: --", text_color="white"
            )
            self.lbl_market_context.configure(
                text="H: -- | L: -- | ATR: --", text_color="white"
            )

        d = self.var_direction.get()
        cur_tactic_str = self.get_current_tactic_string()

        balance = acc["balance"] if acc else 1.0
        if balance == 0:
            balance = 1.0

        if acc:
            equity_text = self._fmt_money(acc["equity"])
            self._set_money_label(
                self.lbl_equity,
                equity_text,
                base_size=32,
                min_size=18,
                comfortable_chars=8,
            )
            if hasattr(self, "lbl_money_unit_note"):
                self.lbl_money_unit_note.configure(text=money_unit_note())
            # Dòng nhỏ: rõ đang xem ví CƠ SỞ (tiền) hay PHÁI SINH (ký quỹ) theo mã đang chọn.
            _sym_cur = self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else ""
            if _sym_cur and not settlement.is_cash_stock(_sym_cur):
                _rtt = acc.get("rtt")
                _rtt_txt = f" · RTT {float(_rtt):.0f}%" if _rtt not in (None, "") else ""
                # Ký quỹ THẬT của ví phái sinh (remainSecure); 0 = chưa chuyển tiền -> chưa đánh được.
                _wallet = f"PHÁI SINH · KQ {self._fmt_money(acc.get('deriv_avail', 0.0))}{_rtt_txt}"
            else:
                _wallet = f"CƠ SỞ · Tiền {self._fmt_money(acc.get('stock_cash', acc.get('cash_available', acc.get('balance', 0.0))))}"
            account_text = f"ID: {acc['login']}  ·  {_wallet}"
            self._set_money_label(
                self.lbl_acc_info,
                account_text,
                base_size=9,
                min_size=7,
                comfortable_chars=42,
                weight="",
            )
        # Danh mục CKCS + tách Tiền/CP/Tổng (read-only). Đặt sau khi set equity để
        # ghi đè bằng tổng tự tính (balances cổ phiếu không trả sẵn NAV).
        self.update_portfolio_table(
            acc,
            current_mode_positions,
        )
        pnl, realized_pnl, floating_pnl = self._combined_display_pnl(
            state.get("pnl_today", 0.0), current_mode_positions
        )
        pnl_text = f"PNL: {self._fmt_money(pnl, signed=True)}"
        self._set_money_label(
            self.lbl_stats,
            pnl_text,
            base_size=17,
            min_size=11,
            comfortable_chars=14,
            text_color=COL_GREEN if pnl >= 0 else COL_RED,
        )

        # Indicator phiên giao dịch theo symbol đang chọn (ATO/MỞ/NGHỈ TRƯA/ATC/ĐÓNG).
        if hasattr(self, "lbl_session"):
            try:
                from core.market_hours import market_session_phase, market_now_hm
                phase, label = market_session_phase(sym)
                color = {
                    "OPEN": COL_GREEN,
                    "ATO": "#26C6DA",
                    "ATC": "#FFB300",
                    "LUNCH": "#FFB300",
                    "WEEKEND": COL_RED,
                    "CLOSED": COL_RED,
                }.get(phase, "#90A4AE")
                self.lbl_session.configure(text=f"PHIÊN: {label} · {market_now_hm()}", text_color=color)
                # Token THẬT: hiện giờ còn lại + TỰ TẮT AUTO_TRADE khi hết (chống bot gửi lệnh fail âm thầm).
                if not getattr(config, "PAPER_TRADING", True) and getattr(self, "connector", None) is not None:
                    left = self.connector.trading_token_seconds_left()
                    persistent = getattr(self.connector, "trading_token_persistent", False) and bool(self.connector.trading_token)
                    if persistent:
                        self._token_expired_alerted = False
                        self.lbl_session.configure(text=f"PHIÊN: {label} · {market_now_hm()} · TOKEN: OK", text_color=color)
                    elif left > 0:
                        self._token_expired_alerted = False
                        self.lbl_session.configure(text=f"PHIÊN: {label} · {market_now_hm()} · TOKEN {left/3600:.1f}h", text_color=color)
                    else:
                        self.lbl_session.configure(text=f"PHIÊN: {label} · {market_now_hm()} · TOKEN HẾT", text_color=COL_RED)
                        if getattr(config, "AUTO_TRADE_ENABLED", False):
                            config.AUTO_TRADE_ENABLED = False
                            if hasattr(self, "var_auto_trade"):
                                self.var_auto_trade.set(False)
                                self.var_bot_ckps.set(False)
                                self.var_bot_ckcs.set(False)
                                self._refresh_bot_lights()
                            if not getattr(self, "_token_expired_alerted", False):
                                self.log_message("⛔ TOKEN HẾT HẠN — đã TẮT AUTO_TRADE. Nhập lại OTP để chạy tiếp.", error=True, target="bot")
                                self._token_expired_alerted = True
                # Luôn hiện đủ NORMAL/ATO/ATC cho dễ chọn/test. ATO/ATC chỉ THỰC SỰ khớp
                # trong phiên đấu giá (xem chỉ báo PHIÊN ở trên); bot AUTO tự chọn theo giờ.
                # Chỉ set values 1 lần (gọi mỗi tick sẽ làm dropdown đang mở bị đóng -> nhấp nháy).
                if hasattr(self, "cbo_trade_mode"):
                    all_modes = ["NORMAL", "ATO", "ATC"]
                    if getattr(self, "_last_trade_mode_values", None) != all_modes:
                        self.cbo_trade_mode.configure(values=all_modes)
                        self._last_trade_mode_values = list(all_modes)
                self.refresh_limit_order_hint()
            except Exception:
                pass

        # Cổ phiếu (CKCS) KHÔNG bán khống: khóa nút SELL khi không giữ CK đã về.
        if hasattr(self, "btn_dir_sell"):
            try:
                if settlement.is_cash_stock(sym):
                    rows = [{
                        "symbol": getattr(p, "symbol", ""),
                        "type": int(getattr(p, "type", 0) or 0),
                        "volume": float(getattr(p, "volume", 0.0) or 0.0),
                        "settle_date": (getattr(p, "raw", {}) or {}).get("settle_date", ""),
                    } for p in current_mode_positions if str(getattr(p, "symbol", "") or "").upper() == str(sym or "").upper()]
                    sellable = settlement.available_to_sell(rows, sym)
                    if sellable <= 0:
                        if self.var_direction.get() == "SELL":
                            self.on_direction_change("BUY")
                        self.btn_dir_sell.configure(state="disabled", fg_color="#2a2a2a")
                    else:
                        self.btn_dir_sell.configure(state="normal")
                else:
                    self.btn_dir_sell.configure(state="normal")
            except Exception:
                pass
        # FEE label sẽ được cập nhật sau vòng lặp positions (cần data lệnh đang mở)

        is_derivative = self._is_derivative_symbol(sym)
        qty_unit = self._quantity_unit(sym)
        qty_label = self._quantity_label(sym)
        if hasattr(self, "lbl_manual_qty_title"):
            self.lbl_manual_qty_title.configure(text=qty_label)
        cur_price = 0.0
        c_size = float(getattr(config, "DNSE_POINT_VALUE", 100000.0) or 100000.0) if is_derivative else float(getattr(config, "DNSE_STOCK_PRICE_VALUE", 1000.0) or 1000.0)
        point = float(getattr(config, "DNSE_PRICE_POINT", 0.1) or 0.1)
        vol_min, vol_max, vol_step = 1.0, (config.MAX_LOT_SIZE if is_derivative else 1000000.0), getattr(config, "LOT_STEP", 1.0)
        
        # [DNSE TICK FALLBACK] Lấy giá real-time từ market context (bid/ask/last từ DNSE)
        if tick is None and sym_ctx and ("current_price" in sym_ctx or "bid" in sym_ctx):
            bid_val = float(sym_ctx.get("bid", sym_ctx.get("current_price", 0)))
            ask_val = float(sym_ctx.get("ask", sym_ctx.get("current_price", 0)))
            # Không có bid/ask thật trong context -> quote dựng từ current_price = synthetic.
            synthetic_val = bool(sym_ctx.get("synthetic_quote", False)) or "bid" not in sym_ctx or "ask" not in sym_ctx
            class MockTick:
                def __init__(self, b, a, synthetic=False):
                    self.bid = b
                    self.ask = a
                    self.synthetic = synthetic
            tick = MockTick(bid_val, ask_val, synthetic_val)

        if tick:
            cur_price = tick.ask if d == "BUY" else tick.bid
            tick_raw = getattr(tick, "raw", {}) or {}
            tick_state = str(
                tick_raw.get("market_state")
                or sym_ctx.get("market_state")
                or self._shared_market_state()
            ).upper()
            is_stale_price = bool(tick_raw.get("stale")) or tick_state in (
                "RECOVERING", "MARKET DATA DOWN", "MARKET_DATA_DOWN"
            )
            self.lbl_dashboard_price.configure(
                text=self._fmt_price(cur_price, sym),
                text_color="#FFB300" if is_stale_price else (COL_GREEN if cur_price >= self.last_price_val else COL_RED),
                font=("Roboto", 28, "bold"),
            )
            self.last_price_val = cur_price
            # Trần/TC/Sàn (nếu DNSE trả về) — hiện cho cả CKCS lẫn phái sinh.
            if hasattr(self, "lbl_band_info"):
                ce = float(getattr(tick, "ceiling", 0.0) or 0.0)
                fl = float(getattr(tick, "floor", 0.0) or 0.0)
                ref = float(getattr(tick, "reference", 0.0) or 0.0)
                if ce > 0 or fl > 0:
                    self.lbl_band_info.configure(
                        text=f"Trần {self._fmt_price(ce, sym)}  ·  TC {self._fmt_price(ref, sym)}  ·  Sàn {self._fmt_price(fl, sym)}"
                    )
                else:
                    self.lbl_band_info.configure(text="")
            try:
                # poll_tick=False: giá đã lấy ở fetch_realtime_tick phía trên, đây chỉ cần
                # cỡ hợp đồng/lô (tĩnh) -> khỏi poll lại endpoint phái sinh nóng, tránh 429 + đơ UI.
                from core.market_hours import is_symbol_trade_window_open
                s_info = (
                    self.connector.get_symbol_info(sym, poll_tick=False)
                    if self.connector and is_symbol_trade_window_open(sym)[0]
                    else None
                )
            except Exception:
                s_info = None
            if s_info:
                c_size, point = s_info.trade_contract_size, s_info.point
                vol_min, vol_max, vol_step = s_info.volume_min, s_info.volume_max, s_info.volume_step
        else:
            # Không có tick: ngoài giờ / chưa có dữ liệu / mã phái sinh chưa có giá khớp.
            ctx_px = float(sym_ctx.get("current_price", 0) or 0) if sym_ctx else 0.0
            if ctx_px > 0:
                cur_price = ctx_px
                self.lbl_dashboard_price.configure(
                    text=self._fmt_price(cur_price, sym),
                    text_color="#9E9E9E",
                    font=("Roboto", 28, "bold"),
                )
            else:
                from core.market_hours import market_session_phase

                phase, phase_label = market_session_phase(sym)
                if phase == "LUNCH":
                    waiting_text = "CHỜ GIÁ · NGHỈ TRƯA"
                    waiting_color = "#FFB300"
                elif phase in {"WEEKEND", "HOLIDAY", "CLOSED"}:
                    waiting_text = f"CHỜ GIÁ · {phase_label.upper()}"
                    waiting_color = "#9E9E9E"
                else:
                    waiting_text = "CHỜ DỮ LIỆU GIÁ"
                    waiting_color = "#FFB300"
                self.lbl_dashboard_price.configure(
                    text=waiting_text,
                    text_color=waiting_color,
                    font=("Roboto", 14, "bold"),
                )
            if hasattr(self, "lbl_band_info"):
                self.lbl_band_info.configure(text="")

        for item in check_res["checks"]:
            name, stt, msg = item["name"], item["status"], item["msg"]
            if name in self.check_labels:
                self.check_labels[name].configure(
                    text=f"{'✔' if stt == 'OK' else '✖'} {name}: {msg}",
                    text_color=COL_GREEN
                    if stt == "OK"
                    else (COL_WARN if stt == "WARN" else COL_RED),
                )

        if tick and acc:
            params = config.PRESETS.get(preset, config.PRESETS["SCALPING"])
            current_risk_pct = params.get("RISK_PERCENT", 0.3)
            sl_pct_display = params.get("SL_PERCENT", 0.0)
            tp_r_display = params.get("TP_RR_RATIO", 0.0)

            # --- ĐOẠN CẦN THAY THẾ BẮT ĐẦU TỪ ĐÂY ---
            try:
                mlot = float(self.var_manual_lot.get() or 0)
                me = self._price_input_to_internal(self.var_manual_entry.get(), sym)
                msl = self._price_input_to_internal(self.var_manual_sl.get(), sym)
                mtp = self._price_input_to_internal(self.var_manual_tp.get(), sym)
            except:
                mlot, me, msl, mtp = 0.0, 0.0, 0.0
            entry_calc_price = me if me > 0 else cur_price

            # Dùng đúng một bộ giải mức giá cho Dashboard, Preview và Telegram.
            # Trước đây Dashboard đọc nhánh USE_SWING_TP cũ nên có thể hiện
            # TARGET LỖI trong khi bộ Preview chuẩn đã có Swing/FIB/R hợp lệ.
            preview_info = SimpleNamespace(
                trade_contract_size=c_size,
                volume_min=vol_min,
                volume_max=vol_max,
                volume_step=vol_step,
                point=point,
            )
            preview_setup = self._resolve_manual_setup_preview(
                sym,
                d,
                preset,
                sym_ctx,
                manual_values={
                    "entry": me,
                    "lot": mlot,
                    "sl": msl,
                    "tp": mtp,
                },
                tick=tick,
                sym_info=preview_info,
                account=acc,
            )
            p_sl = self._safe_float(preview_setup.get("sl"), 0.0)
            p_tp = self._safe_float(preview_setup.get("tp"), 0.0)
            active_sl_dist = abs(entry_calc_price - p_sl)
            sl_label = str(preview_setup.get("sl_source") or "PERCENT")
            tp_label = str(
                preview_setup.get("tp_source_label")
                or preview_setup.get("tp_source")
                or "--"
            )
            sl_missing = p_sl <= 0
            swing_tp_missing = p_tp <= 0

            # [SANDBOX-FETCH] Preset đòi SL kỹ thuật mà đang phải fallback Percent -> báo rõ (màu cam)
            _req_sl_mode = self._manual_rule_mode(params, "MANUAL_SL_MODE", "USE_SWING_SL", "PERCENT")
            _sl_head = sl_label.split(":", 1)[0]
            _sl_fallback = (
                msl <= 0
                and _req_sl_mode in ("SANDBOX", "SWING_REJECTION", "SWING_STRUCTURE")
                and _sl_head == "PERCENT"
            )
            if _sl_fallback:
                if self._manual_context_ready(params, sym_ctx, sym):
                    # Data đủ mà vẫn fallback -> swing nằm sai phía so với giá
                    _state = "SL swing sai phía → Percent"
                elif self._ctx_fetch_pending(sym):
                    _state = "đang tải data…"
                else:
                    _state = "thiếu data → Percent"
                self.lbl_head_sl.configure(text=f"STOPLOSS ({_req_sl_mode}: {_state})", text_color=COL_WARN)
            else:
                self.lbl_head_sl.configure(text=f"STOPLOSS ({_sl_head})", text_color=COL_RED)
            self.lbl_head_tp.configure(text=f"TARGET ({tp_label})")
            try:
                from core.entry_exit_engine import evaluate_entry_exit, format_decision

                brain = self.trade_mgr._get_brain_settings(sym)
                ee_decision = evaluate_entry_exit(
                    sym,
                    d,
                    entry_calc_price,
                    sym_ctx or {},
                    brain.get("entry_exit", {}),
                )
                self.latest_entry_exit_decisions[sym] = ee_decision
                if hasattr(self, "lbl_entry_exit_preview"):
                    self.lbl_entry_exit_preview.configure(
                        text=format_decision(ee_decision),
                        text_color=COL_GREEN
                        if ee_decision.get("status") == "READY"
                        else (COL_WARN if ee_decision.get("status") == "WAIT" else "#00B8D4"),
                    )
            except Exception as exc:
                if hasattr(self, "lbl_entry_exit_preview"):
                    self.lbl_entry_exit_preview.configure(
                        text=f"E/E: ERROR | {exc}",
                        text_color=COL_WARN,
                    )

            # 4. TÍNH TOÁN LOT PREVIEW DỰA TRÊN active_sl_dist
            f_lot = self._normalize_contracts(mlot, vol_min, vol_max) if mlot > 0 else 0
            if f_lot == 0 and active_sl_dist > 0:
                risk_usd = acc["equity"] * (current_risk_pct / 100)
                strict_fee = 0.0
                if params.get("STRICT_RISK", False):
                    comm_rate = self.calculate_round_trip_trade_fee(
                        sym, entry_calc_price, p_sl, 1, d
                    )
                    spread_cost_per_lot = (tick.ask - tick.bid) * c_size
                    strict_fee = comm_rate + spread_cost_per_lot

                order_type = 0 if d == "BUY" else 1
                calc_loss = 0.0
                loss_per_lot = abs(float(calc_loss)) if calc_loss is not None and calc_loss < 0 else active_sl_dist * c_size
                if (loss_per_lot + strict_fee) > 0:
                    raw_calc = risk_usd / (loss_per_lot + strict_fee)
                    f_lot = round(raw_calc / vol_step) * vol_step
                    f_lot = self._normalize_contracts(f_lot, vol_min, vol_max)

            # Cổ phiếu: preview hiển thị lô bội số 100 (đúng cái sẽ thực sự đặt khi bấm).
            from core import settlement as _settlement, stock_rules as _stock_rules
            if _settlement.is_cash_stock(sym) and f_lot > 0:
                f_lot = _stock_rules.round_lot_down(f_lot)

            auto_lot_cap = 0.0
            try:
                brain = self.trade_mgr._get_brain_settings(sym)
                auto_lot_cap = float((brain.get("symbol_configs", {}).get(sym, {}) or {}).get("max_lot_cap", 0.0) or 0.0)
            except Exception:
                auto_lot_cap = 0.0
            if auto_lot_cap <= 0:
                auto_lot_cap = float(getattr(config, "MAX_LOT_CAP", 0.0) or 0.0)
            if mlot <= 0 and auto_lot_cap > 0 and f_lot > auto_lot_cap:
                self.lbl_prev_lot.configure(text=f"CHẶN AUTO {qty_unit}: {f_lot:.0f} > {auto_lot_cap:.0f}", text_color=COL_RED)
                if hasattr(self, "btn_action"):
                    self.btn_action.configure(state="normal")
            elif hasattr(self, "btn_action"):
                self.btn_action.configure(state="normal")

            if f_lot < vol_min:
                f_lot = self._normalize_contracts(vol_min, vol_min, vol_max)
                self.lbl_prev_lot.configure(
                    text=f"{'(TAY)' if mlot > 0 else '(TỰ ĐỘNG)'} {qty_unit}: {f_lot:.0f}",
                    text_color="white" if mlot == 0 else "#FFD700",
                )
            else:
                f_lot = self._normalize_contracts(f_lot, vol_min, vol_max)
                self.lbl_prev_lot.configure(
                    text=f"{'(TAY)' if mlot > 0 else '(TỰ ĐỘNG)'} {qty_unit}: {f_lot:.0f}",
                    text_color="white" if mlot == 0 else "#FFD700",
                )

            # Mọi số tiền hiển thị bằng VND nguyên con; giá nội bộ CKCS vẫn giữ
            # đơn vị bảng điện (nghìn) để không thay đổi công thức/đặt lệnh DNSE.
            comm_total = self.calculate_trade_fee(sym, entry_calc_price, f_lot, side=d)

            # Spread chỉ hợp lệ khi có bid/ask THẬT (không phải fallback từ giá khớp),
            # dương và không bị cross (thị trường đang mở).
            bid_v = float(getattr(tick, "bid", 0.0) or 0.0)
            ask_v = float(getattr(tick, "ask", 0.0) or 0.0)
            spread_cost = 0.0  # tổng VND cho cả vị thế preview; 0 khi chưa có giá
            if bid_v <= 0 or ask_v <= 0 or ask_v < bid_v or bool(getattr(tick, "synthetic", False)):
                spread_part = "Spread: chờ giá"
            else:
                # ask-bid đã là đơn vị giá: điểm (phái sinh) / nghìn VND (cổ phiếu).
                spread = ask_v - bid_v
                spread_cost = spread * f_lot * c_size  # quy ra VND: × khối lượng × giá trị 1 đơn vị giá
                if settlement.is_cash_stock(sym):
                    # Spread/CP thường chỉ vài chục VND. Dùng bộ hiển thị "bỏ 000"
                    # ở đây sẽ làm tròn thành 0 dù tổng spread vẫn đúng.
                    spread_per_share_vnd = spread * 1000.0
                    spread_part = (
                        f"Spread: {spread_per_share_vnd:,.0f} VND/CP"
                        f" ({self._fmt_money(spread_cost)})"
                    )
                else:
                    spread_part = (
                        f"Spread: {spread:.2f} điểm"
                        f" ({self._fmt_money(spread_cost)})"
                    )

            # Phí: luôn lấy giá trị tính được gần nhất (fallback profile), không treo "chờ DNSE".
            fee_part = f"Phí GD: {self._fmt_money(abs(comm_total))}"
            margin_part = ""
            try:
                brain = self.trade_mgr._get_brain_settings(sym)
                margin_cfg = margin_rules.settings_from_brain(brain)
                if settlement.is_cash_stock(sym) and margin_cfg.get("ENABLE_MANUAL_MARGIN"):
                    _base, base_label, _warn = margin_rules.resolve_risk_base(acc, margin_cfg)
                    snap = margin_rules.account_snapshot(acc, margin_cfg)
                    rtt = snap.get("rtt")
                    rtt_txt = "UNK" if rtt is None else f"{float(rtt):.0f}"
                    margin_part = f" | M:{'FREE' if base_label == 'FREE_CASH' else 'EQ'} RTT:{rtt_txt}"
            except Exception:
                margin_part = ""
            fee_info_text = f"{spread_part} | {fee_part}{margin_part}"
            self._set_money_label(
                self.lbl_fee_info,
                fee_info_text,
                base_size=13,
                min_size=9,
                comfortable_chars=42,
            )
            # (Trạng thái T+2 Đã về/Chờ về hiển thị theo từng vị thế trong BẢNG LỆNH.)

            # 5. HIỂN THỊ RỦI RO LÊN GIAO DIỆN
            is_valid_sl = True
            if d == "BUY" and p_sl >= entry_calc_price: is_valid_sl = False
            if d == "SELL" and p_sl <= entry_calc_price: is_valid_sl = False

            if is_valid_sl:
                self.lbl_prev_sl.configure(text=self._fmt_price(p_sl, sym), text_color=COL_RED)
                loss_dist = abs(entry_calc_price - p_sl)
                loss_val = loss_dist * f_lot * c_size
                loss_pct = (loss_val / balance) * 100 if balance > 0 else 0.0
                loss_pct_txt = "--%" if balance <= 0 else (f"{loss_pct:.4f}%" if 0 < abs(loss_pct) < 0.01 else f"{loss_pct:.2f}%")
                risk_text = f"-{self._fmt_money(loss_val)} ({loss_pct_txt})"
                self._set_money_label(
                    self.lbl_prev_risk,
                    risk_text,
                    base_size=13,
                    min_size=9,
                    comfortable_chars=20,
                    family="Consolas",
                    text_color=COL_RED,
                )
            else:
                self.lbl_prev_sl.configure(text="LỖI", text_color=COL_WARN)
                self.lbl_prev_risk.configure(text="---", text_color=COL_WARN)

            is_valid_tp = not swing_tp_missing
            if is_valid_tp and d == "BUY" and p_tp <= entry_calc_price:
                is_valid_tp = False
            if is_valid_tp and d == "SELL" and p_tp >= entry_calc_price:
                is_valid_tp = False

            if is_valid_tp:
                self.lbl_prev_tp.configure(text=self._fmt_price(p_tp, sym), text_color=COL_GREEN)
                prof_dist = abs(p_tp - entry_calc_price)
                prof_val = prof_dist * f_lot * c_size
                prof_pct = (prof_val / balance) * 100 if balance > 0 else 0.0
                prof_pct_txt = "--%" if balance <= 0 else (f"{prof_pct:.4f}%" if 0 < abs(prof_pct) < 0.01 else f"{prof_pct:.2f}%")
                reward_text = f"+{self._fmt_money(prof_val)} ({prof_pct_txt})"
                self._set_money_label(
                    self.lbl_prev_rew,
                    reward_text,
                    base_size=13,
                    min_size=9,
                    comfortable_chars=20,
                    family="Consolas",
                    text_color=COL_GREEN,
                )
            else:
                self.lbl_prev_tp.configure(text="LỖI", text_color=COL_WARN)
                self.lbl_prev_rew.configure(text="---", text_color=COL_WARN)

            if cur_tactic_str == "OFF":
                self.lbl_tsl_preview.configure(text="TSL: OFF")
            else:
                milestones = []
                one_r_dist = abs(entry_calc_price - p_sl)
                is_buy = d == "BUY"
                t_cfg = config.TSL_CONFIG

                cur_tactic_modes = cur_tactic_str.split("+")
                if "BE" in cur_tactic_modes and one_r_dist > 0:
                    trig_r = t_cfg.get("BE_OFFSET_RR", 0.8)
                    trig_p = (
                        entry_calc_price + (one_r_dist * trig_r)
                        if is_buy
                        else entry_calc_price - (one_r_dist * trig_r)
                    )
                    fee_d = (
                        (comm_total + spread_cost) / (f_lot * c_size)
                        if (f_lot * c_size) > 0
                        else 0
                    )
                    mode = t_cfg.get("BE_MODE", "SOFT")
                    base = (
                        entry_calc_price - fee_d
                        if (is_buy and mode == "SOFT")
                        else (
                            entry_calc_price + fee_d
                            if (is_buy and mode == "SMART")
                            else entry_calc_price
                        )
                    )
                    if not is_buy:
                        base = (
                            entry_calc_price + fee_d
                            if mode == "SOFT"
                            else (entry_calc_price - fee_d if mode == "SMART" else entry_calc_price)
                        )
                    be_sl = (
                        base + (t_cfg.get("BE_OFFSET_POINTS", 0) * point)
                        if is_buy
                        else base - (t_cfg.get("BE_OFFSET_POINTS", 0) * point)
                    )
                    milestones.append(
                        (abs(entry_calc_price - trig_p), f"BE_SL | {trig_p:.2f} -> {be_sl:.2f}")
                    )

                if "STEP_R" in cur_tactic_str and one_r_dist > 0:
                    sz, rt = (
                        t_cfg.get("STEP_R_SIZE", 1.0),
                        t_cfg.get("STEP_R_RATIO", 0.8),
                    )
                    n_trig = (
                        entry_calc_price + (sz * one_r_dist)
                        if is_buy
                        else entry_calc_price - (sz * one_r_dist)
                    )
                    n_sl = (
                        entry_calc_price + (sz * one_r_dist * rt)
                        if is_buy
                        else entry_calc_price - (sz * one_r_dist * rt)
                    )
                    milestones.append(
                        (
                            abs(entry_calc_price - n_trig),
                            f"Step 1 | {n_trig:.2f} -> {n_sl:.2f}",
                        )
                    )

                if "PNL" in cur_tactic_str and t_cfg.get("PNL_LEVELS") and acc:
                    lvl = t_cfg["PNL_LEVELS"][0]
                    req_profit_usd = acc["balance"] * (lvl[0] / 100.0)
                    trig_p = (
                        cur_price + (req_profit_usd / (f_lot * c_size))
                        if is_buy
                        else cur_price - (req_profit_usd / (f_lot * c_size))
                    )
                    milestones.append(
                        (abs(cur_price - trig_p), f"PnL {lvl[0]}% | {trig_p:.2f}")
                    )

                if "SWING" in cur_tactic_str and sym_ctx:
                    brain = self.trade_mgr._get_brain_settings()
                    trail_group = brain.get("risk_tsl", {}).get("base_sl", "G2")
                    market_mode = sym_ctx.get("market_mode", "ANY")
                    if "DYNAMIC" in trail_group:
                        trail_group = (
                            "G1" if market_mode in ["TREND", "BREAKOUT"] else "G2"
                        )

                    sh, sl, atr_val = (
                        sym_ctx.get(f"swing_high_{trail_group}", "--"),
                        sym_ctx.get(f"swing_low_{trail_group}", "--"),
                        sym_ctx.get(f"atr_{trail_group}", "--"),
                    )
                    if sh != "--" and sl != "--" and atr_val != "--":
                        t_buf = float(brain.get("risk_tsl", {}).get("sl_atr_multiplier", getattr(config, "sl_atr_multiplier", 0.2)))
                        swing_sl = (
                            float(sl) - (t_buf * float(atr_val))
                            if is_buy
                            else float(sh) + (t_buf * float(atr_val))
                        )
                        milestones.append((0, f"SWING | Đợi mốc ➔ {swing_sl:.2f}"))

                if milestones:
                    closest = sorted(milestones, key=lambda x: x[0])[0][1]
                    self.lbl_tsl_preview.configure(text=f"TSL: {closest}")
                else:
                    preview_setup = {
                        "price": cur_price,
                        "sl": p_sl,
                        "lot": f_lot,
                        "risk_usd": loss_val if "loss_val" in locals() else 0.0,
                        "direction": d,
                        "group": trail_group if "trail_group" in locals() else "G2",
                    }
                    line1, line2 = self._build_tsl_preview_lines(preview_setup, sym_ctx)
                    self.lbl_tsl_preview.configure(text=f"{line1}\n{line2}")

        self.refresh_manual_preview_tab()

        running_trees = getattr(self, "running_trees", None) or {
            ("CKPS PAPER" if getattr(config, "PAPER_TRADING", True) else "CKPS REAL"): self.tree
        }
        existing_by_scope = {name: set(tree.get_children()) for name, tree in running_trees.items()}
        current_by_scope = {name: set() for name in running_trees}

        def _running_scope(symbol, execution_mode):
            market = "CKPS" if self._is_derivative_symbol(symbol) else "CKCS"
            mode = "PAPER" if str(execution_mode or "PAPER").upper() == "PAPER" else "REAL"
            return f"{market} {mode}"

        def _upsert(scope, row_id, values, tag):
            tree = running_trees.get(scope)
            if tree is None:
                return
            current_by_scope[scope].add(str(row_id))
            if str(row_id) in existing_by_scope[scope]:
                tree.item(str(row_id), values=values, tags=(tag,))
            else:
                tree.insert("", "end", iid=str(row_id), values=values, tags=(tag,))

        open_fee_total = 0.0  # [NEW] Tổng fee từ lệnh đang mở
        def _short_id(value):
            text = str(value or "")
            return text[:8] if len(text) > 8 else text

        current_execution_mode = "PAPER" if getattr(config, "PAPER_TRADING", True) else "REAL"
        try:
            import core.storage_manager as storage_manager

            position_magics = storage_manager.get_magic_numbers()
        except Exception:
            position_magics = {}
        try:
            from core import signal_opportunities

            legacy_opportunities = []
            show_opportunities = bool(self.var_show_bot_opportunities.get())
            for item in signal_opportunities.list_active():
                opp_id = str(item.get("id", "") or "")
                if not opp_id:
                    continue
                symbol = str(item.get("symbol", "") or "").upper()
                mode = str(item.get("execution_mode", "PAPER") or "PAPER").upper()
                scope = _running_scope(symbol, mode)
                created = float(item.get("first_seen_at", 0.0) or 0.0)
                last_seen = float(item.get("last_seen_at", 0.0) or 0.0)
                expire_at = float(item.get("expire_at", 0.0) or 0.0)
                detected = float(item.get("detected_price", 0.0) or 0.0)
                side = str(item.get("side", "BUY") or "BUY").upper()
                count = int(item.get("signal_count", 1) or 1)
                setup = item.get("order_setup", {}) if isinstance(item.get("order_setup"), dict) else {}
                if (
                    not setup
                    or str(setup.get("plan_source") or "").upper() != "BOT"
                    or self._safe_float(setup.get("plan_version", 0), 0.0) < 1
                ):
                    legacy_opportunities.append(item)
                setup_ok = bool(setup.get("ok"))
                entry = float(setup.get("price", detected) or detected or 0.0)
                lot = float(setup.get("lot", 0.0) or 0.0)
                sl = float(setup.get("sl", 0.0) or 0.0)
                tp = float(setup.get("tp", 0.0) or 0.0)
                risk_amount = float(setup.get("risk_amount", 0.0) or 0.0)
                reward_amount = float(setup.get("reward_amount", 0.0) or 0.0)
                risk_pct = float(setup.get("risk_pct", 0.0) or 0.0)
                tactic = str(setup.get("tactic", "OFF") or "OFF")
                ee_tactic = str(setup.get("entry_exit_tactic", "OFF") or "OFF")
                unit = self._quantity_unit(symbol)
                reason = str(item.get("block_reason", "BOT_OFF") or "BOT_OFF")
                if len(reason) > 70:
                    reason = reason[:67] + "..."
                if setup_ok:
                    order_text = (
                        f"[CACHE][BOT] {side} {lot:g} {unit} {symbol} "
                        f"@ {self._fmt_price(entry, symbol)}"
                    )
                    target_text = (
                        f"SL {self._fmt_price(sl, symbol) if sl > 0 else 'OFF'}"
                        f"  |  TP {self._fmt_price(tp, symbol) if tp > 0 else 'OFF'}"
                    )
                    risk_text = (
                        f"-{self._fmt_money(risk_amount)} ({risk_pct:.2f}%)"
                        f" | +{self._fmt_money(reward_amount)}"
                    )
                    price_text = (
                        f"{setup.get('entry_mode', 'MARKET')} @{self._fmt_price(entry, symbol)}"
                        f" | {setup.get('market_mode', item.get('market_mode', 'ANY'))}"
                    )
                    status_text = (
                        f"GỢI Ý — CHƯA MUA | TSL={tactic} | E/E={ee_tactic} | {reason}"
                    )
                else:
                    setup_error = str(setup.get("error", "CHƯA TÍNH ĐƯỢC LỆNH") or "CHƯA TÍNH ĐƯỢC LỆNH")
                    order_text = f"[CACHE][BOT] {side} {symbol} @ {self._fmt_price(entry, symbol) if entry > 0 else '--'}"
                    target_text = f"Chưa tính được SL/TP: {setup_error}"
                    risk_text = f"gặp {count} lần | {item.get('market_mode', 'ANY')}"
                    price_text = f"giá cuối {self._fmt_price(float(item.get('last_price', 0.0) or 0.0), symbol)}"
                    status_text = f"GỢI Ý — CHƯA MUA | {reason}"
                values = (
                    f"[GỢI Ý] {_short_id(opp_id)}",
                    datetime.fromtimestamp(created).strftime("%d/%m %H:%M") if created else "--",
                    order_text,
                    target_text,
                    f"hết {datetime.fromtimestamp(expire_at).strftime('%d/%m %H:%M') if expire_at else '--'}",
                    risk_text,
                    price_text,
                    status_text,
                    "X",
                )
                if show_opportunities:
                    _upsert(scope, f"OPP:{opp_id}", values, "bot_opportunity")

            # Cache được tạo bởi phiên bản cũ chưa có lot/SL/TP. Tính bù
            # tuần tự ở luồng nền để không đóng băng UI hay gọi dồn API.
            if legacy_opportunities and not getattr(self, "_opportunity_backfill_running", False):
                self._opportunity_backfill_running = True

                def _backfill(rows):
                    try:
                        for legacy in rows:
                            symbol = str(legacy.get("symbol", "") or "").upper()
                            side = str(legacy.get("side", "BUY") or "BUY").upper()
                            context = legacy.get("context", {}) if isinstance(legacy.get("context"), dict) else {}
                            setup = self.trade_mgr.build_telegram_signal_order(
                                symbol,
                                side,
                                context=context,
                                market_mode=str(legacy.get("market_mode", "ANY") or "ANY"),
                            )
                            if not isinstance(setup, dict):
                                setup = {"ok": False, "error": "BOT_PLAN_INVALID_RESULT"}
                            setup = dict(setup)
                            setup["plan_source"] = "BOT"
                            setup["plan_version"] = 1
                            signal_opportunities.update_active(
                                legacy.get("id"),
                                order_setup=setup,
                            )
                    except Exception as exc:
                        self.log_message(f"[GỢI Ý BOT] Tính bù lệnh cache lỗi: {exc}", error=True, target="bot")
                    finally:
                        self._opportunity_backfill_running = False

                threading.Thread(
                    target=_backfill,
                    args=(list(legacy_opportunities),),
                    daemon=True,
                ).start()
        except Exception:
            pass

        pending_items = pending_orders.list_all()
        active_pending_closes = {}
        active_close_broker_order_ids = set()
        for pending_item in pending_items:
            pending_status = str(pending_item.get("status", "") or "").upper()
            if (
                str(pending_item.get("action", "OPEN") or "OPEN").upper() == "CLOSE"
                and pending_status not in pending_orders.FINAL_STATUSES
            ):
                pending_mode = str(
                    pending_item.get("execution_mode", "PAPER") or "PAPER"
                ).upper()
                pending_ticket = str(
                    pending_item.get("position_ticket", "") or ""
                )
                if pending_ticket:
                    active_pending_closes[(pending_mode, pending_ticket)] = pending_item
                broker_order_id = str(
                    pending_item.get("dnse_order_id", "") or ""
                )
                if broker_order_id:
                    active_close_broker_order_ids.add(broker_order_id)

        for item in pending_items:
            item_mode = str(item.get("execution_mode", "PAPER")).upper()
            action = str(item.get("action", "OPEN") or "OPEN").upper()
            local_id = str(item.get("id", ""))
            if not local_id:
                continue
            # A cached CLOSE belongs to its original position row. Rendering a
            # second [LOCAL] row duplicates one investor action and confuses
            # selection/cancellation.
            if action == "CLOSE":
                continue
            row_id = f"LOCAL:{local_id}"
            scope = _running_scope(str(item.get("symbol", "") or ""), item_mode)
            status = str(item.get("status", "PENDING") or "PENDING").upper()
            side_txt = str(item.get("side", "BUY") or "BUY").upper()
            symbol = str(item.get("symbol", "") or "")
            unit = self._quantity_unit(symbol)
            created = float(item.get("created_at", 0.0) or 0.0)
            expire_at = float(item.get("expire_at", 0.0) or 0.0)
            entry = float(item.get("entry_price", 0.0) or 0.0)
            lot = float(item.get("lot", 0.0) or 0.0)
            sl = float(item.get("sl", 0.0) or 0.0)
            tp = float(item.get("tp", 0.0) or 0.0)
            target = str(item.get("target", "OPEN") or "OPEN").upper()
            lot_source = str(item.get("lot_source", "") or ("MANUAL_LOT" if lot > 0 else "AUTO_RISK"))
            sl_source = str(item.get("sl_source", "") or ("MANUAL_SL" if sl > 0 else "PRESET"))
            tp_source = str(item.get("tp_source", "") or ("MANUAL_TP" if tp > 0 else "PRESET"))
            plan = str(item.get("plan", "") or "")
            if not plan:
                if action == "CLOSE":
                    plan = f"OPEN -> CLOSE #{item.get('position_ticket', '')}"
                else:
                    plan = f"OPEN -> DNSE LO @{entry:g}" if entry > 0 else f"{target} -> DNSE {target}"
            time_str = datetime.fromtimestamp(created).strftime("%d/%m %H:%M") if created else "--"
            exp_str = datetime.fromtimestamp(expire_at).strftime("%d/%m %H:%M") if expire_at else "không hết hạn"
            price_txt = f"LO @{self._fmt_price(entry, symbol)}" if entry > 0 else f"{target} auction"
            lot_txt = f"{lot:g} {unit}" if lot > 0 else f"AUTO {unit}"
            sl_txt = self._fmt_price(sl, symbol) if sl > 0 else "AUTO"
            tp_txt = self._fmt_price(tp, symbol) if tp > 0 else "AUTO"
            result_txt = str(item.get("result", "") or "")
            if len(result_txt) > 42:
                result_txt = result_txt[:39] + "..."
            tag_to_apply = {
                "PENDING": "local_pending",
                "SENDING": "local_sending",
                "FAILED": "order_failed",
                "EXPIRED": "order_cancelled",
                "CANCELLED": "order_cancelled",
                "SENT": "dnse_order",
            }.get(status, "local_pending")
            if action == "CLOSE":
                ticket = str(item.get("position_ticket", "") or "")
                values_data = (
                    f"[LOCAL] {_short_id(local_id)}",
                    time_str,
                    f"[LOCAL][{status}] ĐÓNG {symbol} #{ticket}",
                    "Đóng toàn bộ vị thế",
                    exp_str,
                    f"{lot:g} {unit}" if lot > 0 else "--",
                    "Chờ phiên OPEN",
                    result_txt or f"{status} | {item.get('note', '')}",
                    "" if status == "SENDING" else "X",
                )
            else:
                values_data = (
                    f"[LOCAL] {_short_id(local_id)}",
                    time_str,
                    f"[LOCAL][{status}] {side_txt} {lot_txt} {symbol} | {plan}",
                    f"{sl_txt} ({sl_source})  |  {tp_txt} ({tp_source})",
                    f"exp {exp_str}",
                    f"lot {lot_source} | preset {item.get('preset', '')}",
                    f"entry {price_txt}",
                    result_txt or f"{status} | {item.get('note', '')} | E/E={item.get('manual_entry_tactic', 'OFF')}",
                    "" if status == "SENDING" else "X",
                )
            _upsert(scope, row_id, values_data, tag_to_apply)

        def _order_pick(order, *keys, default=""):
            for key in keys:
                if isinstance(order, dict) and key in order and order.get(key) not in (None, ""):
                    return order.get(key)
            return default

        for order in (open_orders or []):
            order_id = str(_order_pick(order, "orderId", "id", "orderID", "order_id", default=""))
            if not order_id:
                continue
            if order_id in active_close_broker_order_ids:
                continue
            row_id = f"ORDER:{order_id}"
            status = str(_order_pick(order, "orderStatus", "status", "state", default="ORDER")).upper()
            symbol = str(_order_pick(order, "symbol", "code", "stockSymbol", default=self.cbo_symbol.get()))
            scope = _running_scope(symbol, "REAL")
            side_raw = str(_order_pick(order, "side", "orderSide", "orderType", "type", default="")).upper()
            side_txt = "BUY" if side_raw in ("NB", "BUY", "0") else ("SELL" if side_raw in ("NS", "SELL", "1") else side_raw or "ORDER")
            qty = _order_pick(order, "quantity", "orderQuantity", "volume", "qty", default="")
            matched = float(_order_pick(order, "matchedQuantity", "filledQuantity", "filledQty", "executedQuantity", default=0) or 0)
            price = float(_order_pick(order, "price", "orderPrice", "limitPrice", default=0) or 0)
            order_kind = str(_order_pick(order, "orderType", "type", "orderKind", default="LO" if price > 0 else "")).upper()
            created_raw = _order_pick(order, "createdDate", "createdAt", "time", "createdTime", default="")
            tag_to_apply = "dnse_partial" if ("PART" in status or matched > 0) else "dnse_order"
            price_txt = self._fmt_price(price, symbol) if price > 0 else (order_kind or "market")
            values_data = (
                f"[DNSE] {_short_id(order_id)}",
                str(created_raw)[5:16] if created_raw else "--",
                f"[DNSE][ORDER] {side_txt} {qty} {self._quantity_unit(symbol)} {symbol} @ {price_txt}",
                "---  |  ---",
                f"type {order_kind or '--'}",
                "---",
                f"matched {matched:g}" if matched > 0 else "---",
                status,
                "X",
            )
            _upsert(scope, row_id, values_data, tag_to_apply)

        for p in positions:
            ticket_str = str(p.ticket)
            position_mode = (
                "PAPER" if ticket_str.upper().startswith("PAPER") else "REAL"
            )
            scope = _running_scope(p.symbol, position_mode)
            position_trade_mgr = BotUI._trade_manager_for_position(self, p)

            # [FREEZE FIX] Đọc số liệu đã gom sẵn ở thread nền -> KHÔNG gọi mạng trên thread UI.
            extra = (pos_extras or {}).get(ticket_str) or {}
            p_c_size = float(extra.get("c_size") or 0.0)
            if not p_c_size:
                # Fallback không đụng mạng (poll_tick=False + hằng số theo loại thị trường).
                try:
                    p_c_size = self.connector.get_symbol_info(p.symbol, poll_tick=False).trade_contract_size if self.connector else 0.0
                except Exception:
                    p_c_size = 0.0
                if not p_c_size:
                    p_c_size = self._symbol_contract_size(p.symbol)
            p_unit = self._quantity_unit(p.symbol)
            swap_val = getattr(p, "swap", 0.0)

            # Spread hiện tại của mã (đã gom ở thread nền, cache 2s/WS) — chỉ khi có sổ lệnh thật.
            current_spread = float(extra.get("spread", 0.0) or 0.0)
            spread_cost_usd = current_spread * p.volume * p_c_size
            comm_total_usd = float(extra.get("comm", 0.0) or 0.0)

            # [NEW] Cộng fee lệnh đang mở vào tổng (spread + commission + |swap|)
            if position_mode == current_execution_mode:
                open_fee_total += (
                    spread_cost_usd + comm_total_usd + abs(swap_val)
                )

            fee_str = f"Spread -{self._fmt_money(abs(spread_cost_usd))} | Phí -{self._fmt_money(abs(comm_total_usd))}"

            time_str = datetime.fromtimestamp(p.time).strftime("%d/%m %H:%M")
            is_buy = p.type == 0
            icon = "🟢" if is_buy else "🔴"
            side_txt = "BUY" if is_buy else "SELL"

            display_ticket = f"#{ticket_str}"
            is_child = ticket_str in position_trade_mgr.state.get(
                "child_to_parent", {}
            )

            if is_child:
                display_ticket = f" ┗━ #{ticket_str}"

            broker_tag = "[PAPER]" if position_mode == "PAPER" else "[REAL]"
            origin_tag = "[MANUAL]"
            pos_comment = str(getattr(p, "comment", "") or "")
            external_position = (
                position_mode == "REAL"
                and is_external_position(p, position_magics)
            )
            if external_position:
                origin_tag = "[EXTERNAL]"
            elif "[BOT]_AUTO_DCA" in pos_comment:
                origin_tag = "[BOT-DCA]"
            elif "[BOT]_AUTO_PCA" in pos_comment:
                origin_tag = "[BOT-PCA]"
            elif "[BOT]" in pos_comment:
                origin_tag = "[BOT]"
            elif "_Child" in pos_comment:
                origin_tag = "[MANUAL+BOT]"
            margin_meta = position_trade_mgr.state.get(
                "trade_margin_meta", {}
            ).get(ticket_str, {})
            margin_tag = ""
            if margin_meta or "[MARGIN][MANUAL]" in pos_comment:
                margin_tag = "[MARGIN][MANUAL]"

            order_str = f"{broker_tag}{origin_tag}{margin_tag} {icon} {side_txt} {p.volume:.0f} {p_unit} {p.symbol} @ {self._fmt_price(p.price_open, p.symbol)}"

            sl_txt = self._fmt_price(p.sl, p.symbol) if p.sl > 0 else "---"
            tp_txt = self._fmt_price(p.tp, p.symbol) if p.tp > 0 else "---"
            targets_str = f"{sl_txt}  |  {tp_txt}"

            risk_usd = abs(p.price_open - p.sl) * p.volume * p_c_size if p.sl > 0 else 0
            rew_usd = abs(p.price_open - p.tp) * p.volume * p_c_size if p.tp > 0 else 0
            risk_pct = (risk_usd / balance * 100) if balance > 0 else 0
            rew_pct = (rew_usd / balance * 100) if balance > 0 else 0

            is_sl_in_profit = False
            if is_buy and p.sl > p.price_open:
                is_sl_in_profit = True
            if not is_buy and p.sl > 0 and p.sl < p.price_open:
                is_sl_in_profit = True

            if p.sl == 0:
                risk_str = "No SL"
            elif is_sl_in_profit:
                risk_str = f"+{self._fmt_money(risk_usd)} ({risk_pct:.1f}%)"
            else:
                risk_str = f"-{self._fmt_money(risk_usd)} ({risk_pct:.1f}%)"

            rew_str = f"+{self._fmt_money(rew_usd)} ({rew_pct:.1f}%)" if p.tp > 0 else "No TP"
            rr_str = f"{risk_str}  |  {rew_str}"

            stt_txt = self.tsl_states_map.get(p.ticket, "Running")

            # [KAISER FIX] Hiển thị rõ loại lệnh con trên Status nếu là lệnh DCA/PCA
            if (
                external_position
                and position_trade_mgr.get_trade_tactic(p.ticket) == "OFF"
            ):
                stt_txt = "DNSE MOBILE/WEB | TSL:OFF"
            elif "[BOT]_AUTO_DCA" in pos_comment:
                stt_txt = "DCA Child"
            elif "[BOT]_AUTO_PCA" in pos_comment:
                stt_txt = "PCA Child"
            else:
                tactic_info = position_trade_mgr.get_trade_tactic(p.ticket)
                tactic_badges = []
                tactic_modes = tactic_info.split("+")
                if "BE_CASH" in tactic_modes:
                    tactic_badges.append("BE_CASH")
                elif "BE" in tactic_modes:
                    tactic_badges.append("BE")
                if "SWING" in tactic_modes:
                    tactic_badges.append("SWING")
                if "STEP_R" in tactic_modes:
                    tactic_badges.append("STEP_R")
                if "PNL" in tactic_modes:
                    tactic_badges.append("PNL")
                if "PSAR_TRAIL" in tactic_modes:
                    tactic_badges.append("PSAR")
                if "ANTI_CASH" in tactic_modes:
                    tactic_badges.append("ANTI")
                if "REV_C" in tactic_modes:
                    tactic_badges.append("REV")
                stt_extras = []
                if "AUTO_DCA" in tactic_modes:
                    stt_extras.append("DCA")
                if "AUTO_PCA" in tactic_modes:
                    stt_extras.append("PCA")
                if stt_extras:
                    tactic_badges.append("+".join(stt_extras))
                if tactic_badges:
                    stt_txt += f" | TSL:{'+'.join(tactic_badges)}"
                ee_tactic = position_trade_mgr.get_trade_entry_exit_tactic(
                    p.ticket
                )
                if ee_tactic and ee_tactic != "OFF":
                    ee_labels = {
                        "FALLBACK_R": "R",
                        "SWING_REJECTION": "RETEST",
                        "SWING_STRUCTURE": "STRUCT",
                        "FIB_RETRACE": "FIB",
                        "PULLBACK_ZONE": "PULL",
                    }
                    ee_badges = [
                        ee_labels.get(mode, mode)
                        for mode in ee_tactic.split("+")
                        if mode and mode != "OFF"
                    ]
                    if ee_badges:
                        stt_txt += f" | E/E:{'+'.join(ee_badges)}"
                if margin_meta:
                    snap = margin_meta.get("snapshot", {}) or {}
                    rtt = snap.get("rtt")
                    rtt_txt = "UNK" if rtt is None else f"{float(rtt):.0f}%"
                    stt_txt += f" | M:{margin_meta.get('risk_base', 'EQUITY_NAV')} RTT:{rtt_txt}"
                if external_position:
                    stt_txt = f"DNSE EXTERNAL | {stt_txt}"

            net_pnl = p.profit + getattr(p, "swap", 0.0)
            excursion = position_trade_mgr.state.get("trade_excursions", {}).get(
                ticket_str, {}
            )
            mae_usd = float(excursion.get("mae_usd", min(net_pnl, 0.0)))
            mfe_usd = float(excursion.get("mfe_usd", max(net_pnl, 0.0)))
            pnl_excursion_str = (
                f"P:{self._fmt_money(net_pnl, signed=True)} | A:{self._fmt_money(mae_usd, signed=True)} | F:{self._fmt_money(mfe_usd, signed=True)}"
            )

            # CKCS đã khớp -> nền CAM; cột STT ghi thêm CHỜ VỀ/ĐÃ VỀ (T+2). CKPS giữ xanh/đỏ.
            tag_to_apply = "buy_row" if is_buy else "sell_row"
            try:
                if settlement.is_cash_stock(p.symbol):
                    tag_to_apply = "matched_stock"
                    _settle = (getattr(p, "raw", {}) or {}).get("settle_date")
                    if _settle and not settlement.is_settled(_settle):
                        stt_txt = f"⏳ CHỜ VỀ {str(_settle)[5:10]} | {stt_txt}" if stt_txt else f"⏳ CHỜ VỀ {str(_settle)[5:10]}"
                    else:
                        stt_txt = f"✓ ĐÃ VỀ | {stt_txt}" if stt_txt else "✓ ĐÃ VỀ"
            except Exception:
                pass
            if external_position and not settlement.is_cash_stock(p.symbol):
                tag_to_apply = "external_position"

            pending_close = active_pending_closes.get((position_mode, ticket_str))
            action_text = "❌"
            if pending_close:
                pending_status = str(
                    pending_close.get("status", pending_orders.PENDING)
                    or pending_orders.PENDING
                ).upper()
                if pending_status == pending_orders.SENDING:
                    close_badge = "↗ ĐANG GỬI ĐÓNG"
                    action_text = ""
                elif pending_status == pending_orders.WORKING:
                    limit_text = self._fmt_price(
                        pending_close.get("entry_price", 0.0),
                        p.symbol,
                    )
                    close_badge = f"⌛ LIMIT CHỜ KHỚP @{limit_text}"
                    action_text = "↩"
                elif pending_status == pending_orders.ERROR:
                    detail = str(pending_close.get("result", "") or "CLOSE_FAILED")
                    close_badge = f"⚠ ĐÓNG LỖI: {detail[:36]}"
                    action_text = "↩"
                    tag_to_apply = "position_close_error"
                else:
                    close_mode = str(
                        pending_close.get("entry_mode", "MARKET") or "MARKET"
                    ).upper()
                    if close_mode == "LIMIT":
                        limit_text = self._fmt_price(
                            pending_close.get("entry_price", 0.0),
                            p.symbol,
                        )
                        close_mode += f" @{limit_text}"
                    close_badge = f"⏳ CHỜ ĐÓNG | {close_mode}"
                    action_text = "↩"
                stt_txt = f"{close_badge} | {stt_txt}" if stt_txt else close_badge
                if pending_status != pending_orders.ERROR:
                    tag_to_apply = "position_closing"

            values_data = (
                display_ticket,
                time_str,
                order_str,
                targets_str,
                fee_str,
                rr_str,
                pnl_excursion_str,
                stt_txt,
                action_text,
            )

            _upsert(scope, ticket_str, values_data, tag_to_apply)

        for scope, tree in running_trees.items():
            for row_id in existing_by_scope[scope]:
                if row_id not in current_by_scope[scope]:
                    tree.delete(row_id)

        # [NEW] Cập nhật FEE label = fee đã đóng (state) + fee lệnh đang mở (real-time)
        total_fee = state.get("fee_today", 0.0) + open_fee_total
        fee_disp = self._fmt_money(total_fee)
        fee_today_text = f"Phí: -{fee_disp}" if total_fee > 0 else f"Phí: {fee_disp}"
        self._set_money_label(
            self.lbl_fee_today,
            fee_today_text,
            base_size=13,
            min_size=9,
            comfortable_chars=12,
            text_color="#FFD700" if total_fee > 0 else "white",
        )

    def on_click_trade(self):
        d, s, p, t = (
            self.var_direction.get(),
            self.cbo_symbol.get(),
            getattr(config, "DEFAULT_PRESET", "SCALPING"),
            self.get_current_tactic_string(),
        )
        try:
            ml = float(self.var_manual_lot.get() or 0)
            me = self._price_input_to_internal(self.var_manual_entry.get(), s)
            mt = self._price_input_to_internal(self.var_manual_tp.get(), s)
            ms = self._price_input_to_internal(self.var_manual_sl.get(), s)
        except:
            ml = me = mt = ms = 0.0

        if ms == 0.0 and self.var_assist_math_sl.get():
            target_sym_ctx = self.latest_market_context.get(s, {})
            if d == "BUY":
                sl_val = target_sym_ctx.get(
                    "swing_low_entry", target_sym_ctx.get("swing_low")
                )
            else:
                sl_val = target_sym_ctx.get(
                    "swing_high_entry", target_sym_ctx.get("swing_high")
                )

            atr_val = target_sym_ctx.get("atr_entry", target_sym_ctx.get("atr"))

            if sl_val and atr_val:
                brain = self.trade_mgr._get_brain_settings()
                sl_mult = float(brain.get("risk_tsl", {}).get("sl_atr_multiplier", getattr(config, "sl_atr_multiplier", 0.2)))
                ms = (
                    float(sl_val) - (float(atr_val) * sl_mult)
                    if d == "BUY"
                    else float(sl_val) + (float(atr_val) * sl_mult)
                )
                self.log_message(f"🧠 Auto-Math SL: {ms:.2f}", error=False)

        # Truyền thêm biến target_sym_ctx vào execute_manual_trade
        target_sym_ctx = self.latest_market_context.get(s, {})
        try:
            ee_decision = self.latest_entry_exit_decisions.get(s, {})
            if ee_decision.get("status") == "READY":
                if ms == 0.0 and ee_decision.get("sl"):
                    ms = float(ee_decision["sl"])
                if mt == 0.0 and ee_decision.get("tp"):
                    mt = float(ee_decision["tp"])
        except Exception:
            pass

        # Kiểu lệnh ATO/ATC (chỉ khi đang đúng phiên), else None -> LO/MOK.
        order_kind = None
        phase = ""
        try:
            _mode = self.var_manual_trade_mode.get()
            from core.market_hours import market_session_phase
            phase = market_session_phase(s)[0]
            if _mode in ("ATO", "ATC"):
                if phase == _mode:
                    order_kind = _mode
        except Exception:
            order_kind = None

        if phase not in ("ATO", "OPEN", "ATC"):
            self.log_message("Ngoai phien giao dich: dung LIMIT ORDER de luu cache local.", error=True, target="manual")
            return
        if me > 0 and phase != "OPEN":
            self.log_message("LO chi gui trong phien OPEN; dung LIMIT ORDER de cache toi phien.", error=True, target="manual")
            return
        market_data_down_ack = False
        market_state = self._shared_market_state()
        if market_state in ("RECOVERING", "MARKET DATA DOWN", "MARKET_DATA_DOWN"):
            if me <= 0:
                self.log_message(
                    "Mất dữ liệu giá: không gửi lệnh thị trường. Hãy nhập giá LO cụ thể.",
                    error=True,
                    target="manual",
                )
                return
            market_data_down_ack = messagebox.askyesno(
                "Xác nhận LO khi mất giá",
                f"Nguồn giá đang {market_state}.\n\n"
                f"Gửi lệnh LO {d} {s} tại giá {me:g} do Ngài tự nhập?\n"
                "App sẽ không tự thay bằng giá cache.",
                parent=self,
            )
            if not market_data_down_ack:
                return
        if not self._ensure_trading_otp():
            return
        expected_paper_mode = bool(getattr(config, "PAPER_TRADING", True))

        def run_trade_thread(risk_gate_ack=False):
            result = self.trade_mgr.execute_manual_trade(
                d,
                p,
                s,
                self.var_strict_mode.get(),
                target_sym_ctx,   # <--- Đã thêm biến này
                ml,
                mt,
                ms,
                self.var_bypass_checklist.get(),
                t,
                order_kind=order_kind,
                manual_entry_price=me,
                entry_exit_tactic=self.get_current_entry_exit_tactic_string(),
                risk_gate_ack=risk_gate_ack,
                expected_paper_mode=expected_paper_mode,
                market_data_down_ack=market_data_down_ack,
            )
            # [RISK GATE] Vượt trần %NAV -> hỏi user trên Tk main thread rồi gọi lại với ack.
            # Con số user xác nhận = con số code thực thi vừa tính (không dùng preview).
            if str(result).startswith("RISK_GATE_CONFIRM"):
                parts = str(result).split("|", 3)
                gate_msg = parts[3] if len(parts) > 3 else str(result)

                def _ask_risk_gate():
                    if messagebox.askyesno(
                        "Xác nhận RISK", f"{gate_msg}\n\nVẫn vào lệnh?", parent=self
                    ):
                        threading.Thread(target=lambda: run_trade_thread(risk_gate_ack=True)).start()
                    else:
                        self.log_message("Đã hủy lệnh theo RISK GATE.", target="manual")

                self.after(0, _ask_risk_gate)
                return
            if "SUCCESS" not in result:
                self.log_message(f"❌ THẤT BẠI: {result}", error=True)

        threading.Thread(target=run_trade_thread).start()

    def _set_ckcs_raw_status(self, status, error=""):
        self.ckcs_raw_last_status = status
        self.ckcs_raw_last_error = error or ""
        label = getattr(self, "lbl_ckcs_raw_status", None)
        if label is not None:
            try:
                label.configure(
                    text=f"{status}{(' | ' + error) if error else ''}",
                    text_color="#D50000" if error else "#00C853",
                )
            except Exception:
                pass

    def _set_ckcs_api_status(self, status, error=""):
        self.ckcs_api_last_status = status
        self.ckcs_api_last_error = error or ""
        label = getattr(self, "lbl_ckcs_api_status", None)
        if label is not None:
            try:
                label.configure(
                    text=f"{status}{(' | ' + error) if error else ''}",
                    text_color="#D50000" if error else "#00C853",
                )
            except Exception:
                pass

    def _ckcs_session_worker(self, session, send_api=False, reason="manual"):
        session = str(session or "").strip().lower()
        session_vi = "SÁNG" if session == "morning" else "CHIỀU"
        try:
            from ai_advisor import ckcs_api

            try:
                days = max(1, int(self.var_ckcs_report_days.get() or 15))
            except (TypeError, ValueError):
                days = 15
            self.after(0, lambda: self._set_ckcs_api_status(f"Đang tạo báo cáo {session_vi}..."))
            result = ckcs_api.generate_session_report(
                session,
                report_days=days,
                require_current_session=(reason == "schedule"),
            )
            if not result:
                self._ckcs_auto_retry_after[session] = time.time() + 300
                self.after(0, lambda: self._set_ckcs_api_status(
                    f"Chưa tạo được báo cáo {session_vi}",
                    "Kho RAW chưa có dữ liệu; app sẽ thử lại sau.",
                ))
                return

            today = time.strftime("%Y-%m-%d")
            if session == "morning":
                self._ckcs_last_morning_date = today
            else:
                self._ckcs_last_afternoon_date = today
            self._ckcs_auto_retry_after[session] = 0.0
            self.save_advisor_schedule_settings(silent=True)
            report_name = os.path.basename(result.get("report") or "")
            self.log_message(
                f"[CKCS API] Đã tạo {report_name} | {result.get('symbols', 0)} mã | "
                f"{result.get('days', days)} ngày | {reason}",
                target="manual",
            )
            if not send_api:
                self.after(0, lambda: self._set_ckcs_api_status(f"Báo cáo {session_vi} OK | {report_name}"))
                return

            self.after(0, lambda: self._set_ckcs_api_status(f"Đang gửi API phiên {session_vi}..."))
            api_result = ckcs_api.send_session_to_api(session)
            if not api_result.get("ok"):
                error = api_result.get("error", "API failed")
                self.log_message(f"[CKCS API] {session_vi} lỗi: {error}", error=True, target="manual")
                self.after(0, lambda e=error: self._set_ckcs_api_status(f"API {session_vi} lỗi", e))
                return

            response_path = api_result.get("response")
            telegram_result = None
            try:
                from telegram_notify.reporter import send_advisor_response

                telegram_result = send_advisor_response(
                    response_path,
                    title=f"CKCS AI — {session_vi} — {today}",
                )
                if not telegram_result.get("ok") and not telegram_result.get("skipped"):
                    self.log_message(
                        f"[TELEGRAM] CKCS {session_vi} gửi lỗi: "
                        f"{telegram_result.get('error', 'Telegram failed')}",
                        error=True,
                        target="manual",
                    )
            except Exception as exc:
                self.log_message(f"[TELEGRAM] CKCS {session_vi} gửi lỗi: {exc}", error=True, target="manual")
            status = f"CKCS {session_vi} | API OK | {os.path.basename(response_path or '')}"
            if telegram_result and telegram_result.get("ok"):
                status += " | Telegram OK"
            self.after(0, lambda m=status: self._set_ckcs_api_status(m))
        except Exception as exc:
            self.log_message(f"[CKCS API] Worker lỗi: {exc}", error=True, target="manual")
            self.after(0, lambda e=str(exc): self._set_ckcs_api_status("CKCS API lỗi", e))
        finally:
            self._ckcs_api_worker_active = False

    def run_ckcs_session_ui(self, session, send_api=False):
        if self._ckcs_api_worker_active:
            self._set_ckcs_api_status("CKCS đang xử lý, vui lòng chờ...")
            return
        self._ckcs_api_worker_active = True
        threading.Thread(
            target=self._ckcs_session_worker,
            kwargs={"session": session, "send_api": bool(send_api), "reason": "manual"},
            daemon=True,
        ).start()

    def open_ckcs_session_file(self, session, response=False):
        try:
            from ai_advisor import paths as advisor_paths

            path = (
                advisor_paths.ckcs_response_path(session)
                if response
                else advisor_paths.scan_session_report_path(session)
            )
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Chưa có {os.path.basename(path)}")
            os.startfile(path)
            self._set_ckcs_api_status(f"Đã mở {os.path.basename(path)}")
        except Exception as exc:
            self._set_ckcs_api_status("Mở file lỗi", str(exc))

    def refresh_current_ckcs_report_ui(self):
        """Làm mới đúng file phiên hiện tại; không tạo report legacy."""
        session = "morning" if datetime.now().hour < 13 else "afternoon"
        self.run_ckcs_session_ui(session, send_api=False)

    def open_ckcs_research_folder(self):
        try:
            from ai_advisor import paths as advisor_paths

            folder = advisor_paths.ensure_ckcs_research_dir()
            os.startfile(os.path.abspath(folder))
            self._set_ckcs_raw_status("Đã mở thư mục CKCS")
        except Exception as exc:
            self._set_ckcs_raw_status("Mở thư mục lỗi", str(exc))

    def _set_advisor_status(self, status, error=""):
        self.advisor_last_export_status = status
        self.advisor_last_error = error or ""
        label = getattr(self, "lbl_advisor_status", None)
        if label and label.winfo_exists():
            text = status if not error else f"{status} | {error}"
            color = COL_RED if error else (COL_GREEN if "OK" in status else COL_WARN)
            label.configure(text=text, text_color=color)
        inline = getattr(self, "lbl_advisor_inline_status", None)
        if inline and inline.winfo_exists():
            color = COL_RED if error else (COL_GREEN if "OK" in status else "gray")
            inline.configure(text="OK" if "OK" in status else ("ERR" if error else "AI"), text_color=color)

    def _advisor_stdout_log(self, message):
        try:
            main_logger.info("[AI ADVISOR] %s", message)
        except Exception:
            pass

    def _advisor_worker(self, send_api=False, reason="manual", claimed=False):
        if not claimed:
            if self._advisor_worker_active:
                return
            self._advisor_worker_active = True
        api_wait_stop = None
        try:
            from ai_advisor.exporter import generate_advisor_package

            try:
                days = max(1, int(self.var_advisor_export_days.get() or 7))
            except Exception:
                days = 7
            self._advisor_stdout_log(f"worker started reason={reason} send_api={send_api} export_days={days}")
            self.log_message(
                f"[AI ADVISOR] Worker started reason={reason} send_api={send_api} export_days={days}",
                target="manual",
            )
            if send_api:
                self.after(0, lambda: self._set_advisor_status("Advisor exporting before API..."))
            result = generate_advisor_package(
                export_days=days,
                save_archive=False,
                connector=self.connector,
                state=getattr(self.trade_mgr, "state", {}),
                market_contexts=getattr(self, "latest_market_context", {}),
                reason=reason,
            )
            if not result.get("ok"):
                err = result.get("error", "advisor export failed")
                self.after(0, lambda: self._set_advisor_status("Advisor ERR", err))
                self._advisor_stdout_log(f"export failed: {err}")
                self.log_message(f"[AI ADVISOR] Export failed: {err}", error=True, target="manual")
                return
            self._advisor_stdout_log(
                "export ready "
                f"closed={result.get('export_closed_trades', 0)} "
                f"open={result.get('open_trades', 0)}"
            )
            self.log_message(
                "[AI ADVISOR] Export ready "
                f"closed={result.get('export_closed_trades', 0)} "
                f"open={result.get('open_trades', 0)}",
                target="manual",
            )

            api_result = None
            telegram_result = None
            if send_api:
                from ai_advisor.api_client import estimate_api_payload, send_package_to_api

                response_file_var = getattr(
                    self,
                    "var_advisor_send_response_file",
                    getattr(self, "var_advisor_send_previous_response", None),
                )
                include_response_file = bool(response_file_var.get()) if response_file_var else False
                try:
                    estimate = estimate_api_payload(include_previous_response=include_response_file)
                    status_msg = (
                        "Advisor API calling "
                        f"{estimate.get('model')} | "
                        f"tokens~{estimate.get('tokens')} | "
                        f"web={estimate.get('web_search_enabled')}"
                    )
                    self.after(0, lambda m=status_msg: self._set_advisor_status(m))
                    self._advisor_stdout_log(
                        "API sending "
                        f"model={estimate.get('model')} "
                        f"chars={estimate.get('chars')} "
                        f"tokens~{estimate.get('tokens')} "
                        f"web_search={estimate.get('web_search_enabled')} "
                        f"include_response={include_response_file}"
                    )
                    self.log_message(
                        "[AI ADVISOR] API sending "
                        f"model={estimate.get('model')} "
                        f"chars={estimate.get('chars')} "
                        f"tokens~{estimate.get('tokens')} "
                        f"web_search={estimate.get('web_search_enabled')} "
                        f"include_response={include_response_file}",
                        target="manual",
                    )
                except Exception as exc:
                    self.log_message(f"[AI ADVISOR] API estimate warning: {exc}", error=True, target="manual")
                api_wait_stop = threading.Event()

                def api_wait_status():
                    started = time.time()
                    while not api_wait_stop.wait(15):
                        elapsed = int(time.time() - started)
                        mins = elapsed // 60
                        secs = elapsed % 60
                        self.after(
                            0,
                            lambda m=f"Advisor API calling... {mins}m{secs:02d}s": self._set_advisor_status(m),
                        )
                        self._advisor_stdout_log(f"API still waiting elapsed={mins}m{secs:02d}s")

                threading.Thread(target=api_wait_status, daemon=True).start()
                api_result = send_package_to_api(include_previous_response=include_response_file)
                api_wait_stop.set()
                if not api_result.get("ok"):
                    err = api_result.get("error", "API failed")
                    self.after(0, lambda e=err: self._set_advisor_status("Advisor API ERR", e))
                    self._advisor_stdout_log(f"API failed: {err}")
                    self.log_message(
                        f"[AI ADVISOR] API skipped/failed: {err}",
                        error=True,
                        target="manual",
                    )
                    return
                try:
                    from telegram_notify.reporter import send_advisor_response

                    telegram_result = send_advisor_response(api_result.get("response"))
                    if telegram_result.get("ok"):
                        self.log_message(
                            f"[TELEGRAM] Advisor response sent to report group ({telegram_result.get('sent', 0)} parts).",
                            target="manual",
                        )
                    elif not telegram_result.get("skipped"):
                        self.log_message(
                            f"[TELEGRAM] Advisor report skipped/failed: {telegram_result.get('error', 'Telegram failed')}",
                            error=True,
                            target="manual",
                        )
                except Exception as exc:
                    telegram_result = {"ok": False, "error": str(exc)}
                    self.log_message(f"[TELEGRAM] Advisor report error: {exc}", error=True, target="manual")

            msg = (
                f"Advisor OK | export={result.get('export_days', days)}d "
                f"closed={result.get('export_closed_trades', 0)} "
                f"open={result.get('open_trades', 0)}"
            )
            if api_result and api_result.get("ok"):
                msg += " | API OK"
            if telegram_result and telegram_result.get("ok"):
                msg += " | TG OK"
            elif telegram_result and not telegram_result.get("skipped"):
                msg += " | TG WARN"
            self.after(0, lambda m=msg: self._set_advisor_status(m))
            self._advisor_stdout_log(msg)
            self.log_message(f"[AI ADVISOR] {msg}", target="manual")
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._set_advisor_status("Advisor ERR", e))
            self._advisor_stdout_log(f"error: {exc}")
            self.log_message(f"[AI ADVISOR] Error: {exc}", error=True, target="manual")
        finally:
            if api_wait_stop:
                api_wait_stop.set()
            self._advisor_worker_active = False

    def _advisor_api_worker(self, reason="api_button", claimed=False):
        if not claimed:
            if self._advisor_worker_active:
                return
            self._advisor_worker_active = True
        api_wait_stop = None
        try:
            from ai_advisor import paths as advisor_paths
            from ai_advisor.api_client import estimate_api_payload, send_package_to_api

            required_files = [
                ("advisor_export.xlsx", advisor_paths.export_path()),
                ("technical_settings.json", advisor_paths.technical_settings_path()),
                ("advisor_flow.md", advisor_paths.advisor_flow_path()),
                ("user_context.md", advisor_paths.user_context_path()),
            ]
            missing = [name for name, path in required_files if not os.path.exists(path)]
            if missing:
                msg = "Thiếu file Advisor: " + ", ".join(missing) + ". Hãy bấm LÀM MỚI DỮ LIỆU BOT trước."
                self.after(0, lambda m=msg: self._set_advisor_status("Advisor API ERR", m))
                self._advisor_stdout_log(msg)
                self.log_message(f"[AI ADVISOR] API skipped: {msg}", error=True, target="manual")
                return

            response_file_var = getattr(
                self,
                "var_advisor_send_response_file",
                getattr(self, "var_advisor_send_previous_response", None),
            )
            include_response_file = bool(response_file_var.get()) if response_file_var else False

            try:
                estimate = estimate_api_payload(include_previous_response=include_response_file)
                status_msg = (
                    "Advisor API calling "
                    f"{estimate.get('model')} | "
                    f"tokens~{estimate.get('tokens')} | "
                    f"web={estimate.get('web_search_enabled')}"
                )
                self.after(0, lambda m=status_msg: self._set_advisor_status(m))
                self._advisor_stdout_log(
                    "API sending current Advisor files "
                    f"reason={reason} "
                    f"model={estimate.get('model')} "
                    f"chars={estimate.get('chars')} "
                    f"tokens~{estimate.get('tokens')} "
                    f"web_search={estimate.get('web_search_enabled')} "
                    f"include_response={include_response_file}"
                )
                self.log_message(
                    "[AI ADVISOR] API sending current Advisor files "
                    f"model={estimate.get('model')} "
                    f"chars={estimate.get('chars')} "
                    f"tokens~{estimate.get('tokens')} "
                    f"web_search={estimate.get('web_search_enabled')} "
                    f"include_response={include_response_file}",
                    target="manual",
                )
            except Exception as exc:
                self.log_message(f"[AI ADVISOR] API estimate warning: {exc}", error=True, target="manual")

            api_wait_stop = threading.Event()

            def api_wait_status():
                started = time.time()
                while not api_wait_stop.wait(15):
                    elapsed = int(time.time() - started)
                    mins = elapsed // 60
                    secs = elapsed % 60
                    self.after(
                        0,
                        lambda m=f"Advisor API calling... {mins}m{secs:02d}s": self._set_advisor_status(m),
                    )
                    self._advisor_stdout_log(f"API still waiting elapsed={mins}m{secs:02d}s")

            threading.Thread(target=api_wait_status, daemon=True).start()
            api_result = send_package_to_api(include_previous_response=include_response_file)
            if not api_result.get("ok"):
                err = api_result.get("error", "API failed")
                self.after(0, lambda e=err: self._set_advisor_status("Advisor API ERR", e))
                self._advisor_stdout_log(f"API failed: {err}")
                self.log_message(f"[AI ADVISOR] API skipped/failed: {err}", error=True, target="manual")
                return

            telegram_result = None
            try:
                from telegram_notify.reporter import send_advisor_response

                telegram_result = send_advisor_response(api_result.get("response"))
                if telegram_result.get("ok"):
                    self.log_message(
                        f"[TELEGRAM] Advisor response sent to report group ({telegram_result.get('sent', 0)} parts).",
                        target="manual",
                    )
                elif not telegram_result.get("skipped"):
                    self.log_message(
                        f"[TELEGRAM] Advisor report skipped/failed: {telegram_result.get('error', 'Telegram failed')}",
                        error=True,
                        target="manual",
                    )
            except Exception as exc:
                telegram_result = {"ok": False, "error": str(exc)}
                self.log_message(f"[TELEGRAM] Advisor report error: {exc}", error=True, target="manual")

            msg = "Advisor API OK"
            if telegram_result and telegram_result.get("ok"):
                msg += " | TG OK"
            elif telegram_result and not telegram_result.get("skipped"):
                msg += " | TG WARN"
            self.after(0, lambda m=msg: self._set_advisor_status(m))
            self._advisor_stdout_log(msg)
            self.log_message(f"[AI ADVISOR] {msg}", target="manual")
        except Exception as exc:
            self.after(0, lambda e=str(exc): self._set_advisor_status("Advisor API ERR", e))
            self._advisor_stdout_log(f"API error: {exc}")
            self.log_message(f"[AI ADVISOR] API error: {exc}", error=True, target="manual")
        finally:
            if api_wait_stop:
                api_wait_stop.set()
            self._advisor_worker_active = False

    def generate_advisor_package_ui(self):
        if self._advisor_worker_active:
            self._set_advisor_status("Advisor busy")
            return
        self._advisor_worker_active = True
        self._set_advisor_status("Advisor exporting...")
        threading.Thread(
            target=self._advisor_worker,
            kwargs={"send_api": False, "reason": "manual_button", "claimed": True},
            daemon=True,
        ).start()

    def send_advisor_api_now(self):
        if self._advisor_worker_active:
            self._set_advisor_status("Advisor busy")
            return
        self._advisor_worker_active = True
        self._set_advisor_status("Advisor API sending...")
        threading.Thread(
            target=self._advisor_api_worker,
            kwargs={"reason": "api_button", "claimed": True},
            daemon=True,
        ).start()

    def preview_advisor_api_payload(self):
        try:
            from ai_advisor.api_client import estimate_api_payload

            response_file_var = getattr(
                self,
                "var_advisor_send_response_file",
                getattr(self, "var_advisor_send_previous_response", None),
            )
            include_response_file = bool(response_file_var.get()) if response_file_var else False
            estimate = estimate_api_payload(include_previous_response=include_response_file)
            tokens = estimate.get("tokens", 0)
            cost = estimate.get("input_cost_usd", 0.0)
            out_2k = estimate.get("estimated_output_2k_usd", 0.0)
            out_4k = estimate.get("estimated_output_4k_usd", 0.0)
            model = estimate.get("model", "gpt-5.6")
            context_tokens = estimate.get("context_tokens", 0)
            max_output_tokens = estimate.get("max_output_tokens", 0)
            remaining_tokens = estimate.get("context_remaining_tokens", 0)
            context_status = "OK" if estimate.get("fits_context") else "TOO LARGE"
            web_status = "ON" if estimate.get("web_search_enabled") else "OFF"
            reasoning = str((estimate.get("settings") or {}).get("reasoning_effort", "medium"))
            text = (
                f"API payload: ~{tokens:,} input tokens\n"
                f"Input cost: ~${cost:.4f} | Output 2k/4k: ~${out_2k:.4f}/${out_4k:.4f}\n"
                f"Model: {model} | Context: {context_status} "
                f"(limit {context_tokens:,}, output reserve {max_output_tokens:,}, remain {remaining_tokens:,})\n"
                f"Reasoning: {reasoning} | Web Search: {web_status} | Tool cost is not included in this token preview"
            )
            detail_parts = []
            for item in estimate.get("breakdown", []):
                name = str(item.get("name") or "")
                marker = "[AUTO]" if name in {"technical_settings.json", "advisor_export.xlsx"} else "[EDIT]"
                detail_parts.append(
                    f"{marker} {name:<24} ~{int(item.get('tokens', 0)):>8,} tok   {int(item.get('chars', 0)):>10,} chars"
                )
            detail = "\n".join(detail_parts)
            self.advisor_api_preview_text = text
            self.advisor_api_preview_detail_text = detail
            label = getattr(self, "lbl_advisor_api_preview", None)
            if label and label.winfo_exists():
                label.configure(text=text, text_color="#E3F2FD")
            detail_label = getattr(self, "lbl_advisor_api_preview_detail", None)
            if detail_label and detail_label.winfo_exists():
                detail_label.configure(text=detail, text_color="#B3E5FC")
            self._set_advisor_status("API preview OK")
        except Exception as exc:
            text = f"API payload preview ERR: {exc}"
            self.advisor_api_preview_text = text
            self.advisor_api_preview_detail_text = ""
            label = getattr(self, "lbl_advisor_api_preview", None)
            if label and label.winfo_exists():
                label.configure(text=text, text_color=COL_RED)
            detail_label = getattr(self, "lbl_advisor_api_preview_detail", None)
            if detail_label and detail_label.winfo_exists():
                detail_label.configure(text="", text_color=COL_RED)
            self._set_advisor_status("API preview ERR", str(exc))

    def open_advisor_folder(self):
        try:
            from ai_advisor.paths import advisor_root, ensure_advisor_dirs

            ensure_advisor_dirs()
            os.startfile(advisor_root())
            self.log_message("[AI ADVISOR] Opened Advisor folder.", target="manual")
        except Exception as exc:
            self._set_advisor_status("Advisor folder ERR", str(exc))
            self.log_message(f"[AI ADVISOR] Cannot open folder: {exc}", error=True, target="manual")

    def _load_advisor_schedule_settings(self):
        try:
            from ai_advisor import schedule_settings

            saved = schedule_settings.load()
            self.var_advisor_mode.set(saved["mode"])
            self.var_advisor_fixed_time.set(saved["fixed_time"])
            self.var_advisor_export_days.set(str(saved["export_days"]))
            self.var_advisor_global_emergency.set(bool(saved["global_emergency"]))
            self.var_advisor_send_response_file.set(bool(saved["include_previous_response"]))
            self._advisor_last_fixed_date = str(saved.get("last_fixed_date") or "")
            self.var_ckcs_auto_report_morning.set(bool(saved["ckcs_auto_report_morning"]))
            self.var_ckcs_auto_report_afternoon.set(bool(saved["ckcs_auto_report_afternoon"]))
            self.var_ckcs_send_api_morning.set(bool(saved["ckcs_send_api_morning"]))
            self.var_ckcs_send_api_afternoon.set(bool(saved["ckcs_send_api_afternoon"]))
            self.var_ckcs_morning_time.set(saved["ckcs_morning_time"])
            self.var_ckcs_afternoon_time.set(saved["ckcs_afternoon_time"])
            self.var_ckcs_report_days.set(str(saved["ckcs_report_days"]))
            self.var_ckcs_liquidity_filter_enabled.set(
                bool(saved["ckcs_liquidity_filter_enabled"])
            )
            self.var_ckcs_liquidity_sessions.set(
                str(saved["ckcs_liquidity_sessions"])
            )
            self.var_ckcs_liquidity_min_billion.set(
                f"{float(saved['ckcs_liquidity_min_billion']):g}"
            )
            self._ckcs_last_morning_date = str(saved.get("ckcs_last_morning_date") or "")
            self._ckcs_last_afternoon_date = str(saved.get("ckcs_last_afternoon_date") or "")
            return saved
        except Exception as exc:
            main_logger.warning("[AI ADVISOR] Cannot load schedule settings: %s", exc)
            return None

    def save_advisor_schedule_settings(self, silent=False):
        try:
            from ai_advisor import schedule_settings

            saved = schedule_settings.save(
                {
                    "mode": self.var_advisor_mode.get(),
                    "fixed_time": self.var_advisor_fixed_time.get(),
                    "export_days": self.var_advisor_export_days.get(),
                    "global_emergency": self.var_advisor_global_emergency.get(),
                    "include_previous_response": self.var_advisor_send_response_file.get(),
                    "last_fixed_date": self._advisor_last_fixed_date,
                    "ckcs_auto_report_morning": self.var_ckcs_auto_report_morning.get(),
                    "ckcs_auto_report_afternoon": self.var_ckcs_auto_report_afternoon.get(),
                    "ckcs_send_api_morning": self.var_ckcs_send_api_morning.get(),
                    "ckcs_send_api_afternoon": self.var_ckcs_send_api_afternoon.get(),
                    "ckcs_morning_time": self.var_ckcs_morning_time.get(),
                    "ckcs_afternoon_time": self.var_ckcs_afternoon_time.get(),
                    "ckcs_report_days": self.var_ckcs_report_days.get(),
                    "ckcs_liquidity_filter_enabled": (
                        self.var_ckcs_liquidity_filter_enabled.get()
                    ),
                    "ckcs_liquidity_sessions": (
                        self.var_ckcs_liquidity_sessions.get()
                    ),
                    "ckcs_liquidity_min_billion": (
                        self.var_ckcs_liquidity_min_billion.get()
                    ),
                    "ckcs_last_morning_date": self._ckcs_last_morning_date,
                    "ckcs_last_afternoon_date": self._ckcs_last_afternoon_date,
                }
            )
            self.var_advisor_mode.set(saved["mode"])
            self.var_advisor_fixed_time.set(saved["fixed_time"])
            self.var_advisor_export_days.set(str(saved["export_days"]))
            self.var_ckcs_auto_report_morning.set(bool(saved["ckcs_auto_report_morning"]))
            self.var_ckcs_auto_report_afternoon.set(bool(saved["ckcs_auto_report_afternoon"]))
            self.var_ckcs_send_api_morning.set(bool(saved["ckcs_send_api_morning"]))
            self.var_ckcs_send_api_afternoon.set(bool(saved["ckcs_send_api_afternoon"]))
            self.var_ckcs_morning_time.set(saved["ckcs_morning_time"])
            self.var_ckcs_afternoon_time.set(saved["ckcs_afternoon_time"])
            self.var_ckcs_report_days.set(str(saved["ckcs_report_days"]))
            self.var_ckcs_liquidity_filter_enabled.set(
                bool(saved["ckcs_liquidity_filter_enabled"])
            )
            self.var_ckcs_liquidity_sessions.set(
                str(saved["ckcs_liquidity_sessions"])
            )
            self.var_ckcs_liquidity_min_billion.set(
                f"{float(saved['ckcs_liquidity_min_billion']):g}"
            )
            if not silent:
                self._set_advisor_status(
                    f"Đã lưu lịch Advisor | {saved['mode']}"
                    f"{(' | ' + saved['fixed_time']) if saved['fixed_time'] else ''}"
                )
                self._set_ckcs_api_status(
                    f"Đã lưu lịch CKCS | sáng {saved['ckcs_morning_time']} | "
                    f"chiều {saved['ckcs_afternoon_time']}"
                )
            return saved
        except Exception as exc:
            if not silent:
                self._set_advisor_status("Lưu lịch Advisor lỗi", str(exc))
                self._set_ckcs_api_status("Lưu lịch CKCS lỗi", str(exc))
            return None

    def run_advisor_triggers_tick(self):
        now = time.time()
        if now - self._advisor_last_trigger_check < 30:
            return
        self._advisor_last_trigger_check = now
        try:
            try:
                from telegram_notify.opportunity_alerts import run_scheduled_digest
                from telegram_notify.system_alerts import observe_global_cooldown
                from core import storage_manager as _storage_manager

                run_scheduled_digest(
                    log_cb=lambda msg, error=False: self.log_message(
                        msg, error=error, target="bot"
                    )
                )
                _state = _storage_manager.load_state()
                _global = ((_state.get("active_brake") or {}).get("global") or {})
                observe_global_cooldown(
                    float(_state.get("cooldown_until", 0.0) or 0.0),
                    str(_global.get("reason") or ""),
                    now=now,
                )
            except Exception as exc:
                main_logger.debug("Telegram schedule/status tick failed: %s", exc)

            today = time.strftime("%Y-%m-%d")
            current_hhmm = time.strftime("%H:%M")

            if self.var_advisor_mode.get() == "API Trigger" and not self._advisor_worker_active:
                fixed_time = (self.var_advisor_fixed_time.get() or "").strip()
                reasons = []
                if fixed_time and current_hhmm == fixed_time and self._advisor_last_fixed_date != today:
                    reasons.append("fixed_time_report")
                if self.var_advisor_global_emergency.get():
                    from ai_advisor.triggers import evaluate

                    reasons.extend(evaluate(getattr(self.trade_mgr, "state", {}), connector=self.connector))
                fresh = []
                for reason in sorted(set(reasons)):
                    last = self._advisor_last_trigger_fire.get(reason, 0.0)
                    if now - last >= 3600:
                        fresh.append(reason)
                        self._advisor_last_trigger_fire[reason] = now
                if fresh:
                    reason_text = "+".join(fresh)
                    if "fixed_time_report" in fresh:
                        self._advisor_last_fixed_date = today
                        self.save_advisor_schedule_settings(silent=True)
                    self.log_message(f"[AI ADVISOR] Trigger: {reason_text}", target="manual")
                    self._advisor_worker_active = True
                    threading.Thread(
                        target=self._advisor_worker,
                        kwargs={
                            "send_api": True,
                            "reason": f"trigger:{reason_text}",
                            "claimed": True,
                        },
                        daemon=True,
                    ).start()

            if self._ckcs_api_worker_active or self._advisor_worker_active:
                return
            from core.market_calendar import date_status

            calendar_state = str((date_status() or {}).get("status") or "UNKNOWN").upper()
            if calendar_state in {"HOLIDAY", "WEEKEND"}:
                return

            def _minutes(value):
                hour, minute = str(value).split(":", 1)
                return int(hour) * 60 + int(minute)

            current_minutes = _minutes(current_hhmm)
            jobs = [
                (
                    "morning",
                    self.var_ckcs_auto_report_morning.get(),
                    self.var_ckcs_send_api_morning.get(),
                    self.var_ckcs_morning_time.get(),
                    self._ckcs_last_morning_date,
                ),
                (
                    "afternoon",
                    self.var_ckcs_auto_report_afternoon.get(),
                    self.var_ckcs_send_api_afternoon.get(),
                    self.var_ckcs_afternoon_time.get(),
                    self._ckcs_last_afternoon_date,
                ),
            ]
            for session, enabled, send_api, target_time, last_date in jobs:
                if not enabled or last_date == today or now < self._ckcs_auto_retry_after.get(session, 0.0):
                    continue
                target_minutes = _minutes(target_time)
                if current_minutes < target_minutes:
                    continue
                if session == "morning" and current_minutes >= 13 * 60:
                    continue
                self._ckcs_api_worker_active = True
                self.log_message(
                    f"[CKCS API] Lịch {session}: tạo báo cáo"
                    f"{' + gửi API' if send_api else ''}",
                    target="manual",
                )
                threading.Thread(
                    target=self._ckcs_session_worker,
                    kwargs={"session": session, "send_api": bool(send_api), "reason": "schedule"},
                    daemon=True,
                ).start()
                break
        except Exception as exc:
            self.log_message(f"[AI ADVISOR] Trigger check error: {exc}", error=True, target="manual")

    def log_message(self, msg, error=False, target="manual"):
        if target in ("grid", "grid-log", "hedge", "hedge-log"):
            target = "bot-log"
        if "Retcode: 10025" in msg:
            return

        ts = time.strftime("%H:%M:%S")
        txt = f"[{ts}] {msg}\n"

        if "PnL: +" in msg or "SUCCESS" in msg or "Hợp" in msg:
            tag = "SUCCESS"
        elif "PnL: -" in msg or error or "ERR" in msg or "FAIL" in msg:
            tag = "ERROR"
        elif "Đóng lệnh" in msg:
            tag = "INFO"
        elif "BUY" in msg:
            tag = "SUCCESS"
        elif "SELL" in msg:
            tag = "ERROR"
        else:
            tag = "INFO"

        # Tự động định tuyến log của bot vào 2 Tab (BOT và BOT-LOG).
        bot_markers = ("[BOT]", "[BOT]_", "[BOT-DCA]", "[BOT-PCA]", "AUTO_DCA", "AUTO_PCA", "BOT SAFEGUARD")
        if target == "manual" and any(k in msg for k in bot_markers):
            target = "bot"
        if target == "manual" and "[USER EXEC]" in msg:
            target = "manual"

        if target == "bot":
            if "[BOT EXEC]" in msg or "[TSL]" in msg:
                target = "bot"
            elif any(
                k in msg
                for k in ["🚀", "Đóng lệnh", "Bóp cò", "PnL", "SUCCESS", "FAIL"]
            ):
                target = "bot"  # Lệnh thực thi -> Sang Tab BOT
            else:
                target = "bot-log"  # Log logic/check -> Sang Tab BOT-LOG

        self.after(0, lambda: self._write_log(txt, tag, target))

    def _log_target_from_tab_name(self, tab_name):
        tab_name = str(tab_name or "").replace(" *", "")
        if "Preview" in tab_name:
            return "preview"
        if "API Health" in tab_name:
            return "api-health"
        if "Bot-Log" in tab_name:
            return "bot-log"
        if "Bot" in tab_name:
            return "bot"
        return "manual"

    def refresh_api_health_panel(self):
        try:
            connector_health = {}
            if hasattr(self.connector, "get_api_health_snapshot"):
                connector_health = self.connector.get_api_health_snapshot()
            engine_health = {}
            try:
                engine_health = data_engine.get_api_health_snapshot()
            except Exception:
                engine_health = {}

            daemon_health = self.market_data_health if isinstance(self.market_data_health, dict) else {}
            if daemon_health:
                engine_health = daemon_health
            market_connector_health = engine_health.get("connector", {}) if isinstance(engine_health, dict) else {}

            cache = engine_health.get("cache", {}) if isinstance(engine_health, dict) else {}
            sizes = engine_health.get("cache_sizes", {}) if isinstance(engine_health, dict) else {}
            ws_health = engine_health.get("ws", {}) if isinstance(engine_health, dict) else {}
            total = int(connector_health.get("total_requests", 0) or 0) + int(market_connector_health.get("total_requests", 0) or 0)
            started = min(
                float(connector_health.get("started_at", time.time()) or time.time()),
                float(market_connector_health.get("started_at", time.time()) or time.time()),
            )
            elapsed_min = max(1.0 / 60.0, (time.time() - started) / 60.0)
            rpm = total / elapsed_min
            last_endpoint = connector_health.get("last_endpoint", "-")
            last_status = connector_health.get("last_status", "-")

            if hasattr(self, "lbl_api_health_summary"):
                self.lbl_api_health_summary.configure(
                    text=f"API Health | req {total} | {rpm:.1f}/min | last {last_status} {last_endpoint}"
                )

            def _pct_bar(value, limit, width=24):
                try:
                    ratio = 0.0 if float(limit or 0.0) <= 0 else min(1.0, max(0.0, float(value or 0.0) / float(limit)))
                except Exception:
                    ratio = 0.0
                filled = int(round(ratio * width))
                return ("█" * filled) + ("░" * (width - filled)), ratio * 100.0

            def _hit_rate(hit, miss):
                hit = int(hit or 0)
                miss = int(miss or 0)
                total_calls = hit + miss
                return 0.0 if total_calls <= 0 else (hit / total_calls) * 100.0

            tick_hit_rate = _hit_rate(cache.get("tick_hits", 0), cache.get("tick_misses", 0))
            ohlc_hit_rate = _hit_rate(cache.get("ohlc_hits", 0), cache.get("ohlc_misses", 0))
            tick_bar, tick_pct = _pct_bar(tick_hit_rate, 100.0, 18)
            ohlc_bar, ohlc_pct = _pct_bar(ohlc_hit_rate, 100.0, 18)
            latency = float(connector_health.get("last_latency_ms", 0.0) or 0.0)
            selected_symbol = str(self.cbo_symbol.get() if hasattr(self, "cbo_symbol") else "").upper()
            selected_tick = self._shared_market_tick(selected_symbol) if selected_symbol else None
            selected_age = None
            if selected_tick:
                if selected_tick.get("age_seconds") is not None:
                    selected_age = max(0.0, float(selected_tick.get("age_seconds") or 0.0))
                else:
                    selected_ts = float(selected_tick.get("timestamp", 0.0) or 0.0)
                    selected_age = max(0.0, time.time() - selected_ts) if selected_ts > 0 else None
            last_market_ts = float(ws_health.get("last_market_message_ts", 0.0) or 0.0)
            last_market_age = max(0.0, time.time() - last_market_ts) if last_market_ts else None
            rpm_bar, rpm_pct = _pct_bar(rpm, 120.0, 18)
            latency_bar, latency_pct = _pct_bar(latency, 1000.0, 18)

            lines = [
                "LIVE",
                f"  Requests     {total:>8}",
                f"  Req/min      {rpm:>8.2f}  {rpm_bar} {rpm_pct:>5.1f}%",
                f"  Latency      {latency:>8.1f} ms {latency_bar} {latency_pct:>5.1f}%",
                f"  Last         {last_status} {last_endpoint}",
                "",
                "CACHE HIT",
                f"  Tick         {tick_bar} {tick_pct:>5.1f}%  {cache.get('tick_hits', 0)}/{cache.get('tick_misses', 0)}",
                f"  OHLC         {ohlc_bar} {ohlc_pct:>5.1f}%  {cache.get('ohlc_hits', 0)}/{cache.get('ohlc_misses', 0)}",
                "",
                "CACHE TTL",
                f"  Tick/OHLC    {getattr(config, 'DNSE_TICK_CACHE_TTL_SECONDS', 2.0)}s / {getattr(config, 'DNSE_OHLC_CACHE_TTL_SECONDS', 30.0)}s",
                f"  Acc/Pos      {getattr(config, 'DNSE_ACCOUNT_CACHE_TTL_SECONDS', 5.0)}s / {getattr(config, 'DNSE_POSITIONS_CACHE_TTL_SECONDS', 5.0)}s",
                f"  Size         tick={sizes.get('ticks', 0)} ohlc={sizes.get('ohlc', 0)}",
                "",
                "WEBSOCKET",
                f"  Owner/Role   PID {engine_health.get('owner_pid', '--')} / {engine_health.get('role', 'CONSUMER')}",
                f"  Market state {engine_health.get('market_state', self._shared_market_state())}",
                f"  Broker API   {connector_health.get('broker_api_state', 'LIVE')} ({int(connector_health.get('broker_api_consecutive_failures', 0) or 0)} lỗi liên tiếp)",
                f"  Mode/State   {ws_health.get('mode', 'off')} / {'CONNECTED' if ws_health.get('connected') else 'OFFLINE'}",
                f"  Market feed  {'ON' if ws_health.get('market_data_enabled') else 'SLEEP'} · trading feed {'ON' if ws_health.get('connected') else 'OFF'}",
                f"  Auth/Sub     {bool(ws_health.get('authenticated'))} / {len(ws_health.get('subscribed', []) or [])}",
                f"  Messages     {int(ws_health.get('messages', 0) or 0)} · reconnect {int(ws_health.get('reconnects', 0) or 0)}",
                f"  Events       order={int(ws_health.get('order_events', 0) or 0)} position={int(ws_health.get('position_events', 0) or 0)}",
                f"  Ping/Pong    {ws_health.get('last_ping_age_seconds')}s / {ws_health.get('last_pong_age_seconds')}s ago",
                f"  Market msg   {last_market_age if last_market_age is not None else '--'}s ago",
                f"  Selected     {selected_symbol or '--'} · {(selected_tick or {}).get('source', '--')} · age {selected_age if selected_age is not None else '--'}s",
            ]
            if ws_health.get("last_error"):
                lines.append(f"  WS error     {ws_health.get('last_error')}")
            rate_limits = {
                **(connector_health.get("rate_limits", {}) or {}),
                **(market_connector_health.get("rate_limits", {}) or {}),
            }
            throttled = {
                **(connector_health.get("throttled_endpoints", {}) or {}),
                **(market_connector_health.get("throttled_endpoints", {}) or {}),
            }
            now_ts = time.time()
            throttle_wait = max(
                [max(0, int(float(until) - now_ts)) for until in throttled.values()] or [0]
            )
            market_state = str(
                engine_health.get("market_state", self._shared_market_state()) or "RECOVERING"
            ).upper().replace("_", " ")
            selected_source = str((selected_tick or {}).get("source") or "--").upper()
            if market_state == "SLEEP" and selected_source == "--":
                selected_source = "SLEEP"
            ws_online = bool(ws_health.get("connected") and ws_health.get("authenticated"))
            rest_active = market_state == "REST FALLBACK"
            rest_label = "ĐANG DÙNG" if rest_active else (f"CHỜ {throttle_wait}s" if throttle_wait else "DỰ PHÒNG")
            bot_entry_blocked = market_state in {"RECOVERING", "MARKET DATA DOWN", "SLEEP"}
            suppressed_429 = int(connector_health.get("suppressed_429", 0) or 0) + int(
                market_connector_health.get("suppressed_429", 0) or 0
            )
            state_color = {
                "LIVE": "#00C853",
                "REST FALLBACK": "#FFAB00",
                "RECOVERING": "#FFAB00",
                "MARKET DATA DOWN": "#D50000",
                "SLEEP": "#90A4AE",
            }.get(market_state, "white")
            source_color = {
                "WS": "#00C853",
                "REST": "#FFAB00",
                "CACHE": "#FF7043",
                "SLEEP": "#90A4AE",
            }.get(selected_source, "#B0BEC5")

            if hasattr(self, "lbl_api_health_state"):
                self.lbl_api_health_state.configure(text=market_state, text_color=state_color)
            if hasattr(self, "lbl_api_health_source"):
                age_text = "--" if selected_age is None else f"{selected_age:.1f}s"
                self.lbl_api_health_source.configure(
                    text=f"{selected_source} · {age_text}", text_color=source_color
                )
            if hasattr(self, "lbl_api_health_ws"):
                self.lbl_api_health_ws.configure(
                    text="ONLINE" if ws_online else "OFFLINE",
                    text_color="#00C853" if ws_online else "#D50000",
                )
            if hasattr(self, "lbl_api_health_rest"):
                self.lbl_api_health_rest.configure(
                    text=rest_label,
                    text_color="#FFAB00" if rest_active or throttle_wait else "#90A4AE",
                )
            if hasattr(self, "lbl_api_health_summary"):
                self.lbl_api_health_summary.configure(
                    text=(
                        f"{selected_symbol or '--'} · {selected_source} · {market_state} | "
                        f"BOT ENTRY {'TẠM CHẶN' if bot_entry_blocked else 'SẴN SÀNG'} | "
                        f"429: {suppressed_429}"
                    ),
                    text_color=state_color,
                )

            lines[0:0] = [
                "TÓM TẮT NGUỒN GIÁ",
                f"  Mã/Nguồn     {selected_symbol or '--'} / {selected_source}",
                f"  Trạng thái   {market_state}",
                f"  BOT ENTRY    {'TẠM CHẶN' if bot_entry_blocked else 'SẴN SÀNG'}",
                f"  REST         {rest_label} · 429 gộp {suppressed_429}",
                "",
            ]

            lines.extend(["", "RATE LIMIT THỰC TẾ"])
            if rate_limits:
                for endpoint, quota in sorted(rate_limits.items()):
                    lines.append(
                        f"  {endpoint} remaining={quota.get('remaining')} limit={quota.get('limit')} reset={quota.get('reset_at')}"
                    )
            else:
                lines.append("  DNSE chưa trả header hạn mức")
            for endpoint, until in sorted(throttled.items()):
                lines.append(f"  ĐANG CHỜ {endpoint} · {max(0, int(float(until) - time.time()))}s")
            lines.append(f"  429 đã gộp {suppressed_429}")
            if connector_health.get("last_error"):
                lines.extend(["", f"ERROR {connector_health.get('last_error')}"])
            lines.extend(["", "ENDPOINTS"])
            by_endpoint = dict(connector_health.get("by_endpoint", {}) or {})
            for endpoint, count in (market_connector_health.get("by_endpoint", {}) or {}).items():
                by_endpoint[endpoint] = int(by_endpoint.get(endpoint, 0) or 0) + int(count or 0)
            if by_endpoint:
                for endpoint, count in sorted(by_endpoint.items(), key=lambda item: str(item[0])):
                    lines.append(f"  {count:5d}  {endpoint}")
            else:
                lines.append("  --")

            widget = getattr(self, "txt_api_health", None)
            if widget and widget.winfo_exists():
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.insert("end", "\n".join(lines))
                widget.configure(state="disabled")

            endpoint_rows = sorted(by_endpoint.items(), key=lambda item: int(item[1] or 0), reverse=True)
            max_endpoint_count = max([int(count or 0) for _, count in endpoint_rows] or [1])
            detail_lines = ["ENDPOINT CHART"]
            if endpoint_rows:
                for endpoint, count in endpoint_rows[:12]:
                    bar, pct = _pct_bar(int(count or 0), max_endpoint_count, 26)
                    name = str(endpoint).replace("https://services.entrade.com.vn", "")
                    detail_lines.append(f"{count:5d} {bar} {name[:46]}")
            else:
                detail_lines.append("No API request yet")
            detail_lines.extend([
                "",
                "CÁCH ĐỌC",
                "WS     DNSE đẩy giá realtime về app.",
                "REST   App hỏi giá theo lượt, chỉ dùng dự phòng.",
                "CACHE  Giá gần nhất; kiểm tra độ cũ trước khi dùng.",
                "",
                f"Request hiện tại  {rpm:.2f}/phút",
                f"REST fallback     {int(cache.get('rest_fallbacks', 0) or 0)} lượt",
                f"Reconnect WS      {int(ws_health.get('reconnects', 0) or 0)} lần",
                f"429 đã gộp        {suppressed_429}",
            ])
            detail_widget = getattr(self, "txt_api_health_detail", None)
            if detail_widget and detail_widget.winfo_exists():
                detail_widget.configure(state="normal")
                detail_widget.delete("1.0", "end")
                detail_widget.insert("end", "\n".join(detail_lines))
                detail_widget.configure(state="disabled")
        except Exception as exc:
            widget = getattr(self, "txt_api_health", None)
            if widget and widget.winfo_exists():
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.insert("end", f"API Health error: {exc}")
                widget.configure(state="disabled")
        finally:
            self._schedule_api_health_refresh()

    def _schedule_api_health_refresh(self):
        previous = getattr(self, "_api_health_refresh_job", None)
        if previous:
            try:
                self.after_cancel(previous)
            except Exception:
                pass
        self._api_health_refresh_job = None
        try:
            tabview = getattr(self, "log_tabview", None)
            if tabview and "API Health" in str(tabview.get()):
                self._api_health_refresh_job = self.after(2000, self.refresh_api_health_panel)
        except Exception:
            self._api_health_refresh_job = None

    def _set_log_tab_unread(self, target, unread):
        tabview = getattr(self, "log_tabview", None)
        keys = getattr(self, "log_tab_keys", {})
        if not tabview or target not in keys:
            return
        self.log_tab_unread[target] = bool(unread)
        base = keys[target]
        label = f"{base} *" if unread else base
        try:
            buttons = tabview._segmented_button._buttons_dict
            for key, candidate in buttons.items():
                try:
                    current = str(candidate.cget("text"))
                    clean = current.replace(" *", "")
                    if key == base or clean == base:
                        candidate.configure(text=label)
                except Exception:
                    pass
        except Exception:
            pass

    def clear_active_log_unread(self):
        tabview = getattr(self, "log_tabview", None)
        if not tabview:
            return
        active = tabview.get().replace(" *", "")
        self._set_log_tab_unread(self._log_target_from_tab_name(active), False)

    def clear_log_unread_by_tab_name(self, tab_name):
        clean = str(tab_name or "").replace(" *", "")
        self._set_log_tab_unread(self._log_target_from_tab_name(clean), False)
        self.after(50, self.clear_active_log_unread)

    def _write_log(self, txt, tag, target="manual"):
        if target in ("grid", "grid-log", "hedge", "hedge-log"):
            target = "bot-log"
        if target == "bot":
            widget = getattr(self, "txt_log_bot", None)
        elif target == "bot-log":
            # Fallback về txt_log_bot nếu Ngài chưa tạo Text widget cho bot_log
            widget = getattr(
                self, "txt_log_bot_log", getattr(self, "txt_log_bot", None)
            )
        else:
            widget = getattr(self, "txt_log_manual", None)

        if widget and widget.winfo_exists():
            widget.configure(state="normal")
            widget.insert("end", txt, tag)
            widget.see("end")
            widget.configure(state="disabled")
            tabview = getattr(self, "log_tabview", None)
            if tabview and self._log_target_from_tab_name(tabview.get()) != target:
                self._set_log_tab_unread(target, True)
            if tabview:
                self.after(50, self.clear_active_log_unread)

    def reset_daily_stats(self):
        if messagebox.askyesno("Xác nhận", "Tạo Phiên/Group mới (Clear Cache)?", parent=self):
            self.trade_mgr.state.update(
                {
                    "pnl_today": 0.0,
                    "fee_today": 0.0,
                    "trades_today_count": 0,
                    "daily_loss_count": 0,
                    "bot_pnl_today": 0.0,
                    "bot_trades_today": 0,
                    "bot_daily_loss_count": 0,
                    "bot_losing_streak": 0,
                    "bot_symbol_losing_streak": {},
                    "manual_pnl_today": 0.0,
                    "manual_trades_today": 0,
                    "manual_daily_loss_count": 0,
                    "losing_streak": 0,
                    "cooldown_until": 0.0,
                    "active_brake": {"global": None, "symbols": {}},
                    "bot_last_entry_times": {},
                    "bot_last_fail_times": {},
                    "pending_entry_exit": {},
                    "last_close_times": {},
                    "trade_excursions": {},
                    "anti_cash_locks": {},
                    "be_sl_locks": {},
                    "be_sl_arms": {},
                    "current_session_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
                }
            )
            save_state(self.trade_mgr.state)
            try:
                import core.storage_manager as storage_manager

                signal_file = os.path.join(
                    storage_manager._active_account_dir, "live_signals.json"
                )
                if os.path.exists(signal_file):
                    with open(signal_file, "r", encoding="utf-8") as f:
                        signal_payload = json.load(f)
                    signal_payload["pending_signals"] = []
                    with open(signal_file, "w", encoding="utf-8") as f:
                        json.dump(signal_payload, f, indent=4, ensure_ascii=False)
            except Exception:
                pass
            if hasattr(self, "signal_listener"):
                self.signal_listener.processed_signals.clear()
                self.signal_listener.last_safeguard_reason.clear()
                self.signal_listener.last_safeguard_time.clear()
            if self.daemon_process:
                try:
                    self.daemon_process.terminate()
                    self.daemon_process.wait(timeout=5)
                except Exception:
                    pass
                daemon_output = getattr(self, "daemon_output_file", None)
                if daemon_output:
                    try:
                        daemon_output.close()
                    except Exception:
                        pass
                self.start_daemon_process()
            self.log_message("🔄 Đã xóa Cache và tạo Phiên/Group mới.", target="bot")

    def close_all_trades(self):
        tree = self._current_running_tree()
        visible_ids = {
            str(item)
            for item in tree.get_children()
            if not str(item).startswith(("LOCAL:", "ORDER:", "OPP:"))
        }
        snapshot = list(getattr(self, "_ui_all_positions_snapshot", []) or [])
        if not snapshot:
            snapshot = BotUI._get_all_mode_positions(self, refresh_real=True)
        positions = [
            pos
            for pos in snapshot
            if str(getattr(pos, "ticket", "") or "") in visible_ids
        ]
        if not positions:
            return
        if self.var_confirm_close.get() and not messagebox.askyesno(
            "Xác nhận", "ĐÓNG TOÀN BỘ LỆNH?", parent=self
        ):
            return
        for position in positions:
            self._queue_or_execute_position_close(position)

    def _choose_pending_close_mode(self, position):
        if not hasattr(self, "wait_window"):
            return {"entry_mode": "MARKET", "limit_price": 0.0}
        return ui_popups.prompt_pending_close_mode(self, position)

    def _queue_or_execute_position_close(self, position):
        from core.market_hours import market_session_phase

        ticket = str(
            getattr(position, "position_id", None)
            or getattr(position, "ticket", "")
            or ""
        )
        symbol = str(getattr(position, "symbol", "") or "").upper()
        phase = market_session_phase(symbol)[0]
        if phase != "OPEN":
            close_choice = BotUI._choose_pending_close_mode(self, position)
            if not close_choice:
                return None
            close_side = (
                "SELL"
                if int(getattr(position, "type", 0) or 0) == 0
                else "BUY"
            )
            execution_mode = (
                "PAPER" if ticket.upper().startswith("PAPER-") else "REAL"
            )
            pending = pending_orders.add_close_order(
                symbol=symbol,
                position_ticket=ticket,
                side=close_side,
                lot=float(getattr(position, "volume", 0.0) or 0.0),
                execution_mode=execution_mode,
                comment="Manual_Close",
                entry_mode=close_choice.get("entry_mode", "MARKET"),
                limit_price=float(close_choice.get("limit_price", 0.0) or 0.0),
            )
            close_label = str(pending.get("entry_mode", "MARKET") or "MARKET")
            if close_label == "LIMIT":
                close_label += f" @{self._fmt_price(pending.get('entry_price', 0.0), symbol)}"
            self.log_message(
                f"[ĐÓNG LỆNH] Ngoài phiên: đã cache {symbol} #{ticket} "
                f"{close_label} đến phiên OPEN (id={str(pending.get('id', ''))[:8]}).",
                target="manual",
            )
            return pending

        if not self._ensure_trading_otp_for_mode(
            BotUI._position_is_paper(position)
        ):
            return None

        position_manager = BotUI._trade_manager_for_position(self, position)
        position_manager.set_exit_reason(position.ticket, "Manual_Close")

        def _close_now():
            try:
                result = BotUI._close_position_for_mode(
                    self,
                    position,
                    "Manual_Close",
                )
                if getattr(result, "ok", False):
                    self.log_message(
                        f"[ĐÓNG LỆNH] Đã đóng {symbol} #{ticket}.",
                        target="manual",
                    )
                else:
                    message = (
                        getattr(result, "message", "")
                        or getattr(result, "error", "")
                        or "CLOSE_FAILED"
                    )
                    self.log_message(
                        f"[ĐÓNG LỆNH] Không đóng được {symbol} #{ticket}: {message}",
                        error=True,
                        target="manual",
                    )
            except Exception as exc:
                self.log_message(
                    f"[ĐÓNG LỆNH] Lỗi đóng {symbol} #{ticket}: {exc}",
                    error=True,
                    target="manual",
                )

        threading.Thread(target=_close_now, daemon=True).start()
        return None

    def _pending_row_item(self, row_id):
        if not str(row_id).startswith("LOCAL:"):
            return None
        order_id = str(row_id).split(":", 1)[1]
        for item in pending_orders.list_all():
            if str(item.get("id")) == order_id:
                return item
        return None

    def _current_running_tree(self):
        tabs = getattr(self, "running_tabs", None)
        trees = getattr(self, "running_trees", None) or {}
        if tabs and trees:
            return trees.get(tabs.get(), self.tree)
        return self.tree

    def _delete_running_row_everywhere(self, row_id):
        for tree in (getattr(self, "running_trees", None) or {"current": self.tree}).values():
            try:
                if tree.exists(str(row_id)):
                    tree.delete(str(row_id))
            except Exception:
                pass

    def on_show_bot_opportunities_change(self):
        """Ẩn/hiện gợi ý mà không xóa cache hay lịch sử của chúng."""
        visible = bool(self.var_show_bot_opportunities.get())
        if not visible:
            for tree in (getattr(self, "running_trees", None) or {}).values():
                for row_id in list(tree.get_children("")):
                    if str(row_id).startswith("OPP:"):
                        tree.delete(row_id)
        try:
            from core import signal_opportunities, storage_manager

            brain = storage_manager.load_brain_settings()
            settings = signal_opportunities.normalize_settings(brain.get("opportunity_settings", {}))
            settings["show_in_running_table"] = visible
            brain["opportunity_settings"] = settings
            storage_manager.save_brain_settings(brain)
        except Exception as exc:
            main_logger.debug("save opportunity visibility failed: %s", exc)

    def show_running_color_legend(self):
        ui_popups.show_running_color_legend(self)

    def _opportunity_row_item(self, row_id):
        if not str(row_id).startswith("OPP:"):
            return None
        try:
            from core import signal_opportunities
            return signal_opportunities.get(str(row_id).split(":", 1)[1])
        except Exception:
            return None

    def open_opportunity_popup(self, row_id):
        item = self._opportunity_row_item(row_id)
        if not item:
            return
        from core import signal_opportunities

        settings = signal_opportunities.load_settings()
        symbol = str(item.get("symbol", "") or "").upper()
        side = str(item.get("side", "BUY") or "BUY").upper()
        setup = item.get("order_setup", {}) if isinstance(item.get("order_setup"), dict) else {}
        top = ctk.CTkToplevel(self)
        top.title(f"Kích hoạt gợi ý {side} {symbol}")
        screen_w = max(800, int(top.winfo_screenwidth() or 800))
        screen_h = max(600, int(top.winfo_screenheight() or 600))
        width = min(620, max(560, screen_w - 60))
        height = min(750, max(500, screen_h - 100))
        top.geometry(
            f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}"
        )
        top.transient(self)
        top.grab_set()

        ctk.CTkLabel(top, text=f"GỢI Ý BOT — {side} {symbol}", font=("Roboto", 18, "bold"), text_color="#CE93D8").pack(pady=(16, 4))
        ctk.CTkLabel(
            top,
            text="Đây là lệnh BOT đã tính đủ nhưng chưa gửi mua. Kiểm tra/chỉnh rồi bấm KÍCH HOẠT.",
            text_color="#B0BEC5",
        ).pack(pady=(0, 12))
        form = ctk.CTkFrame(top, fg_color="#252526")
        form.pack(fill="x", padx=18, pady=6)

        def field(row, label, value):
            ctk.CTkLabel(form, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=7)
            entry = ctk.CTkEntry(form, width=210, justify="center")
            entry.insert(0, str(value))
            entry.grid(row=row, column=1, sticky="w", padx=8, pady=7)
            return entry

        ctk.CTkLabel(form, text="Kiểu vào:").grid(row=0, column=0, sticky="w", padx=12, pady=7)
        cbo_mode = ctk.CTkOptionMenu(form, values=["MARKET", "LIMIT"], width=210)
        cbo_mode.set(str(setup.get("entry_mode") or settings["default_order_mode"]).upper())
        cbo_mode.grid(row=0, column=1, sticky="w", padx=8, pady=7)
        default_price = float(setup.get("price", item.get("last_price", item.get("detected_price", 0.0))) or 0.0)
        e_price = field(1, "Giá LIMIT chờ:", self._fmt_price(default_price, symbol) if default_price > 0 else "")
        e_slip = field(2, "Chệch tối đa (bước giá):", settings["default_slippage_ticks"])
        default_lot = float(setup.get("lot", 0.0) or 0.0)
        default_sl = float(setup.get("sl", 0.0) or 0.0)
        default_tp = float(setup.get("tp", 0.0) or 0.0)
        e_lot = field(3, f"Số lượng ({self._quantity_unit(symbol)}; 0=AUTO):", f"{default_lot:g}")
        e_sl = field(4, "SL (0=AUTO):", self._fmt_price(default_sl, symbol) if default_sl > 0 else "0")
        e_tp = field(5, "TP (0=AUTO):", self._fmt_price(default_tp, symbol) if default_tp > 0 else "0")
        e_hours = field(6, "Hết hạn sau (giờ):", settings["retention_hours"])
        e_tactic = field(7, "Quản lý lệnh (TSL):", str(setup.get("tactic", "OFF") or "OFF"))
        e_ee = field(8, "Cách vào/thoát (E/E):", str(setup.get("entry_exit_tactic", "OFF") or "OFF"))
        ctk.CTkLabel(
            form,
            text=(
                "MARKET: mua theo giá đang chạy. LIMIT: chỉ kích hoạt khi giá chạm vùng đã đặt; "
                "giá gửi được làm tròn về bước giá gần nhất và không vượt mức chệch."
            ),
            wraplength=500,
            justify="left",
            text_color="#FFD54F",
        ).grid(row=9, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 12))
        lbl = ctk.CTkLabel(top, text="", text_color="#E57373")
        lbl.pack(pady=4)

        def activate():
            try:
                entry_mode = cbo_mode.get().upper()
                trigger = self._price_input_to_internal(e_price.get(), symbol) if entry_mode == "LIMIT" else 0.0
                if entry_mode == "LIMIT" and trigger <= 0:
                    raise ValueError("LIMIT phải có giá chờ lớn hơn 0")
                lot = float(e_lot.get() or 0.0)
                sl = self._price_input_to_internal(e_sl.get(), symbol)
                tp = self._price_input_to_internal(e_tp.get(), symbol)
                ticks = max(0, int(e_slip.get() or 0))
                hours = max(0.05, float(e_hours.get() or settings["retention_hours"]))
                tactic = str(e_tactic.get() or "OFF").strip().upper()
                entry_exit_tactic = str(e_ee.get() or "OFF").strip().upper()
                plan = (
                    f"WAIT @{trigger:g} ±{ticks} tick -> DNSE LO gần nhất"
                    if entry_mode == "LIMIT" else "OPEN -> giá đang chạy"
                )
                pending = pending_orders.add_order(
                    symbol=symbol,
                    side=side,
                    preset=getattr(config, "DEFAULT_PRESET", "SCALPING"),
                    lot=lot,
                    entry_price=trigger,
                    sl=sl,
                    tp=tp,
                    target="OPEN",
                    note=f"TSL={tactic}",
                    expire_hours=hours,
                    manual_entry_tactic=entry_exit_tactic,
                    lot_source="BOT_RISK" if lot > 0 else "AUTO_RISK",
                    sl_source=str(setup.get("sl_source", "BOT_PREVIEW") or "BOT_PREVIEW"),
                    tp_source=str(setup.get("tp_source", "BOT_PREVIEW") or "BOT_PREVIEW"),
                    entry_source="BOT_OPPORTUNITY",
                    plan=plan,
                    execution_mode=item.get("execution_mode", "PAPER"),
                    entry_mode=entry_mode,
                    wait_for_trigger=(entry_mode == "LIMIT"),
                    trigger_price=trigger,
                    slippage_ticks=ticks,
                    opportunity_id=item.get("id", ""),
                )
                signal_opportunities.finalize(
                    item.get("id"),
                    signal_opportunities.ACTIVATED,
                    "Người dùng kích hoạt",
                    pending_order_id=pending.get("id"),
                    selected_entry_mode=entry_mode,
                    selected_trigger_price=trigger,
                    selected_slippage_ticks=ticks,
                )
                self._delete_running_row_everywhere(row_id)
                self.log_message(f"[GỢI Ý BOT] Đã kích hoạt {side} {symbol}: {plan}", target="manual")
                top.destroy()
            except Exception as exc:
                lbl.configure(text=f"Không kích hoạt được: {exc}")

        ctk.CTkButton(top, text="KÍCH HOẠT", height=42, fg_color="#6A1B9A", command=activate).pack(fill="x", padx=18, pady=(8, 4))
        ctk.CTkButton(top, text="ĐÓNG", height=34, fg_color="#455A64", command=top.destroy).pack(fill="x", padx=18, pady=4)

    def _active_pending_close_for_ticket(self, ticket):
        mode = "PAPER" if str(ticket or "").upper().startswith("PAPER") else "REAL"
        try:
            return pending_orders.find_active_close(ticket, execution_mode=mode)
        except Exception:
            return None

    def _running_selection_action_summary(self, row_ids):
        counts = {
            "close": 0,
            "cancel_close": 0,
            "cancel_local": 0,
            "cancel_dnse": 0,
            "delete_opportunity": 0,
            "delete_final": 0,
            "busy": 0,
        }
        for raw_id in row_ids or ():
            row_id = str(raw_id or "")
            if row_id.startswith("OPP:"):
                counts["delete_opportunity"] += 1
            elif row_id.startswith("LOCAL:"):
                item = self._pending_row_item(row_id) or {}
                status = str(item.get("status", "") or "").upper()
                if status == pending_orders.SENDING:
                    counts["busy"] += 1
                elif status in pending_orders.FINAL_STATUSES:
                    counts["delete_final"] += 1
                else:
                    counts["cancel_local"] += 1
            elif row_id.startswith("ORDER:"):
                counts["cancel_dnse"] += 1
            else:
                pending_close = self._active_pending_close_for_ticket(row_id)
                if pending_close:
                    status = str(pending_close.get("status", "") or "").upper()
                    if status == pending_orders.SENDING:
                        counts["busy"] += 1
                    else:
                        counts["cancel_close"] += 1
                else:
                    counts["close"] += 1

        parts = []
        labels = (
            ("close", "Đóng {n} vị thế"),
            ("cancel_close", "Hủy đóng {n} vị thế"),
            ("cancel_local", "Hủy {n} lệnh chờ"),
            ("cancel_dnse", "Hủy {n} lệnh DNSE"),
            ("delete_opportunity", "Xóa {n} gợi ý"),
            ("delete_final", "Dọn {n} dòng hoàn tất"),
            ("busy", "Bỏ qua {n} dòng đang gửi"),
        )
        for key, template in labels:
            if counts[key]:
                parts.append(template.format(n=counts[key]))
        return " · ".join(parts) or "Không có thao tác khả dụng"

    def _cancel_pending_close_request(self, item):
        order_id = str(item.get("id", "") or "")
        status = str(item.get("status", "") or "").upper()
        broker_order_id = str(item.get("dnse_order_id", "") or "")
        symbol = str(item.get("symbol", "") or "").upper()
        if status == pending_orders.SENDING:
            return
        if bool(item.get("unknown_submission", False)):
            self.log_message(
                "[ĐÓNG LỆNH] Trạng thái gửi LIMIT chưa xác định; "
                "chưa thể hủy local cho đến khi đối soát DNSE.",
                error=True,
                target="manual",
            )
            return
        requires_broker_cancel = bool(
            broker_order_id and not bool(item.get("broker_terminal", False))
        )
        if status != pending_orders.WORKING and not requires_broker_cancel:
            pending_orders.cancel(order_id)
            pending_orders.delete_final(order_id)
            try:
                self._delete_running_row_everywhere(f"LOCAL:{order_id}")
            except Exception:
                pass
            self.log_message(
                f"[ĐÓNG LỆNH] Đã hủy yêu cầu đóng {symbol} #{item.get('position_ticket', '')}.",
                target="manual",
            )
            return
        if not broker_order_id:
            pending_orders.mark(
                order_id,
                pending_orders.ERROR,
                "MISSING_DNSE_ORDER_ID",
            )
            return
        if not self._ensure_trading_otp_for_mode(False):
            return

        def _cancel_working():
            try:
                result = self.connector.cancel_order(
                    broker_order_id,
                    symbol=symbol,
                    paper_mode=False,
                )
                if getattr(result, "ok", False):
                    pending_orders.mark(
                        order_id,
                        pending_orders.CANCELLED,
                        "USER_CANCELLED_LIMIT_CLOSE",
                    )
                    pending_orders.delete_final(order_id)
                    self.log_message(
                        f"[ĐÓNG LỆNH] Đã hủy LIMIT chờ đóng {symbol} #{broker_order_id}.",
                        target="manual",
                    )
                else:
                    message = (
                        getattr(result, "message", "")
                        or getattr(result, "error", "")
                        or "CANCEL_FAILED"
                    )
                    pending_orders.mark(
                        order_id,
                        pending_orders.ERROR,
                        message,
                    )
                    self.log_message(
                        f"[ĐÓNG LỆNH] Không hủy được LIMIT #{broker_order_id}: {message}",
                        error=True,
                        target="manual",
                    )
            except Exception as exc:
                pending_orders.mark(
                    order_id,
                    pending_orders.ERROR,
                    str(exc),
                )
                self.log_message(
                    f"[ĐÓNG LỆNH] Lỗi hủy LIMIT #{broker_order_id}: {exc}",
                    error=True,
                    target="manual",
                )

        threading.Thread(target=_cancel_working, daemon=True).start()

    def _handle_running_table_action(self, row_id, confirm=True):
        row_id = str(row_id or "")
        if row_id.startswith("OPP:"):
            item = self._opportunity_row_item(row_id)
            if not item:
                return
            if confirm and not messagebox.askyesno(
                "Xóa gợi ý BOT", f"Xóa gợi ý {item.get('side')} {item.get('symbol')}?", parent=self
            ):
                return
            from core import signal_opportunities
            signal_opportunities.dismiss(item.get("id"))
            self._delete_running_row_everywhere(row_id)
            return
        if row_id.startswith("LOCAL:"):
            item = self._pending_row_item(row_id)
            if not item:
                return
            order_id = str(item.get("id"))
            status = str(item.get("status", "")).upper()
            if status == "SENDING":
                return
            if status in pending_orders.FINAL_STATUSES:
                pending_orders.delete_final(order_id)
                try:
                    self._delete_running_row_everywhere(row_id)
                except Exception:
                    pass
                return
            if confirm and self.var_confirm_close.get() and not messagebox.askyesno(
                "Huy hen lenh", f"Huy lenh hen {item.get('symbol')} #{order_id[:8]}?", parent=self
            ):
                return
            if str(item.get("action", "OPEN") or "OPEN").upper() == "CLOSE":
                return BotUI._cancel_pending_close_request(self, item)
            # Huỷ + xoá khỏi bảng trong 1 lần bấm (trước đây phải bấm 2 lần: huỷ rồi mới xoá).
            pending_orders.cancel(order_id)
            pending_orders.delete_final(order_id)
            try:
                self._delete_running_row_everywhere(row_id)
            except Exception:
                pass
            self.log_message(f"[PENDING] Cancelled + removed local order {order_id[:8]}", target="manual")
            return

        if row_id.startswith("ORDER:"):
            order_id = row_id.split(":", 1)[1]
            symbol = self.cbo_symbol.get()
            if confirm and self.var_confirm_close.get() and not messagebox.askyesno(
                "Huy lenh DNSE", f"Huy lenh DNSE #{order_id}?", parent=self
            ):
                return
            if not self._ensure_trading_otp_for_mode(False):
                return

            def _cancel_order():
                try:
                    try:
                        result = self.connector.cancel_order(
                            order_id,
                            symbol=symbol,
                            paper_mode=False,
                        )
                    except TypeError:
                        result = self.connector.cancel_order(
                            order_id,
                            symbol=symbol,
                        )
                    if getattr(result, "ok", False):
                        self.log_message(f"[DNSE] Cancelled order {order_id}", target="manual")
                    else:
                        msg = getattr(result, "message", "") or getattr(result, "error", "")
                        self.log_message(f"[DNSE] Cancel order failed {order_id}: {msg}", error=True, target="manual")
                except Exception as exc:
                    self.log_message(f"[DNSE] Cancel order error {order_id}: {exc}", error=True, target="manual")

            threading.Thread(target=_cancel_order, daemon=True).start()
            return

        pending_close = BotUI._active_pending_close_for_ticket(self, row_id)
        if pending_close:
            status = str(pending_close.get("status", "") or "").upper()
            if status == pending_orders.SENDING:
                self.log_message(
                    f"[ĐÓNG LỆNH] #{row_id} đang được gửi, không thể hủy.",
                    target="manual",
                )
                return
            return BotUI._handle_running_table_action(
                self,
                f"LOCAL:{pending_close.get('id', '')}",
                confirm=confirm,
            )

        if confirm and self.var_confirm_close.get() and not messagebox.askyesno(
            "Dong lenh", f"Dong lenh #{row_id}?", parent=self
        ):
            return
        p = BotUI._find_open_position(self, row_id)
        if p:
            self._queue_or_execute_position_close(p)

    def close_selected_trades(self):
        tree = self._current_running_tree()
        selected = tree.selection()
        if not selected:
            return
        summary = self._running_selection_action_summary(selected)
        if summary == "Không có thao tác khả dụng":
            return
        if self.var_confirm_close.get() and not messagebox.askyesno(
            "Xác nhận thao tác", f"{summary}?", parent=self
        ):
            return
        for item in selected:
            self._handle_running_table_action(item, confirm=False)

    def on_tree_click(self, event):
        tree = event.widget
        region = tree.identify("region", event.x, event.y)
        if region == "cell":
            col = tree.identify_column(event.x)
            row_id = tree.identify_row(event.y)
            if row_id and col == "#9":
                self._handle_running_table_action(row_id, confirm=True)

    def on_tree_right_click(self, event):
        tree = event.widget
        row_id = tree.identify_row(event.y)
        selected = tuple(tree.selection())
        if row_id and row_id not in selected:
            tree.selection_set(row_id)
            selected = (row_id,)
        menu = Menu(self, tearoff=0, font=("Arial", 14))

        if len(selected) > 1:
            summary = self._running_selection_action_summary(selected)
            menu.add_command(
                label=summary,
                command=self.close_selected_trades,
            )
        else:
            if row_id:
                tree.selection_set(row_id)
                if str(row_id).startswith("OPP:"):
                    item = self._opportunity_row_item(row_id) or {}
                    menu.add_command(
                        label=f"Điều chỉnh / Kích hoạt {item.get('side', '')} {item.get('symbol', '')}",
                        command=lambda row_id=row_id: self.open_opportunity_popup(row_id),
                    )
                    menu.add_separator()
                    menu.add_command(
                        label="Xóa gợi ý",
                        command=lambda row_id=row_id: self._handle_running_table_action(row_id),
                    )
                elif str(row_id).startswith("LOCAL:"):
                    item = self._pending_row_item(row_id) or {}
                    status = str(item.get("status", "")).upper()
                    if status == "FAILED":
                        menu.add_command(
                            label="Xem lỗi",
                            command=lambda item=item: messagebox.showinfo(
                                "Pending order error",
                                str(item.get("result", "") or "No error detail"),
                                parent=self,
                            ),
                        )
                        menu.add_separator()
                    if status in pending_orders.FINAL_STATUSES:
                        menu.add_command(
                            label="Xóa dòng",
                            command=lambda row_id=row_id: self._handle_running_table_action(row_id),
                        )
                    elif status == "SENDING":
                        menu.add_command(label="Đang gửi DNSE", state="disabled")
                    else:
                        menu.add_command(
                            label="Hủy lệnh chờ",
                            command=lambda row_id=row_id: self._handle_running_table_action(row_id),
                        )
                elif str(row_id).startswith("ORDER:"):
                    menu.add_command(
                        label="Hủy lệnh DNSE",
                        command=lambda row_id=row_id: self._handle_running_table_action(row_id),
                    )
                else:
                    ticket = row_id
                    pending_close = self._active_pending_close_for_ticket(ticket)
                    if pending_close:
                        status = str(pending_close.get("status", "") or "").upper()
                        if status == pending_orders.SENDING:
                            menu.add_command(
                                label="Đang gửi yêu cầu đóng",
                                state="disabled",
                            )
                        else:
                            menu.add_command(
                                label="Hủy yêu cầu đóng",
                                command=lambda row_id=row_id: self._handle_running_table_action(
                                    row_id
                                ),
                            )
                    else:
                        menu.add_command(
                            label=f"Sửa SL / TP / TSL #{ticket}",
                            command=lambda: self.open_edit_popup(ticket),
                        )
                        menu.add_separator()
                        menu.add_command(
                            label="Đóng vị thế",
                            command=lambda: self.close_selected_trades(),
                        )

        if menu.index("end") is not None:
            menu.add_separator()
        menu.add_command(label="Chú thích màu và loại dòng", command=self.show_running_color_legend)

        menu.post(event.x_root, event.y_root)


if __name__ == "__main__":
    # Khởi tạo hệ thống Log 3 Lớp trước khi bật App
    # data/logs/sẽ tự động được tạo ra
    setup_logging(debug_mode=getattr(config, "ENABLE_DEBUG_LOGGING", False))

    from core.process_lock import ProcessLock

    app_lock = ProcessLock(os.path.join("data", "run", "rat_ckvn_app.lock"))
    if not app_lock.acquire():
        message = "Ứng dụng RAT-CKVN đang chạy. Không mở thêm phiên thứ hai."
        print(message)
        try:
            hidden = tk.Tk()
            hidden.withdraw()
            messagebox.showerror("RAT-CKVN", message, parent=hidden)
            hidden.destroy()
        except Exception:
            pass
        sys.exit(2)
    try:
        try:
            app = BotUI()
            app.mainloop()
        except Exception as e:
            logger = logging.getLogger("RAT_CKVN")
            logger.critical(f"💥 Lỗi nghiêm trọng tại Main Loop: {e}")
            traceback.print_exc()
            sys.exit(1)
        except KeyboardInterrupt:
            sys.exit(0)
    finally:
        app_lock.release()

