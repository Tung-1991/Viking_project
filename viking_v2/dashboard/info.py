from __future__ import annotations

from typing import Any, Callable, Iterable

import customtkinter as ctk

from ..backtest.data import HistoricalDataStore
from ..backtest.models import BacktestResult, BacktestScenario, BacktestSettings
from ..rules.business import StaticRuleParameters
from .windows import FONT_KEY, FONT_VALUE, PALETTE, _window


class InfoPopup:
    """Compact read-only summary for live and backtest configurations."""

    FONT = "Segoe UI"
    MONO = "Cascadia Mono"

    def __init__(
        self,
        parent: ctk.CTk,
        real_settings: Callable[[], Any],
        backtest_store: HistoricalDataStore,
        *,
        on_visibility_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self.parent = parent
        self.real_settings = real_settings
        self.backtest_store = backtest_store
        self.on_visibility_changed = on_visibility_changed

        parent.update_idletasks()
        sw, sh = int(parent.winfo_screenwidth()), int(parent.winfo_screenheight())
        width, height = min(1240, sw - 60), min(780, sh - 80)
        x, y = max(10, (sw - width) // 2), max(10, (sh - height) // 3)
        self.top = _window(parent, "VIKING · INFO", f"{width}x{height}+{x}+{y}")
        try:
            self.top.grab_release()
        except Exception:
            pass
        self.top.tk.call("wm", "transient", self.top._w, "")
        self.top.resizable(True, True)
        self.top.minsize(900, 600)
        self.top.configure(fg_color=PALETTE["BG"])
        self.top.grid_columnconfigure(0, weight=1)
        self.top.grid_rowconfigure(1, weight=1)
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.hide(), add="+")

        head = ctk.CTkFrame(self.top, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 2))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            head, text="INFO", font=(self.FONT, 20, "bold"),
            text_color=PALETTE["TITLE"], anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text="Cùng bố cục · cấu hình độc lập",
            font=(self.FONT, 12, "bold"), text_color=PALETTE["MUTED"], anchor="w",
        ).grid(row=1, column=0, sticky="w")
        ctk.CTkButton(
            head, text="↻  LÀM MỚI", width=118, height=34,
            font=(self.FONT, 12, "bold"), fg_color=PALETTE["BLUE"],
            hover_color=PALETTE["BLUE_HOVER"], command=self.refresh,
        ).grid(row=0, column=1, rowspan=2)

        self.tabs = ctk.CTkTabview(
            self.top, fg_color=PALETTE["PANEL"], border_width=1,
            border_color=PALETTE["BORDER"],
            segmented_button_selected_color=PALETTE["BLUE"],
            segmented_button_selected_hover_color=PALETTE["BLUE_HOVER"],
            segmented_button_unselected_color=PALETTE["SLATE"],
            segmented_button_unselected_hover_color=PALETTE["SLATE_HOVER"],
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=10, pady=(2, 10))
        try:
            self.tabs._segmented_button.configure(font=(self.FONT, 12, "bold"))
        except AttributeError:
            pass

        self.bodies: dict[str, ctk.CTkScrollableFrame] = {}
        for name in ("REAL", "BACKTEST"):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            body = ctk.CTkScrollableFrame(
                tab, fg_color="transparent",
                scrollbar_button_color=PALETTE["BORDER"],
                scrollbar_button_hover_color=PALETTE["SLATE_HOVER"],
            )
            body.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
            body.grid_columnconfigure(0, weight=1)
            self.bodies[name] = body

        self.refresh()
        self.show()

    @staticmethod
    def _money(value: float) -> str:
        number = float(value or 0.0)
        if abs(number) >= 1_000_000_000:
            return f"{number / 1_000_000_000:,.2f} tỷ"
        if abs(number) >= 1_000_000:
            return f"{number / 1_000_000:,.1f} tr"
        if abs(number) >= 1_000:
            return f"{number / 1_000:,.1f}K"
        return f"{number:,.0f} đ"

    @staticmethod
    def _on_off(value: bool) -> str:
        return "ON" if value else "OFF"

    @staticmethod
    def _em_name(values: Iterable[str]) -> str:
        names = {"TP": "TP", "NORMAL": "PROTECT", "IND_EXIT": "E"}
        result = [names[item] for item in values if item in names]
        return " · ".join(result) if result else "SL"

    def _clear(self, name: str) -> ctk.CTkScrollableFrame:
        body = self.bodies[name]
        for child in body.winfo_children():
            child.destroy()
        for column in (0, 1):
            body.grid_columnconfigure(column, weight=1, uniform="info-layout")
        return body

    def _section(
        self,
        body: Any,
        row: int,
        title: str,
        items: list[tuple[str, str, str]],
        *,
        columns: int = 3,
        grid_column: int = 0,
        grid_span: int = 2,
    ) -> int:
        """Render one compact comparison card with aligned fields."""
        section = ctk.CTkFrame(
            body, fg_color=PALETTE["SURFACE"], corner_radius=9,
            border_width=1, border_color=PALETTE["BORDER"],
        )
        section.grid(
            row=row, column=grid_column, columnspan=grid_span,
            sticky="new", padx=8, pady=4,
        )
        for column in range(columns):
            section.grid_columnconfigure(column, weight=1, uniform=f"info-{row}")

        ctk.CTkLabel(
            section, text=title, font=(self.FONT, 13, "bold"),
            text_color="#60A5FA", anchor="w",
        ).grid(
            row=0, column=0, columnspan=columns, sticky="ew",
            padx=12, pady=(8, 5),
        )

        for index, (label, value, color) in enumerate(items):
            item_row, item_column = divmod(index, columns)
            cell = ctk.CTkFrame(
                section, fg_color=PALETTE["SURFACE_2"], corner_radius=6,
            )
            cell.grid(
                row=item_row + 1, column=item_column, sticky="ew",
                padx=(12 if item_column == 0 else 4, 12 if item_column == columns - 1 else 4),
                pady=3,
            )
            cell.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                cell, text=label, font=FONT_KEY,
                text_color=PALETTE["TITLE"], anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=(9, 10), pady=5)
            ctk.CTkLabel(
                cell, text=value, font=FONT_VALUE,
                text_color=color, anchor="w", justify="left",
            ).grid(row=0, column=1, sticky="w", padx=(0, 9), pady=5)

        last_row = ((len(items) - 1) // columns) + 2 if items else 1
        section.grid_rowconfigure(last_row, minsize=7)
        return row + 1

    def _shared_rule_sections(
        self,
        body: Any,
        row: int,
        p: StaticRuleParameters,
        *,
        signal_mode: str,
        em_modes: Iterable[str],
        whipsaw_enabled: bool,
        loss_lock: str,
        compact_layout: bool = False,
    ) -> int:
        exposure = p.exposure
        phase1_items = [
            ("CẤU TRÚC", f"MA{p.ma_period} · PIVOT {p.pivot_left}/{p.pivot_right}", PALETTE["TEXT"]),
            ("XÁC NHẬN", f"{p.confirm_sessions} PHIÊN", PALETTE["TEXT"]),
            ("VOLUME", self._on_off(p.volume_confirmation), PALETTE["TEXT"]),
            ("UP", f"{exposure.get('UPTREND', .9) * 100:g}%", PALETTE["GREEN"]),
            ("ACC", f"{exposure.get('ACCUMULATION', .6) * 100:g}%", PALETTE["TEXT"]),
            ("DIS / DOWN", f"{exposure.get('DISTRIBUTION', .5) * 100:g}% / {exposure.get('DOWNTREND', .1) * 100:g}%", PALETTE["WARN"]),
        ]
        buy_conditions = " + ".join(
            label for enabled, label in (
                (p.buy_signal_use_ema, f"EMA {p.buy_ema_fast}/{p.buy_ema_slow}"),
                (p.buy_signal_use_rsi, f"RSI{p.rsi_period} ↑"),
            ) if enabled
        ) or "OFF"
        sell_conditions = " + ".join(
            label for enabled, label in (
                (p.sell_signal_use_ema, f"EMA {p.sell_ema_fast}/{p.sell_ema_slow}"),
                (p.sell_signal_use_rsi, f"RSI{p.rsi_period} ↓"),
            ) if enabled
        ) or "OFF"
        phase2_items = [
            ("BUY", buy_conditions, PALETTE["GREEN"]),
            ("E", sell_conditions, PALETTE["RED"]),
            ("TÍN HIỆU", signal_mode, PALETTE["TEXT"]),
            (
                "XÁC NHẬN BUY",
                (
                    f"{p.buy_confirmation_minutes} PHÚT · "
                    f"{'EMA ' if p.buy_confirmation_require_ema else ''}"
                    f"{'RSI' if p.buy_confirmation_require_rsi else ''}"
                    if p.buy_confirmation_enabled else "OFF"
                ),
                PALETTE["TEXT"],
            ),
            (
                "KHUNG MUA",
                f"TỪ {p.buy_window_start}" if p.buy_window_enabled else "OFF",
                PALETTE["TEXT"],
            ),
        ]
        phase3_items = [
            ("VỊ THẾ", str(p.max_positions), PALETTE["TEXT"]),
            ("SL / RE", f"{p.initial_sl_pct:g}% / {p.reentry_sl_pct:g}%", PALETTE["RED"]),
            ("COMPOUND", "OFF" if p.no_compound_enabled else "ON", PALETTE["TEXT"]),
            ("WHIPSAW", f"{self._on_off(whipsaw_enabled)} · {p.whipsaw_n}/{p.whipsaw_x}", PALETTE["WARN"]),
            ("LOSS LOCK", loss_lock, PALETTE["WARN"]),
            ("MIN LOT", "100 CP" if p.force_min_lot_enabled else "OFF", PALETTE["TEXT"]),
            ("TP", f"{p.take_profit_pct:g}%", PALETTE["GREEN"]),
            (
                "PROTECT",
                f"{p.normal_policy} · ARM {p.normal_arm_pct:g}% · TRAIL {p.normal_giveback_pct:g}%"
                f" · SELL {p.normal_sell_pct:g}% · DYN {self._on_off(p.normal_dynamic_enabled)}"
                f" · START×{p.normal_atr_activation_multiplier:g}"
                f" · ATR×{p.normal_atr_multiplier:g}"
                f" · GIỮ {p.normal_retention_pct:g}%→{p.normal_retention_until_pct:g}%"
                f" · REPEAT {self._on_off(p.normal_repeat_enabled and p.normal_sell_pct < 100)}",
                PALETTE["TEXT"],
            ),
            ("E/M BẬT", self._em_name(em_modes), PALETTE["TEXT"]),
        ]
        if compact_layout:
            self._section(
                body, row, "PHASE 1 · VNINDEX 1D", phase1_items,
                columns=2, grid_column=0, grid_span=1,
            )
            self._section(
                body, row, "PHASE 2 · BUY / E", phase2_items,
                columns=2, grid_column=1, grid_span=1,
            )
            self._section(
                body, row + 1, "PHASE 3 · VỐN / THOÁT", phase3_items,
                columns=2, grid_column=0, grid_span=1,
            )
            return row + 1
        row = self._section(body, row, "PHASE 1 · VNINDEX 1D", phase1_items)
        row = self._section(body, row, "PHASE 2 · BUY / E", phase2_items)
        return self._section(body, row, "PHASE 3 · VỐN / THOÁT", phase3_items)

    def _render_real(self) -> None:
        body = self._clear("REAL")
        settings = self.real_settings()
        p = StaticRuleParameters.from_dict(settings.rule_parameters)
        row = self._section(body, 0, "PHẠM VI", [
            ("CHẾ ĐỘ", "BOT THẬT", PALETTE["GREEN"]),
            ("MÃ", f"{len(settings.watchlist)} MÃ", PALETTE["TEXT"]),
            ("PAPER", self._money(settings.paper_initial_balance), PALETTE["TEXT"]),
        ])
        row = self._shared_rule_sections(
            body, row, p,
            signal_mode=(
                f"REALTIME · {settings.realtime_indicator_interval}"
                if settings.signal_mode == "REALTIME" else "CLOSED"
            ),
            em_modes=settings.bot_em_modes,
            whipsaw_enabled=p.whipsaw_enabled,
            loss_lock=f"{p.loss_lock_count} LOSS · {p.loss_lock_hours} GIỜ",
            compact_layout=True,
        )
        self._section(body, row, "THỰC THI", [
            ("LỆNH BOT", "LO LOCAL" if settings.bot_order_mode == "LO_LOCAL" else "MARKET", PALETTE["TEXT"]),
            ("ATO / ATC", f"{self._on_off(settings.allow_ato)} / {self._on_off(settings.allow_atc)}", PALETTE["TEXT"]),
            ("SELL T+", "KIỂM TRA LẠI" if settings.sell_wait_policy == "RECHECK" else "VẪN BÁN", PALETTE["TEXT"]),
            ("PHÍ MUA", f"{settings.buy_fee_pct:g}%", PALETTE["WARN"]),
            ("PHÍ BÁN", f"{settings.sell_fee_pct:g}%", PALETTE["WARN"]),
            ("THUẾ", f"{settings.sell_tax_pct:g}%", PALETTE["WARN"]),
        ], columns=2, grid_column=1, grid_span=1)

    @staticmethod
    def _scenario_profiles(rows: list[BacktestScenario]) -> list[str]:
        profiles: list[str] = []
        for item in rows:
            modes = [short for value, short in (("TP", "TP"), ("NORMAL", "PROTECT"), ("IND_EXIT", "E")) if value in item.em_modes]
            name = "+".join(modes) if modes else "SL"
            value = f"{name} · {item.max_positions} SLOT · W {'ON' if item.whipsaw_enabled else 'OFF'}"
            if value not in profiles:
                profiles.append(value)
        return profiles

    def _mode2_section(self, body: Any, row: int) -> int:
        scenarios: list[BacktestScenario] = []
        for raw in self.backtest_store.load_scenarios():
            try:
                scenarios.append(BacktestScenario.from_dict(raw))
            except (TypeError, ValueError):
                continue
        if not scenarios:
            return self._section(body, row, "MODE 2 · KỊCH BẢN", [
                ("TRẠNG THÁI", "CHƯA CÓ KỊCH BẢN", PALETTE["WARN"]),
            ])

        periods: list[str] = []
        symbols: list[str] = []
        for item in scenarios:
            for symbol in item.symbols:
                if symbol not in symbols:
                    symbols.append(symbol)
            value = f"{item.start_date} → {item.end_date} · {item.market_phase}"
            if value not in periods:
                periods.append(value)
        profiles = self._scenario_profiles(scenarios)
        items: list[tuple[str, str, str]] = [
            ("TỔNG", f"{len(scenarios)} KỊCH BẢN", PALETTE["GREEN"]),
            ("MÃ", ", ".join(symbols), PALETTE["TEXT"]),
            ("CẤU HÌNH", f"{len(periods)} GIAI ĐOẠN · {len(profiles)} BỘ TEST", PALETTE["TEXT"]),
        ]
        for index, period in enumerate(periods, start=1):
            items.append((f"GĐ {index}", period, PALETTE["TEXT"]))
        for index in range(0, len(profiles), 2):
            items.append(("BỘ TEST", "  ·  ".join(profiles[index:index + 2]), PALETTE["TEXT"]))
        return self._section(body, row, "MODE 2 · KỊCH BẢN", items, columns=1)

    def _render_backtest(self) -> None:
        body = self._clear("BACKTEST")
        settings = BacktestSettings.from_dict(self.backtest_store.load_settings())
        p = StaticRuleParameters.from_dict(settings.rule_parameters)
        phase = "TỰ NHẬN DIỆN" if settings.auto_market_phase else f"{settings.fixed_market_phase} · {settings.fixed_exposure_pct:g}%"
        row = self._section(body, 0, "PHẠM VI · MODE 1", [
            ("MÃ", f"{len(settings.symbols)} MÃ", PALETTE["TEXT"]),
            ("THỜI GIAN", f"{settings.start_date or '--'} → {settings.end_date or '--'}", PALETTE["TEXT"]),
            ("VỐN", self._money(settings.initial_capital), PALETTE["TEXT"]),
            ("PHASE", phase, PALETTE["TEXT"]),
            ("KHỚP", settings.fill_session, PALETTE["TEXT"]),
        ])
        row = self._shared_rule_sections(
            body, row, p,
            signal_mode="1D · CLOSED",
            em_modes=settings.em_modes,
            whipsaw_enabled=settings.whipsaw_enabled,
            loss_lock=f"{self._on_off(settings.loss_lock_enabled)} · {settings.loss_lock_hours} GIỜ",
            compact_layout=True,
        )
        self._section(body, row, "THỰC THI", [
            ("SELL T+", "KIỂM TRA LẠI" if settings.sell_wait_policy == "RECHECK" else "VẪN BÁN", PALETTE["TEXT"]),
            ("PHÍ MUA", f"{settings.buy_fee_pct:g}%", PALETTE["WARN"]),
            ("PHÍ BÁN", f"{settings.sell_fee_pct:g}%", PALETTE["WARN"]),
            ("THUẾ", f"{settings.sell_tax_pct:g}%", PALETTE["WARN"]),
        ], columns=2, grid_column=1, grid_span=1)
        row += 1
        row = self._mode2_section(body, row)

        runs: list[BacktestResult] = []
        raw_runs = self.backtest_store.list_runs()
        for raw in raw_runs[:20]:
            try:
                runs.append(BacktestResult.from_dict(raw))
            except (TypeError, ValueError):
                continue
        if runs:
            latest = runs[0]
            best = max(runs, key=lambda item: item.return_pct)
            self._section(body, row, "KẾT QUẢ", [
                ("ĐÃ LƯU", str(len(raw_runs)), PALETTE["TEXT"]),
                ("GẦN NHẤT", f"{latest.return_pct:+.2f}% · {latest.closed_trades} LỆNH", PALETTE["GREEN"] if latest.return_pct >= 0 else PALETTE["RED"]),
                ("WIN RATE", f"{latest.win_rate_pct:.1f}%", PALETTE["TEXT"]),
                ("TỐT NHẤT", f"{best.return_pct:+.2f}%", PALETTE["GREEN"]),
            ])
        else:
            self._section(body, row, "KẾT QUẢ", [
                ("TRẠNG THÁI", "CHƯA CÓ LẦN CHẠY", PALETTE["WARN"]),
            ])

    def refresh(self) -> None:
        self._render_real()
        self._render_backtest()

    def show(self) -> None:
        self.refresh()
        self.top.deiconify()
        self.top.lift()
        self.top.focus_force()
        if self.on_visibility_changed:
            self.on_visibility_changed(True)

    def hide(self) -> None:
        if self.top.winfo_exists():
            self.top.withdraw()
        if self.on_visibility_changed:
            self.on_visibility_changed(False)

    def close(self) -> None:
        if self.on_visibility_changed:
            self.on_visibility_changed(False)
        if self.top.winfo_exists():
            self.top.destroy()
