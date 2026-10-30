"""Ollama context budgets use fictional inputs and mocked HTTP responses only."""
import json

import httpx
import pytest

from job_radar import providers
from test_ollama_provider import Document, completed, invoke, settings
from test_providers import Result, response


def test_ollama_context_budget_legacy_settings_gain_explicit_default_without_thinking_change(tmp_path):
    legacy = settings(ollama_thinking=None)
    legacy.pop("ollama_context_tokens", None)
    folder = tmp_path / "data"
    folder.mkdir()
    path = folder / "ai_settings.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    original = path.read_bytes()
    loaded = providers.load_settings(tmp_path)
    assert loaded["ollama_context_tokens"] == 32_768
    assert loaded["ollama_thinking"] is None
    assert loaded["model"] == legacy["model"]
    assert path.read_bytes() == original, "Loading preferences does not rewrite the user's settings file"


@pytest.mark.parametrize("context", [4096, 8192, 16_384, 32_768, 65_536, 131_072, 262_144])
def test_ollama_context_budget_explicit_choices_round_trip_without_model_changes(tmp_path, context):
    value = settings(ollama_context_tokens=context, ollama_thinking="high")
    saved = providers.save_settings(tmp_path, value)
    assert providers.load_settings(tmp_path) == saved
    assert saved["ollama_context_tokens"] == context
    assert saved["ollama_thinking"] == "high"
    assert saved["model"] == value["model"]


@pytest.mark.parametrize("invalid", [True, False, None, "32768", 32_768.0, 0, -1, 2048, 20_000, 524_288, [], {}])
def test_ollama_context_budget_invalid_sizes_fail_before_network(invalid):
    with pytest.raises(ValueError, match="context window"):
        providers.resolve_response_fn(settings(ollama_context_tokens=invalid),
                                      transport=httpx.MockTransport(lambda _: pytest.fail("Must not contact server")))


def test_ollama_context_budget_preview_and_description_show_requested_window():
    value = settings(ollama_context_tokens=65_536)
    identity = providers.provider_identity(value)
    preview = json.loads(providers.request_preview(value, "System", "Synthetic input", Document))
    assert identity["ollama_context_tokens"] == preview["ollama_context_tokens"] == 65_536
    assert preview["ollama_thinking"] is None
    assert "65,536 tokens" in providers.describe(value)
    assert "thinking: Model default" in providers.describe(value)


@pytest.mark.parametrize("context", [8192, 32_768, 65_536])
@pytest.mark.parametrize("thinking", [None, False, True])
def test_ollama_context_budget_native_payload_and_deadlines(context, thinking):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path == "/api/show":
            assert body == {"model": "synthetic-model:latest"}
            assert request.extensions["timeout"]["read"] == 10
            return httpx.Response(200, json={"thinking": {"values": [False, True], "default": True}})
        assert request.url.path == "/api/chat"
        assert body["options"] == {"num_predict": 16_384, "num_ctx": context}
        assert request.extensions["timeout"]["read"] == 600
        assert request.extensions["timeout"]["connect"] == 10
        assert body["model"] == "synthetic-model:latest"
        assert body["stream"] is False
        if thinking is None:
            assert "think" not in body
        else:
            assert body["think"] is thinking
        return httpx.Response(200, json=completed())
    parsed, raw = invoke(settings(ollama_context_tokens=context, ollama_thinking=thinking), handler)
    assert parsed.answer == "Grounded"
    assert calls == (["/api/show"] if thinking is not None else []) + ["/api/chat"]
    assert raw["provider_settings"]["ollama_context_tokens"] == context
    assert raw["provider_settings"]["ollama_thinking"] is thinking


@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "anthropic", "gemini"])
def test_ollama_context_budget_does_not_change_other_provider_payloads_or_deadlines(provider, monkeypatch):
    value = {**providers.default_settings(provider), "model": "test-model", "ollama_context_tokens": 65_536}
    if value["api_key_env"]:
        monkeypatch.setenv(value["api_key_env"], "synthetic-test-key")
    seen = []
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert "num_ctx" not in body
        assert "options" not in body
        assert "think" not in body
        assert request.extensions["timeout"]["read"] == 180
        return httpx.Response(200, json=response(provider))
    fn = providers.resolve_response_fn(value, transport=httpx.MockTransport(handler))
    parsed, raw = fn(schema_type=Result, schema_name="test", system="Synthetic facts only",
                     user="Synthetic document", use_web_search=False)
    assert parsed.answer == "Grounded" and len(seen) == 1
    assert "ollama_context_tokens" not in raw["provider_settings"]
    assert "ollama_thinking" not in raw["provider_settings"]
