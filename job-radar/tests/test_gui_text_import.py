"""Use the real paste window and database with fictional vacancies only."""
import json
import time
import tkinter as tk

import pytest

from job_radar import providers
from test_gui import app, finish, save_through_form


TEXT = "Company: Fictional Laboratory\nJob title: Research Coordinator\nLocation: Lausanne\n\nCoordinate experiments and samples."


def block_ai_settings(app, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Pasting and saving a vacancy must not read AI settings or call a provider")

    monkeypatch.setattr(app.service, "ai_settings", forbidden)
    monkeypatch.setattr(app.service, "check_codex", forbidden)
    monkeypatch.setattr(app.service, "generate", forbidden)
    monkeypatch.setattr(providers, "resolve_response_fn", forbidden)
    # The importer must also be independent of an unusable current selection.
    app.ai_current = {"provider": "unavailable-provider", "model": "unavailable-model"}
    return calls


def test_paste_window_extracts_and_saves_without_url_or_ai_settings(app, monkeypatch, tmp_path):
    calls = block_ai_settings(app, monkeypatch)
    app.paste_job_text()
    window = app._paste_window
    app.pasted_job_text.insert("1.0", TEXT)
    assert app.pasted_job_url.get() == ""
    app.extract_pasted_job()
    finish(app)

    assert not window.winfo_exists()
    assert app.tabs.select() == str(app.import_tab)
    assert app.job_fields["company"].get() == "Fictional Laboratory"
    assert app.job_fields["title"].get() == "Research Coordinator"
    assert app.job_fields["location"].get() == "Lausanne"
    assert app.job_fields["source_url"].get() == ""
    assert app.description.get("1.0", "end-1c") == TEXT
    assert app.draft_path.is_file()
    assert not app.service.db_path.exists(), "Extraction must wait for the user's reviewed save"
    audit = json.loads(app.draft_path.with_suffix(".extraction.json").read_text(encoding="utf-8"))
    assert audit["model_used"] is False
    assert audit["network_used"] is False

    app.save_job()
    finish(app)
    assert not app.test_errors
    assert app.tabs.select() == str(app.jobs_tab)
    saved = app.service.jobs()
    assert len(saved) == 1
    assert saved[0]["description"] == TEXT
    assert saved[0]["source_url"] == ""
    assert saved[0]["company"] == "Fictional Laboratory"
    assert saved[0]["source"] == "pasted text"
    assert calls == []
    assert not (tmp_path / "applications").exists()


def test_failed_extraction_keeps_pasted_text_form_and_existing_job(app, monkeypatch):
    job_id = save_through_form(app)
    original = app.service.db_path.read_bytes()
    fields = {key: variable.get() for key, variable in app.job_fields.items()}
    description = app.description.get("1.0", "end-1c")
    calls = block_ai_settings(app, monkeypatch)

    def failed_import(*args, **kwargs):
        raise ValueError("Synthetic extraction failure; edit the pasted text and retry.")

    monkeypatch.setattr(app.service, "import_text", failed_import)
    app.paste_job_text()
    window = app._paste_window
    app.pasted_job_text.insert("1.0", TEXT)
    app.pasted_job_url.insert(0, "https://example.org/jobs/research")
    app.extract_pasted_job()
    finish(app)

    assert window.winfo_exists()
    assert app.pasted_job_text.get("1.0", "end-1c") == TEXT
    assert app.pasted_job_url.get() == "https://example.org/jobs/research"
    assert str(app.pasted_job_text["state"]) == "normal"
    assert str(app.pasted_job_url["state"]) == "normal"
    assert {key: variable.get() for key, variable in app.job_fields.items()} == fields
    assert app.description.get("1.0", "end-1c") == description
    assert app.service.db_path.read_bytes() == original
    assert app.selected_job["job_id"] == job_id
    assert len(app.test_errors) == 1
    assert "Synthetic extraction failure" in app.test_errors[0]
    assert calls == []


def test_unlabelled_paste_requires_reviewed_fields_before_save(app, monkeypatch):
    calls = block_ai_settings(app, monkeypatch)
    prose = "Fictional Laboratory seeks a research coordinator.\nWork with our laboratory teams in Lausanne."
    app.paste_job_text()
    app.pasted_job_text.insert("1.0", prose)
    app.extract_pasted_job()
    finish(app)
    assert all(app.job_fields[field].get() == "" for field in ("company", "title", "location"))
    assert "Fill in: company, title, location" in app.import_note.get()
    assert app.description.get("1.0", "end-1c") == prose
    app.save_job()
    finish(app)
    assert len(app.test_errors) == 1
    assert "company" in app.test_errors.pop()
    assert not app.service.db_path.exists()

    for field, value in {"company": "Fictional Laboratory", "title": "Research Coordinator", "location": "Lausanne"}.items():
        app.job_fields[field].set(value)
    app.save_job()
    finish(app)
    assert not app.test_errors
    assert app.service.jobs()[0]["description"] == prose
    assert app.service.jobs()[0]["source_url"] == ""
    assert calls == []


def test_invalid_optional_link_preserves_paste_for_correction(app, monkeypatch):
    calls = block_ai_settings(app, monkeypatch)
    app.paste_job_text()
    window = app._paste_window
    app.pasted_job_text.insert("1.0", TEXT)
    app.pasted_job_url.insert(0, "http://localhost/jobs/123")
    app.extract_pasted_job()
    finish(app)
    assert window.winfo_exists()
    assert app.pasted_job_text.get("1.0", "end-1c") == TEXT
    assert app.test_errors
    assert not app.service.db_path.exists()
    app.test_errors.clear()
    app.pasted_job_url.delete(0, "end")
    app.extract_pasted_job()
    finish(app)
    assert not app.test_errors
    assert not window.winfo_exists()
    assert app.job_fields["company"].get() == "Fictional Laboratory"
    assert calls == []


def test_closing_paste_dialog_before_extraction_writes_nothing(app, monkeypatch, tmp_path):
    calls = block_ai_settings(app, monkeypatch)
    app.paste_job_text()
    window = app._paste_window
    app.pasted_job_text.insert("1.0", TEXT)
    window.destroy()
    app.window.update()
    assert not app.busy
    assert list(tmp_path.iterdir()) == []
    assert calls == []


def test_import_review_save_controls_remain_visible_in_small_window(app):
    app.window.attributes('-alpha', 0)
    app.window.geometry('960x680')
    app.tabs.select(app.import_tab)
    app.window.deiconify()
    app.import_note.set('Review this extracted job. ' * 20)
    buttons = [button for button in app.import_buttons if button['text'] in
               {'Save reviewed job', 'Clear form', 'Paste job text…', 'Fetch details'}]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.window.update()
        if all(button.winfo_ismapped() and button.winfo_height() > 1 for button in buttons):
            break
        time.sleep(.01)
    bottom = app.window.winfo_rooty() + app.window.winfo_height()
    for button in buttons:
        assert button.winfo_ismapped() and button.winfo_height() > 1
        assert button.winfo_rooty() + button.winfo_height() <= bottom
    # Even a long extraction warning cannot push the description out of reach.
    canvas = app.description.master
    while not isinstance(canvas, tk.Canvas):
        canvas = canvas.master
    canvas.yview_moveto(1)
    app.window.update()
    assert app.description.winfo_rooty() < bottom
