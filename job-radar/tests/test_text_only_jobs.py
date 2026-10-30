"""Pasted vacancies stay usable without inventing a source URL."""
import json

import pytest
import yaml

from job_radar import cv, job_match
from job_radar.db import JobDatabase
from job_radar.gui_services import GuiService
from job_radar.manual_import import import_manual_data
from job_radar.normalize import canonicalize_url
from job_radar.tracking import get_application, list_events, update_application


def vacancy(**changes):
    return {"company": "Example Labs", "title": "Research Coordinator", "location": "Example City",
            "description": "Required: coordinate experiments.", **changes}


def versions(db, job_id):
    with db.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM job_versions WHERE job_id = ?", (job_id,)).fetchone()[0]


@pytest.mark.parametrize("blank", ["", " ", "\t\n"])
def test_empty_url_has_no_identity_and_lookup_does_not_create_database(tmp_path, blank):
    db = JobDatabase(tmp_path / "missing.sqlite3")
    assert canonicalize_url(blank) == ""
    assert db.find_job_by_url(blank) is None
    assert not db.path.exists()


def test_distinct_pasted_jobs_coexist_and_blank_lookup_never_returns_either(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    first, action = import_manual_data(db, vacancy())
    second, other_action = import_manual_data(db, vacancy(title="Lab Manager", source_url=""))
    assert first != second and action == other_action == "new"
    assert len(db.list_jobs()) == 2
    assert db.find_job_by_url("") is None
    for job_id in (first, second):
        row = db.get_job(job_id)
        assert row["source_url"] == row["canonical_url"] == ""
        assert json.loads(row["payload_json"])["source_url"] == ""
        assert versions(db, job_id) == 1
        assert get_application(db, job_id)["status"] == "interested"


def test_pasted_job_reimport_versions_changes_and_preserves_tracking(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    job_id, _ = import_manual_data(db, vacancy(), source="pasted text")
    update_application(db, job_id, {"status": "applied", "notes": "Follow up next week"})
    events = list_events(db)
    assert import_manual_data(db, vacancy(source_url="")) == (job_id, "unchanged")
    assert versions(db, job_id) == 1
    assert import_manual_data(db, vacancy(description="Coordinate laboratory experiments.")) == (job_id, "changed")
    assert versions(db, job_id) == 2
    row = get_application(db, job_id)
    assert row["status"] == "applied" and row["notes"] == "Follow up next week"
    assert row["source"] == "pasted text"
    assert list_events(db) == events


@pytest.mark.parametrize("changes", [{}, {"source_url": ""}, {"source_url": "   "}])
def test_pasted_update_does_not_erase_known_job_link(tmp_path, changes):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    url = "https://example.org/careers/123?utm_source=notice"
    job_id, _ = import_manual_data(db, vacancy(source_url=url))
    assert import_manual_data(db, vacancy(**changes), dry_run=True) == (job_id, "unchanged")
    assert import_manual_data(db, vacancy(description="Updated pasted description", **changes)) == (job_id, "changed")
    row = db.get_job(job_id)
    assert row["source_url"] == url
    assert row["canonical_url"] == "https://example.org/careers/123"
    assert row["repost_count"] == 0
    assert json.loads(row["payload_json"])["source_url"] == url
    assert db.find_job_by_url(url)["job_id"] == job_id


@pytest.mark.parametrize("url", [None, 42, False, "not a URL", "file:///tmp/job", "https://", "https://bad host/job", "https://example.org:bad/job", "https://example.org/job\nInjected", "http://[invalid/job"])
def test_malformed_supplied_urls_are_rejected_before_any_write(tmp_path, url):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    with pytest.raises(ValueError, match="source_url"):
        import_manual_data(db, vacancy(source_url=url))
    assert not db.path.exists()


def test_url_collision_with_another_identity_still_refuses_merge(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    url = "https://example.org/careers/123"
    first, _ = import_manual_data(db, vacancy(source_url=url))
    second, _ = import_manual_data(db, vacancy(title="Lab Manager"))
    before = db.path.read_bytes()
    with pytest.raises(ValueError, match="already belongs"):
        import_manual_data(db, vacancy(title="Lab Manager", source_url=url + "?utm_source=test"))
    assert db.path.read_bytes() == before
    assert db.get_job(first)["source_url"] == url
    assert db.get_job(second)["source_url"] == ""


def test_text_only_dry_run_does_not_write(tmp_path):
    db = JobDatabase(tmp_path / "jobs.sqlite3")
    assert import_manual_data(db, vacancy(), dry_run=True)[1] == "new"
    assert not db.path.exists()
    job_id, _ = import_manual_data(db, vacancy())
    before = db.path.read_bytes()
    assert import_manual_data(db, vacancy(), dry_run=True) == (job_id, "unchanged")
    assert import_manual_data(db, vacancy(description="Changed description"), dry_run=True) == (job_id, "changed")
    assert db.path.read_bytes() == before


def profile_inputs(root):
    data = root / "data"
    data.mkdir(exist_ok=True)
    profile = {"candidate_summary": "Researcher coordinating experiments.", "contact": {},
               "education": [], "languages": [], "allowed_headline_tags": [], "experiences": [],
               "skills": [], "publications": [], "additional_experience": [], "never_claim": []}
    (data / "master_profile.yaml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    templates = root / "templates"
    templates.mkdir()
    for name in ("cv_default.tex.j2", "motivation_letter.tex.j2"):
        (templates / name).write_text("Synthetic template", encoding="utf-8")


@pytest.mark.parametrize('offline', [True, False])
def test_saved_pasted_job_previews_without_page_or_ai_requests(tmp_path, monkeypatch, offline):
    service = GuiService(tmp_path)
    profile_inputs(tmp_path)
    job_id, _ = service.save_job(vacancy())
    def denied(*args, **kwargs):
        raise AssertionError("A local preview must not fetch or call AI")
    monkeypatch.setattr(cv, "fetch_job_page_text", denied)
    monkeypatch.setattr(cv, "codex_response", denied)
    monkeypatch.setattr(cv, "api_response", denied)
    original_generate = cv.generate_cvs
    def generate_without_link(**kwargs):
        kwargs['offline'] = offline
        kwargs['page_fetch_fn'] = denied
        return original_generate(**kwargs)
    monkeypatch.setattr(cv, 'generate_cvs', generate_without_link)
    before = service.db_path.read_bytes()
    result = service.preview(job_id, [], provider_settings={"provider": "ollama", "model": "synthetic"})
    assert not result["validation_errors"]
    directory, = result["outputs"]
    prompt = json.loads((directory / "prompt.json").read_text(encoding="utf-8"))
    assert '"source_url": ""' in prompt["user"]
    assert vacancy()["description"] in prompt["user"]
    assert "no page request" in (directory / "job_description.md").read_text(encoding="utf-8")
    assert service.db_path.read_bytes() == before


def test_saved_pasted_job_can_receive_explicit_mocked_ai_match(tmp_path, monkeypatch):
    service = GuiService(tmp_path)
    profile_inputs(tmp_path)
    job_id, _ = service.save_job(vacancy())
    job, = service.jobs()
    calls = []
    def response(**kwargs):
        calls.append(kwargs)
        return {"category": "plausible", "summary": "Coordination is documented.",
                "strengths": [{"explanation": "Research coordination", "evidence_ids": ["candidate_summary"]}],
                "requirements": [{"requirement": "Coordinate experiments", "job_quote": vacancy()["description"],
                                  "importance": "required", "assessment": "supported", "explanation": "Documented in the profile.",
                                  "evidence_ids": ["candidate_summary"]}],
                "uncertainties": [], "next_actions": []}, {}
    monkeypatch.setattr(job_match, "resolve_response_fn", lambda settings: response)
    package = job_match.assess_job(tmp_path, job, {"provider": "ollama", "model": "synthetic"})
    assert package["assessment"]["category"] == "plausible"
    assert len(calls) == 1 and calls[0]["use_web_search"] is False
    assert json.loads(calls[0]["user"])["saved_vacancy"]["source_url"] == ""
    assert job_match.latest_assessment(tmp_path, job)["stale"] is False
    assert service.db.get_job(job_id)["source_url"] == ""
