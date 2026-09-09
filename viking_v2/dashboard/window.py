from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import queue
import subprocess
from typing import Any

import customtkinter as ctk

from .. import config
from ..config import AppSettings, load_settings
from ..connections.dnse.client import DNSEClient
from ..connections.dnse.paper import PaperBroker
from ..connections.telegram import SignalTelegramService
from ..connections.window import ConnectionPopup
from ..backtest.window import BacktestPopup
from ..rules.planner import StrategyOrderPlanner
from ..rules.state import RuleStateStore
from ..rules.window import RuleSettingsPopup
from ..services.runtime import RuntimeBridge, setup_logging
from ..storage import DailyFeeTracker, JSONLineJournal, SignalLog
from ..trading.execution import ExecutionService
from ..trading.orders import OrderQueue
from ..trading.state import TradeStateStore
from .info import InfoPopup
from .windows import DataTablePopup, HistoryPopup, install_fast_scroll

from .actions import DashboardActionsMixin
from .panels import DashboardPanelsMixin
from .tables import DashboardTablesMixin


class VikingApp(DashboardPanelsMixin, DashboardActionsMixin, DashboardTablesMixin, ctk.CTk):
    def __init__(self, *, account_id: str | None = None):
        super().__init__()
        self.account_id = str(account_id or config.active_account_id())
        self.running = True
        self.settings: AppSettings = load_settings(self.account_id)
        self.bridge = RuntimeBridge(self.account_id)
        self.bridge.disarm(self.settings.watchlist, self.settings.paper_mode)
        self.logger = setup_logging(self.bridge.log_dir, "ui")
        self.real = DNSEClient(account_no=None if self.account_id == "PAPER" else self.account_id)
        self.real.connect()
        self.paper = PaperBroker(
            self.bridge.paper_state_path,
            initial_balance=self.settings.paper_initial_balance,
            tick_provider=self._shared_tick,
            fee_rates=lambda: (
                self.settings.buy_fee_pct / 100.0,
                self.settings.sell_fee_pct / 100.0,
                self.settings.sell_tax_pct / 100.0,
            ),
            working_dates_provider=lambda: [
                value for value in self.real.get_working_dates()
                if str(value)[:10] not in set(self.settings.trading_holidays)
            ],
        )
        self.queue = OrderQueue(self.bridge.pending_orders_path)
        self.trade_state = TradeStateStore(self.bridge.trade_state_path)
        self.rule_state = RuleStateStore(self.bridge.rule_state_path)
        self.signal_log = SignalLog(self.bridge.signal_log_path)
        self.execution = ExecutionService(
            self.real,
            self.paper,
            self.queue,
            JSONLineJournal(self.bridge.journal_path),
            quote_provider=self._shared_tick,
            trade_state=self.trade_state,
            rule_state=self.rule_state,
            sell_decision_provider=self._latest_sell_decision,
            trade_event_callback=self._notify_bot_trade_event,
        )
        self.daily_fees = DailyFeeTracker(
            self.bridge.history_csv_path,
            self.bridge.daily_stats_path,
        )
        self.strategy_planner = StrategyOrderPlanner(self.queue, self.trade_state, self.rule_state)
        # All broker I/O in the UI process is serialized here.  Tk callbacks
        # only render completed results and never wait on DNSE.
        self._io_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viking-io")
        self._order_worker_busy = False
        self._snapshot_busy = False
        self._fee_rates: dict[tuple[str, str], float | None] = {}
        self._fee_rate_pending: set[tuple[str, str]] = set()
        self._fee_rate_retry_after: dict[tuple[str, str], float] = {}
        self._ui_callbacks: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._history_busy = False
        self.daemon_process: subprocess.Popen[Any] | None = None
        self._daemon_restart_after = 0.0
        self._daemon_started_at = 0.0
        self._daemon_crash_times: list[float] = []
        self._daemon_dead_pid = 0
        self._daemon_restart_blocked = False
        self.telegram: SignalTelegramService | None = None
        self.snapshots: dict[str, tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]] = {
            "REAL": ({}, [], []),
            "PAPER": self.execution.account_snapshot("PAPER"),
        }
        self.histories: dict[str, list[dict[str, Any]]] = {"REAL": [], "PAPER": []}
        self._bot_enabled = False
        self._bot_toggle_busy = False
        self._bot_sync_until = 0.0
        self._last_running_render = 0.0
        self._data_popups: dict[str, DataTablePopup] = {}
        self._history_popup: HistoryPopup | None = None
        self._info_popup: InfoPopup | None = None
        self._rule_settings_popup: RuleSettingsPopup | None = None
        self._advanced_popup: ConnectionPopup | None = None
        self._backtest_popup: BacktestPopup | None = None
        self._telegram_signature: tuple[Any, ...] | None = None
        self._running_row_actions: dict[str, dict[str, dict[str, Any]]] = {"REAL": {}, "PAPER": {}}
        self._build_window()
        self._build_layout()
        install_fast_scroll(self)
        self.bind("<Button-1>", self._minimize_popups_from_main_click, add="+")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(150, self._start_services)
        self.after(50, self._drain_ui_callbacks)
        self.after(250, self._poll_runtime)
        self.after(600, self._process_orders)
        self.after(800, self._refresh_snapshots)

    def _build_window(self) -> None:
        self.title("Viking V2 CKCS — Static Rule")
        screen_w = max(1024, int(self.winfo_screenwidth() or 1024))
        screen_h = max(720, int(self.winfo_screenheight() or 720))
        width = min(1650, max(1100, screen_w - 60))
        height = min(950, max(720, screen_h - 90))
        self.geometry(f"{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//3)}")
        self.minsize(1080, 700)

    def _build_layout(self) -> None:
        # Giữ bố cục hai cột cũ. Panel trái chỉ rộng hơn một chút để font lớn
        # không ép hoặc cắt nhãn; phần bảng vẫn nhận toàn bộ không gian còn lại.
        self.configure(fg_color="#111318")
        self.grid_columnconfigure(0, weight=0, minsize=420)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.left = ctk.CTkScrollableFrame(
            self, width=405, corner_radius=0, fg_color="#15171B",
            scrollbar_fg_color="#15171B",
            scrollbar_button_color="#262B32",
            scrollbar_button_hover_color="#3A3F47",
        )
        self.left.grid(row=0, column=0, sticky="nsew")
        self.left.grid_columnconfigure(0, weight=1)
        self.right = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.right.grid(row=0, column=1, sticky="nsew", padx=12, pady=10)
        self.right.grid_columnconfigure(0, weight=1)
        # Keep the system panel at a predictable height.  Giving this row a
        # weight used to stretch it on tall screens, leaving a large empty
        # strip below PREVIEW while taking space away from the orders table.
        self.right.grid_rowconfigure(1, weight=1)
        self.right.grid_rowconfigure(2, weight=0, minsize=320)
        self._left_panel()
        self._right_panel()
        self.after_idle(self._sync_left_scrollbar)
        self.after_idle(self._sync_info_selector_mode)
        self.bind("<Configure>", self._on_dashboard_resize, add="+")

    def _on_dashboard_resize(self, _event=None) -> None:
        self.after_idle(self._sync_left_scrollbar)
        self.after_idle(self._sync_info_selector_mode)
