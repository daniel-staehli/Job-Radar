"""Exercise supporting sources through previews, generation and later revisions."""
import hashlib
import json
from pathlib import Path
import socket

import pytest
import yaml

from job_radar import cv, job_context, providers, revisions
from job_radar.gui_services import GuiService
from test_revisions import application, profile


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Supporting-source workflow tried a real network or AI call")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(cv, "fetch_job_page_text", denied)
    monkeypatch.setattr(cv, "codex_response", denied)
    monkeypatch.setattr(cv, "api_response", denied)
    monkeypatch.setattr(revisions, "codex_response", denied)
    monkeypatch.setattr(revisions, "api_response", denied)
    monkeypatch.setattr(cv, "resolve_response_fn", lambda settings: denied)
    monkeypatch.setattr(revisions, "resolve_response_fn", lambda settings: denied)
    service = GuiService(tmp_path)
    ids = []
    for number in (1, 2):
        identifier, _ = service.save_job({
            "company": "Synthetic Research Lab", "title": f"Research Coordinator {number}",
            "location": "Lausanne", "source_url": f"https://example.org/jobs/{number}",
            "description": "Coordinate research experiments and laboratory samples.",
        })
        ids.append(identifier)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "master_profile.yaml").write_text(yaml.safe_dump(profile()), encoding="utf-8")
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "cv_default.tex.j2").write_text("((( profile_text | tex )))", encoding="utf-8")
    (templates / "motivation_letter.tex.j2").write_text("((( letter.subject | tex )))", encoding="utf-8")
    settings = {**providers.default_settings("openai"), "model": "synthetic-model"}
    service.save_ai_settings(settings)
    return service, ids, settings


def test_preview_isolates_jobs_and_preserves_the_reviewed_snapshot(workflow):
    service, (first, second), _ = workflow
    job_context.save_source(service.root, first, {"title": "First role paper", "text": "FIRST-ROLE-CONTEXT", "notes": "Discuss its experiment design."})
    job_context.save_source(service.root, second, {"title": "Second role paper", "text": "SECOND-ROLE-CONTEXT"})
    job_context.save_source(service.root, first, {"title": "Excluded source", "text": "EXCLUDED-CONTEXT", "included": False})
    preview = service.preview(first, [])
    reviewed = service.review_text(preview)
    assert "FIRST-ROLE-CONTEXT" in reviewed
    assert "SECOND-ROLE-CONTEXT" not in reviewed
    assert "EXCLUDED-CONTEXT" not in reviewed
    assert "not evidence" in reviewed
    assert "JOB-SPECIFIC SUPPORTING SOURCES" in reviewed
    # Changing current stored notes must not rewrite an already reviewed request.
    item = job_context.list_sources(service.root, first)[0]
    job_context.save_source(service.root, first, {"title": "Changed source", "text": "LATER-UPDATE"}, source_id=item["id"])
    assert service.review_text(preview) == reviewed
    second_review = service.review_text(service.preview(second, []))
    assert "SECOND-ROLE-CONTEXT" in second_review
    assert "FIRST-ROLE-CONTEXT" not in second_review
    assert "LATER-UPDATE" not in second_review


def test_link_only_preview_and_excluded_sources_are_truthful(workflow):
    service, (first, second), _ = workflow
    job_context.save_source(service.root, first, {"title": "Unread employer article", "url": "https://example.org/company/article"})
    job_context.save_source(service.root, second, {"title": "Excluded article", "notes": "DO-NOT-SEND", "included": False})
    first_result = service.preview(first, [])
    review = service.review_text(first_result)
    assert "https://example.org/company/article" in review
    assert "link contents have not been read" in review
    audit = json.loads((first_result["outputs"][0] / "job_resources_used.json").read_text())
    assert audit["sources"][0]["source_text"] == ""
    second_result = service.preview(second, [])
    second_review = service.review_text(second_result)
    assert "DO-NOT-SEND" not in second_review
    assert "None included." in second_review
    assert not (second_result["outputs"][0] / "job_resources_used.json").exists()


def test_generation_and_revision_keep_original_sources_after_store_changes(workflow, monkeypatch):
    service, (first, second), settings = workflow
    source = job_context.save_source(service.root, first, {"title": "Original employer study", "url": "https://example.org/original-paper", "text": "ORIGINAL-STUDY-CONTEXT", "notes": "Relevant to this employer."})
    job_context.save_source(service.root, second, {"title": "Other job study", "text": "UNRELATED-JOB-CONTEXT"})
    frozen = job_context.snapshot(service.root, first)
    calls = []

    def adapter(chosen):
        assert chosen == settings
        def respond(**request):
            calls.append(request)
            result = application()
            if request["schema_name"] == "revised_application":
                result.profile = "Researcher with practical experience planning experiments and coordinating samples."
            return result, {"synthetic": True}
        return respond

    monkeypatch.setattr(cv, "resolve_response_fn", adapter)
    monkeypatch.setattr(revisions, "resolve_response_fn", adapter)
    generated = service.generate(first, [])
    assert not generated["validation_errors"]
    directory = generated["outputs"][0]
    for name in ("generation_inputs.json", "generation_context.json"):
        assert json.loads((directory / name).read_text())["supporting_context"] == frozen
    assert json.loads((directory / "job_resources_used.json").read_text()) == frozen
    assert "ORIGINAL-STUDY-CONTEXT" in calls[0]["user"]
    assert "UNRELATED-JOB-CONTEXT" not in calls[0]["user"]
    assert calls[0]["use_web_search"] is False
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}

    job_context.save_source(service.root, first, {"title": "Updated study", "text": "NEW-STUDY-CONTEXT"}, source_id=source["id"])
    revised = service.improve(directory, "cv", "Make the profile clearer.")
    assert not revised["validation_errors"]
    result_dir = revised["outputs"][0]
    assert result_dir == directory
    previous = Path(json.loads((result_dir / "revision.json").read_text(encoding="utf-8"))["source_dir"])
    assert previous.parent == service.root / "applications" / ".history"
    task = json.loads(calls[1]["user"].split("\n", 1)[1])
    assert task["supporting_sources_external_context_only"] == frozen
    assert "NEW-STUDY-CONTEXT" not in calls[1]["user"]
    assert calls[1]["use_web_search"] is False
    assert json.loads((result_dir / "generation_context.json").read_text())["supporting_context"] == frozen
    assert (result_dir / "job_resources_used.json").read_bytes() == (previous / "job_resources_used.json").read_bytes()
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in previous.iterdir() if path.is_file()} == hashes


def test_repeated_preview_removes_stale_source_audit_when_all_sources_excluded(workflow):
    service, (first, _), _settings = workflow
    saved = job_context.save_source(service.root, first, {"title": "Original selection", "text": "NOW-EXCLUDED-CONTENT"})
    destination = service.root / "applications" / "same-preview"
    initial = service.preview(first, [], output_root=destination)
    assert (initial["outputs"][0] / "job_resources_used.json").exists()
    job_context.save_source(service.root, first, {"title": "Original selection", "text": "NOW-EXCLUDED-CONTENT", "included": False}, source_id=saved["id"])
    updated = service.preview(first, [], output_root=destination)
    assert "NOW-EXCLUDED-CONTENT" not in service.review_text(updated)
    audit = updated["outputs"][0] / "job_resources_used.json"
    assert not audit.exists() or json.loads(audit.read_text()) == {}
