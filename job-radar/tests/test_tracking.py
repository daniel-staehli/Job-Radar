from datetime import date, datetime
import sqlite3

import pytest

from job_radar.db import JobDatabase
from job_radar.config import local_today
from job_radar.models import SearchJob
from job_radar.tracking import (
    STATUSES, ensure_tracking_schema, get_application, list_applications,
    list_events, update_application,
)


def add_job(db, title="Research Scientist"):
    job = SearchJob(
        company="Example Bio", title=title, location="Lausanne", country="Switzerland",
        work_mode="hybrid", employment_type=None, posted_date=None, deadline=None,
        source_url="https://example.com/jobs/1", apply_url=None,
        description_excerpt="Research position", salary_text=None,
        language_requirements=["English"], required_years_experience=None,
        domain="Biotech", role_family="Research", why_fit=[], hard_gaps=[],
        functional_fit=70, hiring_realism=70, goal_value=70, evidence=[],
    )
    return db.upsert_job(job)[0]


@pytest.fixture
def legacy(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    db.initialize()
    return db, add_job(db)


def test_migration_backs_up_and_preserves_all_legacy_records_without_backfill(legacy):
    db, job_id = legacy
    for number in range(21):
        add_job(db, f"Position {number}")
    with db.connect() as connection:
        jobs_before = [tuple(row) for row in connection.execute("SELECT * FROM jobs ORDER BY job_id")]
        versions_before = [tuple(row) for row in connection.execute("SELECT * FROM job_versions ORDER BY id")]

    backup = ensure_tracking_schema(db)
    assert backup is not None and backup.exists()
    with sqlite3.connect(backup) as saved:
        assert saved.execute("SELECT * FROM jobs ORDER BY job_id").fetchall() == jobs_before
        assert saved.execute("SELECT * FROM job_versions ORDER BY id").fetchall() == versions_before
        assert saved.execute("SELECT name FROM sqlite_master WHERE name = 'applications'").fetchone() is None
    with db.connect() as connection:
        assert [tuple(row) for row in connection.execute("SELECT * FROM jobs ORDER BY job_id")] == jobs_before
        assert [tuple(row) for row in connection.execute("SELECT * FROM job_versions ORDER BY id")] == versions_before
    assert len(jobs_before) == 22
    assert get_application(db, job_id) is None
    assert list_applications(db) == []
    assert list_events(db) == []
    assert ensure_tracking_schema(db) is None
    assert len(list(db.path.parent.glob("*.pre-tracking-*.bak"))) == 1


def test_history_and_documents_survive_rejection_and_reopening(legacy):
    db, job_id = legacy
    applied = update_application(db, job_id, {
        "status": "applied", "cv_version": "CV_v2.pdf", "cover_letter": "Letter_v2.pdf",
        "contact": "Recruiting team", "notes": "Submitted via employer portal",
        "source": "employer portal",
    }, on_date=date(2026, 8, 10))
    assert applied["application_date"] == "2026-08-10"
    interviewed = update_application(db, job_id, {
        "status": "interview", "interview_stage": "First interview",
        "interview_dates": ["2026-08-20"], "follow_up_date": "2026-08-27",
    }, on_date=date(2026, 8, 20))
    rejected = update_application(db, job_id, {
        "status": "rejected", "outcome": "Selected another candidate", "follow_up_date": None,
    }, on_date=date(2026, 8, 25))
    reopened = get_application(JobDatabase(db.path), job_id)
    assert reopened == rejected
    for field in ("application_date", "cv_version", "cover_letter", "contact", "notes", "source", "created_at"):
        assert rejected[field] == applied[field]
    assert rejected["interview_dates"] == ["2026-08-20"]
    assert rejected["interview_stage"] == "First interview"
    assert rejected["follow_up_date"] is None
    events = list_events(db, job_id)
    assert [event["prior_status"] for event in events] == [None, "applied", "interview"]
    assert [event["new_status"] for event in events] == ["applied", "interview", "rejected"]
    assert [event["occurred_at"] for event in events] == ["2026-08-10", "2026-08-20", "2026-08-25"]
    assert events[0]["snapshot"] == applied
    assert events[1]["snapshot"] == interviewed
    assert events[2]["snapshot"] == rejected
    assert all(datetime.fromisoformat(event["recorded_at"]).utcoffset() is not None for event in events)


@pytest.mark.parametrize("status", [value for value in STATUSES if value != "applied"])
def test_other_statuses_never_invent_application_dates(legacy, status):
    db, job_id = legacy
    result = update_application(db, job_id, {"status": status}, on_date=date(2026, 8, 1))
    assert result["application_date"] is None
    assert result["source"] == "unknown"


def test_first_applied_uses_local_today_and_explicit_dates_override_it(legacy):
    db, job_id = legacy
    update_application(db, job_id, {"status": "preparing"}, on_date=date(2020, 1, 1))
    applied = update_application(db, job_id, {"status": "applied"})
    assert applied["application_date"] == local_today().isoformat()
    other = add_job(db, "Other position")
    result = update_application(db, other, {
        "status": "applied", "application_date": "2026-06-05",
    }, on_date=date(2026, 8, 1))
    assert result["application_date"] == "2026-06-05"


def test_explicit_unknown_date_stays_unknown_when_returning_to_applied(legacy):
    db, job_id = legacy
    first = update_application(db, job_id, {"status": "applied", "application_date": None})
    assert first["application_date"] is None
    update_application(db, job_id, {"status": "interview"})
    result = update_application(db, job_id, {"status": "applied"})
    assert result["application_date"] is None


def test_default_date_uses_configured_local_today(legacy, monkeypatch):
    db, job_id = legacy
    monkeypatch.setattr("job_radar.tracking.local_today", lambda: date(2026, 9, 24))
    result = update_application(db, job_id, {"status": "applied"})
    assert result["application_date"] == "2026-09-24"
    assert list_events(db, job_id)[0]["occurred_at"] == "2026-09-24"


def test_partial_edits_and_clearing_preserve_other_fields_and_noop_creates_no_event(legacy):
    db, job_id = legacy
    initial = update_application(db, job_id, {
        "status": "interested", "notes": "Save this", "contact": "Team", "next_action": "Write letter",
    })
    changed = update_application(db, job_id, {"contact": None})
    assert changed["contact"] is None
    assert changed["notes"] == "Save this"
    assert changed["next_action"] == "Write letter"
    assert changed["created_at"] == initial["created_at"]
    assert update_application(db, job_id, {}) == changed
    assert len(list_events(db, job_id)) == 2


def test_closed_vacancy_keeps_application_and_audit_history(legacy):
    db, job_id = legacy
    update_application(db, job_id, {"status": "applied"})
    db.update_verification(job_id, "closed", "Employer removed posting")
    row, = list_applications(db)
    assert row["verification_status"] == "closed"
    assert row["status"] == "applied"
    assert row["company"] == "Example Bio"
    assert row["title"] == "Research Scientist"
    assert row["role_family"] == "Research"
    assert row["location"] == "Lausanne"
    assert row["source_url"] == "https://example.com/jobs/1"
    assert len(list_events(db, job_id)) == 1


def test_reads_and_dry_run_leave_legacy_database_and_directory_unchanged(legacy):
    db, job_id = legacy
    before_bytes = db.path.read_bytes()
    before_mtime = db.path.stat().st_mtime_ns
    before_files = list(db.path.parent.iterdir())
    assert get_application(db, job_id) is None
    assert list_applications(db) == []
    assert list_events(db) == []
    proposed = update_application(db, job_id, {"status": "applied"}, on_date=date(2026, 8, 1), dry_run=True)
    assert proposed["application_date"] == "2026-08-01"
    assert db.path.read_bytes() == before_bytes
    assert db.path.stat().st_mtime_ns == before_mtime
    assert list(db.path.parent.iterdir()) == before_files


def test_dry_run_of_existing_application_does_not_write(legacy):
    db, job_id = legacy
    original = update_application(db, job_id, {"status": "applied"})
    before = db.path.read_bytes()
    proposal = update_application(db, job_id, {"status": "rejected"}, dry_run=True)
    assert proposal["status"] == "rejected"
    assert db.path.read_bytes() == before
    assert get_application(db, job_id) == original
    assert len(list_events(db, job_id)) == 1


def test_read_missing_db_and_unknown_job_do_not_create_files(tmp_path):
    db = JobDatabase(tmp_path / "missing.sqlite3")
    assert get_application(db, "missing") is None
    assert list_applications(db) == []
    assert list_events(db) == []
    with pytest.raises(ValueError, match="Unknown job"):
        update_application(db, "missing", {"status": "applied"}, dry_run=True)
    assert not db.path.exists()


def test_unknown_job_and_missing_status_do_not_migrate(legacy):
    db, job_id = legacy
    before = db.path.read_bytes()
    with pytest.raises(ValueError, match="Unknown job"):
        update_application(db, "missing", {"status": "applied"})
    with pytest.raises(ValueError, match="status is required"):
        update_application(db, job_id, {"notes": "Some notes"})
    assert db.path.read_bytes() == before
    assert not list(db.path.parent.glob("*.bak"))


@pytest.mark.parametrize("changes", [
    {"status": "unknown"}, {"status": None}, {"application_date": "2026-02-30"},
    {"application_date": "20260801"}, {"follow_up_date": "2026-9-01"},
    {"interview_dates": ["2026-08-35"]}, {"interview_dates": "2026-08-01"},
    {"application_date": date(2026, 8, 1)}, {"source": None}, {"source": " "},
    {"contact": 42}, {"made_up_field": "test"}, {"interview_dates": None},
])
def test_invalid_changes_do_not_write_or_migrate(legacy, changes):
    db, job_id = legacy
    before = db.path.read_bytes()
    with pytest.raises(ValueError):
        update_application(db, job_id, {"status": "interested", **changes})
    assert db.path.read_bytes() == before
    assert not list(db.path.parent.glob("*.bak"))


@pytest.mark.parametrize("bad_date", ["2026-08-01", datetime(2026, 8, 1), 42])
def test_invalid_on_date(legacy, bad_date):
    db, job_id = legacy
    with pytest.raises(ValueError, match="on_date"):
        update_application(db, job_id, {"status": "applied"}, on_date=bad_date)


def test_events_cannot_be_changed_or_deleted_and_foreign_keys_are_enforced(legacy):
    db, job_id = legacy
    update_application(db, job_id, {"status": "applied"})
    for statement in (
        "UPDATE application_events SET new_status = 'rejected'",
        "DELETE FROM application_events",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with db.connect() as connection:
                connection.execute(statement)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with db.connect() as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    assert get_application(db, job_id)["status"] == "applied"
    assert len(list_events(db, job_id)) == 1


def test_event_filtering_separates_applications(legacy):
    db, first = legacy
    second = add_job(db, "Second position")
    update_application(db, first, {"status": "interested"})
    update_application(db, second, {"status": "applied"})
    assert len(list_events(db)) == 2
    assert [event["job_id"] for event in list_events(db, first)] == [first]
    assert list_events(db, "missing") == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_backdated_state_change_is_rejected_without_diverging_from_history(legacy, dry_run):
    db, job_id = legacy
    original = update_application(db, job_id, {"status": "interested"}, on_date=date(2026, 9, 24))
    before = db.path.read_bytes()
    with pytest.raises(ValueError, match="application_date.*chronologically"):
        update_application(db, job_id, {"status": "applied"}, on_date=date(2026, 9, 1), dry_run=dry_run)
    assert db.path.read_bytes() == before
    assert get_application(db, job_id) == original
    assert len(list_events(db, job_id)) == 1
    updated = update_application(db, job_id, {
        "status": "applied", "application_date": "2026-09-01",
    }, on_date=date(2026, 9, 24))
    assert updated["application_date"] == "2026-09-01"
    assert list_events(db, job_id)[-1]["snapshot"] == updated
    assert list_events(db, job_id)[-1]["occurred_at"] == "2026-09-24"
