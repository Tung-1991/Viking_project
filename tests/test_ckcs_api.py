# -*- coding: utf-8 -*-
import json

from ai_advisor import api_client, ckcs_api, paths


def _patch_account(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "account_dir", lambda: str(tmp_path))
    paths.ensure_ckcs_research_dir()


def test_ckcs_input_uses_canonical_report_and_private_context(monkeypatch, tmp_path):
    _patch_account(monkeypatch, tmp_path)
    paths.ensure_ckcs_research_dir()
    with open(paths.scan_report_path(), "w", encoding="utf-8") as handle:
        handle.write("RAW CURRENT")
    with open(paths.research_private_context_path(), "w", encoding="utf-8") as handle:
        handle.write("PRIVATE VIEW")
    with open(paths.ckcs_shortlist_path(), "w", encoding="utf-8") as handle:
        handle.write("SHORTLIST CURRENT")
    monkeypatch.setattr(
        api_client,
        "load_api_settings",
        lambda: {"technical_settings_limit": 100000, "user_context_limit": 100000},
    )

    text = ckcs_api.build_input("morning")

    assert "RAW CURRENT" in text
    assert "SHORTLIST CURRENT" in text
    assert "PRIVATE VIEW" in text
    assert text.index("SHORTLIST CURRENT") < text.index("RAW CURRENT")


def test_ckcs_input_includes_previous_analysis_for_change_reason(monkeypatch, tmp_path):
    _patch_account(monkeypatch, tmp_path)
    with open(paths.scan_report_path(), "w", encoding="utf-8") as handle:
        handle.write("RAW AFTERNOON")
    with open(paths.ckcs_response_path("morning"), "w", encoding="utf-8") as handle:
        handle.write("MORNING VIEW")
    monkeypatch.setattr(
        api_client,
        "load_api_settings",
        lambda: {
            "technical_settings_limit": 100000,
            "user_context_limit": 100000,
            "previous_response_limit": 100000,
        },
    )

    text = ckcs_api.build_input("afternoon")

    assert "MORNING VIEW" in text
    assert "NHẬN ĐỊNH CKCS TRƯỚC ĐÓ" in text
    assert "WATCH, CHỜ MUA, MUA, HOLD, GIẢM, EXIT hoặc LOẠI" in ckcs_api.CKCS_API_PROMPT


def test_ckcs_api_reuses_advisor_model_and_saves_response(monkeypatch, tmp_path):
    _patch_account(monkeypatch, tmp_path)
    with open(paths.scan_report_path(), "w", encoding="utf-8") as handle:
        handle.write("RAW AFTERNOON")
    settings = {
        "provider": "openai",
        "model": "gpt-5.6",
        "reasoning_effort": "medium",
        "web_search_enabled": True,
        "max_output_tokens": 4096,
        "technical_settings_limit": 100000,
        "user_context_limit": 100000,
    }
    monkeypatch.setattr(api_client, "load_api_settings", lambda: dict(settings))
    monkeypatch.setattr(
        api_client,
        "provider_config",
        lambda _provider: {
            "env_key": "OPENAI_API_KEY",
            "context_tokens": {"gpt-5.6": 1000000},
        },
    )
    monkeypatch.setattr(api_client, "models_for", lambda _provider: ["gpt-5.6"])
    monkeypatch.setattr(api_client, "_get_env_value", lambda _name: "secret")
    monkeypatch.setattr(api_client, "_check_local_tpm_guard", lambda *_args: {"ok": True})
    monkeypatch.setattr(api_client, "_record_api_usage", lambda *_args: None)
    captured = {}

    def build_request(provider, model, prompt, body, max_tokens, web_search, key, reasoning):
        captured.update(
            provider=provider,
            model=model,
            body=body,
            web_search=web_search,
            reasoning=reasoning,
        )
        return "https://example.invalid", {}, {"model": model}

    monkeypatch.setattr(api_client, "_build_request", build_request)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"model": "gpt-5.6", "output_text": "LLM RESULT"}).encode()

    monkeypatch.setattr(api_client, "_urlopen", lambda _request: _Response())

    result = ckcs_api.send_session_to_api("afternoon")

    assert result["ok"] is True
    assert captured["model"] == "gpt-5.6"
    assert captured["reasoning"] == "medium"
    assert captured["web_search"] is True
    assert "RAW AFTERNOON" in captured["body"]
    with open(paths.ckcs_response_path("afternoon"), encoding="utf-8") as handle:
        assert handle.read().strip() == "LLM RESULT"
