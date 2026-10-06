"""Bind an explicitly selected DNSE order to UNKNOWN; GET only at the broker.

Default is preview. Close Viking before --apply; account OS lease enforces it.
Never marks an unknown request as not sent, and never repeats its POST.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from preflight import ReadOnlyClient
from viking_v2 import config
from viking_v2.connections.dnse.paper import PaperBroker
from viking_v2.models import OrderIntent
from viking_v2.rules.state import RuleStateStore
from viking_v2.services.runtime import RuntimeBridge
from viking_v2.storage import JSONLineJournal
from viking_v2.trading.durable import AccountLease
from viking_v2.trading.execution import ExecutionService
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


def peek_intent(root, intent_id):
    database = root / "trading.sqlite3"
    if database.exists():
        connection = sqlite3.connect(database.as_uri()+"?mode=ro", uri=True)
        try:
            document = connection.execute("SELECT payload FROM documents WHERE name='pending_orders.json'").fetchone()
            rows = json.loads(document[0]) if document else json.loads((root / "pending_orders.json").read_text(encoding="utf-8-sig"))
        finally:
            connection.close()
    else:
        rows = json.loads((root / "pending_orders.json").read_text(encoding="utf-8-sig"))
    return next((OrderIntent.from_dict(row) for row in rows if row.get("id") == intent_id), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True, help="Full local intent UUID")
    parser.add_argument("--broker-order", required=True, help="Order ID explicitly checked in DNSE")
    parser.add_argument("--account", default=config.active_account_id())
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    client = ReadOnlyClient(account_no=args.account)
    bridge = RuntimeBridge(args.account)
    lease = None
    try:
        if args.apply:
            lease = AccountLease(bridge.root)
        intent = peek_intent(bridge.root, args.intent)
        if not intent:
            raise ValueError("Không tìm thấy lệnh local")
        row = client.get_order_detail(args.broker_order)
        if not row:
            raise ValueError("Không đọc được chi tiết lệnh DNSE")
        ExecutionService.validate_order_binding(intent, row)
        if not args.apply:
            print("READY_TO_BIND: chỉ xem trước; chưa thay đổi dữ liệu")
            return 0
        engine = ExecutionService(client, PaperBroker(bridge.paper_state_path), OrderQueue(bridge.pending_orders_path),
                                  JSONLineJournal(bridge.journal_path), trade_state=TradeStateStore(bridge.trade_state_path),
                                  rule_state=RuleStateStore(bridge.rule_state_path))
        engine.bind_confirmed_order(intent.id, row)
        engine.flush_events()
        print("BOUND: đã đối soát fill; không gửi lệnh mới")
        return 0
    finally:
        client.close()
        if lease:
            lease.close()


if __name__ == "__main__":
    raise SystemExit(main())
