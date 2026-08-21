from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .models import BacktestResult, BacktestTrade


# One short name per exit reason.  The old export carried both "Sự kiện" and
# "Lý do" for the same fact, which made every row read twice.
_EVENT_NAMES = {
    "ENTRY_BUY": "MUA",
    "STOP_LOSS": "SL",
    "TAKE_PROFIT": "TP",
    "INDICATOR_EXIT": "EXIT",
    "NORMAL_PROTECTION": "NORMAL",
    "HIGH_PROFIT_PROTECTION": "HIGH",
    "PRICE_PROTECTION": "NORMAL",
}

ROUND_HEADERS = (
    "LẦN CHẠY", "LƯỢT", "MÃ", "VÀO", "GIÁ VÀO", "KHỐI LƯỢNG", "VỐN", "CẮT LỖ",
    "EMA VÀO", "RSI VÀO", "RA", "PHIÊN", "THOÁT BỞI", "EMA RA", "RSI RA",
    "PHÍ+THUẾ", "LÃI/LỖ", "%", "KẾT QUẢ",
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

    line("Giai đoạn", lambda r: f"{r.config.start_date} → {r.config.end_date}")
    line("Phase 1 · tỷ trọng", phase_of)
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
        rows.append((
            "CÁCH ĐỌC",
            "Các cột nối tiếp nhau trên cùng một tài khoản: vốn cuối của cột trước "
            "là vốn đầu của cột sau. Lượt còn mở ở cuối mỗi cột được định giá theo "
            "giá đóng cửa ngày cuối rồi quy về tiền cho cột kế tiếp.",
        ))
    rows.extend((title, *values) for title, values in per_run)

    first = results[0].config
    params = first.rule_parameters or {}
    em = first.em_modes
    rows.append(("", ))
    rows.append(("GIỐNG NHAU Ở MỌI LẦN CHẠY", ))
    shared = [
        ("Mã", ", ".join(first.symbols)),
        ("Tối đa số mã", params.get("max_positions")),
        ("Bảo vệ đang bật", ", ".join(em) or "không, chỉ có cắt lỗ"),
        ("Mua", f"EMA{params.get('buy_ema_fast')} cắt lên EMA{params.get('buy_ema_slow')}"
                f" và RSI{params.get('rsi_period')} tăng"),
        ("Cắt lỗ", f"{params.get('initial_sl_pct')}% · vào lại {params.get('reentry_sl_pct')}%"
                   f" · bán sạch · luôn bật"),
        ("Chốt lời TP", f"lãi ≥ {params.get('take_profit_pct')}% · bán sạch"
                        if "TP" in em else "OFF"),
        ("Normal", f"lãi từng ≥ {params.get('normal_arm_pct')}% rồi giá giảm"
                   f" {params.get('normal_giveback_pct')}% khỏi đỉnh · bán"
                   f" {params.get('normal_sell_pct', 33)}% · một lần"
                   if "NORMAL" in em else "OFF"),
        ("High", f"lãi từng ≥ {params.get('high_profit_arm_pct')}% rồi close giảm"
                 f" {params.get('high_profit_close_drawdown_pct')}% khỏi đỉnh close · bán"
                 f" {params.get('high_sell_pct', 33)}% · một lần"
                 if "HIGH" in em else "OFF"),
        ("Exit", f"EMA{params.get('sell_ema_fast')} cắt xuống EMA{params.get('sell_ema_slow')}"
                 f" và RSI{params.get('rsi_period')} giảm · bán hết phần còn lại"
                 if "IND_EXIT" in em else "OFF"),
        ("Khóa sau 3 lệnh thua", f"{first.loss_lock_hours} giờ" if first.loss_lock_enabled else "OFF"),
        ("Chống nhiễu (whipsaw)", f"EMA cắt qua lại {params.get('whipsaw_n')} lần trong"
                        f" {params.get('whipsaw_x')} phiên thì khóa mua"
                        if first.whipsaw_enabled else "OFF"),
        ("Bán khi cổ về T+2", "kiểm tra lại điều kiện" if first.sell_wait_policy == "RECHECK" else "vẫn bán"),
        ("Không compound", "ON" if params.get("no_compound_enabled") else "OFF"),
        ("Nến tín hiệu", "1D đã đóng cửa"),
        ("Thời điểm khớp",
         "phiên kế tiếp, sau 9h15 như bot thật (ATO tắt)"
         if first.fill_session == "CONTINUOUS"
         else "phiên kế tiếp, giá mở cửa — tức giá đợt ATO"),
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
        who = f"{runs} kich ban"
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
        sheet.append(ROUND_HEADERS[1:])
        for row in round_rows([item]):
            sheet.append(row[1:])

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
