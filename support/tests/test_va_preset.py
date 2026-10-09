"""Portable allocation settings must not replace per-machine connection/runtime."""
import json

import pytest

from support.tools import apply_va_preset as tool
from viking_v2 import config
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.durable import AccountLease
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder, size_buy_order
from viking_v2.trading.state import TradeStateStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    settings = config.AppSettings()
    settings.telegram_enabled = True
    settings.telegram_chat_id = "local-chat-only"
    settings.telegram_token_env = "LOCAL_TOKEN_KEY"
    settings.telegram_notifications["indicator_exit"] = False
    settings.telegram_notifications["blocked_buy"] = False
    settings.telegram_notifications["protect"] = False
    settings.paper_mode = False
    settings.rule_parameters.update(buy_ema_fast=5, buy_ema_slow=10, initial_sl_pct=-4,
                                    buy_signal_require_ema_cross=False)
    settings.signal_trace_enabled = False
    settings.signal_trace_interval_minutes = 5
    config.save_settings(settings, "PARTNER")
    root = config.account_root("PARTNER")
    for name in ("runtime_config.json", "pending_orders.json", "market_bars.json", "state.sqlite"):
        (root / name).write_bytes(b"existing runtime must stay identical")
    env = tmp_path / ".env"
    env.write_bytes(b"DNSE_API_KEY=not-a-real-key\nTELE_BOT_KEY=local-token\n")
    monkeypatch.setattr(config, "ENV_PATH", env)
    return root, settings.to_dict(), env


def test_preset_changes_only_agreed_allocation_e_and_signal_fields_and_backup_is_exact(workspace):
    root, old, env = workspace
    target = root / "settings.json"
    original = target.read_bytes()
    untouched = {path: path.read_bytes() for path in root.iterdir() if path != target}
    untouched[env] = env.read_bytes()
    saved, backup = tool.apply_preset("PARTNER")
    current = config.load_settings("PARTNER").to_dict()
    assert saved == target and backup.read_bytes() == original
    assert current["watchlist"] == current["priority_symbols"] == ["MSN", "CTS", "HDB", "IDC"]
    assert current["priority_total_capital"] == 50_000_000 and current["priority_capital_enabled"]
    assert current["market_phase_override_enabled"] and current["market_phase_override_exposure_pct"] == 100
    assert current["rule_parameters"]["max_positions"] == 4
    assert current["rule_parameters"]["indicator_exit_policy"] == "AUTO"
    assert current["rule_parameters"]["buy_signal_require_ema_cross"] is True
    assert current["signal_trace_enabled"] is True and current["signal_trace_interval_minutes"] == 2
    assert current["signal_trace_start"] == "14:00" and current["signal_trace_end"] == "14:30"
    assert "IND_EXIT" in current["bot_em_modes"]
    assert current["telegram_notifications"]["blocked_buy"] is True
    assert current["telegram_notifications"] == config.TELEGRAM_NOTIFICATION_DEFAULTS
    assert all(current["telegram_notifications"].values())
    assert current["priority_allocations"] == {
        symbol: {"limit_vnd": cap, "use_pct": 100, "max_orders": 1}
        for symbol, cap in (("MSN", 16_000_000), ("CTS", 12_000_000),
                            ("HDB", 15_000_000), ("IDC", 7_000_000))
    }
    for key, value in old.items():
        if key not in tool.PRESET_FIELDS:
            assert current[key] == value
    for key, value in old["rule_parameters"].items():
        if key not in tool.PRESET_RULE_FIELDS:
            assert current["rule_parameters"][key] == value
    assert all(path.read_bytes() == content for path, content in untouched.items())
    # Applying twice is deliberate/idempotent and does not undo old backups.
    tool.apply_preset("PARTNER")
    assert config.load_settings("PARTNER").to_dict() == current
    assert backup.read_bytes() == original


@pytest.mark.parametrize("name", ["execution.lock", "market-worker.lock"])
def test_running_app_or_daemon_refuses_preset_without_writing_settings(workspace, name):
    root, _old, _env = workspace
    lease = AccountLease(root, name)
    try:
        original = (root / "settings.json").read_bytes()
        with pytest.raises(RuntimeError):
            tool.apply_preset("PARTNER")
        assert (root / "settings.json").read_bytes() == original
        assert list(root.glob("*.bak")) == []
    finally:
        lease.close()


def test_corrupt_settings_are_not_replaced_with_defaults(workspace):
    root, _old, _env = workspace
    (root / "settings.json").write_text("invalid-json", encoding="utf-8")
    with pytest.raises(ValueError):
        tool.apply_preset("PARTNER")
    assert (root / "settings.json").read_text(encoding="utf-8") == "invalid-json"
    assert list(root.glob("*.bak")) == []


def test_canceled_prompt_never_applies_settings(workspace, monkeypatch):
    root, _old, _env = workspace
    monkeypatch.setattr("sys.argv", ["apply_va_preset.py", "--account", "PARTNER"])
    monkeypatch.setattr("builtins.input", lambda *_args: "n")
    original = (root / "settings.json").read_bytes()
    assert tool.main() == 0
    assert (root / "settings.json").read_bytes() == original
    assert list(root.glob("*.bak")) == []


@pytest.mark.parametrize("telegram_enabled", [False, True])
@pytest.mark.parametrize("window_enabled", [False, True])
def test_signal_opt_in_changes_only_agreed_intervals_and_keeps_buy_guards(
    workspace, telegram_enabled, window_enabled,
):
    root, _old, env = workspace
    current = config.load_settings("PARTNER")
    current.telegram_enabled = telegram_enabled
    current.telegram_buy_batch_minutes = 7
    current.telegram_buy_delivery_mode = "BATCH"
    current.telegram_notifications["protect"] = True
    current.telegram_cooldown_minutes["blocked_buy"] = 12
    current.rule_parameters.update(buy_window_enabled=window_enabled, buy_window_start="13:45")
    config.save_settings(current, "PARTNER")
    env_before = env.read_bytes()
    tool.apply_preset("PARTNER")
    saved = config.load_settings("PARTNER")
    assert saved.telegram_enabled is telegram_enabled
    assert saved.telegram_buy_batch_minutes == 7
    assert saved.telegram_buy_delivery_mode == "IMMEDIATE"
    assert saved.telegram_notifications == config.TELEGRAM_NOTIFICATION_DEFAULTS
    assert saved.telegram_cooldown_minutes == {
        **current.telegram_cooldown_minutes, "blocked_buy": 60, "buy_lost": 60, "system": 30,
    }
    assert saved.rule_parameters["buy_window_enabled"] is window_enabled
    assert saved.rule_parameters["buy_window_start"] == "13:45"
    assert env.read_bytes() == env_before
    assert (root / "runtime_config.json").read_bytes() == b"existing runtime must stay identical"


def test_preset_details_are_reviewed_before_confirmation_without_writes(workspace, monkeypatch, capsys):
    root, _old, _env = workspace
    monkeypatch.setattr("sys.argv", ["apply_va_preset.py", "--account", "PARTNER"])
    original = (root / "settings.json").read_bytes()

    def review_then_cancel(prompt):
        review = capsys.readouterr().out
        assert "MSN 16 / CTS 12 / HDB 15 / IDC 7 trieu" in review
        assert "[IDC] 7 trieu = 50 - 16 - 12 - 15" in review
        assert "MAX LENH 1" in review and "P1 override 100%" in review
        assert "[E] AUTO" in review and "[GIU] API, token" in review
        assert "TIN HIEU ON" in review and "E ALERT ON" in review
        assert "PROTECT ON" in review and "khong thay doi cong tac bao ve" in review
        assert "[TIN HIEU] Gui ngay" in review and "khong doi gio mua" in review
        assert "BUY gui ngay (co the chon GOM TIN trong app)" in review
        assert "gian 60 phut/ma/so" in review and "[HE THONG] Gian canh bao 30 phut" in review
        assert "local-chat-only" not in review and "LOCAL_TOKEN_KEY" not in review
        assert "[y/N]" in prompt
        assert (root / "settings.json").read_bytes() == original
        return "n"

    monkeypatch.setattr("builtins.input", review_then_cancel)
    assert tool.main() == 0
    assert (root / "settings.json").read_bytes() == original
    assert list(root.glob("*.bak")) == []


@pytest.mark.parametrize("mode", ["PAPER", "REAL"])
@pytest.mark.parametrize("capital", [50_000_000, 100_000_000])
@pytest.mark.parametrize("symbol,envelope", [("MSN", 16_000_000), ("CTS", 12_000_000),
                                              ("HDB", 15_000_000), ("IDC", 7_000_000)])
def test_preset_budgets_use_existing_real_and_paper_calculator(workspace, mode, capital, symbol, envelope):
    root, _old, _env = workspace
    settings = tool.prepared_settings("PARTNER")
    builder = PortfolioContextBuilder(OrderQueue(root / "test_orders.json"),
                                      TradeStateStore(root / "test_trades.json"),
                                      RuleStateStore(root / "test_rule.json"), lambda: .00045)
    result = builder.build(symbol, execution_mode=mode,
                           balance={"equity": capital, "availableCash": capital},
                           positions=[], tick={"ask": 20, "ceiling_price": 21.4},
                           exposure=settings.market_phase_override_exposure_pct / 100,
                           max_positions=settings.rule_parameters["max_positions"],
                           priority_symbols=settings.priority_symbols,
                           priority_capital_enabled=settings.priority_capital_enabled,
                           priority_total_capital=settings.priority_total_capital,
                           priority_allocations=settings.priority_allocations,
                           budget_only=True)
    assert result["order_budget"] == pytest.approx(envelope / 1.00045)
    assert result["minimum_order_room"] <= envelope


@pytest.mark.parametrize("cash,expected", [(50_000_000, 7_000_000), (47_000_000, 4_000_000),
                                         (40_000_000, 0)])
def test_idc_only_uses_cash_left_after_reserving_first_three_symbols(workspace, cash, expected):
    root, _old, _env = workspace
    settings = tool.prepared_settings("PARTNER")
    builder = PortfolioContextBuilder(OrderQueue(root / "idc_orders.json"),
                                      TradeStateStore(root / "idc_trades.json"),
                                      RuleStateStore(root / "idc_rules.json"), lambda: .00045)
    result = builder.build("IDC", execution_mode="PAPER",
                           balance={"equity": 50_000_000, "availableCash": cash},
                           positions=[], tick={"ask": 20, "ceiling_price": 21.4},
                           exposure=1, max_positions=4,
                           priority_symbols=settings.priority_symbols,
                           priority_capital_enabled=True, priority_total_capital=50_000_000,
                           priority_allocations=settings.priority_allocations, budget_only=True)
    assert result["order_budget"] == pytest.approx(expected / 1.00045)


@pytest.mark.parametrize("mode,fee", [("PAPER", .00045), ("REAL", .0012)])
@pytest.mark.parametrize("ceiling,quantity", [(79.3, 200), (80.0, 100)])
def test_new_msn_cap_sizes_market_shares_by_ceiling_including_fee(workspace, mode, fee, ceiling, quantity):
    root, _old, _env = workspace
    settings = tool.prepared_settings("PARTNER")
    builder = PortfolioContextBuilder(OrderQueue(root / "msn_orders.json"),
                                      TradeStateStore(root / "msn_trades.json"),
                                      RuleStateStore(root / "msn_rules.json"), lambda: fee)
    context = builder.build("MSN", execution_mode=mode,
                            balance={"equity": 50_000_000, "availableCash": 50_000_000},
                            positions=[], tick={"ask": 74.2, "ceiling_price": ceiling},
                            exposure=1, max_positions=4,
                            priority_symbols=settings.priority_symbols,
                            priority_capital_enabled=True, priority_total_capital=50_000_000,
                            priority_allocations=settings.priority_allocations, budget_only=True)
    sizing = size_buy_order(budget_vnd=context["order_budget"],
                            price_board=context["buy_budget_price"],
                            available_cash=context["available_cash"], nav=context["nav"],
                            minimum_order_room_vnd=context["minimum_order_room"], buy_fee_rate=fee)
    assert sizing.quantity == quantity
    assert sizing.quantity * ceiling * 1000 * (1 + fee) <= 16_000_000
    assert context["priority_capital"]["reserved_cash"] == 34_000_000


def test_review_reads_allocation_from_preset_instead_of_stale_fixed_text(workspace, monkeypatch, tmp_path, capsys):
    root, _old, _env = workspace
    raw = json.loads(tool.PRESET_PATH.read_text(encoding="utf-8"))
    raw["priority_allocations"]["MSN"]["limit_vnd"] = 17_000_000
    raw["priority_allocations"]["CTS"]["limit_vnd"] = 11_000_000
    preset = tmp_path / "adjusted-preset.json"
    preset.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(tool, "PRESET_PATH", preset)
    monkeypatch.setattr("sys.argv", ["apply_va_preset.py", "--account", "PARTNER"])
    monkeypatch.setattr("builtins.input", lambda *_args: "n")
    original = (root / "settings.json").read_bytes()
    assert tool.main() == 0
    review = capsys.readouterr().out
    assert "MSN 17 / CTS 11 / HDB 15 / IDC 7 trieu" in review
    assert "[IDC] 7 trieu = 50 - 17 - 11 - 15" in review
    assert (root / "settings.json").read_bytes() == original
    assert not list(root.glob("*.bak"))


@pytest.mark.parametrize("old_modes", [[], ["TP"], ["NORMAL"], ["TP", "NORMAL", "IND_EXIT"]])
def test_preset_enables_e_without_disabling_other_exit_flags(workspace, old_modes):
    _root, _old, _env = workspace
    current = config.load_settings("PARTNER")
    current.bot_em_modes = old_modes.copy()
    current.bot_sl_enabled = False
    config.save_settings(current, "PARTNER")
    tool.apply_preset("PARTNER")
    saved = config.load_settings("PARTNER")
    assert saved.bot_em_modes == list(dict.fromkeys([*old_modes, "IND_EXIT"]))
    assert saved.bot_sl_enabled is False
    assert saved.rule_parameters["indicator_exit_policy"] == "AUTO"


@pytest.mark.parametrize("extra", [
    {"telegram_enabled": True},
    {"paper_mode": True},
    {"rule_parameters": {"max_positions": 4, "indicator_exit_policy": "AUTO", "initial_sl_pct": -9}},
    {"bot_em_modes": ["NORMAL", "IND_EXIT"]},
    {"rule_parameters": {"max_positions": 4, "indicator_exit_policy": "ALERT"}},
    {"telegram_notifications": {"blocked_buy": True, "protect": True, "closed": True}},
    {"telegram_notifications": {"blocked_buy": True, "protect": False}},
    {"telegram_notifications": {"blocked_buy": True, "protect": "true"}},
    {"telegram_notifications": {"blocked_buy": True, "protect": 1}},
    {"telegram_notifications": {"blocked_buy": False}},
    {"telegram_notifications": {"blocked_buy": "true"}},
    {"telegram_notifications": {"blocked_buy": 1}},
    {"telegram_notifications": None},
    {"telegram_cooldown_minutes": {"blocked_buy": 60, "system": 15}},
    {"telegram_cooldown_minutes": {"blocked_buy": 30, "system": 30}},
    {"telegram_cooldown_minutes": {"blocked_buy": 60, "system": 30, "protect": 0}},
    {"telegram_cooldown_minutes": {"blocked_buy": 60.0, "system": 30}},
    {"telegram_cooldown_minutes": None},
    {"telegram_buy_delivery_mode": "BATCH"},
    {"telegram_buy_delivery_mode": None},
])
def test_preset_rejects_unagreed_setting_fields_without_writes(workspace, monkeypatch, tmp_path, extra):
    root, _old, _env = workspace
    raw = json.loads(tool.PRESET_PATH.read_text(encoding="utf-8"))
    raw.update(extra)
    preset = tmp_path / "bad-preset.json"
    preset.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(tool, "PRESET_PATH", preset)
    original = (root / "settings.json").read_bytes()
    with pytest.raises(ValueError, match="Preset sai pham vi"):
        tool.apply_preset("PARTNER")
    assert (root / "settings.json").read_bytes() == original
    assert not list(root.glob("*.bak"))


def test_portable_preset_contains_no_secrets_or_runtime_fields():
    raw = json.loads(tool.PRESET_PATH.read_text(encoding="utf-8"))
    assert set(raw) == tool.PRESET_FIELDS
    assert not any(word in tool.PRESET_PATH.read_text(encoding="utf-8").lower()
                   for word in ("token", "api_key", "secret", "chat_id"))
