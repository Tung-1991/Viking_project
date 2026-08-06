# -*- coding: utf-8 -*-
# FILE: signals/signal_generator.py
# V4.2.1: MULTI-GROUP, DYNAMIC MACRO & TREND COMPASS (KAISER EDITION)

import logging
import json

import copy
import config
from core.storage_manager import (
    brain_strategy_fingerprint,
    get_brain_settings_for_symbol,
)

logger = logging.getLogger("SignalGenerator")

from signals.rsi import get_signal_vector as rsi_signal
from signals.macd import get_signal_vector as macd_signal
from signals.bollinger_bands import get_signal_vector as bollinger_bands_signal
from signals.ema import get_signal_vector as ema_signal
from signals.ema_cross import get_signal_vector as ema_cross_signal
from signals.stochastic import get_signal_vector as stochastic_signal
from signals.atr import get_signal_vector as atr_signal
from signals.adx import get_signal_vector as adx_signal
from signals.supertrend import get_signal_vector as supertrend_signal
from signals.psar import get_signal_vector as psar_signal
from signals.volume import get_signal_vector as volume_signal
from signals.multi_candle import get_signal_vector as multi_candle_signal
from signals.candle import get_signal_vector as candle_pattern_signal
from signals.swing_point import get_signal_vector as swing_point_signal
from signals.fibonacci import get_signal_vector as fibonacci_signal
from signals.pivot_points import get_signal_vector as pivot_points_signal
from signals.simple_breakout import get_signal_vector as simple_breakout_signal

class SignalGenerator:
    def __init__(self):

        
        # Đồng bộ toàn bộ Key về chữ thường khớp với cấu hình JSON từ UI
        self.indicator_map = {
            "rsi": rsi_signal, "macd": macd_signal, "bollinger_bands": bollinger_bands_signal,
            "ema": ema_signal, "ema_cross": ema_cross_signal, "stochastic": stochastic_signal,
            "atr": atr_signal, "adx": adx_signal, "supertrend": supertrend_signal,
            "psar": psar_signal, "volume": volume_signal, "multi_candle": multi_candle_signal,
            "candle": candle_pattern_signal, "swing_point": swing_point_signal,
            "fibonacci": fibonacci_signal, "pivot_points": pivot_points_signal,
            "simple_breakout": simple_breakout_signal
        }

    def _get_brain_settings(self, symbol=None):
        return get_brain_settings_for_symbol(symbol)

    def _detect_dynamic_trend(self, dfs, context, inds_config, eval_mode="VETO", min_votes=3):
        """
        V4.2.2: Trend Cục Bộ - Tách biệt độc lập theo từng Group
        Trả về dict: {"G0": "UP", "G1": "NONE", "G2": "DOWN", "G3": "NONE"}
        """
        if not inds_config: return {g: "NONE" for g in ["G0", "G1", "G2", "G3"]}

        group_trends = {"G0": "NONE", "G1": "NONE", "G2": "NONE", "G3": "NONE"}

        for target_grp in ["G0", "G1", "G2", "G3"]:
            trend_votes = []
            for ind_name, cfg in inds_config.items():
                # is_trend chỉ mô tả vai trò, không đồng nghĩa với bật module.
                if cfg.get("active", False) and cfg.get("is_trend", False):
                    groups = cfg.get("groups", [cfg.get("group", "G2")])
                    
                    # CHỈ lấy phiếu bầu của Indicator nếu nó thuộc Group đang xét
                    if target_grp in groups: 
                        df_eval = dfs.get(target_grp)
                        if df_eval is not None and not df_eval.empty:
                            dummy_rules = {"max_opposite": 0, "max_none": 0}
                            vote = self._evaluate_group(target_grp, {ind_name: cfg}, df_eval, context, "ANY", dummy_rules)
                            if vote != 0:
                                trend_votes.append(vote)

            if not trend_votes:
                continue # Giữ là NONE

            # Phân xử riêng cho Group này
            if eval_mode == "VETO":
                if all(v == 1 for v in trend_votes): group_trends[target_grp] = "UP"
                elif all(v == -1 for v in trend_votes): group_trends[target_grp] = "DOWN"
            
            elif eval_mode == "VOTING":
                buy_votes = sum(1 for v in trend_votes if v == 1)
                sell_votes = sum(1 for v in trend_votes if v == -1)
                
                # Giới hạn min_votes theo tổng số indicator được bật để chống kẹt NONE
                actual_min = min(min_votes, len(trend_votes))
                
                if buy_votes >= actual_min and buy_votes > sell_votes: group_trends[target_grp] = "UP"
                elif sell_votes >= actual_min and sell_votes > buy_votes: group_trends[target_grp] = "DOWN"

        return group_trends


    def _detect_market_mode(self, dfs, context, inds_config=None, voting_rules=None, symbol=None):
        """
        V4.3: Cảm biến Vĩ mô Động - Tích hợp Force ANY Mode (Scalping)
        Bước 1: Tính Base Mode (TREND/RANGE)
        Bước 2: Các Indicator bắt Breakout/Exhaustion chà đạp lên kết quả
        """
        if not isinstance(dfs, dict):
            return "ANY", "NONE", 0
                
        if not inds_config or not voting_rules: 
            return "ANY", "NONE", 0

        # --- [V4.3 NEW] FORCE ANY MODE (CHẾ ĐỘ SCALPING BỎ QUA VĨ MÔ) ---
        settings = self._get_brain_settings(symbol)
        if settings.get("FORCE_ANY_MODE", getattr(config, "FORCE_ANY_MODE", False)):
            return "ANY", "FORCED_SCALP", 0

        # 1. Tìm nhóm Macro (Ưu tiên các Indicator ở G0, nếu không thì G1)
        g0_inds = {k: v for k, v in inds_config.items() if v.get("active") and "G0" in v.get("groups", [v.get("group", "G2")])}
        source_grp = "G0"
        macro_inds = g0_inds
        
        if not macro_inds:
            source_grp = "G1"
            macro_inds = {k: v for k, v in inds_config.items() if v.get("active") and "G1" in v.get("groups", [v.get("group", "G2")])}
            
        df_eval = dfs.get(source_grp)
        if df_eval is None or df_eval.empty or not macro_inds:
            return "ANY", "NONE", 0
            
        rules = voting_rules.get(source_grp, {"max_opposite": 0, "max_none": 0})
        
        # 2. Phân loại theo Vai trò Macro (Macro Role)
        # Trend Compass (is_trend) chỉ dùng để tính trend_Gx/preview, không quyết định Market Mode.
        base_inds = {k: v for k, v in macro_inds.items() if v.get("macro_role", "NONE") == "BASE"}
        brk_inds = {k: v for k, v in macro_inds.items() if v.get("macro_role", "NONE") == "BREAKOUT"}
        exh_inds = {k: v for k, v in macro_inds.items() if v.get("macro_role", "NONE") == "EXHAUSTION"}

        # BƯỚC 1: ĐỊNH HÌNH BASE MODE (TREND HAY RANGE)
        base_dir = 0
        if base_inds:
            base_dir = self._evaluate_group(source_grp, base_inds, df_eval, context, "ANY", rules)
            
        mode = "TREND" if base_dir != 0 else "RANGE"

        # BƯỚC 2: GHI ĐÈ ĐỘT BIẾN (BREAKOUT / EXHAUSTION)
        if brk_inds:
            brk_dir = self._evaluate_group(source_grp, brk_inds, df_eval, context, "ANY", rules)
            if brk_dir != 0:
                mode = "BREAKOUT"
                
        if exh_inds and mode != "BREAKOUT": # Ưu tiên Breakout hơn nếu có xung đột, hoặc để song song
            exh_dir = self._evaluate_group(source_grp, exh_inds, df_eval, context, "ANY", rules)
            if exh_dir != 0:
                mode = "EXHAUSTION"
            
        return mode, source_grp, base_dir

    def _evaluate_group(self, group_name, group_indicators, df, context, current_mode, rules):
        if "group_details" not in context:
            context["group_details"] = {}

        if not group_indicators or df is None or df.empty:
            context["group_details"][group_name] = {"B": 0, "S": 0, "N": 0, "inds": [], "status": 0}
            return 0

        votes = []
        ind_details = []
        for ind_name, ind_cfg in group_indicators.items():
            func = self.indicator_map.get(ind_name)
            if func:
                try:
                    params = copy.deepcopy(ind_cfg.get("params", {}))
                    group_params = ind_cfg.get("group_params", {})
                    if isinstance(group_params, dict):
                        params.update(group_params.get(group_name, {}))

                    group_trigger_modes = ind_cfg.get("group_trigger_modes", {})
                    trigger_mode = ind_cfg.get("trigger_mode", "STRICT_CLOSE")
                    if isinstance(group_trigger_modes, dict):
                        trigger_mode = group_trigger_modes.get(group_name, trigger_mode)
                    
                    # Cắt đuôi nến nếu là STRICT_CLOSE để khóa cản tĩnh, chống repaint
                    eval_df = df.iloc[:-1] if trigger_mode == "STRICT_CLOSE" else df
                    if eval_df.empty: continue
                    
                    if ind_name in {"fibonacci", "pivot_points", "swing_point", "simple_breakout"}:
                         signal = func(eval_df, params, context)
                    else:
                         signal = func(eval_df, params)
                    
                    votes.append(signal)
                    
                    # [V4.5] Nâng cấp thẩm quyền hiển thị - Premium Aesthetics + Tags
                    ind_display_name = ind_name.replace("_", " ").upper()
                    m_role = ind_cfg.get("macro_role", "NONE")
                    a_modes = ",".join(ind_cfg.get("active_modes", ["ANY"]))
                    
                    tag_info = f"[{m_role}|{a_modes}]"
                    
                    if signal == 1:
                        ind_details.append(f"● [BUY]  {ind_display_name} {tag_info}")
                    elif signal == -1:
                        ind_details.append(f"● [SELL] {ind_display_name} {tag_info}")
                    else:
                        ind_details.append(f"○ [WAIT] {ind_display_name} {tag_info}")
                except Exception as e:
                    logger.error(f"Lỗi tính toán {ind_name}: {e}")
                    votes.append(0)
                    ind_details.append(f"❌ {ind_name}")

        if not votes:
            context["group_details"][group_name] = {"B": 0, "S": 0, "N": 0, "inds": [], "status": 0}
            return 0

        total_buy = sum(1 for v in votes if v == 1)
        total_sell = sum(1 for v in votes if v == -1)
        total_none = sum(1 for v in votes if v == 0)

        max_opp = rules.get("max_opposite", 0)
        max_none = rules.get("max_none", 1)

        main_direction = 0
        opp_count = 0  # Khởi tạo sớm để tránh UnboundLocalError
        if total_buy > 0 and total_buy > total_sell:  # Kaiser: Chặn Buy Bias
            main_direction = 1
            opp_count = total_sell
        elif total_sell > 0 and total_sell > total_buy:
            main_direction = -1
            opp_count = total_buy

        if main_direction != 0 and opp_count <= max_opp and total_none <= max_none:
            status = main_direction
        else:
            status = 0

        # Ghi vào context để UI vẽ bảng chi tiết
        context["group_details"][group_name] = {
            "B": total_buy,
            "S": total_sell,
            "N": total_none,
            "inds": ind_details,
            "status": status
        }

        return status

    def _evaluate_pipeline_v4(self, dfs, context, current_mode, voting_rules, active_inds, eval_mode="VETO", min_votes=3):
        votes = {}

        for grp in ["G0", "G1", "G2", "G3"]:
            rule = voting_rules.get(grp, {}).get("master_rule", "IGNORE")
            if rule == "IGNORE": continue
            
            df_grp = dfs.get(grp)
            if df_grp is None or df_grp.empty:
                if rule == "FIX":
                    context["block_reason"] = f"Missing data {grp} (FIX rule)"
                    return 0 
                continue

            status = self._evaluate_group(grp, active_inds[grp], df_grp, context, current_mode, voting_rules.get(grp, {}))
            votes[grp] = status

            if eval_mode == "VETO":
                if rule == "FIX" and status == 0:
                    context["block_reason"] = f"Blocked by {grp} (FIX rule)"
                    return 0 
                
                active_votes = [v for v in votes.values() if v != 0]
                if len(set(active_votes)) > 1:
                    context["block_reason"] = "Direction conflict between groups"
                    return 0 

        if eval_mode == "VETO":
            active_votes = [v for v in votes.values() if v != 0]
            if not active_votes:
                context["block_reason"] = "No group has signal (WAIT)"
                return 0
            final_dir = active_votes[0]
            for grp, status in votes.items():
                rule = voting_rules.get(grp, {}).get("master_rule", "IGNORE")
                if (rule == "FIX" or rule == "PASS") and status != 0 and status != final_dir:
                    context["block_reason"] = f"Direction conflict with {grp} ({rule} rule)"
                    return 0
            context["block_reason"] = "OK / Ready"
            return final_dir
        
        elif eval_mode == "VOTING":
            buy_votes = sum(1 for v in votes.values() if v == 1)
            sell_votes = sum(1 for v in votes.values() if v == -1)
            
            if buy_votes >= min_votes and buy_votes > sell_votes:
                context["block_reason"] = "OK / Ready"
                return 1
            if sell_votes >= min_votes and sell_votes > buy_votes:
                context["block_reason"] = "OK / Ready"
                return -1
            
            context["block_reason"] = f"Not enough votes ({max(buy_votes, sell_votes)}/{min_votes})"
            return 0
        
        return 0

    def evaluate_mini_brain(self, df, context, mini_brain_cfg, current_mode="ANY"):
        """[NEW V5.1] Đánh giá độc lập cho DCA/PCA bằng Mini-Brain 1-Group"""
        if not mini_brain_cfg or not mini_brain_cfg.get("active", False):
            return 0 # Nếu không bật Mini-Brain, trả về 0 để hệ thống xử lý logic mặc định
            
        inds = mini_brain_cfg.get("indicators", {})
        if not inds:
            return 0
            
        rules = {
            "max_opposite": int(mini_brain_cfg.get("max_opposite", 0)),
            "max_none": int(mini_brain_cfg.get("max_none", 0)),
            "master_rule": "FIX" 
        }
        
        # Tái sử dụng engine evaluate lõi nhưng chạy độc lập với 1 Group
        return self._evaluate_group("MINI_BRAIN", inds, df, context, current_mode, rules)

    def generate_signal_v4(self, dfs, context, symbol=None):
        settings = self._get_brain_settings(symbol)
        # Không cho UI ghép rule mới với phiếu indicator cũ từ heartbeat cache.
        context["strategy_fingerprint"] = brain_strategy_fingerprint(settings)
        voting_rules = settings.get("voting_rules", {})
        inds_config = settings.get("indicators", {})
        
        # [FIX]: Đọc từ settings trước, fallback về config
        eval_mode = settings.get("MASTER_EVAL_MODE", getattr(config, "MASTER_EVAL_MODE", "VETO"))
        min_votes = int(settings.get("MIN_MATCHING_VOTES", getattr(config, "MIN_MATCHING_VOTES", 3)))
        
        # 1. Tính Dynamic Trend Compass Cục Bộ
        group_trends = self._detect_dynamic_trend(dfs, context, inds_config, eval_mode, min_votes)
        
        # Đóng gói Trend từng khung vào Context để Bot UI Dashboard đọc được
        for g, t in group_trends.items():
            context[f"trend_{g}"] = t
            
        context["real_trend"] = group_trends.get("G0", "NONE") # Giữ fallback 
        
        # 2. Tính Market Mode V4.2
        current_mode, mode_src, macro_dir = self._detect_market_mode(dfs, context, inds_config, voting_rules, symbol)
        
        context["market_mode"] = current_mode
        context["mode_source"] = mode_src
        context["macro_direction"] = macro_dir

        # 3. Phân bổ Indicator vào các Group
        active_inds_by_group = {"G0": {}, "G1": {}, "G2": {}, "G3": {}}
        for name, cfg in inds_config.items():
            if cfg.get("active", False):
                modes = cfg.get("active_modes", ["ANY"])
                if "ANY" in modes or current_mode in modes:
                    groups = cfg.get("groups", [cfg.get("group", "G2")])
                    for grp in groups:
                        if grp in active_inds_by_group:
                            active_inds_by_group[grp][name] = cfg

        # 4. Đóng gói tín hiệu từng Group vào Context (Dùng cho UI Dashboard)
        context["group_signals"] = {
            grp: self._evaluate_group(grp, active_inds_by_group[grp], dfs.get(grp), context, current_mode, voting_rules.get(grp, {}))
            for grp in ["G0", "G1", "G2", "G3"]
        }
        context["group_rules"] = {
            grp: str(voting_rules.get(grp, {}).get("master_rule", "IGNORE")).upper()
            for grp in ["G0", "G1", "G2", "G3"]
        }

        # 5. Đẩy vào Phễu Vote
        return self._evaluate_pipeline_v4(dfs, context, current_mode, voting_rules, active_inds_by_group, eval_mode, min_votes)



signal_generator = SignalGenerator()
