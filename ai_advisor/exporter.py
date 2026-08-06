# -*- coding: utf-8 -*-
import json
import os
import shutil

from . import config_snapshot, history, paths


USER_CONTEXT_TEMPLATE = """# RAT-CKVN AI Advisor Context

## Bot overview
RAT-CKVN is a DNSE OpenAPI trading bot/workstation for CKPS (VN30F) and CKCS cash stocks. It supports manual orders, bot signals, DCA/PCA, TSL, BE, BE_CASH, REV_C, safeguards, Entry/Exit tactics, T+2 settlement, and multi-timeframe context.

## Current operating goal

## Acceptable drawdown

## Priority symbols

## Suspected rule

## Market context notes

## Things AI should not suggest

## Test notes
Examples: testing REV_C, BE_CASH, TSL, DCA/PCA, Entry/Exit, T+2 settlement.
"""
EXPERT_CONTEXT_TEMPLATE = """# Expert Context

Đặt ghi chú/tổng hợp tài liệu chuyên gia tại đây. AI phải xem đây là nguồn tham khảo
do operator cung cấp, đối chiếu với dữ liệu CHECK và thông tin thị trường mới nhất.

## Phạm vi và ngày cập nhật

## Mã liên quan

## Nội dung chuyên gia

## Giả định hoặc rủi ro cần đối chiếu
"""
ADVISOR_FLOW_TEMPLATE = """# RAT-CKVN AI Advisor Flow

This document is the business-flow map for RAT-CKVN. It describes the DNSE OpenAPI bot without requiring the full source code.

## Advisor mission
Review trading performance, risk controls, signal behavior, T+2 settlement, config drift, and missed/poor exits. Do not propose direct automatic order placement and do not ask the bot to edit config by itself.

## Package reading order
For manual upload, open the account `advisor` folder and select only the files listed below. Never upload `.env`, trading-token cache, account workspace files, logs, or runtime state.
1. Read advisor_flow.md first.
2. Read user_context.md for the operator goal and focus symbols.
3. Read technical_settings.json for current config/runtime snapshots.
4. Read advisor_export.xlsx for trade evidence.
5. Treat previous_advisor_response.md as prior advice only, not fact.

## Markets
- CKPS: VN30F aliases such as VN30F1M. No T+2 restriction; orders/trades/quotes use the resolved contract code, while OHLC uses the alias.
- CKCS: cash stocks such as FPT, SSI, VCB. Long-only; T+2 settlement applies before selling/closing.

## Core trading surfaces
- Manual: operator-triggered orders from UI/Telegram, using manual magic/comment classification.
- Bot: signal-driven orders from daemon context, checklist/safeguard, lot, SL/TP, TSL, DCA/PCA, REV_C, and Entry/Exit tactics.

## Signal/data flow
1. bot_daemon scans active symbols.
2. data_engine.fetch_data_v4 builds OHLC, indicators, ATR/swing, market-structure context.
3. signal_generator.generate_signal_v4 evaluates G0/G1/G2/G3, market mode, trend, votes, and final signal.
4. signal_listener routes pending signals to trade_manager.
5. trade_manager checks safeguards, T+2/long-only rules, Entry/Exit, lot, SL/TP, and sends DNSE orders.
6. Runtime scans apply TSL, BE, BE_CASH, REV_C, DCA/PCA, and settlement-aware closes.

## T+2 settlement
- CKCS BUY records buy_date and settle_date.
- CKCS SELL/close is allowed only for long volume that has settled.
- Unsettled CKCS close attempts should stay pending and retry after settlement instead of opening shorts.
- CKPS ignores T+2.

## Timeframe groups
- G0: macro/base timeframe.
- G1: trend/context timeframe.
- G2: execution/swing timeframe.
- G3: fast confirmation timeframe.

## Safeguards and gates
Common block reasons include market hours, re-entry locks, daily loss, max open positions, max trades, losing streak, ping/spread, cooldown, Entry/Exit WAIT/BLOCK, missing ATR/swing, SL too tight, strict min lot, NO_SETTLED_LONG, and STOCK_NOT_SETTLED_T2.

## Workbook sheets
Use closed_trades, open_trades, config_snapshots, config_changes, events, summary_daily, summary_symbol, summary_timeframe, summary_signal_group, summary_close_reason, summary_module, and trade_config_map as evidence.

## Advice rules
- Ground claims in internal evidence first.
- Separate internal bot evidence from web/market context.
- Diagnose by symbol, direction, close reason, signal group, market mode, tactic, MAE/MFE, fee, and T+2 state.
- Never recommend bypassing T+2 or opening shorts on CKCS.
"""
ADVISOR_RESPONSE_TEMPLATE = """# RAT-CKVN AI Advisor Response

No API response has been saved yet.

When Send API succeeds, this file will be replaced with the latest LLM response.
"""

ADVISOR_SCOPE_NOTE = """## Phạm vi Advisor và CKCS Research

- Các file trong `advisor/` dùng để đánh giá BOT, setting và lịch sử giao dịch.
- Đọc `user_context.md` để hiểu mục tiêu và `expert_context.md` để đối chiếu nhận định chuyên gia với dữ liệu BOT hiện tại.
- `scan_report.md` trong `ckcs_research/` chỉ là dữ liệu thị trường bổ trợ/nghiên cứu CKCS.
- Không trộn tín hiệu BOT với nhận định chọn CKCS. AI chỉ đề xuất; app không tự chuyển kết quả thành lệnh.
"""


def _read_template_or_default(filename, default_text):
    template_path = paths.advisor_template_path(filename)
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            pass
    return default_text


def _ensure_editable_file(filename, target_path, default_text):
    paths.ensure_advisor_dirs()
    if not os.path.exists(target_path):
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(_read_template_or_default(filename, default_text))
    return target_path


def _sync_versioned_template(filename, target_path, default_text):
    """Tạo file editable đúng một lần; không sinh file version/latest phụ."""
    return _ensure_editable_file(filename, target_path, default_text)


def _migrate_advisor_text(path):
    """Bỏ chỉ dẫn legacy nhưng giữ phần người dùng đã tự chỉnh."""
    if not os.path.isfile(path):
        return False
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        source_original = handle.read()
    original = source_original
    original = (
        original
        .replace("Thứ tự đọc package:", "Thứ tự đọc file:")
        .replace("## Thứ tự đọc package", "## Thứ tự đọc file")
        .replace("## File trong package", "## File Advisor")
        .replace("## Package reading order", "## File reading order")
    )
    lines = original.splitlines()
    output = []
    skip_watchlist = False
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if stripped.startswith("## Watchlist xếp hạng"):
            skip_watchlist = True
            continue
        if skip_watchlist:
            if stripped.startswith("## "):
                skip_watchlist = False
            else:
                continue
        if any(token in lowered for token in (
            "external_package",
            "package_manifest.json",
            "scan_summary.md",
            "scan_report.md",
            ".template_versions.json",
            "*.latest.md",
        )):
            continue
        output.append(line)
    migrated = "\n".join(output).strip()
    if "## Phạm vi Advisor và CKCS Research" not in migrated:
        migrated = f"{migrated}\n\n{ADVISOR_SCOPE_NOTE.strip()}"
    migrated += "\n"
    if migrated == source_original:
        return False
    temp = f"{path}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(migrated)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return True


def cleanup_legacy_advisor_artifacts():
    """Dọn các bản sao/version cũ; không đụng file người dùng đang sử dụng."""
    removed = []
    candidates = [
        os.path.join(paths.advisor_root(), ".template_versions.json"),
        os.path.join(paths.advisor_root(), "advisor_flow.latest.md"),
        os.path.join(paths.advisor_root(), "advisor_prompt.latest.md"),
        os.path.join(paths.advisor_root(), "scan_report.md"),
        os.path.join(paths.advisor_root(), "scan_summary.md"),
        paths.legacy_scan_cache_path(),
        *paths.legacy_scan_session_report_paths(),
    ]
    external = paths.external_package_root()
    if os.path.isdir(external):
        try:
            shutil.rmtree(external)
            removed.append(external)
        except OSError:
            pass
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                os.remove(candidate)
                removed.append(candidate)
            except OSError:
                pass
    _migrate_advisor_text(paths.advisor_prompt_path())
    _migrate_advisor_text(paths.advisor_flow_path())
    response_path = paths.advisor_response_path()
    if os.path.isfile(response_path):
        with open(response_path, "r", encoding="utf-8", errors="replace") as handle:
            response_text = handle.read()
            if (
                response_text.strip() == ADVISOR_RESPONSE_TEMPLATE.strip()
                or "No API response has been saved yet." in response_text
            ):
                try:
                    os.remove(response_path)
                    removed.append(response_path)
                except OSError:
                    pass
    return removed


def ensure_user_context():
    return _ensure_editable_file(
        "user_context.md",
        paths.user_context_path(),
        USER_CONTEXT_TEMPLATE,
    )


def ensure_expert_context():
    return _ensure_editable_file(
        "expert_context.md",
        paths.expert_context_path(),
        EXPERT_CONTEXT_TEMPLATE,
    )


def ensure_advisor_flow():
    path = _sync_versioned_template(
        "advisor_flow.md",
        paths.advisor_flow_path(),
        ADVISOR_FLOW_TEMPLATE,
    )
    _migrate_advisor_text(path)
    return path


def ensure_advisor_api_files():
    from . import api_client

    prompt_path = _sync_versioned_template("advisor_prompt.md", paths.advisor_prompt_path(), api_client.DEFAULT_PROMPT)
    _migrate_advisor_text(prompt_path)
    if not os.path.exists(paths.advisor_api_settings_path()):
        api_client.save_api_settings(api_client.DEFAULT_API_SETTINGS)
    return paths.advisor_api_settings_path()


def write_technical_settings(reason="manual_export"):
    snapshot = config_snapshot.build_technical_settings(reason=reason)
    path = paths.technical_settings_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
    return path, snapshot.get("config_snapshot_id")


def write_external_package():
    """Tương thích tên hàm cũ: trả danh sách file trực tiếp, không tạo bản sao."""
    cleanup_legacy_advisor_artifacts()
    files = []
    for path in (
        paths.advisor_prompt_path(),
        paths.advisor_flow_path(),
        paths.technical_settings_path(),
        paths.export_path(),
        paths.user_context_path(),
        paths.expert_context_path(),
    ):
        if os.path.isfile(path):
            files.append({"name": os.path.basename(path), "bytes": os.path.getsize(path)})
    return {"root": paths.advisor_root(), "manifest": None, "files": files}


def generate_advisor_package(
    export_days=7,
    save_archive=False,
    connector=None,
    state=None,
    market_contexts=None,
    reason="manual_export",
):
    export_days = max(1, int(export_days or 1))
    result = {
        "ok": False,
        "root": paths.advisor_root(),
        "technical_settings": paths.technical_settings_path(),
        "advisor_history": paths.history_path(),
        "advisor_export": paths.export_path(),
        "advisor_flow": paths.advisor_flow_path(),
        "user_context": paths.user_context_path(),
        "expert_context": paths.expert_context_path(),
        "archive": None,
        "warnings": [],
    }
    try:
        paths.ensure_advisor_dirs()
        ensure_user_context()
        ensure_expert_context()
        ensure_advisor_flow()
        ensure_advisor_api_files()
        cleanup_legacy_advisor_artifacts()
        tech_path, snapshot_id = write_technical_settings(reason=reason)
        history.ensure_config_snapshot(reason=reason)
        synced = history.sync_from_master_csv()
        open_count = history.refresh_open_trades(connector=connector, state=state, market_contexts=market_contexts)
        history.rebuild_summaries()
        export_result = history.build_export_workbook(export_days=export_days)
        from .api_client import validate_advisor_package
        package_validation = validate_advisor_package()
        if not export_result.get("ok"):
            result["warnings"].append(export_result.get("error", "advisor export build failed"))
        result.update(
            {
                "ok": True,
                "technical_settings": tech_path,
                "advisor_export": export_result.get("path", paths.export_path()),
                "config_snapshot_id": snapshot_id,
                "synced_closed_trades": synced,
                "export_closed_trades": export_result.get("closed_trades", 0),
                "open_trades": open_count,
                "export_days": export_days,
                "package_validation": package_validation,
                "advisor_files": write_external_package(),
            }
        )
        history.record_event("advisor_package_exported", "Advisor package generated", payload=result)
    except Exception as exc:
        result["error"] = str(exc)
        history.record_event("advisor_package_export_error", str(exc), severity="ERROR", payload=result)
    return result
