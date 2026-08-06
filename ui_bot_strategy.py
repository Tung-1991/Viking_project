# -*- coding: utf-8 -*-
# FILE: ui_bot_strategy.py
# V4.3: UNIFIED BOT STRATEGY UI - DYNAMIC MACRO, SCALPING & STRICT RISK (KAISER EDITION)

import customtkinter as ctk
import copy
import json
import logging
import os
import threading
import time
from datetime import datetime
import config
from core.money import (
    money_input_from_display,
    money_input_to_display,
    money_setting_hint,
    unit_from_display,
    unit_to_display,
)
from tkinter import messagebox, filedialog
from ui_indicators_config import open_indicator_config_popup

logger = logging.getLogger("RAT_CKVN")

COL_PANEL = "#202020"
COL_PANEL_SOFT = "#24172B"
COL_FIELD = "#2F3336"
COL_PURPLE = "#8E24AA"
COL_PURPLE_HOVER = "#7B1FA2"
COL_BLUE = "#0288D1"
COL_BLUE_HOVER = "#0277BD"
COL_AMBER = "#FFB300"

def _get_brain_path():
    try:
        import core.storage_manager as sm
        return sm.BRAIN_FILE
    except:
        return "data/brain_settings.json"

def _get_template_dir():
    try:
        import core.storage_manager as sm
        return os.path.join(sm._active_account_dir, "templates")
    except:
        return "data/templates"

def _build_trade_symbol_list():
    symbols = []
    for raw in list(getattr(config, "CKPS_SYMBOLS", []) or []) + list(getattr(config, "CKCS_WATCHLIST", []) or []):
        sym = str(raw or "").strip().upper()
        if sym and sym not in symbols:
            symbols.append(sym)
    if not symbols:
        default_symbol = str(getattr(config, "DEFAULT_SYMBOL", "VN30F1M") or "VN30F1M").strip().upper()
        if default_symbol:
            symbols.append(default_symbol)
    return symbols

EE_EXIT_LABELS = {
    "AUTO": "TP theo Entry thắng",
    "NO_TP": "OFF - không đặt TP",
    "FALLBACK_R": "TP theo RR",
    "SWING_REJECTION": "TP Swing Retest",
    "SWING_STRUCTURE": "TP Swing Struct",
    "FIB_RETRACE": "TP theo FIB",
    "PULLBACK_ZONE": "TP theo Pullback",
}
EE_EXIT_VALUES = {v: k for k, v in EE_EXIT_LABELS.items()}

EE_SL_LABELS = {
    "SANDBOX": "SL Sandbox (không override)",
    "AUTO": "SL theo Entry thắng",
    "SWING_REJECTION": "SL Swing Retest (SwingPoint + ATR)",
    "SWING_STRUCTURE": "SL Swing Struct (HL/LH + ATR)",
    "FIB_RETRACE": "SL FIB (+ ATR buffer)",
    "PULLBACK_ZONE": "SL Pullback (+ ATR buffer)",
    "FALLBACK_R": "OFF - SL Sandbox",
}
EE_SL_VALUES = {v: k for k, v in EE_SL_LABELS.items()}
EE_SL_PICK_OPTIONS = [v for k, v in EE_SL_LABELS.items() if k != "FALLBACK_R"]

EE_MISSING_LABELS = {
    "FALLBACK_R": "Thiếu dữ liệu -> dùng R",
    "BLOCK": "Thiếu dữ liệu -> chặn lệnh",
}
EE_MISSING_VALUES = {v: k for k, v in EE_MISSING_LABELS.items()}

def _default_entry_exit_config():
    return {
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
        "sl_distance": {
            "min_atr": 0.3,
            "max_atr": 2.0,
        },
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
        "bb_reclaim": {
            "band": "MID",
            "max_atr_from_band": 0.5,
        },
    }

def _merge_dict(dst, src):
    if not isinstance(src, dict):
        return dst
    for key, val in src.items():
        if isinstance(val, dict) and isinstance(dst.get(key), dict):
            _merge_dict(dst[key], val)
        else:
            dst[key] = val
    return dst


class BotStrategyUI(ctk.CTkToplevel):
    def __init__(self, master=None, symbol=None):
        super().__init__(master)
        self.override_symbol = symbol
        title_str = "RAT6 CKVN Bot Strategy Sandbox"
        if symbol:
            title_str += f" - CẤU HÌNH CON: {symbol}"
        self.title(title_str)
        screen_w = max(800, int(self.winfo_screenwidth() or 800))
        screen_h = max(600, int(self.winfo_screenheight() or 600))
        width = min(1400, max(760, screen_w - 60))
        height = min(850, max(540, screen_h - 100))
        self.geometry(
            f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}"
        )
        self.minsize(min(1180, width), min(720, height))
        self.attributes("-topmost", True)
        self.resizable(True, True)  # Khôi phục tính năng co giãn/phóng to
        if symbol:
            self.grab_set()         # Modal: Khóa UI mẹ khi đang chỉnh UI con
        self.focus_force()

        os.makedirs(_get_template_dir(), exist_ok=True)

        self.brain_data = self._load_brain_data()
        self.ind_widgets = {}
        self.check_widgets = {}
        self.vote_widgets = {}
        self.risk_widgets = {}
        self.tf_vars = {}
        self.preview_symbol_var = None
        self.group_label_widgets = []
        self.preview_status_cache = {}
        self.preview_last_symbol = None
        self.preview_fetch_lock = threading.Lock()
        self.preview_fetch_inflight = set()
        self.preview_fetch_last = {}
        self.preview_last_render_error = ""

        self._build_ui()

    def _load_brain_data(self):
        base_data = {
            "MASTER_EVAL_MODE": getattr(config, "MASTER_EVAL_MODE", "VETO"),
            "MIN_MATCHING_VOTES": getattr(config, "MIN_MATCHING_VOTES", 3),
            "FORCE_ANY_MODE": getattr(
                config, "FORCE_ANY_MODE", False
            ),  # [NEW]: Chế độ Scalping
            "G0_TIMEFRAME": getattr(config, "G0_TIMEFRAME", "1d"),
            "G1_TIMEFRAME": getattr(config, "G1_TIMEFRAME", "1h"),
            "G2_TIMEFRAME": getattr(config, "G2_TIMEFRAME", "15m"),
            "G3_TIMEFRAME": getattr(config, "G3_TIMEFRAME", "15m"),
            "voting_rules": {
                "G0": {"max_opposite": 0, "max_none": 0, "master_rule": "FIX"},
                "G1": {"max_opposite": 0, "max_none": 1, "master_rule": "PASS"},
                "G2": {"max_opposite": 0, "max_none": 2, "master_rule": "PASS"},
                "G3": {"max_opposite": 0, "max_none": 1, "master_rule": "IGNORE"},
            },
            "risk_tsl": {
                "base_risk": getattr(config, "BOT_RISK_PERCENT", 0.3),
                "base_sl": "G2",
                "sl_atr_multiplier": getattr(config, "sl_atr_multiplier", 0.2),
                "tsl_mode": getattr(config, "TSL_LOGIC_MODE", "STATIC"),
                "bot_tsl": getattr(config, "BOT_DEFAULT_TSL", "BE+STEP_R+SWING"),
                "mode_multipliers": {
                    "TREND": 1.0,
                    "RANGE": 0.5,
                    "BREAKOUT": 1.5,
                    "EXHAUSTION": 1.0,
                    "ANY": 1.0,
                },
                "strict_risk": getattr(
                    config, "STRICT_RISK_CALC", False
                ),  # [NEW]: Trừ phí
            },
            "indicators": getattr(config, "SANDBOX_CONFIG", {}).get("indicators", {}),
            "check_indicators": copy.deepcopy(getattr(config, "CHECK_INDICATORS", {})),
            "dca_config": getattr(config, "DCA_CONFIG", {}),
            "pca_config": getattr(config, "PCA_CONFIG", {}),
            "entry_exit": _default_entry_exit_config(),
            "bot_safeguard": getattr(config, "BOT_SAFEGUARD", {}).copy(),
        }

        if self.override_symbol:
            from core.storage_manager import get_brain_settings_for_symbol
            saved_data = get_brain_settings_for_symbol(self.override_symbol)
            # Merge logic for override
            for key in ["MASTER_EVAL_MODE", "MIN_MATCHING_VOTES", "FORCE_ANY_MODE", "G0_TIMEFRAME", "G1_TIMEFRAME", "G2_TIMEFRAME", "G3_TIMEFRAME"]:
                if key in saved_data: base_data[key] = saved_data[key]
            if "voting_rules" in saved_data:
                for grp in ["G0", "G1", "G2", "G3"]:
                    if grp in saved_data["voting_rules"]: base_data["voting_rules"][grp] = saved_data["voting_rules"][grp]
            if "risk_tsl" in saved_data: base_data["risk_tsl"].update(saved_data["risk_tsl"])
            if "bot_safeguard" in saved_data: base_data["bot_safeguard"].update(saved_data["bot_safeguard"])
            if "entry_exit" in saved_data: _merge_dict(base_data["entry_exit"], saved_data["entry_exit"])
            if "indicators" in saved_data:
                for k, v in saved_data["indicators"].items():
                    if k not in base_data["indicators"]: base_data["indicators"][k] = {}
                    base_data["indicators"][k].update(v)
                    if "group" in v and "groups" not in v: base_data["indicators"][k]["groups"] = [v["group"]]
            if "check_indicators" in saved_data and isinstance(saved_data["check_indicators"], dict):
                for name, check_cfg in saved_data["check_indicators"].items():
                    if name not in base_data["check_indicators"]:
                        base_data["check_indicators"][name] = {}
                    if isinstance(check_cfg, dict):
                        base_data["check_indicators"][name].update(copy.deepcopy(check_cfg))
            if "dca_config" in saved_data: base_data["dca_config"].update(saved_data["dca_config"])
            if "pca_config" in saved_data: base_data["pca_config"].update(saved_data["pca_config"])
            return base_data

        brain_path = _get_brain_path()
        if os.path.exists(brain_path):
            try:
                with open(brain_path, "r", encoding="utf-8") as f:
                    saved_data = json.load(f)

                    for key in [
                        "MASTER_EVAL_MODE",
                        "MIN_MATCHING_VOTES",
                        "FORCE_ANY_MODE",
                        "G0_TIMEFRAME",
                        "G1_TIMEFRAME",
                        "G2_TIMEFRAME",
                        "G3_TIMEFRAME",
                    ]:
                        if key in saved_data:
                            base_data[key] = saved_data[key]

                    if "voting_rules" in saved_data:
                        for grp in ["G0", "G1", "G2", "G3"]:
                            if grp in saved_data["voting_rules"]:
                                base_data["voting_rules"][grp] = saved_data[
                                    "voting_rules"
                                ][grp]

                    if "risk_tsl" in saved_data:
                        base_data["risk_tsl"].update(saved_data["risk_tsl"])
                    if "bot_safeguard" in saved_data:
                        base_data["bot_safeguard"].update(saved_data["bot_safeguard"])
                    if "entry_exit" in saved_data:
                        _merge_dict(base_data["entry_exit"], saved_data["entry_exit"])

                    if "indicators" in saved_data:
                        for k, v in saved_data["indicators"].items():
                            if k not in base_data["indicators"]:
                                base_data["indicators"][k] = {}
                            base_data["indicators"][k].update(v)
                            # Tương thích ngược: ổi 'group' cũ thành 'groups' mảng
                            if "group" in v and "groups" not in v:
                                base_data["indicators"][k]["groups"] = [v["group"]]

                    if "check_indicators" in saved_data and isinstance(saved_data["check_indicators"], dict):
                        for name, check_cfg in saved_data["check_indicators"].items():
                            if name not in base_data["check_indicators"]:
                                base_data["check_indicators"][name] = {}
                            if isinstance(check_cfg, dict):
                                base_data["check_indicators"][name].update(copy.deepcopy(check_cfg))

                    if "dca_config" in saved_data:
                        base_data["dca_config"].update(saved_data["dca_config"])
                    if "pca_config" in saved_data:
                        base_data["pca_config"].update(saved_data["pca_config"])
            except Exception as e:
                logger.warning("[UI Sandbox] L\u1ed7i \u0111\u1ecdc JSON: %s", e)

        return base_data

    def _build_ui(self):
        self.tabview = ctk.CTkTabview(self)
        try:
            self.tabview.configure(
                fg_color="#181818",
                border_width=1,
                border_color=COL_PURPLE,
                segmented_button_fg_color=COL_PANEL,
                segmented_button_selected_color=COL_PURPLE,
                segmented_button_selected_hover_color=COL_PURPLE_HOVER,
                segmented_button_unselected_color="#2A2A2A",
                segmented_button_unselected_hover_color="#343434",
                text_color="#F3E5F5",
            )
        except Exception:
            pass
        self.tabview.pack(fill="both", expand=True, padx=10, pady=5)

        # Bắt đầu vòng lặp cập nhật Preview
        self.after(1000, self.update_preview)

        # Preview này thuộc riêng BOT/Sandbox; Manual Preview nằm ở màn hình chính.
        self.tab_preview_root = self.tabview.add("BOT Preview")
        self.tab_inds = self.tabview.add("Signals")
        self.tab_check = self.tabview.add("CHECK")
        self.tab_rules = self.tabview.add("Vote Rules")
        self.tab_risk_root = self.tabview.add("Risk & TSL")
        self.tab_dca_pca_root = self.tabview.add("DCA/PCA")
        if not self.override_symbol:
            self.tab_overwrite = self.tabview.add("Overwrite")

        self.tab_preview = ctk.CTkScrollableFrame(
            self.tab_preview_root, fg_color="transparent"
        )
        self.tab_preview.pack(fill="both", expand=True, padx=4, pady=4)
        self.tab_risk = ctk.CTkScrollableFrame(
            self.tab_risk_root, fg_color="transparent"
        )
        self.tab_risk.pack(fill="both", expand=True, padx=4, pady=4)
        self.tab_dca_pca = ctk.CTkScrollableFrame(
            self.tab_dca_pca_root, fg_color="transparent"
        )
        self.tab_dca_pca.pack(fill="both", expand=True, padx=4, pady=4)

        self._build_preview_tab()

        self._build_indicators_tab()
        self._build_check_tab()
        self._build_voting_tab()
        self._build_risk_tab()
        self._build_dca_pca_tab()
        if not self.override_symbol:
            self._build_overwrite_tab()

        btn_frame = ctk.CTkFrame(
            self,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color=COL_PURPLE,
        )
        btn_frame.pack(fill="x", padx=10, pady=(4, 10))

        ctk.CTkButton(
            btn_frame,
            text="LOAD TEMPLATE",
            fg_color=COL_BLUE,
            hover_color=COL_BLUE_HOVER,
            height=36,
            corner_radius=7,
            command=self.load_template,
        ).pack(side="left", padx=(10, 5), pady=10)

        ctk.CTkButton(
            btn_frame,
            text="SAVE TEMPLATE",
            fg_color="#455A64",
            hover_color="#37474F",
            height=36,
            corner_radius=7,
            command=self.save_as_template,
        ).pack(side="left", padx=5, pady=10)

        ctk.CTkButton(
            btn_frame,
            text="SAVE & APPLY",
            fg_color=COL_PURPLE,
            hover_color=COL_PURPLE_HOVER,
            font=("Roboto", 13, "bold"),
            height=38,
            corner_radius=7,
            command=self.save_strategy,
        ).pack(side="right", padx=(5, 10), pady=10)

        if self.override_symbol:
            ctk.CTkButton(
                btn_frame,
                text="RESET OVERRIDE",
                fg_color="#D50000",
                hover_color="#B71C1C",
                font=("Roboto", 13, "bold"),
                height=38,
                corner_radius=7,
                command=self.reset_override,
            ).pack(side="right", padx=5, pady=10)

    def _add_hint_box(self, parent, text, padx=10, pady=(10, 5)):
        hint_f = ctk.CTkFrame(
            parent,
            fg_color="#241F12",
            corner_radius=8,
            border_width=1,
            border_color=COL_AMBER,
        )
        hint_f.pack(fill="x", padx=padx, pady=pady)
        ctk.CTkLabel(
            hint_f,
            text=text,
            font=("Arial", 13, "italic"),
            text_color="#FFE082",
            justify="left",
            anchor="w",
            wraplength=1080,
        ).pack(fill="x", padx=10, pady=6)
        return hint_f

    def _get_group_timeframe(self, grp):
        if grp in self.tf_vars:
            return str(self.tf_vars[grp].get())
        return str(self.brain_data.get(f"{grp}_TIMEFRAME", "15m"))

    def _format_tf_label(self, timeframe):
        tf = str(timeframe).strip().lower()
        if tf.endswith("m"):
            return f"M{tf[:-1]}"
        if tf.endswith("h"):
            return f"H{tf[:-1]}"
        if tf.endswith("d"):
            return f"D{tf[:-1]}"
        return tf.upper()

    def _group_label(self, grp):
        return f"{grp}({self._format_tf_label(self._get_group_timeframe(grp))})"

    def _effective_preview_brain(self, symbol):
        """Cấu hình thực tế mà engine dùng cho mã đang Preview."""
        if getattr(self, "override_symbol", None):
            return self.brain_data
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return self.brain_data
        try:
            from core.storage_manager import get_brain_settings_for_symbol

            return get_brain_settings_for_symbol(symbol)
        except Exception:
            return self.brain_data

    def _group_label_for_brain(self, grp, brain):
        brain = brain if isinstance(brain, dict) else {}
        timeframe = brain.get(f"{grp}_TIMEFRAME", self._get_group_timeframe(grp))
        return f"{grp}({self._format_tf_label(timeframe)})"

    @staticmethod
    def _format_duration(seconds):
        seconds = max(0, int(seconds or 0))
        minutes = seconds // 60
        hours = minutes // 60
        days = hours // 24
        if days:
            rem_hours = hours % 24
            return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"
        if hours:
            return f"{hours}h"
        if minutes:
            return f"{minutes}m"
        return "0m"

    @staticmethod
    def _preview_data_age_text(context, stale=False, now=None, cached=False):
        """Describe when the displayed indicator context was actually calculated."""
        context = context if isinstance(context, dict) else {}
        timestamp = context.get("timestamp", context.get("tick_timestamp"))
        try:
            timestamp = float(timestamp or 0.0)
        except (TypeError, ValueError):
            timestamp = 0.0
        if timestamp <= 0:
            return "DATA: N/A"

        now = time.time() if now is None else float(now)
        age_seconds = max(0.0, now - timestamp)
        stamp = datetime.fromtimestamp(timestamp).strftime("%d/%m %H:%M:%S")
        age_text = (
            "just now"
            if age_seconds < 60
            else f"{BotStrategyUI._format_duration(age_seconds)} ago"
        )
        if stale:
            prefix = "CACHE SETTING CŨ"
        elif cached:
            prefix = "CACHE CUỐI PHIÊN"
        else:
            prefix = "UPDATED"
        return f"{prefix}: {stamp} · {age_text}"

    @staticmethod
    def _context_has_preview_snapshot(context):
        """A persisted context may be displayed, but never treated as live."""
        if not isinstance(context, dict) or not context:
            return False
        details = context.get("group_details")
        if not isinstance(details, dict) or not details:
            return False
        return any(
            isinstance(details.get(f"G{i}"), dict)
            and bool(details.get(f"G{i}"))
            for i in range(4)
        )

    @staticmethod
    def _preview_market_open(symbol):
        try:
            from core.market_hours import is_symbol_trade_window_open

            return bool(is_symbol_trade_window_open(symbol)[0])
        except Exception:
            return False

    def _status_text(self, status):
        return {1: "BUY", -1: "SELL", 0: "WAIT"}.get(status, "WAIT")

    def _update_preview_status_timer(self, symbol, grp, status):
        if not symbol:
            return "0m", ""

        now = time.time()
        key = f"{symbol}:{grp}"
        tracker = getattr(self.master, "group_status_tracker", {})
        state = tracker.get(key) if isinstance(tracker, dict) else None

        if state:
            try:
                since = float(state.get("since", now))
            except (TypeError, ValueError):
                since = now
            current_duration = self._format_duration(now - since)
            prev_parts = []
            last_duration = state.get("last_duration", {})
            for prev_status in [1, -1, 0]:
                if prev_status == status:
                    continue
                duration = last_duration.get(str(prev_status), last_duration.get(prev_status)) if isinstance(last_duration, dict) else None
                if duration is not None:
                    prev_parts.append(f"{self._status_text(prev_status)} - {self._format_duration(duration)}")
            return current_duration, "Trước: " + " | ".join(prev_parts) if prev_parts else "Trước: --"

        state = self.preview_status_cache.get(key)
        if not state:
            state = {"status": status, "since": now, "last_duration": {}}
            self.preview_status_cache[key] = state
        elif state.get("status") != status:
            prev_status = state.get("status", 0)
            state.setdefault("last_duration", {})[prev_status] = now - state.get("since", now)
            state["status"] = status
            state["since"] = now

        current_duration = self._format_duration(now - state.get("since", now))
        prev_parts = []
        for prev_status in [1, -1, 0]:
            if prev_status == status:
                continue
            duration = state.get("last_duration", {}).get(prev_status)
            if duration is not None:
                prev_parts.append(f"{self._status_text(prev_status)} - {self._format_duration(duration)}")

        return current_duration, "Trước: " + " | ".join(prev_parts) if prev_parts else "Trước: --"

    def _refresh_group_labels(self, *_):
        for widget, template, grp in self.group_label_widgets:
            try:
                widget.configure(text=template.format(label=self._group_label(grp), grp=grp))
            except Exception:
                pass

    def _entry_exit_preview_text(self, symbol=None, context=None):
        cfg = self._collect_entry_exit_config()
        context = context or {}
        try:
            from core.entry_exit_engine import evaluate_entry_exit, format_decision

            final_sig = int(context.get("latest_signal", 0) or 0)
            direction = "BUY" if final_sig == 1 else "SELL" if final_sig == -1 else None
            price = context.get("current_price") or context.get("price") or context.get("last_price")
            if not direction or not price:
                state = "PREVIEW" if cfg.get("enabled") and cfg.get("active_tactics") else "OFF"
                tactics = ", ".join(cfg.get("active_tactics", [])) or "none"
                return (
                    f"E/E: {state} | TACTICS: {tactics}\n"
                    "WAITING: BUY/SELL signal and live price are required."
                )
            decision = evaluate_entry_exit(symbol or "---", direction, float(price), context, cfg)
            text = format_decision(decision)
            if cfg.get("preview_only", False) and text.startswith("E/E:"):
                text = text.replace("E/E:", "E/E PREVIEW:", 1)
            return text
        except Exception as exc:
            return f"E/E: ERROR | {exc}"

    def _build_preview_tab(self):
        f = ctk.CTkFrame(self.tab_preview, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=5, pady=5)

        self._add_hint_box(
            f,
            "- Preview only reads live context; it never places orders.\n"
            "- B/S/N = BUY/SELL/NONE votes after Mode filtering.\n"
            "- Master Action = final result after group rules + Master Mode.\n"
            "- FIX = required, PASS = allows WAIT but blocks opposite, IGNORE = skipped.",
            padx=5,
            pady=(0, 10),
        )

        if not self.override_symbol:
            picker_f = ctk.CTkFrame(f, fg_color="transparent")
            picker_f.pack(fill="x", pady=(0, 8))

            symbols = _build_trade_symbol_list()

            active_symbol = getattr(config, "UI_ACTIVE_SYMBOL", None)
            if not active_symbol:
                try:
                    active_symbol = self.master.cbo_symbol.get()
                except Exception:
                    active_symbol = symbols[0]
            active_symbol = str(active_symbol or symbols[0]).strip().upper()
            if active_symbol not in symbols:
                symbols.insert(0, active_symbol)

            self.preview_symbol_var = ctk.StringVar(value=active_symbol)
            ctk.CTkLabel(
                picker_f,
                text="Preview Symbol:",
                font=("Roboto", 14, "bold"),
            ).pack(side="left", padx=(5, 8))
            ctk.CTkComboBox(
                picker_f,
                values=symbols,
                variable=self.preview_symbol_var,
                width=140,
                font=("Roboto", 14),
            ).pack(side="left")

        # Header: Master Action
        header_f = ctk.CTkFrame(
            f,
            fg_color=COL_PANEL_SOFT,
            corner_radius=8,
            border_width=1,
            border_color=COL_PURPLE,
        )
        header_f.pack(fill="x", pady=(0, 10))
        
        self.master_action_lbl = ctk.CTkLabel(header_f, text="MASTER ACTION: WAITING", font=("Roboto", 18, "bold"), text_color="#FFF")
        self.master_action_lbl.pack(pady=(10, 5))
        
        self.market_mode_lbl = ctk.CTkLabel(header_f, text="MARKET MODE: --- | BASE TREND: --- | RULE: ---", font=("Roboto", 15, "bold"), text_color="#29B6F6")
        self.market_mode_lbl.pack(pady=5)
        
        self.master_reason_lbl = ctk.CTkLabel(header_f, text="REASON: Waiting for signal...", font=("Roboto", 14), text_color="#AAA")
        self.master_reason_lbl.pack(pady=(0, 10))

        entry_exit_f = ctk.CTkFrame(
            f,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color="#00B8D4",
        )
        entry_exit_f.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            entry_exit_f,
            text="ENTRY/EXIT PREVIEW",
            font=("Roboto", 16, "bold"),
            text_color="#29B6F6",
        ).pack(anchor="w", padx=10, pady=(8, 2))
        self.entry_exit_preview_lbl = ctk.CTkLabel(
            entry_exit_f,
            text=self._entry_exit_preview_text(),
            font=("Consolas", 14),
            text_color="#B0BEC5",
            justify="left",
            anchor="w",
        )
        self.entry_exit_preview_lbl.pack(fill="x", padx=10, pady=(0, 8))

        # Grid 4 Columns
        grid_f = ctk.CTkFrame(f, fg_color="transparent")
        grid_f.pack(fill="both", expand=True)

        self.preview_cards = {}
        grp_colors = {"G0": "#AB47BC", "G1": "#00E676", "G2": "#00B0FF", "G3": "#FF3D00"}
        for i in range(4):
            grp = f"G{i}"
            col = ctk.CTkFrame(
                grid_f,
                fg_color=COL_PANEL,
                corner_radius=8,
                border_width=1,
                border_color=grp_colors.get(grp, "#555"),
            )
            col.pack(side="left", fill="both", expand=True, padx=5)

            # Title
            lbl_title = ctk.CTkLabel(col, text=f"{grp} STATUS", font=("Roboto", 15, "bold"), fg_color="#333", corner_radius=4)
            lbl_title.pack(fill="x", padx=5, pady=5)

            # B/S/N summary
            lbl_summary = ctk.CTkLabel(col, text="B: 0 | S: 0 | N: 0", font=("Roboto", 14, "bold"), text_color="#FFF")
            lbl_summary.pack(pady=(4, 1))

            lbl_trend = ctk.CTkLabel(col, text="TREND: NONE | --", font=("Consolas", 14, "bold"), text_color="#FFD600")
            lbl_trend.pack(pady=(0, 3))

            lbl_prev = ctk.CTkLabel(col, text="DATA: N/A", font=("Roboto", 13), text_color="#BDBDBD")
            lbl_prev.pack(pady=(0, 5))

            # Details List (Scrollable)
            scroll_f = ctk.CTkScrollableFrame(col, fg_color="#1A1A1A", corner_radius=4, height=200)
            scroll_f.pack(fill="both", expand=True, padx=5, pady=5)
            
            self.preview_cards[grp] = {
                "title": lbl_title,
                "summary": lbl_summary,
                "trend": lbl_trend,
                "prev": lbl_prev,
                "scroll_f": scroll_f,
                "frame": col,
                "last_data": "",  # Để chống flicker
                "last_cached_display": None,
            }

    def update_preview(self):
        """Cập nhật dữ liệu Live Preview từ context của Master (Main App)"""
        try:
            # [FIX] latest_market_context là dict {symbol: ctx_data}, cần lấy đúng symbol
            all_ctx = getattr(self.master, "latest_market_context", {})

            if self.override_symbol:
                # UI con: lấy context của đúng symbol override
                active_symbol = str(self.override_symbol or "").strip().upper()
                context = self._context_for_symbol(all_ctx, active_symbol)
            else:
                # UI mẹ: lấy symbol đang chn trên combobox chính
                active_symbol = self.preview_symbol_var.get() if self.preview_symbol_var else None
                if not active_symbol:
                    try:
                        active_symbol = self.master.cbo_symbol.get()
                    except Exception:
                        active_symbol = None
                active_symbol = str(active_symbol or "").strip().upper()
                context = self._context_for_symbol(all_ctx, active_symbol) if active_symbol else {}

            preview_brain = self._effective_preview_brain(active_symbol)
            market_open = BotStrategyUI._preview_market_open(active_symbol)
            context_strategy_stale = False
            if active_symbol:
                try:
                    from core.storage_manager import brain_strategy_fingerprint

                    actual_fingerprint = str(
                        (context or {}).get("strategy_fingerprint") or ""
                    )
                    expected_fingerprint = brain_strategy_fingerprint(preview_brain)
                    context_strategy_stale = bool(context) and (
                        not actual_fingerprint
                        or actual_fingerprint != expected_fingerprint
                    )
                    if context_strategy_stale and market_open:
                        self._schedule_preview_context_fetch(active_symbol)
                except Exception:
                    context_strategy_stale = bool(context)
                    if market_open:
                        self._schedule_preview_context_fetch(active_symbol)

            context_ready = self._context_ready_for_preview(context, preview_brain)
            has_cached_snapshot = BotStrategyUI._context_has_preview_snapshot(context)
            if active_symbol and not context_ready:
                # Snapshot cũ chỉ phục vụ quan sát. Engine/E/E vẫn phải chờ
                # context khớp setting hiện tại trước khi được phép hành động.
                if not context_strategy_stale and market_open:
                    self._schedule_preview_context_fetch(active_symbol)
                if not has_cached_snapshot:
                    context = {}

            cached_display = bool(context) and (
                context_strategy_stale or not market_open
            )

            if active_symbol != self.preview_last_symbol:
                self.preview_status_cache.clear()
                self.preview_last_symbol = active_symbol
            no_context = not bool(context)
            if no_context:
                self.preview_status_cache.clear()

            group_details = context.get("group_details", {})


            # Cập nhật 4 cột Grid
            colors = {1: "#2E7D32", -1: "#C62828", 0: "#424242"}
            texts = {1: "BUY", -1: "SELL", 0: "WAIT"}
            
            for i in range(4):
                grp = f"G{i}"
                card = self.preview_cards[grp]
                data = group_details.get(grp, {"B": 0, "S": 0, "N": 0, "inds": [], "status": 0})
                
                # [NEW] Lấy luật để hiển thị làm Hint
                rules_cfg = preview_brain.get("voting_rules", {}).get(grp, {})
                m_rule = rules_cfg.get("master_rule", "FIX")
                max_o = rules_cfg.get("max_opposite", 0)
                max_n = rules_cfg.get("max_none", 0)
                if context_strategy_stale:
                    cached_rule = (context.get("group_rules", {}) or {}).get(
                        grp,
                        m_rule,
                    )
                    rule_hint = f"[CACHE {cached_rule}]"
                else:
                    rule_hint = f"[{m_rule} | O:{max_o}, N:{max_n}]"
                
                status_val = data.get("status", 0)
                cached_tf = (context.get("group_timeframes", {}) or {}).get(grp)
                group_label = (
                    f"{grp}({self._format_tf_label(cached_tf)})"
                    if cached_display and cached_tf
                    else self._group_label_for_brain(grp, preview_brain)
                )
                title_text = f"{group_label}: {texts.get(status_val, 'WAIT')}\n{rule_hint}"
                card["title"].configure(
                    text=title_text,
                    fg_color="#6D5A00" if cached_display else colors.get(status_val, "#333"),
                )
                card["summary"].configure(
                    text=f"B: {data.get('B', 0)}  |  S: {data.get('S', 0)}  |  N: {data.get('N', 0)}",
                    text_color="#FFD600" if cached_display else "#FFF",
                )
                trend_state = str(context.get(f"trend_{grp}", "NONE") or "NONE").upper()
                trend_names = []
                for ind_name, cfg in (preview_brain.get("indicators", {}) or {}).items():
                    groups = cfg.get("groups", [cfg.get("group", "G2")])
                    if cfg.get("is_trend", False) and grp in groups:
                        trend_names.append(ind_name.upper())
                trend_color = (
                    "#FFD600"
                    if cached_display
                    else "#00E676"
                    if trend_state == "UP"
                    else "#FF5252"
                    if trend_state == "DOWN"
                    else "#FFD600"
                )
                card["trend"].configure(
                    text=f"TREND: {trend_state} | {','.join(trend_names) if trend_names else '--'}",
                    text_color=trend_color,
                )
                card["prev"].configure(
                    text=BotStrategyUI._preview_data_age_text(
                        context,
                        stale=context_strategy_stale,
                        cached=cached_display,
                    ),
                    text_color="#FFD600" if cached_display else "#BDBDBD",
                )
                
                inds_list = data.get("inds", [])
                if not isinstance(inds_list, list):
                    inds_list = []
                if not inds_list:
                    inds_list = BotStrategyUI._configured_group_indicator_placeholders(
                        preview_brain,
                        grp,
                        trend_state=trend_state,
                        stale=context_strategy_stale,
                    )
                
                # Chống flicker: Chỉ vẽ lại khi dữ liệu thay đổi
                current_data_str = json.dumps(inds_list)
                if (
                    card.get("last_data") != current_data_str
                    or card.get("last_cached_display") != cached_display
                ):
                    # Xóa widgets cũ
                    for widget in card["scroll_f"].winfo_children():
                        widget.destroy()
                        
                    if not inds_list:
                        ctk.CTkLabel(card["scroll_f"], text="-- NO DATA --", font=("Roboto", 14), text_color="gray").pack(fill="x", pady=10)
                    else:
                        for line in inds_list:
                            t_color = "#FFD600" if cached_display else "#999"
                            if not cached_display and "[BUY]" in line:
                                t_color = "#00C853" # Xanh lá vibrance
                            elif not cached_display and "[SELL]" in line:
                                t_color = "#FF3D00" # Đỏ rực
                            
                            ctk.CTkLabel(
                                card["scroll_f"], 
                                text=line, 
                                font=("Consolas", 14, "bold"), 
                                text_color=t_color, 
                                anchor="w", 
                                justify="left"
                            ).pack(fill="x", padx=5, pady=1)
                    
                    card["last_data"] = current_data_str
                    card["last_cached_display"] = cached_display

            # Cập nhật Master Action
            final_sig = context.get("latest_signal", 0)
            act_color = (
                "#FFD600"
                if cached_display
                else "#00C853"
                if final_sig == 1
                else "#FF3D00"
                if final_sig == -1
                else "#777"
            )
            act_text = (
                f"MASTER ACTION{' [CACHE]' if cached_display else ''}: "
                f"{'BUY' if final_sig == 1 else 'SELL' if final_sig == -1 else 'WAIT'}"
            )
            self.master_action_lbl.configure(text=act_text, text_color=act_color)

            # [NEW] Cập nhật Market Mode & Macro
            m_mode = context.get("market_mode", "ANY")
            m_src = context.get("mode_source", "---")
            m_dir = context.get("macro_direction", 0)
            dir_text = "UP" if m_dir == 1 else "DOWN" if m_dir == -1 else "NONE"
            
            # [FIX] Lấy Evaluation Mode trực tiếp từ biến UI để cập nhật Realtime
            eval_mode = str(
                preview_brain.get(
                    "MASTER_EVAL_MODE",
                    self.master_eval_var.get(),
                )
                or self.master_eval_var.get()
            )
            
            mode_color = (
                "#FFD600"
                if cached_display
                else "#00E676"
                if m_mode in ["TREND", "BREAKOUT"]
                else "#FFB300"
            )
            self.market_mode_lbl.configure(
                text=f"MARKET MODE: {m_mode} (by {m_src}) | BASE TREND: {dir_text} | RULE: {eval_mode}",
                text_color=mode_color
            )

            # Cập nhật Block Reason (Highlight lý do chặn)
            block_reason = context.get("block_reason", "OK / Ready")
            if cached_display:
                cache_kind = (
                    "CACHE CUỐI PHIÊN / SETTING CŨ"
                    if context_strategy_stale and not market_open
                    else "CACHE SETTING CŨ"
                    if context_strategy_stale
                    else "CACHE CUỐI PHIÊN"
                )
                block_reason = f"{cache_kind} — chỉ xem, không dùng để giao dịch"
            reason_color = "#00C853" if "OK" in block_reason else "#FFAB00"
            self.master_reason_lbl.configure(text=f"REASON: {block_reason}", text_color=reason_color)
            if hasattr(self, "entry_exit_preview_lbl"):
                ee_context = {} if cached_display else context
                ee_text = self._entry_exit_preview_text(active_symbol, ee_context)
                if cached_display:
                    ee_text += "\nCACHE ONLY: Entry/Exit không dùng snapshot cũ."
                self.entry_exit_preview_lbl.configure(
                    text=ee_text
                )

            self.preview_last_render_error = ""
        except Exception as exc:
            error_key = f"{type(exc).__name__}: {exc}"
            if error_key != self.preview_last_render_error:
                logger.exception(
                    "[Strategy Preview] Render thất bại: %s",
                    error_key,
                )
                self.preview_last_render_error = error_key

        # Lặp lại sau 1 giây
        self.after(1000, self.update_preview)

    def _build_overwrite_tab(self):
        f = ctk.CTkScrollableFrame(self.tab_overwrite)
        f.pack(fill="both", expand=True, padx=5, pady=5)

        ctk.CTkLabel(
            f,
            text="OVERRIDE SANDBOX THEO SYMBOL",
            font=("Roboto", 14, "bold"),
            text_color="#00B8D4",
        ).pack(pady=10)
        self._add_hint_box(
            f,
            "- EDIT/SELECT để mở cấu hình riêng cho symbol.\n"
            "- RESET để xóa cấu hình riêng và quay về Global.\n"
            "- Bảng này chỉ quản lý Sandbox override, không đụng TSL hoặc E/E override riêng.",
            padx=10,
            pady=(0, 10),
        )

        f_overview = ctk.CTkFrame(f, fg_color="#2b2b2b", corner_radius=8)
        f_overview.pack(fill="x", padx=20, pady=8)
        f_overview.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            f_overview,
            text="OVERRIDE OVERVIEW",
            font=("Roboto", 13, "bold"),
            text_color="#00B8D4",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 6))
        ctk.CTkLabel(f_overview, text="Symbol", font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(f_overview, text="Status", font=("Roboto", 11, "bold"), text_color="#D7DCE2").grid(row=1, column=1, sticky="w", padx=12, pady=(0, 4))

        rows = ctk.CTkFrame(f_overview, fg_color="transparent")
        rows.grid(row=2, column=0, columnspan=4, sticky="ew", padx=8, pady=(0, 8))
        rows.grid_columnconfigure(0, weight=1)

        def refresh():
            for child in rows.winfo_children():
                child.destroy()
            from core.storage_manager import load_symbol_overrides, save_symbol_overrides

            overrides = load_symbol_overrides()
            symbols = _build_trade_symbol_list()
            for row, sym in enumerate(symbols):
                has_override = bool(overrides.get(sym, {}).get("sandbox"))
                row_frame = ctk.CTkFrame(rows, fg_color="#2F2A12" if has_override else "#242424", corner_radius=6)
                row_frame.grid(row=row, column=0, sticky="ew", padx=2, pady=2)
                row_frame.grid_columnconfigure(1, weight=1)
                ctk.CTkLabel(row_frame, text=sym, width=110, anchor="w", font=("Roboto", 12, "bold"), text_color="#FFFFFF").grid(row=0, column=0, sticky="w", padx=10, pady=6)
                ctk.CTkLabel(row_frame, text="OVERRIDE" if has_override else "GLOBAL", anchor="w", font=("Roboto", 11, "bold"), text_color="#FFAB00" if has_override else "#B0BEC5").grid(row=0, column=1, sticky="w", padx=8, pady=6)
                ctk.CTkButton(
                    row_frame,
                    text="EDIT" if has_override else "SELECT",
                    width=76,
                    height=24,
                    fg_color="#1f538d" if has_override else "#424242",
                    hover_color="#14375e" if has_override else "#616161",
                    command=lambda s=sym: self._open_symbol_override_ui(s),
                ).grid(row=0, column=2, sticky="e", padx=(4, 6), pady=5)

                def reset_symbol(symbol=sym):
                    latest = load_symbol_overrides()
                    if symbol in latest and "sandbox" in latest[symbol]:
                        del latest[symbol]["sandbox"]
                        if not latest[symbol]:
                            del latest[symbol]
                        save_symbol_overrides(latest)
                    refresh()

                ctk.CTkButton(
                    row_frame,
                    text="RESET",
                    width=70,
                    height=24,
                    fg_color="#B71C1C" if has_override else "#303030",
                    hover_color="#7F0000" if has_override else "#303030",
                    state="normal" if has_override else "disabled",
                    command=reset_symbol,
                ).grid(row=0, column=3, sticky="e", padx=(0, 6), pady=5)

        refresh()

    def _context_for_symbol(self, contexts, symbol):
        if not isinstance(contexts, dict) or not symbol:
            return {}
        symbol = str(symbol or "").strip()
        if symbol in contexts:
            return contexts.get(symbol) or {}
        upper_symbol = symbol.upper()
        if upper_symbol in contexts:
            return contexts.get(upper_symbol) or {}
        for key, val in contexts.items():
            if str(key or "").strip().upper() == upper_symbol:
                return val or {}
        return {}

    def _context_ready_for_preview(self, context, brain=None):
        if not isinstance(context, dict) or not context:
            return False
        group_details = context.get("group_details")
        if not isinstance(group_details, dict) or not group_details:
            return False
        if isinstance(brain, dict) and brain:
            try:
                from core.storage_manager import brain_strategy_fingerprint

                actual = str(context.get("strategy_fingerprint") or "")
                expected = brain_strategy_fingerprint(brain)
                if not actual or actual != expected:
                    return False
            except Exception:
                return False
        return any(context.get(f"trend_G{i}") for i in range(4))

    @staticmethod
    def _configured_group_indicator_placeholders(
        brain,
        group,
        trend_state="NONE",
        stale=False,
    ):
        """List current settings without inventing votes before recompute."""
        state_label = "WAIT"
        placeholders = []
        for name, cfg in ((brain or {}).get("indicators", {}) or {}).items():
            if not isinstance(cfg, dict) or not cfg.get("active", False):
                continue
            groups = cfg.get("groups", [cfg.get("group", "G2")])
            if group not in groups:
                continue
            label = str(name or "").replace("_", " ").upper()
            macro_role = str(cfg.get("macro_role", "NONE") or "NONE").upper()
            active_modes = cfg.get("active_modes", ["ANY"])
            if not isinstance(active_modes, (list, tuple)):
                active_modes = [active_modes]
            mode_text = ",".join(str(mode or "ANY").upper() for mode in active_modes)
            bullet = "○"
            spacing = " "
            placeholders.append(
                f"{bullet} [{state_label}]{spacing}{label} [{macro_role}|{mode_text}]"
            )
        return placeholders

    def _schedule_preview_context_fetch(self, symbol):
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return
        now = time.time()
        with self.preview_fetch_lock:
            if symbol in self.preview_fetch_inflight:
                return
            if now - self.preview_fetch_last.get(symbol, 0) < 15:
                return
            self.preview_fetch_last[symbol] = now
            self.preview_fetch_inflight.add(symbol)
        worker = threading.Thread(
            target=self._fetch_preview_context_worker,
            args=(symbol,),
            daemon=True,
        )
        worker.start()

    def _fetch_preview_context_worker(self, symbol):
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
                self.after(0, lambda s=symbol, c=context: self._store_preview_context(s, c))
            except Exception:
                pass
        except Exception as exc:
            logger.debug(
                "[Strategy Preview] Không thể làm mới context %s: %s",
                symbol,
                exc,
            )
        finally:
            with self.preview_fetch_lock:
                self.preview_fetch_inflight.discard(symbol)

    def _store_preview_context(self, symbol, context):
        try:
            if not self.winfo_exists():
                return
            contexts = getattr(self.master, "latest_market_context", None)
            if not isinstance(contexts, dict):
                contexts = {}
                setattr(self.master, "latest_market_context", contexts)
            existing = self._context_for_symbol(contexts, symbol)
            merged = {}
            if isinstance(existing, dict):
                merged.update(existing)
            merged.update(context or {})
            contexts[str(symbol).strip().upper()] = merged
        except Exception:
            pass

    def _open_symbol_override_ui(self, symbol):
        override_ui = BotStrategyUI(self, symbol=symbol)
        override_ui.focus_force()

    def reset_override(self):
        if not self.override_symbol: return
        from core.storage_manager import load_symbol_overrides, save_symbol_overrides
        overrides = load_symbol_overrides()
        if self.override_symbol in overrides and "sandbox" in overrides[self.override_symbol]:
            del overrides[self.override_symbol]["sandbox"]
            save_symbol_overrides(overrides)
            self.destroy()

    def _build_indicators_tab(self):
        self._add_hint_box(
            self.tab_inds,
            "- G0 quyết định Market Mode & Macro Direction; không có G0 thì fallback G1.\n"
            "- Trend Compass CHỈ XEM TRƯỚC (mũi tên UP/DOWN/NONE), KHÔNG quyết định lệnh mua/bán.\n"
            "- Macro Role mới quyết định BASE/BREAKOUT/EXHAUSTION; Mode ANY = luôn được xét.",
        )

        self._add_hint_box(
            self.tab_inds,
            "- Trigger Mode áp dụng chung cho indicator này ở mọi group đã tick.",
            pady=(0, 5),
        )

        scroll_frame = ctk.CTkScrollableFrame(self.tab_inds)
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Cập nhật Header theo Rule V4.2 mới
        headers = [
            "Chỉ báo",
            "ON",
            "Nhóm (đã chọn)",
            "Trend Compass",
            "Vai trò Macro",
            "Chạy khi (Mode)",
            "Trigger Mode",
            "Thông số",
        ]
        for col, h in enumerate(headers):
            ctk.CTkLabel(scroll_frame, text=h, font=("Roboto", 12, "bold")).grid(
                row=0, column=col, padx=5, pady=5, sticky="w"
            )

        row = 1
        inds_data = self.brain_data.get("indicators", {})

        for ind_name, cfg in inds_data.items():
            # [FIX UX]: Biến Tên chỉ báo thành Nút bấm (Row Toggle)
            btn_name = ctk.CTkButton(
                scroll_frame,
                text=ind_name.upper(),
                font=("Roboto", 12, "bold"),
                text_color="#90CAF9",
                fg_color="transparent",
                hover_color="#333333",
                anchor="w",
                width=80,
            )
            btn_name.grid(row=row, column=0, padx=5, pady=5, sticky="w")

            # Kích hoạt
            active_var = ctk.BooleanVar(value=cfg.get("active", False))
            ctk.CTkCheckBox(scroll_frame, text="", variable=active_var, width=30).grid(
                row=row, column=1, padx=5, pady=5, sticky="w"
            )

            # Multi-Group Checkboxes (G0-G3)
            f_groups = ctk.CTkFrame(scroll_frame, fg_color="transparent")
            f_groups.grid(row=row, column=2, padx=5, pady=5, sticky="w")

            grp_vars = {}
            saved_groups = cfg.get("groups", [cfg.get("group", "G2")])
            for g in ["G0", "G1", "G2", "G3"]:
                g_var = ctk.BooleanVar(value=(g in saved_groups))
                chk_group = ctk.CTkCheckBox(
                    f_groups, text=self._group_label(g), variable=g_var, width=64, font=("Roboto", 11)
                )
                chk_group.pack(side="left", padx=2)
                self.group_label_widgets.append((chk_group, "{label}", g))
                grp_vars[g] = g_var

            # Trend Compass (La bàn xu hướng)
            is_trend_var = ctk.BooleanVar(value=cfg.get("is_trend", False))
            ctk.CTkCheckBox(
                scroll_frame,
                text="Compass",
                variable=is_trend_var,
                width=50,
                text_color="#FFD700",
            ).grid(row=row, column=3, padx=5, pady=5, sticky="w")

            # Gắn lệnh Toggle cho Nút Tên Chỉ Báo (Bắt trạng thái để lật công tắc)
            def toggle_row_state(a_var=active_var, g_vars=grp_vars, t_var=is_trend_var):
                target_state = not a_var.get()
                a_var.set(target_state)
                for var in g_vars.values():
                    var.set(target_state)
                t_var.set(target_state)

            btn_name.configure(command=toggle_row_state)

            # Vai trò Macro
            macro_role_var = ctk.StringVar(value=cfg.get("macro_role", "NONE"))
            ctk.CTkComboBox(
                scroll_frame,
                values=["NONE", "BASE", "BREAKOUT", "EXHAUSTION"],
                variable=macro_role_var,
                width=110,
            ).grid(row=row, column=4, padx=5, pady=5, sticky="w")

            # Mode hoạt động
            modes_list = cfg.get("active_modes", ["ANY"])
            mode_var = ctk.StringVar(value=modes_list[0] if modes_list else "ANY")
            ctk.CTkComboBox(
                scroll_frame,
                values=["ANY", "TREND", "RANGE", "BREAKOUT", "EXHAUSTION"],
                variable=mode_var,
                width=100,
            ).grid(row=row, column=5, padx=5, pady=5, sticky="w")

            # Trigger Mode
            trigger_mode_var = ctk.StringVar(
                value=cfg.get("trigger_mode", "STRICT_CLOSE")
            )
            ctk.CTkComboBox(
                scroll_frame,
                values=["STRICT_CLOSE", "REALTIME_TICK"],
                variable=trigger_mode_var,
                width=120,
            ).grid(row=row, column=6, padx=5, pady=5, sticky="w")

            # Nút Cài đặt Thông số
            btn_cfg = ctk.CTkButton(
                scroll_frame,
                text="⚙ Cài đặt",
                width=70,
                fg_color="#424242",
                hover_color="#616161",
                command=lambda n=ind_name: self.open_ind_setting(n),
            )
            btn_cfg.grid(row=row, column=7, padx=5, pady=5)
            btn_reset_cfg = ctk.CTkButton(
                scroll_frame,
                text="Reset",
                width=48,
                fg_color="#5D4037",
                hover_color="#4E342E",
                command=lambda n=ind_name: self.reset_ind_overrides(n),
            )
            btn_reset_cfg.grid(row=row, column=8, padx=(0, 5), pady=5)

            self.ind_widgets[ind_name] = {
                "active_var": active_var,
                "grp_vars": grp_vars,
                "is_trend_var": is_trend_var,
                "macro_role_var": macro_role_var,
                "mode_var": mode_var,
                "trigger_mode_var": trigger_mode_var,
                "params": cfg.get("params", {}),
                "group_params": cfg.get("group_params", {}),
                "group_trigger_modes": cfg.get("group_trigger_modes", {}),
                "config_btn": btn_cfg,
                "reset_config_btn": btn_reset_cfg,
            }
            self._refresh_indicator_config_button(ind_name)
            row += 1

    def _build_check_tab(self):
        self._add_hint_box(
            self.tab_check,
            "CHECK chỉ phục vụ báo cáo, không ảnh hưởng BOT TRADE.\n"
            "Module bật ở đây dùng chung dữ liệu nến đã tải nhưng không tham gia vote, market mode hoặc đặt lệnh.",
        )
        scroll_frame = ctk.CTkScrollableFrame(self.tab_check)
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)
        for col, title in enumerate(("Chỉ báo", "ON", "Nhóm báo cáo", "Thông số")):
            ctk.CTkLabel(scroll_frame, text=title, font=("Roboto", 12, "bold")).grid(
                row=0, column=col, padx=8, pady=6, sticky="w"
            )

        for row, (ind_name, cfg) in enumerate(
            (self.brain_data.get("check_indicators", {}) or {}).items(), start=1
        ):
            active_var = ctk.BooleanVar(value=bool(cfg.get("active", False)))
            ctk.CTkLabel(
                scroll_frame,
                text=ind_name.upper(),
                font=("Roboto", 12, "bold"),
                text_color="#80DEEA",
                width=150,
                anchor="w",
            ).grid(row=row, column=0, padx=8, pady=5, sticky="w")
            ctk.CTkCheckBox(scroll_frame, text="", variable=active_var, width=30).grid(
                row=row, column=1, padx=8, pady=5, sticky="w"
            )
            groups_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
            groups_frame.grid(row=row, column=2, padx=8, pady=5, sticky="w")
            saved_groups = cfg.get("groups", ["G2"])
            group_vars = {}
            for group in ("G0", "G1", "G2", "G3"):
                group_var = ctk.BooleanVar(value=group in saved_groups)
                checkbox = ctk.CTkCheckBox(
                    groups_frame,
                    text=self._group_label(group),
                    variable=group_var,
                    width=64,
                    font=("Roboto", 11),
                )
                checkbox.pack(side="left", padx=2)
                self.group_label_widgets.append((checkbox, "{label}", group))
                group_vars[group] = group_var

            button = ctk.CTkButton(
                scroll_frame,
                text="⚙ Cài đặt",
                width=90,
                fg_color="#424242",
                hover_color="#616161",
                command=lambda name=ind_name: self.open_check_setting(name),
            )
            button.grid(row=row, column=3, padx=8, pady=5, sticky="w")
            self.check_widgets[ind_name] = {
                "active_var": active_var,
                "grp_vars": group_vars,
                "params": copy.deepcopy(cfg.get("params", {})),
                "group_params": copy.deepcopy(cfg.get("group_params", {})),
                "config_btn": button,
            }

    def open_check_setting(self, ind_name):
        widgets = self.check_widgets[ind_name]
        current_params = copy.deepcopy(widgets.get("params", {}))

        def on_save_params(new_params, group_params=None, _group_trigger_modes=None):
            widgets["params"] = copy.deepcopy(new_params or {})
            if group_params is not None:
                widgets["group_params"] = copy.deepcopy(group_params)
            has_override = bool(widgets.get("group_params"))
            widgets["config_btn"].configure(
                text="⚙* Cài đặt" if has_override else "⚙ Cài đặt",
                fg_color="#1565C0" if has_override else "#424242",
            )

        open_indicator_config_popup(
            self,
            ind_name,
            current_params,
            on_save_params,
            group_params=widgets.get("group_params", {}),
            global_trigger_mode="REALTIME_TICK",
            group_trigger_modes={},
            group_labels={g: self._group_label(g) for g in ("G0", "G1", "G2", "G3")},
        )

    def open_ind_setting(self, ind_name):
        current_params = dict(self.ind_widgets[ind_name]["params"])
        if ind_name == "simple_breakout":
            if "atr_buffer" not in current_params and "buffer_points" in current_params:
                current_params["atr_buffer"] = current_params["buffer_points"]
            current_params.pop("buffer_points", None)
            self.ind_widgets[ind_name]["params"] = current_params

        def on_save_params(new_params, group_params=None, group_trigger_modes=None):
            if ind_name == "simple_breakout":
                if "atr_buffer" not in new_params and "buffer_points" in new_params:
                    new_params["atr_buffer"] = new_params["buffer_points"]
                new_params.pop("buffer_points", None)
                if isinstance(group_params, dict):
                    for params in group_params.values():
                        if "atr_buffer" not in params and "buffer_points" in params:
                            params["atr_buffer"] = params["buffer_points"]
                        params.pop("buffer_points", None)
            self.ind_widgets[ind_name]["params"] = new_params
            if group_params is not None:
                self.ind_widgets[ind_name]["group_params"] = group_params
            if group_trigger_modes is not None:
                self.ind_widgets[ind_name]["group_trigger_modes"] = group_trigger_modes
            self._refresh_indicator_config_button(ind_name)

        open_indicator_config_popup(
            self,
            ind_name,
            current_params,
            on_save_params,
            group_params=self.ind_widgets[ind_name].get("group_params", {}),
            global_trigger_mode=self.ind_widgets[ind_name]["trigger_mode_var"].get(),
            group_trigger_modes=self.ind_widgets[ind_name].get("group_trigger_modes", {}),
            group_labels={g: self._group_label(g) for g in ["G0", "G1", "G2", "G3"]},
        )

    def _has_indicator_overrides(self, ind_name):
        widgets = self.ind_widgets.get(ind_name, {})
        return bool(widgets.get("group_params")) or bool(widgets.get("group_trigger_modes"))

    def _refresh_indicator_config_button(self, ind_name):
        widgets = self.ind_widgets.get(ind_name, {})
        btn = widgets.get("config_btn")
        if not btn:
            return
        if self._has_indicator_overrides(ind_name):
            btn.configure(text="* Cai dat", fg_color="#1565C0", hover_color="#0D47A1")
        else:
            btn.configure(text="Cai dat", fg_color="#424242", hover_color="#616161")
        reset_btn = widgets.get("reset_config_btn")
        if reset_btn:
            reset_btn.configure(state="normal" if self._has_indicator_overrides(ind_name) else "disabled")

    def reset_ind_overrides(self, ind_name):
        widgets = self.ind_widgets.get(ind_name)
        if not widgets:
            return
        widgets["group_params"] = {}
        widgets["group_trigger_modes"] = {}
        self._refresh_indicator_config_button(ind_name)

    def _build_voting_tab(self):
        top_frame = ctk.CTkFrame(self.tab_rules, fg_color="transparent")
        top_frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            top_frame, text="Chế độ phân xử (Master Mode):", font=("Roboto", 12, "bold")
        ).grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.master_eval_var = ctk.StringVar(
            value=self.brain_data.get("MASTER_EVAL_MODE", "VETO")
        )
        ctk.CTkComboBox(
            top_frame,
            values=["VETO", "VOTING"],
            variable=self.master_eval_var,
            width=100,
        ).grid(row=0, column=1, padx=5, pady=5)

        ctk.CTkLabel(
            top_frame, text="Min Votes (Dùng cho VOTING):", font=("Roboto", 12, "bold")
        ).grid(row=0, column=2, padx=20, pady=5, sticky="w")
        self.min_votes_var = ctk.StringVar(
            value=str(self.brain_data.get("MIN_MATCHING_VOTES", 3))
        )
        ctk.CTkEntry(
            top_frame, textvariable=self.min_votes_var, width=60, justify="center"
        ).grid(row=0, column=3, padx=5, pady=5)

        self._add_hint_box(
            self.tab_rules,
            "- VETO: FIX must have a signal; PASS may WAIT/NONE.\n"
            "- PASS with opposite direction blocks the final action; IGNORE skips group.\n"
            "- VOTING needs enough Min Votes.\n"
            "- Timeframe G0-G3 quyết định data dùng cho từng group.\n"
            "- Vote Rules decide signal/bias/confirm only; they do not choose entry price, SL, or TP.",
            pady=(5, 10),
        )

        tf_frame = ctk.CTkFrame(
            self.tab_rules,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color=COL_BLUE,
        )
        tf_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(
            tf_frame,
            text="CẤU HÌNH KHUNG THỜI GIAN (TIMEFRAMES):",
            font=("Roboto", 13, "bold"),
            text_color="#29B6F6",
        ).grid(row=0, column=0, columnspan=8, pady=5, sticky="w", padx=10)

        tfs_options = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
        for idx, grp in enumerate(["G0", "G1", "G2", "G3"]):
            ctk.CTkLabel(tf_frame, text=f"{grp}:").grid(
                row=1, column=idx * 2, padx=(15, 2), pady=10, sticky="e"
            )
            tf_var = ctk.StringVar(
                value=str(self.brain_data.get(f"{grp}_TIMEFRAME", "15m"))
            )
            ctk.CTkComboBox(
                tf_frame, values=tfs_options, variable=tf_var, width=70, command=self._refresh_group_labels
            ).grid(row=1, column=idx * 2 + 1, padx=5, pady=10)
            self.tf_vars[grp] = tf_var

        scroll_rules = ctk.CTkScrollableFrame(self.tab_rules)
        scroll_rules.pack(fill="both", expand=True, padx=10, pady=5)

        rules = self.brain_data.get("voting_rules", {})
        titles = {
            "G0": "G0: LA BÀN VĨ MÔ (MACRO)",
            "G1": "G1: BỘ LỌC XU HƯỚNG",
            "G2": "G2: ĐIỂM NỔ (TRIGGER)",
            "G3": "G3: QUYỀN PHỦ QUYẾT (VETO)",
        }
        colors = {"G0": "#AB47BC", "G1": "#00E676", "G2": "#00B0FF", "G3": "#FF3D00"}

        for grp in ["G0", "G1", "G2", "G3"]:
            default_group_rules = {
                "G0": {
                    "max_opposite": 0,
                    "max_none": 0,
                    "master_rule": "FIX",
                },
                "G1": {
                    "max_opposite": 0,
                    "max_none": 1,
                    "master_rule": "PASS",
                },
                "G2": {
                    "max_opposite": 0,
                    "max_none": 2,
                    "master_rule": "PASS",
                },
                "G3": {
                    "max_opposite": 0,
                    "max_none": 1,
                    "master_rule": "IGNORE",
                },
            }
            grp_data = rules.get(
                grp,
                default_group_rules[grp],
            )

            frame = ctk.CTkFrame(
                scroll_rules,
                fg_color=COL_PANEL,
                corner_radius=8,
                border_width=1,
                border_color=colors[grp],
            )
            frame.pack(fill="x", padx=10, pady=5)

            lbl_rule_title = ctk.CTkLabel(
                frame,
                text=f"{self._group_label(grp)}: {titles[grp].split(':', 1)[1].strip()}",
                font=("Roboto", 13, "bold"),
                text_color=colors[grp],
            )
            lbl_rule_title.grid(row=0, column=0, columnspan=4, pady=5, sticky="w", padx=10)
            self.group_label_widgets.append((lbl_rule_title, "{label}: " + titles[grp].split(":", 1)[1].strip(), grp))

            ctk.CTkLabel(frame, text="Max Opposite (Nghịch):").grid(
                row=1, column=0, padx=10, pady=5, sticky="w"
            )
            max_opp_var = ctk.StringVar(value=str(grp_data.get("max_opposite", 0)))
            ctk.CTkEntry(
                frame, textvariable=max_opp_var, width=60, justify="center"
            ).grid(row=1, column=1, padx=10, pady=5)

            ctk.CTkLabel(frame, text="Max None (Trắng):").grid(
                row=1, column=2, padx=10, pady=5, sticky="w"
            )
            max_none_var = ctk.StringVar(value=str(grp_data.get("max_none", 1)))
            ctk.CTkEntry(
                frame, textvariable=max_none_var, width=60, justify="center"
            ).grid(row=1, column=3, padx=10, pady=5)

            ctk.CTkLabel(frame, text="Master Rule (VETO):").grid(
                row=2, column=0, padx=10, pady=5, sticky="w"
            )
            master_rule_var = ctk.StringVar(value=grp_data.get("master_rule", "FIX"))
            ctk.CTkComboBox(
                frame,
                values=["FIX", "PASS", "IGNORE"],
                variable=master_rule_var,
                width=100,
            ).grid(row=2, column=1, padx=10, pady=5)

            self.vote_widgets[grp] = {
                "max_opp": max_opp_var,
                "max_none": max_none_var,
                "master_rule": master_rule_var,
            }

    def _entry_exit_cfg(self):
        cfg = _default_entry_exit_config()
        raw_entry_exit = self.brain_data.get("entry_exit", {})
        _merge_dict(cfg, raw_entry_exit)
        safe_cfg = self.brain_data.get("bot_safeguard", {})
        if not isinstance(raw_entry_exit, dict) or "default_exit" not in raw_entry_exit:
            cfg["default_exit"] = {
                "use_rr_tp": safe_cfg.get("BOT_USE_RR_TP", True),
                "tp_rr_ratio": safe_cfg.get("BOT_TP_RR_RATIO", 1.5),
                "use_swing_tp": safe_cfg.get("BOT_USE_SWING_TP", False),
            }
        return cfg

    def _collect_entry_exit_config(self):
        cfg = self._entry_exit_cfg()
        if hasattr(self, "bot_entry_exit_tactic_vars"):
            selected_entry = [k for k, v in self.bot_entry_exit_tactic_vars.items() if v.get()]
            use_fallback_r = bool(getattr(self, "bot_entry_exit_fallback_r_var", None) and self.bot_entry_exit_fallback_r_var.get())
            active = list(selected_entry)
            if use_fallback_r:
                active.append("FALLBACK_R")
            exit_tactic = getattr(self, "bot_entry_exit_var", None)
            exit_tactic = exit_tactic.get() if exit_tactic else cfg.get("exit_tactic")
            exit_tactic = EE_EXIT_VALUES.get(exit_tactic, exit_tactic)
            sl_mode = getattr(self, "bot_entry_exit_sl_var", None)
            sl_mode = sl_mode.get() if sl_mode else cfg.get("sl_mode", "SANDBOX")
            sl_mode = EE_SL_VALUES.get(sl_mode, sl_mode)
            missing_policy = getattr(self, "bot_entry_exit_missing_var", None)
            missing_policy = missing_policy.get() if missing_policy else cfg.get("missing_data_policy", "FALLBACK_R")
            missing_policy = EE_MISSING_VALUES.get(missing_policy, missing_policy)
            cfg["active_tactics"] = active
            cfg["entry_tactics"] = active or ["SWING_REJECTION"]
            cfg["exit_tactic"] = exit_tactic or "AUTO"
            cfg["sl_mode"] = sl_mode or "SANDBOX"
            cfg["missing_data_policy"] = missing_policy or "FALLBACK_R"
            cfg["fallback_tactic"] = "FALLBACK_R" if use_fallback_r else "OFF"
            cfg.setdefault("default_exit", {})
            cfg["default_exit"]["use_rr_tp"] = cfg["exit_tactic"] == "FALLBACK_R"
            cfg["default_exit"]["use_swing_tp"] = cfg["exit_tactic"] in (
                "SWING_REJECTION",
                "SWING_STRUCTURE",
            )
            cfg["enabled"] = bool(active)
            preview_var = getattr(self, "bot_entry_exit_preview_only_var", None)
            cfg["preview_only"] = (
                bool(preview_var.get())
                if preview_var is not None
                else bool(cfg.get("preview_only", False))
            )
        return cfg

    def _build_risk_tab(self):
        risk_data = self.brain_data.get("risk_tsl", {})
        self.var_tsl_mode = ctk.StringVar(value=risk_data.get("tsl_mode", getattr(config, "TSL_LOGIC_MODE", "STATIC")))

        self._add_hint_box(
            self.tab_risk,
            "- Force ANY Mode: bỏ qua macro/mode, phù hợp scalping khi muốn indicator luôn chạy.\n"
            "- Base Risk: risk gốc của bot; Market Mode multiplier sẽ nhân thêm để ra risk thực tế.\n"
            "- Nguồn SL G0-G3: chọn group dùng Swing/ATR để cắm SL; DYNAMIC-G1/G2 dùng G1 khi TREND/BREAKOUT, còn lại dùng G2.\n"
            "- SWING TSL Logic Mode nằm trong popup TSL; chỉ ảnh hưởng TSL SWING sau khi lệnh đã mở, không phải SL ban đầu Swing + ATR buffer.\n"
            "- REV_C/Close on Reverse: cắt lệnh bot khi tín hiệu đảo chiều; có thể yêu cầu giữ lệnh tối thiểu, min profit hoặc max loss.\n"
            "- Watermark/Basket nằm ở Bot Safeguard: bảo vệ lợi nhuận toàn bot/rổ DCA-PCA, không dùng cho lệnh manual.",
            padx=20,
            pady=(10, 5),
        )

        # [UI] Ghi rõ thứ tự ưu tiên SL/TP để hết mơ hồ "cái nào thắng".
        ctk.CTkLabel(
            self.tab_risk,
            text=("Thứ tự SL: Entry/Exit SL mode › Swing nhóm base_sl − buffer ATR.    "
                  "Thứ tự TP: Entry/Exit TP mode › Swing TP › RR ratio › Không TP."),
            font=("Roboto", 11, "bold"),
            text_color="#80CBC4",
            justify="left",
            anchor="w",
            wraplength=900,
        ).pack(fill="x", padx=20, pady=(0, 4))

        # --- [NEW] CỤM OPTIONS NÂNG CAO (SCALPING & STRICT RISK) ---
        f_adv = ctk.CTkFrame(
            self.tab_risk,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color="#00B8D4",
        )
        f_adv.pack(fill="x", padx=20, pady=(10, 10))
        f_adv.grid_columnconfigure(0, weight=0)
        f_adv.grid_columnconfigure(1, weight=1)

        self.var_force_any = ctk.BooleanVar(
            value=self.brain_data.get("FORCE_ANY_MODE", False)
        )
        ctk.CTkCheckBox(
            f_adv,
            text="Force ANY Mode (Scalping)",
            variable=self.var_force_any,
            font=("Roboto", 13, "bold"),
            text_color="#FF9800",
        ).grid(row=0, column=0, padx=15, pady=10, sticky="w")

        self.var_strict_risk = ctk.BooleanVar(value=risk_data.get("strict_risk", False))
        ctk.CTkCheckBox(
            f_adv,
            text="Strict Risk (Trừ Phí)",
            variable=self.var_strict_risk,
            font=("Roboto", 13, "bold"),
            text_color="#F44336",
        ).grid(row=0, column=1, padx=15, pady=10, sticky="w")
        ctk.CTkLabel(
            f_adv,
            text=(
                "Strict Risk chỉ giảm số HĐ/CP để phần lỗ tại SL cộng spread và phí "
                "không vượt ngân sách rủi ro; không đổi tín hiệu, Entry, SL hoặc TP."
            ),
            text_color="#90CAF9",
            font=("Roboto", 11, "italic"),
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, padx=15, pady=(0, 10), sticky="w")

        # [FIX V4.4] TCH HỢP TNH NĂNG CLOSE ON REVERSE VÀO SANDBOX
        import os, json

        safe_cfg = {}
        try:
            if self.override_symbol:
                from core.storage_manager import get_brain_settings_for_symbol
                safe_cfg = get_brain_settings_for_symbol(self.override_symbol).get("bot_safeguard", {})
            else:
                _cfg_path = _get_brain_path()
                if os.path.exists(_cfg_path):
                    with open(_cfg_path, "r", encoding="utf-8") as _f:
                        safe_cfg = json.load(_f).get("bot_safeguard", {})
        except:
            pass

        self.var_close_rev = ctk.BooleanVar(
            value=safe_cfg.get("CLOSE_ON_REVERSE", False)
        )
        ctk.CTkCheckBox(
            f_adv,
            text="Close on Reverse (Đảo chiều cắt lệnh)",
            variable=self.var_close_rev,
            font=("Roboto", 13, "bold"),
            text_color="#00E676",
        ).grid(row=1, column=0, padx=15, pady=10, sticky="w")

        f_rev_time = ctk.CTkFrame(f_adv, fg_color="transparent")
        f_rev_time.grid(row=1, column=1, padx=15, pady=10, sticky="ew")
        ctk.CTkLabel(f_rev_time, text="Min Hold Time (s):").grid(row=0, column=0, padx=(0, 5), pady=3, sticky="w")
        self.var_rev_time = ctk.StringVar(
            value=str(safe_cfg.get("CLOSE_ON_REVERSE_MIN_TIME", 180))
        )
        ctk.CTkEntry(
            f_rev_time, textvariable=self.var_rev_time, width=60, justify="center"
        ).grid(row=0, column=1, padx=(0, 10), pady=3, sticky="w")

        self.var_close_rev_pnl = ctk.BooleanVar(
            value=safe_cfg.get("CLOSE_ON_REVERSE_USE_PNL", True)
        )
        ctk.CTkCheckBox(
            f_rev_time,
            text="Use PnL Check",
            variable=self.var_close_rev_pnl,
            font=("Roboto", 12),
        ).grid(row=0, column=2, padx=(0, 10), pady=3, sticky="w")

        self.var_rev_none = ctk.BooleanVar(
            value=safe_cfg.get("REV_CLOSE_ON_NONE", False)
        )
        ctk.CTkCheckBox(
            f_rev_time,
            text="NONE cũng cắt",
            variable=self.var_rev_none,
            font=("Roboto", 12),
        ).grid(row=0, column=3, padx=(0, 10), pady=3, sticky="w")

        ctk.CTkLabel(f_rev_time, text="Min Profit:").grid(row=1, column=0, padx=(0, 5), pady=3, sticky="w")
        rev_profit_unit = safe_cfg.get("REV_CLOSE_MIN_PROFIT_UNIT", "USD")
        rev_profit_value = float(safe_cfg.get("REV_CLOSE_MIN_PROFIT", 0.0) or 0.0)
        if rev_profit_unit in ("%R", "PERCENT_R"):
            _rev_profit_str = str(rev_profit_value / 100.0)
        else:
            # Ô setting tiền luôn nhập VND thật; kiểu hiển thị không đổi giá trị nhập.
            _rev_profit_str = money_input_to_display(rev_profit_value, rev_profit_unit)
        self.var_rev_profit = ctk.StringVar(value=_rev_profit_str)
        ctk.CTkEntry(
            f_rev_time, textvariable=self.var_rev_profit, width=50, justify="center"
        ).grid(row=1, column=1, padx=(0, 5), pady=3, sticky="w")
        self.cbo_rev_profit_unit = ctk.CTkOptionMenu(
            f_rev_time, values=["VND", "R", "%Equity"], width=85
        )
        self.cbo_rev_profit_unit.set("R" if rev_profit_unit in ("%R", "PERCENT_R") else unit_to_display(rev_profit_unit))
        self.cbo_rev_profit_unit.grid(row=1, column=2, padx=(0, 10), pady=3, sticky="w")

        ctk.CTkLabel(f_rev_time, text="Max Loss:").grid(row=1, column=3, padx=(0, 5), pady=3, sticky="w")
        rev_loss_unit = safe_cfg.get("REV_CLOSE_MAX_LOSS_UNIT", "USD")
        rev_loss_value = float(safe_cfg.get("REV_CLOSE_MAX_LOSS", 0.0) or 0.0)
        if rev_loss_unit in ("%R", "PERCENT_R"):
            _rev_loss_str = str(rev_loss_value / 100.0)
        else:
            _rev_loss_str = money_input_to_display(rev_loss_value, rev_loss_unit)
        self.var_rev_loss = ctk.StringVar(value=_rev_loss_str)
        ctk.CTkEntry(
            f_rev_time, textvariable=self.var_rev_loss, width=50, justify="center"
        ).grid(row=1, column=4, padx=(0, 5), pady=3, sticky="w")
        self.cbo_rev_loss_unit = ctk.CTkOptionMenu(
            f_rev_time, values=["VND", "R", "%Equity"], width=85
        )
        self.cbo_rev_loss_unit.set("R" if rev_loss_unit in ("%R", "PERCENT_R") else unit_to_display(rev_loss_unit))
        self.cbo_rev_loss_unit.grid(row=1, column=5, padx=(0, 5), pady=3, sticky="w")

        ctk.CTkLabel(f_rev_time, text="Confirm(s):").grid(row=2, column=0, padx=(0, 5), pady=3, sticky="w")
        self.var_rev_confirm_seconds = ctk.StringVar(
            value=str(safe_cfg.get("REV_CONFIRM_SECONDS", 300))
        )
        ctk.CTkEntry(
            f_rev_time, textvariable=self.var_rev_confirm_seconds, width=60, justify="center"
        ).grid(row=2, column=1, padx=(0, 5), pady=3, sticky="w")
        ctk.CTkLabel(f_rev_time, text="Scans:").grid(row=2, column=2, padx=(0, 5), pady=3, sticky="w")
        self.var_rev_confirm_scans = ctk.StringVar(
            value=str(safe_cfg.get("REV_CONFIRM_SCANS", 2))
        )
        ctk.CTkEntry(
            f_rev_time, textvariable=self.var_rev_confirm_scans, width=50, justify="center"
        ).grid(row=2, column=3, padx=(0, 5), pady=3, sticky="w")
        ctk.CTkLabel(
            f_rev_time,
            text=(
                "REV_C: khi signal đảo chiều, lệnh lời chỉ cắt nếu PnL >= Min Profit; "
                "lệnh âm chỉ cắt nếu PnL <= Max Loss. Giá trị 0 = bỏ qua điều kiện phía đó.\n"
                + money_setting_hint()
            ),
            font=("Arial", 11, "italic"),
            text_color="#B0BEC5",
            wraplength=760,
            justify="left",
            anchor="w",
        ).grid(row=3, column=0, columnspan=6, padx=(0, 5), pady=(4, 0), sticky="ew")
        # -------------------------------------------------------------

        effective_sl_buffer = float(risk_data.get("sl_atr_multiplier", 0.2) or 0.2)
        scope_text = (
            f"PHẠM VI: OVERRIDE {self.override_symbol} · "
            f"BOT SL {risk_data.get('base_sl', 'G2')} · "
            f"BUFFER {effective_sl_buffer:g} ATR"
            if self.override_symbol
            else
            f"PHẠM VI: GLOBAL · BOT SL {risk_data.get('base_sl', 'G2')} · "
            f"BUFFER {effective_sl_buffer:g} ATR"
        )
        ctk.CTkLabel(
            self.tab_risk,
            text=scope_text,
            font=("Roboto", 13, "bold"),
            text_color="#FFD54F" if not self.override_symbol else "#69F0AE",
        ).pack(anchor="w", padx=20, pady=(6, 4))

        f_base = ctk.CTkFrame(self.tab_risk, fg_color="transparent")
        f_base.pack(fill="x", padx=20, pady=(5, 5))
        ctk.CTkLabel(
            f_base,
            text="BOT BASE RISK (% Cắt lỗ/Lệnh):",
            font=("Roboto", 13, "bold"),
            text_color="#E040FB",
        ).pack(side="left")
        self.var_base_risk = ctk.StringVar(value=str(risk_data.get("base_risk", 0.3)))
        ctk.CTkEntry(
            f_base, textvariable=self.var_base_risk, width=80, justify="center"
        ).pack(side="left", padx=15)

        f_base_sl = ctk.CTkFrame(self.tab_risk, fg_color="transparent")
        f_base_sl.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(
            f_base_sl,
            text="BOT BASE SL GROUP:",
            font=("Roboto", 13, "bold"),
            text_color="#FF3D00",
        ).pack(side="left")

        cur_sl = risk_data.get("base_sl", "G2")
        if cur_sl == "entry":
            cur_sl = "G2"
        self.var_base_sl = ctk.StringVar(value=cur_sl)
        swing_cfg = self.brain_data.get("indicators", {}).get("swing_point", {})
        swing_params = copy.deepcopy(swing_cfg.get("params", {})) if isinstance(swing_cfg, dict) else {}
        swing_group_params = copy.deepcopy(swing_cfg.get("group_params", {})) if isinstance(swing_cfg, dict) else {}
        if not isinstance(swing_params, dict):
            swing_params = {}
        if not isinstance(swing_group_params, dict):
            swing_group_params = {}
        self.swing_group_params = swing_group_params

        def _effective_swing_group(raw_group):
            group = str(raw_group or "G2").upper()
            return "G1" if "DYNAMIC" in group else group if group in {"G0", "G1", "G2", "G3"} else "G2"

        def _swing_lookback_for(raw_group):
            group = _effective_swing_group(raw_group)
            group_cfg = self.swing_group_params.get(group, {})
            if not isinstance(group_cfg, dict):
                group_cfg = {}
            return max(
                5,
                min(
                    100,
                    int(group_cfg.get("lookback", swing_params.get("lookback", 50)) or 50),
                ),
            )

        def _sync_swing_lookback(raw_group):
            if hasattr(self, "var_swing_lookback"):
                self.var_swing_lookback.set(str(_swing_lookback_for(raw_group)))
        # [NEW] Thêm Option DYNAMIC vào Base SL
        ctk.CTkComboBox(
            f_base_sl,
            values=["G0", "G1", "G2", "G3", "DYNAMIC-G1/G2"],
            variable=self.var_base_sl,
            width=140,
            command=_sync_swing_lookback,
        ).pack(side="left", padx=15)
        ctk.CTkLabel(
            f_base_sl,
            text="SL gốc của bot: BUY dùng swing low group này, SELL dùng swing high group này.",
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
        ).pack(side="left", padx=(0, 10))

        # [NEW] ATR Multiplier cho Bot SL
        f_sl_mult = ctk.CTkFrame(self.tab_risk, fg_color="transparent")
        f_sl_mult.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(
            f_sl_mult,
            text="BOT BASE SL BUFFER ATR:",
            font=("Roboto", 13, "bold"),
            text_color="#29B6F6",
        ).pack(side="left")
        self.var_sl_mult = ctk.StringVar(value=str(risk_data.get("sl_atr_multiplier", 0.2)))
        ctk.CTkEntry(
            f_sl_mult, textvariable=self.var_sl_mult, width=80, justify="center"
        ).pack(side="left", padx=15)
        ctk.CTkLabel(
            f_sl_mult,
            text="Buffer cộng thêm quanh SL Sandbox.",
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
        ).pack(side="left", padx=(0, 10))

        f_swing_lookback = ctk.CTkFrame(self.tab_risk, fg_color="transparent")
        f_swing_lookback.pack(fill="x", padx=20, pady=5)
        ctk.CTkLabel(
            f_swing_lookback,
            text="SWING LOOKBACK (NẾN):",
            font=("Roboto", 13, "bold"),
            text_color="#FFD54F",
        ).pack(side="left")
        self.var_swing_lookback = ctk.StringVar(value=str(_swing_lookback_for(cur_sl)))
        ctk.CTkEntry(
            f_swing_lookback,
            textvariable=self.var_swing_lookback,
            width=80,
            justify="center",
        ).pack(side="left", padx=15)
        ctk.CTkLabel(
            f_swing_lookback,
            text="Áp dụng cho BOT BASE SL GROUP đang chọn; 5–100 nến. VN30 G1 M15 mặc định 12.",
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            self.tab_risk,
            text="BOT TSL TACTICS:",
            font=("Roboto", 13, "bold"),
            text_color="#00C853",
        ).pack(anchor="w", padx=20, pady=(10, 0))
        f_tactic_btns = ctk.CTkFrame(self.tab_risk, fg_color="transparent")
        f_tactic_btns.pack(fill="x", padx=20, pady=5)

        self.bot_tactic_vars = {}
        current_tactic_str = risk_data.get("bot_tsl", "BE+STEP_R+SWING")
        current_tactics = {x.strip() for x in str(current_tactic_str).split("+") if x.strip()}
        if "BE_CASH" in current_tactics:
            current_tactics.discard("BE")

        # [NEW V4.4] Bổ sung thêm BE_CASH và PSAR_TRAIL vào danh sách chiến thuật Bot
        for t in ["BE", "PNL", "STEP_R", "SWING", "BE_CASH", "PSAR_TRAIL", "ANTI_CASH"]:
            is_active = t in current_tactics
            var = ctk.BooleanVar(value=is_active)
            ctk.CTkCheckBox(
                f_tactic_btns,
                text=t,
                variable=var,
                font=("Roboto", 12, "bold"),
                width=80,
            ).pack(side="left", padx=10)
            self.bot_tactic_vars[t] = var

        ctk.CTkLabel(
            self.tab_risk,
            text="BOT ENTRY / EXIT:",
            font=("Roboto", 13, "bold"),
            text_color="#00B8D4",
        ).pack(anchor="w", padx=20, pady=(10, 0))
        f_entry_btns = ctk.CTkFrame(
            self.tab_risk,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color="#00B8D4",
        )
        f_entry_btns.pack(fill="x", padx=20, pady=5)
        self.bot_entry_exit_preview_only_var = ctk.BooleanVar(
            value=bool(self._entry_exit_cfg().get("preview_only", False))
        )
        ctk.CTkCheckBox(
            f_entry_btns,
            text=(
                "E/E CHỈ XEM — bật: chỉ xem, không chặn BOT; "
                "tắt: BOT phải chờ Swing Retest READY"
            ),
            variable=self.bot_entry_exit_preview_only_var,
            font=("Roboto", 12, "bold"),
            text_color="#FFCA28",
        ).pack(anchor="w", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            f_entry_btns,
            text="1. ENTRY MODE — mode READY đầu tiên được chọn:",
            font=("Roboto", 12, "bold"),
            text_color="#D7DCE2",
        ).pack(anchor="w", padx=12, pady=(8, 2))
        self.bot_entry_exit_tactic_vars = {}
        entry_exit_data = self._entry_exit_cfg()
        current_entry_tactics = (
            set(entry_exit_data.get("entry_tactics", []))
            if entry_exit_data.get("enabled") and entry_exit_data.get("active_tactics")
            else set()
        )
        entry_tactic_labels = {
            "SWING_REJECTION": "SWING RETEST",
            "SWING_STRUCTURE": "SWING STRUCT",
            "FIB_RETRACE": "FIB",
            "PULLBACK_ZONE": "PULLBACK",
        }
        f_entry_checks = ctk.CTkFrame(f_entry_btns, fg_color="transparent")
        f_entry_checks.pack(fill="x", padx=4, pady=(0, 8))
        for key, label in entry_tactic_labels.items():
            var = ctk.BooleanVar(value=key in current_entry_tactics)
            ctk.CTkCheckBox(
                f_entry_checks,
                text=label,
                variable=var,
                font=("Roboto", 12, "bold"),
                width=100,
            ).pack(side="left", padx=10)
            self.bot_entry_exit_tactic_vars[key] = var
        self.bot_entry_exit_fallback_r_var = ctk.BooleanVar(value="FALLBACK_R" in current_entry_tactics)
        ctk.CTkCheckBox(
            f_entry_checks,
            text="Fallback R",
            variable=self.bot_entry_exit_fallback_r_var,
            font=("Roboto", 12, "bold"),
            width=110,
        ).pack(side="left", padx=(18, 10))
        ctk.CTkLabel(
            f_entry_btns,
            text=(
                "Mặc định tắt: E/E là cổng vào lệnh BOT. "
                "SL Sandbox dùng SL BOT; TP mặc định dùng Swing Retest, không dùng RR."
            ),
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 6))
        f_policy_pick = ctk.CTkFrame(f_entry_btns, fg_color="transparent")
        f_policy_pick.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(
            f_policy_pick,
            text="2. DATA POLICY:",
            font=("Roboto", 12, "bold"),
            text_color="#D7DCE2",
            width=120,
            anchor="w",
        ).pack(side="left", padx=(0, 8))
        self.bot_entry_exit_missing_var = ctk.StringVar(
            value=EE_MISSING_LABELS.get(entry_exit_data.get("missing_data_policy", "FALLBACK_R"), "Thiếu dữ liệu -> dùng R")
        )
        ctk.CTkOptionMenu(
            f_policy_pick,
            values=list(EE_MISSING_VALUES.keys()),
            variable=self.bot_entry_exit_missing_var,
            width=190,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            f_policy_pick,
            text="Quyết định cách xử lý khi tactic thiếu dữ liệu.",
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
            wraplength=820,
            justify="left",
        ).pack(side="left")
        f_sl_pick = ctk.CTkFrame(f_entry_btns, fg_color="transparent")
        f_sl_pick.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(
            f_sl_pick,
            text="3. SL MODE:",
            font=("Roboto", 12, "bold"),
            text_color="#D7DCE2",
            width=120,
            anchor="w",
        ).pack(side="left", padx=(0, 8))
        self.bot_entry_exit_sl_var = ctk.StringVar(
            value=EE_SL_LABELS.get(entry_exit_data.get("sl_mode", "SANDBOX"), "SL Sandbox (không override)")
        )
        ctk.CTkOptionMenu(
            f_sl_pick,
            values=EE_SL_PICK_OPTIONS,
            variable=self.bot_entry_exit_sl_var,
            width=260,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            f_sl_pick,
            text=(
                "Sandbox dùng SL BOT theo mã. Retest/Struct dùng SL của tactic. "
                "Chỉ tác động lệnh BOT khi CHỈ PREVIEW đã tắt."
            ),
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
            wraplength=820,
            justify="left",
        ).pack(side="left")
        f_exit_pick = ctk.CTkFrame(f_entry_btns, fg_color="transparent")
        f_exit_pick.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(
            f_exit_pick,
            text="4. TP MODE:",
            font=("Roboto", 12, "bold"),
            text_color="#D7DCE2",
            width=120,
            anchor="w",
        ).pack(side="left", padx=(0, 8))
        self.bot_entry_exit_var = ctk.StringVar(
            value=EE_EXIT_LABELS.get(entry_exit_data.get("exit_tactic", "AUTO"), "TP theo Entry thắng")
        )
        ctk.CTkOptionMenu(
            f_exit_pick,
            values=list(EE_EXIT_VALUES.keys()),
            variable=self.bot_entry_exit_var,
            width=210,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(
            f_exit_pick,
            text=(
                "Mục tiêu theo tactic đã chọn. Khi CHỈ PREVIEW bật, đây chỉ là thông tin xem trước."
            ),
            font=("Roboto", 11, "italic"),
            text_color="#B0BEC5",
            wraplength=820,
            justify="left",
        ).pack(side="left")

        ctk.CTkFrame(self.tab_risk, height=2, fg_color="#333").pack(
            fill="x", padx=20, pady=15
        )
        # [UI] Phần ít dùng -> gập vào "Nâng cao" (mặc định đóng). Lego: vẫn chỉnh được khi mở.
        self._mult_adv_open = False
        f_mult = ctk.CTkFrame(
            self.tab_risk,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color=COL_AMBER,
        )

        btn_mult_adv = ctk.CTkButton(
            self.tab_risk,
            text="▸ Nâng cao: Dynamic Risk Multipliers (hệ số rủi ro theo Market Mode)",
            font=("Roboto", 12, "bold"),
            fg_color="#37474F",
            hover_color="#455A64",
            anchor="w",
            text_color="#FFB300",
        )
        btn_mult_adv.pack(fill="x", padx=20, pady=5)

        def _toggle_mult_adv():
            self._mult_adv_open = not self._mult_adv_open
            if self._mult_adv_open:
                f_mult.pack(fill="x", padx=20, after=btn_mult_adv)
                btn_mult_adv.configure(text="▾ Nâng cao: Dynamic Risk Multipliers (hệ số rủi ro theo Market Mode)")
            else:
                f_mult.pack_forget()
                btn_mult_adv.configure(text="▸ Nâng cao: Dynamic Risk Multipliers (hệ số rủi ro theo Market Mode)")

        btn_mult_adv.configure(command=_toggle_mult_adv)

        mults = risk_data.get("mode_multipliers", {})
        modes = ["ANY", "TREND", "RANGE", "BREAKOUT", "EXHAUSTION"]
        self.mult_vars = {}

        for i, mode in enumerate(modes):
            ctk.CTkLabel(f_mult, text=f"{mode}:").grid(
                row=i // 3, column=(i % 3) * 2, padx=15, pady=10, sticky="e"
            )
            var = ctk.StringVar(value=str(mults.get(mode, 1.0)))
            ctk.CTkEntry(f_mult, textvariable=var, width=60, justify="center").grid(
                row=i // 3, column=(i % 3) * 2 + 1, padx=5, pady=10, sticky="w"
            )
            self.mult_vars[mode] = var

    def _build_dca_pca_tab(self):
        dca_cfg = self.brain_data.get("dca_config", {})
        pca_cfg = self.brain_data.get("pca_config", {})

        self._add_hint_box(
            self.tab_dca_pca,
            "- SL lenh con co the bam SL me hoac lay SwingPoint theo Nguon cam SL; SwingPoint khong cong ATR buffer.\n"
            "- DCA nhồi khi giá đi ngược lệnh mẹ theo khoảng ATR.\n"
            "- PCA nhồi thuận khi lệnh mẹ đang đúng hướng/trend.\n"
            "- Mini-Brain nếu bật sẽ xác nhận riêng trước khi nhồi.",
            pady=(10, 5),
        )

        # --- DCA FRAME ---
        dca_frame = ctk.CTkFrame(
            self.tab_dca_pca,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color=COL_AMBER,
        )
        dca_frame.pack(fill="x", padx=10, pady=10)

        self.dca_active = ctk.BooleanVar(value=dca_cfg.get("ENABLED", False))
        ctk.CTkCheckBox(
            dca_frame,
            text="Kích hoạt AUTO DCA (Gồng lỗ/Bắt đáy thuận nến)",
            variable=self.dca_active,
            font=("Roboto", 13, "bold"),
            text_color="#FFAB00",
        ).grid(row=0, column=0, columnspan=6, padx=10, pady=10, sticky="w")

        # [NEW V5.1] Nút cài đặt Mini-Brain cho DCA
        self.dca_mb_cfg = dca_cfg.get("MINI_BRAIN", {})
        ctk.CTkButton(
            dca_frame, 
            text="⚙ Cài đặt Mini-Brain", 
            width=120, 
            fg_color="#F57C00", 
            command=lambda: self._open_mb_popup("DCA")
        ).grid(row=0, column=6, padx=20, pady=10, sticky="e")

        ctk.CTkLabel(dca_frame, text="Max Steps:").grid(
            row=1, column=0, padx=10, pady=5, sticky="w"
        )
        self.dca_steps = ctk.StringVar(value=str(dca_cfg.get("MAX_STEPS", 3)))
        ctk.CTkEntry(dca_frame, textvariable=self.dca_steps, width=70).grid(
            row=1, column=1, padx=10, pady=5
        )

        ctk.CTkLabel(dca_frame, text="Vol Multiplier (x):").grid(
            row=1, column=2, padx=10, pady=5, sticky="w"
        )
        self.dca_mult = ctk.StringVar(value=str(dca_cfg.get("STEP_MULTIPLIER", 1.5)))
        ctk.CTkEntry(dca_frame, textvariable=self.dca_mult, width=70).grid(
            row=1, column=3, padx=10, pady=5
        )

        # [NEW] ATR Distance cho DCA
        ctk.CTkLabel(dca_frame, text="ATR Distance:").grid(
            row=1, column=4, padx=10, pady=5, sticky="w"
        )
        self.dca_atr = ctk.StringVar(value=str(dca_cfg.get("DISTANCE_ATR_R", 1.0)))
        ctk.CTkEntry(dca_frame, textvariable=self.dca_atr, width=70).grid(
            row=1, column=5, padx=10, pady=5
        )

        self.dca_use_parent_sl = ctk.BooleanVar(value=dca_cfg.get("USE_PARENT_SL", True))
        ctk.CTkCheckBox(
            dca_frame,
            text="DCA dung SL lenh me (bo tick = SwingPoint theo nguon SL, khong ATR buffer)",
            variable=self.dca_use_parent_sl,
            font=("Roboto", 11),
            text_color="#BDBDBD",
        ).grid(row=2, column=0, columnspan=7, padx=10, pady=(2, 10), sticky="w")

        # --- PCA FRAME ---
        pca_frame = ctk.CTkFrame(
            self.tab_dca_pca,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color="#00C853",
        )
        pca_frame.pack(fill="x", padx=10, pady=10)

        self.pca_active = ctk.BooleanVar(value=pca_cfg.get("ENABLED", False))
        ctk.CTkCheckBox(
            pca_frame,
            text="Kích hoạt AUTO PCA (Nhồi thuận Trend mạnh)",
            variable=self.pca_active,
            font=("Roboto", 13, "bold"),
            text_color="#00C853",
        ).grid(row=0, column=0, columnspan=6, padx=10, pady=10, sticky="w")

        # [NEW V5.1] Nút cài đặt Mini-Brain cho PCA
        self.pca_mb_cfg = pca_cfg.get("MINI_BRAIN", {})
        ctk.CTkButton(
            pca_frame, 
            text="⚙ Cài đặt Mini-Brain", 
            width=120, 
            fg_color="#00C853", 
            command=lambda: self._open_mb_popup("PCA")
        ).grid(row=0, column=6, padx=20, pady=10, sticky="e")

        ctk.CTkLabel(pca_frame, text="Max Steps:").grid(
            row=1, column=0, padx=10, pady=5, sticky="w"
        )
        self.pca_steps = ctk.StringVar(value=str(pca_cfg.get("MAX_STEPS", 2)))
        ctk.CTkEntry(pca_frame, textvariable=self.pca_steps, width=70).grid(
            row=1, column=1, padx=10, pady=5
        )

        ctk.CTkLabel(pca_frame, text="Vol Multiplier (x):").grid(
            row=1, column=2, padx=10, pady=5, sticky="w"
        )
        self.pca_mult = ctk.StringVar(value=str(pca_cfg.get("STEP_MULTIPLIER", 0.5)))
        ctk.CTkEntry(pca_frame, textvariable=self.pca_mult, width=70).grid(
            row=1, column=3, padx=10, pady=5
        )

        # [NEW] ATR Distance cho PCA
        ctk.CTkLabel(pca_frame, text="ATR Distance:").grid(
            row=1, column=4, padx=10, pady=5, sticky="w"
        )
        self.pca_atr = ctk.StringVar(value=str(pca_cfg.get("DISTANCE_ATR_R", 1.5)))
        ctk.CTkEntry(pca_frame, textvariable=self.pca_atr, width=70).grid(
            row=1, column=5, padx=10, pady=5
        )

        self.pca_use_parent_sl = ctk.BooleanVar(value=pca_cfg.get("USE_PARENT_SL", True))
        ctk.CTkCheckBox(
            pca_frame,
            text="PCA dung SL lenh me (bo tick = SwingPoint theo nguon SL, khong ATR buffer)",
            variable=self.pca_use_parent_sl,
            font=("Roboto", 11),
            text_color="#BDBDBD",
        ).grid(row=2, column=0, columnspan=7, padx=10, pady=(2, 10), sticky="w")

        # --- COOLDOWN FRAME ---
        cd_frame = ctk.CTkFrame(
            self.tab_dca_pca,
            fg_color=COL_PANEL,
            corner_radius=8,
            border_width=1,
            border_color=COL_BLUE,
        )
        cd_frame.pack(fill="x", padx=10, pady=10)

        ctk.CTkLabel(
            cd_frame,
            text="DCA/PCA Cooldown (giây):",
            font=("Roboto", 12, "bold"),
            text_color="#29B6F6",
        ).grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.dca_pca_cooldown = ctk.StringVar(value=str(dca_cfg.get("COOLDOWN", 60)))
        ctk.CTkEntry(cd_frame, textvariable=self.dca_pca_cooldown, width=70).grid(
            row=0, column=1, padx=10, pady=10
        )

    def _pack_data(self):
        new_inds = {}
        for ind_name, widgets in self.ind_widgets.items():
            mode_val = widgets["mode_var"].get()
            # Trích xuất mảng các Group được chn
            selected_groups = [g for g, var in widgets["grp_vars"].items() if var.get()]
            params = dict(widgets["params"])
            if ind_name == "simple_breakout":
                if "atr_buffer" not in params and "buffer_points" in params:
                    params["atr_buffer"] = params["buffer_points"]
                params.pop("buffer_points", None)

            new_inds[ind_name] = {
                "active": widgets["active_var"].get(),
                "groups": selected_groups,
                "is_trend": widgets["is_trend_var"].get(),
                "macro_role": widgets["macro_role_var"].get(),
                "active_modes": [mode_val] if mode_val != "ANY" else ["ANY"],
                "trigger_mode": widgets["trigger_mode_var"].get(),
                "params": params,
                "group_params": dict(widgets.get("group_params", {})),
                "group_trigger_modes": dict(widgets.get("group_trigger_modes", {})),
            }

        # Đây là cửa sổ cấu trúc dùng cho SL Sandbox, Entry/Exit Preview và
        # Telegram; không tự biến SWING_POINT thành phiếu BUY/SELL.
        swing_group = str(self.var_base_sl.get() or "G2").upper()
        swing_groups = ["G1", "G2"] if "DYNAMIC" in swing_group else [swing_group]
        swing_groups = [g for g in swing_groups if g in {"G0", "G1", "G2", "G3"}]
        try:
            swing_lookback = max(5, min(100, int(float(self.var_swing_lookback.get() or 50))))
        except Exception:
            swing_lookback = 50
        swing_ind = new_inds.setdefault(
            "swing_point",
            {
                "active": False,
                "groups": ["G2"],
                "is_trend": False,
                "macro_role": "NONE",
                "active_modes": ["ANY"],
                "trigger_mode": "STRICT_CLOSE",
                "params": {"lookback": 50, "strength": 2, "atr_buffer": 0.5},
                "group_params": {},
                "group_trigger_modes": {},
            },
        )
        group_params = swing_ind.setdefault("group_params", {})
        if not isinstance(group_params, dict):
            group_params = {}
            swing_ind["group_params"] = group_params
        for group in swing_groups:
            params = group_params.setdefault(group, {})
            if not isinstance(params, dict):
                params = {}
                group_params[group] = params
            params["lookback"] = swing_lookback

        new_check_inds = {}
        for ind_name, widgets in self.check_widgets.items():
            params = copy.deepcopy(widgets.get("params", {}))
            if ind_name == "simple_breakout":
                if "atr_buffer" not in params and "buffer_points" in params:
                    params["atr_buffer"] = params["buffer_points"]
                params.pop("buffer_points", None)
            new_check_inds[ind_name] = {
                "active": bool(widgets["active_var"].get()),
                "groups": [g for g, var in widgets["grp_vars"].items() if var.get()],
                "params": params,
                "group_params": copy.deepcopy(widgets.get("group_params", {})),
            }

        new_voting = {}
        for grp in ["G0", "G1", "G2", "G3"]:
            new_voting[grp] = {
                "max_opposite": int(self.vote_widgets[grp]["max_opp"].get() or 0),
                "max_none": int(self.vote_widgets[grp]["max_none"].get() or 1),
                "master_rule": self.vote_widgets[grp]["master_rule"].get(),
            }

        selected_tactics = [k for k, v in self.bot_tactic_vars.items() if v.get()]
        if "BE_CASH" in selected_tactics and "BE" in selected_tactics:
            selected_tactics.remove("BE")
        bot_tsl_str = "+".join(selected_tactics) if selected_tactics else "OFF"

        new_risk_tsl = {
            "base_risk": float(self.var_base_risk.get() or 0.3),
            "base_sl": self.var_base_sl.get(),
            "sl_atr_multiplier": float(self.var_sl_mult.get() or 0.2), # [NEW]
            "tsl_mode": getattr(config, "TSL_LOGIC_MODE", self.var_tsl_mode.get()),
            "bot_tsl": bot_tsl_str,
            "mode_multipliers": {
                mode: float(var.get() or 1.0) for mode, var in self.mult_vars.items()
            },
            "strict_risk": self.var_strict_risk.get(),  # [NEW]
        }

        # [FIX V4.4] Trích xuất config Close on Reverse
        self.temp_close_rev = self.var_close_rev.get()
        self.temp_rev_time = float(self.var_rev_time.get() or 180)
        self.temp_rev_confirm_seconds = float(self.var_rev_confirm_seconds.get() or 0)
        self.temp_rev_confirm_scans = int(self.var_rev_confirm_scans.get() or 0)

        # [NEW] Thêm Distance ATR R
        new_dca = {
            "ENABLED": self.dca_active.get(),
            "MAX_STEPS": int(self.dca_steps.get() or 3),
            "STEP_MULTIPLIER": float(self.dca_mult.get() or 1.5),
            "DISTANCE_ATR_R": float(self.dca_atr.get() or 1.0),
            "USE_PARENT_SL": self.dca_use_parent_sl.get(),
            "COOLDOWN": int(self.dca_pca_cooldown.get() or 60),
            "MINI_BRAIN": getattr(self, "dca_mb_cfg", {})
        }
        new_pca = {
            "ENABLED": self.pca_active.get(),
            "MAX_STEPS": int(self.pca_steps.get() or 2),
            "STEP_MULTIPLIER": float(self.pca_mult.get() or 0.5),
            "DISTANCE_ATR_R": float(self.pca_atr.get() or 1.5),
            "USE_PARENT_SL": self.pca_use_parent_sl.get(),
            "CONFIRM_ADX": getattr(config, "PCA_CONFIG", {}).get("CONFIRM_ADX", 23),
            "MINI_BRAIN": getattr(self, "pca_mb_cfg", {})
        }
        new_entry_exit = self._collect_entry_exit_config()
        new_bot_safeguard = copy.deepcopy(self.brain_data.get("bot_safeguard", {}) or {})
        exit_tactic = str(new_entry_exit.get("exit_tactic", "SWING_REJECTION") or "").upper()
        new_bot_safeguard.update(
            {
                "BOT_USE_SWING_TP": exit_tactic in ("SWING_REJECTION", "SWING_STRUCTURE"),
                "BOT_USE_RR_TP": exit_tactic == "FALLBACK_R",
                "BOT_TP_RR_RATIO": float(
                    new_entry_exit.get("default_exit", {}).get("tp_rr_ratio", 1.5) or 1.5
                ),
            }
        )

        return {
            "MASTER_EVAL_MODE": self.master_eval_var.get(),
            "MIN_MATCHING_VOTES": int(self.min_votes_var.get() or 3),
            "FORCE_ANY_MODE": self.var_force_any.get(),  # [NEW]
            "G0_TIMEFRAME": self.tf_vars["G0"].get(),
            "G1_TIMEFRAME": self.tf_vars["G1"].get(),
            "G2_TIMEFRAME": self.tf_vars["G2"].get(),
            "G3_TIMEFRAME": self.tf_vars["G3"].get(),
            "voting_rules": new_voting,
            "risk_tsl": new_risk_tsl,
            "indicators": new_inds,
            "check_indicators": new_check_inds,
            "entry_exit": new_entry_exit,
            "bot_safeguard": new_bot_safeguard,
            "dca_config": new_dca,
            "pca_config": new_pca,
        }

    def _open_mb_popup(self, mode):
        from ui_popups import open_minibrain_popup
        if mode == "DCA":
            def save_cb(new_cfg):
                self.dca_mb_cfg = new_cfg
            open_minibrain_popup(self, "Cài đặt Mini-Brain (DCA)", getattr(self, "dca_mb_cfg", {}), save_cb)
        else:
            def save_cb(new_cfg):
                self.pca_mb_cfg = new_cfg
            open_minibrain_popup(self, "Cài đặt Mini-Brain (PCA)", getattr(self, "pca_mb_cfg", {}), save_cb)

    def save_strategy(self):
        try:
            output_data = self._pack_data()
            if self.override_symbol:
                from core.storage_manager import load_symbol_overrides, save_symbol_overrides
                overrides = load_symbol_overrides()
                if self.override_symbol not in overrides:
                    overrides[self.override_symbol] = {}
                previous_sandbox = overrides[self.override_symbol].get("sandbox", {}) or {}
                saved_safeguard = copy.deepcopy(
                    previous_sandbox.get("bot_safeguard", {}) or {}
                )
                saved_safeguard.update(output_data.get("bot_safeguard", {}) or {})
                saved_safeguard.update({
                    "CLOSE_ON_REVERSE": self.temp_close_rev,
                    "CLOSE_ON_REVERSE_MIN_TIME": self.temp_rev_time,
                    "REV_CONFIRM_SECONDS": self.temp_rev_confirm_seconds,
                    "REV_CONFIRM_SCANS": self.temp_rev_confirm_scans,
                    "CLOSE_ON_REVERSE_USE_PNL": self.var_close_rev_pnl.get(),
                    "REV_CLOSE_ON_NONE": self.var_rev_none.get(),
                    "REV_CLOSE_MIN_PROFIT": money_input_from_display(self.var_rev_profit.get(), self.cbo_rev_profit_unit.get()),
                    "REV_CLOSE_MIN_PROFIT_UNIT": unit_from_display(self.cbo_rev_profit_unit.get()),
                    "REV_CLOSE_MAX_LOSS": money_input_from_display(self.var_rev_loss.get(), self.cbo_rev_loss_unit.get()),
                    "REV_CLOSE_MAX_LOSS_UNIT": unit_from_display(self.cbo_rev_loss_unit.get()),
                })
                output_data["bot_safeguard"] = saved_safeguard
                overrides[self.override_symbol]["sandbox"] = output_data
                save_symbol_overrides(overrides)
                self.destroy()
                return

            brain_path = _get_brain_path()
            os.makedirs(os.path.dirname(brain_path), exist_ok=True)

            existing_data = {}
            if os.path.exists(brain_path):
                try:
                    with open(brain_path, "r", encoding="utf-8") as f:
                        existing_data = json.load(f)
                except Exception:
                    pass

            # [FIX]: Cập nhật gia tăng thay vì ghi đè thô bạo
            existing_data.update(output_data)

            # [FIX V4.4] Cập nhật Close on Reverse vào bot_safeguard khi Sandbox bấm lưu
            if "bot_safeguard" not in existing_data:
                existing_data["bot_safeguard"] = {}
            existing_data["bot_safeguard"]["CLOSE_ON_REVERSE"] = self.temp_close_rev
            existing_data["bot_safeguard"]["CLOSE_ON_REVERSE_MIN_TIME"] = (
                self.temp_rev_time
            )
            existing_data["bot_safeguard"]["REV_CONFIRM_SECONDS"] = self.temp_rev_confirm_seconds
            existing_data["bot_safeguard"]["REV_CONFIRM_SCANS"] = self.temp_rev_confirm_scans
            existing_data["bot_safeguard"]["CLOSE_ON_REVERSE_USE_PNL"] = self.var_close_rev_pnl.get()
            existing_data["bot_safeguard"]["REV_CLOSE_ON_NONE"] = self.var_rev_none.get()
            existing_data["bot_safeguard"]["REV_CLOSE_MIN_PROFIT"] = money_input_from_display(self.var_rev_profit.get(), self.cbo_rev_profit_unit.get())
            existing_data["bot_safeguard"]["REV_CLOSE_MIN_PROFIT_UNIT"] = unit_from_display(self.cbo_rev_profit_unit.get())
            existing_data["bot_safeguard"]["REV_CLOSE_MAX_LOSS"] = money_input_from_display(self.var_rev_loss.get(), self.cbo_rev_loss_unit.get())
            existing_data["bot_safeguard"]["REV_CLOSE_MAX_LOSS_UNIT"] = unit_from_display(self.cbo_rev_loss_unit.get())
            from core.storage_manager import save_brain_settings
            save_brain_settings(existing_data)

            # [HOT-FIX]: ồng bộ ngay vào config Runtime của UI Main
            if hasattr(self.master, "reload_config_from_json"):
                self.master.reload_config_from_json()
            else:
                # Fallback nếu gi từ nơi khác
                config.MASTER_EVAL_MODE = output_data["MASTER_EVAL_MODE"]
                config.MIN_MATCHING_VOTES = output_data["MIN_MATCHING_VOTES"]
                config.BOT_RISK_PERCENT = output_data["risk_tsl"]["base_risk"]
                config.TSL_LOGIC_MODE = output_data["risk_tsl"]["tsl_mode"]
                config.FORCE_ANY_MODE = output_data["FORCE_ANY_MODE"]
                config.STRICT_RISK_CALC = output_data["risk_tsl"]["strict_risk"]
                config.DCA_CONFIG = output_data["dca_config"]
                config.PCA_CONFIG = output_data["pca_config"]

            # Tự động đóng cửa sổ mượt mà
            self.destroy()
        except Exception as e:
            messagebox.showerror("Lỗi hệ thống", f"Lỗi ghi file cấu hình:\n{e}", parent=self)

    def load_template(self):
        file_path = filedialog.askopenfilename(
            initialdir=_get_template_dir(),
            title="Chọn Template",
            filetypes=[("JSON files", "*.json")],
            parent=self
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self.brain_data = json.load(f)

                # [FIX]: Reset tracking dictionaries trước khi build lại UI
                self.ind_widgets = {}
                self.check_widgets = {}
                self.vote_widgets = {}
                self.risk_widgets = {}
                self.tf_vars = {}
                self.group_label_widgets = []
                self.preview_status_cache = {}
                self.preview_last_symbol = None

                for widget in self.winfo_children():
                    widget.destroy()
                self._build_ui()
                messagebox.showinfo(
                    "Thành công",
                    "Đã nạp Template thành công. Hãy bấm LƯU & ÁP DỤNG để kích hoạt!",
                    parent=self
                )
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể đọc Template:\n{e}", parent=self)

    def save_as_template(self):
        try:
            output_data = self._pack_data()
            file_path = filedialog.asksaveasfilename(
                initialdir=_get_template_dir(),
                title="Lưu Template",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json")],
                parent=self
            )
            if file_path:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=4)
                messagebox.showinfo("Thành công", f"Đã lưu Template tại:\n{file_path}", parent=self)
        except Exception as e:
            messagebox.showerror("Lỗi", f"Lỗi lưu Template:\n{e}", parent=self)
