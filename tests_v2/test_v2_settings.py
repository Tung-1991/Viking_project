from __future__ import annotations

import json
import os

from viking_v2 import config
from viking_v2.config import AppSettings
from viking_v2.rules.business import StaticRuleParameters
from viking_v2.connections.window import ConnectionPopup
from viking_v2.dashboard.windows import parse_symbols
from viking_v2.config import update_env


def test_v2_settings_only_keep_two_bot_execution_modes():
    assert AppSettings(bot_order_mode="lo_local").normalize().bot_order_mode == "LO_LOCAL"
    assert AppSettings(bot_order_mode="resting-lo").normalize().bot_order_mode == "MARKET"


def test_execution_defaults_are_explicit_and_minimal():
    settings = AppSettings().normalize()
    assert settings.bot_em_modes == ["NORMAL", "HIGH", "IND_EXIT"]
    assert settings.sell_wait_policy == "RECHECK"
    assert settings.signal_mode == "REALTIME"
    assert AppSettings(sell_wait_policy="keep").normalize().sell_wait_policy == "KEEP"
    assert AppSettings(sell_wait_policy="unknown").normalize().sell_wait_policy == "RECHECK"
    assert StaticRuleParameters().whipsaw_n == 3
    assert StaticRuleParameters().whipsaw_x == 7


def test_default_watchlist_keeps_the_40_legacy_ckcs_symbols(monkeypatch):
    monkeypatch.delenv("DNSE_CKCS_WATCHLIST", raising=False)
    symbols = config._watchlist_from_env()
    assert len(symbols) == 40
    assert symbols == list(config.DEFAULT_CKCS_WATCHLIST)
    assert "FPT" not in symbols


def test_telegram_settings_only_keep_connection_values():
    settings = AppSettings(telegram_enabled=True, telegram_chat_id=" 123 ").normalize()
    assert settings.telegram_enabled is True
    assert settings.telegram_chat_id == "123"
    assert settings.telegram_token_env == "TELE_BOT_KEY"
    assert settings.telegram_buy_batch_minutes == 30
    assert not hasattr(settings, "telegram_system_alerts")


def test_official_2026_exchange_holidays_are_always_applied():
    settings = AppSettings(custom_holidays=["2026-08-13", "2026-09-02"]).normalize()

    assert "2026-01-02" in settings.trading_holidays
    assert "2026-02-16" in settings.trading_holidays
    assert "2026-09-02" in settings.trading_holidays
    assert "2026-08-13" in settings.trading_holidays
    assert settings.custom_holidays == ["2026-08-13"]


def test_rule_parameter_overrides_are_single_flat_source():
    params = StaticRuleParameters.from_dict({"ema_fast": 4, "whipsaw_n": 5, "unknown": 999})
    assert params.ema_fast == 4
    assert params.buy_ema_fast == 4
    assert params.sell_ema_fast == 4
    assert params.buy_ema_slow == 6
    assert params.sell_ema_slow == 6
    assert params.whipsaw_n == 5
    assert not hasattr(params, "unknown")


def test_rule_parameter_supports_independent_buy_and_sell_ema_pairs():
    params = StaticRuleParameters.from_dict(
        {
            "buy_ema_fast": 3,
            "buy_ema_slow": 6,
            "sell_ema_fast": 5,
            "sell_ema_slow": 10,
        }
    )

    assert params.buy_ema_fast == 3
    assert params.buy_ema_slow == 6
    assert params.sell_ema_fast == 5
    assert params.sell_ema_slow == 10
    saved = params.to_dict()
    assert "ema_fast" not in saved
    assert saved["buy_ema_fast"] == 3
    assert saved["sell_ema_slow"] == 10


def test_v2_account_workspace_is_self_contained_and_safe(tmp_path, monkeypatch):
    accounts = tmp_path / "viking_v2" / "runtime" / "accounts"
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", accounts)

    path = config.account_root("../ACC 01")

    assert path.parent == accounts
    assert path.name == "_ACC_01"
    assert ".." not in path.parts


def test_missing_account_settings_are_materialized_as_json(tmp_path, monkeypatch):
    accounts = tmp_path / "runtime" / "accounts"
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", accounts)

    settings = config.load_settings("ACC01")
    saved = accounts / "ACC01" / "settings.json"

    assert saved.is_file()
    assert json.loads(saved.read_text(encoding="utf-8"))["watchlist"] == settings.watchlist


def test_connection_popup_normalizes_watchlist_and_dnse_account_payload():
    symbols, invalid = parse_symbols("fpt, SSI  fpt;FUEVFVND bad!")
    rows, custody, owner = ConnectionPopup._account_payload(
        {"data": {"accounts": [{"id": "001", "dealAccount": True}], "custodyCode": "C01", "name": "K"}}
    )

    assert symbols == ["FPT", "SSI", "FUEVFVND"]
    assert invalid == ["BAD!"]
    assert rows == [{"id": "001", "dealAccount": True}]
    assert (custody, owner) == ("C01", "K")
    assert ConnectionPopup._dnse_error_message("Authorization field missing, malformed or invalid") == (
        "XÁC THỰC API KHÔNG HỢP LỆ · KIỂM TRA API KEY VÀ API SECRET"
    )


def test_connection_popup_parses_paper_balance_in_common_formats():
    assert ConnectionPopup._parse_paper_balance("100,000,000") == 100_000_000
    assert ConnectionPopup._parse_paper_balance("100.000.000") == 100_000_000
    assert ConnectionPopup._parse_paper_balance("100000000") == 100_000_000


def test_env_store_can_remove_only_selected_secret(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("DNSE_API_KEY=keep\nDNSE_TRADING_TOKEN=remove\nDNSE_TRADING_TOKEN_EXPIRES_AT=123\n", encoding="utf-8")
    monkeypatch.setenv("DNSE_TRADING_TOKEN", "remove")
    monkeypatch.setenv("DNSE_TRADING_TOKEN_EXPIRES_AT", "123")

    update_env(
        {
            "DNSE_TRADING_TOKEN": None,
            "DNSE_TRADING_TOKEN_EXPIRES_AT": None,
        },
        path,
    )

    assert path.read_text(encoding="utf-8") == "DNSE_API_KEY=keep\n"
    assert "DNSE_TRADING_TOKEN" not in os.environ
    assert "DNSE_TRADING_TOKEN_EXPIRES_AT" not in os.environ


def test_no_two_cards_ever_share_a_grid_cell():
    """A card gridded onto an occupied cell is drawn over and never seen.

    This has bitten twice already, so the layout is checked instead of eyeballed.
    """
    import customtkinter as ctk
    from viking_v2.config import load_settings
    from viking_v2.rules.window import RuleSettingsPopup
    from viking_v2.backtest.window import BacktestPopup

    root = ctk.CTk()
    root.withdraw()
    try:
        clashes = []

        def scan(label, widget):
            # CustomTkinter stacks a canvas and a raw label inside its own
            # widgets on purpose, so only real containers are checked.
            if not isinstance(widget, ctk.CTkFrame):
                return
            taken: dict[tuple[int, int], str] = {}
            for child in widget.winfo_children():
                info = child.grid_info()
                if not info or not isinstance(child, ctk.CTkBaseClass | ctk.CTkFrame):
                    continue
                row, column = int(info.get("row", 0)), int(info.get("column", 0))
                span = int(info.get("columnspan", 1))
                for offset in range(span):
                    cell = (row, column + offset)
                    if cell in taken:
                        clashes.append(f"{label} ô {cell}")
                    taken[cell] = str(child)

        rules = RuleSettingsPopup(root, load_settings("PAPER"), "PAPER", lambda: None)
        for name in ("NGHIỆP VỤ", "EXIT MANAGER", "THỰC THI"):
            rules.tabs.set(name)
            root.update_idletasks()
            for frame in rules.tabs.tab(name).winfo_children():
                scan(f"RULE/{name}", frame)

        back = BacktestPopup(root, load_settings("PAPER"), None)
        for name in ("MODE 1", "MODE 2", "THAM SỐ"):
            back.tabs.set(name)
            root.update_idletasks()
            for frame in back.tabs.tab(name).winfo_children():
                scan(f"BACKTEST/{name}", frame)

        assert not clashes, "thẻ chồng lên nhau: " + ", ".join(clashes)
    finally:
        root.destroy()


def test_no_tab_is_wider_than_the_window_it_lives_in():
    """A tab that needs more width than it has gets its right edge cut off.

    Both popups sized themselves past the window twice already, hiding a whole
    switch, so the widths are measured instead of eyeballed.
    """
    import customtkinter as ctk
    from viking_v2.config import load_settings
    from viking_v2.rules.window import RuleSettingsPopup
    from viking_v2.backtest.window import BacktestPopup

    root = ctk.CTk()
    root.withdraw()
    try:
        too_wide = []
        settings = load_settings("PAPER")
        pages = (
            ("RULE", RuleSettingsPopup(root, settings, "PAPER", lambda: None),
             ("NGHIỆP VỤ", "EXIT MANAGER", "THỰC THI")),
            ("BACKTEST", BacktestPopup(root, settings, None),
             ("MODE 1", "MODE 2", "THAM SỐ")),
        )
        for label, popup, names in pages:
            for name in names:
                popup.tabs.set(name)
                root.update_idletasks()
                popup.top.update()
                tab = popup.tabs.tab(name)
                # MODE 2 holds a table that scrolls sideways on purpose.
                if name == "MODE 2":
                    continue
                if tab.winfo_reqwidth() > tab.winfo_width():
                    too_wide.append(
                        f"{label}/{name} cần {tab.winfo_reqwidth()} có {tab.winfo_width()}"
                    )
        assert not too_wide, "tab tràn khỏi cửa sổ: " + ", ".join(too_wide)
    finally:
        root.destroy()


def test_signal_log_records_a_change_not_every_loop(tmp_path):
    """The daemon evaluates every symbol every second; only changes are worth keeping."""
    from viking_v2.storage import SignalLog

    log = SignalLog(tmp_path / "signal_log.csv")
    base = dict(price=21.5, ema_fast=21.4, ema_slow=21.2, rsi=58.3,
                market_state="UPTREND", acted="WAIT", blocked_by="MAX_POSITIONS")

    assert log.record({**base, "timestamp": "10:00", "symbol": "HSG", "signal": "BUY"})
    assert not log.record({**base, "timestamp": "10:01", "symbol": "HSG", "signal": "BUY"})
    assert not log.record({**base, "timestamp": "10:02", "symbol": "HSG", "signal": "BUY"})
    # Losing the signal is a change, but an empty signal is not worth a row.
    assert not log.record({**base, "timestamp": "14:00", "symbol": "HSG", "signal": ""})
    assert log.record({**base, "timestamp": "14:30", "symbol": "HSG", "signal": "BUY"})
    assert log.record({**base, "timestamp": "10:00", "symbol": "FPT", "signal": "SELL"})

    rows = log.read_all()
    assert [(r["symbol"], r["signal"], r["timestamp"]) for r in rows] == [
        ("HSG", "BUY", "10:00"), ("HSG", "BUY", "14:30"), ("FPT", "SELL", "10:00"),
    ]
    # The reason the bot stood still is the whole point of keeping this.
    assert rows[0]["blocked_by"] == "MAX_POSITIONS"
