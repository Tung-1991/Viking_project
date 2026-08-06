# -*- coding: utf-8 -*-
"""Đánh giá bộ CHECK/REPORT độc lập với pipeline đặt lệnh.

Module CHECK có thể tự định nghĩa ``get_check_metrics(df, params, context)``.
Nếu không có hook, engine tự đọc các cột TA mà Data Engine đã ghi nhận cho
module đó. Kết quả trả về chỉ dành cho scan cache/report.
"""
import copy
import hashlib
import importlib
import json

import pandas as pd

from core.storage_manager import get_brain_settings_for_symbol


GROUPS = ("G0", "G1", "G2", "G3")


def _json_value(value):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if hasattr(value, "item"):
        try:
            return _json_value(value.item())
        except Exception:
            pass
    return str(value)


def _effective_params(cfg, group):
    params = copy.deepcopy((cfg or {}).get("params", {}))
    overrides = (cfg or {}).get("group_params", {})
    if isinstance(overrides, dict) and isinstance(overrides.get(group), dict):
        params.update(overrides[group])
    return params


def _config_id(check_config):
    effective = {}
    for name, cfg in (check_config or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("active", False):
            continue
        effective[name] = {
            "groups": cfg.get("groups", [cfg.get("group", "G2")]),
            "params": cfg.get("params", {}),
            "group_params": cfg.get("group_params", {}),
        }
    raw = json.dumps(effective, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def evaluate(dfs, context, symbol=None, settings=None):
    """Trả kết quả CHECK có cấu trúc; không mutate context/dfs/settings."""
    symbol = str(symbol or (context or {}).get("symbol") or "").upper()
    settings = settings or get_brain_settings_for_symbol(symbol)
    check_config = copy.deepcopy(settings.get("check_indicators", {}) or {})
    output = {"config_id": _config_id(check_config), "groups": {}}
    column_map = (context or {}).get("check_indicator_columns", {}) or {}

    for group in GROUPS:
        df = (dfs or {}).get(group)
        if df is None or df.empty:
            continue
        group_output = {}
        for name, cfg in check_config.items():
            if not isinstance(cfg, dict) or not cfg.get("active", False):
                continue
            groups = cfg.get("groups", [cfg.get("group", "G2")])
            if isinstance(groups, str):
                groups = [groups]
            if group not in groups:
                continue
            params = _effective_params(cfg, group)
            result = {"params": params, "signal": 0, "metrics": {}}
            try:
                module = importlib.import_module(f"signals.{name}")
                signal_func = getattr(module, "get_signal_vector", None)
                if callable(signal_func):
                    result["signal"] = int(signal_func(df, params, copy.deepcopy(context or {})) or 0)

                metrics_func = getattr(module, "get_check_metrics", None)
                if callable(metrics_func):
                    raw_metrics = metrics_func(df, params, copy.deepcopy(context or {})) or {}
                    if isinstance(raw_metrics, dict):
                        result["metrics"] = {
                            str(key): _json_value(value) for key, value in raw_metrics.items()
                        }
                else:
                    columns = ((column_map.get(group) or {}).get(name) or [])
                    latest = df.iloc[-1]
                    metrics = {}
                    for column in columns:
                        if column in df.columns:
                            value = _json_value(latest[column])
                            if value is not None:
                                metrics[str(column)] = value
                    result["metrics"] = metrics
            except Exception as exc:
                result["error"] = str(exc)
            group_output[name] = result
        if group_output:
            output["groups"][group] = group_output
    return output
