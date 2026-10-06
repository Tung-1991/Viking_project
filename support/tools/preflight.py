"""Read-only DNSE contract check. Never sends POST/PUT/DELETE or prints secrets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from viking_v2.connections.dnse.client import DNSEClient
from viking_v2 import config
from viking_v2.connections.dnse.paper import PaperBroker
from viking_v2.rules.state import RuleStateStore
from viking_v2.trading.orders import OrderQueue
from viking_v2.trading.state import TradeStateStore


class ReadOnlyClient(DNSEClient):
    def _request(self, method, path, **kwargs):
        if method.upper() != "GET":
            raise RuntimeError("Preflight permits GET only")
        kwargs.setdefault("timeout", 5)
        return super()._request(method, path, **kwargs)


def inspect(client, symbol="FPT"):
    report = {"configured": client.configured(), "trading_token_ready": client.has_trading_token(), "read_only": True}
    if not report["configured"]:
        return report
    def check(name, load, validate):
        try:
            value = load()
            report[name] = "OK" if validate(value) else "INVALID_RESPONSE"
            return value if report[name] == "OK" else None
        except Exception as exc:
            # Never include raw payloads, account IDs, signatures or errors
            # carrying a full URL. HTTP status is in api_health separately.
            report[name] = type(exc).__name__
            return None
    check("balance", lambda: client.get_balance(force=True), lambda value: isinstance(value, dict) and "stock" in value)
    check("positions", lambda: client.get_positions(force=True), lambda value: isinstance(value, list))
    orders = check("orders", lambda: client.get_orders(force=True), lambda value: isinstance(value, list))
    if orders is not None:
        report["orders_return_remark_field"] = any("remark" in row for row in orders) if orders else "NO_ORDERS_TO_CHECK"
    check("calendar", lambda: client.get_working_dates(force=True), lambda value: isinstance(value, list) and len(value) > 0)
    package = check("cash_package", lambda: client.cash_package(symbol), lambda value: str(value.get("initialRate")) in {"1", "1.0"})
    secdef = check("security_definition", lambda: client.get_secdef(symbol), lambda value: isinstance(value, dict) and float(value.get("referencePrice", value.get("ceilingPrice", 0)) or 0) > 0)
    if package and secdef:
        price = float(secdef.get("referencePrice", secdef.get("ceilingPrice", 0)) or 0)
        check("cash_buying_power", lambda: client.get_buying_power(symbol, str(package["id"]), price), lambda value: "qmaxBuy" in value)
    report["http_statuses"] = {key: value for key, value in client.api_health().get("by_endpoint", {}).items()}
    return report


def inspect_local(root):
    root = Path(root).resolve()
    document_rows = {}
    database = root / "trading.sqlite3"
    if database.exists():
        connection = sqlite3.connect(database.as_uri()+"?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Invalid financial database")
            document_rows = dict(connection.execute("SELECT name,payload FROM documents"))
        finally:
            connection.close()
    validators = [OrderQueue(root / "pending_orders.json").store, TradeStateStore(root / "trade_state.json").store,
                  RuleStateStore(root / "rule_state.json").store, PaperBroker(root / "paper_state.json").store]
    report = {}
    for store in validators:
        if store.path.name in document_rows:
            value = json.loads(document_rows[store.path.name])
            source = "SQLite"
        elif store.path.exists():
            value = json.loads(store.path.read_text(encoding="utf-8-sig"))
            source = "legacy_JSON"
        else:
            report[store.path.name] = "NEW_EMPTY_STORE"
            continue
        store._validate(value)  # Pure validation: no .read(), import or writes.
        report[store.path.name] = "OK:" + source
    return report


def preview_migration(root):
    """Run the real import on a disposable COPY, never the account workspace."""
    import shutil
    root = Path(root).resolve()
    if (root / "trading.sqlite3").exists():
        return "ALREADY_MIGRATED"
    with tempfile.TemporaryDirectory(prefix="viking-migration-") as scratch:
        copied = Path(scratch)
        for name in ("pending_orders.json", "trade_state.json", "rule_state.json", "paper_state.json"):
            if (root / name).exists():
                shutil.copy2(root / name, copied / name)
        queue = OrderQueue(copied / "pending_orders.json")
        trades = TradeStateStore(copied / "trade_state.json")
        rules = RuleStateStore(copied / "rule_state.json")
        paper = PaperBroker(copied / "paper_state.json")
        for store in (queue.store, trades.store, rules.store, paper.store):
            store.read()
        queue.recover_claims()
        queue.discard_unsubmitted_bot_buys("migration preview")
        rules.discard_buy_candidates()
        trades.list_cycles()
        paper.get_positions()
    return "OK_TEMP_COPY"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="FPT")
    parser.add_argument("--state-only", action="store_true")
    parser.add_argument("--migration-preview", action="store_true")
    args = parser.parse_args()
    client = ReadOnlyClient()
    try:
        local = inspect_local(config.account_root())
        if args.migration_preview:
            local["migration_preview"] = preview_migration(config.account_root())
        if args.state_only:
            print(json.dumps({"read_only": True, "local_state": local}, indent=2))
            return 0
        report = inspect(client, args.symbol.upper())
        report["local_state"] = local
        print(json.dumps(report, ensure_ascii=False, indent=2))
        checks = ("balance", "positions", "orders", "calendar", "cash_package", "security_definition", "cash_buying_power")
        return 0 if all(report.get(name) == "OK" for name in checks) else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
