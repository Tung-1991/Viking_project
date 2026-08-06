# -*- coding: utf-8 -*-
# FILE: core/signal_listener.py
# V4.4 (FINAL): SIGNAL ROUTER - HANDLING ENTRY, DCA, PCA & REVERSE CLOSE (KAISER EDITION)

import json
import os
import time
import threading
import logging
from copy import deepcopy
from typing import Callable, Any

import config
from core.money import format_vnd_full

logger = logging.getLogger("SignalListener")

def _get_signal_file():
    """Lấy đường dẫn live_signals.json động theo Account Workspace."""
    try:
        import core.storage_manager as sm
        return os.path.join(sm._active_account_dir, "live_signals.json")
    except:
        return os.path.join("data", "live_signals.json")

def _get_brain_file():
    """Lấy đường dẫn brain_settings.json động theo Account Workspace."""
    try:
        import core.storage_manager as sm
        return sm.BRAIN_FILE
    except:
        return os.path.join("data", "brain_settings.json")


def _get_telegram_signal_phase_file():
    try:
        import core.storage_manager as sm
        return os.path.join(sm._active_account_dir, "telegram_signal_phases.json")
    except:
        return os.path.join("data", "telegram_signal_phases.json")


class SignalListener:
    def __init__(
        self,
        trade_manager: Any,
        get_auto_trade_cb: Callable[[], bool],
        get_preset_cb: Callable[[], str],  # [ĐÃ FIX] Thêm tham số này để không bị Crash
        get_tsl_mode_cb: Callable[
            [], str
        ],  # [ĐÃ FIX] Thêm tham số này để không bị Crash
        ui_heartbeat_cb: Callable[[dict], None],
        log_cb: Callable[[str, bool], None],
        build_opportunity_plan_cb: Callable[..., dict] | None = None,
    ):
        """
        Lắng nghe và điều phối tín hiệu từ Daemon.
        """
        self.trade_manager = trade_manager
        self.get_auto_trade = get_auto_trade_cb
        self.get_preset = get_preset_cb  # [ĐÃ FIX] Khởi tạo biến
        self.get_tsl_mode = get_tsl_mode_cb  # [ĐÃ FIX] Khởi tạo biến
        self.update_ui_heartbeat = ui_heartbeat_cb
        self.log_ui = log_cb
        self.build_opportunity_plan = build_opportunity_plan_cb

        self.running = False
        self.thread = None

        # Lưu UUID các tín hiệu đã xử lý để tránh lặp lệnh
        self.processed_signals = set()
        self.last_thinking_signal = {}  # [NEW] Track để chống spam log

        # [NEW V4.3.1] Trí nhớ dài hạn cho Safeguard Log
        self.last_safeguard_reason = {}
        self.last_safeguard_time = {}
        self.last_bot_log_time = {}
        self.last_telegram_signal_proposal_action = {}

    def _build_opportunity_setup(self, symbol, action, context, market_mode):
        """Build daemon suggestions from the BOT order-plan source."""
        if callable(self.build_opportunity_plan):
            setup = self.build_opportunity_plan(
                symbol,
                action,
                context=context,
                market_mode=market_mode,
            )
            if not isinstance(setup, dict):
                setup = {"ok": False, "error": "BOT_PLAN_INVALID_RESULT"}
            setup = dict(setup)
            setup["plan_source"] = "BOT"
            setup["plan_version"] = 1
            return setup
        # Không quay lại bộ dựng Manual Preview. Telegram phải đọc đúng kế
        # hoạch BOT; thiếu nguồn BOT thì bỏ thông báo thay vì tự dựng giá.
        return {
            "ok": False,
            "error": "BOT_PLAN_SOURCE_UNAVAILABLE",
            "plan_source": "BOT",
            "plan_version": 1,
        }

    def _record_and_queue_opportunity(
        self,
        signal,
        action,
        context,
        market_mode,
        *,
        block_reason,
    ):
        """Store/send an ENTRY signal independently from order execution."""
        if str(signal.get("signal_class") or "ENTRY").upper() != "ENTRY":
            return None
        try:
            from core.signal_opportunities import record_signal
            from telegram_notify.opportunity_alerts import queue_opportunity

            setup = self._build_opportunity_setup(
                signal.get("symbol"),
                action,
                context=context,
                market_mode=market_mode,
            )
            opportunity = record_signal(
                signal,
                block_reason=str(block_reason or ""),
                order_setup=setup,
            )
            if opportunity:
                # Kho thống kê giữ nguyên plan đầu tiên để chấm kết quả giả lập.
                # Telegram phải dùng BOT plan vừa tính; plan WAIT/BLOCK hiện tại
                # không được phép rơi về Entry/SL/TP cũ đã bị freeze.
                notification = deepcopy(opportunity)
                notification["order_setup"] = deepcopy(setup)
                queue_opportunity(
                    notification,
                    log_cb=lambda msg, error=False: self.log_ui(
                        msg,
                        error=error,
                    ),
                )
            return opportunity
        except Exception as exc:
            logger.error(f"[Listener] Store/send BOT opportunity failed: {exc}")
            return None

    def _auto_trade_for(self, symbol) -> bool:
        """Cờ bật-bot theo nhóm mã. Tương thích ngược:
        - callback mới: get_auto_trade(symbol) -> bool (gate riêng CKPS / CKCS)
        - callback cũ:  get_auto_trade()       -> bool (1 cờ tổng)
        """
        try:
            return bool(self.get_auto_trade(symbol))
        except TypeError:
            return bool(self.get_auto_trade())

    def start(self):
        if not self.running:
            self._prime_existing_signals()
            self.running = True
            self.thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.thread.start()
            self.log_ui(
                "📡 Signal Listener Online. Đang đợi tín hiệu từ Brain...", error=False
            )

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)

    def _prime_existing_signals(self):
        signal_file = _get_signal_file()
        if not os.path.exists(signal_file):
            return
        try:
            with open(signal_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return

        now = time.time()
        for sig in payload.get("pending_signals", []) or []:
            sig_id = sig.get("signal_id")
            if sig_id:
                self.processed_signals.add(sig_id)

            signal_class = str(sig.get("signal_class") or "ENTRY").upper()
            symbol = str(sig.get("symbol") or "").upper()
            action = str(sig.get("action") or "").upper()
            if signal_class != "ENTRY":
                continue
            if not symbol:
                continue

            try:
                sig_time = float(sig.get("timestamp", 0) or 0)
                valid_for = float(sig.get("valid_for", 300) or 300)
            except Exception:
                continue
            if sig_time and now - sig_time > valid_for:
                continue

            if action == "NONE":
                self.last_telegram_signal_proposal_action.pop(symbol, None)
                self._save_telegram_signal_phase(symbol, "")
            elif action in {"BUY", "SELL"}:
                self.last_telegram_signal_proposal_action[symbol] = action
                self._save_telegram_signal_phase(symbol, action)

    def _load_telegram_signal_phases(self):
        path = _get_telegram_signal_phase_file()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_telegram_signal_phase(self, symbol: str, action: str):
        symbol_key = str(symbol or "").upper()
        if not symbol_key:
            return

        data = self._load_telegram_signal_phases()
        if action:
            data[symbol_key] = str(action).upper()
        else:
            data.pop(symbol_key, None)

        path = _get_telegram_signal_phase_file()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception as exc:
            logger.error(f"[Listener] Telegram signal phase save error: {exc}")

    def _listen_loop(self):
        while self.running:
            try:
                signal_file = _get_signal_file()
                if not os.path.exists(signal_file):
                    time.sleep(0.5)
                    continue

                # Đọc file với try-except bắt lỗi JSON đang ghi dở từ Daemon
                # [FIX V4.3.2] Xử lý tranh chấp file (Permission denied) trên Windows
                try:
                    with open(signal_file, "r", encoding="utf-8") as f:
                        try:
                            payload = json.load(f)
                        except json.JSONDecodeError:
                            time.sleep(0.1)
                            continue
                except PermissionError:
                    time.sleep(0.1)  # File đang bị Daemon ghi, đợi một chút rồi thử lại
                    continue

                # 1. Cập nhật Heartbeat (Sync Context) lên UI
                heartbeat = payload.get("brain_heartbeat", {})
                if heartbeat:
                    self.update_ui_heartbeat(heartbeat)

                # 2. Quét tín hiệu chờ (Signals)
                pending_signals = payload.get("pending_signals", [])
                for sig in pending_signals:
                    sig_id = sig.get("signal_id")

                    if not sig_id or sig_id in self.processed_signals:
                        continue

                    self.processed_signals.add(sig_id)

                    # Kiểm tra TTL (Tránh đánh lệnh cũ nếu Bot UI vừa bật lên)
                    sig_time = sig.get("timestamp", 0)
                    valid_for = sig.get("valid_for", 300)
                    if time.time() - sig_time > valid_for:
                        logger.debug(
                            f"Bỏ qua tín hiệu {sig.get('action')} {sig.get('symbol')}: Đã hết hạn."
                        )
                        continue

                    # Thực thi
                    self._process_signal(sig)

            except Exception as e:
                logger.error(f"[Listener] Lỗi vòng lặp: {e}")

            time.sleep(0.5)  # Quét nhanh mỗi 0.5s để bắt nhịp DCA/PCA

    def _get_log_cooldown_seconds(self) -> float:
        try:
            cpath = _get_brain_file()
            cooldown_min = float(
                getattr(config, "BOT_SAFEGUARD", {}).get("LOG_COOLDOWN_MINUTES", 60.0)
            )
            if os.path.exists(cpath):
                with open(cpath, "r", encoding="utf-8") as cf:
                    b_set = json.load(cf)
                    safe_cfg = b_set.get("bot_safeguard", {})
                    cooldown_min = float(
                        safe_cfg.get(
                            "LOG_COOLDOWN_MINUTES",
                            cooldown_min,
                        )
                    )
        except Exception:
            cooldown_min = float(
                getattr(config, "BOT_SAFEGUARD", {}).get("LOG_COOLDOWN_MINUTES", 60.0)
            )
        return max(0.0, cooldown_min * 60.0)

    def _should_log_bot_status(self, key: str) -> bool:
        now = time.time()
        last_log = self.last_bot_log_time.get(key, 0)
        if now - last_log < self._get_log_cooldown_seconds():
            return False

        self.last_bot_log_time[key] = now
        return True

    def _should_send_telegram_signal_proposal(self, symbol: str, action: str) -> bool:
        symbol_key = str(symbol or "").upper()
        action_key = str(action or "").upper()
        if not symbol_key or action_key not in {"BUY", "SELL"}:
            return False

        phases = self._load_telegram_signal_phases()
        last_action = phases.get(symbol_key) or self.last_telegram_signal_proposal_action.get(symbol_key)
        if last_action == action_key:
            return False

        self.last_telegram_signal_proposal_action[symbol_key] = action_key
        self._save_telegram_signal_phase(symbol_key, action_key)
        return True

    def _process_signal(self, signal: dict):
        """Xử lý định tuyến tín hiệu vào TradeManager"""
        action = signal.get("action")
        symbol = signal.get("symbol")
        sig_class = signal.get("signal_class", "ENTRY")
        context = signal.get("context", {})
        market_mode = signal.get("market_mode", "ANY")

        # Telegram opportunity remains a market-signal feed.  Position alerts
        # are separate and are emitted only for positions that are already open.
        try:
            from telegram_notify.position_alerts import observe_reversal

            open_positions = self.trade_manager.connector.get_all_open_positions()
            current_price = float(
                context.get(
                    "current_price",
                    context.get("last", context.get("bid", context.get("ask", 0.0))),
                )
                or 0.0
            )
            observe_reversal(
                open_positions,
                symbol,
                action,
                current_price=current_price,
            )
        except Exception as exc:
            logger.error(f"[Listener] Position Telegram alert error: {exc}")

        # [NEW V4.4 FINAL] LƯỚI LỌC 0: TỰ ĐỘNG CẮT LỆNH ĐẢO CHIỀU (CLOSE ON REVERSE)
        # Chạy độc lập với AUTO-TRADE. Bất cứ lệnh nào đang ôm tactic REVERSE_CLOSE
        # đều sẽ bị chém ngay lập tức khi có tín hiệu ngược (bảo vệ tài khoản).
        try:
            # REV_C close ownership lives in TradeManager._check_recovery().
            positions = []
            opposite_type = 1 if action == "BUY" else 0  # 1 = SELL, 0 = BUY

            for p in positions:
                b_set = {}
                try:
                    from core.storage_manager import get_brain_settings_for_symbol
                    b_set = get_brain_settings_for_symbol(symbol)
                except Exception:
                    pass

                allow_none_close = b_set.get("bot_safeguard", {}).get("REV_CLOSE_ON_NONE", False)
                is_reverse_match = (
                    action == "NONE" and allow_none_close
                ) or (
                    action != "NONE" and p.type == opposite_type
                )

                if p.symbol == symbol and is_reverse_match:
                    tactic = self.trade_manager.get_trade_tactic(p.ticket)
                    if "REV_C" in tactic or "REVERSE_CLOSE" in tactic:
                        hold_time = time.time() - p.time

                        # Đọc thông số chống nhiễu (Min Hold Time + Min PnL)
                        min_hold = 180.0
                        try:
                            min_hold = float(b_set.get("bot_safeguard", {}).get("CLOSE_ON_REVERSE_MIN_TIME", 180))
                        except Exception:
                            pass

                        profit_usd = p.profit + p.swap + getattr(p, "commission", 0.0)

                        # [NEW V4.4] REFINED PNL CHECK
                        pnl_ok = True
                        safe_cfg = b_set.get("bot_safeguard", {})
                        if safe_cfg.get("CLOSE_ON_REVERSE_USE_PNL", True):
                            equity = 0.0
                            try:
                                acc = self.trade_manager.connector.get_account_info()
                                equity = acc.get("equity", 0.0) if acc else 0.0
                            except Exception:
                                pass
                            min_profit = self.trade_manager._resolve_money_value(
                                safe_cfg.get("REV_CLOSE_MIN_PROFIT", 0.0),
                                safe_cfg.get("REV_CLOSE_MIN_PROFIT_UNIT", "USD"),
                                pos=p,
                                equity=equity,
                            )
                            max_loss = -abs(
                                self.trade_manager._resolve_money_value(
                                    safe_cfg.get("REV_CLOSE_MAX_LOSS", 0.0),
                                    safe_cfg.get("REV_CLOSE_MAX_LOSS_UNIT", "USD"),
                                    pos=p,
                                    equity=equity,
                                )
                            )

                            if profit_usd >= 0:
                                # [REFINED] Nếu đang lãi, kiểm tra lợi nhuận tối thiểu để cắt (Tránh phí)
                                pnl_ok = (profit_usd >= min_profit) if min_profit > 0 else True
                            else:
                                # [REFINED] Nếu đang lỗ, chỉ cắt nếu lỗ chưa vượt quá giới hạn (Chống cắt đáy)
                                # Ví dụ: -5 >= -10 là True (Cắt), -15 >= -10 là False (Giữ)
                                # Current behavior: -15 > -20 -> hold, -20 <= -20 -> close.
                                pnl_ok = (profit_usd <= max_loss) if max_loss != 0 else True

                        if hold_time >= min_hold and pnl_ok:
                            self.log_ui(
                                f"  [REVERSE TACTIC] Đảo chiều {action} | PnL: {format_vnd_full(profit_usd, signed=True)} -> Đóng lệnh #{p.ticket}",
                                False,
                            )

                            # Lưu lý do thoát lệnh để ghi log CSV
                            if "exit_reasons" not in self.trade_manager.state:
                                self.trade_manager.state["exit_reasons"] = {}
                            self.trade_manager.state["exit_reasons"][str(p.ticket)] = (
                                f"Reverse_to_{action}"
                            )

                            threading.Thread(
                                target=self.trade_manager.connector.close_position,
                                args=(p,),
                                daemon=True,
                            ).start()
                        else:
                            reason = "HoldTime" if hold_time < min_hold else "PnL_Filter"
                            if self._should_log_bot_status(
                                f"reverse_hold_{symbol}_{p.ticket}_{reason}"
                            ):
                                self.log_ui(
                                    f"⏳ [REVERSE TACTIC] Tín hiệu {action} nhưng giữ lệnh #{p.ticket} ({reason} | PnL: {format_vnd_full(profit_usd, signed=True)})",
                                    False,
                                )
        except Exception as e:
            logger.error(f"[Listener] Lỗi Reverse Check: {e}")

        # Dùng đúng giá daemon đã có để theo dõi gợi ý giả lập; không gọi thêm API
        # và hoàn toàn không tạo lệnh PAPER/REAL.
        try:
            from core.signal_opportunities import observe_price

            observed_price = float(
                context.get(
                    "current_price",
                    context.get("last", context.get("bid", context.get("ask", 0.0))),
                )
                or 0.0
            )
            if observed_price > 0:
                observe_price(symbol, observed_price)
        except Exception:
            pass

        if action == "NONE":
            self.last_telegram_signal_proposal_action.pop(str(symbol or "").upper(), None)
            self._save_telegram_signal_phase(symbol, "")
            try:
                from telegram_notify.opportunity_alerts import mark_wait

                mark_wait(symbol)
            except Exception:
                pass
            return

        if not self._auto_trade_for(symbol):
            if str(sig_class or "ENTRY").upper() == "ENTRY":
                self._record_and_queue_opportunity(
                    signal,
                    action,
                    context,
                    market_mode,
                    block_reason="BOT_OFF",
                )
            try:
                from telegram_notify.signal_bridge import maybe_send_signal_proposal

                if sig_class == "ENTRY" and self._should_send_telegram_signal_proposal(symbol, action):
                    maybe_send_signal_proposal(
                        self.trade_manager,
                        signal,
                        enforce_cooldown=False,
                        log_cb=lambda msg, error=False: self.log_ui(msg, error=error),
                    )
            except Exception as exc:
                logger.error(f"[Listener] Telegram signal proposal error: {exc}")

            try:
                cpath = _get_brain_file()
                manual_log_enable = getattr(config, "BOT_SAFEGUARD", {}).get(
                    "MANUAL_SIGNAL_LOG_ENABLE", False
                )
                manual_log_cooldown = float(
                    getattr(config, "BOT_SAFEGUARD", {}).get(
                        "LOG_COOLDOWN_MINUTES", 60.0
                    )
                )
                if os.path.exists(cpath):
                    with open(cpath, "r", encoding="utf-8") as cf:
                        b_set = json.load(cf)
                        safe_cfg = b_set.get("bot_safeguard", {})
                        manual_log_enable = safe_cfg.get(
                            "MANUAL_SIGNAL_LOG_ENABLE", manual_log_enable
                        )
                        manual_log_cooldown = float(
                            safe_cfg.get(
                                "LOG_COOLDOWN_MINUTES",
                                manual_log_cooldown,
                            )
                        )
            except Exception:
                manual_log_enable = False
                manual_log_cooldown = 60.0

            if not manual_log_enable:
                return

            # [FIX] Thinking Logs (Chống spam)
            now = time.time()
            last_sig = self.last_thinking_signal.get(symbol, {"action": "", "time": 0})

            cooldown_s = max(0.0, manual_log_cooldown * 60.0)
            if last_sig["action"] != action or (now - last_sig["time"] > cooldown_s):
                self.log_ui(
                    f"📡 [SIGNAL] Phát hiện {action} {symbol}. (Chế độ: MANUAL - Bỏ qua)",
                    error=False,
                )
                self.last_thinking_signal[symbol] = {"action": action, "time": now}
            return

        # Khi AUTO đang bật, reset lại tracker để khi tắt đi nó sẽ báo ngay
        if symbol in self.last_thinking_signal:
            del self.last_thinking_signal[symbol]

        signal_paper_mode = bool(getattr(config, "PAPER_TRADING", True))

        def run_bot_trade():
            if bool(getattr(config, "PAPER_TRADING", True)) != signal_paper_mode:
                self.log_ui("Bo tin hieu dang xu ly vi vua doi PAPER/REAL.", error=False)
                return
            auto_log_enabled = self._should_log_bot_status(
                f"signal_seen_{symbol}_{sig_class}_{action}"
            )
            if auto_log_enabled:
                self.log_ui(
                    f"📡 [SIGNAL] Bot nhận {sig_class}: {action} {symbol}. Đang kiểm tra safeguard...",
                    error=False,
                )
            # Truyền đẩy đủ Context và Signal Class sang TradeManager
            result = self.trade_manager.execute_bot_trade(
                direction=action,
                symbol=symbol,
                context=context,
                market_mode=market_mode,
                signal_class=sig_class,
                tactic_override=None,  # [FIX] Bot phải tự đọc bot_tsl từ Sandbox, KHÔNG dùng Panel TSL
                expected_paper_mode=signal_paper_mode,
            )

            if "SUCCESS" not in result:
                if str(sig_class or "ENTRY").upper() == "ENTRY":
                    self._record_and_queue_opportunity(
                        signal,
                        action,
                        context,
                        market_mode,
                        block_reason=str(result),
                    )
                # [FIX] Tường minh lý do lỗi
                if "SAFEGUARD_FAIL" in result:
                    parts = result.split("|")
                    reason_key = parts[1] if len(parts) > 1 else "UNK"
                    reason_msg = parts[2] if len(parts) > 2 else "Không xác định"

                    # [NEW] Chống Spam Log: CÓ THỜI GIAN COOLDOWN ĐỘNG (Lấy từ UI)
                    track_key = f"{symbol}_{sig_class}"

                    last_key = self.last_safeguard_reason.get(track_key, "")
                    last_time = self.last_safeguard_time.get(track_key, 0)
                    now = time.time()

                    is_cooldown_expired = (
                        now - last_time
                    ) >= self._get_log_cooldown_seconds()

                    # [KAISER FIX] Tránh edgecase khi reason flap (VD: Ping,MaxOrders -> MaxOrders) làm bypass cooldown
                    # Chúng ta sẽ block theo track_key (Symbol + Class). Chỉ báo log mới nếu HẾT COOLDOWN.
                    if is_cooldown_expired:
                        self.log_ui(
                            f"🤖 Bot định bóp cò {sig_class}: {action} {symbol} nhưng bị chặn!",
                            error=False,
                        )
                        self.log_ui(
                            f"⚠️ [BOT SAFEGUARD] Lý do: {reason_msg}", error=True
                        )

                        self.last_safeguard_reason[track_key] = reason_key
                        self.last_safeguard_time[track_key] = now
                else:
                    fail_key = f"bot_trade_fail_{symbol}_{sig_class}_{result}"
                    if self._should_log_bot_status(fail_key):
                        self.log_ui(
                            f"🤖 Bot chuẩn bị bóp cò {sig_class}: {action} {symbol}",
                            error=False,
                        )
                        self.log_ui(f"❌ Bot vào lệnh thất bại: {result}", error=True)
            else:
                # Telegram độc lập với kết quả đặt lệnh. Trước đây nhánh
                # SUCCESS không làm gì nên BOT vào lệnh nhưng nhóm Gợi ý im.
                self._record_and_queue_opportunity(
                    signal,
                    action,
                    context,
                    market_mode,
                    block_reason=str(result),
                )

        # Chạy Thread độc lập để Listener không bị kẹt
        threading.Thread(target=run_bot_trade, daemon=True).start()
