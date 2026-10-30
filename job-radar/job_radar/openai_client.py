from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .config import api_key_available, model_name


T = TypeVar("T", bound=BaseModel)


class MissingApiKey(RuntimeError):
    pass


def _strict_schema(model_type: type[BaseModel]) -> dict:
    schema = model_type.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def structured_response(
    *,
    schema_type: type[T],
    schema_name: str,
    system: str,
    user: str,
    use_web_search: bool,
    reasoning_effort: str = "medium",
    artifact_dir: Path | None = None,
) -> tuple[T, dict]:
    if not api_key_available():
        raise MissingApiKey(
            "OPENAI_API_KEY is not set. Add it to your shell environment; "
            "do not put it in committed files."
        )

    from openai import OpenAI

    client = OpenAI()
    request: dict = {
        "model": model_name(),
        "reasoning": {"effort": reasoning_effort},
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": _strict_schema(schema_type),
                "strict": True,
            }
        },
    }
    if use_web_search:
        request["tools"] = [
            {
                "type": "web_search",
                "filters": {
                    "blocked_domains": [
                        "linkedin.com",
                        "indeed.com",
                        "glassdoor.com",
                        "jooble.org",
                        "ziprecruiter.com",
                    ]
                },
            }
        ]
        request["include"] = ["web_search_call.action.sources"]

    response = client.responses.create(**request)
    parsed = schema_type.model_validate_json(response.output_text)
    raw = response.model_dump(mode="json")
    return parsed, raw


def extract_web_sources(raw_response: dict) -> list[str]:
    found: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            url = value.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                found.add(url)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(raw_response.get("output", []))
    return sorted(found)


def pretty_request_preview(system: str, user: str) -> str:
    return json.dumps(
        {
            "model": model_name(),
            "system": system,
            "user": user,
        },
        ensure_ascii=False,
        indent=2,
    )
