"""Verify the exported file against a fictional application snapshot."""
from pathlib import Path

import pytest

from test_gui import app, finish
from test_references import pdf_bytes
from test_word_export import contents, replace_part, saved


def test_flags_changes_in_actual_word_file(saved):
    service, folder = saved
    path, = service.export_word(folder, {"cv": "classic"})
    assert "were found" in service.check_exported_cv(folder, path)
    # Both dates still exist. Only the associations and the email change.
    data = path.read_bytes()
    body = contents(data)["word/document.xml"].decode()
    body = body.replace("2016–2020", "TEMP").replace("2020–2026", "2016–2020")
    body = body.replace("TEMP", "2020–2026").replace("alex@example.org", "new@example.org")
    changed = replace_part(data, "word/document.xml", body.encode())
    path.write_bytes(changed)
    report = service.check_exported_cv(folder, path)
    assert "Email not found as written: alex@example.org" in report
    assert "right entry: Research scientist" in report
    assert "right entry: Example University" in report
    assert "new@example.org" in report.partition("Extracted text\n")[2]
    assert path.read_bytes() == changed


def test_pdf_and_legacy_snapshot(saved):
    service, folder = saved
    path = folder / "cv.pdf"
    path.write_bytes(pdf_bytes("Alex Example unmatched@example.org"))
    report = service.check_exported_cv(folder, path)
    assert "Name not found" not in report
    assert "Email not found" in report and "Role not found" in report
    assert "Check the section heading: Education" in report
    (folder / "generation_context.json").unlink()
    assert "unavailable for comparison" in service.check_exported_cv(folder, path)


@pytest.mark.parametrize("options,message", [({"text": ""}, "no readable text"), ({"encrypted": True}, "Encrypted")])
def test_unreadable_pdf_cannot_pass(saved, options, message):
    service, folder = saved
    path = folder / "cv.pdf"
    path.write_bytes(pdf_bytes(**options))
    with pytest.raises(ValueError, match=message):
        service.check_exported_cv(folder, path)


def test_review_button_reads_chosen_export(app, saved, monkeypatch):
    service, folder = saved
    path, = service.export_word(folder, {"cv": "clean"})
    app.refresh_drafts(folder)
    assert Path(app._chosen_draft()["directory"]) == folder
    button = next(b for b in app.action_buttons if b.cget("text") == "Check exported CV…")
    reports = []
    monkeypatch.setattr(app, "_show_text", lambda title, text: reports.append((title, text)))
    monkeypatch.setattr("job_radar.gui.filedialog.askopenfilename", lambda **kw: "")
    button.invoke()
    assert not app.busy and not reports
    monkeypatch.setattr("job_radar.gui.filedialog.askopenfilename", lambda **kw: str(path))
    button.invoke()
    finish(app)
    assert not app.test_errors
    title, report = reports.pop()
    assert path.name in title and "were found" in report and "Alex Example" in report
