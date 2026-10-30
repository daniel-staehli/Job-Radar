"""No live models, personal records or credentials are used by these checks."""

import json
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field
import pytest
import yaml

from job_radar import codex_client, providers
from job_radar.config import ROOT
from job_radar.cv import generate_cvs
from job_radar.db import JobDatabase
from job_radar.revisions import revise_application
from test_cv import job
from test_revisions import application, profile, source as revision_source


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    examples: list[str] = Field(min_length=2, max_length=3)


def settings(provider="openai_compatible", **changes):
    return {**providers.default_settings(provider), "model": "test-model", **changes}


def response(provider, content=None, **changes):
    content = content or '{"answer":"Grounded","examples":["one","two"]}'
    values = {
        "openai": {"status": "completed", "model": "returned-model", "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]},
        "openai_compatible": {"model": "returned-model", "choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        "anthropic": {"model": "returned-model", "stop_reason": "end_turn", "content": [{"type": "text", "text": content}]},
        "gemini": {"modelVersion": "returned-model", "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": content}]}}]},
    }
    return {**values[provider], **changes}


def invoke(value=None, *, handler=None, content=None, **request_changes):
    value = value or settings()
    provider = value["provider"]
    fn = providers.resolve_response_fn(value, transport=httpx.MockTransport(
        handler or (lambda request: httpx.Response(200, json=response(provider, content)))
    ))
    return fn(**{"schema_type": Result, "schema_name": "test", "system": "Verified facts only.",
                 "user": "Synthetic record.", "use_web_search": False, **request_changes})


def test_settings_store_only_public_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret-never-save")
    value = settings(base_url="https://models.example/v1/", api_key_env="TEST_API_KEY")
    saved = providers.save_settings(tmp_path, value)
    assert saved["base_url"] == "https://models.example/v1"
    assert providers.load_settings(tmp_path) == saved
    text = (tmp_path / "data" / "ai_settings.json").read_text()
    assert "secret-never-save" not in text
    assert "TEST_API_KEY" in text
    with pytest.raises(ValueError, match="Never enter an API key"):
        providers.save_settings(tmp_path, {**value, "api_key": "secret-never-save"})
    assert providers.load_settings(tmp_path) == saved
    assert providers.load_settings(tmp_path / "empty") == providers.default_settings()


@pytest.mark.parametrize("url", [
    "http://remote.example/v1", "https://user:password@example.org/v1", "https://example.org/v1?key=secret",
    "https://example.org/v1#secret", "file:///path", "https://example.org/\nheader", "https://example.org\\@evil.example",
    "https://example.org:bad/v1", "https://example.org:0/v1", "http://localhost.evil.example/v1",
])
def test_unsafe_endpoints_rejected(url):
    with pytest.raises(ValueError):
        providers.normalize_settings(settings(base_url=url))


@pytest.mark.parametrize("url", ["http://localhost:11434/v1", "http://127.0.0.1:11434/v1", "http://[::1]:11434/v1"])
def test_local_endpoint_may_omit_key(url):
    assert providers.normalize_settings(settings(base_url=url))["api_key_env"] == ""


def test_remote_endpoint_requires_key_environment_name():
    with pytest.raises(ValueError, match="environment variable"):
        providers.normalize_settings(settings(base_url="https://remote.example/v1"))
    with pytest.raises(ValueError, match="official API endpoint"):
        providers.normalize_settings(settings("openai", base_url="https://other.example/v1"))
    with pytest.raises(ValueError, match="not the key"):
        providers.normalize_settings(settings(api_key_env="sk-do-not-save"))


def test_check_settings_never_contacts_network_or_runs_model(monkeypatch, synthetic_codex_metadata):
    monkeypatch.setattr(providers.httpx, "Client", lambda *a, **k: pytest.fail("No network allowed"))
    monkeypatch.setattr(codex_client, "codex_executable", lambda: "codex.exe")
    monkeypatch.setattr(codex_client.subprocess, "run", lambda *a, **k: pytest.fail("No process expected"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert providers.settings_check(providers.default_settings())["ready"]
    assert not providers.settings_check(settings("openai"))["ready"]
    assert "OPENAI_API_KEY" in providers.settings_check(settings("openai"))["detail"]
    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret")
    check = providers.settings_check(settings("openai"))
    assert check["ready"] and check["network_checked"] is False
    assert "fake-secret" not in str(check)
    assert not providers.settings_check(settings(model=""))["ready"]


@pytest.mark.parametrize("provider,path,header", [
    ("openai", "/v1/responses", "authorization"),
    ("openai_compatible", "/v1/chat/completions", None),
    ("anthropic", "/v1/messages", "x-api-key"),
    ("gemini", "/v1beta/models/test-model:generateContent", "x-goog-api-key"),
])
def test_provider_request_shapes_and_validated_output(provider, path, header, monkeypatch):
    value = settings(provider)
    if value["api_key_env"]:
        monkeypatch.setenv(value["api_key_env"], "fake-secret")
    captured = []
    def handler(request):
        captured.append(request)
        assert request.url.path == path
        body = json.loads(request.content)
        assert "tools" not in body and "web_search" not in str(body)
        assert "Verified facts only." in request.content.decode()
        assert "Synthetic record." in request.content.decode()
        if header:
            assert "fake-secret" in request.headers[header]
        else:
            assert "authorization" not in request.headers
        if provider == "openai":
            assert body["store"] is False
            schema = body["text"]["format"]["schema"]
        elif provider == "openai_compatible":
            schema = body["response_format"]["json_schema"]["schema"]
        elif provider == "anthropic":
            schema = body["output_config"]["format"]["schema"]
            assert "minItems" not in schema["properties"]["examples"]
            assert request.headers["anthropic-version"] == "2023-06-01"
        else:
            schema = body["generationConfig"]["responseFormat"]["text"]["schema"]
        assert schema["additionalProperties"] is False
        return httpx.Response(200, json=response(provider, unexpected_echo="fake-secret", usage={"input_tokens": 10, "private": "fake-secret"}))
    parsed, raw = invoke(value, handler=handler)
    assert len(captured) == 1
    assert parsed.answer == "Grounded"
    assert raw["provider_settings"]["provider"] == provider
    assert raw["provider_settings"]["model"] == "test-model"
    assert raw["returned_model"] == "returned-model"
    assert raw["usage"] == {"input_tokens": 10}
    assert "fake-secret" not in json.dumps(raw)


def test_no_fallback_or_network_for_missing_model_and_key(monkeypatch):
    no_request = lambda request: pytest.fail("Must fail before network")
    with pytest.raises(ValueError, match="model name"):
        invoke(settings(model=""), handler=no_request)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(providers.ProviderError, match="OPENAI_API_KEY"):
        invoke(settings("openai"), handler=no_request)
    with pytest.raises(ValueError, match="web search"):
        invoke(handler=no_request, use_web_search=True)


@pytest.mark.parametrize("provider", ["openai", "openai_compatible", "anthropic", "gemini"])
def test_local_schema_validation_remains_strict(provider, monkeypatch):
    value = settings(provider)
    if value["api_key_env"]:
        monkeypatch.setenv(value["api_key_env"], "fake-secret")
    with pytest.raises(providers.ProviderError, match="document schema"):
        invoke(value, content='{"answer":"Invalid","examples":["only one"]}')


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 404, 422, 429, 500])
def test_errors_are_sanitized_and_never_retried_or_redirected(status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"message": "sensitive-server-echo"}, headers={"Location": "https://leak.example"})
    with pytest.raises(providers.ProviderError) as error:
        invoke(handler=handler)
    assert len(requests) == 1
    assert "sensitive-server-echo" not in str(error.value)
    assert f"HTTP {status}" in str(error.value)


def test_network_errors_hide_request_and_secret():
    def handler(request):
        raise httpx.ConnectError("secret-and-prompt", request=request)
    with pytest.raises(providers.ProviderError) as error:
        invoke(handler=handler)
    assert "secret-and-prompt" not in str(error.value)


def test_oversized_response_and_request_are_bounded(monkeypatch):
    monkeypatch.setattr(providers, "MAX_RESPONSE_BYTES", 128)
    with pytest.raises(providers.ProviderError, match="size limit"):
        invoke(handler=lambda request: httpx.Response(200, content=b"x" * 129))
    monkeypatch.setattr(providers, "MAX_REQUEST_BYTES", 1)
    with pytest.raises(providers.ProviderError, match="request is too large"):
        invoke(handler=lambda request: pytest.fail("Oversized request not sent"))


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
                                      {"choices": [{"finish_reason": "stop", "message": {"refusal": "No", "content": "{}"}}]}])
def test_incomplete_refused_or_unexpected_responses_do_not_generate(payload):
    with pytest.raises(providers.ProviderError, match="complete structured document"):
        invoke(handler=lambda request: httpx.Response(200, json=payload))


def test_codex_model_override_is_explicit_and_does_not_mutate_environment(monkeypatch):
    monkeypatch.setenv("JOB_RADAR_CODEX_MODEL", "environment-model")
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        return Result(answer="Grounded", examples=["one", "two"]), {"backend": "codex"}
    monkeypatch.setattr(codex_client, "structured_response", fake)
    _, raw = invoke(settings("codex", model="chosen-model", custom_model=True))
    assert calls[0]["model"] == "chosen-model"
    assert providers.os.getenv("JOB_RADAR_CODEX_MODEL") == "environment-model"
    assert raw["provider_settings"]["model"] == "chosen-model"
    assert providers.provider_identity(providers.default_settings())["model"] == "environment-model"


def test_preview_explains_target_without_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret")
    preview = json.loads(providers.request_preview(settings("openai"), "system", "user", Result))
    assert preview["provider"] == "openai"
    assert preview["base_url"] == "https://api.openai.com/v1"
    assert preview["model"] == "test-model" and preview["web_search"] == "disabled"
    assert "fake-secret" not in str(preview)


@pytest.mark.parametrize("value", [settings(), settings("ollama", ollama_thinking=False)])
def test_generation_audits_provider_and_rejects_resume_after_model_change(tmp_path, value):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    job_id, _ = db.upsert_job(job())
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile()), encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "cv.tex.j2").write_text("((( profile_text | tex )))", encoding="utf-8")
    (templates / "motivation_letter.tex.j2").write_text("((( letter.subject | tex )))", encoding="utf-8")
    arguments = dict(db=db, selection_path=tmp_path / "unused.yaml", job_ids_override=[job_id],
                     profile_path=profile_path, template_path=templates / "cv.tex.j2",
                     output_root=tmp_path / "applications", dry_run=False, compile_pdf=False, offline=True,
                     response_fn=lambda **kwargs: (application(), {}), reference_library=tmp_path / "references")
    [directory] = generate_cvs(**arguments, provider_settings=value)
    for name in ("generation_context.json", "generation_inputs.json"):
        data = json.loads((directory / name).read_text())
        assert data["provider_settings"]["provider"] == value["provider"]
        assert data["provider_settings"]["model"] == "test-model"
        if value["provider"] == "ollama":
            assert data["provider_settings"]["ollama_thinking"] is False
    assert (directory / "cv.tex").exists() and (directory / "motivation_letter.tex").exists()
    with pytest.raises(ValueError, match="Cannot resume"):
        generate_cvs(**arguments, provider_settings={**value, "model": "different-model"}, resume=True)
    if value["provider"] == "ollama":
        with pytest.raises(ValueError, match="Cannot resume"):
            generate_cvs(**arguments, provider_settings={**value, "ollama_thinking": True}, resume=True)


def test_revision_uses_chosen_provider_retains_companion_and_original_context(revision_source, tmp_path, monkeypatch):
    before = {path.name: path.read_bytes() for path in revision_source.iterdir() if path.is_file()}
    proposal = application().model_copy(deep=True)
    proposal.profile = "Researcher with experience coordinating samples."
    proposal.motivation_letter.subject = "Must not replace the companion"
    calls = []
    def response_fn(value):
        assert value["provider"] == "anthropic"
        def answer(**kwargs):
            calls.append(kwargs)
            return proposal, {"provider_settings": providers.provider_identity(value)}
        return answer
    monkeypatch.setattr("job_radar.revisions.resolve_response_fn", response_fn)
    directory = revise_application(source_dir=revision_source, document="cv", feedback="Clarify opening.",
                                   output_root=tmp_path / "versions", provider_settings=settings("anthropic"))
    audit = json.loads((directory / "revision.json").read_text())
    assert audit["provider"] == "anthropic" and audit["model"] == "test-model"
    assert audit["status"] == "validated" and len(calls) == 1
    assert (directory / "motivation_letter.tex").read_bytes() == before["motivation_letter.tex"]
    assert {path.name: path.read_bytes() for path in revision_source.iterdir() if path.is_file()} == before
    assert json.loads((directory / "generation_context.json").read_text())["provider_settings"]["provider"] == "anthropic"
