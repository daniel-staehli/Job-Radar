"""Explicit application tracking, separate from discovered job vacancies.

Legacy databases remain readable without a migration. A first write adds the
tracking schema and backs up the complete existing database before doing so.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .config import local_today
from .db import JobDatabase, utc_now


STATUSES = (
    "interested", "preparing", "applied", "interview", "rejected", "withdrawn", "offer"
)

_NULLABLE_TEXT_FIELDS = (
    "interview_stage", "cv_version", "cover_letter", "contact", "next_action",
    "outcome", "notes",
)
_DATE_FIELDS = ("application_date", "follow_up_date")
_EDITABLE_FIELDS = {
    "status", "source", "interview_dates", *_NULLABLE_TEXT_FIELDS, *_DATE_FIELDS
}
_APPLICATION_COLUMNS = (
    "job_id", "status", "interview_stage", "application_date", "cv_version",
    "cover_letter", "contact", "next_action", "follow_up_date",
    "interview_dates_json", "outcome", "notes", "source", "created_at", "updated_at",
)

_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS applications (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN
        ('interested', 'preparing', 'applied', 'interview', 'rejected', 'withdrawn', 'offer')),
    interview_stage TEXT,
    application_date TEXT,
    cv_version TEXT,
    cover_letter TEXT,
    contact TEXT,
    next_action TEXT,
    follow_up_date TEXT,
    interview_dates_json TEXT NOT NULL DEFAULT '[]',
    outcome TEXT,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES applications(job_id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'updated')),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    prior_status TEXT,
    new_status TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_events_job
    ON application_events(job_id, id);
CREATE TRIGGER IF NOT EXISTS application_events_no_update
BEFORE UPDATE ON application_events
BEGIN
    SELECT RAISE(ABORT, 'Application audit events are immutable');
END;
CREATE TRIGGER IF NOT EXISTS application_events_no_delete
BEFORE DELETE ON application_events
BEGIN
    SELECT RAISE(ABORT, 'Application audit events are immutable');
END;
COMMIT;
"""


@contextmanager
def _read_connection(db: JobDatabase) -> Iterator[sqlite3.Connection | None]:
    """Never create a file, journal, schema, or backup for a read/dry run."""
    if not db.path.exists() or db.path.stat().st_size == 0:
        yield None
        return
    uri = db.path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _has_table(connection: sqlite3.Connection | None, name: str) -> bool:
    return connection is not None and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def ensure_tracking_schema(db: JobDatabase) -> Path | None:
    """Add tracking tables without backfilling jobs; return a new backup path.

    SQLite's backup API captures committed records, including WAL contents.
    Existing backups are never overwritten. Repeated calls are no-ops.
    """
    backup_path = None
    with _read_connection(db) as connection:
        if _has_table(connection, "applications") and _has_table(connection, "application_events"):
            return None
        base_exists = _has_table(connection, "jobs")
        if connection is not None:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup_path = db.path.with_name(
                f"{db.path.name}.pre-tracking-{stamp}-{uuid4().hex[:8]}.bak"
            )
            destination = sqlite3.connect(backup_path)
            try:
                connection.backup(destination)
            finally:
                destination.close()
    if not base_exists:
        db.initialize()
    with db.connect() as connection:
        connection.executescript(_SCHEMA)
    return backup_path


def _application_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["interview_dates"] = json.loads(result.pop("interview_dates_json"))
    return result


def _get_application(connection: sqlite3.Connection | None, job_id: str) -> dict | None:
    if not _has_table(connection, "applications"):
        return None
    row = connection.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    return _application_dict(row) if row is not None else None


def get_application(db: JobDatabase, job_id: str) -> dict | None:
    with _read_connection(db) as connection:
        return _get_application(connection, job_id)


def list_applications(db: JobDatabase) -> list[dict]:
    """Include applications even after the related vacancy is marked closed."""
    with _read_connection(db) as connection:
        if not _has_table(connection, "applications"):
            return []
        rows = connection.execute(
            """SELECT a.*, j.company, j.title, j.role_family, j.location,
                      j.source_url, j.verification_status
               FROM applications AS a JOIN jobs AS j ON j.job_id = a.job_id
               ORDER BY a.updated_at DESC, a.job_id"""
        ).fetchall()
        return [_application_dict(row) for row in rows]


def list_events(db: JobDatabase, job_id: str | None = None) -> list[dict]:
    """Return immutable history in recording order, oldest first.

    occurred_at is the effective local date; recorded_at is the actual UTC
    recording timestamp, which may differ for an explicitly backdated event.
    """
    with _read_connection(db) as connection:
        if not _has_table(connection, "application_events"):
            return []
        where, args = (" WHERE job_id = ?", (job_id,)) if job_id is not None else ("", ())
        rows = connection.execute(
            "SELECT * FROM application_events" + where + " ORDER BY id", args
        ).fetchall()
        result = []
        for row in rows:
            event = dict(row)
            event["snapshot"] = json.loads(event.pop("snapshot_json"))
            result.append(event)
        return result


def _validated_date(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a valid ISO date (YYYY-MM-DD)") from error
    return value


def _validate_changes(changes: dict) -> dict:
    if not isinstance(changes, dict):
        raise ValueError("Application changes must be a dictionary")
    unknown = changes.keys() - _EDITABLE_FIELDS
    if unknown:
        raise ValueError("Unknown application fields: " + ", ".join(sorted(map(str, unknown))))
    validated = dict(changes)
    if "status" in validated and validated["status"] not in STATUSES:
        raise ValueError("status must be one of: " + ", ".join(STATUSES))
    if "source" in validated:
        if not isinstance(validated["source"], str) or not validated["source"].strip():
            raise ValueError("source must be a nonempty string")
    for field in _NULLABLE_TEXT_FIELDS:
        if field in validated and validated[field] is not None and not isinstance(validated[field], str):
            raise ValueError(f"{field} must be a string or None")
    for field in _DATE_FIELDS:
        if field in validated and validated[field] is not None:
            validated[field] = _validated_date(validated[field], field)
    if "interview_dates" in validated:
        dates = validated["interview_dates"]
        if not isinstance(dates, list):
            raise ValueError("interview_dates must be a list of ISO dates; use [] to clear")
        validated["interview_dates"] = [
            _validated_date(value, "interview_dates") for value in dates
        ]
    return validated


def _prepare_update(
    connection: sqlite3.Connection | None, job_id: str, changes: dict,
    effective_date: date, now: str,
) -> tuple[dict | None, dict]:
    if not _has_table(connection, "jobs") or connection.execute(
        "SELECT 1 FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone() is None:
        raise ValueError(f"Unknown job: {job_id}")
    previous = _get_application(connection, job_id)
    if previous is None:
        if "status" not in changes:
            raise ValueError("A status is required when first tracking an application")
        current = {field: None for field in (*_NULLABLE_TEXT_FIELDS, *_DATE_FIELDS)}
        current.update(
            job_id=job_id, interview_dates=[], source="unknown", created_at=now, updated_at=now
        )
    else:
        current = dict(previous)
    current.update(changes)
    if changes.get("status") == "applied" and current["application_date"] is None and "application_date" not in changes:
        previously_applied = previous is not None and previous["status"] == "applied"
        if _has_table(connection, "application_events"):
            previously_applied = previously_applied or connection.execute(
                "SELECT 1 FROM application_events WHERE job_id = ? AND new_status = 'applied' LIMIT 1",
                (job_id,),
            ).fetchone() is not None
        if not previously_applied:
            current["application_date"] = effective_date.isoformat()
    changed = previous is None or any(current[field] != previous[field] for field in _EDITABLE_FIELDS)
    if changed and _has_table(connection, "application_events"):
        latest = connection.execute(
            "SELECT MAX(occurred_at) FROM application_events WHERE job_id = ?", (job_id,)
        ).fetchone()[0]
        if latest is not None and effective_date.isoformat() < latest:
            raise ValueError(
                f"Event date must be on or after the latest recorded event date ({latest}). "
                "Use application_date for a past submission date, or enter history chronologically."
            )
    if changed:
        current["updated_at"] = now
    return previous, current


def update_application(
    db: JobDatabase, job_id: str, changes: dict, *,
    on_date: date | None = None, dry_run: bool = False,
) -> dict:
    """Apply explicit field changes, preserving all unspecified values/history.

    New applications require a status. First setting applied supplies today's
    local date, or on_date, only if application_date was not explicitly given.
    Passing None explicitly clears a nullable value, including an unknown date.
    No date is inferred from an interview, rejection, or vacancy search record.
    Effective event dates must be nondecreasing to keep snapshots consistent.
    """
    changes = _validate_changes(changes)
    if on_date is not None and (not isinstance(on_date, date) or isinstance(on_date, datetime)):
        raise ValueError("on_date must be a datetime.date")
    effective_date = on_date if on_date is not None else local_today()
    with _read_connection(db) as connection:
        _, proposed = _prepare_update(connection, job_id, changes, effective_date, utc_now())
    if dry_run:
        return proposed

    ensure_tracking_schema(db)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous, current = _prepare_update(connection, job_id, changes, effective_date, utc_now())
        if previous == current:
            return current
        stored = dict(current)
        stored["interview_dates_json"] = json.dumps(stored.pop("interview_dates"))
        columns = ", ".join(_APPLICATION_COLUMNS)
        placeholders = ", ".join("?" for _ in _APPLICATION_COLUMNS)
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in _APPLICATION_COLUMNS if column != "job_id"
        )
        connection.execute(
            f"INSERT INTO applications ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(job_id) DO UPDATE SET {updates}",
            tuple(stored[column] for column in _APPLICATION_COLUMNS),
        )
        connection.execute(
            """INSERT INTO application_events
               (job_id, event_type, occurred_at, recorded_at, prior_status, new_status, snapshot_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, "created" if previous is None else "updated", effective_date.isoformat(),
             current["updated_at"], previous["status"] if previous else None, current["status"],
             json.dumps(current, ensure_ascii=False, sort_keys=True)),
        )
        return current
