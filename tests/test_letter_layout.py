"""Layout-only changes preserve saved prose and require no live AI requests."""
import hashlib
import json
from pathlib import Path

import pytest

from job_radar.config import ROOT
from job_radar.cv import latex_environment, latex_escape, materialize_letter_context
from job_radar.letter_layout import (
    LEGACY_RECIPIENT_BLOCK, RECIPIENT_BLOCK, change_recipient_block, prepare_letter_template,
)
from job_radar.manual_edits import load_manual_document, save_manual_document
from job_radar.models import TailoredApplication
from job_radar.revisions import revise_application
from test_revisions import application, profile, source, snapshot, write_json


def environment():
    env = latex_environment(ROOT / "templates")
    env.filters["tex"] = latex_escape
    return env


def standard_template():
    return (ROOT / "templates" / "motivation_letter.tex.j2").read_text(encoding="utf-8")


def make_layout_source(source, *, legacy=False, crlf=False, bom=False):
    saved = json.loads((source / "generation_context.json").read_text())
    saved["job"]["company"] = "ExampleWorks"
    template = standard_template()
    if legacy:
        template = template.replace(RECIPIENT_BLOCK, LEGACY_RECIPIENT_BLOCK)
    saved["templates"]["cover_letter"] = template
    write_json(source / "generation_context.json", saved)
    context = materialize_letter_context(application(), saved["profile"], saved["job"])
    context["date_line"] = "3 January 2001"
    tex = environment().from_string(template).render(**context)
    if crlf:
        tex = tex.replace("\n", "\r\n")
    if bom:
        tex = "\ufeff" + tex
    (source / "motivation_letter.tex").write_bytes(tex.encode())
    hashes = json.loads((source / "document_hashes.json").read_text())
    hashes["motivation_letter.tex"] = hashlib.sha256(tex.encode()).hexdigest()
    write_json(source / "document_hashes.json", hashes)
    return saved


def run_layout_revision(source, tmp_path, *, proposal=None, **kwargs):
    proposal = proposal or application().model_copy(deep=True)
    proposal.motivation_letter.show_recipient_block = False
    return revise_application(
        source_dir=source, feedback="Remove the lone ExampleWorks header, without rewriting the letter.",
        document="cover_letter", formatting_only=True, output_root=tmp_path / "versions",
        response_fn=lambda **request: (proposal, {"synthetic": True}), **kwargs,
    )


def test_default_show_preserves_old_saved_schema():
    old = application().model_dump()
    old["motivation_letter"].pop("show_recipient_block")
    assert TailoredApplication.model_validate(old).motivation_letter.show_recipient_block is True


@pytest.mark.parametrize("visible", [True, False])
def test_template_controls_only_recipient_block(visible):
    draft = application()
    draft.motivation_letter.recipient_name = "Known Contact"
    draft.motivation_letter.recipient_role = "Hiring Lead"
    draft.motivation_letter.show_recipient_block = visible
    draft.motivation_letter.paragraphs[0] = "ExampleWorks in the body remains unchanged."
    context = materialize_letter_context(draft, profile(), {"company": "ExampleWorks", "title": "Research"})
    text = environment().from_string(standard_template()).render(**context)
    assert (r"\textbf{ExampleWorks}" in text) is visible
    assert ("Known Contact" in text) is visible
    assert ("Hiring Lead" in text) is visible
    assert "ExampleWorks in the body remains unchanged." in text
    assert "example@example.org" in text
    assert text.count(r"\begin{minipage}") == text.count(r"\end{minipage}") == 2
    if not visible:
        assert r"\mbox{}" in text


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("crlf", [True, False])
@pytest.mark.parametrize("bom", [True, False])
def test_formatting_only_preserves_every_other_field_and_saved_date(source, tmp_path, legacy, crlf, bom):
    make_layout_source(source, legacy=legacy, crlf=crlf, bom=bom)
    before = snapshot(source)
    proposal = application().model_copy(deep=True)
    proposal.profile = "Unrequested rewritten profile"
    proposal.honest_gaps = ["Unrequested new analysis"]
    proposal.motivation_letter.subject = "Unrequested new subject"
    proposal.motivation_letter.paragraphs = ["Invented rewritten prose."] * 3
    proposal.motivation_letter.recipient_name = "Invented Recipient"
    output = run_layout_revision(source, tmp_path, proposal=proposal)
    assert snapshot(source) == before
    expected = application().model_dump()
    expected["motivation_letter"]["show_recipient_block"] = False
    assert json.loads((output / "tailoring.json").read_text(encoding="utf-8")) == expected
    assert (output / "cv.tex").read_bytes() == before["cv.tex"]
    text = (output / "motivation_letter.tex").read_bytes()
    assert b"3 January 2001" in text
    assert b"Unrequested" not in text
    assert b"\\textbf{ExampleWorks}" not in text
    old = before["motivation_letter.tex"]
    old_block = environment().from_string(LEGACY_RECIPIENT_BLOCK).render(
        **materialize_letter_context(application(), profile(), {"company": "ExampleWorks", "title": "Research"}),
    ).encode()
    new_block = b"\\begin{minipage}[t]{\\linewidth}\n\\mbox{}\n\\end{minipage}"
    if crlf:
        old_block = old_block.replace(b"\n", b"\r\n")
        new_block = new_block.replace(b"\n", b"\r\n")
    assert text == old.replace(old_block, new_block, 1)
    saved = json.loads((output / "generation_context.json").read_text())
    assert RECIPIENT_BLOCK in saved["templates"]["cover_letter"]
    assert json.loads((output / "revision.json").read_text())["formatting_only"] is True


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_known_template_upgrade_is_narrow_and_idempotent(newline):
    source = "Company in body: ((( company | tex )))\n" + LEGACY_RECIPIENT_BLOCK + "\nEnd"
    source = source.replace("\n", newline)
    updated = prepare_letter_template(source, require_support=True)
    assert updated == source.replace(LEGACY_RECIPIENT_BLOCK.replace("\n", newline), RECIPIENT_BLOCK.replace("\n", newline))
    assert prepare_letter_template(updated, require_support=True) == updated


def test_unknown_formatting_template_rejected_before_request(source, tmp_path):
    before = snapshot(source)
    with pytest.raises(ValueError, match="does not support the recipient-block"):
        revise_application(
            source_dir=source, feedback="Hide company", document="cover_letter", formatting_only=True,
            output_root=tmp_path / "versions", response_fn=lambda **kw: pytest.fail("Must not call AI"),
        )
    assert snapshot(source) == before
    assert not (tmp_path / "versions").exists()


def test_normal_revision_cannot_silently_ignore_hide_on_unknown_template(source, tmp_path):
    proposal = application().model_copy(deep=True)
    proposal.motivation_letter.show_recipient_block = False
    output = revise_application(
        source_dir=source, feedback="Hide company", document="cover_letter",
        output_root=tmp_path / "versions", response_fn=lambda **kw: (proposal, {}),
    )
    assert "does not support the recipient-block" in (output / "VALIDATION_FAILED.txt").read_text()
    assert not (output / "motivation_letter.tex").exists()


def test_formatting_noop_reports_limit_without_claiming_success(source, tmp_path):
    make_layout_source(source)
    before = snapshot(source)
    output = revise_application(
        source_dir=source, feedback="Use larger margins", document="cover_letter", formatting_only=True,
        output_root=tmp_path / "versions", response_fn=lambda **kw: (application(), {}),
    )
    assert json.loads((output / "revision.json").read_text())["status"] == "no_changes"
    assert "showing or hiding" in (output / "REVIEW_NOTES.txt").read_text()
    assert not (output / "motivation_letter.tex").exists()
    assert snapshot(source) == before


def test_cv_formatting_is_rejected_before_request(source, tmp_path):
    with pytest.raises(ValueError, match="supports the cover letter"):
        revise_application(
            source_dir=source, feedback="Hide company", document="cv", formatting_only=True,
            output_root=tmp_path / "versions", response_fn=lambda **kw: pytest.fail("Must not call AI"),
        )


def test_manual_edits_keep_hidden_recipient_block_after_revision(source, tmp_path):
    make_layout_source(source, legacy=True)
    output = run_layout_revision(source, tmp_path)
    edit = load_manual_document(output, "cover_letter")
    values = {field["id"]: field["value"] for field in edit["fields"]}
    subject = next(field for field in edit["fields"] if field["label"] == "Subject")
    values[subject["id"]] = "A manually clarified subject"
    manual = save_manual_document(
        source_dir=output, document="cover_letter", values=values, notes="",
        expected_fingerprint=edit["source_fingerprint"], output_root=tmp_path / "manual",
    )
    assert "A manually clarified subject" in (manual / "motivation_letter.tex").read_text()
    assert r"\textbf{ExampleWorks}" not in (manual / "motivation_letter.tex").read_text()
    assert json.loads((manual / "tailoring.json").read_text())["motivation_letter"]["show_recipient_block"] is False


def test_formatting_only_can_restore_block(source, tmp_path):
    make_layout_source(source)
    hidden = run_layout_revision(source, tmp_path)
    hidden_data = TailoredApplication.model_validate_json((hidden / "tailoring.json").read_text())
    hidden_data.motivation_letter.show_recipient_block = True
    restored = revise_application(
        source_dir=hidden, feedback="Show recipient block again", document="cover_letter", formatting_only=True,
        output_root=tmp_path / "restored", response_fn=lambda **kw: (hidden_data, {}),
    )
    assert (restored / "motivation_letter.tex").read_bytes() == (source / "motivation_letter.tex").read_bytes()


def test_normal_content_noop_reports_unchanged(source, tmp_path):
    make_layout_source(source)
    context = json.loads((source / "generation_context.json").read_text())
    current = environment().from_string(context["templates"]["cover_letter"]).render(
        **materialize_letter_context(application(), profile(), context["job"]),
    )
    (source / "motivation_letter.tex").write_text(current, encoding="utf-8")
    hashes = json.loads((source / "document_hashes.json").read_text())
    hashes["motivation_letter.tex"] = hashlib.sha256((source / "motivation_letter.tex").read_bytes()).hexdigest()
    write_json(source / "document_hashes.json", hashes)
    output = revise_application(
        source_dir=source, feedback="Please make a change", document="cover_letter",
        output_root=tmp_path / "unchanged", response_fn=lambda **kw: (application(), {}),
    )
    assert json.loads((output / "revision.json").read_text())["status"] == "no_changes"
    assert not (output / "motivation_letter.tex").exists()


def test_saved_block_mismatch_fails_before_model_request(source, tmp_path):
    make_layout_source(source)
    text = (source / "motivation_letter.tex").read_text().replace(r"\textbf{ExampleWorks}", r"\textbf{Unexpected Company}")
    (source / "motivation_letter.tex").write_text(text, encoding="utf-8")
    hashes = json.loads((source / "document_hashes.json").read_text())
    hashes["motivation_letter.tex"] = hashlib.sha256((source / "motivation_letter.tex").read_bytes()).hexdigest()
    write_json(source / "document_hashes.json", hashes)
    with pytest.raises(ValueError, match="No improvement request was sent"):
        revise_application(
            source_dir=source, feedback="Hide company", document="cover_letter", formatting_only=True,
            output_root=tmp_path / "versions", response_fn=lambda **kw: pytest.fail("Must not call AI"),
        )


def test_formatting_only_preserves_manual_wording_and_review_exceptions(source, tmp_path):
    saved = make_layout_source(source)
    original = application()
    original.motivation_letter.subject += " — personal draft"
    (source / "tailoring.json").write_text(original.model_dump_json(), encoding="utf-8")
    saved["manual_documents"] = ["cover_letter"]
    write_json(source / "generation_context.json", saved)
    text = environment().from_string(saved["templates"]["cover_letter"]).render(
        **materialize_letter_context(original, profile(), saved["job"]),
    )
    (source / "motivation_letter.tex").write_text(text, encoding="utf-8")
    hashes = json.loads((source / "document_hashes.json").read_text())
    hashes["motivation_letter.tex"] = hashlib.sha256((source / "motivation_letter.tex").read_bytes()).hexdigest()
    write_json(source / "document_hashes.json", hashes)
    output = run_layout_revision(source, tmp_path)
    result = TailoredApplication.model_validate_json((output / "tailoring.json").read_text(encoding="utf-8"))
    assert result.motivation_letter.subject == original.motivation_letter.subject
    assert json.loads((output / "generation_context.json").read_text())["manual_documents"] == ["cover_letter"]
    assert not (output / "VALIDATION_FAILED.txt").exists()
