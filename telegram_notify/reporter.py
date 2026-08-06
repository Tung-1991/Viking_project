# -*- coding: utf-8 -*-
import os

from .client import TelegramClient, get_env_value
from .settings import load_settings, settings_path


def report_diagnostics(settings=None):
    settings = settings or load_settings()
    token_env = settings.get("bot_token_env", "TELE_BOT_KEY")
    token = get_env_value(token_env)
    insecure_ssl = True
    return {
        "settings_path": settings_path(),
        "enabled": bool(settings.get("enabled")),
        "token_env": token_env,
        "token_present": bool(token),
        "token_length": len(token),
        "insecure_ssl": insecure_ssl,
        "report_chat_id": settings.get("report_chat_id", ""),
    }


def send_text_report(text, title="RAT6 AI Advisor", require_enabled=True):
    settings = load_settings()
    diag = report_diagnostics(settings)
    if require_enabled and not settings.get("enabled"):
        return {"ok": False, "skipped": True, "error": "Telegram report is disabled.", "diagnostics": diag}
    chat_id = settings.get("report_chat_id")
    if not chat_id:
        return {"ok": False, "error": "Telegram report_chat_id is not configured.", "diagnostics": diag}
    client = TelegramClient(
        token_env=settings.get("bot_token_env", "TELE_BOT_KEY"),
        allow_insecure_ssl=diag.get("insecure_ssl"),
    )
    result = client.send_long_message(
        chat_id,
        text,
        chunk_size=settings.get("chunk_size", 3500),
        title=title,
    )
    if isinstance(result, dict):
        result.setdefault("diagnostics", diag)
    return result


def send_advisor_response(path, title="RAT6 AI Advisor Response"):
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "advisor_response.md not found."}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return send_text_report(text, title=title, require_enabled=True)
