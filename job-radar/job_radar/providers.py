"""Small, explicit document-generation adapters using the existing HTTP client.

Settings contain names and endpoints, never API keys. No provider is contacted
while saving/checking settings. Requests have no search/tools, retries, redirects
or automatic fallback to another service. Local schema and fact checks remain
outside the provider and apply equally to every response.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from copy import deepcopy
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ValidationError

from . import codex_client
from .cancellation import check_cancelled, current_token
from .model_catalog import EFFORTS, resolve_selection


PROVIDER_LABELS = {
    "codex": "Codex", "openai": "OpenAI API",
    "openai_compatible": "OpenAI-compatible API / local Ollama",
    "ollama": "Ollama (local)",
    "anthropic": "Anthropic API", "gemini": "Google Gemini API",
}
PROVIDER_DEFAULTS = {
    "codex": {"base_url": "", "api_key_env": ""},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "openai_compatible": {"base_url": "http://localhost:11434/v1", "api_key_env": ""},
    "ollama": {"base_url": "http://localhost:11434/api", "api_key_env": ""},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "api_key_env": "ANTHROPIC_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta", "api_key_env": "GEMINI_API_KEY"},
}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
REQUEST_TIMEOUT = 180
MAX_OUTPUT_TOKENS = 16_384
OLLAMA_METADATA_TIMEOUT = 10
OLLAMA_CONTEXT_DEFAULT = 32_768
OLLAMA_CONTEXT_CHOICES = (4096, 8192, 16_384, 32_768, 65_536, 131_072, 262_144)
OLLAMA_REQUEST_TIMEOUT = 600
MAX_OLLAMA_THINKING_VALUES = 16
SETTINGS_KEYS = {"schema_version", "provider", "model", "base_url", "api_key_env", "reasoning_effort", "custom_model", "ollama_thinking", "ollama_context_tokens"}


class ProviderError(RuntimeError):
    """A safe diagnostic that never includes server bodies or credentials."""


def default_settings(provider: str = "codex") -> dict:
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError("Choose a supported AI provider.")
    return {"schema_version": 1, "provider": provider, "model": "", "reasoning_effort": "",
            "custom_model": False, "ollama_thinking": None, "ollama_context_tokens": OLLAMA_CONTEXT_DEFAULT, **PROVIDER_DEFAULTS[provider]}


def _local_endpoint(url: str) -> bool:
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _endpoint(value: str) -> str:
    if not value or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("Enter a valid API base URL without spaces or credentials.")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"https", "http"} or not parts.hostname or
                parts.username is not None or parts.password is not None or
                parts.query or parts.fragment or parts.port == 0):
            raise ValueError
        if parts.scheme == "http" and not _local_endpoint(value):
            raise ValueError
    except ValueError:
        raise ValueError("API URLs must use HTTPS (HTTP is allowed only on this computer), with no credentials, query or fragment.") from None
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _valid_ollama_thinking(value) -> bool:
    """Accept bounded model-defined names without interpreting their meaning."""
    return isinstance(value, bool) or (isinstance(value, str) and
                                     re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", value) is not None)


def normalize_settings(settings: dict, *, require_model: bool = False) -> dict:
    if not isinstance(settings, dict) or set(settings) - SETTINGS_KEYS:
        raise ValueError("AI settings accept only provider, model, reasoning level, Ollama thinking/context choices, custom-model choice, base URL and API-key environment-variable name. Never enter an API key here.")
    if settings.get("schema_version", 1) != 1:
        raise ValueError("Unsupported AI settings version.")
    provider = settings.get("provider", "codex")
    if not isinstance(provider, str) or provider not in PROVIDER_DEFAULTS:
        raise ValueError("Choose a supported AI provider.")
    normalized = default_settings(provider)
    for name in ("model", "base_url", "api_key_env", "reasoning_effort"):
        value = settings.get(name, normalized[name])
        if not isinstance(value, str):
            raise ValueError("AI setting values must be text.")
        normalized[name] = value.strip()
    custom = settings.get("custom_model", False)
    if not isinstance(custom, bool):
        raise ValueError("Custom model must be an explicit on/off choice.")
    normalized["custom_model"] = custom
    model = normalized["model"]
    if model and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,199}", model)
                  or model.startswith(("sk-", "AIza"))):
        raise ValueError("Enter a model name, not an API key or URL.")
    if provider == "gemini" and model and not re.fullmatch(r"[A-Za-z0-9._\-]+", model):
        raise ValueError("Enter the Gemini model ID without the models/ prefix.")
    key_env = normalized["api_key_env"]
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key_env):
        raise ValueError("Enter an environment-variable name such as OPENAI_API_KEY, not the key itself.")
    if provider == "codex":
        normalized.update(base_url="", api_key_env="")
        if normalized["reasoning_effort"] and normalized["reasoning_effort"] not in EFFORTS:
            raise ValueError("Choose a supported Codex reasoning level.")
        selection = resolve_selection(normalized)
        if require_model and not selection["model"]:
            raise ValueError("Codex's default model is not known locally. Select a model in AI settings before generating.")
    else:
        # Codex reasoning names are independent of Ollama's model-defined
        # thinking choices. Other API adapters keep their existing behavior.
        normalized.update(reasoning_effort="", custom_model=False)
        normalized["base_url"] = _endpoint(normalized["base_url"])
        if provider == "ollama":
            if not _local_endpoint(normalized["base_url"]):
                raise ValueError("Ollama (local) requires localhost or a loopback IP address.")
            if urlsplit(normalized["base_url"]).path != "/api":
                raise ValueError("Ollama (local) uses a base URL ending in /api. For /v1, choose OpenAI-compatible API instead.")
            thinking = settings.get("ollama_thinking")
            if thinking is not None and not _valid_ollama_thinking(thinking):
                raise ValueError("Ollama thinking must be Model default, on/off, or a model-defined name of at most 32 safe characters.")
            normalized["ollama_thinking"] = thinking
            context = settings.get("ollama_context_tokens", OLLAMA_CONTEXT_DEFAULT)
            if type(context) is not int or context not in OLLAMA_CONTEXT_CHOICES:
                raise ValueError("Choose an Ollama context window from 4,096 to 262,144 tokens using the selector.")
            normalized["ollama_context_tokens"] = context
        elif provider != "openai_compatible" and normalized["base_url"] != PROVIDER_DEFAULTS[provider]["base_url"]:
            raise ValueError("This provider uses its official API endpoint. Choose OpenAI-compatible API for a custom endpoint.")
        if not key_env and not (provider in {"openai_compatible", "ollama"} and _local_endpoint(normalized["base_url"])):
            raise ValueError("Set the name of the environment variable holding this service's API key.")
        if require_model and not model:
            raise ValueError("Enter the exact model name for the selected API provider.")
    return normalized


def load_settings(root: Path) -> dict:
    path = Path(root) / "data" / "ai_settings.json"
    if not path.exists():
        return default_settings()
    if path.is_symlink() or path.stat().st_size > 16_384:
        raise ValueError("AI settings must be a small local JSON file.")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        # Existing free-text selections remain explicit custom selections on
        # upgrade. This does not silently replace them with a newer model.
        if isinstance(value, dict) and value.get("provider", "codex") == "codex" and "custom_model" not in value:
            value["custom_model"] = bool(value.get("model"))
        return normalize_settings(value)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("AI settings could not be read. Correct data/ai_settings.json before generating documents.") from None


def save_settings(root: Path, settings: dict) -> dict:
    normalized = normalize_settings(settings)
    path = Path(root) / "data" / "ai_settings.json"
    if path.is_symlink():
        raise ValueError("AI settings must be a local file, not a link.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".ai-settings-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return normalized


def provider_identity(settings: dict) -> dict:
    value = normalize_settings(settings)
    model = value["model"]
    identity = {"provider": value["provider"], "model": model or "Not selected",
                "base_url": value["base_url"], "api_key_env": value["api_key_env"]}
    if value["provider"] == "codex":
        identity.update(resolve_selection(value))
        identity["model"] = identity["model"] or "Codex built-in default (not known locally)"
        identity["reasoning_effort"] = identity["reasoning_effort"] or "Model default"
    elif value["provider"] == "ollama":
        identity["ollama_thinking"] = value["ollama_thinking"]
        identity["ollama_context_tokens"] = value["ollama_context_tokens"]
    return identity


def describe(settings: dict) -> str:
    identity = provider_identity(settings)
    label = f"{PROVIDER_LABELS[identity['provider']]} · {identity['model']}"
    if identity["provider"] == "codex":
        label += f" · reasoning: {identity['reasoning_effort']}"
        if identity["custom_model"]:
            label += " · custom model"
    elif identity["provider"] == "ollama":
        thinking = identity["ollama_thinking"]
        display = "Model default" if thinking is None else ("On" if thinking is True else "Off" if thinking is False else thinking)
        label += f" · thinking: {display} · context: {identity['ollama_context_tokens']:,} tokens"
    if identity["base_url"]:
        label += f" · {identity['base_url']}"
    return label


def settings_check(settings: dict) -> dict:
    """Check configuration/key presence locally; does not verify an account/model."""
    try:
        value = normalize_settings(settings, require_model=True)
    except ValueError as exc:
        return {"ready": False, "detail": str(exc), "network_checked": False}
    if value["provider"] == "codex":
        try:
            present = bool(codex_client.codex_executable())
        except (OSError, RuntimeError):
            present = False
        detail = (describe(value) + ". Helper found. Use Check setup to verify sign-in. Model access has not been tested; no model request was made."
                  if present else "Codex helper was not found. Use Check setup for help.")
        return {"ready": present, "detail": detail, "network_checked": False}
    key_name = value["api_key_env"]
    if key_name and not os.getenv(key_name, "").strip():
        return {"ready": False, "detail": f"{key_name} is not set in this application's environment. Set it and restart Job Radar. No request was sent.", "network_checked": False}
    return {"ready": True, "detail": "Settings are complete. Connection, model support and account access have not been tested. No request was sent.", "network_checked": False}


def request_preview(settings: dict, system: str, user: str, schema_type: type[BaseModel]) -> str:
    identity = provider_identity(settings)
    return json.dumps({"backend": identity["provider"], **identity,
                       "web_search": "disabled", "schema": schema_type.model_json_schema(),
                       "system": system, "user": user}, ensure_ascii=False, indent=2)


def _schema(schema_type: type[BaseModel], provider: str) -> dict:
    """Keep local validation strict even if a provider accepts fewer constraints."""
    schema = deepcopy(schema_type.model_json_schema())
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            if provider == "anthropic":
                # Claude's grammar excludes these constraints. They remain in
                # the prompt schema and are enforced by Pydantic afterwards.
                for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                            "multipleOf", "minLength", "maxLength", "maxItems", "minItems"):
                    node.pop(key, None)
            for child in node.values():
                visit(child)
    visit(schema)
    return schema


def _check_response_status(code: int) -> None:
    if code == 200:
        return
    if code in (401, 403):
        message = "The service rejected authentication or access. Check the API-key environment variable and account permissions."
    elif code == 429:
        message = "The service reported a usage or rate limit. Check the account before retrying."
    elif 300 <= code < 400:
        message = "The API returned a redirect. No credentials or document data were forwarded. Check the base URL."
    elif code in (400, 404, 422):
        message = "The service rejected the request. Check the exact model name and its JSON-schema support."
    else:
        message = "The API request failed. Try again later after checking the service."
    raise ProviderError(f"{message} (HTTP {code}; no automatic retry.)")


async def _post_cancellable(url, headers, encoded, request_timeout, transport, token):
    """Cancel the HTTP task and await socket cleanup; never detach a request."""
    async def receive():
        async with httpx.AsyncClient(timeout=httpx.Timeout(request_timeout, connect=10),
                                     follow_redirects=False, trust_env=False,
                                     transport=transport) as client:
            async with client.stream("POST", url, headers=headers, content=encoded) as response:
                _check_response_status(response.status_code)
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16_384):
                    token.check()
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ProviderError("The service response exceeded the size limit. No document was generated.")
                return data

    token.check()
    # wait_for bounds the entire request, including a server which trickles
    # bytes indefinitely without hitting the per-read timeout.
    task = asyncio.create_task(asyncio.wait_for(receive(), timeout=request_timeout))
    try:
        while not task.done():
            token.check()
            await asyncio.wait({task}, timeout=.05)
        token.check()
        return await task
    finally:
        if not task.done():
            task.cancel()
        # Retrieve every task's exception and finish connection cleanup before
        # the generation worker can be marked stopped or start another run.
        with suppress(asyncio.CancelledError, Exception):
            await task


def _post(url: str, headers: dict, payload: dict, *, transport=None, timeout=None) -> dict:
    check_cancelled()
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ProviderError("The document request is too large. Reduce reference text before trying again.")
    started = time.monotonic()
    request_timeout = REQUEST_TIMEOUT if timeout is None else timeout
    request_headers = {"Content-Type": "application/json", **headers}
    try:
        token = current_token()
        if token is not None:
            data = asyncio.run(_post_cancellable(url, request_headers, encoded, request_timeout, transport, token))
        else:
            with httpx.Client(timeout=httpx.Timeout(request_timeout, connect=10),
                              follow_redirects=False, trust_env=False, transport=transport) as client:
                with client.stream("POST", url, headers=request_headers, content=encoded) as response:
                    _check_response_status(response.status_code)
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16_384):
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ProviderError("The service response exceeded the size limit. No document was generated.")
                        if time.monotonic() - started > request_timeout:
                            raise ProviderError("The service response timed out. No automatic retry was made.")
    except (httpx.TimeoutException, asyncio.TimeoutError):
        check_cancelled()
        raise ProviderError("The AI request timed out. No automatic retry was made; check the service before retrying.") from None
    except (httpx.HTTPError, UnicodeError, ValueError):
        check_cancelled()
        raise ProviderError("The AI service could not be reached. Check the endpoint and connection; no automatic retry was made.") from None
    check_cancelled()
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("The service returned unreadable JSON. No document was generated.") from None


def ollama_thinking_options(settings: dict, *, transport=None) -> dict:
    """Read supported choices from local model metadata, without document text.

    Missing metadata does not establish whether thinking is supported. In that
    case only Model default can be used, with no explicit ``think`` parameter.
    This is an explicit network action, separate from saving/checking settings.
    """
    value = normalize_settings(settings, require_model=True)
    if value["provider"] != "ollama":
        raise ValueError("Thinking options can be loaded only for Ollama (local).")
    check = settings_check(value)
    if not check["ready"]:
        raise ProviderError(check["detail"])
    key = os.getenv(value["api_key_env"], "").strip() if value["api_key_env"] else ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    raw = _post(value["base_url"] + "/show", headers, {"model": value["model"]},
                transport=transport, timeout=OLLAMA_METADATA_TIMEOUT)
    if "error" in raw:
        raise ProviderError("Ollama could not provide model information. Check the model name; no document request was sent.")
    if "thinking" not in raw:
        return {"values": [], "default": None,
                "detail": "This model's metadata does not report thinking controls. Use Model default; explicit choices cannot be verified."}
    metadata = raw["thinking"]
    if not isinstance(metadata, dict):
        raise ProviderError("Ollama returned invalid thinking metadata. Use Model default or check the local model; no document request was sent.")
    values = metadata.get("values")
    default = metadata.get("default")
    if (not isinstance(values, list) or not 1 <= len(values) <= MAX_OLLAMA_THINKING_VALUES or
            any(not _valid_ollama_thinking(item) for item in values) or
            len(set(values)) != len(values) or
            (default is not None and (not _valid_ollama_thinking(default) or default not in values))):
        raise ProviderError("Ollama returned invalid thinking metadata. Use Model default or check the local model; no document request was sent.")
    detail = ("This model reports no thinking support; its only explicit choice is Off."
              if values == [False] else "Thinking choices reported by the selected local model. Model default leaves the choice to Ollama.")
    return {"values": list(values), "default": default, "detail": detail}


def _response_error(provider: str, reason: str, *, thinking_only: bool = False) -> ProviderError:
    """Fixed user-facing explanations; never interpolate a server error/body."""
    label = PROVIDER_LABELS.get(provider, "The service")
    explanations = {
        "output_limit": "stopped at its generation or context limit before completing the document. Check the context/output allowance or reduce the input before retrying.",
        "refusal": "declined to provide the requested document.",
        "incomplete": "did not report that its response had completed.",
        "empty_content": ("returned reasoning but no document text." if thinking_only else "returned no document text."),
        "malformed_response": "returned an unexpected response structure.",
        "service_error": "reported a generation error. Check the service's own status or local logs.",
        "unexpected_tool_call": "returned a tool request instead of the requested document.",
    }
    error = ProviderError(f"{label} {explanations[reason]} It did not return a complete structured document. No automatic retry was made.")
    error.reason = reason
    return error


def _response_diagnostics(provider: str, raw: dict, reason: str) -> dict:
    """Retain only bounded status/count evidence, never text, traces or errors."""
    allowed_reasons = {"output_limit", "refusal", "incomplete", "empty_content", "malformed_response",
                       "service_error", "unexpected_tool_call", "schema_validation"}
    result = {"schema_version": 1, "provider": provider if provider in PROVIDER_LABELS else "unknown",
              "failure_reason": reason if reason in allowed_reasons else "unknown"}
    if not isinstance(raw, dict):
        return result
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    stop = raw.get("done_reason", raw.get("stop_reason"))
    choices = raw.get("choices")
    if provider == "openai_compatible" and isinstance(choices, list) and choices and isinstance(choices[0], dict):
        stop = choices[0].get("finish_reason")
        candidate = choices[0].get("message")
        message = candidate if isinstance(candidate, dict) else {}
    candidates = raw.get("candidates")
    if provider == "gemini" and isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        stop = candidates[0].get("finishReason")
    permitted_stops = {"stop", "length", "end_turn", "max_tokens", "tool_use", "tool_calls", "content_filter",
                       "refusal", "STOP", "MAX_TOKENS", "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}
    result["stop_reason"] = stop if isinstance(stop, str) and stop in permitted_stops else "unknown"
    if type(raw.get("done")) is bool:
        result["done"] = raw["done"]
    if raw.get("status") in ("completed", "incomplete", "failed", "cancelled", "queued", "in_progress"):
        result["status"] = raw["status"]
    result["has_service_error"] = "error" in raw
    result["has_refusal"] = bool(message.get("refusal"))
    result["has_tool_calls"] = bool(message.get("tool_calls"))
    for field in ("content", "thinking"):
        value = message.get(field)
        result[field + "_chars"] = min(len(value), MAX_RESPONSE_BYTES) if isinstance(value, str) else 0
    numeric_fields = {"prompt_eval_count", "eval_count", "total_duration", "load_duration", "prompt_eval_duration", "eval_duration",
                      "input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
                      "promptTokenCount", "candidatesTokenCount", "totalTokenCount", "thoughtsTokenCount"}
    usage = raw.get("usage", raw.get("usageMetadata", {}))
    usage = usage if isinstance(usage, dict) else {}
    result["counts"] = {name: number for source in (usage, raw) for name, number in source.items()
                        if name in numeric_fields and type(number) is int and 0 <= number <= 1_000_000_000_000_000}
    return result


def _request_limits(provider: str, payload: dict) -> dict:
    """Numeric request limits only; never preserve arbitrary request fields."""
    options = payload.get("options")
    options = options if isinstance(options, dict) else {}
    timeout = OLLAMA_REQUEST_TIMEOUT if provider == "ollama" else REQUEST_TIMEOUT
    candidates = {"context_tokens": options.get("num_ctx"),
                  "output_tokens": options.get("num_predict", payload.get("max_output_tokens", payload.get("max_tokens"))),
                  "timeout_seconds": timeout}
    return {name: value for name, value in candidates.items()
            if type(value) is int and 0 < value <= 1_000_000_000}


def _save_response_failure(artifact_dir, provider: str, raw: dict, reason: str,
                           *, request_limits: dict | None = None) -> None:
    if artifact_dir is None:
        return
    try:
        directory = Path(artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "provider_failure.json"
        # Failed diagnostics must neither follow an existing link nor obscure
        # the underlying provider failure if the folder cannot be written.
        if target.is_symlink():
            return
        diagnostics = _response_diagnostics(provider, raw, reason)
        if isinstance(request_limits, dict):
            diagnostics["request_limits"] = {name: value for name, value in request_limits.items()
                if name in {"context_tokens", "output_tokens", "timeout_seconds"}
                and type(value) is int and 0 < value <= 1_000_000_000}
        target.write_text(json.dumps(diagnostics, indent=2), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass


def _response_text(provider: str, raw: dict) -> str:
    try:
        if provider == "openai":
            if raw.get("status") != "completed":
                detail = raw.get("incomplete_details")
                cause = detail.get("reason") if isinstance(detail, dict) else None
                reason = "output_limit" if cause == "max_output_tokens" else "refusal" if cause == "content_filter" else "incomplete"
                raise _response_error(provider, reason)
            parts = [part for item in raw["output"] if item.get("type") == "message"
                     for part in item.get("content", [])]
            if any(part.get("type") == "refusal" for part in parts):
                raise _response_error(provider, "refusal")
            text = "".join(part["text"] for part in parts if part.get("type") == "output_text")
        elif provider == "openai_compatible":
            choice = raw["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") == "length":
                raise _response_error(provider, "output_limit")
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise _response_error(provider, "refusal")
            if message.get("tool_calls"):
                raise _response_error(provider, "unexpected_tool_call")
            if choice.get("finish_reason") != "stop":
                raise _response_error(provider, "incomplete")
            text = message["content"]
        elif provider == "ollama":
            if "error" in raw:
                raise _response_error(provider, "service_error")
            message = raw["message"]
            if raw.get("done_reason") == "length":
                raise _response_error(provider, "output_limit")
            if message.get("refusal"):
                raise _response_error(provider, "refusal")
            if message.get("tool_calls"):
                raise _response_error(provider, "unexpected_tool_call")
            if raw.get("done") is not True or raw.get("done_reason") != "stop":
                raise _response_error(provider, "incomplete")
            # Reasoning traces are deliberately excluded from output/artifacts.
            text = message.get("content")
            if not isinstance(text, str) or not text.strip():
                raise _response_error(provider, "empty_content", thinking_only=bool(message.get("thinking")))
        elif provider == "anthropic":
            if raw.get("stop_reason") == "max_tokens":
                raise _response_error(provider, "output_limit")
            if raw.get("stop_reason") == "refusal":
                raise _response_error(provider, "refusal")
            if raw.get("stop_reason") == "tool_use":
                raise _response_error(provider, "unexpected_tool_call")
            if raw.get("stop_reason") != "end_turn":
                raise _response_error(provider, "incomplete")
            text = "".join(part["text"] for part in raw["content"] if part.get("type") == "text")
        else:
            candidate = raw["candidates"][0]
            if candidate.get("finishReason") == "MAX_TOKENS":
                raise _response_error(provider, "output_limit")
            if candidate.get("finishReason") in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
                raise _response_error(provider, "refusal")
            if candidate.get("finishReason") != "STOP":
                raise _response_error(provider, "incomplete")
            text = "".join(part["text"] for part in candidate["content"]["parts"]
                           if "text" in part and not part.get("thought"))
        if not isinstance(text, str) or not text.strip():
            raise _response_error(provider, "empty_content")
        return text
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise _response_error(provider, "malformed_response") from None


def resolve_response_fn(settings: dict, *, transport=None) -> Callable:
    value = normalize_settings(settings)
    identity = provider_identity(value)

    def respond(*, schema_type, schema_name, system, user, use_web_search,
                reasoning_effort="medium", artifact_dir=None):
        check_cancelled()
        if use_web_search:
            raise ValueError("Document providers do not support web search. Import the vacancy before generating documents.")
        normalize_settings(value, require_model=True)
        provider = value["provider"]
        if provider == "codex":
            # Snapshot the values displayed/audited for this run. In particular,
            # environment variables must not overwrite an explicit UI effort.
            parsed, raw = codex_client.structured_response(
                schema_type=schema_type, schema_name=schema_name, system=system, user=user,
                use_web_search=False, reasoning_effort="" if identity["reasoning_effort"] == "Model default" else identity["reasoning_effort"],
                artifact_dir=artifact_dir, model=identity["model"], use_environment=False,
            )
            check_cancelled()
            return parsed, {**raw, "provider_settings": identity}
        check = settings_check(value)
        if not check["ready"]:
            raise ProviderError(check["detail"])
        key = os.getenv(value["api_key_env"], "").strip() if value["api_key_env"] else ""
        schema = _schema(schema_type, provider)
        headers = {}
        # The full local schema also appears in the instructions, so constraints
        # omitted from a provider's grammar remain visible to that model.
        instructions = system + "\nReturn only one JSON object matching this schema:\n" + json.dumps(schema_type.model_json_schema())
        if provider == "openai":
            url = value["base_url"] + "/responses"
            headers = {"Authorization": f"Bearer {key}"}
            payload = {"model": value["model"], "store": False, "max_output_tokens": MAX_OUTPUT_TOKENS,
                       "input": [{"role": "system", "content": instructions}, {"role": "user", "content": user}],
                       "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}}}
        elif provider == "openai_compatible":
            url = value["base_url"] + "/chat/completions"
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            payload = {"model": value["model"], "max_tokens": MAX_OUTPUT_TOKENS,
                       "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": user}],
                       "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}}}
        elif provider == "ollama":
            thinking = value["ollama_thinking"]
            if thinking is not None:
                options = ollama_thinking_options(value, transport=transport)
                if thinking not in options["values"]:
                    raise ProviderError("The selected Ollama thinking choice is not supported by the model's current metadata. Load thinking options and choose a supported value or Model default. No document request was sent.")
            url = value["base_url"] + "/chat"
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            payload = {"model": value["model"], "stream": False,
                       "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": user}],
                       "format": schema, "options": {"num_predict": MAX_OUTPUT_TOKENS,
                                                      "num_ctx": value["ollama_context_tokens"]}}
            if thinking is not None:
                payload["think"] = thinking
        elif provider == "anthropic":
            url = value["base_url"] + "/messages"
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
            payload = {"model": value["model"], "max_tokens": MAX_OUTPUT_TOKENS, "system": instructions,
                       "messages": [{"role": "user", "content": user}],
                       "output_config": {"format": {"type": "json_schema", "schema": schema}}}
        else:
            url = value["base_url"] + f"/models/{value['model']}:generateContent"
            headers = {"x-goog-api-key": key}
            payload = {"systemInstruction": {"parts": [{"text": instructions}]},
                       "contents": [{"role": "user", "parts": [{"text": user}]}],
                       "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS,
                                            "responseFormat": {"text": {"mimeType": "application/json", "schema": schema}}}}
        # Local thinking models can use the input + output context for several
        # minutes. Keep a finite deadline and the existing cancellation path.
        request_timeout = OLLAMA_REQUEST_TIMEOUT if provider == "ollama" else REQUEST_TIMEOUT
        raw = _post(url, headers, payload, transport=transport, timeout=request_timeout)
        check_cancelled()
        try:
            text = _response_text(provider, raw)
        except ProviderError as exc:
            _save_response_failure(artifact_dir, provider, raw, getattr(exc, "reason", "unknown"),
                                   request_limits=_request_limits(provider, payload))
            raise
        try:
            parsed = schema_type.model_validate_json(text)
        except (ValidationError, ValueError):
            _save_response_failure(artifact_dir, provider, raw, "schema_validation",
                                   request_limits=_request_limits(provider, payload))
            raise ProviderError("The AI response did not match the required document schema. No document was generated; no automatic retry was made.") from None
        check_cancelled()
        # Persist only validated output and safe identifiers. Provider responses
        # can echo headers/prompts in unexpected fields; never log those fields.
        actual_model = raw.get("model", raw.get("modelVersion"))
        actual_model = actual_model if isinstance(actual_model, str) and re.fullmatch(r"[A-Za-z0-9._:/+\-]{1,200}", actual_model) and not (key and key in actual_model) else None
        usage = raw.get("usage", raw.get("usageMetadata", {}))
        allowed_usage = {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
                         "promptTokenCount", "candidatesTokenCount", "totalTokenCount", "thoughtsTokenCount"}
        usage = {name: number for name, number in usage.items() if name in allowed_usage
                 and isinstance(number, int) and not isinstance(number, bool)} if isinstance(usage, dict) else {}
        if provider == "ollama":
            usage = {name: raw[name] for name in ("prompt_eval_count", "eval_count")
                     if isinstance(raw.get(name), int) and not isinstance(raw[name], bool)
                     and 0 <= raw[name] <= 1_000_000_000_000}
        return parsed, {"backend": provider, "provider_settings": identity, "returned_model": actual_model,
                        "schema_name": schema_name, "usage": usage, "structured_output": parsed.model_dump(mode="json")}
    return respond
