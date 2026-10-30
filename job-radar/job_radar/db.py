from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .models import SearchJob
from .normalize import canonicalize_url, payload_hash, stable_job_id


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS search_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    strategy TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    notes_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    country TEXT,
    work_mode TEXT NOT NULL,
    employment_type TEXT,
    posted_date TEXT,
    deadline TEXT,
    source_url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    apply_url TEXT,
    description_excerpt TEXT NOT NULL,
    salary_text TEXT,
    domain TEXT NOT NULL,
    role_family TEXT NOT NULL,
    functional_fit INTEGER NOT NULL,
    hiring_realism INTEGER NOT NULL,
    goal_value INTEGER NOT NULL,
    overall_priority REAL NOT NULL,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    verification_note TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_verified_at TEXT,
    repost_count INTEGER NOT NULL DEFAULT 0,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    is_changed INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(overall_priority DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen ON jobs(last_seen_at DESC);

CREATE TABLE IF NOT EXISTS job_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class JobDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def start_run(self, mode: str, strategy: str) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO search_runs(mode, strategy, started_at, status)
                VALUES (?, ?, ?, 'running')
                """,
                (mode, strategy, utc_now()),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, notes: list[str], status: str = "completed") -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE search_runs
                SET completed_at = ?, status = ?, notes_json = ?
                WHERE id = ?
                """,
                (utc_now(), status, json.dumps(notes, ensure_ascii=False), run_id),
            )

    def upsert_job(self, job: SearchJob, *, job_id: str | None = None) -> tuple[str, str]:
        now = utc_now()
        job_id = job_id or stable_job_id(job.company, job.title, job.location)
        canonical_url = canonicalize_url(job.source_url)
        payload = job.model_dump(mode="json")
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = payload_hash(payload_json)
        overall = round(
            job.hiring_realism * 0.40
            + job.functional_fit * 0.35
            + job.goal_value * 0.25,
            1,
        )

        with self.connect() as connection:
            existing = connection.execute(
                "SELECT source_url, payload_hash FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing is None:
                action = "new"
                first_seen = now
                repost_count = 0
            else:
                first_seen_row = connection.execute(
                    "SELECT first_seen_at, repost_count FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                first_seen = first_seen_row["first_seen_at"]
                repost_count = int(first_seen_row["repost_count"])
                if canonicalize_url(existing["source_url"]) != canonical_url:
                    repost_count += 1
                action = "unchanged" if existing["payload_hash"] == digest else "changed"

            fields = (
                job_id,
                job.company,
                job.title,
                job.location,
                job.country,
                job.work_mode,
                job.employment_type,
                job.posted_date,
                job.deadline,
                job.source_url,
                canonical_url,
                job.apply_url,
                job.description_excerpt,
                job.salary_text,
                job.domain,
                job.role_family,
                job.functional_fit,
                job.hiring_realism,
                job.goal_value,
                overall,
                first_seen,
                now,
                repost_count,
                digest,
                payload_json,
                0 if action == "unchanged" else 1,
            )
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, company, title, location, country, work_mode,
                    employment_type, posted_date, deadline, source_url,
                    canonical_url, apply_url, description_excerpt, salary_text,
                    domain, role_family, functional_fit, hiring_realism,
                    goal_value, overall_priority, first_seen_at, last_seen_at,
                    repost_count, payload_hash, payload_json, is_changed
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(job_id) DO UPDATE SET
                    company = excluded.company,
                    title = excluded.title,
                    location = excluded.location,
                    country = excluded.country,
                    work_mode = excluded.work_mode,
                    employment_type = excluded.employment_type,
                    posted_date = excluded.posted_date,
                    deadline = excluded.deadline,
                    source_url = excluded.source_url,
                    canonical_url = excluded.canonical_url,
                    apply_url = excluded.apply_url,
                    description_excerpt = excluded.description_excerpt,
                    salary_text = excluded.salary_text,
                    domain = excluded.domain,
                    role_family = excluded.role_family,
                    functional_fit = excluded.functional_fit,
                    hiring_realism = excluded.hiring_realism,
                    goal_value = excluded.goal_value,
                    overall_priority = excluded.overall_priority,
                    last_seen_at = excluded.last_seen_at,
                    repost_count = excluded.repost_count,
                    payload_hash = excluded.payload_hash,
                    payload_json = excluded.payload_json,
                    is_changed = excluded.is_changed
                """,
                fields,
            )
            if action != "unchanged":
                connection.execute(
                    """
                    INSERT INTO job_versions(job_id, captured_at, payload_hash, payload_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (job_id, now, digest, payload_json),
                )
        return job_id, action

    def list_jobs(
        self, limit: int = 50, changed_only: bool = False
    ) -> list[sqlite3.Row]:
        where = (
            "WHERE is_changed = 1 AND verification_status != 'closed'"
            if changed_only
            else "WHERE verification_status != 'closed'"
        )
        with self.connect() as connection:
            return list(
                connection.execute(
                    f"""
                    SELECT * FROM jobs
                    {where}
                    ORDER BY overall_priority DESC, hiring_realism DESC, last_seen_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

    def find_job_by_url(self, url: str) -> sqlite3.Row | None:
        canonical = canonicalize_url(url)
        if not canonical:
            return None
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM jobs WHERE canonical_url = ?", (canonical,)
            ).fetchone()

    def update_verification(self, job_id: str, status: str, note: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET verification_status = ?, verification_note = ?,
                    last_verified_at = ?
                WHERE job_id = ?
                """,
                (status, note, utc_now(), job_id),
            )

    def mark_reported(self, job_ids: list[str]) -> None:
        if not job_ids:
            return
        placeholders = ",".join("?" for _ in job_ids)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE jobs SET is_changed = 0 WHERE job_id IN ({placeholders})",
                job_ids,
            )
