"""Read-only E/M milestones; calculated previews never authorize an order."""
from dataclasses import dataclass

from ..rules.business import StaticRuleParameters, protect_level, protect_peak_for_sellable_phase
from ..rules.observations import finite
from ..rules.protection import dynamic_floor_previous, policy_for_stage
from .view import _display_price, _number


@dataclass(frozen=True)
class Milestone:
    name: str
    target: float
    distance: float
    summary: str


@dataclass(frozen=True)
class EMStatus:
    line: str
    focus: str
    hint: str
    milestones: tuple[Milestone, ...]
    floor: float
    stage: str
    hit: bool
    e_hit: bool
    policy: str
    e_policy: str
    blocked: bool = False


def em_status(*, entry, price, params, details, metrics, ready, normal_enabled,
              indicator_enabled, normal_state, history_untrusted, signal, sl_price=0):
    rule = StaticRuleParameters.from_dict(params)
    arm_policy, dyn_policy = policy_for_stage(params, "ARM"), policy_for_stage(params, "DYN")
    e_policy = rule.indicator_exit_policy
    marks = details.get("indicators") or {}
    e_hit = bool(ready and indicator_enabled and signal == "SELL" and marks.get("signal_ready") is not False)
    e = "E TẮT" if not indicator_enabled else f"E {e_policy}: " + (
        "ĐẠT" if e_hit else "CHỜ MẪU" if marks.get("signal_ready") is False else "CHỜ")
    verified = not history_untrusted and not details.get("protection_history_unverified")
    mfe = finite(details.get("normal_mfe_pct")) if verified else None
    if mfe is None and verified and metrics.get("price_history_verified") is True:
        saved = finite(metrics.get("peak_profit_pct", metrics.get("mfe_pct")))
        if saved is not None:
            mfe = protect_peak_for_sellable_phase(saved, _number(metrics.get("normal_sellable_peak_profit_pct")),
                rule.normal_arm_pct, reset_applied=bool(rule.normal_dynamic_enabled and rule.normal_t2_reset_enabled
                                                       and metrics.get("normal_t2_reset_applied")))
    arm_price = entry * (1 + rule.normal_arm_pct / 100) if entry > 0 else 0
    armed = bool(verified and (mfe is not None and mfe + 1e-9 >= rule.normal_arm_pct or
                 ready and (details.get("protection_stage") == "ARM" or normal_state == "ARM")))
    stage = "ARM" if armed else "DYN"
    arm_on = normal_enabled and params.get("normal_arm_enabled", True)
    dyn_on = normal_enabled and rule.normal_dynamic_enabled
    configured_floor = rule.normal_atr_trail_enabled or rule.normal_retention_enabled and rule.normal_retention_pct > 0
    policy = arm_policy if armed else dyn_policy
    floor = _number(details.get("normal_trigger_price")) if ready and verified else 0
    active = bool(normal_enabled and (arm_on if armed else dyn_on and configured_floor) and floor > 0
                  and normal_state in {"ARM", "DYN", "ALERT", "T+2", "WAITING_SETTLEMENT"})
    hit = bool(active and price > 0 and price <= floor + 1e-9)
    arm = "ARM TẮT" if not arm_on else f"ARM {arm_policy}: " + (
        f"MỐC {_display_price(floor)}" if armed and active else "ĐÃ ĐẠT" if armed else f"CHỜ {_display_price(arm_price)}")
    if armed:
        dyn = "DYN: NHƯỜNG ARM" if dyn_on else "DYN TẮT"
    elif not dyn_on:
        dyn = "DYN TẮT"
    else:
        dyn = f"DYN {dyn_policy}: "
    milestones, notes = [], []
    focus, blocked = "ĐANG THEO DÕI · Chưa có điều kiện thoát", False
    if normal_enabled and entry <= 0:
        arm = f"ARM {arm_policy}: CHỜ VỐN" if arm_on else arm
        dyn = f"DYN {dyn_policy}: CHỜ VỐN" if dyn_on else dyn
        focus, blocked = "Chờ giá vốn hợp lệ để tính ARM/START/PROTECT", True
    elif normal_enabled and not verified:
        arm = f"ARM {arm_policy}: XÁC MINH" if arm_on else arm
        dyn = f"DYN {dyn_policy}: XÁC MINH" if dyn_on else dyn
        focus, blocked = "Chờ xác minh đỉnh/đáy; cần đo lại MAE/MFE", True
    elif not normal_enabled:
        pass
    elif armed and not arm_on:
        focus, blocked = "Đã đạt ARM · Nhánh SAU ARM đang tắt", True
    elif normal_state in {"DONE", "REARM"}:
        state = "ĐÃ XONG" if normal_state == "DONE" else "CHỜ LẶP"
        if armed:
            arm = f"ARM {arm_policy}: {state}"
        else:
            dyn = f"DYN {dyn_policy}: {state}"
        focus = "Đã hoàn tất lượt bảo vệ đã cấu hình" if normal_state == "DONE" else "Chờ điều kiện lặp lại sau lần báo/bán trước"
    elif dyn_on and not armed and not configured_floor:
        dyn = f"DYN {dyn_policy}: CHƯA CHỌN MỨC"
        focus, blocked = "DYNAMIC chưa chọn giữ lãi hoặc ATR trail", True
    else:
        atr = _number(details.get("normal_atr_pct", details.get("atr14_daily_pct")))
        activation = (atr * rule.normal_atr_activation_multiplier) if rule.normal_atr_activation_enabled else 0
        start = entry * (1 + activation / 100) if entry > 0 and (activation > 0 or not rule.normal_atr_activation_enabled) else 0
        if dyn_on and not armed:
            dyn += f"MỐC {_display_price(floor)}" if active else (
                f"START {_display_price(start)}" if rule.normal_atr_activation_enabled and start > 0 else
                "CHỜ ATR" if rule.normal_atr_activation_enabled else "CHỜ CÓ LÃI")
        components, floors = [], []
        if mfe is not None and entry > 0:
            peak = entry * (1 + max(0, mfe) / 100)
            if not armed:
                cutoff = min(rule.normal_arm_pct, rule.normal_retention_until_pct) if rule.normal_retention_until_enabled else rule.normal_arm_pct
                if rule.normal_retention_enabled and rule.normal_retention_pct > 0 and mfe + 1e-9 < cutoff:
                    retained = entry + (peak - entry) * rule.normal_retention_pct / 100
                    components.append(f"GIỮ {rule.normal_retention_pct:g}%: {_display_price(retained)}")
                    floors.append(retained)
                if rule.normal_atr_trail_enabled and atr > 0:
                    trail = peak * (1 - atr * rule.normal_atr_multiplier / 100)
                    components.append(f"ATR TRAIL: {_display_price(trail)}")
                    floors.append(trail)
                notes.append(("Các sàn DYNAMIC đang áp dụng: " if active else "PREVIEW sàn DYNAMIC: ")
                             + (" · ".join(components) or "chưa chọn mức/thiếu ATR"))
                notes.append("Mốc chạm lấy sàn cao hơn đang hiệu lực và mốc cũ được giữ; GIỮ LÃI chỉ dùng trước ngưỡng kết thúc đã cấu hình.")
            else:
                components.append(f"LÙI ĐỈNH {rule.normal_giveback_pct:g}%")
                notes.append("Đỉnh đã đạt ARM: SAU ARM tiếp quản; DYNAMIC trước ARM không chạy song song.")
            notes.append(f"Đỉnh theo dõi {_display_price(peak)} · MFE {mfe:+.2f}% · ARM {_display_price(arm_price)} (+{rule.normal_arm_pct:g}%)")
            if not armed and start > 0:
                notes.append(f"START {_display_price(start)} (+{activation:.4g}%) · Chưa START" if mfe + 1e-9 < activation else
                             f"Đỉnh đã đạt START {_display_price(start)}")
        if active:
            label = "ARM" if armed else "DYNAMIC"
            gap = max(0, price - floor)
            explanation = " · ".join(components)
            if not armed and (not floors or floor > max(floors) + 1e-9):
                explanation = f"GIỮ MỐC CŨ {_display_price(floor)}"
            focus = (explanation + " · " if explanation else f"ĐANG BẢO VỆ {label} · ") + f"Cách mốc {_display_price(gap)}đ/CP"
            milestones.append(Milestone(label, floor, gap, focus))
            notes.append(f"Mốc {label} hiện tại {_display_price(floor)} · {policy}; chỉ khớp thực tế mới giảm số cổ.")
        elif not ready:
            focus, blocked = "Chờ đánh giá vị thế mới từ daemon", True
        elif dyn_on and not armed and rule.normal_atr_activation_enabled and atr <= 0:
            focus, blocked = "Chờ ATR14 từ nến ngày đã đóng", True
        elif mfe is None:
            focus, blocked = "Chưa có đỉnh theo dõi của vị thế", True
        else:
            if dyn_on and not armed and start > 0:
                if mfe + 1e-9 < activation or not rule.normal_atr_activation_enabled and mfe <= 0:
                    gap = max(0, start - price)
                    focus = f"Chưa START {_display_price(start)} · Giá {_display_price(price)} · Còn {_display_price(gap)}đ/CP"
                    if not rule.normal_atr_activation_enabled:
                        focus = f"CHỜ CÓ LÃI · Cần đỉnh vượt giá vốn {_display_price(entry)}"
                    if (price >= start if rule.normal_atr_activation_enabled else price > start):
                        focus, blocked = f"GIÁ ĐÃ TỚI START {_display_price(start)} · Chờ daemon cập nhật đỉnh", True
                    milestones.append(Milestone("START", start, gap, focus))
                else:
                    level = protect_level(entry, mfe, rule.normal_arm_pct, rule.normal_giveback_pct, sl_price=sl_price,
                        dynamic_enabled=True, atr_pct=atr, atr_multiplier=rule.normal_atr_multiplier,
                        atr_activation_multiplier=rule.normal_atr_activation_multiplier,
                        atr_activation_enabled=rule.normal_atr_activation_enabled, atr_trail_enabled=rule.normal_atr_trail_enabled,
                        retention_pct=rule.normal_retention_pct, retention_until_pct=rule.normal_retention_until_pct,
                        retention_enabled=rule.normal_retention_enabled, retention_until_enabled=rule.normal_retention_until_enabled,
                        previous_trigger_price=dynamic_floor_previous(metrics, rule.normal_retention_pct))
                    focus, blocked = (f"PREVIEW PROTECT {_display_price(level.trigger_price)} · Chờ daemon xác nhận mốc"
                                      if level.active else "DYNAMIC chưa có mức bảo vệ hiệu lực"), True
            if arm_on and not armed:
                gap = max(0, arm_price - price)
                milestones.append(Milestone("ARM", arm_price, gap,
                    f"CHỜ ARM {_display_price(arm_price)} · Giá {_display_price(price)} · Còn {_display_price(gap)}đ/CP" if gap > 0 else
                    f"GIÁ ĐÃ TỚI ARM {_display_price(arm_price)} · Chờ daemon cập nhật đỉnh"))
    return EMStatus(" · ".join((arm, dyn, e)), focus, "\n".join(notes), tuple(milestones), floor,
                    stage, hit, e_hit, policy, e_policy, blocked)
