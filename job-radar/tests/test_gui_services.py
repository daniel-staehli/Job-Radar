import hashlib
import json
import socket

import pytest
import yaml

from job_radar import cv, gui_services
from job_radar.gui_services import GuiService


@pytest.fixture(autouse=True)
def no_network_or_model(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("GUI service test attempted a network or model call")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(cv, "codex_response", denied)
    monkeypatch.setattr(cv, "api_response", denied)
    monkeypatch.setattr(cv, "fetch_job_page_text", denied)


def vacancy(**changes):
    return {
        "company": "Example Bio", "title": "Scientist", "location": "Lausanne",
        "source_url": "https://example.org/jobs/one",
        "description": "Coordinate protein laboratory experiments.",
        "source": "company website", **changes,
    }


def document_inputs(root):
    (root / "data").mkdir(exist_ok=True)
    (root / "templates").mkdir()
    profile = {
        "candidate_summary": "Example candidate", "contact": {}, "education": [],
        "languages": [], "allowed_headline_tags": [], "experiences": [], "skills": [],
        "publications": [], "additional_experience": [], "never_claim": [],
    }
    (root / "data" / "master_profile.yaml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    (root / "data" / "current_application.yaml").write_text(
        "job_ids: [never-select-this-default-job]\nreference_ids: [never-select-this-default-ref]\n",
        encoding="utf-8",
    )
    for name in ("cv_default.tex.j2", "motivation_letter.tex.j2"):
        (root / "templates" / name).write_text("Example template", encoding="utf-8")


def test_startup_reads_create_nothing(tmp_path):
    service = GuiService(tmp_path / "not-created")
    assert service.jobs() == []
    assert service.refs() == []
    assert service.history("absent") == []
    assert "Tracked: 0" in service.dashboard_text()
    assert not service.root.exists()


def test_review_save_tracking_and_reads_preserve_database(tmp_path, monkeypatch):
    service = GuiService(tmp_path)

    def fetch(url, *, output_dir):
        assert url == "https://example.org/jobs/one"
        output_dir.mkdir(parents=True)
        path = output_dir / "example.yaml"
        draft = vacancy(location="")
        path.write_text(yaml.safe_dump(draft), encoding="utf-8")
        return {"yaml_path": path, "job": draft, "complete": False,
                "missing_fields": ["location"], "warnings": ["Review this draft"]}

    monkeypatch.setattr(gui_services, "prepare_job_url", fetch)
    imported = service.import_url("https://example.org/jobs/one")
    assert not service.db_path.exists()
    draft = service.load_draft(imported["yaml_path"])
    with pytest.raises(ValueError, match="location"):
        service.save_job(draft)
    assert not service.db_path.exists()
    draft["location"] = "Lausanne"
    job_id, action = service.save_job(draft)
    assert action == "new"
    assert service.save_job(draft) == (job_id, "unchanged")
    service.update_tracking(job_id, {"status": "preparing", "notes": "Review qualifications",
                                     "follow_up_date": "2026-10-01", "next_action": "Finish CV"})
    before = service.db_path.read_bytes()
    files_before = set(tmp_path.rglob("*"))
    rows = service.jobs()
    assert len(rows) == 1
    assert rows[0]["description"] == draft["description"]
    assert rows[0]["source"] == "company website"
    assert rows[0]["status"] == "preparing"
    assert rows[0]["next_action"] == "Finish CV"
    assert [event["new_status"] for event in service.history(job_id)] == ["interested", "preparing"]
    assert "Tracked: 1" in service.dashboard_text()
    assert service.db_path.read_bytes() == before
    assert set(tmp_path.rglob("*")) == files_before


def test_closed_legacy_jobs_are_visible_without_migrating(tmp_path):
    service = GuiService(tmp_path, tmp_path / "custom.sqlite3")
    job_id, _ = service.save_job(vacancy())
    with service.db.connect() as connection:
        connection.execute("DROP TABLE application_events")
        connection.execute("DROP TABLE applications")
        connection.execute("UPDATE jobs SET verification_status='closed'")
    before = service.db_path.read_bytes()
    files_before = set(tmp_path.iterdir())
    row, = service.jobs()
    assert row["job_id"] == job_id
    assert row["status"] == "untracked"
    assert row["verification_status"] == "closed"
    assert service.db_path.read_bytes() == before
    assert set(tmp_path.iterdir()) == files_before
    with pytest.raises(ValueError, match="status is required"):
        service.update_tracking(job_id, {"notes": "No silent status change"})
    assert service.db_path.read_bytes() == before


@pytest.mark.parametrize("content, match", [
    ("- one\n- two\n", "mapping"),
    ("company: Example\ncommands: something\n", "Unknown job fields"),
])
def test_invalid_draft_does_not_create_database(tmp_path, content, match):
    path = tmp_path / "draft.yaml"
    path.write_text(content, encoding="utf-8")
    service = GuiService(tmp_path)
    with pytest.raises(ValueError, match=match):
        service.load_draft(path)
    assert not service.db_path.exists()


def test_reference_library_is_local_and_preserves_source(tmp_path):
    service = GuiService(tmp_path)
    source = tmp_path / "reference.txt"
    source.write_text("Example CV\nClear sections and specific achievements.", encoding="utf-8")
    original = source.read_bytes()
    reference = service.add_ref(source, label="Research reference")
    assert service.add_ref(source)["duplicate"]
    assert [item["id"] for item in service.refs()] == [reference["id"]]
    assert "text" not in service.refs()[0]
    service.remove_ref(reference["id"])
    assert service.refs() == []
    assert source.read_bytes() == original
    assert not service.db_path.exists()


def test_offline_preview_uses_one_job_and_only_selected_references(tmp_path):
    service = GuiService(tmp_path)
    document_inputs(tmp_path)
    job_id, _ = service.save_job(vacancy())
    service.save_job(vacancy(title="Unselected role", source_url="https://example.org/jobs/two"))
    source = tmp_path / "chosen.txt"
    source.write_text("SELECTED_STYLE_EXAMPLE", encoding="utf-8")
    reference = service.add_ref(source)
    other = tmp_path / "unselected.txt"
    other.write_text("UNSELECTED_PRIVATE_EXAMPLE", encoding="utf-8")
    service.add_ref(other)
    protected = [service.db_path, tmp_path / "data" / "master_profile.yaml",
                 tmp_path / "data" / "current_application.yaml"]
    before = {path: path.read_bytes() for path in protected}
    result = service.preview(job_id, [reference["id"]])
    assert len(result["outputs"]) == 1
    prompt = (result["outputs"][0] / "prompt.json").read_text(encoding="utf-8")
    assert "SELECTED_STYLE_EXAMPLE" in prompt
    assert "UNSELECTED_PRIVATE_EXAMPLE" not in prompt
    assert "Unselected role" not in prompt
    assert not (result["outputs"][0] / "cv.tex").exists()
    metadata = json.loads((result["outputs"][0] / "generation_preview.json").read_text(encoding="utf-8"))
    assert metadata["job_id"] == job_id
    assert metadata["references"][0]["id"] == reference["id"]
    assert result["validation_errors"] == result["compile_errors"] == []
    second = service.preview(job_id, [])
    assert second["output_root"] != result["output_root"]
    assert "SELECTED_STYLE_EXAMPLE" not in (second["outputs"][0] / "prompt.json").read_text(encoding="utf-8")
    assert all(path.read_bytes() == original for path, original in before.items())


def test_generation_routes_explicit_request_and_reports_failures(tmp_path, monkeypatch):
    service = GuiService(tmp_path)
    seen = []

    def fake_generation(**kwargs):
        seen.append(kwargs)
        directory = kwargs["output_root"] / "example-role"
        directory.mkdir(parents=True)
        (directory / "VALIDATION_FAILED.txt").write_text("Unsupported factual claim", encoding="utf-8")
        (directory / "LETTER_COMPILE_FAILED.txt").write_text("TeX not installed", encoding="utf-8")
        return [directory]

    monkeypatch.setattr(cv, "generate_cvs", fake_generation)
    result = service.generate("job_0123456789ab", [], output_root=tmp_path / "explicit-output", compile_pdf=True)
    request, = seen
    assert request["job_ids_override"] == ["job_0123456789ab"]
    assert request["reference_ids"] == []
    assert request["offline"] and not request["dry_run"] and not request["resume"]
    assert request["backend"] == "codex"
    assert request["compile_pdf"]
    assert request["profile_path"] == tmp_path / "data" / "master_profile.yaml"
    assert "Unsupported factual claim" in result["validation_errors"][0]
    assert "TeX not installed" in result["compile_errors"][0]


def test_invalid_selection_does_not_write_outputs(tmp_path):
    service = GuiService(tmp_path)
    with pytest.raises(ValueError, match="Select one saved vacancy"):
        service.preview("", [])
    with pytest.raises(ValueError, match="reference IDs"):
        service.generate("some-job", None)
    assert list(tmp_path.iterdir()) == []


def test_review_shows_only_saved_selected_information_without_current_inputs(tmp_path):
    service = GuiService(tmp_path)
    document_inputs(tmp_path)
    job_id, _ = service.save_job(vacancy())
    chosen = tmp_path / "chosen.txt"
    chosen.write_text("SELECTED_REFERENCE — café", encoding="utf-8")
    selected = service.add_ref(chosen, label="Research example")
    other = tmp_path / "other.txt"
    other.write_text("UNSELECTED_PRIVATE_REFERENCE", encoding="utf-8")
    service.add_ref(other)
    result = service.preview(job_id, [selected["id"]])
    snapshot = {path: path.read_bytes() for path in result["outputs"][0].iterdir()}
    # A review must not reload a changed profile or the current reference library.
    (tmp_path / "data" / "master_profile.yaml").write_text("NEW_PRIVATE_PROFILE", encoding="utf-8")
    service.remove_ref(selected["id"])

    report = service.review_text(result)

    assert "No CV or cover letter has been generated" in report
    assert "nothing has been sent" in report
    assert "Company: Example Bio" in report
    assert "Coordinate protein laboratory experiments." in report
    assert "Example candidate" in report
    assert "SELECTED_REFERENCE — café" in report
    assert "Research example" in report
    assert "UNSELECTED_PRIVATE_REFERENCE" not in report
    assert "NEW_PRIVATE_PROFILE" not in report
    assert all(path.read_bytes() == original for path, original in snapshot.items())


def test_review_without_references_and_section_markers_inside_vacancy(tmp_path):
    service = GuiService(tmp_path)
    document_inputs(tmp_path)
    description = "Real vacancy\n\nVERIFIED CANDIDATE FACTS\nUntrusted vacancy wording"
    job_id, _ = service.save_job(vacancy(description=description))
    report = service.review_text(service.preview(job_id, []))
    assert description in report
    assert "Candidate summary: Example candidate" in report
    assert "No reference documents selected." in report


def test_review_unknown_prompt_format_keeps_exact_saved_source(tmp_path):
    source = "An older saved request\n\nJOB\nArbitrary source material."
    (tmp_path / "prompt.json").write_text(
        json.dumps({"user": source, "system": "Original instructions"}), encoding="utf-8"
    )
    (tmp_path / "job_description.md").write_text("Original vacancy", encoding="utf-8")
    report = GuiService(tmp_path).review_text({"outputs": [tmp_path]})
    assert source in report
    assert "Original instructions" in report
    assert "Original vacancy" in report


def test_review_rejects_missing_output_and_invalid_snapshot(tmp_path):
    service = GuiService(tmp_path)
    with pytest.raises(ValueError, match="No saved information"):
        service.review_text({"outputs": []})
    (tmp_path / "prompt.json").write_text('{"user": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid format"):
        service.review_text({"outputs": [tmp_path]})


def test_codex_connection_check_is_explicit_and_returns_status(tmp_path, monkeypatch):
    from job_radar import codex_client

    calls = []
    status = {"installed": True, "authenticated": True, "detail": "Signed in"}

    def fake_status():
        calls.append("check")
        return status

    monkeypatch.setattr(codex_client, "codex_status", fake_status)
    service = GuiService(tmp_path / "not-created")
    assert calls == []
    assert service.check_codex() == status
    assert calls == ["check"]
    assert not service.root.exists()


def saved_application(root, folder="run/example-role", *, title="Scientist", revision=False):
    directory = root / "applications" / folder
    directory.mkdir(parents=True)
    profile = {
        "candidate_summary": "Original verified candidate", "contact": {"name": "Example Candidate"},
        "education": [], "languages": ["English"], "allowed_headline_tags": ["Research"],
        "experiences": [{"key": "original-role", "role": "Researcher", "location": "Lausanne",
                         "dates": "2020–2024", "subtitle": "Original laboratory",
                         "bullets": [{"id": "original-bullet", "text": "ORIGINAL_VERIFIED_ACHIEVEMENT"},
                                     {"id": "unused-bullet", "text": "UNSELECTED_ACHIEVEMENT"}]}],
        "skills": [], "publications": [], "additional_experience": [], "never_claim": [],
        "interests": ["Walking"],
    }
    job = vacancy(title=title)
    context = {"schema_version": 1, "job": job, "profile": profile,
               "full_description": job["description"], "references": [],
               "templates": {"cv": "Saved template", "cover_letter": "Saved letter template"}}
    tailoring = {
        "verdict": "apply", "match_summary": "Match", "headline_tags": ["Research"],
        "profile": "ORIGINAL_SAVED_PROFILE_TEXT",
        "selected_roles": [{"role_key": "original-role", "bullet_ids": ["original-bullet"]}],
        "selected_skill_ids": [], "selected_publication_ids": [], "selected_additional_ids": [],
        "priority_keywords": [], "honest_gaps": [], "interview_angles": [], "cv_risks_to_review": [],
        "motivation_letter": {"language": "English", "recipient_name": None, "recipient_role": None,
                              "salutation": "Dear team,", "subject": "Application for Scientist",
                              "paragraphs": ["Original opening.", "Original evidence.", "Original motivation."],
                              "closing": "Yours sincerely,"},
    }
    (directory / "generation_context.json").write_text(json.dumps(context), encoding="utf-8")
    (directory / "tailoring.json").write_text(json.dumps(tailoring), encoding="utf-8")
    (directory / "cv.tex").write_text("Original rendered CV", encoding="utf-8")
    (directory / "motivation_letter.tex").write_text("Original rendered letter", encoding="utf-8")
    if revision:
        (directory / "revision.json").write_text(json.dumps({"status": "validated"}), encoding="utf-8")
    return directory, context, tailoring


def test_cover_letter_reference_can_be_added_and_reclassified_locally(tmp_path):
    service = GuiService(tmp_path)
    source = tmp_path / "letter.txt"
    source.write_text("Dear team,\nMy research motivates this application.", encoding="utf-8")
    original = source.read_bytes()
    record = service.add_ref(source, label="Successful letter", document_type="cover_letter")
    assert service.refs()[0]["document_type"] == "cover_letter"
    changed = service.set_ref_type(record["id"], "cv")
    assert changed["id"] == record["id"]
    assert service.refs()[0]["document_type"] == "cv"
    assert source.read_bytes() == original


def test_drafts_list_only_completed_versions_and_can_filter_by_saved_job(tmp_path):
    service = GuiService(tmp_path)
    original, _, _ = saved_application(tmp_path)
    revision, _, _ = saved_application(tmp_path, "revision-one", revision=True)
    other, _, _ = saved_application(tmp_path, "run/other", title="Other role")
    failed, _, _ = saved_application(tmp_path, "run/failed")
    (failed / "VALIDATION_FAILED.txt").write_text("Unsupported claim", encoding="utf-8")
    incomplete, _, _ = saved_application(tmp_path, "run/incomplete")
    (incomplete / "cv.tex").unlink()
    invalid, _, _ = saved_application(tmp_path, "run/invalid")
    (invalid / "tailoring.json").write_text("{}", encoding="utf-8")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    drafts = service.list_drafts()

    assert {item["directory"] for item in drafts} == {original, revision, other}
    item = next(item for item in drafts if item["directory"] == original)
    assert item["company"] == "Example Bio" and item["title"] == "Scientist"
    assert item["job_id"] == gui_services.stable_job_id("Example Bio", "Scientist", "Lausanne")
    assert {item["directory"] for item in service.list_drafts(item["job_id"])} == {original, revision}
    assert next(item for item in drafts if item["directory"] == revision)["is_revision"]
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not service.db_path.exists()


def test_draft_text_uses_saved_profile_and_selected_facts_not_current_data(tmp_path):
    service = GuiService(tmp_path)
    directory, _, _ = saved_application(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "master_profile.yaml").write_text("NEW_PRIVATE_PROFILE", encoding="utf-8")
    before = {path: path.read_bytes() for path in directory.iterdir()}

    text = service.draft_text(directory, "cv")

    assert "ORIGINAL_SAVED_PROFILE_TEXT" in text
    assert "ORIGINAL_VERIFIED_ACHIEVEMENT" in text
    assert "UNSELECTED_ACHIEVEMENT" not in text
    assert "original-bullet" not in text
    assert "NEW_PRIVATE_PROFILE" not in text
    assert "Walking" in text
    assert "Fixed template text (such as Additional information)" in text
    letter = service.draft_text(directory, "cover_letter")
    assert "Original opening.\n\nOriginal evidence.\n\nOriginal motivation." in letter
    assert "Dear team," in letter and "Yours sincerely," in letter
    assert letter.endswith("Example Candidate")
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize("marker", [None, "REFERENCE CV EXAMPLES", "REFERENCE DOCUMENT EXAMPLES"])
def test_legacy_draft_uses_saved_request_for_identity_and_text(tmp_path, marker):
    service = GuiService(tmp_path)
    directory, context, _ = saved_application(tmp_path)
    profile = context["profile"]
    profile.pop("interests")
    reference_section = f"\n\n{marker} (STYLE ONLY, NOT FACTUAL EVIDENCE)\n[]" if marker else ""
    user = ("JOB\n" + json.dumps(context["job"]) + "\n\nFULL JOB-PAGE TEXT\nOriginal vacancy" +
            "\n\nVERIFIED CANDIDATE FACTS\n" + json.dumps(profile) +
            reference_section + "\n\nTASK\nWrite documents")
    (directory / "prompt.json").write_text(json.dumps({"user": user}), encoding="utf-8")
    (directory / "generation_context.json").unlink()

    assert service.list_drafts()[0]["title"] == "Scientist"
    text = service.draft_text(directory, "cv")
    assert "ORIGINAL_VERIFIED_ACHIEVEMENT" in text
    assert "older snapshot did not retain" in text


@pytest.mark.parametrize("boundary", [
    "\n\nARBITRARY CONTENT\nUnexpected text\n\nTASK\n",
    "\n\nSome unrelated text\n\nTASK\n",
    "\n\nTASK WITHOUT EXACT HEADER\n",
])
def test_pre_reference_parser_rejects_content_between_profile_and_task(boundary):
    source = ("JOB\n{}\n\nFULL JOB-PAGE TEXT\nOriginal vacancy" +
              "\n\nVERIFIED CANDIDATE FACTS\n{}" + boundary + "Write documents")
    with pytest.raises(ValueError, match="Unknown saved reference format"):
        gui_services._review_sections(source)


def test_pre_reference_parser_accepts_exact_task_boundary_with_no_reference_text():
    source = ("JOB\n{\"title\": \"Scientist\"}\n\nFULL JOB-PAGE TEXT\nOriginal vacancy" +
              "\n\nVERIFIED CANDIDATE FACTS\n{\"candidate_summary\": \"Original candidate\"}" +
              "\n\nTASK\nWrite documents")
    job, description, profile, references, task = gui_services._review_sections(source)
    assert job == {"title": "Scientist"}
    assert description == "Original vacancy"
    assert profile == {"candidate_summary": "Original candidate"}
    assert references == []
    assert task == "Write documents"


def test_draft_actions_reject_outside_folders_before_model_calls(tmp_path, monkeypatch):
    service = GuiService(tmp_path / "inside")
    outside, _, _ = saved_application(tmp_path / "outside")
    with pytest.raises(ValueError, match="inside Job Radar"):
        service.draft_text(outside, "cv")
    with pytest.raises(ValueError, match="inside Job Radar"):
        service.improve(outside, "cv", "Shorten the profile")
    assert not service.root.exists()


def test_linked_draft_folders_are_not_read(tmp_path):
    service = GuiService(tmp_path / "inside")
    outside, _, _ = saved_application(tmp_path / "outside")
    (service.root / "applications").mkdir(parents=True)
    link = service.root / "applications" / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a local directory symlink is unavailable")
    assert service.list_drafts() == []
    with pytest.raises(ValueError, match="inside Job Radar|Linked draft"):
        service.draft_text(link, "cover_letter")


def test_improve_delegates_feedback_and_updates_same_folder_with_backup(tmp_path, monkeypatch):
    import shutil
    from job_radar import revisions

    service = GuiService(tmp_path)
    source, _, _ = saved_application(tmp_path)
    before = {path: path.read_bytes() for path in source.iterdir()}
    seen = []

    def revise(**kwargs):
        seen.append(kwargs)
        output = kwargs["output_root"] / "revision-new"
        shutil.copytree(source, output)
        (output / "motivation_letter.tex").write_text("Revised rendered letter", encoding="utf-8")
        write_document_hashes(output)
        (output / "revision.json").write_text(json.dumps({
            "schema_version": 1, "source_dir": str(source), "status": "validated",
            "document": "cover_letter", "source_hashes": {p.name: hashlib.sha256(data).hexdigest() for p, data in before.items()},
        }), encoding="utf-8")
        (output / "LETTER_COMPILE_FAILED.txt").write_text("TeX is unavailable", encoding="utf-8")
        (output / "REVIEW_NOTES.txt").write_text("The saved photo was unavailable and was omitted.", encoding="utf-8")
        return output

    monkeypatch.setattr(revisions, "revise_application", revise)
    feedback = "Keep my opening.\nMake the second paragraph more concise — café."
    result = service.improve(source, "cover_letter", feedback, compile_pdf=True)
    assert seen == [{"source_dir": source, "document": "cover_letter", "feedback": feedback,
                     "output_root": tmp_path / "applications" / ".pending", "compile_pdf": True, "backend": "codex",
                     "provider_settings": service.ai_settings()}]
    assert result["outputs"] == [source]
    assert result["validation_errors"] == []
    assert "TeX is unavailable" in result["compile_errors"][0]
    assert "saved photo was unavailable" in result["review_notes"][0]
    from pathlib import Path
    archive = Path(json.loads((source / "revision.json").read_text(encoding="utf-8"))["source_dir"])
    assert all((archive / path.name).read_bytes() == data for path, data in before.items())
    assert (source / "motivation_letter.tex").read_text(encoding="utf-8") == "Revised rendered letter"
    with pytest.raises(ValueError, match="Describe"):
        service.improve(source, "cv", " \n")
    with pytest.raises(ValueError, match="Choose CV"):
        service.improve(source, "both", "Edit both")
    assert len(seen) == 1


def write_document_hashes(directory):
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
              for name in ("cv.tex", "motivation_letter.tex")}
    (directory / "document_hashes.json").write_text(json.dumps(hashes), encoding="utf-8")


def test_draft_text_refuses_stale_wording_after_selected_file_was_edited(tmp_path):
    service = GuiService(tmp_path)
    directory, _, _ = saved_application(tmp_path)
    write_document_hashes(directory)
    assert "ORIGINAL_SAVED_PROFILE_TEXT" in service.draft_text(directory, "cv")
    (directory / "cv.tex").write_text("My manual wording", encoding="utf-8")

    with pytest.raises(ValueError, match="edited after generation.*cannot show those edits"):
        service.draft_text(directory, "cv")

    assert "Original opening." in service.draft_text(directory, "cover_letter")
    assert (directory / "cv.tex").read_text(encoding="utf-8") == "My manual wording"


def test_compare_draft_shows_selected_wording_changes_and_unchanged_companion(tmp_path):
    service = GuiService(tmp_path)
    source, _, _ = saved_application(tmp_path)
    current, _, tailoring = saved_application(tmp_path, "revised", revision=True)
    tailoring["motivation_letter"]["paragraphs"][1] = "Shortened evidence."
    (current / "tailoring.json").write_text(json.dumps(tailoring), encoding="utf-8")
    (current / "revision.json").write_text(json.dumps({
        "schema_version": 1, "source_dir": str(source), "status": "validated", "document": "cover_letter",
        "source_hashes": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                          for name in ("tailoring.json", "cv.tex", "motivation_letter.tex")},
    }), encoding="utf-8")
    write_document_hashes(source)
    write_document_hashes(current)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    changes = service.compare_draft(current, "cover_letter")

    assert "-Original evidence." in changes
    assert "+Shortened evidence." in changes
    assert "Previous Cover letter" in changes and "Selected Cover letter" in changes
    assert "No wording changes" in service.compare_draft(current, "cv")
    assert all(path.read_bytes() == data for path, data in before.items())


def test_compare_draft_rejects_originals_outside_sources_and_changed_predecessors(tmp_path):
    service = GuiService(tmp_path / "inside")
    original, _, _ = saved_application(service.root)
    with pytest.raises(ValueError, match="original draft.*no previous version"):
        service.compare_draft(original, "cv")
    current, _, _ = saved_application(service.root, "revised")
    outside, _, _ = saved_application(tmp_path / "outside")
    audit = {"schema_version": 1, "source_dir": str(outside), "status": "validated"}
    (current / "revision.json").write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(ValueError, match="inside the applications folder"):
        service.compare_draft(current, "cv")
    audit["source_dir"] = str(original)
    audit["source_hashes"] = {"tailoring.json": hashlib.sha256((original / "tailoring.json").read_bytes()).hexdigest()}
    (current / "revision.json").write_text(json.dumps(audit), encoding="utf-8")
    (original / "tailoring.json").write_text(
        (original / "tailoring.json").read_text(encoding="utf-8").replace("Match", "Modified match"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="previous draft changed"):
        service.compare_draft(current, "cv")


def test_compare_rejects_linked_revision_record_before_following_it(tmp_path, monkeypatch):
    service = GuiService(tmp_path)
    source, _, _ = saved_application(tmp_path)
    current, _, _ = saved_application(tmp_path, "revised")
    marker = current / "revision.json"
    marker.write_text(json.dumps({"schema_version": 1, "source_dir": str(source), "status": "validated"}),
                      encoding="utf-8")
    real_linked = gui_services._linked
    monkeypatch.setattr(gui_services, "_linked", lambda path: path == marker or real_linked(path))
    with pytest.raises(ValueError, match="revision.json file is unavailable"):
        service.compare_draft(current, "cv")


def test_document_result_reports_asset_notes_separately_from_errors(tmp_path):
    (tmp_path / "REVIEW_NOTES.txt").write_text("External photo was not copied.\nCheck the rendered document.",
                                               encoding="utf-8")
    result = GuiService._document_result([tmp_path], tmp_path)
    assert result["review_notes"] == [f"{tmp_path.name}: External photo was not copied.\nCheck the rendered document."]
    assert result["validation_errors"] == result["compile_errors"] == []
