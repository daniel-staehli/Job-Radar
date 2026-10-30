"""Make a reviewable vacancy draft from pasted text, entirely locally.

Only explicit labels and recognisable vacancy links supply structured fields.
The complete pasted text remains the description; prose is never sent to AI.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import yaml

from .url_import import validate_public_url

MAX_TEXT_BYTES = 250_000
REQUIRED_FIELDS = ("company", "title", "location", "description")
_LABELS = {
    "company": ("company", "employer", "organisation", "organization", "unternehmen", "arbeitgeber", "firma", "entreprise", "employeur"),
    "title": ("position", "title", "job title", "role", "job", "stellenbezeichnung", "stellentitel", "berufsbezeichnung", "poste", "intitulé du poste", "intitule du poste", "fonction"),
    "location": ("location", "job location", "work location", "standort", "arbeitsort", "ort", "lieu", "lieu de travail", "localisation"),
    "source_url": ("source url", "source_url", "job url", "vacancy url", "job link", "vacancy link", "url", "link", "stellenlink", "lien", "lien de l'offre", "lien de l’offre"),
}
_FIELD_BY_LABEL = {label: field for field, labels in _LABELS.items() for label in labels}
_LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?P<label>[^:\n=]{1,50})\s*[:=]\s*(?P<value>.*)$")
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_VACANCY_SEGMENT = re.compile(r"^(?:jobs?|careers?|vacancies|vacancy|positions?|stellen|stellenangebote|offres?|recrutement)(?:[-_].*)?$", re.IGNORECASE)


def _clean_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).strip("*")


def _input_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Paste the vacancy text first.")
    if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\r\t" for char in value):
        raise ValueError("The pasted text contains unsupported control characters. Paste plain text instead.")
    if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("The pasted vacancy exceeds the 250 KB text limit.")
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n").strip()


def _url_value(value: str) -> str:
    # Ignore prose around a labelled URL rather than treating it as part of a
    # host/path. Bracketed and Markdown pasted links are common clipboard text.
    matches = _URL.findall(value)
    if len(matches) != 1:
        raise ValueError("Expected one vacancy URL.")
    raw = matches[0].rstrip(".,;!?)\u3002]")
    url, _ = validate_public_url(raw, resolve=False)
    if any(char.isspace() for char in url):
        raise ValueError("The vacancy URL contains whitespace.")
    return url


def _looks_like_vacancy(url: str) -> bool:
    parts = urlsplit(url)
    segments = [part for part in parts.path.split("/") if part]
    # A board homepage alone is not a vacancy; require a detail path.
    if parts.hostname in {"jobs.ashbyhq.com", "jobs.lever.co", "jobs.eu.lever.co"}:
        return len(segments) >= 2
    return any(_VACANCY_SEGMENT.fullmatch(part) and index < len(segments) - 1
               for index, part in enumerate(segments))


def _one_value(values: list[str], field: str, warnings: list[str]) -> str:
    distinct = {}
    for value in values:
        cleaned = " ".join(value.split())
        if cleaned:
            distinct.setdefault(unicodedata.normalize("NFKC", cleaned).casefold(), cleaned)
    if len(distinct) > 1:
        warnings.append(f"Several different {field.replace('_', ' ')} values were found; choose the correct one before saving.")
        return ""
    return next(iter(distinct.values()), "")


class _TextDumper(yaml.SafeDumper):
    pass


def _represent_text(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


_TextDumper.add_representer(str, _represent_text)


def prepare_job_text(text: str, *, output_dir: Path, source_url: str = "") -> dict:
    """Save a local YAML draft and audit, without a model or database changes.

    A supplied source URL is optional and takes precedence over embedded links.
    Missing/ambiguous labels stay blank for human review. ``complete`` only
    means required fields are present, never that extraction has been verified.
    """
    description = _input_text(text)
    if not isinstance(source_url, str):
        raise ValueError("The optional vacancy link must be text.")
    explicit_url = ""
    if source_url.strip():
        explicit_url, _ = validate_public_url(source_url.strip(), resolve=False)
        if any(char.isspace() for char in explicit_url):
            raise ValueError("The vacancy link contains whitespace.")
    values = {field: [] for field in _LABELS}
    warnings = []
    labelled_url_seen = False
    for line in description.splitlines():
        match = _LINE.fullmatch(line)
        if match is None:
            continue
        field = _FIELD_BY_LABEL.get(_clean_label(match.group("label")))
        value = match.group("value").strip()
        if field is None or not value:
            continue
        if field == "source_url":
            labelled_url_seen = True
            try:
                value = _url_value(value)
            except ValueError:
                warnings.append("An invalid or ambiguous labelled vacancy link was left blank; review the source link.")
                continue
        values[field].append(value)
    job = {field: _one_value(values[field], field, warnings) for field in ("company", "title", "location")}
    urls = values["source_url"]
    if not labelled_url_seen:
        for raw in _URL.findall(description):
            try:
                candidate = _url_value(raw)
            except ValueError:
                continue
            if _looks_like_vacancy(candidate):
                urls.append(candidate)
    # URL paths can be case-sensitive. Do not use text's case-insensitive
    # deduplication when deciding whether several links refer to one vacancy.
    urls = list(dict.fromkeys(urls))
    found_url = urls[0] if len(urls) == 1 else ""
    if len(urls) > 1 and not explicit_url:
        warnings.append("Several vacancy links were found; choose the correct source link before saving.")
    if explicit_url and any(url != explicit_url for url in urls):
        warnings.append("The supplied source link differs from a pasted link; the supplied link was used. Review it before saving.")
    job.update(source_url=explicit_url or found_url, description=description, source="pasted text")
    missing = [field for field in REQUIRED_FIELDS if not job[field]]
    method = "pasted-text-labels" if any(values[field] for field in ("company", "title", "location")) else "pasted-text"
    warnings.insert(0, "Review the extracted details before saving. Unlabelled prose is kept as the full description; missing details are not guessed.")
    result = {"job": job, "complete": not missing, "needs_review": True,
              "missing_fields": missing, "warnings": list(dict.fromkeys(warnings)),
              "extraction_method": method, "final_url": job["source_url"]}
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # A bounded filename avoids long Windows paths and never incorporates
    # untrusted company text as a filesystem path.
    output_path = directory / f"pasted-vacancy-{uuid4().hex[:12]}.yaml"
    audit_path = output_path.with_suffix(".extraction.json")
    audit = {"source": "pasted text", "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
             "source_url": job["source_url"], "retrieved_at": datetime.now(timezone.utc).isoformat(),
             "extraction_method": method, "complete": result["complete"], "needs_review": True,
             "missing_fields": missing, "warnings": result["warnings"], "model_used": False,
             "network_used": False}
    content = "# Vacancy draft from pasted text. Review the details before saving.\n"
    content += yaml.dump(job, Dumper=_TextDumper, sort_keys=False, allow_unicode=True, width=100)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    with audit_path.open("x", encoding="utf-8") as handle:
        json.dump(audit, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    result.update(yaml_path=output_path, audit_path=audit_path)
    return result
