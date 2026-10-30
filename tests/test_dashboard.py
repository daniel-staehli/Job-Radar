import csv
import hashlib
import json
import sqlite3
from datetime import date

import pytest

from job_radar import dashboard
from job_radar.db import JobDatabase


DAY = date(2026, 9, 24)


def vacancy_db(tmp_path, job_ids):
    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO jobs VALUES (?)", [(value,) for value in job_ids])
    return JobDatabase(path)


def application(job_id, status="applied", **updates):
    row = {
        "job_id": job_id, "company": "Example", "title": "Researcher",
        "role_family": "Research", "location": "Basel", "source_url": "https://example.test/job",
        "verification_status": "unverified", "status": status,
        "application_date": "2026-09-01", "cv_version": None, "cover_letter": None,
        "source": "unknown", "contact": None, "next_action": None, "follow_up_date": None,
        "interview_stage": None, "interview_dates": [], "outcome": None, "notes": None,
        "created_at": "2026-09-01T12:00:00+00:00", "updated_at": "2026-09-24T12:00:00+00:00",
    }
    row.update(updates)
    return row


def event(event_id, on, row, status=None, **updates):
    snapshot = dict(row)
    snapshot.update(updates)
    if status:
        snapshot["status"] = status
    return {
        "id": event_id, "job_id": row["job_id"], "event_type": "updated",
        "occurred_at": on, "recorded_at": "2026-09-24T12:00:00+00:00",
        "prior_status": None, "new_status": snapshot["status"], "snapshot": snapshot,
    }


def mocked_report(monkeypatch, tmp_path, rows, events, **kwargs):
    db = vacancy_db(tmp_path, [row["job_id"] for row in rows])
    monkeypatch.setattr(dashboard, "list_applications", lambda db: rows)
    monkeypatch.setattr(dashboard, "list_events", lambda db: events)
    return dashboard.build_dashboard(db, as_of=DAY, **kwargs)


def test_rejected_after_interview_keeps_conversion_and_original_attribution(monkeypatch, tmp_path):
    rejected = application("one", "rejected", cv_version="later-draft", source="later-source")
    offered = application("two", "offer", role_family=None)
    events = [
        event(1, "2026-09-01", rejected, "applied", cv_version="submitted-v1", source="referral"),
        event(2, "2026-09-10", rejected, "interview"),
        event(3, "2026-09-20", rejected, "rejected"),
        event(4, "2026-09-01", offered, "applied"),
        event(5, "2026-09-21", offered, "offer"),
    ]
    report = mocked_report(monkeypatch, tmp_path, [rejected, offered], events)
    summary = report["summary"]
    assert summary["submitted_applications"] == 2
    assert summary["applications_reaching_interview"] == 1
    assert summary["applications_reaching_offer"] == 1
    assert summary["interview_rate"] == summary["offer_rate"] == 0.5
    assert report["status_counts"]["rejected"] == 1
    assert report["applications"][0]["metric_cv_version"] == "submitted-v1"
    assert report["applications"][0]["metric_source"] == "referral"
    assert {group["group"] for group in report["groups"]["cv_version"]} == {"submitted-v1", "unknown"}
    assert {group["group"] for group in report["groups"]["role_family"]} == {"Research", "unknown"}


def test_future_interview_and_future_status_do_not_count_as_conversion(monkeypatch, tmp_path):
    row = application("one", "interview", interview_dates=["2026-10-02"])
    events = [event(1, "2026-09-01", row, "applied"),
              event(2, "2026-10-02", row, "interview")]
    report = mocked_report(monkeypatch, tmp_path, [row], events)
    assert report["applications"][0]["status"] == "applied"
    assert report["summary"]["applications_reaching_interview"] == 0
    assert report["summary"]["interview_rate"] == 0.0
    assert report["upcoming_interviews"][0]["interview_date"] == "2026-10-02"
    assert report["summary"]["stale_applications"] == 1


def test_missing_submitted_metadata_can_be_completed_later(monkeypatch, tmp_path):
    row = application("one", cv_version="actual-v1.pdf", source="referral")
    events = [event(1, "2026-09-01", row, cv_version=None, source="unknown"),
              event(2, "2026-09-20", row)]
    report = mocked_report(monkeypatch, tmp_path, [row], events)
    assert report["applications"][0]["metric_cv_version"] == "actual-v1.pdf"
    assert report["applications"][0]["metric_source"] == "referral"
    assert report["applications"][0]["metric_attribution"] == "original_applied_event_with_completed_metadata"
    past = dashboard.build_dashboard(JobDatabase(tmp_path / "jobs.sqlite3"), as_of=date(2026, 9, 10))
    assert past["applications"][0]["metric_cv_version"] == "unknown"
    assert past["applications"][0]["metric_source"] == "unknown"


def test_due_stale_closed_and_recent_activity_rules(monkeypatch, tmp_path):
    closed = application("closed", verification_status="closed", follow_up_date="2026-09-20")
    rejected = application("rejected", "rejected", follow_up_date="2026-09-20", interview_dates=["2026-10-02"])
    offer = application("offer", "offer", follow_up_date="2026-09-24", next_action="Respond to offer")
    recent = application("recent", follow_up_date="2026-09-25")
    future = application("future", application_date="2026-10-01")
    rows = [closed, rejected, offer, recent, future]
    events = [event(index, "2026-09-01", row) for index, row in enumerate(rows, 1)]
    events.append(event(10, "2026-09-20", recent, notes="Contacted recruiter"))
    report = mocked_report(monkeypatch, tmp_path, rows, events)
    assert {row["job_id"] for row in report["due_followups"]} == {"closed", "offer"}
    assert [row["job_id"] for row in report["stale_applications"]] == ["closed"]
    assert {row["job_id"] for row in report["active_applications"]} == {"closed", "offer", "recent", "future"}
    assert report["summary"]["submitted_applications"] == 4
    assert report["summary"]["upcoming_interviews"] == 0


def test_future_created_tracking_is_not_projected_into_past(monkeypatch, tmp_path):
    row = application("future", application_date="2026-10-01", created_at="2026-10-01T12:00:00Z")
    report = mocked_report(monkeypatch, tmp_path, [row], [event(1, "2026-10-01", row)])
    assert report["summary"]["tracked_applications"] == 0
    assert report["summary"]["tracking_records_not_yet_effective"] == 1
    assert report["summary"]["submitted_applications"] == 0
    assert report["summary"]["interview_rate"] is None


def test_legacy_only_database_is_read_only_and_never_infers_applications(tmp_path):
    db = vacancy_db(tmp_path, ["one", "two", "three"])
    before = hashlib.sha256(db.path.read_bytes()).hexdigest()
    report = dashboard.build_dashboard(db, as_of=DAY)
    assert report["summary"]["total_vacancies"] == 3
    assert report["summary"]["untracked_vacancies"] == 3
    assert report["summary"]["tracked_applications"] == 0
    assert report["summary"]["interview_rate"] is None
    assert report["applications"] == []
    assert hashlib.sha256(db.path.read_bytes()).hexdigest() == before
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("jobs",)]


def test_missing_database_is_not_created_and_uses_local_today(monkeypatch, tmp_path):
    db = JobDatabase(tmp_path / "missing.sqlite3")
    monkeypatch.setattr(dashboard, "local_today", lambda: DAY)
    assert dashboard.build_dashboard(db)["as_of"] == DAY.isoformat()
    assert not db.path.exists()
    with pytest.raises(ValueError, match="stale_days"):
        dashboard.build_dashboard(db, stale_days=0)


def test_exports_are_unique_and_csv_is_formula_safe(monkeypatch, tmp_path):
    row = application("one", company='=HYPERLINK("https://bad.test")', title="  +SUM(1,2)",
                      notes="\t=1+1", contact="@unexpected")
    report = mocked_report(monkeypatch, tmp_path, [row], [event(1, "2026-09-01", row)])
    first = dashboard.write_dashboard(report, tmp_path / "reports")
    second = dashboard.write_dashboard(report, tmp_path / "reports")
    assert set(first).isdisjoint(second)
    with first[1].open(encoding="utf-8-sig", newline="") as handle:
        saved = next(csv.DictReader(handle))
    assert saved["company"].startswith("'=")
    assert saved["title"].startswith("'  +")
    assert saved["notes"].startswith("'\t=")
    assert saved["contact"].startswith("'@")
    assert json.loads(first[2].read_text(encoding="utf-8"))["applications"][0]["company"] == row["company"]
    markdown = first[0].read_text(encoding="utf-8")
    assert "Each rate denominator" in markdown
    assert "do not show" in markdown
    assert markdown == dashboard.format_dashboard(report)
