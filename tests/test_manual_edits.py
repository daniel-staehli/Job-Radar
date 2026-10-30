"""Human edits preserve originals and remain usable for later AI improvement."""
import hashlib
import json

import pytest

from job_radar.manual_edits import load_manual_document, save_manual_document, validate_revision_tailoring
from job_radar.models import TailoredApplication
from job_radar.revisions import revise_application
from test_revisions import source, application, snapshot, write_json


def edit(source, tmp_path, document="cover_letter", changed=None, notes="Check the opening."):
    saved = load_manual_document(source, document)
    values = {item["id"]: item["value"] for item in saved["fields"]}
    for field in saved["fields"]:
        if field["label"] in (changed or {}):
            values[field["id"]] = changed[field["label"]]
    return save_manual_document(source_dir=source, document=document, values=values, notes=notes,
                                expected_fingerprint=saved["source_fingerprint"],
                                output_root=tmp_path / "manual")


def test_cover_letter_edit_preserves_original_cv_pdf_and_tracks_author(source, tmp_path):
    (source / "cv.pdf").write_bytes(b"unchanged companion PDF")
    before = snapshot(source)
    result = edit(source, tmp_path, changed={"Subject": "My directly edited subject"})
    assert snapshot(source) == before
    assert (result / "cv.tex").read_bytes() == before["cv.tex"]
    assert (result / "cv.pdf").read_bytes() == before["cv.pdf"]
    actual = json.loads((result / "tailoring.json").read_text(encoding="utf-8"))
    expected = application().model_dump()
    assert actual.pop("motivation_letter")["subject"] == "My directly edited subject"
    expected.pop("motivation_letter")
    assert actual == expected
    assert "My directly edited subject" in (result / "motivation_letter.tex").read_text(encoding="utf-8")
    assert json.loads((result / "revision.json").read_text(encoding="utf-8"))["author"] == "user"
    assert load_manual_document(result, "cover_letter")["notes"] == "Check the opening."
    for name, digest in json.loads((result / "document_hashes.json").read_text(encoding="utf-8")).items():
        assert digest == hashlib.sha256((result / name).read_bytes()).hexdigest()


def test_cv_bank_wording_is_local_to_new_version_and_used_by_later_improvement(source, tmp_path):
    context = json.loads((source / "generation_context.json").read_text(encoding="utf-8"))
    context["job_id"] = "job_ext_0123456789abcdef01234567"
    context["templates"]["cv"] += "((* for role in experiences *))((* for bullet in role.bullets *))((( bullet.text | tex )))\n((* endfor *))((* endfor *))"
    write_json(source / "generation_context.json", context)
    before = snapshot(source)
    result = edit(source, tmp_path, document="cv", changed={
        "Researcher · bullet 1": "Personally planned and organised laboratory experiments.",
        "Profile": "Researcher with practical laboratory experience and clear communication.",
    })
    assert snapshot(source) == before
    saved_context = json.loads((result / "generation_context.json").read_text(encoding="utf-8"))
    assert saved_context["job_id"] == context["job_id"]
    assert saved_context["profile"]["experiences"][0]["bullets"][0]["text"] == "Personally planned and organised laboratory experiments."
    assert "Personally planned and organised" in (result / "cv.tex").read_text(encoding="utf-8")
    assert (result / "motivation_letter.tex").read_bytes() == before["motivation_letter.tex"]
    requests = []

    def respond(**request):
        requests.append(json.loads(request["user"].split("\n", 1)[1]))
        return TailoredApplication.model_validate_json((result / "tailoring.json").read_text(encoding="utf-8")), {"mock": True}

    revised = revise_application(source_dir=result, document="cv", feedback="Keep the new facts and make the profile concise.",
                                 output_root=tmp_path / "improved", response_fn=respond)
    assert requests[0]["existing_application"]["profile"].startswith("Researcher with practical")
    assert requests[0]["verified_candidate_facts"]["experiences"][0]["bullets"][0]["text"].startswith("Personally planned")
    assert json.loads((revised / "generation_context.json").read_text(encoding="utf-8"))["job_id"] == context["job_id"]


def test_notes_only_version_preserves_both_documents_byte_for_byte(source, tmp_path):
    (source / "motivation_letter.pdf").write_bytes(b"same selected PDF")
    before = snapshot(source)
    result = edit(source, tmp_path)
    assert snapshot(source) == before
    for name in ("cv.tex", "motivation_letter.tex", "motivation_letter.pdf"):
        assert (result / name).read_bytes() == before[name]
    assert json.loads((result / "revision.json").read_text(encoding="utf-8"))["changed_fields"] == []


def test_manual_dashes_and_short_letter_saved_with_review_notes(source, tmp_path):
    result = edit(source, tmp_path, changed={
        "Letter body (3–6 paragraphs, separated by blank lines)": "My first paragraph — written by me.\n\nMy second paragraph.\n\nMy third paragraph."})
    saved = json.loads((result / "tailoring.json").read_text(encoding="utf-8"))
    assert "—" in saved["motivation_letter"]["paragraphs"][0]
    warnings = (result / "REVIEW_NOTES.txt").read_text(encoding="utf-8")
    assert "expected 180-500" in warnings
    assert "em/en dash" in warnings
    assert not (result / "VALIDATION_FAILED.txt").exists()


def test_manual_unsupported_claim_in_cv_bank_is_blocked_without_writes(source, tmp_path):
    before = snapshot(source)
    with pytest.raises(ValueError, match="forbidden/unsupported"):
        edit(source, tmp_path, "cv", {"Researcher · bullet 1": "Worked as a software engineer."})
    assert snapshot(source) == before
    assert not (tmp_path / "manual").exists()


def test_manual_scope_cannot_be_expanded_by_posting_arbitrary_field(source, tmp_path):
    saved = load_manual_document(source, "cover_letter")
    values = {item["id"]: item["value"] for item in saved["fields"]}
    values["profile"] = "Unexpected CV change"
    with pytest.raises(ValueError, match="editable fields changed"):
        save_manual_document(source_dir=source, document="cover_letter", values=values, notes="",
                             expected_fingerprint=saved["source_fingerprint"], output_root=tmp_path / "manual")
    assert not (tmp_path / "manual").exists()


def test_external_tex_edits_and_stale_editor_snapshot_are_rejected(source, tmp_path):
    saved = load_manual_document(source, "cover_letter")
    context = json.loads((source / "generation_context.json").read_text(encoding="utf-8"))
    context["job"]["company"] = "Changed while editor was open"
    write_json(source / "generation_context.json", context)
    with pytest.raises(ValueError, match="changed while the editor was open"):
        save_manual_document(source_dir=source, document="cover_letter",
                             values={item["id"]: item["value"] for item in saved["fields"]}, notes="",
                             expected_fingerprint=saved["source_fingerprint"], output_root=tmp_path / "manual")
    (source / "motivation_letter.tex").write_text("External change")
    with pytest.raises(ValueError, match="changed outside Job Radar"):
        load_manual_document(source, "cover_letter")
    assert not (tmp_path / "manual").exists()


def test_unselected_changed_tex_remains_marked_and_bytes_preserved(source, tmp_path):
    old_hash = json.loads((source / "document_hashes.json").read_text(encoding="utf-8"))["cv.tex"]
    (source / "cv.tex").write_bytes(b"External edits to CV must be preserved")
    result = edit(source, tmp_path, changed={"Subject": "My subject"})
    assert (result / "cv.tex").read_bytes() == (source / "cv.tex").read_bytes()
    assert json.loads((result / "document_hashes.json").read_text(encoding="utf-8"))["cv.tex"] == old_hash
    with pytest.raises(ValueError, match="changed outside Job Radar"):
        load_manual_document(result, "cv")


def test_latex_in_manual_text_is_escaped(source, tmp_path):
    result = edit(source, tmp_path, changed={"Subject": r"Interest in R&D: 100% \input{private}"})
    tex = (result / "motivation_letter.tex").read_text(encoding="utf-8")
    assert r"R\&D" in tex
    assert r"100\%" in tex
    assert r"\textbackslash{}input\{private\}" in tex


def test_manual_short_letter_survives_two_ai_cv_revisions(source, tmp_path):
    manual = edit(source, tmp_path, changed={
        "Letter body (3–6 paragraphs, separated by blank lines)": "My first paragraph — written by me.\n\nMy second paragraph.\n\nMy third paragraph."})
    saved_letter = (manual / "motivation_letter.tex").read_bytes()
    current = manual
    for index in range(2):
        proposal = application()
        proposal.profile = f"Researcher with experience coordinating experiments and samples at stage {index}."
        current = revise_application(source_dir=current, document="cv", feedback="Clarify my CV profile.",
                                     output_root=tmp_path / f"ai-cv-{index}",
                                     response_fn=lambda **kwargs: (proposal, {"mock": True}))
        assert not (current / "VALIDATION_FAILED.txt").exists()
        assert (current / "motivation_letter.tex").read_bytes() == saved_letter
        assert json.loads((current / "manual_notes.json").read_text(encoding="utf-8"))["cover_letter"] == "Check the opening."


def test_manual_long_profile_survives_ai_letter_revision(source, tmp_path):
    long_profile = "Research coordination and laboratory experience. " * 24
    manual = edit(source, tmp_path, "cv", {"Profile": long_profile})
    current = revise_application(source_dir=manual, document="cover_letter", feedback="Clarify the letter subject.",
                                 output_root=tmp_path / "ai-letter",
                                 response_fn=lambda **kwargs: (application(), {"mock": True}))
    assert not (current / "VALIDATION_FAILED.txt").exists()
    assert (current / "cv.tex").read_bytes() == (manual / "cv.tex").read_bytes()
    assert json.loads((current / "tailoring.json").read_text(encoding="utf-8"))["profile"] == long_profile


def test_ai_selected_document_still_obeys_limits_after_manual_companion(source, tmp_path):
    manual = edit(source, tmp_path, "cv", {"Profile": "Research coordination and laboratory experience. " * 24})
    proposal = application()
    proposal.motivation_letter.paragraphs = ["Too short.", "Another short paragraph.", "A last short paragraph."]
    current = revise_application(source_dir=manual, document="cover_letter", feedback="Make the letter concise.",
                                 output_root=tmp_path / "ai-letter",
                                 response_fn=lambda **kwargs: (proposal, {"mock": True}))
    assert (current / "VALIDATION_FAILED.txt").exists()
    assert "expected 180-500" in (current / "VALIDATION_FAILED.txt").read_text(encoding="utf-8")
    assert not (current / "motivation_letter.tex").exists()


def test_manual_companion_does_not_mask_same_ai_style_problem(source):
    context = json.loads((source / "generation_context.json").read_text(encoding="utf-8"))
    context["manual_documents"] = ["cover_letter"]
    proposal = application()
    proposal.motivation_letter.subject = "Human text — with a dash"
    proposal.profile = "AI text — also containing a dash"
    errors, notes = validate_revision_tailoring(proposal, context, selected_document="cv")
    assert "Generated prose contains an em/en dash; use normal punctuation." in errors
    assert "Generated prose contains an em/en dash; use normal punctuation." in notes


def test_shared_contact_not_exposed_to_single_document_editor(source):
    saved = load_manual_document(source, "cv")
    assert not any(field["label"].startswith("Contact") for field in saved["fields"])
