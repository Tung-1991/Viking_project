# -*- coding: utf-8 -*-
from __future__ import annotations

import config


MONEY_DISPLAY_UNITS = {
    "VND": {"label": "Không bỏ số 0", "short": "VND", "scale": 1.0, "digits": 0},
    # "Bỏ 000" nghĩa là bỏ hẳn ba chữ số cuối khi hiển thị,
    # không chuyển ba chữ số đó thành phần thập phân.
    "K_VND": {"label": "000", "short": "000", "scale": 1000.0, "digits": 0},
    "M_VND": {"label": "000 000", "short": "000 000", "scale": 1000000.0, "digits": 0},
}

MONEY_DISPLAY_LABELS = [item["label"] for item in MONEY_DISPLAY_UNITS.values()]
_LABEL_TO_UNIT = {item["label"]: key for key, item in MONEY_DISPLAY_UNITS.items()}
_LABEL_TO_UNIT.update({"VND": "VND", "nghìn VND": "K_VND", "triệu VND": "M_VND"})

ZERO_TRIM_TO_UNIT = {
    "NONE": "VND",
    "0": "VND",
    "KHÔNG BỎ": "VND",
    "000": "K_VND",
    "000000": "M_VND",
    "000 000": "M_VND",
}
UNIT_TO_ZERO_TRIM = {"VND": "NONE", "K_VND": "000", "M_VND": "000 000"}


def normalize_zero_trim(value: str | None = None) -> str:
    raw = str(
        value
        if value is not None
        else getattr(config, "MONEY_DISPLAY_ZERO_TRIM", "000")
    ).strip().upper()
    if raw in ("", "DEFAULT"):
        raw = "000"
    return UNIT_TO_ZERO_TRIM.get(raw, raw) if raw in UNIT_TO_ZERO_TRIM else (
        "NONE" if raw in ("NONE", "0", "KHÔNG BỎ") else
        "000 000" if raw in ("000000", "000 000") else
        "000"
    )


def set_money_display_zero_trim(value: str | None) -> str:
    trim = normalize_zero_trim(value)
    config.MONEY_DISPLAY_ZERO_TRIM = trim
    config.MONEY_DISPLAY_UNIT = ZERO_TRIM_TO_UNIT.get(trim, "K_VND")
    return trim


def money_display_scale() -> float:
    return float(MONEY_DISPLAY_UNITS[normalize_money_unit()]["scale"])


def normalize_money_unit(value: str | None = None) -> str:
    if value is None:
        trim = normalize_zero_trim()
        return ZERO_TRIM_TO_UNIT.get(trim, "K_VND")
    raw = str(value).strip()
    unit = _LABEL_TO_UNIT.get(raw, raw)
    return unit if unit in MONEY_DISPLAY_UNITS else "K_VND"


def money_unit_label(unit: str | None = None) -> str:
    return MONEY_DISPLAY_UNITS[normalize_money_unit(unit)]["short"]


def money_unit_note(unit: str | None = None) -> str:
    trim = normalize_zero_trim() if unit is None else UNIT_TO_ZERO_TRIM.get(normalize_money_unit(unit), "000")
    if trim == "NONE":
        return "Hiển thị tiền: VND đầy đủ"
    zeros = "000 000" if trim == "000 000" else "000"
    scale = "1.000.000" if trim == "000 000" else "1.000"
    return f"Hiển thị tiền: bỏ {zeros} (1 = {scale} VND)"


def money_setting_hint() -> str:
    """Hướng dẫn thống nhất cho mọi ô setting nhận số tiền VND."""
    return (
        "Ô setting luôn nhập đủ VND: 500000 = 500.000 VND. "
        "Nhãn đọc có thể hiện 500 khi chọn bỏ 000."
    )


def format_vnd(value: float | int | None, signed: bool = False, suffix: bool = False) -> str:
    unit = normalize_money_unit()
    meta = MONEY_DISPLAY_UNITS[unit]
    try:
        amount = float(value or 0.0) / float(meta["scale"])
    except Exception:
        amount = 0.0
    digits = int(meta["digits"])
    sign = ""
    if signed:
        sign = "+" if amount > 0 else "-" if amount < 0 else ""
    amount = abs(amount) if signed else amount
    text = f"{amount:,.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.replace(",", "") in ("0", "-0"):
        sign = ""
    if suffix:
        text = f"{text} {meta['short']}"
    return f"{sign}{text}"


def format_vnd_full(value: float | int | None, signed: bool = False) -> str:
    """Tên cũ để tương thích; mọi nơi nay cùng dùng setting bỏ số 0."""
    return format_vnd(value, signed=signed, suffix=False)


def format_money_k(value: float | int | None, max_decimals: int = 2) -> str:
    """Hiển thị tiền theo lựa chọn bỏ số 0, luôn là số nguyên.

    ``max_decimals`` được giữ trong chữ ký hàm để tương thích với code cũ;
    setting ``000``/``000 000`` không còn hiển phần tiền bị bỏ sau dấu chấm.
    """
    unit = normalize_money_unit()
    meta = MONEY_DISPLAY_UNITS[unit]
    try:
        amount = float(value or 0.0) / float(meta["scale"])
    except Exception:
        amount = 0.0
    text = f"{amount:,.0f}"
    if text in ("", "-", "-0"):
        text = "0"
    return text


def parse_money_unit_label(label: str | None) -> str:
    return normalize_money_unit(label)


# [UNIT VND] Trong config/logic, unit tiền lưu là "USD" (di sản MT5) nhưng nghĩa thật là
# VND (đồng nguyên con). UI hiển thị "VND" cho khỏi nhầm; khi LƯU phải map ngược về "USD"
# để không đụng các so sánh == "USD" trong trade_manager và settings cũ đã lưu.
def unit_to_display(value: str | None) -> str:
    v = str(value or "USD").strip()
    return "VND" if v.upper() == "USD" else v


def unit_from_display(value: str | None) -> str:
    v = str(value or "VND").strip()
    return "USD" if v.upper() == "VND" else v


_MONEY_UNITS = ("USD", "VND")


def _is_money_unit(unit: str | None) -> bool:
    return str(unit or "").strip().upper() in _MONEY_UNITS


def money_input_to_display(value, unit: str | None) -> str:
    """Ô setting luôn hiện đúng giá trị lưu; rút gọn chỉ dành cho nhãn đọc."""
    try:
        v = float(value or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    if v.is_integer():
        return str(int(v))
    return f"{v:.12f}".rstrip("0").rstrip(".")


def money_input_from_display(value, unit: str | None) -> float:
    """Ô setting nhập bao nhiêu lưu bấy nhiêu; không nhân theo kiểu hiển thị."""
    try:
        v = float(value or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    return v
