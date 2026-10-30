"""Native Ollama checks use synthetic metadata and responses, never local models."""

import json

import httpx
from pydantic import BaseModel, ConfigDict, Field
import pytest

from job_radar import providers


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=3)


def settings(**changes):
    return {**providers.default_settings("ollama"), "model": "synthetic-model:latest", **changes}


def completed(**changes):
    return {"model": "synthetic-model:latest", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": '{"answer":"Grounded"}',
                        "thinking": "private reasoning trace"},
            "prompt_eval_count": 30, "eval_count": 10, **changes}


def invoke(value, handler):
    respond = providers.resolve_response_fn(value, transport=httpx.MockTransport(handler))
    return respond(schema_type=Document, schema_name="synthetic", system="Use verified facts.",
                   user="Synthetic private document.", use_web_search=False)


@pytest.mark.parametrize("thinking,metadata", [
    (None, None), (False, {"values": [False, True], "default": True}),
    (True, {"values": [False, True], "default": False}),
    ("low", {"values": ["low", "medium", "high"], "default": "medium"}),
    ("high", {"values": ["low", "medium", "high"], "default": "medium"}),
])
def test_native_payload_and_checked_selection(thinking, metadata):
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert "authorization" not in request.headers
        if request.url.path == "/api/show":
            assert body == {"model": "synthetic-model:latest"}
            assert request.extensions["timeout"]["read"] == 10
            assert "Synthetic private document" not in request.content.decode()
            return httpx.Response(200, json={"thinking": metadata})
        assert request.url.path == "/api/chat"
        assert request.extensions["timeout"]["read"] == providers.OLLAMA_REQUEST_TIMEOUT
        assert body["model"] == "synthetic-model:latest"
        assert body["stream"] is False
        assert body["options"] == {"num_predict": providers.MAX_OUTPUT_TOKENS, "num_ctx": providers.OLLAMA_CONTEXT_DEFAULT}
        assert body["format"]["additionalProperties"] is False
        assert body["format"]["properties"]["answer"]["minLength"] == 3
        assert body["messages"][0]["role"] == "system"
        assert "Use verified facts." in body["messages"][0]["content"]
        assert body["messages"][1] == {"role": "user", "content": "Synthetic private document."}
        assert "tools" not in body and "reasoning_effort" not in body
        if thinking is None:
            assert "think" not in body
        else:
            assert type(body["think"]) is type(thinking) and body["think"] == thinking
        return httpx.Response(200, json=completed(unexpected_echo="sensitive response echo"))

    parsed, raw = invoke(settings(ollama_thinking=thinking), handler)
    assert parsed.answer == "Grounded"
    assert [request.url.path for request in requests] == (["/api/show"] if thinking is not None else []) + ["/api/chat"]
    assert raw["backend"] == "ollama"
    assert raw["provider_settings"]["ollama_thinking"] == thinking
    assert raw["usage"] == {"prompt_eval_count": 30, "eval_count": 10}
    assert "private reasoning trace" not in json.dumps(raw)
    assert "sensitive response echo" not in json.dumps(raw)


def test_settings_save_load_and_local_check_do_not_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(providers.httpx, "Client", lambda *args, **kwargs: pytest.fail("No network allowed"))
    value = settings(ollama_thinking="model-specific")
    saved = providers.save_settings(tmp_path, value)
    assert providers.load_settings(tmp_path) == saved
    assert saved["ollama_thinking"] == "model-specific"
    assert providers.settings_check(saved) == {
        "ready": True, "detail": "Settings are complete. Connection, model support and account access have not been tested. No request was sent.",
        "network_checked": False,
    }
    old = {key: value for key, value in settings().items() if key != "ollama_thinking"}
    assert providers.normalize_settings(old)["ollama_thinking"] is None
    assert providers.default_settings("ollama")["base_url"] == "http://localhost:11434/api"
    assert providers.PROVIDER_LABELS["ollama"] == "Ollama (local)"


@pytest.mark.parametrize("provider", ["codex", "openai", "openai_compatible", "anthropic", "gemini"])
def test_ollama_selection_is_cleared_for_other_providers(provider):
    value = {**providers.default_settings(provider), "ollama_thinking": "high"}
    assert providers.normalize_settings(value)["ollama_thinking"] is None
    assert "ollama_thinking" not in providers.provider_identity(value)


@pytest.mark.parametrize("thinking", [0, 1, 1.5, [], {}, "", " high", "high ", "name with spaces", "a" * 33,
                                      "unsafe\nheader", "<script>", "../../path"])
def test_unsafe_or_ambiguous_saved_choices_are_rejected(thinking):
    with pytest.raises(ValueError, match="Ollama thinking"):
        providers.normalize_settings(settings(ollama_thinking=thinking))


@pytest.mark.parametrize("url", ["http://localhost:11434/api/", "http://127.0.0.1:11434/api",
                                 "http://[::1]:11434/api", "https://localhost:12000/api"])
def test_native_endpoint_allows_local_hosts_and_custom_ports(url):
    assert providers.normalize_settings(settings(base_url=url))["base_url"] == url.rstrip("/")


@pytest.mark.parametrize("url", ["http://remote.example/api", "https://remote.example/api",
                                 "https://localhost.remote.example/api", "http://0.0.0.0:11434/api",
                                 "http://192.168.1.2:11434/api", "http://[::]:11434/api",
                                 "http://user:pass@localhost:11434/api", "http://localhost:11434/api?key=secret",
                                 "http://localhost:11434/api#fragment"])
def test_native_metadata_endpoint_cannot_target_remote_or_unsafe_urls(url):
    transport = httpx.MockTransport(lambda request: pytest.fail("No network expected"))
    with pytest.raises(ValueError):
        providers.ollama_thinking_options(settings(base_url=url), transport=transport)


@pytest.mark.parametrize("path", ["/v1", "", "/api/show", "/api/../v1", "/API", "/%61pi"])
def test_native_endpoint_explains_api_path(path):
    with pytest.raises(ValueError, match="/api.*For /v1"):
        providers.normalize_settings(settings(base_url="http://localhost:11434" + path))


@pytest.mark.parametrize("metadata,expected", [
    ({"values": [False, True], "default": True}, {"values": [False, True], "default": True}),
    ({"values": ["low", "medium", "high"], "default": "medium"}, {"values": ["low", "medium", "high"], "default": "medium"}),
    ({"values": [False]}, {"values": [False], "default": None}),
    ({"values": [False, "custom.level_1"]}, {"values": [False, "custom.level_1"], "default": None}),
])
def test_choices_come_only_from_model_metadata(metadata, expected):
    result = providers.ollama_thinking_options(settings(), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"thinking": metadata})))
    assert result["values"] == expected["values"]
    assert result["default"] == expected["default"]
    assert isinstance(result["detail"], str) and result["detail"]
    if metadata["values"] == [False]:
        assert "no thinking support" in result["detail"]


@pytest.mark.parametrize("name", ["synthetic-model", "qwen3", "gpt-oss", "deepseek-r1"])
def test_absent_metadata_never_guesses_from_model_name(name):
    result = providers.ollama_thinking_options(settings(model=name), transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"capabilities": ["thinking"], "model_info": {"thinking": True}})))
    assert result["values"] == [] and result["default"] is None
    assert "does not report" in result["detail"]


@pytest.mark.parametrize("metadata", [None, [], True, "sensitive metadata echo", {}, {"values": []},
    {"values": "high"}, {"values": [0, 1]}, {"values": [True, False, False]},
    {"values": ["same", "same"]}, {"values": ["a" * 33]}, {"values": ["secret\ncontent"]},
    {"values": [["nested"]]}, {"values": [{}]}, {"values": [f"level-{i}" for i in range(17)]},
    {"values": ["high"], "default": "low"}, {"values": [False], "default": 0},
    {"values": [False], "default": {"secret": "echo"}},
])
def test_malformed_metadata_is_rejected_without_echo(metadata):
    with pytest.raises(providers.ProviderError, match="invalid thinking metadata") as error:
        providers.ollama_thinking_options(settings(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"thinking": metadata})))
    assert "sensitive metadata echo" not in str(error.value)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("choice,metadata", [
    ("high", {}), (True, {}), (False, {}),
    ("HIGH", {"thinking": {"values": ["low", "high"]}}),
    (True, {"thinking": {"values": [False]}}),
    ("true", {"thinking": {"values": [True, False]}}),
    (False, {"thinking": {"values": ["off", "high"]}}),
])
def test_unsupported_selection_stops_before_sending_document(choice, metadata):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/show"
        assert json.loads(request.content) == {"model": "synthetic-model:latest"}
        return httpx.Response(200, json=metadata)
    with pytest.raises(providers.ProviderError, match="current metadata"):
        invoke(settings(ollama_thinking=choice), handler)
    assert len(requests) == 1


def test_generation_rechecks_current_metadata_instead_of_trusting_saved_selection():
    metadata = {"thinking": {"values": ["low", "high"]}}
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/show"
        return httpx.Response(200, json=metadata)
    value = settings(ollama_thinking="high")
    assert "high" in providers.ollama_thinking_options(value, transport=httpx.MockTransport(handler))["values"]
    metadata["thinking"]["values"] = ["low"]
    with pytest.raises(providers.ProviderError, match="current metadata"):
        invoke(value, handler)
    assert len(requests) == 2


@pytest.mark.parametrize("status", [301, 400, 401, 404, 429, 500])
def test_metadata_http_errors_are_safe_and_never_send_document(status):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/show"
        return httpx.Response(status, json={"error": "sensitive server echo"},
                              headers={"Location": "https://remote.example/api"})
    with pytest.raises(providers.ProviderError) as error:
        invoke(settings(ollama_thinking=True), handler)
    assert len(requests) == 1
    assert "sensitive server echo" not in str(error.value)


def test_metadata_timeout_and_size_errors_are_safe(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("sensitive timeout echo", request=request)
    with pytest.raises(providers.ProviderError, match="timed out") as error:
        providers.ollama_thinking_options(settings(), transport=httpx.MockTransport(timeout))
    assert "sensitive timeout echo" not in str(error.value)
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 128)
    with pytest.raises(providers.ProviderError, match="size limit"):
        providers.ollama_thinking_options(settings(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 129)))


def test_optional_local_authentication_stays_in_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("SYNTHETIC_OLLAMA_KEY", "fake-local-key")
    value = settings(api_key_env="SYNTHETIC_OLLAMA_KEY", ollama_thinking=False)
    saved = providers.save_settings(tmp_path, value)
    def handler(request):
        assert request.headers["authorization"] == "Bearer fake-local-key"
        assert "fake-local-key" not in request.content.decode()
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"thinking": {"values": [False]}})
        return httpx.Response(200, json=completed(model="fake-local-key"))
    _, raw = invoke(saved, handler)
    assert raw["returned_model"] is None
    assert "fake-local-key" not in json.dumps(raw)
    assert "fake-local-key" not in (tmp_path / "data" / "ai_settings.json").read_text()
    monkeypatch.delenv("SYNTHETIC_OLLAMA_KEY")
    with pytest.raises(providers.ProviderError, match="SYNTHETIC_OLLAMA_KEY"):
        providers.ollama_thinking_options(value, transport=httpx.MockTransport(lambda request: pytest.fail("No request expected")))


@pytest.mark.parametrize("changes", [
    {"done": False}, {"done": 1}, {"done_reason": "length"}, {"done_reason": None},
    {"error": "sensitive error echo"}, {"message": {"thinking": "private reasoning trace"}},
    {"message": {"content": '{"answer":"valid"}', "tool_calls": [{}]}},
])
def test_incomplete_native_responses_are_rejected(changes):
    with pytest.raises(providers.ProviderError, match="complete structured document") as error:
        invoke(settings(), lambda request: httpx.Response(200, json=completed(**changes)))
    assert "sensitive error echo" not in str(error.value)
    assert "private reasoning trace" not in str(error.value)


@pytest.mark.parametrize("content", ['{"answer":"x"}', '{"answer":"valid","thinking":"trace"}', 'not json'])
def test_native_output_keeps_strict_local_schema_validation(content):
    with pytest.raises(providers.ProviderError, match="document schema"):
        invoke(settings(), lambda request: httpx.Response(200, json=completed(message={"content": content})))


def test_native_usage_does_not_save_untrusted_values():
    _, raw = invoke(settings(), lambda request: httpx.Response(200, json=completed(
        prompt_eval_count="secret", eval_count=True, usage={"input_tokens": 42, "trace": "secret"})))
    assert raw["usage"] == {}
    assert "secret" not in json.dumps(raw)


@pytest.mark.parametrize("choice,label", [(None, "Model default"), (True, "On"), (False, "Off"), ("high", "high")])
def test_identity_description_and_preview_include_exact_choice(choice, label):
    value = settings(ollama_thinking=choice)
    assert providers.provider_identity(value)["ollama_thinking"] == choice
    assert f"thinking: {label}" in providers.describe(value)
    preview = json.loads(providers.request_preview(value, "system", "user", Document))
    assert preview["ollama_thinking"] == choice and preview["provider"] == "ollama"

