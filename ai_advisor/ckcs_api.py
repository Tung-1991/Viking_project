# -*- coding: utf-8 -*-
"""Automatic CKCS RAW reports and optional external LLM calls.

This module never selects symbols in Python and never touches trading logic.
It only packages the selected RAW report, calls the shared Advisor provider,
and stores the provider's text response.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

import config

from . import api_client, paths
from .scan_report import export_ckcs_report


logger = logging.getLogger("RAT_CKVN")
SESSIONS = {"morning", "afternoon"}

CKCS_API_PROMPT = """Bạn là AI phân tích dữ liệu CKCS cho RAT-CKVN.
Đọc shortlist trước, sau đó tra RAW của các mã đó trong scan report.
Nếu shortlist có CKPS/VN30F, phân tích thành mục riêng theo tín hiệu và cấu hình CKPS; không áp bốn tiêu chí cơ bản hoặc bộ lọc thanh khoản CKCS cho CKPS.
Đọc private context để áp dụng bốn tiêu chí và trả lời bằng tiếng Việt, rõ ràng, dựa trên dữ liệu.
Python chỉ thu thập và gửi dữ liệu; không được tuyên bố app đã tự chọn, tự chấm điểm hay tự đặt lệnh.
Chỉ phân tích module CHECK thực sự xuất hiện. Phân biệt dữ liệu RAT-CKVN với thông tin web mới.
Xếp hạng các mã đủ dữ liệu và gán đúng một trạng thái: WATCH, CHỜ MUA, MUA, HOLD, GIẢM, EXIT hoặc LOẠI.
Với mỗi mã đáng chú ý, nêu: vùng mua; điều kiện kích hoạt; mức không mua đuổi; mốc nhận định sai hoặc SL; TP hoặc cách trailing; thời gian giữ; tỷ trọng đề xuất; ngày hết hiệu lực.
Nếu có nhận định CKCS trước đó, nêu rõ lý do thay đổi. Được phép kết luận không có mã phù hợp.
Không thay đổi setting, không phát lệnh và không coi output là lệnh giao dịch. AI chỉ đề xuất tỷ trọng; app không tự chuyển kết quả thành lệnh CKCS."""


def normalize_session(session):
    value = str(session or "").strip().lower()
    if value not in SESSIONS:
        raise ValueError("CKCS session phải là morning hoặc afternoon")
    return value


def session_label(session):
    return "PHIÊN SÁNG" if normalize_session(session) == "morning" else "CUỐI NGÀY"


def generate_session_report(session, report_days=15, require_current_session=False):
    session = normalize_session(session)
    return export_ckcs_report(
        report_days=report_days,
        output_path=paths.scan_report_path(),
        report_label=session_label(session),
        report_session=session,
        require_current_session=bool(require_current_session),
    )


def _read(path, limit):
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read(max(1, int(limit)))


def build_input(session):
    session = normalize_session(session)
    settings = api_client.load_api_settings()
    report = _read(
        paths.scan_report_path(),
        settings.get("technical_settings_limit", 1_000_000),
    )
    if not report.strip():
        raise FileNotFoundError(
            f"Chưa có {os.path.basename(paths.scan_report_path())}"
        )
    private_context = _read(
        paths.research_private_context_path(),
        settings.get("user_context_limit", 100_000),
    )
    shortlist = _read(
        paths.ckcs_shortlist_path(),
        settings.get("user_context_limit", 100_000),
    )
    parts = [
        f"# CKCS SESSION\n{session_label(session)}",
    ]
    if shortlist.strip():
        parts.append(f"# ckcs_shortlist.md\n{shortlist}")
    parts.append(f"# {os.path.basename(paths.scan_report_path())}\n{report}")
    if private_context.strip():
        parts.append(f"# private_context.md\n{private_context}")
    previous_session = "afternoon" if session == "morning" else "morning"
    previous_response = _read(
        paths.ckcs_response_path(previous_session),
        settings.get("previous_response_limit", 60_000),
    )
    if previous_response.strip():
        parts.append(
            f"# NHẬN ĐỊNH CKCS TRƯỚC ĐÓ ({previous_session})\n"
            f"{previous_response}"
        )
    return api_client._sanitize_external_text("\n\n".join(parts))


def estimate_payload(session):
    body = build_input(session)
    settings = api_client.load_api_settings()
    provider = settings.get("provider", api_client.DEFAULT_PROVIDER)
    model = settings.get("model", api_client.DEFAULT_MODEL)
    pcfg = api_client.provider_config(provider)
    tokens = max(1, int((len(body) + len(CKCS_API_PROMPT)) / 4))
    context_map = pcfg.get("context_tokens", {})
    context_tokens = context_map.get(model) or next(iter(context_map.values()), 200_000)
    max_output_tokens = int(settings.get("max_output_tokens", api_client.DEFAULT_MAX_OUTPUT_TOKENS))
    return {
        "provider": provider,
        "model": model,
        "tokens": tokens,
        "chars": len(body) + len(CKCS_API_PROMPT),
        "context_tokens": context_tokens,
        "max_output_tokens": max_output_tokens,
        "fits_context": tokens + max_output_tokens <= context_tokens,
        "web_search_enabled": bool(settings.get("web_search_enabled")),
        "settings": settings,
    }


def _write_response(session, text):
    target = paths.ckcs_response_path(session)
    paths.ensure_ckcs_research_dir()
    temp = f"{target}.{os.getpid()}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(str(text or "").strip() + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return target


def send_session_to_api(session):
    session = normalize_session(session)
    try:
        body = build_input(session)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    estimate = estimate_payload(session)
    settings = estimate["settings"]
    provider = estimate["provider"]
    model = estimate["model"]
    pcfg = api_client.provider_config(provider)
    env_name = pcfg.get("env_key", "OPENAI_API_KEY")
    key = api_client._get_env_value(env_name)
    if not key:
        return {"ok": False, "error": f"{env_name} is not configured; API mode skipped."}
    if model not in api_client.models_for(provider):
        return {"ok": False, "error": f"Model {model} không hợp lệ cho provider {provider}."}
    if not estimate["fits_context"]:
        return {
            "ok": False,
            "error": (
                f"CKCS payload quá lớn cho {model}: ~{estimate['tokens']} input + "
                f"{estimate['max_output_tokens']} output > {estimate['context_tokens']} context."
            ),
            "estimate": estimate,
        }
    requested_tokens = api_client._requested_tokens_for_guard(estimate)
    guard = api_client._check_local_tpm_guard(model, requested_tokens)
    if not guard.get("ok"):
        return {
            "ok": False,
            "error": f"CKCS API local TPM guard: chờ {guard.get('wait_seconds')} giây.",
            "wait_seconds": guard.get("wait_seconds"),
        }

    endpoint, headers, payload = api_client._build_request(
        provider,
        model,
        CKCS_API_PROMPT,
        body,
        estimate["max_output_tokens"],
        settings.get("web_search_enabled"),
        key,
        settings.get("reasoning_effort", "medium"),
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    api_client._record_api_usage(model, requested_tokens)
    max_retries = max(0, int(getattr(config, "ADVISOR_API_RETRIES", 2) or 0))
    attempt = 0
    while True:
        try:
            with api_client._urlopen(request) as response:
                data = json.loads(response.read().decode("utf-8"))
            text = api_client._parse_response(provider, data)
            if not text:
                text = json.dumps(data, ensure_ascii=False, indent=2)
            citations = api_client._extract_citations(data) if provider == "openai" else []
            text = api_client._append_citations(text, citations)
            response_path = _write_response(session, text)
            logger.info(
                "[CKCS API] %s response saved: %s",
                session,
                response_path,
            )
            return {
                "ok": True,
                "response": response_path,
                "model": str(data.get("model") or model) if isinstance(data, dict) else model,
                "usage": data.get("usage", {}) if isinstance(data, dict) else {},
                "citations": citations,
                "estimate": estimate,
            }
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc.reason)
            retryable = exc.code == 429 or 500 <= int(exc.code) < 600
            if retryable and attempt < max_retries:
                attempt += 1
                time.sleep(api_client._retry_after_seconds(getattr(exc, "headers", {}), attempt))
                continue
            return {"ok": False, "error": api_client._safe_error_text(f"HTTP {exc.code}: {detail}", key)}
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < max_retries:
                attempt += 1
                time.sleep(api_client._retry_after_seconds({}, attempt))
                continue
            return {"ok": False, "error": api_client._safe_error_text(exc, key)}
        except Exception as exc:
            return {"ok": False, "error": api_client._safe_error_text(exc, key)}
