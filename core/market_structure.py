# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Optional


def analyze_market_structure(df, lookback: int = 50, strength: int = 2) -> Dict[str, Any]:
    result = _empty_structure()
    if df is None or len(df) < (strength * 2 + 5):
        return result

    try:
        window = df.tail(max(lookback, strength * 2 + 5)).reset_index(drop=True)
        highs = [float(x) for x in window["high"].values]
        lows = [float(x) for x in window["low"].values]
    except Exception:
        return result

    pivots: List[Dict[str, Any]] = []
    for i in range(strength, len(window) - strength):
        left_highs = highs[i - strength:i]
        right_highs = highs[i + 1:i + strength + 1]
        left_lows = lows[i - strength:i]
        right_lows = lows[i + 1:i + strength + 1]

        if all(highs[i] > x for x in left_highs + right_highs):
            pivots.append({"type": "HIGH", "price": highs[i], "index": i})
        if all(lows[i] < x for x in left_lows + right_lows):
            pivots.append({"type": "LOW", "price": lows[i], "index": i})

    pivots.sort(key=lambda item: item["index"])
    highs_p = [p for p in pivots if p["type"] == "HIGH"]
    lows_p = [p for p in pivots if p["type"] == "LOW"]
    result["pivots"] = pivots[-8:]

    if len(highs_p) < 2 or len(lows_p) < 2:
        return result

    prev_high, last_high = highs_p[-2], highs_p[-1]
    prev_low, last_low = lows_p[-2], lows_p[-1]

    high_label = "HH" if last_high["price"] > prev_high["price"] else "LH"
    low_label = "HL" if last_low["price"] > prev_low["price"] else "LL"

    result.update(
        {
            "bias": "UP" if high_label == "HH" and low_label == "HL" else "DOWN" if high_label == "LH" and low_label == "LL" else "RANGE",
            "high_label": high_label,
            "low_label": low_label,
            "last_high": last_high["price"],
            "last_low": last_low["price"],
            "prev_high": prev_high["price"],
            "prev_low": prev_low["price"],
            "hh": last_high["price"] if high_label == "HH" else None,
            "hl": last_low["price"] if low_label == "HL" else None,
            "lh": last_high["price"] if high_label == "LH" else None,
            "ll": last_low["price"] if low_label == "LL" else None,
        }
    )
    return result


def write_structure_context(context: Dict[str, Any], group: str, structure: Dict[str, Any]) -> None:
    prefix = f"ms_{group}"
    context[f"{prefix}_bias"] = structure.get("bias", "UNKNOWN")
    context[f"{prefix}_high_label"] = structure.get("high_label")
    context[f"{prefix}_low_label"] = structure.get("low_label")
    for key in ["hh", "hl", "lh", "ll", "last_high", "last_low", "prev_high", "prev_low"]:
        val = structure.get(key)
        if val is not None:
            context[f"{prefix}_{key}"] = float(val)


def structure_from_context(context: Dict[str, Any], group: str) -> Dict[str, Any]:
    prefix = f"ms_{group}"
    return {
        "bias": context.get(f"{prefix}_bias", "UNKNOWN"),
        "high_label": context.get(f"{prefix}_high_label"),
        "low_label": context.get(f"{prefix}_low_label"),
        "hh": _num(context.get(f"{prefix}_hh")),
        "hl": _num(context.get(f"{prefix}_hl")),
        "lh": _num(context.get(f"{prefix}_lh")),
        "ll": _num(context.get(f"{prefix}_ll")),
        "last_high": _num(context.get(f"{prefix}_last_high")),
        "last_low": _num(context.get(f"{prefix}_last_low")),
    }


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _empty_structure() -> Dict[str, Any]:
    return {
        "bias": "UNKNOWN",
        "high_label": None,
        "low_label": None,
        "hh": None,
        "hl": None,
        "lh": None,
        "ll": None,
        "last_high": None,
        "last_low": None,
        "prev_high": None,
        "prev_low": None,
        "pivots": [],
    }
