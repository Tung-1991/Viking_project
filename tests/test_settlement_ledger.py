# -*- coding: utf-8 -*-
from types import SimpleNamespace

from core import settlement_ledger


def test_settlement_ledger_record_enrich_and_drop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    account = "TEST_ACCOUNT"

    entry = settlement_ledger.record_buy(account, "O1", "fpt", 100, "2026-06-17", "2026-06-19")
    assert entry["symbol"] == "FPT"

    pos = SimpleNamespace(
        ticket="P1",
        position_id="P1",
        order_id="O1",
        symbol="FPT",
        type=0,
        volume=100,
        raw={"orderId": "O1"},
    )
    rows = settlement_ledger.enrich_positions(account, [pos])

    assert rows == [{"symbol": "FPT", "type": 0, "volume": 100.0, "settle_date": "2026-06-19"}]
    assert pos.raw["settle_date"] == "2026-06-19"

    settlement_ledger.drop(account, "P1")
    assert settlement_ledger.enrich_positions(account, [pos])[0]["settle_date"] == "2026-06-19"


def test_settlement_ledger_prunes_missing_positions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    account = "TEST_ACCOUNT"
    settlement_ledger.record_buy(account, "O1", "FPT", 100, "2026-06-17", "2026-06-19")

    rows = settlement_ledger.enrich_positions(account, [])

    assert rows == []
    assert settlement_ledger.enrich_positions(account, []) == []
