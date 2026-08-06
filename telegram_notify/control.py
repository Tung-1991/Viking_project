# -*- coding: utf-8 -*-
import threading
import time
from datetime import datetime

import config
from core.position_classifier import (
    is_bot_position,
    is_manual_position,
)

from .client import TelegramClient
from . import drafts, proposals
from .settings import allowed_user_ids, load_settings, normalize_settings


ORDER_FIELDS = {"symbol", "side", "lot", "sl", "tp"}
PENDING_PAGE_SIZE = 5


CONTROL_HELP_TEXT = """RAT-control

/status
/positions
/pending

/order
/set VN30F1M BUY 1 1280.0 1290.0
"""


def _money(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "n/a"


def _command_name(text):
    token = str(text or "").strip().split()[0].lower() if str(text or "").strip() else ""
    if "@" in token:
        token = token.split("@", 1)[0]
    return token


def _side(pos):
    # DNSE BrokerPosition: prefer explicit side/direction string; fall back to numeric type (0 = BUY/LONG).
    raw = getattr(pos, "side", None) or getattr(pos, "direction", None)
    if raw is not None:
        txt = str(raw).strip().upper()
        if txt in ("BUY", "LONG", "B", "0"):
            return "BUY"
        if txt in ("SELL", "SHORT", "S", "1"):
            return "SELL"
    try:
        return "BUY" if int(getattr(pos, "type", -1) or 0) == 0 else "SELL"
    except (TypeError, ValueError):
        return "BUY"


def _pos_source(pos, magics):
    if is_bot_position(pos, magics):
        return "BOT"
    if is_manual_position(pos, magics):
        return "MANUAL"
    return "UNKNOWN"


def _keyboard(include_positions=True):
    rows = [
        [
            {"text": "Refresh", "callback_data": "ctl:status"},
            {"text": "Positions", "callback_data": "ctl:positions"},
        ],
        [
            {"text": "Bot ON", "callback_data": "ctl:bot_on"},
            {"text": "Bot OFF", "callback_data": "ctl:bot_off"},
        ],
    ]
    if include_positions:
        rows.append([{"text": "Close by /close TICKET", "callback_data": "ctl:help_close"}])
    return rows


def _status_keyboard(positions=None, include_close_buttons=True):
    rows = [
        [
            {"text": "Refresh", "callback_data": "ctl:status"},
            {"text": "Positions", "callback_data": "ctl:positions"},
        ],
        [
            {"text": "Bot ON", "callback_data": "ctl:bot_on"},
            {"text": "Bot OFF", "callback_data": "ctl:bot_off"},
        ],
    ]
    if include_close_buttons:
        for pos in list(positions or [])[:8]:
            ticket = getattr(pos, "ticket", "")
            if ticket:
                rows.append([{"text": f"Close #{ticket}", "callback_data": f"ctl:close:{ticket}"}])
    return rows


def _owner_user_id(settings):
    try:
        return int(str(settings.get("owner_user_id") or "").strip())
    except Exception:
        return None


def _is_owner(user_id, settings):
    owner = _owner_user_id(settings)
    return owner is not None and int(user_id or 0) == owner


def _parse_kv(parts):
    data = {}
    for part in parts:
        if "=" not in part:
            raise ValueError(f"Bad token: {part}")
        key, value = part.split("=", 1)
        key = key.strip().lower()
        if key not in ORDER_FIELDS:
            raise ValueError(f"Unsupported field: {key}")
        data[key] = value.strip()
    return data


def _normalize_order_fields(raw, require_all=True):
    data = dict(raw or {})
    if "symbol" in data:
        data["symbol"] = str(data["symbol"] or "").strip().upper()
    if "side" in data:
        data["side"] = str(data["side"] or "").strip().upper()
    if require_all:
        missing = [field for field in ("symbol", "side", "lot", "sl", "tp") if field not in data or str(data[field]).strip() == ""]
        if missing:
            raise ValueError(f"Missing fields: {', '.join(missing)}")
    if "side" in data and data["side"] not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    for key in ("lot", "sl", "tp"):
        if key in data:
            try:
                data[key] = float(data[key])
            except Exception:
                raise ValueError(f"{key} must be numeric")
    if "lot" in data and data["lot"] <= 0:
        raise ValueError("lot must be > 0")
    if "sl" in data and data["sl"] <= 0:
        raise ValueError("sl must be > 0")
    if "tp" in data and data["tp"] < 0:
        raise ValueError("tp must be >= 0")
    return data


def parse_order_command(text):
    parts = str(text or "").strip().split()
    if len(parts) < 6 or parts[0].lower() != "/order":
        raise ValueError("Use /order SYMBOL BUY lot=0.03 sl=1980 tp=2050")
    raw = {"symbol": parts[1], "side": parts[2]}
    raw.update(_parse_kv(parts[3:]))
    return _normalize_order_fields(raw, require_all=True)


def parse_edit_command(text):
    parts = str(text or "").strip().split()
    if len(parts) < 3 or parts[0].lower() != "/edit":
        raise ValueError("Use /edit ORDER_ID side=SELL lot=0.02 sl=2070 tp=2000")
    order_id = parts[1].strip().upper()
    updates = _normalize_order_fields(_parse_kv(parts[2:]), require_all=False)
    if not updates:
        raise ValueError("No editable fields supplied")
    return order_id, updates


def parse_set_command(text):
    parts = str(text or "").strip().split()
    if len(parts) < 2 or parts[0].lower() != "/set":
        raise ValueError("Use /set VN30F1M BUY 1 1280.0 1290.0")
    if all("=" not in part for part in parts[1:]):
        if len(parts) == 4:
            raw = {"lot": parts[1], "sl": parts[2], "tp": parts[3]}
        elif len(parts) == 6:
            raw = {"symbol": parts[1], "side": parts[2], "lot": parts[3], "sl": parts[4], "tp": parts[5]}
        else:
            raise ValueError("Use /set SYMBOL BUY LOT SL TP")
    else:
        raw = _parse_kv(parts[1:])
        unsupported = set(raw) - ORDER_FIELDS
        if unsupported:
            raise ValueError("Use /set symbol=VN30F1M side=BUY lot=1 sl=1280.0 tp=1290.0")
    return _normalize_order_fields(raw, require_all=False)


def format_proposal(proposal):
    if not proposal:
        return "Proposal not found."
    source = str(proposal.get("source") or "MANUAL").upper()
    title = "SIGNAL" if source == "SIGNAL" else "ORDER"
    lines = [
        f"{title} {proposal.get('order_id')}",
        f"Status: {proposal.get('status')}",
        f"Creator: {proposal.get('created_by_label') or proposal.get('created_by')}",
        f"{proposal.get('symbol')} {proposal.get('side')} lot={proposal.get('lot')} sl={proposal.get('sl')} tp={proposal.get('tp')}",
    ]
    if source == "SIGNAL":
        meta = proposal.get("metadata") or {}
        lines.append(f"Bot: OFF | Mode: {meta.get('market_mode') or '-'}")
    if proposal.get("ticket"):
        lines.append(f"Ticket: {proposal.get('ticket')}")
    if proposal.get("error"):
        lines.append(f"Error: {proposal.get('error')}")
    return "\n".join(lines)


def proposal_keyboard(order_id):
    return [
        [
            {"text": "Approve", "callback_data": f"ord:approve:{order_id}"},
            {"text": "Edit", "callback_data": f"wiz:edit:{order_id}"},
            {"text": "Cancel", "callback_data": f"ord:cancel:{order_id}"},
        ],
        [{"text": "Refresh", "callback_data": f"ord:refresh:{order_id}"}],
    ]


def pending_keyboard(items, page=0, total=0):
    rows = []
    for item in items:
        order_id = item.get("order_id")
        label = f"{item.get('symbol')} {item.get('side')} {item.get('lot')}"
        rows.append(
            [
                {"text": f"Open {label}", "callback_data": f"ord:refresh:{order_id}"},
                {"text": "Edit", "callback_data": f"wiz:edit:{order_id}"},
                {"text": "Cancel", "callback_data": f"ord:cancel:{order_id}"},
            ]
        )
    nav = []
    if page > 0:
        nav.append({"text": "Prev", "callback_data": f"pend:page:{page - 1}"})
    if (page + 1) * PENDING_PAGE_SIZE < total:
        nav.append({"text": "Next", "callback_data": f"pend:page:{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "Clear all pending", "callback_data": "pend:clear_all"}])
    return rows


def wizard_keyboard(symbols=None, draft=None):
    return [
        [
            {"text": "Sample", "callback_data": "wiz:sample"},
            {"text": "Save", "callback_data": "wiz:save"},
            {"text": "Cancel", "callback_data": "wiz:cancel"},
        ]
    ]


def format_positions(positions, magics=None, max_rows=12):
    magics = magics or {}
    if not positions:
        return "Open positions: 0"
    lines = [f"Open positions: {len(positions)}"]
    for pos in list(positions)[:max_rows]:
        profit = (
            float(getattr(pos, "profit", 0.0) or 0.0)
            + float(getattr(pos, "swap", 0.0) or 0.0)
            + float(getattr(pos, "commission", 0.0) or 0.0)
        )
        lines.append(
            "#{} {} {} {} lot={} entry={} pnl={} SL={} TP={}".format(
                getattr(pos, "ticket", ""),
                _pos_source(pos, magics),
                getattr(pos, "symbol", ""),
                _side(pos),
                getattr(pos, "volume", ""),
                getattr(pos, "price_open", ""),
                _money(profit),
                getattr(pos, "sl", ""),
                getattr(pos, "tp", ""),
            )
        )
    if len(positions) > max_rows:
        lines.append(f"... +{len(positions) - max_rows} more")
    return "\n".join(lines)


def format_status(account_info, state, positions, bot_enabled, brain_status="", active_symbols=None, magics=None):
    active_symbols = active_symbols or []
    floating = 0.0
    for pos in positions or []:
        floating += (
            float(getattr(pos, "profit", 0.0) or 0.0)
            + float(getattr(pos, "swap", 0.0) or 0.0)
            + float(getattr(pos, "commission", 0.0) or 0.0)
        )
    lines = ["RAT-control status"]
    if account_info:
        lines.extend(
            [
                f"Login: {account_info.get('login', 'n/a')}",
                f"Balance: {_money(account_info.get('balance'))}",
                f"Equity: {_money(account_info.get('equity'))}",
                f"Margin: {_money(account_info.get('margin'))}",
                f"Free margin: {_money(account_info.get('margin_free'))}",
            ]
        )
    else:
        lines.append("Account: disconnected")
    lines.extend(
        [
            f"Floating PnL: {_money(floating)}",
            f"Bot: {'ON' if bot_enabled else 'OFF'}",
            f"Brain: {brain_status or 'UNKNOWN'}",
            f"Active symbols: {', '.join(active_symbols) if active_symbols else '-'}",
            f"Bot today: pnl={_money(state.get('bot_pnl_today', 0.0))} trades={state.get('bot_trades_today', 0)}",
            f"Manual today: pnl={_money(state.get('manual_pnl_today', 0.0))} trades={state.get('manual_trades_today', 0)}",
        ]
    )
    cooldown_until = float(state.get("cooldown_until", 0.0) or 0.0)
    if cooldown_until > time.time():
        until = datetime.fromtimestamp(cooldown_until).strftime("%H:%M:%S")
        lines.append(f"Cooldown until: {until}")
    lines.append("")
    lines.append(format_positions(positions, magics=magics))
    return "\n".join(lines)


class TelegramControlService:
    def __init__(
        self,
        connector,
        get_state_cb,
        get_bot_enabled_cb,
        set_bot_enabled_cb,
        get_brain_status_cb=None,
        get_active_symbols_cb=None,
        execute_order_cb=None,
        log_cb=None,
    ):
        self.connector = connector
        self.get_state = get_state_cb
        self.get_bot_enabled = get_bot_enabled_cb
        self.set_bot_enabled = set_bot_enabled_cb
        self.get_brain_status = get_brain_status_cb or (lambda: "")
        self.get_active_symbols = get_active_symbols_cb or (lambda: [])
        self.execute_order = execute_order_cb
        self.log = log_cb or (lambda msg, error=False: None)
        self.running = False
        self.thread = None
        self.offset = None
        self.offset_primed = False

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)

    def refresh_settings(self):
        return normalize_settings(load_settings())

    def _client(self, settings):
        return TelegramClient(token_env=settings.get("bot_token_env", "TELE_BOT_KEY"), timeout=25)

    def _authorized(self, user_id, settings):
        allowed = allowed_user_ids(settings)
        return bool(allowed) and int(user_id or 0) in allowed

    def _control_chat_matches(self, chat_id, settings):
        expected = str(settings.get("control_chat_id") or "").strip()
        actual = str(chat_id or "").strip()
        return bool(expected) and actual in {expected, f"-{expected}" if expected.startswith("100") else f"-100{expected}"}

    def _magics(self):
        try:
            import core.storage_manager as storage_manager

            return storage_manager.get_magic_numbers()
        except Exception:
            return {}

    def _positions(self):
        try:
            return list(self.connector.get_all_open_positions() or [])
        except Exception:
            return []

    def _symbol_choices(self):
        seen = []
        for sym in list(self.get_active_symbols() or []) + list(getattr(config, "COIN_LIST", []) or []):
            sym = str(sym or "").strip().upper()
            if sym and sym not in seen:
                seen.append(sym)
        return seen

    def _draft_text(self, draft):
        draft = draft or {}
        mode = "EDIT" if draft.get("mode") == "edit" else "ORDER"
        order_id = draft.get("order_id")
        lines = [f"{mode}" + (f" {order_id}" if order_id else "")]
        lines.append(f"{draft.get('symbol') or 'SYMBOL'} {draft.get('side') or 'BUY/SELL'}")
        lines.append(
            "lot={} sl={} tp={}".format(
                draft.get("lot", "-"),
                draft.get("sl", "-"),
                draft.get("tp", "-"),
            )
        )
        lines.append("Sample: /set SYMBOL SIDE LOT SL TP")
        return "\n".join(lines)

    def _draft_sample_text(self, draft):
        draft = draft or {}
        symbol = draft.get("symbol") or "VN30F1M"
        side = draft.get("side") or "BUY"
        lot = draft.get("lot") if draft.get("lot") not in (None, "") else "1"
        sl = draft.get("sl") if draft.get("sl") not in (None, "") else "1280.0"
        tp = draft.get("tp") if draft.get("tp") not in (None, "") else "1290.0"
        return "\n".join(
            [
                f"/set {symbol} {side} {lot} {sl} {tp}",
            ]
        )

    def _send_draft(self, client, chat_id):
        draft = drafts.get_draft(chat_id)
        return client.send_message_with_keyboard(
            chat_id,
            self._draft_text(draft),
            keyboard=wizard_keyboard(self._symbol_choices(), draft),
        )

    def _send_status(self, client, chat_id, positions_only=False, plain=False):
        positions = self._positions()
        account_info = self.connector.get_account_info()
        magics = self._magics()
        if positions_only:
            text = format_positions(positions, magics=magics)
        else:
            text = format_status(
                account_info,
                self.get_state() or {},
                positions,
                bool(self.get_bot_enabled()),
                brain_status=self.get_brain_status(),
                active_symbols=self.get_active_symbols(),
                magics=magics,
            )
        if plain:
            return client.send_message(chat_id, text)
        return client.send_message_with_keyboard(chat_id, text, keyboard=_status_keyboard(positions))

    def _owner_required(self, client, chat_id, user_id, settings, action="this action"):
        if _is_owner(user_id, settings):
            return True
        client.send_message(chat_id, f"Owner only: {action}.")
        return False

    def _close_ticket(self, client, chat_id, ticket):
        try:
            ticket = int(str(ticket).strip().lstrip("#"))
        except Exception:
            return client.send_message(chat_id, "Invalid ticket. Sample: /close 123456")
        positions = self._positions()
        pos = next((p for p in positions if int(getattr(p, "ticket", 0) or 0) == ticket), None)
        if not pos:
            return client.send_message(chat_id, f"Ticket #{ticket} is not open anymore.")
        try:
            from core.market_hours import is_symbol_trade_window_open
            if not is_symbol_trade_window_open(getattr(pos, "symbol", ""))[0]:
                return client.send_message(chat_id, "Market is closed; close request was not sent to DNSE.")
        except Exception:
            return client.send_message(chat_id, "Cannot verify market session; close request was not sent.")
        result = self.connector.close_position(pos, comment="telegram_control_close")
        if result:
            return client.send_message(chat_id, f"Close sent for #{ticket} {getattr(pos, 'symbol', '')}.")
        return client.send_message(chat_id, f"Close failed for #{ticket}. Check DNSE logs.")

    def _toggle_bot(self, client, chat_id, enabled):
        self.set_bot_enabled(bool(enabled), reason="TELEGRAM_CONTROL")
        return client.send_message(chat_id, f"Bot is now {'ON' if enabled else 'OFF'}.")

    def _execute_proposal(self, proposal):
        if not self.execute_order:
            return {"ok": False, "error": "Order executor is not configured."}
        result = self.execute_order(
            proposal.get("symbol"),
            proposal.get("side"),
            proposal.get("lot"),
            proposal.get("sl"),
            proposal.get("tp"),
        )
        if isinstance(result, str) and result.startswith("SUCCESS"):
            parts = result.split("|")
            ticket = parts[1] if len(parts) > 1 else ""
            proposals.mark_executed(proposal.get("order_id"), ticket)
            return {"ok": True, "ticket": ticket}
        proposals.mark_failed(proposal.get("order_id"), result)
        return {"ok": False, "error": result}

    def _handle_order(self, client, settings, chat_id, user_id, text, channel_text=False):
        if str(text or "").strip().lower() == "/order":
            drafts.upsert_draft(
                chat_id,
                {
                    "mode": "new",
                    "created_by": int(user_id or 0),
                    "created_by_label": "CHANNEL" if channel_text else str(int(user_id or 0)),
                },
            )
            return self._send_draft(client, chat_id)
        try:
            order = parse_order_command(text)
        except ValueError as exc:
            return client.send_message(chat_id, str(exc))
        label = "CHANNEL" if channel_text else None
        proposal = proposals.create_proposal(user_id, order, user_label=label)
        if not channel_text and _is_owner(user_id, settings):
            client.send_message(chat_id, "Owner order received. Executing...\n" + format_proposal(proposal))
            result = self._execute_proposal(proposal)
            if result.get("ok"):
                return client.send_message(chat_id, f"Order executed. Ticket #{result.get('ticket')}")
            return client.send_message(chat_id, f"Order failed: {result.get('error')}")
        return client.send_message_with_keyboard(
            chat_id,
            format_proposal(proposal) + "\nOwner approve required.",
            keyboard=proposal_keyboard(proposal["order_id"]),
        )

    def _handle_set(self, client, chat_id, user_id, text, channel_text=False):
        draft = drafts.get_draft(chat_id)
        if not draft:
            return client.send_message(chat_id, "No draft. Use /order first.")
        try:
            updates = parse_set_command(text)
        except ValueError as exc:
            return client.send_message(chat_id, str(exc))
        if not updates:
            return client.send_message(chat_id, "Use /set VN30F1M BUY 1 1280.0 1290.0")
        draft = drafts.update_draft(chat_id, updates)
        return client.send_message_with_keyboard(
            chat_id,
            self._draft_text(draft),
            keyboard=wizard_keyboard(self._symbol_choices(), draft),
        )

    def _handle_edit(self, client, chat_id, user_id, text, channel_text=False):
        try:
            order_id, updates = parse_edit_command(text)
        except ValueError as exc:
            return client.send_message(chat_id, str(exc))
        proposal = proposals.get_proposal(order_id)
        if not proposal:
            return client.send_message(chat_id, f"Order {order_id} not found.")
        if proposal.get("status") != "PENDING":
            return client.send_message(chat_id, f"Order {order_id} is {proposal.get('status')}, cannot edit.")
        proposal = proposals.update_proposal(
            order_id,
            updates,
            user_id=user_id,
            user_label="CHANNEL" if channel_text else None,
        )
        return client.send_message_with_keyboard(
            chat_id,
            "Order updated.\n" + format_proposal(proposal),
            keyboard=proposal_keyboard(order_id),
        )

    def _start_edit_draft(self, client, chat_id, order_id):
        proposal = proposals.get_proposal(order_id)
        if not proposal:
            return client.send_message(chat_id, f"Order {order_id} not found.")
        if proposal.get("status") != "PENDING":
            return client.send_message(chat_id, f"Order {order_id} is {proposal.get('status')}, cannot edit.")
        draft = drafts.upsert_draft(
            chat_id,
            {
                "mode": "edit",
                "order_id": proposal.get("order_id"),
                "symbol": proposal.get("symbol"),
                "side": proposal.get("side"),
                "lot": proposal.get("lot"),
                "sl": proposal.get("sl"),
                "tp": proposal.get("tp"),
            },
        )
        return client.send_message_with_keyboard(
            chat_id,
            self._draft_text(draft),
            keyboard=wizard_keyboard(self._symbol_choices(), draft),
        )

    def _save_draft(self, client, settings, chat_id, user_id, channel_text=False):
        draft = drafts.get_draft(chat_id)
        if not draft:
            return client.send_message(chat_id, "No draft. Use /order first.")
        try:
            normalized = _normalize_order_fields(draft, require_all=True)
            order = {field: normalized[field] for field in ("symbol", "side", "lot", "sl", "tp")}
        except ValueError as exc:
            return client.send_message_with_keyboard(
                chat_id,
                f"{exc}\n" + self._draft_text(draft),
                keyboard=wizard_keyboard(self._symbol_choices(), draft),
            )

        if draft.get("mode") == "edit":
            order_id = draft.get("order_id")
            proposal = proposals.get_proposal(order_id)
            if not proposal:
                return client.send_message(chat_id, f"Order {order_id} not found.")
            if proposal.get("status") != "PENDING":
                return client.send_message(chat_id, f"Order {order_id} is {proposal.get('status')}, cannot edit.")
            proposal = proposals.update_proposal(
                order_id,
                order,
                user_id=user_id,
                user_label="CHANNEL" if channel_text else None,
            )
            drafts.clear_draft(chat_id)
            return client.send_message_with_keyboard(
                chat_id,
                "Edit saved.\n" + format_proposal(proposal),
                keyboard=proposal_keyboard(order_id),
            )

        label = draft.get("created_by_label") or ("CHANNEL" if channel_text else None)
        proposal = proposals.create_proposal(user_id or 0, order, user_label=label)
        drafts.clear_draft(chat_id)
        if not channel_text and _is_owner(user_id, settings):
            client.send_message(chat_id, "Owner order received. Executing...\n" + format_proposal(proposal))
            result = self._execute_proposal(proposal)
            if result.get("ok"):
                return client.send_message(chat_id, f"Order executed. Ticket #{result.get('ticket')}")
            return client.send_message(chat_id, f"Order failed: {result.get('error')}")
        return client.send_message_with_keyboard(
            chat_id,
            format_proposal(proposal) + "\nOwner approve required.",
            keyboard=proposal_keyboard(proposal["order_id"]),
        )

    def _handle_pending(self, client, chat_id, page=0):
        pending = proposals.pending_proposals()
        if not pending:
            return client.send_message(chat_id, "Pending: 0")
        total = len(pending)
        max_page = max(0, (total - 1) // PENDING_PAGE_SIZE)
        page = max(0, min(int(page or 0), max_page))
        start = page * PENDING_PAGE_SIZE
        items = pending[start : start + PENDING_PAGE_SIZE]
        lines = [f"Pending proposal: {total} | page {page + 1}/{max_page + 1}"]
        for idx, item in enumerate(items, start=start + 1):
            lines.append(
                f"{idx}. {item.get('order_id')} {item.get('symbol')} {item.get('side')} lot={item.get('lot')} sl={item.get('sl')} tp={item.get('tp')}"
            )
        return client.send_message_with_keyboard(
            chat_id,
            "\n".join(lines),
            keyboard=pending_keyboard(items, page=page, total=total),
        )

    def _approve_order(self, client, chat_id, user_id, settings, order_id):
        order_id = str(order_id or "").strip().upper()
        if not _is_owner(user_id, settings):
            return client.send_message(chat_id, "Bạn thiếu quyền approve order.")
        proposal = proposals.get_proposal(order_id)
        if not proposal:
            return client.send_message(chat_id, f"Order {order_id} not found.")
        if proposal.get("status") != "PENDING":
            return client.send_message(chat_id, f"Order {order_id} is {proposal.get('status')}.")
        client.send_message(chat_id, "Owner approved. Executing...\n" + format_proposal(proposal))
        result = self._execute_proposal(proposal)
        if result.get("ok"):
            return client.send_message(chat_id, f"Order executed. Ticket #{result.get('ticket')}")
        return client.send_message(chat_id, f"Order failed: {result.get('error')}")

    def _cancel_order(self, client, chat_id, user_id, settings, order_id):
        order_id = str(order_id or "").strip().upper()
        return self._cancel_order_unchecked(client, chat_id, user_id, order_id)

    def _cancel_order_unchecked(self, client, chat_id, user_id, order_id):
        order_id = str(order_id or "").strip().upper()
        proposal = proposals.get_proposal(order_id)
        if not proposal:
            return client.send_message(chat_id, f"Order {order_id} not found.")
        if proposal.get("status") != "PENDING":
            return client.send_message(chat_id, f"Order {order_id} is {proposal.get('status')}.")
        proposals.mark_cancelled(order_id, user_id=user_id)
        return client.send_message(chat_id, f"Order {order_id} cancelled.")

    def _handle_wizard_callback(self, client, settings, chat_id, user_id, data, channel_text=False):
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "new":
            drafts.upsert_draft(
                chat_id,
                {
                    "mode": "new",
                    "created_by": int(user_id or 0),
                    "created_by_label": "CHANNEL" if channel_text else str(int(user_id or 0)),
                },
            )
            return self._send_draft(client, chat_id)
        if action == "edit":
            order_id = parts[2] if len(parts) > 2 else ""
            return self._start_edit_draft(client, chat_id, order_id)
        if action == "cancel":
            drafts.clear_draft(chat_id)
            return client.send_message(chat_id, "Draft cancelled.")
        if action == "sample":
            draft = drafts.get_draft(chat_id)
            return client.send_message(chat_id, self._draft_sample_text(draft))
        if action == "save":
            return self._save_draft(client, settings, chat_id, user_id, channel_text=channel_text)
        if action == "side":
            side = parts[2] if len(parts) > 2 else ""
            if side not in ("BUY", "SELL"):
                return client.send_message(chat_id, "Bad side.")
            drafts.update_draft(chat_id, {"side": side})
            return self._send_draft(client, chat_id)
        if action == "symbol":
            symbol = parts[2] if len(parts) > 2 else ""
            if symbol not in self._symbol_choices():
                return client.send_message(chat_id, "Bad symbol.")
            drafts.update_draft(chat_id, {"symbol": symbol})
            return self._send_draft(client, chat_id)

    def _handle_pending_callback(self, client, chat_id, user_id, data):
        parts = data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        if action == "page":
            try:
                page = int(parts[2])
            except Exception:
                page = 0
            return self._handle_pending(client, chat_id, page=page)
        if action == "clear_all":
            count = proposals.clear_pending(user_id=user_id)
            drafts.clear_draft(chat_id)
            return client.send_message(chat_id, f"Cleared pending: {count}")

    def _handle_text(self, client, settings, message):
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        user_id = user.get("id")
        if not self._control_chat_matches(chat_id, settings):
            return
        text = str(message.get("text") or "").strip()
        cmd = _command_name(text)
        if cmd in {"/start", "/help"}:
            client.send_message(chat_id, CONTROL_HELP_TEXT)
        elif cmd == "/status":
            self._send_status(client, chat_id, positions_only=False)
        elif cmd == "/positions":
            self._send_status(client, chat_id, positions_only=True)
        elif cmd == "/bot_on":
            self._toggle_bot(client, chat_id, True)
        elif cmd == "/bot_off":
            self._toggle_bot(client, chat_id, False)
        elif cmd == "/close":
            parts = text.split(maxsplit=1)
            self._close_ticket(client, chat_id, parts[1] if len(parts) > 1 else "")
        elif cmd == "/order":
            self._handle_order(client, settings, chat_id, user_id, text)
        elif cmd == "/edit":
            self._handle_edit(client, chat_id, user_id, text)
        elif cmd == "/set":
            self._handle_set(client, chat_id, user_id, text)
        elif cmd == "/approve":
            parts = text.split(maxsplit=1)
            self._approve_order(client, chat_id, user_id, settings, parts[1] if len(parts) > 1 else "")
        elif cmd in {"/cancel", "/veto"}:
            parts = text.split(maxsplit=1)
            self._cancel_order(client, chat_id, user_id, settings, parts[1] if len(parts) > 1 else "")
        elif cmd == "/pending":
            self._handle_pending(client, chat_id)

    def _handle_channel_post(self, client, settings, message):
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if not self._control_chat_matches(chat_id, settings):
            return
        user_id = 0
        text = str(message.get("text") or "").strip()
        cmd = _command_name(text)
        if cmd in {"/start", "/help"}:
            client.send_message(chat_id, CONTROL_HELP_TEXT)
        elif cmd == "/status":
            self._send_status(client, chat_id, positions_only=False, plain=True)
        elif cmd == "/positions":
            self._send_status(client, chat_id, positions_only=True, plain=True)
        elif cmd == "/bot_on":
            self._toggle_bot(client, chat_id, True)
        elif cmd == "/bot_off":
            self._toggle_bot(client, chat_id, False)
        elif cmd == "/close":
            parts = text.split(maxsplit=1)
            self._close_ticket(client, chat_id, parts[1] if len(parts) > 1 else "")
        elif cmd == "/order":
            self._handle_order(client, settings, chat_id, user_id, text, channel_text=True)
        elif cmd == "/edit":
            self._handle_edit(client, chat_id, user_id, text, channel_text=True)
        elif cmd == "/set":
            self._handle_set(client, chat_id, user_id, text, channel_text=True)
        elif cmd == "/approve":
            client.send_message(chat_id, "Use Approve button.")
        elif cmd in {"/cancel", "/veto"}:
            parts = text.split(maxsplit=1)
            self._cancel_order_unchecked(client, chat_id, user_id, parts[1] if len(parts) > 1 else "")
        elif cmd == "/pending":
            self._handle_pending(client, chat_id)

    def _handle_callback(self, client, settings, callback):
        user = callback.get("from", {})
        user_id = user.get("id")
        msg = callback.get("message", {}) or {}
        chat_id = (msg.get("chat") or {}).get("id")
        data = str(callback.get("data") or "")
        client.answer_callback_query(callback.get("id"), "")
        if not self._control_chat_matches(chat_id, settings):
            return
        is_channel_callback = (msg.get("chat") or {}).get("type") == "channel"
        if data == "ctl:status":
            self._send_status(client, chat_id, positions_only=False)
        elif data == "ctl:positions":
            self._send_status(client, chat_id, positions_only=True)
        elif data == "ctl:bot_on":
            self._toggle_bot(client, chat_id, True)
        elif data == "ctl:bot_off":
            self._toggle_bot(client, chat_id, False)
        elif data.startswith("ctl:close:"):
            self._close_ticket(client, chat_id, data.split(":", 2)[2])
        elif data == "ctl:help_close":
            client.send_message(chat_id, "Sample: /close 123456")
        elif data.startswith("ord:"):
            parts = data.split(":")
            action = parts[1] if len(parts) > 1 else ""
            order_id = parts[2] if len(parts) > 2 else ""
            if action == "approve":
                self._approve_order(client, chat_id, user_id, settings, order_id)
            elif action == "cancel":
                self._cancel_order_unchecked(client, chat_id, user_id, order_id)
            elif action == "refresh":
                proposal = proposals.get_proposal(order_id)
                client.send_message_with_keyboard(
                    chat_id,
                    format_proposal(proposal),
                    keyboard=proposal_keyboard(order_id) if proposal and proposal.get("status") == "PENDING" else None,
                )
        elif data.startswith("wiz:"):
            self._handle_wizard_callback(
                client,
                settings,
                chat_id,
                user_id,
                data,
                channel_text=is_channel_callback,
            )
        elif data.startswith("pend:"):
            self._handle_pending_callback(client, chat_id, user_id, data)

    def process_update(self, client, settings, update):
        if "message" in update:
            self._handle_text(client, settings, update["message"])
        elif "channel_post" in update:
            self._handle_channel_post(client, settings, update["channel_post"])
        elif "callback_query" in update:
            self._handle_callback(client, settings, update["callback_query"])

    def _prime_offset(self, client):
        result = client.get_updates(offset=-1, timeout=0)
        if not result.get("ok"):
            return result
        updates = result.get("updates", []) or []
        if updates:
            self.offset = int(updates[-1].get("update_id", 0)) + 1
        else:
            self.offset = None
        self.offset_primed = True
        return {"ok": True, "skipped": len(updates)}

    def _loop(self):
        while self.running:
            settings = self.refresh_settings()
            if not settings.get("control_enabled"):
                time.sleep(2)
                continue
            client = self._client(settings)
            if not client.enabled():
                time.sleep(5)
                continue
            if not self.offset_primed:
                result = self._prime_offset(client)
                if not result.get("ok"):
                    self.log(f"[TELEGRAM CONTROL] offset prime failed: {result.get('error')}", error=True)
                    time.sleep(5)
                    continue
                skipped = int(result.get("skipped", 0) or 0)
                if skipped:
                    self.log(f"[TELEGRAM CONTROL] skipped {skipped} pending old update(s) on startup.")
                time.sleep(float(settings.get("control_poll_interval_seconds", 2.0) or 2.0))
                continue
            result = client.get_updates(offset=self.offset, timeout=15)
            if not result.get("ok"):
                self.log(f"[TELEGRAM CONTROL] getUpdates failed: {result.get('error')}", error=True)
                time.sleep(5)
                continue
            for update in result.get("updates", []):
                self.offset = int(update.get("update_id", 0)) + 1
                try:
                    self.process_update(client, settings, update)
                except Exception as exc:
                    self.log(f"[TELEGRAM CONTROL] update error: {exc}", error=True)
            time.sleep(float(settings.get("control_poll_interval_seconds", 2.0) or 2.0))
