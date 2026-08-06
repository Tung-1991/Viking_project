# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime

import config
from core import pending_orders, storage_manager


def _isolated_account(monkeypatch, tmp_path, expire_hours=24):
    account_dir = tmp_path / "ACC1"
    account_dir.mkdir()
    monkeypatch.setattr(storage_manager, "_active_account_dir", str(account_dir))
    monkeypatch.setattr(storage_manager, "BRAIN_FILE", str(account_dir / "brain_settings.json"))
    with open(storage_manager.BRAIN_FILE, "w", encoding="utf-8") as f:
        json.dump({"bot_safeguard": {"PENDING_ORDER_EXPIRE_HOURS": expire_hours}}, f)
    storage_manager.invalidate_settings_cache()
    return account_dir


def test_pending_order_add_cancel_and_delete(monkeypatch, tmp_path):
    account_dir = _isolated_account(monkeypatch, tmp_path, expire_hours=12)

    item = pending_orders.add_order(
        symbol="VN30F1M",
        side="BUY",
        preset="SCALPING",
        lot=1,
        entry_price=1200,
        sl=1190,
        tp=1220,
    )

    assert os.path.exists(account_dir / "pending_orders.json")
    assert item["target"] == "OPEN"
    assert item["expire_at"] >= item["created_at"] + 12 * 3600
    assert len(pending_orders.list_active()) == 1

    cancelled = pending_orders.cancel(item["id"])
    assert cancelled["status"] == pending_orders.CANCELLED
    assert pending_orders.delete_final(item["id"]) is True
    assert pending_orders.list_all() == []


def test_recover_stuck_sending_back_to_pending(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    item = pending_orders.add_order(symbol="VN30F1M", side="BUY", preset="SCALPING", lot=1, entry_price=1200)
    # Ép sang SENDING với claimed_at đã lâu (mô phỏng app crash giữa claim->gửi).
    pending_orders.mark(item["id"], pending_orders.SENDING, "", claimed_at=1.0)
    recovered = pending_orders.recover_stuck(max_age_sec=600.0)
    assert len(recovered) == 1
    assert recovered[0]["status"] == pending_orders.PENDING
    # SENDING còn mới (vừa claim) thì KHÔNG đụng.
    import time as _t
    pending_orders.mark(item["id"], pending_orders.SENDING, "", claimed_at=_t.time())
    assert pending_orders.recover_stuck(max_age_sec=600.0) == []


def test_claim_due_is_atomic_and_does_not_claim_twice(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    item = pending_orders.add_order(
        symbol="VN30F1M",
        side="BUY",
        preset="SCALPING",
        lot=1,
        entry_price=0,
    )

    due = pending_orders.claim_due(lambda _symbol: ("ATO", ""), now=item["created_at"] + 1)

    assert [x["id"] for x in due] == [item["id"]]
    assert pending_orders.list_all()[0]["status"] == pending_orders.SENDING
    assert pending_orders.claim_due(lambda _symbol: ("ATO", ""), now=item["created_at"] + 2) == []


def test_expired_pending_order_is_not_claimed(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pending_orders,
        "_next_target_session_end",
        lambda _symbol, _target, created_at: created_at,
    )
    item = pending_orders.add_order(
        symbol="VN30F1M",
        side="BUY",
        preset="SCALPING",
        lot=1,
        entry_price=1200,
        expire_hours=0.01,
    )

    forced_expiry = item["created_at"] + 1
    pending_orders.mark(item["id"], pending_orders.PENDING, expire_at=forced_expiry)
    due = pending_orders.claim_due(lambda _symbol: ("OPEN", ""), now=forced_expiry + 1)

    assert due == []
    stored = pending_orders.list_all()[0]
    assert stored["status"] == pending_orders.EXPIRED


def test_pending_entry_survives_weekend_and_holiday_until_next_session(
    monkeypatch, tmp_path
):
    _isolated_account(monkeypatch, tmp_path)
    friday_after_close = datetime(2026, 7, 31, 16, 0).timestamp()
    monkeypatch.setattr(pending_orders, "_now", lambda: friday_after_close)
    monkeypatch.setattr(
        "core.market_calendar.date_status",
        lambda value=None: {
            "status": (
                "HOLIDAY"
                if value.strftime("%Y-%m-%d") == "2026-08-03"
                else "TRADING"
            )
        },
    )

    item = pending_orders.add_order(
        symbol="VN30F1M",
        side="BUY",
        preset="SCALPING",
        lot=1,
        entry_price=1200,
        expire_hours=24,
    )

    next_session_end = datetime(2026, 8, 4, 14, 30).timestamp()
    assert item["expire_at"] >= next_session_end


def test_pending_orders_never_cross_paper_real(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    paper = pending_orders.add_order(
        symbol="VN30F1M", side="BUY", preset="SCALPING", target="OPEN", entry_price=1200
    )
    assert paper["execution_mode"] == "PAPER"

    monkeypatch.setattr(config, "PAPER_TRADING", False)
    assert pending_orders.claim_due(lambda _symbol: ("OPEN", ""), now=paper["created_at"] + 1) == []
    real = pending_orders.add_order(
        symbol="VN30F1M", side="BUY", preset="SCALPING", target="OPEN", entry_price=1200
    )
    assert real["execution_mode"] == "REAL"
    due = pending_orders.claim_due(lambda _symbol: ("OPEN", ""), now=real["created_at"] + 1)
    assert [item["id"] for item in due] == [real["id"]]

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    due = pending_orders.claim_due(lambda _symbol: ("OPEN", ""), now=paper["created_at"] + 2)
    assert [item["id"] for item in due] == [paper["id"]]


def test_pending_close_waits_for_open_and_does_not_expire(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", True)

    item = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="PAPER-1",
        side="SELL",
        lot=1,
        execution_mode="PAPER",
    )

    assert item["action"] == "CLOSE"
    assert item["target"] == "OPEN"
    assert item["expire_at"] == 0
    assert pending_orders.expire_pending(now=item["created_at"] + 365 * 86400) == []
    assert pending_orders.claim_due(
        lambda _symbol: ("CLOSED", ""),
        now=item["created_at"] + 1,
    ) == []

    due = pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=item["created_at"] + 2,
    )
    assert [row["id"] for row in due] == [item["id"]]


def test_paper_limit_close_waits_for_executable_price(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    item = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="PAPER-2",
        side="SELL",
        lot=1,
        execution_mode="PAPER",
        entry_mode="LIMIT",
        limit_price=1888.4,
    )

    assert item["entry_mode"] == "LIMIT"
    assert item["entry_price"] == 1888.4
    assert item["wait_for_trigger"] is True
    assert pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=item["created_at"] + 1,
        quote_fn=lambda _symbol, _side: 1888.3,
    ) == []
    due = pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=item["created_at"] + 2,
        quote_fn=lambda _symbol, _side: 1888.4,
    )
    assert [row["id"] for row in due] == [item["id"]]


def test_real_limit_close_is_sent_to_broker_without_local_price_trigger(
    monkeypatch,
    tmp_path,
):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    item = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="9123",
        side="SELL",
        lot=1,
        execution_mode="REAL",
        entry_mode="LIMIT",
        limit_price=1888.4,
    )

    assert item["wait_for_trigger"] is False
    due = pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=item["created_at"] + 1,
        quote_fn=lambda _symbol, _side: 1800.0,
    )
    assert [row["id"] for row in due] == [item["id"]]


def test_pending_entry_persists_bypass_choice(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    item = pending_orders.add_order(
        symbol="VN30F1M",
        side="BUY",
        preset="SCALPING",
        entry_price=1888.4,
        bypass_checklist=True,
    )

    assert item["bypass_checklist"] is True


def test_pending_close_is_deduplicated_per_position_and_mode(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)

    first = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="PAPER-9",
        side="SELL",
        execution_mode="PAPER",
    )
    duplicate = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="PAPER-9",
        side="SELL",
        execution_mode="PAPER",
    )

    assert duplicate["id"] == first["id"]
    assert len(pending_orders.list_active()) == 1


def test_find_active_close_is_mode_safe_and_ignores_final_rows(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    paper = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="PAPER-11",
        side="SELL",
        execution_mode="PAPER",
    )

    assert pending_orders.find_active_close("PAPER-11", "PAPER")["id"] == paper["id"]
    assert pending_orders.find_active_close("PAPER-11", "REAL") is None

    pending_orders.cancel(paper["id"])
    assert pending_orders.find_active_close("PAPER-11", "PAPER") is None


def test_scheduler_can_claim_inactive_close_but_not_inactive_entry(
    monkeypatch,
    tmp_path,
):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    close_item = pending_orders.add_close_order(
        symbol="VN30F1M",
        position_ticket="PAPER-21",
        side="SELL",
        execution_mode="PAPER",
    )
    entry_item = pending_orders.add_order(
        symbol="VN30F1M",
        side="BUY",
        preset="SCALPING",
        execution_mode="PAPER",
        entry_price=1200,
    )

    due = pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=max(close_item["created_at"], entry_item["created_at"]) + 1,
        include_inactive_closes=True,
    )

    assert [item["id"] for item in due] == [close_item["id"]]
    assert pending_orders.find_active_close("PAPER-21", "PAPER")["status"] == "SENDING"
    assert next(
        item for item in pending_orders.list_all() if item["id"] == entry_item["id"]
    )["status"] == "PENDING"


def test_limit_opportunity_waits_for_price_and_uses_nearest_allowed_tick(monkeypatch, tmp_path):
    _isolated_account(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    item = pending_orders.add_order(
        symbol="AAA",
        side="BUY",
        preset="SCALPING",
        target="OPEN",
        entry_price=7.20,
        entry_mode="LIMIT",
        wait_for_trigger=True,
        trigger_price=7.20,
        slippage_ticks=2,
    )

    assert pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=item["created_at"] + 1,
        quote_fn=lambda _symbol, _side: 7.25,
        point_fn=lambda _symbol: 0.01,
    ) == []

    due = pending_orders.claim_due(
        lambda _symbol: ("OPEN", ""),
        now=item["created_at"] + 2,
        quote_fn=lambda _symbol, _side: 7.215,
        point_fn=lambda _symbol: 0.01,
    )
    assert len(due) == 1
    assert due[0]["entry_price"] == 7.22
    assert due[0]["wait_for_trigger"] is False
