from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from ..exit_modes import exit_mode_label
from .models import BacktestResult, BacktestTrade


# One short name per exit reason.  The old export carried both "Sự kiện" and
# "Lý do" for the same fact, which made every row read twice.
_EVENT_NAMES = {
    "ENTRY_BUY": "MUA",
    "STOP_LOSS": "SL",
    "TAKE_PROFIT": "TP",
    "INDICATOR_EXIT": "E",
    "NORMAL_PROTECTION": "PROTECT",
    "PRICE_PROTECTION": "PROTECT",
}

ROUND_HEADERS = (
    "LẦN CHẠY", "LƯỢT", "MÃ", "VÀO", "GIÁ VÀO", "KHỐI LƯỢNG", "VỐN", "CẮT LỖ",
    "EMA VÀO", "RSI VÀO", "RA", "PHIÊN", "THOÁT BỞI", "EMA RA", "RSI RA",
    "PHÍ+THUẾ", "LÃI/LỖ", "%", "PROTECT MODE", "ARM LÚC", "GIÁ ARM",
    "MFE SAU ARM %", "+ TRÊN ARM %", "LN THOÁT %", "TRẢ LẠI %", "EXIT MODE",
    "KẾT QUẢ",
)


def round_headers(result: BacktestResult) -> tuple[str, ...]:
    """Label the two EMA values with the configured fast/slow periods."""
    params = result.config.rule_parameters
    buy_fast = int(params.get("buy_ema_fast", params.get("ema_fast", 3)))
    buy_slow = int(params.get("buy_ema_slow", params.get("ema_slow", 6)))
    sell_fast = int(params.get("sell_ema_fast", buy_fast))
    sell_slow = int(params.get("sell_ema_slow", buy_slow))
    headers = list(ROUND_HEADERS)
    headers[8] = f"EMA{buy_fast} / EMA{buy_slow} VÀO"
    headers[13] = f"EMA{sell_fast} / EMA{sell_slow} RA"
    return tuple(headers)

SIGNAL_HEADERS = (
    "LẦN CHẠY", "TÍN HIỆU LÚC", "TRẠNG THÁI LÚC", "MÃ", "TÍN HIỆU", "QUYẾT ĐỊNH", "SỰ KIỆN",
    "LÝ DO", "EMA NHANH", "EMA CHẬM", "RSI", "THỊ TRƯỜNG", "CHẾ ĐỘ",
    "NGUỒN", "CHẤT LƯỢNG", "XÁC NHẬN BUY", "KHUNG GIỜ MUA",
)

FILL_HEADERS = (
    "LẦN CHẠY", "TÍN HIỆU LÚC", "XÁC NHẬN LÚC", "KHỚP LÚC", "MÃ", "MUA/BÁN", "SỰ KIỆN",
    "KHỐI LƯỢNG", "GIÁ", "PHÍ", "THUẾ", "EMA NHANH", "EMA CHẬM", "RSI",
    "CHẾ ĐỘ", "NGUỒN", "CHẤT LƯỢNG", "LÝ DO",
)

SIGNAL_HEADERS = SIGNAL_HEADERS + (
    "PROTECT MODE", "PROTECT STATE", "MFE %", "PEAK", "EFFECTIVE TRAIL %",
    "ATR14 T-1 %", "ATR MULTIPLIER", "PROTECT PRICE", "SELL %",
    "HYPOTHETICAL QUANTITY",
)
FILL_HEADERS = FILL_HEADERS + ("WAITED T+2",)
PROFIT_PATH_HEADERS = (
    "Láº¦N CHáº Y", "LÆ¯á»¢T", "MÃƒ", "THá»œI ÄIá»‚M", "NGUá»’N",
    "OPEN %", "HIGH %", "LOW %", "CLOSE %",
)
TRADE_PATH_METRIC_HEADERS = (
    "Láº¦N CHáº Y", "LÆ¯á»¢T", "MÃƒ", "MFE %", "MAE %", "PEAK LÃšC",
    "VÃ€Oâ†’PEAK (GIá»œ)", "PEAKâ†’RA (GIá»œ)", "MAX GIVEBACK %",
)


def event_name(event: str) -> str:
    parts = [_EVENT_NAMES.get(value.strip().upper(), value.strip())
             for value in str(event or "").split("+") if value.strip()]
    return "+".join(dict.fromkeys(parts))


def exit_detail(trade: BacktestTrade) -> str:
    """Spell out each sell so no blended average price is ever reported."""
    if not trade.exit_fills:
        return "CÒN MỞ"
    detail = " + ".join(
        f"{event_name(fill.get('event', ''))} {int(fill.get('quantity', 0)):,}"
        f"@{float(fill.get('price', 0.0)):.2f}"
        for fill in trade.exit_fills
    )
    # A partly sold position is still open; say so instead of letting the last
    # fill read like the trade was closed.
    if trade.outcome == "OPEN" and trade.remaining_quantity > 0:
        return f"{detail} · CÒN GIỮ {trade.remaining_quantity:,}"
    return detail


def round_rows(results: list[BacktestResult]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for result in results:
        label = result.config.run_name or result.run_id
        for trade in sorted(result.trades, key=lambda item: (item.opened_date, item.cycle_id)):
            rows.append((
                label, trade.cycle_id, trade.symbol, trade.opened_date,
                round(trade.avg_entry_price, 2), trade.entry_quantity, round(trade.entry_value),
                round(trade.stop_price, 2),
                f"{trade.entry_ema_fast:.2f}/{trade.entry_ema_slow:.2f}",
                round(trade.entry_rsi, 1),
                trade.closed_date or "—", trade.sessions_held, exit_detail(trade),
                f"{trade.exit_ema_fast:.2f}/{trade.exit_ema_slow:.2f}" if trade.exit_fills else "—",
                round(trade.exit_rsi, 1) if trade.exit_fills else "—",
                round(trade.fees + trade.tax), round(trade.net_pnl), round(trade.pnl_pct, 2),
                trade.normal_policy, trade.normal_arm_time or "—",
                round(trade.normal_arm_price, 2) if trade.normal_arm_price else "—",
                round(trade.mfe_after_arm_pct, 2) if trade.normal_arm_time else "—",
                round(trade.mfe_extra_pct, 2) if trade.normal_arm_time else "—",
                round(trade.exit_profit_pct, 2),
                round(trade.profit_giveback_pct, 2) if trade.normal_arm_time else "—",
                event_name(trade.exit_mode) or "—",
                {"WIN": "THẮNG", "LOSS": "LỖ"}.get(trade.outcome, "CÒN MỞ"),
            ))
    return rows


def info_rows(results: list[BacktestResult]) -> list[tuple[str, Any]]:
    """One column per run, not one block per run.

    Stacking a full parameter dump per run repeated the same forty lines four
    times over.  Everything that differs goes side by side; everything shared
    is stated once at the bottom.
    """
    def short(name: str) -> str:
        return (name or "").rsplit("·", 1)[0].strip().rsplit("·", 1)[-1].strip() or name

    per_run: list[tuple[str, list[Any]]] = []
    def line(title: str, value) -> None:
        per_run.append((title, [value(r) for r in results]))

    def stats(result: BacktestResult) -> dict[str, Any]:
        closed = [x for x in result.trades if x.outcome in {"WIN", "LOSS"}]
        held = sorted(x.sessions_held for x in closed)
        exits: dict[str, int] = {}
        for trade in result.trades:
            for fill in trade.exit_fills:
                name = event_name(fill.get("event", ""))
                exits[name] = exits.get(name, 0) + 1
        return {"held": held, "exits": exits, "open": len(result.trades) - len(closed)}

    box = {id(r): stats(r) for r in results}

    def phase_of(result: BacktestResult) -> str:
        s = result.config
        if s.auto_market_phase:
            return "Tự nhận diện"
        if not s.use_market_phase:
            return f"Không dùng · {s.fixed_exposure_pct:g}%"
        return f"{s.fixed_market_phase} · {s.fixed_exposure_pct:g}%"

    def protect_of(result: BacktestResult) -> str:
        values = result.config.rule_parameters or {}
        sell = float(values.get("normal_sell_pct", 100.0) or 100.0)
        repeat = bool(values.get("normal_repeat_enabled", False) and sell < 100.0)
        return (
            f"{values.get('normal_policy', 'AUTO')} · "
            f"ARM {values.get('normal_arm_pct')}% · "
            f"TRAIL {values.get('normal_giveback_pct')}% · "
            f"SELL {sell:g}% · "
            f"DYNAMIC {'ON' if values.get('normal_dynamic_enabled') else 'OFF'}"
            f" (ATR×{float(values.get('normal_atr_multiplier', 0.6) or 0.6):g}) · "
            f"REPEAT {'ON' if repeat else 'OFF'}"
        )

    line("Giai đoạn", lambda r: f"{r.config.start_date} → {r.config.end_date}")
    line("Chế độ mô phỏng", lambda r: r.config.simulation_mode)
    line("Fallback 1D", lambda r: int((r.data_quality or {}).get("fallback_count", 0) or 0))
    line("Cảnh báo dữ liệu", lambda r: " | ".join(r.warnings) or "—")
    line("Phase 1 · tỷ trọng", phase_of)
    line("Mã", lambda r: ", ".join(r.config.symbols))
    line("Sàn", lambda r: " · ".join(
        f"{symbol}:{r.config.symbol_exchanges.get(symbol, '?')}" for symbol in r.config.symbols
    ))
    line("Tối đa số mã", lambda r: (r.config.rule_parameters or {}).get("max_positions"))
    line("Bảo vệ", lambda r: ", ".join(
        exit_mode_label(mode) for mode in r.config.em_modes if exit_mode_label(mode)
    ) or "chỉ cắt lỗ")
    line("PROTECT", protect_of)
    line("Khóa sau LOSS", lambda r: (
        f"{(r.config.rule_parameters or {}).get('loss_lock_count')} LOSS · {r.config.loss_lock_hours} giờ"
        if r.config.loss_lock_enabled else "OFF"
    ))
    line("Whipsaw", lambda r: (
        f"{(r.config.rule_parameters or {}).get('whipsaw_n')} lần / "
        f"{(r.config.rule_parameters or {}).get('whipsaw_x')} phiên"
        if r.config.whipsaw_enabled else "OFF"
    ))
    line("Vốn đầu", lambda r: round(r.initial_capital))
    line("Vốn cuối", lambda r: round(r.final_equity))
    line("Lãi/lỗ", lambda r: f"{round(r.net_pnl):,} ({r.return_pct:+.2f}%)")
    line("Sụt đỉnh", lambda r: f"{r.max_drawdown_pct:.2f}%")
    line("Lượt đã đóng", lambda r: r.closed_trades)
    line("Lượt còn mở", lambda r: box[id(r)]["open"])
    line("Thắng", lambda r: f"{r.win_count}/{r.closed_trades} ({r.win_rate_pct:.0f}%)")
    line("Phiên giữ trung vị", lambda r: (lambda h: h[len(h) // 2] if h else 0)(box[id(r)]["held"]))
    line("Phiên giữ trung bình", lambda r: (lambda h: round(sum(h) / len(h), 1) if h else 0)(box[id(r)]["held"]))
    line("Phí + thuế", lambda r: round(r.total_fees + r.total_tax))
    line("Thoát bởi", lambda r: " · ".join(
        f"{k} {v}" for k, v in sorted(box[id(r)]["exits"].items())) or "—")

    rows: list[tuple[str, Any]] = [("MỤC", *[short(r.config.run_name or r.run_id) for r in results])]
    if len(results) > 1:
        independent = all(
            (result.data_quality or {}).get("comparison") == "INDEPENDENT_EXIT_POLICY"
            for result in results
        )
        rows.append((
            "CÁCH ĐỌC",
            (
                "Mỗi cột khởi chạy độc lập với cùng vốn đầu; chỉ policy thoát thay đổi."
                if independent else
                "Các cột nối tiếp nhau trên cùng một tài khoản: vốn cuối của cột trước "
                "là vốn đầu của cột sau. Lượt còn mở ở cuối mỗi cột được định giá theo "
                "giá đóng cửa ngày cuối rồi quy về tiền cho cột kế tiếp."
            ),
        ))
    rows.extend((title, *values) for title, values in per_run)

    first = results[0].config
    params = first.rule_parameters or {}
    buy_terms = [
        value for enabled, value in (
            (params.get("buy_signal_use_ema", True), f"EMA{params.get('buy_ema_fast')} cắt lên EMA{params.get('buy_ema_slow')}"),
            (params.get("buy_signal_use_rsi", True), f"RSI{params.get('rsi_period')} tăng"),
        ) if enabled
    ]
    sell_terms = [
        value for enabled, value in (
            (params.get("sell_signal_use_ema", True), f"EMA{params.get('sell_ema_fast')} cắt xuống EMA{params.get('sell_ema_slow')}"),
            (params.get("sell_signal_use_rsi", True), f"RSI{params.get('rsi_period')} giảm"),
        ) if enabled
    ]
    rows.append(("", ))
    rows.append(("GIỐNG NHAU Ở MỌI LẦN CHẠY", ))
    shared = [
        ("Đọc VNINDEX", f"MA{params.get('ma_period')} · vùng MA {params.get('ma_zone_pct')}% · "
                         f"xác nhận {params.get('confirm_sessions')} phiên"),
        ("Pivot", f"trái {params.get('pivot_left')} · phải {params.get('pivot_right')} · "
                  f"ngang {params.get('pivot_horizontal_pct')}%"),
        ("Tỷ trọng 4 state", " · ".join(
            f"{state} {float(value) * 100:g}%"
            for state, value in (params.get("exposure") or {}).items()
        )),
        ("Volume", (
            f"HIỆN ĐỘ TIN CẬY · TB {params.get('volume_average_sessions')} phiên · "
            f"cao {params.get('high_volume_ratio')} · thấp {params.get('low_volume_ratio')}"
            if params.get("volume_confirmation") else "OFF"
        )),
        ("Mua", " và ".join(buy_terms) or "OFF"),
        ("Cắt lỗ", f"{params.get('initial_sl_pct')}% · vào lại {params.get('reentry_sl_pct')}%"
                   f" · bán sạch · luôn bật"),
        ("Ngưỡng TP", f"{params.get('take_profit_pct')}% · bán sạch nếu tactic TP bật"),
        ("Điều kiện E", f"{' và '.join(sell_terms) or 'OFF'} · bán hết phần còn lại nếu tactic bật"),
        ("Khung giờ mua", (
            f"Từ {params.get('buy_window_start', '14:00')} đến hết phiên hợp lệ · giờ Việt Nam"
            if params.get("buy_window_enabled") else "OFF"
        )),
        ("Xác nhận BUY", (
            f"{params.get('buy_confirmation_minutes', 5)} phút · "
            + "+".join(name for name, enabled in (
                ("EMA", params.get("buy_confirmation_require_ema", True)),
                ("RSI", params.get("buy_confirmation_require_rsi", True)),
            ) if enabled)
            if params.get("buy_confirmation_enabled") else "OFF"
        )),
        (
            "Bán khi cổ về T+2",
            "từ 13:00 phiên chiều · "
            + ("kiểm tra lại điều kiện" if first.sell_wait_policy == "RECHECK" else "vẫn bán"),
        ),
        ("Không compound", "ON" if params.get("no_compound_enabled") else "OFF"),
        ("Auto 100 CP", "ON" if params.get("force_min_lot_enabled") else "OFF"),
        ("Nến tín hiệu", (
            "1D đang chạy, dựng lại sau từng nến nguồn"
            if first.simulation_mode in {"REPLAY", "AUTO_HYBRID"}
            else "1D đã đóng cửa"
        )),
        ("Thời điểm khớp", (
            "Open nến nguồn hợp lệ kế tiếp sau khi tín hiệu xuất hiện"
            if first.simulation_mode in {"REPLAY", "AUTO_HYBRID"}
            else (
                "Open phiên kế tiếp; SELL chờ T+2 sang phiên kế tiếp vì DAILY không có giá chiều T+2"
            )
        )),
        ("Khối lượng", "bội 100, làm tròn xuống"),
        ("Trượt giá", "không mô phỏng"),
        ("Phí mua / phí bán / thuế bán",
         f"{first.buy_fee_rate * 100:g}% / {first.sell_fee_rate * 100:g}% / {first.sell_tax_rate * 100:g}%"),
    ]
    rows.extend(shared)
    return rows


def sheet_name(run_name: str, used: set[str]) -> str:
    """Excel tab names cap at 31 chars and drop the characters Excel forbids."""
    tail = (run_name or "").rsplit("·", 1)[0].strip().rsplit("·", 1)[-1].strip()
    clean = re.sub(r"[:\/?*\[\]]", "-", tail or run_name or "LƯỢT")[:31].strip() or "LƯỢT"
    name, index = clean, 2
    while name in used:
        suffix = f" {index}"
        name = clean[: 31 - len(suffix)] + suffix
        index += 1
    used.add(name)
    return name


def common_label(names: list[str]) -> str:
    """Return the tail every run name shares, e.g. GỐC from 'HSG · GĐ1 · GỐC'.

    Batches that differ only by configuration would otherwise all land on the
    same file name, since symbol, window and run count are identical.
    """
    tails = {name.rsplit("·", 1)[-1].strip() for name in names if "·" in name}
    return tails.pop() if len(tails) == 1 else ""


def workbook_name(
    mode: str, *, symbols: list[str], start: str, end: str, runs: int, stamp: str,
    label: str = "",
) -> str:
    """Name the file after what is inside it, not after a random run id.

    Takes plain values so the popup can preview the name before a run starts.
    """
    unique = list(dict.fromkeys(symbols))
    who = unique[0] if len(unique) == 1 else f"{len(unique)}ma"
    if runs > 1:
        who = f"{unique[0]} · {runs} kich ban" if len(unique) == 1 else f"{runs} kich ban"
    raw = f"{mode} · {label + ' · ' if label else ''}{who} · {start} den {end} · {stamp}"
    return re.sub(r"[^0-9A-Za-z·\- _]", "", raw).strip() + ".xlsx"


def export_run_excel(results: BacktestResult | list[BacktestResult], directory: str | Path,
                     *, mode: str = "BACKTEST", stamp: str = "") -> Path:
    """Write one workbook per mode: sheet LƯỢT for data, sheet THÔNG TIN for settings."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover - dependency ships in the app venv
        raise RuntimeError("Thiếu thư viện openpyxl để xuất Excel.") from exc

    items = list(results) if isinstance(results, list) else [results]
    if not items:
        raise ValueError("Không có kết quả nào để xuất.")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / workbook_name(
        mode,
        symbols=[s for item in items for s in item.config.symbols],
        start=min(item.config.start_date for item in items),
        end=max(item.config.end_date for item in items),
        runs=len(items),
        stamp=stamp or items[-1].run_id.split("-", 1)[-1],
        label=common_label([item.config.run_name or "" for item in items]),
    )

    book = Workbook()
    book.remove(book.active)
    # One sheet per run instead of one long sheet: four scenarios all restart
    # their cycle at HSG-01, so mixing them made the rows unreadable.
    used: set[str] = set()
    for item in items:
        name = sheet_name(item.config.run_name or item.run_id, used)
        sheet = book.create_sheet(name)
        sheet.append(round_headers(item)[1:])
        for row in round_rows([item]):
            sheet.append(row[1:])

    signal_items = [item for item in items if item.signals]
    if signal_items:
        signals = book.create_sheet("TÍN HIỆU")
        signals.append(SIGNAL_HEADERS)
        for item in signal_items:
            label = item.config.run_name or item.run_id
            for row in item.signals:
                indicators = (row.get("details") or {}).get("indicators") or {}
                confirmation = (row.get("details") or {}).get("buy_confirmation") or {}
                window = (row.get("details") or {}).get("buy_window") or {}
                confirmation_text = ""
                if confirmation:
                    confirmation_text = (
                        f"{int(float(confirmation.get('minutes_held', 0) or 0))}/"
                        f"{int(float(confirmation.get('minutes_required', 0) or 0))}P · "
                        f"EMA{'✓' if confirmation.get('ema_ok') else '×'} "
                        f"RSI{'✓' if confirmation.get('rsi_ok') else '×'}"
                    )
                signals.append((
                    label, row.get("signal_time") or row.get("time") or row.get("date"),
                    row.get("decision_time") or row.get("time") or row.get("date"),
                    row.get("symbol"), row.get("signal"), row.get("action"), row.get("event"),
                    row.get("reason"), indicators.get("buy_ema_fast", indicators.get("ema_fast", 0.0)),
                    indicators.get("buy_ema_slow", indicators.get("ema_slow", 0.0)), indicators.get("rsi", 0.0),
                    row.get("market_state"), row.get("simulation_mode"),
                    row.get("source_resolution"), row.get("data_quality"), confirmation_text,
                    f"{window['start']}–{window['end']}" if window else "",
                    (row.get("details") or {}).get("normal_policy", ""),
                    (row.get("details") or {}).get("normal_state", ""),
                    (row.get("details") or {}).get("normal_mfe_pct", ""),
                    (row.get("details") or {}).get("normal_peak_price", ""),
                    (row.get("details") or {}).get("normal_effective_trail_pct", ""),
                    (row.get("details") or {}).get("normal_atr_pct", ""),
                    (row.get("details") or {}).get("normal_atr_multiplier", ""),
                    (row.get("details") or {}).get("normal_trigger_price", ""),
                    (row.get("details") or {}).get("sell_share_pct", ""),
                    (row.get("details") or {}).get("hypothetical_quantity", ""),
                ))

    event_items = [item for item in items if item.events]
    if event_items:
        fills = book.create_sheet("KHỚP LỆNH")
        fills.append(FILL_HEADERS)
        for item in event_items:
            label = item.config.run_name or item.run_id
            for event in item.events:
                fills.append((
                    label, event.signal_time or event.signal_date,
                    event.decision_time or event.signal_time or event.signal_date,
                    event.fill_time or event.date,
                    event.symbol, event.side, event.event, event.quantity, event.price,
                    event.fee, event.tax, event.ema_fast, event.ema_slow, event.rsi,
                    event.simulation_mode, event.source_resolution, event.data_quality,
                    event.reason, bool((event.details or {}).get("settlement_waited", False)),
                ))

    path_items = [
        (item, trade) for item in items for trade in item.trades if trade.profit_path
    ]
    if path_items:
        metrics = book.create_sheet("PROTECT METRICS")
        metrics.append(TRADE_PATH_METRIC_HEADERS)
        path_sheet = book.create_sheet("PROFIT PATH")
        path_sheet.append(PROFIT_PATH_HEADERS)
        for item, trade in path_items:
            label = item.config.run_name or item.run_id
            metrics.append((
                label, trade.cycle_id, trade.symbol,
                round(trade.peak_profit_pct, 4), round(trade.mae_profit_pct, 4),
                trade.peak_at, round(trade.entry_to_peak_hours, 4),
                round(trade.peak_to_exit_hours, 4), round(trade.max_giveback_pct, 4),
            ))
            for point in trade.profit_path:
                path_sheet.append((
                    label, trade.cycle_id, trade.symbol, point.get("time", ""),
                    point.get("source_resolution", ""),
                    point.get("open_profit_pct", 0.0), point.get("high_profit_pct", 0.0),
                    point.get("low_profit_pct", 0.0), point.get("close_profit_pct", 0.0),
                ))

    info = book.create_sheet("THÔNG TIN")
    for row in info_rows(items):
        info.append(row)

    header_fill = PatternFill("solid", fgColor="1B1F25")
    header_font = Font(color="FFFFFF", bold=True)
    for sheet in book.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        for column in range(1, sheet.max_column + 1):
            values = [
                str(sheet.cell(row=row, column=column).value or "")
                for row in range(1, min(sheet.max_row, 300) + 1)
            ]
            sheet.column_dimensions[get_column_letter(column)].width = min(
                44, max(11, max((len(value) for value in values), default=11) + 2)
            )
    book.save(path)
    return path
