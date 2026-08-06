# -*- coding: utf-8 -*-
import json
import os
import time

from .client import TelegramClient
from . import proposals
from .settings import account_dir, load_settings


def cooldown_path():
    return os.path.join(account_dir(), "telegram_signal_cooldowns.json")


def _load_cooldowns():
    path = cooldown_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cooldowns(data):
    path = cooldown_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data if isinstance(data, dict) else {}, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


def _cooldown_key(symbol, side):
    return f"{str(symbol or '').upper()}|{str(side or '').upper()}"


def _is_on_cooldown(symbol, side, cooldown_minutes):
    cooldowns = _load_cooldowns()
    key = _cooldown_key(symbol, side)
    last = float(cooldowns.get(key, 0.0) or 0.0)
    now = time.time()
    cooldown_s = max(0.0, float(cooldown_minutes or 0.0) * 60.0)
    if cooldown_s > 0 and now - last < cooldown_s:
        return True, int(cooldown_s - (now - last))
    return False, 0


def _mark_cooldown(symbol, side):
    cooldowns = _load_cooldowns()
    cooldowns[_cooldown_key(symbol, side)] = time.time()
    _save_cooldowns(cooldowns)


def format_signal_proposal(proposal):
    if not proposal:
        return "Signal proposal not found."
    meta = proposal.get("metadata") or {}
    mode = meta.get("market_mode") or "-"
    lines = [
        f"SIGNAL {proposal.get('order_id')}",
        f"{proposal.get('symbol')} {proposal.get('side')} lot={proposal.get('lot')} sl={proposal.get('sl')} tp={proposal.get('tp')}",
        f"Bot: OFF | Mode: {mode}",
    ]
    if proposal.get("error"):
        lines.append(f"Error: {proposal.get('error')}")
    return "\n".join(lines)


def maybe_send_signal_proposal(trade_manager, signal, log_cb=None, enforce_cooldown=True):
    log = log_cb or (lambda msg, error=False: None)
    settings = load_settings()
    if not settings.get("signal_proposals_enabled"):
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if not settings.get("control_enabled"):
        return {"ok": False, "skipped": True, "reason": "control_disabled"}
    chat_id = settings.get("control_chat_id")
    if not chat_id:
        return {"ok": False, "skipped": True, "reason": "missing_control_chat_id"}
    client = TelegramClient(token_env=settings.get("bot_token_env", "TELE_BOT_KEY"))
    if not client.enabled():
        return {"ok": False, "error": f"{settings.get('bot_token_env', 'TELE_BOT_KEY')} is not configured."}

    side = str((signal or {}).get("action") or "").strip().upper()
    symbol = str((signal or {}).get("symbol") or "").strip().upper()
    signal_class = str((signal or {}).get("signal_class") or "ENTRY").strip().upper()
    if signal_class != "ENTRY" or side not in ("BUY", "SELL") or not symbol:
        return {"ok": False, "skipped": True, "reason": "unsupported_signal"}

    if enforce_cooldown:
        blocked, remaining_s = _is_on_cooldown(
            symbol,
            side,
            settings.get("signal_proposal_cooldown_minutes", 15.0),
        )
        if blocked:
            return {"ok": False, "skipped": True, "reason": "cooldown", "remaining_seconds": remaining_s}

    context = (signal or {}).get("context") or {}
    market_mode = (signal or {}).get("market_mode") or context.get("market_mode") or "ANY"
    plan = trade_manager.build_telegram_signal_order(symbol, side, context=context, market_mode=market_mode)
    if not plan.get("ok"):
        log(f"[TELEGRAM SIGNAL] preview failed {symbol} {side}: {plan.get('error')}", error=True)
        return {"ok": False, "error": plan.get("error") or "preview_failed"}

    order = {
        "symbol": plan["symbol"],
        "side": plan["side"],
        "lot": plan["lot"],
        "sl": plan["sl"],
        "tp": plan["tp"],
    }
    proposal = proposals.create_proposal(
        0,
        order,
        user_label="SIGNAL",
        source="SIGNAL",
        metadata={
            "signal_id": (signal or {}).get("signal_id", ""),
            "signal_class": signal_class,
            "market_mode": market_mode,
            "price": plan.get("price"),
        },
    )
    _mark_cooldown(symbol, side)

    from .control import proposal_keyboard

    result = client.send_message_with_keyboard(
        chat_id,
        format_signal_proposal(proposal),
        keyboard=proposal_keyboard(proposal["order_id"]),
    )
    if result.get("ok"):
        log(f"[TELEGRAM SIGNAL] sent {proposal['order_id']} {symbol} {side}")
        result["proposal"] = proposal
    return result
