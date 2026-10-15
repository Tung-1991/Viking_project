"""Passive, zero-fill BUY records and explicit operator activation metadata."""
from datetime import datetime

from .market import VN_TZ


def managed_buy(intent):
    return intent.side == "BUY" and (intent.source == "BOT" or bool(intent.details.get("operator_armed_buy")))


def submitted_day(intent):
    return datetime.fromtimestamp(intent.handed_off_at or intent.claimed_at or intent.created_at, VN_TZ).date().isoformat()


def zero_fill_candidate(intent):
    return (intent.side == "BUY" and intent.source == "BOT" and intent.filled_quantity == 0
            and intent.status in {"CANCELLED", "EXPIRED"} and bool(intent.broker_order_id or intent.broker_order_ids)
            and not intent.details.get("operator_armed_buy") and not intent.details.get("buy_remainder"))


def watch_visible(intent, now):
    watch = intent.details.get("buy_watch") or {}
    return zero_fill_candidate(intent) and bool(watch) and (
        not watch.get("visible_until") or now < float(watch["visible_until"]))


def watch_armable(intent, now):
    watch = intent.details.get("buy_watch") or {}
    return (watch_visible(intent, now) and bool(watch.get("session_date"))
            and not watch.get("armed_order_id"))
