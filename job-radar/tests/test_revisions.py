"""Revision checks use synthetic candidate data and mocked model calls only."""

import hashlib
import json

import pytest

from job_radar.cv import build_cv_prompt
from job_radar.models import TailoredApplication
from job_radar.revisions import load_generation_context, revise_application


def profile():
    return {
        "candidate_summary": "Researcher with experience coordinating experiments.",
        "contact": {
            "name": "Test Candidate", "location": "Test City", "phone": "000000",
            "email": "example@example.org", "linkedin_url": "https://example.org/profile",
            "linkedin_label": "Profile", "photo_path": None,
        },
        "education": [], "languages": ["English"], "interests": [],
        "allowed_headline_tags": ["Research", "Coordination"],
        "experiences": [{
            "key": "research", "title": "Researcher", "organisation": "Test Institute",
            "location": "Test City", "dates": "2020-2024", "subtitle": "Laboratory",
            "bullets": [{"id": "research_plan", "text": "Planned experiments."},
                        {"id": "research_coordinate", "text": "Coordinated samples."}],
        }],
        "skills": [], "publications": [], "additional_experience": [],
        "never_claim": [{"pattern": "software engineer"}],
    }


def application():
    paragraph = (
        "I am applying because this role combines research and coordination. "
        "I have planned experiments and coordinated samples in a laboratory. "
        "Those responsibilities required careful preparation and clear communication. "
        "I would bring this experience to the team while learning its working methods. "
        "The opportunity to contribute to a new research programme interests me. "
        "I would welcome a discussion of the role and its priorities."
    )
    return TailoredApplication(
        verdict="apply", match_summary="Relevant laboratory experience.",
        headline_tags=["Research", "Coordination"],
        profile="Researcher experienced in planning experiments and coordinating samples.",
        selected_roles=[{"role_key": "research", "bullet_ids": ["research_plan", "research_coordinate"]}],
        selected_skill_ids=[], selected_publication_ids=[], selected_additional_ids=[],
        priority_keywords=["coordination"], honest_gaps=["New sector"],
        interview_angles=["Experiment planning"], cv_risks_to_review=[],
        motivation_letter={"language": "English", "recipient_name": None, "recipient_role": None,
                           "salutation": "Dear Hiring Team,", "subject": "Research application",
                           "paragraphs": [paragraph, paragraph, paragraph], "closing": "Yours sincerely,"},
    )


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / "original"
    directory.mkdir()
    saved = application()
    (directory / "tailoring.json").write_text(saved.model_dump_json(), encoding="utf-8")
    (directory / "cv.tex").write_bytes(b"Original CV\r\nPreserve this document when revising the letter.\r\n")
    (directory / "motivation_letter.tex").write_bytes(b"Original letter\r\nPreserve this document when revising the CV.\r\n")
    write_json(directory / "document_hashes.json", {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("cv.tex", "motivation_letter.tex")
    })
    write_json(directory / "generation_context.json", {
        "schema_version": 1,
        "job": {"company": "Example Laboratory", "title": "Research Coordinator",
                "source_url": "https://example.org/job", "description_excerpt": "Coordinate research."},
        "profile": profile(), "full_description": "Coordinate experiments and samples.",
        "references": [{"id": "ref-test", "label": "Selected reference", "type": "cv",
                        "text": "A short reference example.", "tags": [], "notes": None, "truncated": False}],
        "templates": {
            "cv": "CV SNAPSHOT TEMPLATE\n((( profile_text | tex )))\n((( headline | tex )))\n",
            "cover_letter": "LETTER SNAPSHOT TEMPLATE\n((( letter.subject | tex )))\n"
                            "((* for paragraph in letter.paragraphs *))((( paragraph | tex )))\n((* endfor *))",
        },
    })
    return directory


def run_revision(source, tmp_path, proposal=None, **kwargs):
    calls = []

    def respond(**request):
        calls.append(request)
        return proposal or application(), {"mock": True}

    output = revise_application(
        source_dir=source, feedback="Make the opening clearer.", document=kwargs.pop("document", "cv"),
        output_root=tmp_path / "versions", response_fn=respond, **kwargs,
    )
    return output, calls


def snapshot(path):
    return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}


@pytest.mark.parametrize("identity_source", ["generation_context.json", "generation_inputs.json"])
def test_revision_preserves_external_job_identity_across_later_versions(source, tmp_path, identity_source):
    identifier = "job_ext_0123456789abcdef01234567"
    identity_path = source / identity_source
    data = json.loads(identity_path.read_text()) if identity_path.exists() else {}
    write_json(identity_path, {**data, "job_id": identifier})
    before = snapshot(source)

    first, calls = run_revision(source, tmp_path)
    assert len(calls) == 1
    assert json.loads((first / "generation_context.json").read_text())["job_id"] == identifier
    assert snapshot(source) == before

    second, calls = run_revision(first, tmp_path)
    assert len(calls) == 1
    assert json.loads((second / "generation_context.json").read_text())["job_id"] == identifier


@pytest.mark.parametrize("identity", [None, "", "   ", 123, {"job_id": "nested"}])
def test_invalid_saved_job_identity_fails_before_revision_request(source, tmp_path, identity):
    write_json(source / "generation_inputs.json", {"job_id": identity})
    before = snapshot(source)
    with pytest.raises(ValueError, match="Invalid saved job identity"):
        revise_application(source_dir=source, feedback="Clarify opening.", document="cv",
                           output_root=tmp_path / "versions",
                           response_fn=lambda **request: pytest.fail("No model request should be made"))
    assert snapshot(source) == before
    assert not (tmp_path / "versions").exists()


def test_cv_revision_is_one_request_freezes_letter_and_keeps_source(source, tmp_path):
    before = snapshot(source)
    proposal = application().model_copy(deep=True)
    proposal.profile = "Researcher with practical experience coordinating samples and planning experiments."
    proposal.motivation_letter.subject = "This unrequested change must not appear"
    proposal.honest_gaps = ["An unwanted change to the fit analysis"]
    output, calls = run_revision(source, tmp_path, proposal)

    assert len(calls) == 1
    assert calls[0]["use_web_search"] is False
    assert calls[0]["artifact_dir"] == output
    assert snapshot(source) == before
    assert output.parent == tmp_path / "versions"
    assert (output / "motivation_letter.tex").read_bytes() == before["motivation_letter.tex"]
    revised = TailoredApplication.model_validate_json((output / "tailoring.json").read_text())
    assert revised.profile == proposal.profile
    assert revised.motivation_letter == application().motivation_letter
    assert revised.honest_gaps == application().honest_gaps
    assert (output / "cv.tex").read_text().startswith("CV SNAPSHOT TEMPLATE")
    assert proposal.profile in (output / "cv.tex").read_text()
    assert "previous/cv.tex" in (output / "changes.diff").read_text()
    metadata = json.loads((output / "revision.json").read_text())
    assert metadata["document"] == "cv"
    assert metadata["status"] == "validated"
    assert metadata["source_dir"] == str(source.resolve())
    assert metadata["source_hashes"]["cv.tex"] == hashlib.sha256(before["cv.tex"]).hexdigest()
    hashes = json.loads((output / "document_hashes.json").read_text())
    for name in ("cv.tex", "motivation_letter.tex"):
        assert hashes[name] == hashlib.sha256((output / name).read_bytes()).hexdigest()


def test_letter_revision_freezes_all_cv_fields_and_copies_existing_pdf(source, tmp_path):
    (source / "cv.pdf").write_bytes(b"synthetic PDF bytes retained verbatim")
    proposal = application().model_copy(deep=True)
    proposal.profile = "software engineer"  # Outside scope: never applied or validated as a new CV.
    proposal.selected_roles[0].bullet_ids = ["invented_id"]
    proposal.headline_tags = ["Unsupported"]
    proposal.motivation_letter.subject = "A more specific research application"
    output, _ = run_revision(source, tmp_path, proposal, document="cover_letter")
    revised = json.loads((output / "tailoring.json").read_text())
    original = application().model_dump()
    assert revised.pop("motivation_letter")["subject"] == proposal.motivation_letter.subject
    original.pop("motivation_letter")
    assert revised == original
    assert (output / "cv.tex").read_bytes() == (source / "cv.tex").read_bytes()
    assert (output / "cv.pdf").read_bytes() == (source / "cv.pdf").read_bytes()
    assert "LETTER SNAPSHOT TEMPLATE" in (output / "motivation_letter.tex").read_text()


def test_revising_same_source_twice_and_revising_new_version_never_overwrites(source, tmp_path):
    first, _ = run_revision(source, tmp_path)
    first_snapshot = snapshot(first)
    second, _ = run_revision(source, tmp_path)
    third, _ = run_revision(first, tmp_path, document="cover_letter")
    assert len({first, second, third}) == 3
    assert snapshot(first) == first_snapshot
    assert json.loads((third / "revision.json").read_text())["source_dir"] == str(first.resolve())


@pytest.mark.parametrize("feedback", ["", "   \n", None, "a" * 12_001])
def test_invalid_feedback_fails_before_request_or_writing(source, tmp_path, feedback):
    with pytest.raises(ValueError):
        revise_application(source_dir=source, feedback=feedback, document="cv", output_root=tmp_path / "versions",
                           response_fn=lambda **kwargs: pytest.fail("Must not request a model"))
    assert not (tmp_path / "versions").exists()


def test_unsupported_old_source_fails_before_model(source, tmp_path):
    (source / "generation_context.json").unlink()
    write_json(source / "prompt.json", {"user": "An unrelated document cannot supply verified facts."})
    with pytest.raises(ValueError, match="Generate a new CV and cover letter"):
        revise_application(source_dir=source, feedback="Shorten", document="cv", output_root=tmp_path / "versions",
                           response_fn=lambda **kwargs: pytest.fail("Must not request a model"))
    assert not (tmp_path / "versions").exists()


def test_unsupported_snapshot_version_does_not_fall_back_to_live_profile(source, tmp_path):
    write_json(source / "generation_context.json", {"schema_version": 999})
    with pytest.raises(ValueError, match="Unsupported saved context version"):
        run_revision(source, tmp_path)
    assert not (tmp_path / "versions").exists()


def test_original_that_failed_validation_is_not_sent_to_model(source, tmp_path):
    invalid = application().model_copy(deep=True)
    invalid.selected_roles[0].bullet_ids = ["not_verified"]
    (source / "tailoring.json").write_text(invalid.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="does not pass validation"):
        run_revision(source, tmp_path)
    assert not (tmp_path / "versions").exists()


@pytest.mark.parametrize("bad_field", ["bullet", "claim", "recipient"])
def test_invalid_revised_content_writes_audit_without_final_documents(source, tmp_path, bad_field):
    proposal = application().model_copy(deep=True)
    document = "cv"
    if bad_field == "bullet":
        proposal.selected_roles[0].bullet_ids = ["not_verified"]
    elif bad_field == "claim":
        proposal.profile = "I am a software engineer."
    else:
        document = "cover_letter"
        proposal.motivation_letter.recipient_name = "Unverified Person"
    output, calls = run_revision(source, tmp_path, proposal, document=document)
    assert len(calls) == 1
    assert (output / "VALIDATION_FAILED.txt").is_file()
    assert not (output / "cv.tex").exists()
    assert not (output / "motivation_letter.tex").exists()
    assert (output / "raw_response.json").is_file()
    assert json.loads((output / "revision.json").read_text())["status"] == "validation_failed"


@pytest.mark.parametrize("marker", ["REFERENCE CV EXAMPLES", "REFERENCE DOCUMENT EXAMPLES"])
def test_legacy_prompt_recovery_uses_only_original_saved_facts(source, tmp_path, marker):
    saved = json.loads((source / "generation_context.json").read_text())
    description = "Vacancy text with\n\nVERIFIED CANDIDATE FACTS\na misleading heading."
    user = build_cv_prompt(saved["job"], saved["profile"], description, saved["references"])
    user = user.replace("REFERENCE CV EXAMPLES", marker).replace("REFERENCE DOCUMENT EXAMPLES", marker)
    write_json(source / "prompt.json", {"user": user, "system": "Original instructions"})
    (source / "generation_context.json").unlink()
    context = load_generation_context(source)
    assert context["profile"]["candidate_summary"] == saved["profile"]["candidate_summary"]
    assert context["profile"]["interests"] == []
    assert context["full_description"] == description
    assert context["references"][0]["text"] == saved["references"][0]["text"]
    # Cover-letter template is supplied locally for old runs; no current candidate
    # profile or reference library is ever read during this recovery.
    output, _ = run_revision(source, tmp_path, document="cover_letter")
    assert (output / "motivation_letter.tex").is_file()
    assert json.loads((output / "generation_context.json").read_text())["recovered_from"] == "legacy_prompt"


def test_selected_references_and_written_feedback_are_sent_with_fact_constraints(source, tmp_path):
    output, calls = run_revision(source, tmp_path)
    user = json.loads(calls[0]["user"].split("\n", 1)[1])
    assert user["written_feedback"] == "Make the opening clearer."
    assert user["reference_examples_style_only"][0]["text"] == "A short reference example."
    assert user["existing_application"] == application().model_dump()
    assert user["existing_selected_document"] == (source / "cv.tex").read_bytes().decode("utf-8")
    assert "not new verified" in calls[0]["system"]
    assert (output / "generation_context.json").is_file()


def test_selected_document_manual_edits_are_not_silently_lost(source, tmp_path):
    (source / "cv.tex").write_text("Manual changes must be preserved.", encoding="utf-8")
    before = snapshot(source)
    with pytest.raises(ValueError, match="manually edited"):
        run_revision(source, tmp_path)
    assert snapshot(source) == before
    assert not (tmp_path / "versions").exists()


def test_unselected_document_manual_edits_are_preserved(source, tmp_path):
    manual = b"This companion document was manually improved.\r\n"
    (source / "motivation_letter.tex").write_bytes(manual)
    output, _ = run_revision(source, tmp_path)
    assert (output / "motivation_letter.tex").read_bytes() == manual
    with pytest.raises(ValueError, match="manually edited"):
        run_revision(output, tmp_path, document="cover_letter")


def test_only_selected_document_is_compiled_and_failure_reported(source, tmp_path, monkeypatch):
    compiled = []

    def compile_fake(directory, tex_name):
        compiled.append((directory, tex_name))
        return False, "Synthetic compiler failure"

    monkeypatch.setattr("job_radar.revisions.compile_latex", compile_fake)
    output, _ = run_revision(source, tmp_path, document="cover_letter", compile_pdf=True)
    assert compiled == [(output, "motivation_letter.tex")]
    assert (output / "LETTER_COMPILE_FAILED.txt").is_file()
    assert not (output / "COMPILE_FAILED.txt").exists()
    assert (output / "motivation_letter_compile.log").read_text() == "Synthetic compiler failure"


def test_api_failure_leaves_source_untouched_and_failed_version_auditable(source, tmp_path):
    before = snapshot(source)

    def failed_request(**kwargs):
        raise RuntimeError("Synthetic unavailable backend")

    with pytest.raises(RuntimeError, match="Synthetic unavailable"):
        revise_application(source_dir=source, feedback="Shorten", document="cv", output_root=tmp_path / "versions",
                           response_fn=failed_request)
    assert snapshot(source) == before
    output = next((tmp_path / "versions").iterdir())
    assert (output / "REVISION_FAILED.txt").is_file()
    assert json.loads((output / "revision.json").read_text())["status"] == "request_failed"
    assert not (output / "cv.tex").exists()


def test_external_photo_path_is_not_copied_or_rendered(source, tmp_path):
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"Private unrelated file")
    context = json.loads((source / "generation_context.json").read_text())
    context["profile"]["contact"]["photo_path"] = str(secret)
    context["templates"]["cv"] = "((( contact.photo_path )))\n((( profile_text )))"
    write_json(source / "generation_context.json", context)
    output, _ = run_revision(source, tmp_path)
    assert not (output / "outside.png").exists()
    assert str(secret) not in (output / "cv.tex").read_text()


def test_saved_local_photo_is_reused(source, tmp_path):
    context = json.loads((source / "generation_context.json").read_text())
    context["profile"]["contact"]["photo_path"] = "portrait.png"
    write_json(source / "generation_context.json", context)
    (source / "portrait.png").write_bytes(b"Synthetic saved image")
    output, _ = run_revision(source, tmp_path)
    assert (output / "portrait.png").read_bytes() == b"Synthetic saved image"


def test_malformed_generated_schema_is_a_failed_version_not_a_document(source, tmp_path):
    output = revise_application(
        source_dir=source, feedback="Shorten", document="cv", output_root=tmp_path / "versions",
        response_fn=lambda **kwargs: ({"unknown": "field"}, {"synthetic": "malformed"}),
    )
    assert (output / "VALIDATION_FAILED.txt").is_file()
    assert json.loads((output / "revision.json").read_text())["status"] == "validation_failed"
    assert not (output / "cv.tex").exists()
    assert not (output / "tailoring.json").exists()


@pytest.mark.parametrize("template", ["((( unavailable_profile_field )))", "((* if broken *))"])
def test_unrenderable_saved_template_is_rejected_before_model(source, tmp_path, template):
    context = json.loads((source / "generation_context.json").read_text())
    context["templates"]["cv"] = template
    write_json(source / "generation_context.json", context)
    with pytest.raises(ValueError, match="No improvement request was sent"):
        run_revision(source, tmp_path)
    assert not (tmp_path / "versions").exists()


def test_new_content_render_failure_keeps_audit_without_partial_documents(source, tmp_path):
    context = json.loads((source / "generation_context.json").read_text())
    context["templates"]["cv"] = (
        "((* if 'Revised' in profile_text *))((( absent_field )))((* endif *))((( profile_text )))"
    )
    write_json(source / "generation_context.json", context)
    proposal = application().model_copy(update={"profile": "Revised research profile."})
    before = snapshot(source)
    output, calls = run_revision(source, tmp_path, proposal)
    assert len(calls) == 1
    assert (output / "VALIDATION_FAILED.txt").is_file()
    assert json.loads((output / "revision.json").read_text())["status"] == "render_failed"
    assert not (output / "cv.tex").exists()
    assert not (output / "motivation_letter.tex").exists()
    assert snapshot(source) == before


def test_companion_photo_and_reports_survive_letter_revision(source, tmp_path):
    context = json.loads((source / "generation_context.json").read_text())
    context["profile"]["contact"]["photo_path"] = "./portrait.png"
    write_json(source / "generation_context.json", context)
    (source / "portrait.png").write_bytes(b"Companion CV photo")
    for name in ("fit_analysis.md", "job_description.md", "references_used.json"):
        (source / name).write_bytes(f"Preserved {name}\r\n".encode())
    output, _ = run_revision(source, tmp_path, document="cover_letter")
    for name in ("portrait.png", "fit_analysis.md", "job_description.md", "references_used.json"):
        assert (output / name).read_bytes() == (source / name).read_bytes()
    assert (output / "cv.tex").read_bytes() == (source / "cv.tex").read_bytes()


def test_missing_photo_is_reported_without_losing_a_saved_companion(source, tmp_path):
    context = json.loads((source / "generation_context.json").read_text())
    context["profile"]["contact"]["photo_path"] = "missing.png"
    write_json(source / "generation_context.json", context)
    output, _ = run_revision(source, tmp_path, document="cover_letter")
    assert (output / "cv.tex").read_bytes() == (source / "cv.tex").read_bytes()
    assert "missing.png" in (output / "REVIEW_NOTES.txt").read_text()
    assert json.loads((output / "revision.json").read_text())["review_notes"]


def test_unreadable_companion_asset_fails_before_a_model_request(source, tmp_path):
    (source / "cv.pdf").write_bytes(b"a" * (5 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="too large"):
        run_revision(source, tmp_path, document="cover_letter")
    assert not (tmp_path / "versions").exists()


def test_versioned_context_requires_saved_templates(source, tmp_path):
    context = json.loads((source / "generation_context.json").read_text())
    del context["templates"]
    write_json(source / "generation_context.json", context)
    with pytest.raises(ValueError, match="Missing saved document templates"):
        run_revision(source, tmp_path)
    assert not (tmp_path / "versions").exists()


def test_pre_reference_legacy_prompt_recovers_empty_reference_list(source, tmp_path):
    saved = json.loads((source / "generation_context.json").read_text())
    facts = dict(saved["profile"])
    facts.pop("interests")
    user = (
        "JOB\n" + json.dumps(saved["job"]) +
        "\n\nFULL JOB-PAGE TEXT\nOriginal vacancy description." +
        "\n\nVERIFIED CANDIDATE FACTS\n" + json.dumps(facts) +
        "\n\nTASK\nWrite the CV and cover letter."
    )
    write_json(source / "prompt.json", {"user": user, "system": "Original writing instructions"})
    (source / "generation_context.json").unlink()
    context = load_generation_context(source)
    assert context["references"] == []
    assert context["profile"]["interests"] == []
    assert context["full_description"] == "Original vacancy description."
    output, calls = run_revision(source, tmp_path, document="cover_letter")
    assert len(calls) == 1
    assert (output / "motivation_letter.tex").is_file()
    assert json.loads((output / "generation_context.json").read_text())["references"] == []


@pytest.mark.parametrize("boundary", ["\nTASK\n", "\n\nOTHER REFERENCES\n[]\n\nTASK\n", "\n\nTASKS\n"])
def test_pre_reference_legacy_prompt_requires_exact_task_boundary(source, tmp_path, boundary):
    saved = json.loads((source / "generation_context.json").read_text())
    user = (
        "JOB\n" + json.dumps(saved["job"]) +
        "\n\nFULL JOB-PAGE TEXT\nOriginal vacancy." +
        "\n\nVERIFIED CANDIDATE FACTS\n" + json.dumps(saved["profile"]) +
        boundary + "Write the application."
    )
    write_json(source / "prompt.json", {"user": user})
    (source / "generation_context.json").unlink()
    with pytest.raises(ValueError, match="Unsupported saved reference format"):
        run_revision(source, tmp_path)
    assert not (tmp_path / "versions").exists()
