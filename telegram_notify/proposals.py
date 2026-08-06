# -*- coding: utf-8 -*-
import json
import os
import time
import uuid
from datetime import datetime

from .settings import account_dir


ACTIVE_STATUS = "PENDING"
FINAL_STATUSES = {"EXECUTED", "CANCELLED", "FAILED"}


def proposals_path():
    return os.path.join(account_dir(), "telegram_order_proposals.json")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def new_order_id():
    return f"TG{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4].upper()}"


def load_proposals():
    path = proposals_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_proposals(data):
    path = proposals_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data if isinstance(data, dict) else {}, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)
    return True


def create_proposal(user_id, order, user_label=None, source="MANUAL", metadata=None):
    data = load_proposals()
    order_id = new_order_id()
    now = _now()
    proposal = {
        "order_id": order_id,
        "created_by": int(user_id),
        "updated_by": int(user_id),
        "created_by_label": str(user_label or int(user_id)),
        "updated_by_label": str(user_label or int(user_id)),
        "status": ACTIVE_STATUS,
        "source": str(source or "MANUAL").upper(),
        "symbol": order["symbol"],
        "side": order["side"],
        "lot": float(order["lot"]),
        "sl": float(order["sl"]),
        "tp": float(order["tp"]),
        "ticket": None,
        "error": "",
        "created_at": now,
        "updated_at": now,
        "executed_at": "",
        "metadata": metadata or {},
    }
    data[order_id] = proposal
    save_proposals(data)
    return proposal


def get_proposal(order_id):
    return load_proposals().get(str(order_id or "").strip().upper())


def update_proposal(order_id, updates, user_id=None, user_label=None):
    data = load_proposals()
    key = str(order_id or "").strip().upper()
    proposal = data.get(key)
    if not isinstance(proposal, dict):
        return None
    proposal.update(updates or {})
    if user_id is not None:
        proposal["updated_by"] = int(user_id)
        proposal["updated_by_label"] = str(user_label or int(user_id))
    proposal["updated_at"] = _now()
    data[key] = proposal
    save_proposals(data)
    return proposal


def pending_proposals():
    return [
        item
        for item in load_proposals().values()
        if isinstance(item, dict) and item.get("status") == ACTIVE_STATUS
    ]


def clear_pending(user_id=None):
    data = load_proposals()
    count = 0
    now = _now()
    for key, proposal in list(data.items()):
        if isinstance(proposal, dict) and proposal.get("status") == ACTIVE_STATUS:
            proposal["status"] = "CANCELLED"
            proposal["executed_at"] = now
            proposal["updated_at"] = now
            if user_id is not None:
                proposal["updated_by"] = int(user_id)
            data[key] = proposal
            count += 1
    if count:
        save_proposals(data)
    return count


def mark_executed(order_id, ticket):
    return update_proposal(order_id, {"status": "EXECUTED", "ticket": ticket, "error": "", "executed_at": _now()})


def mark_failed(order_id, error):
    return update_proposal(order_id, {"status": "FAILED", "error": str(error or "ORDER_FAILED"), "executed_at": _now()})


def mark_cancelled(order_id, user_id=None):
    return update_proposal(order_id, {"status": "CANCELLED", "executed_at": _now()}, user_id=user_id)
