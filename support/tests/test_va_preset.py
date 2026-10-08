"""Portable allocation settings must not replace per-machine connection/runtime."""
import json

import pytest

from support.tools import apply_va_preset as tool
from viking_v2 import config
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.durable import AccountLease
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.portfolio import PortfolioContextBuilder
from viking_v2.trading.state import TradeStateStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ACCOUNTS_ROOT", tmp_path / "accounts")
    settings = config.AppSettings()
    settings.telegram_enabled = True
    settings.telegram_chat_id = "local-chat-only"
    settings.telegram_token_env = "LOCAL_TOKEN_KEY"
    settings.telegram_notifications["indicator_exit"] = False
    settings.paper_mode = False
    settings.rule_parameters.update(buy_ema_fast=5, buy_ema_slow=10, initial_sl_pct=-4)
    config.save_settings(settings, "PARTNER")
    root = config.account_root("PARTNER")
    for name in ("runtime_config.json", "pending_orders.json", "market_bars.json", "state.sqlite"):
        (root / name).write_bytes(b"existing runtime must stay identical")
    env = tmp_path / ".env"
    env.write_bytes(b"DNSE_API_KEY=not-a-real-key\nTELE_BOT_KEY=local-token\n")
    monkeypatch.setattr(config, "ENV_PATH", env)
    return root, settings.to_dict(), env


def test_preset_changes_only_agreed_allocation_fields_and_backup_is_exact(workspace):
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
    for key, value in old.items():
        if key not in tool.PRESET_FIELDS:
            assert current[key] == value
    for key, value in old["rule_parameters"].items():
        if key != "max_positions":
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


@pytest.mark.parametrize("mode", ["PAPER", "REAL"])
@pytest.mark.parametrize("symbol,envelope", [("MSN", 7_500_000), ("CTS", 7_500_000),
                                              ("HDB", 2_500_000), ("IDC", 7_500_000)])
def test_preset_budgets_use_existing_real_and_paper_calculator(workspace, mode, symbol, envelope):
    root, _old, _env = workspace
    settings = tool.prepared_settings("PARTNER")
    builder = PortfolioContextBuilder(OrderQueue(root / "test_orders.json"),
                                      TradeStateStore(root / "test_trades.json"),
                                      RuleStateStore(root / "test_rule.json"), lambda: .00045)
    result = builder.build(symbol, execution_mode=mode,
                           balance={"equity": 50_000_000, "availableCash": 50_000_000},
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


def test_portable_preset_contains_no_secrets_or_runtime_fields():
    raw = json.loads(tool.PRESET_PATH.read_text(encoding="utf-8"))
    assert set(raw) == tool.PRESET_FIELDS
    assert not any(word in tool.PRESET_PATH.read_text(encoding="utf-8").lower()
                   for word in ("token", "api_key", "secret", "chat_id", "cooldown"))
