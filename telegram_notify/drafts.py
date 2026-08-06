# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime

from .settings import account_dir


def drafts_path():
    return os.path.join(account_dir(), "telegram_order_drafts.json")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def draft_key(chat_id):
    return str(chat_id or "").strip()


def load_drafts():
    path = drafts_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_drafts(data):
    path = drafts_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data if isinstance(data, dict) else {}, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)
    return True


def get_draft(chat_id):
    return load_drafts().get(draft_key(chat_id))


def upsert_draft(chat_id, draft):
    data = load_drafts()
    key = draft_key(chat_id)
    item = dict(draft or {})
    item["chat_id"] = key
    item["updated_at"] = _now()
    item.setdefault("created_at", item["updated_at"])
    data[key] = item
    save_drafts(data)
    return item


def update_draft(chat_id, updates):
    item = get_draft(chat_id) or {}
    item.update(updates or {})
    return upsert_draft(chat_id, item)


def clear_draft(chat_id):
    data = load_drafts()
    key = draft_key(chat_id)
    existed = key in data
    if existed:
        data.pop(key, None)
        save_drafts(data)
    return existed
