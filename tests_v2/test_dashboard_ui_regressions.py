from __future__ import annotations

import time

from viking_v2.dashboard.panels import DashboardPanelsMixin
from viking_v2.dashboard.view import COL_TEXT, COL_TITLE
from viking_v2.dashboard.tables import (
    DashboardTablesMixin,
    RUNNING_COLUMNS,
    RUNNING_HEADERS,
    RUNNING_WIDTHS,
)
from viking_v2.dashboard.windows import (
    FONT_KEY,
    FONT_MONO_VALUE,
    FONT_TABLE_HEADING,
    FONT_TABLE_VALUE,
    FONT_VALUE,
    HINT_FONT,
    PALETTE,
    fit_entry_text,
)


class _Value:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


def test_operator_typography_separates_keys_from_values() -> None:
    assert FONT_KEY == ("Segoe UI", 14, "bold", "italic")
    assert FONT_VALUE == ("Segoe UI", 14)
    assert FONT_TABLE_HEADING == ("Segoe UI", 16, "bold", "italic")
    assert FONT_TABLE_VALUE == ("Segoe UI", 14)
    assert HINT_FONT == ("Segoe UI", 20)
    title_rgb = tuple(int(COL_TITLE[index:index + 2], 16) for index in (1, 3, 5))
    text_rgb = tuple(int(COL_TEXT[index:index + 2], 16) for index in (1, 3, 5))
    brightness_ratio = sum(title_rgb) / sum(text_rgb)
    assert PALETTE["TITLE"] == COL_TITLE
    assert 0.65 <= brightness_ratio <= 0.75


def test_dynamic_entry_values_shrink_only_when_they_need_more_room(ui_root) -> None:
    import customtkinter as ctk

    entry = ctk.CTkEntry(ui_root, width=115, font=FONT_MONO_VALUE)
    entry.grid(row=0, column=0)
    ui_root.update_idletasks()
    try:
        assert fit_entry_text(entry, "100 CP") == 14
        long_size = fit_entry_text(entry, "999,999,999,999,999 CP")
        assert 11 <= long_size < 14
    finally:
        entry.destroy()


class _Label:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **options: object) -> None:
        self.options.update(options)


class _Entry(_Label):
    def __init__(self, value: str = "") -> None:
        super().__init__()
        self.value = value

    def get(self) -> str:
        return self.value


def test_auto_quantity_placeholder_is_compact_and_unambiguous() -> None:
    subject = DashboardPanelsMixin()
    subject.symbol = _Value("AAA")
    subject.quantity = _Entry()

    quantity, _budget, _forced = subject._suggested_order_quantity(
        7.29,
        {
            "decisions": {
                "AAA": {
                    "details": {
                        "entry_checks": {
                            "order_budget": 1_400_000,
                            "available_cash": 100_000_000,
                            "nav": 100_000_000,
                        }
                    }
                }
            }
        },
    )

    assert quantity == 100
    assert subject.quantity.options["placeholder_text"] == "100 CP"


class _Real:
    @staticmethod
    def api_health() -> dict[str, object]:
        return {"total_requests": 1, "last_status": 200, "last_error": ""}

    @staticmethod
    def configured() -> bool:
        return True

    @staticmethod
    def has_trading_token() -> bool:
        return True


def test_health_panel_renders_runtime_state_instead_of_staying_on_dashes() -> None:
    subject = DashboardPanelsMixin()
    subject.real = _Real()
    subject.mode = _Value("REAL")
    subject.symbol = _Value("AAA")
    for name in ("title", "daemon", "core", "ws", "rest", "token", "trade"):
        setattr(subject, f"preview_health_{name}", _Label())

    now = time.time()
    subject._refresh_api_health_panel({
        "heartbeat_at": now,
        "daemon_status": "RUNNING",
        "market_status": "OPEN",
        "api_health": {
            "websocket": {"connected": True, "authenticated": True},
            "rest": {"total_requests": 1, "last_status": 200, "last_error": ""},
        },
        "ticks": {"AAA": {"price": 7.15, "timestamp": now}},
    })

    assert subject.preview_health_daemon.options["text"] == "DAEMON RUNNING"
    assert subject.preview_health_core.options["text"] == "DNSE READY"
    assert subject.preview_health_ws.options["text"] == "WS ONLINE"
    assert subject.preview_health_rest.options["text"] == "API OK"
    assert subject.preview_health_token.options["text"] == "OTP OK"
    assert subject.preview_health_trade.options["text"] == "PRICE OPEN"


class _Tree:
    def __init__(self) -> None:
        self.columns: tuple[str, ...] = ()
        self.headings: dict[str, dict[str, object]] = {}
        self.column_options: dict[str, dict[str, object]] = {}

    def __getitem__(self, key: str) -> tuple[str, ...]:
        assert key == "columns"
        return self.columns

    def configure(self, **options: object) -> None:
        self.columns = tuple(options["columns"])

    def heading(self, column: str, **options: object) -> None:
        self.headings[column] = options

    def column(self, column: str, **options: object) -> None:
        self.column_options[column] = options


def test_running_table_keeps_the_completed_split_column_design() -> None:
    tree = _Tree()

    DashboardTablesMixin._configure_tree(tree, RUNNING_COLUMNS)

    assert tree.columns == RUNNING_COLUMNS
    assert [tree.headings[key]["text"] for key in RUNNING_COLUMNS] == [
        "Ticket",
        "Thời gian",
        "Thông tin Lệnh",
        "Chốt lời/Lỗ (SL|TP)",
        "Chi phí/Phí qua đêm",
        "Rủi ro/Kỳ vọng (%)",
        "PnL / MAE / MFE",
        "Trạng thái",
        "✖",
    ]
    assert all(tree.column_options[key]["width"] == RUNNING_WIDTHS[key] for key in RUNNING_COLUMNS)
    assert RUNNING_HEADERS["Order"] != "Lệnh / KL / SL / TP / FEE"


def test_settings_popups_open_and_have_no_overlapping_grid_controls(ui_root) -> None:
    import customtkinter as ctk

    from viking_v2.backtest.window import BacktestPopup
    from viking_v2.config import load_settings
    from viking_v2.connections.dnse.client import DNSEClient
    from viking_v2.connections.window import ConnectionPopup
    from viking_v2.rules.window import RuleSettingsPopup

    root = ui_root
    client = DNSEClient(account_no="PAPER")
    popups = ()
    try:
        settings = load_settings("PAPER")
        popups = (
            ("RULE", RuleSettingsPopup(root, settings, "PAPER", lambda: None)),
            ("CONNECTION", ConnectionPopup(root, settings, "PAPER", client, lambda: None)),
            ("BACKTEST", BacktestPopup(root, settings, None)),
        )
        root.update_idletasks()
        clashes: list[str] = []

        def scan(label: str, widget: object) -> None:
            children = list(widget.winfo_children())
            if isinstance(widget, (ctk.CTkFrame, ctk.CTkScrollableFrame, ctk.CTkTabview)):
                occupied: dict[tuple[int, int], object] = {}
                for child in children:
                    info = child.grid_info()
                    if not info:
                        continue
                    row = int(info.get("row", 0))
                    column = int(info.get("column", 0))
                    rowspan = int(info.get("rowspan", 1))
                    columnspan = int(info.get("columnspan", 1))
                    for rr in range(row, row + rowspan):
                        for cc in range(column, column + columnspan):
                            if (rr, cc) in occupied:
                                clashes.append(f"{label} {type(widget).__name__} ô {(rr, cc)}")
                            occupied[(rr, cc)] = child
            for child in children:
                scan(label, child)

        for label, popup in popups:
            scan(label, popup.top)

        assert not clashes, "control bị đè lên nhau: " + ", ".join(clashes)
        rule_popup = popups[0][1]
        assert int(rule_popup.phase2_right_column.grid_info()["column"]) == 1
        assert int(rule_popup.phase2_mode_card.grid_info()["row"]) == 0
        assert int(rule_popup.phase2_confirmation_card.grid_info()["row"]) == 1
    finally:
        for _label, popup in popups:
            closer = getattr(popup, "close", None) or getattr(popup, "_close", None)
            if callable(closer) and popup.top.winfo_exists():
                closer()
        client.close()


def test_phase2_preview_uses_readable_stacked_rows(ui_root) -> None:
    import customtkinter as ctk

    root = ui_root
    parent = None
    try:
        parent = ctk.CTkFrame(root)
        parent.grid(row=0, column=0, sticky="nsew")
        subject = DashboardPanelsMixin()
        subject._build_order_preview_tab(parent)

        assert int(subject.preview_rule_ema.grid_info()["row"]) == 1
        assert int(subject.preview_rule_sell_ema.grid_info()["row"]) == 2
        assert int(subject.preview_rule_rsi.grid_info()["row"]) == 3
        assert subject.preview_rule_ema.master is subject.preview_rule_sell_ema.master
        assert subject.preview_rule_ema.master is subject.preview_rule_rsi.master
        assert int(subject.preview_rule_phase3_detail.grid_info()["row"]) == 1

        root.update_idletasks()
        management_cards = (
            subject.preview_tp_value.master,
            subject.preview_sl_value.master,
            subject.preview_normal_value.master,
            subject.preview_exit_value.master,
        )
        assert {
            (int(card.grid_info()["row"]), int(card.grid_info()["column"]))
            for card in management_cards
        } == {(0, 0), (0, 1), (1, 0), (1, 1)}
        for card in management_cards:
            content_bottom = max(
                child.winfo_y() + child.winfo_height()
                for child in card.winfo_children()
            )
            assert content_bottom <= card.winfo_height()
    finally:
        if parent is not None and parent.winfo_exists():
            parent.destroy()


def test_backtest_ui_covers_every_static_rule_parameter() -> None:
    from viking_v2.backtest.window import BACKTEST_RULE_KEYS
    from viking_v2.rules.business import StaticRuleParameters

    assert BACKTEST_RULE_KEYS == frozenset(StaticRuleParameters.__dataclass_fields__)
