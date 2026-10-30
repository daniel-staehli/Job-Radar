from pathlib import Path
import subprocess
import sys

import yaml
import pytest

from job_radar.db import JobDatabase
from job_radar.manual_import import add_job_to_selection, import_manual_job, import_manual_data
from job_radar.tracking import get_application, list_events, update_application


def write_job(path: Path) -> None:
    path.write_text(
        """
company: Example Bio
title: Application Scientist
location: Lausanne, Switzerland
country: Switzerland
work_mode: hybrid
source_url: https://example.com/jobs/123
language_requirements:
  - English
domain: Scientific imaging
role_family: Field application scientist
functional_fit: 80
hiring_realism: 70
goal_value: 75
description: |
  Support imaging customers and explain scientific workflows.
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_import_manual_job_and_select(tmp_path):
    job_path = tmp_path / "manual.yaml"
    write_job(job_path)
    db = JobDatabase(tmp_path / "jobs.sqlite3")

    job_id, action = import_manual_job(db, job_path)
    assert action == "new"
    row = db.get_job(job_id)
    assert row is not None
    assert "Support imaging customers" in row["description_excerpt"]

    selection_path = tmp_path / "selected.yaml"
    add_job_to_selection(selection_path, job_id)
    add_job_to_selection(selection_path, job_id)
    selection = yaml.safe_load(selection_path.read_text(encoding="utf-8"))
    assert selection == {"job_ids": [job_id], "urls": []}


def test_manual_preview_does_not_checkpoint_committed_wal(tmp_path):
    job_path = tmp_path / "manual.yaml"
    write_job(job_path)
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    import_manual_job(db, job_path)
    # Simulate an interrupted writer after a successful commit. A write-capable
    # lookup connection would checkpoint and remove the WAL when it closes.
    subprocess.run([
        sys.executable, "-c", """
import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=WAL")
connection.execute("PRAGMA wal_autocheckpoint=0")
connection.execute("UPDATE jobs SET verification_note = 'Committed before interruption'")
connection.commit()
os._exit(0)
""", str(db.path),
    ], check=True)
    wal_path = Path(str(db.path) + "-wal")
    assert wal_path.exists()
    before_database = db.path.read_bytes()
    before_wal = wal_path.read_bytes()
    before_files = set(tmp_path.iterdir())

    _, action = import_manual_job(db, job_path, dry_run=True)

    assert action == "unchanged"
    assert db.path.read_bytes() == before_database
    assert wal_path.read_bytes() == before_wal
    assert set(tmp_path.iterdir()) == before_files


def test_reimport_preserves_optional_fields_and_application_history(tmp_path):
    job_path = tmp_path / "job.yaml"
    write_job(job_path)
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    job_id, _ = import_manual_job(db, job_path, source="referral")
    update_application(db, job_id, {"status": "applied", "cv_version": "v1.pdf"})
    events = list_events(db)
    raw = yaml.safe_load(job_path.read_text(encoding="utf-8"))
    minimal = {key: raw[key] for key in ("company", "title", "location", "source_url", "description")}
    assert import_manual_data(db, minimal) == (job_id, "unchanged")
    assert db.get_job(job_id)["functional_fit"] == 80
    assert db.get_job(job_id)["role_family"] == raw["role_family"]
    assert get_application(db, job_id)["status"] == "applied"
    assert get_application(db, job_id)["source"] == "referral"
    assert list_events(db) == events
    assert import_manual_data(db, {**minimal, "country": None, "description": "Changed text"}) == (job_id, "changed")
    assert db.get_job(job_id)["country"] is None
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM job_versions").fetchone()[0] == 2


def test_manual_validation_and_url_collision_do_not_write(tmp_path):
    path = tmp_path / "job.yaml"
    write_job(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    for changes in ({"company": None}, {"source_url": "not a URL"}, {"source": ""}, {"role_famly": "typo"}):
        with pytest.raises(ValueError):
            import_manual_data(db, {**raw, **changes})
        assert not db.path.exists()
    import_manual_data(db, raw)
    before = db.path.read_bytes()
    with pytest.raises(ValueError, match="already belongs"):
        import_manual_data(db, {**raw, "title": "Different title"})
    assert db.path.read_bytes() == before


def test_existing_import_dry_run_preserves_every_file(tmp_path):
    path = tmp_path / "job.yaml"
    write_job(path)
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    import_manual_job(db, path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert import_manual_data(db, {**raw, "description": "New description"}, dry_run=True)[1] == "changed"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
