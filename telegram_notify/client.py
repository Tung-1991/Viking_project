# -*- coding: utf-8 -*-
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


TELEGRAM_TEXT_LIMIT = 4096
logger = logging.getLogger("RAT_CKVN")
_failure_log_times = {}


def _log_send_failure(action, error):
    """Log one compact failure per action/error each minute; no retry queue."""
    safe_error = str(error or "Telegram send failed")
    signature = (str(action), safe_error)
    now = time.time()
    if now - float(_failure_log_times.get(signature, 0.0) or 0.0) < 60.0:
        return
    _failure_log_times[signature] = now
    logger.warning("[TELEGRAM] %s failed: %s", action, safe_error)


def get_env_value(name):
    name = str(name or "").strip()
    if not name:
        return ""
    value = os.environ.get(name, "")
    if value:
        return value
    if os.name != "nt":
        return ""
    try:
        import winreg

        locations = [
            (winreg.HKEY_CURRENT_USER, "Environment"),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
        ]
        for root, path in locations:
            try:
                with winreg.OpenKey(root, path) as key:
                    registry_value, _value_type = winreg.QueryValueEx(key, name)
                if registry_value:
                    return str(registry_value)
            except OSError:
                continue
    except Exception:
        pass
    return ""


def _is_ssl_verify_error(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    text = str(exc).lower()
    return "certificate_verify_failed" in text or "self-signed certificate" in text


def _chunk_text(text, chunk_size=3500):
    text = str(text or "")
    try:
        size = int(chunk_size)
    except Exception:
        size = 3500
    size = max(500, min(3900, size, TELEGRAM_TEXT_LIMIT - 200))
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


def _chat_id_candidates(chat_id):
    raw = str(chat_id or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if raw.startswith("-"):
        return candidates
    compact = raw.replace(" ", "")
    if compact.isdigit():
        if compact.startswith("100"):
            candidates.append(f"-{compact}")
        else:
            candidates.append(f"-100{compact}")
    return list(dict.fromkeys(candidates))


class TelegramClient:
    def __init__(self, token=None, token_env="TELE_BOT_KEY", timeout=6, allow_insecure_ssl=None):
        self.token_env = token_env or "TELE_BOT_KEY"
        self.token = token or get_env_value(self.token_env)
        self.timeout = timeout
        if allow_insecure_ssl is None:
            allow_insecure_ssl = str(os.getenv("TELEGRAM_ALLOW_INSECURE_SSL", "false")).lower() in {"1", "true", "yes", "on"}
        self.allow_insecure_ssl = bool(allow_insecure_ssl)

    def enabled(self):
        return bool(self.token)

    def _safe_error(self, value):
        text = str(value or "")
        return text.replace(str(self.token), "[REDACTED]") if self.token else text

    def _urlopen(self, req):
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except Exception as exc:
            if self.allow_insecure_ssl and _is_ssl_verify_error(exc):
                context = ssl._create_unverified_context()
                return urllib.request.urlopen(req, timeout=self.timeout, context=context)
            raise

    def _request(self, method, payload):
        if not self.token:
            return {"ok": False, "error": f"{self.token_env} is not configured."}
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with self._urlopen(req) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            if not parsed.get("ok", False):
                return {"ok": False, "error": parsed.get("description", "Telegram API returned ok=false"), "raw": parsed}
            return {"ok": True, "raw": parsed}
        except urllib.error.HTTPError as exc:
            detail = ""
            retry_after = None
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(detail)
                retry_after = parsed.get("parameters", {}).get("retry_after")
                detail = parsed.get("description", detail)
            except Exception:
                detail = detail or str(exc)
            return {"ok": False, "error": self._safe_error(f"HTTP {exc.code}: {detail}"), "retry_after": retry_after}
        except Exception as exc:
            return {"ok": False, "error": self._safe_error(exc)}

    def _json_request(self, method, payload):
        if not self.token:
            return {"ok": False, "error": f"{self.token_env} is not configured."}
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen(req) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            if not parsed.get("ok", False):
                return {"ok": False, "error": parsed.get("description", "Telegram API returned ok=false"), "raw": parsed}
            return {"ok": True, "raw": parsed}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(detail)
                detail = parsed.get("description", detail)
            except Exception:
                detail = detail or str(exc)
            return {"ok": False, "error": self._safe_error(f"HTTP {exc.code}: {detail}")}
        except Exception as exc:
            return {"ok": False, "error": self._safe_error(exc)}

    def send_message(self, chat_id, text, parse_mode=None):
        last_error = ""
        for candidate in _chat_id_candidates(chat_id):
            payload = {
                "chat_id": candidate,
                "text": text,
                "disable_web_page_preview": "true",
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            result = self._request("sendMessage", payload)
            if result.get("ok"):
                result["chat_id"] = candidate
                return result
            last_error = result.get("error", "Telegram send failed")
        error = last_error or "Telegram chat_id is not configured."
        _log_send_failure("sendMessage", self._safe_error(error))
        return {"ok": False, "error": error}

    def send_message_with_keyboard(self, chat_id, text, keyboard=None):
        last_error = ""
        for candidate in _chat_id_candidates(chat_id):
            payload = {
                "chat_id": candidate,
                "text": text,
                "disable_web_page_preview": True,
            }
            if keyboard:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
            result = self._json_request("sendMessage", payload)
            if result.get("ok"):
                result["chat_id"] = candidate
                return result
            last_error = result.get("error", "Telegram send failed")
        error = last_error or "Telegram chat_id is not configured."
        _log_send_failure("sendMessageWithKeyboard", self._safe_error(error))
        return {"ok": False, "error": error}

    def edit_message_text(self, chat_id, message_id, text, keyboard=None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return self._json_request("editMessageText", payload)

    def answer_callback_query(self, callback_query_id, text=""):
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._json_request("answerCallbackQuery", payload)

    def get_updates(self, offset=None, timeout=15):
        payload = {
            "timeout": int(timeout),
            "allowed_updates": json.dumps(["message", "channel_post", "callback_query"]),
        }
        if offset is not None:
            payload["offset"] = int(offset)
        result = self._request("getUpdates", payload)
        if not result.get("ok"):
            return result
        return {"ok": True, "updates": result.get("raw", {}).get("result", []) or []}

    def send_long_message(self, chat_id, text, chunk_size=3500, title="RAT6 Report"):
        if not self.token:
            return {"ok": False, "error": f"{self.token_env} is not configured.", "sent": 0}
        chunks = _chunk_text(text, chunk_size=chunk_size)
        sent = 0
        for idx, chunk in enumerate(chunks, start=1):
            if title:
                header = (
                    f"{title} ({idx}/{len(chunks)})\n\n"
                    if len(chunks) > 1
                    else f"{title}\n\n"
                )
            else:
                header = f"({idx}/{len(chunks)})\n\n" if len(chunks) > 1 else ""
            result = self.send_message(chat_id, header + chunk)
            if not result.get("ok"):
                result["sent"] = sent
                return result
            sent += 1
            if len(chunks) > 1:
                time.sleep(0.35)
        return {"ok": True, "sent": sent}
