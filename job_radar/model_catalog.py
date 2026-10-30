"""Read only Codex model metadata; never read sign-in data or contact a model.

The local cache is a useful picker, not proof of current account access. Model
names and reasoning levels come from the installed client rather than a static
list that becomes outdated when Codex changes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tomllib

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,199}\Z")


def valid_model(value: object) -> bool:
    return (isinstance(value, str) and bool(MODEL_PATTERN.fullmatch(value))
            and not value.startswith(("sk-", "AIza")))


def _small_text(path: Path, limit: int) -> str:
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("Not a small local metadata file")
    with path.open("r", encoding="utf-8-sig") as stream:
        text = stream.read(limit + 1)
    if len(text) > limit:
        raise ValueError("Metadata file is too large")
    return text


def read_catalog(codex_home: Path | None = None) -> dict:
    """Return a projection of two known files, excluding all unrelated settings."""
    home = codex_home or Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
    result = {"models": [], "configured_model": "", "configured_effort": "",
              "catalog_available": False, "notes": []}
    try:
        raw = json.loads(_small_text(home / "models_cache.json", 4 * 1024 * 1024))
        records = raw.get("models", []) if isinstance(raw, dict) else []
        if not isinstance(records, list) or len(records) > 500:
            raise ValueError("Invalid model list")
        models = {}
        for record in records:
            if not isinstance(record, dict) or record.get("visibility", "list") != "list":
                continue
            model = record.get("slug")
            if not valid_model(model):
                continue
            levels = record.get("supported_reasoning_levels", [])
            if not isinstance(levels, list):
                continue
            supported = [level.get("effort") if isinstance(level, dict) else level for level in levels]
            supported = [level for level in EFFORTS if level in supported]
            default = record.get("default_reasoning_level", "")
            priority = record.get("priority", 999)
            models[model] = {"model": model, "efforts": supported,
                             "default_effort": default if default in supported else "",
                             "priority": priority if isinstance(priority, (float, int)) else 999}
        result["models"] = sorted(models.values(), key=lambda value: (value["priority"], value["model"]))
        result["catalog_available"] = bool(result["models"])
    except (OSError, ValueError, UnicodeError, RecursionError):
        result["notes"].append("No readable local model list. Open Codex and sign in, then reopen AI settings; custom model entry is also available.")
    try:
        config = tomllib.loads(_small_text(home / "config.toml", 1024 * 1024))
        # Support legacy named profiles, without reading referenced files,
        # providers, credentials or the rest of the user's configuration.
        selected = config.get("profile")
        profiles = config.get("profiles", {})
        profile = profiles.get(selected, {}) if isinstance(selected, str) and isinstance(profiles, dict) else {}
        profile = profile if isinstance(profile, dict) else {}
        model = profile.get("model", config.get("model", ""))
        effort = profile.get("model_reasoning_effort", config.get("model_reasoning_effort", ""))
        result["configured_model"] = model if valid_model(model) else ""
        result["configured_effort"] = effort if effort in EFFORTS else ""
    except (OSError, ValueError, UnicodeError, RecursionError):
        result["notes"].append("No readable configured model default. Select a model explicitly.")
    return result


def resolve_selection(settings: dict, *, catalog: dict | None = None) -> dict:
    """Resolve the exact model/effort for a request and validate known combinations."""
    catalog = read_catalog() if catalog is None else catalog
    known = {item["model"]: item for item in catalog["models"]}
    model = settings.get("model", "").strip()
    custom = settings.get("custom_model", False)
    source = "Job Radar selection"
    if model and model not in known and not custom:
        raise ValueError("This model is not in Codex's local model list. Choose a listed model, or select Custom model (not verified) to enter an exact identifier.")
    explicit_model = bool(model)
    if not model:
        model = os.getenv("JOB_RADAR_CODEX_MODEL", "").strip()
        source = "JOB_RADAR_CODEX_MODEL" if model else "Codex config.toml"
        model = model or catalog["configured_model"]
        if not model:
            source = "Codex built-in default (not known locally)"
    if model and not valid_model(model):
        raise ValueError("The configured Codex model name is invalid. Choose a model in AI settings.")
    effort = settings.get("reasoning_effort", "").strip()
    effort_source = "Job Radar selection"
    if not effort:
        # Explicit model selection uses that model's default. Environment and
        # user config defaults apply only to the configured-default selection.
        if not explicit_model:
            effort = os.getenv("JOB_RADAR_CODEX_REASONING", "").strip()
            effort_source = "JOB_RADAR_CODEX_REASONING" if effort else "Codex config.toml"
            effort = effort or catalog["configured_effort"]
        if not effort:
            effort = known.get(model, {}).get("default_effort", "")
            effort_source = "Model default"
    if effort and effort not in EFFORTS:
        raise ValueError("Choose a supported Codex reasoning level.")
    levels = known.get(model, {}).get("efforts", [])
    if effort and model in known and effort not in levels:
        raise ValueError(f"{model} does not list '{effort}' as supported. Choose one of: {', '.join(levels) or 'model default'}.")
    return {"model": model, "reasoning_effort": effort, "model_source": source,
            "reasoning_source": effort_source, "model_in_local_catalog": model in known,
            "custom_model": bool(custom)}
