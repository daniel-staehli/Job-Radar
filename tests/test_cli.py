import json
import socket
import sys
from datetime import date

from job_radar.cli import main
from job_radar.db import JobDatabase
from job_radar.normalize import stable_job_id
from job_radar.tracking import get_application, list_events


def add_args(db, *extra):
    return ["--db", str(db), "add-job", "--company", "Example Bio", "--title", "Scientist",
            "--location", "Lausanne", "--url", "https://example.org/jobs/1",
            "--description", "Support scientific customers.", *extra]


def test_local_workflow_and_reports_without_network(tmp_path, monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise AssertionError("Local workflow attempted network access")
    monkeypatch.setattr(socket.socket, "connect", denied)
    db_path = tmp_path / "jobs.sqlite3"
    prefix = ["--db", str(db_path)]
    assert main(add_args(db_path, "--source", "referral", "--role-family", "Science", "--on", "2026-09-01")) == 0
    job_id = stable_job_id("Example Bio", "Scientist", "Lausanne")
    assert main(prefix + ["track", job_id, "--status", "preparing", "--on", "2026-09-02"]) == 0
    assert main(prefix + ["track", job_id, "--status", "applied", "--on", "2026-09-03",
                          "--cv-version", "cv-v1.pdf", "--cover-letter", "letter-v1.pdf",
                          "--contact", "Hiring team", "--follow-up-date", "2026-09-10",
                          "--next-action", "Follow up"]) == 0
    assert main(prefix + ["track", job_id, "--interview-date", "2026-09-15", "--on", "2026-09-08"]) == 0
    assert main(prefix + ["track", job_id, "--interview-date", "2026-09-22", "--on", "2026-09-09"]) == 0
    db = JobDatabase(db_path)
    current = get_application(db, job_id)
    assert current["application_date"] == "2026-09-03"
    assert current["interview_dates"] == ["2026-09-15", "2026-09-22"]
    assert main(prefix + ["dashboard", "--as-of", "2026-09-12", "--output-dir", str(tmp_path / "reports")]) == 0
    report = json.loads(next((tmp_path / "reports").glob("*.json")).read_text(encoding="utf-8"))
    assert report["summary"]["due_followups"] == 1
    assert report["summary"]["upcoming_interviews"] == 2
    assert report["summary"]["interview_rate"] == 0
    assert main(prefix + ["track", job_id, "--status", "interview", "--interview-stage", "Technical round",
                          "--on", "2026-09-15", "--clear", "follow-up-date"]) == 0
    assert main(prefix + ["track", job_id, "--status", "rejected", "--outcome", "Role filled", "--on", "2026-09-24"]) == 0
    assert main(prefix + ["history", job_id]) == 0
    assert "Role filled" in capsys.readouterr().out
    assert get_application(db, job_id)["cv_version"] == "cv-v1.pdf"
    assert [event["new_status"] for event in list_events(db, job_id)][-2:] == ["interview", "rejected"]


def test_default_dashboard_and_add_dry_run_create_nothing(tmp_path, capsys):
    db_path = tmp_path / "absent.sqlite3"
    before = set(tmp_path.iterdir())
    assert main(["--db", str(db_path)]) == 0
    assert "Untracked vacancies: 0" in capsys.readouterr().out
    assert main(add_args(db_path, "--dry-run")) == 0
    assert set(tmp_path.iterdir()) == before


def test_track_dry_run_and_invalid_change_preserve_database(tmp_path):
    db_path = tmp_path / "jobs.sqlite3"
    assert main(add_args(db_path)) == 0
    job_id = stable_job_id("Example Bio", "Scientist", "Lausanne")
    before = db_path.read_bytes()
    prefix = ["--db", str(db_path), "track", job_id]
    assert main(prefix + ["--status", "applied", "--dry-run"]) == 0
    assert main(prefix + ["--status", "applied", "--follow-up-date", "2026-02-30"]) == 1
    assert main(prefix + ["--notes", "hello", "--clear", "notes"]) == 1
    assert db_path.read_bytes() == before


def test_import_dry_run_does_not_create_selection(tmp_path):
    import yaml
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump(dict(company="Example", title="Scientist", location="Basel",
                                      source_url="https://example.org/1", description="A role")))
    db_path, selected = tmp_path / "jobs.sqlite3", tmp_path / "selected.yaml"
    assert main(["--db", str(db_path), "import-job", "--file", str(path), "--select",
                 "--selection", str(selected), "--dry-run"]) == 0
    assert not db_path.exists() and not selected.exists()
