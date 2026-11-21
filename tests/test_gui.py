"""Exercise the native window with synthetic data and no visible windows."""
import socket
import gc
import threading
import weakref
import time
import tkinter as tk
from pathlib import Path

import pytest

from job_radar import cv, gui
from job_radar.gui_services import GuiService


@pytest.fixture
def app(tmp_path, monkeypatch, synthetic_codex_metadata, tk_session_root):
    def denied(*args, **kwargs):
        raise AssertionError("GUI test attempted a network or model call")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(cv, "codex_response", denied)
    monkeypatch.setattr(cv, "api_response", denied)
    monkeypatch.setattr(cv, "fetch_job_page_text", denied)
    monkeypatch.setattr("job_radar.revisions.codex_response", denied)
    monkeypatch.setattr("job_radar.revisions.api_response", denied)
    errors, notices = [], []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, message, **kw: errors.append(message))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda title, message, **kw: notices.append(message))
    gc.collect()
    try:
        window = tk.Toplevel(tk_session_root)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    window.withdraw()
    instance = gui.JobRadarApp(window, GuiService(tmp_path))
    instance.test_errors = errors
    instance.test_notices = notices
    monkeypatch.setattr(instance.service, "check_codex", lambda: {"installed": True, "authenticated": True})
    try:
        yield instance
    finally:
        if not instance._closed:
            # Assertions can fail before finish(); never destroy an active worker's UI.
            finish(instance)
            window.destroy()


def finish(app):
    """Let the worker's queue callback execute through the real Tk event loop."""
    deadline = time.monotonic() + 5
    while app.busy and time.monotonic() < deadline:
        app.window.update()
        time.sleep(0.01)
    app.window.update()
    assert not app.busy, "GUI worker did not complete"


def vacancy(**changes):
    return {
        "company": "Example Bio", "title": "Scientist", "location": "Lausanne",
        "source_url": "https://example.org/jobs/one",
        "description": "Coordinate protein laboratory experiments.",
        "source": "company website", **changes,
    }


def save_through_form(app, **changes):
    raw = vacancy(**changes)
    for key, value in raw.items():
        if key in app.job_fields:
            app.job_fields[key].set(value)
    app.description.delete("1.0", "end")
    app.description.insert("1.0", raw["description"])
    app.save_job()
    finish(app)
    assert not app.test_errors
    return app.selected_job["job_id"]


def test_window_opens_with_expected_tabs_and_creates_no_data(app, tmp_path):
    assert [app.tabs.tab(tab, "text") for tab in app.tabs.tabs()] == [
        "Jobs", "Find jobs", "Add a job", "Reference library", "My profile", "Create documents", "Review & improve", "My Network",
    ]
    assert app.window.state() == "withdrawn"
    assert app.window.minsize() == (960, 680)
    assert app.job_tree.get_children() == ()
    assert app.document_job.get() == ""
    assert not app.compile_pdf.get()
    assert not app.test_errors
    assert list(tmp_path.iterdir()) == []


def test_reference_type_is_used_for_add_and_explicit_correction(app, monkeypatch, tmp_path):
    path = tmp_path / "letter.txt"
    path.write_text("Dear Hiring Team,\nA concise example letter.", encoding="utf-8")
    monkeypatch.setattr(gui.filedialog, "askopenfilenames", lambda **kw: (str(path),))
    app.reference_type.set("Cover letter")
    app.add_references()
    finish(app)
    assert app.references[0]["document_type"] == "cover_letter"
    assert app.doc_refs.get(0).startswith("[Cover letter]")
    app.ref_tree.selection_set(app.references[0]["id"])
    app.reference_type.set("CV")
    app.set_reference_type()
    finish(app)
    assert app.references[0]["document_type"] == "cv"
    assert path.read_text(encoding="utf-8").startswith("Dear Hiring Team")
    assert not app.test_errors


def test_improve_requires_feedback_and_sends_only_selected_document(app, monkeypatch, tmp_path):
    source = tmp_path / "applications" / "draft"
    revised = tmp_path / "applications" / "revision"
    drafts = [{"directory": source, "label": "Initial version"}]
    monkeypatch.setattr(app.service, "list_drafts", lambda: list(drafts))
    monkeypatch.setattr(app.service, "draft_text", lambda directory, document: f"{Path(directory).name}: {document}")
    calls = []
    def improve(directory, document, feedback, **kwargs):
        calls.append((directory, document, feedback, kwargs))
        drafts.insert(0, {"directory": revised, "label": "New version"})
        return {"outputs": [revised], "output_root": revised, "validation_errors": [], "compile_errors": []}
    monkeypatch.setattr(app.service, "improve", improve)
    app.refresh_drafts()
    app.improve_document()
    assert "Write the changes" in app.test_errors.pop()
    assert not calls
    app.review_document.set("cover_letter")
    app.revision_compile_pdf.set(True)
    app.revision_feedback.insert("1.0", "Keep my opening. Shorten the final paragraph.")
    app.improve_document()
    finish(app)
    assert calls == [(source, "cover_letter", "Keep my opening. Shorten the final paragraph.",
                      {"compile_pdf": True, "provider_settings": app.ai_current})]
    assert app._chosen_draft()["directory"] == revised
    assert app.draft_viewer.get("1.0", "end-1c") == "revision: cover_letter"
    assert app.revision_feedback.get("1.0", "end-1c") == ""
    assert "previous version is saved in history" in app.revision_result.get()
    shown = []
    monkeypatch.setattr(app.service, "compare_draft", lambda directory, document: "- old words\n+ new words")
    monkeypatch.setattr(app, "_show_text", lambda title, text: shown.append(text))
    app.compare_revision()
    assert shown == ["- old words\n+ new words"]
    assert not app.test_errors


def test_failed_improvement_keeps_feedback_and_current_version(app, monkeypatch, tmp_path):
    source = tmp_path / "applications" / "draft"
    monkeypatch.setattr(app.service, "list_drafts", lambda: [{"directory": source, "label": "Initial version"}])
    monkeypatch.setattr(app.service, "draft_text", lambda *args: "Original letter")
    monkeypatch.setattr(app.service, "improve", lambda *args, **kwargs: {
        "outputs": [tmp_path / "failed"], "output_root": tmp_path / "failed",
        "validation_errors": ["Unsupported factual claim"], "compile_errors": []})
    app.refresh_drafts()
    app.revision_feedback.insert("1.0", "Please revise my opening.")
    app.improve_document()
    finish(app)
    assert app._chosen_draft()["directory"] == source
    assert app.revision_feedback.get("1.0", "end-1c") == "Please revise my opening."
    assert "Unsupported factual claim" in app.revision_result.get()
    assert app.draft_viewer.get("1.0", "end-1c") == "Original letter"


def test_manual_form_save_selects_one_job_and_filters_saved_jobs(app, monkeypatch):
    job_id = save_through_form(app)
    assert app.job_tree.selection() == (job_id,)
    assert app.selected_job["status"] == "interested"
    assert app.tabs.select() == str(app.jobs_tab)
    assert app.document_job.current() == 0
    assert app.service.jobs()[0]["description"] == vacancy()["description"]
    shown = []
    monkeypatch.setattr(app, "_show_text", lambda title, text: shown.append((title, text)))
    app.show_vacancy()
    assert shown[0][0] == "Saved vacancy details"
    assert vacancy()["source_url"] in shown[0][1]
    assert vacancy()["description"] in shown[0][1]
    app.documents_for_selected()
    assert app.tabs.select() == str(app.docs_tab)
    app.search.set("nonexistent employer")
    app.window.update()
    assert app.job_tree.get_children() == ()
    assert app.selected_job is None
    assert all(not variable.get() for variable in app.tracking.values())
    app.search.set("lausanne")
    assert app.job_tree.get_children() == (job_id,)


def test_applied_tracking_autodates_and_preserves_date_on_later_edits(app):
    job_id = save_through_form(app)
    app.tracking["status"].set("applied")
    app.tracking["next_action"].set("Wait for recruiter")
    app.save_tracking()
    finish(app)
    saved = app.service.jobs()[0]
    assert saved["status"] == "applied"
    assert saved["application_date"]
    assert app.selected_job["application_date"] == saved["application_date"]
    assert app.tracking["application_date"].get() == saved["application_date"]
    app.tracking["contact"].set("Hiring team")
    app.notes.insert("1.0", "CV reviewed before applying")
    app.save_tracking()
    finish(app)
    updated = app.service.jobs()[0]
    assert updated["application_date"] == saved["application_date"]
    assert updated["contact"] == "Hiring team"
    assert updated["notes"] == "CV reviewed before applying"
    assert len(app.service.history(job_id)) == 3
    assert not app.test_errors


def test_incomplete_fetch_is_reviewed_before_database_save(app, monkeypatch, tmp_path):
    seen = []
    draft_path = tmp_path / "fetched.yaml"

    def fetch(url):
        seen.append(url)
        return {"job": vacancy(location=""), "yaml_path": draft_path,
                "missing_fields": ["location"], "warnings": ["Review extracted text"]}

    monkeypatch.setattr(app.service, "import_url", fetch)
    app.import_url.set("https://example.org/jobs/one")
    app.fetch_job()
    finish(app)
    assert seen == ["https://example.org/jobs/one"]
    assert app.job_fields["company"].get() == "Example Bio"
    assert "location" in app.import_note.get()
    assert app.draft_path == draft_path
    assert not app.service.db_path.exists()
    app.save_job()
    finish(app)
    assert "location" in app.test_errors.pop()
    assert not app.service.db_path.exists()
    app.job_fields["location"].set("Lausanne")
    app.save_job()
    finish(app)
    assert app.service.jobs()[0]["location"] == "Lausanne"
    assert not app.test_errors


def test_reference_picker_adds_local_files_and_keeps_only_chosen_ids(app, monkeypatch, tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("Research CV example", encoding="utf-8")
    second.write_text("Operations CV example", encoding="utf-8")
    monkeypatch.setattr(gui.filedialog, "askopenfilenames", lambda **kw: (str(first), str(second)))
    app.add_references()
    finish(app)
    assert len(app.references) == 2
    assert app.doc_refs.curselection() == ()
    chosen_id = app.references[1]["id"]
    app.doc_refs.selection_set(1)
    app.add_document_reference()
    third = tmp_path / "third.txt"
    third.write_text("Another CV example", encoding="utf-8")
    app.service.add_ref(third)
    app.refresh()
    assert app.chosen_reference_ids == [chosen_id]
    assert app.doc_selected_refs.size() == 1
    app.ref_tree.selection_set(chosen_id)
    app.remove_reference()
    finish(app)
    assert app.chosen_reference_ids == []
    assert app.doc_selected_refs.size() == 0
    assert second.read_text(encoding="utf-8") == "Operations CV example"
    assert len(app.references) == 2
    assert not app.test_errors


def test_document_actions_send_only_chosen_job_and_references(app, monkeypatch, tmp_path):
    first_id = save_through_form(app)
    save_through_form(app, title="Other role", source_url="https://example.org/jobs/two")
    for name in ("chosen", "unused"):
        path = tmp_path / f"{name}.txt"
        path.write_text(f"{name} reference contents", encoding="utf-8")
        app.service.add_ref(path)
    app.refresh()
    app.document_job.current(next(i for i, job in enumerate(app.jobs) if job["job_id"] == first_id))
    app.doc_refs.selection_set(0)
    app.add_document_reference()
    reference_id = app.references[0]["id"]
    seen = []

    def capture(kind, job_id, reference_ids, **kwargs):
        seen.append((kind, job_id, reference_ids, kwargs))
        return {"output_root": tmp_path / kind, "validation_errors": [], "compile_errors": []}

    monkeypatch.setattr(app.service, "preview", lambda *args, **kwargs: capture("preview", *args, **kwargs))
    monkeypatch.setattr(app.service, "generate", lambda *args, **kwargs: capture("generate", *args, **kwargs))
    reviews = []
    monkeypatch.setattr(app.service, "review_text", lambda result: "Selected source information")
    monkeypatch.setattr(app, "_show_text", lambda title, body: reviews.append((title, body)))
    app.create_documents(preview=True)
    finish(app)
    assert seen == [("preview", first_id, [reference_id], {"provider_settings": app.ai_current})]
    assert "No model was called" in app.document_result.get()
    assert reviews == [("Information to be sent to Codex", "Selected source information")]
    app.compile_pdf.set(True)
    app.create_documents(preview=False)
    finish(app)
    assert seen[-1] == ("generate", first_id, [reference_id],
                       {"compile_pdf": True, "provider_settings": app.ai_current})
    assert app.last_output == tmp_path / "generate"
    assert not app.test_errors


def test_document_selection_limits_stop_before_service_calls(app, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid selection must not reach the service")

    monkeypatch.setattr(app.service, "preview", unexpected)
    app.create_documents(preview=True)
    assert "choose a job" in app.test_errors.pop()
    save_through_form(app)
    app.references = [{"id": f"ref-{i}"} for i in range(4)]
    for item in app.references:
        app.doc_refs.insert("end", item["id"])
    app.chosen_reference_ids = [item["id"] for item in app.references]
    app.create_documents(preview=True)
    assert "up to three" in app.test_errors.pop()
    assert not app.busy


def test_async_failure_restores_buttons_and_saved_state(app):
    job_id = save_through_form(app)
    before = app.service.db_path.read_bytes()

    def fail():
        raise ValueError("Synthetic extraction failure")

    app._run("Testing worker", fail, lambda _: pytest.fail("Failed action ran success callback"))
    assert app.busy
    assert all(str(button["state"]) == "disabled" for button in app.action_buttons)
    app._close()
    assert app.window.winfo_exists()
    assert app.test_notices
    finish(app)
    assert app.test_errors == ["Synthetic extraction failure"]
    assert all(str(button["state"]) == "normal" for button in app.action_buttons)
    assert app.service.db_path.read_bytes() == before
    assert app.selected_job["job_id"] == job_id


def test_document_validation_and_compile_failures_are_visible(app, tmp_path):
    app._documents_ready({"output_root": tmp_path / "draft",
                          "validation_errors": ["Unsupported factual claim"],
                          "compile_errors": ["LaTeX is unavailable"]}, preview=False)
    assert "needs review" in app.document_result.get()
    assert "Unsupported factual claim" in app.document_result.get()
    assert "LaTeX is unavailable" in app.document_result.get()
    assert app.last_output == tmp_path / "draft"


def test_clear_form_discards_previous_vacancy_metadata(app):
    app._fill_draft(vacancy(description_excerpt="Old excerpt", job_id="old-id"))
    app.import_url.set("https://example.org/jobs/old")
    app.draft_path = Path("old.yaml")
    app.clear_import()
    assert app.draft == {}
    assert app.draft_path is None
    assert app.import_url.get() == ""
    assert all(not field.get() for field in app.job_fields.values())
    assert app.description.get("1.0", "end-1c") == ""


def test_connection_check_reports_status_without_generating(app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("A connection check must not generate documents")
    monkeypatch.setattr(app.service, "generate", forbidden)
    app.check_codex_connection()
    finish(app)
    assert "found and signed in" in app.connection_status.get()
    assert "No document generation" in app.status.get()
    assert not app.test_errors


def test_missing_codex_stops_generation_and_replaces_old_preview_message(app, monkeypatch):
    save_through_form(app)
    app.document_result.set("Old local preview ready")
    monkeypatch.setattr(app.service, "check_codex", lambda: {"installed": False, "authenticated": False, "detail": "No installation found."})
    monkeypatch.setattr(app.service, "generate", lambda *args, **kwargs: pytest.fail("Missing Codex must stop generation"))
    app.create_documents(preview=False)
    finish(app)
    assert "could not be found" in app.test_errors[0]
    assert "new run did not finish" in app.document_result.get()
    assert "Old local preview ready" not in app.document_result.get()


def test_explicit_reference_lists_enforce_three_and_only_add_sends_documents(app, tmp_path):
    for index in range(4):
        source = tmp_path / f"reference-{index}.txt"
        source.write_text(f"Synthetic reference text {index}", encoding="utf-8")
        app.service.add_ref(source, document_type="cover_letter" if index == 0 else "cv")
    app.refresh()
    assert app.doc_refs.size() == 4
    assert app.doc_selected_refs.size() == 0
    app.doc_refs.selection_set(0)
    assert app.chosen_reference_ids == []  # Highlighting is not adding.
    for _ in range(3):
        app.doc_refs.selection_set(0)
        app.add_document_reference()
    chosen = list(app.chosen_reference_ids)
    assert len(set(chosen)) == 3
    assert app.doc_selected_refs.size() == 3
    assert "(3/3)" in app.reference_selection_count.get()
    app.doc_refs.selection_set(0)
    app.add_document_reference()
    assert app.chosen_reference_ids == chosen
    assert "Remove one" in app.reference_selection_note.get()
    app.refresh()
    assert app.chosen_reference_ids == chosen
    assert app.doc_refs.size() == 1
    app.doc_selected_refs.selection_clear(0, "end")
    app.doc_selected_refs.selection_set(1)
    app.remove_document_reference()
    assert chosen[1] not in app.chosen_reference_ids
    assert app.doc_refs.size() == 2
    app.doc_refs.selection_set(0)
    app.add_document_reference()
    assert len(app.chosen_reference_ids) == 3
    assert not app.test_errors


def test_background_action_shows_elapsed_time_without_changing_result(app, monkeypatch):
    import threading
    done = threading.Event()
    ticks = [100.0]
    monkeypatch.setattr(gui.time, "monotonic", lambda: ticks[0])
    app._run("Generating drafts", lambda: done.wait(2), lambda result: app.status.set("Finished"))
    try:
        ticks[0] = 165.0
        app._poll()
        assert "elapsed 1:05" in app.status.get()
    finally:
        done.set()
    finish(app)
    assert app.status.get() == "Finished"


def test_worker_must_exit_before_completion_callback_or_close(app, monkeypatch):
    """A queued result is not proof that its worker released its Tk closures."""
    result_queued = threading.Event()
    allow_exit = threading.Event()
    worker_class = threading.Thread
    callback_threads = []

    class DelayedExitThread(worker_class):
        def run(self):
            super().run()
            result_queued.set()
            assert allow_exit.wait(5), "Test did not release its worker"

    monkeypatch.setattr(gui.threading, "Thread", DelayedExitThread)
    app._run("Finishing synthetic work", lambda: "result",
             lambda result: callback_threads.append((result, threading.get_ident())))
    assert result_queued.wait(5)
    try:
        app._poll()
        assert app.busy, "A live worker must still prevent closing the Tk window"
        assert callback_threads == []
        app._close()
        assert app.window.winfo_exists()
        assert app.test_notices
    finally:
        allow_exit.set()
        # Join even on the old implementation so a failing regression is safe.
        for worker in threading.enumerate():
            if isinstance(worker, DelayedExitThread):
                worker.join(5)
    finish(app)
    assert callback_threads == [("result", threading.get_ident())]


def test_worker_releases_action_on_the_ui_thread(app):
    released_on = []

    class Action:
        def __call__(self):
            return "complete"

    action = Action()
    weakref.finalize(action, lambda: released_on.append(threading.get_ident()))
    app._run("Synthetic local action", action, lambda _: None)
    del action
    app._worker.join(5)
    assert not app._worker.is_alive()
    assert released_on == [], "The app must retain the closure until UI completion"
    finish(app)
    assert released_on == [threading.get_ident()]
    assert app._worker is None
    assert app._active_action is None


def test_direct_destroy_cancels_poll_and_does_not_reschedule(app):
    app._poll()
    app._poll()
    assert app.window.tk.call("after", "info") == (app._poll_timer,)
    app.window.destroy()
    assert app._closed
    assert app._poll_timer is None
    assert app.window.tk.splitlist(app.window.tk.call("after", "info")) == ()
    app._poll()
    app._run("Already closed", lambda: pytest.fail("Closed app ran work"), lambda _: None)
    assert app.window.tk.splitlist(app.window.tk.call("after", "info")) == ()


@pytest.mark.parametrize("cycle", range(20))
def test_repeated_window_save_and_worker_shutdown(app, cycle):
    """Exercise real SQLite saves across successive test windows."""
    identifier = save_through_form(app, title=f"Synthetic role {cycle}")
    assert app._worker is None
    assert app._active_action is None
    app.tracking["status"].set("applied")
    app.save_status()
    app.tracking["next_action"].set("Synthetic follow-up")
    app.save_tracking()
    assert app.service.jobs()[0]["job_id"] == identifier
    assert app.service.jobs()[0]["next_action"] == "Synthetic follow-up"
    assert not app.test_errors
    assert not any(worker.name == "JobRadarAction" for worker in threading.enumerate())
    app.window.destroy()
    assert app._closed
    assert app.window.tk.splitlist(app.window.tk.call("after", "info")) == ()
