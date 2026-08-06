# -*- coding: utf-8 -*-

from types import SimpleNamespace

import config
import main
from core import storage_manager
from core.dnse_connector import ModeBoundConnector


def test_bot_runtime_state_is_separate_for_paper_and_real(monkeypatch, tmp_path):
    legacy = tmp_path / "bot_state.json"
    monkeypatch.setattr(storage_manager, "STATE_FILE", str(legacy))

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    paper_state = storage_manager.load_state()
    paper_state["pnl_today"] = -123000.0
    paper_state["active_trades"] = ["PAPER-1"]
    storage_manager.save_state(paper_state)

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    real_state = storage_manager.load_state()
    assert real_state["pnl_today"] == 0.0
    assert real_state["active_trades"] == []
    real_state["pnl_today"] = 456000.0
    real_state["active_trades"] = ["REAL-1"]
    storage_manager.save_state(real_state)

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    restored_paper = storage_manager.load_state()
    assert restored_paper["pnl_today"] == -123000.0
    assert restored_paper["active_trades"] == ["PAPER-1"]

    assert (tmp_path / "bot_state.paper.json").exists()
    assert (tmp_path / "bot_state.real.json").exists()


def test_old_state_is_migrated_only_to_matching_mode(monkeypatch, tmp_path):
    import json

    legacy = tmp_path / "bot_state.json"
    legacy.write_text(
        json.dumps({"pnl_today": -62000000.0, "active_trades": ["PAPER-4"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(storage_manager, "STATE_FILE", str(legacy))

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    assert storage_manager.load_state()["pnl_today"] == 0.0

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    migrated = storage_manager.load_state()
    assert migrated["pnl_today"] == 0.0
    assert migrated["state_mode"] == "PAPER"


def test_state_can_be_loaded_for_inactive_mode_without_changing_global_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(storage_manager, "STATE_FILE", str(tmp_path / "bot_state.json"))
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    paper = storage_manager.load_state(paper=True)
    paper["pnl_today"] = 1.0
    storage_manager.save_state(paper)
    real = storage_manager.load_state(paper=False)
    real["pnl_today"] = 2.0
    storage_manager.save_state(real)

    assert config.PAPER_TRADING is True
    assert storage_manager.load_state(paper=True)["pnl_today"] == 1.0
    assert storage_manager.load_state(paper=False)["pnl_today"] == 2.0


def test_mode_bound_connector_routes_positions_and_updates_to_bound_mode():
    calls = []

    class Base:
        def get_positions(self, paper_mode=None):
            calls.append(("positions", paper_mode))
            return []

        def get_all_open_positions(self, paper_mode=None):
            calls.append(("all", paper_mode))
            return []

        def get_account_info(self, paper_mode=None):
            calls.append(("account", paper_mode))
            return {}

        def get_orders(self, paper_mode=None, **_params):
            calls.append(("orders", paper_mode))
            return []

        def modify_position(self, pos, sl, tp, paper_mode=None):
            calls.append(("modify", paper_mode))
            return SimpleNamespace(ok=True)

        def close_position(self, pos, comment="", paper_mode=None):
            calls.append(("close", paper_mode))
            return SimpleNamespace(ok=True)

    bound = ModeBoundConnector(Base(), paper_mode=False)
    bound.get_positions()
    bound.get_all_open_positions()
    bound.get_account_info()
    bound.get_orders()
    bound.modify_position("1", 10, 20)
    bound.close_position("1")

    assert all(call[1] is False for call in calls)


def test_ui_position_snapshot_combines_paper_and_real_without_switching_mode(
    monkeypatch,
):
    paper = SimpleNamespace(ticket="PAPER-1", position_id="PAPER-1")
    real = SimpleNamespace(ticket="9988", position_id="9988")
    calls = []

    class Connector:
        def get_all_open_positions(self, paper_mode=None):
            calls.append(paper_mode)
            return [paper] if paper_mode else [real]

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    app = SimpleNamespace(
        connector=Connector(),
        _ui_all_positions_snapshot=[],
    )

    positions = main.BotUI._get_all_mode_positions(app, refresh_real=True)

    assert [position.ticket for position in positions] == ["PAPER-1", "9988"]
    assert calls == [True, False]
    assert config.PAPER_TRADING is False


def test_account_totals_only_use_positions_from_current_execution_mode():
    paper = SimpleNamespace(
        ticket="PAPER-2",
        position_id="PAPER-2",
        profit=-325122.0,
        swap=0.0,
    )
    real = SimpleNamespace(
        ticket="9988",
        position_id="9988",
        profit=123000.0,
        swap=0.0,
    )

    assert main.BotUI._positions_for_execution_mode([paper, real], False) == [real]
    assert main.BotUI._positions_for_execution_mode([paper, real], True) == [paper]

    real_positions = main.BotUI._positions_for_execution_mode([paper], False)
    pnl, realized, floating = main.BotUI._combined_display_pnl(
        0.0, real_positions
    )
    assert (pnl, realized, floating) == (0.0, 0.0, 0.0)


def test_inactive_position_modify_and_close_are_routed_by_ticket():
    calls = []
    connector = SimpleNamespace(
        modify_position=lambda pos, sl, tp, paper_mode=None: (
            calls.append(("modify", pos.ticket, paper_mode))
            or SimpleNamespace(ok=True)
        ),
        close_position=lambda pos, comment="", paper_mode=None: (
            calls.append(("close", pos.ticket, paper_mode))
            or SimpleNamespace(ok=True)
        ),
    )
    app = SimpleNamespace(connector=connector)
    paper = SimpleNamespace(ticket="PAPER-3")
    real = SimpleNamespace(ticket="8123")

    main.BotUI._modify_position_for_mode(app, paper, 10, 20)
    main.BotUI._close_position_for_mode(app, real, "Manual_Close")

    assert calls == [
        ("modify", "PAPER-3", True),
        ("close", "8123", False),
    ]


def test_ui_mode_switch_disarms_bot_and_reloads_matching_state(monkeypatch):
    from types import SimpleNamespace
    from core import env_utils

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    saved_env = []
    monkeypatch.setattr(env_utils, "update_env", lambda values: saved_env.append(values))
    next_state = {"state_mode": "REAL", "pnl_today": 7.0}
    monkeypatch.setattr(main, "load_state", lambda: next_state)

    calls = []
    connector = SimpleNamespace(
        get_all_open_positions=lambda: [],
        reset_session_caches=lambda: calls.append("reset"),
        connect=lambda: calls.append("connect"),
    )
    app = SimpleNamespace(
        _mode_switching=False,
        connector=connector,
        trade_mgr=SimpleNamespace(state={"state_mode": "PAPER"}),
        tsl_states_map={"PAPER-1": "Running"},
        _ui_all_positions_snapshot=["paper"],
        set_auto_trade_enabled=lambda enabled, reason="": calls.append((enabled, reason)),
        log_message=lambda *_args, **_kwargs: None,
        _save_brain_live_config=lambda: calls.append("save"),
        update_portfolio_table=lambda: calls.append("portfolio"),
    )

    main.BotUI.on_paper_mode_change(app, "REAL")

    assert config.PAPER_TRADING is False
    assert (False, "Đổi PAPER/REAL") in calls
    assert "reset" not in calls
    assert "connect" in calls and "portfolio" in calls
    assert app.trade_mgr.state is next_state
    assert app.tsl_states_map == {"PAPER-1": "Running"}
    assert app._ui_all_positions_snapshot == ["paper"]
    assert app._mode_switching is False
    assert saved_env[-1] == {"PAPER_TRADING": "False"}


def test_ui_mode_switch_allows_open_position_and_disarms_new_entries(monkeypatch):
    from types import SimpleNamespace
    from core import env_utils

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(env_utils, "update_env", lambda _values: None)
    monkeypatch.setattr(main, "load_state", lambda paper=None: {"state_mode": "REAL"})
    monkeypatch.setattr(main, "save_state", lambda _state: None)
    calls = []
    app = SimpleNamespace(
        connector=SimpleNamespace(
            get_all_open_positions=lambda: [SimpleNamespace(ticket="PAPER-1")],
            reset_session_caches=lambda: calls.append("reset"),
            connect=lambda: calls.append("connect"),
        ),
        _mode_switching=False,
        trade_mgr=SimpleNamespace(state={"state_mode": "PAPER"}),
        tsl_states_map={},
        _ui_all_positions_snapshot=[],
        set_auto_trade_enabled=lambda enabled, reason="": calls.append(
            ("bot", enabled, reason)
        ),
        log_message=lambda message, **kwargs: calls.append(("log", message)),
        _save_brain_live_config=lambda: None,
        update_portfolio_table=lambda: None,
    )

    main.BotUI.on_paper_mode_change(app, "REAL")

    assert config.PAPER_TRADING is False
    assert ("bot", False, "Đổi PAPER/REAL") in calls
