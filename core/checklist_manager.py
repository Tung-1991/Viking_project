# -*- coding: utf-8 -*-
# FILE: core/checklist_manager.py
# Checklist Manager V4.4: Auto-detect Loss Mode & Smart UI Display

import config
import time
from core.data_engine import data_engine
from core.market_hours import is_symbol_trade_window_open
from core.position_classifier import is_bot_position, is_manual_position
from core.storage_manager import (
    apply_state_defaults,
    get_active_safeguard_brake,
    mark_safeguard_brake,
    release_expired_safeguard_brakes,
    rollover_daily_session,
    save_state,
)


class ChecklistManager:
    def __init__(self, connector):
        self.connector = connector

    def run_pre_trade_checks(
        self, account_info, state, symbol, strict_mode=True
    ) -> dict:
        checks = []
        all_passed = True

        # 1. Connection & Ping & Spread Check
        if self.connector._is_connected:
            # --- A. CHECK PING ---
            ping_ms = 0
            ping_status = "OK"

            try:
                max_ping = config.MAX_PING_MS
            except AttributeError:
                max_ping = 150

            if ping_ms > max_ping:
                ping_status = "WARN"
                if strict_mode:
                    all_passed = False
                    ping_status = "FAIL"

            # --- B. CHECK SPREAD ---
            tick = data_engine._last_tick.get(symbol)
            spread_points = 0
            if tick and "spread" in tick:
                spread_points = tick["spread"]

            try:
                max_spread = config.MAX_SPREAD_POINTS
            except AttributeError:
                max_spread = 5

            # Format tin nhắn hiển thị chi tiết
            spread_msg = f"Ping {ping_ms:.0f}ms (Max {max_ping}) | Spr {spread_points:.2f} (Max {max_spread})"

            if spread_points > max_spread:
                if strict_mode:
                    checks.append(
                        {"name": "Mạng/Spread", "status": "FAIL", "msg": spread_msg}
                    )
                    all_passed = False
                else:
                    checks.append(
                        {"name": "Mạng/Spread", "status": "WARN", "msg": spread_msg}
                    )
            elif ping_status == "FAIL":
                checks.append(
                    {"name": "Mạng/Spread", "status": "FAIL", "msg": spread_msg}
                )
            else:
                checks.append(
                    {"name": "Mạng/Spread", "status": "OK", "msg": spread_msg}
                )

        else:
            checks.append(
                {"name": "Kết nối DNSE", "status": "FAIL", "msg": "Mất kết nối Server"}
            )
            all_passed = False

        # 2. Account Check
        if not account_info:
            checks.append(
                {
                    "name": "Dữ liệu TK",
                    "status": "FAIL",
                    "msg": "Không lấy được dữ liệu",
                }
            )
            return {"passed": False, "checks": checks}

        if state["starting_balance"] == 0:
            state["starting_balance"] = account_info.get("balance", 0)

        # 3. Daily Loss Check ($/%)
        start_bal = state["starting_balance"]
        pnl_today = state.get("manual_pnl_today", 0.0)
        loss_pct = (pnl_today / start_bal * 100) if start_bal > 0 else 0
        max_loss_limit = -config.MAX_DAILY_LOSS_PERCENT

        loss_msg = f"{loss_pct:.2f}% (Limit {max_loss_limit}%)"

        if loss_pct <= max_loss_limit:
            checks.append({"name": "Daily Loss", "status": "FAIL", "msg": loss_msg})
            all_passed = False
        else:
            # Cảnh báo vàng nếu sắp chạm trần (còn cách 0.5%)
            if loss_pct <= (max_loss_limit + 0.5):
                checks.append({"name": "Daily Loss", "status": "WARN", "msg": loss_msg})
            else:
                checks.append({"name": "Daily Loss", "status": "OK", "msg": loss_msg})

        # 4. [UPDATED] Losing Trades Check (Hiển thị Mode Streak hay Total)
        current_losses = state.get("manual_daily_loss_count", 0)
        max_allowed_losses = config.MAX_LOSING_STREAK

        # Tự động lấy tên Mode để hiển thị cho Boss biết
        mode_name = getattr(config, "LOSS_COUNT_MODE", "TOTAL")

        # Ví dụ hiển thị: "[Total] 1 (Max 3)" hoặc "[Streak] 2 (Max 3)"
        loss_count_msg = f"[{mode_name}] {current_losses} (Max {max_allowed_losses})"

        if current_losses >= max_allowed_losses:
            checks.append(
                {"name": "Số Lệnh Thua", "status": "FAIL", "msg": loss_count_msg}
            )
            all_passed = False
        else:
            checks.append(
                {"name": "Số Lệnh Thua", "status": "OK", "msg": loss_count_msg}
            )

        # 5. Trades Today Check (Hiển thị Max)
        count = state.get("manual_trades_today", 0)
        max_trades = config.MAX_TRADES_PER_DAY
        trade_msg = f"{count} (Max {max_trades})"

        if count >= max_trades:
            checks.append({"name": "Số Lệnh", "status": "FAIL", "msg": trade_msg})
            all_passed = False
        else:
            checks.append({"name": "Số Lệnh", "status": "OK", "msg": trade_msg})

        # 6. Open Position Check
        positions = self.connector.get_all_open_positions()
        import core.storage_manager as storage_manager

        magics = storage_manager.get_magic_numbers()
        my_pos = [
            p for p in positions
            if is_manual_position(p, magics)
        ]

        try:
            max_open_pos = config.MAX_OPEN_POSITIONS
        except AttributeError:
            max_open_pos = 1

        pos_msg = f"Đang chạy: {len(my_pos)} (Max {max_open_pos})"

        if len(my_pos) >= max_open_pos:
            checks.append({"name": "Trạng thái", "status": "FAIL", "msg": pos_msg})
            all_passed = False
        else:
            checks.append({"name": "Trạng thái", "status": "OK", "msg": pos_msg})

        return {"passed": all_passed, "checks": checks}

    def run_bot_safeguard_checks(
        self, account_info, state, symbol, safeguard_cfg, signal_class="ENTRY",
        direction=None, priority_symbol=False,
    ) -> dict:
        apply_state_defaults(state)
        changed = rollover_daily_session(state)
        changed = release_expired_safeguard_brakes(state) or changed
        if changed:
            save_state(state)

        checks = []
        all_passed = True

        max_loss_pct = float(safeguard_cfg.get("MAX_DAILY_LOSS_PERCENT", 2.5))
        max_open = int(safeguard_cfg.get("MAX_OPEN_POSITIONS", 3))
        max_trades = int(safeguard_cfg.get("MAX_TRADES_PER_DAY", 30))
        max_streak = int(safeguard_cfg.get("MAX_LOSING_STREAK", 3))
        loss_mode = str(safeguard_cfg.get("LOSS_COUNT_MODE", "TOTAL")).upper()
        brake_mode = str(safeguard_cfg.get("GLOBAL_BRAKE_MODE", "Mode 1: Total Freeze"))
        is_symbol_brake_mode = "Mode 2" in brake_mode
        cooldown_hours = float(safeguard_cfg.get("GLOBAL_COOLDOWN_HOURS", 4.0))

        # Giới hạn số lệnh trên mỗi Symbol (Mặc định 1 lệnh ENTRY cho mỗi coin)
        max_per_symbol = int(safeguard_cfg.get("MAX_POS_PER_SYMBOL", 1))
        max_same_direction = 0

        check_ping = safeguard_cfg.get("CHECK_PING", True)
        max_ping = int(safeguard_cfg.get("MAX_PING_MS", 150))
        check_spread = safeguard_cfg.get("CHECK_SPREAD", True)
        # Spread DNSE = khoảng giá (điểm PS / nghìn VND CS), không phải points MT5 -> ngưỡng thang nhỏ.
        max_spread = float(safeguard_cfg.get("MAX_SPREAD_POINTS", 5))

        # [NEW] Đọc cấu hình riêng lẻ của từng cặp tiền (nếu có)
        import json, os
        import core.storage_manager as storage_manager

        cfg_path = storage_manager.BRAIN_FILE
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    sym_cfgs = json.load(f).get("symbol_configs", {})
                    if symbol in sym_cfgs:
                        sc = sym_cfgs[symbol]
                        max_per_symbol = sc.get("max_orders", max_per_symbol)
                        max_same_direction = int(
                            sc.get("max_same_direction_orders", 0) or 0
                        )
                        max_ping = sc.get("max_ping", max_ping)
                        max_spread = sc.get("max_spread", max_spread)
        except:
            pass

        if not self.connector._is_connected:
            return {
                "passed": False,
                "checks": [
                    {"name": "Kết nối", "status": "FAIL", "msg": "Mất kết nối DNSE"}
                ],
            }

        is_open, closed_reason = is_symbol_trade_window_open(symbol)
        if not is_open:
            checks.append(
                {"name": "Market Hours", "status": "FAIL", "msg": closed_reason}
            )
            all_passed = False

        cooldown_until = state.get("cooldown_until", 0.0)
        now = time.time()
        if now < cooldown_until:
            rem_minutes = int((cooldown_until - now) / 60)
            return {
                "passed": False,
                "checks": [
                    {
                        "name": "Global Cooldown",
                        "status": "FAIL",
                        "msg": f"Bot bị chặn. Mở lại sau {rem_minutes} phút",
                    }
                ],
            }

        global_brake = get_active_safeguard_brake(state, "GLOBAL", now=now)
        if global_brake:
            state["cooldown_until"] = float(global_brake.get("until", 0.0))
            rem_minutes = int((state["cooldown_until"] - now) / 60)
            save_state(state)
            return {
                "passed": False,
                "checks": [
                    {
                        "name": "Global Cooldown",
                        "status": "FAIL",
                        "msg": f"Bot bị chặn safeguard. Mở lại sau {rem_minutes} phút",
                    }
                ],
            }

        symbol_brake = get_active_safeguard_brake(state, "SYMBOL", symbol=symbol, now=now)
        if is_symbol_brake_mode and symbol_brake:
            rem_seconds = max(0, int(float(symbol_brake.get("until", 0.0)) - now))
            msg = (
                f"{symbol} cách ly safeguard (Còn {rem_seconds//60}m {rem_seconds%60}s)"
                if rem_seconds >= 60
                else f"{symbol} cách ly safeguard (Còn {rem_seconds}s)"
            )
            return {
                "passed": False,
                "checks": [{"name": "Isolation", "status": "FAIL", "msg": msg}],
            }

        if not account_info:
            return {
                "passed": False,
                "checks": [
                    {
                        "name": "Dữ liệu",
                        "status": "FAIL",
                        "msg": "Không có Account Info",
                    }
                ],
            }

        if check_ping:
            ping_ms = float(getattr(self.connector, "last_latency_ms", 0.0) or 0.0)
            if ping_ms > max_ping:
                checks.append(
                    {
                        "name": "Ping",
                        "status": "FAIL",
                        "msg": f"Ping {ping_ms:.0f}ms vượt mức {max_ping}ms",
                    }
                )
                all_passed = False

        if check_spread:
            tick = data_engine._last_tick.get(symbol)
            spread_points = 0
            if isinstance(tick, dict):
                spread_points = float(tick.get("spread", 0.0) or 0.0)
            elif tick:
                spread_points = float(getattr(tick, "spread", 0.0) or 0.0)
            if spread_points > max_spread:
                checks.append(
                    {
                        "name": "Spread",
                        "status": "FAIL",
                        "msg": f"Spread {spread_points:.2f} vượt mức {max_spread}",
                    }
                )
                all_passed = False

        if state["starting_balance"] == 0:
            state["starting_balance"] = account_info.get("balance", 0)
        start_bal = state["starting_balance"]
        pnl_today = state.get("bot_pnl_today", 0.0)
        loss_pct = (pnl_today / start_bal * 100) if start_bal > 0 else 0
        threshold_reason = None
        trigger_losses = 0
        if loss_pct <= -max_loss_pct:
            threshold_reason = f"Chạm Max Loss ({loss_pct:.2f}% / {max_loss_pct}%)"
            checks.append(
                {
                    "name": "Daily Loss",
                    "status": "FAIL",
                    "msg": f"Lỗ {loss_pct:.2f}% chạm trần {max_loss_pct}%",
                }
            )
            all_passed = False

        count = state.get("bot_trades_today", 0)
        if count >= max_trades:
            if threshold_reason is None:
                threshold_reason = f"Chạm Max Trades ({count}/{max_trades})"
            checks.append(
                {
                    "name": "Số Lệnh",
                    "status": "FAIL",
                    "msg": f"Bot đã đánh {count} lệnh (Max {max_trades})",
                }
            )
            all_passed = False

        current_losses = (
            state.get("bot_symbol_losing_streak", {}).get(symbol, 0)
            if loss_mode == "STREAK"
            else state.get("bot_daily_loss_count", 0)
        )
        trigger_losses = current_losses
        if current_losses >= max_streak:
            if threshold_reason is None:
                scope_label = symbol if loss_mode == "STREAK" and symbol else "BOT"
                threshold_reason = f"Chạm Max {loss_mode} Loss {scope_label} ({current_losses}/{max_streak})"
            checks.append(
                {
                    "name": "Lệnh Thua",
                    "status": "FAIL",
                    "msg": f"[{loss_mode}] {current_losses} lệnh thua (Max {max_streak})",
                }
            )
            all_passed = False

        if threshold_reason:
            cooldown_time = now + (cooldown_hours * 3600)
            trigger_snapshot = {
                "loss_pct": loss_pct,
                "trades": count,
                "losses": trigger_losses,
                "loss_mode": loss_mode,
                "max_loss_pct": max_loss_pct,
                "max_trades": max_trades,
                "max_streak": max_streak,
            }
            if is_symbol_brake_mode and symbol:
                item, _created = mark_safeguard_brake(
                    state,
                    "SYMBOL",
                    threshold_reason,
                    cooldown_time,
                    symbol=symbol,
                    trigger=trigger_snapshot,
                )
                state.setdefault("bot_last_fail_times", {})[symbol] = float(
                    item.get("until", cooldown_time)
                )
            else:
                item, _created = mark_safeguard_brake(
                    state,
                    "GLOBAL",
                    threshold_reason,
                    cooldown_time,
                    trigger=trigger_snapshot,
                )
                state["cooldown_until"] = float(item.get("until", cooldown_time))
            save_state(state)

        positions = self.connector.get_all_open_positions()
        import core.storage_manager as storage_manager

        magics = storage_manager.get_magic_numbers()
        all_bot_pos = [p for p in positions if is_bot_position(p, magics)]

        # [KAISER FIX] Chỉ đếm các lệnh Gốc (ENTRY), bỏ qua lệnh con (DCA/PCA) khi check giới hạn
        parent_bot_pos = [
            p
            for p in all_bot_pos
            if "_AUTO_DCA" not in p.comment and "_AUTO_PCA" not in p.comment
        ]

        # [V5.2] Kiểm tra Cooldown CÁCH LY (Dùng chung cho cả ENTRY/DCA/PCA)
        # Mode 2 lưu cooldown_time (timestamp tương lai) vào bot_last_fail_times → So sánh trực tiếp
        isolation_deadline = state.get("bot_last_fail_times", {}).get(symbol, 0)
        now = time.time()

        try:
            cooldown_min = float(safeguard_cfg.get("COOLDOWN_MINUTES", 1.0))
        except:
            cooldown_min = 1.0

        if is_symbol_brake_mode and isolation_deadline > now:
            # Phạt nặng Mode 2: vẫn còn trong thời gian cách ly
            rem_fail = int(isolation_deadline - now)
            msg = f"{symbol} cách ly (Còn {rem_fail//60}m {rem_fail%60}s)" if rem_fail >= 60 else f"{symbol} cách ly (Còn {rem_fail}s)"
            checks.append({"name": "Isolation", "status": "FAIL", "msg": msg})
            all_passed = False
        elif is_symbol_brake_mode and isolation_deadline > 0 and (now - isolation_deadline) < (cooldown_min * 60):
            # Phạt nhẹ: lưu timestamp quá khứ, kiểm tra theo COOLDOWN_MINUTES
            rem_fail = int((cooldown_min * 60) - (now - isolation_deadline))
            checks.append({"name": "Fail Cooldown", "status": "FAIL", "msg": f"{symbol} vừa lỗi kỹ thuật (Còn {rem_fail}s)"})
            all_passed = False


        if signal_class == "ENTRY":
            # 1. Kiểm tra tổng số lệnh Bot (Chỉ tính lệnh Gốc)
            if len(parent_bot_pos) >= max_open and not priority_symbol:
                checks.append(
                    {
                        "name": "Trạng thái",
                        "status": "FAIL",
                        "msg": f"Tổng Bot đang chạy {len(parent_bot_pos)} lệnh gốc (Max {max_open})",
                    }
                )
                all_passed = False
            elif len(parent_bot_pos) >= max_open and priority_symbol:
                checks.append(
                    {
                        "name": "Priority",
                        "status": "OK",
                        "msg": (
                            f"{symbol} là mã PRIORITY: bỏ qua giới hạn tổng "
                            f"{len(parent_bot_pos)}/{max_open}"
                        ),
                    }
                )

            # 2. Kiểm tra giới hạn riêng cho từng Symbol (Chỉ tính lệnh Gốc)
            symbol_parent_pos = [p for p in parent_bot_pos if p.symbol == symbol]
            if len(symbol_parent_pos) >= max_per_symbol:
                checks.append(
                    {
                        "name": "Symbol Limit",
                        "status": "FAIL",
                        "msg": f"{symbol} đã có {len(symbol_parent_pos)} lệnh gốc (Max {max_per_symbol})",
                    }
                )
                all_passed = False

            # 3. Optional same-direction cap. 0 = unlimited within max_orders.
            # This lets a symbol allow multiple entries while avoiding one-side overstacking.
            if direction:
                direction = str(direction).upper()
                dir_type = 0 if direction == "BUY" else 1
                if max_same_direction > 0:
                    same_dir_pos = [p for p in symbol_parent_pos if p.type == dir_type]
                    if len(same_dir_pos) >= max_same_direction:
                        checks.append(
                            {
                                "name": "Direction Limit",
                                "status": "FAIL",
                                "msg": f"{symbol} đã có {len(same_dir_pos)} lệnh {direction} gốc cùng chiều (Max {max_same_direction})",
                            }
                        )
                        all_passed = False

            # [NEW] Kiểm tra Cooldown (Thời gian nghỉ giữa 2 lệnh ENTRY của cùng 1 coin)
            last_entry = state.get("bot_last_entry_times", {}).get(symbol, 0)
            elapsed_sec = time.time() - last_entry

            if elapsed_sec < (cooldown_min * 60):
                rem_sec = int((cooldown_min * 60) - elapsed_sec)
                checks.append(
                    {
                        "name": "Cooldown",
                        "status": "FAIL",
                        "msg": f"{symbol} đang nghỉ (Còn {rem_sec}s)",
                    }
                )
                all_passed = False

            # [NEW V4.4] Kiểm tra Post-Close Cooldown (Nghỉ sau khi vừa đóng lệnh)
            post_close_cd = int(safeguard_cfg.get("POST_CLOSE_COOLDOWN", 0))
            if post_close_cd > 0:
                last_close = state.get("last_close_times", {}).get(symbol, 0)
                elapsed_close = time.time() - last_close
                if elapsed_close < post_close_cd:
                    rem_close = int(post_close_cd - elapsed_close)
                    checks.append(
                        {
                            "name": "Post-Close",
                            "status": "FAIL",
                            "msg": f"{symbol} vừa đóng lệnh (Còn {rem_close}s)",
                        }
                    )
                    all_passed = False

        return {"passed": all_passed, "checks": checks}
