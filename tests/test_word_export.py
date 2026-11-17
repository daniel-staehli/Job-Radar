"""Word output uses synthetic saved facts only; all tests run without AI."""
import hashlib
from io import BytesIO
import json
import socket
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from job_radar.gui_services import GuiService
from job_radar import word_export as word


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Word export must not use the network")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def saved(tmp_path):
    folder = tmp_path / "applications" / "example"
    folder.mkdir(parents=True)
    profile = {
        "contact": {"name": "Alex Example", "email": "alex@example.org", "location": "Basel", "linkedin": "https://example.org/alex"},
        "experiences": [{"key": "research", "role": "Research scientist", "location": "Example Institute", "dates": "2020–2026",
                         "subtitle": "Laboratory research", "bullets": [{"id": "b1", "text": "Coordinated a collaborative research project."}, {"id": "unused", "text": "Unselected fact"}]}],
        "skills": [{"id": "s1", "label": "Data", "text": "Python and R"}],
        "publications": [{"id": "p1", "citation": "Example A. A research study. 2025.", "url": "https://example.org/paper"}],
        "education": [{"institution": "Example University", "degree": "PhD in biology", "dates": "2016–2020", "detail": "Experimental methods"}],
        "additional_experience": [{"id": "a1", "text": "University teaching"}],
        "languages": ["English", "German"], "interests": ["Hiking"],
    }
    tailoring = {"verdict": "apply", "match_summary": "Suitable", "headline_tags": ["Life sciences"], "profile": "A scientist with experience in collaborative research.",
                 "selected_roles": [{"role_key": "research", "bullet_ids": ["b1"]}], "selected_skill_ids": ["s1"], "selected_publication_ids": ["p1"],
                 "selected_additional_ids": ["a1"], "priority_keywords": [], "honest_gaps": [], "interview_angles": [], "cv_risks_to_review": [],
                 "motivation_letter": {"language": "English", "recipient_name": None, "recipient_role": None, "show_recipient_block": True,
                                       "salutation": "Dear Hiring Team,", "subject": "Application for Scientist",
                                       "paragraphs": ["My saved opening.", "My saved evidence.", "My saved motivation."], "closing": "Yours sincerely,"}}
    (folder / "generation_context.json").write_text(json.dumps({"schema_version": 1, "profile": profile, "job": {"company": "Example Bio", "title": "Scientist"}}), encoding="utf-8")
    (folder / "tailoring.json").write_text(json.dumps(tailoring), encoding="utf-8")
    for name in ("cv.tex", "motivation_letter.tex"):
        (folder / name).write_text("Saved generated source", encoding="utf-8")
    (folder / "document_hashes.json").write_text(json.dumps({name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in ("cv.tex", "motivation_letter.tex")}), encoding="utf-8")
    return GuiService(tmp_path), folder


def contents(data):
    with ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def text(path):
    root = ET.fromstring(contents(path.read_bytes())["word/document.xml"])
    return "\n".join(n.text or "" for n in root.iter(word.w("t")))


def replace_part(data, name, value):
    parts = contents(data)
    parts[name] = value
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for key, content in parts.items():
            archive.writestr(key, content)
    return out.getvalue()


def test_export_both_preserves_sources_and_selections(saved):
    service, folder = saved
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    cv, letter = service.export_word(folder, {"cv": "clean", "cover_letter": "classic"}, "16 November 2026")
    assert all((folder / name).read_bytes() == value for name, value in before.items())
    assert cv.parent == letter.parent == folder
    cv_text, letter_text = text(cv), text(letter)
    assert "Alex Example" in cv_text and "Coordinated a collaborative research project." in cv_text
    assert "Unselected fact" not in cv_text
    assert "PhD in biology" in cv_text and "Hiking" in cv_text and "https://example.org/alex" in cv_text
    assert "My saved opening." in letter_text and "16 November 2026" in letter_text
    assert "Example Bio" in letter_text and "{{" not in letter_text
    assert b"Georgia" in contents(letter.read_bytes())["word/styles.xml"]
    assert b"Arial" in contents(cv.read_bytes())["word/styles.xml"]
    assert not any(name.startswith("docProps") for name in contents(cv.read_bytes()))
    cv.write_bytes(b"User's subsequent Word edits")
    second, = service.export_word(folder, {"cv": "classic"})
    assert second != cv and cv.read_bytes() == b"User's subsequent Word edits"


def test_exports_current_saved_manual_edits_and_recipient_visibility(saved):
    service, folder = saved
    path = folder / "tailoring.json"
    tailoring = json.loads(path.read_text())
    tailoring["motivation_letter"]["paragraphs"][0] = "My updated opening."
    tailoring["motivation_letter"]["show_recipient_block"] = False
    path.write_text(json.dumps(tailoring))
    letter, = service.export_word(folder, {"cover_letter": "clean"})
    assert "My updated opening." in text(letter)
    assert "Example Bio" not in text(letter)
    assert "My saved opening." not in text(letter)


def test_blocks_export_of_changed_tex(saved):
    service, folder = saved
    (folder / "cv.tex").write_text("External manual change")
    with pytest.raises(ValueError, match="edited after generation"):
        service.export_word(folder, {"cv": "clean"})
    assert not list(folder.glob("*.docx"))
    # An unrelated CV edit does not block exporting the unchanged letter.
    assert service.export_word(folder, {"cover_letter": "clean"})


def test_custom_template_preserves_styles_and_split_placeholders(saved, tmp_path):
    service, folder = saved
    data = word.starter_template("cover_letter", "classic")
    parts = contents(data)
    source = parts["word/document.xml"].replace(b"{{NAME}}", b"{{NA</w:t></w:r><w:r><w:t>ME}}")
    data = replace_part(data, "word/document.xml", source)
    data = replace_part(data, "word/styles.xml", parts["word/styles.xml"].replace(b"Georgia", b"Cambria"))
    data = replace_part(data, "docProps/core.xml", b"<private>Original template author</private>")
    data = replace_part(data, "word/webSettings.xml", b"<webSettings/>")
    data = replace_part(data, "word/stylesWithEffects.xml", b"<styles/>")
    path = tmp_path / "My layout.docx"
    path.write_bytes(data)
    identity = service.import_word_layout(path, "cover_letter")
    assert identity in dict(service.word_layouts("cover_letter"))
    assert "My layout" in dict(service.word_layouts("cover_letter"))[identity]
    output, = service.export_word(folder, {"cover_letter": identity})
    assert b"Cambria" in contents(output.read_bytes())["word/styles.xml"]
    assert "Alex Example" in text(output)
    assert not any(b"Original template author" in data for data in contents(output.read_bytes()).values())


@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
def test_missing_or_duplicate_placeholder_rejected(kind):
    data = word.starter_template(kind)
    body = contents(data)["word/document.xml"]
    with pytest.raises(ValueError, match="required placeholder"):
        word.read_template(replace_part(data, "word/document.xml", body.replace(b"{{NAME}}", b"Static name")), kind)
    with pytest.raises(ValueError, match="required placeholder"):
        word.read_template(replace_part(data, "word/document.xml", body.replace(b"{{CONTACT}}", b"{{NAME}}")), kind)


@pytest.mark.parametrize("part,payload,match", [
    ("word/vbaProject.bin", b"macro", "unsupported"),
    ("word/comments.xml", b"<comments>Private notes</comments>", "unsupported"),
    ("word/_rels/document.xml.rels", b'<Relationships><Relationship TargetMode="External" Target="file:///private"/></Relationships>', "external"),
    ("word/document.xml", b'<!DOCTYPE x [<!ENTITY a "private">]><x>&a;</x>', "entities"),
])
def test_unsafe_template_parts_rejected(part, payload, match):
    with pytest.raises(ValueError, match=match):
        word.read_template(replace_part(word.starter_template("cv"), part, payload), "cv")


def test_no_partial_export_if_second_layout_invalid(saved):
    service, folder = saved
    with pytest.raises(ValueError):
        service.export_word(folder, {"cv": "clean", "cover_letter": "../../private"})
    assert not list(folder.glob("*.docx"))
    with pytest.raises(ValueError, match="at least one"):
        service.export_word(folder, {})


def test_legacy_cv_is_not_silently_incomplete(saved):
    service, folder = saved
    path = folder / "generation_context.json"
    data = json.loads(path.read_text())
    del data["profile"]["interests"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="older CV snapshot"):
        service.export_word(folder, {"cv": "clean"})


def test_unsupported_template_fields_rejected():
    data = word.starter_template("cv")
    body = contents(data)["word/document.xml"].replace(b"<w:body>", b'<w:body><w:fldSimple w:instr="DDE dangerous"/>')
    with pytest.raises(ValueError, match="fields"):
        word.read_template(replace_part(data, "word/document.xml", body), "cv")


def test_corrupt_and_wrong_kind_template_rejected():
    with pytest.raises(ValueError, match="readable"):
        word.read_template(b"Not a zip", "cv")
    with pytest.raises(ValueError, match="placeholders"):
        word.read_template(word.starter_template("cv"), "cover_letter")
