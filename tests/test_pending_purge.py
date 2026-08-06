# -*- coding: utf-8 -*-

import core.storage_manager as sm
from core import pending_orders


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "_active_account_dir", str(tmp_path), raising=False)
    # đảm bảo file sạch
    return tmp_path


def test_purge_removes_old_dead_orders(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    now = 1_000_000.0

    o1 = pending_orders.add_order(symbol="CTG", side="BUY", preset="X")
    o2 = pending_orders.add_order(symbol="HPG", side="BUY", preset="X")
    o3 = pending_orders.add_order(symbol="SSI", side="BUY", preset="X")

    # o1 EXPIRED 3h trước, o2 FAILED 30 phút trước, o3 vẫn PENDING
    pending_orders.mark(o1["id"], pending_orders.EXPIRED, finalized_at=now - 3 * 3600)
    pending_orders.mark(o2["id"], pending_orders.FAILED, finalized_at=now - 1800)

    removed = pending_orders.purge_stale(max_age_sec=2 * 3600, now=now)
    ids = {r["id"] for r in removed}
    assert o1["id"] in ids            # EXPIRED > 2h -> dọn
    assert o2["id"] not in ids        # FAILED mới 30' -> giữ
    remaining = {o["id"] for o in pending_orders.list_all()}
    assert o1["id"] not in remaining
    assert o2["id"] in remaining and o3["id"] in remaining


def test_purge_keeps_sent_and_pending(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    now = 1_000_000.0
    sent = pending_orders.add_order(symbol="CTG", side="BUY", preset="X")
    pending_orders.mark(sent["id"], pending_orders.SENT, finalized_at=now - 10 * 3600)
    # SENT (đã lên sàn) không bao giờ bị dọn dù cũ
    removed = pending_orders.purge_stale(max_age_sec=3600, now=now)
    assert removed == []
    assert sent["id"] in {o["id"] for o in pending_orders.list_all()}


def test_purge_disabled_when_zero(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    now = 1_000_000.0
    o = pending_orders.add_order(symbol="CTG", side="BUY", preset="X")
    pending_orders.mark(o["id"], pending_orders.EXPIRED, finalized_at=now - 100 * 3600)
    assert pending_orders.purge_stale(max_age_sec=0, now=now) == []
    assert o["id"] in {x["id"] for x in pending_orders.list_all()}


def test_purge_fallback_expire_at_for_legacy(tmp_path, monkeypatch):
    """Item cũ không có finalized_at -> dùng expire_at làm mốc tuổi."""
    _setup(tmp_path, monkeypatch)
    now = 1_000_000.0
    o = pending_orders.add_order(symbol="CTG", side="BUY", preset="X", expire_hours=0.01)
    # ép EXPIRED nhưng xóa finalized_at để giả lập dữ liệu cũ
    pending_orders.mark(o["id"], pending_orders.EXPIRED)
    items = pending_orders._read_unlocked()
    for it in items:
        it.pop("finalized_at", None)
        it["expire_at"] = now - 5 * 3600
    pending_orders._write_unlocked(items)
    removed = pending_orders.purge_stale(max_age_sec=2 * 3600, now=now)
    assert o["id"] in {r["id"] for r in removed}


# --- logic phân loại scope (tách riêng để test độc lập với UI) ---
def _row_scope(ticket, symbol, derivatives=("VN30F1M",)):
    derivatives = {s.upper() for s in derivatives}
    ticket = str(ticket).upper()
    symbol = str(symbol).upper()
    is_paper = ticket.startswith("PAPER") or ticket.startswith("#PAPER")
    is_ps = symbol.startswith("VN30F") or symbol in derivatives
    if is_paper:
        return "Paper-PS" if is_ps else "Paper-CKCS"
    return "Phái sinh" if is_ps else "CKCS"


def test_row_scope_classification():
    assert _row_scope("123456", "VN30F1M") == "Phái sinh"
    assert _row_scope("123456", "HPG") == "CKCS"
    assert _row_scope("PAPER-1", "AAA") == "Paper-CKCS"
    assert _row_scope("PAPER-2", "VN30F1M") == "Paper-PS"
    assert _row_scope("#PAPER-3", "MBS") == "Paper-CKCS"
