# -*- coding: utf-8 -*-
"""Xuất báo cáo động từ kho tổng hợp ngày của nhánh CHECK."""
import json
import logging
import os
import statistics
import threading
import uuid
from datetime import datetime

from ai_advisor import paths, scan_cache

logger = logging.getLogger(__name__)
_REPORT_LOCK = threading.RLock()
_REPORT_SKIP_LOG_SIGNATURES = {}

AI_INSTRUCTIONS = """> **CÁCH ĐỌC DỮ LIỆU**
> - CHECK là dữ liệu kỹ thuật phục vụ báo cáo; không phải lệnh và không tác động BOT TRADE.
> - Chỉ phân tích những module CHECK thực sự xuất hiện. Không giả định RSI, MACD, Bollinger Bands hoặc bất kỳ module nào luôn tồn tại.
> - `BOT signal` là tín hiệu của nhánh TRADE. `CHECK view` chỉ là BUY/SELL/WAIT tham khảo của module báo cáo.
> - Nếu một ngày có nhiều `CHECK segment`, operator đã đổi cấu hình giữa phiên; không trộn số liệu giữa các segment.
> - `Thanh khoản 60 phiên` là bộ lọc điều kiện CKCS, không phải indicator và không tạo BUY/SELL. GTGD được ước tính từ typical price × volume của 60 nến ngày đã hoàn tất.
> - Có thể dùng web search để bổ sung tin tức/bối cảnh mới, nhưng phải tách rõ dữ liệu nội bộ và thông tin tìm trên web.
> - Đơn vị giá: CKCS thường là nghìn VND; CKPS là điểm chỉ số.
>
> **KẾT QUẢ PHÂN TÍCH CKCS CẦN TRẢ VỀ**
> - Xếp hạng mã và gán trạng thái: `WATCH/CHỜ MUA/MUA/HOLD/GIẢM/EXIT/LOẠI`; được phép kết luận không có mã phù hợp.
> - Với mã đáng chú ý: vùng mua, điều kiện kích hoạt, mức không mua đuổi, mốc nhận định sai/SL, TP hoặc cách trailing.
> - Nêu thời gian giữ, tỷ trọng đề xuất, ngày hết hiệu lực và lý do thay đổi so với nhận định trước nếu có.
> - Đây chỉ là đề xuất cho người dùng quyết định; app không tự biến kết quả AI thành lệnh CKCS."""


def _fmt(value, nd=None):
    if value is None:
        return "—"
    try:
        if nd is not None:
            value = round(float(value), nd)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{value:,}" if isinstance(value, (int, float)) else str(value)
    except Exception:
        return str(value)


def _pct(value):
    return "—" if value is None else f"{float(value):+.2f}%"


def _slice_days(sym_node, report_days=None):
    days = sym_node.get("days", {})
    names = sorted(days)
    if report_days is not None:
        names = names[-max(1, int(report_days)):]
    return [(day, days[day]) for day in names]


def _signal_label(value):
    try:
        value = int(value)
    except Exception:
        value = 0
    return "BUY" if value > 0 else ("SELL" if value < 0 else "WAIT")


def _metric_text(metric):
    if not isinstance(metric, dict):
        return _fmt(metric)
    if metric.get("kind") == "number":
        return (f"đầu {_fmt(metric.get('first'), 4)}; thấp {_fmt(metric.get('min'), 4)}; "
                f"cao {_fmt(metric.get('max'), 4)}; TB {_fmt(metric.get('avg'), 4)}; "
                f"cuối {_fmt(metric.get('last'), 4)}")
    if metric.get("kind") == "state":
        return (f"đầu {metric.get('first', '—')}; cuối {metric.get('last', '—')}; "
                f"đổi {metric.get('changes', 0)} lần")
    return json.dumps(metric, ensure_ascii=False, default=str)


def _module_line(group, name, module):
    params = json.dumps(module.get("params", {}), ensure_ascii=False, sort_keys=True, default=str)
    counts = module.get("signal_counts", {})
    views = "/".join(f"{key}:{counts[key]}" for key in ("BUY", "SELL", "WAIT") if key in counts) or "—"
    head = f"- CHECK `{group}.{name}` params `{params}` | view cuối {_signal_label(module.get('latest_signal'))} | đếm {views}"
    if module.get("error"):
        head += f" | lỗi `{module['error']}`"
    lines = [head]
    for metric_name, metric in sorted((module.get("metrics") or {}).items()):
        lines.append(f"  - `{metric_name}`: {_metric_text(metric)}")
    return lines


def _segments_lines(entry):
    segments = [
        segment
        for segment in (entry.get("check_segments") or [])
        if any((modules or {}) for modules in (segment.get("groups") or {}).values())
    ]
    if not segments:
        return []
    lines = []
    for index, segment in enumerate(segments, 1):
        lines.append(
            f"- CHECK segment {index}: config `{segment.get('config_id', '—')}`, "
            f"{segment.get('first_scan', '—')}–{segment.get('last_scan', '—')}, "
            f"{segment.get('samples', 0)} lần cập nhật"
        )
        for group, modules in sorted((segment.get("groups") or {}).items()):
            for name, module in sorted((modules or {}).items()):
                lines.extend(_module_line(group, name, module))
    return lines


def _bot_line(entry):
    bot = entry.get("bot") or {}
    trends = "/".join(str(bot.get(f"trend_{group}") or "-") for group in ("G0", "G1", "G2", "G3"))
    events = ", ".join(f"{event.get('side')} {event.get('time')}" for event in entry.get("signals", [])) or "không có"
    counts = entry.get("bot_signal_counts") or {}
    count_text = "/".join(f"{name}:{counts.get(name, 0)}" for name in ("BUY", "SELL", "WAIT"))
    return (f"BOT signal cuối {_signal_label(bot.get('latest_signal'))}; trend {trends}; "
            f"mode {bot.get('market_mode') or '—'}; đếm lần quét {count_text}; sự kiện trong ngày: {events}")


def _daily_header(day, entry):
    price, volume = entry.get("price") or {}, entry.get("volume") or {}
    status = str(entry.get("day_status") or ("EOD" if entry.get("eod_final") else "INTRADAY")).upper()
    freshness = "EOD" if status == "EOD" else (
        f"incomplete, lần cuối {entry.get('last_scan', '—')}"
        if status == "INCOMPLETE"
        else f"intraday {entry.get('last_scan', '—')}"
    )
    projected = _number(volume.get("projected_ratio"))
    volume_ratio_text = f"x{_fmt(volume.get('ratio'), 2)}"
    if projected is not None:
        volume_ratio_text += f"; ước tính cả phiên x{projected:.2f}"
    lines = [
        f"### {day} ({freshness}; cập nhật {entry.get('samples', 0)} lần)",
        (f"- Giá O/H/L/C: {_fmt(price.get('open'), 2)}/{_fmt(price.get('high'), 2)}/"
         f"{_fmt(price.get('low'), 2)}/{_fmt(price.get('close'), 2)}; hiện tại {_fmt(price.get('current'), 2)}; "
         f"1D {_pct(price.get('pct_1d'))}; 1W {_pct(price.get('pct_1w'))}"),
        (f"- Khối lượng: {_fmt(volume.get('today'))}; TB20 {_fmt(volume.get('avg20'))}; "
         f"tỷ lệ {volume_ratio_text}; xu hướng 5 ngày {volume.get('trend_5d') or '—'}"),
        f"- {_bot_line(entry)}",
    ]
    liquidity = entry.get("liquidity_60d") or {}
    if liquidity and liquidity.get("status") != "NOT_APPLICABLE":
        status = str(liquidity.get("status") or "INSUFFICIENT")
        available = int(liquidity.get("sessions_available", 0) or 0)
        required = int(liquidity.get("sessions_required", 60) or 60)
        average = liquidity.get("average_value_billion")
        threshold = liquidity.get("threshold_billion")
        average_text = "—" if average is None else f"{float(average):.2f} tỷ/phiên"
        lines.append(
            f"- Thanh khoản {required} phiên: {average_text}; ngưỡng "
            f"{_fmt(threshold, 2)} tỷ; **{status}** ({available}/{required} phiên)"
        )
    return lines


def _number(value):
    try:
        value = float(value)
        return value if value == value else None
    except (TypeError, ValueError):
        return None


def _entry_price(entry):
    price = entry.get("price") or {}
    return _number(price.get("current")) or _number(price.get("close"))


def _effective_volume_ratio(entry):
    volume = entry.get("volume") or {}
    status = str(entry.get("day_status") or "INTRADAY").upper()
    if status == "INTRADAY":
        return _number(volume.get("projected_ratio")) or _number(volume.get("ratio"))
    return _number(volume.get("ratio"))


def _market_overview(symbols, report_days=None):
    """Tóm tắt chéo toàn bộ mã để LLM nhìn được trạng thái thị trường trước."""
    latest = []
    all_dates = set()
    check_modules = set()
    for symbol, node in sorted(symbols.items()):
        days = _slice_days(node, report_days)
        if not days:
            continue
        day, entry = days[-1]
        all_dates.add(day)
        price = entry.get("price") or {}
        change = _number(price.get("pct_1d"))
        latest.append({
            "symbol": symbol,
            "day": day,
            "entry": entry,
            "change": change,
            "volume_ratio": _effective_volume_ratio(entry),
            "status": str(entry.get("day_status") or "INTRADAY").upper(),
            "signal": _signal_label((entry.get("bot") or {}).get("latest_signal")),
        })
        for segment in entry.get("check_segments") or []:
            for group, modules in (segment.get("groups") or {}).items():
                for name in (modules or {}):
                    check_modules.add(f"{group}.{name}")

    if not latest:
        return []
    changes = [row["change"] for row in latest if row["change"] is not None]
    up = sum(value > 0 for value in changes)
    down = sum(value < 0 for value in changes)
    flat = len(changes) - up - down
    missing_change = len(latest) - len(changes)
    status_counts = {
        status: sum(row["status"] == status for row in latest)
        for status in ("EOD", "INTRADAY", "INCOMPLETE")
    }
    signal_counts = {
        side: sum(row["signal"] == side for row in latest)
        for side in ("BUY", "SELL", "WAIT")
    }
    ranked = sorted(
        (row for row in latest if row["change"] is not None),
        key=lambda row: row["change"],
    )
    decliners = ", ".join(
        f"{row['symbol']} {row['change']:+.2f}%" for row in ranked[:5]
    ) or "—"
    gainers = ", ".join(
        f"{row['symbol']} {row['change']:+.2f}%" for row in reversed(ranked[-5:])
    ) or "—"
    unusual_volume = sorted(
        (
            row for row in latest
            if row["volume_ratio"] is not None and row["volume_ratio"] >= 1.5
        ),
        key=lambda row: row["volume_ratio"],
        reverse=True,
    )
    volume_text = ", ".join(
        f"{row['symbol']} x{row['volume_ratio']:.2f}" for row in unusual_volume[:8]
    ) or "không có mã đạt x1.5"
    date_text = ", ".join(sorted(all_dates))
    mean = sum(changes) / len(changes) if changes else None
    median = statistics.median(changes) if changes else None
    lines = [
        "## TÓM TẮT TOÀN BỘ DANH SÁCH",
        "",
        f"- Ngày dữ liệu mới nhất: {date_text}; số mã có bản ghi: {len(latest)}.",
        (f"- Độ rộng: tăng {up}, giảm {down}, đứng giá {flat}, thiếu biến động {missing_change}; "
         f"trung bình {_pct(mean)}, trung vị {_pct(median)}."),
        f"- Giảm mạnh nhất: {decliners}.",
        f"- Tăng mạnh nhất: {gainers}.",
        f"- Khối lượng bất thường so với TB20: {volume_text}.",
        (f"- BOT quan sát cuối: BUY {signal_counts['BUY']}, SELL {signal_counts['SELL']}, "
         f"WAIT {signal_counts['WAIT']}. Đây không phải danh sách lệnh đã đặt."),
        (f"- Độ phủ bản ghi mới nhất: EOD {status_counts['EOD']}, "
         f"INTRADAY {status_counts['INTRADAY']}, INCOMPLETE {status_counts['INCOMPLETE']}."),
    ]
    if check_modules:
        lines.append("- Module CHECK có dữ liệu: " + ", ".join(sorted(check_modules)) + ".")
    else:
        lines.append(
            "- CHECK chưa bật module nào; báo cáo hiện dùng giá, khối lượng và tín hiệu BOT quan sát."
        )
    lines.extend([
        "- Ngày INCOMPLETE vẫn được giữ vì máy có thể không chạy liên tục; không coi đó là dữ liệu cả ngày.",
        "",
    ])
    return lines


def _period_summary(days):
    observations = []
    highs, lows, volume_ratios = [], [], []
    statuses = {"EOD": 0, "INTRADAY": 0, "INCOMPLETE": 0}
    signals = {"BUY": 0, "SELL": 0, "WAIT": 0}
    for day, entry in days:
        price = entry.get("price") or {}
        status = str(entry.get("day_status") or "INTRADAY").upper()
        value = _entry_price(entry)
        if value is not None:
            observations.append((day, value))
        high = _number(price.get("high"))
        low = _number(price.get("low"))
        ratio = _number((entry.get("volume") or {}).get("ratio")) if status == "EOD" else None
        if high is not None:
            highs.append(high)
        if low is not None:
            lows.append(low)
        # Dữ liệu legacy có ngày chỉ lưu current/close mà thiếu high/low.
        # Giá quan sát luôn phải nằm trong biên ngày; đưa nó vào hai tập giúp
        # báo cáo không còn trường hợp "giá cuối > cao nhất" hoặc ngược lại.
        if value is not None:
            highs.append(value)
            lows.append(value)
        if ratio is not None:
            volume_ratios.append(ratio)
        statuses[status] = statuses.get(status, 0) + 1
        signal = _signal_label((entry.get("bot") or {}).get("latest_signal"))
        signals[signal] += 1

    lines = []
    if observations:
        first_day, first = observations[0]
        last_day, last = observations[-1]
        change = ((last / first) - 1) * 100 if first else None
        lines.append(
            f"- Giá đầu/cuối khoảng: {first_day} {_fmt(first, 2)} → "
            f"{last_day} {_fmt(last, 2)} ({_pct(change)}); "
            f"cao nhất {_fmt(max(highs), 2) if highs else '—'}; "
            f"thấp nhất {_fmt(min(lows), 2) if lows else '—'}."
        )
    else:
        lines.append("- Không có giá hợp lệ trong khoảng đã chọn.")
    lines.append(
        f"- Chất lượng ngày: EOD {statuses.get('EOD', 0)}, "
        f"INTRADAY {statuses.get('INTRADAY', 0)}, "
        f"INCOMPLETE {statuses.get('INCOMPLETE', 0)}."
    )
    if volume_ratios:
        lines.append(
            f"- Tỷ lệ khối lượng/TB20 trung bình x{sum(volume_ratios) / len(volume_ratios):.2f}; "
            f"cao nhất x{max(volume_ratios):.2f}."
        )
    lines.append(
        f"- BOT signal cuối từng ngày: BUY {signals['BUY']}, "
        f"SELL {signals['SELL']}, WAIT {signals['WAIT']}."
    )
    return lines


def _history_table(days):
    if not days:
        return []
    lines = [
        "### Lịch sử ngày (bản gọn)",
        "",
        "| Ngày | Trạng thái | O/H/L/C | 1D | 1W | Vol/TB20 | Trend G0/G1/G2/G3 | Mode | BOT |",
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for day, entry in reversed(days):
        price = entry.get("price") or {}
        volume = entry.get("volume") or {}
        bot = entry.get("bot") or {}
        status = str(entry.get("day_status") or "INTRADAY").upper()
        ohlc = "/".join(
            _fmt(price.get(name), 2) for name in ("open", "high", "low", "close")
        )
        ratio = _effective_volume_ratio(entry)
        ratio_prefix = "~" if status == "INTRADAY" and _number(volume.get("projected_ratio")) is not None else ""
        trends = "/".join(str(bot.get(f"trend_{group}") or "-") for group in ("G0", "G1", "G2", "G3"))
        lines.append(
            f"| {day} | {status} | {ohlc} | {_pct(price.get('pct_1d'))} | "
            f"{_pct(price.get('pct_1w'))} | {'—' if ratio is None else f'{ratio_prefix}x{ratio:.2f}'} | "
            f"{trends} | {bot.get('market_mode') or '—'} | "
            f"{_signal_label(bot.get('latest_signal'))} |"
        )
    lines.append("")
    return lines


def _range_summary(days):
    """Tổng hợp giá trị cuối ngày theo tên module/metric trong toàn khoảng N ngày."""
    values = {}
    for day, entry in days:
        for segment in entry.get("check_segments") or []:
            config_id = str(segment.get("config_id") or "unknown")
            for group, modules in (segment.get("groups") or {}).items():
                for name, module in (modules or {}).items():
                    for metric_name, metric in (module.get("metrics") or {}).items():
                        key = (config_id, group, name, metric_name)
                        value = metric.get("last") if isinstance(metric, dict) else metric
                        values.setdefault(key, []).append((day, value))
    lines = []
    for (config_id, group, name, metric_name), observations in sorted(values.items()):
        numeric = [float(value) for _, value in observations if isinstance(value, (int, float)) and not isinstance(value, bool)]
        first_day, first = observations[0]
        last_day, last = observations[-1]
        if numeric:
            text = (f"đầu {first_day}={_fmt(first, 4)}; thấp {_fmt(min(numeric), 4)}; "
                    f"cao {_fmt(max(numeric), 4)}; TB {_fmt(sum(numeric) / len(numeric), 4)}; "
                    f"cuối {last_day}={_fmt(last, 4)}")
        else:
            changes = sum(observations[i][1] != observations[i - 1][1] for i in range(1, len(observations)))
            text = f"đầu {first_day}={first}; cuối {last_day}={last}; đổi {changes} lần"
        lines.append(f"- config `{config_id}` · `{group}.{name}.{metric_name}`: {text}")
    return lines


def _check_config_lines(days):
    seen = set()
    lines = []
    for _day, entry in days:
        for segment in entry.get("check_segments") or []:
            config_id = str(segment.get("config_id") or "unknown")
            for group, modules in (segment.get("groups") or {}).items():
                for name, module in (modules or {}).items():
                    params = json.dumps(
                        module.get("params", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    key = (config_id, group, name, params)
                    if key in seen:
                        continue
                    seen.add(key)
                    lines.append(
                        f"- config `{config_id}` · `{group}.{name}` params `{params}`."
                    )
    return lines


def _volatility_event_lines():
    try:
        from datetime import datetime
        from core.storage_manager import load_state

        events = list((load_state() or {}).get("volatility_events", []) or [])
    except Exception:
        events = []
    if not events:
        return []

    lines = ["## PHANH BIẾN ĐỘNG ĐÃ KÍCH HOẠT", ""]
    for event in reversed(events[-30:]):
        try:
            stamp = datetime.fromtimestamp(
                float(event.get("triggered_at", 0.0) or 0.0)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            stamp = "—"
        direction = "TĂNG" if event.get("direction") == "UP" else "GIẢM"
        movement = (
            f"{float(event.get('change_points', 0.0) or 0.0):+.2f} điểm"
            if event.get("threshold_unit") == "POINTS"
            else f"{float(event.get('change_pct', 0.0) or 0.0):+.2f}%"
        )
        action = str(event.get("action") or "CLOSE_ALL").upper()
        action_text = {
            "ALERT_ONLY": "chỉ cảnh báo",
            "BLOCK_NEW_EXPOSURE": (
                f"chặn BOT tăng vị thế {float(event.get('cooldown_hours', 0.0) or 0.0):g} giờ"
            ),
            "CLOSE_ALL": (
                f"đóng {int(event.get('closed_positions', 0) or 0)}, "
                f"đóng lỗi {int(event.get('failed_positions', 0) or 0)}; "
                f"Global Cooldown {float(event.get('cooldown_hours', 0.0) or 0.0):g} giờ"
            ),
        }.get(action, "chỉ cảnh báo")
        lines.append(
            f"- `{stamp}` **{event.get('symbol', '—')} {direction} {movement}** trong "
            f"{float(event.get('window_seconds', 0.0) or 0.0):.0f}s; "
            f"hành động: {action_text}."
        )
    lines.append("")
    return lines


def build_compact_summary(cache=None, report_days=None):
    cache = cache or scan_cache.load_cache()
    symbols = cache.get("symbols", {})
    if not symbols:
        return ""
    parts = ["# SCAN SUMMARY — CHECK ĐỘNG", "", AI_INSTRUCTIONS, "",
             f"Cập nhật: {cache.get('updated_at') or '—'} | Số mã: {len(symbols)}", ""]
    parts.extend(_volatility_event_lines())
    parts.extend(_market_overview(symbols, report_days))
    for symbol in sorted(symbols):
        days = _slice_days(symbols[symbol], report_days)
        if not days:
            continue
        day, entry = days[-1]
        parts.extend([f"## {symbol}", *_daily_header(day, entry)])
        parts.extend(_segments_lines(entry))
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def render_full_report(cache=None, report_days=None):
    cache = cache or scan_cache.load_cache()
    symbols = cache.get("symbols", {})
    if not symbols:
        return ""
    parts = ["# SCAN REPORT — DỮ LIỆU NGÀY VÀ CHECK ĐỘNG", "", AI_INSTRUCTIONS, "",
             f"Cập nhật: {cache.get('updated_at') or '—'} | Số mã: {len(symbols)}", ""]
    parts.extend(_volatility_event_lines())
    parts.extend(_market_overview(symbols, report_days))
    def _liquidity_order(symbol):
        days = _slice_days(symbols[symbol], report_days)
        latest = days[-1][1] if days else {}
        status = str((latest.get("liquidity_60d") or {}).get("status") or "")
        rank = {"PASS": 0, "INSUFFICIENT": 1, "ERROR": 2, "FAIL": 3}.get(status, 1)
        return rank, symbol

    for symbol in sorted(symbols, key=_liquidity_order):
        days = _slice_days(symbols[symbol], report_days)
        if not days:
            continue
        parts.extend([f"## {symbol} — {len(days)} ngày giao dịch", "", "### Biến động toàn khoảng"])
        parts.extend(_period_summary(days))
        parts.append("")
        check_configs = _check_config_lines(days)
        check_range = _range_summary(days)
        if check_configs or check_range:
            parts.extend(["### CHECK trong khoảng", ""])
            parts.extend(check_configs)
            parts.extend(check_range)
            parts.append("")
        latest_day, latest_entry = days[-1]
        parts.extend(["### Ngày mới nhất", ""])
        parts.extend(_daily_header(latest_day, latest_entry))
        parts.append(
            "- Chi tiết CHECK đã được gộp ở mục `CHECK trong khoảng` phía trên "
            "để không lặp lại cùng dữ liệu."
        )
        parts.append("")
        parts.extend(_history_table(days))
    return "\n".join(parts).strip() + "\n"


def append_volatility_event_to_existing_reports(event):
    """Ghi ngay vào các báo cáo CKCS đã tồn tại; tuyệt đối không tạo thêm file MD."""
    try:
        from datetime import datetime

        stamp = datetime.fromtimestamp(
            float(event.get("triggered_at", 0.0) or 0.0)
        ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        stamp = "—"
    marker = (
        f"<!-- VOLATILITY_BRAKE:{event.get('symbol', '')}:"
        f"{int(float(event.get('triggered_at', 0.0) or 0.0))} -->"
    )
    direction = "TĂNG" if event.get("direction") == "UP" else "GIẢM"
    movement = (
        f"{float(event.get('change_points', 0.0) or 0.0):+.2f} điểm"
        if event.get("threshold_unit") == "POINTS"
        else f"{float(event.get('change_pct', 0.0) or 0.0):+.2f}%"
    )
    action = str(event.get("action") or "CLOSE_ALL").upper()
    action_text = {
        "ALERT_ONLY": "Chỉ cảnh báo; không sửa lệnh và không khóa BOT.",
        "BLOCK_NEW_EXPOSURE": (
            f"Chặn BOT tăng vị thế trong "
            f"{float(event.get('cooldown_hours', 0.0) or 0.0):g} giờ."
        ),
        "CLOSE_ALL": (
            f"Đã đóng {int(event.get('closed_positions', 0) or 0)} vị thế; "
            f"đóng lỗi {int(event.get('failed_positions', 0) or 0)}. "
            f"Global Cooldown "
            f"{float(event.get('cooldown_hours', 0.0) or 0.0):g} giờ."
        ),
    }.get(action, "Chỉ cảnh báo.")
    block = (
        f"\n\n{marker}\n"
        "## CẢNH BÁO PHANH BIẾN ĐỘNG\n\n"
        f"- `{stamp}` **{event.get('symbol', '—')} {direction} {movement}** trong "
        f"{float(event.get('window_seconds', 0.0) or 0.0):.0f} giây.\n"
        f"- Hành động: {action_text}\n"
    )
    candidates = [paths.scan_report_path()]
    updated = []
    with _REPORT_LOCK:
        for report_path in candidates:
            if not os.path.isfile(report_path):
                continue
            try:
                with open(report_path, "r", encoding="utf-8", errors="replace") as handle:
                    existing = handle.read()
                if marker in existing:
                    continue
                with open(report_path, "a", encoding="utf-8") as handle:
                    handle.write(block)
                updated.append(report_path)
            except OSError:
                continue
    return updated


def export_ckcs_report(
    report_days=None,
    output_path=None,
    report_label=None,
    report_session=None,
    require_current_session=False,
):
    """Tạo/ghi đè một scan_report.md từ kho RAW nội bộ."""
    cache = scan_cache.load_cache()
    selected = set(scan_cache.selected_research_symbols())
    cache = dict(cache)
    cache["symbols"] = {
        symbol: node
        for symbol, node in (cache.get("symbols", {}) or {}).items()
        if str(symbol).upper() in selected
    }
    if not cache.get("symbols"):
        return None
    quality = None
    if report_session:
        quality = scan_cache.report_session_quality(
            cache,
            report_session,
            selected_symbols=selected,
        )
        if require_current_session and not quality["ready"]:
            session_key = str(report_session or "").strip().lower()
            signature = (
                quality["today"],
                quality["covered_entries"],
                quality["expected_symbols"],
                quality["latest_day"],
            )
            # Scheduler có thể thử lại nhiều lần trong giờ nghỉ trưa. Chỉ ghi
            # cảnh báo khi chất lượng kho thực sự thay đổi để không spam console.
            if _REPORT_SKIP_LOG_SIGNATURES.get(session_key) != signature:
                logger.warning(
                    "CKCS %s report skipped: current=%s covered=%s/%s latest=%s",
                    report_session,
                    quality["today"],
                    quality["covered_entries"],
                    quality["expected_symbols"],
                    quality["latest_day"],
                )
                _REPORT_SKIP_LOG_SIGNATURES[session_key] = signature
            return None
        if report_session:
            _REPORT_SKIP_LOG_SIGNATURES.pop(
                str(report_session or "").strip().lower(),
                None,
            )
    paths.ensure_ckcs_research_dir()
    report_text = render_full_report(cache, report_days=report_days)
    if report_label:
        quality_lines = []
        if quality:
            quality_lines.append(
                f"> Ngày tạo: {quality['today']} | Ngày dữ liệu mới nhất: "
                f"{quality['latest_day'] or '—'} | Đủ lượt quét phiên: "
                f"{quality['covered_entries']}/{quality['expected_symbols']} mã."
            )
            if not quality["ready"]:
                quality_lines.append(
                    "> **CẢNH BÁO: Kho RAW chưa có lượt quét phù hợp của phiên hôm nay; "
                    "không dùng file này như báo cáo hiện tại.**"
                )
        report_text = (
            f"> Báo cáo tự động: {str(report_label).strip()}\n"
            + "\n".join(quality_lines)
            + ("\n" if quality_lines else "")
            + "> Đây là báo cáo mới nhất được ghi đè từ kho RAW; lịch sử nhiều ngày vẫn nằm trong báo cáo.\n\n"
            + report_text
        )
    if output_path:
        report_path = output_path
    else:
        report_path = paths.scan_report_path()
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    tmp_path = f"{report_path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(report_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, report_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    for legacy_path in paths.legacy_scan_session_report_paths():
        if os.path.abspath(legacy_path) == os.path.abspath(report_path):
            continue
        try:
            if os.path.isfile(legacy_path):
                os.remove(legacy_path)
        except OSError:
            pass
    requested_days = max(1, int(report_days or 1))
    included_days = {
        day
        for node in cache["symbols"].values()
        for day, _entry in _slice_days(node, requested_days)
    }
    return {
        "report": os.path.abspath(report_path),
        "symbols": len(cache["symbols"]),
        "days": len(included_days),
        "requested_days": requested_days,
        "quality": quality,
    }
