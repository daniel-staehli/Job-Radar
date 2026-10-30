from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .config import load_yaml
from .db import JobDatabase
from .models import SearchJob
from .normalize import canonicalize_url, payload_hash, stable_job_id
from .tracking import ensure_tracking_schema, get_application, update_application

REQUIRED_FIELDS = ("company", "title", "location")


def import_manual_job(db: JobDatabase, path: Path, *, dry_run: bool = False,
                      source: str | None = None, on_date: date | None = None) -> tuple[str, str]:
    return import_manual_data(db, load_yaml(path), dry_run=dry_run, source=source, on_date=on_date)


def import_manual_data(db: JobDatabase, raw: dict, *, dry_run: bool = False,
                       source: str | None = None, on_date: date | None = None,
                       identity_key: str | None = None) -> tuple[str, str]:
    """Import without fetching pages or using AI; preserve omitted existing fields."""
    if identity_key is not None and not re.fullmatch(r"job_ext_[0-9a-f]{24}", identity_key):
        raise ValueError("External job identity must be a validated connector identity.")
    if on_date is not None and (not isinstance(on_date, date) or isinstance(on_date, datetime)):
        raise ValueError("on_date must be a datetime.date")
    if not isinstance(raw, dict):
        raise ValueError("Manual job file must contain a YAML mapping.")
    unknown = set(raw) - set(SearchJob.model_fields) - {"description", "source"}
    if unknown:
        raise ValueError("Unknown manual job fields: " + ", ".join(sorted(map(str, unknown))))
    missing = [f for f in REQUIRED_FIELDS if not isinstance(raw.get(f), str) or not raw[f].strip()]
    if missing:
        raise ValueError("Manual job file is missing text for: " + ", ".join(missing))
    description = raw.get("description", raw.get("description_excerpt"))
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Manual job file is missing: description")
    source_url = raw.get("source_url", "")
    if not isinstance(source_url, str):
        raise ValueError("source_url must be an http:// or https:// job URL, or left blank.")
    source_url = source_url.strip()
    if source_url:
        try:
            parts = urlsplit(source_url)
            valid_url = (parts.scheme in ("http", "https") and bool(parts.hostname)
                         and not any(char.isspace() or ord(char) < 32 for char in source_url))
            parts.port  # Reject malformed or out-of-range ports before saving.
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("source_url must be an http:// or https:// job URL.")

    job_id = identity_key or stable_job_id(raw["company"], raw["title"], raw["location"])
    existing = None
    if db.path.is_file() and db.path.stat().st_size:
        connection = sqlite3.connect(db.path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            has_jobs = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()
            if has_jobs:
                existing = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
                same_url = (connection.execute(
                    "SELECT * FROM jobs WHERE canonical_url = ?", (canonicalize_url(source_url),)
                ).fetchone() if source_url else None)
                if same_url is not None and same_url["job_id"] != job_id:
                    raise ValueError(
                        f"This URL already belongs to {same_url['job_id']} ({same_url['company']} / "
                        f"{same_url['title']} / {same_url['location']}). "
                        "Review the existing saved job before importing. No records were merged or changed. "
                        "Use its existing identity to update it."
                    )
        finally:
            connection.close()
    values = json.loads(existing["payload_json"]) if existing is not None else {}
    values.update(raw)
    # Pasted vacancies need no URL. An omitted/blank URL is not a request to
    # erase a known link when updating the same saved job.
    values["source_url"] = source_url or (existing["source_url"] if existing is not None else "")
    values["description_excerpt"] = description.strip()
    chosen_source = source if source is not None else raw.get("source", "unknown")
    if not isinstance(chosen_source, str) or not chosen_source.strip():
        raise ValueError("source must be a non-empty label, such as LinkedIn, referral or company website.")

    job = SearchJob(
        company=values["company"].strip(),
        title=values["title"].strip(),
        location=values["location"].strip(),
        country=_optional_text(values.get("country")),
        work_mode=values.get("work_mode", "unknown"),
        employment_type=_optional_text(values.get("employment_type")),
        posted_date=_optional_text(values.get("posted_date")),
        deadline=_optional_text(values.get("deadline")),
        source_url=values["source_url"].strip(),
        apply_url=_optional_text(values.get("apply_url")),
        description_excerpt=values["description_excerpt"],
        salary_text=_optional_text(values.get("salary_text")),
        language_requirements=_text_list(values.get("language_requirements")),
        required_years_experience=_optional_text(values.get("required_years_experience")),
        domain=str(values.get("domain") or "Unclassified"),
        role_family=str(values.get("role_family") or "Unclassified"),
        why_fit=_text_list(values.get("why_fit")),
        hard_gaps=_text_list(values.get("hard_gaps")),
        functional_fit=_score(values.get("functional_fit")),
        hiring_realism=_score(values.get("hiring_realism")),
        goal_value=_score(values.get("goal_value")),
        evidence=_text_list(values.get("evidence")),
    )
    if dry_run:
        digest = payload_hash(json.dumps(job.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
        action = "new" if existing is None else "unchanged" if existing["payload_hash"] == digest else "changed"
        return job_id, action
    ensure_tracking_schema(db)
    job_id, action = db.upsert_job(job, job_id=job_id)
    if get_application(db, job_id) is None:
        update_application(db, job_id, {"status": "interested", "source": chosen_source.strip()}, on_date=on_date)
    return job_id, action


def add_job_to_selection(path: Path, job_id: str) -> None:
    selection = (load_yaml(path) or {}) if path.exists() else {}
    if not isinstance(selection, dict):
        raise ValueError("Selection file must contain a YAML mapping.")
    for name in ("job_ids", "urls"):
        if not isinstance(selection.get(name, []), list):
            raise ValueError(f"Selection {name} must be a YAML list.")
    job_ids = [str(item) for item in selection.get("job_ids", [])]
    if job_id not in job_ids:
        job_ids.append(job_id)
    selection["job_ids"] = job_ids
    selection.setdefault("urls", [])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(selection, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _text_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("List fields in the manual job file must use YAML lists.")
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


def _score(value: object) -> int:
    if value in (None, ""):
        return 50
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValueError("Scores must be whole numbers between 0 and 100.")
    return int(value)

