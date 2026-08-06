# -*- coding: utf-8 -*-
from types import SimpleNamespace

from telegram_notify import drafts, proposals
from telegram_notify.control import (
    TelegramControlService,
    format_positions,
    format_status,
    parse_edit_command,
    parse_order_command,
)


class FakeClient:
    def __init__(self):
        self.messages = []
        self.keyboards = []
        self.answers = []

    def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))
        return {"ok": True}

    def send_message_with_keyboard(self, chat_id, text, keyboard=None):
        self.messages.append((chat_id, text))
        self.keyboards.append(keyboard)
        return {"ok": True}

    def answer_callback_query(self, callback_query_id, text=""):
        self.answers.append((callback_query_id, text))
        return {"ok": True}


class FakeUpdatesClient(FakeClient):
    def __init__(self, updates):
        super().__init__()
        self.updates = updates
        self.calls = []

    def get_updates(self, offset=None, timeout=15):
        self.calls.append((offset, timeout))
        return {"ok": True, "updates": list(self.updates)}


class FakeConnector:
    def __init__(self):
        self.positions = []
        self.closed = []
        self.close_ok = True

    def get_account_info(self):
        return {
            "login": 123,
            "balance": 1000,
            "equity": 1010,
            "margin": 100,
            "margin_free": 910,
        }

    def get_all_open_positions(self):
        return list(self.positions)

    def close_position(self, pos, comment=""):
        self.closed.append((pos.ticket, comment))
        return SimpleNamespace(ok=True) if self.close_ok else SimpleNamespace(ok=False)


def _pos(ticket=1, symbol="ETHUSD", type=0, volume=0.1, profit=5.0, comment="[BOT]_AUTO_ENTRY", magic=11):
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=type,
        volume=volume,
        price_open=2000,
        profit=profit,
        swap=0.0,
        commission=0.0,
        sl=1980,
        tp=2040,
        comment=comment,
        magic=magic,
    )


def _service(connector=None):
    connector = connector or FakeConnector()
    toggles = []
    executions = []
    service = TelegramControlService(
        connector=connector,
        get_state_cb=lambda: {"bot_pnl_today": 3.0, "bot_trades_today": 2, "manual_pnl_today": -1.0, "manual_trades_today": 1},
        get_bot_enabled_cb=lambda: bool(toggles[-1]) if toggles else False,
        set_bot_enabled_cb=lambda enabled, reason="": toggles.append(enabled),
        get_brain_status_cb=lambda: "ONLINE",
        get_active_symbols_cb=lambda: ["ETHUSD"],
        execute_order_cb=lambda symbol, side, lot, sl, tp: executions.append((symbol, side, lot, sl, tp)) or "SUCCESS|777",
    )
    return service, toggles, executions


def test_format_status_includes_account_and_positions():
    pos = _pos()
    text = format_status(
        {"login": 123, "balance": 1000, "equity": 1010, "margin": 100, "margin_free": 910},
        {"bot_pnl_today": 3, "bot_trades_today": 2, "manual_pnl_today": -1, "manual_trades_today": 1},
        [pos],
        False,
        brain_status="ONLINE",
        active_symbols=["ETHUSD"],
        magics={"bot_magic": 11},
    )
    assert "Login: 123" in text
    assert "Bot: OFF" in text
    assert "#1 BOT ETHUSD BUY" in text


def test_format_positions_empty():
    assert format_positions([]) == "Open positions: 0"


def test_control_allows_any_user_in_control_chat():
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": "/status"}},
    )
    assert "RAT-control status" in client.messages[-1][1]


def test_control_status_command_sends_dashboard():
    connector = FakeConnector()
    connector.positions = [_pos()]
    service, _toggles, _executions = _service(connector)
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": "/status"}},
    )
    assert "RAT-control status" in client.messages[-1][1]
    assert client.keyboards[-1]


def test_control_accepts_commands_with_bot_suffix():
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": "/help@RATBot"}},
    )
    assert "/set VN30F1M BUY" in client.messages[-1][1]
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/status@RATBot"}},
    )
    assert "RAT-control status" in client.messages[-1][1]


def test_control_primes_offset_and_skips_old_backlog():
    service, _toggles, _executions = _service()
    client = FakeUpdatesClient(
        [
            {"update_id": 40, "channel_post": {"chat": {"id": "-1003941549878"}, "text": "/status"}},
            {"update_id": 41, "channel_post": {"chat": {"id": "-1003941549878"}, "text": "/positions"}},
        ]
    )
    result = service._prime_offset(client)
    assert result == {"ok": True, "skipped": 2}
    assert service.offset == 42
    assert service.offset_primed is True
    assert client.messages == []
    assert client.calls == [(-1, 0)]


def test_control_channel_post_status_sends_dashboard():
    connector = FakeConnector()
    connector.positions = [_pos()]
    service, _toggles, _executions = _service(connector)
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/status"}},
    )
    assert "RAT-control status" in client.messages[-1][1]
    assert client.keyboards == []


def test_control_channel_post_text_toggle_allowed():
    service, toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/bot_off"}},
    )
    assert toggles == [False]
    assert "Bot is now OFF" in client.messages[-1][1]


def test_control_channel_post_order_creates_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/order ETHUSD BUY lot=0.03 sl=1980 tp=2050"}},
    )
    assert executions == []
    saved = list(proposals.load_proposals().values())[0]
    assert saved["created_by"] == 0
    assert saved["created_by_label"] == "CHANNEL"
    assert saved["status"] == "PENDING"
    assert "Owner approve required" in client.messages[-1][1]


def test_control_channel_post_edit_sets_channel_label(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": f"/edit {proposal['order_id']} side=SELL lot=0.02"}},
    )
    updated = proposals.get_proposal(proposal["order_id"])
    assert updated["side"] == "SELL"
    assert updated["updated_by"] == 0
    assert updated["updated_by_label"] == "CHANNEL"


def test_control_channel_post_blocks_text_approve_but_allows_cancel_close(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": f"/approve {proposal['order_id']}"}},
    )
    assert "Approve button" in client.messages[-1][1]
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": f"/cancel {proposal['order_id']}"}},
    )
    assert "cancelled" in client.messages[-1][1]
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/close 88"}},
    )
    assert "not open" in client.messages[-1][1]
    assert executions == []
    assert proposals.get_proposal(proposal["order_id"])["status"] == "CANCELLED"


def test_channel_owner_callback_approves_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 1},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": f"ord:approve:{proposal['order_id']}",
            }
        },
    )
    assert executions == [("ETHUSD", "BUY", 0.03, 1980.0, 2050.0)]
    assert proposals.get_proposal(proposal["order_id"])["status"] == "EXECUTED"


def test_channel_non_owner_callback_approve_is_denied(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb2",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": f"ord:approve:{proposal['order_id']}",
            }
        },
    )
    assert executions == []
    assert proposals.get_proposal(proposal["order_id"])["status"] == "PENDING"
    assert "approve" in client.messages[-1][1]


def test_channel_non_owner_callback_can_cancel_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb3",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": f"ord:cancel:{proposal['order_id']}",
            }
        },
    )
    assert proposals.get_proposal(proposal["order_id"])["status"] == "CANCELLED"


def test_channel_owner_callbacks_toggle_and_close(monkeypatch, tmp_path):
    monkeypatch.setattr("core.market_hours.is_symbol_trade_window_open", lambda _symbol: (True, ""))
    connector = FakeConnector()
    connector.positions = [_pos(ticket=88)]
    service, toggles, _executions = _service(connector)
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb5",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "ctl:bot_off",
            }
        },
    )
    assert toggles == [False]
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb6",
                "from": {"id": 1},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "ctl:bot_off",
            }
        },
    )
    assert toggles == [False, False]
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb7",
                "from": {"id": 1},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "ctl:close:88",
            }
        },
    )
    assert connector.closed == [(88, "telegram_control_close")]


def test_control_bot_toggle_command():
    service, toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 1}, "text": "/bot_on"}},
    )
    assert toggles == [True]
    assert "Bot is now ON" in client.messages[-1][1]


def test_operator_bot_toggle_is_allowed():
    service, toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": "/bot_on"}},
    )
    assert toggles == [True]
    assert "Bot is now ON" in client.messages[-1][1]


def test_control_close_ticket_refetches_position(monkeypatch):
    monkeypatch.setattr("core.market_hours.is_symbol_trade_window_open", lambda _symbol: (True, ""))
    connector = FakeConnector()
    connector.positions = [_pos(ticket=88)]
    service, _toggles, _executions = _service(connector)
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 1}, "text": "/close 88"}},
    )
    assert connector.closed == [(88, "telegram_control_close")]
    assert "Close sent" in client.messages[-1][1]


def test_control_close_missing_ticket():
    connector = FakeConnector()
    service, _toggles, _executions = _service(connector)
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 1}, "text": "/close 88"}},
    )
    assert "not open" in client.messages[-1][1]


def test_parse_order_command_accepts_five_fields():
    parsed = parse_order_command("/order ETHUSD BUY lot=0.03 sl=1980 tp=2050")
    assert parsed == {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980.0, "tp": 2050.0}


def test_parse_order_rejects_unknown_field():
    try:
        parse_order_command("/order ETHUSD BUY lot=0.03 sl=1980 tp=2050 note=x")
    except ValueError as exc:
        assert "Unsupported field" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_parse_edit_command_accepts_partial_five_fields():
    order_id, updates = parse_edit_command("/edit TG1 side=SELL lot=0.02")
    assert order_id == "TG1"
    assert updates == {"side": "SELL", "lot": 0.02}


def test_operator_order_creates_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": "/order ETHUSD BUY lot=0.03 sl=1980 tp=2050"}},
    )
    pending = proposals.pending_proposals()
    assert len(pending) == 1
    assert pending[0]["status"] == "PENDING"
    assert executions == []
    assert "Owner approve required" in client.messages[-1][1]


def test_owner_order_executes_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 1}, "text": "/order ETHUSD BUY lot=0.03 sl=1980 tp=2050"}},
    )
    assert executions == [("ETHUSD", "BUY", 0.03, 1980.0, 2050.0)]
    saved = list(proposals.load_proposals().values())[0]
    assert saved["status"] == "EXECUTED"
    assert saved["ticket"] == "777"


def test_operator_approve_is_denied(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": f"/approve {proposal['order_id']}"}},
    )
    assert executions == []
    assert "thiếu quyền" in client.messages[-1][1]


def test_owner_approve_executes_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 1}, "text": f"/approve {proposal['order_id']}"}},
    )
    assert executions == [("ETHUSD", "BUY", 0.03, 1980.0, 2050.0)]
    assert proposals.get_proposal(proposal["order_id"])["status"] == "EXECUTED"


def test_operator_edit_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": f"/edit {proposal['order_id']} side=SELL lot=0.02 sl=2070 tp=2000"}},
    )
    updated = proposals.get_proposal(proposal["order_id"])
    assert updated["side"] == "SELL"
    assert updated["lot"] == 0.02


def test_owner_cancel_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 1}, "text": f"/cancel {proposal['order_id']}"}},
    )
    assert proposals.get_proposal(proposal["order_id"])["status"] == "CANCELLED"


def test_pending_lists_buttons_for_each_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": "/pending"}},
    )
    assert "Pending proposal: 1" in client.messages[-1][1]
    assert proposal["order_id"] in client.messages[-1][1]
    assert client.keyboards[-1][0][0]["callback_data"] == f"ord:refresh:{proposal['order_id']}"
    assert client.keyboards[-1][0][1]["callback_data"] == f"wiz:edit:{proposal['order_id']}"


def test_pending_paginates_and_clear_all_cancels_only_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    ids = []
    for idx in range(6):
        proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.01 + idx, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
        ids.append(proposal["order_id"])
    executed = proposals.create_proposal(0, {"symbol": "BTCUSD", "side": "SELL", "lot": 0.1, "sl": 65000, "tp": 62000}, user_label="CHANNEL")
    proposals.mark_executed(executed["order_id"], "777")
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}

    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/pending"}},
    )
    assert "Pending proposal: 6 | page 1/2" in client.messages[-1][1]
    assert len([row for row in client.keyboards[-1] if row[0]["callback_data"].startswith("ord:refresh")]) == 5
    assert any(button["callback_data"] == "pend:page:1" for row in client.keyboards[-1] for button in row)

    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb-clear",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "pend:clear_all",
            }
        },
    )
    assert "Cleared pending: 6" in client.messages[-1][1]
    assert proposals.pending_proposals() == []
    assert proposals.get_proposal(executed["order_id"])["status"] == "EXECUTED"


def test_order_wizard_creates_pending_from_short_set(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    service, _toggles, executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}

    service.process_update(client, settings, {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/order"}})
    assert drafts.get_draft("-1003941549878")["mode"] == "new"
    assert client.keyboards[-1][0][0]["callback_data"] == "wiz:sample"
    assert client.keyboards[-1][0][1]["callback_data"] == "wiz:save"

    service.process_update(client, settings, {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/set ETHUSD BUY 0.03 1980 2050"}})
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "save",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "wiz:save",
            }
        },
    )
    pending = proposals.pending_proposals()
    assert len(pending) == 1
    assert pending[0]["symbol"] == "ETHUSD"
    assert pending[0]["lot"] == 0.03
    assert executions == []
    assert drafts.get_draft("-1003941549878") is None


def test_order_wizard_sample_sends_copyable_set(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}

    service.process_update(client, settings, {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/order"}})
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "sample",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "wiz:sample",
            }
        },
    )
    assert "/set VN30F1M BUY 1 1280.0 1290.0" in client.messages[-1][1]
    assert client.messages[-1][1].count("/set") == 1


def test_set_accepts_symbol_side_full_line(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}

    service.process_update(client, settings, {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/order"}})
    service.process_update(
        client,
        settings,
        {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/set BTCUSD SELL 0.26 63911.36 61503.2"}},
    )
    draft = drafts.get_draft("-1003941549878")
    assert draft["symbol"] == "BTCUSD"
    assert draft["side"] == "SELL"
    assert draft["lot"] == 0.26


def test_order_wizard_requires_all_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}

    service.process_update(client, settings, {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/order"}})
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "save",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "wiz:save",
            }
        },
    )
    assert "Missing fields" in client.messages[-1][1]
    assert proposals.pending_proposals() == []


def test_edit_wizard_updates_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    monkeypatch.setattr(drafts, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(0, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050}, user_label="CHANNEL")
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": ""}

    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "edit",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": f"wiz:edit:{proposal['order_id']}",
            }
        },
    )
    assert drafts.get_draft("-1003941549878")["mode"] == "edit"
    service.process_update(client, settings, {"channel_post": {"chat": {"id": "-1003941549878"}, "text": "/set lot=0.02 sl=1970 tp=2040"}})
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "save-edit",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878", "type": "channel"}},
                "data": "wiz:save",
            }
        },
    )
    updated = proposals.get_proposal(proposal["order_id"])
    assert updated["lot"] == 0.02
    assert updated["sl"] == 1970.0
    assert updated["tp"] == 2040.0
    assert updated["status"] == "PENDING"


def test_operator_cancel_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {"message": {"chat": {"id": "-1003941549878"}, "from": {"id": 2}, "text": f"/cancel {proposal['order_id']}"}},
    )
    assert proposals.get_proposal(proposal["order_id"])["status"] == "CANCELLED"
    assert "cancelled" in client.messages[-1][1]


def test_operator_cancel_callback_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(proposals, "account_dir", lambda: str(tmp_path))
    proposal = proposals.create_proposal(2, {"symbol": "ETHUSD", "side": "BUY", "lot": 0.03, "sl": 1980, "tp": 2050})
    service, _toggles, _executions = _service()
    client = FakeClient()
    settings = {"control_chat_id": "1003941549878", "owner_user_id": "1", "operator_user_ids": "2"}
    service.process_update(
        client,
        settings,
        {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 2},
                "message": {"chat": {"id": "-1003941549878"}},
                "data": f"ord:cancel:{proposal['order_id']}",
            }
        },
    )
    assert proposals.get_proposal(proposal["order_id"])["status"] == "CANCELLED"
